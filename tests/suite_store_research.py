import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import json
import os

import core.naver_local as nl
import core.store_research as sr
from core.store_research import merge_research, naver_blog_snippets, research_store

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


os.environ["STORE_RESEARCH_DISABLE"] = ""
NAME = "효정명인갈비"
LOCAL = [{"name": NAME, "road_address": "경기도 가평군 설악면 한양부로 93 1층", "address": "", "category": "한식>육류,고기요리", "business_type": "food"}]
BLOG = [f"{NAME} 가평 설악면 방문 후기예요. 양념돼지갈비 시켰는데 명이나물이 같이 나와요. 무료주차도 돼요.",
        f"가평 설악 {NAME} 점심에 갔어요. 양념돼지갈비 숯불에 구워 먹었고 명이나물 리필했어요. 주차장도 넓어요.",
        f"{NAME} 가평 갈비집이에요. 숯불 향이 좋고 가평수제갈비도 있어요. 주차 편해요. 맛집 내돈내산 후기"]
GEMINI = {"address": "경기도 가평군 설악면 한양부로 93 1층", "location_context": "설악IC 인근 자라섬 30분 거리", "grounded": False, "sources": [],
          "special_menus": [{"name": "양념돼지갈비", "source": "블로그"}, {"name": "한우육회비빔밥", "source": "식신"}, {"name": "가짜메뉴탕", "source": ""}],
          "review_nouns": [{"noun": "명이나물", "source": "블로그"}, {"noun": "셀프바", "source": "식신"}]}

# ---------------------------------------------------------------- 1. 합치기: 확인됨 / 미확인, 근거 없는 항목은 버림
r = research_store(NAME, "", region="가평", local_fn=lambda q: LOCAL, blog_fn=lambda q: BLOG, gemini_fn=lambda q, rg: dict(GEMINI))
c, u = r["confirmed"], r["unconfirmed"]
check(r["address"] == "경기도 가평군 설악면 한양부로 93 1층" and r["address_from"] == "naver" and r["business_type"] == "food",
      "주소·업종은 공식 지역 검색 결과를 우선")
check("양념돼지갈비" in c["special_menus"] and "명이나물" in c["review_nouns"],
      f"웹 검색 항목이 블로그 요약문에도 있으면 '확인됨' (메뉴 {c['special_menus']}, 후기 명사 {c['review_nouns']})")
check("한우육회비빔밥" in u["special_menus"] and "셀프바" in u["review_nouns"] and "한우육회비빔밥" not in c["special_menus"]
      and "가짜메뉴탕" not in c["special_menus"] + u["special_menus"],
      f"블로그에서 확인 못 한 AI 요약은 '미확인'으로만, 출처 없는 항목은 버림 ({u['special_menus']}, {u['review_nouns']})")
check(c["location_context"].startswith("가평 나들이") and "자라섬" in u["location_context"] and "자라섬" not in c["location_context"],
      "상권: 주소 기반 표는 확인됨, AI가 말한 주변 거리·시설은 근거 링크 없으면 미확인")
check(not any(w in " ".join(c["special_menus"] + c["review_nouns"]) for w in ("맛집", "내돈내산", "효정", "명인", "설악", "가평 ", "후기")),
      f"상호·지역·검색용 문구(맛집·내돈내산)는 명사로 뽑지 않음 {c}")
rg = research_store(NAME, "", region="가평", local_fn=lambda q: [], blog_fn=lambda q: [], gemini_fn=lambda q, rg_: {**GEMINI, "grounded": True})
check("한우육회비빔밥" in rg["confirmed"]["special_menus"] and "셀프바" in rg["confirmed"]["review_nouns"] and rg["address"] == ""
      and rg["address_candidate"].startswith("경기도 가평군") and rg["steps"]["grounded"],
      "웹 검색 근거 링크가 있으면 그 항목은 확인됨, 공식 주소가 없으면 AI 주소는 후보로만")


def boom(*a):
    raise RuntimeError("down")


rb = research_store(NAME, "", region="가평", local_fn=boom, blog_fn=boom, gemini_fn=boom)
check(rb["address"] == "" and rb["confirmed"]["special_menus"] == [] and rb["unconfirmed"]["special_menus"] == [],
      "세 출처가 모두 실패해도 예외 없이 빈 결과 (수동 입력으로 계속)")
os.environ["STORE_RESEARCH_DISABLE"] = "1"
check(research_store(NAME, gemini_api_key="x", naver_id="a", naver_secret="b")["steps"]["naver_blog"] == 0, "STORE_RESEARCH_DISABLE=1 이면 외부 호출 없음")
os.environ["STORE_RESEARCH_DISABLE"] = ""

# 공식 API 요약문에 한 번만 나와도 음식·반찬·시설 어휘는 즉시 확정 (Gemini 교차 확인 없이)
rv = research_store(NAME, "", region="가평", local_fn=lambda q: LOCAL, gemini_fn=lambda q, rg: {},
                    blog_fn=lambda q: [f"{NAME} 가평 설악 갔는데 밑반찬으로 양념게장이 나왔어요. 주차장도 넓었어요."])
check("양념게장" in rv["confirmed"]["review_nouns"] and "주차장" in rv["confirmed"]["review_nouns"] and rv["unconfirmed"]["review_nouns"] == [],
      f"공식 API 요약문에 1회만 나온 반찬·시설도 확정: {rv['confirmed']['review_nouns']}")

# ---------------------------------------------------------------- 2. 네이버 블로그 검색 (공식 API HUB, 가짜 응답): 같은 이름 다른 지역 글·다른 가게 글 제외
calls = []


class R:
    status_code = 200

    def json(self):
        return {"items": [
            {"title": f"<b>{NAME}</b> 가평 후기", "description": f"가평 설악 {NAME} 양념돼지갈비 먹었어요"},
            {"title": f"{NAME} 광주 북구", "description": "광주광역시 북구 점심 갈비 후기"},
            {"title": "다른집 가평 갈비", "description": "가평 다른 가게 갈비 이야기"}]}


_orig = nl.requests.get
nl.requests.get = lambda url, params=None, headers=None, timeout=None: (calls.append((url, (params or {}).get("query"), headers)), R())[1]
try:
    sn = naver_blog_snippets(NAME, "ID", "SECRET", region_tokens=["가평", "설악"])
finally:
    nl.requests.get = _orig
kinds_called = {u.rsplit("/", 1)[-1] for u, _, _ in calls}
check(len(sn) == 1 and "양념돼지갈비" in sn[0] and len({q for _, q, _ in calls}) >= 3 and "X-NCP-APIGW-API-KEY-ID" in calls[0][2]
      and calls[0][0].endswith("/search/v1/blog") and {"blog", "webkr", "cafearticle", "news"} <= kinds_called,
      f"네이버 검색: 상호+지역이 맞는 글만(다른 지역·다른 가게 제외), 블로그 검색어 3종 + 웹문서·카페글·뉴스, 중복 제거, API HUB 헤더 ({len(sn)}건)")
check({q.split(" ", 1)[1] for _, q, _ in calls if q} >= {"밑반찬", "냉면 갈비탕", "메뉴판"}, "블로그 검색어에 본문 중간 키워드(밑반찬·냉면 갈비탕·메뉴판) 포함")
# 앱에서 켜지 않은 검색 종류(401)는 건너뛰고 블로그만으로 진행
nl.requests.get = lambda url, params=None, headers=None, timeout=None: (R() if url.endswith("/blog") else type("E", (), {"status_code": 401, "json": lambda self: {}})())
try:
    sn2 = naver_blog_snippets(NAME, "ID", "SECRET", region_tokens=["가평", "설악"])
finally:
    nl.requests.get = _orig
check(len(sn2) == 1, "웹문서·카페글·뉴스 API를 켜지 않았어도(401) 블로그 결과만으로 정상 진행")
check(naver_blog_snippets(NAME, "", "") == [], "키가 없으면 호출하지 않고 빈 목록")

# ---------------------------------------------------------------- 3. 화면: 가게 정보 가져오기 → 주소·가게 자랑·🔎 고유 팩트 자동 채움 (가짜 네이버 응답, 웹 검색은 키 없어 건너뜀)
from streamlit.testing.v1 import AppTest
os.environ.update({"NAVER_CLIENT_ID": "ID", "NAVER_CLIENT_SECRET": "SECRET", "GEMINI_API_KEY": ""})


class Resp:
    status_code = 200

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def fake_get(url, params=None, headers=None, timeout=None):
    if url.endswith("/search/v1/local"):
        return Resp({"items": [{"title": f"<b>{NAME}</b>", "category": "한식>육류,고기요리", "description": "", "address": "경기도 가평군 설악면 신천리",
                                "roadAddress": "경기도 가평군 설악면 한양부로 93 1층", "telephone": "", "link": ""}]})
    return Resp({"items": [{"title": f"{NAME} 가평 후기", "description": d} for d in BLOG]})


nl.requests.get = fake_get
try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120).run()
    at.text_input(key="place_query").input(NAME)
    at.button(key="place_fetch").click().run()
    usp = at.session_state.owner_usp or ""
    facts_ok = [x.value for x in at.success if "뽑힌 고유 팩트" in (x.value or "")]
    check(not at.exception and at.session_state.owner_address == "경기도 가평군 설악면 한양부로 93 1층"
          and "양념돼지갈비" in usp and "손님 리뷰에서 반복된 것" in usp and "명이나물" in usp,
          f"가게 정보 가져오기 한 번으로 주소·가게 자랑(USP)에 대표 메뉴·반복 명사가 자동으로 채워짐: {usp[:80]}")
    check(facts_ok and "양념돼지갈비" in facts_ok[0] and "명이나물" in facts_ok[0] and "가평 나들이" in facts_ok[0],
          "🔎 뽑힌 고유 팩트 박스에 자동 수집 결과 표시 (붙여넣기 없이)")
    # 미확인 항목 + 체크박스: 기본은 대본 팩트에 안 들어가고, 켜면 들어감
    at.session_state.store_research = {**at.session_state.store_research, "unconfirmed": {"special_menus": ["한우육회비빔밥"], "review_nouns": [], "location_context": ""}}
    at.run()
    off = [x.value for x in at.success if "뽑힌 고유 팩트" in (x.value or "")]
    at.checkbox(key="use_unconfirmed_facts").set_value(True).run()
    on = [x.value for x in at.success if "뽑힌 고유 팩트" in (x.value or "")]
    check(off and "한우육회비빔밥" not in off[0] and on and "한우육회비빔밥" in on[0],
          "미확인 항목은 기본으로 대본 팩트에서 제외, '확인 못 한 검색 요약도 쓰기'를 켜야 포함")
finally:
    nl.requests.get = _orig
    for k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
        os.environ.pop(k, None)

finish(fails)
