"""자체 음원 BGM 믹싱: 음원 고르기, 나레이션 구간 32% 덕킹, 페이드인·아웃, 반복, 음량 보정, 영상 무손실 복사, 실패 시 BGM 없이 저장, 사이드바 UI"""
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
from core.compositor import AdCompositor

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
EXPECT = 10 ** ((bgm.BGM_FULL_BELOW_VOICE_DB + 20 * np.log10(1 / bgm.DUCK_GAIN)) / 20)   # 나레이션 : 덕킹된 BGM ≈ 4.9배 (32%)

full = amp(2.6, 150)          # 나레이션 사이 1.2초 틈(> 0.8초) 가운데: BGM 원래 레벨
duck1, duck2 = amp(1.25, 150), amp(3.9, 150)
start, end = amp(0.04, 150, 0.06), amp(5.93, 150, 0.1)
lo_, hi_ = bgm.DUCK_GAIN * 0.8, bgm.DUCK_GAIN * 1.2
check(bgm.DUCK_GAIN == 0.32 and lo_ < duck1 / full < hi_ and lo_ < duck2 / full < hi_,
      f"나레이션 구간 BGM = 원래 레벨의 {duck1 / full:.0%}·{duck2 / full:.0%} (목표 32%, 약 -10dB: 폰 스피커에서도 비트가 들리게)")
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

# ---------------------------------------------------------------- 2-1. 시작 지점 자동 탐색 (인트로 건너뛰고 드롭부터)
drop_track = os.path.join(tdir, "drop_song.wav")
# 0~6초: 작은 고음 패드(인트로, 저음 없음) / 6초~: 60Hz 킥 + 큰 저음 (드롭) / 20초 이후까지 유지
sh("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=6", "-f", "lavfi",
   "-i", "sine=frequency=60:sample_rate=44100:duration=24", "-filter_complex",
   "[0:a]volume=0.05[i];[1:a]volume='0.9*(0.6+0.4*gt(mod(t,0.5),0.25))':eval=frame[d];[i][d]concat=n=2:v=0:a=1", drop_track)
st = bgm.energy_start(drop_track, use_cache=False)
check(5.7 <= st <= 6.3, f"인트로(0~6초) 건너뛰고 드롭 시작 6초 근처에서 시작 ({st:.2f}초)")
flat_track = os.path.join(tdir, "flat_song.wav")
sh("-f", "lavfi", "-i", "sine=frequency=80:sample_rate=44100:duration=30", "-af", "volume=0.5", flat_track)
check(bgm.energy_start(flat_track, use_cache=False) == 0.0, "처음부터 비트가 살아 있는 곡은 0초부터")
short_track = os.path.join(tdir, "short_intro.wav")
sh("-f", "lavfi", "-i", "sine=frequency=440:duration=10", "-f", "lavfi", "-i", "sine=frequency=60:duration=12", "-filter_complex",
   "[0:a]volume=0.05[i];[1:a]volume=0.9[d];[i][d]concat=n=2:v=0:a=1", short_track)
check(bgm.energy_start(short_track, use_cache=False) == 0.0, "드롭 뒤 남은 길이가 20초 미만이면 처음부터 (반복 시 어색함 방지)")
check(bgm.energy_start(drop_track) == st, "시작 지점은 음원 폴더 캐시에 저장 (다시 분석 안 함)")
drop_out = os.path.join(work, "drop_mix.mp4")
bgm.mix_bgm(silent_v, drop_out, drop_track, [], voice_db=VOICE_DB)
zero_out = os.path.join(work, "zero_mix.mp4")
bgm.mix_bgm(silent_v, zero_out, drop_track, [], voice_db=VOICE_DB, start_at=0)
dm, zm = pcm(drop_out), pcm(zero_out)
check(amp_of(dm, 1.0, 60) > amp_of(dm, 1.0, 440) * 5 and amp_of(zm, 1.0, 440) > amp_of(zm, 1.0, 60) * 5,
      "믹싱은 드롭부터(1초 지점에 60Hz 킥), start_at=0이면 인트로(440Hz)부터")
# 영상 길이 맞춤: 첫 드롭(6초) 뒤 8초 만에 브레이크다운(14~26초), 2차 드롭(26초~)은 영상 끝까지 유지 → 2차 드롭 선택
two_drop = os.path.join(tdir, "two_drop.wav")
sh("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=6",
   "-f", "lavfi", "-i", "sine=frequency=60:sample_rate=44100:duration=8",
   "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=12",
   "-f", "lavfi", "-i", "sine=frequency=60:sample_rate=44100:duration=30", "-filter_complex",
   "[0:a]volume=0.05[a];[1:a]volume='0.9*(0.6+0.4*gt(mod(t,0.5),0.25))':eval=frame[b];[2:a]volume=0.06[c];"
   "[3:a]volume='0.9*(0.6+0.4*gt(mod(t,0.5),0.25))':eval=frame[d];[a][b][c][d]concat=n=4:v=0:a=1", two_drop)
cands = [s for s, _ in bgm.drop_candidates(two_drop)]
first_drop = bgm.energy_start(two_drop, use_cache=False)
best20 = bgm.best_window_start(two_drop, 20.0, use_cache=False)
best6 = bgm.best_window_start(two_drop, 6.0, use_cache=False)
check(any(abs(c - 6) < 0.4 for c in cands) and any(abs(c - 26) < 0.4 for c in cands) and abs(first_drop - 6) < 0.4,
      f"곡 안의 드롭 후보 모두 찾기 (6초·26초) {cands}, 첫 드롭 {first_drop:.2f}초")
check(abs(best20 - 26) < 0.4 and bgm.window_energy(two_drop, best20, 20) > bgm.window_energy(two_drop, first_drop, 20) + 3,   # 전력 평균: 8초 큰 소리 / 20초 → 약 4dB 차
      f"20초 영상: 첫 드롭은 8초 뒤 잔잔해짐 → 끝까지 유지되는 2차 드롭 {best20:.2f}초 선택")
check(abs(best6 - 6) < 0.4 or abs(best6 - 26) < 0.4, f"6초 영상: 어느 드롭이든 6초 내내 꽉 참 → 드롭에서 시작 ({best6:.2f}초)")
s50 = bgm.best_window_start(two_drop, 50.0, use_cache=False)          # 곡 길이 56초: 26초에서 50초면 넘침
check(s50 + 50.0 <= 56.0 and abs(s50 - 6) < 0.4,
      "곡 끝을 넘어가는 시작 지점은 고르지 않음 (반복으로 이어 붙는 어색함 방지)")
auto_out = os.path.join(work, "two_drop_mix.mp4")
long_v = os.path.join(work, "long20.mp4")
sh("-f", "lavfi", "-i", "color=c=black:s=540x960:r=24:d=20", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", long_v)
bgm.mix_bgm(long_v, auto_out, two_drop, [], voice_db=VOICE_DB)
am = pcm(auto_out)
check(amp_of(am, 15.0, 60) > amp_of(am, 15.0, 440) * 5 and amp_of(am, 15.0, 60) > amp_of(am, 3.0, 60) * 0.7,
      "자동 믹싱(20초 영상): 영상 15초 지점도 킥이 그대로 (첫 드롭이었으면 브레이크다운)")
os.remove(two_drop)

os.environ["BGM_SIGNATURE_TRACKS"] = "c_song.m4a, b_song.wav"
check([os.path.basename(p) for p in bgm.ordered_tracks(tdir)][:3] == ["c_song.m4a", "b_song.wav", "a_song.mp3"]
      and bgm.signature_tracks() == ["c_song.m4a", "b_song.wav"], "시그니처 트랙(.env BGM_SIGNATURE_TRACKS)은 목록 맨 앞")
os.environ.pop("BGM_SIGNATURE_TRACKS")
for f in (drop_track, flat_track, short_track):
    os.remove(f)

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
    check(at.toggle(key="bgm_on_main").value is True and not at.toggle(key="bgm_on_main").disabled,
          "렌더 버튼 옆 '🎵 BGM 사용' 스위치도 켜짐으로 (사이드바와 같은 상태)")
    at.toggle(key="bgm_on_main").set_value(False).run()
    check(at.sidebar.toggle(key="bgm_on").value is False and at.toggle(key="bgm_on_main").value is False,
          "렌더 버튼 옆에서 끄면 사이드바도 꺼짐 (모바일: 사이드바를 안 열어도 됨)")
    at.toggle(key="bgm_on_main").set_value(True).run()
    check(at.sidebar.toggle(key="bgm_on").value is True and at.sidebar.selectbox(key="bgm_choice").value == bgm.RANDOM,
          "렌더 버튼 옆에서 켜면 사이드바도 켜지고 음원은 랜덤")

    # 렌더 후 완료 화면: 실제로 들어간 BGM 표시 (BGM 켬 / 끔 두 번)
    real_track = os.path.join(tdir, "Neon Skyline_5-2.wav")
    sh("-f", "lavfi", "-i", "sine=frequency=150:sample_rate=44100:duration=40", "-af", "volume=0.5", real_track)
    job = at.session_state.job
    srcs = [fixture("photo_pool.jpg"), fixture("photo_room.jpg")]
    at.session_state.uploaded_paths = [shutil.copy(p, os.path.join(job.uploads_dir, f"u{i}.jpg")) for i, p in enumerate(srcs)]
    at.sidebar.checkbox[0].check()
    at.text_input(key="owner_name").input("설맥 가평현리점")
    at.run()
    at.sidebar.selectbox(key="bgm_choice").set_value("Neon Skyline_5-2.wav").run()
    [b for b in at.button if "스토리보드 자동 기획" in b.label][0].click().run()
    [b for b in at.button if "영상 만들기" in b.label][0].click().run()
    created.append(job.workdir)
    st_ok = [s.value for s in at.success if "BGM:" in (s.value or "")]
    # 맨 앞 이모지(🎵)는 Streamlit 알림 아이콘으로 빠지므로 본문만 비교
    check(not at.exception and st_ok and "BGM: Neon Skyline (적용)" in st_ok[0] and "초부터" in st_ok[0],
          f"완료 화면: 실제로 섞인 BGM 표시 {st_ok[:1]}")
    at.toggle(key="bgm_on_main").set_value(False).run()
    [b for b in at.button if "영상 만들기" in b.label][0].click().run()
    off = [i.value for i in at.info if "BGM 꺼짐" in (i.value or "")]
    check(not at.exception and off and not [s for s in at.success if "BGM:" in (s.value or "")],
          "BGM을 끄고 만들면 완료 화면에 '🔇 BGM 꺼짐' (토글 상태가 아니라 실제 결과 기준)")
finally:
    bgm.BGM_DIR = _orig_dir
    for wd in created:
        shutil.rmtree(wd, ignore_errors=True)

shutil.rmtree(work, ignore_errors=True)
# 원본 wav + 배포용 mp3가 같이 있어도 한 곡 (원본 우선), mp3만 있으면 mp3
_dd = tmp("bgm_dedupe_")
for _n in ("Song_1-2.wav", "Song_1-2.mp3", "Only_2-2.mp3", ".hidden.mp3"):
    open(os.path.join(_dd, _n), "wb").write(b"x")
_lt = [os.path.basename(t) for t in bgm.list_tracks(_dd)]
check(_lt == ["Only_2-2.mp3", "Song_1-2.wav"], f"같은 곡의 wav·mp3는 한 곡으로 (원본 우선) {_lt}")

finish(fails)
