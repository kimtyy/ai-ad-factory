"""공용 제작 엔진(core/pipeline.py)을 화면 없이 직접 실행 + 구글 캘린더 연동 기초(core/calendar_sync.py) - 스케줄러의 바탕"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import json
import os
import shutil

from core import pipeline
from core.job import JobContext
from core.shorts_meta import ALADIN_DISCLOSURE_FULL, COUPANG_DISCLOSURE_FULL

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


work = tmp()
P1, P2 = fixture("photo_room.jpg"), fixture("photo_pool.jpg")
ups = [shutil.copy(P1, os.path.join(work, "a.jpg")), shutil.copy(P2, os.path.join(work, "b.jpg")),
       shutil.copy(P1, os.path.join(work, "c.jpg"))]
jobs = []
import core.youtube_upload as yu  # noqa: E402
_drafts = yu.DRAFTS_PATH
yu.DRAFTS_PATH = os.path.join(work, "drafts.json")
try:
    # ---------------------------------------------------------------- 1. 쇼핑 묶음: 화면 없이 기획 → 렌더 → 업로드 문구
    items = [{"name": "미니 무선 핸디청소기", "usp": "무선", "link": "https://link.coupang.com/a/aaa"},
             {"name": "LED 무드등", "usp": "3단 밝기", "link": "https://smartstore.naver.com/x/products/1"},
             {"name": "접이식 빨래 건조대", "usp": "접이식", "link": "https://link.coupang.com/a/ccc"}]
    spec = pipeline.JobSpec(mode="commerce", layout="bundle", name="자취생 삶의 질", bundle_theme="자취생 삶의 질",
                            bundle_items=items, media_paths=ups, mock=True)
    job = JobContext.create(app="test")
    jobs.append(job.workdir)
    scenes = pipeline.plan(spec, job)
    check(len(scenes) == 8 and not pipeline.missing_media(scenes) and scenes[3]["image_path"] == ups[1],
          "엔진 기획: 묶음 8씬 + 상품별 사진 배정 (화면 없이)")
    steps = []
    res = pipeline.render(spec, job, scenes, progress=lambda pct, t: steps.append(pct))
    check(os.path.exists(res.final_path) and res.thumb_path and os.path.exists(res.thumb_path) and steps[-1] == 100
          and res.shorts_copy and res.shorts_copy["title"].endswith("#Shorts"), "엔진 렌더: 최종 영상·썸네일·업로드 카피·진행률")
    tx = pipeline.upload_texts(spec, res.shorts_copy)
    check("1️⃣ 미니 무선 핸디청소기 👉 https://link.coupang.com/a/aaa" in tx["comment"] and COUPANG_DISCLOSURE_FULL in tx["comment"]
          and "스마트스토어 제휴" in tx["comment"] and "첫 번째 댓글" in tx["desc"] and tx["title"] == res.shorts_copy["title"],
          "업로드 문구: 설명란(첫 댓글 안내) + 첫 댓글(상품별 구매 좌표·링크별 고지)")

    # ---------------------------------------------------------------- 2. 시골책방: 렌더 → 첫 댓글 대기열 (채널 미연결)
    bspec = pipeline.JobSpec(mode="book", name="시골책방", book_title="하늘과 바람과 별과 시", book_author="윤동주",
                             usp="윤동주 유고 시집", book_quote="죽는 날까지 하늘을 우러러 한 점 부끄럼이 없기를,",
                             book_link="https://www.aladin.co.kr/shop/wproduct.aspx?ItemId=1", media_paths=ups, mock=True)
    bjob = JobContext.create(app="test")
    jobs.append(bjob.workdir)
    bscenes = pipeline.plan(bspec, bjob)
    bres = pipeline.render(bspec, bjob, bscenes)
    drafts = json.load(open(yu.DRAFTS_PATH, encoding="utf-8"))
    check(os.path.exists(bres.final_path) and ALADIN_DISCLOSURE_FULL in bres.comment and drafts[-1]["text"] == bres.comment
          and drafts[-1]["job_id"] == bjob.job_id, "시골책방 엔진 렌더 → 첫 댓글(알라딘 고지) 대기열 자동 저장")
    check(pipeline.disclosure_short(bspec).startswith("알라딘 제휴") and pipeline.disclosure_short(spec).startswith("쿠팡 파트너스·제휴")
          and pipeline.disclosure_short(pipeline.JobSpec(mode="owner")) is None, "모드별 화면 제휴 표시 (사장님은 없음)")
    try:
        pipeline.plan(pipeline.JobSpec(mode="travel", name="x"), bjob)
        check(False, "travel")
    except ValueError as e:
        check("화면에서만" in str(e), "숙소 기획은 화면 전용 (엔진은 렌더만)")
    try:
        pipeline.plan(pipeline.JobSpec(mode="owner", name=" "), bjob)
        check(False, "상호명 없음")
    except ValueError as e:
        check("상호명" in str(e), "입력이 모자라면 지어내지 않고 오류")
finally:
    yu.DRAFTS_PATH = _drafts
    for j in jobs:
        shutil.rmtree(j, ignore_errors=True)

# ---------------------------------------------------------------- 3. 구글 캘린더 연동 기초
from core import calendar_sync as cs  # noqa: E402
check(cs.parse_title("[쇼핑] 자취 주방템") == {"mode": "commerce", "label": "쇼핑", "keyword": "자취 주방템"}
      and cs.parse_title(" [시골책방]  윤동주 시집 ")["mode"] == "book" and cs.parse_title("[사장님] 설맥 가평")["mode"] == "owner"
      and cs.parse_title("[숙소] 강릉 오션뷰")["mode"] == "travel" and cs.parse_title("회의") is None
      and cs.parse_title("[운동] 헬스") is None, "일정 제목 [모드명] 키워드 해석 (모르는 모드·형식은 건너뜀)")


class Req:
    def __init__(self, r):
        self.r = r

    def execute(self):
        return self.r


class FakeCal:
    def __init__(self, items):
        self.items = items

    def calendarList(self):
        return type("X", (), {"list": lambda s, **k: Req({"items": self.items})})()


_env = {k: os.environ.get(k) for k in ("CALENDAR_ID", "CALENDAR_NAME")}
os.environ.pop("CALENDAR_ID", None)
os.environ.pop("CALENDAR_NAME", None)
cal = cs.find_calendar(FakeCal([{"id": "me@x", "summary": "개인"}, {"id": "adf@group", "summary": "AI 광고 공장", "timeZone": "Asia/Seoul"}]))
check(cal == {"id": "adf@group", "summary": "AI 광고 공장", "timeZone": "Asia/Seoul"}, "전용 캘린더를 이름으로 찾음 (개인 캘린더는 안 씀)")
try:
    cs.find_calendar(FakeCal([{"id": "me@x", "summary": "개인"}]))
    check(False, "없음")
except cs.CalendarError as e:
    check("AI 광고 공장" in str(e) and "개인" in str(e), "전용 캘린더가 없으면 있는 캘린더 이름을 보여 주며 안내")
os.environ["CALENDAR_ID"] = "me@x"
check(cs.find_calendar(FakeCal([{"id": "me@x", "summary": "개인"}]))["id"] == "me@x", ".env CALENDAR_ID로 직접 지정 가능")
for k, v in _env.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v
check(cs.SCOPES == ["https://www.googleapis.com/auth/calendar.calendarlist.readonly",
                    "https://www.googleapis.com/auth/calendar.events"], "권한은 캘린더 목록 읽기 + 일정 읽기·쓰기 두 가지만")
finish(fails)
