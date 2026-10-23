import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import json

from PIL import Image

import core.llm as llm
import core.naver_place_menu as npm
import core.planners.owner as ow
from core.script_guard import find_unphotographed_menu, menu_keywords, photographed_menus
from core.store_research import research_store

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


# ---------------------------------------------------------------- 1. 네이버 플레이스 메뉴 페이지 파싱 (실제 페이지와 같은 구조의 축약본)
state = {
    "PlaceDetailBase:1": {"__typename": "PlaceDetailBase", "name": "설맥 가평현리점"},
    "PlaceDetail_BaeminMenu:1": {"__typename": "PlaceDetail_BaeminMenu", "order": 0, "name": "골뱅이 무침(기본)", "desc": "을지로 스타일", "price": "18900"},
    "PlaceDetail_BaeminMenu:2": {"__typename": "PlaceDetail_BaeminMenu", "order": 1, "name": "골뱅이 무침(특)", "desc": "", "price": "25000"},
    "PlaceDetail_BaeminMenu:3": {"__typename": "PlaceDetail_BaeminMenu", "order": 0, "name": "눈꽃빙수", "desc": "", "price": "14900"},
    "PlaceDetail_BaeminMenu:4": {"__typename": "PlaceDetail_BaeminMenu", "order": 0, "name": "철판불고기", "desc": "", "price": "10900"},
    "PlaceDetail_BaeminMenuGroup:a": {"__typename": "PlaceDetail_BaeminMenuGroup", "order": 0, "name": "대표메뉴", "menus": [{"__ref": "PlaceDetail_BaeminMenu:3"}]},
    "PlaceDetail_BaeminMenuGroup:b": {"__typename": "PlaceDetail_BaeminMenuGroup", "order": 1, "name": "HOT", "menus": [{"__ref": "PlaceDetail_BaeminMenu:1"}, {"__ref": "PlaceDetail_BaeminMenu:2"}]},
    "PlaceDetail_BaeminMenuGroup:c": {"__typename": "PlaceDetail_BaeminMenuGroup", "order": 2, "name": "MAIN", "menus": [{"__ref": "PlaceDetail_BaeminMenu:4"}]},
}
PAGE = ("<html><script>window.__APOLLO_STATE__ = " + json.dumps(state, ensure_ascii=False) + ";\n</script>"
        '"keywordList":["눈꽃맥주","쌈스테이키","버터먹은먹태","고르곤졸라피자"]}</html>')
pg = npm.parse_menu_page(PAGE)
names = [m["name"] for m in pg["menus"]]
check(names == ["눈꽃빙수", "골뱅이 무침", "철판불고기"] and pg["menus"][0]["rep"] and pg["menus"][0]["price"] == 14900 and pg["place_name"] == "설맥 가평현리점",
      f"메뉴 페이지 파싱: 그룹 순서·옵션 표기((기본)/(특)) 중복 제거·가격·대표 여부 {names}")
check(pg["keywords"] == ["눈꽃맥주", "쌈스테이키", "버터먹은먹태", "고르곤졸라피자"], f"사장님 등록 대표 키워드도 수집: {pg['keywords']}")

SEARCH = '<a href="https://m.place.naver.com/restaurant/1430341417/home">설맥</a> <a href="https://m.place.naver.com/restaurant/999999999/home">다른집</a>'


def fake_get(url, params=None, **kw):
    return SEARCH if "search.naver.com" in url else PAGE


r = npm.collect_official_menu("설맥 가평현리점", "가평", get=fake_get)
check(r["source"] == "naver_place" and r["place_id"] == "1430341417" and r["url"].endswith("/1430341417/menu/list")
      and r["names"] == ["눈꽃빙수", "골뱅이 무침", "철판불고기", "눈꽃맥주", "쌈스테이키", "버터먹은먹태", "고르곤졸라피자"], f"상호 → 플레이스 ID → 메뉴판 이름 전체(메뉴 + 대표 키워드): {r['names']}")
txt = npm.menu_text_lines(r)
check(npm.parse_menu_text(txt) == r["names"] and "14,900원" in txt, "화면 편집칸 텍스트 ↔ 메뉴 이름 목록 왕복 (⭐·가격 제거)")
r_g = npm.collect_official_menu("없는집", "", get=lambda u, params=None, **k: "", gemini_fn=lambda n, rg: ["김치찌개", "된장찌개"])
check(r_g["source"] == "gemini" and r_g["names"] == ["김치찌개", "된장찌개"], "플레이스에서 못 찾으면 Gemini 웹 검색 결과 사용")
r_n = npm.collect_official_menu("없는집", "", get=lambda u, params=None, **k: (_ for _ in ()).throw(OSError("net")), gemini_fn=lambda n, rg: [])
check(r_n["names"] == [] and r_n["source"] == "", "네트워크 실패도 예외 없이 빈 결과")

res = research_store("설맥 가평현리점", region="가평", local_fn=lambda q: [], blog_fn=lambda q: [], gemini_fn=lambda q, rg: {}, menu_fn=lambda q, rg: r)
check(res.get("official_menu", {}).get("names") == r["names"], "매장 정보 수집(research_store)에 공식 메뉴판이 함께 담김")

# ---------------------------------------------------------------- 2. 사진에 없는 메뉴 판별
OFFICIAL = r["names"] + ["콜라", "순살치킨"]
PHOTO = "눈꽃맥주 잔 / 버터먹은먹태 한 접시 / 골뱅이무침 / 반건조 오징어 / 눈꽃빙수"
check(menu_keywords("골뱅이 무침(기본)") == ["골뱅이무침", "골뱅이"] or "골뱅이" in menu_keywords("골뱅이 무침(기본)"), f"메뉴 핵심어: {menu_keywords('골뱅이 무침(기본)')}")
shown = photographed_menus(OFFICIAL, PHOTO)
check(set(shown) == {"눈꽃빙수", "골뱅이 무침", "눈꽃맥주", "버터먹은먹태"}, f"사진에 찍힌 공식 메뉴: {shown}")
check(find_unphotographed_menu("철판불고기도 푸짐하게 나와요!", OFFICIAL, PHOTO) == "철판불고기"
      and find_unphotographed_menu("고르곤졸라피자까지 있어요", OFFICIAL, PHOTO) == "고르곤졸라피자", "사진에 없는 메뉴(철판불고기·피자)를 말하면 걸림")
check(find_unphotographed_menu("바삭한 먹태와 골뱅이무침까지 완벽해요!", OFFICIAL, PHOTO) is None and find_unphotographed_menu("시원한 눈꽃맥주 한 잔!", OFFICIAL, PHOTO) is None,
      "사진에 있는 메뉴는 말해도 됨")
check(find_unphotographed_menu("고르곤졸라피자 어때요", OFFICIAL, "") is None, "사진 설명이 없으면 판단하지 않음 (모르면 막지 않음)")

# ---------------------------------------------------------------- 3. 대본: 사진에 없는 메뉴는 LLM이 써도 교체
NAME = "설맥 가평현리점"
TAGS = ["눈꽃맥주 잔", "골뱅이무침 한 접시", "과일 화채", "버터먹은먹태", "반건조 오징어", "안주 모둠", "매장 홀", "매장 입구 간판"]
PATHS = [f"/m/{i}.jpg" for i in range(8)]
TAGMAP = dict(zip(PATHS, TAGS))
FACTS = {"special_menus": ["철판불고기", "버터먹은먹태"], "review_nouns": [], "location_context": "", "official_menus": OFFICIAL}
_gj, _ready, _rep, _pv = llm.generate_json, llm.script_llm_ready, llm.repair_scene_lines, ow._media_preview_image
seen = {}


def fake_script(contents, system, **kw):
    seen["prompt"] = " ".join(c for c in contents if isinstance(c, str))
    lines = ["저녁 먹고 아쉬울 때, 눈꽃맥주 한 잔!", "철판불고기랑 골뱅이무침이 진짜 맛있어요!", "시원한 과일 화채로 입가심!", "고르곤졸라피자도 있어요!",
             "쫄깃한 반건조 오징어까지!", "안주 모둠이라 푸짐해요!", "홀도 넓어 편하게 한 잔!"]
    return ({"core_concept": "눈꽃맥주와 안주 라인업", "story": "s",
             "scenes": [{"scene_index": i + 1, "role": "signature", "hook_title": "한 잔 더?" if i == 0 else "", "voiceover": t} for i, t in enumerate(lines + ["오늘 밤 바로 오세요!"])]}, "claude")


ow._media_preview_image = lambda p, d=None: Image.new("RGB", (64, 64), (120, 90, 60))
llm.script_llm_ready = lambda k=None: True
llm.repair_scene_lines = lambda d, c, s_, rr, **kw: (d, [])
llm.generate_json = fake_script
try:
    sc = ow.plan_owner_storyboard(NAME, "눈꽃맥주, 철판불고기 / 골뱅이무침", "food", NAME, PATHS, api_key="k", mock=False, region="경기 가평군",
                                  core_concept="눈꽃맥주와 안주 라인업", photo_tags=TAGMAP, keep_order=True, tempo="rap", store_facts=FACTS)
finally:
    llm.generate_json, llm.script_llm_ready, llm.repair_scene_lines, ow._media_preview_image = _gj, _ready, _rep, _pv
blob = " ".join(s["voiceover"] for s in sc)
check("철판불고기" not in blob and "피자" not in blob, f"대본에 사진 없는 메뉴(철판불고기·피자)가 없음: {[s['voiceover'] for s in sc[:5]]}")
check("철판불고기" in seen["prompt"] and "사진에 없는 메뉴" in seen["prompt"] and "버터먹은먹태" in seen["prompt"], "프롬프트에 '사진에 없는 메뉴 언급 금지' 목록과 사진 속 메뉴가 들어감")
check("철판불고기" not in seen["prompt"].split("사진에 없는 메뉴")[0].split("사장님이 직접 적은 특장점(USP):")[-1][:80], "특장점(USP) 글에서도 사진에 없는 메뉴 구절 제거")

# ---------------------------------------------------------------- 4. 컨셉 제안: 사진에 없는 메뉴 컨셉은 교체
_pv2 = ow._media_preview_image
ow._media_preview_image = lambda p, d=None: Image.new("RGB", (64, 64), (120, 90, 60))


def fake_concepts(contents, system, **kw):
    seen["cprompt"] = " ".join(c for c in contents if isinstance(c, str))
    return ({"concepts": [{"id": "a", "angle": "x", "badge": "x", "headline": "철판불고기 한 판", "core_concept": "철판불고기에 시원한 맥주", "description": "d"},
                          {"id": "b", "angle": "y", "badge": "y", "headline": "밥 대신 안주", "core_concept": "퇴근 후 눈꽃맥주와 버터먹은먹태", "description": "1차"},
                          {"id": "c", "angle": "z", "badge": "z", "headline": "라인업", "core_concept": "눈꽃맥주와 안주 라인업", "description": "전체"}]}, "claude")


llm.generate_json = fake_concepts
try:
    cs = ow.suggest_ad_concepts(NAME, "food", PATHS, "눈꽃맥주", api_key="k", mock=False, photo_tags=TAGMAP, official_menus=OFFICIAL)
finally:
    llm.generate_json, ow._media_preview_image = _gj, _pv2
check("철판불고기" not in str(cs) and cs[1]["core_concept"].startswith("퇴근 후") and "철판불고기" in seen["cprompt"], f"컨셉: 사진에 없는 메뉴를 내세운 안은 기본 컨셉으로 교체: {[c['core_concept'] for c in cs]}")

# ---------------------------------------------------------------- 5. 사진 판별 프롬프트: 공식 메뉴 대조 + 배경 사물 금지
import core.llm.router as rt
_os.environ["ANTHROPIC_API_KEY"] = "test-key"
from core.store_facts import analyze_photos
W = tmp("place_menu_")
pics = [fixture("photo_pool.jpg"), fixture("photo_room.jpg")]


def fake_claude(contents, system, temperature, max_tokens):
    seen["vision"] = " ".join(c for c in contents if isinstance(c, str))
    return json.dumps({"photos": [{"index": i + 1, "subject": "버터먹은먹태 한 접시", "bucket": 1, "readiness": "ready_to_eat"} for i in range(2)]}, ensure_ascii=False)


rt._overrides = {"claude": fake_claude}
try:
    tg, _b = analyze_photos(pics, business_name=NAME, api_key="g", thumb_dir=W, cache_path=_os.path.join(W, "t.json"), official_menus=OFFICIAL)
    n1 = len(seen["vision"])
    seen.pop("vision")
    analyze_photos(pics, business_name=NAME, api_key="g", thumb_dir=W, cache_path=_os.path.join(W, "t.json"), official_menus=OFFICIAL)
    cached = "vision" not in seen
    analyze_photos(pics, business_name=NAME, api_key="g", thumb_dir=W, cache_path=_os.path.join(W, "t.json"), official_menus=OFFICIAL[:3])
    redo = "vision" in seen
finally:
    rt._overrides = {}
check("공식 메뉴판 대조" in seen["vision"] and "철판불고기" in seen["vision"] and "배경 사물" in seen["vision"] and "선풍기" in seen["vision"], "사진 판별 프롬프트: 공식 메뉴판과 대조해 메뉴 이름으로 기록, 배경 사물·글씨 기록 금지")
check(cached and redo, "같은 메뉴판이면 캐시 재사용, 메뉴판이 바뀌면 다시 판별")

# ---------------------------------------------------------------- 6. 호프집 안주 오독('튀긴 오징어채')·명사 실종 비문('매콤하게 무친까지')
from core.script_guard import find_broken_phrase
from core.store_facts import canon_snack
check(find_broken_phrase("눈꽃빙수에 매콤하게 무친까지 안주 라인업이 완벽해요!") and find_broken_phrase("튀긴 오징어채와, 구운 오징어와,")
      and not find_broken_phrase("달콤한 눈꽃빙수에 매콤새콤 골뱅이소면까지 안주 라인업이 완벽해요!") and not find_broken_phrase("바삭한 먹태와 쫄깃한 구운 오징어까지 완벽해요!"),
      "비문 가드: 명사가 빠진 '무친까지'·'튀긴 오징어채' 차단, 온전한 문장은 통과")
check(canon_snack("튀긴 오징어채", OFFICIAL) == "버터먹은먹태" and canon_snack("찢어 구운 진미채 접시", []) == "찢어 구운 먹태구이 접시" and canon_snack("구운 통오징어") == "구운 통오징어",
      "비전이 먹태를 오징어채로 읽으면 공식 메뉴(버터먹은먹태)로 교정, 통오징어는 그대로")
rt._overrides = {"claude": lambda contents, system, temperature, max_tokens: (seen.__setitem__("v2", " ".join(c for c in contents if isinstance(c, str))) or
                  json.dumps({"photos": [{"index": 1, "subject": "튀긴 오징어채와 구운 오징어", "bucket": 1, "readiness": "ready_to_eat"}, {"index": 2, "subject": "눈꽃빙수", "bucket": 1, "readiness": "ready_to_eat"}]}, ensure_ascii=False))}
try:
    tg2, _b2 = analyze_photos(pics, business_name=NAME, api_key="g", thumb_dir=W, cache_path=_os.path.join(W, "t2.json"), official_menus=OFFICIAL)
finally:
    rt._overrides = {}
check(all(w in seen["v2"] for w in ("호프집 안주 판별 가이드", "버터먹은먹태", "구운 통오징어", "눈꽃빙수", "골뱅이무침")) and "오징어채" not in " ".join(tg2.values()) and "버터먹은먹태" in tg2[pics[0]],
      f"Vision 프롬프트에 호프집 안주 가이드 + 오독 결과 교정: {list(tg2.values())}")

TAGS2 = ["눈꽃맥주 잔", "눈꽃빙수", "골뱅이소면", "버터먹은먹태", "구운 통오징어", "안주 모둠", "매장 홀", "매장 입구 간판"]
TAGMAP2 = dict(zip(PATHS, TAGS2))


def fake_beat(contents, system, **kw):
    lines = ["퇴근 후 밥 대신 눈꽃맥주 한 잔 어때요?", "안주 모둠이라 여럿이 나눠 먹기 좋아요.", "눈꽃빙수에 매콤하게 무친까지 안주 라인업이 완벽해요!", "홀도 넓어서 퇴근길에 편하게 들르기 좋아요."]
    return ({"core_concept": "눈꽃맥주와 안주 라인업", "story": "s",
             "scenes": [{"scene_index": i + 1, "role": "signature", "hook_title": "한 잔 더?" if i == 0 else "", "voiceover": t} for i, t in enumerate(lines + ["지금 저장하고 검색해 보세요!"])]}, "claude")


ow._media_preview_image = lambda p, d=None: Image.new("RGB", (64, 64), (120, 90, 60))
llm.script_llm_ready = lambda k=None: True
llm.repair_scene_lines = lambda d, c, s_, rr, **kw: (d, [])
llm.generate_json = fake_beat
try:
    scb = ow.plan_owner_storyboard(NAME, "눈꽃맥주, 버터먹은먹태", "food", NAME, PATHS, api_key="k", mock=False, region="경기 가평군", core_concept="눈꽃맥주와 안주 라인업",
                                   photo_tags=TAGMAP2, keep_order=True, tempo="beat", store_facts={"special_menus": [], "review_nouns": [], "location_context": "", "official_menus": OFFICIAL})
finally:
    llm.generate_json, llm.script_llm_ready, llm.repair_scene_lines, ow._media_preview_image = _gj, _ready, _rep, _pv
snack = scb[2]["voiceover"]
pos = [snack.find(w) for w in ("눈꽃빙수", "골뱅이소면", "버터먹은먹태", "오징어")]
check(len(scb) == 5 and all(p >= 0 for p in pos) and pos == sorted(pos) and not find_broken_phrase(snack) and "무친까지" not in snack and snack.count("오징어") == 1 and snack.endswith("완벽해요!"),
      f"설맥 8장 비트형 안주 문장: 비문을 걸러 4컷 안주를 순서대로, 중복 없이 완전한 문장으로 호명: {snack}")

# ---------------------------------------------------------------- 7. 화면에 보여 줄 '실제로 응답한 엔진' 기록
rt._overrides = {"claude": lambda c, sy, t, m: json.dumps({"ok": 1})}
try:
    rt.generate_json(["x"], "s", purpose="사진 피사체 태깅")
    rt.generate_json(["x"], "s", purpose="사장님 광고 대본")
    rt.generate_json(["x"], "s", purpose="대본 규칙 보정")
finally:
    rt._overrides = {}
check(rt.LAST_ENGINE["사진 피사체 태깅"]["engine"] == "claude" and rt.LAST_ENGINE["_script"] == rt.LAST_ENGINE["사장님 광고 대본"]
      and rt.LAST_ENGINE["_script"]["model"] == rt.claude_model_for("script")[0], f"엔진 기록: 사진 판독·대본이 실제 응답한 엔진·모델로 남음 {rt.LAST_ENGINE['_script']}")

# ---------------------------------------------------------------- 8. 안전 문구: 여러 메뉴 모음·곁들임 사진 설명에서도 온전한 말
fb = [ow.photo_fallback_line(t, 5, short=True) for t in ("여러 메뉴 모음: 양념 요리, 버터먹은먹태, 치즈 요리, 눈꽃빙수", "눈꽃맥주 한 잔과 생맥주잔, 떡 바구니", "구운 통오징어 한 접시와 땅콩")]
check(fb[0] == "버터먹은먹태, 치즈 요리까지 한 상에!" and fb[1].startswith("눈꽃맥주,") and fb[2].startswith("통오징어,") and not any(w in " ".join(fb) for w in ("요리 눈꽃빙수", "잔과", "접시와", "땅콩", "바구니")),
      f"사진 설명이 모음·곁들임이어도 메뉴 이름만 호명: {fb}")

finish(fails)
