"""
ui/auth.py - 외부 접속(Cloudflare 터널·다른 기기) PIN 잠금

- 터널/프록시를 거친 요청(Cf-Connecting-Ip·X-Forwarded-For 헤더)이나 localhost가 아닌 호스트로 들어온 접속만 PIN을 묻고,
  PC에서 http://localhost:8501 로 여는 경우는 그대로 통과합니다.
- PIN은 .env의 DASHBOARD_PIN. 설정이 없으면 외부 접속을 아예 막습니다 (fail-closed).
- 같은 IP에서 MAX_FAILS번 틀리면 LOCK_SEC 동안 잠금 (터널 주소는 누구나 접근 가능하므로 무차별 대입 방지).
- 통과 여부는 브라우저 세션(st.session_state) 단위: 앱을 완전히 닫았다 열면 다시 입력.
"""
import hmac
import os
import time
from typing import Dict, List, Mapping

import streamlit as st

MAX_FAILS = 5
LOCK_SEC = 600
PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")


@st.cache_resource(show_spinner=False)
def _fail_log() -> Dict[str, List[float]]:
    """IP별 PIN 실패 시각 (서버 프로세스 전체 공유)"""
    return {}


def _lower(headers: Mapping[str, str]) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def is_remote(headers: Mapping[str, str]) -> bool:
    """PIN을 물어야 하는 접속인지 (DASHBOARD_PIN_ALWAYS=1이면 로컬도 잠금: 테스트·공용 PC용)"""
    if os.getenv("DASHBOARD_PIN_ALWAYS", "").strip() == "1":
        return True
    h = _lower(headers)
    if any(k in h for k in PROXY_HEADERS):
        return True
    host = h.get("host", "")
    host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return bool(host) and host not in LOCAL_HOSTS


def client_key(headers: Mapping[str, str]) -> str:
    h = _lower(headers)
    return h.get("cf-connecting-ip") or h.get("x-forwarded-for", "").split(",")[0].strip() or "unknown"


def recent_fails(key: str, now: float) -> List[float]:
    log = _fail_log()
    log[key] = [t for t in log.get(key, []) if now - t < LOCK_SEC]
    return log[key]


def _request_headers() -> Dict[str, str]:
    try:
        return dict(st.context.headers)
    except Exception:
        return {}


def require_pin():
    """외부 접속이면 PIN 화면을 띄우고, 통과 전까지 나머지 앱 실행을 멈춤 (st.stop)"""
    headers = _request_headers()
    if st.session_state.get("pin_ok") or not is_remote(headers):
        return
    pin = os.getenv("DASHBOARD_PIN", "").strip()

    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        store = os.getenv("STORE_APP", "").strip() == "1"          # 단독 매장 앱(app_store.py)은 샵스토리 이름으로 표시
        st.markdown("## 🔒 샵스토리" if store else "## 🔒 AI 광고 공장")
        if not pin:
            st.error("외부 접속이 잠겨 있습니다. PC의 .env에 DASHBOARD_PIN을 설정한 뒤 다시 접속해 주세요.")
            st.stop()
        key = client_key(headers)
        now = time.time()
        fails = recent_fails(key, now)
        if len(fails) >= MAX_FAILS:
            wait_min = int((LOCK_SEC - (now - fails[0])) // 60) + 1
            st.error(f"PIN을 {MAX_FAILS}번 틀려 잠겼습니다. 약 {wait_min}분 뒤에 다시 시도해 주세요.")
            st.stop()
        st.caption("점주 전용입니다. 이용 코드(PIN)를 입력하세요. 코드 문의: kimtyy@gmail.com" if store else "대시보드를 열려면 PIN을 입력하세요.")
        with st.form("pin_form", clear_on_submit=True):
            entered = st.text_input("PIN", type="password", max_chars=16, key="pin_input", label_visibility="collapsed",
                                    placeholder="PIN 입력")
            submitted = st.form_submit_button("열기", type="primary", use_container_width=True)
        if submitted:
            if hmac.compare_digest(entered.strip().encode(), pin.encode()):
                st.session_state.pin_ok = True
                st.rerun()
            fails.append(now)
            left = MAX_FAILS - len(fails)
            st.error("PIN이 맞지 않습니다." + (f" (남은 시도 {left}번)" if left > 0 else f" {LOCK_SEC // 60}분 동안 잠깁니다."))
    st.stop()
