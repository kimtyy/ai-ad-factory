"""자체 음원 BGM 믹싱: 음원 고르기, 나레이션 구간 20% 덕킹, 페이드인·아웃, 반복, 음량 보정, 영상 무손실 복사, 실패 시 BGM 없이 저장, 사이드바 UI"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402
import os
import random
import shutil
import subprocess
import logging
logging.disable(logging.WARNING)

import imageio_ffmpeg
import numpy as np

import core.render.bgm as bgm
from core.compositor import XFADE_SEC, AdCompositor

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


work = tmp("bgm_")
ff = imageio_ffmpeg.get_ffmpeg_exe()


def sh(*args):
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


# ---------------------------------------------------------------- 1. 음원 목록·선택
tdir = os.path.join(work, "bgm")
os.makedirs(tdir)
for name in ("a_song.mp3", "b_song.wav", "c_song.m4a", "notes.txt", ".hidden.mp3"):
    open(os.path.join(tdir, name), "wb").write(b"x")
tracks = bgm.list_tracks(tdir)
check([os.path.basename(t) for t in tracks] == ["a_song.mp3", "b_song.wav", "c_song.m4a"], "assets/bgm의 mp3·wav·m4a만 목록에 (숨김·기타 파일 제외)")
check(bgm.pick_track("b_song.wav", tdir).endswith("b_song.wav") and bgm.pick_track(None, tdir) is None
      and bgm.pick_track("없는곡.mp3", tdir) is None and bgm.pick_track(bgm.RANDOM, tdir, random.Random(1)) in tracks
      and bgm.pick_track(bgm.RANDOM, os.path.join(work, "empty")) is None,
      "파일 이름으로 고르기 / 랜덤 / 끄기·없는 곡·빈 폴더는 None")
check(bgm.merge_intervals([(0.5, 2.0), (2.3, 3.0), (4.0, 5.0)]) == [(0.5, 3.0), (4.0, 5.0)],
      "나레이션 사이 0.8초 미만 틈은 이어서 계속 낮춤 (씬 사이 펌핑 방지)")

# ---------------------------------------------------------------- 2. 믹싱 측정 (나레이션 1000Hz, BGM 150Hz로 주파수 분리)
video = os.path.join(work, "v.mp4")
# 6초 영상 + 나레이션: 0.5~2.0초, 3.2~4.6초에 1000Hz
sh("-f", "lavfi", "-i", "color=c=0x336699:s=540x960:r=24:d=6", "-f", "lavfi",
   "-i", "sine=frequency=1000:sample_rate=44100:duration=6,volume='if(between(t,0.5,2.0)+between(t,3.2,4.6),0.35,0)':eval=frame",
   "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video)
track = os.path.join(work, "song.wav")
sh("-f", "lavfi", "-i", "sine=frequency=150:sample_rate=44100:duration=4", "-af", "volume=0.5", track)
voice_ref = os.path.join(work, "voice_ref.wav")      # 나레이션과 같은 크기의 연속 음 (나레이션 평균 음량 측정용)
sh("-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100:duration=2", "-af", "volume=0.35", voice_ref)
VOICE_DB = bgm.mean_volume_db(voice_ref)
out = os.path.join(work, "mixed.mp4")
shutil.copyfile(video, out)
intervals = [(0.5, 2.0), (3.2, 4.6)]
bgm.mix_bgm(out, out, track, intervals, voice_db=VOICE_DB)


def pcm(path):
    raw = subprocess.run([ff, "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", "8000", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(float)


def amp_of(sig, t, f, w=0.2):
    seg = sig[int((t - w / 2) * 8000):int((t + w / 2) * 8000)]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    k = int(round(f * len(seg) / 8000))
    return float(spec[max(0, k - 1):k + 2].max())


x = pcm(out)
amp = lambda t, f, w=0.2: amp_of(x, t, f, w)
EXPECT = 10 ** ((bgm.BGM_FULL_BELOW_VOICE_DB + 20 * np.log10(1 / bgm.DUCK_GAIN)) / 20)   # 나레이션 : 덕킹된 BGM ≈ 7.9배

full = amp(2.6, 150)          # 나레이션 사이 1.2초 틈(> 0.8초) 가운데: BGM 원래 레벨
duck1, duck2 = amp(1.25, 150), amp(3.9, 150)
start, end = amp(0.1, 150, 0.1), amp(5.93, 150, 0.1)
check(0.15 < duck1 / full < 0.26 and 0.15 < duck2 / full < 0.26,
      f"나레이션 구간 BGM = 원래 레벨의 {duck1 / full:.0%}·{duck2 / full:.0%} (목표 20%, 약 -14dB)")
check(start < full * 0.3 and end < full * 0.3, f"시작 페이드인({start / full:.0%})·끝 페이드아웃({end / full:.0%})")
ratio = amp(1.25, 1000) / duck1
check(EXPECT * 0.7 < ratio < EXPECT * 1.3, f"나레이션 중: 나레이션이 BGM의 {ratio:.1f}배 (목표 {EXPECT:.1f}배 = 4dB + 14dB 아래)")

# 반복·음량 보정: 10초 무음 영상 + 2.5초 음원
silent_v = os.path.join(work, "silent.mp4")
sh("-f", "lavfi", "-i", "color=c=black:s=540x960:r=24:d=10", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", silent_v)
short_track = os.path.join(work, "short.wav")
sh("-f", "lavfi", "-i", "sine=frequency=150:sample_rate=44100:duration=2.5", "-af", "volume=0.5", short_track)
lvl_out = os.path.join(work, "level.mp4")
bgm.mix_bgm(silent_v, lvl_out, short_track, [], voice_db=VOICE_DB)
yv = pcm(lvl_out)
a1, a2, a3 = amp_of(yv, 1.8, 150), amp_of(yv, 4.2, 150), amp_of(yv, 6.6, 150)
check(min(a2, a3) > a1 * 0.8, f"음원(2.5초)이 영상(10초)보다 짧으면 반복 (1.8s {a1:.0f} / 4.2s {a2:.0f} / 6.6s {a3:.0f})")
seg = yv[int(1.5 * 8000):int(7.5 * 8000)]
rms_db = 20 * np.log10(np.sqrt(np.mean(seg ** 2)) / 32768)
check(abs(rms_db - (VOICE_DB - bgm.BGM_FULL_BELOW_VOICE_DB)) < 1.5,
      f"음량 보정: BGM 평균 {rms_db:.1f}dB (목표 나레이션 {VOICE_DB:.1f} - 4 = {VOICE_DB - 4:.1f}dB), 무음 영상에도 BGM만 들어감")


def vhash(p):
    return subprocess.run([ff, "-v", "error", "-i", p, "-map", "0:v", "-f", "md5", "-"], capture_output=True, text=True).stdout


check(vhash(video) == vhash(out), "영상은 다시 인코딩하지 않음 (프레임 그대로, 오디오만 새로 섞음)")

# ---------------------------------------------------------------- 3. 조립과 연결 (타임라인으로 덕킹 구간 계산) + 실패 처리
man = []
for i, f in enumerate((440, 660)):
    v, a = os.path.join(work, f"s{i}.mp4"), os.path.join(work, f"s{i}.m4a")
    sh("-loop", "1", "-i", fixture("photo_room.jpg"), "-t", "3", "-r", "24", "-vf", "scale=540:960,setsar=1", "-pix_fmt", "yuv420p", v)
    sh("-f", "lavfi", "-i", f"sine=frequency={f}:duration=2", "-c:a", "aac", a)
    man.append({"scene_index": i + 1, "video_path": v, "audio_path": a, "caption": "자막"})
comp = AdCompositor(output_dir=os.path.join(work, "final"))
res = comp.assemble_ad(man, output_filename="f.mp4", mode="owner", bgm_path=track)
z = pcm(res)
v1, b1 = amp_of(z, 1.0, 440), amp_of(z, 1.0, 150)
v2, b2 = amp_of(z, 3.2, 660), amp_of(z, 3.2, 150)
check(comp.last_bgm_error is None and EXPECT * 0.6 < v1 / b1 < EXPECT * 1.4 and EXPECT * 0.6 < v2 / b2 < EXPECT * 1.4,
      f"조립 결과에 BGM: 씬 1·2 나레이션 위치(디졸브 타임라인)에서 덕킹 (나레이션/BGM {v1 / b1:.1f}배·{v2 / b2:.1f}배)")
bad = AdCompositor(output_dir=os.path.join(work, "bad"))
r2 = bad.assemble_ad(man, output_filename="b.mp4", mode="owner", bgm_path=os.path.join(work, "없음.mp3"))
check(os.path.exists(r2) and bad.last_bgm_error, f"음원이 깨졌거나 없으면 BGM 없이 저장 + 사유 기록 ({str(bad.last_bgm_error)[:40]}…)")

# ---------------------------------------------------------------- 4. 사이드바 UI
from streamlit.testing.v1 import AppTest
_orig_dir = bgm.BGM_DIR
created = []
try:
    bgm.BGM_DIR = os.path.join(work, "empty_ui")
    os.makedirs(bgm.BGM_DIR)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120).run()
    created.append(at.session_state.job.workdir)
    tg = at.sidebar.toggle(key="bgm_on")
    check(not at.exception and tg.disabled and any("assets/bgm" in (c.value or "") for c in at.sidebar.caption),
          "음원이 없으면 BGM 스위치 꺼짐 + 폴더 안내")
    bgm.BGM_DIR = tdir
    at.run()
    at.sidebar.toggle(key="bgm_on").set_value(True).run()
    sel = at.sidebar.selectbox(key="bgm_choice")
    check(not at.exception and sel.options[0].startswith("🎲") and "a_song.mp3" in sel.options and sel.value == bgm.RANDOM,
          f"음원이 있으면 켜고 고르기 (기본 랜덤) {sel.options}")
finally:
    bgm.BGM_DIR = _orig_dir
    for wd in created:
        shutil.rmtree(wd, ignore_errors=True)

shutil.rmtree(work, ignore_errors=True)
finish(fails)
