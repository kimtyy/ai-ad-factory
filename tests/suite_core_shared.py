import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import inspect
import os

import run_all
from core.compositor import AdCompositor
from core.mode_policy import get_mode_policy

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


# ---------------------------------------------------------------- 1. 자막 위치: 하나의 스위치가 중앙 자막 모드 공통으로 적용
for k in ("OWNER_CAPTION_POS", "TRAVEL_STAY_STYLE"):
    os.environ.pop(k, None)
mid = (get_mode_policy("owner").center_y, get_mode_policy("travel").center_y)
os.environ["TRAVEL_STAY_STYLE"] = "1"
stay_mid = get_mode_policy("travel").center_y
os.environ["OWNER_CAPTION_POS"] = "top"
top = (get_mode_policy("owner").center_y, get_mode_policy("travel").center_y)
os.environ.pop("OWNER_CAPTION_POS", None)
os.environ.pop("TRAVEL_STAY_STYLE", None)
check(mid == (0.55, 0.50) and stay_mid == 0.55 and top == (0.24, 0.24), f"자막 위치 단일 스위치: 기본 55%(매장·숙소 자동 수집) / 상단 24%(둘 다 같이) — {mid} {stay_mid} {top}")

# ---------------------------------------------------------------- 2. 2줄 분할 자막은 같은 함수 (모드가 달라도 같은 문장은 같게 나뉨)
comp = AdCompositor(output_dir=tmp("coreshared_"))
text = "주말에 뷰 좋은 풀빌라 가려다 1박 가격에 놀라서 포기하셨죠, 여긴 달라요!"
os.environ["TRAVEL_STAY_STYLE"] = "1"
a, b = comp.caption_chunks(text, "owner"), comp.caption_chunks(text, "travel")
os.environ.pop("TRAVEL_STAY_STYLE", None)
check(len(a) >= 2 and len(b) >= 2 and "".join(a).replace(" ", "") == "".join(b).replace(" ", "") == text.replace(" ", "") and len(b) <= 2,
      f"자막 분할은 매장·숙소가 같은 코어 함수: 매장 {len(a)}덩어리(2줄 방식), 숙소 자동 수집 {len(b)}덩어리(의미 단위 1줄)")
check(comp.caption_chunks(text, "travel") == [text], "자동 수집이 아닌 기존 숙소 영상은 분할 없이 기존 방식 그대로")

# ---------------------------------------------------------------- 3. 사진 안전 필터는 수집기 한 곳 (매장·숙소·쇼핑 공통)
import core.stay_photos as sp
import core.store_photos as stp
check(inspect.signature(stp.download_store_image).parameters["transform"].default is True and inspect.signature(stp.search_store_images).parameters["official_only"].default is True
      and "official_only" in inspect.signature(sp.search_stay_candidates).parameters and "search_store_images" in inspect.getsource(sp.search_stay_candidates),
      "사진 안전 필터(공식 우선·편집 변환 기본 ON)는 공통 수집기 하나, 숙소 수집은 그걸 그대로 사용")

# ---------------------------------------------------------------- 4. 모드별 쾌속 테스트 지도: 모든 스위트 파일이 실제로 존재·등록
names = {s[0] for s in run_all.SUITES}
files = [f for f in run_all.CORE_QUICK] + [f for v in run_all.MODE_SUITES.values() for f in v]
check(all(f in names and os.path.exists(os.path.join(ROOT, "tests", f)) for f in files) and "suite_stay_pipeline.py" in run_all.MODE_SUITES["stay"],
      f"모드별 스위트 지도: 코어 {len(run_all.CORE_QUICK)}개 + 모드 {len(run_all.MODE_SUITES)}종, 모두 등록·존재")

# ---------------------------------------------------------------- 5. 점주 시연 모드 (단일 모드 노출)
from streamlit.testing.v1 import AppTest


def tabs(at):
    modes = next((s for s in at.main if hasattr(s, "options") and s.options and any("사장님" in str(o) for o in s.options)), None)
    return modes


at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120).run()
check(not at.exception and at.session_state.demo_mode is False and any(c.key == "demo_mode" for c in at.sidebar.checkbox), "사이드바 하단에 '🎯 점주 시연 모드' 체크박스 (기본 OFF)")
cb = [c for c in at.sidebar.checkbox if c.key == "demo_mode"][0]
check("매장 홍보 탭만 표시" in cb.label, "체크박스 문구: 점주 시연 모드 (매장 홍보 탭만 표시)")
at.session_state.mode_switch = "🧳 숙소 제휴 쇼츠"
at.session_state.production_mode = "travel"
at.run()
cb = [c for c in at.sidebar.checkbox if c.key == "demo_mode"][0]
cb.check().run()
sc = [e for e in at.main if getattr(e, "key", None) == "mode_switch"]
pg = [e for e in at.main if getattr(e, "key", None) == "page_switch"]
check(not at.exception and at.session_state.production_mode == "owner" and sc and len(sc[0].options) == 1 and "사장님" in str(sc[0].options[0]) and pg and len(pg[0].options) == 1,
      "시연 모드 ON: 숙소 모드에 있어도 매장 홍보로 전환, 모드 탭·화면 탭 모두 1개만 표시")
cb = [c for c in at.sidebar.checkbox if c.key == "demo_mode"][0]
cb.uncheck().run()
sc = [e for e in at.main if getattr(e, "key", None) == "mode_switch"]
pg = [e for e in at.main if getattr(e, "key", None) == "page_switch"]
check(not at.exception and sc and len(sc[0].options) == 5 and pg and len(pg[0].options) == 3, "시연 모드 OFF: 5개 모드·3개 화면이 모두 돌아옴")

os.environ["DEMO_MODE"] = "1"
try:
    at2 = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120).run()
    sc2 = [e for e in at2.main if getattr(e, "key", None) == "mode_switch"]
    check(not at2.exception and at2.session_state.demo_mode is True and sc2 and len(sc2[0].options) == 1, "환경변수 DEMO_MODE=1이면 처음부터 시연 모드")
finally:
    os.environ.pop("DEMO_MODE", None)
try:
    at3 = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120)
    at3.query_params["demo"] = "1"
    at3.run()
    check(not at3.exception and at3.session_state.demo_mode is True, "주소 뒤 ?demo=1 이면 시연 모드")
except Exception as e:                                         # 이 Streamlit 버전의 AppTest가 쿼리 파라미터를 지원하지 않으면 건너뜀
    print(f"SKIP 주소 ?demo=1 (AppTest 쿼리 파라미터 미지원: {type(e).__name__})")

finish(fails)
