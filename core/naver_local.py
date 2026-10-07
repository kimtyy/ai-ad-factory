"""
core/naver_local.py - 네이버 지역 검색 공식 API (상호명·업종·주소)

네이버 플레이스·지도 페이지는 robots.txt로 모든 자동 접근을 막고, 사진은 방문자·블로거 사진이 섞여 저작권 문제가 있어
크롤링하지 않습니다. 대신 developers.naver.com '검색' API(지역)를 씁니다 (무료, 하루 25,000회).
- 키: .env의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET. 없으면 enabled=False → 화면은 직접 입력 안내
- 지역 검색은 상호명(검색어)으로만 찾을 수 있음: 플레이스 링크만으로는 이름을 알 수 없어(페이지를 열지 않으므로)
  링크에서는 플레이스 번호만 참고로 뽑고, 검색은 상호명으로 함
- 사진·리뷰는 가져오지 않음 (사장님 직접 업로드)
"""
import html
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional

import requests

LOCAL_SEARCH_URL = "https://openapi.naver.com/v1/search/local.json"
NAVER_HOSTS = ("naver.com", "naver.me")

# 네이버 카테고리 → 사장님 모드 업종 (stay 숙소 / food 음식점·카페 / shop 그 외)
STAY_WORDS = ("숙박", "펜션", "호텔", "모텔", "게스트하우스", "캠핑", "글램핑", "리조트", "민박", "한옥스테이", "풀빌라")
FOOD_WORDS = ("음식점", "카페", "디저트", "베이커리", "제과", "술집", "주점", "식당", "한식", "중식", "일식", "양식", "분식", "고기",
              "치킨", "피자", "커피", "브런치", "바(BAR)", "포차")


# 키 등록 직후 자주 나는 오류 → 사장님이 바로 고칠 수 있는 안내
HTTP_ERROR_HINTS = {
    401: "네이버 API 키가 맞지 않아요. .env의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 값을 다시 확인해 주세요.",
    403: "이 애플리케이션에 '검색' API 사용 권한이 없어요. developers.naver.com → 내 애플리케이션 → API 설정에서 '검색'을 추가해 주세요.",
    429: "오늘 네이버 검색 한도(하루 25,000회)를 넘었어요. 내일 다시 쓰거나 아래에 직접 입력해 주세요.",
}


class NaverAPIError(RuntimeError):
    pass


def is_naver_link(text: str) -> bool:
    host = urllib.parse.urlparse((text or "").strip()).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in NAVER_HOSTS)


def place_id_from_link(url: str) -> str:
    """플레이스·지도 링크에서 플레이스 번호 (…/place/1234567, …/restaurant/1234567 등). 단축 링크는 리다이렉트 주소만 따라감"""
    u = (url or "").strip()
    m = re.search(r"/(?:place|restaurant|accommodation|hairshop|hospital|entry/place)/(\d{5,})", u)
    if m:
        return m.group(1)
    if "naver.me" in u:
        from core.scraper import resolve_link     # 본문은 받지 않고 Location 헤더만 따라감
        final = resolve_link(u).get("url", "")
        m = re.search(r"/(?:place|restaurant|accommodation|hairshop|hospital|entry/place)/(\d{5,})", final)
        if m:
            return m.group(1)
    return ""


def business_type_for(category: str) -> str:
    c = category or ""
    if any(w in c for w in STAY_WORDS):
        return "stay"
    if any(w in c for w in FOOD_WORDS):
        return "food"
    return "shop"


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def region_of(address: str) -> str:
    """주소에서 '시·군·구' 수준까지 (엔딩 카드 보조 문구용, 예: '경기 가평군 청평면 …' → '경기 가평군')"""
    parts = (address or "").split()
    return " ".join(parts[:2]) if len(parts) >= 2 else (address or "").strip()


class NaverLocalSearch:
    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None, timeout: int = 5):
        self.client_id = (client_id or os.getenv("NAVER_CLIENT_ID", "")).strip()
        self.client_secret = (client_secret or os.getenv("NAVER_CLIENT_SECRET", "")).strip()
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def search(self, query: str, display: int = 5) -> List[Dict[str, Any]]:
        """상호명 검색 → [{name, category, business_type, address, road_address, phone, link}]"""
        if not self.enabled:
            raise NaverAPIError("네이버 검색 API 키가 없습니다 (.env의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET)")
        q = (query or "").strip()
        if not q:
            return []
        try:
            resp = requests.get(LOCAL_SEARCH_URL, params={"query": q, "display": max(1, min(display, 5))}, timeout=self.timeout,
                                headers={"X-Naver-Client-Id": self.client_id, "X-Naver-Client-Secret": self.client_secret})
        except requests.RequestException:
            raise NaverAPIError("네이버 검색 서버에 연결하지 못했어요. 잠시 뒤 다시 눌러 주시거나 아래에 직접 입력해 주세요.")
        if resp.status_code != 200:
            raise NaverAPIError(HTTP_ERROR_HINTS.get(resp.status_code, f"네이버 검색 API 오류 (HTTP {resp.status_code})"))
        out = []
        for it in (resp.json().get("items") or []):
            category = _clean(it.get("category", ""))
            out.append({
                "name": _clean(it.get("title", "")), "category": category, "business_type": business_type_for(category),
                "address": _clean(it.get("address", "")), "road_address": _clean(it.get("roadAddress", "")),
                "phone": _clean(it.get("telephone", "")), "link": it.get("link", ""),
            })
        return out
