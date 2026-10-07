@echo off
rem AI 광고 공장 대시보드 (독립 콘솔: 감시·도구 창이 닫혀도 유지). 로그: %TEMP%\streamlit_8501.log/.err
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
python -m streamlit run app.py --server.port 8501 --server.headless true 1>>"%TEMP%\streamlit_8501.log" 2>>"%TEMP%\streamlit_8501.err"
