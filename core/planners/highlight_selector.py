"""
core/planners/highlight_selector.py - 롱폼 전사(Transcript) → 1컨셉 하이라이트 후보 3개

흐름
  1) 문장 경계 기준으로 15~22초 창(窓)을 전부 만들고 컨셉 적합도로 점수화 → 비겹침 상위 6개(텍스트 후보)
  2) LLM이 있으면 6개 중 3개를 '세그먼트 ID'로만 고른다(초 계산은 코드). 키가 없거나 실패하면 결정적 폴백(점수순)
  3) 경계 보정: 문장 경계 → 무음 스냅(±0.5s) → 마진(앞 0.3s, 뒤 0.4s), 인접 발화를 침범하면 틈의 절반으로 클램프
  4) 발화 커버리지로 트랙 판정 A(≥60%) / C(30~60%) / B(<30%), 음악 지배 휴리스틱(music_suspected)
  5) STT 자막은 최소 정제만 하고, 가격·효능·개인정보는 '경고 플래그'만 단다 (원문 수정 금지)

LLM은 시간을 만들지 못한다: 응답의 start_id/end_id가 후보 메뉴에 없으면 버리고, 시간은 항상 코드가 계산한다.
"""
import logging
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.media.audio_stt import Sentence, Transcript
from core.script_guard import PRICE_CLAIM_PATTERNS, SPEC_CLAIM_PATTERNS, content_stems

logger = logging.getLogger("ad_factory.planners.highlight")

# ---- 시간 규격
WINDOW_MIN_SEC = 15.0
WINDOW_MAX_SEC = 22.0
CTA_SEC = 3.0
TOTAL_MAX_SEC = 25.0
MARGIN_BEFORE = 0.3
MARGIN_AFTER = 0.4
SNAP_SEC = 0.5
FADE_SEC = 0.1
MAX_INTERNAL_SILENCE = 6.0      # 창 안에 이보다 긴 무음이 있으면 후보에서 제외
SOFT_SILENCE = 2.5              # 이보다 긴 무음은 감점

# ---- 트랙 판정
TRACK_A_MIN = 0.60
TRACK_C_MIN = 0.30
MUSIC_RMS_DB = -30.0

POOL_SIZE = 6
TOP_K = 3
VISUAL_WEIGHT = 0.25            # 최종 점수 = (1-w)*텍스트 + w*시각
VISUAL_MAX_CHECKS = 24          # 시각 분석(영상 디코딩)을 해 볼 후보 창의 상한 (텍스트 점수 상위부터)
FILLER_STARTS = ("안녕", "어 ", "음 ", "자 ", "그 ", "저기", "여러분", "오늘은", "네 ", "아 ")


@dataclass
class HighlightCandidate:
    start_sec: float                       # 원본 영상 기준 (마진·스냅 반영)
    end_sec: float
    cta_sec: float
    track: str                             # "A" | "B" | "C"
    coverage: float                        # 창 안 발화 비율 0~1
    caption_scenes: List[Dict[str, Any]]   # [{start, end, text}] 창 시작점 기준 상대 시간 (0.0 기반)
    flags: List[Dict[str, Any]]            # [{type, text, at}] 경고만 (원문 수정 없음), at은 창 기준 초
    music_suspected: bool
    fade_in_sec: float = FADE_SEC
    fade_out_sec: float = FADE_SEC
    concept_score: float = 0.0
    visual_score: Optional[float] = None
    guard: Optional[Dict[str, Any]] = None   # 시작/끝 지점 컷 보정 기록 (이전·다음 장면 잔상 제거)
    first_word_sec: Optional[float] = None   # 첫 단어가 실제로 시작하는 원본 시각 (문장 시작보다 정확)
    video_hold_sec: float = 0.0              # 영상만 컷 직후 프레임을 이만큼 붙잡아 두고 오디오는 그대로 (인점 가드)
    visual: Optional[Dict[str, Any]] = None  # 시각 분석 상세 (선명도·밝기·패닝·떨림·컷·사유) — VisualScorer를 쓴 경우
    first_sentence: int = 0                # 문장 ID (Transcript.sentences 인덱스)
    last_sentence: int = 0
    source: str = "fallback"               # "llm" | "fallback"
    rank: int = 0
    reason: str = ""

    @property
    def window_sec(self) -> float:
        return round(self.end_sec - self.start_sec, 3)

    @property
    def total_sec(self) -> float:
        return round(self.window_sec + self.cta_sec, 3)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["window_sec"], d["total_sec"] = self.window_sec, self.total_sec
        return d


# ---------------------------------------------------------------- 트랙 / 경계
def classify_track(coverage: float) -> str:
    """발화 커버리지 → 트랙. ≥60% A(원본 유지) / 30~60% C(노이즈 제거) / <30% B(TTS 덮기)"""
    if coverage >= TRACK_A_MIN:
        return "A"
    if coverage >= TRACK_C_MIN:
        return "C"
    return "B"


def _containing_span(spans: Sequence[Tuple[float, float]], starts: List[float], t: float) -> Optional[Tuple[float, float]]:
    i = bisect_right(starts, t) - 1
    if i >= 0 and spans[i][0] <= t <= spans[i][1]:
        return spans[i]
    return None


def adjust_boundaries(raw_start: float, raw_end: float, spans: Sequence[Tuple[float, float]], duration: float,
                      margin_before: float = MARGIN_BEFORE, margin_after: float = MARGIN_AFTER,
                      snap: float = SNAP_SEC, presorted: bool = False) -> Tuple[float, float]:
    """
    문장 경계(raw) → 무음 스냅(±snap) → 마진. 마진이 이웃 발화를 침범하면 이웃과의 틈의 절반까지만 쓴다.
    - 스냅: 경계가 VAD 발화 구간 안쪽이고 그 구간 끝점(=무음 쪽)이 snap 이내면 그 끝점으로 이동
    - 경계가 발화 구간 한가운데(연속 발화)면 마진을 주지 않는다 (앞 문장 꼬리를 끌어오지 않음)
    spans는 서로 겹치지 않는 VAD 발화 구간 [(start, end)] (presorted=True면 정렬 생략)
    """
    sp = list(spans) if presorted else sorted(spans)
    starts = [x[0] for x in sp]
    ends = [x[1] for x in sp]

    s0, e0 = raw_start, raw_end
    cs = _containing_span(sp, starts, s0)
    if cs and 0 < s0 - cs[0] <= snap:
        s0 = cs[0]                                # 발화가 조금 더 일찍 시작: 그 시작점으로
    ce = _containing_span(sp, starts, e0)
    if ce and 0 < ce[1] - e0 <= snap:
        e0 = ce[1]                                # 발화가 조금 더 이어짐: 그 끝점으로

    # 시작 마진: 연속 발화 한가운데면 0, 아니면 직전 발화 끝과의 틈의 절반까지
    mid = _containing_span(sp, starts, s0)
    if mid and mid[0] < s0 - 1e-6:
        m_before = 0.0
    else:
        k = bisect_right(ends, s0 + 1e-6) - 1
        if k < 0:                                 # 앞에 발화가 없음: 영상 시작점까지만 쓸 수 있음
            m_before = min(margin_before, max(0.0, s0))
        else:
            m_before = min(margin_before, max(0.0, (s0 - ends[k]) / 2.0))
    # 끝 마진: 연속 발화 한가운데면 0, 아니면 다음 발화 시작과의 틈의 절반까지
    mid = _containing_span(sp, starts, e0)
    if mid and mid[1] > e0 + 1e-6:
        m_after = 0.0
    else:
        k = bisect_left(starts, e0 - 1e-6)
        if k >= len(starts):                      # 뒤에 발화가 없음: 영상 끝점까지만 쓸 수 있음
            m_after = min(margin_after, max(0.0, duration - e0))
        else:
            m_after = min(margin_after, max(0.0, (starts[k] - e0) / 2.0))
    return max(0.0, round(s0 - m_before, 3)), min(duration, round(e0 + m_after, 3))


# ---------------------------------------------------------------- 후보 탐색 / 점수
def _norm_text(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip())


@dataclass
class _Window:
    i: int
    j: int
    start: float
    end: float
    score: float
    detail: str = ""
    text_score: float = 0.0
    visual: Any = None                     # VisualScore (또는 None)


def _concept_keys(core_concept: str, usp: str, store_name: str) -> Tuple[set, set]:
    primary = content_stems(core_concept)
    secondary = (content_stems(usp) | content_stems(store_name)) - primary
    return primary, secondary


def _score_window(sents: List[Sentence], i: int, j: int, primary: set, secondary: set, tr: Transcript,
                  start: float, end: float) -> Tuple[float, str]:
    chunk = sents[i:j + 1]
    n = len(chunk)
    stems_per = [content_stems(s.text) for s in chunk]
    hit_sent = sum(1 for st in stems_per if primary & st)
    found = set().union(*stems_per) if stems_per else set()
    cov_stems = (len(primary & found) / len(primary)) if primary else 0.0
    sec_hits = sum(1 for st in stems_per if secondary & st)
    relevance = 0.50 * (hit_sent / n) + 0.35 * cov_stems + 0.15 * (sec_hits / n)
    if primary and primary & stems_per[0]:
        relevance += 0.10                          # 첫 문장이 컨셉으로 시작 → 훅이 될 가능성
    first = _norm_text(chunk[0].text)
    if len(first) <= 6 or first.startswith(FILLER_STARTS):
        relevance -= 0.10                          # 군말·인사로 시작
    # 내부 무음(죽은 공기) 감점
    longest = max((chunk[k + 1].start - chunk[k].end for k in range(n - 1)), default=0.0)
    if longest > SOFT_SILENCE:
        relevance -= min(0.15, 0.05 * (longest - SOFT_SILENCE))
    dens = tr.speech_coverage(start, end)
    relevance += 0.10 * dens                       # 말이 많은 구간 선호 (소폭)
    return round(relevance, 4), f"문장 {n}개 중 컨셉어 {hit_sent}개, 키워드 {len(primary & found)}/{len(primary)}"


def _enumerate_windows(tr: Transcript, primary: set, secondary: set) -> List[_Window]:
    sents = tr.sentences
    spans = sorted((sp.start, sp.end) for sp in tr.speech_spans)
    out: List[_Window] = []
    n = len(sents)
    for i in range(n):
        for j in range(i, n):
            raw_len = sents[j].end - sents[i].start
            if raw_len > WINDOW_MAX_SEC:
                break
            if raw_len < WINDOW_MIN_SEC - 1.0:     # 마진(+0.7) 반영해도 15초에 못 미침
                continue
            gaps = [sents[k + 1].start - sents[k].end for k in range(i, j)]
            if gaps and max(gaps) > MAX_INTERNAL_SILENCE:
                break
            a, b = adjust_boundaries(sents[i].start, sents[j].end, spans, tr.duration_sec, presorted=True)
            length = b - a
            if length < WINDOW_MIN_SEC or length > WINDOW_MAX_SEC:
                continue
            score, detail = _score_window(sents, i, j, primary, secondary, tr, a, b)
            out.append(_Window(i, j, a, b, score, detail))
    return out


def _as_visual(v: Any) -> Any:
    """시각 점수기의 반환을 VisualScore 형태로 통일: float(0~1)이면 불합격 없는 점수로 취급 (하위 호환)"""
    if hasattr(v, "score") and hasattr(v, "hard_reject"):
        return v
    from core.media.visual_scorer import VisualScore
    return VisualScore(score=max(0.0, min(1.0, float(v))))


def _visual_pool(windows: List[_Window], scorer: Optional[Callable[[float, float], Any]], pool_size: int) -> List[_Window]:
    """
    텍스트 점수 순으로 후보 창을 훑으며 시각 분석 → 심각한 불량(hard_reject)은 원천 제외, 나머지는 텍스트+시각 가중 합산.
    영상 디코딩 비용을 위해 분석 횟수는 VISUAL_MAX_CHECKS로 제한한다. 시각 점수기가 없으면 텍스트만으로 비겹침 상위 pool_size개.
    """
    if scorer is None:
        return _pick_non_overlapping(windows, pool_size)
    accepted: List[_Window] = []
    checks = 0
    for w in sorted(windows, key=lambda x: (-x.score, x.start)):
        if any(not (w.end <= a.start or w.start >= a.end) for a in accepted):
            continue
        if checks >= VISUAL_MAX_CHECKS:
            break
        checks += 1
        try:
            vs = _as_visual(scorer(w.start, w.end))
        except Exception as e:                    # 시각 분석 실패는 후보를 죽이지 않음 (텍스트 점수만)
            logger.debug(f"시각 점수 계산 실패(무시): {e}")
            vs = None
        if vs is not None and vs.hard_reject:
            logger.info(f"[시각 품질 제외] {w.start:.1f}~{w.end:.1f}s: {', '.join(vs.reasons)}")
            continue
        if vs is not None:
            w.visual = vs
            w.score = round((1.0 - VISUAL_WEIGHT) * w.score + VISUAL_WEIGHT * float(vs.score), 4)
        accepted.append(w)
        if len(accepted) >= pool_size:
            break
    accepted.sort(key=lambda x: (-x.score, x.start))
    return accepted


def _pick_non_overlapping(windows: Sequence[_Window], k: int) -> List[_Window]:
    chosen: List[_Window] = []
    for w in sorted(windows, key=lambda x: (-x.score, x.start)):
        if all(w.end <= c.start or w.start >= c.end for c in chosen):
            chosen.append(w)
            if len(chosen) >= k:
                break
    return chosen


# ---------------------------------------------------------------- 플래그 (경고만)
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")
_RRN = re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)")
_ACCOUNT = re.compile(r"(?<!\d)\d{2,6}-\d{2,6}-\d{2,8}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SPOKEN_ACCOUNT = re.compile(r"(계좌|입금).{0,12}\d{4,}")
_SPEC_WORDS = tuple(p for p in SPEC_CLAIM_PATTERNS if not p.startswith("\\d"))   # 숫자+단위 패턴은 '3초' 같은 일상 표현이 많아 제외


def scan_flags(text: str, at: float) -> List[Dict[str, Any]]:
    """STT 자막의 가격·효능·개인정보 표현을 찾아 경고만 반환한다 (원문은 수정하지 않는다)."""
    flags: List[Dict[str, Any]] = []
    t = text or ""

    def add(kind: str, m: str):
        flags.append({"type": kind, "text": m, "at": round(at, 2)})

    for pat in PRICE_CLAIM_PATTERNS:
        for m in re.finditer(pat, t):
            add("price_claim", m.group(0))
    for w in _SPEC_WORDS:
        if w in t:
            add("spec_claim", w)
    for kind, rx in (("privacy_phone", _PHONE), ("privacy_rrn", _RRN), ("privacy_account", _ACCOUNT),
                     ("privacy_email", _EMAIL), ("privacy_account", _SPOKEN_ACCOUNT)):
        for m in rx.finditer(t):
            add(kind, m.group(0))
    return flags


# ---------------------------------------------------------------- 음악 지배 휴리스틱
def music_suspected(tr: Transcript, start: float, end: float, coverage: float) -> bool:
    """비발화 구간 평균 RMS ≥ -30 dBFS 이면서 발화 커버리지가 낮으면(<60%) 배경음악/소음 지배로 의심"""
    if not tr.rms_db or coverage >= TRACK_A_MIN:
        return False
    hop = tr.rms_hop_sec or 0.5
    spans = [(sp.start, sp.end) for sp in tr.speech_spans]
    vals = []
    k0, k1 = int(start / hop), int(end / hop)
    for k in range(max(0, k0), min(len(tr.rms_db), k1 + 1)):
        c = (k + 0.5) * hop
        if not (start <= c <= end):
            continue
        if any(s <= c <= e for s, e in spans):
            continue
        vals.append(tr.rms_db[k])
    if len(vals) < 3:
        return False
    return sum(vals) / len(vals) >= MUSIC_RMS_DB


# ---------------------------------------------------------------- 후보 조립
def _build_candidate(tr: Transcript, w: _Window, source: str, reason: str = "") -> HighlightCandidate:
    cov = round(tr.speech_coverage(w.start, w.end), 3)
    scenes: List[Dict[str, Any]] = []
    flags: List[Dict[str, Any]] = []
    length = w.end - w.start
    for s in tr.sentences[w.i:w.j + 1]:
        text = _norm_text(s.text)
        if not text:
            continue
        a = max(0.0, round(s.start - w.start, 3))
        b = min(round(length, 3), round(s.end - w.start, 3))
        if b <= a:
            continue
        scenes.append({"start": a, "end": b, "text": text})
        flags.extend(scan_flags(text, a))
    first_word = None
    try:
        i0 = tr.sentences[w.i].word_range[0]
        if 0 <= i0 < len(tr.words):
            first_word = float(tr.words[i0].start)
    except (IndexError, AttributeError, TypeError):
        first_word = None
    return HighlightCandidate(
        start_sec=w.start, end_sec=w.end, cta_sec=CTA_SEC, track=classify_track(cov), coverage=cov, first_word_sec=first_word,
        caption_scenes=scenes, flags=flags, music_suspected=music_suspected(tr, w.start, w.end, cov),
        concept_score=w.score, first_sentence=w.i, last_sentence=w.j, source=source, reason=reason or w.detail)


# ---------------------------------------------------------------- LLM (세그먼트 ID만)
_LLM_SYSTEM = ("당신은 숏폼 광고 편집자입니다. 롱폼 전사에서 '확정된 단 하나의 컨셉'을 가장 잘 입증하는 구간을 고릅니다. "
               "시간(초)은 만들지 말고 후보 메뉴의 문장 번호(start_id, end_id)만 고르세요. JSON만 출력합니다.")


def _menu_text(tr: Transcript, pool: Sequence[_Window]) -> str:
    lines = []
    for n, w in enumerate(pool, 1):
        txt = " / ".join(_norm_text(s.text) for s in tr.sentences[w.i:w.j + 1])
        lines.append(f"[후보 {n}] start_id={w.i} end_id={w.j} ({w.end - w.start:.0f}초)\n  {txt[:420]}")
    return "\n".join(lines)


def _llm_rank(tr: Transcript, pool: List[_Window], core_concept: str, store_name: str, usp: str, top_k: int,
              api_key: Optional[str], llm_fn: Optional[Callable[[str, str], Any]]) -> Optional[List[_Window]]:
    prompt = (f"[확정 컨셉] {core_concept}\n[상호] {store_name or '(없음)'}\n[사장님이 적은 특장점] {usp or '(없음)'}\n\n"
              f"아래 후보 중 컨셉을 가장 잘 증명하는 순서대로 최대 {top_k}개를 고르세요.\n"
              "- 컨셉과 무관한 잡담·인사·다른 주제 구간은 고르지 마세요.\n"
              "- 후보 메뉴에 있는 start_id/end_id 조합만 사용하세요 (새 번호·초 단위 시간 금지).\n"
              '- JSON: {"picks": [{"start_id": 12, "end_id": 19, "reason": "한 줄"}]}\n\n' + _menu_text(tr, pool))
    try:
        if llm_fn is not None:
            data = llm_fn(_LLM_SYSTEM, prompt)
        else:
            from core.llm import generate_json
            data, _engine = generate_json([prompt], _LLM_SYSTEM, temperature=0.2, max_tokens=1500,
                                          gemini_api_key=api_key, purpose="롱폼 하이라이트 선정")
    except Exception as e:
        logger.warning(f"[하이라이트 LLM 실패 → 결정적 폴백] {e}")
        return None
    picks = data.get("picks") if isinstance(data, dict) else data
    if not isinstance(picks, list):
        return None
    by_ids = {(w.i, w.j): w for w in pool}
    out: List[_Window] = []
    for p in picks:
        try:
            key = (int(p.get("start_id")), int(p.get("end_id")))
        except (AttributeError, TypeError, ValueError):
            continue
        w = by_ids.get(key)
        if w is not None and w not in out:
            out.append(w)                          # 메뉴에 없는 ID 조합은 버림 (LLM이 만든 시간/번호를 믿지 않음)
        if len(out) >= top_k:
            break
    return out or None


# ---------------------------------------------------------------- 진입점
def select_highlights(tr: Transcript, core_concept: str, *, store_name: str = "", usp: str = "",
                      api_key: Optional[str] = None, use_llm: Optional[bool] = None,
                      llm_fn: Optional[Callable[[str, str], Any]] = None,
                      visual_scorer: Optional[Callable[[float, float], Any]] = None,
                      top_k: int = TOP_K, pool_size: int = POOL_SIZE) -> List[HighlightCandidate]:
    """
    Transcript + 확정 컨셉 → HighlightCandidate 최대 top_k개 (서로 겹치지 않음, 창 15~22초, 총 ≤ 25초).
    visual_scorer(start, end): 선택 입력. core.media.visual_scorer.VisualScorer(VisualScore: 점수+hard_reject) 또는 0~1 float.
    VisualScore.hard_reject 인 창은 후보에서 원천 제외하고, 나머지는 (텍스트×0.75 + 시각×0.25)로 합산한다.
    후보가 부족하면 적게 반환한다 (모두 시각 불량이면 빈 목록).
    """
    if not tr.sentences:
        return []
    primary, secondary = _concept_keys(core_concept, usp, store_name)
    windows = _enumerate_windows(tr, primary, secondary)
    if not windows:
        logger.info("[하이라이트] 15~22초 창을 만들 수 있는 문장 구간이 없음")
        return []
    for w in windows:
        w.text_score = w.score
    pool = _visual_pool(windows, visual_scorer, max(pool_size, top_k))
    if visual_scorer is not None and not pool:
        logger.info("[하이라이트] 시각 품질 기준을 통과한 후보 창이 없음 (모두 흐림·어두움·급패닝·흔들림 등)")
        return []

    llm_allowed = use_llm
    if llm_allowed is None:
        from core.llm import script_llm_ready
        llm_allowed = bool(llm_fn) or script_llm_ready(api_key)
    chosen: Optional[List[_Window]] = None
    source = "fallback"
    if llm_allowed and len(pool) > 1:
        ranked = _llm_rank(tr, pool, core_concept, store_name, usp, top_k, api_key, llm_fn)
        if ranked:
            chosen, source = ranked, "llm"
            for w in pool:                         # LLM이 덜 고른 만큼은 점수순으로 채움
                if len(chosen) >= top_k:
                    break
                if w not in chosen and all(w.end <= c.start or w.start >= c.end for c in chosen):
                    chosen.append(w)
    if chosen is None:
        chosen = pool[:top_k]

    out: List[HighlightCandidate] = []
    for rank, w in enumerate(chosen[:top_k], 1):
        cand = _build_candidate(tr, w, source)
        cand.rank = rank
        cand.concept_score = w.text_score
        if w.visual is not None:
            cand.visual_score = round(float(w.visual.score), 3)
            cand.visual = w.visual.to_dict() if hasattr(w.visual, "to_dict") else {"score": cand.visual_score}
        out.append(cand)
    return out
