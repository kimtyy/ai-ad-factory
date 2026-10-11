"""아고다·트립닷컴 숙소 제휴: 링크 판별(PC·모바일·단축), 숙소 ID·이름, 제휴 수익 링크, 페이지 미접속, 앱 흐름(링크 → 사진 업로드 → 기획 → 렌더 → 링크 복사)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, finish  # noqa: E402
import os
import shutil
import logging
logging.disable(logging.WARNING)

import requests
from streamlit.testing.v1 import AppTest

import core.affiliate_ota as ota
import core.scraper as sc

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


ENV = {"AGODA_CID": "1234567", "TRIP_AID": "111", "TRIP_SID": "222"}
AG = "https://www.agoda.com/ko-kr/st-john-s-hotel/hotel/gangneung-si-kr.html?cid=999&checkIn=2026-10-20&los=1&adults=2&tag=x"

# ---------------------------------------------------------------- 1. 링크 판별
check(ota.detect_ota(AG) == "agoda" and ota.detect_ota("https://m.agoda.com/ko-kr/x/hotel/seoul-kr.html") == "agoda"
      and ota.detect_ota("https://kr.trip.com/hotels/x-hotel-detail-1/") == "trip" and ota.detect_ota("https://m.trip.com/html5/hotel/HotelDetail/1.html") == "trip",
      "PC·모바일·국가별 도메인 판별 (www/m/kr)")
check(ota.detect_ota("https://www.agoda.com.evil.net/x") is None and ota.detect_ota("https://notagoda.com/") is None
      and ota.detect_ota("https://www.yanolja.com/x") is None, "비슷한 가짜 도메인·다른 사이트는 제외")
check(ota.is_short_ota_link("https://www.trip.com/w/AbCd123") and ota.is_short_ota_link("https://www.agoda.com/sp/XyZ")
      and not ota.is_short_ota_link(AG), "공유용 단축 링크 판별")

# ---------------------------------------------------------------- 2. 숙소 ID·이름 (링크 주소에서만)
a = ota.parse_agoda(AG)
check(a["name"] == "St John's Hotel" and a["city"] == "gangneung-si-kr", f"아고다 주소 → 영문 숙소명 {a}")
check(ota.parse_agoda("https://www.agoda.com/partners/partnersearch.aspx?hid=55123")["hotel_id"] == "55123", "아고다 hid → 숙소 ID")
t = ota.parse_trip("https://kr.trip.com/hotels/gangneung-hotel-detail-1234567/st-johns-hotel/?checkIn=2026-10-20")
check(t["hotel_id"] == "1234567" and t["name"] == "St Johns Hotel", f"트립닷컴 주소 → 숙소 ID·이름 {t}")
check(ota.parse_trip("https://m.trip.com/html5/hotel/HotelDetail/7654321.html")["hotel_id"] == "7654321"
      and ota.parse_trip("https://www.trip.com/hotels/detail/?hotelId=998877")["hotel_id"] == "998877", "트립닷컴 모바일·hotelId 주소 → 숙소 ID")

# ---------------------------------------------------------------- 3. 제휴 수익 링크
la = ota.affiliate_link(AG, env=ENV)["link"]
check(la == "https://www.agoda.com/ko-kr/st-john-s-hotel/hotel/gangneung-si-kr.html?cid=1234567",
      f"아고다: 내 cid로 교체, 남의 cid·날짜·인원·추적값 제거 {la}")
check(ota.affiliate_link("https://m.agoda.com/ko-kr/x/hotel/seoul-kr.html", env=ENV)["link"].startswith("https://www.agoda.com/"),
      "아고다 모바일 주소 → PC 주소로 통일")
lt = ota.affiliate_link("https://kr.trip.com/hotels/gangneung-hotel-detail-1234567/st-johns-hotel/?checkIn=2026-10-20&Allianceid=9&SID=9", env=ENV)["link"]
check(lt == "https://kr.trip.com/hotels/gangneung-hotel-detail-1234567/st-johns-hotel/?Allianceid=111&SID=222&trip_sub1=shorts",
      f"트립닷컴: Allianceid·SID 교체 + 쇼츠 유입 표시(trip_sub1) {lt}")
miss = ota.affiliate_link(AG, env={})
check(miss["link"] == "" and miss["missing_keys"] == ["AGODA_CID"]
      and ota.affiliate_link(lt, env={"TRIP_AID": "1"})["missing_keys"] == ["TRIP_SID"], "제휴 ID 없으면 링크를 지어내지 않고 빠진 키 안내")

# ---------------------------------------------------------------- 4. 크롤러: 아고다·트립닷컴 페이지는 열지 않음
opened = []
_orig_get, _orig_head = requests.get, requests.head


def _rec(url, *a, **k):
    opened.append(url)
    raise AssertionError("열면 안 되는 주소")


requests.get = requests.head = _rec
for k, v in ENV.items():
    os.environ[k] = v
out = sc.scrape_product_info(AG, output_image_dir=os.path.join(ROOT, "outputs", "_test_ota"), mode="travel")
check(out["success"] and out["ota"] == "agoda" and out["product_name"] == "St John's Hotel"
      and out["affiliate_link"].endswith("cid=1234567") and out["image_pool"] == [] and "photos" in out["missing"] and not opened,
      "아고다 링크 → 페이지 미접속, 숙소명·제휴 링크만, 사진은 직접 업로드 요청")
_res = sc.resolve_link
sc.resolve_link = lambda u, **k: {"url": "https://kr.trip.com/hotels/seoul-hotel-detail-4455/lotte-hotel-seoul/", "is_search": False}
import core.affiliate_ota as _o2
out2 = sc.scrape_product_info("https://www.trip.com/w/AbCd123", output_image_dir=os.path.join(ROOT, "outputs", "_test_ota"), mode="travel")
sc.resolve_link = _res
check(out2["ota"] == "trip" and out2["hotel_id"] == "4455" and out2["product_name"] == "Lotte Hotel Seoul"
      and "Allianceid=111" in out2["affiliate_link"] and not opened, "트립닷컴 단축 링크 → 리다이렉트 주소만 → 숙소 ID·이름·제휴 링크")
requests.get, requests.head = _orig_get, _orig_head
shutil.rmtree(os.path.join(ROOT, "outputs", "_test_ota"), ignore_errors=True)

# ---------------------------------------------------------------- 5. 앱 흐름 (mock): 링크 → 사진 → 기획 → 렌더 → 제휴 링크·복사 버튼
os.environ["YOUTUBE_API_KEY"] = ""
created = []
try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=600).run()
    created.append(at.session_state.job.workdir)
    at.session_state.mode_switch = "🧳 숙소 제휴 쇼츠"
    at.session_state.production_mode = "travel"
    at.run()
    at.sidebar.checkbox[0].check().run()                                    # Mock
    [ti for ti in at.text_input if ti.label == "상품 페이지 URL 입력"][0].input(AG).run()
    [b for b in at.button if "숙소 정보 크롤링" in b.label][0].click().run()
    created.append(at.session_state.job.workdir)
    check(not at.exception and at.session_state.affiliate_program == "agoda"
          and at.session_state.affiliate_link.endswith("cid=1234567")
          and at.text_input(key="product_name_input").value == "St John's Hotel"
          and any("사진은 가져오지 않아요" in (i.value or "") for i in at.info), "앱: 아고다 링크 → 숙소명 채움 + 제휴 링크 + 사진 업로드 안내")
    job = at.session_state.job
    srcs = [fixture("photo_pool.jpg"), fixture("photo_room.jpg"), fixture("photo_pool.jpg"), fixture("photo_room.jpg")]
    at.session_state.uploaded_paths = [shutil.copy(p, os.path.join(job.uploads_dir, f"h{i}.jpg")) for i, p in enumerate(srcs)]
    at.text_input(key="product_usp_input").input("오션뷰 인피니티 풀, 객실 통창 바다 전망").run()
    [b for b in at.button if "스토리보드 자동 기획" in b.label][0].click().run()
    check(not at.exception and len(at.session_state.storyboard) >= 3, f"앱: 올린 사진으로 숙소 콘티 기획 ({len(at.session_state.storyboard)}씬)")
    [b for b in at.button if "영상 만들기" in b.label][0].click().run()
    final = at.session_state.final_video_path
    check(not at.exception and bool(final) and os.path.exists(final), f"앱: 렌더 완료 {[e.value for e in at.error]}")
    codes = [c.value for c in at.code]
    check(any(c == at.session_state.affiliate_link for c in codes) and any("수수료" in c and "cid=1234567" in c for c in codes),
          "렌더 완료 화면: 어필리에이트 추천 링크 + 고지 문구+링크")
    check(any("copy" in (getattr(e, "proto", None) and str(e.proto) or "") for e in at.get("iframe")) or len(at.get("iframe")) >= 2,
          "원클릭 복사 버튼 2개 (링크만 / 고지 문구+링크)")

    # 렌더와 함께 썸네일 + 유튜브 업로드 패널 (미연결 → 안내, 연결 → 원클릭 업로드)
    import core.youtube_upload as yu
    thumb = at.session_state.thumb_path
    check(bool(thumb) and os.path.exists(thumb) and thumb.endswith("final_thumb.png") and os.path.dirname(thumb) == os.path.dirname(final),
          "렌더 시 썸네일 final_thumb.png 동시 생성 (영상과 같은 폴더)")
    _secrets = yu.SECRETS_DIR
    yu.SECRETS_DIR = os.path.join(os.path.dirname(final), "_secrets")
    os.environ["YOUTUBE_CHANNEL_TRAVEL"] = "여행을 떠나자"
    at.run()
    check(any("youtube_auth.py --mode travel" in (i.value or "") for i in at.info)
          and any("여행을 떠나자" in (m.value or "") for m in at.markdown), "숙소 모드: '여행을 떠나자' 채널 미연결 → --mode travel 연결 안내")
    os.makedirs(yu.SECRETS_DIR, exist_ok=True)
    open(yu.token_path("travel"), "w").write("{}")
    sent = {}

    class _R:
        def __init__(self, r): self.r = r
        def execute(self): return self.r
        def next_chunk(self): return None, self.r

    class _Svc:
        def channels(self): return type("X", (), {"list": lambda s, **k: _R({"items": [{"id": "UC1", "snippet": {"title": "여행을 떠나자"}}]})})()
        def videos(self): return type("X", (), {"insert": lambda s, **k: (sent.update(insert=k), _R({"id": "V9", "status": {"privacyStatus": "private"}}))[1]})()
        def thumbnails(self): return type("X", (), {"set": lambda s, **k: (sent.update(thumb=k), _R({}))[1]})()
        def commentThreads(self): return type("X", (), {"insert": lambda s, **k: (sent.update(comment=k), _R({"id": "C"}))[1]})()

    yu._service_factory = lambda key: (sent.update(key=key), _Svc())[1]
    _pending = yu.PENDING_PATH
    yu.PENDING_PATH = os.path.join(yu.SECRETS_DIR, "pending_comments.json")
    at.run()
    [b for b in at.button if "유튜브 쇼츠 업로드" in b.label][0].click().run()
    queued = yu.pending_comment("V9") or {}
    yu._service_factory, yu.SECRETS_DIR, yu.PENDING_PATH = None, _secrets, _pending
    os.environ.pop("YOUTUBE_CHANNEL_TRAVEL", None)
    body = sent.get("insert", {}).get("body", {})
    check(not at.exception and any("V9" in (s.value or "") for s in at.success) and sent.get("key") == "travel"
          and "첫 번째 댓글" in body.get("snippet", {}).get("description", "") and "수수료" in body["snippet"]["description"]
          and body["snippet"]["title"].endswith("#Shorts")
          and body["status"]["privacyStatus"] == "private" and "thumb" in sent
          and "comment" not in sent and "cid=1234567" in queued.get("text", "")
          and any("첫 댓글 대기 중" in (i.value or "") for i in at.info),
          "원클릭 업로드: 설명란(제목·제휴 고지·첫 댓글 안내)·썸네일, 기본 비공개 → 첫 댓글(아고다 링크)은 공개 전환 대기열 + 안내")
finally:
    for wd in set(created):
        shutil.rmtree(wd, ignore_errors=True)
    for k in ENV:
        os.environ.pop(k, None)

finish(fails)
