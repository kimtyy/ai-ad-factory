import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import io
import os

import numpy as np
import requests
from PIL import Image

import core.photo_safety as ps
import core.store_photos as stp
from core.shorts_meta import build_first_comment, build_shorts_description

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


class R:
    def __init__(self, status=200, payload=None, content=b"", ctype="image/jpeg"):
        self.status_code, self._p, self.content = status, payload, content
        self.headers = {"Content-Type": ctype}

    def json(self):
        return self._p


def jpg(w=900, h=700, color=(120, 120, 120)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG", quality=95)
    return b.getvalue()


# ---------------------------------------------------------------- 1. 도메인: 블로그 차단 / 공식 우선
U = {"blog1": "https://blogfiles.naver.net/MjAy/a.jpg", "blog2": "https://postfiles.naver.net/x/b.jpg", "blog3": "https://mblogthumb-phinf.pstatic.net/z/c.jpg",
     "place": "https://ldb-phinf.pstatic.net/2026/p.jpg", "yeogi": "https://image.goodchoice.kr/resize/a.jpg", "shop": "https://shop-phinf.pstatic.net/s.jpg",
     "home": "https://www.hyojung-galbi.co.kr/img/1.jpg"}
check(all(ps.source_kind(U[k]) == "blog" for k in ("blog1", "blog2", "blog3")) and all(ps.source_kind(U[k]) == "official" for k in ("place", "yeogi", "shop"))
      and ps.source_kind(U["home"]) == "other", "도메인 분류: 블로거(blogfiles·postfiles·mblogthumb) / 공식(ldb-phinf·goodchoice·shop-phinf) / 기타(업체 홈페이지)")
items = [{"link": U[k], "title": k} for k in ("blog1", "home", "place", "blog2", "yeogi", "shop")]
on = ps.rank_sources(items, True)
off = ps.rank_sources(items, False)
check([i["title"] for i in on] == ["place", "yeogi", "shop", "home"] and [i["title"] for i in off][-2:] == ["blog1", "blog2"] and len(off) == 6,
      f"공식 수집 ON: 블로그 제외 + 공식 → 기타 순 {[i['title'] for i in on]}, OFF: 블로그는 맨 뒤 경고용 {[i['title'] for i in off]}")

# ---------------------------------------------------------------- 2. 검색기에 적용
NAME = "효정명인갈비"


def mk(k, w=900, h=700):
    return {"title": f"<b>{NAME}</b> {k}", "link": U[k], "thumbnail": U[k], "sizewidth": str(w), "sizeheight": str(h)}


_get = requests.get
requests.get = lambda url, params=None, headers=None, timeout=None: R(200, {"items": [mk(k) for k in ("blog1", "blog2", "place", "home", "yeogi", "blog3")]})
try:
    r1 = stp.search_store_images(NAME, "가평", "ID", "SECRET")
    r2 = stp.search_store_images(NAME, "가평", "ID", "SECRET", official_only=False)
finally:
    requests.get = _get
check([i["source"] for i in r1["items"]] == ["official", "official", "other"] and r1["blog_dropped"] == 3, f"검색 기본값: 블로그 사진 3장 제외, 공식 우선 {[i['source'] for i in r1['items']]}")
check(r2["items"][-1]["source"] == "blog" and len(r2["items"]) == 6, "끄면 블로그 사진이 맨 뒤에 포함")
requests.get = lambda url, params=None, headers=None, timeout=None: R(200, {"items": [mk("blog1"), mk("blog2")]})
try:
    r3 = stp.search_store_images(NAME, "가평", "ID", "SECRET")
finally:
    requests.get = _get
check(r3["items"] == [] and "개인 블로거 사진" in r3["error"], "전부 블로그 사진이면 후보 없음 + 이유 안내")

# ---------------------------------------------------------------- 3. 시각 변형
gray = Image.new("RGB", (1000, 800), (120, 120, 120))
t = ps.transform_photo(gray)
a = np.asarray(t).astype(float)
mid = a[len(a) // 2 - 20: len(a) // 2 + 20].mean(axis=(0, 1))
top, bot = a[:8].mean(), a[-8:].mean()
check(abs(t.size[0] - 1000 * 0.89) <= 3 and abs(t.size[1] - 800 * 0.89) <= 3, f"테두리 크롭: 가로·세로 11%를 잘라 중앙 89% 사용 ({t.size})")
check(mid[0] > mid[2] + 4 and mid[0] > 120, f"색감 보정: 따뜻한 톤(R {mid[0]:.0f} > B {mid[2]:.0f}) + 채도·대비 소폭 상향")
check(top < mid.mean() - 10 and bot < mid.mean() - 15 and bot < top, f"상하단 그라데이션 비네팅: 위 {top:.0f} · 가운데 {mid.mean():.0f} · 아래 {bot:.0f}")
check(ps.transform_photo(Image.new("RGB", (80, 60), (50, 50, 50))).size == (80, 60), "아주 작은 사진은 크롭 없이 안전하게 처리")

W = tmp("photo_safety_")
requests.get = lambda url, headers=None, timeout=None, params=None: R(200, None, jpg(1000 + (len(url) % 7), 800, (120, 120, 120)))
try:
    p_t = stp.download_store_image({"link": U["place"], "thumbnail": ""}, W)
    p_raw = stp.download_store_image({"link": U["place"] + "?v=2", "thumbnail": ""}, W, transform=False)
finally:
    requests.get = _get
im_t, im_r = Image.open(p_t), Image.open(p_raw)
check(im_t.size[0] < im_r.size[0] and np.asarray(im_t)[:6].mean() < np.asarray(im_r)[:6].mean() - 8, "내려받는 즉시 편집 프레임으로 저장 (원본 복제 아님, 기본 적용)")

# ---------------------------------------------------------------- 4. 비전 검수: 사람·워터마크·글씨
import core.llm as llm
_gj = llm.generate_json
llm.generate_json = lambda *a, **k: ({"photos": [{"index": 1, "flag": ""}, {"index": 2, "flag": "person"}, {"index": 3, "flag": "watermark"}, {"index": 4, "flag": "text"}]}, "x")
try:
    fl = ps.screen_flags([Image.new("RGB", (100, 100))] * 4, "k")
    requests.get = lambda url, headers=None, timeout=None, params=None: R(200, None, jpg(600, 600))
    its = [{"link": f"https://ldb-phinf.pstatic.net/{i}.jpg", "thumbnail": f"https://ldb-phinf.pstatic.net/t{i}.jpg"} for i in range(4)]
    out = stp.screen_candidates(list(its), "k")
    none = stp.screen_candidates(list(its), "")
finally:
    llm.generate_json = _gj
    requests.get = _get
check(fl == ["", "person", "watermark", "text"] and [i.get("flag") for i in out] == [None, "person", "watermark", "text"] and not any(i.get("flag") for i in none),
      "비전: 사람(손·뒷모습 포함)·워터마크·글씨 위주 사진에 제외 표시, 키가 없으면 검사 못 함")


def _boom(*a, **k):
    raise RuntimeError("down")


llm.generate_json = _boom
try:
    check(ps.screen_flags([Image.new("RGB", (50, 50))], "k") == [""], "검수가 실패해도 멈추지 않음")
finally:
    llm.generate_json = _gj

# ---------------------------------------------------------------- 5. 출처·대가성 문구
d_tr = build_shorts_description("travel", "제목", ["#a"], "제휴 링크 포함")
d_co = build_shorts_description("commerce", "제목", ["#a"], "쿠팡")
d_ow = build_shorts_description("owner", "제목", ["#a"])
c_tr = build_first_comment("travel", "질문", ["#a"], link="https://x.y", disclosure_full="고지")
c_co = build_first_comment("commerce", "질문", ["#a"], link="https://x.y")
c_ow = build_first_comment("owner", "질문", ["#a"], search_keyword="효정명인갈비")
check(all(ps.SOURCE_NOTICE in s for s in (d_tr, d_co, c_tr, c_co)) and ps.SOURCE_NOTICE not in d_ow + c_ow, "제휴 쇼츠(숙소·쇼핑) 설명·첫 댓글에 출처·대가성 문구 자동 포함, 매장 모드는 제외")
check(ps.SOURCE_NOTICE == "※ 본 영상은 업체 공식 안내 이미지를 활용하여 제작되었으며, 제휴 마케팅 활동의 일환으로 수수료를 제공받을 수 있습니다.", "문구는 지시한 원문 그대로")

# ---------------------------------------------------------------- 6. 화면: 공식 체크박스 기본 ON, 제외 사진은 선택 불가
from streamlit.testing.v1 import AppTest
os.environ.update({"NAVER_CLIENT_ID": "ID", "NAVER_CLIENT_SECRET": "SECRET", "GEMINI_API_KEY": ""})


def ui_get(url, params=None, headers=None, timeout=None):
    if url.endswith("/search/v1/image"):
        its = [mk(k) for k in ("place", "yeogi", "home", "blog1")] + [{**mk("place"), "link": f"https://ldb-phinf.pstatic.net/{i}.jpg", "thumbnail": f"https://ldb-phinf.pstatic.net/{i}.jpg"} for i in range(8)]
        return R(200, {"items": its})
    if url.endswith("/search/v1/local"):
        return R(200, {"items": []})
    if "pstatic.net" in url or "goodchoice" in url or "co.kr" in url:
        return R(200, None, jpg(800, 600, (90, 100, 110)))
    return R(200, {"items": []})


requests.get = ui_get
try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120).run()
    cb = [c for c in at.checkbox if c.key == "official_only"]
    check(bool(cb) and cb[0].value is True and "공식 홍보 이미지만 수집" in cb[0].label, "🛡️ 공식 홍보 이미지만 수집(블로그 개인 사진 제외) 체크박스, 기본 ON")
    at.text_input(key="owner_name").input(NAME)
    at.button(key="store_photo_find").click().run()
    cands = at.session_state.store_photo_candidates
    check(not at.exception and all(c["source"] != "blog" for c in cands) and cands[0]["source"] == "official", f"기본 수집 결과에 블로그 사진 없음 ({len(cands)}장, 공식 우선)")
    at.session_state.store_photo_candidates = [{**c, "flag": "person"} if i in (1, 2) else c for i, c in enumerate(cands)]
    at.run()
    boxes = {c.key: c for c in at.checkbox if c.key and c.key.startswith("web")}
    bad = [boxes["webflag_1"], boxes["webflag_2"]]
    check(all(b.value is False and b.disabled and "제외" in b.label for b in bad), "사람·워터마크·글씨 감지 사진: '🚫 제외' 표시, 체크 해제 + 선택 불가")
    btn = at.button(key="store_photo_use")
    n_on = sum(1 for k, c in boxes.items() if k.startswith("webpick_") and c.value)
    check(f"{n_on}장" in btn.label and all(not c.value for k, c in boxes.items() if k.startswith("webflag_")), f"제외 사진은 영상 소스에 넣는 장수에서 빠짐: {btn.label}")
finally:
    requests.get = _get
    for k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
        os.environ.pop(k, None)

finish(fails)
