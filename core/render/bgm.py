"""
core/render/bgm.py - 자체 음원(BGM) 자동 믹싱: 나레이션 구간 덕킹 + 시작 페이드인 + 끝(엔딩 카드) 페이드아웃

- 음원: 프로젝트 assets/bgm/ 의 mp3·wav·m4a (직접 만들었거나 권리를 가진 음원만. 남의 곡은 넣지 않음)
- 레벨: 음원마다 평균 음량을 한 번 재서(캐시) 나레이션 평균보다 BGM_FULL_BELOW_VOICE_DB 낮게 맞춤
  → 나레이션이 나오는 동안은 그 레벨의 DUCK_GAIN(20%, 약 -14dB)으로 내림. 덕킹은 실제 나레이션 위치(타임라인)로
     정확히 걸고, 앞뒤 ATTACK/RELEASE초 동안 부드럽게 오르내림. 나레이션 사이 짧은 틈(MERGE_GAP 미만)은 계속 낮춰 둠
     (씬 사이 0.2초마다 음악이 튀어 오르는 펌핑 방지)
- 짧은 곡은 반복, 긴 곡은 영상 길이에서 자름. 영상은 다시 인코딩하지 않음(-c:v copy), 오디오만 새로 섞음
- 최종 조립 엔진(ffmpeg/MoviePy)과 무관하게 완성 영상에 후처리로 적용
"""
import json
import logging
import os
import random
import re
import shutil
import subprocess
from typing import Dict, List, Optional, Sequence, Tuple

import imageio_ffmpeg

logger = logging.getLogger("ad_factory.render.bgm")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BGM_DIR = os.path.join(ROOT, "assets", "bgm")
BGM_EXTS = (".mp3", ".wav", ".m4a")
RANDOM = "__random__"

DUCK_GAIN = 0.20                # 나레이션 중 BGM 배율 (20% ≈ -14dB)
BGM_FULL_BELOW_VOICE_DB = 4.0   # 나레이션이 없을 때 BGM 평균 음량 = 나레이션 평균 - 4dB
ATTACK_SEC = 0.25               # 나레이션 시작 전부터 낮추기 시작
RELEASE_SEC = 0.40              # 나레이션 끝난 뒤 천천히 올림
MERGE_GAP_SEC = 0.8             # 이보다 짧은 나레이션 사이 틈은 이어서 계속 낮춤
FADE_IN_SEC = 1.0
FADE_OUT_SEC = 1.8              # 끝(엔딩 카드) 페이드아웃
DEFAULT_VOICE_DB = -18.6        # 나레이션 평균 음량 실측값 (Edge-TTS SunHi, 무음 모드 등 측정 불가일 때)


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def list_tracks(bgm_dir: Optional[str] = None) -> List[str]:
    bgm_dir = bgm_dir or BGM_DIR          # 호출 시점의 폴더 (기본값에 경로를 묶어 두면 바꿔도 반영 안 됨)
    if not os.path.isdir(bgm_dir):
        return []
    return sorted(os.path.join(bgm_dir, f) for f in os.listdir(bgm_dir)
                  if f.lower().endswith(BGM_EXTS) and not f.startswith("."))


def pick_track(choice: Optional[str], bgm_dir: Optional[str] = None, rng: Optional[random.Random] = None) -> Optional[str]:
    """choice: 파일 이름(또는 경로) / RANDOM / None·'' → 없음"""
    if not choice:
        return None
    tracks = list_tracks(bgm_dir)
    if not tracks:
        return None
    if choice == RANDOM:
        return (rng or random).choice(tracks)
    for t in tracks:
        if choice in (t, os.path.basename(t)):
            return t
    return None


def mean_volume_db(path: str) -> Optional[float]:
    """
    평균 음량(dB, volumedetect)을 믹싱과 같은 44.1kHz 스테레오로 바꾼 뒤 잼
    (ffmpeg는 모노를 스테레오로 바꿀 때 -3dB → 모노 나레이션·스테레오 음원을 원본 그대로 재면 상대 레벨이 3dB 어긋남).
    음원은 파일 옆 캐시(.bgmlevel.json)에 저장해 매번 다시 재지 않음
    """
    cache = os.path.join(os.path.dirname(path), ".bgmlevel.json")
    key = f"v2:{os.path.basename(path)}:{os.path.getsize(path)}:{int(os.path.getmtime(path))}"
    data: Dict[str, float] = {}
    is_track = os.path.basename(os.path.dirname(os.path.abspath(path))) == "bgm"
    if is_track and os.path.exists(cache):
        try:
            data = json.load(open(cache, encoding="utf-8"))
            if key in data:
                return data[key]
        except Exception:
            data = {}
    res = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path, "-af",
                          "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,volumedetect", "-vn", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=120)
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", res.stderr or "")
    if not m:
        return None
    val = float(m.group(1))
    if is_track:
        data[key] = val
        try:
            json.dump(data, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass
    return val


def merge_intervals(intervals: Sequence[Tuple[float, float]], gap: float = MERGE_GAP_SEC) -> List[Tuple[float, float]]:
    out: List[List[float]] = []
    for s, e in sorted((float(a), float(b)) for a, b in intervals if b > a):
        if out and s - out[-1][1] < gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def duck_expr(intervals: Sequence[Tuple[float, float]], duck: float = DUCK_GAIN,
              attack: float = ATTACK_SEC, release: float = RELEASE_SEC) -> str:
    """ffmpeg volume 식: 나레이션 구간 duck, 그 밖 1.0, 경계는 선형 램프 (구간마다 사다리꼴, 겹치면 max)"""
    if not intervals:
        return "1"
    parts = [f"clip((t-{s - attack:.3f})/{attack:.3f},0,1)*clip(({e + release:.3f}-t)/{release:.3f},0,1)" for s, e in intervals]
    d = parts[0]
    for p in parts[1:]:
        d = f"max({d},{p})"
    return f"1-{1 - duck:.3f}*({d})"


def narration_intervals(scene_audio: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """[(시작 시각, 길이)] → 나레이션 구간 (덕킹용)"""
    return [(s, s + d) for s, d in scene_audio if d and d > 0]


def mix_bgm(video_in: str, video_out: str, bgm_path: str, intervals: Sequence[Tuple[float, float]],
            voice_db: Optional[float] = None, duck: float = DUCK_GAIN) -> str:
    """완성 영상 + BGM → video_out (영상은 복사, 오디오만 재인코딩). 실패하면 RuntimeError"""
    if not bgm_path or not os.path.isfile(bgm_path):
        raise RuntimeError(f"BGM 음원 파일이 없어요: {os.path.basename(bgm_path or '')} (assets/bgm 폴더 확인)")
    probe = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", video_in], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore").stderr or ""
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe)
    if not m:
        raise RuntimeError(f"영상 길이를 읽지 못함: {video_in}")
    total = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    has_voice = "Audio:" in probe
    bgm_db = mean_volume_db(bgm_path)
    if bgm_db is None:
        raise RuntimeError(f"BGM 음량을 읽지 못함: {bgm_path}")
    target_db = (voice_db if voice_db is not None else DEFAULT_VOICE_DB) - BGM_FULL_BELOW_VOICE_DB
    gain_db = max(-40.0, min(20.0, target_db - bgm_db))
    merged = merge_intervals(intervals)
    fo = min(FADE_OUT_SEC, total / 3)
    chain = (f"[1:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=duration={total:.3f},"
             f"volume={gain_db:.2f}dB,volume='{duck_expr(merged, duck)}':eval=frame,"
             f"afade=t=in:st=0:d={min(FADE_IN_SEC, total / 3):.2f},afade=t=out:st={total - fo:.3f}:d={fo:.2f}[bgm]")
    if has_voice:
        graph = (chain + f";[0:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[vo];"
                 f"[vo][bgm]amix=inputs=2:normalize=0:duration=first:dropout_transition=0,alimiter=limit=0.95[aout]")
    else:
        graph = chain + ";[bgm]alimiter=limit=0.95[aout]"
    tmp = video_out + ".bgm.mp4"
    cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-i", video_in, "-stream_loop", "-1", "-i", bgm_path,
           "-filter_complex", graph, "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-t", f"{total:.3f}", "-movflags", "+faststart", tmp]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if res.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 1000:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(f"BGM 믹싱 실패: {(res.stderr or '')[-300:]}")
    shutil.move(tmp, video_out)
    logger.info(f"BGM 믹싱: '{os.path.basename(bgm_path)}' (평균 {bgm_db:.1f}dB → {gain_db:+.1f}dB 보정, 나레이션 {len(merged)}구간 "
                f"{duck:.0%}로 덕킹, 페이드인 {FADE_IN_SEC}s·아웃 {fo:.1f}s) → {video_out}")
    return video_out
