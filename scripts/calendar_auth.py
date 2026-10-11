"""
scripts/calendar_auth.py - 'AI 광고 공장' 전용 구글 캘린더 연결 (이 PC에서 처음 한 번)

  python scripts/calendar_auth.py           # 브라우저 동의 → 전용 캘린더 확인 → 토큰 저장 → 앞으로 7일 일정 미리보기
  python scripts/calendar_auth.py --check   # 저장된 연결로 캘린더·일정만 다시 확인 (브라우저 없음)

1) Google Cloud 콘솔에서 Google Calendar API 사용 설정 (유튜브와 같은 프로젝트, secrets/client_secret.json 재사용)
2) 브라우저가 열리면 전용 캘린더가 있는 구글 계정으로 로그인 → 두 권한(캘린더 목록 보기, 일정 보기·수정) 모두 허용
3) 캘린더 이름이 'AI 광고 공장'이 아니면 .env에 CALENDAR_NAME=이름 (또는 CALENDAR_ID=...)
"""
import argparse
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from core.calendar_sync import (CalendarError, authorize_interactive, build_service, calendar_name, find_calendar,  # noqa: E402
                                list_events, parse_title, save_calendar)


def preview(cal_id: str) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    events = list_events(build_service(), cal_id, now - dt.timedelta(days=1), now + dt.timedelta(days=7))
    print(f"[일정 미리보기] 어제~7일 뒤 {len(events)}개")
    for e in events[:15]:
        start = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date") or "?"
        p = parse_title(e.get("summary", ""))
        tag = f"→ 모드 {p['mode']} · 키워드 '{p['keyword']}'" if p else "→ (제목이 [모드명] 키워드 형식이 아니라 건너뜀)"
        print(f"  {start}  {e.get('summary', '(제목 없음)')}  {tag}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI 광고 공장 전용 구글 캘린더 연결")
    ap.add_argument("--check", action="store_true", help="브라우저 없이 저장된 연결만 확인")
    args = ap.parse_args(argv)
    try:
        if args.check:
            cal = save_calendar(find_calendar(build_service()))
        else:
            print(f"브라우저에서 구글 계정으로 로그인하고 두 권한을 허용해 주세요 (찾을 캘린더: '{calendar_name()}')")
            cal = authorize_interactive()
        print(f"[연결 완료] 캘린더 '{cal['summary']}' (시간대 {cal.get('timeZone') or '?'})")
        preview(cal["id"])
    except CalendarError as e:
        print(f"[실패] {e}")
        return 1
    except Exception as e:     # 네트워크·API 미사용 설정 등
        msg = str(e)
        if "accessNotConfigured" in msg or "has not been used" in msg or "disabled" in msg:
            import re as _re
            proj = _re.search(r"project[ =:]*(\d{6,})", msg)
            print("[실패] 이 OAuth 클라이언트의 Google Cloud 프로젝트"
                  + (f"(번호 {proj.group(1)})" if proj else "") + "에서 Google Calendar API가 꺼져 있어요. "
                  "방금 켰다면 몇 분 뒤 `python scripts/calendar_auth.py --check`로 다시 확인해 주세요 (토큰은 저장됨). "
                  "계속 같으면 콘솔 상단의 프로젝트가 위 번호와 같은지 확인해 주세요.")
        else:
            print(f"[실패] {msg[:300]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
