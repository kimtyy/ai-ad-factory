"""
core/render/caption_chunks.py - 긴 자막을 '최대 2줄씩' 앞/뒤 덩어리로 나눠 순차 표시 (음식·매장 사진을 가리지 않게)

TTS는 한 문장을 그대로 읽고, 화면 자막만 나눈다:
  - 공백 포함 chunk_chars(기본 18자)를 넘는 문장은 쉼표·어절 경계에서 앞/뒤 두 덩어리로 (한 덩어리가 2줄에 안 들어가면 더 쪼갬)
  - 덩어리별 표시 시간은 글자 수 비율로 나눔 (마지막 덩어리는 씬 끝까지)
  - 어느 순간에도 자막이 max_lines(2)줄을 넘지 않게: 줄 수는 실제 폰트로 재는 fits() 함수를 받아 판단 (없으면 글자 수 근사)
"""
import re
from typing import Callable, List, Optional, Tuple

MIN_CHUNK_SEC = 0.6


def _approx_fits(text: str, line_chars: int = 12, max_lines: int = 2) -> bool:
    lines, cur = 1, 0
    for w in text.split(" "):
        need = len(w) + (1 if cur else 0)
        if cur and cur + need > line_chars:
            lines += 1
            cur = len(w)
        else:
            cur += need
    return lines <= max_lines


def _boundaries(words: List[str]) -> List[int]:
    return list(range(1, len(words)))


def _best_split(text: str, fits: Callable[[str], bool]) -> Optional[Tuple[str, str]]:
    """한 덩어리를 두 덩어리로: 쉼표·문장부호 뒤를 우선, 글자 수가 비슷한 곳, 두 덩어리 모두 2줄에 들어가는 곳. 어절이 하나뿐이면 None"""
    words = text.split(" ")
    if len(words) < 2:
        return None
    best = None
    total = len(text.replace(" ", ""))
    for i in _boundaries(words):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        left = len(a.replace(" ", ""))
        score = abs(left - total / 2) / max(total, 1)            # 0(정확히 반반) ~ 1
        if re.search(r"[,，、.!?…]$", words[i - 1]):
            score -= 0.25                                         # 쉼표·부호 뒤에서 자르면 의미가 덜 끊김
        if re.match(r"^(을|를|이|가|은|는|에|의|도|만|로|와|과)\b", words[i]):
            score += 0.3
        ok = fits(a) and fits(b)
        key = (0 if ok else 1, score)
        if best is None or key < best[0]:
            best = (key, a, b)
    return (best[1], best[2]) if best else None


def split_caption(text: str, fits: Optional[Callable[[str], bool]] = None, chunk_chars: int = 18, max_chunks: int = 3) -> List[str]:
    """
    자막 문장 → 덩어리 목록 (1개면 그대로). chunk_chars 이하이거나 어절이 하나면 그대로.
    덩어리가 2줄에 안 들어가면 그 덩어리를 한 번 더 쪼갬 (최대 max_chunks)
    """
    t = re.sub(r"\s+", " ", text or "").strip()
    fits = fits or _approx_fits
    if not t or len(t) <= chunk_chars:
        return [t] if t else []
    first = _best_split(t, fits)
    if not first:
        return [t]
    chunks = [first[0], first[1]]
    while len(chunks) < max_chunks:
        bad = next((i for i, c in enumerate(chunks) if not fits(c)), None)
        if bad is None:
            break
        sp = _best_split(chunks[bad], fits)
        if not sp:
            break
        chunks[bad:bad + 1] = [sp[0], sp[1]]
    return chunks


_BOUNDARY_END = re.compile(r"(에는|에서|에도|에게|으로|처럼|까지|부터|보다|하고|하며|해서|이고|지만|으며|[을를이가은는도와과의에로만고서며면])$")


def semantic_split(text: str, fits: Optional[Callable[[str], bool]] = None, lo: int = 9, hi: int = 13, max_words: int = 4) -> List[str]:
    """
    의미 단위(3~4어절, 공백 포함 lo~hi자 내외) 1줄 자막 덩어리: 글자 수만 보지 않고 쉼표·조사(~에는, ~에서, ~하고, ~과/와, ~을/를 …) 뒤에서 끊는다.
    hi자 이하 문장은 그대로. 덩어리가 2줄에 안 들어가면 _best_split으로 한 번 더 쪼갬. 마지막 덩어리가 너무 짧으면(5자 미만) 앞 덩어리에 붙임
    예: '여긴 겨울에는 눈부신 설경과 온수풀을 동시에 즐기는 숙소예요' → ['여긴 겨울에는 눈부신', '설경과 온수풀을 동시에', '즐기는 숙소예요']
    """
    t = re.sub(r"\s+", " ", text or "").strip()
    fits = fits or _approx_fits
    if not t or len(t) <= hi:
        return [t] if t else []
    chunks: List[str] = []
    cur: List[str] = []
    for w in t.split(" "):
        if cur and len(" ".join(cur + [w])) > hi:
            chunks.append(" ".join(cur))
            cur = []
        cur.append(w)
        joined = " ".join(cur)
        bare = w.rstrip(",，、.!?…")
        if len(joined) >= lo and (w != bare or _BOUNDARY_END.search(bare) or len(cur) >= max_words):
            chunks.append(joined)
            cur = []
    if cur:
        chunks.append(" ".join(cur))
    if len(chunks) > 1 and len(chunks[-1].replace(" ", "")) < 5 and len(chunks[-2]) + 1 + len(chunks[-1]) <= hi + 3:
        chunks[-2:] = [chunks[-2] + " " + chunks[-1]]
    out: List[str] = []
    for c in chunks:
        if fits(c):
            out.append(c)
        else:
            sp = _best_split(c, fits)
            out.extend(list(sp) if sp else [c])
    return out


def chunk_windows(chunks: List[str], speech_sec: float, scene_sec: float) -> List[Tuple[float, float]]:
    """
    덩어리별 (시작, 끝) 초. 음성 길이(speech_sec)를 글자 수 비율로 나누고, 마지막 덩어리는 씬 끝(scene_sec)까지 이어 붙임.
    각 덩어리는 최소 MIN_CHUNK_SEC 보장 (씬이 아주 짧으면 균등 분할)
    """
    n = len(chunks)
    if n <= 1:
        return [(0.0, scene_sec)]
    weights = [max(1, len(c.replace(" ", ""))) for c in chunks]
    tw = float(sum(weights))
    speech = max(speech_sec, MIN_CHUNK_SEC * n)
    speech = min(speech, scene_sec)
    starts = [0.0]
    for w in weights[:-1]:
        starts.append(starts[-1] + speech * w / tw)
    for i in range(1, n):                                   # 최소 길이 보정
        starts[i] = max(starts[i], starts[i - 1] + MIN_CHUNK_SEC)
    starts = [min(s, max(0.0, scene_sec - MIN_CHUNK_SEC * (n - i))) for i, s in enumerate(starts)]
    return [(starts[i], starts[i + 1] if i + 1 < n else scene_sec) for i in range(n)]
