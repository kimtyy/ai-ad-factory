"""AppTest: 쇼핑 원클릭 흐름 - 상품 선택(사진 1장) 상태에서 [영상 만들기]만 누르면 기획 → 렌더까지 (mock)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import shutil

from streamlit.testing.v1 import AppTest

os.environ["YOUTUBE_API_KEY"] = ""
os.environ["COUPANG_ACCESS_KEY"] = ""      # 실제 쿠팡 API 호출 방지 (load_dotenv는 이미 있는 값을 덮어쓰지 않음)
fails, jobs = 0, []


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=600).run()
    jobs.append(at.session_state.job.workdir)
    at.session_state.mode_switch = "🛒 쇼핑 제휴 쇼츠"
    at.session_state.production_mode = "commerce"
    at.run()
    at.session_state["mock_mode"] = True; at.run()                                   # Mock (API 과금 없음)
    photo = fixture("photo_room.jpg")
    at.session_state.uploaded_paths = [photo]                              # '이 상품 사용'으로 대표 사진 1장이 들어온 상태
    at.session_state.product_name_input = "유파 PTC 전기히터 TSK-5318"         # 입력칸 위젯 key (이 상품 사용이 채우는 값)
    at.session_state.product_usp_input = "저소음 설계, 과열 방지"
    at.run()
    check(any("네이버 쇼핑 꿀템" in m.value for m in at.markdown) and any(b.key == "shop_find" for b in at.button), "쇼핑: 최상단에 네이버 쇼핑 꿀템 TOP 5 + 검색 (쿠팡은 보조)")
    [b for b in at.button if "영상 만들기" in b.label][0].click().run()
    sb = at.session_state.storyboard
    jobs.append(at.session_state.job.workdir)
    check(not at.exception and sb and sb[-1]["role"] == "cta", f"콘티 없이 [영상 만들기] → 기획 자동 실행 ({len(sb)}씬)")
    check([s.get("shot") for s in sb] == ["full", "center", "top", "bottom"][:len(sb)] and all(s.get("image_path") == photo for s in sb),
          f"대표 사진 1장 → 씬마다 다른 앵글 {[s.get('shot') for s in sb]}")
    final = at.session_state.final_video_path
    check(bool(final) and os.path.exists(final) and os.path.getsize(final) > 100_000, "같은 클릭으로 렌더까지 완료 (final.mp4)")
    check(any("수수료를 제공받습니다" in (c.value or "") for c in at.code), "렌더 후 쿠팡 파트너스 고지 문구 제공")

    # [이 상품 사용] → 상품명·특장점 입력칸이 위젯 key로 채워짐 (예전엔 value= 인자만 바꿔 실제 브라우저에서 빈칸으로 남았음)
    harness = (
        "import core.coupang as cp, core.scraper as sc, core.planners as cpl\n"
        "cp.CoupangPartners.search = lambda self, kw, limit=10: [{'product_id': '1', 'name': '한경희 불멍 온풍기 HAN-P9900',"
        " 'price': 129900, 'image_url': 'https://x/i.png', 'partner_url': 'https://link.coupang.com/re/x', 'category': '', 'is_rocket': True}]\n"
        "cp.CoupangPartners.deeplink = lambda self, url: 'https://link.coupang.com/a/TEST'\n"
        "import importlib\n"
        "importlib.reload = lambda m: m\n"   # 앱이 시작 시 core.planners*를 reload해 아래 패치를 지우므로 reload 무력화
        "import requests\n"   # 앱이 core.scraper를 reload하므로 함수 대신 네트워크 층을 가짜로 (사진 1장 응답)
        f"_img = open({photo!r}, 'rb').read()\n"
        "requests.get = lambda url, **kw: type('R', (), {'status_code': 200, 'content': _img, 'headers': {}})()\n"
        "cpl.analyze_product_photo = lambda *a, **k: {'points': ['AI 자동 온도 조절', '감성 불멍 디스플레이'], 'box': [0.1, 0.0, 0.5, 1.0]}\n"
        f"exec(open({os.path.join(ROOT, 'app.py')!r}, encoding='utf-8').read(), {{'__name__': '__main__'}})\n")
    os.environ["COUPANG_ACCESS_KEY"], os.environ["COUPANG_SECRET_KEY"] = "AK", "SK"
    pk = AppTest.from_string(harness, default_timeout=120).run()
    jobs.append(pk.session_state.job.workdir)
    pk.session_state.mode_switch = "🛒 쇼핑 제휴 쇼츠"
    pk.session_state.production_mode = "commerce"
    pk.run()
    [t for t in pk.text_input if t.label.startswith("홍보 제품명")][0].input("").run()       # 브라우저가 빈 값을 보낸 상태
    pk.text_input(key="coupang_kw").input("히터").run()
    [b for b in pk.button if b.label == "검색"][0].click().run()
    [b for b in pk.button if b.label == "이 상품 사용"][0].click().run()
    jobs.append(pk.session_state.job.workdir)
    check(pk.text_input(key="product_name_input").value == "한경희 불멍 온풍기 HAN-P9900"
          and pk.text_input(key="product_usp_input").value == "AI 자동 온도 조절, 감성 불멍 디스플레이"
          and pk.session_state.affiliate_link.endswith("TEST") and len(pk.session_state.uploaded_paths) == 1
          and os.path.exists(pk.session_state.uploaded_paths[0]),
          "[이 상품 사용] → 상품명·특장점 입력칸(위젯 key)·사진·파트너스 링크가 함께 채워짐 "
          + str((pk.text_input(key="product_name_input").value, pk.text_input(key="product_usp_input").value,
                 pk.session_state.affiliate_link, pk.session_state.uploaded_paths)))
    app_src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    check('key="product_name_input"' in app_src and 'key="product_usp_input"' in app_src
          and 'value=scraped_now' not in app_src, "상품명·특장점 입력칸은 key로 연결 (value= 인자 갱신 방식 제거)")
finally:
    os.environ.pop("COUPANG_SECRET_KEY", None)
    os.environ["COUPANG_ACCESS_KEY"] = ""
    for j in set(jobs):
        shutil.rmtree(j, ignore_errors=True)
finish(fails)
