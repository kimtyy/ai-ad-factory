"""구글 캘린더 스케줄러: 일정 제목·설명란 → 자동 제작 → 비공개 업로드/첫 댓글 대기열 → 설명란 결과 기록 (가짜 캘린더·유튜브)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402
import datetime as dt
import json
import os
import shutil

from core import calendar_sync as cs
from core import scheduler as sch

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


work = tmp()
inbox = os.path.join(work, "inbox")
os.makedirs(os.path.join(inbox, "자취템"))
os.makedirs(os.path.join(inbox, "윤동주"))
for i, src in enumerate([fixture("photo_room.jpg"), fixture("photo_pool.jpg"), fixture("photo_room.jpg")]):
    shutil.copy(src, os.path.join(inbox, "자취템", f"{i + 1:02d}.jpg"))
for i, src in enumerate([fixture("photo_room.jpg"), fixture("photo_pool.jpg")]):
    shutil.copy(src, os.path.join(inbox, "윤동주", f"{i + 1:02d}.jpg"))
_env = {k: os.environ.get(k) for k in ("CALENDAR_INBOX", "CALENDAR_MAX_PER_DAY")}
os.environ["CALENDAR_INBOX"] = inbox
os.environ["CALENDAR_MAX_PER_DAY"] = "2"
_state_dir = sch.STATE_DIR
sch.STATE_DIR = os.path.join(work, "scheduler")

# ---------------------------------------------------------------- 1. 설명란 양식
desc = """<b>구성: 묶음</b><br>주제: 자취생 삶의 질<br>상품1: 미니 무선 청소기 | 무선, 500g | https://link.coupang.com/a/aaa<br>
상품2: LED 무드등 | 3단 밝기 | https://smartstore.naver.com/x/products/1<br>상품3: 접이식 건조대 | 접이식 | https://link.coupang.com/a/ccc<br>
사진폴더: 자취템"""
f = sch.parse_fields(desc)
check(f["구성"] == "묶음" and f["상품2"].startswith("LED 무드등 | 3단 밝기") and f["사진폴더"] == "자취템",
      "설명란 해석: 구글 캘린더 HTML(<br>·<b>)도 '키: 값' 줄로")
multi = sch.parse_fields("책: 하늘과 바람과 별과 시\n문장: 죽는 날까지 하늘을 우러러\n한 점 부끄럼이 없기를,\n템플릿: 우화\n"
                         + sch.RESULT_MARK + " (예전) ────\n✅ 예전 결과")
check(multi["문장"] == "죽는 날까지 하늘을 우러러 한 점 부끄럼이 없기를," and multi["템플릿"] == "우화" and "✅" not in json.dumps(multi),
      "여러 줄 원문은 이어 붙이고, 이전 결과 블록은 입력에서 제외")
spec = sch.spec_from_event({"mode": "commerce", "keyword": "자취 주방템"}, f, None, mock=True)
check(spec.is_bundle and [it["link"] for it in spec.bundle_items][1].startswith("https://smartstore") and len(spec.media_paths) == 3
      and spec.bundle_theme == "자취생 삶의 질", "쇼핑 묶음 JobSpec: 상품 3개(이름|특징|링크) + inbox 사진 (파일 이름 순)")
try:
    sch.spec_from_event({"mode": "commerce", "keyword": "주방템"}, {"사진폴더": "없는폴더"}, None)
    check(False, "빈 입력")
except sch.SpecError as e:
    check("상품:" in str(e) and "링크:" in str(e) and "inbox/없는폴더" in str(e), f"빠진 항목을 한꺼번에 안내 ({e})")
try:
    sch.spec_from_event({"mode": "travel", "keyword": "강릉"}, {}, None, mock=True)
    check(False, "숙소")
except sch.SpecError as e:
    check("대시보드" in str(e), "숙소 모드는 캘린더 자동 제작 미지원 안내")
bs = sch.spec_from_event({"mode": "book", "keyword": "윤동주"}, {"책": "x", "템플릿": "우화", "자동수집": "끔"}, None, mock=True)
check(bs.book_template == "basic" and any("기본 큐레이션" in n_ for n_ in bs.notes),
      "우화 템플릿인데 원문이 없으면 기본 큐레이션으로 자동 전환 (원문은 지어내지 않음) + 결과에 안내")
bs2 = sch.spec_from_event({"mode": "book", "keyword": "윤동주"}, {"책": "x", "문장": "원문", "자동수집": "끔"}, None, mock=True)
check(bs2.book_template == "fable", "템플릿을 안 적어도 원문이 있으면 우화")
check(sch._photos("../../") == [] and sch._photos("") == [], "사진폴더는 inbox 밖으로 못 나감")


# ---------------------------------------------------------------- 2. 가짜 캘린더
class Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


now = dt.datetime(2026, 10, 12, 7, 0, tzinfo=dt.timezone.utc)


def ev(i, title, minutes_ago, description=""):
    return {"id": f"E{i}", "summary": title, "description": description,
            "start": {"dateTime": (now - dt.timedelta(minutes=minutes_ago)).isoformat()}}


class Cal:
    def __init__(self, events):
        self.store = {e["id"]: e for e in events}
        self.patches = []

    def events(self):
        cal = self

        class E:
            def list(self_, **k):
                return Req(lambda: {"items": sorted(cal.store.values(), key=lambda e: e["start"]["dateTime"])})

            def patch(self_, calendarId, eventId, body):
                def go():
                    cal.patches.append((eventId, body))
                    e = cal.store[eventId]
                    if "description" in body:
                        e["description"] = body["description"]
                    e.setdefault("extendedProperties", {}).setdefault("private", {}).update(
                        body.get("extendedProperties", {}).get("private", {}))
                    return e
                return Req(go)
        return E()


book_desc = ("자동수집: 끔\n책: 하늘과 바람과 별과 시\n저자: 윤동주\n소개: 윤동주 유고 시집\n문장: 죽는 날까지 하늘을 우러러 한 점 부끄럼이 없기를,\n"
             "링크: https://www.aladin.co.kr/shop/wproduct.aspx?ItemId=1\n사진폴더: 윤동주")
cal = Cal([ev(1, "[쇼핑] 자취 주방템", 30, desc), ev(2, "[시골책방] 윤동주 시집", 20, book_desc), ev(3, "팀 회의", 10),
           ev(4, "[쇼핑] 미래 일정", -60, desc), ev(5, "[쇼핑] 너무 오래된 일정", 60 * 30, desc), ev(6, "[쇼핑] 빈 일정", 5, "")])
due = sch.due_events(list(cal.store.values()), now, {"events": {}, "runs": []})
check([e["id"] for e, _ in due] == ["E1", "E2", "E6"], "처리 대상: 시작 시간 지난 [모드명] 일정만 (미래·24시간 넘게 지난·일반 일정 제외)")

stale = ev(7, "[쇼핑] 멈춘 일정", 300, desc)
stale["extendedProperties"] = {"private": {"adf_status": "running", "adf_started": (now - dt.timedelta(hours=3)).isoformat()}}
fresh = ev(8, "[쇼핑] 진행 중 일정", 300, desc)
fresh["extendedProperties"] = {"private": {"adf_status": "running", "adf_started": (now - dt.timedelta(minutes=10)).isoformat()}}
check([e["id"] for e, _ in sch.due_events([stale, fresh], now, {"events": {}, "runs": []})] == ["E7"],
      "'진행 중'이 2시간 넘게 멈춘 일정(실행 중 PC 꺼짐)은 다시 처리, 진행 중인 건 건너뜀")
_saved = cs.saved_calendar
cs.saved_calendar = lambda: {"id": "adf@group", "summary": "AI 광고 공장"}
import core.youtube_upload as yu  # noqa: E402
_up, _pend, _drafts = yu.upload_short, yu.post_pending_comments, yu.DRAFTS_PATH
uploads = []
yu.upload_short = lambda video, title, desc_, **kw: (uploads.append({"video": video, "title": title, "desc": desc_, **kw}), {
    "video_id": "VID1", "url": "https://youtube.com/shorts/VID1", "studio_url": "https://studio.youtube.com/video/VID1/edit",
    "channel": "쇼핑 전용", "thumbnail_ok": True, "comment_ok": False, "comment_pending": True, "warnings": []})[1]
yu.post_pending_comments = lambda: [{"video_id": "OLD", "state": "posted", "comment_url": "", "warning": ""}]
yu.DRAFTS_PATH = os.path.join(work, "drafts.json")
jobs = []
try:
    # ---------------------------------------------------------------- 3. 실행: 쇼핑 묶음 → 비공개 업로드 + 결과 기록
    rep = sch.run_once(now, service=cal, mock=True, busy_check=lambda: False)
    r = rep.processed[0]
    jobs.append(os.path.join(ROOT, "outputs", "jobs", r.get("job_id", "x")))
    e1 = cal.store["E1"]
    check(r["status"] == "done" and os.path.exists(r["final"]) and len(rep.processed) == 1 and rep.pending_comments[0]["state"] == "posted",
          "한 번에 1건: 가장 오래된 일정부터 기획 → 렌더 (대기 첫 댓글도 함께 확인)")
    u = uploads[0]
    check(u["privacy"] == "private" and u["mode"] == "commerce" and "1️⃣" in u["comment"] and "쿠팡 파트너스 활동의 일환" in u["comment"],
          "비공개 업로드 + 첫 댓글(구매 좌표·제휴 고지) 전달")
    check(sch.RESULT_MARK in e1["description"] and "https://youtube.com/shorts/VID1" in e1["description"]
          and "공개(또는 일부 공개)로 바꾸면 자동 등록" in e1["description"] and "1️⃣ 미니 무선 청소기" in e1["description"]
          and e1["description"].startswith("구성: 묶음") and e1["extendedProperties"]["private"]["adf_status"] == "done"
          and e1["extendedProperties"]["private"]["adf_video"] == "VID1",
          "설명란: 원래 입력 + 결과 블록(링크·첫 댓글 전문), 숨은 속성에 처리 완료")
    check([p[1].get("extendedProperties", {}).get("private", {}).get("adf_status") for p in cal.patches if p[0] == "E1"][:1] == ["running"],
          "제작 시작 전에 '진행 중' 표시 (겹쳐 실행 방지)")

    # ---------------------------------------------------------------- 4. 시골책방 → 채널 미연결: 렌더 + 첫 댓글 초안
    rep2 = sch.run_once(now, service=cal, mock=True, busy_check=lambda: False)
    r2 = rep2.processed[0]
    jobs.append(os.path.join(ROOT, "outputs", "jobs", r2.get("job_id", "x")))
    e2 = cal.store["E2"]
    drafts = json.load(open(yu.DRAFTS_PATH, encoding="utf-8"))
    check(r2["status"] == "done" and len(uploads) == 1 and "채널 미연결" in e2["description"]
          and "알라딘 제휴 마케팅 활동의 일환" in e2["description"] and drafts[-1]["job_id"] == r2["job_id"],
          "시골책방: 업로드 안 하고 '채널 미연결' 기록 + 첫 댓글 초안 대기열")

    # ---------------------------------------------------------------- 5. 하루 한도·입력 부족·재실행·렌더 중
    rep3 = sch.run_once(now, service=cal, mock=True, busy_check=lambda: False)
    check(not rep3.processed and "하루 한도 2건" in rep3.skipped, "하루 최대 처리 건수(CALENDAR_MAX_PER_DAY) 지나면 쉼")
    os.environ["CALENDAR_MAX_PER_DAY"] = "5"
    rep4 = sch.run_once(now, service=cal, mock=True, busy_check=lambda: True)
    check(not rep4.processed and "렌더가 진행 중" in rep4.skipped, "대시보드에서 렌더 중이면 다음 주기로")
    rep5 = sch.run_once(now, service=cal, mock=True, busy_check=lambda: False)
    e6 = cal.store["E6"]
    check(rep5.processed[0]["status"] == "failed" and "시작 못 함" in e6["description"] and "상품:" in e6["description"]
          and "재실행: 예" in e6["description"] and e6["extendedProperties"]["private"]["adf_status"] == "failed",
          "입력이 모자라면 만들지 않고 빠진 항목·재실행 방법을 설명란에")
    rep6 = sch.run_once(now, service=cal, mock=True, busy_check=lambda: False)
    check(not rep6.processed and rep6.skipped == "처리할 일정 없음", "처리한 일정은 다시 돌지 않음")
    cal.store["E6"]["description"] = desc + "\n재실행: 예\n\n" + cal.store["E6"]["description"].split(sch.RESULT_MARK)[0] + sch.RESULT_MARK + " (전) ────\n❌ 예전"
    rep7 = sch.run_once(now, service=cal, mock=True, busy_check=lambda: False, upload=False)
    r7 = rep7.processed[0]
    jobs.append(os.path.join(ROOT, "outputs", "jobs", r7.get("job_id", "x")))
    e6 = cal.store["E6"]
    check(r7["status"] == "done" and "재실행" not in e6["description"].split(sch.RESULT_MARK)[0] and e6["description"].count(sch.RESULT_MARK) == 1
          and "업로드 안 함" in e6["description"], "'재실행: 예' → 다시 제작, 그 줄과 이전 결과는 지우고 새 결과만")
    st = json.load(open(os.path.join(sch.STATE_DIR, "state.json"), encoding="utf-8"))
    check(st["events"]["E1"]["status"] == "done" and len(st["runs"]) == 4, "로컬 상태 기록 (캘린더 숨은 속성과 이중으로)")
finally:
    yu.upload_short, yu.post_pending_comments, yu.DRAFTS_PATH = _up, _pend, _drafts
    cs.saved_calendar = _saved
    sch.STATE_DIR = _state_dir
    for k, v in _env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for j in jobs:
        shutil.rmtree(j, ignore_errors=True)
finish(fails)
