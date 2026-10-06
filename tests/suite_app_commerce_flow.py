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
    at.sidebar.checkbox[0].check().run()                                   # Mock (API 과금 없음)
    photo = fixture("photo_room.jpg")
    at.session_state.uploaded_paths = [photo]                              # '이 상품 사용'으로 대표 사진 1장이 들어온 상태
    at.session_state.scraped_data = {"product_name": "유파 PTC 전기히터 TSK-5318", "usp": "저소음 설계, 과열 방지"}
    at.run()
    check(any("세 번이면 끝" in c.value for c in at.caption), "쇼핑: 3번 터치 안내")
    [b for b in at.button if "영상 만들기" in b.label][0].click().run()
    sb = at.session_state.storyboard
    jobs.append(at.session_state.job.workdir)
    check(not at.exception and sb and sb[-1]["role"] == "cta", f"콘티 없이 [영상 만들기] → 기획 자동 실행 ({len(sb)}씬)")
    check([s.get("shot") for s in sb] == ["full", "center", "top", "bottom"][:len(sb)] and all(s.get("image_path") == photo for s in sb),
          f"대표 사진 1장 → 씬마다 다른 앵글 {[s.get('shot') for s in sb]}")
    final = at.session_state.final_video_path
    check(bool(final) and os.path.exists(final) and os.path.getsize(final) > 100_000, "같은 클릭으로 렌더까지 완료 (final.mp4)")
    check(any("수수료를 제공받습니다" in (c.value or "") for c in at.code), "렌더 후 쿠팡 파트너스 고지 문구 제공")
finally:
    for j in set(jobs):
        shutil.rmtree(j, ignore_errors=True)
finish(fails)
