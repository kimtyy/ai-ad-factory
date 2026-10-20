import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import subprocess
import threading
import time
import numpy as np
import imageio_ffmpeg
from PIL import Image

import core.render.ffmpeg_assemble as fa
from core.compositor import AdCompositor
from core.media.imgutil import MAX_SIDE, open_rgb_downscaled
from core.render.brand import BrandOverlay

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


ff = imageio_ffmpeg.get_ffmpeg_exe()
work = tmp("memlimit_")

# ---------------------------------------------------------------- 1. 고해상도 사진 사전 축소 (긴 변 2560px 이하, EXIF 방향 보정)
big = os.path.join(work, "big.jpg")
im = Image.fromarray((np.random.rand(300, 400, 3) * 255).astype("uint8")).resize((4000, 3000), Image.BICUBIC)
exif = Image.Exif()
exif[274] = 6                                   # 오른쪽으로 90도 회전된 사진 (스마트폰 세로 촬영)
im.save(big, quality=92, exif=exif)
small = open_rgb_downscaled(big)
check(max(small.size) <= MAX_SIDE and small.size == (1920, 2560) and small.mode == "RGB",
      f"4000x3000 원본 → 긴 변 {MAX_SIDE}px 이하로 축소 + EXIF 회전 반영 ({small.size})")
png = os.path.join(work, "small.png")
Image.new("RGB", (800, 600), (10, 120, 200)).save(png)
check(open_rgb_downscaled(png).size == (800, 600), "이미 작은 사진은 그대로")
prev = open_rgb_downscaled(big, 768)
check(max(prev.size) <= 768, f"미리보기·비전 분석용은 긴 변 768px 이하 ({prev.size})")

# ---------------------------------------------------------------- 2. ffmpeg 조립: 오버레이는 -loop 없는 한 장 입력, 컷이 둘이면 프로세스를 따로 (한 번에 영상 하나만 디코딩), 저메모리는 스레드 1개
black = os.path.join(work, "black.mp4")
black2 = os.path.join(work, "black2.mp4")
aud = os.path.join(work, "a.m4a")
for p_, c_ in ((black, "black"), (black2, "0x303030")):
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={c_}:s=1080x1920:r=24:d=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", p_], check=True)
subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=3.0", "-c:a", "aac", aud], check=True)
brand = BrandOverlay(business_name="효정명인갈비")
man = [{"scene_index": 1, "video_path": black, "extra_videos": [black2], "audio_path": aud, "caption": "주말 외식 장소로 나중에 찾으면 늦어요, 지금 저장하고 검색해 보세요!"},
       {"scene_index": 2, "video_path": black, "audio_path": aud, "caption": "둘째 장면이에요"}]
cmds = []
sampled = {}
_orig_popen = subprocess.Popen


def spy_popen(cmd, *a, **k):
    pr = _orig_popen(cmd, *a, **k)
    cmds.append(list(cmd))
    sampled[pr.pid] = 0
    return pr


stop = False


def sample():
    if os.name != "nt":
        return
    import ctypes
    import ctypes.wintypes as wt

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("a", ctypes.c_size_t), ("b", ctypes.c_size_t), ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", ctypes.c_size_t), ("f", ctypes.c_size_t)]
    pmc = PMC()
    pmc.cb = ctypes.sizeof(pmc)
    handles = {}
    while not stop:
        for pid in list(sampled):
            h = handles.get(pid) or ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0400, False, pid)
            handles[pid] = h
            if h and ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                sampled[pid] = max(sampled[pid], pmc.PeakWorkingSetSize)
        time.sleep(0.03)


os.environ["RENDER_LOW_MEMORY"] = "1"
subprocess.Popen = spy_popen
fa.subprocess.Popen = spy_popen
th = threading.Thread(target=sample, daemon=True)
th.start()
try:
    out = AdCompositor(output_dir=os.path.join(work, "out")).assemble_ad(man, output_filename="m.mp4", mode="owner", brand=brand)
finally:
    stop = True
    th.join(timeout=3)
    subprocess.Popen = _orig_popen
    fa.subprocess.Popen = _orig_popen
    os.environ.pop("RENDER_LOW_MEMORY", None)

dec = [c for c in cmds if "-f" in c and "rawvideo" in c and c[-1] == "-" and "-filter_complex" in c]
loops_in_scene_cmds = [c for c in dec if "-loop" in c and "-framerate" in c and not any("endcard" in str(x) for x in c)]
check(os.path.exists(out) and len(dec) >= 3 and not loops_in_scene_cmds,
      f"씬 디코딩 {len(dec)}회(컷 2개 씬은 컷마다 따로), 오버레이 PNG는 '-loop' 없는 한 장 입력")
check(all("eof_action=repeat" in " ".join(c) and "eof_action=pass" not in " ".join(c) for c in dec if "overlay" in " ".join(c)),
      "오버레이는 eof_action=repeat (마지막 프레임 유지)")
check(all(c[c.index("-threads") + 1] == "1" for c in cmds if "-threads" in c and ("rawvideo" in c)),
      "저메모리 환경: 디코더·인코더 ffmpeg 스레드 1개")
if os.name == "nt":
    peak = max(sampled.values() or [0]) / 1048576
    check(0 < peak < 450, f"조립 중 ffmpeg 프로세스 하나의 최대 메모리 {peak:.0f}MB < 450MB (이전: 오버레이 반복 입력으로 약 850MB)")

# ---------------------------------------------------------------- 3. 저메모리 판별: Streamlit Cloud 경로(/mount/src)만 있어도 저메모리
_orig_exists = os.path.exists
os.path.exists = lambda p_: True if str(p_) == "/mount/src" else _orig_exists(p_)
try:
    low_cloud = fa.low_memory()
finally:
    os.path.exists = _orig_exists
check(low_cloud and fa.ffmpeg_threads() in (1, 2), "Streamlit Cloud(/mount/src)에서는 cgroup 값을 못 읽어도 저메모리 모드")

# ---------------------------------------------------------------- 4. 12MP 사진으로 Ken Burns 클립 생성 (축소 디코딩 후 정상 렌더)
from core.asset_generator import AssetGenerator
gen = AssetGenerator(cache_dir=os.path.join(work, "assets"), mock=True, mode="owner")
sc1 = {"scene_index": 1, "duration_sec": 2.0, "voiceover": "테스트", "video_motion_prompt_en": "zoom_in"}
img = gen.generate_image(sc1, force=True, gallery_image_path=big)
clip = gen.generate_video(sc1, img, force=True)
saved = Image.open(img)
info = subprocess.run([ff, "-hide_banner", "-i", clip], capture_output=True, text=True).stderr
check(max(saved.size) <= MAX_SIDE and "1080x1920" in info, f"12MP 원본으로도 씬 이미지 {saved.size}로 줄여 저장하고 1080x1920 클립 생성")

finish(fails)
