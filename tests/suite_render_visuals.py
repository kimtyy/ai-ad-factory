"""렌더 시각 요소: 하단 단일 자막 블록(위치·다크 필), 커머스 레이아웃 유지, Ken Burns 줌 폭·속도·나레이션 길이 연동"""
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

from core.asset_generator import KB_ZOOM, AssetGenerator, _audio_seconds, kb_ease
from core.compositor import CAPTION_BLOCK_BOTTOM, AdCompositor

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
top_h, bot_h = alpha_rows(hook)
top_c, bot_c = alpha_rows(cap)
check(bot_h <= CAPTION_BLOCK_BOTTOM + 0.02 and bot_c <= CAPTION_BLOCK_BOTTOM + 0.02 and top_h > 0.45 and top_c > 0.6,
      f"씬 1(헤드라인+자막) {top_h:.2f}~{bot_h:.2f}, 일반 씬 {top_c:.2f}~{bot_c:.2f}: 중앙~하단 한 블록, 하단 25% UI 영역 침범 없음")
mid_c = (top_c + bot_c) / 2
check(0.62 <= mid_c <= 0.72, f"일반 씬 자막 중심 {mid_c:.2f} = 화면 하단 30~35% 지점")
yellow = (hook[:, :, 0] > 230) & (hook[:, :, 1] > 190) & (hook[:, :, 2] < 60) & (hook[:, :, 3] > 200)
ys = np.where(yellow.any(axis=1))[0] / H
check(len(ys) and ys.min() > 0.5 and not ((cap[:, :, 0] > 230) & (cap[:, :, 2] < 60) & (cap[:, :, 3] > 200)).any(),
      f"노란 후킹 헤드라인은 자막 블록 안({ys.min():.2f}~)에만, 상단 별도 배너 없음")
# 다크 필: 글자 위 여백(패딩)에도 반투명 어두운 배경이 깔려 있어야 밝은 배경에서 읽힘
px = cap[int(top_c * H) + 8, W // 2]
shadow_tail = alpha_rows(cap, min_alpha=1)[1]
check(130 <= px[3] <= 200 and px[:3].max() < 40 and shadow_tail < 0.8,
      f"자막 뒤 반투명 다크 필 (alpha {px[3]}, RGB {tuple(int(v) for v in px[:3])}), 그림자 끝 {shadow_tail:.2f}")

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

shutil.rmtree(work, ignore_errors=True)
finish(fails)
