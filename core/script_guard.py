"""
core/script_guard.py - 대본 후처리 가드 (트랙 A/B 공통)

1. shorten_line: 숏폼 대사 길이 맞춤. 글자 수로 자르지 않고 절/어절 경계에서 자르며,
   끝에 남은 연결어미(~해서, ~하고)·관형형(~한, ~는)은 떼어내 '즐겨보!' 같은 잘린 단어가 생기지 않게 합니다.
2. find_unsupported_claim: 근거 없는 주장 탐지.
   - 시설·속성 주장(자쿠지, 온수, 오션뷰, 침대 추가, 조식 …): 그 씬에 실제 배정된 사진 설명 또는 USP에 근거가 있어야 허용
   - 가격·할인·순위 주장(30%, 9,900원, 1위, 무료 …): 사장님/크롤링 USP에 적힌 경우만 허용
"""
import re
from typing import Iterable, Optional, Tuple

# ---------------------------------------------------------------- 1. 길이 맞춤
CONNECTIVE_ENDINGS = ("해서", "하고", "하며", "면서", "인데", "지만", "니까", "는데", "고", "며", "서")
# 뒤에 오는 명사를 꾸미는 관형형 어미 (예: '따뜻한', '있는') - 문장 끝에 남으면 말이 끊긴 느낌이 남
ADNOMINAL_ENDINGS = ("한", "는", "은", "던", "운", "같은", "있는", "없는")


def _dangling(word: str) -> bool:
    w = word.rstrip(",.")
    return w.endswith(CONNECTIVE_ENDINGS) or (len(w) >= 3 and w.endswith(ADNOMINAL_ENDINGS))


def shorten_line(text: str, limit: int = 20, max_len: int = 22) -> str:
    """max_len 이하면 그대로, 넘으면 limit 이내에서 절 → 어절 경계로 줄이고 원래 문장부호(!/?)를 유지"""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) <= max_len:
        return t
    end = "?" if t.endswith("?") else "!"
    body = t.rstrip("!?. ")

    # 1) 문장 단위: 문장부호(.!?)로 끝나는 앞쪽 문장을 limit 안에서 유지 (쉼표는 구 중간인 경우가 많아 제외)
    sentences = [c.strip() for c in re.split(r"[.!?]\s*", body) if c.strip()]
    kept = ""
    for c in sentences:
        cand = f"{kept} {c}".strip() if kept else c
        if len(cand) > limit:
            break
        kept = cand
    if len(kept) >= 8 and not _dangling(kept.split(" ")[-1]):
        return kept.rstrip(",. ") + end

    # 2) 어절 단위: limit 안에 들어가는 만큼, 끝의 연결어미 어절은 제거
    words = body.split(" ")
    out = []
    for w in words:
        if len(" ".join(out + [w])) > limit:
            break
        out.append(w)
    while len(out) > 1 and _dangling(out[-1]):
        out.pop()
    if out and len(" ".join(out)) >= 6:
        return " ".join(out).rstrip(",. ") + end

    # 3) 띄어쓰기 없는 긴 한 덩어리: 어쩔 수 없이 글자 기준
    return body[:limit].rstrip() + end


# ---------------------------------------------------------------- 2. 근거 없는 주장
# 시설·속성 주장 → 근거로 인정할 표현 (한국어 사진 태그 + 비전 판정 영어 설명 + USP)
AMENITY_CLAIMS = {
    "자쿠지": ("자쿠지", "jacuzzi", "hot tub", "whirlpool"),
    "스파": ("스파", "spa"),
    "온수": ("온수", "heated", "warm water", "hot spring", "온천"),
    "온천": ("온천", "hot spring", "onsen"),
    "노천탕": ("노천", "open-air bath", "outdoor bath"),
    "인피니티": ("인피니티", "infinity"),
    "오션": ("오션", "바다", "ocean", "sea", "beach"),
    "바다": ("바다", "오션", "ocean", "sea", "beach"),
    "해변": ("해변", "바다", "beach"),
    "호수": ("호수", "lake"),
    "루프탑": ("루프탑", "rooftop"),
    "조식": ("조식", "breakfast"),
    "뷔페": ("뷔페", "buffet"),
    "레스토랑": ("레스토랑", "restaurant", "dining"),
    "다이닝": ("다이닝", "레스토랑", "restaurant", "dining"),
    "바베큐": ("바베큐", "바비큐", "bbq", "barbecue", "grill"),
    "바비큐": ("바베큐", "바비큐", "bbq", "barbecue", "grill"),
    "불멍": ("불멍", "firepit", "fire pit", "fireplace", "bonfire"),
    "키즈": ("키즈", "kids", "children", "play area", "playground"),
    "반려": ("반려", "pet", "dog"),
    "애견": ("애견", "반려", "pet", "dog"),
    "셔틀": ("셔틀", "shuttle"),
    "픽업": ("픽업", "pickup", "pick-up"),
}
SERVICE_CLAIM_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (r"(침대|베드)\s*추가|엑스트라\s*베드|추가\s*침대", ("extra bed", "침대 추가", "추가 침대", "엑스트라")),
    (r"늦은\s*체크아웃|레이트\s*체크아웃", ("late check", "레이트 체크아웃", "늦은 체크아웃")),
    (r"얼리\s*체크인", ("early check", "얼리 체크인")),
)
# 가격·혜택·순위: USP에 적힌 표현만 허용
PRICE_CLAIM_PATTERNS = (r"\d+\s*%", r"\d[\d,]*\s*원", "할인", "무료", "공짜", "최저가", "1위", "일등",
                        "특가", "이벤트", "증정", "5성급", "오성급", "최고급")


def find_unsupported_claim(text: str, evidence: Iterable[Optional[str]] = (), usp: str = "",
                           check_amenities: bool = True) -> Optional[str]:
    """근거 없는 주장 표현을 반환 (없으면 None). evidence: 그 씬의 실제 사진 설명 등"""
    t = (text or "")
    usp_l = (usp or "").lower()
    ev = " ".join(e for e in evidence if e).lower() + " " + usp_l

    for pat in PRICE_CLAIM_PATTERNS:
        m = re.search(pat, t)
        if m and m.group(0).lower() not in usp_l:
            return m.group(0)
    if not check_amenities:
        return None
    for claim, proofs in AMENITY_CLAIMS.items():
        if claim in t and not any(p in ev for p in proofs):
            return claim
    for pat, proofs in SERVICE_CLAIM_PATTERNS:
        m = re.search(pat, t)
        if m and not any(p in ev for p in proofs):
            return m.group(0)
    return None


# 근거 없는 대사를 대체할 사진 중립 문구 (카테고리별 2종을 번갈아 사용해 연속 반복 방지)
SAFE_LINES = {
    "pool": ("물빛만 봐도 힐링되는 수영장!", "보기만 해도 시원한 물놀이 공간!"),
    "bedroom": ("포근한 객실에서 푹 쉬어가세요!", "머물고 싶어지는 아늑한 객실!"),
    "view": ("눈앞에 펼쳐지는 그림 같은 뷰!", "창밖 풍경만 봐도 힐링돼요!"),
    "facility": ("머물수록 좋은 공간이 가득해요!", "쉬어가기 딱 좋은 분위기!"),
}
ROLE_TO_CATEGORY = {"pool": "pool", "bedroom": "bedroom", "bedroom_or_view": "bedroom", "view": "view",
                    "view_or_night": "view", "facility": "facility", "facility_or_night_view": "facility"}
NOTE_PREFIX_TO_CATEGORY = {"수영장": "pool", "객실": "bedroom", "전경·뷰": "view", "부대시설": "facility"}


def safe_line_for(role: str, note: Optional[str], index: int) -> str:
    cat = None
    if note:
        cat = NOTE_PREFIX_TO_CATEGORY.get(note.split(" 사진:")[0].strip())
    cat = cat or ROLE_TO_CATEGORY.get(role, "view")
    lines = SAFE_LINES[cat]
    return lines[index % len(lines)]
