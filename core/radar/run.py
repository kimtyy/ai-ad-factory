"""
core/radar/run.py - 쇼츠 레이더 CLI

  python -m core.radar.run --vertical stay --days 30
  python -m core.radar.run --vertical food --dry-run          # 키 없이 전체 흐름 검증 (가짜 데이터, 별도 DB)
  python -m core.radar.run --vertical all --hooks 10 --budget 3000

YOUTUBE_API_KEY(.env)가 없으면 자동으로 dry-run으로 전환합니다 (실 데이터 DB와 분리).
"""
import argparse
import os
import sys
import time

from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.radar import store
from core.radar.collector import collect
from core.radar.hooks import HOOK_TYPE_KO, try_extract_hook
from core.radar.keywords import DEFAULT_DAYS, VERTICAL_KEYWORDS, VERTICAL_LABELS, keywords_for
from core.radar.scoring import RadarFilters, rank
from core.radar.youtube import MockYouTubeClient, YouTubeClient


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="쇼츠 레이더: 구독자 대비 조회수 배수가 높은 최근 떡상 쇼츠 수집 + 첫 3초 후킹 구조 추출")
    p.add_argument("--vertical", choices=list(VERTICAL_KEYWORDS) + ["all"], default="stay")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"최근 N일 내 업로드 (기본 {DEFAULT_DAYS})")
    p.add_argument("--keywords", type=str, default=None, help="쉼표로 구분한 키워드 (기본: vertical 기본 세트)")
    p.add_argument("--pages", type=int, default=1, help="키워드당 검색 페이지 수 (페이지당 100 units)")
    p.add_argument("--min-ratio", type=float, default=5.0)
    p.add_argument("--min-subs", type=int, default=300)
    p.add_argument("--min-views", type=int, default=30_000)
    p.add_argument("--hooks", type=int, default=5, help="후킹 구조를 추출할 상위 영상 수 (Gemini 호출 수)")
    p.add_argument("--budget", type=int, default=3000, help="이번 실행의 YouTube 쿼터 예산 (units)")
    p.add_argument("--dry-run", action="store_true", help="API 호출 없이 가짜 데이터로 전체 흐름 검증")
    p.add_argument("--db", type=str, default=None, help="SQLite 경로 (기본: outputs/radar/radar.db, dry-run은 radar_dryrun.db)")
    p.add_argument("--top", type=int, default=10, help="출력할 상위 영상 수")
    return p


def execute(vertical: str = "stay", days: int = DEFAULT_DAYS, keywords=None, pages: int = 1, min_ratio: float = 5.0,
            min_subs: int = 300, min_views: int = 30_000, hooks: int = 5, budget: int = 3000,
            dry_run: bool = False, db_path=None):
    """
    레이더 1회 실행 (CLI·Streamlit 공용). 반환: {"mode", "db_path", "notice", "verticals": [요약...]}
    YOUTUBE_API_KEY가 없으면 자동으로 dry-run(가짜 데이터, 별도 DB)으로 전환합니다.
    """
    load_dotenv()
    yt_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    auto_dry = not dry_run and not yt_key
    dry_run = dry_run or not yt_key
    db_path = db_path or (store.DRYRUN_DB if dry_run else store.DEFAULT_DB)
    conn = store.connect(db_path)
    filters = RadarFilters(min_ratio=min_ratio, min_subscribers=min_subs, min_views=min_views)
    verticals = list(VERTICAL_KEYWORDS) if vertical == "all" else [vertical]
    client = MockYouTubeClient(budget_units=budget) if dry_run else YouTubeClient(yt_key, budget_units=budget)
    result = {"mode": client.mode, "db_path": db_path, "verticals": [],
              "notice": "YOUTUBE_API_KEY가 .env에 없어 dry-run(가짜 데이터)으로 실행했습니다." if auto_dry else ""}
    try:
        for v in verticals:
            if isinstance(keywords, str) and keywords.strip():
                kws = [k.strip() for k in keywords.split(",") if k.strip()]
            else:
                kws = list(keywords) if keywords else keywords_for(v)
            run_id = store.start_run(conn, client.mode, v, days, kws)
            t0 = time.time()
            units_before = client.used_units
            records, stats = collect(client, v, kws, days=days, pages=pages, filters=filters)
            store.save_records(conn, run_id, records)
            top = rank(records)
            # 업종에 맞는 후킹 hooks개를 목표로 분석 (부적합 클립이 섞여도 최대 3배수까지만 Gemini 호출)
            hooked, irrelevant, attempts = 0, 0, 0
            for rec in top:
                if hooked >= hooks or attempts >= hooks * 3:
                    break
                if store.has_hook(conn, rec.video_id):
                    continue
                attempts += 1
                hook = try_extract_hook(rec.video_id, rec.title, gemini_key, dry_run, vertical=v)
                if hook:
                    store.save_hook(conn, rec.video_id, hook)
                    if hook.get("relevant"):
                        hooked += 1
                    else:
                        irrelevant += 1
            used = client.used_units - units_before
            rejects = {}
            for r in records:
                if r.reject_reason:
                    key = r.reject_reason.split(" (")[0]
                    rejects[key] = rejects.get(key, 0) + 1
            store.finish_run(conn, run_id, used, len(records), len(top),
                             note=f"budget_stopped={stats.get('budget_stopped', 0)} rejects={rejects}")
            result["verticals"].append({
                "vertical": v, "keywords": kws, "searched_keywords": stats["searched_keywords"],
                "search_calls": stats["search_calls"], "candidates": len(records), "outliers": len(top),
                "quota_used": used, "hooks_extracted": hooked, "hooks_irrelevant": irrelevant, "rejects": rejects,
                "budget_stopped": bool(stats.get("budget_stopped")), "seconds": round(time.time() - t0, 1),
            })
    finally:
        conn.close()
    return result


def run(args) -> int:
    res = execute(vertical=args.vertical, days=args.days, keywords=args.keywords, pages=args.pages,
                  min_ratio=args.min_ratio, min_subs=args.min_subs, min_views=args.min_views, hooks=args.hooks,
                  budget=args.budget, dry_run=args.dry_run, db_path=args.db)
    if res["notice"]:
        print(f"⚠️  {res['notice']} 실 수집은 키 설정 후 다시 실행하세요.")
    print(f"📡 쇼츠 레이더 [{res['mode'].upper()}] 최근 {args.days}일 · 배수 ≥ {args.min_ratio}x · "
          f"구독자 ≥ {args.min_subs:,} · 조회수 ≥ {args.min_views:,} · 쿼터 예산 {args.budget} units · DB {res['db_path']}")
    conn = store.connect(res["db_path"])
    try:
        for v in res["verticals"]:
            print()
            print(f"{VERTICAL_LABELS[v['vertical']]}  키워드 {v['searched_keywords']}/{len(v['keywords'])}개 · 검색 {v['search_calls']}회 · "
                  f"후보 {v['candidates']}개 → 떡상 {v['outliers']}개 · 쿼터 {v['quota_used']} units · 후킹 추출 {v['hooks_extracted']}건"
                  f"{' (도메인 부적합 ' + str(v['hooks_irrelevant']) + '건 제외)' if v.get('hooks_irrelevant') else ''} · {v['seconds']}s")
            if v["rejects"]:
                print("   탈락 사유: " + ", ".join(f"{k} {n}" for k, n in v["rejects"].items()))
            if v["budget_stopped"]:
                print("   ⚠️  쿼터 예산 도달로 일부 키워드를 건너뛰었습니다 (--budget 조정).")
            for i, row in enumerate(store.recent_outliers(conn, v["vertical"], args.days, args.min_ratio, args.top), 1):
                if row.get("hook_type") and not row.get("hook_relevant"):
                    hook = f" | 🚫 힌트 제외 (도메인 부적합: {row.get('hook_content_type')})"
                elif row.get("hook_type"):
                    hook = f" | 후킹[{HOOK_TYPE_KO.get(row['hook_type'], '-')}] {row['pattern_template'] or ''}"
                else:
                    hook = ""
                print(f"   {i:>2}. {row['outlier_ratio']:>5.1f}x  조회 {row['views']:>9,} / 구독 {row['subscribers']:>8,}  "
                      f"{row['title'][:34]}{hook}")
            summary = store.hook_type_summary(conn, v["vertical"])
            if summary:
                print("   후킹 유형 분포: " + ", ".join(
                    f"{HOOK_TYPE_KO.get(s['hook_type'], s['hook_type'])} {s['n']}건(평균 {s['avg_ratio']}x)" for s in summary))
    finally:
        conn.close()
    return 0


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
