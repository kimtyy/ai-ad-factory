"""
core/hashtags.py - 영상 1개당 단일 태그 풀 (설명란 · 첫 댓글 · 유튜브 검색 태그가 같은 소스를 쓴다)

예전에는 설명란(seo_generator), 첫 댓글(shorts_meta.hashtags_for), 검색 태그(DEFAULT_TAG_POOLS/UPLOAD_TAGS)가 각자 다른 템플릿이라
내용이 제각각이었고 편의점 영상에 '#로컬맛집 #데이트코스 #숨은맛집' 같은 식당 태그가 섞였다.

build_tag_set() 한 번의 결과(TagSet)에서
  - core    : 핵심 태그 5~7개 (#붙임) → 설명란 해시태그 줄
  - comment : core의 앞 3개          → 첫 댓글 해시태그 (같은 풀의 부분집합, 순서 동일)
  - pool    : core를 앞에 둔 검색 태그 15~20개 (# 없음) → 유튜브 tags 필드
를 만든다. 업종(industry)별 사전과 '이 업종엔 쓰지 않는 단어' 가드로 편의점에 맛집 태그가 섞이는 일을 막는다.
"""
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional

CORE_MAX = 7
COMMENT_MAX = 7          # 첫 댓글 해시태그 = 설명란과 같은 핵심 태그 전부 (댓글에서도 눌러 채널·상호 태그 페이지로 이동)
POOL_MAX = 20
POOL_MIN = 15
ALWAYS_TAG = "shorts"

RETAIL_WORDS = ("편의점", "마트", "슈퍼", "소매", "무인", "문구", "잡화", "식료품", "할인점", "이마트24", "gs25", "세븐일레븐",
                "미니스톱", "다이소", "올리브영")
RETAIL_WEAK = ("1+1", "2+1", "행사")        # 식당·카페 문맥이 없을 때만 소매점 신호로 본다
CAFE_WORDS = ("카페", "커피", "디저트", "베이커리", "브런치", "빵집", "로스터")
FOOD_WORDS = ("맛집", "식당", "메뉴", "고기", "국밥", "찌개", "치킨", "피자", "분식", "횟집", "술집", "식사", "밥집", "삼겹", "구이", "갈비",
              "곱창", "냉면", "국수", "초밥", "족발", "보쌈", "뷔페", "한식", "중식", "일식", "양식", "해장", "맥주", "와인")

# 업종별 태그 사전 (base: 핵심에 우선 채움 / extended: 검색 태그를 15~20개로 채움)
INDUSTRY_TAGS: Dict[str, Dict[str, List[str]]] = {
    "retail": {
        "noun": "편의점",
        "base": ["편의점", "편의점추천", "동네편의점", "편의점꿀템", "생활편의"],
        "extended": ["편의점신상", "편의점할인", "행사상품", "생필품", "야식", "간식", "편의점음료", "꿀템", "동네생활", "쇼츠"],
    },
    "restaurant": {
        "noun": "맛집",
        "base": ["맛집", "맛집추천", "동네맛집", "로컬맛집", "점심맛집"],
        "extended": ["맛스타그램", "저녁맛집", "먹방", "숨은맛집", "가성비맛집", "데이트코스", "먹스타그램", "오늘뭐먹지", "쇼츠"],
    },
    "cafe": {
        "noun": "카페",
        "base": ["카페", "카페추천", "동네카페", "카페투어", "디저트"],
        "extended": ["감성카페", "카페스타그램", "커피", "데이트코스", "카공", "브런치", "쇼츠"],
    },
    "stay": {
        "noun": "숙소",
        "base": ["숙소추천", "국내여행", "여행", "주말여행", "감성숙소"],
        "extended": ["펜션추천", "호캉스", "힐링여행", "여행브이로그", "가볼만한곳", "여행지추천", "데이트코스", "쇼츠"],
    },
    "general_store": {
        "noun": "가게",
        "base": ["동네가게", "가게추천", "소상공인", "동네추천", "로컬"],
        "extended": ["단골가게", "골목상권", "동네핫플", "응원합니다", "일상", "쇼츠"],
    },
    "commerce": {
        "noun": "",
        "base": ["추천템", "꿀템", "살림템", "생활꿀팁", "쇼핑추천"],
        "extended": ["리뷰", "상품리뷰", "가성비템", "신박템", "자취템", "인기템", "필수템", "쇼츠"],
    },
    "book": {
        "noun": "책방",
        "base": ["책추천", "동네책방", "독립서점", "오늘의책", "북스타그램"],
        "extended": ["독서", "도서추천", "책방투어", "문장수집", "독서기록", "인생책", "쇼츠"],
    },
}

# 업종별로 쓰지 않는 단어 (부분 문자열). 키: 업종 → 이 업종 태그에서 제외할 표현
BLOCKED: Dict[str, List[str]] = {
    "retail": ["맛집", "데이트", "카페", "핫플", "먹방", "맛스타", "먹스타", "레스토랑", "브런치", "디저트", "술집", "숨은", "가볼만한", "오늘뭐",
               "숙소", "여행", "펜션", "책방", "독립서점"],
    "restaurant": ["편의점", "원플러스원", "투플러스원", "행사상품", "숙소", "펜션", "여행", "책방", "독립서점", "쿠팡", "스마트스토어"],
    "cafe": ["편의점", "원플러스원", "투플러스원", "맛집추천", "숙소", "펜션", "여행", "책방", "독립서점", "쿠팡"],
    "stay": ["편의점", "원플러스원", "맛집", "카페", "책방", "독립서점", "쿠팡", "먹방"],
    "general_store": ["숙소", "펜션", "여행", "책방", "독립서점", "쿠팡", "내돈내산"],
    "commerce": ["맛집", "카페", "숙소", "펜션", "여행", "편의점", "책방", "데이트", "핫플", "내돈내산"],   # 제휴(수수료) 영상에 '내돈내산'은 모순
    "book": ["맛집", "편의점", "숙소", "펜션", "원플러스원", "쿠팡", "데이트", "핫플"],
}

# 이 목록(체인 상호)은 상호의 첫 어절을 브랜드 태그로 쓴다
CHAIN_BRANDS = ("이마트24", "gs25", "cu", "세븐일레븐", "미니스톱", "다이소", "올리브영", "스타벅스", "투썸플레이스", "메가커피", "컴포즈커피")


@dataclass
class TagSet:
    industry: str
    core: List[str] = field(default_factory=list)       # '#태그' 5~7개 (설명란)
    comment: List[str] = field(default_factory=list)    # '#태그' 앞 3개 (첫 댓글)
    pool: List[str] = field(default_factory=list)       # '태그' 15~20개 (유튜브 검색 태그, core가 앞)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Optional["TagSet"]:
        if not d or not d.get("core"):
            return None
        return cls(industry=str(d.get("industry") or ""), core=list(d.get("core") or []), comment=list(d.get("comment") or []),
                   pool=list(d.get("pool") or []))


def _clean(tag: str) -> str:
    return re.sub(r"[#\s,]+", "", str(tag or "")).strip()


def _has(text: str, words: Iterable[str]) -> bool:
    t = (text or "").lower()
    return any(w.lower() in t for w in words)


def detect_industry(mode: str, business_type: str = "", name: str = "", usp: str = "", concept: str = "",
                    search_keyword: str = "") -> str:
    """영상의 업종 → INDUSTRY_TAGS 키. 편의점/마트 판별이 식당·카페 판별보다 먼저 (가장 흔한 오분류 방지)"""
    if mode == "commerce":
        return "commerce"
    if mode == "book":
        return "book"
    if mode == "travel":
        return "stay"
    text = f"{business_type} {name} {usp} {concept} {search_keyword}"
    bt = (business_type or "").strip().lower()
    eatery = _has(text, CAFE_WORDS) or _has(text, FOOD_WORDS) or bt in ("food", "restaurant", "cafe", "음식점", "카페")
    if _has(text, RETAIL_WORDS) or (_has(text, RETAIL_WEAK) and not eatery):
        return "retail"
    if bt in ("stay", "숙소", "펜션") or "숙소" in bt or "펜션" in bt:
        return "stay"
    if _has(text, CAFE_WORDS) or bt in ("cafe", "카페"):
        return "cafe"
    if bt in ("food", "restaurant", "음식점") or _has(text, FOOD_WORDS):
        return "restaurant"
    return "general_store"


def guard_tags(tags: Iterable[str], industry: str) -> List[str]:
    """업종에 맞지 않는 태그 제거 (예: 편의점에서 '로컬맛집'·'데이트코스'). 반환은 # 없는 태그, 중복 제거"""
    blocked = BLOCKED.get(industry, [])
    out: List[str] = []
    for t in tags:
        c = _clean(t)
        if not c or len(c) < 2:
            continue
        if any(b in c for b in blocked):
            continue
        if c.lower() not in [o.lower() for o in out]:
            out.append(c)
    return out


def channel_brand_tag(mode: str) -> List[str]:
    """우리 채널 이름 해시태그 (.env YOUTUBE_CHANNEL_STORE 등, 예: '샵스토리' → '샵스토리'). 채널 태그 페이지에 영상이 모이고,
    댓글·설명란에서 눌러 우리 영상 모음으로 올 수 있다. 채널명이 없거나 모드에 채널이 없으면 빈 목록"""
    try:
        from core.youtube_upload import expected_channel
        name = expected_channel(mode)
    except Exception:
        return []
    tag = re.sub(r"[^0-9A-Za-z가-힣]+", "", name or "")
    return [tag] if 2 <= len(tag) <= 22 else []


def _brand_tags(name: str) -> List[str]:
    n = re.sub(r"\s+", " ", (name or "").strip())
    if not n or n in ("촬영 영상 쇼츠",):
        return []
    tokens = n.split(" ")
    full = re.sub(r"[^0-9A-Za-z가-힣]+", "", n)
    out: List[str] = []
    if 2 <= len(full) <= 22:
        out.append(full)
    first = re.sub(r"[^0-9A-Za-z가-힣]+", "", tokens[0])
    if len(tokens) >= 2 and len(first) >= 2 and (first.lower() in CHAIN_BRANDS or tokens[-1].endswith(("점", "지점"))):
        out.append(first)
    return out


def _region_noun(region: str) -> str:
    toks = [t for t in re.split(r"\s+", (region or "").strip()) if t]
    if not toks:
        return ""
    r = toks[-1]
    r = re.sub(r"(특별시|광역시|특별자치시|특별자치도)$", "", r)
    if len(r) > 2:
        r = re.sub(r"[시군구]$", "", r)
    return r if 2 <= len(r) <= 6 and re.match(r"^[가-힣]+$", r) else ""


_CARD = re.compile(r"[가-힣A-Za-z0-9]{2,12}(?:사랑카드|지역화폐|상품권|카드|페이|쿠폰)")


def _concept_tags(industry: str, text: str) -> List[str]:
    out: List[str] = []
    if re.search(r"1\s*\+\s*1|원\s*플러스\s*원", text):
        out.append("원플러스원")
    if re.search(r"2\s*\+\s*1|투\s*플러스\s*원", text):
        out.append("투플러스원")
    if industry == "retail":
        for m in _CARD.findall(text):
            out.append(m)
        if re.search(r"행사", text) or out:
            out.insert(0, "편의점행사")
    return out


def _keyword_tags(text: str) -> List[str]:
    """USP·컨셉 문장에 든 키워드 → 태그 (shorts_meta.KEYWORD_TAGS 사전). 업종에 안 맞는 건 guard_tags가 거른다"""
    from core.shorts_meta import KEYWORD_TAGS
    out: List[str] = []
    for words, tag in KEYWORD_TAGS:
        if any(w in text for w in words):
            out.append(tag.lstrip("#"))
    return out


def build_tag_set(mode: str, *, business_type: str = "", name: str = "", usp: str = "", concept: str = "", region: str = "",
                  search_keyword: str = "", extra: Iterable[str] = (), trend_tags: Iterable[str] = ()) -> TagSet:
    """영상 1개의 태그 풀을 만든다 (설명란·첫 댓글·검색 태그가 모두 이 결과를 쓴다). 같은 입력 → 같은 결과(결정적)."""
    industry = detect_industry(mode, business_type, name, usp, concept, search_keyword)
    spec = INDUSTRY_TAGS[industry]
    text = f"{name} {usp} {concept} {search_keyword}"

    ordered: List[str] = []
    ordered += _brand_tags(name if mode != "travel" else "")          # 숙소명은 영상에서 숨기므로 태그에도 쓰지 않음
    ordered += channel_brand_tag(mode)                                  # 우리 채널 태그 (#샵스토리): 상호 바로 다음, 설명란 앞 3개 안
    ordered += guard_tags(extra, industry)                 # 모델이 낸 이 영상 고유 태그(메뉴·특징): 업종 가드를 통과한 것만
    ordered += guard_tags(list(trend_tags)[:2], industry)  # 떡상 레이더 태그 최소 2개
    reg = _region_noun(region)
    if reg and spec["noun"]:
        ordered.append(f"{reg}{spec['noun']}")
    ordered += list(spec["base"][:1])                     # 업종 대표 태그(#편의점 등)
    ordered += _concept_tags(industry, text)
    ordered += _keyword_tags(f"{usp} {concept}")           # USP 키워드가 있으면 일반 기본 태그보다 먼저
    ordered += list(spec["base"][1:])

    core = guard_tags(ordered, industry)
    # 핵심 태그: 상호 → 지역 → 컨셉 → 업종 기본 순으로 앞에서 CORE_MAX-1개 + #shorts
    core = [t for t in core if t.lower() != ALWAYS_TAG][: CORE_MAX - 1] + [ALWAYS_TAG]

    pool: List[str] = list(core)
    for src in (guard_tags(extra, industry), guard_tags(trend_tags, industry), guard_tags(spec["extended"], industry),
                guard_tags(spec["base"], industry)):
        for t in src:
            if t.lower() not in [p.lower() for p in pool]:
                pool.append(t)
    pool = pool[:POOL_MAX]
    return TagSet(industry=industry, core=["#" + t for t in core], comment=["#" + t for t in core[:COMMENT_MAX]], pool=pool)


def replace_hashtag_line(description: str, core: List[str]) -> str:
    """설명란 안의 기존 해시태그 줄(# 토큰만 있는 줄)을 지우고 맨 끝에 core 한 줄을 둔다 (LLM이 다른 태그를 써도 한 소스로 통일)"""
    lines = (description or "").splitlines()
    kept = [ln for ln in lines if not (ln.strip() and all(tok.startswith("#") for tok in ln.split()))]
    body = "\n".join(kept).rstrip()
    return (body + "\n\n" if body else "") + " ".join(core)
