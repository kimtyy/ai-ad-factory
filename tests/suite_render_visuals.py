"""렌더 시각 요소: 하단 자막(위치·배경 박스 없음·외곽선+그림자), 씬 1 후킹→서브 시간차, 커머스 레이아웃 유지, Ken Burns"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import shutil
import logging
logging.disable(logging.WARNING)

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw

from core.asset_generator import HOOK_SOLO_SEC, KB_ZOOM, AssetGenerator, _audio_seconds, kb_ease
from core.compositor import CAPTION_CENTER_Y, CAPTION_MAX_WIDTH, AdCompositor

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


work = tmp("render_visuals_")
W, H = 1080, 1920
comp = AdCompositor(output_dir=os.path.join(work, "final"))


def alpha_rows(arr, min_alpha=100):
    """불투명도 min_alpha 이상인 행 범위 (블록 아래로 번지는 옅은 그림자는 제외)"""
    rows = np.where(arr[:, :, 3].max(axis=1) >= min_alpha)[0]
    return (rows.min() / H, rows.max() / H) if len(rows) else (None, None)


# ---------------------------------------------------------------- 1. 자막 블록 (travel/owner)
hook = comp.create_overlay_image("수영장이랑 바다 경계가 그냥 사라집니다", "우리나라 손꼽히는 인피니티풀", W, H, mode="travel")
cap = comp.create_overlay_image("침대에 누워서 바로 일출 직관 가능한 룸 컨디션", "", W, H, mode="owner")
hook_only = comp.create_overlay_image(None, "국내 손꼽히는 끝판왕 오션뷰 풀", W, H, mode="travel")
long_cap = comp.create_overlay_image("날씨 쌀쌀할 땐 통유리 실내 풀로 쏙 들어가면 끝납니다", None, W, H, mode="travel")
for name, arr in (("후킹 단독", hook_only), ("서브 자막", cap), ("긴 자막", long_cap)):
    t_, b_ = alpha_rows(arr)
    cols = np.where(arr[:, :, 3].max(axis=0) >= 100)[0] / W
    check(0.48 <= (t_ + b_) / 2 <= 0.52 and b_ < 0.65 and cols.min() >= 0.12 and cols.max() <= 0.88,
          f"{name}: 세로 중심 {(t_ + b_) / 2:.2f} (48~52%), 아래 끝 {b_:.2f} < 쇼츠 하단 UI(약 65%~), 가로 {cols.min():.2f}~{cols.max():.2f} (우측 버튼 열 회피)")
yellow = (hook_only[:, :, 0] > 230) & (hook_only[:, :, 1] > 190) & (hook_only[:, :, 2] < 60) & (hook_only[:, :, 3] > 200)
ys = np.where(yellow.any(axis=1))[0] / H
check(len(ys) and ys.min() > 0.4 and not ((cap[:, :, 0] > 230) & (cap[:, :, 2] < 60) & (cap[:, :, 3] > 200)).any(),
      f"노란 후킹 헤드라인은 중앙({ys.min():.2f}~)에만, 상단 별도 배너 없음")
# 배경 박스 없음: 글자 줄 사이 빈 공간·좌우 바깥은 투명, 글자 = 흰 본체 + 검은 외곽선 + 블러 그림자
a = cap[:, :, 3]
rows = np.where(a.max(axis=1) >= 200)[0]
row_band = cap[rows.min():rows.max() + 1]
cols = np.where(row_band[:, :, 3].max(axis=0) >= 200)[0]
outside = np.concatenate([row_band[:, :max(cols.min() - 40, 1), 3].ravel(), row_band[:, cols.max() + 40:, 3].ravel()])
opaque = cap[a >= 250][:, :3]
semi = cap[(a > 20) & (a < 200)][:, :3]
check(outside.max() == 0 and (opaque.min(axis=1) > 240).any() and (opaque.max(axis=1) < 30).any()
      and len(semi) and semi.max(axis=1).mean() < 40,
      "자막 배경 박스 제거: 글자 바깥 완전 투명, 흰 글자 + 검은 외곽선 + 반투명 검은 그림자")

# 줄바꿈: 어절 안 절단 없음 + 의미 덩어리 유지 (실측 '수영장 안 / 가보면', '바다 / 뷰가' 재발 방지)
from PIL import ImageFont
from core.compositor import CAPTION_FONT_RATIO, break_penalty
_d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
_f = ImageFont.truetype(comp.font_path, int(W * CAPTION_FONT_RATIO))
wrap = lambda t: comp._wrap_text(t, _d, _f, int(W * CAPTION_MAX_WIDTH))
samples = {
    "솔직히 오자마자 수영장 안 가보면 손해거든요": ("안 가보면",),
    "여기 오시면 바다랑 연결된 특급 바다 뷰가 펼쳐져요": ("바다 뷰가",),
    "근데 진짜 대박인 건 객실 침구가 대박 편해요": ("객실 침구가",),
    "침대에 누워서 바다 보고 있으면 시간 가는 줄 몰라요": ("보고 있으면",),
    "날씨 나쁠 땐 통유리 실내 풀로 들어가면 돼요": ("실내 풀로",),
}
bad = {}
for text, keep in samples.items():
    lines = wrap(text)
    if " ".join(lines) != text or not all(any(k in ln for ln in lines) for k in keep):
        bad[text] = lines
check(not bad, f"자막 줄바꿈: 어절 보존 + 의미 덩어리('안 가보면'·'바다 뷰가'·'보고 있으면') 한 줄 유지 {bad or ''}")
check(break_penalty("안", "가보면") > 1 and break_penalty("바다", "뷰가") > 1 and break_penalty("좋은데,", "날씨") < 0
      and break_penalty("건", "객실") < 0, "끊기 점수: 1음절 부사 뒤·1음절 명사 앞 감점, 쉼표·'~건' 뒤 가점")
check(all(len(wrap(t)) <= 3 for t in samples) and wrap("숙소 정보는 댓글에 여행 남겨주세요!") == ["숙소 정보는 댓글에", "여행 남겨주세요!"],
      "최대 3줄, 균형 있는 2줄 분할")

# 커머스 모드는 기존 레이아웃 유지 (씬 2 제품 누끼가 62% 지점이라 하단 블록으로 옮기지 않음)
ch = comp.create_overlay_image("자막", "아직도 건조함 참으세요?", W, H, mode="commerce")
cs = comp.create_overlay_image("48시간 촉촉! 속당김 끝", "", W, H, mode="commerce")
check(alpha_rows(ch)[1] < 0.3 and 0.55 < sum(alpha_rows(cs)) / 2 < 0.68, "커머스: 상단 17% 후킹 바 / 62% 자막 그대로")

# ---------------------------------------------------------------- 2. Ken Burns
src = os.path.join(work, "grid.png")
im = Image.new("RGB", (1500, 1200), (40, 40, 40))
ImageDraw.Draw(im).rectangle([700, 550, 800, 650], fill=(255, 255, 255))
im.save(src)
gen = AssetGenerator(cache_dir=os.path.join(work, "assets"), mock=True, mode="travel")


def frames(path):
    r = imageio_ffmpeg.read_frames(path)
    next(r)
    return [np.frombuffer(f, np.uint8).reshape(H, W, 3) for f in r]


def sq(fr):
    return int((fr[960, :, 0] > 128).sum())


for mt in ("zoom_in", "zoom_out"):
    out = os.path.join(work, f"{mt}.mp4")
    gen._create_ken_burns_video(src, 4.0, out, motion_type=mt, motion_sec=3.0)
    f = frames(out)
    w0, w1, w_end, w_hold = sq(f[0]), sq(f[24]), sq(f[71]), sq(f[-1])
    ratio = max(w0, w_end) / min(w0, w_end)
    share = abs(w1 - w0) / abs(w_end - w0)
    check(ratio >= 1.15 and (w_end > w0) == (mt == "zoom_in"),
          f"{mt}: 화면 노출 3초 안에 {ratio:.3f}배 (목표 1.15~1.2, 기존 실측 약 1.07)")
    check(share >= 0.45 and abs(w_hold - w_end) <= 1, f"{mt}: 첫 1초에 모션 {share:.0%} 진행(ease-out), 노출 이후 구도 유지")
check(abs(kb_ease(0.5) - 0.75) < 1e-9 and kb_ease(0) == 0 and kb_ease(1) == 1 and KB_ZOOM >= 1.15, "ease-out 곡선, 줌 상수 1.15 이상")

grad = os.path.join(work, "grad.png")   # 패닝 검증용: 위치마다 색이 다른 그라데이션
g = np.zeros((1200, 1500, 3), np.uint8)
g[:, :, 0] = np.linspace(0, 255, 1500, dtype=np.uint8)[None, :]
g[:, :, 1] = np.linspace(0, 255, 1200, dtype=np.uint8)[:, None]
Image.fromarray(g).save(grad)
for mt in ("pan_right", "pan_left", "pan_up", "pan_down"):
    out = os.path.join(work, f"{mt}.mp4")
    gen._create_ken_burns_video(grad, 2.0, out, motion_type=mt, motion_sec=1.5)
    f = frames(out)
    diff = float(np.abs(f[0].astype(int) - f[35].astype(int)).mean())
    check(len(f) == 48 and diff > 1.0, f"{mt}: 1080x1920 {len(f)}프레임, 시작/끝 화면 차이 {diff:.1f}")

# 클립 길이 = 나레이션 길이 + 0.2(노출 테일) + 1.0(마지막 씬 여유)
scene = {"scene_index": 3, "duration_sec": 2.5, "video_motion_prompt_en": "zoom_in"}
voice = os.path.join(gen.cache_dir, "scene_03.mp3")
gen._create_mock_audio(2.0, voice)
vsec = _audio_seconds(voice)
clip = gen.generate_video(scene, src, force=True)
n = len(frames(clip))
check(vsec and abs(vsec - 2.0) < 0.15 and abs(n / 24 - (vsec + 1.2)) < 0.1,
      f"Ken Burns 클립 길이를 나레이션({vsec:.2f}초)에 맞춤 → {n / 24:.2f}초 (기존 고정 5초)")
os.utime(voice, None)
gen._create_mock_audio(3.0, voice)
n2 = len(frames(gen.generate_video(scene, src)))
check(n2 > n, "나레이션이 바뀌면 캐시 무시하고 모션 클립 재생성")

# ---------------------------------------------------------------- 2-1. TTS 앞뒤 무음 제거 (씬 사이 1초 넘는 공백 방지)
import subprocess
from core.asset_generator import TTS_KEEP_TAIL_SEC, trim_silence
padded = os.path.join(work, "padded.mp3")
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
                "-af", "adelay=200|200,apad=pad_dur=0.85", "-c:a", "libmp3lame", padded], check=True)
trimmed = trim_silence(padded, os.path.join(work, "trimmed.mp3"))
before, after = _audio_seconds(padded), _audio_seconds(trimmed)
check(abs(before - 3.05) < 0.1 and 2.0 <= after <= 2.0 + 0.05 + TTS_KEEP_TAIL_SEC + 0.08,
      f"Edge-TTS식 앞 0.2초·뒤 0.85초 무음 제거: {before:.2f}초 → {after:.2f}초 (말소리 2.0초는 보존)")

# ---------------------------------------------------------------- 2-2. Edge-TTS 잘린 응답 재시도
import core.asset_generator as ag
import core.tts as tts_mod
from core.asset_generator import tts_too_short
check(tts_too_short("근데 진짜 대박인 건 객실에서도 오션뷰가 쏟아지고,", 0.36) and not tts_too_short("근데 진짜 대박인 건 객실에서도 오션뷰가 쏟아지고,", 3.9)
      and not tts_too_short("네!", 0.3), "잘린 TTS 판정: 22글자에 0.36초 → 잘림, 3.9초 → 정상, 짧은 감탄사는 예외")
_calls = []


def _flaky_speech(text, output_path, **kw):
    _calls.append(1)
    gen._create_mock_audio(0.3 if len(_calls) == 1 else 2.5, output_path)   # 첫 응답만 잘린 파일
    return output_path


_orig_speech, tts_mod.generate_speech = tts_mod.generate_speech, _flaky_speech
real_gen = AssetGenerator(cache_dir=os.path.join(work, "flaky"), mode="travel")
fixed = real_gen._synthesize("근데 진짜 대박인 건 객실에서도 오션뷰가 쏟아지고,", os.path.join(work, "flaky", "s.mp3"), {"scene_index": 4})
tts_mod.generate_speech = _orig_speech
check(len(_calls) == 2 and (_audio_seconds(fixed) or 0) > 2.0, f"첫 응답이 잘리면 재시도해 정상 음성 확보 (호출 {len(_calls)}회)")

# ---------------------------------------------------------------- 2-2-1. 의문문 억양 (물음표 유지 + 숨 쉼표 후보 중 말끝 상승 최대 선택)
from core.tts import clean_tts_text, end_rise_percent, question_variants
check(clean_tts_text("여기 진짜 한국 맞아요?!") == "여기 진짜 한국 맞아요?" and clean_tts_text("솔직히 없거든요!") == "솔직히 없거든요"
      and clean_tts_text("이 가격에 이 뷰가 나온다고요??") == "이 가격에 이 뷰가 나온다고요?" and clean_tts_text("숙소 를 예약") == "숙소를 예약",
      "TTS 텍스트: 물음표 유지('?!'·'??' 정리), 느낌표·마침표 제거, '이 가격'처럼 다음 어절 첫 글자를 앞말에 붙이지 않음")
check(question_variants("여기 진짜 한국 맞아요?") == ["여기 진짜 한국 맞아요?", "여기 진짜 한국, 맞아요?", "여기 진짜, 한국 맞아요?"]
      and question_variants("솔직히 여기만 한 데가 없거든요.") == ["솔직히 여기만 한 데가 없거든요"],
      "의문문만 숨 쉼표 후보 3개 (평서문은 그대로 1개)")


def _tone(path, f0, f1):
    """f0 → f1 Hz로 변하는 배음 섞인 톤 (말소리 대용)"""
    expr = f"0.5*sin(2*PI*({f0}*t+({f1}-{f0})*t*t/4))+0.25*sin(4*PI*({f0}*t+({f1}-{f0})*t*t/4))"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"aevalsrc='{expr}':s=24000:d=2", "-c:a", "libmp3lame", path], check=True)
    return path


up_r = end_rise_percent(_tone(os.path.join(work, "up.mp3"), 200, 280))
down_r = end_rise_percent(_tone(os.path.join(work, "down.mp3"), 240, 170))
check(up_r is not None and down_r is not None and up_r > 15 and down_r < -15,
      f"말끝 억양 측정: 올라가는 톤 {up_r:+.0f}%, 내려가는 톤 {down_r:+.0f}%")
_spoken = []


def _fake_tts(text, output_path, **kw):
    _spoken.append(text)
    rising = text == "여기 진짜, 한국 맞아요?"      # 세 번째 후보만 말끝이 올라가는 응답
    return _tone(output_path, 200, 280) if rising else _tone(output_path, 240, 180)


_orig_tts, tts_mod.generate_speech = tts_mod.generate_speech, _fake_tts
qgen = AssetGenerator(cache_dir=os.path.join(work, "q"), mode="travel")
qout = qgen._synthesize("여기 진짜 한국 맞아요?", os.path.join(work, "q", "hook.mp3"), {"scene_index": 1})
tts_mod.generate_speech = _orig_tts
check(len(_spoken) == 3 and (end_rise_percent(qout) or 0) > 15, f"의문문: 후보 3개 합성 → 말끝이 올라가는 후보 선택 ({_spoken[-1]})")
_spoken.clear()
tts_mod.generate_speech = _fake_tts
qgen._synthesize("솔직히 여기만 한 데가 없거든요.", os.path.join(work, "q", "plain.mp3"), {"scene_index": 2})
tts_mod.generate_speech = _orig_tts
check(len(_spoken) == 1, "평서문은 1회만 합성 (추가 비용 없음)")

# ---------------------------------------------------------------- 2-3. 나레이션이 클립보다 길면 음성을 자르지 않고 화면을 늘림
long_voice = os.path.join(work, "long_voice.mp3")
gen._create_mock_audio(6.0, long_voice)
short_clip = os.path.join(work, "short_clip.mp4")
gen._create_ken_burns_video(src, 2.0, short_clip, motion_type="zoom_in", motion_sec=2.0)
synced = comp.assemble_ad([{"scene_index": 1, "video_path": short_clip, "audio_path": long_voice, "subtitle": "긴 나레이션", "hook_title": ""},
                           {"scene_index": 2, "video_path": short_clip, "audio_path": long_voice, "subtitle": "두 번째", "hook_title": ""}],
                          output_filename="sync.mp4", mode="travel")
from core.media import probe_media
sp = probe_media(synced)
check(abs(sp.duration - (6.0 + 0.2 + 6.0 + 1.0)) < 0.3,
      f"나레이션 6초 > 클립 2초: 음성 전부 재생, 화면은 마지막 프레임 유지로 연장 (총 {sp.duration:.2f}초 = 6.0+0.2 + 6.0+1.0)")

# ---------------------------------------------------------------- 3. 씬 1: 후킹 먼저 읽고 → 서브 문장 (음성·자막 시간차)
s1 = {"scene_index": 1, "duration_sec": 2.0, "hook_title": "국내 손꼽히는 끝판왕 오션뷰 풀",
      "voiceover": "바다랑 수영장이 그냥 이어져 있거든요", "video_motion_prompt_en": "zoom_in"}
v1 = gen.generate_voice(s1, force=True)
v1_sec = _audio_seconds(v1)
check(s1.get("hook_sec") == HOOK_SOLO_SEC and abs(v1_sec - (HOOK_SOLO_SEC + 2.0)) < 0.15,
      f"씬 1 2단 음성: 후킹 {s1.get('hook_sec')}초 + 서브 2.0초 = {v1_sec:.2f}초")
s2 = {"scene_index": 2, "duration_sec": 2.0, "hook_title": "", "voiceover": "x"}
gen.generate_voice(s2, force=True)
check("hook_sec" not in s2, "후킹 없는 씬은 1단 음성")
cg = AssetGenerator(cache_dir=os.path.join(work, "commerce"), mock=True, mode="commerce")
s3 = dict(s1)
s3.pop("hook_sec", None)
cg.generate_voice(s3, force=True)
check("hook_sec" not in s3, "커머스는 기존대로 후킹을 읽지 않음 (상단 후킹 바 유지)")

clip1 = gen.generate_video(s1, src, force=True)
final = comp.assemble_ad([{**s1, "video_path": clip1, "audio_path": v1, "subtitle": s1["voiceover"]}],
                         output_filename="s1.mp4", mode="travel")
ff = frames(final)


def yellow_px(fr):
    return int(((fr[:, :, 0] > 220) & (fr[:, :, 1] > 180) & (fr[:, :, 2] < 70)).sum())


def white_px(fr):
    return int((fr[int(H * 0.40):int(H * 0.60)].min(axis=2) > 235).sum())


early, late = ff[int(0.8 * 24)], ff[int((HOOK_SOLO_SEC + 0.8) * 24)]
check(yellow_px(early) > 2000 and yellow_px(late) < 200 and white_px(late) > white_px(early) + 1000,
      f"합성 결과: 0.8초엔 노란 후킹만({yellow_px(early)}px), {HOOK_SOLO_SEC + 0.8}초엔 후킹 사라지고 흰 서브 자막")

shutil.rmtree(work, ignore_errors=True)
finish(fails)
