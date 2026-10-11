"""
core/script_guard.py - 대본 후처리 가드 (트랙 A/B 공통)

1. shorten_line: 숏폼 대사 길이 맞춤. 글자 수로 자르지 않고 절/어절 경계에서 자르며,
   끝에 남은 연결어미(~해서, ~하고)·관형형(~한, ~는)은 떼어내 '즐겨보!' 같은 잘린 단어가 생기지 않게 합니다.
2. find_unsupported_claim: 근거 없는 주장 탐지.
   - 시설·속성 주장(자쿠지, 온수, 오션뷰, 침대 추가, 조식 …): 그 씬에 실제 배정된 사진 설명 또는 USP에 근거가 있어야 허용
   - 가격·할인·순위 주장(30%, 9,900원, 1위, 무료 …): 사장님/크롤링 USP에 적힌 경우만 허용
3. find_promo_tone: 홍보 어투 탐지. '~해보세요/~마세요/~딱!' 같은 강요·설명형 어미는 1초 만에 광고 티가 나서
   이탈을 부르므로 금지 (엔딩 CTA 씬은 제외)
4. to_haeyo: 합쇼체(~입니다/~습니다/~ㅂ니다) 어미를 해요체로 변환 (한 영상 안에서 말투가 뉴스체로 튀지 않게)
5. hook_echo: 씬 1 서브 대사가 후킹 헤드라인 단어를 앵무새처럼 반복하는지
6. find_off_scene_topic: 숙소 정보에만 있고 그 씬 화면에 없는 주제(반려견 용품, 조식 …)가 대사에 난입했는지
"""
import re
from typing import Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------- 1. 길이 맞춤
CONNECTIVE_ENDINGS = ("해서", "하고", "하며", "면서", "인데", "지만", "니까", "는데", "고", "며", "서")
# 뒤에 오는 명사를 꾸미는 관형형 어미 (예: '따뜻한', '있는') - 문장 끝에 남으면 말이 끊긴 느낌이 남
ADNOMINAL_ENDINGS = ("한", "는", "은", "던", "운", "같은", "있는", "없는")
# 뒤 말을 꾸미는 부사 - 잘린 끝에 남으면 '푹 쉬어가기 진짜!'처럼 말이 끊김
DANGLING_ADVERBS = ("진짜", "정말", "너무", "완전", "그냥", "제일", "가장", "엄청", "되게", "꼭")


def _dangling(word: str) -> bool:
    w = word.rstrip(",.")
    return (w.endswith(CONNECTIVE_ENDINGS) or (len(w) >= 3 and w.endswith(ADNOMINAL_ENDINGS))
            or w in DANGLING_ADVERBS)


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
    "댕댕": ("반려", "애견", "pet", "dog"),
    "강아지": ("반려", "애견", "pet", "dog", "puppy"),
    "펫": ("반려", "애견", "pet", "dog"),
    "셔틀": ("셔틀", "shuttle"),
    "픽업": ("픽업", "pickup", "pick-up"),
}
SERVICE_CLAIM_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (r"(침대|베드)\s*추가|엑스트라\s*베드|추가\s*침대", ("extra bed", "침대 추가", "추가 침대", "엑스트라")),
    (r"늦은\s*체크아웃|레이트\s*체크아웃", ("late check", "레이트 체크아웃", "늦은 체크아웃")),
    (r"얼리\s*체크인", ("early check", "얼리 체크인")),
)
# 상품 효능·사양·인증 주장: 판매 페이지(USP)에 적힌 것만 허용 (쇼핑 제휴는 수치·효능을 지어내면 허위·과장 광고)
SPEC_CLAIM_PATTERNS = (
    r"\d+(?:[.,]\d+)?\s*(?:시간|일|주|개월|년|배|%|퍼센트|도|분|초|kg|g|mm|cm|m|W|와트|mAh|ml|L|단|평|인용|인분|명|석|개)",
    "특허", "인증", "임상", "FDA", "KC", "식약처", "의학", "피부과", "최초", "유일", "완치", "치료", "효과 입증", "검증",
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


# ---------------------------------------------------------------- 3. 홍보 어투
# 문장 끝 '~세요'(해보세요·즐기세요·마세요·쉬어가세요), '~딱!'/'딱 좋아요'. 질문형 '~세요?'(아세요?)는 후킹으로 허용
PROMO_TONE_PATTERNS = (
    (r"세요\s*[!.~]*$", "~세요 강요형 어미"),
    (r"딱\s*[!.~]*$", "~딱! 어미"),
    (r"딱\s*좋", "'딱 좋아요' 상투 표현"),
    (r"놓치지\s*마", "'놓치지 마세요' 상투 표현"),
)


def find_promo_tone(text: str) -> Optional[str]:
    """홍보 어투 표현의 설명을 반환 (없으면 None)"""
    t = (text or "").strip()
    for pat, label in PROMO_TONE_PATTERNS:
        if re.search(pat, t):
            return label
    return None


# 팜플렛·브로슈어 낭독체 형용사: 명사 앞 수식어라 빼도 문장이 성립 (예: '고급스러운 라운지에서' → '라운지에서')
BROCHURE_ADJECTIVES = ("고급스러운", "세련된", "럭셔리한", "감각적인", "모던한", "트렌디한", "우아한", "품격 있는", "프리미엄한")


def strip_brochure_words(text: str) -> str:
    """브로슈어 수식어 제거 (뒤에 명사가 오는 경우만, 문장 맨 앞이면 다음 글자부터)"""
    t = text or ""
    for adj in BROCHURE_ADJECTIVES:
        t = re.sub(rf"{adj}\s+(?=\S)", "", t)
    return re.sub(r"\s{2,}", " ", t).strip()


# 실사 모드(트랙 A/B) 씬당 나레이션 길이: 추임새·연결 어미가 들어갈 여유 (15~20자는 사진 설명 단문만 남겼음)
VO_LIMIT = 26     # 프롬프트 목표 상한 (권장)
VO_MAX = 30       # (구) 기계적 절단 상한 - 이야기형 대본에서 '수영할 수!'처럼 문장을 잘라 더는 쓰지 않음
VO_HARD_MAX = 45  # 이보다 길 때만 문장 경계에서 줄임. 영상 길이는 나레이션 길이에 맞춰지므로 길어도 음성은 잘리지 않음

# 유튜버가 시청자 1명에게 꿀팁 썰을 푸는 100% 구어체 대본 규칙 (트랙 A/B 프롬프트 공용)
# 이력: '~됨/~임' 예시 → 개조식 단문, '~거든요' 어미만 바꾼 규칙 → 사진 낭독(브로슈어)체가 남아 전면 교체
COPY_TONE_RULES = """[대본 톤 - 3자 큐레이터가 시청자에게 '이런 순간엔 여기'를 짚어 주는 25~30초 숏폼 구어체]
- 화자: 가게·숙소 주인도, '제가 가 봤는데' 하는 체험자도 아닌 3자 큐레이터. 지어낸 경험담·후기('먹어 보니', '가 봤더니')는 쓰지 마세요.
- TPO 중심: 메뉴·시설을 순서대로 나열하지 말고, 방문자의 실제 상황(언제·누구와·어떤 기분일 때)을 장면마다 하나씩 그리세요.
  예: '퇴근길에 시원한 한 잔이 당기는 날', '비 오는 주말 둘이서 늘어지고 싶을 때', '친구들 단체로 모일 자리 찾을 때'.
  그 상황에서 이 사진 속 공간·메뉴가 왜 답인지 한 문장으로 연결하세요. 'A도 있고 B도 있어요' 같은 나열은 금지.
- 리듬: 최종 영상 27~29초(나레이션 24~26초) 안에 질문형('~할 때 어디 가요?'), 단언형('정답은 여기예요.'),
  감탄형('이 조합, 반칙이에요!')을 섞어 씬마다 문장 모양을 바꾸세요. 같은 어미가 두 씬 연속 나오면 안 됩니다.
- 어미 리듬 (필수): CTA를 뺀 대사 중 최소 1문장은 질문형('~어때요?', '~있나요?', '~있죠?'),
  최소 1문장은 감탄·확신형('~딱이죠!', '~제격이네요!', '~최고죠!')으로 쓰세요.
  '~에요/~예요/~해요.' 같은 평서 어미만 이어지는 단조로운 대본 금지: 평서 어미는 두 문장 연속 금지, 전체의 절반 이하.
- 분량: 사진 설명을 늘리지 말고 감각 표현(바삭한, 시원하게 넘어가는)이나 방문 상황(퇴근 후, 비 오는 주말, 넷이 모인 날)을
  구체적으로 더해 문장을 채우세요.
- 각 씬 대사는 해요체로 끝나는 완결 문장 하나 (~예요, ~이에요, ~어요/아요, ~죠, ~네요, ~까요? 등). 문장 중간에서 끊지 마세요.
- 엄격 금지 (판에 박힌 패턴): '~거든요', '~하거든요', '~고요', '~되고요' 어미, '솔직히', '근데' (문장 어디에서도)
- 금지 (홍보 어투): '~해보세요', '~즐겨보세요', '~마세요', '~놓치지 마세요', '~딱!', '~딱 좋아요'
- 금지 (개조식 단문): '~가능함', '~보임', '~됨', '~임'처럼 메모하듯 끊기는 어미
- 금지 (합쇼체·뉴스체): '~입니다', '~습니다', '~아닙니다'
- 금지 (팜플렛 낭독): '~가 보여요', '고급스러운·세련된·포근한·깔끔한 ~' 같은 사진 설명·수식어 나열
- 화면 일치: 정보에 있어도 그 씬 화면에 안 보이는 것은 그 씬 대사에 넣지 마세요.
- 감탄사 다양성: '대박', '미쳤어요', '반칙이에요', '역대급', '레전드' 같은 감탄은 영상 전체(헤드라인 포함)에서 같은 것을 한 번만."""

# 판에 박힌 패턴 (요청: 엄격 금지) → 생성 직후 한 번 다시 쓰게 하고, 그래도 남으면 안전 문구로 교체
BANNED_STYLE_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"거든요", "'~거든요' 어미"),
    (r"고요\s*[.!?~]*$", "'~고요' 어미"),
    (r"솔직히", "'솔직히'"),
    (r"(^|[\s,.!?])근데", "'근데'"),
)


STYLE_REPAIR_RULES = ("[다시 쓰기 규칙] 3자 큐레이터가 방문 상황(TPO)을 짚어 주는 해요체 완결 문장 한 줄. "
                      "엄격 금지: '~거든요', '~하거든요', '~고요', '~되고요' 어미, '솔직히', '근데'. "
                      "위반 설명에 어미 방향이 있으면 그 형태로, 없으면 질문형·단언형·감탄형 중 원래 흐름에 맞는 형태로. "
                      "길이는 원래와 비슷하거나 조금 길게 (공백 포함 26~30자).")


def _broken_da_yo(text: str) -> bool:
    """'사라진다요', '먹는다요', '있다요'처럼 평서형(~ㄴ다/~는다/~다)에 '요'를 붙인 비문 (명사+요인 '바다요'는 제외)"""
    m = re.search(r"([가-힣])다요\s*[.!?~]*$", text or "")
    if not m:
        return False
    ch = m.group(1)
    jong = (ord(ch) - 0xAC00) % 28
    return jong == 4 or ch in ("있", "없", "좋", "같", "싶")     # ㄴ 받침(진·한·는·간) 또는 형용사·존재사 어간


def find_banned_style(text: str) -> Optional[str]:
    """판에 박힌 구어 패턴('~거든요', '~되고요', '솔직히', '근데')·'~ㄴ다요' 비문 → 걸린 표현 설명 (없으면 None)"""
    t = (text or "").strip()
    for pat, label in BANNED_STYLE_PATTERNS:
        if re.search(pat, t):
            return label
    if _broken_da_yo(t):
        return "'~ㄴ다요' 비문 (평서형에 '요')"
    return None


# 나레이션 분량 → 영상 길이 (Edge-TTS SunHi 실측: 설맥 7씬 대사 127자 = 15.7초, 후킹 헤드라인 ~1.7초 + CTA ~3.0초)
NARRATION_TARGET_SEC = (24.0, 26.0)   # 엔딩 카드 제외 순수 나레이션 → 최종 영상 약 27~29초
TTS_SEC_PER_CHAR = 0.123              # 공백 포함 1자당 초
HOOK_CTA_SEC = 4.7


def line_char_range(n_lines: int) -> Tuple[int, int, int, int]:
    """CTA를 뺀 대사 n줄이 나레이션 24~26초가 되는 줄당 글자 수 (최소, 최대) + 합계 (최소, 최대), 공백 포함"""
    n = max(1, n_lines)
    lo_total, hi_total = ((t - HOOK_CTA_SEC) / TTS_SEC_PER_CHAR for t in NARRATION_TARGET_SEC)
    return int(lo_total / n), int(hi_total / n) + 1, int(lo_total), int(hi_total) + 1


def length_rule(n_lines: int) -> str:
    lo, hi, lo_total, hi_total = line_char_range(n_lines)
    return (f"각 voiceover는 공백 포함 {lo}~{hi}자 (씬 1~{n_lines} 합계 {lo_total}~{hi_total}자 → 나레이션 24~26초). "
            f"{lo}자보다 짧으면 영상이 25초 아래로 떨어집니다")


def ending_kind(text: str) -> str:
    """문장 끝 모양: question(?) / exclaim(! 또는 ~죠·~네요·~군요 확신·감탄) / plain(~에요·~해요. 평서)"""
    t = (text or "").strip()
    if t.endswith("?"):
        return "question"
    if t.endswith("!") or re.search(r"(죠|네요|군요)\s*[.~]*$", t):
        return "exclaim"
    return "plain"


RHYTHM_FIX = {
    "question": "질문형으로 바꾸기 ('~어때요?', '~있나요?', '~있죠?')",
    "exclaim": "감탄·확신형으로 바꾸기 ('~딱이죠!', '~제격이네요!', '~최고죠!')",
    "streak": "앞 문장도 평서 '~요.'라 단조로움 → 질문형이나 감탄·확신형으로 바꾸기",
}


def rhythm_issues(lines: List[str]) -> Dict[int, str]:
    """
    CTA를 뺀 대사 목록의 어미 리듬 검사 → {줄 번호: 고칠 방향}. 질문형·감탄형이 하나도 없거나
    평서 어미가 두 줄 연속이면, 바꿀 평서 줄을 골라 지시 (없으면 빈 dict)
    """
    kinds = [ending_kind(t) for t in lines]
    issues: Dict[int, str] = {}

    def pick(prefer_from: int = 1) -> Optional[int]:
        order = list(range(prefer_from, len(kinds))) + list(range(0, prefer_from))
        return next((i for i in order if kinds[i] == "plain" and i not in issues), None)

    for need in ("question", "exclaim"):
        if need not in kinds:
            i = pick(1 if need == "question" else len(kinds) // 2)
            if i is not None:
                issues[i] = RHYTHM_FIX[need]
                kinds[i] = need
    for i in range(1, len(kinds)):
        if kinds[i] == "plain" and kinds[i - 1] == "plain" and i not in issues:
            issues[i] = RHYTHM_FIX["streak"]
            kinds[i] = "exclaim"
    return issues


def script_violation_finder(lines: List[str]):
    """대본 보정용 위반 탐지기: 판에 박힌 패턴 + 어미 리듬 (줄 텍스트 → 위반 설명)"""
    rhythm = {lines[i]: msg for i, msg in rhythm_issues(lines).items()}
    return lambda text: find_banned_style(text) or rhythm.get(text)


def find_unsupported_spec(text: str, usp: str = "") -> Optional[str]:
    """판매 페이지(USP)에 없는 효능·수치·인증 주장 (예: USP에 없는 '48시간 보습', '특허', '1200W')"""
    t = text or ""
    usp_n = re.sub(r"\s+", "", (usp or "").lower())
    for pat in SPEC_CLAIM_PATTERNS:
        for m in re.finditer(pat, t):
            if re.sub(r"\s+", "", m.group(0).lower()) not in usp_n:
                return m.group(0)
    return None


# 근거 없는 대사·홍보 어투 대사를 대체할 사진 중립 문구 (카테고리별 2종을 번갈아 사용해 연속 반복 방지)
# 시설·속성·가격 단어 없이(허위 방지) 감각 묘사 구어체로만 작성
SAFE_LINES = {
    "pool": ("더운 날엔 이 물빛이 정답이에요", "발 담그는 순간 일상이 꺼져요"),
    "bedroom": ("긴 하루 끝, 그대로 뻗는 방이에요", "늦잠이 허락되는 방이에요"),
    "view": ("창밖만 봐도 충분한 날 있죠?", "이 풍경 하나로 떠날 이유 충분해요"),
    "facility": ("반나절이 순식간에 지나가는 공간이에요", "같이 온 사람 모두 만족하는 자리예요"),
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


# ---------------------------------------------------------------- 4. 해요체 통일
_H0, _HN = 0xAC00, 0xD7A3
_JONG_B, _JONG_SS = 17, 20
_V_A, _V_AE, _V_YA, _V_EO, _V_E, _V_YEO, _V_O, _V_WA, _V_WAE, _V_OE, _V_U, _V_WO, _V_EU, _V_I = 0, 1, 2, 4, 5, 6, 8, 9, 10, 11, 13, 14, 18, 20
# '이'로 끝나는 동사 어간 (보이다 → 보여요). 이 외의 '…입니다'는 서술격 조사(이다)로 보고 '이에요/예요'
_VERB_I_STEMS = ("보이", "놓이", "쌓이", "모이", "끓이", "들이", "먹이", "숙이", "속이", "섞이", "꺾이", "덮이", "높이", "줄이", "붙이")
# ㅂ 불규칙 형용사 어간 끝 (아름답다 → 아름다워요)
_B_IRREGULAR = ("답", "럽", "롭", "깝", "겁", "쉽", "덥", "춥", "맵", "밉", "곱")


def _parts(ch: str):
    code = ord(ch) - _H0
    return code // 588, (code % 588) // 28, code % 28


def _join(cho: int, jung: int, jong: int = 0) -> str:
    return chr(_H0 + cho * 588 + jung * 28 + jong)


def _is_hangul(ch: str) -> bool:
    return bool(ch) and _H0 <= ord(ch) <= _HN


def _haeyo_stem(stem_char: str) -> str:
    """'ㅂ니다' 앞 음절(받침 ㅂ 제거한 어간 끝)을 해요체 활용형으로 (하→해, 되→돼, 보→봐, 지→져, 쓰→써)"""
    if stem_char == "하":
        return "해"
    cho, jung, _ = _parts(stem_char)
    if jung == _V_OE:
        return _join(cho, _V_WAE)
    if jung in (_V_A, _V_AE, _V_YA, _V_EO, _V_E, _V_YEO, _V_WA, _V_WO, _V_WAE):
        return stem_char
    if jung == _V_O:
        return _join(cho, _V_WA)
    if jung == _V_U:
        return _join(cho, _V_WO)
    if jung == _V_I:
        return _join(cho, _V_YEO)
    if jung == _V_EU:
        return _join(cho, _V_EO)
    return stem_char + "어"


def _convert_ending(before: str, x: str) -> str:
    """before = 'X' 앞까지의 문자열, x = '니다' 바로 앞 음절. 변환된 (before 끝부분 포함) 문자열 반환"""
    if x == "닙" and before.endswith("아"):                       # 아닙니다 → 아니에요
        return before + "니에요"
    if x == "습":                                                # 있습니다/좋습니다/갔습니다/아름답습니다
        t = before[-1:] if before else ""
        if not _is_hangul(t):
            return before + "습니다"
        cho, jung, jong = _parts(t)
        if jong == _JONG_B and t in _B_IRREGULAR:
            return before[:-1] + _join(cho, jung) + "워요"
        if jong != _JONG_SS and jung in (_V_A, _V_YA, _V_O, _V_WA):
            return before + "아요"
        return before + "어요"
    if not _is_hangul(x):
        return before + x + "니다"
    cho, jung, jong = _parts(x)
    if jong != _JONG_B:
        return before + x + "니다"
    stem = _join(cho, jung)
    if stem == "이":                                              # 입니다: 보이다(동사) vs 이다(서술격)
        prev = before[-1:] if before else ""
        if (prev + "이") in _VERB_I_STEMS or any(before.endswith(v[:-1]) for v in _VERB_I_STEMS if len(v) > 2):
            return before + "여요"
        if not _is_hangul(prev):
            return before + "이에요"
        return before + ("이에요" if _parts(prev)[2] else "예요")
    return before + _haeyo_stem(stem) + "요"


_HAPSYO = re.compile(r"(\S)니다(?=[\s.!?,~]|$)")


def to_haeyo(text: str) -> str:
    """합쇼체 어미를 해요체로 (예: '장난 아닙니다.' → '장난 아니에요.', '바다가 보입니다' → '바다가 보여요',
    '쏙 들어가면 끝납니다' → '끝나요', '힐링 끝입니다' → '끝이에요', '말할 것도 없습니다' → '없어요')"""
    t = text or ""
    out, pos = [], 0
    for m in _HAPSYO.finditer(t):
        out.append(_convert_ending(t[pos:m.start(1)], m.group(1)))
        pos = m.end()
    out.append(t[pos:])
    return "".join(out)


def has_hapsyo(text: str) -> bool:
    return bool(_HAPSYO.search(text or ""))


# ---------------------------------------------------------------- 5. 후킹 앵무새 반복
def hook_echo(hook: str, text: str) -> list:
    """후킹 헤드라인의 단어(2자 이상) 중 서브 대사에 그대로(또는 앞 3글자가) 다시 나온 것. 2개 이상이면 앵무새 반복"""
    words = [re.sub(r"[^\w]", "", w) for w in (hook or "").split()]
    words = [w for w in words if len(w) >= 2]
    t = text or ""
    return [w for w in words if w in t or (len(w) >= 3 and w[:3] in t)]


def is_hook_echo(hook: str, text: str) -> bool:
    hits = hook_echo(hook, text)
    words = [w for w in (hook or "").split() if len(re.sub(r"[^\w]", "", w)) >= 2]
    return len(hits) >= 2 or (words and len(hits) / len(words) >= 0.6)


# ---------------------------------------------------------------- 6. 화면과 무관한 주제 난입
# 숙소 정보(USP·크롤링)에 있어도 그 씬 사진에 보이지 않으면 대사에 넣지 않는 주제 → 사진 설명 근거만 인정
SCENE_BOUND_TOPICS = {
    "반려견": (("반려", "애견", "댕댕", "강아지", "펫", "멍멍"), ("pet", "dog", "puppy", "반려", "애견")),
    "키즈": (("키즈", "아이들 놀이", "어린이", "유아"), ("kids", "children", "child", "play area", "playground", "키즈")),
    "조식": (("조식", "뷔페", "아침 식사"), ("breakfast", "buffet", "조식", "뷔페")),
    "바베큐": (("바베큐", "바비큐", "그릴"), ("bbq", "barbecue", "grill", "바베큐")),
    "불멍": (("불멍", "캠프파이어", "모닥불"), ("firepit", "fire pit", "fireplace", "bonfire", "불멍")),
    "셔틀": (("셔틀", "픽업"), ("shuttle", "pickup", "셔틀")),
    # 공간 시설: 그 공간 사진이 아닌 씬(예: 일반 침실)에서 '전용 테라스', '스파' 등을 말하면 화면과 어긋남
    "테라스": (("테라스", "발코니"), ("terrace", "balcony", "테라스", "발코니")),
    "스파": (("스파",), ("spa", "스파", "sauna", "사우나")),
    "자쿠지": (("자쿠지", "월풀"), ("jacuzzi", "hot tub", "whirlpool", "자쿠지")),
    "루프탑": (("루프탑", "옥상"), ("rooftop", "roof", "루프탑", "옥상")),
    "피트니스": (("피트니스", "헬스장", "헬스", "짐에서"), ("fitness", "gym", "피트니스", "헬스")),
}


def find_off_scene_topic(text: str, scene_notes: Iterable[Optional[str]]) -> Optional[str]:
    """대사에 나온 SCENE_BOUND_TOPICS 주제가 이 씬(과 이어지는 다음 씬) 사진 설명에 없으면 그 주제 이름 반환"""
    t = text or ""
    ev = " ".join(n for n in scene_notes if n).lower()
    for topic, (words, proofs) in SCENE_BOUND_TOPICS.items():
        if any(w in t for w in words) and not any(p in ev for p in proofs):
            return topic
    return None


# ---------------------------------------------------------------- 7. 종결 어미 검증
# 씬 대사는 해요체 완결 문장으로 끝나야 함. 연결 어미로 끝나면 '요'를 붙여 해요체 종결로 (구어체에서 자연스러운 형태)
_CONNECTIVE_TAILS = ("는데", "은데", "인데", "던데", "한데", "운데", "는데도", "지만", "니까", "면서", "거든",
                     "어서", "아서", "해서", "라서", "워서", "여서", "고", "면")


def complete_sentence(text: str) -> Optional[str]:
    """
    완결 문장이면 문장부호를 정리해 반환, 연결 어미로 끝나면 '요'를 붙여 종결 ('~아까운데,' → '~아까운데요.',
    '~좋고,' → '~좋고요.'), 그 외(잘린 문장 '~할 수!', 명사로 끝남 '~바다!')는 None
    """
    t = re.sub(r"\s+", " ", (text or "")).strip()
    body = t.rstrip(" ,.!?~…")
    if not body:
        return None
    tail = t[len(body):]
    end = "?" if "?" in tail else ("!" if "!" in tail else ".")
    if body.endswith(("요", "죠")) or (body.endswith("까") and end == "?"):
        return body + end
    if body.endswith(_CONNECTIVE_TAILS) and len(body.split()[-1]) >= 2:
        return body + "요."
    return None


def is_complete_sentence(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and complete_sentence(t) == t.rstrip() and t.rstrip()[-1] in ".!?"


# ---------------------------------------------------------------- 8. 감탄사 다양성
# 같은 감탄 표현(어근)은 영상 전체(헤드라인 포함)에서 1회만. 두 번째부터는 아직 안 쓴 어근의 '같은 활용 자리'로 바꿈.
# 서술형 감탄 어근별 활용형 (같은 키끼리 서로 바꿔 끼워도 문법이 맞음)
PRED_EXCLAIMS = {
    "미치": {"요": "미쳤어요", "거든요": "미쳤거든요", "더라고요": "미쳤더라고요", "고요": "미쳤고요", "는데요": "미쳤는데요", "adn": "미친 "},
    "장난아니": {"요": "장난 아니에요", "거든요": "장난 아니거든요", "더라고요": "장난 아니더라고요", "고요": "장난 아니고요",
             "는데요": "장난 아닌데요", "adn": "장난 아닌 "},
    "끝내주": {"요": "끝내줘요", "거든요": "끝내주거든요", "더라고요": "끝내주더라고요", "고요": "끝내주고요", "는데요": "끝내주는데요",
            "adn": "끝내주는 "},
    "완벽": {"요": "완벽해요", "거든요": "완벽하거든요", "더라고요": "완벽하더라고요", "고요": "완벽하고요", "는데요": "완벽한데요",
           "adn": "완벽한 "},
    "흠잡을데없": {"요": "흠잡을 데가 없어요", "거든요": "흠잡을 데가 없거든요", "더라고요": "흠잡을 데가 없더라고요",
              "고요": "흠잡을 데가 없고요", "는데요": "흠잡을 데가 없는데요", "adn": "흠잡을 데 없는 "},
}
# 명사형 감탄 (받침 있는 것끼리만 교체: 뒤 '이에요/인 건' 그대로 맞음)
NOUN_EXCLAIMS = ("대박", "역대급")
# 명사 감탄 + 서술격 조사 → 서술형 감탄의 같은 활용 자리 (명사 대체어가 바닥났을 때: '대박이에요' → '끝내줘요')
COPULA_TO_SLOT = (("이더라고요", "더라고요"), ("이거든요", "거든요"), ("이에요", "요"), ("이고요", "고요"), ("인데요", "는데요"))
# 명사 감탄이 부사로 쓰일 때('대박 편해요') 대신 쓸 부사
EXCLAIM_ADVERBS = ("엄청", "완전", "정말")
_PRED_INDEX = sorted(((form, root, slot) for root, forms in PRED_EXCLAIMS.items() for slot, form in forms.items()),
                     key=lambda x: -len(x[0]))
_EXCLAIM_RE = re.compile("|".join([re.escape(f) for f, _, _ in _PRED_INDEX] + [re.escape(n) for n in NOUN_EXCLAIMS]))
_PREDICATE_TAIL = re.compile(r"(요|다|고|서|게|해|하|져|워|와|지|면|는데|어|아|돼|됨)[.,!?~]*$")


def _exclaim_hits(text: str):
    """(시작, 끝, 어근, 종류, 활용 자리) 목록. 종류: pred / noun / adverb"""
    hits = []
    for m in _EXCLAIM_RE.finditer(text or ""):
        word = m.group(0)
        pred = next(((root, slot) for f, root, slot in _PRED_INDEX if f == word), None)
        if pred:
            hits.append((m.start(), m.end(), pred[0], "pred", pred[1]))
            continue
        rest = text[m.end():]
        nxt = rest.split(" ", 2)[1] if rest.startswith(" ") and len(rest.split(" ", 2)) > 1 else ""
        kind = "adverb" if nxt and not rest.startswith((" 건", " 거")) and _PREDICATE_TAIL.search(nxt) else "noun"
        hits.append((m.start(), m.end(), word, kind, None))
    return hits


def diversify_exclamations(lines: List[str], reserved: str = "") -> Tuple[List[str], List[str]]:
    """
    씬 대사(순서대로)에서 같은 감탄 어근의 두 번째 사용부터 아직 안 쓴 어근으로 바꿈. reserved(씬 1 헤드라인 등)에 나온 것은
    이미 1회 쓴 것으로 셈. 바꿀 후보가 없으면 그대로 둠. 반환: (바뀐 대사 목록, 변경 기록)
    """
    used = {}
    for _, _, root, kind, _ in _exclaim_hits(reserved):
        used[root] = used.get(root, 0) + 1
    adverb_i = 0
    out, log = [], []
    for line in lines:
        text = line or ""
        pieces, pos = [], 0
        for start, end, root, kind, slot in _exclaim_hits(text):
            pieces.append(text[pos:start])
            original = text[start:end]
            if used.get(root, 0) == 0:
                used[root] = 1
                pieces.append(original)
            elif kind == "pred":
                alt = next((r for r in PRED_EXCLAIMS if used.get(r, 0) == 0 and slot in PRED_EXCLAIMS[r]), None)
                if alt:
                    used[alt] = 1
                    pieces.append(PRED_EXCLAIMS[alt][slot])
                else:
                    pieces.append(original)
            elif kind == "adverb":
                pieces.append(EXCLAIM_ADVERBS[adverb_i % len(EXCLAIM_ADVERBS)])
                adverb_i += 1
            else:
                alt = next((n for n in NOUN_EXCLAIMS if used.get(n, 0) == 0), None)
                if alt:
                    used[alt] = 1
                    pieces.append(alt)
                else:
                    # 명사 대체어가 없으면 '대박이에요'처럼 서술어로 쓰인 경우 안 쓴 서술형 감탄으로 ('끝내줘요')
                    cop = next(((c, sl) for c, sl in COPULA_TO_SLOT if text.startswith(c, end)), None)
                    pred = next((r for r in PRED_EXCLAIMS if used.get(r, 0) == 0), None) if cop else None
                    if pred:
                        used[pred] = 1
                        pieces.append(PRED_EXCLAIMS[pred][cop[1]])
                        end += len(cop[0])
                    else:
                        pieces.append(original)
            pos = end
        pieces.append(text[pos:])
        new = "".join(pieces)
        if new != text:
            log.append(f"'{text}' → '{new}'")
        out.append(new)
    return out, log
