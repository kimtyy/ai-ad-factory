"""
core/radar/trends.py - 레이더 수집 데이터 → 업로드 메타데이터용 '트렌드 다이제스트' (DB 캐시)

레이더는 대시보드 구경용이 아니라 제작 파이프라인의 센서: 주기 수집(scripts/radar_daily.cmd) 뒤 업종별로
  - title_patterns : 떡상 영상 제목을 추상화한 문장 구조 (손실 회피·호기심 훅). Gemini가 {장소}/{특징} 같은 자리표시자로 바꿈
                     (원문 제목을 그대로 쓰지 않도록 구조만 저장). 키가 없으면 첫 3초 후킹 템플릿만 사용
  - emotion_words  : 떡상 영상 제목에 전체 후보 대비 유난히 자주 나오는 감정·호기심 단어 (lift 기준)
  - hashtag_pool   : 떡상 영상 제목의 #태그를 배수 가중으로 집계
  - first3s_hooks  : 기존 첫 3초 후킹 템플릿 (hooks 테이블)
을 만들어 trend_digest 테이블에 저장합니다. 업로드 카피 생성기(core/shorts_meta.py)가 렌더 때 최신 다이제스트를 필수 맥락으로 받음.
남의 상호·브랜드가 섞이지 않도록 '서로 다른 채널 2곳 이상'에서 쓰인 단어·태그만 남깁니다.

  python -m core.radar.trends --vertical all        # 다이제스트 다시 만들기 (수집 후 자동 실행)
"""
import argparse
import json
import logging
import math
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.radar import store
from core.radar.hints import VIRAL, vertical_for
from core.radar.hooks import HOOK_TYPE_KO, HOOK_TYPES
from core.radar.scoring import min_ratio_for

logger = logging.getLogger("ad_factory.radar.trends")

DIGEST_DDL = """CREATE TABLE IF NOT EXISTS trend_digest (
  vertical TEXT PRIMARY KEY, built_at TEXT, latest_run_at TEXT, n_outliers INTEGER, n_videos INTEGER, data TEXT)"""
MAX_AGE_DAYS = 14               # 이보다 오래된 다이제스트는 '오래됨' 표시 (그래도 사용은 함)
MIN_CHANNELS = 2                # 단어·태그는 서로 다른 채널 2곳 이상에서 쓰였을 때만 (남의 상호·브랜드 배제)
TOP_TITLES_FOR_LLM = 15
STOPWORDS = {"쇼츠", "shorts", "추천", "영상", "브이로그", "vlog", "그리고", "진짜로", "에서", "으로", "하는", "있는", "없는", "the", "and"}
# 감정·호기심 단어 사전 (lift 통계와 함께, 이 단어들은 1곳만 나와도 후보로)
EMOTION_LEXICON = ("대박", "미쳤", "미친", "소름", "레전드", "실화", "역대급", "무조건", "손해", "몰랐", "충격", "꿀팁", "비밀", "이유",
                   "절대", "인생", "끝판왕", "찐", "갓성비", "가성비", "감동", "힐링", "ㄷㄷ", "헐", "와", "최고", "필수", "꼭", "모르면",
                   "아무도", "숨은", "여기", "이런", "있다고", "인줄", "줄 알았")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_table(conn) -> None:
    conn.execute(DIGEST_DDL)
    conn.commit()


def _irrelevant_ids(conn, vertical: str) -> set:
    """후킹 분석에서 이 업종에 부적합(예능·밈·반려동물·애니·연예인 토크 등)으로 판정된 영상 (제목 패턴·태그에서도 제외)"""
    return {r[0] for r in conn.execute("SELECT video_id FROM hooks WHERE vertical=? AND relevant=0", (vertical,)).fetchall()}


def _outlier_rows(conn, vertical: str, days: int, min_ratio: float) -> List[Dict[str, Any]]:
    """떡상 영상 (부적합 판정 영상 제외, 아직 판정 전인 영상은 포함)"""
    rows = store.recent_outliers(conn, vertical, days, min_ratio, limit=300)
    return [r for r in rows if r.get("title") and r.get("hook_relevant") != 0]


def _all_titles(conn, vertical: str, days: int) -> List[Dict[str, Any]]:
    """업종에서 최근 수집한 영상 전부 + 영상별 최고 배수 (태그·단어 통계 모수)"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute("""SELECT v.video_id, v.title, v.channel_id, MAX(s.outlier_ratio) FROM videos v
                          JOIN snapshots s ON s.video_id = v.video_id JOIN runs r ON r.run_id = s.run_id
                          WHERE r.vertical = ? AND v.published_at >= ? GROUP BY v.video_id""", (vertical, since))
    bad = _irrelevant_ids(conn, vertical)
    return [{"video_id": a, "title": b or "", "channel_id": c, "outlier_ratio": float(d or 0)} for a, b, c, d in cur.fetchall()
            if a not in bad]


def split_title(title: str):
    tags = [t.lower() for t in re.findall(r"#([0-9A-Za-z가-힣_]+)", title or "")]
    body = re.sub(r"#[0-9A-Za-z가-힣_]+", " ", title or "")
    return body, tags


def _words(text: str) -> List[str]:
    ws = re.findall(r"[가-힣A-Za-z]{2,8}", text or "")
    return [w for w in ws if w.lower() not in STOPWORDS]


def hashtag_pool(rows: List[Dict[str, Any]], limit: int = 25) -> List[Dict[str, Any]]:
    """업종 수집 영상 제목의 #태그를 배수 가중(log2(1+배수))으로 집계. 서로 다른 채널 2곳 이상에서 쓰인 태그만"""
    weight, count, chans = defaultdict(float), defaultdict(int), defaultdict(set)
    for r in rows:
        _, tags = split_title(r["title"])
        for t in set(tags):
            weight[t] += math.log2(1 + max(0.0, float(r.get("outlier_ratio") or 0)))
            count[t] += 1
            chans[t].add(r.get("channel") or r.get("channel_id") or r["video_id"])
    pool = [{"tag": "#" + t, "weight": round(w, 2), "count": count[t], "channels": len(chans[t])}
            for t, w in weight.items() if len(chans[t]) >= MIN_CHANNELS and t not in STOPWORDS and len(t) >= 2]
    return sorted(pool, key=lambda d: -d["weight"])[:limit]


def emotion_words(outliers: List[Dict[str, Any]], all_rows: List[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
    """떡상 제목에 유난히 많이 나오는 단어 (lift = 떡상 내 비율 / 전체 비율). 서로 다른 채널 2곳 이상"""
    def doc_freq(rows):
        df, ch = defaultdict(int), defaultdict(set)
        for r in rows:
            body, _ = split_title(r["title"])
            for w in set(_words(body)):
                df[w] += 1
                ch[w].add(r.get("channel") or r.get("channel_id") or r["video_id"])
        return df, ch
    if not outliers:
        return []
    o_df, o_ch = doc_freq(outliers)
    a_df, _ = doc_freq(all_rows or outliers)
    n_o, n_a = len(outliers), max(1, len(all_rows or outliers))
    out = []
    for w, c in o_df.items():
        lex = any(k in w for k in EMOTION_LEXICON)
        # 통계로만 뽑힌 단어는 떡상 제목 3개·채널 3곳 이상일 때만 (인물·상호 이름이 섞이지 않게)
        if not lex and (c < 3 or len(o_ch[w]) < 3):
            continue
        lift = (c / n_o) / max(1e-9, (a_df.get(w, c) / n_a))
        out.append({"word": w, "count": c, "lift": round(lift, 2), "emotion": lex, "score": round(c * math.log(1 + lift) * (1.5 if lex else 1), 2)})
    return sorted(out, key=lambda d: -d["score"])[:limit]


def _first3s_hooks(conn, vertical: str, days: int, min_ratio: float) -> List[Dict[str, Any]]:
    rows = store.recent_outliers(conn, vertical, days, min_ratio, limit=100)
    hooks, seen = [], set()
    for r in rows:
        tpl = r.get("pattern_template")
        if tpl and r.get("hook_relevant") and r.get("hook_source") != "mock" and tpl not in seen:
            seen.add(tpl)
            hooks.append({"template": tpl, "hook_type": r.get("hook_type"), "ratio": round(float(r["outlier_ratio"]), 1),
                          "why": r.get("why_it_works") or ""})
    return hooks[:6]


TITLE_PATTERN_PROMPT = """아래는 최근 30일 한국 유튜브 쇼츠 중 '구독자 대비 조회수 배수'가 높은 떡상 영상 제목들입니다 (배수 높은 순).
제목에서 사람들을 클릭하게 만든 '문장 구조'만 뽑아 최대 6개 패턴으로 정리하세요.
- template: 구조만 남긴 문장. 상호·지명·브랜드·숫자·가격은 {{장소}} {{지역}} {{특징}} {{메뉴}} {{숫자}} {{가격}} 같은 자리표시자로 바꾸고,
  원문 제목을 그대로 옮기지 마세요. 20자 안팎. 예: "여길 아직도 모른다고? {{특징}} {{장소}}"
- hook_type: {types} 중 하나
- why: 왜 클릭되는지 한 줄 (손실 회피, 호기심 공백, 반전 등)
- source_ratio: 이 구조가 나온 제목 중 가장 높은 배수
JSON만 출력: {{"patterns": [{{"template": "...", "hook_type": "...", "why": "...", "source_ratio": 0}}]}}

[제목들]
{titles}
"""


def llm_title_patterns(outliers: List[Dict[str, Any]], api_key: Optional[str]) -> List[Dict[str, Any]]:
    if not api_key or not outliers:
        return []
    titles = "\n".join(f"- ({float(r['outlier_ratio']):.1f}배) {split_title(r['title'])[0].strip()[:80]}"
                       for r in outliers[:TOP_TITLES_FOR_LLM])
    try:
        from google import genai
        from google.genai import types
        from core.guardrail import validate_model_call
        client = genai.Client(api_key=api_key)
        res = client.models.generate_content(
            model=validate_model_call("gemini-3.6-flash", category="llm"),
            contents=[TITLE_PATTERN_PROMPT.format(types=", ".join(HOOK_TYPES), titles=titles)],
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.3))
        raw = json.loads(res.text).get("patterns") or []
    except Exception as e:
        logger.warning(f"제목 패턴 추출 실패 (첫 3초 후킹 템플릿만 사용): {e}")
        return []
    originals = [_norm(split_title(r["title"])[0]) for r in outliers]
    out = []
    for p in raw:
        tpl = str(p.get("template") or "").strip()
        if not tpl or len(tpl) > 40 or "{" not in tpl:
            continue
        if any(_norm(tpl) and _norm(tpl) in o for o in originals):      # 원문 그대로면 버림
            continue
        out.append({"template": tpl, "hook_type": p.get("hook_type") if p.get("hook_type") in HOOK_TYPES else "other",
                    "why": str(p.get("why") or "")[:80], "ratio": round(float(p.get("source_ratio") or 0), 1)})
    return out[:6]


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", (text or "").lower())


def build_digest(conn, vertical: str, days: int = 30, api_key: Optional[str] = None, min_ratio: float = 5.0) -> Dict[str, Any]:
    mr = min_ratio_for(vertical, min_ratio)
    outliers = _outlier_rows(conn, vertical, days, mr)
    all_rows = _all_titles(conn, vertical, days)
    latest = conn.execute("SELECT max(finished_at) FROM runs WHERE vertical=?", (vertical,)).fetchone()[0]
    digest = {
        "vertical": vertical, "built_at": _now(), "latest_run_at": latest, "days": days, "min_ratio": mr,
        "n_outliers": len(outliers), "n_videos": len(all_rows),
        "top_ratio": round(max((float(r["outlier_ratio"]) for r in outliers), default=0.0), 1),
        "title_patterns": llm_title_patterns(outliers, api_key),
        "first3s_hooks": _first3s_hooks(conn, vertical, days, mr),
        "emotion_words": emotion_words(outliers, all_rows),
        "hashtag_pool": hashtag_pool(all_rows),
    }
    return digest


def save_digest(conn, digest: Dict[str, Any]) -> None:
    ensure_table(conn)
    conn.execute("INSERT OR REPLACE INTO trend_digest VALUES (?,?,?,?,?,?)",
                 (digest["vertical"], digest["built_at"], digest.get("latest_run_at"), digest["n_outliers"], digest["n_videos"],
                  json.dumps(digest, ensure_ascii=False)))
    conn.commit()


def load_digest(vertical: Optional[str], db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """업종 다이제스트 (없으면 viral 범용 다이제스트로 대체, 둘 다 없으면 None). age_days·stale·fallback 표시 추가"""
    path = db_path or store.DEFAULT_DB
    if not vertical or not os.path.exists(path):
        return None
    conn = store.connect(path)
    try:
        ensure_table(conn)
        for v, fallback in ((vertical, False), (VIRAL, True)):
            row = conn.execute("SELECT data FROM trend_digest WHERE vertical=?", (v,)).fetchone()
            if not row:
                continue
            d = json.loads(row[0])
            if not (d.get("title_patterns") or d.get("first3s_hooks") or d.get("hashtag_pool")):
                continue
            built = datetime.strptime(d["built_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            d["age_days"] = round((datetime.now(timezone.utc) - built).total_seconds() / 86400, 1)
            d["stale"] = d["age_days"] > MAX_AGE_DAYS
            d["fallback_from"] = vertical if fallback else None
            return d
    finally:
        conn.close()
    return None


def digest_for(mode: str, business_type: Optional[str] = None, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return load_digest(vertical_for(mode, business_type) or VIRAL, db_path)


def rebuild(verticals: List[str], db_path: Optional[str] = None, api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = store.connect(db_path or store.DEFAULT_DB)
    out = []
    try:
        for v in verticals:
            d = build_digest(conn, v, api_key=api_key)
            save_digest(conn, d)
            out.append(d)
            logger.info(f"트렌드 다이제스트 [{v}]: 떡상 {d['n_outliers']}/{d['n_videos']}개, 제목 패턴 {len(d['title_patterns'])}, "
                        f"첫3초 {len(d['first3s_hooks'])}, 감정어 {len(d['emotion_words'])}, 태그 {len(d['hashtag_pool'])}")
    finally:
        conn.close()
    return out


def main(argv=None) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    from core.radar.keywords import VERTICAL_KEYWORDS
    p = argparse.ArgumentParser(description="레이더 트렌드 다이제스트 생성 (업로드 메타데이터 자동 주입용)")
    p.add_argument("--vertical", choices=list(VERTICAL_KEYWORDS) + ["all"], default="all")
    p.add_argument("--db", default=None)
    a = p.parse_args(argv)
    vs = list(VERTICAL_KEYWORDS) if a.vertical == "all" else [a.vertical]
    for d in rebuild(vs, a.db, os.getenv("GEMINI_API_KEY", "").strip() or None):
        print(f"[{d['vertical']}] 떡상 {d['n_outliers']}/{d['n_videos']} · 최고 {d['top_ratio']}배 · 제목 패턴 {len(d['title_patterns'])} · "
              f"첫3초 {len(d['first3s_hooks'])} · 감정어 {len(d['emotion_words'])} · 태그 {len(d['hashtag_pool'])}")
        for t in d["title_patterns"][:6]:
            print(f"   패턴 [{HOOK_TYPE_KO.get(t['hook_type'], t['hook_type'])}] {t['template']} ({t['ratio']}배) - {t['why']}")
        print("   감정어:", ", ".join(f"{w['word']}({w['count']})" for w in d["emotion_words"][:10]))
        print("   태그:", " ".join(f"{t['tag']}({t['count']})" for t in d["hashtag_pool"][:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
