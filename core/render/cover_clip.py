"""
0초 커버: 이미 완성된 영상(final.mp4)의 맨 앞에 독립 커버 이미지(thumbnail.jpg) 1장을 짧은 무음 정지 클립으로 이어 붙인다.

렌더·TTS·자막 로직과 완전히 분리된 후처리다. 본문 영상은 한 글자도 건드리지 않고(자막·나레이션 그대로),
앞에 cover_sec초 길이의 '정지 화면 + 무음'이 붙을 뿐이라 씬 1 자막/나레이션이 훅 텍스트로 오염될 수 없다.
유튜브 쇼츠는 영상 안의 0초 프레임을 대표 화면으로 쓰기 때문에 이 한 장이 피드 썸네일이 된다.

메모리: 원본을 한 번에 필터 그래프로 올리지 않고 raw 프레임으로 풀어 인코더에 흘려 보낸다 (클라우드 1GB 한도 대응).
"""
import logging
import os
import re
import subprocess
from typing import Optional

import imageio_ffmpeg

logger = logging.getLogger("ad_factory.cover_clip")

COVER_SEC = 0.5
CLEAN_SUFFIX = "_clean"          # final.mp4 → final_clean.mp4 (커버 붙이기 전 원본 보관)


def _probe_fps(ffmpeg: str, path: str, default: float = 24.0) -> float:
    res = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    m = re.search(r"Video:.*?,\s*(\d+(?:\.\d+)?)\s*fps", res.stderr or "")
    try:
        return float(m.group(1)) if m else default
    except ValueError:
        return default


def prepend_cover(video_path: str, cover_path: str, sec: float = COVER_SEC, keep_clean: bool = True) -> bool:
    """video_path 맨 앞에 cover_path를 sec초 무음 정지 클립으로 붙여 video_path를 제자리 교체. 성공 여부 반환(실패 시 원본 유지)."""
    if not (video_path and cover_path and os.path.exists(video_path) and os.path.exists(cover_path)) or sec <= 0:
        return False
    tmp = os.path.splitext(video_path)[0] + "._cover.mp4"
    wav = os.path.splitext(video_path)[0] + "._cover.wav"
    try:
        from core.media.normalize import probe_media
        from core.render.ffmpeg_assemble import encoder_args
        info = probe_media(video_path)
        w, h = int(info.width) // 2 * 2, int(info.height) // 2 * 2
        if w <= 0 or h <= 0:
            return False
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        fps = _probe_fps(ffmpeg, video_path)
        n_cover = max(1, int(round(sec * fps)))
        frame_bytes = w * h * 3 // 2

        # 커버 1프레임 (raw yuv420p)
        res = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", cover_path, "-frames:v", "1",
                              "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p",
                              "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"], capture_output=True)
        cover_frame = res.stdout
        if res.returncode != 0 or len(cover_frame) != frame_bytes:
            logger.warning("0초 커버 프레임 변환 실패 (원본 영상 유지)")
            return False

        # 오디오: 무음 sec초 + 원본 오디오
        fmt = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
        a_cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-t", f"{sec:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
        if info.has_audio:
            a_cmd += ["-i", video_path, "-filter_complex", f"[0:a]{fmt}[s];[1:a]{fmt}[b];[s][b]concat=n=2:v=0:a=1[a]", "-map", "[a]"]
        else:
            a_cmd += ["-f", "lavfi", "-t", f"{info.duration:.3f}", "-i", "anullsrc=r=44100:cl=stereo",
                      "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]"]
        a_cmd += ["-c:a", "pcm_s16le", wav]
        ares = subprocess.run(a_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        if ares.returncode != 0 or not os.path.exists(wav):
            logger.warning(f"0초 커버 오디오 준비 실패 (원본 영상 유지): {(ares.stderr or '')[-200:]}")
            return False

        total = info.duration + sec
        from core.render.ffmpeg_assemble import ffmpeg_threads
        dec = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-threads", str(ffmpeg_threads()), "-i", video_path, "-an",
                                "-vf", f"scale={w}:{h},setsar=1,format=yuv420p", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        enc = subprocess.Popen([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-threads", str(ffmpeg_threads()),
                                "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}", "-r", f"{fps:g}", "-i", "-", "-i", wav,
                                "-map", "0:v", "-map", "1:a", *encoder_args("libx264"), "-force_key_frames", "0",
                                "-pix_fmt", "yuv420p", "-r", f"{fps:g}", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                                "-t", f"{total:.3f}", tmp], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for _ in range(n_cover):
                enc.stdin.write(cover_frame)
            while True:
                buf = dec.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    break
                enc.stdin.write(buf)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                enc.stdin.close()
            except OSError:
                pass
            dec.stdout.close()
            dec.wait()
        err = enc.stderr.read().decode("utf-8", "ignore")
        rc = enc.wait()
        if rc != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            logger.warning(f"0초 커버 붙이기 실패 (원본 영상 유지): {err[-300:]}")
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
    finally:
        for leftover in (tmp, wav):
            try:
                if os.path.exists(leftover):
                    os.remove(leftover)
            except OSError:
                pass


def apply_cover_if_enabled(video_path: str, cover_path: Optional[str]) -> bool:
    """INTRO_COVER 정책(기본 ON)에 따라 완성된 영상에 커버를 붙인다. 호출 위치는 항상 '영상이 완전히 끝난 뒤'."""
    from core.mode_policy import INTRO_COVER_SEC, intro_cover_enabled
    if not intro_cover_enabled() or not cover_path:
        return False
    return prepend_cover(video_path, cover_path, sec=INTRO_COVER_SEC)
