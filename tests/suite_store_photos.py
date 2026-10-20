import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import io
import os

import requests
from PIL import Image

import core.store_photos as sp

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


NAME = "효정명인갈비"
os.environ["STORE_RESEARCH_DISABLE"] = ""


def png_bytes(w=800, h=600, color=(200, 80, 40)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


def item(i, title=None, w=900, h=700, name=NAME):
    return {"title": title or f"<b>{name}</b> 가평 후기 {i}", "link": f"https://img.example.com/{i}.jpg", "thumbnail": f"https://thumb.example.com/{i}.jpg",
            "sizewidth": str(w), "sizeheight": str(h)}


calls = []


class R:
    def __init__(self, status=200, payload=None, content=b"", ctype="image/jpeg"):
        self.status_code, self._p, self.content = status, payload, content
        self.headers = {"Content-Type": ctype}

    def json(self):
        return self._p


_orig_get = requests.get

# ---------------------------------------------------------------- 1. 검색·필터: 상호 어절 + 400px 이상 + 최대 12장, 부족하면 검색어·필터 확대
def fake_search(url, params=None, headers=None, timeout=None):
    calls.append((url, dict(params or {}), headers))
    q = (params or {}).get("query", "")
    base = [item(i) for i in range(30)] if "대표메뉴" not in q else [item(100 + i) for i in range(5)]
    base += [item(200, title="전혀 다른 가게 사진"), item(201, w=300, h=300), item(202, w=900, h=399)]
    return R(200, {"items": base})


requests.get = fake_search
try:
    res = sp.search_store_images(NAME, "가평", "ID", "SECRET")
finally:
    requests.get = _orig_get
titles_ok = all("효정명인갈비" in it["title"] for it in res["items"])
check(len(res["items"]) == 12 and titles_ok and not any(it["width"] < 400 or it["height"] < 400 for it in res["items"]) and len({it["link"] for it in res["items"]}) == 12,
      f"검색: 제목에 상호가 있고 가로·세로 400px 이상인 사진만, 중복 없이 최대 12장 ({len(res['items'])}장)")
check(calls[0][0].endswith("/search/v1/image") and calls[0][1] == {"query": "가평 효정명인갈비", "display": 20, "sort": "sim", "filter": "large"}
      and "X-NCP-APIGW-API-KEY-ID" in calls[0][2], "첫 호출: 'query=지역 상호, display=20, sort=sim, filter=large', API HUB 헤더")

few_calls = []


def fake_few(url, params=None, headers=None, timeout=None):
    few_calls.append(dict(params or {}))
    q, f = (params or {}).get("query", ""), (params or {}).get("filter")
    n = 3 if (f == "large" and "대표메뉴" not in q) else (2 if f == "large" else 6)
    return R(200, {"items": [item(hash((q, f, i)) % 10_000_000) for i in range(n)]})


requests.get = fake_few
try:
    res2 = sp.search_store_images(NAME, "가평", "ID", "SECRET")
finally:
    requests.get = _orig_get
check(len(res2["items"]) >= 8 and any("대표메뉴" in c["query"] for c in few_calls) and any(c["filter"] == "all" for c in few_calls),
      f"8장 미만이면 '{{상호}} 대표메뉴'로 더 찾고, 그래도 부족하면 filter=all로 다시 ({len(res2['items'])}장, 호출 {len(few_calls)}회)")

requests.get = lambda url, params=None, headers=None, timeout=None: R(401, {"error": {"errorCode": 401}})
try:
    res3 = sp.search_store_images(NAME, "가평", "ID", "SECRET")
finally:
    requests.get = _orig_get
check(res3["items"] == [] and "이미지 검색" in res3["error"] and "NAVER API HUB" in res3["error"], "이미지 검색이 앱에 꺼져 있으면(401) 켜는 방법 안내")
check(sp.search_store_images(NAME, "가평", "", "")["items"] == [] and sp.search_store_images("", "가평", "a", "b")["error"], "키·상호가 없으면 호출하지 않음")

# ---------------------------------------------------------------- 2. 다운로드: UA·Referer·3초, 원본 403이면 썸네일, 큰 사진은 2560px 이하로
dl = []
big = png_bytes(4000, 3000)


def fake_dl(url, headers=None, timeout=None, params=None):
    dl.append((url, headers, timeout))
    if "img.example.com/1" in url:
        return R(403, None, b"")
    if "thumb.example.com/1" in url:
        return R(200, None, big)
    if "img.example.com/2" in url:
        return R(200, None, b"<html>blocked</html>", "text/html")
    return R(200, None, png_bytes(900, 700))


W = tmp("store_photos_")
requests.get = fake_dl
try:
    p_thumb = sp.download_store_image(item(1), W)
    p_ok = sp.download_store_image(item(3), W)
    p_html = sp.download_store_image({"link": "https://img.example.com/2.jpg", "thumbnail": ""}, W)
finally:
    requests.get = _orig_get
sz = Image.open(p_thumb).size if p_thumb else None
check(p_thumb and max(sz) <= 2560 and p_ok and os.path.basename(p_ok).startswith("web_") and p_html is None,
      f"다운로드: 원본이 403이면 썸네일로 대체, 큰 사진은 긴 변 2560px 이하로 줄여 JPEG 저장 ({sz}), HTML 응답은 사진으로 안 침")
h0 = dl[0][1]
check(h0["Referer"] == "https://search.naver.com" and "Mozilla" in h0["User-Agent"] and all(t == 3 for _, _, t in dl), "다운로드 헤더: User-Agent + Referer https://search.naver.com, 타임아웃 3초")

# ---------------------------------------------------------------- 3. 화면: 4열 갤러리·상위 8장 기본 체크·체크 해제 후 영상 소스로
from streamlit.testing.v1 import AppTest
os.environ.update({"NAVER_CLIENT_ID": "ID", "NAVER_CLIENT_SECRET": "SECRET", "GEMINI_API_KEY": ""})


def ui_get(url, params=None, headers=None, timeout=None):
    if url.endswith("/search/v1/image"):
        return R(200, {"items": [item(i) for i in range(12)]})
    if url.endswith("/search/v1/local"):
        return R(200, {"items": []})
    if "thumb.example.com" in url or "img.example.com" in url:
        n = int(url.rsplit("/", 1)[-1].split(".")[0])
        return R(200, None, png_bytes(800 + n, 600, (n * 20 % 255, 100, 50)))
    return R(200, {"items": []})


requests.get = ui_get
try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120).run()
    at.text_input(key="owner_name").input(NAME)
    at.button(key="store_photo_find").click().run()
    boxes = sorted([c for c in at.checkbox if c.key and c.key.startswith("webpick_")], key=lambda c: int(c.key.split("_")[1]))   # 화면은 열 단위로 나열됨
    check(not at.exception and len(boxes) == 12 and [b.value for b in boxes] == [True] * 8 + [False] * 4 and all("✅ 사용" in b.label for b in boxes),
          f"갤러리: 후보 {len(boxes)}장, 각 사진 밑 '✅ 사용' 체크, 상위 8장 기본 체크")
    check(any("블로거·방문자" in (c.value or "") and "초상권" in (c.value or "") for c in at.caption), "저작권·초상권 확인 경고 표시")
    at.checkbox(key="webpick_2").set_value(False).run()               # 사람 얼굴 등 부적절한 사진 체크 해제
    use_btn = at.button(key="store_photo_use")
    check("7장" in use_btn.label, f"체크를 풀면 버튼의 장수가 바뀜: {use_btn.label}")
    use_btn.click().run()
    ups = at.session_state.uploaded_paths
    webs = at.session_state.web_photo_paths
    check(not at.exception and len(ups) == 7 and all(os.path.basename(p).startswith("web_") for p in ups) and set(webs) == set(ups)
          and all(os.path.exists(p) for p in ups), f"선택한 7장이 직접 올린 사진처럼 영상 소스 목록에 들어감 ({len(ups)}장), 웹 사진으로 기록")
    check(at.session_state.store_photo_candidates == [] and any("영상 소스에 넣었어요" in (i.value or "") for i in at.info), "넣은 뒤 갤러리는 정리되고 안내 표시")
finally:
    requests.get = _orig_get
    for k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
        os.environ.pop(k, None)

finish(fails)
