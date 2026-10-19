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
check(OWNER_POLICY.center_y == 0.72 and 0.063 <= OWNER_POLICY.caption_font_ratio <= 0.065 and OWNER_POLICY.caption_chunk_chars == 18
      and OWNER_POLICY.max_chars == 30 and OWNER_POLICY.landscape_fg_center_y == 0.43 and OWNER_POLICY.center_y <= 0.82,
      "매장 정책: 자막 중심 72%(82% 이하), 폰트 약 13.5% 축소(0.074→0.064), 18자 초과 문장은 덩어리 자막, 대본 문장 최대 30자, 가로 원본 중심 43%")
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
    ok = len(rows) > 0 and rows.min() / H >= 0.62 and rows.max() / H <= 0.82 and (rows.max() - rows.min()) <= 2 * line_h + 30
    check(ok, f"{label}: 자막이 화면 62~82% 구간에만, 높이 {(rows.max() - rows.min()) if len(rows) else 0}px ≤ 2줄({2 * line_h}px), 위쪽 절반엔 자막 없음")
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

finish(fails)
