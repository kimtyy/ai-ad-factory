"""
core/render/bgm.py - 자체 음원(BGM) 자동 믹싱: 나레이션 구간 덕킹 + 시작 페이드인 + 끝(엔딩 카드) 페이드아웃

- 음원: 프로젝트 assets/bgm/ 의 mp3·wav·m4a (직접 만들었거나 권리를 가진 음원만. 남의 곡은 넣지 않음)
- 레벨: 음원마다 평균 음량을 한 번 재서(캐시) 나레이션 평균보다 BGM_FULL_BELOW_VOICE_DB 낮게 맞춤
  → 나레이션이 나오는 동안은 그 레벨의 DUCK_GAIN(32%, 약 -10dB)으로 내림. 덕킹은 실제 나레이션 위치(타임라인)로
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

DUCK_GAIN = 0.32                # 나레이션 중 BGM 배율 (32% ≈ -9.9dB → 나레이션보다 약 14dB 아래, 폰 스피커에서도 비트가 들림)
BGM_FULL_BELOW_VOICE_DB = 4.0   # 나레이션이 없을 때 BGM 평균 음량 = 나레이션 평균 - 4dB
ATTACK_SEC = 0.25               # 나레이션 시작 전부터 낮추기 시작
RELEASE_SEC = 0.40              # 나레이션 끝난 뒤 천천히 올림
MERGE_GAP_SEC = 0.8             # 이보다 짧은 나레이션 사이 틈은 이어서 계속 낮춤
FADE_IN_SEC = 0.3               # 비트가 살아 있는 지점부터 붙이므로 짧게 (첫 3초 텐션)
# 시작 지점 자동 탐색: 인트로 빌드업을 건너뛰고 드롭(저음 킥 + 전체 음량이 곡의 활성 레벨에 도달해 유지되는 첫 구간)부터
ENERGY_WIN_SEC = 0.5            # 에너지 측정 창
ENERGY_HOLD_SEC = 4.0           # 활성 레벨을 이만큼 유지해야 시작 후보
ENERGY_BASS_BELOW = 4.0         # 저음(40~150Hz) 활성 기준 = 곡 저음 상위 20% 레벨 - 4dB
ENERGY_FULL_BELOW = 3.0         # 전체 음량 활성 기준 = 곡 음량 상위 20% 레벨 - 3dB
ENERGY_SNAP_SEC = 2.0           # 후보 앞뒤 이 범위에서 저음이 가장 크게 올라서는 순간(드롭 첫 박)으로 맞춤
ENERGY_MIN_TAIL_SEC = 20.0      # 시작 후 남은 길이가 이보다 짧으면 처음부터
DROP_MIN_JUMP_DB = 6.0          # 드롭 후보: '다음 1초 저음 평균 - 이전 0.5초 평균'이 이 이상인 지점
DROP_MIN_GAP_SEC = 2.0          # 드롭 후보끼리 최소 간격 (같은 드롭의 연속 박을 하나로)
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


def signature_tracks() -> List[str]:
    """.env BGM_SIGNATURE_TRACKS(쉼표로 구분한 파일 이름) → 대시보드 목록 맨 앞에 ⭐로 표시"""
    return [s.strip() for s in os.getenv("BGM_SIGNATURE_TRACKS", "").split(",") if s.strip()]


def ordered_tracks(bgm_dir: Optional[str] = None) -> List[str]:
    sig = signature_tracks()
    tracks = list_tracks(bgm_dir)
    first = [t for name in sig for t in tracks if os.path.basename(t) == name]
    return first + [t for t in tracks if t not in first]


def display_name(track: str) -> str:
    """'Neon Skyline_5-2.wav' → 'Neon Skyline' (완료 화면 표시용: 확장자·테이크 번호 제거)"""
    stem = os.path.splitext(os.path.basename(track or ""))[0]
    return re.sub(r"_\d+-\d+$", "", stem).strip() or stem


def book_default_track(bgm_dir: Optional[str] = None) -> Optional[str]:
    """시골책방 기본 BGM (.env BGM_BOOK_TRACK = assets/bgm 안 파일 이름, 잔잔한 피아노 곡 권장). 없거나 파일이 없으면 None"""
    name = os.getenv("BGM_BOOK_TRACK", "").strip()
    if not name:
        return None
    return name if name in [os.path.basename(t) for t in list_tracks(bgm_dir)] else None


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


def _cache_get(path: str, key: str) -> Optional[float]:
    cache = os.path.join(os.path.dirname(path), ".bgmlevel.json")
    try:
        return json.load(open(cache, encoding="utf-8")).get(key)
    except Exception:
        return None


def _cache_put(path: str, key: str, value: float) -> None:
    cache = os.path.join(os.path.dirname(path), ".bgmlevel.json")
    try:
        data = json.load(open(cache, encoding="utf-8")) if os.path.exists(cache) else {}
    except Exception:
        data = {}
    data[key] = value
    try:
        json.dump(data, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def energy_start(path: str, use_cache: bool = True) -> float:
    """
    인트로를 건너뛴 시작 지점(초): 저음(40~150Hz)과 전체 음량이 둘 다 곡 자체의 활성 레벨(상위 20% - 4dB / - 3dB)에
    도달해 ENERGY_HOLD_SEC 동안 유지되는 첫 창 → 그 앞 ENERGY_SNAP_SEC 안에서 저음이 가장 크게 뛰는 50ms 지점(드롭 첫 박).
    활성 구간을 못 찾거나 뒤에 남는 길이가 짧으면 0. 결과는 음원 폴더 캐시에 저장
    """
    import numpy as np
    key = f"start:v2:{os.path.basename(path)}:{os.path.getsize(path)}:{int(os.path.getmtime(path))}"
    if use_cache:
        cached = _cache_get(path, key)
        if cached is not None:
            return float(cached)
    sr = 11025
    raw = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
                         capture_output=True, timeout=180).stdout
    sig = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    duration = len(sig) / sr
    win = int(sr * ENERGY_WIN_SEC)
    n = len(sig) // win
    start = 0.0
    if n >= int(2 * ENERGY_HOLD_SEC / ENERGY_WIN_SEC):
        frames = sig[:n * win].reshape(n, win)
        full = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
        freqs = np.fft.rfftfreq(win, 1 / sr)
        band = (freqs > 40) & (freqs < 150)
        bass = 10 * np.log10((np.abs(np.fft.rfft(frames * np.hanning(win), axis=1)) ** 2)[:, band].sum(axis=1) + 1e-12)
        bass_on = np.percentile(bass, 80) - ENERGY_BASS_BELOW
        full_on = np.percentile(full, 80) - ENERGY_FULL_BELOW
        hold = int(ENERGY_HOLD_SEC / ENERGY_WIN_SEC)
        cand = None
        for i in range(0, n - hold):
            if np.median(bass[i:i + hold]) >= bass_on and np.median(full[i:i + hold]) >= full_on:
                cand = i * ENERGY_WIN_SEC
                break
        if cand is not None and cand > 0:
            # 드롭 첫 박: 50ms 간격 저음 에너지(0.1초 창)에서 '다음 1초 평균 - 이전 0.5초 평균'이 가장 큰 지점.
            # 한 프레임 차이만 보면 빌드업 킥(드롭 직전)에 걸림 → 앞뒤 맥락으로 실제 드롭(직전 숨 고르기 뒤 저음 폭발)을 찾음
            hop, w2 = int(sr * 0.05), int(sr * 0.1)
            lo = max(0, int((cand - ENERGY_SNAP_SEC) * sr))
            hi = min(len(sig) - w2, int((cand + ENERGY_SNAP_SEC + 1.0) * sr))
            f2 = np.fft.rfftfreq(w2, 1 / sr)
            b2 = (f2 > 40) & (f2 < 150)
            pos = list(range(lo, hi, hop))
            env = np.array([10 * np.log10((np.abs(np.fft.rfft(sig[q:q + w2] * np.hanning(w2))) ** 2)[b2].sum() + 1e-12)
                            for q in pos])
            after, before = int(1.0 / 0.05), int(0.5 / 0.05)
            best, best_score = None, -1e9
            for k in range(before, len(env) - after):
                score = env[k:k + after].mean() - env[k - before:k].mean()
                if score > best_score:
                    best, best_score = k, score
            start = pos[best] / sr if best is not None else cand
        elif cand is not None:
            start = 0.0
    if duration - start < ENERGY_MIN_TAIL_SEC:
        start = 0.0
    start = round(float(start), 2)
    _cache_put(path, key, start)
    logger.info(f"BGM 시작 지점: '{os.path.basename(path)}' {start:.2f}초 (인트로 건너뜀, 곡 길이 {duration:.0f}초)")
    return start


_analysis_cache: Dict[str, Dict[str, object]] = {}


def _analyze(path: str) -> Dict[str, object]:
    """곡 전체 분석 (한 번만): 0.5초 창 전체 음량(dB), 50ms 간격 저음 에너지, 드롭 점수"""
    import numpy as np
    key = f"{path}:{os.path.getsize(path)}:{int(os.path.getmtime(path))}"
    if key in _analysis_cache:
        return _analysis_cache[key]
    sr = 11025
    raw = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
                         capture_output=True, timeout=180).stdout
    sig = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    win = int(sr * ENERGY_WIN_SEC)
    n = len(sig) // win
    frames = sig[:n * win].reshape(n, win)
    full = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    hop, w2 = int(sr * 0.05), int(sr * 0.1)
    f2 = np.fft.rfftfreq(w2, 1 / sr)
    band = (f2 > 40) & (f2 < 150)
    starts = np.arange(0, len(sig) - w2, hop)
    fr2 = np.stack([sig[s:s + w2] for s in starts]) * np.hanning(w2)
    env = 10 * np.log10((np.abs(np.fft.rfft(fr2, axis=1)) ** 2)[:, band].sum(axis=1) + 1e-12)
    after, before = int(1.0 / 0.05), int(0.5 / 0.05)
    cs = np.concatenate([[0.0], np.cumsum(env)])
    score = np.full(len(env), -1e9)
    k = np.arange(before, len(env) - after)
    score[k] = (cs[k + after] - cs[k]) / after - (cs[k] - cs[k - before]) / before
    out = {"sr": sr, "duration": len(sig) / sr, "full": full, "env_hop": 0.05, "drop_score": score}
    _analysis_cache[key] = out
    return out


def drop_candidates(path: str) -> List[Tuple[float, float]]:
    """곡 안의 드롭(저음이 크게 올라서는 지점) 후보 [(초, 점수dB)], 2초 안의 연속 박은 가장 센 것 하나만"""
    import numpy as np
    a = _analyze(path)
    score = a["drop_score"]
    order = np.argsort(score)[::-1]
    picked: List[int] = []
    gap = int(DROP_MIN_GAP_SEC / a["env_hop"])
    for k in order:
        if score[k] < DROP_MIN_JUMP_DB:
            break
        if all(abs(int(k) - q) >= gap for q in picked):
            picked.append(int(k))
    return sorted((round(q * a["env_hop"], 2), round(float(score[q]), 1)) for q in picked)


def window_energy(path: str, start: float, length: float) -> float:
    """start초부터 length초 동안 평균 음량(dB, 0.5초 창 RMS의 전력 평균)"""
    import numpy as np
    a = _analyze(path)
    full = a["full"]
    i0, i1 = int(start / ENERGY_WIN_SEC), int((start + length) / ENERGY_WIN_SEC)
    seg = full[i0:max(i0 + 1, i1)]
    return float(10 * np.log10(np.mean(10 ** (seg / 10)) + 1e-12))


def best_window_start(path: str, length: float, use_cache: bool = True) -> float:
    """
    영상 길이(length초)만큼 이어 들었을 때 평균 에너지가 가장 큰 드롭에서 시작 (곡 끝을 넘는 후보는 제외).
    첫 드롭 뒤 곧 브레이크다운이 오는 곡(예: Neon Skyline 31초 드롭은 15초 뒤 잔잔해짐)도 영상 끝까지 텐션 유지.
    후보가 없거나 다 넘치면 첫 드롭(energy_start) 방식
    """
    length = max(1.0, float(length))
    key = f"win:v1:{int(round(length))}:{os.path.basename(path)}:{os.path.getsize(path)}:{int(os.path.getmtime(path))}"
    if use_cache:
        cached = _cache_get(path, key)
        if cached is not None:
            return float(cached)
    dur = float(_analyze(path)["duration"])
    cands = [s for s, _ in drop_candidates(path) if s + length <= dur]
    if not cands:
        start = energy_start(path, use_cache=use_cache)
    else:
        start = max(cands, key=lambda s: (window_energy(path, s, length), -s))
    start = round(float(start), 2)
    _cache_put(path, key, start)
    logger.info(f"BGM 시작 지점(영상 {length:.0f}초 맞춤): '{os.path.basename(path)}' {start:.2f}초 "
                f"(이후 {length:.0f}초 평균 {window_energy(path, start, length):.1f}dB, 드롭 후보 {len(cands)}개)")
    return start


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
            voice_db: Optional[float] = None, duck: float = DUCK_GAIN, start_at: Optional[float] = None,
            info: Optional[Dict[str, object]] = None) -> str:
    """
    완성 영상 + BGM → video_out (영상은 복사, 오디오만 재인코딩). 실패하면 RuntimeError
    start_at: 음원 시작 지점(초). None이면 best_window_start(): 영상 길이만큼 평균 에너지가 가장 큰 드롭 자동 선택, 0이면 처음부터
    """
    if not bgm_path or not os.path.isfile(bgm_path):
        raise RuntimeError(f"BGM 음원 파일이 없어요: {os.path.basename(bgm_path or '')} (assets/bgm 폴더 확인)")
    probe = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", video_in], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore").stderr or ""
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe)
    if not m:
        raise RuntimeError(f"영상 길이를 읽지 못함: {video_in}")
    total = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    has_voice = "Audio:" in probe
    # 시작 지점: 영상 길이만큼 이어 들었을 때 가장 에너지가 큰 드롭 (start_at을 주면 그대로, 0이면 처음부터)
    offset = best_window_start(bgm_path, total) if start_at is None else max(0.0, float(start_at))
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
    cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-i", video_in,
           "-stream_loop", "-1", "-ss", f"{offset:.3f}", "-i", bgm_path,
           "-filter_complex", graph, "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-t", f"{total:.3f}", "-movflags", "+faststart", tmp]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if res.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 1000:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(f"BGM 믹싱 실패: {(res.stderr or '')[-300:]}")
    shutil.move(tmp, video_out)
    if info is not None:     # 실제로 적용된 내용 (완료 화면 표시용)
        info.update({"track": os.path.basename(bgm_path), "start": round(offset, 2), "gain_db": round(gain_db, 1), "duck": duck})
    logger.info(f"BGM 믹싱: '{os.path.basename(bgm_path)}' {offset:.2f}초부터 (평균 {bgm_db:.1f}dB → {gain_db:+.1f}dB 보정, 나레이션 {len(merged)}구간 "
                f"{duck:.0%}로 덕킹, 페이드인 {FADE_IN_SEC}s·아웃 {fo:.1f}s) → {video_out}")
    return video_out
