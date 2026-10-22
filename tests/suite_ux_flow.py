import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import io
import os

import requests
from PIL import Image
from streamlit.testing.v1 import AppTest

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


class R:
    def __init__(self, status=200, payload=None, content=b"", ctype="application/json"):
        self.status_code, self._p, self.content = status, payload, content
        self.headers = {"Content-Type": ctype}

    def json(self):
        return self._p


def jpg(w=900, h=700, color=(80, 130, 90)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


APP = os.path.join(ROOT, "app.py")


def new_app():
    return AppTest.from_file(APP, default_timeout=300).run()


def go(at, label, mode):
    at.session_state.mode_switch = label
    at.session_state.production_mode = mode
    at.run()
    return at


def seg(at, key):
    return next((e for e in at.main if getattr(e, "key", None) == key), None)


def side(at, label):
    return next((w for w in list(at.sidebar.selectbox) + list(at.sidebar.radio) if w.label == label), None)


MODES = {"owner": "🏠 사장님 매장 홍보", "travel": "🧳 숙소 제휴 쇼츠", "commerce": "🛒 쇼핑 제휴 쇼츠", "book": "📚 시골책방 도서 큐레이션"}
EXPECT = {"owner": ("voice_and_sub", "impact", "mid"), "travel": ("bgm_only", "serif", "top"), "commerce": ("voice_and_sub", "pop", "mid"), "book": ("bgm_only", "serif", "mid")}

# ---------------------------------------------------------------- 1. 모드별 최적 스타일 프리셋 자동 장착
at = new_app()
got = {}
for mode in ("owner", "travel", "commerce", "book"):
    if mode != "owner":
        # 실제 모드 탭 조작처럼 on_change 콜백을 거치도록 segmented_control 값을 바꿈
        seg(at, "mode_switch").set_value(MODES[mode]).run()
    ss = at.session_state
    got[mode] = (ss.audio_choice, ss.caption_font_choice, ss.caption_pos)
check(got == EXPECT, f"모드 탭을 누르면 오디오·폰트·자막 위치 프리셋 자동 장착: {got}")
a2 = side(at, "오디오 모드")
check(a2 is not None and a2.value == "bgm_only", "사이드바 위젯에도 반영됨 (책방: 나레이션 없음)")

# 사용자가 직접 바꾸면 그 모드에서는 기억하고, 프리셋으로 덮어쓰지 않음
seg(at, "mode_switch").set_value("🛒 쇼핑 제휴 쇼츠").run()
fr = side(at, "자막 폰트")
fr.set_value("impact").run()                                        # 쇼핑에서 임팩트 고딕으로 직접 변경
seg(at, "mode_switch").set_value("🧳 숙소 제휴 쇼츠").run()
check(at.session_state.caption_font_choice == "serif", "다른 모드로 가면 그 모드 프리셋(명조)")
seg(at, "mode_switch").set_value("🛒 쇼핑 제휴 쇼츠").run()
check(at.session_state.caption_font_choice == "impact", "쇼핑으로 돌아오면 직접 바꾼 값(임팩트 고딕)을 그대로 유지")

# ---------------------------------------------------------------- 2. 모드 전환 시 이전 모드 데이터 분리
at = new_app()
seg(at, "mode_switch").set_value("🧳 숙소 제휴 쇼츠").run()
at.text_input(key="product_name_input").input("강릉오션뷰펜션더영진").run()
at.text_input(key="product_usp_input").input("히노끼탕, 통창 뷰, 오션뷰").run()
seg(at, "mode_switch").set_value("🛒 쇼핑 제휴 쇼츠").run()
name_w = [t for t in at.text_input if t.key == "product_name_input"][0]
usp_w = [t for t in at.text_input if t.key == "product_usp_input"][0]
check(name_w.value == "" and usp_w.value == "", "숙소에서 입력한 숙소명·특장점이 쇼핑 입력칸에 남지 않음")
conc = at.session_state.suggested_concepts
txt = " ".join(f"{c.get('headline')} {c.get('core_concept')} {c.get('description')}" for c in conc)
check(conc and "우리 매장" not in txt and "손님" not in txt, f"쇼핑 1컨셉 기본안에 매장 문구 없음: {conc[0]['core_concept'] if conc else ''}")
seg(at, "mode_switch").set_value("🏠 사장님 매장 홍보").run()
conc2 = at.session_state.suggested_concepts
check(conc2 and "매장" in " ".join(c.get("core_concept", "") for c in conc2), "매장 모드는 기존 매장 컨셉 기본안")

# ---------------------------------------------------------------- 3. 2단계 화면: 접이식 + 요약 카드 + ② 버튼
at = new_app()
exps = [e for e in at.expander if "직접 수정하기" in e.label]
check(len(exps) == 1 and exps[0].proto.expanded is False, "세부 편집(정보·컨셉·대본·사진)은 접이식(기본 접힘) 안에")
labels = [b.label for b in at.button]
check("⚡ 정보 불러오기 & 자동 기획 (5줄 대본)" in labels and "✨ 인스타 릴스 / 유튜브 쇼츠용 영상 만들기" in labels, "① 자동 기획 버튼 + ② 영상 만들기 버튼이 한 화면 흐름에 노출")
inside = [b for e in exps for b in e.button]
check(not any("영상 만들기" in b.label for b in inside) and any("스토리보드 자동 기획" in b.label for b in inside), "스토리보드 자동 기획·컨셉 도출 버튼은 접이식 안으로, ② 버튼은 밖")
check(seg(at, "chip_audio") is not None and seg(at, "chip_font") is not None, "스타일 칩(나레이션/BGM, 폰트 3종)이 요약 카드에 노출")
seg(at, "chip_audio").set_value("bgm").run()
check(at.session_state.audio_choice == "bgm_only" and side(at, "오디오 모드").value == "bgm_only", "칩으로 바꾸면 사이드바 오디오 설정도 같이 바뀜")
seg(at, "chip_font").set_value("pop").run()
check(at.session_state.caption_font_choice == "pop", "폰트 칩 → 폰트 설정 반영")

# ---------------------------------------------------------------- 4. 네이버 쇼핑 원클릭 (가짜 서버)
SHOP = [{"title": f"<b>캠핑 제빙기</b> 모델{i}", "link": f"https://search.shopping.naver.com/catalog/{i}", "image": f"https://shop-phinf.pstatic.net/m/{i}.jpg",
         "lprice": str(39900 + i * 1000), "hprice": "", "mallName": f"스토어{i}", "productId": str(1000 + i), "productType": "2", "brand": "브랜드", "maker": "",
         "category1": "스포츠/레저", "category2": "캠핑", "category3": "", "category4": ""} for i in range(8)]


def fake_get(url, params=None, headers=None, timeout=None, **kw):
    q = (params or {}).get("query", "")
    if "shop" in url and ("search/v1/shop" in url or url.endswith("shop.json")):
        return R(200, {"items": SHOP[:5] if "캠핑" in q or "제빙기" in q else SHOP[:3]})
    if "blog" in url:
        return R(200, {"total": 50, "items": [{"title": f"캠핑 제빙기 후기 {i}", "description": "얼음이 금방 만들어지고 소음이 적어요 캠핑 갈 때 챙겼어요", "link": "x", "bloggername": "b", "postdate": "20261015"} for i in range(8)]})
    if "image" in url and ("search/v1/image" in url or url.endswith("image.json")):
        return R(200, {"items": [{"title": f"캠핑 제빙기 {i}", "link": f"https://shop-phinf.pstatic.net/img/{i}.jpg", "thumbnail": f"https://shop-phinf.pstatic.net/img/{i}.jpg", "sizewidth": "900", "sizeheight": "900"} for i in range(12)]})
    if "pstatic.net" in url or "example" in url:
        return R(200, None, jpg(800 + (abs(hash(url)) % 97), 800, (abs(hash(url)) % 200, 90, 120)), "image/jpeg")
    return R(200, {"items": []})


def fake_post(url, json=None, headers=None, timeout=None, **kw):
    return R(200, {"results": [{"title": g["groupName"], "keywords": g["keywords"], "data": [{"period": f"2026-10-{i + 1:02d}", "ratio": 10 + (i if idx == 0 else 0)} for i in range(30)]}
                               for idx, g in enumerate(json["keywordGroups"])]})


_get, _post = requests.get, requests.post
os.environ.update({"NAVER_CLIENT_ID": "ID", "NAVER_CLIENT_SECRET": "SECRET", "GEMINI_API_KEY": "", "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": ""})
requests.get, requests.post = fake_get, fake_post
try:
    at = new_app()
    seg(at, "mode_switch").set_value("🛒 쇼핑 제휴 쇼츠").run()
    check(not at.exception and any(b.key == "shop_find" for b in at.button) and any(t.key == "shop_query" for t in at.text_input), "쇼핑 최상단: 꿀템 TOP 5 + 상품 검색(키워드·스마트스토어 URL) 입력")
    cats = [s for s in at.selectbox if s.key == "shop_category"][0]
    check(len(cats.options) == 5 and any("캠핑" in o for o in cats.options) and any("계절 가전" in o for o in cats.options), f"카테고리: {cats.options}")
    at.button(key="shop_find").click().run()
    picks = [b for b in at.button if b.key and b.key.startswith("shop_pick_")]
    check(not at.exception and len(picks) == 5 and all("바로 기획" in b.label for b in picks), f"꿀템 카드 {len(picks)}개 + 각 카드 [바로 기획] 버튼")
    at.button(key=picks[0].key).click().run()
    ss = at.session_state
    item = ss.shop_item
    sb = ss.storyboard
    check(not at.exception and item and item["product"]["name"].startswith("캠핑 제빙기"), "카드 한 번 → 상품·후기·사진 팩 수집")
    ups = ss.uploaded_paths
    check(len(ups) >= 2 and item["cut_roles"][-1] == "official" and "thumb" not in os.path.basename(ups[0]), f"사진 {len(ups)}장 큐레이션, 마지막 컷 = 공식 대표 썸네일 (역할 {item['cut_roles']})")
    check(len(sb) == 5 and sb[0].get("shop_auto") and sb[-1]["voiceover"].startswith("품절되기 전에 고정 댓글 네이버 최저가"), f"카드 클릭만으로 5문장 대본까지 자동 기획 ({len(sb)}씬, 5문장=CTA)")
    check(any("체감" in (c.get("core_concept", "") + c.get("angle", "")) or "상품" in c.get("core_concept", "") or "불편" in c.get("core_concept", "") for c in ss.suggested_concepts) and "우리 매장" not in str(ss.suggested_concepts), "1컨셉은 쇼핑용 기본안으로 자동 선택")
    check(at.session_state.affiliate_program == "naver_shop", "제휴 종류 = 네이버 쇼핑커넥트")
    txt = at.session_state.shop_connect_link
    at.text_input(key="shop_connect_link").input("https://naver.me/abcd").run()
    from core import pipeline
    spec_text = None
    check(at.session_state.affiliate_link == "https://naver.me/abcd", "쇼핑커넥트 링크 입력 → 첫 댓글 링크로 사용")
    # 검색 + URL 하이브리드
    at.text_input(key="shop_query").input("캠핑 제빙기").run()
    at.button(key="shop_search").click().run()
    res_btns = [b for b in at.button if b.key and b.key.startswith("shop_res_")]
    check(len(res_btns) == 5, f"키워드 검색 결과 카드 {len(res_btns)}개")
    at.text_input(key="shop_query").input("https://smartstore.naver.com/abc/products/123").run()
    at.button(key="shop_search").click().run()
    check(any("상품명" in (i.value or "") for i in at.info), "스마트스토어 URL은 페이지를 열지 않고 상품명 입력을 안내 (링크는 보존)")
finally:
    requests.get, requests.post = _get, _post

# ---------------------------------------------------------------- 5. 숙소 원클릭: 고르면 사진 투입 + 5문장 자동 기획
import core.stay_curation as stc
LOCAL = {"items": [{"title": "<b>가평 리버뷰 풀빌라</b>", "category": "숙박>펜션", "address": "경기도 가평군 1", "roadAddress": "경기도 가평군 청평면 1", "link": ""}]}


def stay_get(url, params=None, headers=None, timeout=None, **kw):
    if url.endswith("/search/v1/local"):
        return R(200, LOCAL)
    if url.endswith("/search/v1/blog"):
        return R(200, {"total": 500, "items": [{"title": "가평 리버뷰 풀빌라", "description": "가평 리버뷰 풀빌라 1박 180,000원부터 기준 4인 개별 온수풀 불멍", "postdate": "20261015"}] * 3})
    if url.endswith("/search/v1/image"):
        return R(200, {"items": [{"title": f"<b>가평 리버뷰 풀빌라</b> {i}", "link": f"https://ldb-phinf.pstatic.net/{i}.jpg", "thumbnail": f"https://ldb-phinf.pstatic.net/{i}.jpg", "sizewidth": "900", "sizeheight": "700"} for i in range(10)]})
    if "pstatic.net" in url:
        return R(200, None, jpg(800 + (abs(hash(url)) % 97), 700, (abs(hash(url)) % 200, 100, 150)), "image/jpeg")
    if "yeogi.com" in url:
        return R(403, None)
    return R(200, {"items": []})


requests.get, requests.post = stay_get, fake_post
try:
    at = new_app()
    seg(at, "mode_switch").set_value("🧳 숙소 제휴 쇼츠").run()
    at.button(key="stay_find").click().run()
    picks = [b for b in at.button if b.key and b.key.startswith("stay_pick_")]
    if picks:
        at.button(key=picks[0].key).click().run()
        ss = at.session_state
        check(not at.exception and len(ss.uploaded_paths) >= 5 and len(ss.storyboard) == 5 and ss.storyboard[0].get("stay_auto"),
              f"숙소 카드 한 번 → 공식 사진 {len(ss.uploaded_paths)}장 투입 + 5문장 자동 기획 ({len(ss.storyboard)}씬)")
        check("📐 자막 위치" in [r.label for r in at.sidebar.radio], "숙소 수집 후 자막 위치 선택 노출 (프리셋 상단 24%)")
    else:
        check(False, "숙소 카드가 안 나옴")
finally:
    requests.get, requests.post = _get, _post
    for k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
        os.environ.pop(k, None)

finish(fails)
