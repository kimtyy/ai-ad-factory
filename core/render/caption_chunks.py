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


_JOSA_END = re.compile(r"(에는|에서|에도|에게|으로|처럼|까지|부터|보다|하고|하며|해서|이고|지만|으며|다가|전에|후에|[을를이가은는도와과의에로만고서며면])$")
_ADNOMINAL_LAST = set("는은한춘신된진른린킨운찬픈긴할길갈울")        # 갖춘·보이는·담그는·눈부신·따뜻한·즐길 같은 꾸미는 말(관형형)의 끝 글자
_DEP = {"수", "줄", "것", "리", "뿐"}                                  # 의존명사: 앞뒤를 갈라 놓으면 안 됨 ('즐길 수 있어요')
_AUX_START = re.compile(r"^(있|없|보세|보시|보셨|같|몰랐|드릴|드려|되|돼)")
_AUX_PREV_END = "해어아게지수줄봐"                                       # '확인해 보세요', '즐길 수 있어요' 같은 보조 용언 결합


_CLAUSE_END = re.compile(r"(다가|하고|해서|지만|면서|라서|니까|는데|으며|하며|전에|후에)$")


def _has_josa(word: str) -> bool:
    return bool(_JOSA_END.search(word.rstrip(",，、.!?…")))


def _is_adnominal(word: str) -> bool:
    w = word.rstrip(",，、.!?…")
    return len(w) >= 2 and w[-1] in _ADNOMINAL_LAST


def _no_split(prev: str, nxt: str) -> bool:
    """이 두 어절 사이는 절대 끊지 않음: 의존명사(수·줄·것)·보조 용언(해 보세요, 수 있어요)"""
    p = prev.rstrip(",，、.!?…")
    if p in _DEP or nxt.rstrip(",，、.!?…") in _DEP:
        return True
    return bool(_AUX_START.match(nxt)) and bool(p) and p[-1] in _AUX_PREV_END


def semantic_split(text: str, fits: Optional[Callable[[str], bool]] = None, short: int = 14, soft_line: int = 16) -> List[str]:
    """
    의미 단위 자막: 한 문장은 1덩어리(14자 이하) 또는 균형 잡힌 2덩어리로만 나눈다 (3.6초 씬 = 전반 + 후반, 1어절 꼬투리 금지).
    - 15자 이상이면 전체 길이 35~65% 구간에서 가장 자연스러운 경계를 고름 (한 줄은 15~16자 내외, 길면 2줄까지)
    - 조사·쉼표·연결어미(~까지, ~다가, ~전에, ~순간이, ~과/와, ,) 뒤를 선호. 조사 없이 명사가 이어지는 곳(강릉|오션뷰, 야외|테라스까지)은 큰 감점
    - 관형형(갖춘, 보이는, 담그는, 눈부신 …) 바로 뒤는 감점 (꾸밈말과 꾸며지는 말을 떼지 않게)
    - 의존명사·보조 용언(즐길|수|있어요, 확인해|보세요) 사이는 절대 금지
    - 한쪽이 1어절이거나 공백 제외 5자 이하가 되는 경계는 금지 (그런 경계뿐이면 통자막 1덩어리)
    """
    t = re.sub(r"\s+", " ", text or "").strip()
    fits = fits or _approx_fits
    if not t or len(t) <= short:
        return [t] if t else []
    words = t.split(" ")
    n, total = len(words), len(t)
    best = None
    for i in range(1, n):
        left, right = " ".join(words[:i]), " ".join(words[i:])
        if i == 1 or n - i == 1 or len(left.replace(" ", "")) <= 5 or len(right.replace(" ", "")) <= 5 or _no_split(words[i - 1], words[i]):
            continue
        pos = len(left) / total
        dev = abs(pos - 0.5)
        score = dev * 10 + 25 * max(0.0, dev - 0.15)          # 35~65% 구간을 벗어나는 만큼 비례 감점
        w = words[i - 1]
        if w[-1] in ",，、" or _CLAUSE_END.search(w):
            score -= 2.5                        # 쉼표·절 경계(~다가, ~하고, ~전에): 가장 자연스러운 호흡
        elif _has_josa(w):
            score -= 1.5
        elif _is_adnominal(w):
            score += 1.0                        # 꾸미는 말(갖춘·담그는)에서 끊는 건 가능하나 조사 뒤보다 덜 자연스러움
        else:
            score += 9.0                        # 조사 없이 명사가 이어지는 곳(강릉|오션뷰, 야외|테라스까지)은 사실상 금지
        if not (fits(left) and fits(right)):
            score += 6.0
        score += 0.3 * (max(0, len(left) - soft_line * 2) + max(0, len(right) - soft_line * 2))
        if best is None or score < best[0]:
            best = (score, left, right)
    return [best[1], best[2]] if best else [t]


def chunk_windows(chunks: List[str], speech_sec: float, scene_sec: float) -> List[Tuple[float, float]]:
    """
    덩어리별 (시작, 끝) 초. 음성 길이(speech_sec)를 글자 수 비율로 나누고, 마지막 덩어리는 씬 끝(scene_sec)까지 이어 붙임.
    각 덩어리는 최소 MIN_CHUNK_SEC 보장 (씬이 아주 짧으면 균등 분할)
    """
    n = len(chunks)
    if n <= 1:
        return [(0.0, scene_sec)]
    min_sec = 1.2 if (n == 2 and scene_sec >= 2.4) else MIN_CHUNK_SEC          # 2덩어리 자막은 글자 수 비례로 나누되 각각 최소 1.2초는 읽을 수 있게
    weights = [max(1, len(c.replace(" ", ""))) for c in chunks]
    tw = float(sum(weights))
    speech = max(speech_sec, min_sec * n)
    speech = min(speech, scene_sec)
    starts = [0.0]
    for w in weights[:-1]:
        starts.append(starts[-1] + speech * w / tw)
    for i in range(1, n):                                   # 최소 길이 보정
        starts[i] = max(starts[i], starts[i - 1] + min_sec)
    starts = [min(s, max(0.0, scene_sec - min_sec * (n - i))) for i, s in enumerate(starts)]
    return [(starts[i], starts[i + 1] if i + 1 < n else scene_sec) for i in range(n)]
