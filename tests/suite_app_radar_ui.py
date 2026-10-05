"""AppTest: 🔥 실시간 떡상 레이더 화면 + 영상 기획 연동"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import shutil

from streamlit.testing.v1 import AppTest

import core.radar.store as radar_store

# .env에 실제 YOUTUBE_API_KEY가 있어도 테스트가 실 API(쿼터·과금)를 호출하지 않도록 키를 비움
# (load_dotenv는 이미 설정된 환경 변수를 덮어쓰지 않으므로 빈 값이 유지됨)
os.environ["YOUTUBE_API_KEY"] = ""

# 레이더 DB를 임시 폴더로 돌려 사용자의 실제 outputs/radar/ 데이터를 건드리지 않음
_DB_DIR = tmp("radar_ui_db_")
radar_store.DEFAULT_DB = os.path.join(_DB_DIR, "radar.db")
radar_store.DRYRUN_DB = os.path.join(_DB_DIR, "radar_dryrun.db")
DRY_DB = radar_store.DRYRUN_DB
REAL_DB = os.path.join(ROOT, "outputs", "radar", "radar.db")
REAL_DRY = os.path.join(ROOT, "outputs", "radar", "radar_dryrun.db")
_real_state = [(p, os.path.getmtime(p) if os.path.exists(p) else None) for p in (REAL_DB, REAL_DRY)]
fails, jobs = 0, []


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


def all_text(at):
    out = []
    for kind in ("subheader", "markdown", "caption", "info", "warning", "success", "error", "text"):
        out += [getattr(e, "value", "") or "" for e in getattr(at, kind)]
    return "\n".join(str(x) for x in out)


try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=300).run()
    jobs.append(at.session_state.job.workdir)
    check(not at.exception and at.session_state.page_switch == "make", "기본 화면 = 영상 만들기")
    check("Session State API" not in all_text(at), "위젯 기본값 + 세션 재할당 경고 없음 (제작 화면)")

    # 레이더 화면
    at.session_state.page_switch = "radar"
    at.run()
    txt = all_text(at)
    check(not at.exception and "실시간 떡상 레이더" in txt and "Dry-run" in txt and len(at.sidebar.selectbox) == 0,
          "레이더 화면: 키 미설정 Dry-run 안내, 제작 설정 사이드바 숨김")
    check("아직 수집된 데이터가 없습니다" in txt or "최근" in txt, "수집 전 안내 표시")
    [b for b in at.button if "레이더 실행" in b.label][0].click().run()
    txt = all_text(at)
    check(not at.exception and any("떡상" in s.value and "DRY-RUN" in s.value for s in at.success), "실행 버튼 → Dry-run 수집 완료 메시지")
    check("요즘 먹히는 후킹 유형" in txt and txt.count("🔥") >= 5 and "🪝" in txt, "후킹 유형 요약 + 떡상 카드(배수·후킹 패턴) 표시")
    check("Session State API" not in txt, "위젯 기본값 + 세션 재할당 경고 없음 (레이더 화면)")
    check(at.slider(key="radar_days").value == 30, "기본 수집 기간 30일")

    # 도메인 부적합 판정 영상: 카드 기본 숨김, 체크박스로 펼쳐보기
    conn = radar_store.connect(DRY_DB)
    n_cards = sum("**🔥" in (m.value or "") for m in at.markdown)
    victim = radar_store.recent_outliers(conn, "stay", 30, 5.0, 24)[0]
    radar_store.save_hook(conn, victim["video_id"], {"source": "gemini:test", "spoken_line": "", "on_screen_text": "",
                                                     "visual_hook": "", "hook_type": "other", "pattern_template": "",
                                                     "why_it_works": "", "relevant": False, "content_type": "animation"})
    conn.close()
    at.run()
    txt = all_text(at)
    cb = at.checkbox(key="radar_show_irrelevant")
    check("🚫" not in txt and sum("**🔥" in (m.value or "") for m in at.markdown) == n_cards - 1 and "비적합 1개 숨김" in txt
          and cb.value is False and "(1개)" in cb.label, "부적합 영상 카드 기본 숨김 + 숨김 개수 표시")
    cb.check().run()
    txt = all_text(at)
    check("🚫 힌트 제외" in txt and sum("**🔥" in (m.value or "") for m in at.markdown) == n_cards and "(animation)" in txt and "숨김" not in txt, "'제외된 비적합 영상 보기' 체크 → 🚫 카드 펼침")
    at.checkbox(key="radar_show_irrelevant").uncheck().run()
    exp = [e.label for e in at.expander]
    check(any("사용 가능한 패턴 없음" in l for l in exp), f"Dry-run 데이터만 있을 때 힌트 미리보기 = 패턴 없음 (가짜 패턴 차단) {exp}")
    at.toggle(key="radar_allow_dryrun").set_value(True).run()
    check(any("(Dry-run)" in e.label for e in at.expander), "테스트 토글 켜면 Dry-run 패턴 미리보기")

    # 제작 화면으로 돌아와 연동 확인 (토글 값 유지)
    at.session_state.page_switch = "make"
    at.run()
    check(at.session_state.radar_allow_dryrun is True and at.session_state.radar_hints_on is True, "화면 전환 후 레이더 설정 유지")
    at.text_input(key="owner_name").input("가평 솔바람 펜션").run()
    at.selectbox(key="owner_type").select("stay").run()
    cap = [c.value for c in at.caption if "레이더 후킹 패턴" in c.value]
    check(cap and "Dry-run" in cap[0], f"기획 버튼 옆 힌트 사용 안내: {cap[:1]}")
    at.sidebar.checkbox[0].check().run()   # Mock
    [b for b in at.button if "스토리보드 자동 기획" in b.label][0].click().run()
    sb = at.session_state.storyboard
    check(not at.exception and sb and sb[0].get("hook_reference"), "사장님 기획 → 씬 1에 레이더 참고 출처 기록")
    check(any("씬 1 후킹" in c.value for c in at.caption), "콘티 화면에 씬 1 후킹 출처 표시")
    at.selectbox(key="owner_type").select("shop").run()
    check(not [c.value for c in at.caption if "레이더 후킹 패턴" in c.value], "매장·기타 업종: 레이더 vertical 없음 → 힌트 미사용")
    at.toggle  # noqa
    at.session_state.radar_hints_on = False
    at.selectbox(key="owner_type").select("food").run()
    check(not [c.value for c in at.caption if "레이더 후킹 패턴" in c.value], "힌트 사용 끄면 주입 안 함")
finally:
    for j in jobs + ([at.session_state.job.workdir] if "at" in dir() else []):
        shutil.rmtree(j, ignore_errors=True)
    shutil.rmtree(_DB_DIR, ignore_errors=True)
check([(p, os.path.getmtime(p) if os.path.exists(p) else None) for p in (REAL_DB, REAL_DRY)] == _real_state,
      "사용자의 실제 outputs/radar DB는 변경 없음")
finish(fails)