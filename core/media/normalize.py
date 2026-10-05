"""
core/media/normalize.py - 스마트폰 직접 촬영 영상(UGC) 정규화

사장님이 올린 MP4/MOV(가변 프레임, 회전 메타데이터, HEVC, HDR, 오디오 포함)를
타임라인에 바로 넣을 수 있는 규격 클립(1080x1920, 고정 24fps, H.264, 무음)으로 변환합니다.
- 회전: ffmpeg CLI 기본 autorotate로 표시 방향 그대로 처리
- 세로 영상: 화면을 꽉 채우도록 스케일 후 중앙 크롭 (cover)
- 가로/정사각 영상: 피사체가 잘리지 않도록 원본을 중앙에 두고 흐린 배경으로 채움 (blur)
- 가변 프레임(VFR) -> 고정 프레임(CFR), 오디오 제거(-an)
- HDR(HLG/PQ, 아이폰 기본 촬영): SDR(bt709)로 톤매핑하여 색 바램 방지
- 길이 부족: 1.5배 이내면 슬로모션으로 늘리고, 그보다 짧으면 반복 재생
"""
import os
import re
import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

import imageio_ffmpeg

logger = logging.getLogger("ad_factory.media")

VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".3gp", ".webm")
MAX_SLOWDOWN = 1.5          # 이 배율까지는 슬로모션으로 길이 보충, 초과 시 반복 재생
DEFAULT_TRIM_START = 0.5    # 촬영 시작 직후 흔들림 구간 건너뛰기
HDR_TRANSFERS = ("arib-std-b67", "smpte2084")


def is_video_file(path: Optional[str]) -> bool:
    return bool(path) and os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


@dataclass
class MediaInfo:
    duration: float
    width: int            # 회전 적용 후 표시 기준 가로
    height: int           # 회전 적용 후 표시 기준 세로
    rotation: float
    is_hdr: bool
    has_audio: bool

    @property
    def is_portrait(self) -> bool:
        return self.height >= self.width * 1.3


def _ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe_media(path: str) -> MediaInfo:
    """ffmpeg -i 출력에서 길이/해상도/회전/HDR/오디오 여부를 읽습니다 (번들 ffmpeg에 ffprobe가 없음)."""
    res = subprocess.run([_ffmpeg(), "-hide_banner", "-i", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    info = res.stderr.decode("utf-8", errors="ignore")

    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if not m:
        raise ValueError(f"영상 정보를 읽을 수 없습니다 (지원하지 않는 파일): {path}")
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

    video_line = next((ln for ln in info.splitlines() if "Video:" in ln), "")
    size = re.search(r",\s*(\d{2,5})x(\d{2,5})", video_line)
    if not size:
        raise ValueError(f"영상 스트림을 찾을 수 없습니다: {path}")
    width, height = int(size.group(1)), int(size.group(2))

    rot = re.search(r"rotation of (-?\d+(?:\.\d+)?) degrees", info)
    rotation = float(rot.group(1)) if rot else 0.0
    if round(abs(rotation)) % 180 == 90:
        width, height = height, width

    return MediaInfo(
        duration=duration,
        width=width,
        height=height,
        rotation=rotation,
        is_hdr=any(t in video_line for t in HDR_TRANSFERS),
        has_audio=" Audio:" in info,
    )


def _hdr_to_sdr_filter() -> str:
    return (
        "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
        "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
    )


def normalize_ugc_clip(
    src: str,
    dst: str,
    duration: float,
    start: float = DEFAULT_TRIM_START,
    width: int = 1080,
    height: int = 1920,
    fps: int = 24,
    fit: str = "auto"
) -> str:
    """
    UGC 영상을 [start, start+duration] 구간의 규격 클립으로 변환하여 dst에 저장합니다.
    fit: 'cover'(꽉 채워 크롭) | 'blur'(흐린 배경 채움) | 'auto'(세로면 cover, 아니면 blur)
    """
    info = probe_media(src)
    duration = max(float(duration), 0.5)
    start = max(float(start or 0.0), 0.0)
    if start >= info.duration - 0.5:
        start = 0.0
    available = info.duration - start

    input_opts = []
    speed_filter = ""
    if available >= duration:
        input_opts += ["-ss", f"{start:.3f}"]
    elif available * MAX_SLOWDOWN >= duration:
        input_opts += ["-ss", f"{start:.3f}"]
        speed_filter = f"setpts={duration / available:.4f}*PTS,"
    else:
        # 너무 짧은 클립은 처음부터 반복 재생
        input_opts += ["-stream_loop", "-1"]

    if fit == "auto":
        fit = "cover" if info.is_portrait else "blur"

    pre = (_hdr_to_sdr_filter() + ",") if info.is_hdr else ""
    if fit == "blur":
        graph = (
            f"[0:v]{pre}{speed_filter}split[a][b];"
            f"[a]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"gblur=sigma=40,eq=brightness=-0.08[bg];"
            f"[b]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={fps},format=yuv420p,setsar=1[v]"
        )
    else:
        graph = (
            f"[0:v]{pre}{speed_filter}scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},format=yuv420p,setsar=1[v]"
        )

    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    tmp = dst + ".part.mp4"
    cmd = [
        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        *input_opts, "-i", src,
        "-t", f"{duration:.3f}",
        "-filter_complex", graph, "-map", "[v]",
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-r", str(fps), "-fps_mode", "cfr",
        "-movflags", "+faststart",
        tmp,
    ]
    logger.info(
        f"[UGC 정규화] {os.path.basename(src)} ({info.width}x{info.height}, {info.duration:.1f}s, "
        f"rot={info.rotation:g}, hdr={info.is_hdr}) -> fit={fit}, start={start:.2f}s, len={duration:.2f}s"
    )
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(f"UGC 영상 정규화 실패: {res.stderr.decode('utf-8', errors='ignore')[-800:]}")
    os.replace(tmp, dst)  # 실패 시 반쯤 쓰인 파일이 캐시로 남지 않도록 원자적 교체
    return dst


def extract_thumbnail(src: str, dst: str, at: float = DEFAULT_TRIM_START, max_width: int = 1080) -> str:
    """갤러리 미리보기/키프레임용 대표 프레임 1장 추출 (회전·HDR 반영)"""
    info = probe_media(src)
    at = at if at < info.duration - 0.1 else 0.0
    pre = (_hdr_to_sdr_filter() + ",") if info.is_hdr else ""
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    cmd = [
        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{at:.3f}", "-i", src,
        "-frames:v", "1", "-vf", f"{pre}scale='min({max_width},iw)':-2", "-q:v", "3", dst,
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not os.path.exists(dst):
        raise RuntimeError(f"썸네일 추출 실패: {res.stderr.decode('utf-8', errors='ignore')[-500:]}")
    return dst
