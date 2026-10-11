"""
core/shorts_meta.py - 쇼츠 업로드 메타데이터(제목·설명·해시태그) + 썸네일 카피(메인·서브·포인트)

- 제목: 손실 회피·극단적 호기심형 ('{지역}에 이런 {특징}이 숨겨져 있었다니?!', '모르면 무조건 손해! …'), 끝에 #Shorts
- 근거 없는 사실 주장은 금지: 예약 폭주·품절·완판·1위·최저가·할인율 같은 표현은 USP에 적혀 있을 때만 허용
  (표시광고법상 기만 광고 + 제휴 계정 정지 사유). '끝판왕'·'인생 숙소' 같은 주관적 감탄은 허용
- Gemini가 있으면 카피 생성, 없거나(mock·키 없음) 검증에서 걸리면 근거 안전한 템플릿으로 대체
- 설명란: 1줄 제목 → 제휴 고지(공정위 추천·보증 심사지침: 게시물 첫 부분) → 📌 첫 댓글 안내 → 해시태그 5~7개
- 첫 댓글(업로드 직후 자동 등록): 참여 유도 질문 → 사장님=네이버 검색·지도 링크 / 제휴=링크 + 고지 전문 → 핵심 해시태그 3개
"""
import json
import logging
import random
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

logger = logging.getLogger("ad_factory.shorts_meta")

TITLE_BODY_MAX = 60            # '#Shorts' 앞 본문 길이 (모바일 제목 2줄 안)
THUMB_MAIN_MAX = 14            # 썸네일 메인 카피 (공백 포함): 큰 글자 2줄
THUMB_SUB_MAX = 16
ACCENTS = ("🔥", "💥", "😱", "🤯", "ㄷㄷ", "?!")
HASHTAG_MIN, HASHTAG_MAX = 5, 7

PINNED_NOTICE = {
    "travel": "📌 숙소 상세 정보 및 예약 링크는 첫 번째 댓글을 확인해 주세요!",
    "commerce": "📌 제품 상세 정보 및 구매 링크는 첫 번째 댓글을 확인해 주세요!",
    "owner": "📌 가게 위치·예약 정보는 첫 번째 댓글을 확인해 주세요!",
}

# 수요·인기·희소성 사실 주장 (USP에 없으면 금지) - 가격·순위·수치는 script_guard.find_unsupported_claim/spec이 검사
HYPE_CLAIMS = ("예약 폭주", "예약폭주", "예약 마감", "마감 임박", "마감임박", "품절", "완판", "매진", "대란", "오픈런", "웨이팅",
               "줄 서", "줄서", "조회수", "역대급 할인", "한정 수량", "선착순", "난리 난", "난리난")

# USP·상품명 키워드 → 해시태그
KEYWORD_TAGS = (
    (("풀빌라",), "#풀빌라"), (("오션뷰", "바다 전망", "바다뷰", "바다 뷰"), "#오션뷰숙소"),
    (("인피니티", "수영장", "풀 ", "루프탑 풀", "실내 수영장"), "#수영장숙소"), (("감성",), "#감성숙소"),
    (("글램핑",), "#글램핑"), (("캠핑",), "#캠핑"), (("펜션",), "#펜션추천"), (("호텔",), "#호텔추천"),
    (("리조트",), "#리조트"), (("스파", "자쿠지", "온천"), "#스파숙소"), (("한옥",), "#한옥스테이"),
    (("키즈", "아이와", "가족"), "#가족여행"), (("커플", "데이트"), "#커플여행"), (("반려", "애견", "댕댕"), "#애견동반숙소"),
    (("카페",), "#카페추천"), (("맛집", "고기", "삼겹"), "#맛집추천"), (("뷰",), "#뷰맛집"),
)
# 모드별 기본 태그 (키워드 태그가 부족할 때 채움). '#내돈내산'은 제휴(수수료) 영상과 모순 → 쓰지 않음
BASE_TAGS = {
    "travel": ["#숙소추천", "#국내여행", "#여행", "#주말여행", "#여행브이로그", "#가볼만한곳", "#힐링"],
    "commerce": ["#추천템", "#살림템", "#꿀템", "#생활꿀팁", "#쇼핑추천", "#리뷰", "#신박한템"],
    "owner": ["#가게추천", "#가볼만한곳", "#동네핫플", "#로컬추천", "#오늘뭐하지", "#핫플", "#추천"],
}
# 근거 없이도 쓸 수 있는 템플릿 (사실 주장 없음). {kw}=시그니처·USP 키워드, {where}=지역(없으면 '국내'·'동네' 등)
# 호기심 공백·손실 회피형. '여길 아직도 안 가봤다고?' 같은 진부한 패턴은 쓰지 않음 (STALE_HOOKS로 모델 출력에서도 차단)
TITLE_TEMPLATES = {
    "travel": ["{where}에 이런 {kw}{iga} 숨겨져 있었다니?!", "모르면 무조건 손해! {where} {kw} 숙소의 정체", "아무도 안 알려준 {where} {kw} 숙소",
               "사진으로는 절대 모르는 {where} {kw}의 반전 🔥", "예약 전에 꼭 봐야 할 {where} {kw} 숙소"],
    "commerce": ["이거 모르고 사면 무조건 손해! {kw}", "왜 이제 알았지? {kw}의 반전 🔥", "아무도 안 알려준 {kw} 꿀템의 정체",
                 "사기 전에 꼭 봐야 할 {kw}"],
    "owner": ["{where}에 이런 {kw}{iga} 숨겨져 있었다니?!", "모르면 무조건 손해! {where} {kw}의 정체", "아무도 안 알려준 {where} {kw}",
              "{where} 사람들도 잘 모르는 {kw} 🔥", "{where}에서 한 번 오면 다시 찾게 되는 {kw}의 비밀"],
}
THUMB_TEMPLATES = {
    "travel": [("이게 숨어 있었다고?", "{kw}"), ("모르면 무조건 손해!", "{kw}"), ("사진보다 실물이 반전", "{kw}")],
    "commerce": [("모르면 무조건 손해!", "{kw}"), ("왜 이제 알았지?", "{kw}")],
    "owner": [("이게 숨어 있었다고?", "{kw}"), ("모르면 무조건 손해!", "{kw}"), ("아무도 안 알려준 곳", "{kw}")],
}
WHERE_FALLBACK = {"travel": "국내", "commerce": "", "owner": "동네"}
# 진부한 후킹 (모델이 써도 버리고 템플릿·다른 필드로): '여길 아직도 안 가봤다고?', '여길 모른다고?', '여길 안 간다고?' 계열
STALE_HOOKS = (r"여길\s*(아직도\s*)?(안\s*가|안\s*간|모르|모른|몰라|몰랐)", r"아직도\s*안\s*가\s*봤", r"여기\s*아직도\s*모르")
KW_FALLBACK = {"travel": "숙소", "commerce": "꿀템", "owner": "가게"}
# 첫 댓글 참여 유도 질문 (답하기 쉬운 선택·경험 질문, 사실 주장 없음). AI 질문이 없거나 검증에서 걸리면 사용
COMMENT_QUESTIONS = {
    "owner": ["여기 가면 제일 먼저 시킬 메뉴는? 댓글로 골라 주세요 👇", "{kw}, 누구랑 같이 오고 싶어요? 친구 태그 👇",
              "여러분 동네 {kw} 맛집도 댓글로 알려 주세요 👇"],
    "travel": ["이 숙소, 누구랑 가고 싶어요? 댓글로 태그해 주세요 👇", "{kw} 숙소에서 제일 먼저 하고 싶은 건? 👇"],
    "commerce": ["여러분은 이거 어디에 제일 먼저 쓰고 싶어요? 👇", "{kw} 고를 때 제일 중요하게 보는 건? 👇"],
}
COMMENT_QUESTION_MAX = 45
COMMENT_TAGS = 3
NAVER_MAP_SEARCH = "https://map.naver.com/p/search/{q}"     # 네이버 지도 검색 결과 링크 (페이지 접속·크롤링 없음, 링크만 만듦)


def is_stale_hook(text: str) -> bool:
    return any(re.search(p, text or "") for p in STALE_HOOKS)


def _iga(word: str) -> str:
    """받침 있으면 '이', 없으면 '가' (한글 아니면 '가')"""
    w = (word or "").strip()
    if not w or not ("가" <= w[-1] <= "힣"):
        return "가"
    return "이" if (ord(w[-1]) - 0xAC00) % 28 else "가"


def find_hype_claim(text: str, usp: str = "") -> Optional[str]:
    """근거(USP) 없는 수요·가격·순위·수치 주장 → 걸린 표현 (없으면 None)"""
    from core.script_guard import find_unsupported_claim, find_unsupported_spec
    t = text or ""
    for w in HYPE_CLAIMS:
        if w in t and w not in (usp or ""):
            return w
    return find_unsupported_claim(t, usp=usp or "", check_amenities=False) or find_unsupported_spec(t, usp or "")


def pick_keyword(mode: str, usp: str = "", product_name: str = "") -> str:
    """제목·썸네일 서브 카피용 짧은 키워드 (USP 첫 구절, 너무 길면 상품 종류 단어)"""
    for part in re.split(r"[,/·\n]| 및 ", usp or ""):
        p = part.strip()
        if 2 <= len(p) <= 12 and not find_hype_claim(p, usp):
            return p
    for words, tag in KEYWORD_TAGS:
        if any(w.strip() in f"{usp} {product_name}" for w in words):
            return tag.lstrip("#").replace("추천", "")
    return KW_FALLBACK.get(mode, "여기")


def hashtags_for(mode: str, usp: str = "", product_name: str = "", extra: Optional[List[str]] = None) -> List[str]:
    text = f"{usp} {product_name}"
    tags: List[str] = []
    for words, tag in KEYWORD_TAGS:
        if any(w in text for w in words) and tag not in tags:
            tags.append(tag)
    for t in (extra or []):
        t = "#" + re.sub(r"[\s#]+", "", str(t))
        if len(t) > 1 and t not in tags:
            tags.append(t)
    for t in BASE_TAGS.get(mode, BASE_TAGS["travel"]):
        if len(tags) >= HASHTAG_MAX:
            break
        if t not in tags:
            tags.append(t)
    return tags[:HASHTAG_MAX]


def ensure_shorts(title: str) -> str:
    body = re.sub(r"\s*#shorts\b", "", (title or "").strip(), flags=re.I).strip()
    return f"{body[:TITLE_BODY_MAX].rstrip()} #Shorts"


def _clean_accent(a: str) -> str:
    a = (a or "").strip()
    return a if a in ACCENTS else ""


def _template_copy(mode: str, usp: str, product_name: str, hook: str, seed: Optional[int] = None,
                   region: str = "", signature: str = "") -> Dict[str, Any]:
    rnd = random.Random(seed if seed is not None else hash((mode, usp, product_name)) & 0xFFFF)
    kw = (signature or pick_keyword(mode, usp, product_name)).strip()
    where = (region.split()[-1] if region and region.split() else "") or WHERE_FALLBACK.get(mode, "")
    where = re.sub(r"(특별시|광역시|특별자치시|특별자치도)$", "", where)
    if len(where) > 2:
        where = re.sub(r"[시군구]$", "", where)          # '가평군' → '가평' (제목에선 짧은 지명이 자연스러움)
    title = rnd.choice(TITLE_TEMPLATES.get(mode, TITLE_TEMPLATES["travel"])).format(kw=kw, where=where, iga=_iga(kw))
    title = re.sub(r"\s+", " ", title).strip()
    main, sub = rnd.choice(THUMB_TEMPLATES.get(mode, THUMB_TEMPLATES["travel"]))
    if hook and len(hook) <= THUMB_MAIN_MAX and not is_stale_hook(hook):
        main = hook                       # 씬 1 후킹이 짧으면 썸네일과 영상 첫 문장을 맞춤
    accent = "😱" if main.endswith("?") else "🔥"
    question = rnd.choice(COMMENT_QUESTIONS.get(mode, COMMENT_QUESTIONS["travel"])).format(kw=kw)
    return {"title": ensure_shorts(title), "thumb_main": main, "thumb_sub": sub.format(kw=kw)[:THUMB_SUB_MAX],
            "accent": accent, "comment_question": question, "source": "template"}


RADAR_PATTERNS_IN_PROMPT = 6
RADAR_TAGS_IN_PROMPT = 15
RADAR_TAGS_MIN = 2              # 업로드 해시태그 중 레이더 태그 풀에서 최소 몇 개


def trend_tag_pool(trend: Optional[Dict[str, Any]]) -> List[str]:
    """레이더 해시태그 풀. 업종 무관(viral) 데이터의 태그는 드라마·연예인 등 다른 분야 주제라 쓰지 않음 (제목 구조만 참고)"""
    if not trend or trend.get("vertical") == "viral":
        return []
    return [t["tag"] for t in trend.get("hashtag_pool") or []]


# 사실(USP)로 채울 수 없는 자리: 숫자·가격은 USP에 숫자가 있을 때만, 인물·전문가·브랜드는 항상 제외
_NUMERIC_SLOTS = ("숫자", "가격", "시간", "인원")
_PERSON_SLOTS = ("인물", "전문가", "현지인", "브랜드", "유명인", "집단")


def usable_patterns(trend: Optional[Dict[str, Any]], usp: str = "") -> List[Dict[str, Any]]:
    """프롬프트에 보여 줄 제목 패턴 (사실로 못 채우는 패턴 제외 → 모델이 '상위 1%'처럼 수치를 지어내지 않게)"""
    has_number = bool(re.search(r"\d", usp or ""))
    out = []
    for pt in (trend or {}).get("title_patterns") or []:
        slots = re.findall(r"\{([^}]+)\}", pt.get("template", ""))
        if any(any(k in s for k in _PERSON_SLOTS) for s in slots):
            continue
        if not has_number and any(any(k in s for k in _NUMERIC_SLOTS) for s in slots):
            continue
        out.append(pt)
    return out[:RADAR_PATTERNS_IN_PROMPT]


def radar_block(trend: Optional[Dict[str, Any]], usp: str = "") -> str:
    """레이더 다이제스트 → 카피 프롬프트의 필수 맥락 블록 (패턴은 구조만, 원문 제목 없음)"""
    if not trend:
        return ""
    pats = usable_patterns(trend, usp)
    hooks = (trend.get("first3s_hooks") or [])[:3]
    words = [w["word"] for w in (trend.get("emotion_words") or [])[:10]]
    tags = trend_tag_pool(trend)[:RADAR_TAGS_IN_PROMPT]
    lines = [f"[떡상 레이더 최신 데이터 - 필수 반영] {str(trend.get('built_at', ''))[:10]} 기준, 최근 {trend.get('days', 30)}일 "
             f"떡상 쇼츠 {trend.get('n_outliers', 0)}개(최고 {trend.get('top_ratio', 0)}배) 분석"]
    for i, pt in enumerate(pats, 1):
        tag = "[범용 차용] " if pt.get("cross") else ""
        lines.append(f"  제목 패턴 {i}. {tag}\"{pt['template']}\" ({pt.get('ratio', 0)}배) - {pt.get('why', '')}")
    for h in hooks:
        lines.append(f"  첫 3초 훅 구조: \"{h['template']}\" ({h.get('ratio', 0)}배)")
    if words:
        lines.append("  떡상 제목 감정·호기심 단어: " + ", ".join(words))
    if tags:
        lines.append("  떡상 해시태그 풀(배수 가중 순): " + " ".join(tags))
    if trend.get("vertical") == "viral" or any(pt.get("cross") for pt in pats):
        lines.append("  ([범용 차용]·업종 무관 패턴은 다른 분야 떡상 영상의 '문장 구조'만 참고합니다. 소재·주제는 가져오지 말고 "
                     "자리표시자에 이번 업장의 사실을 넣으세요. 업종 전용 패턴이 사실과 맞으면 그쪽을 우선)")
    lines.append("[결합 규칙] title은 '지역·매장 특성 + 시그니처(사실 근거의 대표 메뉴·특징) + 위 제목 패턴 중 하나의 구조'를 "
                 "새 문장으로 합치세요. 패턴의 {장소}/{특징} 같은 자리에는 이번 가게의 사실만 넣고, 패턴에 없는 사실을 지어내지 마세요. "
                 "고른 제목 패턴 번호를 pattern_used(정수, 맞는 게 없으면 0)에 적으세요. "
                 f"hashtags는 해시태그 풀에서 이번 영상에 맞는 것 {RADAR_TAGS_MIN}~4개 + 가게·메뉴·지역 고유 태그 2~3개.")
    return "\n".join(lines)


def _radar_tags(trend: Optional[Dict[str, Any]], mode: str, usp: str, product_name: str, n: int) -> List[str]:
    """레이더 태그 풀에서 이번 영상과 같은 갈래(USP·업종 키워드와 겹치거나 상위 일반 태그) n개"""
    if not trend:
        return []
    pool = trend_tag_pool(trend)
    text = f"{usp} {product_name}"
    related = [t for t in pool if any(w in text for w in re.findall(r"[가-힣A-Za-z]{2,}", t[1:]))]
    return list(dict.fromkeys(related + pool))[:n]


def radar_summary(trend: Optional[Dict[str, Any]], pattern: Optional[Dict[str, Any]], used_tags: List[str]) -> Optional[Dict[str, Any]]:
    if not trend:
        return None
    return {"vertical": trend.get("vertical"), "built_at": trend.get("built_at"), "age_days": trend.get("age_days"),
            "stale": trend.get("stale"), "fallback_from": trend.get("fallback_from"), "n_outliers": trend.get("n_outliers"),
            "top_ratio": trend.get("top_ratio"), "pattern": pattern, "tags": used_tags}


def make_shorts_copy(mode: str, hook: str = "", usp: str = "", product_name: str = "", api_key: Optional[str] = None,
                     mock: bool = False, seed: Optional[int] = None, trend: Optional[Dict[str, Any]] = None,
                     region: str = "", signature: str = "") -> Dict[str, Any]:
    """
    → {title('… #Shorts'), thumb_main, thumb_sub, accent, hashtags[5~7], source('gemini'|'template'), rejected[], radar}
    trend: 떡상 레이더 다이제스트(core/radar/trends.py) → 프롬프트 필수 맥락 (제목 패턴·감정어·해시태그 풀)
    region·signature: 지역(예: 경기 가평군)·시그니처(대표 메뉴·특징, 사실). 제목 = 지역/매장 특성 + 시그니처 + 레이더 패턴
    숙소 모드는 상품명(숙소명)을 제목·썸네일에 넣지 않음 (대본에서 숨기는 원칙과 같음: 댓글 링크로 유도)
    """
    mode = mode if mode in TITLE_TEMPLATES else "travel"
    signature = (signature or pick_keyword(mode, usp, product_name)).strip()
    base = _template_copy(mode, usp, product_name, hook, seed, region=region, signature=signature)
    radar_tags = _radar_tags(trend, mode, usp, product_name, RADAR_TAGS_MIN + 1)
    base["hashtags"] = hashtags_for(mode, usp, product_name, extra=radar_tags)
    base["rejected"] = []
    chosen = None   # 레이더 제목 패턴은 Gemini가 사실로 새 문장을 쓸 때만 사용 (자리표시자 기계적 치환은 어색한 문장이 됨)
    base["radar"] = radar_summary(trend, chosen, [t for t in base["hashtags"] if t in radar_tags])
    if mock or not api_key:
        return base
    name_rule = ("숙소 이름은 쓰지 마세요 (궁금증으로 댓글 링크를 누르게)" if mode == "travel"
                 else f"이름이 필요하면 '{product_name}'만 그대로" if product_name else "")
    prompt = f"""
유튜브 쇼츠 업로드용 카피를 만드세요. 모드: {dict(travel='숙소 소개', commerce='제품 리뷰', owner='동네 가게 소개')[mode]}
영상 첫 문장(후킹): {hook or '(없음)'}
사실 근거(USP, 여기 있는 사실만 쓸 수 있음): {usp or '(없음)'}
지역: {region or '(없음)'} / 시그니처(대표 메뉴·특징): {signature or '(없음)'}{(' / 가게 이름: ' + product_name) if mode == 'owner' and product_name else ''}
{radar_block(trend, usp)}

1) title: 손실 회피·극단적 호기심형 한 줄 ({TITLE_BODY_MAX}자 이내, '#Shorts'는 쓰지 마세요). 단순 설명형 금지.
   예: "가평에 이런 맥주얼음 맥주가 숨겨져 있었다니?!", "모르면 무조건 손해! 강릉 오션뷰 숙소의 정체", "이 가격에 이 뷰가 나온다고?"
   금지: "여길 아직도 안 가봤다고?", "여길 모른다고?", "여길 안 간다고?" 같은 진부한 문구
2) thumb_main: 썸네일 메인 카피 ({THUMB_MAIN_MAX}자 이내, 아주 짧고 강하게. 예: "이게 숨어 있었다고?", "모르면 무조건 손해!")
3) thumb_sub: 썸네일 서브 카피 ({THUMB_SUB_MAX}자 이내, USP 핵심 명사구. 예: "오션뷰 인피니티 풀")
4) accent: {list(ACCENTS)} 중 하나 (메인 카피 끝에 붙일 포인트)
5) hashtags: 핵심 키워드 해시태그 5~7개 (#숙소추천 #감성숙소 #풀빌라 처럼, 띄어쓰기 없이)
6) comment_question: 업로드 직후 채널이 다는 첫 댓글의 참여 유도 질문 한 줄 ({COMMENT_QUESTION_MAX}자 이내, 물음표 포함, 끝에 👇 가능).
   시청자가 한 단어로 답하기 쉬운 선택·경험 질문 (예: "여기 가면 제일 먼저 시킬 메뉴는? 👇", "누구랑 오고 싶어요? 친구 태그 👇").
   가격·순위·인기 같은 사실 주장 금지
규칙: 예약 폭주·품절·완판·마감 임박·1위·최저가·할인율·인원·시간 같은 사실 주장은 USP에 적힌 것만. 없으면 쓰지 마세요.
'끝판왕', '인생 숙소', 'ㄷㄷ', '미쳤다' 같은 감탄은 괜찮아요. {name_rule}
JSON만 출력: {{"title": "...", "thumb_main": "...", "thumb_sub": "...", "accent": "...", "hashtags": ["#...", "..."], "comment_question": "...", "pattern_used": 0}}
"""
    try:
        from google import genai
        from google.genai import types
        from core.guardrail import validate_model_call
        client = genai.Client(api_key=api_key)
        res = client.models.generate_content(
            model=validate_model_call("gemini-3.6-flash", category="llm"), contents=[prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.9))
        raw = json.loads(res.text)
    except Exception as e:
        logger.warning(f"쇼츠 카피 생성 실패 → 템플릿 사용: {e}")
        return base
    out = dict(base)
    out["source"] = "gemini"
    for field, limit in (("title", TITLE_BODY_MAX), ("thumb_main", THUMB_MAIN_MAX), ("thumb_sub", THUMB_SUB_MAX)):
        val = re.sub(r"\s*#shorts\b", "", str(raw.get(field) or ""), flags=re.I).strip()
        bad = find_hype_claim(val, usp) if val else "빈 값"
        if not bad and is_stale_hook(val):
            bad = "진부한 후킹"
        if mode == "travel" and product_name and product_name.strip() and product_name.strip() in val:
            bad = "숙소명 노출"
        if bad or len(val) > limit + 4:
            out["rejected"].append(f"{field}: '{val}' ({bad or '너무 김'})")
            continue
        out[field] = ensure_shorts(val) if field == "title" else val[:limit]
    q = str(raw.get("comment_question") or "").strip()
    q_bad = (None if not q else find_hype_claim(q, usp) or ("물음표 없음" if "?" not in q else None)
             or ("너무 김" if len(q) > COMMENT_QUESTION_MAX + 5 else None)
             or ("숙소명 노출" if mode == "travel" and product_name and product_name.strip() in q else None))
    if q_bad:
        out["rejected"].append(f"comment_question: '{q}' ({q_bad})")
    elif q:
        out["comment_question"] = q
    out["accent"] = _clean_accent(raw.get("accent")) or base["accent"]
    if out["accent"] in ("?!", "ㄷㄷ") and re.search(r"[?!ㄷ]$", out["thumb_main"]):
        out["accent"] = "😱" if out["thumb_main"].endswith("?") else "🔥"     # '모른다고??!'처럼 부호가 겹치지 않게
    tags = [t for t in (raw.get("hashtags") or []) if isinstance(t, str) and not find_hype_claim(t, usp)]
    if mode == "travel" and product_name:
        tags = [t for t in tags if _norm_tag(product_name) not in _norm_tag(t)]
    pool = set(trend_tag_pool(trend))
    from_pool = [("#" + t.lstrip("#")) for t in tags if ("#" + t.lstrip("#")) in pool]
    if pool and len(from_pool) < RADAR_TAGS_MIN:           # 레이더 태그가 부족하면 풀 상위에서 보충
        tags = list(dict.fromkeys(tags + [t for t in radar_tags if t not in tags]))[:HASHTAG_MAX]
    out["hashtags"] = hashtags_for(mode, usp, product_name, extra=tags)
    pats = usable_patterns(trend, usp)          # 프롬프트에 보여 준 것과 같은 목록으로 번호 해석
    try:
        k = int(raw.get("pattern_used") or 0)
    except (TypeError, ValueError):
        k = 0
    chosen_g = pats[k - 1] if 1 <= k <= min(len(pats), RADAR_PATTERNS_IN_PROMPT) and "title" not in " ".join(out["rejected"]) else chosen
    out["radar"] = radar_summary(trend, chosen_g, [t for t in out["hashtags"] if t in pool])
    if out["rejected"]:
        logger.info(f"[쇼츠 카피 검증] 근거 없는 주장·규칙 위반 → 템플릿 유지: {out['rejected']}")
    if len([r for r in out["rejected"] if r.split(":")[0] in ("title", "thumb_main", "thumb_sub")]) == 3:
        out["source"] = "template"
    return out


def _norm_tag(s: str) -> str:
    return re.sub(r"[\s#'’]+", "", (s or "").lower())


def build_shorts_description(mode: str, title: str, hashtags: List[str], disclosure_short: str = "") -> str:
    """
    1줄: 제목 그대로(【 】로 강조 - 유튜브 설명란은 굵은 글씨 미지원) / 2줄: 제휴 고지 / 가운데: 📌 첫 댓글 안내 / 끝: 해시태그
    """
    body = re.sub(r"\s*#shorts\b", "", title or "", flags=re.I).strip()
    lines = [f"【 {body} 】"]
    if disclosure_short:
        lines.append(disclosure_short.strip())
    lines += ["", PINNED_NOTICE.get(mode, PINNED_NOTICE["travel"]), "", " ".join(hashtags[:HASHTAG_MAX])]
    return "\n".join(lines).strip()


def naver_map_link(keyword: str) -> str:
    return NAVER_MAP_SEARCH.format(q=quote((keyword or "").strip())) if (keyword or "").strip() else ""


def build_first_comment(mode: str, question: str, hashtags: Optional[List[str]] = None, *, search_keyword: str = "",
                        link: str = "", disclosure_full: str = "") -> str:
    """
    업로드 직후 채널 계정으로 다는 첫 댓글 (고정은 스튜디오에서 한 번):
    참여 유도 질문 → 사장님: 네이버 검색 안내 + 네이버 지도 링크 / 숙소·쇼핑: 제휴 링크 + 공정위 고지 전문 → 핵심 해시태그 3개
    """
    blocks = [question.strip()] if (question or "").strip() else []
    if mode == "owner":
        kw = (search_keyword or "").strip()
        if kw:
            blocks.append(f"📍 네이버에서 '{kw}' 검색하면 위치·영업시간·메뉴를 바로 볼 수 있어요\n🗺️ 네이버 지도: {naver_map_link(kw)}")
    elif link:
        what = "숙소 상세 정보·예약" if mode == "travel" else "제품 상세 정보·구매"
        blocks.append(f"🔗 {what} 링크: {link}" + (f"\n{disclosure_full.strip()}" if (disclosure_full or "").strip() else ""))
    tags = " ".join(t if t.startswith("#") else "#" + t for t in (hashtags or [])[:COMMENT_TAGS])
    if tags:
        blocks.append(tags)
    return "\n\n".join(blocks).strip()
