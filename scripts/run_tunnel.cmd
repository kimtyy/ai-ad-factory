@echo off
rem 모바일 접속용 Cloudflare 임시 터널 (주소는 로그에서 watchdog이 읽어 outputs\tunnel_url.txt에 기록)
set CF=C:\Program Files (x86)\cloudflared\cloudflared.exe
if not exist "%CF%" set CF=cloudflared
"%CF%" tunnel --no-autoupdate --url http://localhost:8501 2>"%TEMP%\cloudflared_8501.log"
