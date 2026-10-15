"""
scripts/railway_scheduler.py - Railway(리눅스 컨테이너)용 상시 스케줄러 (PC의 작업 스케줄러 대신)

  - 캘린더 자동 제작: 10분마다 scripts/calendar_runner.py   (.env SCHED_CALENDAR=0 이면 끔)
  - 떡상 레이더 + 대기 첫 댓글: 매일 07:00 KST                (.env SCHED_RADAR=0 이면 끔, 시각은 RADAR_AT=07:00)
한 번에 하나씩 순서대로 실행 (렌더가 겹치지 않게). 상태: outputs/scheduler/railway_sched.json, 로그: outputs/scheduler_loop.log
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = dt.timezone(dt.timedelta(hours=9))
CAL_EVERY_SEC = 600
TICK_SEC = 30
STATE = os.path.join(ROOT, "outputs", "scheduler", "railway_sched.json")
RADAR_STEPS = [
    [sys.executable, "-m", "core.radar.run", "--vertical", "all", "--hooks", "5", "--budget", "4000"],
    [sys.executable, "-m", "core.radar.trends", "--vertical", "all"],
    [sys.executable, "scripts/post_pending_comments.py"],
]


def _on(name: str) -> bool:
    return os.getenv(name, "1").strip() not in ("0", "false", "off", "끔")


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def radar_due(now: dt.datetime, st: dict, at: str = "07:00") -> bool:
    """오늘(KST) 지정 시각이 지났고 아직 안 돌았으면 True (컨테이너가 07시에 꺼져 있었어도 켜진 뒤 한 번)"""
    hh, mm = (int(x) for x in at.split(":"))
    local = now.astimezone(KST)
    return local.time() >= dt.time(hh, mm) and st.get("radar_day") != local.strftime("%Y-%m-%d")


def calendar_due(now: dt.datetime, st: dict) -> bool:
    last = st.get("calendar_at")
    return not last or (now - dt.datetime.fromisoformat(last)).total_seconds() >= CAL_EVERY_SEC


def run(cmd, timeout: int) -> int:
    t0 = time.time()
    try:
        rc = subprocess.run(cmd, cwd=ROOT, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        rc = -1
    print(f"{dt.datetime.now(KST):%Y-%m-%d %H:%M:%S} {' '.join(cmd[1:3])} → 종료 코드 {rc} ({time.time() - t0:.0f}초)", flush=True)
    return rc


def tick(now: dt.datetime, st: dict, runner=run) -> dict:
    if _on("SCHED_CALENDAR") and calendar_due(now, st):
        st["calendar_at"] = now.isoformat()
        save_state(st)
        runner([sys.executable, "scripts/calendar_runner.py"], 2 * 3600)
    if _on("SCHED_RADAR") and radar_due(now, st, os.getenv("RADAR_AT", "07:00")):
        st["radar_day"] = now.astimezone(KST).strftime("%Y-%m-%d")
        save_state(st)
        for cmd in RADAR_STEPS:
            runner(cmd, 3600)
    return st


def main() -> None:
    print(f"{dt.datetime.now(KST):%Y-%m-%d %H:%M:%S} 스케줄러 시작 (캘린더 {'켬' if _on('SCHED_CALENDAR') else '끔'}, "
          f"레이더 {'켬' if _on('SCHED_RADAR') else '끔'} {os.getenv('RADAR_AT', '07:00')})", flush=True)
    time.sleep(60)                     # 대시보드가 먼저 뜨도록
    st = load_state()
    while True:
        try:
            st = tick(dt.datetime.now(dt.timezone.utc), st)
        except Exception as e:         # 스케줄러는 죽지 않고 다음 주기에 다시
            print(f"스케줄러 오류: {e}", flush=True)
        time.sleep(TICK_SEC)


if __name__ == "__main__":
    main()
