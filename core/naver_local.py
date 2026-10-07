"""
core/naver_local.py - 네이버 지역 검색 공식 API (상호명·업종·주소) - NAVER API HUB (네이버 클라우드 플랫폼)

네이버 플레이스·지도 페이지는 robots.txt로 모든 자동 접근을 막고, 사진은 방문자·블로거 사진이 섞여 저작권 문제가 있어
크롤링하지 않습니다. 대신 공식 지역 검색 API를 씁니다.
- 검색 API는 developers.naver.com에서 종료되고 NCP의 NAVER API HUB로 이관됨 (2026, 응답 형식은 기존과 동일)
  엔드포인트 https://naverapihub.apigw.ntruss.com/search/v1/local, 헤더 X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY
- 키: .env의 NAVER_CLIENT_ID(10자) / NAVER_CLIENT_SECRET(40자) = NCP 콘솔 → NAVER API HUB 앱의 인증 정보.
  없으면 enabled=False → 화면은 직접 입력 안내
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

LOCAL_SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/local"
NAVER_HOSTS = ("naver.com", "naver.me")

# 네이버 카테고리 → 사장님 모드 업종 (stay 숙소 / food 음식점·카페 / shop 그 외)
STAY_WORDS = ("숙박", "펜션", "호텔", "모텔", "게스트하우스", "캠핑", "글램핑", "리조트", "민박", "한옥스테이", "풀빌라")
FOOD_WORDS = ("음식점", "카페", "디저트", "베이커리", "제과", "술집", "주점", "식당", "한식", "중식", "일식", "양식", "분식", "고기",
              "치킨", "피자", "커피", "브런치", "바(BAR)", "포차")


# 키 등록 직후 자주 나는 오류 → 사장님이 바로 고칠 수 있는 안내
HTTP_ERROR_HINTS = {
    401: "네이버 API 키가 맞지 않아요. .env의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET에 NCP 콘솔 → NAVER API HUB 앱의 "
         "Client ID·Client Secret을 넣었는지 확인해 주세요.",
    403: "이 API HUB 앱에 '검색' 상품 사용 권한이 없어요. NCP 콘솔 → NAVER API HUB → 앱 설정에서 검색(지역)을 추가해 주세요.",
    429: "네이버 검색 호출 한도를 넘었어요. 잠시 뒤 다시 쓰거나 아래에 직접 입력해 주세요.",
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


# 검색 결과 주소의 공식 광역 명칭 → 엔딩 카드용 짧은 표기 ('강원특별자치도 강릉시' → '강원 강릉시')
PROVINCE_SHORT = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
    "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "강원특별자치도": "강원", "강원도": "강원",
    "충청북도": "충북", "충청남도": "충남", "전북특별자치도": "전북", "전라북도": "전북", "전라남도": "전남",
    "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주", "제주도": "제주",
}


def region_of(address: str) -> str:
    """주소에서 '시·군·구' 수준까지 (엔딩 카드 보조 문구용, 예: '경기도 가평군 청평면 …' → '경기 가평군')"""
    parts = (address or "").split()
    if parts:
        parts[0] = PROVINCE_SHORT.get(parts[0], parts[0])
    if parts and parts[0] == "세종":
        return "세종"
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
                                headers={"X-NCP-APIGW-API-KEY-ID": self.client_id, "X-NCP-APIGW-API-KEY": self.client_secret})
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
