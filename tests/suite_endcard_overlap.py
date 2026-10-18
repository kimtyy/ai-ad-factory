import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import subprocess
import numpy as np
import imageio_ffmpeg
from PIL import Image

from core.compositor import AdCompositor
from core.render.brand import BrandOverlay, EndCard
import core.render.ffmpeg_assemble as fa

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


ff = imageio_ffmpeg.get_ffmpeg_exe()
work = tmp("endcard_")


def sh(*a):
    subprocess.run([ff, "-y", "-v", "error", *a], check=True)


vid, aud = os.path.join(work, "s.mp4"), os.path.join(work, "s.m4a")
sh("-loop", "1", "-i", fixture("photo_room.jpg"), "-t", "3", "-r", "24", "-vf", "scale=1080:1920:out_range=tv,setsar=1,format=yuv420p", vid)
sh("-f", "lavfi", "-i", "sine=frequency=440:duration=2.0", "-c:a", "aac", aud)
brand = BrandOverlay(business_name="효정명인갈비", end_card=EndCard(search_keyword="가평 효정명인갈비", sub_text="경기 가평군", duration_sec=2.5))
LONG = "나중에 찾으려면 늦으니 지금 저장하고 가평 효정명인갈비를 검색해 보세요!"


def render(name, caption, force_moviepy=False):
    comp = AdCompositor(output_dir=os.path.join(work, name))
    man = [{"scene_index": 1, "video_path": vid, "audio_path": aud, "caption": "첫 장면이에요", "subtitle": "첫 장면이에요"},
           {"scene_index": 2, "video_path": vid, "audio_path": aud, "caption": caption, "subtitle": caption}]
    if force_moviepy:
        orig = fa.assemble
        fa.assemble = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("force moviepy"))      # ffmpeg 경로 실패 → MoviePy 대체 경로
        try:
            return comp.assemble_ad(man, output_filename=f"{name}.mp4", mode="owner", brand=brand)
        finally:
            fa.assemble = orig
    return comp.assemble_ad(man, output_filename=f"{name}.mp4", mode="owner", brand=brand)


def frame(path, t):
    raw = subprocess.run([ff, "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)


def duration(path):
    err = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    import re
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", err)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


for label, mp in (("ffmpeg 조립", False), ("MoviePy 대체", True)):
    with_cap = render(f"cap_{int(mp)}", LONG, mp)
    no_cap = render(f"nocap_{int(mp)}", "", mp)
    d = duration(with_cap)
    # 엔딩 카드가 완전히 올라온 구간(끝 1.5초 전): 마지막 씬 자막 유무와 상관없이 같은 화면이어야 함 (자막이 카드 위·뒤에 남지 않음)
    t_card = d - 1.5
    diff = float(np.abs(frame(with_cap, t_card) - frame(no_cap, t_card)).mean())
    # 카드 직전(마지막 씬 끝 0.5초 전)에는 자막이 보임 (자막이 아예 사라진 게 아님을 확인)
    t_scene = d - 2.5 - 0.5 - 0.35
    d_scene = float(np.abs(frame(with_cap, t_scene) - frame(no_cap, t_scene)).mean())
    check(diff < 1.0, f"[{label}] 엔딩 카드 화면에 마지막 씬 자막이 겹치지 않음 (자막 유무 화면 차이 {diff:.2f})")
    # 카드가 페이드인되는 중간: 자막은 이미 끝나 있어야 함 (전환 화면에서도 자막이 비치지 않음)
    t_x = d - 2.5 - 0.35 + 0.17
    d_x = float(np.abs(frame(with_cap, t_x) - frame(no_cap, t_x)).mean())
    check(d_x < 1.0, f"[{label}] 카드 페이드인 중에도 마지막 씬 자막이 비치지 않음 (차이 {d_x:.2f})")
    check(d_scene > 3.0, f"[{label}] 카드 직전까지는 마지막 씬 자막이 보임 (차이 {d_scene:.2f})")

finish(fails)
