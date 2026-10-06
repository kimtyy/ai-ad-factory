"""크롤링 입력 정직성: 가짜 기본값 제거, 공유 링크 추적, 검색 결과 페이지 차단 (네트워크는 가짜 응답)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import logging

logging.disable(logging.WARNING)
import core.scraper as sc

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


class Resp:
    def __init__(self, status=200, html="", location=None):
        self.status_code, self.headers = status, ({"Location": location} if location else {})
        self.content = html.encode("utf-8")
        self.text = html

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def close(self):
        pass


ROUTES = {}
fetched = []


def fake_get(url, headers=None, timeout=None, allow_redirects=True, stream=False, **kw):
    fetched.append(url)
    return ROUTES.get(url, Resp(404))


sc.requests.get = fake_get
GOOGLE = "https://www.google.com/search?q=%EC%A0%84%EA%B8%B0%ED%9E%88%ED%84%B0+TSK-5318+%EC%9C%A0%ED%8C%8C&ibp=oshop"
ROUTES["https://share.google/abc"] = Resp(302, location=GOOGLE)
ROUTES["https://naver.me/short"] = Resp(301, location="https://shop.example.com/p/1")
ROUTES["https://shop.example.com/p/1"] = Resp(200, "<html><head><meta property='og:title' content='유파 전기히터 TSK-5318'>"
                                                     "<meta property='og:description' content='과열 방지, 2단 온도 조절'></head></html>")
ROUTES["https://shop.example.com/bare"] = Resp(200, "<html><body><div>no meta</div></body></html>")

# 1. 공유 링크 추적
r = sc.resolve_link("https://share.google/abc")
check(r["is_search"] and r["search_query"] == "전기히터 TSK-5318 유파" and GOOGLE not in fetched,
      "share.google → 구글 검색 결과: 검색어만 추출하고 검색 페이지는 열지 않음")
r2 = sc.resolve_link("https://naver.me/short")
check(not r2["is_search"] and r2["url"] == "https://shop.example.com/p/1", "단축 링크 → 실제 판매 페이지 주소까지 추적")

out = tmp("scraper_input_")
res = sc.scrape_product_info("https://share.google/abc", output_image_dir=out, mode="commerce")
check(not res["success"] and res["product_name_hint"] == "전기히터 TSK-5318 유파" and "실제 판매 페이지" in res["error"]
      and res["product_name"] == "" and res["usp"] == "",
      "검색 결과 링크: 크롤링 실패로 안내 + 상품명 제안만 (상품명·특장점은 비움)")

res2 = sc.scrape_product_info("https://naver.me/short", output_image_dir=out, mode="commerce")
check(res2["success"] and res2["product_name"] == "유파 전기히터 TSK-5318" and res2["usp"] == "과열 방지, 2단 온도 조절"
      and res2["url"] == "https://shop.example.com/p/1" and not res2["missing"], "정상 판매 페이지: 실제 상품명·특장점 추출")

# 2. 못 찾으면 지어내지 않음
for mode in ("commerce", "travel"):
    res3 = sc.scrape_product_info("https://shop.example.com/bare", output_image_dir=out, mode=mode)
    check(res3["product_name"] == "" and res3["usp"] == "" and set(res3["missing"]) == {"product_name", "usp"},
          f"[{mode}] 정보 없는 페이지 → 빈값 + missing 표시 (가짜 기본값 없음)")

# 3. 코드 어디에도 지어낸 기본값이 남아 있지 않음
FABRICATED = ("인기 뷰티 수분 앰플", "히알루론산 수분 앰플", "속당김 즉각 해결", "48시간 고보습 지속", "제주 애월 프라이빗 감성 풀빌라",
              "전 객실 파노라마 오션뷰")
leaks = []
for rel in ("app.py", "main.py", os.path.join("core", "scraper.py")):
    text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    leaks += [f"{rel}: {f}" for f in FABRICATED if f in text]
check(not leaks, f"앱·CLI·크롤러에 가짜 상품명/특장점 기본값 없음 {leaks or ''}")
app_src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
check('raise ValueError("숙소명(또는 상품명)을 입력해 주세요.' in app_src, "이름이 비어 있으면 기획 단계에서 입력 요청 (빈 이름으로 대본 생성 안 함)")
finish(fails)
