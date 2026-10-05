"""
core/radar/store.py - 레이더 SQLite 저장소

테이블
  runs       실행 이력 (모드, 키워드, 쿼터 사용량, 후보/떡상 수)
  channels   채널 구독자 수 (최신값)
  videos     영상 메타데이터 (최초/최근 발견 시각, 키워드)
  snapshots  실행 시점별 조회수·배수·속도 (같은 영상을 여러 번 수집하면 추이가 쌓임)
  hooks      첫 3초 후킹 구조 (영상당 1개)
실 데이터(radar.db)와 dry-run 데이터(radar_dryrun.db)는 파일을 분리해 섞이지 않게 합니다.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from core.radar.scoring import VideoRecord

DEFAULT_DB = os.path.join("outputs", "radar", "radar.db")
DRYRUN_DB = os.path.join("outputs", "radar", "radar_dryrun.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, mode TEXT, vertical TEXT,
  days INTEGER, keywords TEXT, quota_used INTEGER, candidates INTEGER, outliers INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS channels (
  channel_id TEXT PRIMARY KEY, title TEXT, subscribers INTEGER, hidden INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS videos (
  video_id TEXT PRIMARY KEY, vertical TEXT, keyword TEXT, title TEXT, channel_id TEXT, published_at TEXT,
  duration_sec INTEGER, url TEXT, first_seen TEXT, last_seen TEXT, category_id TEXT);
CREATE TABLE IF NOT EXISTS snapshots (
  video_id TEXT, run_id INTEGER, captured_at TEXT, views INTEGER, likes INTEGER, comments INTEGER,
  subscribers INTEGER, outlier_ratio REAL, velocity_per_day REAL, passed INTEGER, reject_reason TEXT,
  PRIMARY KEY (video_id, run_id));
CREATE TABLE IF NOT EXISTS hooks (
  video_id TEXT PRIMARY KEY, extracted_at TEXT, source TEXT, spoken_line TEXT, on_screen_text TEXT,
  visual_hook TEXT, hook_type TEXT, pattern_template TEXT, why_it_works TEXT, relevant INTEGER, content_type TEXT);
CREATE INDEX IF NOT EXISTS idx_snap_ratio ON snapshots(outlier_ratio);
CREATE INDEX IF NOT EXISTS idx_videos_vertical ON videos(vertical);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# 기존 DB에 나중에 추가된 컬럼 (CREATE TABLE IF NOT EXISTS는 기존 테이블을 바꾸지 않으므로 ALTER로 보강)
MIGRATIONS = {"videos": {"category_id": "TEXT"}, "hooks": {"relevant": "INTEGER", "content_type": "TEXT"}}


def connect(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    conn.commit()
    return conn


def start_run(conn, mode: str, vertical: str, days: int, keywords: List[str]) -> int:
    cur = conn.execute("INSERT INTO runs (started_at, mode, vertical, days, keywords) VALUES (?,?,?,?,?)",
                       (_now(), mode, vertical, days, json.dumps(keywords, ensure_ascii=False)))
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, quota_used: int, candidates: int, outliers: int, note: str = ""):
    conn.execute("UPDATE runs SET finished_at=?, quota_used=?, candidates=?, outliers=?, note=? WHERE run_id=?",
                 (_now(), quota_used, candidates, outliers, note, run_id))
    conn.commit()


def save_records(conn, run_id: int, records: Iterable[VideoRecord]):
    now = _now()
    for r in records:
        conn.execute("""INSERT INTO channels (channel_id, title, subscribers, hidden, updated_at) VALUES (?,?,?,?,?)
                        ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title, subscribers=excluded.subscribers,
                        hidden=excluded.hidden, updated_at=excluded.updated_at""",
                     (r.channel_id, r.channel_title, r.subscribers, int(r.subscribers_hidden), now))
        conn.execute("""INSERT INTO videos (video_id, vertical, keyword, title, channel_id, published_at, duration_sec, url,
                        first_seen, last_seen, category_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(video_id) DO UPDATE SET title=excluded.title, last_seen=excluded.last_seen,
                        category_id=excluded.category_id""",
                     (r.video_id, r.vertical, r.keyword, r.title, r.channel_id,
                      r.published_at.strftime("%Y-%m-%dT%H:%M:%SZ"), r.duration_sec, r.url, now, now, r.category_id))
        conn.execute("""INSERT OR REPLACE INTO snapshots (video_id, run_id, captured_at, views, likes, comments, subscribers,
                        outlier_ratio, velocity_per_day, passed, reject_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (r.video_id, run_id, now, r.views, r.likes, r.comments, r.subscribers, r.outlier_ratio,
                      r.velocity_per_day, int(r.reject_reason is None), r.reject_reason))
    conn.commit()


def delete_hooks(conn, video_ids: List[str]) -> int:
    """잘못 수집된 후킹 패턴 삭제 (영상·스냅샷 이력은 유지)"""
    cur = conn.executemany("DELETE FROM hooks WHERE video_id = ?", [(v,) for v in video_ids])
    conn.commit()
    return cur.rowcount


def has_hook(conn, video_id: str) -> bool:
    return conn.execute("SELECT 1 FROM hooks WHERE video_id=?", (video_id,)).fetchone() is not None


def save_hook(conn, video_id: str, hook: Dict[str, Any]):
    conn.execute("""INSERT OR REPLACE INTO hooks (video_id, extracted_at, source, spoken_line, on_screen_text, visual_hook,
                    hook_type, pattern_template, why_it_works, relevant, content_type) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                 (video_id, _now(), hook.get("source"), hook.get("spoken_line"), hook.get("on_screen_text"),
                  hook.get("visual_hook"), hook.get("hook_type"), hook.get("pattern_template"), hook.get("why_it_works"),
                  int(bool(hook.get("relevant"))), hook.get("content_type")))
    conn.commit()


def recent_outliers(conn, vertical: Optional[str] = None, days: int = 14, min_ratio: float = 5.0,
                    limit: int = 20) -> List[Dict[str, Any]]:
    """최근 업로드된 떡상 영상 (영상별 최신 스냅샷 기준) + 후킹 구조"""
    q = """
    SELECT v.video_id, v.vertical, v.keyword, v.title, v.url, v.published_at, v.duration_sec, v.category_id, c.title AS channel,
           s.views, s.subscribers, s.outlier_ratio, s.velocity_per_day, s.captured_at,
           h.hook_type, h.pattern_template, h.spoken_line, h.on_screen_text, h.visual_hook, h.why_it_works, h.source AS hook_source,
           h.relevant AS hook_relevant, h.content_type AS hook_content_type
    FROM videos v
    JOIN snapshots s ON s.video_id = v.video_id
     AND s.run_id = (SELECT MAX(run_id) FROM snapshots s2 WHERE s2.video_id = v.video_id)
    LEFT JOIN channels c ON c.channel_id = v.channel_id
    LEFT JOIN hooks h ON h.video_id = v.video_id
    WHERE s.passed = 1 AND s.outlier_ratio >= ? AND v.published_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)
    """
    args: List[Any] = [min_ratio, f"-{int(days)} days"]
    if vertical:
        q += " AND v.vertical = ?"
        args.append(vertical)
    q += " ORDER BY s.outlier_ratio DESC, s.velocity_per_day DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(q, args).fetchall()]


def hook_type_summary(conn, vertical: Optional[str] = None) -> List[Dict[str, Any]]:
    """후킹 유형별 떡상 영상 수/평균 배수 (어떤 후킹이 요즘 먹히는지)"""
    q = """SELECT h.hook_type, COUNT(*) AS n, ROUND(AVG(s.outlier_ratio), 1) AS avg_ratio
           FROM hooks h JOIN videos v ON v.video_id = h.video_id
           JOIN snapshots s ON s.video_id = v.video_id AND s.run_id = (SELECT MAX(run_id) FROM snapshots s2 WHERE s2.video_id = v.video_id)
           WHERE s.passed = 1 AND h.relevant = 1"""
    args: List[Any] = []
    if vertical:
        q += " AND v.vertical = ?"
        args.append(vertical)
    q += " GROUP BY h.hook_type ORDER BY n DESC"
    return [dict(r) for r in conn.execute(q, args).fetchall()]
