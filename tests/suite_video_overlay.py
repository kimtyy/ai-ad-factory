"""촬영 영상 원본 직가공(video_overlay): 나레이션 겹침 재배치·9:16 변환(크롭/블러, 가로/세로/HDR)·오디오 더킹/음소거·특수문자 자막"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402
import os
import re
import shutil
import subprocess
import logging
logging.disable(logging.WARNING)

import imageio_ffmpeg

from core.renderers.video_overlay_renderer import render_video_overlay, retime_scenes, render_top_badge

FF = imageio_ffmpeg.get_ffmpeg_exe()
fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


def ff(*a):
    return subprocess.run([FF, "-y", "-hide_banner", *a], capture_output=True, text=True, encoding="utf-8", errors="ignore")


def video_size(p):
    m = re.search(r"Video:.*?, (\d{2,5})x(\d{2,5})", ff("-i", p).stderr)
    return (int(m[1]), int(m[2])) if m else None


def mean_vol(p, t0, t1):
    r = ff("-ss", str(t0), "-t", str(t1 - t0), "-i", p, "-af", "volumedetect", "-vn", "-f", "null", "-").stderr
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf)", r)
    return float("-inf") if (not m or m[1] == "-inf") else float(m[1])


W = tmp("vo_suite_")
try:
    # 합성 소스: 1280x720, 8초, 현장음(사인파 진폭 0.5)
    src = os.path.join(W, "land.mp4")
    ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=8", "-f", "lavfi", "-i", "sine=f=220:d=8,volume=0.5",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src)
    # 합성 나레이션 (실제 TTS 대신 길이만 같은 1kHz 톤)
    def tone(name, sec):
        p = os.path.join(W, name)
        ff("-f", "lavfi", "-i", f"sine=f=1000:d={sec}", "-c:a", "libmp3lame", p)
        return p
    long_tts, short_tts = tone("long.mp3", 4.0), tone("short.mp3", 1.0)

    # 1. 재배치: 씬1 슬롯(2초)보다 나레이션(4초)이 길면 씬2는 그 뒤로 밀림 (겹침 없음)
    sb = [{"start_sec": 0.0, "end_sec": 2.0, "caption": "첫 씬", "hook_title": "후킹", "audio_path": long_tts},
          {"start_sec": 2.0, "end_sec": 4.0, "caption": "둘째 씬", "audio_path": short_tts},
          {"start_sec": 9.0, "end_sec": 12.0, "caption": "영상 밖 씬", "audio_path": short_tts}]
    tl = retime_scenes(sb, 8.0)
    check(len(tl) == 2 and tl[1]["start"] >= tl[0]["start"] + tl[0]["tts_sec"] - 0.01,
          f"나레이션이 길면 다음 씬을 뒤로 밀어 겹치지 않음 ({[(round(t['start'], 2), round(t['end'], 2)) for t in tl]})")
    check(all(tl[i]["end"] <= tl[i + 1]["start"] + 1e-6 for i in range(len(tl) - 1)), "표시 구간도 겹치지 않음")
    check(all(t["idx"] != 2 for t in tl), "영상 길이를 넘는 씬은 제외")

    # 2. 가로 영상 → 9:16 (크롭 / 블러), 오디오 더킹
    outs = {}
    for fit in ("crop", "blur"):
        o = os.path.join(W, f"{fit}.mp4")
        render_video_overlay(src, sb[:2], o, audio_mode="keep_ducking", fit_mode=fit, badge_text="24시간 안심 매장")
        outs[fit] = o
        check(video_size(o) == (1080, 1920), f"가로 → 1080x1920 ({fit}) {video_size(o)}")
    # 나레이션이 없는 구간(6.5~7.8초)의 원본 음량이 20%(-14dB)로 낮아졌는지 (원본 같은 구간과 비교)
    v = mean_vol(outs["crop"], 6.5, 7.8)
    delta = v - mean_vol(src, 6.5, 7.8)
    check(-16.5 < delta < -11.5, f"원본 현장음 20% 더킹 (원본 대비 {delta:.1f} dB, 기대 약 -14 dB)")
    # 나레이션이 있는 구간은 1kHz 톤이 더해져 더 큼
    check(mean_vol(outs["crop"], 0.5, 3.0) > v + 3, "나레이션 구간이 현장음보다 크게 들림")

    # 3. 음소거 모드: 나레이션 없는 구간 무음
    o = os.path.join(W, "mute.mp4")
    render_video_overlay(src, sb[:2], o, audio_mode="mute", fit_mode="crop", badge_text="")
    vm = mean_vol(o, 6.5, 7.8)
    check(vm == float("-inf") or vm < -60, f"음소거 모드: 나레이션 외 현장음 없음 ({vm} dB)")

    # 4. 세로(회전 메타) / HDR 원본
    for name in ("phone_portrait.mov", "phone_hdr.mov"):
        o = os.path.join(W, name + ".mp4")
        render_video_overlay(fixture(name), [{"start_sec": 0, "end_sec": 2, "caption": "세로 테스트", "hook_title": "훅"}], o,
                             fit_mode="crop", badge_text="배지")
        check(video_size(o) == (1080, 1920), f"{name} → 1080x1920 {video_size(o)}")

    # 5. 자막·뱃지 특수문자/따옴표/이모지 (PNG 렌더라 ffmpeg 필터 구문과 무관해야 함)
    nasty = "100% 할인 'A' \"B\" : ; , [x] {y} " + chr(92) + " = ' 😀 <b>"
    o = os.path.join(W, "nasty.mp4")
    try:
        render_video_overlay(src, [{"start_sec": 0, "end_sec": 3, "caption": nasty, "hook_title": nasty}], o,
                             badge_text=nasty)
        ok = os.path.exists(o) and video_size(o) == (1080, 1920)
    except Exception as e:
        ok = False
        print("   ", e)
    check(ok, "특수문자·따옴표·이모지 자막/뱃지에서도 렌더 성공")

    # 6. 뱃지: 한글 폰트에 없는 이모지 접두어(깨진 □) 제거
    from PIL import ImageChops
    b = render_top_badge("텍스트", 1080, 1920)
    check(b.getbbox() is not None and "🏷" not in open(os.path.join(ROOT, "core/renderers/video_overlay_renderer.py"), encoding="utf-8").read().split("def render_video_overlay")[0].split("text = ")[1][:80],
          "뱃지에 깨지는 이모지 접두어 없음")

    # 7. 원본 영상보다 계획 타임라인이 긴 경우(짧은 클립)에도 길이 초과/실패 없음
    o = os.path.join(W, "short.mp4")
    render_video_overlay(fixture("phone_landscape.mp4"), sb[:2], o, badge_text="x")
    d = re.search(r"Duration: 00:00:0?(\d+\.\d+)", ff("-i", o).stderr)
    check(bool(d) and float(d[1]) <= 2.1, f"짧은 클립(2초) 길이 유지 ({d[1] if d else '?'}초)")

    # 8. 특수문자(1+1·2+1) 및 씬 1 오프닝 자막 시간차 싱크 (말토막 짤림 방지)
    special_vo = "같은 돈으로 두 배 챙기는 1+1·2+1 진열, 이것만 기억해요!"
    clipped_hook = "같은 돈으로 두 배 챙기는 1"
    o_sync = os.path.join(W, "special_sync.mp4")
    render_video_overlay(
        src,
        [{"start_sec": 0, "end_sec": 3, "caption": special_vo, "hook_title": clipped_hook}],
        o_sync,
        badge_text="1+1 행사 진열"
    )
    check(os.path.exists(o_sync) and video_size(o_sync) == (1080, 1920),
          "특수문자(1+1·2+1) 및 첫 프레임 말토막 훅 싱크 보정 렌더 성공")
finally:
    shutil.rmtree(W, ignore_errors=True)
finish(fails)
