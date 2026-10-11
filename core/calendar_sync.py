"""
core/calendar_sync.py - 'AI 광고 공장' 전용 구글 캘린더 연동 (일정 → 자동 제작 → 결과를 일정 설명란에 기록)

- 인증: PC에서 한 번 `python scripts/calendar_auth.py` (유튜브와 같은 OAuth 클라이언트 secrets/client_secret.json)
  → secrets/token_calendar.json + secrets/calendar.json(찾은 전용 캘린더 id). secrets/는 git 제외
- 권한은 두 가지만: 캘린더 목록 읽기(전용 캘린더 찾기) + 일정 읽기·쓰기(결과 기록). 다른 캘린더 일정은 읽지 않음
- 전용 캘린더 이름: .env CALENDAR_NAME (기본 'AI 광고 공장'), 또는 CALENDAR_ID로 직접 지정
"""
import datetime as dt
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ad_factory.calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar.calendarlist.readonly",
          "https://www.googleapis.com/auth/calendar.events"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS_DIR = os.path.join(ROOT, "secrets")
DEFAULT_CALENDAR_NAME = "AI 광고 공장"

# 일정 제목 [모드명] 키워드 → 제작 모드
MODE_TAGS = {"쇼핑": "commerce", "시골책방": "book", "책방": "book", "사장님": "owner", "샵스토리": "owner", "숙소": "travel"}
TITLE_RE = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*(.+?)\s*$")


class CalendarError(RuntimeError):
    pass


def token_path() -> str:
    return os.path.join(SECRETS_DIR, "token_calendar.json")


def calendar_info_path() -> str:
    return os.path.join(SECRETS_DIR, "calendar.json")


def calendar_name() -> str:
    return os.getenv("CALENDAR_NAME", "").strip() or DEFAULT_CALENDAR_NAME


def client_secrets_path() -> str:
    return os.getenv("YOUTUBE_CLIENT_SECRETS", "").strip() or os.path.join(SECRETS_DIR, "client_secret.json")


def parse_title(title: str) -> Optional[Dict[str, str]]:
    """'[쇼핑] 자취 주방템' → {mode: commerce, label: 쇼핑, keyword: 자취 주방템}. 형식이 아니거나 모르는 모드면 None"""
    m = TITLE_RE.match(title or "")
    if not m:
        return None
    label, keyword = m.group(1), m.group(2)
    mode = MODE_TAGS.get(label.replace(" ", ""))
    return {"mode": mode, "label": label, "keyword": keyword} if mode else None


# 테스트에서 가짜 서비스로 바꿔 끼우는 지점
_service_factory: Optional[Callable[[], Any]] = None


def load_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    if not os.path.exists(token_path()):
        raise CalendarError("구글 캘린더가 연결되지 않았어요. PC에서 `python scripts/calendar_auth.py`를 한 번 실행해 주세요.")
    creds = Credentials.from_authorized_user_file(token_path(), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path(), "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        else:
            raise CalendarError("구글 캘린더 연결이 만료됐어요. `python scripts/calendar_auth.py`를 다시 실행해 주세요.")
    return creds


def build_service(creds=None):
    if _service_factory is not None:
        return _service_factory()
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=creds or load_credentials(), cache_discovery=False)


def find_calendar(service, name: Optional[str] = None) -> Dict[str, str]:
    """캘린더 목록에서 이름이 같은 캘린더 (.env CALENDAR_ID가 있으면 그것). 없으면 CalendarError"""
    want_id = os.getenv("CALENDAR_ID", "").strip()
    name = (name or calendar_name()).strip()
    items, token = [], None
    while True:
        res = service.calendarList().list(pageToken=token, minAccessRole="writer").execute()
        items += res.get("items") or []
        token = res.get("nextPageToken")
        if not token:
            break
    for c in items:
        if (want_id and c.get("id") == want_id) or (not want_id and (c.get("summary") or "").strip() == name):
            return {"id": c["id"], "summary": c.get("summary", ""), "timeZone": c.get("timeZone", "")}
    names = ", ".join(f"'{c.get('summary')}'" for c in items[:10])
    raise CalendarError(f"'{want_id or name}' 캘린더를 찾지 못했어요 (쓰기 권한이 있는 캘린더: {names}). "
                        "구글 캘린더에서 이름을 확인하거나 .env에 CALENDAR_NAME / CALENDAR_ID를 적어 주세요.")


def authorize_interactive() -> Dict[str, Any]:
    """PC 브라우저로 OAuth 동의 → 전용 캘린더 확인 → 토큰·캘린더 id 저장. 권한 일부를 빼고 허용했으면 저장하지 않음"""
    from google_auth_oauthlib.flow import InstalledAppFlow
    secrets = client_secrets_path()
    if not os.path.exists(secrets):
        raise CalendarError(f"OAuth 클라이언트 파일이 없어요: {secrets}")
    flow = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent select_account", authorization_prompt_message="")
    granted = set(getattr(creds, "granted_scopes", None) or getattr(creds, "scopes", None) or SCOPES)
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        raise CalendarError("동의 화면에서 일부 권한이 빠졌어요 (" + ", ".join(s.rsplit("/", 1)[1] for s in missing)
                            + "). 다시 실행해 두 권한을 모두 허용해 주세요.")
    os.makedirs(SECRETS_DIR, exist_ok=True)
    with open(token_path(), "w", encoding="utf-8") as f:      # 먼저 저장: 캘린더 확인이 실패해도 --check로 브라우저 없이 재시도
        f.write(creds.to_json())
    cal = find_calendar(build_service(creds))
    with open(calendar_info_path(), "w", encoding="utf-8") as f:
        json.dump(cal, f, ensure_ascii=False, indent=2)
    return cal


def saved_calendar() -> Dict[str, str]:
    try:
        with open(calendar_info_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        raise CalendarError("전용 캘린더 정보가 없어요. `python scripts/calendar_auth.py`를 실행해 주세요.")


def list_events(service, calendar_id: str, time_min: dt.datetime, time_max: dt.datetime) -> List[Dict[str, Any]]:
    """기간 안의 일정 (반복 일정은 하나씩 펼침, 시작 시간순)"""
    out, token = [], None
    while True:
        res = service.events().list(calendarId=calendar_id, timeMin=time_min.isoformat(), timeMax=time_max.isoformat(),
                                    singleEvents=True, orderBy="startTime", pageToken=token, maxResults=250).execute()
        out += res.get("items") or []
        token = res.get("nextPageToken")
        if not token:
            return out
