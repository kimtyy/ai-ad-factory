"""쇼츠 레이더 단위/통합 테스트 (오프라인) + Gemini 후킹 추출 실호출 1건"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.chdir(ROOT)
sys.path.insert(0, ROOT)
import logging
logging.disable(logging.WARNING)

import core.radar.youtube as yt
from core.radar import store
from core.radar.collector import collect
from core.radar.hooks import mock_hook, extract_hook
from core.radar.keywords import keywords_for, VERTICAL_KEYWORDS
from core.radar.scoring import RadarFilters, VideoRecord, parse_iso_duration, rank, score
from core.radar.run import main as radar_main

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


# ---------------------------------------------------------------- keywords
check(keywords_for("stay") == ["숙소 브이로그", "풀빌라 투어", "호텔 룸투어", "숙소 리뷰", "감성숙소 추천", "호캉스", "숙소 추천"],
      "stay 키워드 7개 (리뷰·투어 의도 5 + 대형 트래픽 2)")
from core.radar.keywords import DEFAULT_DAYS
from core.radar.hooks import HOOK_PROMPT, RELEVANCE_CRITERIA
import inspect
import core.radar.run as _run_mod
check(DEFAULT_DAYS == 30 and inspect.signature(_run_mod.execute).parameters["days"].default == 30
      and _run_mod.build_parser().parse_args([]).days == 30,
      "기본 수집 기간 30일 (CLI·execute)")
check("dog-friendly pension" in RELEVANCE_CRITERIA["stay"] and "rather than the place" in HOOK_PROMPT,
      "적합성 기준: 반려견 동반 숙소 리뷰는 통과, 반려동물 중심 클립은 제외")
check(keywords_for("food") == ["맛집 추천", "가성비 맛집", "숨은 맛집", "웨이팅 맛집", "카페 추천"], "food 기본 키워드 5개")

# ---------------------------------------------------------------- scoring
check([parse_iso_duration(x) for x in ("PT45S", "PT1M5S", "PT3M", "PT1H2M3S", "P0D", "")] == [45, 65, 180, 3723, 0, 0], "ISO 8601 길이 파싱")
now = datetime.now(timezone.utc)


def rec(**kw):
    base = dict(video_id="v", title="t", channel_id="c", channel_title="ch", published_at=now - timedelta(days=2),
                duration_sec=30, views=100_000, likes=0, comments=0, subscribers=10_000, subscribers_hidden=False,
                keyword="k", vertical="stay")
    base.update(kw)
    return score(VideoRecord(**base), RadarFilters(), now)


check(rec().reject_reason is None and rec().outlier_ratio == 10.0 and rec().velocity_per_day == 50_000.0, "10배·이틀 → 통과, 배수 10.0x, 일 5만 회")
check("Shorts 아님" in rec(duration_sec=181).reject_reason, "181초 → Shorts 아님")
check("비공개" in rec(subscribers=None, subscribers_hidden=True).reject_reason, "구독자 비공개 → 제외")
check("소형 채널" in rec(subscribers=250, views=50_000).reject_reason, "구독자 250 (배수 200x) → 소형 채널 제외")
check(rec(subscribers=400, views=50_000).reject_reason is None, "구독자 400 (기준 300으로 완화) → 통과")
check("클립성 카테고리 (엔터테인먼트)" in rec(category_id="24").reject_reason
      and "영화/애니메이션" in rec(category_id="1").reject_reason, "엔터테인먼트(24)·영화/애니메이션(1) → 수집 단계 제외")
check(rec(category_id="19").reject_reason is None and rec(category_id="22").reject_reason is None, "여행(19)·인물/블로그(22) → 통과")
check("조회수 부족" in rec(subscribers=2_000, views=20_000).reject_reason, "조회 2만 → 조회수 부족")
check("배수 미달" in rec(views=49_000).reject_reason, "4.9배 → 배수 미달")
r_list = [rec(video_id="a", views=60_000), rec(video_id="b", views=300_000), rec(video_id="c", views=60_000, published_at=now - timedelta(hours=5))]
check([r.video_id for r in rank(r_list)] == ["b", "c", "a"], "정렬: 배수 → 같은 배수면 속도(최근 업로드) 우선")

# ---------------------------------------------------------------- YouTubeClient (가짜 HTTP)
calls = []


class Resp:
    def __init__(self, code, data):
        self.status_code, self._d, self.text = code, data, str(data)

    def json(self):
        return self._d


script = []


def fake_get(url, params=None, timeout=None):
    calls.append((url.rsplit("/", 1)[-1], dict(params)))
    return script.pop(0) if script else Resp(200, {"items": []})


yt.requests.get = fake_get
yt.time.sleep = lambda s: None
c = yt.YouTubeClient("KEY", budget_units=250)
c.search_shorts("호캉스", now - timedelta(days=14))
ep, p = calls[-1]
check(ep == "search" and p["videoDuration"] == "short" and p["type"] == "video" and p["regionCode"] == "KR"
      and p["order"] == "viewCount" and p["publishedAt" if False else "publishedAfter"].endswith("Z") and p["key"] == "KEY",
      f"search 파라미터 (short/KR/viewCount/publishedAfter={p['publishedAfter']})")
calls.clear()
c.videos([f"id{i}" for i in range(120)] + ["id0"])
check([e for e, _ in calls] == ["videos"] * 3 and all(len(pp["id"].split(",")) <= 50 for _, pp in calls),
      "videos: 중복 제거 후 50개씩 3배치 (1 unit씩)")
check(c.used_units == 103, f"쿼터 집계 103 units (search 100 + videos 3) → {c.used_units}")
c.search_shorts("x", now)          # 203
try:
    c.search_shorts("y", now)      # 303 > 250 → 호출 전 차단
    check(False, "예산 초과 차단")
except yt.QuotaBudgetExceeded:
    check(calls[-1][1].get("q") == "x", "예산(250) 초과 search는 HTTP 호출 전에 차단")
script[:] = [Resp(403, {"error": {"errors": [{"reason": "quotaExceeded"}]}})]
try:
    yt.YouTubeClient("KEY").videos(["a"])
    check(False, "403 quotaExceeded")
except yt.QuotaBudgetExceeded:
    check(True, "API 403 quotaExceeded → QuotaBudgetExceeded")
script[:] = [Resp(500, {}), Resp(200, {"items": [{"id": "a"}]})]
check(yt.YouTubeClient("KEY").videos(["a"]) == [{"id": "a"}], "5xx 1회 → 재시도 후 성공")
try:
    yt.YouTubeClient("")
    check(False, "키 없음")
except ValueError:
    check(True, "키 없이 실 클라이언트 생성 불가 (dry-run 안내)")

# ---------------------------------------------------------------- collector (Mock)
mc = yt.MockYouTubeClient(budget_units=3000)
recs, stats = collect(mc, "stay", keywords_for("stay"), days=14)
check(stats["search_calls"] == 7 and len(recs) == len({r.video_id for r in recs}) and mc.used_units == 703,
      f"수집: 검색 7회, 영상 중복 없음 {len(recs)}개, 쿼터 {mc.used_units} units")
check(all(r.reject_reason is None for r in rank(recs)) and all((r.outlier_ratio or 0) >= 5 for r in rank(recs)), "떡상 목록은 모두 5배 이상 + 필터 통과")
mc2 = yt.MockYouTubeClient(budget_units=250)
recs2, stats2 = collect(mc2, "stay", keywords_for("stay"))
check(stats2["searched_keywords"] == 2 and stats2["budget_stopped"] == 1 and mc2.used_units <= 250 and recs2,
      f"예산 250: 키워드 2개만 검색 후 중단, 수집분은 보강까지 완료 ({mc2.used_units} units)")

# ---------------------------------------------------------------- store
db = os.path.join(tempfile.mkdtemp(), "t.db")
conn = store.connect(db)
r1 = store.start_run(conn, "dry-run", "stay", 14, ["a"])
store.save_records(conn, r1, recs)
top = rank(recs)
store.save_hook(conn, top[0].video_id, mock_hook(top[0].title))
store.finish_run(conn, r1, 503, len(recs), len(top))
r2 = store.start_run(conn, "dry-run", "stay", 14, ["a"])
bumped = [VideoRecord(**{**vars(x), "views": x.views * 2}) for x in recs]
store.save_records(conn, r2, [score(b, RadarFilters()) for b in bumped])
n_videos = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
n_snaps = conn.execute("SELECT COUNT(*) FROM snapshots WHERE video_id=?", (top[0].video_id,)).fetchone()[0]
check(n_videos == len(recs) and n_snaps == 2, f"재수집: 영상은 갱신(중복 없음 {n_videos}개), 스냅샷은 실행마다 누적({n_snaps}개)")
rows = store.recent_outliers(conn, "stay", 14, 5.0, 50)
row0 = next(r for r in rows if r["video_id"] == top[0].video_id)
check(row0["views"] == top[0].views * 2 and row0["hook_type"], "조회 시 최신 스냅샷 + 후킹 결합")
check(store.hook_type_summary(conn, "stay")[0]["n"] == 1, "후킹 유형 요약 집계")
irrelevant = dict(mock_hook(top[1].title), relevant=False, content_type="celebrity_talk", source="gemini:test")
store.save_hook(conn, top[1].video_id, irrelevant)
check(sum(s["n"] for s in store.hook_type_summary(conn, "stay")) == 1, "도메인 부적합 후킹은 유형 요약에서 제외")
row1 = next(r for r in store.recent_outliers(conn, "stay", 14, 5.0, 50) if r["video_id"] == top[1].video_id)
check(row1["hook_relevant"] == 0 and row1["hook_content_type"] == "celebrity_talk", "조회 결과에 적합성·콘텐츠 유형 포함")
check(store.delete_hooks(conn, [top[1].video_id]) == 1 and not store.has_hook(conn, top[1].video_id), "잘못 수집된 후킹 패턴 삭제")

# 기존(구 스키마) DB를 열면 새 컬럼이 자동 추가되고 기존 행은 보존
import sqlite3
old_db = os.path.join(tempfile.mkdtemp(), "old.db")
oc = sqlite3.connect(old_db)
oc.executescript("""CREATE TABLE videos (video_id TEXT PRIMARY KEY, vertical TEXT, keyword TEXT, title TEXT, channel_id TEXT,
  published_at TEXT, duration_sec INTEGER, url TEXT, first_seen TEXT, last_seen TEXT);
CREATE TABLE hooks (video_id TEXT PRIMARY KEY, extracted_at TEXT, source TEXT, spoken_line TEXT, on_screen_text TEXT,
  visual_hook TEXT, hook_type TEXT, pattern_template TEXT, why_it_works TEXT);
INSERT INTO hooks (video_id, hook_type) VALUES ('legacy', 'question');""")
oc.commit()
oc.close()
mc_ = store.connect(old_db)
hook_cols = {r["name"] for r in mc_.execute("PRAGMA table_info(hooks)")}
check({"relevant", "content_type"} <= hook_cols and "category_id" in {r["name"] for r in mc_.execute("PRAGMA table_info(videos)")}
      and mc_.execute("SELECT hook_type FROM hooks WHERE video_id='legacy'").fetchone()[0] == "question",
      "구 스키마 DB 자동 마이그레이션 (컬럼 추가, 기존 행 보존)")
mc_.close()

# ---------------------------------------------------------------- hooks (mock 규칙)
check(mock_hook("[호캉스] 3초 만에 예약 마감된 이유")["pattern_template"] == "{숫자}초 만에 예약 마감된 이유", "mock 후킹: 숫자 → {숫자} 자리표시자")
check(mock_hook("[x] POV: 문 열자마자")["hook_type"] == "pov" and mock_hook("[x] 이 가격에 이게 된다고?")["hook_type"] == "question", "mock 후킹 유형 규칙")

# ---------------------------------------------------------------- 후킹 루프: 적합 N개 목표, 분석 상한 3N
import core.radar.run as radar_run
_calls = []


def _fake_hook(video_id, title, api_key, dry_run, vertical="stay"):
    _calls.append(video_id)
    return dict(mock_hook(title), relevant=len(_calls) > 2, content_type="review" if len(_calls) > 2 else "tv_clip")


_orig_hook = radar_run.try_extract_hook
radar_run.try_extract_hook = _fake_hook
loop_db = os.path.join(tempfile.mkdtemp(), "loop.db")
res = radar_run.execute(vertical="stay", dry_run=True, db_path=loop_db, hooks=2)
radar_run.try_extract_hook = _orig_hook
v0 = res["verticals"][0]
check(v0["hooks_extracted"] == 2 and v0["hooks_irrelevant"] == 2 and len(_calls) == 4,
      f"부적합 2건을 건너뛰고 적합 후킹 2건 확보 (분석 {len(_calls)}회, 상한 6회)")

# ---------------------------------------------------------------- CLI (dry-run, 임시 DB)
tmpdb = os.path.join(tempfile.mkdtemp(), "cli.db")
code = radar_main(["--vertical", "all", "--dry-run", "--db", tmpdb, "--top", "3"])
c2 = store.connect(tmpdb)
runs = c2.execute("SELECT vertical, mode, quota_used, outliers FROM runs ORDER BY run_id").fetchall()
check(code == 0 and [r["vertical"] for r in runs] == ["stay", "food"] and all(r["mode"] == "dry-run" for r in runs),
      f"CLI --vertical all --dry-run: 종료코드 0, 실행 2건 기록 {[dict(r) for r in runs]}")
check(store.DRYRUN_DB != store.DEFAULT_DB, "dry-run 기본 DB가 실 데이터 DB와 분리")

# ---------------------------------------------------------------- Gemini 실호출: 공개 YouTube URL 0~3초 분석
if not live_skip("Gemini: 공개 YouTube URL 0~3초 구간 분석"):
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    try:
        h = extract_hook("jNQXAC9IVRw", os.getenv("GEMINI_API_KEY"))   # 'Me at the zoo' (YouTube 최초 공개 영상, 19초)
        print("     Gemini 결과:", h)
        check(h["source"].startswith("gemini") and h["hook_type"] in __import__("core.radar.hooks", fromlist=["HOOK_TYPES"]).HOOK_TYPES
              and (h["spoken_line"] or h["visual_hook"]), "Gemini: 공개 YouTube URL 0~3초 구간 분석 → 구조화 결과")
        check(h["relevant"] is False, f"Gemini 적합성 판정: 동물원 영상은 숙소 콘텐츠 아님 → relevant=False ({h['content_type']})")
    except Exception as e:
        check(False, f"Gemini 실호출 실패: {e}")
finish(fails)