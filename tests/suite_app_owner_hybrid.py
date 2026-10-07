"""사장님 모드 하이브리드: 영상 소스 관리(추가·삭제·순서·영상), 네이버 지역 검색 API(키 없으면 수동), 사진 기반 매장 특징 정리, 기획 연동"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import json
import logging
import os
import shutil

logging.disable(logging.WARNING)
from streamlit.testing.v1 import AppTest

from core.media.library import MAX_MEDIA, add_media, move_media, remove_media
import core.naver_local as nl
import core.planners.owner as ow

fails, jobs = 0, []


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


# ---------------------------------------------------------------- 1. 목록 연산
check(add_media(["a", "b"], ["b", "c"]) == ["a", "b", "c"] and len(add_media([], [str(i) for i in range(20)])) == MAX_MEDIA,
      "추가: 뒤에 붙이고 중복 제외, 최대 개수 제한")
check(move_media(["a", "b", "c"], 2, -1) == ["a", "c", "b"] and move_media(["a", "b"], 0, -1) == ["a", "b"]
      and remove_media(["a", "b", "c"], 1) == ["a", "c"] and remove_media(["a"], 5) == ["a"], "순서 변경·삭제 (범위 밖은 그대로)")

# ---------------------------------------------------------------- 2. 네이버 지역 검색 (공식 API, 가짜 응답)
check(nl.business_type_for("음식점>한식>육류,고기요리") == "food" and nl.business_type_for("숙박>펜션") == "stay"
      and nl.business_type_for("카페,디저트") == "food" and nl.business_type_for("생활,편의>꽃집") == "shop", "네이버 카테고리 → 업종")
check(nl.place_id_from_link("https://map.naver.com/p/entry/place/1234567890?c=15") == "1234567890"
      and nl.place_id_from_link("https://m.place.naver.com/restaurant/37428191/home") == "37428191"
      and nl.is_naver_link("https://naver.me/AbCd") and not nl.is_naver_link("성수 불판"), "플레이스 링크 → 플레이스 번호 (페이지는 열지 않음)")
check(nl.region_of("경기 가평군 청평면 호반로 123") == "경기 가평군", "주소 → 엔딩 카드용 지역")
import core.scraper as _sc
_orig_resolve = _sc.resolve_link
_sc.resolve_link = lambda u, **k: {"url": "https://map.naver.com/p/entry/place/987654321", "search_query": "", "is_search": False}
check(nl.place_id_from_link("https://naver.me/xYz") == "987654321", "naver.me 단축 링크 → 리다이렉트 주소만 따라가 플레이스 번호")
_sc.resolve_link = _orig_resolve
os.environ["NAVER_CLIENT_ID"], os.environ["NAVER_CLIENT_SECRET"] = "", ""
check(not nl.NaverLocalSearch().enabled, "키 없으면 비활성 (수동 입력)")
_req = []


class R:
    status_code = 200

    def json(self):
        return {"items": [{"title": "<b>성수</b> 불판 성수점", "category": "음식점>한식>육류,고기요리",
                           "address": "서울특별시 성동구 성수동2가 1-1", "roadAddress": "서울특별시 성동구 성수이로 1",
                           "telephone": "", "link": "https://example.com"}]}


nl.requests.get = lambda url, params=None, timeout=None, headers=None: (_req.append((url, params, headers)), R())[1]
items = nl.NaverLocalSearch("ID", "SECRET").search("성수 불판")
check(items[0]["name"] == "성수 불판 성수점" and items[0]["business_type"] == "food" and items[0]["road_address"].endswith("성수이로 1")
      and _req[0][2]["X-Naver-Client-Id"] == "ID" and "openapi.naver.com" in _req[0][0], "지역 검색: 태그 제거·업종·도로명 주소, 공식 API 헤더")

# ---------------------------------------------------------------- 3. 사진 기반 매장 특징 정리 (가짜 Gemini)
import google.genai as gg
_orig_client = gg.Client
reply = {"points": ["통창 너머 계곡 뷰", "단체석 30명 수용", "오늘만 20% 할인", "아늑한 원목 인테리어", "숙성 삼겹살"]}


class M:
    def generate_content(self, **kw):
        return type("X", (), {"text": json.dumps(reply, ensure_ascii=False)})()


gg.Client = lambda api_key=None: type("C", (), {"models": M()})()
pts = ow.suggest_store_points("성수 불판", "food", [fixture("photo_room.jpg")], existing_usp="숙성 삼겹살", api_key="fake")
gg.Client = _orig_client
check(pts == ["통창 너머 계곡 뷰", "아늑한 원목 인테리어"], f"매장 특징: 근거 없는 인원수·할인 제외, 이미 적은 자랑과 중복 제외 {pts}")
check(ow.suggest_store_points("x", "food", [], api_key="fake") == [] and ow.suggest_store_points("x", "food", ["a"], mock=True, api_key="k") == [],
      "사진 없음·mock → 빈값 (지어내지 않음)")

# ---------------------------------------------------------------- 4. 화면 흐름 (AppTest, mock)
os.environ["YOUTUBE_API_KEY"] = ""
os.environ["COUPANG_ACCESS_KEY"] = ""
try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=300).run()
    jobs.append(at.session_state.job.workdir)
    check(not at.exception and at.session_state.production_mode == "owner"
          and any("가게 정보 가져오기" in (m.value or "") for m in at.markdown), "사장님 탭 최상단: 링크·상호명 가게 정보 가져오기")
    at.text_input(key="place_query").input("성수 불판").run()
    at.button(key="place_fetch").click().run()
    check(at.text_input(key="owner_name").value == "성수 불판" and any("NAVER_CLIENT_ID" in (i.value or "") for i in at.info),
          "API 키 없음: 상호명은 입력값으로 채우고 키 등록 안내")
    at.text_input(key="place_query").input("https://map.naver.com/p/entry/place/987654321?c=15").run()
    at.button(key="place_fetch").click().run()
    check(at.session_state.place_id == "987654321" and at.text_input(key="owner_name").value == "성수 불판",
          "플레이스 링크: 번호만 기록, 이미 입력한 상호명은 유지")

    # API 키가 있으면: 상호명 검색 → 고르면 상호명·업종·주소 자동 채움 (가짜 응답)
    os.environ["NAVER_CLIENT_ID"], os.environ["NAVER_CLIENT_SECRET"] = "ID", "SECRET"
    at.text_input(key="place_query").input("성수 불판").run()
    at.button(key="place_fetch").click().run()
    at.button(key="place_pick_0").click().run()
    os.environ["NAVER_CLIENT_ID"], os.environ["NAVER_CLIENT_SECRET"] = "", ""
    check(at.text_input(key="owner_name").value == "성수 불판 성수점" and at.selectbox(key="owner_type").value == "food"
          and at.text_input(key="owner_address").value == "서울특별시 성동구 성수이로 1",
          "API 키 있음: 검색 결과 고르면 상호명·업종·주소 자동 채움")

    at.sidebar.checkbox[0].check().run()                                          # Mock
    media = [fixture("photo_pool.jpg"), fixture("photo_room.jpg"), fixture("phone_portrait.mov")]
    at.session_state.uploaded_paths = list(media)                                 # '추가'로 올라온 상태
    at.run()
    check(any("영상 소스 3개" in (m.value or "") for m in at.markdown) and any("🎬 영상" in (c.value or "") for c in at.caption),
          "영상 소스 목록: 3개 (MP4/MOV 영상은 대표 프레임 + 길이)")
    tag = lambda p: __import__("hashlib").md5(p.encode()).hexdigest()[:8]
    at.button(key=f"media_down_{tag(media[0])}").click().run()
    check(at.session_state.uploaded_paths == [media[1], media[0], media[2]], "⬇ 순서 변경")
    at.button(key=f"media_del_{tag(media[1])}").click().run()
    check(at.session_state.uploaded_paths == [media[0], media[2]], "🗑 삭제 (목록에서만 빠짐)")
    at.session_state.uploaded_paths = at.session_state.uploaded_paths + [media[1]]
    at.run()

    at.text_input(key="owner_usp").input("숙성 삼겹살").run()
    at.button(key="store_points_btn").click().run()
    check(at.text_input(key="owner_usp").value == "숙성 삼겹살" and any("Mock" in (c.value or "") for c in at.caption),
          "매장 특징 정리 버튼: mock에선 지어내지 않고 안내만 (자랑 칸 보존)")

    [b for b in at.button if "스토리보드 자동 기획" in b.label][0].click().run()
    sb = at.session_state.storyboard
    jobs.append(at.session_state.job.workdir)
    srcs = [(s.get("media") or {}).get("path") or s.get("image_path") for s in sb]
    check(not at.exception and len(sb) == 3 and srcs == [media[0], media[2], media[1]] and sb[1].get("media", {}).get("type") == "video",
          f"기획: 최종 목록 순서대로 씬 배정 (사진·영상 혼합) {len(sb)}씬")
    at.button(key=f"media_up_{tag(media[1])}").click().run()
    srcs2 = [(s.get("media") or {}).get("path") or s.get("image_path") for s in at.session_state.storyboard]
    check(srcs2 == [media[0], media[1], media[2]] and any("다시 눌러" in (i.value or "") for i in at.info),
          "기획 후 순서 변경 → 씬 사진 즉시 재배정 + 다시 기획 안내")
finally:
    for j in set(jobs):
        shutil.rmtree(j, ignore_errors=True)
finish(fails)
