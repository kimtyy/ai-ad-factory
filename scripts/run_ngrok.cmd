@echo off
rem 모바일 접속용 ngrok 고정 주소 터널 (watchdog이 .env의 NGROK_AUTHTOKEN / NGROK_DOMAIN을 환경변수로 넘겨 실행)
rem 주소가 바뀌지 않음: https://%NGROK_DOMAIN%
if "%NGROK_DOMAIN%"=="" exit /b 1
set NG=%NGROK_EXE%
if "%NG%"=="" set NG=ngrok
"%NG%" http 8501 --url=https://%NGROK_DOMAIN% --log=stdout --log-format=logfmt > "%TEMP%\ngrok_8501.log" 2>&1
