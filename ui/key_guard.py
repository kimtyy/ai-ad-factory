"""
ui/key_guard.py - 브라우저 콘솔 "Cannot read properties of undefined (reading 'toLowerCase')" 차단

원인: Streamlit 프런트엔드의 전역 단축키 컴포넌트(document keydown/keyup 리스너)가
`event.key.toLowerCase()`를 호출하는데, 크롬 자동완성·비밀번호 관리자·확장 프로그램·한글 IME가
`key`가 없는 KeyboardEvent를 발생시키면 undefined.toLowerCase()로 터진다 (Python 위젯 옵션과 무관).
대책: window 캡처 단계에서 key가 문자열이 아닌 키보드 이벤트를 Streamlit 리스너에 닿기 전에 삼킨다.
"""
import streamlit.components.v1 as components

_GUARD_JS = """
<script>
(function () {
  try {
    var w = window.parent;
    if (!w || w.__keyGuardInstalled) return;
    w.__keyGuardInstalled = true;
    ["keydown", "keyup", "keypress"].forEach(function (t) {
      w.addEventListener(t, function (e) {
        if (typeof e.key !== "string") { e.stopImmediatePropagation(); }
      }, true);
    });
  } catch (err) {}
})();
</script>
"""


def install_key_guard() -> None:
    components.html(_GUARD_JS, height=0)
