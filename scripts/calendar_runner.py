"""
scripts/calendar_runner.py - 구글 캘린더 일정 → 자동 제작 1회 실행 (작업 스케줄러 AdFactoryCalendar가 10분마다)

  python scripts/calendar_runner.py              # 실제 실행 (쇼핑·사장님 비공개 업로드, 시골책방은 첫 댓글 초안)
  python scripts/calendar_runner.py --no-upload  # 렌더까지만 (업로드 안 함) - 첫 시험용
  python scripts/calendar_runner.py --list       # 처리 대상 일정만 보기 (제작 안 함)

로그: outputs/scheduler/runner.log · 상태: outputs/scheduler/state.json · 한 번에 한 실행만 (잠금 파일)
"""
import argparse
import datetime as dt
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from core import calendar_sync as cs  # noqa: E402
from core import scheduler  # noqa: E402

LOCK = os.path.join(scheduler.STATE_DIR, "runner.lock")


def _acquire() -> bool:
    """이전 실행이 아직 렌더 중이면 이번 주기는 건너뜀 (잠금이 2시간 넘었으면 죽은 실행으로 보고 무시)"""
    os.makedirs(scheduler.STATE_DIR, exist_ok=True)
    if os.path.exists(LOCK):
        age = dt.datetime.now().timestamp() - os.path.getmtime(LOCK)
        if age < 2 * 3600:
            return False
    with open(LOCK, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="구글 캘린더 일정 → 자동 제작")
    ap.add_argument("--no-upload", action="store_true", help="렌더까지만 (업로드 안 함)")
    ap.add_argument("--list", action="store_true", help="처리 대상 일정만 출력")
    args = ap.parse_args(argv)
    os.makedirs(scheduler.STATE_DIR, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(os.path.join(scheduler.STATE_DIR, "runner.log"), encoding="utf-8"),
                                  logging.StreamHandler()])
    for n in ("httpx", "google_genai", "googleapiclient.discovery_cache", "streamlit"):
        logging.getLogger(n).setLevel(logging.WARNING)
    now = dt.datetime.now(dt.timezone.utc)
    try:
        if args.list:
            svc, cal = cs.build_service(), cs.saved_calendar()
            events = cs.list_events(svc, cal["id"], now - dt.timedelta(hours=scheduler.LOOKBACK_HOURS), now + dt.timedelta(days=7))
            due = {e["id"] for e, _ in scheduler.due_events(events, now, scheduler.load_state())}
            for e in events:
                t = cs.parse_title(e.get("summary", ""))
                st = "▶ 지금 처리 대상" if e["id"] in due else (scheduler._status(e) or ("예정" if t else "형식 아님"))
                print(f"{(e.get('start') or {}).get('dateTime', '?')}  {e.get('summary')}  [{st}]")
            return 0
        if not _acquire():
            print("이전 실행이 아직 진행 중이라 이번 주기는 건너뜀")
            return 0
        try:
            rep = scheduler.run_once(now, api_key=os.getenv("GEMINI_API_KEY"), upload=not args.no_upload)
        finally:
            os.remove(LOCK)
        for r in rep.pending_comments:
            print(f"대기 첫 댓글 {r['video_id']}: {r['state']}")
        for r in rep.processed:
            print(f"{r['status'].upper()}  {r['title']}  {r.get('final') or r.get('error', '')}")
        if rep.skipped:
            print(rep.skipped)
        # 매 주기 한 줄 요약을 로그에 (작업 스케줄러로 돌 땐 화면 출력이 안 보임)
        logging.getLogger("ad_factory.calendar_runner").info(
            "실행 요약: " + (", ".join(f"{r['status']} {r['title']}" for r in rep.processed) or rep.skipped or "처리 없음")
            + (f" · 대기 첫 댓글 {len(rep.pending_comments)}건 확인" if rep.pending_comments else ""))
        return 1 if any(r["status"] == "failed" for r in rep.processed) else 0
    except cs.CalendarError as e:
        print(f"[캘린더] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
