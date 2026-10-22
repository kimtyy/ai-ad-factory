import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import datetime as dt
import io
import os

import numpy as np
import requests
from PIL import Image

import core.stay_curation as sc
import core.stay_match as sm
import core.stay_photos as sp
from core.mode_policy import get_mode_policy
from core.planners import stay as sty

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


class R:
    def __init__(self, status=200, payload=None, content=b"", ctype="application/json", text=""):
        self.status_code, self._p, self.content, self.text = status, payload, content, text
        self.headers = {"Content-Type": ctype}

    def json(self):
        return self._p


def jpg(w=900, h=700, color=(30, 120, 200)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


_get, _post = requests.get, requests.post
TODAY = dt.date(2026, 10, 21)

# ---------------------------------------------------------------- 서버 흉내: 데이터랩(POST)·지역·블로그·이미지·여기어때
LOCAL = {
    "가평 풀빌라": [("가평 리버뷰 풀빌라", "숙박>펜션", "경기도 가평군 청평면 1"), ("가평 별빛 펜션", "숙박>펜션", "경기도 가평군 설악면 2"), ("가평 맛있는 식당", "음식점>한식", "경기도 가평군 3")],
    "강릉 오션뷰 숙소": [("강릉 파도 오션스테이", "숙박>호텔", "강원도 강릉시 4"), ("강릉 바다뷰 펜션", "숙박>펜션", "강원도 강릉시 5")],
}
TREND = {"가평 풀빌라": [10] * 23 + [30] * 7, "강릉 오션뷰 숙소": [50] * 23 + [52] * 7, "경주 한옥 스테이": [5] * 30, "양평 글램핑": [8] * 30, "제주 풀빌라": [20] * 30}
BLOG_TOTAL = {"가평 리버뷰 풀빌라": 5200, "가평 별빛 펜션": 800, "강릉 파도 오션스테이": 9000, "강릉 바다뷰 펜션": 300}
DESC = "{n} 후기: 1박 180,000원부터, 기준 4인 최대 6인. 개별 온수풀 노천 자쿠지 리버뷰 불멍 바비큐 조식 무료까지 좋았어요"


def hub_post(url, json=None, headers=None, timeout=None):
    if url.endswith("/search-trend/v1/search"):
        return R(200, {"results": [{"title": g["groupName"], "keywords": g["keywords"],
                                    "data": [{"period": f"2026-09-{i + 1:02d}", "ratio": v} for i, v in enumerate(TREND[g["groupName"]])]}
                                   for g in json["keywordGroups"]]})
    return R(404, {})


def hub_get(url, params=None, headers=None, timeout=None):
    q = (params or {}).get("query", "")
    if url.endswith("/search/v1/local"):
        base = next((v for k, v in LOCAL.items() if q.startswith(k)), [])
        return R(200, {"items": [{"title": f"<b>{n}</b>", "category": c, "address": a, "roadAddress": a, "link": ""} for n, c, a in base]})
    if url.endswith("/search/v1/blog"):
        name = q.split(" ")[0] if q.split(" ")[0] in BLOG_TOTAL else q
        name = next((n for n in BLOG_TOTAL if q.startswith(n)), q)
        return R(200, {"total": BLOG_TOTAL.get(name, 0), "items": [{"title": name, "description": DESC.format(n=name), "postdate": "20261015" if "가평" in name else "20260801"}] * 3 if name in BLOG_TOTAL else []})
    if url.endswith("/search/v1/image"):
        return R(200, {"items": [{"title": f"<b>가평 리버뷰 풀빌라</b> 사진 {i}", "link": f"https://img.example.com/{i}.jpg", "thumbnail": f"https://thumb.example.com/{i}.jpg",
                                  "sizewidth": "900", "sizeheight": "700"} for i in range(12)]})
    if "yeogi.com" in url:
        return R(403, None, b"", "text/html", "blocked")
    if "example.com" in url:
        n = int(url.rsplit("/", 1)[-1].split(".")[0])
        return R(200, None, jpg(800 + n, 600, (n * 20 % 255, 100, 60)), "image/jpeg")
    return R(404, {})


requests.get, requests.post = hub_get, hub_post
try:
    # ------------------------------------------------------------ 1. TOP 5 큐레이션
    tr = sc.fetch_trends(list(TREND), "ID", "SECRET", today=TODAY)
    check(tr["ok"] and tr["stats"]["가평 풀빌라"]["surge"] == 3.0 and tr["stats"]["강릉 오션뷰 숙소"]["steady"] > 40,
          f"데이터랩: 최근 7일÷앞 23일 급상승 배수(가평 {tr['stats']['가평 풀빌라']['surge']}배)와 30일 평균을 계산")
    kws = list(TREND)
    check(sc.rank_keywords(kws, tr["stats"], "season")[0] == "가평 풀빌라" and sc.rank_keywords(kws, tr["stats"], "steady")[0] == "강릉 오션뷰 숙소",
          "시즌 급상승 1위(가평)와 스테디 1위(강릉)가 다르게 뽑힘")
    top = sc.curate_top5("전체(전국 핫플)", "season", "ID", "SECRET", today=TODAY)
    names = [i["name"] for i in top["items"]]
    check(0 < len(names) <= 5 and "가평 맛있는 식당" not in names and top["items"][0]["name"] == "가평 리버뷰 풀빌라" and top["picked"][0] == "가평 풀빌라",
          f"지역·블로그 검색으로 실제 숙박업소만 TOP: {names} (음식점 제외, 시즌 키워드 '가평 풀빌라' 우선)")
    top_st = sc.curate_top5("전체(전국 핫플)", "steady", "ID", "SECRET", today=TODAY)
    check(top_st["items"][0]["name"] == "강릉 파도 오션스테이" and top_st["items"][0]["blog_total"] == 9000, "스테디 기준은 블로그 후기 총량 순 (강릉 9,000건 1위)")
    it0 = top["items"][0]
    check("개별 온수풀" in it0["highlight"] or "자쿠지" in it0["highlight"] or "통창" in it0["highlight"], f"매력 한 줄은 후기 요약에 실제 나온 시설만: '{it0['highlight']}'")
    check(sc.curate_top5("전체(전국 핫플)", "season", "", "")["items"] == [] and sc.curate_top5("전체(전국 핫플)", "season", "", "")["error"], "API 키가 없으면 가짜 목록 없이 안내만")
    requests.post = lambda *a, **k: R(401, {})
    top2 = sc.curate_top5("전체(전국 핫플)", "season", "ID", "SECRET", today=TODAY)
    check(len(top2["items"]) > 0 and any("검색어트렌드" in n for n in top2["notes"]), "데이터랩이 꺼져 있으면(401) 켜는 방법을 안내하고 테마 기본 키워드로 계속")
    requests.post = hub_post

    # ------------------------------------------------------------ 2. 가격·인원·시설·1인 체감가·링크
    check(sm.parse_night_price("주말 1박 최저 189,000원부터, 바비큐 세트 35,000원") == 189000 and sm.parse_night_price("평일 1박 12만 5천원") == 125000
          and sm.parse_night_price("바비큐 3만원") is None, "1박 최저 요금: 숙박 문맥의 4만~300만 원만 (바비큐 세트 같은 소액 제외)")
    check(sm.parse_capacity("기준 2인 최대 6인") == {"base": 2, "max": 6} and sm.parse_capacity("4~6인 독채")["base"] == 4 and sm.parse_capacity("넓어요") == {"base": None, "max": None},
          "기준·최대 인원 파싱, 없으면 None")
    check(sm.per_person_text(180000, 4) == "4만 원대" and sm.per_person_text(250000, 4) == "6만 원대" and sm.per_person_text(None, 4) == "", "1인 체감가 = 1박 요금 ÷ 인원 → 'N만 원대', 요금 없으면 빈 값")
    fac = sm.extract_facilities("개별 온수풀과 노천 자쿠지, 통창 리버뷰, 불멍·무한 바비큐, 조식 무료")
    check(fac[:2] == ["개별 온수풀", "노천 자쿠지"] and "불멍" in fac and "바비큐" in fac and "조식 무료" in fac, f"핵심 시설 키워드 추출: {fac}")
    f1 = sm.build_stay_facts({"yeogi": "1박 평일 160,000원부터 기준 4인", "blog": "1박 90,000원 기준 2인 인피니티풀"}, {"region": "경기도 가평군", "category": "숙박>펜션"})
    check(f1["nightly_price"] == 160000 and f1["price_source"] == "yeogi" and f1["per_person"] == "4만 원대" and "인피니티풀" in f1["facilities"] and f1["alias"].startswith("가평"),
          f"여기어때 값 우선, 시설은 출처 합집합: 1인 {f1['per_person']}, 별칭 '{f1['alias']}'")
    f2 = sm.build_stay_facts({"blog": "1박 200,000원부터 개별 온수풀 불멍"}, {"region": "가평군", "category": "숙박>풀빌라"})
    check(f2["price_source"] == "blog" and f2["people_estimated"] and f2["people_used"] == 4 and f2["per_person"] == "5만 원대", "여기어때가 막히면 블로그 요약에서 요금, 인원을 모르면 4인(추정)")
    check(sm.build_stay_facts({"blog": "경치 좋아요"}, {})["per_person"] == "" and sm.build_stay_facts({}, {})["nightly_price"] is None, "요금을 못 찾으면 지어내지 않고 비움")
    check([sm.place_token(x) for x in ("경상북도 경주시", "충청남도 태안군", "전북특별자치도 전주시", "서울 마포구", "가평군")] == ["경주", "태안", "전주", "마포", "가평"], "지역 표기: 도 이름(경상북도 등)은 건너뛰고 시·군만 ('경상북 숙소' 방지)")
    ye = sm.find_yeogi("가평 리버뷰 풀빌라")
    check(ye["blocked"] and ye["url"] == "" and "403" in ye["error"] and "keyword=" in ye["search_url"], "여기어때 403이면 멈추지 않고 차단 안내 + 검색 주소 폴백")
    page = '<a href="/domestic-accommodations/777?x=1">다른 숙소</a><a href="/domestic-accommodations/9854"><span>가평 리버뷰 풀빌라</span></a>'
    requests.get = lambda url, **k: R(200, None, b"", "text/html", page) if "yeogi.com" in url else hub_get(url, **k)
    ye2 = sm.find_yeogi("가평 리버뷰 풀빌라")
    requests.get = hub_get
    check(ye2["url"].endswith("/domestic-accommodations/9854") and not ye2["blocked"], "검색 결과에서 이름이 확인된 숙소만 상세 URL로 매칭 (엉뚱한 첫 결과 금지)")
    L = sm.build_affiliate_link("https://partner.example/go?pid={partner_id}&to={url}", "P1", "https://www.yeogi.com/domestic-accommodations/9854")
    check(L["tracked"] and "pid=P1" in L["link"] and "to=https%3A%2F%2Fwww.yeogi.com" in L["link"], "제휴 템플릿에 파트너 ID·예약 주소 치환")
    L0 = sm.build_affiliate_link("", "P1", "https://www.yeogi.com/domestic-accommodations/9854")
    check(not L0["tracked"] and "수익이 추적되지" in L0["note"] and L0["link"].startswith("https://www.yeogi.com"), "템플릿이 없으면 일반 링크 + 수익 미추적 경고 (파라미터를 지어내지 않음)")
    pk = sm.comment_package("가평 리버뷰 독채풀빌라", f1, L["link"], sm.stay_hashtags("경기도 가평군", "가평 리버뷰 독채풀빌라", f1))
    check("1인 4만 원대" in pk and "인피니티풀" in pk and L["link"] in pk and "수수료" in pk and "#여기어때" in pk and "#가평숙소" in pk, "고정 댓글 패키지: 특징 요약 + 제휴 링크 + 고지 + 해시태그")
finally:
    requests.get, requests.post = _get, _post

# ---------------------------------------------------------------- 3. 사진: 3-2-2 정렬·블랙리스트 제외
P = [f"/x/p{i}.jpg" for i in range(12)]
bk = {P[0]: 2, P[1]: 1, P[2]: 3, P[3]: 1, P[4]: 0, P[5]: 1, P[6]: 2, P[7]: 3, P[8]: 1, P[9]: 0, P[10]: 3, P[11]: 2}
o = sp.order_stay_photos(P, bk)
bks = [bk[p] for p in o["ordered"]]
check(bks == [1, 1, 1, 2, 2, 3, 3] and set(o["excluded"]) == {P[4], P[9]} and len(o["unused"]) == 3, f"3-2-2 정렬: 물3 → 뷰2 → 바비큐·야경2 {bks}, 화장실·복도 컷 제외 2장")
o2 = sp.order_stay_photos(P[:5], {P[0]: 1, P[1]: 2, P[2]: 2, P[3]: 3, P[4]: 0})
check([{P[0]: 1, P[1]: 2, P[2]: 2, P[3]: 3}[p] for p in o2["ordered"]] == [1, 2, 2, 3] or len(o2["ordered"]) == 4, f"버킷이 모자라면 이웃 버킷으로 채우거나 있는 만큼만 ({len(o2['ordered'])}장)")
check(sp.order_stay_photos(P[:9], {})["ordered"] == P[:7], "비전 분류가 없으면 올린 순서 그대로 앞 7장")

import core.llm as llm
_gj = llm.generate_json
import core.planners.owner as ow
_pv = ow._media_preview_image
W = tmp("stayphotos_")
real = []
for i in range(3):
    pth = os.path.join(W, f"s{i}.jpg")
    Image.new("RGB", (800, 600), (i * 50, 90, 140)).save(pth)
    real.append(pth)
ow._media_preview_image = lambda p, d=None: Image.open(p)
llm.generate_json = lambda *a, **k: ({"photos": [{"index": 1, "subject": "야외 온수풀", "bucket": 1}, {"index": 2, "subject": "변기 클로즈업", "bucket": 0},
                                                 {"index": 3, "subject": "거실 통창 강뷰", "bucket": 2}]}, "mock")
try:
    tags, buckets = sp.classify_stay_photos(real, stay_name="가평 리버뷰 풀빌라", api_key="k", thumb_dir=W, cache_path=os.path.join(W, "c.json"))
    calls = []
    llm.generate_json = lambda *a, **k: calls.append(1) or ({}, "x")
    tags2, buckets2 = sp.classify_stay_photos(real, stay_name="x", api_key="k", thumb_dir=W, cache_path=os.path.join(W, "c.json"))
finally:
    llm.generate_json, ow._media_preview_image = _gj, _pv
check([buckets[p] for p in real] == [1, 0, 2] and tags[real[0]] == "야외 온수풀" and buckets2 == buckets and not calls,
      "비전 분류: 변기 클로즈업은 버킷 0(제외), 결과는 캐시돼 같은 사진은 다시 부르지 않음")

# ---------------------------------------------------------------- 4. 대본: 1컨셉 5문장·18초·사진 묶음·환각 차단
facts = {"per_person": "4만 원대", "facilities": ["개별 온수풀", "리버뷰", "불멍", "바비큐"], "signature": "개별 온수풀", "people_used": 4}
sc_ = sty.plan_stay_storyboard("효정 리버뷰 풀빌라", "가평 리버뷰 독채풀빌라", "경기도 가평군", facts, [f"/m/{i}.jpg" for i in range(7)], mock=True)
vo = [s["voiceover"] for s in sc_]
check(len(sc_) == 5 and sum(s["duration_sec"] for s in sc_) == 18.0 and [s["media_group"] for s in sc_] == [[0], [1, 2], [3, 4], [5], [6]],
      f"5문장·18초(3+3.5+4.5+4+3), 사진 7장은 [1,2,2,1,1]로 묶음 (물3→뷰2→바비큐2)")
check("1인 4만 원대" in vo[0] and "가평" in vo[0] and "효정" not in " ".join(vo) and vo[4] == sty.STAY_CTA, f"1문장 숫자 후킹(본명 없음), 5문장 CTA 고정: {vo[0]}")
check("개별 온수풀" in vo[2] and "리버뷰" in vo[2] and "불멍" in vo[3] and "바비큐" in vo[3], f"3문장 공간 도파민·4문장 혜택은 확인된 시설만: {vo[2]} / {vo[3]}")
check(not any(w in " ".join(vo) for w in ("침실", "주방", "화장실", "냉장고")) and all(len(v) <= sty.MAX_LEN for v in vo), "방 나열(부동산식) 없음, 문장 46자 이내")
nofact = sty.plan_stay_storyboard("효정 풀빌라", "가평 독채풀빌라", "경기도 가평군", {"per_person": "", "facilities": [], "signature": "", "people_used": 4}, [f"/m/{i}.jpg" for i in range(7)], mock=True)
nv = " ".join(s["voiceover"] for s in nofact)
check(not any(c.isdigit() for c in nv.replace("5", "")) or "만 원" not in nv, "요금·시설을 못 찾으면 가격 숫자·시설 단어를 쓰지 않음")
check(sty.validate_line("테라스 노천 자쿠지에 몸을 담그면 여행의 피로가 싹 풀려요.", facts, "노천 자쿠지 테라스", "").startswith("어색한 문구")
      and sty.validate_line("불멍 앞에서 바비큐까지 즐기면 밤이 훨씬 길게 느껴져요.", facts, "불멍 바비큐", "").startswith("어색한 문구"), "숙소 대본: 근거 없는 효과·체감 문구는 검증 탈락 → 팩트 템플릿 문장")
check(sty.validate_line("1인 3만 원대에 자쿠지까지 즐겨요", facts, "개별 온수풀 리버뷰", "") != "" and sty.validate_line("효정 리버뷰 풀빌라 가보세요", facts, "개별 온수풀", "효정 리버뷰 풀빌라") == "본명 노출"
      and sty.validate_line("1인 4만 원대에 개별 온수풀까지 쓰는 곳이에요", facts, "개별 온수풀 리버뷰", "") == "", "검증: 틀린 1인 가격·근거 없는 시설·본명 노출은 탈락, 맞는 문장은 통과")
llm.generate_json = lambda *a, **k: ({"scenes": [{"hook_title": "1인 4만 원대 풀빌라", "voiceover": "1인 4만 원대에 개별 온수풀까지 다 쓰는 가평 독채 실화일까요?"},
                                                 {"voiceover": "주말에 풀빌라 가려다 1박 가격에 놀라서 포기하셨죠!"}, {"voiceover": "문 열자마자 수영장과 사우나, 헬스장까지 즐겨요."},
                                                 {"voiceover": "저녁엔 불멍과 바비큐까지 즐길 수 있어요."}, {"voiceover": "아무 말"}]}, "claude")
import core.llm as _l2
_sr = _l2.script_llm_ready
_l2.script_llm_ready = lambda k=None: True
try:
    scl = sty.plan_stay_storyboard("효정 리버뷰 풀빌라", "가평 리버뷰 독채풀빌라", "경기도 가평군", facts, [f"/m/{i}.jpg" for i in range(7)], api_key="k", mock=False)
finally:
    _l2.script_llm_ready, llm.generate_json = _sr, _gj
check(scl[0]["voiceover"].startswith("1인 4만 원대에 개별 온수풀") and "수영장" not in scl[2]["voiceover"] and "개별 온수풀" in scl[2]["voiceover"] and scl[4]["voiceover"] == sty.STAY_CTA
      and scl[0]["script_engine"] == "claude", "LLM 대본: 맞는 문장은 채택, 지어낸 시설(수영장·사우나·헬스장)은 팩트 템플릿으로 교체, CTA는 고정")

# ---------------------------------------------------------------- 5. 렌더 정책·엔딩 카드
os.environ.pop("TRAVEL_STAY_STYLE", None)
base = get_mode_policy("travel")
os.environ["TRAVEL_STAY_STYLE"] = "1"
sp_ = get_mode_policy("travel")
os.environ.pop("TRAVEL_STAY_STYLE", None)
check(base.center_y == 0.50 and base.caption_chunk_chars == 0 and sp_.center_y == 0.55 and sp_.caption_chunk_chars == 18 and sp_.max_chars >= 46,
      "숙소 자동 수집 영상만 자막 55%·2줄 덩어리 분할 (기존 숙소 크롤링 흐름은 그대로)")
from core.render.brand import BrandOverlay, EndCard
bo = BrandOverlay(business_name="가평 리버뷰 풀빌라", end_card=EndCard(search_keyword="", headline="가평 리버뷰 풀빌라", cta_text="최저가 할인 예약 링크는\n[고정 댓글 / 프로필 링크] 확인!", show_search_bar=False))
bg = np.full((1920, 1080, 3), 90, np.uint8)
ec = bo.render_end_card(bg)
bo2 = BrandOverlay(business_name="가평 리버뷰 풀빌라", end_card=EndCard(search_keyword="가평 리버뷰 풀빌라"))
ec2 = bo2.render_end_card(bg)
check(ec.shape == (1920, 1080, 3) and int(np.abs(ec.astype(int) - ec2.astype(int)).sum()) > 0 and (ec[int(1920 * 0.40):int(1920 * 0.60)] == 255).all(axis=2).sum() < (ec2[int(1920 * 0.40):int(1920 * 0.60)] == 255).all(axis=2).sum(),
      "엔딩 카드: 검색창 없이 '[고정 댓글 / 프로필 링크] 확인!' 안내 문구만 (포털 검색 이탈 방지)")

# ---------------------------------------------------------------- 6. 화면 흐름 (AppTest, 외부 서버는 가짜)
from streamlit.testing.v1 import AppTest
os.environ.update({"NAVER_CLIENT_ID": "ID", "NAVER_CLIENT_SECRET": "SECRET", "GEMINI_API_KEY": "", "STORE_RESEARCH_DISABLE": ""})
requests.get, requests.post = hub_get, hub_post
try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=300).run()
    at.session_state.mode_switch = "🧳 숙소 제휴 쇼츠"
    at.session_state.production_mode = "travel"
    at.run()
    check(not at.exception and any(r.label == "랭킹 기준" for r in at.radio) and any(s.label == "지역·테마" for s in at.selectbox), "숙소 모드 상단: 시즌 급상승/스테디 라디오 + 지역·테마 드롭다운")
    check(any("제휴 트래킹" in t.label for t in at.text_input) and any(r.label == "상단 워터마크" for r in at.radio), "사이드바: 제휴 트래킹 템플릿·파트너 ID·워터마크(특징 별칭/끄기)")
    at.button(key="stay_find").click().run()
    picks = [b for b in at.button if b.key and b.key.startswith("stay_pick_")]
    check(not at.exception and 1 <= len(picks) <= 5 and all("자동 수집 시작" in b.label for b in picks), f"TOP 5 카드 {len(picks)}개, 각 카드 아래 '이 숙소 선택 및 자동 수집 시작' 버튼")
    at.button(key="stay_pick_0").click().run()
    ctx = at.session_state.stay_ctx
    boxes = [c for c in at.checkbox if c.key and c.key.startswith("webpick_")]
    check(not at.exception and ctx["stay"]["name"] == "가평 리버뷰 풀빌라" and at.session_state.product_name_input == "가평 리버뷰 풀빌라" and ctx["facts"]["per_person"] == "4만 원대",
          f"원클릭 선택: 링크 복사 없이 숙소명·시설·1인 체감가({ctx['facts']['per_person']}) 자동 채움")
    check(len(boxes) == 12 and at.session_state.affiliate_program == "yeogi" and ctx["yeogi"]["blocked"], "여기어때가 막혀도(403) 네이버 사진 후보 12장 갤러리 + 폴백 안내")
    at.button(key="store_photo_use").click().run()
    ups = at.session_state.uploaded_paths
    check(not at.exception and len(ups) == 8, f"선택한 사진 8장이 영상 소스로 ({len(ups)}장)")
    plan_btn = [b for b in at.button if "대본 만들기" in b.label][0]
    plan_btn.click().run()
    sb = at.session_state.storyboard
    check(not at.exception and len(sb) == 5 and [s.get("media_group") for s in sb] == [[0], [1, 2], [3, 4], [5], [6]] and sb[4]["voiceover"] == sty.STAY_CTA,
          f"스토리보드: 5문장 + 사진 묶음 [1,2,2,1,1], 5문장 CTA 고정 ({len(sb)}씬)")
    check(all(s.get("source_path") or s.get("image_path") or s.get("video_path") or s.get("media_path") for s in sb) or at.session_state.uploaded_paths, "씬에 사진 배정")
    check("효정" not in " ".join(s["voiceover"] for s in sb) and "가평 리버뷰 풀빌라" not in " ".join(s["voiceover"] for s in sb), "대본에 숙소 본명 노출 없음")
finally:
    requests.get, requests.post = _get, _post
    for k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TRAVEL_STAY_STYLE"):
        os.environ.pop(k, None)

# ---------------------------------------------------------------- 7. 실제 렌더: 18초 5문장 + 사진 묶음 + 엔딩 카드(검색창 없음), 숙소 자동 수집 정책
import re as _re
import shutil
import subprocess
import imageio_ffmpeg
import core.pipeline as pl
from core.job import JobContext
import core.mode_policy as mp
mp.INTRO_COVER_FORCE = False
W2 = tmp("stay_render_")
os.environ["TRAVEL_STAY_STYLE"] = "1"
try:
    job = JobContext.create(app="stay_render")
    job.root = W2
    job = JobContext(job.job_id, root=W2)
    from _support import fixture as _fx
    ups = []
    for i in range(7):
        dst = os.path.join(job.uploads_dir, f"u{i}.jpg")
        shutil.copy(_fx("photo_pool.jpg" if i < 3 else "photo_room.jpg"), dst)
        ups.append(dst)
    scenes = sty.plan_stay_storyboard("효정 리버뷰 풀빌라", "가평 리버뷰 독채풀빌라", "경기도 가평군", facts, ups, mock=True)
    brand = BrandOverlay(business_name="가평 리버뷰 독채풀빌라", show_label=True,
                         end_card=EndCard(search_keyword="", headline="가평 리버뷰 독채풀빌라", cta_text="최저가 할인 예약 링크는" + chr(10) + "[고정 댓글 / 프로필 링크] 확인!", show_search_bar=False))
    spec = pl.JobSpec(mode="travel", name="효정 리버뷰 풀빌라", usp="개별 온수풀", media_paths=ups, mock=True, brand=brand, affiliate_program="yeogi",
                      link="https://www.yeogi.com/x", audio_mode="voice_and_sub")
    pl.assign_uploads_to_scenes(job, scenes, ups, cycle=False)
    ex = [len(s.get("extra_media") or []) for s in scenes]
    res = pl.render(spec, job, scenes)
    err = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", res.final_path], capture_output=True, text=True).stderr
    m = _re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
    check(os.path.exists(res.final_path) and ex == [0, 1, 1, 0, 0] and "1080x1920" in err and 14 <= dur <= 30,
          f"실제 렌더: 숙소 5문장 + 사진 묶음 [1,2,2,1,1] + 엔딩 카드 → {dur:.1f}초 1080x1920")
finally:
    os.environ.pop("TRAVEL_STAY_STYLE", None)

finish(fails)
