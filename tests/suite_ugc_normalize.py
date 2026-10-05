import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import sys
import subprocess

sys.path.insert(0, ROOT)
from core.media import probe_media, normalize_ugc_clip, extract_thumbnail
import imageio_ffmpeg

OUT = tmp("normalize_")
os.makedirs(OUT, exist_ok=True)


def count_frames(path):
    res = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", path, "-map", "0:v", "-f", "null", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    txt = res.stderr.decode("utf-8", "ignore")
    import re
    m = re.findall(r"frame=\s*(\d+)", txt)
    return int(m[-1]) if m else -1


failures = 0
cases = [
    # (파일, 요청 길이, 기대 fit 설명)
    ("phone_portrait.mov", 3.0, "세로(회전 메타) + VFR + 오디오 -> cover, 트림"),
    ("phone_landscape.mp4", 2.8, "가로 2초 -> blur, 슬로모션(1.4x)"),
    ("phone_landscape.mp4", 5.0, "가로 2초 -> blur, 반복 재생"),
    ("phone_hdr.mov", 2.0, "HDR(HLG 10bit) -> SDR 톤매핑"),
]
for i, (name, dur, desc) in enumerate(cases, 1):
    src = fixture(name)
    src_info = probe_media(src)
    dst = os.path.join(OUT, f"case{i}.mp4")
    normalize_ugc_clip(src, dst, duration=dur, start=0.5)
    out = probe_media(dst)
    frames = count_frames(dst)
    ok = (out.width, out.height) == (1080, 1920) and not out.has_audio and abs(out.duration - dur) < 0.1 \
        and abs(frames - round(dur * 24)) <= 1 and not out.is_hdr
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'} case{i}: {desc}")
    print(f"     입력 {src_info.width}x{src_info.height} rot={src_info.rotation:g} hdr={src_info.is_hdr} audio={src_info.has_audio} {src_info.duration:.2f}s"
          f" -> 출력 {out.width}x{out.height} audio={out.has_audio} {out.duration:.2f}s frames={frames} (기대 {round(dur * 24)})")
    extract_thumbnail(dst, os.path.join(OUT, f"case{i}_thumb.jpg"), at=1.0)

extract_thumbnail(fixture("phone_portrait.mov"), os.path.join(OUT, "src_portrait_thumb.jpg"))
finish(failures)