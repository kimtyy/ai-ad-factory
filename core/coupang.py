"""
core/coupang.py - 쿠팡 파트너스 Open API (공식)

쿠팡 상품 페이지는 서버 자동 접속을 403으로 막으므로(약관상 우회 금지) 상품 정보는 공식 API로만 가져옵니다.
- search(keyword): 상품 검색 → 상품명·가격·대표 이미지(고해상도)·파트너스 추적 링크
- deeplink(urls): 쿠팡 상품 URL → 파트너스 단축 링크 (영상 설명·고정 댓글용)
- 파트너스 API에는 '상품 번호로 상세 조회'가 없어, URL만으로는 검색 결과에 그 상품이 있을 때만 이름·이미지를 찾을 수 있음
- 검색 API는 시간당 호출 수가 제한됨 → 같은 검색어는 1시간 캐시, 호출 수를 세어 한도 전에 막음
키: .env의 COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY (화면·로그에 출력 금지)
"""
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("ad_factory.coupang")

API_BASE = "https://api-gateway.coupang.com"
SEARCH_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search"
DEEPLINK_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"
SEARCH_LIMIT_PER_HOUR = 10          # 쿠팡 파트너스 검색 API 시간당 한도
CACHE_SEC = 3600
COUPANG_HOSTS = ("coupang.com",)

_search_cache: Dict[str, Any] = {}   # keyword → (시각, 결과)
_search_calls: List[float] = []      # 최근 1시간 검색 호출 시각 (서버 프로세스 공유)


class CoupangAPIError(RuntimeError):
    pass


def is_coupang_url(url: str) -> bool:
    host = urllib.parse.urlparse(url or "").netloc.lower()
    return any(host == h or host.endswith("." + h) for h in COUPANG_HOSTS)


def parse_coupang_url(url: str) -> Dict[str, str]:
    """상품 번호·옵션 번호·검색어 추출 (/vp/products/{id}, 파트너스 링크의 pageKey, q=검색어)"""
    parsed = urllib.parse.urlparse(url or "")
    qs = urllib.parse.parse_qs(parsed.query)
    m = re.search(r"/products/(\d+)", parsed.path)
    pid = m.group(1) if m else (qs.get("pageKey") or [""])[0]
    return {"product_id": pid, "item_id": (qs.get("itemId") or [""])[0],
            "vendor_item_id": (qs.get("vendorItemId") or [""])[0], "keyword": (qs.get("q") or [""])[0].strip()}


def canonical_product_url(url: str) -> str:
    """추적·광고 파라미터를 뺀 상품 URL (딥링크 생성용)"""
    info = parse_coupang_url(url)
    if not info["product_id"]:
        return url
    q = {k: v for k, v in (("itemId", info["item_id"]), ("vendorItemId", info["vendor_item_id"])) if v}
    return f"https://www.coupang.com/vp/products/{info['product_id']}" + (("?" + urllib.parse.urlencode(q)) if q else "")


class CoupangPartners:
    def __init__(self, access_key: Optional[str] = None, secret_key: Optional[str] = None, timeout: int = 15):
        self.access_key = (access_key or os.getenv("COUPANG_ACCESS_KEY", "")).strip()
        self.secret_key = (secret_key or os.getenv("COUPANG_SECRET_KEY", "")).strip()
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.access_key and self.secret_key)

    def _auth(self, method: str, path: str, query: str) -> str:
        signed = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
        sig = hmac.new(self.secret_key.encode(), (signed + method + path + query).encode(), hashlib.sha256).hexdigest()
        return f"CEA algorithm=HmacSHA256, access-key={self.access_key}, signed-date={signed}, signature={sig}"

    def _call(self, method: str, path: str, query: str = "", body: Optional[dict] = None) -> Dict[str, Any]:
        if not self.enabled:
            raise CoupangAPIError("쿠팡 파트너스 API 키가 없습니다 (.env의 COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY)")
        url = API_BASE + path + (("?" + query) if query else "")
        resp = requests.request(method, url, timeout=self.timeout, data=json.dumps(body) if body is not None else None,
                                headers={"Authorization": self._auth(method, path, query),
                                         "Content-Type": "application/json;charset=UTF-8"})
        try:
            data = resp.json()
        except Exception:
            raise CoupangAPIError(f"쿠팡 API 응답 오류 (HTTP {resp.status_code})")
        if resp.status_code != 200 or str(data.get("rCode")) != "0":
            raise CoupangAPIError(f"쿠팡 API 오류 (HTTP {resp.status_code}): {data.get('rMessage') or data.get('message') or ''}")
        return data

    def searches_left(self) -> int:
        now = time.time()
        _search_calls[:] = [t for t in _search_calls if now - t < 3600]
        return max(0, SEARCH_LIMIT_PER_HOUR - len(_search_calls))

    def search(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        """상품 검색 (같은 검색어는 1시간 캐시). 반환: [{product_id, name, price, image_url, partner_url, category, is_rocket}]"""
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        key = f"{keyword}|{limit}"
        cached = _search_cache.get(key)
        if cached and time.time() - cached[0] < CACHE_SEC:
            return cached[1]
        if self.searches_left() <= 0:
            raise CoupangAPIError(f"쿠팡 검색은 1시간에 {SEARCH_LIMIT_PER_HOUR}번까지 가능합니다. 잠시 후 다시 시도하거나 상품 정보를 직접 입력해 주세요.")
        _search_calls.append(time.time())
        data = self._call("GET", SEARCH_PATH, urllib.parse.urlencode({"keyword": keyword, "limit": limit}))
        items = [{
            "product_id": str(it.get("productId", "")), "name": str(it.get("productName", "")).strip(),
            "price": it.get("productPrice"), "image_url": it.get("productImage", ""), "partner_url": it.get("productUrl", ""),
            "category": it.get("categoryName", ""), "is_rocket": bool(it.get("isRocket")),
        } for it in ((data.get("data") or {}).get("productData") or [])]
        _search_cache[key] = (time.time(), items)
        return items

    def deeplink(self, url: str) -> Optional[str]:
        """쿠팡 URL → 파트너스 단축 링크 (실패하면 None)"""
        try:
            data = self._call("POST", DEEPLINK_PATH, body={"coupangUrls": [canonical_product_url(url)]})
            rows = data.get("data") or []
            return (rows[0].get("shortenUrl") or rows[0].get("landingUrl")) if rows else None
        except CoupangAPIError as e:
            logger.warning(f"파트너스 링크 생성 실패: {e}")
            return None

    def find_product(self, url: str) -> Dict[str, Any]:
        """
        쿠팡 상품 URL → {product(검색 결과에서 같은 상품을 찾으면), partner_link, keyword, product_id}.
        파트너스 API에는 상품 번호 조회가 없어 URL 속 검색어(q=)로 검색해 같은 상품 번호를 찾음
        """
        info = parse_coupang_url(url)
        out = {"product": None, "partner_link": self.deeplink(url), "keyword": info["keyword"], "product_id": info["product_id"]}
        if info["keyword"] and info["product_id"]:
            try:
                out["product"] = next((p for p in self.search(info["keyword"]) if p["product_id"] == info["product_id"]), None)
            except CoupangAPIError as e:
                logger.warning(f"쿠팡 검색 실패: {e}")
        return out
