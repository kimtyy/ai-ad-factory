"""쿠팡 파트너스 공식 API 연동 (가짜 응답, 실호출 없음): 서명, 검색·캐시·시간당 한도, 딥링크, 쿠팡 URL은 페이지를 열지 않음"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import hashlib
import hmac
import io
import json
import logging
import os
import time

logging.disable(logging.WARNING)
import numpy as np
from PIL import Image

import core.coupang as cp
import core.scraper as sc

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


URL = ("https://www.coupang.com/vp/products/9099469196?itemId=26907316942&vendorItemId=93876726562&sourceType=srp_product_ads"
       "&clickEventId=5a28&q=%EC%A0%84%EC%97%B4%EA%B8%B0&searchId=b9b4&searchRank=5")

# 1. URL 해석
info = cp.parse_coupang_url(URL)
check(info == {"product_id": "9099469196", "item_id": "26907316942", "vendor_item_id": "93876726562", "keyword": "전열기"},
      "쿠팡 URL → 상품 번호·옵션 번호·검색어")
check(cp.canonical_product_url(URL) == "https://www.coupang.com/vp/products/9099469196?itemId=26907316942&vendorItemId=93876726562",
      "광고·추적 파라미터 제거한 상품 URL (딥링크용)")
check(cp.is_coupang_url(URL) and cp.is_coupang_url("https://link.coupang.com/a/abc") and not cp.is_coupang_url("https://notcoupang.com.evil.io/x"),
      "쿠팡 도메인 판별 (유사 도메인 제외)")

# 2. 서명 (쿠팡 HMAC 규격: signed-date + method + path + query)
client = cp.CoupangPartners("AK", "SK")
fixed = time.struct_time((2026, 10, 6, 10, 30, 0, 0, 279, 0))
_orig_gmtime = cp.time.gmtime
cp.time.gmtime = lambda *a: fixed
auth = client._auth("GET", cp.SEARCH_PATH, "keyword=x&limit=10")
cp.time.gmtime = _orig_gmtime
expected = hmac.new(b"SK", ("261006T103000Z" + "GET" + cp.SEARCH_PATH + "keyword=x&limit=10").encode(), hashlib.sha256).hexdigest()
check(auth == f"CEA algorithm=HmacSHA256, access-key=AK, signed-date=261006T103000Z, signature={expected}", "HMAC 서명 헤더 형식")

# 3. 검색·캐시·한도·딥링크 (가짜 API)
calls = []


class Resp:
    def __init__(self, data, code=200):
        self.status_code, self._d = code, data

    def json(self):
        return self._d


PRODUCTS = [{"productId": 9099469196, "productName": "유파 PTC 전기히터 TSK-5318", "productPrice": 49900,
             "productImage": "https://ads-partners.coupang.com/image1/AAA", "productUrl": "https://link.coupang.com/re/AFFSDP?pageKey=9099469196",
             "categoryName": "계절가전", "isRocket": True},
            {"productId": 111, "productName": "다른 히터", "productPrice": 10000, "productImage": "https://ads-partners.coupang.com/image1/BBB",
             "productUrl": "https://link.coupang.com/re/AFFSDP?pageKey=111", "categoryName": "계절가전", "isRocket": False}]


def fake_request(method, url, timeout=None, data=None, headers=None):
    calls.append((method, url, headers.get("Authorization", "")))
    if cp.DEEPLINK_PATH in url:
        return Resp({"rCode": "0", "data": [{"shortenUrl": "https://link.coupang.com/a/TEST1"}]})
    return Resp({"rCode": "0", "rMessage": "", "data": {"productData": PRODUCTS}})


cp.requests.request = fake_request
cp._search_cache.clear()
cp._search_calls.clear()
items = client.search("전열기")
again = client.search("전열기")
check(len(items) == 2 and items[0]["name"] == "유파 PTC 전기히터 TSK-5318" and items[0]["product_id"] == "9099469196"
      and items[0]["is_rocket"] and len(calls) == 1 and again == items, "검색 결과 정리 + 같은 검색어는 1시간 캐시 (API 1회)")
for i in range(cp.SEARCH_LIMIT_PER_HOUR - 1):
    client.search(f"kw{i}")
try:
    client.search("한도 초과")
    check(False, "한도")
except cp.CoupangAPIError as e:
    check("1시간에" in str(e) and client.searches_left() == 0, "시간당 검색 한도 도달 전 차단 (API 호출 안 함)")
cp._search_cache.clear()
cp._search_calls.clear()
check(client.deeplink(URL) == "https://link.coupang.com/a/TEST1", "딥링크 → 파트너스 단축 링크")
found = client.find_product(URL)
check(found["product"] and found["product"]["product_id"] == "9099469196" and found["partner_link"].endswith("TEST1"),
      "URL 속 검색어로 검색해 같은 상품 번호를 찾음 (상품명·사진·링크)")
cp._search_cache.clear()
miss = client.find_product(URL.replace("9099469196", "555"))
check(miss["product"] is None and miss["partner_link"], "검색 결과에 없는 상품 → 상품 정보 없음 (링크는 생성)")

# 4. 크롤러: 쿠팡 URL은 페이지를 열지 않고 API로만
opened = []
sc.requests.get = lambda url, **kw: (opened.append(url), _img_resp(url))[1]


def _img_resp(url):
    if "coupang.com/vp" in url or url.startswith("https://www.coupang.com"):
        return type("R", (), {"status_code": 403, "content": b"", "headers": {}})()
    buf = io.BytesIO()
    Image.fromarray(np.random.default_rng(1).integers(0, 255, (900, 900, 3), dtype=np.uint8)).save(buf, "JPEG", quality=90)
    return type("R", (), {"status_code": 200, "content": buf.getvalue(), "headers": {}})()


os.environ["COUPANG_ACCESS_KEY"], os.environ["COUPANG_SECRET_KEY"] = "AK", "SK"
cp._search_cache.clear()
cp._search_calls.clear()
out = tmp("coupang_scrape_")
res = sc.scrape_product_info(URL, output_image_dir=out, mode="commerce")
check(res["success"] and res["product_name"] == "유파 PTC 전기히터 TSK-5318" and res["affiliate_link"].endswith("TEST1")
      and len(res["product_images"]) == 1 and "usp" in res["missing"], "쿠팡 URL → API로 상품명·고해상도 사진·파트너스 링크, 특장점은 직접 입력 요청")
check(not any("coupang.com/vp" in u for u in opened), "쿠팡 상품 페이지에는 접속하지 않음 (403·약관 회피)")
os.environ["COUPANG_ACCESS_KEY"] = ""
res2 = sc.scrape_product_info(URL, output_image_dir=out, mode="commerce")
check(not res2["success"] and "직접 입력" in res2["error"] and "403" not in res2["error"], "API 키 없으면 친절한 안내 (403 기술 오류 문구 대신)")
import os as _o
_o.environ.pop("COUPANG_ACCESS_KEY", None)
_o.environ.pop("COUPANG_SECRET_KEY", None)
finish(fails)
