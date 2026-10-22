import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish, fixture  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess

from PIL import Image

import core.naver_shop as ns
from core.mode_policy import SHOP_POLICY, SHOP_TOP_POLICY, get_mode_policy
from core.planners import shop as sh

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


class R:
    def __init__(self, status=200, payload=None):
        self.status_code, self._p = status, payload

    def json(self):
        if self._p is None:
            raise ValueError("no json")
        return self._p


def jpg(w=900, h=700, color=(30, 120, 200)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


# ---------------------------------------------------------------- 가짜 네이버 서버 (공식 openapi 형식과 API HUB 형식 둘 다)
SHOP_ITEMS = {
    "배수구 클리너": [
        {"title": "<b>배수구</b> 거품 <b>클리너</b> 500ml", "link": "https://search.shopping.naver.com/catalog/111", "image": "https://shopping-phinf.pstatic.net/main_1/1.jpg",
         "lprice": "9900", "hprice": "", "mallName": "클린몰", "productId": "111", "productType": "2", "brand": "클린웍스", "maker": "클린웍스",
         "category1": "생활/건강", "category2": "욕실용품", "category3": "배수구클리너", "category4": ""},
        {"title": "중고 배수구 클리너", "link": "x", "image": "https://shopping-phinf.pstatic.net/main_1/2.jpg", "lprice": "1000", "productType": "5"},
        {"title": "가격없음", "link": "x", "image": "https://shopping-phinf.pstatic.net/main_1/3.jpg", "lprice": "0", "productType": "1"},
    ],
    "캠핑 제빙기": [{"title": "캠핑 <b>제빙기</b> 휴대용", "link": "https://search.shopping.naver.com/catalog/222", "image": "https://shopping-phinf.pstatic.net/main_1/4.jpg",
                   "lprice": "89000", "mallName": "캠핑몰", "productId": "222", "productType": "1", "brand": "", "maker": "", "category1": "스포츠/레저"}],
    "캠핑 랜턴": [{"title": "캠핑 랜턴 LED", "link": "l", "image": "https://shopping-phinf.pstatic.net/main_1/5.jpg", "lprice": "15000", "productId": "333", "productType": "1"}],
    "접이식 캠핑 테이블": [{"title": "접이식 캠핑 테이블", "link": "t", "image": "https://shopping-phinf.pstatic.net/main_1/6.jpg", "lprice": "32000", "productId": "444", "productType": "1"}],
    "차박 매트": [{"title": "차박 매트 자충", "link": "m", "image": "https://shopping-phinf.pstatic.net/main_1/7.jpg", "lprice": "54000", "productId": "555", "productType": "1"}],
    "휴대용 버너": [{"title": "휴대용 버너", "link": "b", "image": "https://shopping-phinf.pstatic.net/main_1/8.jpg", "lprice": "21000", "productId": "666", "productType": "1"}],
    "캠핑 의자": [{"title": "캠핑 의자 경량", "link": "c", "image": "https://shopping-phinf.pstatic.net/main_1/9.jpg", "lprice": "27000", "productId": "777", "productType": "1"}],
}
BLOG_ITEMS = [{"title": f"배수구 클리너 <b>후기</b> {i}", "description": "배수구 냄새 거품 청소 후기예요. 거품이 배관까지 내려가서 냄새 걱정이 없었어요. 배수구 냄새 거품", "link": f"https://blog.naver.com/a/{i}",
               "bloggername": "b", "postdate": "20261010"} for i in range(6)]


def image_items(kw):
    base = "https://shopping-phinf.pstatic.net"       # 공식 도메인
    blog = "https://blogfiles.naver.net"               # 개인 블로그 도메인 (기본 수집에서 제외)
    out = []
    for i in range(4):
        out.append({"title": f"{kw} {i}", "link": f"{blog}/a/blog_{abs(hash(kw)) % 999}_{i}.jpg", "thumbnail": f"{blog}/t/{i}.jpg", "sizewidth": "900", "sizeheight": "900"})
    for i in range(3):
        out.append({"title": f"{kw} {i}", "link": f"{base}/official/{abs(hash(kw)) % 999}_{i}.jpg", "thumbnail": f"{base}/t/{i}.jpg", "sizewidth": "800", "sizeheight": "800"})
    out.append({"title": "작은 사진", "link": f"{base}/small.jpg", "thumbnail": "", "sizewidth": "200", "sizeheight": "200"})
    return out


class FakeSession:
    """requests.get 흉내: legacy(openapi.naver.com)와 HUB(naverapihub) 중 mode에 따라 한쪽만 200"""

    def __init__(self, mode="legacy", fail_status=None):
        self.mode, self.fail_status, self.calls = mode, fail_status, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        is_hub = "naverapihub" in url
        if self.fail_status:
            return R(self.fail_status, {})
        if (self.mode == "hub") != is_hub:
            return R(401, {})
        kind = re.search(r"search/v1/(\w+)|search/(\w+)\.json", url)
        kind = kind.group(1) or kind.group(2)
        q = (params or {}).get("query", "")
        if kind == "shop":
            return R(200, {"items": SHOP_ITEMS.get(q, [])})
        if kind == "blog":
            return R(200, {"items": BLOG_ITEMS})
        if kind == "image":
            return R(200, {"items": image_items(q)})
        return R(404, {})


# ---------------------------------------------------------------- 1. API 클라이언트: 엔드포인트·헤더·캐시·오류 안내
ns.NaverShop._preferred = None
fs = FakeSession("legacy")
cli = ns.NaverShop("cid", "sec", session=fs)
prods = cli.search_products("배수구 클리너")
check(len(prods) == 1 and prods[0]["name"] == "배수구 거품 클리너 500ml" and prods[0]["lprice"] == 9900 and prods[0]["mall"] == "클린몰"
      and prods[0]["category"] == "생활/건강 > 욕실용품 > 배수구클리너", f"shop.json 정규화(태그 제거·카테고리): {prods[0]['name']} {prods[0]['lprice']}원")
check(len(SHOP_ITEMS["배수구 클리너"]) == 3 and len(prods) == 1, "중고(productType 5)·가격 없는 상품은 제외")
urls = [c[0] for c in fs.calls]
check(urls[0] == "https://openapi.naver.com/v1/search/shop.json" and fs.calls[0][2].get("X-Naver-Client-Id") == "cid", f"공식 오픈 API 주소·헤더로 호출: {urls[0]}")
cli.search_products("배수구 클리너")
check(len(fs.calls) == len(urls), "같은 검색은 1시간 캐시 (두 번째 호출 없음)")
ns.NaverShop._preferred = None
fh = FakeSession("hub")
cli_h = ns.NaverShop("cid", "sec", session=fh)
cli_h.search_products("배수구 클리너")
check(fh.calls[0][0].endswith("openapi.naver.com/v1/search/shop.json") and "naverapihub" in fh.calls[1][0] and fh.calls[1][2].get("X-NCP-APIGW-API-KEY-ID") == "cid",
      "공식 주소가 401이면 NCP API HUB(같은 키)로 자동 대체")
cli_h2 = ns.NaverShop("cid", "sec", session=fh)
n_before = len(fh.calls)
cli_h2.search_products("캠핑 제빙기")
check("naverapihub" in fh.calls[n_before][0], "한 번 성공한 엔드포인트(HUB)를 기억해 다음 호출부터 먼저 사용")
ns.NaverShop._preferred = None
try:
    ns.NaverShop("", "", session=fs).search_products("x")
    check(False, "키 없음 안내")
except ns.NaverShopError as e:
    check("NAVER_CLIENT_ID" in str(e) and not ns.NaverShop("", "", session=fs).enabled, "키 없으면 enabled=False + 설정 안내")
for status, hint in ((401, "키가 맞지 않아요"), (403, "권한이 없어요"), (429, "한도를 넘었어요")):
    try:
        ns.NaverShop("c", "s", session=FakeSession(fail_status=status)).search_products("배수구 클리너")
        check(False, f"HTTP {status} 안내")
    except ns.NaverShopError as e:
        check(hint in str(e), f"HTTP {status} → 한국어 안내: {e}")
imgs = ns.NaverShop("c", "s", session=FakeSession()).search_images("배수구 클리너 사용")
check(all(min(i["width"], i["height"]) >= 400 for i in imgs) and len(imgs) == 7, f"image.json: 400px 미만 제외 ({len(imgs)}장)")
rv = ns.NaverShop("c", "s", session=FakeSession()).search_reviews("배수구 클리너 후기")
check(len(rv) == 6 and "<b>" not in rv[0]["title"], "blog.json 후기 6건 (태그 제거)")

# ---------------------------------------------------------------- 2. 인기 꿀템 카드: 데이터랩 급상승 순 → 키워드별 shop.json 1위
import core.stay_curation as sc_mod
_orig_trend = sc_mod.fetch_trends
CAMP = ns.category_keywords("⛺ 캠핑·차박")


def fake_trends(keywords, cid, secret, today=None, timeout=8):
    surge = {"캠핑 제빙기": 5.0, "차박 매트": 3.0, "캠핑 의자": 2.0, "접이식 캠핑 테이블": 1.5, "휴대용 버너": 1.2, "캠핑 랜턴": 1.0}
    return {"ok": True, "error": "", "stats": {k: {"steady": 10.0, "avg7": 10.0, "surge": surge.get(k, 0.5)} for k in keywords}}


sc_mod.fetch_trends = fake_trends
ns.NaverShop._preferred = None
res = ns.trending_cards("⛺ 캠핑·차박", ns.NaverShop("c", "s", session=FakeSession()), today=dt.date(2026, 10, 22))
names = [c["keyword"] for c in res["cards"]]
check(names == ["캠핑 제빙기", "차박 매트", "캠핑 의자", "접이식 캠핑 테이블", "휴대용 버너"] and res["ranked_by_trend"] and not res["error"],
      f"인기 꿀템 TOP 5 = 데이터랩 급상승 순: {names}")
check(all(c["lprice"] > 0 and c["image"] and c["link"] for c in res["cards"]), "카드마다 상품명·최저가·이미지·링크")
check(len(ns.TREND_CATEGORIES) == 5 and set(ns.TREND_CATEGORIES) == {"🏠 자취·생활 꿀템", "⛺ 캠핑·차박", "🍳 주방·요리", "🍺 홈술·밀키트", "🔥 계절 가전"},
      "카테고리 5종 (자취·생활 / 캠핑·차박 / 주방·요리 / 홈술·밀키트 / 계절 가전)")
check(ns.category_keywords("🔥 계절 가전", dt.date(2026, 10, 22))[0] == "전기 히터" and ns.category_keywords("🔥 계절 가전", dt.date(2026, 7, 10))[0] == "서큘레이터",
      "계절 가전은 월별 시즌 후보 (10~2월 전기 히터 / 5~8월 서큘레이터)")
sc_mod.fetch_trends = lambda *a, **k: {"ok": False, "error": "데이터랩 권한이 없어요", "stats": {}}
res2 = ns.trending_cards("⛺ 캠핑·차박", ns.NaverShop("c", "s", session=FakeSession()))
check(not res2["ranked_by_trend"] and res2["notes"] == ["데이터랩 권한이 없어요"] and [c["keyword"] for c in res2["cards"]][:1] == ["캠핑 제빙기"],
      "트렌드 실패: 지어낸 순위 대신 이유를 알리고 후보 순서로 카드 표시")
res3 = ns.trending_cards("⛺ 캠핑·차박", ns.NaverShop("", "", session=FakeSession()))
check(not res3["cards"] and "API 키" in res3["error"], "키 없음: 카드 대신 안내")
sc_mod.fetch_trends = _orig_trend

# ---------------------------------------------------------------- 3. 입력 해석: 검색어 / 스마트스토어·쇼핑커넥트 URL
r = ns.resolve_input("캠핑 제빙기")
check(r["query"] == "캠핑 제빙기" and not r["link"] and not r["needs_keyword"], "검색어는 그대로")
r = ns.resolve_input("https://smartstore.naver.com/abc/products/1234567890", resolver=lambda u: {"url": u, "search_query": "", "is_search": False})
check(r["link"].startswith("https://smartstore") and r["product_id"] == "1234567890" and r["needs_keyword"] and "검색어" in r["note"],
      "스마트스토어 URL: 링크 보존 + 상품 번호 + '상품명을 한 번만 적어 주세요' (페이지는 열지 않음)")
r = ns.resolve_input("https://naver.me/xyz", resolver=lambda u: {"url": "https://search.naver.com/search.naver?query=%EC%BA%A0%ED%95%91+%EC%A0%9C%EB%B9%99%EA%B8%B0", "search_query": "", "is_search": True})
check(r["query"] == "캠핑 제빙기" and not r["needs_keyword"] and r["link"] == "https://naver.me/xyz", "단축 링크 → 리다이렉트 주소의 검색어로 상품 검색")
check(ns.is_shop_url("https://brand.naver.com/x/products/1") and not ns.is_shop_url("https://example.com/a") and not ns.is_shop_url("캠핑"), "쇼핑 URL 판별")

# ---------------------------------------------------------------- 4. 후기 포인트 (blog.json 요약에서 자주 나온 말)
pts = ns.review_points(BLOG_ITEMS, "배수구 거품 클리너 500ml")
check(pts["count"] == 6 and any("거품" in n for n in pts["nouns"]) and "배수구 클리너" not in pts["nouns"] and pts["snippets"],
      f"후기에 자주 나온 말(상품명 단어로만 된 말 제외): {pts['nouns']}")
check(ns.review_points([], "x")["nouns"] == [], "후기 없으면 빈 목록")

# ---------------------------------------------------------------- 5. 체감 가격 환산 (최저가를 그대로 말하지 않음)
a = ns.value_assumption(9900)
check(a["phrase"] == "만 원 안팎 하나로 석 달 쓴다고 치면 한 번에 약 330원꼴이에요." and a["per_unit_won"] == 330, f"9,900원 → {a['phrase']}")
check(ns.value_assumption(12900)["phrase"].startswith("만 원대 하나로 석 달") and ns.value_assumption(4500)["phrase"].startswith("몇천 원대"), "구간 표현: 12,900원 → 만 원대 / 4,500원 → 몇천 원대")
b = ns.value_assumption(59000)
check(b["unit"] == "달" and b["period"] == "1년" and b["per_unit_won"] == 4920 and "5만 원대" in b["phrase"], f"59,000원 → {b['phrase']}")
c = ns.value_assumption(9900, months=1, uses_per_month=5)
check(c["per_unit_won"] == 1980 and "한 달 쓴다고 치면" in c["phrase"], f"사용자가 가정을 고치면 반영: {c['phrase']}")
check("9,900" not in a["phrase"] and "9900" not in a["phrase"] and ns.price_bucket(150000) == "15만 원대" and ns.value_assumption(0)["phrase"] == "",
      "환산 문구에 최저가 원문 없음, 가격 0이면 문구 없음")

# ---------------------------------------------------------------- 6. 상품 팩 + 사진 큐레이션 (블로그 사진 제외, 마지막 = 공식 썸네일)
ns.NaverShop._preferred = None
shop = ns.NaverShop("c", "s", session=FakeSession())
product = prods[0]
pack = ns.collect_pack(product, "배수구 클리너", shop)
check(pack["keyword"] == "배수구 클리너" and any("거품" in n for n in pack["reviews"]["nouns"]), f"collect_pack: 후기 포인트 {pack['reviews']['nouns']}")
allc = [it for items in pack["bucket_images"].values() for it in items]
check(allc and all("blogfiles" not in it["link"] for it in allc), f"개인 블로그 도메인 사진은 기본 제외 ({len(allc)}장 후보 모두 공식·기타 출처)")
curated = ns.curate_photo_items(pack)
roles = [x["cut_role"] for x in curated]
check(roles == ["usage", "detail", "space", "package", "official"], f"컷 순서: 실사용 → 작동 디테일 → 공간 연출 → 구성품 → 공식 썸네일: {roles}")
check(len({x["link"] for x in curated}) == len(curated) and curated[-1]["link"] == product["image"], "같은 사진 반복 없음 + 마지막 컷 = shop.json 공식 대표 썸네일")
pack_flag = json.loads(json.dumps(pack))
for items in pack_flag["bucket_images"].values():
    for it in items[:1]:
        it["flag"] = "person"
cur_flag = ns.curate_photo_items(pack_flag)
check(all(not x.get("flag") for x in cur_flag) and len(cur_flag) >= 4, "비전 검수에서 사람 얼굴 등으로 표시된 사진은 컷에서 제외하고 다른 후보로 채움")
empty = {"product": product, "bucket_images": {}, "official": {"link": product["image"], "title": "x", "thumbnail": product["image"], "bucket": "official"}}
check([x["cut_role"] for x in ns.curate_photo_items(empty)] == ["official"], "실사용 사진이 하나도 없으면 공식 썸네일만")

# 내려받기: 편집 프레임(크롭·색감·비네팅) + 검수 제외 + 썸네일 마지막
import core.store_photos as spmod
_dl, _scr = spmod.download_store_image, spmod.screen_candidates
dest = tmp("shop_dl_")
made = {}


def fake_dl(item, dest_dir, timeout=3, transform=True):
    p = os.path.join(dest_dir, f"{abs(hash(item['link'])) % 99999}.jpg")
    Image.new("RGB", (600, 600), (10, 20, 30)).save(p)
    made[item["link"]] = p
    return p


def fake_screen(items, api_key=None, timeout=3):
    return [({**it, "flag": "watermark"} if it.get("cut_role") == "detail" else it) for it in items]


spmod.download_store_image, spmod.screen_candidates = fake_dl, fake_screen
down = ns.download_pack_photos(curated, dest, api_key="k")
spmod.download_store_image, spmod.screen_candidates = _dl, _scr
check([d["cut_role"] for d in down] == ["usage", "space", "package", "official"], f"비전 검수(워터마크) 걸린 컷은 버림: {[d['cut_role'] for d in down]}")
paths = ns.ordered_paths(down)
check(paths[-1] == down[-1]["path"] and len(paths) == 4, "ordered_paths: 공식 썸네일이 항상 맨 뒤")

# ---------------------------------------------------------------- 7. 5문장 대본: 템플릿·검증·묶음
value = ns.value_assumption(product["lprice"])
ctx = sh.build_context(product, pack, "", value, "배수구 클리너")
tpl = sh.template_lines(ctx)
check(len(tpl) == 5 and tpl[4] == sh.SHOP_CTA == "품절되기 전에 고정 댓글 네이버 최저가 링크부터 확인해 보세요!", "5문장 + CTA 고정 문구")
check(all(not sh.validate_line(i, tpl[i], ctx) for i in range(4)), f"팩트 템플릿 문장은 자체 검증 통과: {[sh.validate_line(i, tpl[i], ctx) for i in range(4)]}")
check(tpl[3] == value["phrase"] and "9,900" not in tpl[3], f"문장 4 = 체감 가격 환산 (최저가 원문 없음): {tpl[3]}")
bad = {
    0: ("재질은 스테인리스고 무게 500g에 크기 20cm예요.", "스펙 나열"),
    1: ("48시간 지속되고 특허 받은 제품이라 믿고 쓰세요.", "근거 없는 수치"),
    2: ("이건 물만 부으면 3초 만에 싹 녹여버려요.", "근거 없는 수치"),
    3: ("지금 9,900원에 최저가로 사세요.", "최저가"),
}
for i, (txt, why) in bad.items():
    got = sh.validate_line(i, txt, ctx)
    check(bool(got), f"문장 {i + 1} 탈락({why}): '{txt}' → {got}")
check(sh.validate_line(3, "후기에서 거품이 자주 나와요.", ctx) == "체감 가격 환산 문구 누락", "문장 4는 환산 문구가 없으면 탈락")
check(sh.validate_line(3, "만 원 안팎 하나로 석 달 쓴다고 치면 한 번에 약 330원꼴, 9,900원이에요.", ctx) == "최저가 직접 노출", "환산 문구가 있어도 최저가 숫자를 같이 말하면 탈락")
check(sh.validate_line(0, "배수구 머리카락 아직도 손으로 건지세요?", ctx) == "", "후킹 질문(~세요?)은 허용")
check(sh.find_spec_listing("재질 무게 크기") and not sh.find_spec_listing("거품이 배관까지 내려가요."), "스펙 나열 판별")
check(sh.shop_groups(5) == [[0], [1], [2], [3], [4]] and sh.shop_groups(6) == [[0], [1, 2], [3], [4], [5]] and sh.shop_groups(8) == [[0, 1], [2, 3], [4, 5], [6], [7]]
      and sh.shop_groups(3) == [[0], [1], [0], [1], [2]] and sh.shop_groups(1) == [[0]] * 5 and sh.shop_groups(0) == [],
      "사진 묶음: 마지막 문장(CTA)은 항상 공식 썸네일 1장, 부족하면 돌려 씀")

sc_list = sh.plan_shop_storyboard(product, pack, paths, media_roles=[d["cut_role"] for d in down], value=value, keyword="배수구 클리너", mock=True)
check(len(sc_list) == 5 and [s["role"] for s in sc_list] == sh.ROLES and [s["duration_sec"] for s in sc_list] == [3.0, 4.0, 4.0, 4.0, 3.0]
      and sum(s["duration_sec"] for s in sc_list) == 18.0, "콘티: 5씬·역할·0~3/3~7/7~11/11~15/15~18초")
check(sc_list[0]["shop_auto"] and sc_list[0]["hook_title"] and sc_list[0]["script_engine"] == "template" and "킬러 기능" in sc_list[0]["core_concept"], "씬 1: 후킹 헤드라인·shop_auto·코어 컨셉")
check(sc_list[4]["layout"] == "card" and sc_list[4]["caption_center_y"] and all(s["layout"] == "full" for s in sc_list[:4]), "실사용 컷은 전체 화면, 마지막 공식 썸네일은 상품 카드(자막은 카드 위)")
check(sc_list[4].get("media_group") == [3] and sc_list[0].get("media_group") == [0], f"사진 묶음 연결: {[s.get('media_group') for s in sc_list]}")
check(not sc_list[0]["shop"]["warn"] or len(paths) < 5, "사진 4장 이하면 안내 문구, 5장 이상이면 없음")
try:
    sh.plan_shop_storyboard({}, mock=True)
    check(False, "상품 없으면 오류")
except ValueError as e:
    check("상품을 먼저 골라" in str(e), "상품을 고르지 않으면 지어내지 않고 안내")

# LLM 경로: 검증 탈락 문장은 템플릿으로 교체, 통과 문장은 채택, 환산 문구는 유지
import core.llm as llm_mod
_ready, _gen = llm_mod.script_llm_ready, llm_mod.generate_json
llm_mod.script_llm_ready = lambda key=None: True
LLM_OUT = {"scenes": [
    {"scene_index": 1, "hook_title": "배수구 아직도?", "voiceover": "배수구 머리카락 아직도 손으로 건지세요?"},
    {"scene_index": 2, "voiceover": "재질은 스테인리스고 무게 500g에 크기 20cm예요."},
    {"scene_index": 3, "voiceover": "이건 거품이 배관 끝까지 내려가 냄새를 줄이는 방식이에요."},
    {"scene_index": 4, "voiceover": value["phrase"]},
    {"scene_index": 5, "voiceover": "아무 문장"},
]}
llm_mod.generate_json = lambda *a, **k: (LLM_OUT, "claude")
sc_llm = sh.plan_shop_storyboard(product, pack, paths, value=value, keyword="배수구 클리너", api_key="k")
spoken = [s["voiceover"] for s in sc_llm]
check(spoken[0] == LLM_OUT["scenes"][0]["voiceover"] and spoken[2] == LLM_OUT["scenes"][2]["voiceover"] and spoken[3] == value["phrase"] and spoken[4] == sh.SHOP_CTA,
      f"LLM 문장 검증: 통과 문장 채택 + CTA는 항상 고정 문구: {spoken}")
check(spoken[1] == tpl[1] and "재질" not in spoken[1] and sc_llm[0]["script_engine"] == "claude" and sc_llm[0]["hook_title"] == "배수구 아직도?", "스펙 나열 문장은 템플릿으로 교체, 엔진 표시")
llm_mod.generate_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
sc_fail = sh.plan_shop_storyboard(product, pack, paths, value=value, keyword="배수구 클리너", api_key="k")
check([s["voiceover"] for s in sc_fail] == tpl and sc_fail[0]["script_engine"] == "template", "엔진 실패 시 팩트 템플릿으로 대체")
llm_mod.script_llm_ready, llm_mod.generate_json = _ready, _gen

# ---------------------------------------------------------------- 8. 공용 엔진 연결: 정책·고지·첫 댓글·사진 배정
import core.pipeline as pl
from core.job import JobContext
from core.render.brand import BrandOverlay
SI = {"product": product, "pack": pack, "value": value, "keyword": "배수구 클리너", "cut_roles": [d["cut_role"] for d in down]}
spec = pl.JobSpec(mode="commerce", name=product["name"], usp="", media_paths=paths, mock=True, shop_item=SI,
                  link="https://naver.me/abcd", audio_mode="silent")
check(spec.is_shop and not pl.JobSpec(mode="commerce", name="a").is_shop and not pl.JobSpec(mode="commerce", name="a", layout="bundle", shop_item=SI).is_shop, "is_shop: 쇼핑 단일 + 상품 팩일 때만")
W = tmp("shop_job_")
job = JobContext.create(app="shop_test")
job.root = W
job = JobContext(job.job_id, root=W)
for i, p in enumerate(paths):
    pass
os.environ.pop("COMMERCE_SHOP_STYLE", None)
check(get_mode_policy("commerce").mode == "commerce" and get_mode_policy("commerce") is not SHOP_POLICY, "기본 쇼핑 정책은 그대로 (상단 자막 + 상품 카드)")
scenes = pl.plan(spec, job)
check(os.environ.get("COMMERCE_SHOP_STYLE") == "1" and get_mode_policy("commerce") is SHOP_POLICY and SHOP_POLICY.center_y == 0.55 and SHOP_POLICY.semantic_chunks,
      "plan()이 18초 5문장 쇼핑 스타일 정책(중단 55%·의미 단위 자막)을 켬")
os.environ["OWNER_CAPTION_POS"] = "top"
check(get_mode_policy("commerce") is SHOP_TOP_POLICY and SHOP_TOP_POLICY.center_y == 0.24, "자막 위치 '상단'이면 24%")
os.environ.pop("OWNER_CAPTION_POS", None)
has = [pl.scene_has_media(s) for s in scenes]
check(all(has) and scenes[4].get("image_path") == paths[-1] and scenes[0].get("image_path") == paths[0], f"사진 배정: 문장별 컷, 마지막 씬 = 공식 썸네일 ({sum(has)}/5)")
check(pl.disclosure_short(spec) == ns.CONNECT_DISCLOSURE_SHORT and "쇼핑커넥트" in pl.disclosure_short(spec), "영상 화면 고지 = 네이버 쇼핑커넥트 문구")
tx = pl.upload_texts(spec, None)
check(ns.CONNECT_DISCLOSURE_FULL in tx["comment"] and "https://naver.me/abcd" in tx["comment"] and "쿠팡" not in tx["comment"] and "네이버 최저가" in tx["comment"],
      "첫 댓글: 쇼핑커넥트 링크 + 공정위 대가성 문구 (쿠팡 문구 없음)")
check(ns.CONNECT_DISCLOSURE_FULL == "이 포스팅은 네이버 쇼핑커넥트 활동의 일환으로 판매 발생 시 수수료를 제공받습니다.", "공정위 문구 원문")
pkg = ns.comment_package("배수구 클리너", "", ["꿀템"])
check(ns.CONNECT_LINK_PLACEHOLDER in pkg and ns.CONNECT_DISCLOSURE_FULL in pkg and "#꿀템" in pkg, "링크 비어 있으면 붙여 넣기 안내 + 고지 문구")
plain = pl.JobSpec(mode="commerce", name="일반 상품", media_paths=paths, mock=True)
pl.plan(plain, job) if False else None
pl.apply_style_env(plain)
check(os.environ.get("COMMERCE_SHOP_STYLE") is None and pl.disclosure_short(plain) != ns.CONNECT_DISCLOSURE_SHORT, "쇼핑 상품 팩이 없으면 기존 쿠팡 쇼핑 경로 그대로")

# ---------------------------------------------------------------- 9. 실제 렌더: 18초 5문장, 자막 이벤트는 문장당 최대 2덩어리
import core.render.caption_chunks as cc
events = []
_orig_cw = cc.chunk_windows
cc.chunk_windows = lambda chunks, sp, sc_: (events.append(list(chunks)), _orig_cw(chunks, sp, sc_))[1]
FF = None
try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
    ups = []
    for i in range(5):
        dst = os.path.join(job.uploads_dir, f"u{i}.jpg")
        shutil.copy(fixture("photo_pool.jpg" if i % 2 == 0 else "photo_room.jpg"), dst)
        ups.append(dst)
    spec_r = pl.JobSpec(mode="commerce", name=product["name"], usp="", media_paths=ups, mock=True, shop_item={**SI, "cut_roles": ["usage", "detail", "space", "package", "official"]},
                        link="https://naver.me/abcd", audio_mode="silent", caption_font="pop")
    scenes_r = pl.plan(spec_r, job)
    res = pl.render(spec_r, job, scenes_r)
    err = subprocess.run([FF, "-hide_banner", "-i", res.final_path], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
    check(os.path.exists(res.final_path) and 15 <= dur <= 22, f"실제 렌더: 쇼핑 18초 5문장 영상 {dur:.1f}초")
    long_lines = [s["voiceover"] for s in scenes_r if len(s["voiceover"]) > 18]
    ev_ok = all(1 < len(e) <= 2 and not any(len(x.replace(' ', '')) <= 5 for x in e) for e in events)
    check(events and ev_ok, f"실제 렌더 자막 이벤트: 긴 문장은 정확히 2덩어리, 1어절 꼬투리 없음 ({len(events)}문장): {events[:2]}")
    check(long_lines and len(events) >= 3, f"18자 넘는 문장 {len(long_lines)}개가 모두 의미 단위로 분할됨")
finally:
    cc.chunk_windows = _orig_cw
    os.environ.pop("COMMERCE_SHOP_STYLE", None)

finish(fails)
