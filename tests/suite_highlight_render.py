"""롱폼 하이라이트 → 직가공 렌더러 연결(Step 3): window 단일 인코딩(중간 컷 파일·-c copy 없음)·길이=창+CTA·CTA 연장/정지·
트랙 A/C/B 오디오(100% / 노이즈 제거 / ≤8% 더킹 / 음악 의심 0%)·자막 상대시간 유지·HDR 창 정규화·선정→렌더 통합"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402
import os
import re
import shutil
import subprocess
import logging
logging.disable(logging.WARNING)

for _k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ[_k] = ""

import imageio_ffmpeg
from PIL import Image

from core.media.audio_stt import Sentence, Span, Transcript, Word
from core.planners.highlight_selector import HighlightCandidate, select_highlights
from core.renderers import video_overlay_renderer as vor
from core.renderers.highlight_renderer import (audio_mode_for_track, build_highlight_storyboard, make_track_b_scenes,
                                               render_highlight)

FF = imageio_ffmpeg.get_ffmpeg_exe()
fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


def ff(*a):
    return subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", *a], capture_output=True)


def duration(p):
    r = subprocess.run([FF, "-hide_banner", "-i", p], capture_output=True, text=True, encoding="utf-8", errors="ignore").stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def mean_vol(p, t0, t1):
    r = subprocess.run([FF, "-hide_banner", "-ss", str(t0), "-t", str(t1 - t0), "-i", p, "-af", "volumedetect", "-vn", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore").stderr
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf)", r)
    return float("-inf") if (not m or m[1] == "-inf") else float(m[1])


def pixel(p, t, xy=(40, 700)):
    out = os.path.join(W, "px.png")
    ff("-ss", str(t), "-i", p, "-frames:v", "1", out)
    return Image.open(out).convert("RGB").getpixel(xy)


def dominant(rgb):
    r, g, b = rgb
    if r > 150 and g < 100 and b < 100:
        return "red"
    if g > 120 and r < 100 and b < 100:
        return "green"
    if b > 150 and r < 100 and g < 100:
        return "blue"
    if r > 150 and g > 150 and b < 100:
        return "yellow"
    if r > 150 and b > 150 and g < 100:
        return "magenta"
    return f"?{rgb}"


def tone(name, sec, f=1000):
    p = os.path.join(W, name)
    ff("-f", "lavfi", "-i", f"sine=f={f}:d={sec},volume=4", "-c:a", "libmp3lame", p)
    return p


W = tmp("hl_render_")
try:
    # 원본: 60초 1280x720, 구간별 색 (0-10 빨강 / 10-28 초록 / 28-31 파랑 / 31-58 노랑 / 58-60 자홍) + 440Hz 현장음
    src = os.path.join(W, "long.mp4")
    inputs = []
    for c, d in (("red", 10), ("green", 18), ("blue", 3), ("yellow", 27), ("magenta", 2)):
        inputs += ["-f", "lavfi", "-i", f"color=c={c}:s=1280x720:r=15:d={d}"]
    ff(*inputs, "-f", "lavfi", "-i", "sine=f=440:d=60,volume=4",
       "-filter_complex", "[0:v][1:v][2:v][3:v][4:v]concat=n=5:v=1:a=0[v]", "-map", "[v]", "-map", "5:a",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src)
    check(abs(duration(src) - 60.0) < 0.3, f"합성 원본 60초 ({duration(src):.1f}s)")
    src_level = mean_vol(src, 12, 20)

    def cand(track="A", start=10.0, end=28.0, music=False, coverage=0.8):
        return HighlightCandidate(
            start_sec=start, end_sec=end, cta_sec=3.0, track=track, coverage=coverage,
            caption_scenes=[{"start": 0.3, "end": 6.0, "text": "주문하면 바로 무쳐요"}, {"start": 7.0, "end": 12.5, "text": "그 자리에서 신선하게 버무려요"},
                            {"start": 13.0, "end": end - start - 0.2, "text": "아삭한 식감이 살아 있어요"}],
            flags=[], music_suspected=music)

    cta_audio = tone("cta.mp3", 2.0)
    CTA = "네이버에서 오늘식탁 검색해 보세요!"

    # 스파이: 인코딩 호출 수·인자 확인 (중간 컷 파일/-c copy 금지)
    calls, scripts = [], []
    _orig_run = vor.subprocess.run

    def spy(cmd, *a, **k):
        if isinstance(cmd, (list, tuple)) and "-filter_complex_script" in cmd:
            calls.append(list(cmd))
            try:
                scripts.append(open(cmd[list(cmd).index("-filter_complex_script") + 1], encoding="utf-8").read())
            except OSError:
                scripts.append("")
        return _orig_run(cmd, *a, **k)

    vor.subprocess.run = spy

    # 1. 트랙 A: 길이 = 창 + CTA, 단 1회 인코딩, 입력 측 -ss, 중간 파일 없음
    outdir = os.path.join(W, "out_a")
    os.makedirs(outdir)
    out_a = os.path.join(outdir, "a.mp4")
    render_highlight(src, cand("A"), out_a, cta_text=CTA, cta_audio_path=cta_audio, badge_text="오늘식탁")
    check(abs(duration(out_a) - 21.0) < 0.15, f"출력 길이 = 창 18초 + CTA 3초 ({duration(out_a):.2f}s)")
    cmd = calls[-1]
    i_ss, i_i = cmd.index("-ss"), cmd.index("-i")
    check(len(calls) == 1 and i_ss < i_i and "copy" not in cmd and cmd[i_ss + 1] == "10.000",
          f"단 1회 정밀 인코딩, 입력 측 -ss 10.000 (-c copy 없음, 호출 {len(calls)}회)")
    check(sorted(os.listdir(outdir)) == ["a.mp4"], f"중간 컷 파일·임시 폴더 없음 {os.listdir(outdir)}")

    # 2. 영상 내용: 창 구간이 맞고, CTA 동안 원본이 이어짐
    check(dominant(pixel(out_a, 0.5)) == "green" and dominant(pixel(out_a, 17.5)) == "green",
          f"창 구간(원본 10~28s)이 정확히 잘림 (0.5s→{dominant(pixel(out_a, 0.5))}, 17.5s→{dominant(pixel(out_a, 17.5))})")
    check(dominant(pixel(out_a, 18.6)) == "blue", f"CTA 동안 원본이 이어서 재생 (18.6s → {dominant(pixel(out_a, 18.6))})")
    check(dominant(pixel(src, 5)) == "red", "측정 도구 점검: 원본 5초는 빨강")

    # 3. 원본이 끝나 CTA를 못 채우면 마지막 프레임 정지 (길이 유지)
    out_f = os.path.join(W, "freeze.mp4")
    render_highlight(src, cand("A", start=50.0, end=58.0), out_f, cta_text=CTA, cta_audio_path=cta_audio)
    check(abs(duration(out_f) - 11.0) < 0.15 and dominant(pixel(out_f, 10.8)) == "magenta",
          f"원본이 모자라면 마지막 프레임 정지 (길이 {duration(out_f):.2f}s, 끝 프레임 {dominant(pixel(out_f, 10.8))})")
    out_end = os.path.join(W, "end.mp4")
    render_highlight(src, cand("A", start=52.0, end=60.0), out_end, cta_text=CTA, cta_audio_path=cta_audio)
    check(abs(duration(out_end) - 11.0) < 0.15, f"원본 끝까지 쓰는 창도 CTA 3초가 붙음 ({duration(out_end):.2f}s)")

    # 4. 트랙별 오디오
    a_lvl = mean_vol(out_a, 2, 10)
    check(abs(a_lvl - src_level) < 1.5, f"트랙 A: 원본 100% 유지 (출력 {a_lvl} dB vs 원본 {src_level} dB)")
    check(mean_vol(out_a, 0.0, 0.05) < src_level - 8, f"원본 오디오 시작 페이드인 100ms ({mean_vol(out_a, 0.0, 0.05)} dB)")
    check(mean_vol(out_a, 18.3, 20.0) > -40, f"CTA 구간: TTS 한 문장이 들림 ({mean_vol(out_a, 18.3, 20.0)} dB)")
    out_nocta = os.path.join(W, "nocta.mp4")
    render_highlight(src, cand("A"), out_nocta, cta_text=CTA, cta_audio_path=None)
    check(mean_vol(out_nocta, 18.4, 20.8) < -50, f"CTA 구간에 원본 음성이 새지 않음 (페이드아웃 후 무음 {mean_vol(out_nocta, 18.4, 20.8)} dB)")

    out_c = os.path.join(W, "c.mp4")
    render_highlight(src, cand("C", coverage=0.45), out_c, cta_text=CTA, cta_audio_path=cta_audio)
    c_lvl = mean_vol(out_c, 2, 10)
    check(abs(duration(out_c) - 21.0) < 0.15 and c_lvl > src_level - 8,
          f"트랙 C: 노이즈 제거(afftdn) 후 원본 유지, 렌더 성공 ({c_lvl} dB)")
    c_script = scripts[len(calls) - 1]
    check("afftdn" in c_script and "afftdn" not in scripts[0], "트랙 C 필터 그래프에만 afftdn 적용 (트랙 A에는 없음)")

    tts_lines = []
    for k, (s, e) in enumerate(((0.3, 5.0), (6.0, 11.0), (12.0, 17.0))):
        tts_lines.append({"text": f"새 컨셉 대사 {k + 1}", "start": s, "end": e, "audio_path": tone(f"b{k}.mp3", 1.5, 800 + 100 * k)})
    out_b = os.path.join(W, "b.mp4")
    render_highlight(src, cand("B", coverage=0.1), out_b, cta_text=CTA, cta_audio_path=cta_audio, tts_scenes=tts_lines)
    b_gap = mean_vol(out_b, 2.5, 5.5)                        # 첫 TTS(0.3~1.8s) 뒤, 다음 TTS(6.0s~) 전: 원본 더킹만
    delta = b_gap - src_level
    check(-26 < delta < -18, f"트랙 B: 원본 ≤ 8% 더킹 (원본 대비 {delta:.1f} dB, 기대 약 -22 dB)")
    check(mean_vol(out_b, 0.5, 1.5) > b_gap + 6, "트랙 B: 새 TTS가 더킹된 원본 위로 또렷이 들림")
    out_bm = os.path.join(W, "bm.mp4")
    render_highlight(src, cand("B", coverage=0.1, music=True), out_bm, cta_text=CTA, cta_audio_path=cta_audio, tts_scenes=tts_lines)
    bm = mean_vol(out_bm, 2.5, 5.5)
    check(bm == float("-inf") or bm < -60, f"음악 의심(music_suspected)이면 원본 0% ({bm} dB)")
    try:
        render_highlight(src, cand("B", coverage=0.1), os.path.join(W, "x.mp4"), cta_text=CTA)
        ok = False
    except ValueError:
        ok = True
    check(ok, "트랙 B는 TTS 대사 없이 렌더하지 않음")
    check([audio_mode_for_track(t) for t in "ACB"] == ["original", "denoise", "keep_ducking"], "트랙 → 오디오 모드 매핑")

    # 5. 자막은 창 기준 상대시간 그대로 (retime으로 밀리지 않음) + CTA 씬은 창 끝에서 시작
    sb = build_highlight_storyboard(cand("A"), cta_text=CTA, cta_audio_path=cta_audio)
    tl = vor.retime_scenes(sb, 21.0)
    check([round(t["start"], 2) for t in tl][:3] == [0.3, 7.0, 13.0] and abs(sb[-1]["start_sec"] - 18.0) < 1e-6
          and abs(sb[-1]["end_sec"] - 21.0) < 1e-6, f"STT 자막 원래 시간 유지, CTA는 창 끝(18.0s)에서 시작 {[round(t['start'], 2) for t in tl]}")
    check(all("audio_path" in s and s["audio_path"] is None for s in sb[:-1]) and sb[-1]["audio_path"] == cta_audio,
          "트랙 A: TTS는 CTA 한 문장뿐")

    # 6. 창 검증 · 창에만 정규화(HDR) · 기존 호출 불변
    try:
        vor.render_video_overlay(src, [], os.path.join(W, "bad.mp4"), window=(20.0, 10.0))
        ok = False
    except ValueError:
        ok = True
    check(ok, "window 시작이 끝보다 늦으면 ValueError")
    hdr = fixture("phone_hdr.mov")
    out_h = os.path.join(W, "hdr.mp4")
    render_highlight(hdr, cand("C", start=0.5, end=2.5, coverage=0.4), out_h, cta_text=CTA, cta_audio_path=cta_audio)
    check(abs(duration(out_h) - 5.0) < 0.2, f"HDR 원본도 창만 톤매핑해 렌더 ({duration(out_h):.2f}s)")
    n_before = len(calls)
    plain = os.path.join(W, "plain.mp4")
    vor.render_video_overlay(fixture("phone_landscape.mp4"), [{"start_sec": 0, "end_sec": 2, "caption": "기존 호출"}], plain)
    cmd_plain = calls[n_before]
    check(len(calls) == n_before + 1 and "-ss" not in cmd_plain and abs(duration(plain) - 2.0) < 0.15,
          f"window=None은 기존과 동일 (입력 -ss 없음, 길이 {duration(plain):.2f}s)")

    # 7. 선정 → 렌더 통합: 가짜 전사 → 후보 → 렌더 길이 = 후보 창 + CTA
    items, t, n = [], 5.0, 0
    while t < 54.0:
        items.append((round(t, 2), round(t + 2.9, 2), "주문하면 바로 무쳐요 겉절이" if 20 <= t < 45 else "오늘 날씨가 좋네요"))
        t += 3.5
        n += 1
    sents, words = [], []
    for s, e, txt in items:
        words.append(Word(txt, s, e, 0.9))
        sents.append(Sentence(txt, s, e, (len(words) - 1, len(words)), 0.9, 0.02))
    tr = Transcript(3, "k", "ko", "fake", 60.0, words, sents, [Span(s - 0.05, e + 0.05) for s, e, _ in items])
    top = select_highlights(tr, "주문 즉시 무쳐내는 신선한 겉절이", use_llm=False)[0]
    out_i = os.path.join(W, "integ.mp4")
    render_highlight(src, top, out_i, cta_text=CTA, cta_audio_path=cta_audio, badge_text="오늘식탁")
    check(top.track == "A" and abs(duration(out_i) - (top.window_sec + 3.0)) < 0.15,
          f"선정 후보({top.start_sec:.1f}~{top.end_sec:.1f}s, 트랙 {top.track}) → 렌더 {duration(out_i):.2f}s = 창 {top.window_sec}s + CTA 3s")

    # 8. 트랙 B 대본 생성 연결: 창 길이 기준 대본 + 씬별 TTS 주입 → 렌더
    made = []

    def voice(sc):
        made.append(sc["scene_index"] if "scene_index" in sc else len(made))
        return tone(f"v{len(made)}.mp3", 1.2, 700 + 50 * len(made))

    bscenes = make_track_b_scenes(cand("B", coverage=0.05), "30년 손기술로 깎아내는 맞춤 원목 도마", store_name="나무손 공방",
                                  mock=True, voice_fn=voice)
    check(bscenes and all(0.0 <= s["start"] < s["end"] <= 18.0 + 1e-6 for s in bscenes) and len(made) == len(bscenes)
          and all(s["audio_path"] and os.path.exists(s["audio_path"]) for s in bscenes),
          f"트랙 B 대본: 창 길이(18초) 안에서 씬별 TTS 연결 ({len(bscenes)}씬)")
    out_b2 = os.path.join(W, "b2.mp4")
    render_highlight(src, cand("B", coverage=0.05), out_b2, cta_text="네이버에서 나무손 공방을 검색해 보세요!",
                     cta_audio_path=cta_audio, tts_scenes=bscenes)
    check(abs(duration(out_b2) - 21.0) < 0.15, f"트랙 B 생성 대본으로 렌더 완료 ({duration(out_b2):.2f}s)")
    vor.subprocess.run = _orig_run
finally:
    shutil.rmtree(W, ignore_errors=True)
finish(fails)
