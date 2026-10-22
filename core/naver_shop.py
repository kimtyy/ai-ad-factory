"""
core/naver_shop.py - 네이버 쇼핑 제휴 쇼츠(쇼핑커넥트·스마트스토어)의 데이터 소스: 공식 검색 오픈 API만 사용 (페이지 크롤링·봇 우회 없음)

  shop.json  상품명·최저가(lprice)·판매처·브랜드·카테고리·대표 썸네일
  blog.json  실구매 후기 요약 (제목·요약문에서 후기에 자주 나온 말만 뽑아 대본 근거로 사용)
  image.json 실사용·디테일·공간·구성품 사진 보강 (4중 초상권·워터마크 방어 필터를 그대로 통과한 것만 영상에 사용)

- 엔드포인트: 공식 https://openapi.naver.com/v1/search/{shop|blog|image}.json (X-Naver-Client-Id/Secret) 와
  NCP NAVER API HUB https://naverapihub.apigw.ntruss.com/search/v1/{shop|blog|image} (X-NCP-APIGW-API-KEY-ID/KEY, 같은 키 형식) 둘 다 지원.
  한 번 성공한 쪽을 기억해 다음 호출부터 먼저 쓴다. 키는 .env의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (사장님 모드 지역 검색과 같은 키).
- 인기 꿀템 카드: 카테고리별 후보 키워드를 데이터랩 검색어트렌드(최근 7일 ÷ 앞선 23일)로 줄 세워 상위 5개 → 각 키워드의 shop.json 1위 상품.
  데이터랩이 꺼져 있으면 지어낸 순위 대신 이유를 알려 주고 후보 키워드 순서대로 보여 준다.
- 상품 URL(스마트스토어·쇼핑커넥트·naver.me 단축 링크): 페이지를 열지 않고 링크만 분석한다. 리다이렉트 주소의 상품명 힌트가 있으면 검색에 쓰고,
  없으면 사용자가 검색어를 적게 한다. 사용자가 넣은 링크는 그대로 고정 댓글용 제휴 링크로 보존한다.
"""
import datetime as dt
import html
import logging
import os
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger("ad_factory.naver_shop")

HUB_URL = "https://naverapihub.apigw.ntruss.com/search/v1/{kind}"
LEGACY_URL = "https://openapi.naver.com/v1/search/{kind}.json"
KINDS = ("shop", "blog", "image")
CACHE_TTL_SEC = 3600
MIN_IMAGE_SIDE = 400

HTTP_HINTS = {
    401: "네이버 검색 API 키가 맞지 않아요. .env의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET을 확인해 주세요.",
    403: "이 네이버 API 앱에 '쇼핑·블로그·이미지 검색' 권한이 없어요. 앱 설정에서 검색 API를 추가해 주세요 (키는 그대로).",
    429: "네이버 검색 호출 한도를 넘었어요. 잠시 뒤 다시 시도하거나 아래에서 직접 입력해 주세요.",
}

# 쇼핑커넥트 공정위 대가성 문구 (영상 설명·고정 댓글용 전문 / 영상 화면용 짧은 표기)
CONNECT_DISCLOSURE_FULL = "이 포스팅은 네이버 쇼핑커넥트 활동의 일환으로 판매 발생 시 수수료를 제공받습니다."
CONNECT_DISCLOSURE_SHORT = "네이버 쇼핑커넥트 활동의 일환으로 판매 발생 시 수수료를 제공받습니다"
CONNECT_LINK_PLACEHOLDER = "(네이버 쇼핑커넥트 링크를 붙여 넣으세요)"

# 상품 종류가 새 상품인 shop.json productType (1~3). 4 이상은 중고·단종·판매예정이라 제외
NEW_PRODUCT_TYPES = {"", "1", "2", "3"}


class NaverShopError(RuntimeError):
    pass


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", str(text or "")))).strip()


def _int(value: Any) -> int:
    try:
        return int(str(value).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0


# ================================================================ 1. 공식 API 클라이언트
class NaverShop:
    """네이버 검색 API(쇼핑·블로그·이미지) 얇은 클라이언트. 1시간 메모리 캐시, 실패 이유는 NaverShopError의 한국어 안내문"""

    _preferred: Optional[str] = None            # 마지막으로 성공한 엔드포인트 ('legacy' / 'hub')

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None, timeout: int = 6,
                 session: Any = None, now_fn=time.time):
        self.client_id = (client_id if client_id is not None else os.getenv("NAVER_CLIENT_ID", "")).strip()
        self.client_secret = (client_secret if client_secret is not None else os.getenv("NAVER_CLIENT_SECRET", "")).strip()
        self.timeout = timeout
        self.http = session or requests
        self._now = now_fn
        self._cache: Dict[Tuple, Tuple[float, Dict[str, Any]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _attempts(self, kind: str) -> List[Tuple[str, str, Dict[str, str]]]:
        legacy = ("legacy", LEGACY_URL.format(kind=kind), {"X-Naver-Client-Id": self.client_id, "X-Naver-Client-Secret": self.client_secret})
        hub = ("hub", HUB_URL.format(kind=kind), {"X-NCP-APIGW-API-KEY-ID": self.client_id, "X-NCP-APIGW-API-KEY": self.client_secret})
        return [hub, legacy] if NaverShop._preferred == "hub" else [legacy, hub]

    def _call(self, kind: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(kind)
        if not self.enabled:
            raise NaverShopError("네이버 검색 API 키(NAVER_CLIENT_ID / NAVER_CLIENT_SECRET)가 없어요. .env에 넣거나 아래에서 직접 입력해 주세요.")
        key = (kind, tuple(sorted((k, str(v)) for k, v in params.items())))
        hit = self._cache.get(key)
        if hit and self._now() - hit[0] < CACHE_TTL_SEC:
            return hit[1]
        err = ""
        for name, url, headers in self._attempts(kind):
            try:
                resp = self.http.get(url, params=params, headers=headers, timeout=self.timeout)
            except Exception as e:                      # 네트워크 오류는 다음 엔드포인트로
                err = f"네이버 검색 서버에 연결하지 못했어요 ({type(e).__name__})"
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    err = "네이버 검색 응답을 읽지 못했어요"
                    continue
                NaverShop._preferred = name
                self._cache[key] = (self._now(), data)
                return data
            err = HTTP_HINTS.get(resp.status_code) or f"네이버 검색 오류 (HTTP {resp.status_code})"
        raise NaverShopError(err or "네이버 검색을 호출하지 못했어요")

    # ---- shop.json
    def search_products(self, query: str, display: int = 10, sort: str = "sim") -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        data = self._call("shop", {"query": q, "display": max(1, min(int(display), 100)), "sort": sort})
        return [p for p in (normalize_product(it) for it in data.get("items") or []) if p]

    # ---- blog.json
    def search_reviews(self, query: str, display: int = 20) -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        data = self._call("blog", {"query": q, "display": max(1, min(int(display), 100)), "sort": "sim"})
        return [{"title": _clean(it.get("title")), "description": _clean(it.get("description")), "link": it.get("link", ""),
                 "blogger": _clean(it.get("bloggername")), "postdate": str(it.get("postdate") or "")}
                for it in data.get("items") or []]

    # ---- image.json
    def search_images(self, query: str, display: int = 20, size_filter: str = "large") -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        data = self._call("image", {"query": q, "display": max(1, min(int(display), 100)), "sort": "sim", "filter": size_filter})
        out = []
        for it in data.get("items") or []:
            w, h = _int(it.get("sizewidth")), _int(it.get("sizeheight"))
            link = it.get("link") or ""
            if not link or min(w, h) < MIN_IMAGE_SIDE:
                continue
            out.append({"title": _clean(it.get("title")), "link": link, "thumbnail": it.get("thumbnail") or "", "width": w, "height": h})
        return out


def normalize_product(it: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """shop.json 항목 → 화면·대본용 상품 dict. 새 상품이 아니거나 가격·이미지가 없으면 None"""
    if str(it.get("productType") or "") not in NEW_PRODUCT_TYPES:
        return None
    name = _clean(it.get("title"))
    lprice = _int(it.get("lprice"))
    image = it.get("image") or ""
    if not name or lprice <= 0 or not image:
        return None
    return {"name": name, "lprice": lprice, "hprice": _int(it.get("hprice")), "mall": _clean(it.get("mallName")),
            "brand": _clean(it.get("brand")), "maker": _clean(it.get("maker")), "link": it.get("link", ""), "image": image,
            "product_id": str(it.get("productId") or ""),
            "category": " > ".join(c for c in (_clean(it.get(f"category{i}")) for i in range(1, 5)) if c)}


# ================================================================ 2. 인기 꿀템 카드 (카테고리 → 데이터랩 급상승 키워드 → shop.json 1위)
TREND_CATEGORIES: Dict[str, List[str]] = {
    "🏠 자취·생활 꿀템": ["배수구 클리너", "틈새 청소 솔", "다용도 정리함", "욕실 선반", "무선 청소기", "빨래 건조대", "압축 팩", "냉장고 정리함", "곰팡이 제거제", "미니 건조기"],
    "⛺ 캠핑·차박": ["캠핑 제빙기", "캠핑 랜턴", "접이식 캠핑 테이블", "차박 매트", "휴대용 버너", "캠핑 의자", "아이스박스", "캠핑 전기장판", "화로대", "차박 암막 커튼"],
    "🍳 주방·요리": ["에어프라이어 라이너", "실리콘 뒤집개", "채칼", "멀티 쿠커", "전기 포트", "양념 보관통", "스텐 밀폐용기", "원터치 믹서", "계란 찜기", "진공 밀폐용기"],
    "🍺 홈술·밀키트": ["하이볼 잔", "위스키 얼음 틀", "와인 쿨러", "밀키트 전골", "맥주 거품기", "소주잔 세트", "안주 밀키트", "와인 오프너", "홈바 세트", "탄산수 제조기"],
    "🔥 계절 가전": [],           # 월별 시즌 후보 (아래 SEASONAL_APPLIANCES)
}
SEASONAL_APPLIANCES = {
    "winter": ["전기 히터", "미니 온풍기", "전기 장판", "가습기", "발열 조끼", "전기요", "온수 매트", "손난로", "제습기", "서큘레이터"],
    "summer": ["서큘레이터", "미니 선풍기", "제습기", "에어컨 청소 스프레이", "넥쿨러", "냉감 패드", "제빙기", "휴대용 선풍기", "이동식 에어컨", "냉풍기"],
    "spring_fall": ["공기청정기", "제습기", "가습기", "서큘레이터", "건조기 시트", "로봇청소기", "전기 포트", "빨래 건조기", "스팀 다리미", "무선 청소기"],
}
DEFAULT_CATEGORY = "🏠 자취·생활 꿀템"
CARD_COUNT = 5


def season_of(today: Optional[dt.date] = None) -> str:
    m = (today or dt.date.today()).month
    return "winter" if m in (10, 11, 12, 1, 2) else "summer" if m in (5, 6, 7, 8) else "spring_fall"      # 히터·가습기 수요는 10월부터


def category_keywords(category: str, today: Optional[dt.date] = None) -> List[str]:
    if category == "🔥 계절 가전":
        return list(SEASONAL_APPLIANCES[season_of(today)])
    return list(TREND_CATEGORIES.get(category) or TREND_CATEGORIES[DEFAULT_CATEGORY])


def trending_cards(category: str, shop: Optional[NaverShop] = None, today: Optional[dt.date] = None, count: int = CARD_COUNT) -> Dict[str, Any]:
    """
    → {"cards": [{keyword, name, lprice, mall, image, link, product_id, surge}], "notes": [안내문], "error": 실패 이유, "ranked_by_trend": bool}
    키워드 10개를 5개씩 데이터랩에 두 번 물어 급상승(최근 7일 ÷ 앞선 23일) 순으로 정렬 → 상위 count개 각각의 shop.json 1위 상품.
    트렌드가 안 되면 후보 순서대로 (notes에 이유). 상품 검색까지 실패하면 error
    """
    shop = shop or NaverShop()
    out: Dict[str, Any] = {"cards": [], "notes": [], "error": "", "ranked_by_trend": False}
    if not shop.enabled:
        out["error"] = "네이버 검색 API 키가 없어 인기 꿀템을 찾을 수 없어요. 아래 검색창에 상품명을 직접 입력해 주세요."
        return out
    keywords = category_keywords(category, today)
    stats: Dict[str, Dict[str, float]] = {}
    try:
        from core import stay_curation as sc
        for i in range(0, len(keywords), 5):
            res = sc.fetch_trends(keywords[i:i + 5], shop.client_id, shop.client_secret, today=today)
            if res["ok"]:
                stats.update(res["stats"])
            elif res["error"] and res["error"] not in out["notes"]:
                out["notes"].append(res["error"])
        ranked = sc.rank_keywords(keywords, stats, "season") if stats else keywords
    except Exception as e:                                 # 데이터랩 모듈·호출 실패가 카드 전체를 막지 않게
        logger.info(f"인기 키워드 트렌드 실패: {type(e).__name__}")
        ranked = keywords
    out["ranked_by_trend"] = bool(stats)
    last_error = ""
    for kw in ranked:
        if len(out["cards"]) >= count:
            break
        try:
            items = shop.search_products(kw, display=5)
        except NaverShopError as e:
            last_error = str(e)
            if "키" in last_error or "권한" in last_error:
                break
            continue
        if items:
            top = items[0]
            out["cards"].append({**top, "keyword": kw, "surge": (stats.get(kw) or {}).get("surge")})
    if not out["cards"]:
        out["error"] = last_error or "이 카테고리에서 상품을 찾지 못했어요. 다른 카테고리를 고르거나 직접 검색해 주세요."
    return out


# ================================================================ 3. 입력 해석: 검색어 / 스마트스토어·쇼핑커넥트 URL
SHOP_HOSTS = ("smartstore.naver.com", "brand.naver.com", "shopping.naver.com", "naver.me", "link.naver.com", "m.smartstore.naver.com", "cr.shopping.naver.com")


def is_url(text: str) -> bool:
    return bool(re.match(r"^https?://", (text or "").strip(), re.I))


def is_shop_url(text: str) -> bool:
    if not is_url(text):
        return False
    host = urllib.parse.urlparse(text.strip()).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in SHOP_HOSTS)


def product_id_from_url(url: str) -> str:
    """스마트스토어(/products/123…)·가격비교(/catalog/123…, nvMid=123…) 주소의 상품 번호 (없으면 '')"""
    u = (url or "").strip()
    m = re.search(r"/(?:products|catalog|product)/(\d{6,})", u) or re.search(r"[?&](?:nvMid|nv_mid|productId)=(\d{6,})", u)
    return m.group(1) if m else ""


def resolve_input(text: str, shop: Optional[NaverShop] = None, resolver=None) -> Dict[str, Any]:
    """
    사용자가 검색창에 넣은 값 → {"query": 검색어, "link": 보존할 제휴·상품 링크, "product_id": 상품 번호, "note": 안내, "needs_keyword": bool}
    - 일반 검색어: 그대로 shop.json 검색어
    - URL: 페이지를 열지 않고 단축 링크만 리다이렉트로 풀고(resolver, 기본 core.scraper.resolve_link) 검색어 힌트(검색 결과 주소의 q=)를 얻는다.
      힌트가 없으면 needs_keyword=True로 돌려줘 화면이 '상품명을 직접 적어 주세요'를 안내한다 (페이지 크롤링·봇 우회 없음)
    """
    t = (text or "").strip()
    out = {"query": "", "link": "", "product_id": "", "note": "", "needs_keyword": False}
    if not t:
        return out
    if not is_url(t):
        out["query"] = t
        return out
    out["link"] = t
    final = t
    hint = ""
    try:
        if resolver is None:
            from core.scraper import resolve_link as resolver      # 본문은 받지 않고 Location 헤더만 따라감
        res = resolver(t)
        final = res.get("url") or t
        hint = (res.get("search_query") or "").strip()
    except Exception as e:
        logger.info(f"링크 해석 건너뜀: {type(e).__name__}")
    out["product_id"] = product_id_from_url(final) or product_id_from_url(t)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(final).query)
    for key in ("query", "q", "keyword"):
        if qs.get(key) and not hint:
            hint = _clean(qs[key][0])
    if hint:
        out["query"] = hint
        out["note"] = "링크에서 찾은 검색어로 상품을 찾았어요."
    else:
        out["needs_keyword"] = True
        out["note"] = ("링크는 고정 댓글용으로 그대로 저장했어요. 스마트스토어·쇼핑커넥트 페이지는 자동으로 열지 않아서 "
                       "상품명(검색어)을 한 번만 적어 주세요.")
    return out


# ================================================================ 4. 후기 포인트 (blog.json 요약에서 자주 나온 말)
def review_points(reviews: List[Dict[str, Any]], product_name: str = "", limit: int = 4) -> Dict[str, Any]:
    """
    블로그 후기 제목·요약문 → {"nouns": 후기에 2번 이상 나온 명사(상품명 단어만으로 된 말 제외, 형용사 제외), "count": 후기 수, "snippets": 대표 문장 최대 2}
    수치·효능은 뽑지 않는다 (대본은 이 명사와 상품명·카테고리에 있는 말만 근거로 쓴다)
    """
    texts = [f"{r.get('title', '')} {r.get('description', '')}".strip() for r in (reviews or []) if r]
    out = {"nouns": [], "count": len(texts), "snippets": []}
    if not texts:
        return out
    try:
        from core.store_facts import build_store_facts
        facts = build_store_facts(review_text="\n".join(texts))
        nouns = list(facts.get("review_nouns") or [])
    except Exception:
        nouns = []
    stop = {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", product_name or "")}
    stop |= {"후기", "리뷰", "추천", "내돈내산", "사용", "구매", "제품", "상품", "정말", "진짜", "블로그", "오늘", "이번"}
    # 상품명 단어로만 이루어진 말(예: '배수구 클리너')은 근거가 아니라 상품명이므로 제외, '배수구 냄새'처럼 후기에서 나온 새 말은 유지
    out["nouns"] = [n for n in nouns if not all(tok in stop for tok in n.split())][:limit]
    for r in reviews:
        d = _clean(r.get("description"))
        if d and any(n in d for n in out["nouns"]) and len(out["snippets"]) < 2:
            out["snippets"].append(d[:80])
    return out


# ================================================================ 5. 체감 가격 환산 (최저가를 그대로 말하지 않기)
def price_bucket(lprice: int) -> str:
    """최저가를 정확한 숫자 대신 '만 원대' 같은 구간 표현으로"""
    p = int(lprice or 0)
    if p <= 0:
        return ""
    if p < 1000:
        return "몇백 원대"
    if p < 9000:
        return "몇천 원대"
    if p < 10000:
        return "만 원 안팎"                    # 9,900원을 '만 원대'(1만~1만9천)라고 하면 사실과 달라져 '안팎'으로
    man = p // 10000
    return "만 원대" if man == 1 else f"{man}만 원대"


def value_assumption(lprice: int, months: Optional[int] = None, uses_per_month: Optional[int] = None) -> Dict[str, Any]:
    """
    체감 가격 환산의 기본 가정 (사용자가 화면에서 고칠 수 있음). 소모품(3만 원 미만)은 한 번 쓸 때, 오래 쓰는 물건은 한 달 기준.
    → {"months", "uses_per_month", "unit": '번'|'달', "period": '석 달'..., "per_unit_won": 정수, "phrase": 환산 문구}
    문구는 항상 조건문('~ 쓴다고 치면')으로 써서 사실 주장이 아니라 계산임을 드러낸다.
    """
    p = int(lprice or 0)
    if p <= 0:
        return {"months": 0, "uses_per_month": 0, "unit": "", "period": "", "per_unit_won": 0, "phrase": ""}
    if p < 30000:
        months = months or 3
        per_month = max(1, uses_per_month or 10)
        unit, total, unit_word = "번", months * per_month, "한 번"
    else:
        months = months or (12 if p < 100000 else 24)
        unit, total, unit_word = "달", months, "한 달"
    per = max(10, int(round(p / max(1, total) / 10.0) * 10))
    period = {1: "한 달", 2: "두 달", 3: "석 달", 6: "반년", 12: "1년", 24: "2년"}.get(months, f"{months}개월")
    return {"months": months, "uses_per_month": uses_per_month or (10 if unit == "번" else 0), "unit": unit, "period": period,
            "per_unit_won": per, "unit_word": unit_word,
            "phrase": f"{price_bucket(p)} 하나로 {period} 쓴다고 치면 {unit_word}에 약 {per:,}원꼴이에요."}


# ================================================================ 6. 상품 팩: shop + blog + image → 대본·사진 근거
IMAGE_BUCKETS = (            # (역할, 검색 접미어) — 컷 1~4: 실사용 장면 / 작동 디테일 / 공간 연출 / 구성품
    ("usage", ("사용", "실사용")),
    ("detail", ("디테일", "작동")),
    ("space", ("설치", "인테리어")),
    ("package", ("구성품", "언박싱")),
)
BUCKET_LABELS = {"usage": "실사용 장면", "detail": "작동 디테일", "space": "공간 연출", "package": "구성품", "official": "공식 대표 사진"}


def search_keyword_of(product: Dict[str, Any], user_query: str = "") -> str:
    """이미지·후기 검색에 쓸 짧은 키워드: 사용자가 친 검색어가 있으면 그것, 없으면 상품명 앞 3어절"""
    q = (user_query or "").strip()
    if q and not is_url(q):
        return q
    words = (product.get("name") or "").split()
    return " ".join(words[:3])


def collect_pack(product: Dict[str, Any], user_query: str = "", shop: Optional[NaverShop] = None, official_only: bool = True,
                 per_bucket: int = 3) -> Dict[str, Any]:
    """
    선택한 상품 1개 → {"product", "keyword", "reviews": 후기 포인트, "bucket_images": {역할: [후보…]}, "official": 대표 썸네일 item, "errors": [안내]}
    후보는 아직 내려받지 않은 URL 목록이며, 블로그 도메인은 official_only일 때 제외 (photo_safety.rank_sources)
    """
    from core.photo_safety import rank_sources
    shop = shop or NaverShop()
    kw = search_keyword_of(product, user_query)
    pack: Dict[str, Any] = {"product": product, "keyword": kw, "reviews": {"nouns": [], "count": 0, "snippets": []},
                            "bucket_images": {}, "official": {"title": product.get("name", ""), "link": product.get("image", ""),
                                                              "thumbnail": product.get("image", ""), "bucket": "official"}, "errors": []}
    try:
        pack["reviews"] = review_points(shop.search_reviews(f"{kw} 후기", display=20), product.get("name", ""))
    except NaverShopError as e:
        pack["errors"].append(f"후기 요약을 가져오지 못했어요: {e}")
    seen = {pack["official"]["link"]}
    for role, suffixes in IMAGE_BUCKETS:
        found: List[Dict[str, Any]] = []
        for suf in suffixes:
            if len(found) >= per_bucket:
                break
            try:
                items = shop.search_images(f"{kw} {suf}", display=20)
            except NaverShopError as e:
                if str(e) not in pack["errors"]:
                    pack["errors"].append(str(e))
                break
            for it in rank_sources(items, official_only=official_only):
                if it["link"] in seen or len(found) >= per_bucket:
                    continue
                seen.add(it["link"])
                found.append({**it, "bucket": role})
        pack["bucket_images"][role] = found
    return pack


def curate_photo_items(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    컷 순서 확정: 1 실사용 → 2 작동 디테일 → 3 공간 연출 → 4 구성품 (버킷마다 후보 중 아직 안 쓴 것, 부족하면 다른 버킷 후보로 채움),
    마지막 컷 = shop.json 공식 대표 썸네일 (CTA 씬: 시청자가 상품 페이지에서 '방금 영상의 그 상품'을 알아보게).
    같은 사진은 두 번 쓰지 않는다. 사용할 수 있는 실사용 계열 사진이 4장보다 적으면 있는 만큼만 (부족분은 렌더 단계에서 앵글을 바꿔 재사용)
    """
    buckets = pack.get("bucket_images") or {}
    used: set = set()
    picked: List[Dict[str, Any]] = []
    spare = [it for role, _ in IMAGE_BUCKETS for it in buckets.get(role, [])]
    for role, _ in IMAGE_BUCKETS:
        cand = next((it for it in buckets.get(role, []) if it["link"] not in used and not it.get("flag")), None)
        if cand is None:
            cand = next((it for it in spare if it["link"] not in used and not it.get("flag")), None)
        if cand is not None:
            used.add(cand["link"])
            picked.append({**cand, "cut_role": role})
    official = pack.get("official")
    if official and official.get("link"):
        picked.append({**official, "cut_role": "official"})
    return picked


def download_pack_photos(items: List[Dict[str, Any]], dest_dir: str, api_key: Optional[str] = None,
                         screen: bool = True) -> List[Dict[str, Any]]:
    """
    선정한 컷을 내려받아 편집 프레임(크롭·색감·비네팅)으로 저장. 비전 검수(사람·워터마크·글씨)에 걸린 컷은 버리고 다음 후보는 호출 쪽에서 채운다.
    → [{**item, "path": 저장 경로}] (실패·검수 제외 컷은 빠짐). 공식 대표 썸네일은 사람·글씨가 있어도 상품 대표 사진이라 검수만 표시하고 쓴다.
    """
    from core import store_photos as sp
    work = [dict(it) for it in items]
    if screen and api_key:
        work = sp.screen_candidates(work, api_key)
    out = []
    for it in work:
        if it.get("flag") and it.get("cut_role") != "official":
            logger.info(f"[사진 검수 제외] {it.get('cut_role')}: {it.get('flag')}")
            continue
        path = sp.download_store_image(it, dest_dir)
        if path:
            out.append({**it, "path": path})
    return out


def ordered_paths(downloaded: List[Dict[str, Any]]) -> List[str]:
    """내려받은 컷 → 사진 경로 목록. 공식 썸네일은 항상 맨 뒤 (CTA 씬)"""
    body = [d["path"] for d in downloaded if d.get("cut_role") != "official"]
    tail = [d["path"] for d in downloaded if d.get("cut_role") == "official"]
    return body + tail[:1]


# ================================================================ 7. 첫 댓글·설명란 패키지 (쇼핑커넥트 링크 + 공정위 문구)
def comment_package(product_short: str, connect_link: str, hashtags: Optional[List[str]] = None) -> str:
    """유튜브/릴스 첫 댓글 원클릭 복사용: 구매 안내 → 쇼핑커넥트 링크(없으면 붙여 넣기 안내) → 공정위 대가성 문구 → 해시태그"""
    link = (connect_link or "").strip() or CONNECT_LINK_PLACEHOLDER
    name = (product_short or "").strip() or "이 상품"
    lines = [f"🛒 {name} 네이버 최저가 확인 👉 {link}", CONNECT_DISCLOSURE_FULL]
    tags = " ".join(t if t.startswith("#") else "#" + t for t in (hashtags or [])[:4])
    if tags:
        lines.append(tags)
    return "\n\n".join(lines)
