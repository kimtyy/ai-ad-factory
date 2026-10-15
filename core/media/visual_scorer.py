"""
core/media/visual_scorer.py - 하이라이트 후보 창의 '화면 품질' 점수 (OpenCV, 경량·결정적)

텍스트(STT)만 보고 고르면 말은 좋은데 화면이 흐리거나 어둡거나 흔들리는 구간이 뽑힌다. 이를 후보 창 단위로 걸러낸다.
전체 영상을 디코딩하지 않는다: 후보 창(15~22초)만 낮은 해상도(기본 폭 320px)·낮은 fps(기본 4)로 뽑아 분석한다.

측정 (창 안의 샘플 프레임 기준)
- 선명도: Laplacian 분산의 중앙값 (낮을수록 흐림 / 초점 나감)
- 조도: 그레이스케일 평균 밝기의 중앙값 (너무 어둡거나 날아간 화면)
- 흔들림·급패닝: 연속 프레임 간 위상 상관(phaseCorrelate)으로 전역 이동량을 구해
    pan_speed = 화면 폭 대비 초당 이동량의 중앙값 (급패닝), jitter = 이동량의 프레임 간 변화량 (좌우로 떨리는 흔들림)
- 컷: 프레임 간 평균 절대 차이가 큰 지점의 수 (창 안에서 장면이 바뀜)

결과 VisualScore.score(0~1)와 hard_reject(심각한 불량이면 후보에서 원천 제외) + 사유(reasons).
기준치는 모듈 상수로 노출되어 테스트·튜닝이 쉽다. 실제 촬영본 기준 보정은 아직 하지 않았다(합성 프레임으로만 검증).
"""
import glob
import logging
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("ad_factory.media.visual")

# ---- 샘플링
SAMPLE_FPS = 4.0               # 초당 분석 프레임 수 (프레임 간격 0.25초)
FRAME_WIDTH = 320              # 분석 해상도 폭
MIN_FRAMES = 6                 # 이보다 적으면 판단 보류(중립 점수, 불합격 없음)

# ---- 선명도 (폭 320 기준 Laplacian 분산)
BLUR_REJECT = 8.0              # 중앙값이 이보다 낮으면 심한 흐림 → 원천 제외
BLUR_GOOD = 60.0               # 이 이상이면 만점

# ---- 조도 (0~255)
DARK_REJECT = 20.0
DARK_GOOD = 70.0
BRIGHT_GOOD = 200.0
BRIGHT_REJECT = 245.0

# ---- 모션 (화면 폭 대비)
PAN_SOFT = 0.30                # 초당 이동량(폭 비율) — 이 정도면 steady 점수 절반 감점
PAN_REJECT = 0.90              # 급패닝 → 원천 제외
JITTER_SOFT = 0.030            # 프레임 간 이동량 변화(폭 비율) — 흔들림
JITTER_REJECT = 0.090          # 심한 흔들림 → 원천 제외
CUT_MAD = 45.0                 # 평균 절대 차이가 이 이상이면서
CUT_RESPONSE = 0.12            # 위상 상관 응답이 이보다 낮으면(= 이동으로 설명되지 않음) 장면 컷으로 본다. 응답이 높으면 빠른 패닝/흔들림
FLAT_STD = 3.0                 # 프레임 밝기 표준편차가 이보다 작으면 평평한 화면(암전 등): 이동 추정이 무의미해 건너뜀
CUT_PENALTY = 0.15
CUT_REJECT = 3                 # 창 안 컷이 이 이상이면 몽타주 → 원천 제외

W_SHARP, W_BRIGHT, W_STEADY = 0.4, 0.2, 0.4


@dataclass
class VisualScore:
    score: float                         # 0~1 (높을수록 좋음)
    hard_reject: bool = False            # True면 후보에서 원천 제외
    reasons: List[str] = field(default_factory=list)
    sharpness: float = 0.0               # Laplacian 분산 중앙값
    brightness: float = 0.0              # 평균 밝기 중앙값
    pan_speed: float = 0.0               # 폭/초
    jitter: float = 0.0                  # 폭 비율/프레임 간격
    cuts: int = 0
    frames: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _median(vals: Sequence[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def analyze_frames(frames: Sequence[Any], fps: float = SAMPLE_FPS) -> VisualScore:
    """그레이스케일 프레임 시퀀스(numpy uint8, 같은 크기) → VisualScore. 영상 파일과 무관한 순수 함수라 합성 프레임으로 테스트한다."""
    import cv2
    import numpy as np

    n = len(frames)
    if n < MIN_FRAMES:
        return VisualScore(score=0.5, reasons=["프레임이 부족해 판단 보류"], frames=n)

    laps = [float(cv2.Laplacian(f, cv2.CV_64F).var()) for f in frames]
    means = [float(f.mean()) for f in frames]
    sharp, bright = _median(laps), _median(means)

    h, w = frames[0].shape[:2]
    window = cv2.createHanningWindow((w, h), cv2.CV_32F)
    shifts: List[Tuple[float, float]] = []
    cuts = 0
    prev = frames[0]
    for cur in frames[1:]:
        mad = float(np.mean(np.abs(cur.astype(np.int16) - prev.astype(np.int16))))
        if float(prev.std()) >= FLAT_STD and float(cur.std()) >= FLAT_STD:
            (dx, dy), resp = cv2.phaseCorrelate(prev.astype(np.float32), cur.astype(np.float32), window)
            if mad > CUT_MAD and resp < CUT_RESPONSE:
                cuts += 1                           # 큰 변화인데 이동으로 설명되지 않음 → 장면 전환 (모션 통계에서 제외)
            elif resp >= CUT_RESPONSE * 0.5:
                shifts.append((dx / w, dy / w))     # 화면 폭 대비 비율
        elif mad > CUT_MAD:
            cuts += 1                               # 평평한 화면에서 갑자기 큰 변화 (암전↔장면)
        prev = cur

    mags = [math.hypot(dx, dy) for dx, dy in shifts]
    pan_speed = _median(mags) * fps
    jit = [math.hypot(shifts[i][0] - shifts[i - 1][0], shifts[i][1] - shifts[i - 1][1]) for i in range(1, len(shifts))]
    jitter = sum(jit) / len(jit) if jit else 0.0

    sharp_s = _clip((sharp - BLUR_REJECT) / (BLUR_GOOD - BLUR_REJECT))
    if bright < DARK_GOOD:
        bright_s = _clip((bright - DARK_REJECT) / (DARK_GOOD - DARK_REJECT))
    elif bright > BRIGHT_GOOD:
        bright_s = _clip((BRIGHT_REJECT - bright) / (BRIGHT_REJECT - BRIGHT_GOOD))
    else:
        bright_s = 1.0
    steady_s = 1.0 - _clip(0.5 * _clip(pan_speed / (2 * PAN_SOFT)) + 0.5 * _clip(jitter / (2 * JITTER_SOFT)))
    score = _clip(W_SHARP * sharp_s + W_BRIGHT * bright_s + W_STEADY * steady_s - min(0.45, CUT_PENALTY * cuts))

    reasons: List[str] = []
    reject = False
    if sharp < BLUR_REJECT:
        reject = True
        reasons.append(f"심한 흐림(선명도 {sharp:.1f} < {BLUR_REJECT:.0f})")
    elif sharp_s < 0.5:
        reasons.append(f"다소 흐림(선명도 {sharp:.1f})")
    if bright < DARK_REJECT:
        reject = True
        reasons.append(f"너무 어두움(밝기 {bright:.0f})")
    elif bright > BRIGHT_REJECT:
        reject = True
        reasons.append(f"과노출(밝기 {bright:.0f})")
    elif bright_s < 0.5:
        reasons.append(f"어둡거나 밝음(밝기 {bright:.0f})")
    if pan_speed > PAN_REJECT:
        reject = True
        reasons.append(f"급패닝(초당 화면 폭의 {pan_speed:.2f}배)")
    elif pan_speed > PAN_SOFT:
        reasons.append(f"빠른 패닝({pan_speed:.2f}폭/초)")
    if jitter > JITTER_REJECT:
        reject = True
        reasons.append(f"심한 흔들림(떨림 {jitter:.3f})")
    elif jitter > JITTER_SOFT:
        reasons.append(f"흔들림(떨림 {jitter:.3f})")
    if cuts >= CUT_REJECT:
        reject = True
        reasons.append(f"창 안에 장면 컷 {cuts}회")
    elif cuts:
        reasons.append(f"장면 컷 {cuts}회")

    return VisualScore(score=round(score, 3), hard_reject=reject, reasons=reasons, sharpness=round(sharp, 2),
                       brightness=round(bright, 1), pan_speed=round(pan_speed, 3), jitter=round(jitter, 4),
                       cuts=cuts, frames=n)


def load_gray_frames(video_path: str, start: float, end: float, fps: float = SAMPLE_FPS,
                     width: int = FRAME_WIDTH) -> List[Any]:
    """영상의 [start, end) 구간만 ffmpeg로 낮은 해상도·fps JPG로 뽑아 그레이스케일 numpy 배열 목록으로 읽는다 (회전·HDR 톤매핑 반영)."""
    import cv2
    from core.media.normalize import probe_media, _hdr_to_sdr_filter
    from core.render.ffmpeg_assemble import ffmpeg_exe

    info = probe_media(video_path)
    start = max(0.0, float(start))
    dur = max(0.1, min(float(end), info.duration) - start)
    vf = (_hdr_to_sdr_filter() + "," if info.is_hdr else "") + f"fps={fps},scale={width}:-2:flags=area"
    tmp_dir = tempfile.mkdtemp(prefix="adf_vis_")
    try:
        cmd = [ffmpeg_exe(), "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
               "-i", video_path, "-an", "-vf", vf, "-q:v", "3", os.path.join(tmp_dir, "f_%05d.jpg")]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode != 0:
            raise RuntimeError("프레임 추출 실패: " + res.stderr.decode("utf-8", "ignore")[-200:])
        out = []
        for p in sorted(glob.glob(os.path.join(tmp_dir, "f_*.jpg"))):
            img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                out.append(img)
        return out
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class VisualScorer:
    """
    영상 하나에 대한 창 단위 시각 점수기. highlight_selector의 visual_scorer로 그대로 넘긴다: scorer(start, end) → VisualScore.
    frame_loader(start, end) → 그레이스케일 프레임 목록 을 주입하면 영상 없이 테스트할 수 있다. 같은 창은 캐시한다.
    """

    def __init__(self, video_path: str = "", *, fps: float = SAMPLE_FPS, width: int = FRAME_WIDTH,
                 frame_loader: Optional[Callable[[float, float], List[Any]]] = None):
        self.video_path = video_path
        self.fps = fps
        self.width = width
        self._loader = frame_loader
        self._cache: Dict[Tuple[float, float], VisualScore] = {}
        self.calls = 0

    def score_window(self, start: float, end: float) -> VisualScore:
        key = (round(float(start), 1), round(float(end), 1))
        if key in self._cache:
            return self._cache[key]
        self.calls += 1
        try:
            frames = (self._loader(start, end) if self._loader
                      else load_gray_frames(self.video_path, start, end, self.fps, self.width))
            vs = analyze_frames(frames, self.fps)
        except Exception as e:                  # 프레임을 못 읽어도 후보를 죽이지 않는다 (판단 보류)
            logger.warning(f"[시각 점수 실패 → 판단 보류] {start:.1f}~{end:.1f}s: {e}")
            vs = VisualScore(score=0.5, reasons=[f"시각 분석 실패: {type(e).__name__}"])
        self._cache[key] = vs
        return vs

    __call__ = score_window


# ---------------------------------------------------------------- 정밀 컷 검출 (하이라이트 시작/끝 지점 보정용)
LOCAL_CUT_MAD = 28.0           # 구간이 짧아 오탐 위험이 작으므로 analyze_frames보다 느슨하게
LOCAL_CUT_RESPONSE = 0.20
LOCAL_CUT_HIST = 0.80          # 히스토그램 상관이 이보다 낮으면(= 화면 구성이 통째로 바뀜) 컷 후보
CUT_FPS = 30.0


def cut_times_from_frames(frames: Sequence[Any], t0: float, fps: float = CUT_FPS) -> List[float]:
    """연속 프레임에서 장면 컷의 시각(컷 직후 첫 프레임의 시각)을 반환. frames[k]의 시각 = t0 + k/fps"""
    import cv2
    import numpy as np
    out: List[float] = []
    if len(frames) < 2:
        return out
    h, w = frames[0].shape[:2]
    window = cv2.createHanningWindow((w, h), cv2.CV_32F)
    prev = frames[0]
    prev_hist = cv2.calcHist([prev], [0], None, [64], [0, 256])
    for k in range(1, len(frames)):
        cur = frames[k]
        mad = float(np.mean(np.abs(cur.astype(np.int16) - prev.astype(np.int16))))
        hist = cv2.calcHist([cur], [0], None, [64], [0, 256])
        hcorr = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL))
        resp = 1.0
        if float(prev.std()) >= FLAT_STD and float(cur.std()) >= FLAT_STD:
            _shift, resp = cv2.phaseCorrelate(prev.astype(np.float32), cur.astype(np.float32), window)
        is_cut = (mad > LOCAL_CUT_MAD and (resp < LOCAL_CUT_RESPONSE or hcorr < LOCAL_CUT_HIST)) or (hcorr < 0.55 and mad > 15)
        if is_cut:
            out.append(round(t0 + k / fps, 3))
        prev, prev_hist = cur, hist
    return out


def find_cuts(video_path: str, start: float, end: float, fps: float = CUT_FPS, width: int = FRAME_WIDTH) -> List[float]:
    """[start, end] 안의 장면 컷 시각(원본 기준 초). 짧은 구간을 30fps로 읽어 프레임 단위로 찾는다."""
    start = max(0.0, float(start))
    frames = load_gray_frames(video_path, start, float(end), fps=fps, width=width)
    return cut_times_from_frames(frames, start, fps)
