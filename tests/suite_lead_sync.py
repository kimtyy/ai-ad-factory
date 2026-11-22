import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import json
import os
import shutil
import subprocess
from unittest import mock

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


# ---------------------------------------------------------------- 가짜 네이버 플레이스 페이지 (실제 구조: m.place.naver.com Apollo state)
def apollo_html(pid, name, road, addr, category="맥주,호프", keywords=("눈꽃맥주", "설맥치킨"), menus=(("눈꽃빙수", "14900"), ("골뱅이 무침(기본)", "18900"), ("골뱅이 무침(특)", "25000"))):
    state = {
        f"PlaceDetailBase:{pid}": {"__typename": "PlaceDetailBase", "id": pid, "name": name, "category": category, "roadAddress": road, "address": addr,
                                   "conveniences": ["단체 이용 가능", "예약", "주차"]},
        "RestaurantSeatItems:1": {"__typename": "RestaurantSeatItems", "name": "단체석", "description": "최소 10명 ~ 최대 30명"},
        "FsasReview:a": {"__typename": "FsasReview", "type": "blog", "title": "가평 설맥 후기", "contents": "가평 설맥 후기 눈꽃맥주가 시원하고 안주가 푸짐해요", "url": "https://m.blog.naver.com/x/1"},
        f"VisitorReviewStatsResult:{pid}": {"__typename": "VisitorReviewStatsResult", "analysis": {"votedKeyword": {"details": [
            {"displayName": "친절해요", "count": 11}, {"displayName": "음식이 맛있어요", "count": 24}, {"displayName": "단체모임 하기 좋아요", "count": 12}, {"displayName": "양이 많아요", "count": 3}]}}},
        "PlaceDetailTopPhotoItem:1": {"__typename": "PlaceDetailTopPhotoItem", "originalUrl": "https://ldb-phinf.pstatic.net/a.jpg", "mediaSource": "business", "mediaFormat": "image", "width": 1000, "height": 800},
        "PlaceDetailTopPhotoItem:2": {"__typename": "PlaceDetailTopPhotoItem", "originalUrl": "https://ldb-phinf.pstatic.net/blog.jpg", "mediaSource": "visitor", "mediaFormat": "image"},
        "PlaceDetail_BaeminMenuGroup:1": {"__typename": "PlaceDetail_BaeminMenuGroup", "name": "대표메뉴", "order": 1, "menus": [{"__ref": "PlaceDetail_BaeminMenu:0"}]},
    }
    for i, (n, p) in enumerate(menus):
        state[f"PlaceDetail_BaeminMenu:{i}"] = {"__typename": "PlaceDetail_BaeminMenu", "name": n, "price": p, "order": i}
    kw = ",".join(json.dumps(k, ensure_ascii=False) for k in keywords)
    return f'<html><script>window.__APOLLO_STATE__ = {json.dumps(state, ensure_ascii=False)};\n</script><script>{{"keywordList":[{kw}]}}</script></html>'


RIGHT, WRONG = "1430341417", "1999999999"
PAGES = {
    RIGHT: apollo_html(RIGHT, "설맥 가평현리점", "경기 가평군 조종면 조종희망로 16-19 1층", "경기 가평군 조종면 현리 259-10"),
    WRONG: apollo_html(WRONG, "설맥 강남점", "서울 강남구 테헤란로 152", "서울 강남구 역삼동 737", menus=(("엉뚱한 메뉴", "1000"),), keywords=("강남키워드",)),
}
SEARCH_HTML = (f'<a href="https://m.place.naver.com/restaurant/{WRONG}/home">a</a><a href="https://m.place.naver.com/restaurant/{WRONG}/menu">b</a>'
               f'<a href="https://m.place.naver.com/restaurant/{RIGHT}/home">c</a>')
ONLY_WRONG_SEARCH = f'<a href="https://m.place.naver.com/restaurant/{WRONG}/home">a</a>'


def fake_get_factory(search_html):
    def fake_get(url, params=None, **kw):
        class R:
            status_code = 200

            def __init__(self, text):
                self.content = text.encode("utf-8")

            def raise_for_status(self):
                return None
        if "m.search.naver.com" in url:
            return R(search_html)
        for pid, html in PAGES.items():
            if f"/place/{pid}/" in url or f"/{pid}/" in url:
                return R(html)
        raise ConnectionError("offline test: " + url[:60])
    return fake_get


def get_str(search_html):
    fg = fake_get_factory(search_html)
    return lambda url, params=None, **kw: fg(url, params).content.decode("utf-8")


import core.naver_place_menu as NPM
from core import lead_link as LL

# ---------------------------------------------------------------- 1. 플레이스 홈 파서 (place_id 기반 공식정보)
home = NPM.parse_place_home(PAGES[RIGHT], RIGHT)
check(home and home["name"] == "설맥 가평현리점" and home["road_address"].startswith("경기 가평군") and home["category"] == "맥주,호프", "홈 파서: 상호·도로명 주소·업종")
check(home["keywords"] == ["눈꽃맥주", "설맥치킨"] and home["seats"] == ["단체석 최소 10명 ~ 최대 30명"] and "예약" in home["conveniences"], "대표 키워드·좌석·편의시설")
check([v["keyword"] for v in home["voted"]][:3] == ["음식이 맛있어요", "단체모임 하기 좋아요", "친절해요"], "방문자가 고른 키워드는 많이 고른 순")
check(len(home["reviews"]) == 1 and home["reviews"][0]["title"] == "가평 설맥 후기", "이 매장에 연결된 블로그 후기")
check(len(home["photos"]) == 1 and home["photos"][0]["url"].endswith("/a.jpg"), "사장님이 올린 공식 사진(business)만, 방문자 사진 제외")
check(NPM.parse_place_home(PAGES[RIGHT], WRONG) is None and NPM.parse_place_home("<html></html>") is None, "다른 place_id·빈 페이지는 None (예외 없음)")
summ = NPM.build_official_summary(home)
check("대표 키워드: 눈꽃맥주, 설맥치킨" in summ and "좌석: 단체석" in summ and "편의시설: 단체 이용 가능" in summ and "음식이 맛있어요(24)" in summ, f"공식 요약: {summ[:80]}...")
check(NPM.build_official_summary(None) == "" and NPM.build_official_summary({"keywords": [], "voted": [], "seats": [], "conveniences": [], "category": ""}) == "", "없는 항목은 지어내지 않고 빈 문자열")

# ---------------------------------------------------------------- 2. 주소로 정확한 지점 고르기 (동명 다른 지점이 섞이지 않게)
check(NPM.region_tokens("경기 가평군 조종면 조종희망로 16-19 1층")[:2] == ["가평", "조종"], "주소 → 지역 핵심어 (경기 같은 광역명 제외)")
pid = NPM.resolve_place_id("설맥 가평현리점", "경기 가평군 조종면 조종희망로 16-19 1층", get=get_str(SEARCH_HTML))
check(pid == RIGHT, f"검색 1순위가 다른 지점이어도 주소가 맞는 지점을 고름 ({pid})")
check(NPM.resolve_place_id("설맥 가평현리점", "경기 가평군 조종면", get=get_str(ONLY_WRONG_SEARCH)) == "", "주소가 맞는 지점이 없으면 엉뚱한 지점 대신 '' ")
check(NPM.resolve_place_id("설맥 가평현리점", "", get=get_str(SEARCH_HTML)) == WRONG, "주소를 모르면 검색 1순위 (기존 동작)")
check(NPM.resolve_place_id("", "경기", get=get_str(SEARCH_HTML)) == "" and NPM.resolve_place_id("x", "경기", get=lambda *a, **k: (_ for _ in ()).throw(ConnectionError())) == "", "빈 상호·네트워크 오류도 예외 없이 ''")

# ---------------------------------------------------------------- 3. 번들(메뉴판+홈) / place_id 직접 지정
b = NPM.fetch_place_bundle(RIGHT, get=get_str(SEARCH_HTML))
check(b["source"] == "naver_place" and b["place_id"] == RIGHT and b["place_name"] == "설맥 가평현리점" and [m["name"] for m in b["menus"]] == ["눈꽃빙수", "골뱅이 무침"], f"번들: 공식 메뉴(괄호 옵션 정리·중복 제거) {[m['name'] for m in b['menus']]}")
check("엉뚱한 메뉴" not in b["names"] and "눈꽃맥주" in b["names"] and "강남키워드" not in b["names"], "다른 지점 메뉴·키워드는 섞이지 않음, 대표 키워드는 포함")
check(b["official_summary"] == summ and b["road_address"].startswith("경기 가평군") and len(b["photos"]) == 1, "번들에 공식 요약·주소·사진")
check(NPM.fetch_place_bundle("abc")["source"] == "" and NPM.fetch_place_bundle("123")["source"] == "", "place_id 형식이 틀리면 빈 결과(숫자 6자리 이상만)")
check(NPM.fetch_place_bundle(RIGHT, get=lambda *a, **k: (_ for _ in ()).throw(ConnectionError()))["source"] == "", "네트워크 오류 → 빈 결과, 예외 없음")
o = NPM.collect_official_menu("", place_id=RIGHT, get=get_str(SEARCH_HTML))
check(o["source"] == "naver_place" and o["place_id"] == RIGHT and "눈꽃빙수" in o["names"], "collect_official_menu(place_id=…): 검색 없이 그 매장 메뉴판")
lines = NPM.menu_text_lines(b)
check("눈꽃빙수 · 14,900원" in lines and "⭐ 눈꽃맥주" in lines, "화면용 메뉴판 텍스트")

# ---------------------------------------------------------------- 4. 주소 파라미터 해석 (외부 입력이므로 제한)
lead = LL.parse_lead_params({"store": " 설맥  가평현리점 ", "address": "경기 가평군", "place_id": "1430341417", "menu": "눈꽃빙수", "font": "둥근 예능 팝체",
                             "voice": "차분한 남성", "bgm": "따뜻한", "memo": "단체석 30석 완비\n한우육회 메인 강조"})
check(lead["store"] == "설맥 가평현리점" and lead["place_id"] == "1430341417" and lead["memo"] == "단체석 30석 완비 한우육회 메인 강조", "공백·줄바꿈 정리")
check(LL.parse_lead_params({}) is None and LL.parse_lead_params({"store": "  "}) is None and LL.parse_lead_params({"menu": "x"}) is None, "상호명(store)이 없으면 일반 접속(None)")
bad = LL.parse_lead_params({"store": "가게", "place_id": "12abc", "memo": "가" * 500, "address": "주" * 500, "menu": "\x00\x1f메뉴"})
check(bad["place_id"] == "" and len(bad["memo"]) == 100 and len(bad["address"]) == 120 and bad["menu"] == "메뉴", "place_id는 숫자만, 길이 제한, 제어문자 제거")
check(LL.parse_lead_params({"store": ["여러", "값"]})["store"] == "여러", "리스트 값은 첫 값")
check(LL.lead_signature(lead) == LL.lead_signature(dict(lead)) and LL.lead_signature(lead) != LL.lead_signature({**lead, "memo": "다름"}), "같은 리드는 같은 식별값, 다르면 다른 값")
st = lambda **k: LL.style_from_lead({"font": "", "voice": "", "bgm": "", **k})
check(st(font="임팩트 고딕")["font"] == "impact" and st(font="감성 명조 (AI 추천)")["font"] == "serif" and st(font="둥근 예능 팝체")["font"] == "pop" and st(font="단단한 붓글씨")["font"] == "impact" and st(font="모르는")["font"] is None, "폰트 선택 → 작업실 폰트")
check(st(voice="밝은 여성 (AI 추천)")["actor"] == "female_20s" and st(voice="차분한 남성")["actor"] == "male_30s" and st(voice="나레이션 없음 (BGM만)")["audio"] == "bgm_only" and st(voice="나레이션 없음 (BGM만)")["actor"] is None, "목소리 선택 → 성우·나레이션 없음")
check(st(bgm="신나는")["bgm_mood"] == "업템포" and st(bgm="따뜻한")["bgm_mood"] == "어쿠스틱" and st(bgm="세련된")["bgm_mood"] == "미드템포" and st(bgm="AI 추천")["bgm_mood"] is None, "BGM 선택 → 분위기 (AI 추천은 기본값 유지)")
usp = LL.compose_usp(official_summary=summ, memo="단체석 30석 완비", menu="눈꽃빙수", strengths=LL.voted_keywords(home))
check(usp.startswith("사장님이 강조: 단체석 30석 완비 / 주인공 메뉴: 눈꽃빙수 / ") and "대표 키워드" in usp and usp.endswith("음식이 맛있어요, 단체모임 하기 좋아요, 친절해요"), "USP: 점주 메모 → 선택 메뉴 → 공식 특징 → 손님이 꼽은 강점")
check(len(LL.compose_usp(official_summary="가" * 900)) <= 400 and LL.compose_usp() == "", "USP 길이 제한 / 아무것도 없으면 빈 문자열")
check(LL.review_text_from_home(home) == "가평 설맥 후기: 눈꽃맥주가 시원하고 안주가 푸짐해요" and LL.review_text_from_home(None) == "", "리뷰 글: 제목 중복 제거, 없으면 빈 문자열")
check(LL.banner_text(lead) == "📋 접수 리드 자동 연동됨: 설맥 가평현리점 (주인공 메뉴: 눈꽃빙수)", "상단 배너 문구")

# ---------------------------------------------------------------- 5. 화면: 주소 파라미터로 열면 자동 조회 1회 + 입력칸 전체 채움
from streamlit.testing.v1 import AppTest
os.environ["STORE_APP"] = "1"
for k in ("GEMINI_API_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
    os.environ[k] = ""                                          # 사진 검색·Gemini 등 키가 필요한 외부 호출은 하지 않음
os.environ.pop("STORE_RESEARCH_DISABLE", None)
QS = dict(store="설맥 가평현리점", address="경기 가평군 조종면 조종희망로 16-19 1층", place_id=RIGHT, menu="눈꽃빙수", font="둥근 예능 팝체",
          voice="차분한 남성", bgm="따뜻한", memo="단체석 30석 완비, 한우육회 메인 강조")


def open_with(qs, search_html=SEARCH_HTML, mock_mode=False):
    at = AppTest.from_file(os.path.join(ROOT, "app_store.py"), default_timeout=180)
    at.session_state["mock_mode"] = mock_mode
    for k, v in qs.items():
        at.query_params[k] = v
    with mock.patch("requests.get", fake_get_factory(search_html)):                # 네이버 플레이스는 가짜 응답, 그 밖의 호출은 오프라인 오류
        at.run()
    return at


at = open_with(QS)
ss = at.session_state
check(not at.exception, f"리드 주소로 첫 화면 오류 없음 {[e.value[:100] for e in at.exception]}")
check(ss["owner_name"] == "설맥 가평현리점" and ss["owner_address"] == QS["address"] and ss["store_kind"] == "shop", "상호명·주소·매장 홍보 모드")
check(ss["owner_type"] == "shop" or ss["owner_type"] == "food", f"업종 자동 매핑 ({ss['owner_type']})")
check(ss["owner_usp"].startswith("사장님이 강조: 단체석 30석 완비, 한우육회 메인 강조 / 주인공 메뉴: 눈꽃빙수 / ") and "대표 키워드: 눈꽃맥주" in ss["owner_usp"] and "음식이 맛있어요" in ss["owner_usp"],
      "✨ 우리 가게 자랑: 점주 메모 + 선택 메뉴 + 공식 특징 + 손님이 꼽은 강점")
check("눈꽃빙수 · 14,900원" in ss["official_menu_text"] and "엉뚱한 메뉴" not in ss["official_menu_text"] and ss["official_menu_meta"]["place_id"] == RIGHT, "📋 공식 메뉴판: 해당 place_id 것만")
check("눈꽃맥주가 시원하고" in ss["owner_review_text"], "📝 손님 리뷰 글: 이 매장 연결 후기")
check(ss["caption_font_choice"] == "pop" and ss["selected_actor"] == "male_30s" and ss["audio_choice"] == "voice_and_sub" and ss["bgm_on"] is True and ss["bgm_choice"] == "__mood__:어쿠스틱",
      f"폰트·성우·BGM 초기값 자동 선택 ({ss['caption_font_choice']}, {ss['selected_actor']}, {ss['bgm_choice']})")
check([i.value for i in at.info if "접수 리드 자동 연동됨: 설맥 가평현리점 (주인공 메뉴: 눈꽃빙수)" in i.value], "상단 배너 표시")
check(any("공식 메뉴 2개" in x.value and RIGHT in x.value for x in at.success), "조회 결과 안내(공식 메뉴 개수·place_id)")
check(ss["store_photo_candidates"] and ss["store_photo_candidates"][0]["source"] == "official" and ss["store_photo_candidates"][0]["link"].endswith("/a.jpg"), "사진 후보 맨 앞에 사장님 공식 사진")
check(any((getattr(b_, "label", "") or "").startswith("📍 네이버 플레이스 사진 바로 열기") for b_ in at.get("link_button")) and
      any(getattr(b_, "proto", None) and b_.proto.url == f"https://m.place.naver.com/place/{RIGHT}/photo" for b_ in at.get("link_button")), "📍 플레이스 사진 바로 열기 링크 (place_id 주소)")
exps = [e.label for e in at.expander]
check("⚙️ 세부 설정 (검색어 · 발음 · 리뷰 원문)" in exps, "보조 칸은 ⚙️ 세부 설정 아코디언 1개로")
in_exp = {w.key for e in at.expander if "세부 설정" in e.label for w in list(e.text_input) + list(e.text_area) + list(e.button)}
check({"owner_search_kw", "owner_reading", "owner_menu_text", "owner_review_text", "store_points_btn"} <= in_exp, f"검색어·발음·붙여넣기·사진 특징 정리가 아코디언 안 {sorted(k for k in in_exp if k)}")
top_inputs = {w.key for w in list(at.text_input) + list(at.text_area)} - in_exp
check({"owner_name", "owner_address", "owner_usp", "official_menu_text", "place_query"} <= top_inputs and not ({"owner_search_kw", "owner_reading"} & top_inputs), "기본 화면: 상호·주소·USP·공식 메뉴판(+가게 정보 가져오기)만")
check(not any("대본의 고유함은" in (c.value or "") for c in at.caption) and not any("스마트플레이스의 영수증 리뷰" in (c.value or "") for c in at.caption), "장문 안내 문구 제거")
check(not [w for w in list(at.text_input) + list(at.text_area) if w.key in ("owner_name", "owner_address", "owner_usp", "official_menu_text", "owner_search_kw", "owner_reading") and w.help], "입력칸 ? 도움말 아이콘 제거")

# 같은 리드로 다시 실행해도 재조회 없음 (사용자가 고친 값 보존)
at.session_state["owner_usp"] = "사장님이 고친 자랑"
at.run()
check(at.session_state["owner_usp"] == "사장님이 고친 자랑", "rerun해도 자동 조회를 반복하지 않아 사용자가 고친 값 유지")

# 같은 이름의 다른 지점만 검색되면: 엉뚱한 정보로 채우지 않고 경고
QS2 = {**QS, "place_id": ""}
at2 = open_with(QS2, ONLY_WRONG_SEARCH)
s2 = at2.session_state
check(not at2.exception and "엉뚱한" not in (s2.get("official_menu_text") or "") and "강남" not in (s2.get("owner_address") or "") and s2.get("lead_place_id") == "", "place_id 없고 주소가 맞는 지점도 없음 → 다른 지점 정보 안 섞임")
check(any("주소가 일치하는 지점을 찾지 못해" in w.value for w in at2.warning) and s2["owner_usp"].startswith("사장님이 강조:") and "대표 키워드" not in s2["owner_usp"], "경고 안내 + 점주 메모·선택 메뉴만 자랑 칸에")

# place_id 없이 상호+주소로 찾는 경우 (주소가 맞는 지점 선택)
at3 = open_with(QS2, SEARCH_HTML)
check(at3.session_state["lead_place_id"] == RIGHT and "눈꽃빙수 · 14,900원" in at3.session_state["official_menu_text"], "place_id 없이도 상호+주소로 맞는 지점 자동 선택")

# Mock 모드(오프라인): 네트워크 없이 메모·메뉴만, 오류 없음
at4 = open_with(QS, SEARCH_HTML, mock_mode=True)
check(not at4.exception and at4.session_state["owner_usp"] == "사장님이 강조: 단체석 30석 완비, 한우육회 메인 강조 / 주인공 메뉴: 눈꽃빙수"
      and any("Mock/오프라인" in (c.value or "") for c in at4.caption), "Mock 모드: 점주 메모·선택 메뉴만 반영, 네이버 조회 생략")

# 나레이션 없음 + 신나는 BGM
at5 = open_with({"store": "가게", "voice": "나레이션 없음 (BGM만)", "bgm": "신나는", "font": "임팩트 고딕"}, mock_mode=True)
check(at5.session_state["audio_choice"] == "bgm_only" and at5.session_state["bgm_choice"] == "__mood__:업템포" and at5.session_state["caption_font_choice"] == "impact", "나레이션 없음 → BGM only, 신나는 → 업템포")

# 일반 접속(파라미터 없음)은 이전과 똑같이: 배너·자동 조회 없음
at6 = AppTest.from_file(os.path.join(ROOT, "app_store.py"), default_timeout=120).run()
check(not at6.exception and not [i for i in at6.info if "접수 리드" in i.value] and "lead_params" not in at6.session_state, "파라미터 없는 일반 접속: 배너·자동 조회 없음")

# [처음으로]는 리드 연동 표시도 비움
at.button(key="nav_home").click().run()
check("lead_params" not in at.session_state or not at.session_state["lead_params"], "처음으로 → 리드 연동 표시 초기화")

# ---------------------------------------------------------------- 6. 사진 후보 24장
import core.store_photos as SP
check(SP.MAX_CANDIDATES == 12 and "max_items" in SP.search_store_images.__code__.co_varnames, "기본(숙소 등)은 12장 유지, 호출자가 최대 개수를 지정")
calls = []


def fake_fetch(query, cid, secret, filter_, timeout):
    calls.append(query)
    n = len(calls)
    return {"items": [{"title": f"<b>설맥</b> 가평현리점 {n}-{i}", "link": f"https://ldb-phinf.pstatic.net/{n}_{i}.jpg", "thumbnail": f"https://x/{n}_{i}.jpg", "sizewidth": 800, "sizeheight": 800} for i in range(20)], "error": ""}


with mock.patch.object(SP, "_fetch", fake_fetch):
    r12 = SP.search_store_images("설맥 가평현리점", "가평", "id", "secret")
    n12 = len(calls)
    calls.clear()
    r24 = SP.search_store_images("설맥 가평현리점", "가평", "id", "secret", max_items=24)
check(len(r12["items"]) == 12 and len(r24["items"]) == 24 and n12 == 1 and len(calls) >= 2, f"max_items=24 → 24장 (기본 12장, 호출 {len(calls)}회)")

# ---------------------------------------------------------------- 7. 사이트(Node) 서버 로직: 검색 후보·진단·리드 저장·작업실 URL
node = shutil.which("node")
if node:
    r = subprocess.run([node, os.path.join(ROOT, "tests", "site_api_test.mjs")], capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=ROOT)
    for line in r.stdout.splitlines():
        if line.startswith(("PASS ", "FAIL ")):
            check(line.startswith("PASS "), "사이트: " + line[5:])
    check(r.returncode == 0, f"사이트 테스트 종료 코드 {r.returncode} {r.stderr[:200]}")
else:
    print("SKIP 사이트 Node 테스트 (node 없음)")

finish(fails)
