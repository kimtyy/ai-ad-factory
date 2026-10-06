"""외부 접속 PIN 잠금: 판정(터널·다른 기기 vs localhost), PIN 화면, 틀린 PIN 차단, 5회 실패 잠금, 미설정 시 fail-closed"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import shutil

from streamlit.testing.v1 import AppTest

import ui.auth as auth

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


# ---------------------------------------------------------------- 1. 접속 판정
check(auth.is_remote({"Host": "trek-acrylic-offering-releases.trycloudflare.com", "Cf-Connecting-Ip": "1.2.3.4"}),
      "Cloudflare 터널 접속 → PIN 필요")
check(auth.is_remote({"Host": "192.168.0.12:8501"}), "같은 와이파이 다른 기기(IP 접속) → PIN 필요")
check(not auth.is_remote({"Host": "localhost:8501"}) and not auth.is_remote({"Host": "127.0.0.1:8501"}) and not auth.is_remote({}),
      "PC에서 localhost 접속 → 바로 열림")
check(auth.client_key({"Cf-Connecting-Ip": "1.2.3.4", "X-Forwarded-For": "9.9.9.9"}) == "1.2.3.4"
      and auth.client_key({"X-Forwarded-For": "5.6.7.8, 10.0.0.1"}) == "5.6.7.8", "실패 횟수는 실제 접속자 IP 기준")

# ---------------------------------------------------------------- 2. 앱 화면 (로컬 테스트에서도 강제 잠금)
os.environ["DASHBOARD_PIN_ALWAYS"] = "1"
os.environ["DASHBOARD_PIN"] = "482915"
os.environ["YOUTUBE_API_KEY"] = ""
jobs = []


def boot():
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120).run()
    return at


def texts(at):
    return " ".join(str(getattr(e, "value", "")) for kind in ("markdown", "caption", "error", "subheader")
                    for e in getattr(at, kind))


try:
    auth._fail_log.clear()
    at = boot()
    check(not at.exception and "AI 광고 공장" in texts(at) and len(at.text_input) == 1 and "job" not in at.session_state,
          "PIN 화면만 표시, 앱 본문·작업 공간 생성 안 됨")
    at.text_input(key="pin_input").input("000000")
    at.button[0].click().run()
    check("PIN이 맞지 않습니다" in texts(at) and "남은 시도 4번" in texts(at) and "pin_ok" not in at.session_state
          and "job" not in at.session_state, "틀린 PIN → 거부 + 남은 시도 안내, 앱 본문 미실행")
    at.text_input(key="pin_input").input("482915")
    at.button[0].click().run()
    check(not at.exception and at.session_state["pin_ok"] and "job" in at.session_state and len(at.text_input) > 1,
          "맞는 PIN → 대시보드 열림")
    jobs.append(at.session_state.job.workdir)
    at.run()
    check(at.session_state["pin_ok"] and len(at.text_input) > 1, "같은 세션 재실행 시 다시 묻지 않음")

    # 5회 실패 → 잠금 (맞는 PIN도 거부)
    auth._fail_log.clear()
    at2 = boot()
    for _ in range(auth.MAX_FAILS):
        at2.text_input(key="pin_input").input("111111")
        at2.button[0].click().run()
    at2.run()
    locked = "잠겼습니다" in texts(at2) and len(at2.text_input) == 0
    check(locked, f"{auth.MAX_FAILS}번 틀리면 {auth.LOCK_SEC // 60}분 잠금 (입력창 사라짐)")
    auth._fail_log.clear()

    # PIN 미설정 → 외부 접속 차단
    os.environ["DASHBOARD_PIN"] = ""
    at3 = boot()
    check("DASHBOARD_PIN" in texts(at3) and len(at3.text_input) == 0 and "job" not in at3.session_state,
          "DASHBOARD_PIN 미설정 → 외부 접속 자체 차단 (fail-closed)")
finally:
    os.environ.pop("DASHBOARD_PIN_ALWAYS", None)
    for j in jobs:
        shutil.rmtree(j, ignore_errors=True)

# 잠금 해제 상태(기본)에서는 기존 테스트처럼 바로 열림
at4 = boot()
check(not at4.exception and "job" in at4.session_state and len(at4.text_input) > 1, "로컬 기본 실행은 PIN 없이 바로 열림 (기존 테스트 영향 없음)")
shutil.rmtree(at4.session_state.job.workdir, ignore_errors=True)
finish(fails)
