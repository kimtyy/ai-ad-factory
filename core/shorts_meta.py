"""
core/shorts_meta.py - 쇼츠 업로드 메타데이터(제목·설명·해시태그) + 썸네일 카피(메인·서브·포인트)

- 제목: 손실 회피·극단적 호기심형 ('여길 안 간다고? … ㄷㄷ', '모르면 손해! …'), 끝에 #Shorts
- 근거 없는 사실 주장은 금지: 예약 폭주·품절·완판·1위·최저가·할인율 같은 표현은 USP에 적혀 있을 때만 허용
  (표시광고법상 기만 광고 + 제휴 계정 정지 사유). '끝판왕'·'인생 숙소' 같은 주관적 감탄은 허용
- Gemini가 있으면 카피 생성, 없거나(mock·키 없음) 검증에서 걸리면 근거 안전한 템플릿으로 대체
- 설명란: 1줄 제목 → 제휴 고지(공정위 추천·보증 심사지침: 게시물 첫 부분) → 📌 첫 댓글 안내 → 해시태그 5~7개
"""
import json
import logging
import random
import re
from typing import Any, Dict, List, Optional

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
# 근거 없이도 쓸 수 있는 템플릿 (사실 주장 없음, {kw}는 USP·상품명에서 뽑은 키워드)
TITLE_TEMPLATES = {
    "travel": ["여길 아직도 안 가봤다고? {kw} 끝판왕 ㄷㄷ", "모르면 무조건 손해! {kw} 인생 숙소", "이 {kw}를 놓친다고? 진짜 손해예요 🔥",
               "여기 진짜 한국 맞아요? {kw} 클라스 ㄷㄷ"],
    "commerce": ["이거 모르고 사면 손해! {kw} ㄷㄷ", "아직도 이거 없이 산다고? {kw} 끝판왕", "써 보고 놀란 {kw} 이 정도일 줄은 🔥"],
    "owner": ["여길 아직도 모른다고? {kw} ㄷㄷ", "모르면 손해! 동네 숨은 {kw}", "이 동네 사는데 여길 안 가봤다고? 🔥"],
}
THUMB_TEMPLATES = {
    "travel": [("여길 안 간다고?", "{kw}"), ("모르면 손해!", "{kw}"), ("이게 한국이라고?", "{kw}")],
    "commerce": [("모르면 손해!", "{kw}"), ("아직도 없어?", "{kw}")],
    "owner": [("여길 모른다고?", "{kw}"), ("동네 숨은 찐맛집", "{kw}")],
}
KW_FALLBACK = {"travel": "숙소", "commerce": "꿀템", "owner": "가게"}


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


def _template_copy(mode: str, usp: str, product_name: str, hook: str, seed: Optional[int] = None) -> Dict[str, Any]:
    rnd = random.Random(seed if seed is not None else hash((mode, usp, product_name)) & 0xFFFF)
    kw = pick_keyword(mode, usp, product_name)
    title = rnd.choice(TITLE_TEMPLATES.get(mode, TITLE_TEMPLATES["travel"])).format(kw=kw)
    main, sub = rnd.choice(THUMB_TEMPLATES.get(mode, THUMB_TEMPLATES["travel"]))
    main = main if not hook or len(hook) > THUMB_MAIN_MAX else hook   # 씬 1 후킹이 짧으면 썸네일과 영상 첫 문장을 맞춤
    accent = "😱" if main.endswith("?") else "🔥"
    return {"title": ensure_shorts(title), "thumb_main": main, "thumb_sub": sub.format(kw=kw)[:THUMB_SUB_MAX],
            "accent": accent, "source": "template"}


def make_shorts_copy(mode: str, hook: str = "", usp: str = "", product_name: str = "", api_key: Optional[str] = None,
                     mock: bool = False, seed: Optional[int] = None) -> Dict[str, Any]:
    """
    → {title('… #Shorts'), thumb_main, thumb_sub, accent, hashtags[5~7], source('gemini'|'template'), rejected[]}
    숙소 모드는 상품명(숙소명)을 제목·썸네일에 넣지 않음 (대본에서 숨기는 원칙과 같음: 댓글 링크로 유도)
    """
    mode = mode if mode in TITLE_TEMPLATES else "travel"
    base = _template_copy(mode, usp, product_name, hook, seed)
    base["hashtags"] = hashtags_for(mode, usp, product_name)
    base["rejected"] = []
    if mock or not api_key:
        return base
    name_rule = ("숙소 이름은 쓰지 마세요 (궁금증으로 댓글 링크를 누르게)" if mode == "travel"
                 else f"이름이 필요하면 '{product_name}'만 그대로" if product_name else "")
    prompt = f"""
유튜브 쇼츠 업로드용 카피를 만드세요. 모드: {dict(travel='숙소 소개', commerce='제품 리뷰', owner='동네 가게 소개')[mode]}
영상 첫 문장(후킹): {hook or '(없음)'}
사실 근거(USP, 여기 있는 사실만 쓸 수 있음): {usp or '(없음)'}

1) title: 손실 회피·극단적 호기심형 한 줄 ({TITLE_BODY_MAX}자 이내, '#Shorts'는 쓰지 마세요). 단순 설명형 금지.
   예: "여길 안 간다고? 국내 끝판왕 풀빌라 ㄷㄷ", "모르면 무조건 손해! 인생 숙소", "이 가격에 이 뷰가 나온다고?"
2) thumb_main: 썸네일 메인 카피 ({THUMB_MAIN_MAX}자 이내, 아주 짧고 강하게. 예: "여길 안 간다고?", "모르면 손해!")
3) thumb_sub: 썸네일 서브 카피 ({THUMB_SUB_MAX}자 이내, USP 핵심 명사구. 예: "오션뷰 인피니티 풀")
4) accent: {list(ACCENTS)} 중 하나 (메인 카피 끝에 붙일 포인트)
5) hashtags: 핵심 키워드 해시태그 5~7개 (#숙소추천 #감성숙소 #풀빌라 처럼, 띄어쓰기 없이)
규칙: 예약 폭주·품절·완판·마감 임박·1위·최저가·할인율·인원·시간 같은 사실 주장은 USP에 적힌 것만. 없으면 쓰지 마세요.
'끝판왕', '인생 숙소', 'ㄷㄷ', '미쳤다' 같은 감탄은 괜찮아요. {name_rule}
JSON만 출력: {{"title": "...", "thumb_main": "...", "thumb_sub": "...", "accent": "...", "hashtags": ["#...", "..."]}}
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
        if mode == "travel" and product_name and product_name.strip() and product_name.strip() in val:
            bad = "숙소명 노출"
        if bad or len(val) > limit + 4:
            out["rejected"].append(f"{field}: '{val}' ({bad or '너무 김'})")
            continue
        out[field] = ensure_shorts(val) if field == "title" else val[:limit]
    out["accent"] = _clean_accent(raw.get("accent")) or base["accent"]
    tags = [t for t in (raw.get("hashtags") or []) if isinstance(t, str) and not find_hype_claim(t, usp)]
    out["hashtags"] = hashtags_for(mode, usp, product_name, extra=tags)
    if out["rejected"]:
        logger.info(f"[쇼츠 카피 검증] 근거 없는 주장·규칙 위반 → 템플릿 유지: {out['rejected']}")
    if all(r.split(":")[0] in ("title", "thumb_main", "thumb_sub") for r in out["rejected"]) and len(out["rejected"]) == 3:
        out["source"] = "template"
    return out


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
