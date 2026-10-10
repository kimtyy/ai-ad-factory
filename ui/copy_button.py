"""
ui/copy_button.py - 원클릭 복사 버튼 (모바일 포함)

st.code의 복사 아이콘은 마우스를 올려야 보여 휴대폰에서 찾기 어려움 → 눈에 보이는 버튼.
navigator.clipboard(HTTPS·localhost)를 먼저 쓰고, 안 되면 숨긴 textarea + execCommand('copy')로 복사
"""
import html
import json

import streamlit.components.v1 as components


def copy_button(text: str, label: str = "📋 복사", key: str = "copy") -> None:
    payload = json.dumps(text or "")
    btn_id = "cp_" + "".join(ch for ch in key if ch.isalnum() or ch == "_")
    components.html(f"""
<button id="{btn_id}" style="width:100%;padding:10px 12px;border-radius:8px;border:1px solid #d0d0d0;background:#fff;
  font-size:15px;font-weight:600;cursor:pointer;font-family:inherit">{html.escape(label)}</button>
<script>
const b = document.getElementById("{btn_id}");
const text = {payload};
function done(ok) {{
  const old = {json.dumps(label)};
  b.textContent = ok ? "✅ 복사했어요" : "길게 눌러 직접 복사해 주세요";
  setTimeout(() => b.textContent = old, 1800);
}}
function fallback() {{
  const t = document.createElement("textarea");
  t.value = text; t.style.position = "fixed"; t.style.opacity = "0";
  document.body.appendChild(t); t.focus(); t.select();
  let ok = false; try {{ ok = document.execCommand("copy"); }} catch (e) {{}}
  document.body.removeChild(t); done(ok);
}}
b.addEventListener("click", () => {{
  if (navigator.clipboard && window.isSecureContext) {{
    navigator.clipboard.writeText(text).then(() => done(true), fallback);
  }} else {{ fallback(); }}
}});
</script>""", height=52)
