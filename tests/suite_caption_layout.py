import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import re
import subprocess
import numpy as np
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

from core.compositor import AdCompositor
from core.media.normalize import normalize_ugc_clip
from core.mode_policy import OWNER_POLICY, get_mode_policy
from core.render.caption_chunks import chunk_windows, split_caption

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


ff = imageio_ffmpeg.get_ffmpeg_exe()
work = tmp("caption_layout_")
comp = AdCompositor(output_dir=os.path.join(work, "final"))
H, W = 1920, 1080

# ---------------------------------------------------------------- 1. 정책: 하단 배치·폰트 축소·폭·대본 길이
check(OWNER_POLICY.center_y == 0.55 and 0.063 <= OWNER_POLICY.caption_font_ratio <= 0.065 and OWNER_POLICY.caption_chunk_chars == 18
      and OWNER_POLICY.max_chars == 30 and OWNER_POLICY.landscape_fg_center_y == 0.43 and OWNER_POLICY.center_y <= 0.82,
      "매장 정책: 자막 중심 55%(실제 쇼츠 앱 하단 UI 60~90% 위), 폰트 약 13.5% 축소(0.074→0.064), 18자 초과 문장은 덩어리 자막, 대본 문장 최대 30자, 가로 원본 중심 43%")
check(get_mode_policy("travel").center_y == 0.50 and get_mode_policy("travel").caption_chunk_chars == 0 and get_mode_policy("travel").landscape_fg_center_y == 0.50,
      "다른 모드(숙소 등)는 기존 자막 위치·방식 그대로")

# ---------------------------------------------------------------- 2. 덩어리 나누기: 18자 초과만, 쉼표 기준 앞/뒤, 어느 덩어리도 2줄 이내
cta = "주말 외식 장소로 나중에 찾으면 늦어요, 지금 저장하고 수원 동해바다를 검색해 보세요!"
ch = comp.caption_chunks(cta, "owner", W)
check(ch == ["주말 외식 장소로 나중에 찾으면 늦어요,", "지금 저장하고 수원 동해바다를 검색해 보세요!"],
      f"긴 문장(CTA)은 쉼표 기준 앞/뒤 두 덩어리: {ch}")
check(comp.caption_chunks("짧은 문장이에요.", "owner", W) == ["짧은 문장이에요."] and comp.caption_chunks(cta, "travel", W) == [cta],
      "18자 이하 문장·다른 모드는 나누지 않음")
draw = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
font = ImageFont.truetype(comp.font_path, int(W * OWNER_POLICY.caption_font_ratio))
samples = [cta, "수원 외식 메뉴 고민될 땐, 동해바다 모둠회 한 상이면 끝납니다!", "한우 갈비가 숯불 위에서 노릇하게 익어가요.",
           "눈꽃버섯 갈비탕에 격자 칼집 낸 생갈비까지 한 상에 올라와요, 가족 외식으로 딱입니다!"]
worst = max(len(comp._wrap_text(c, draw, font, int(W * OWNER_POLICY.caption_max_width))) for t in samples for c in comp.caption_chunks(t, "owner", W))
check(worst <= 2, f"어느 덩어리도 화면에서 2줄을 넘지 않음 (최대 {worst}줄, 매장 정책 폭 {OWNER_POLICY.caption_max_width:.0%})")
wins = chunk_windows(ch, 4.0, 4.6)
check(abs(wins[0][0]) < 1e-6 and abs(wins[0][1] - wins[1][0]) < 1e-6 and abs(wins[1][1] - 4.6) < 1e-6 and 1.6 < wins[0][1] < 2.2,
      f"표시 시간: 음성 길이를 글자 수 비율로 나누고 마지막 덩어리는 씬 끝까지 {[(round(a, 2), round(b, 2)) for a, b in wins]}")

# ---------------------------------------------------------------- 3. 실제 렌더(ffmpeg 조립): 검은 배경 위에서 자막이 하단 영역에만, 한 번에 2줄 이내, 앞→뒤로 바뀜
black = os.path.join(work, "black.mp4")
aud = os.path.join(work, "a.m4a")
subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=24:d=8", "-c:v", "libx264", "-pix_fmt", "yuv420p", black], check=True)
subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=4.0", "-c:a", "aac", aud], check=True)
man = [{"scene_index": 1, "video_path": black, "audio_path": aud, "caption": cta, "subtitle": cta}]
out = comp.assemble_ad(man, output_filename="chunks.mp4", mode="owner")


def frame(t):
    raw = subprocess.run([ff, "-v", "error", "-ss", f"{t:.3f}", "-i", out, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(int)


def text_rows(fr):
    rows = np.where((fr.min(axis=2) > 200).sum(axis=1) > 6)[0]
    return rows


f1, f2 = frame(0.9), frame(3.2)
r1, r2 = text_rows(f1), text_rows(f2)
line_h = int(W * OWNER_POLICY.caption_font_ratio * 1.28)
for label, rows in (("앞 덩어리", r1), ("뒤 덩어리", r2)):
    ok = len(rows) > 0 and rows.min() / H >= 0.45 and rows.max() / H <= 0.65 and (rows.max() - rows.min()) <= 2 * line_h + 30
    check(ok, f"{label}: 자막이 화면 45~65% 구간(중심 55%)에만, 높이 {(rows.max() - rows.min()) if len(rows) else 0}px ≤ 2줄({2 * line_h}px)")
check(float(np.abs(f1 - f2).mean()) > 1.0, "앞 덩어리 → 뒤 덩어리로 자막이 바뀜 (한 번에 다 띄우지 않음)")

# ---------------------------------------------------------------- 4. 가로 영상: 흐린 배경 위 원본 중심이 43% (정중앙 50%가 아님)
land = os.path.join(work, "land.mp4")
subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x909090:s=640x360:r=24:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", land], check=True)
cl = os.path.join(work, "cl.mp4")
normalize_ugc_clip(land, cl, 2.0, fg_center_y=OWNER_POLICY.landscape_fg_center_y)
raw = subprocess.run([ff, "-v", "error", "-ss", "0.5", "-i", cl, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
g = np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(int)[:, W // 2, :].mean(axis=1)       # 배경은 밝기를 낮춰 놓아서 원본 영역이 더 밝음
fg = np.where(g > g.max() - 4)[0]
center = (fg.min() + fg.max()) / 2 / H
cl50 = os.path.join(work, "cl50.mp4")
normalize_ugc_clip(land, cl50, 2.0)
raw = subprocess.run([ff, "-v", "error", "-ss", "0.5", "-i", cl50, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
g50 = np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(int)[:, W // 2, :].mean(axis=1)
fg50 = np.where(g50 > g50.max() - 4)[0]
center50 = (fg50.min() + fg50.max()) / 2 / H
check(abs(center - 0.43) < 0.015 and abs(center50 - 0.50) < 0.015,
      f"가로 영상: 매장 모드는 원본 중심 {center:.3f}(≈43%)로 올려 하단 자막 자리 확보, 기본값은 {center50:.3f}(정중앙)")

# ---------------------------------------------------------------- 5. 씬 전환(디졸브) 구간: 앞뒤 자막이 겹쳐 보이지 않음, 워터마크는 끊기지 않음 (ffmpeg·MoviePy 두 경로)
import core.render.ffmpeg_assemble as fa
from core.render.brand import BrandOverlay
brand = BrandOverlay(business_name="효정명인갈비")


def two_scene(name, cap1, cap2, force_moviepy=False):
    c = AdCompositor(output_dir=os.path.join(work, name))
    m = [{"scene_index": 1, "video_path": black, "audio_path": aud, "caption": cap1, "subtitle": cap1},
         {"scene_index": 2, "video_path": black, "audio_path": aud, "caption": cap2, "subtitle": cap2}]
    if force_moviepy:
        orig = fa.assemble
        fa.assemble = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("force moviepy"))
        try:
            return c.assemble_ad(m, output_filename=f"{name}.mp4", mode="owner", brand=brand)
        finally:
            fa.assemble = orig
    return c.assemble_ad(m, output_filename=f"{name}.mp4", mode="owner", brand=brand)


def fr_at(path, t):
    raw = subprocess.run([ff, "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(int)


for label, mp in (("ffmpeg 조립", False), ("MoviePy 대체", True)):
    with_c1 = two_scene(f"x_{int(mp)}", "첫째 장면이에요", "둘째 장면이에요", mp)
    no_c1 = two_scene(f"y_{int(mp)}", "", "둘째 장면이에요", mp)
    # 첫 씬 길이(음성 4.0 + 꼬리 0.2 + 디졸브 0.35 = 4.55초) 끝의 디졸브 구간(4.2~4.55초): 이전 자막은 이미 꺼져 있어야 함 → 첫 자막이 있든 없든 같은 화면
    t_mid = 4.38
    d_mid = float(np.abs(fr_at(with_c1, t_mid) - fr_at(no_c1, t_mid)).mean())
    d_before = float(np.abs(fr_at(with_c1, 3.2) - fr_at(no_c1, 3.2)).mean())
    mid = fr_at(with_c1, t_mid)
    wm_top = int((mid[: int(H * 0.2), int(W * 0.5):].max(axis=2) > 120).sum())           # 워터마크(우측 상단) 밝은 픽셀
    wm_early = int((fr_at(with_c1, 1.0)[: int(H * 0.2), int(W * 0.5):].max(axis=2) > 120).sum())
    check(d_mid < 0.2 and d_before > 1.0, f"[{label}] 디졸브 중 이전 자막이 이미 꺼져 있음 (자막 유무 차이 {d_mid:.2f}), 그 전엔 보임 ({d_before:.2f})")
    check(wm_early > 50 and wm_top > 50, f"[{label}] 워터마크는 씬 전환에서도 끊기지 않음 (전환 중 {wm_top}px ≈ 평소 {wm_early}px)")

# ---------------------------------------------------------------- 6. 자막 위치 선택: 기본 55%, 상단 여백 24%(가로 사진 중심 52%)
import os as _os2
from core.compositor import AdCompositor as _AC
_os2.environ.pop("OWNER_CAPTION_POS", None)
check(get_mode_policy("owner").center_y == 0.55, "기본 자막 위치 55% (채널명·제목·AI 라벨이 올라오는 하단 60~90% 위)")
_os2.environ["OWNER_CAPTION_POS"] = "top"
_top = get_mode_policy("owner")
check(_top.center_y == 0.24 and _top.landscape_fg_center_y == 0.52 and get_mode_policy("travel").center_y == 0.50, "상단 여백 선택: 자막 24%, 가로 사진 중심 52%, 다른 모드는 그대로")
def _rows(ov):
    a = np.array(ov)[..., 3] > 40 if np.array(ov).shape[-1] == 4 else None
    ys = np.where(a.any(axis=1))[0]
    return ys.min() / H, ys.max() / H
_cap = "젓가락도 심심하지 않아요!"
_t0, _t1 = _rows(_AC(output_dir=tmp("cappos_")).create_overlay_image(_cap, None, W, H, mode="owner"))
_os2.environ.pop("OWNER_CAPTION_POS", None)
_m0, _m1 = _rows(_AC(output_dir=tmp("cappos_")).create_overlay_image(_cap, None, W, H, mode="owner"))
check(abs((_m0 + _m1) / 2 - 0.55) < 0.02 and abs((_t0 + _t1) / 2 - 0.24) < 0.02, f"실제 자막 그림 위치: 기본 중심 {(_m0+_m1)/2:.2f}, 상단 {(_t0+_t1)/2:.2f}")

finish(fails)
