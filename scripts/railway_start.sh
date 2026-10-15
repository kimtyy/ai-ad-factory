#!/bin/sh
# Railway 시작 스크립트: 볼륨(/data)에 영구 데이터 연결 → 스케줄러(백그라운드) → 대시보드
set -e
cd /app
DATA="${DATA_DIR:-/data}"

# 1) 영구 데이터는 볼륨에 (컨테이너가 다시 떠도 유지): 작업물·레이더 DB·토큰·BGM·글꼴·캘린더 사진
for d in outputs secrets inbox fonts assets/bgm; do
  mkdir -p "$DATA/$d"
  if [ -e "/app/$d" ] && [ ! -L "/app/$d" ]; then
    cp -rn "/app/$d/." "$DATA/$d/" 2>/dev/null || true      # 이미지에 들어 있던 안내 파일(README 등)만 옮김
    rm -rf "/app/$d"
  fi
  mkdir -p "$(dirname "/app/$d")"
  ln -sfn "$DATA/$d" "/app/$d"
done

# 2) 스케줄러: 캘린더 10분마다 + 레이더 매일 07:00 (SCHED_CALENDAR=0 / SCHED_RADAR=0 으로 끄기)
python scripts/railway_scheduler.py >> "$DATA/outputs/scheduler_loop.log" 2>&1 &

# 3) 대시보드 (외부 접속은 항상 PIN: ui/auth.py, DASHBOARD_PIN 필수)
exec streamlit run app.py \
  --server.port "${PORT:-8501}" --server.address 0.0.0.0 \
  --server.headless true --browser.gatherUsageStats false
