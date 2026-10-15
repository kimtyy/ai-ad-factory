"""
0초 커버: 이미 완성된 영상(final.mp4)의 맨 앞에 독립 커버 이미지(thumbnail.jpg) 1장을 짧은 무음 정지 클립으로 이어 붙인다.

렌더·TTS·자막 로직과 완전히 분리된 후처리다. 본문 영상은 한 글자도 건드리지 않고(자막·나레이션 그대로),
앞에 cover_sec초 길이의 '정지 화면 + 무음'이 붙을 뿐이라 씬 1 자막/나레이션이 훅 텍스트로 오염될 수 없다.
유튜브 쇼츠는 영상 안의 0초 프레임을 대표 화면으로 쓰기 때문에 이 한 장이 피드 썸네일이 된다.
"""
import logging
import os
import subprocess
from typing import Optional

import imageio_ffmpeg

logger = logging.getLogger("ad_factory.cover_clip")

COVER_SEC = 0.5
CLEAN_SUFFIX = "_clean"          # final.mp4 → final_clean.mp4 (커버 붙이기 전 원본 보관)


def prepend_cover(video_path: str, cover_path: str, sec: float = COVER_SEC, keep_clean: bool = True,
                  fps: int = 24) -> bool:
    """video_path 맨 앞에 cover_path를 sec초 무음 정지 클립으로 붙여 video_path를 제자리 교체. 성공 여부 반환(실패 시 원본 유지)."""
    if not (video_path and cover_path and os.path.exists(video_path) and os.path.exists(cover_path)) or sec <= 0:
        return False
    try:
        from core.media.normalize import probe_media
        info = probe_media(video_path)
        w, h = int(info.width) // 2 * 2, int(info.height) // 2 * 2
        if w <= 0 or h <= 0:
            return False
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        tmp = os.path.splitext(video_path)[0] + "._cover.mp4"
        fmt = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
        # 입력: 0=커버(정지), 1=무음 0.5초, 2=본문 영상 (본문에 오디오가 없으면 무음 트랙을 만들어 길이를 맞춘다)
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-loop", "1", "-framerate", str(fps), "-t", f"{sec:.3f}", "-i", cover_path,
               "-f", "lavfi", "-t", f"{sec:.3f}", "-i", "anullsrc=r=44100:cl=stereo",
               "-i", video_path]
        vf = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},format=yuv420p[c];"
              f"[2:v]scale={w}:{h},setsar=1,fps={fps},format=yuv420p[v];")
        if info.has_audio:
            af = f"[1:a]{fmt}[sa];[2:a]{fmt}[ba];[c][sa][v][ba]concat=n=2:v=1:a=1[vo][ao]"
        else:
            af = (f"[1:a]{fmt}[sa];anullsrc=r=44100:cl=stereo,atrim=0:{info.duration:.3f},{fmt}[ba];"
                  f"[c][sa][v][ba]concat=n=2:v=1:a=1[vo][ao]")
        cmd += ["-filter_complex", vf + af, "-map", "[vo]", "-map", "[ao]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        if res.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            logger.warning(f"0초 커버 붙이기 실패 (원본 영상 유지): {res.stderr[-300:]}")
            if os.path.exists(tmp):
                os.remove(tmp)
            return False
        if keep_clean:
            clean = os.path.splitext(video_path)[0] + CLEAN_SUFFIX + os.path.splitext(video_path)[1]
            os.replace(video_path, clean)
        os.replace(tmp, video_path)
        logger.info(f"0초 커버 {sec:.1f}초 앞에 붙임: {cover_path} → {video_path}")
        return True
    except Exception as e:           # 커버는 부가 기능: 어떤 오류도 영상 자체를 막지 않는다
        logger.warning(f"0초 커버 붙이기 오류 (원본 영상 유지): {type(e).__name__}: {e}")
        return False


def apply_cover_if_enabled(video_path: str, cover_path: Optional[str]) -> bool:
    """INTRO_COVER 정책(기본 ON)에 따라 완성된 영상에 커버를 붙인다. 호출 위치는 항상 '영상이 완전히 끝난 뒤'."""
    from core.mode_policy import INTRO_COVER_SEC, intro_cover_enabled
    if not intro_cover_enabled() or not cover_path:
        return False
    return prepend_cover(video_path, cover_path, sec=INTRO_COVER_SEC)
