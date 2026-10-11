@echo off
rem 떡상 레이더 정기 수집 (작업 스케줄러 AdFactoryRadar가 매일 실행): 수집 → 트렌드 다이제스트 → 업로드 카피 생성에 자동 주입
rem 쿼터: 숙소·맛집·범용 키워드 약 17개 x 100 units + 메타데이터 = 하루 약 2,000 units (무료 한도 10,000)
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if not exist "outputs\radar" mkdir "outputs\radar"
echo ==== %date% %time% radar collect ==== >> "outputs\radar\radar_daily.log"
python -m core.radar.run --vertical all --hooks 5 --budget 4000 >> "outputs\radar\radar_daily.log" 2>&1
echo ==== %date% %time% trend digest ==== >> "outputs\radar\radar_daily.log"
python -m core.radar.trends --vertical all >> "outputs\radar\radar_daily.log" 2>&1
echo ==== %date% %time% pending first comments ==== >> "outputs\radar\radar_daily.log"
python scripts\post_pending_comments.py >> "outputs\radar\radar_daily.log" 2>&1
