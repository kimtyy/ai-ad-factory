"""
core/radar/hints.py - 레이더 후킹 패턴 → 플래너 씬 1 후킹 힌트

레이더 DB에 쌓인 최근 고배수 쇼츠의 '후킹 구조(pattern_template)'를 골라 대본 프롬프트용 힌트 블록을 만듭니다.
- 기본적으로 실데이터(radar.db)만 사용합니다. dry-run(가짜) 패턴은 allow_dryrun=True일 때만 (테스트용)
- 프롬프트에는 구조(템플릿·유형·이유)만 넘기고, 원문 대사/자막을 그대로 베낀 후킹은 copies_reference로 걸러냅니다.
- 업종 전용 패턴이 MIN_VERTICAL_PATTERNS개 미만이면 viral(업종 무관 15배+) 범용 스켈레톤으로 보충합니다.
"""
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.radar import store
from core.radar.hooks import HOOK_TYPE_KO
from core.radar.scoring import min_ratio_for

# 제작 모드/업종 → 레이더 vertical
MODE_TO_VERTICAL = {"travel": "stay"}
OWNER_TYPE_TO_VERTICAL = {"stay": "stay", "food": "food"}   # 'shop'(매장·기타)은 아직 레이더 키워드 세트가 없음
VIRAL = "viral"
MIN_VERTICAL_PATTERNS = 3   # 업종 전용 패턴이 이보다 적으면 viral 범용 패턴으로 이 개수까지 보충


@dataclass
class HookHints:
    vertical: str
    references: List[Dict[str, Any]] = field(default_factory=list)
    source_db: str = ""
    is_dryrun: bool = False

    def __bool__(self) -> bool:
        return bool(self.references)

    @property
    def viral_count(self) -> int:
        return sum(r.get("vertical") == VIRAL for r in self.references)

    @property
    def prompt_block(self) -> str:
        if not self.references:
            return ""
        lines = [f"[최근 떡상 쇼츠 후킹 패턴 참고 - 구독자 대비 조회수 배수가 높은 {len(self.references)}개 영상의 첫 3초 구조]"]
        for i, r in enumerate(self.references, 1):
            tag = "[범용] " if r.get("vertical") == VIRAL else ""
            lines.append(f"{i}. {tag}{HOOK_TYPE_KO.get(r['hook_type'], r['hook_type'])} ({r['outlier_ratio']:.1f}배): "
                         f"구조 \"{r['pattern_template']}\"" + (f" / 먹힌 이유: {r['why_it_works']}" if r.get("why_it_works") else ""))
        lines.append("[힌트 사용 규칙] 씬 1 hook_title(과 필요하면 첫 대사)은 위 패턴 중 이번 사진·사실에 가장 맞는 하나의 '구조'를 골라 "
                     "새 문장으로 쓰세요. 톤앤매너 예시보다 이 패턴 구조가 우선입니다. 문구를 그대로 베끼지 말고, "
                     "{장소}/{숫자} 같은 자리에는 이번 영상의 실제 사진·사실만 넣으세요(사실이 아닌 가격·숫자·순위 금지). "
                     "고른 패턴 번호를 씬 1의 \"hook_pattern_used\" 필드에 정수로 적고, 사실과 맞는 패턴이 하나도 없을 때만 0을 적으세요.")
        if self.viral_count:
            lines.append("[범용 패턴 규칙] [범용] 표시는 다른 분야에서 터진 영상의 소재를 지운 뼈대입니다. "
                         "{주체}/{대상}/{행동}/{결과} 자리를 이번 업장의 실제 사진·사실로 채워 업종에 맞는 문장으로 바꾸고, "
                         "업종 전용 패턴이 사실과 맞으면 그쪽을 우선하세요.")
        return "\n".join(lines)

    def pattern_used(self, value: Any) -> Optional[Dict[str, Any]]:
        """LLM이 적은 hook_pattern_used 번호 → 해당 참고 패턴 (0/잘못된 값이면 None)"""
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        return self.references[n - 1] if 1 <= n <= len(self.references) else None

    def summary(self) -> List[Dict[str, Any]]:
        """씬 1에 남길 출처 기록"""
        return [{"video_id": r["video_id"], "outlier_ratio": r["outlier_ratio"], "hook_type": r["hook_type"],
                 "pattern_template": r["pattern_template"], "vertical": r.get("vertical")} for r in self.references]


def vertical_for(mode: str, business_type: Optional[str] = None) -> Optional[str]:
    if mode == "owner":
        return OWNER_TYPE_TO_VERTICAL.get(business_type or "")
    return MODE_TO_VERTICAL.get(mode)


def load_hook_hints(vertical: Optional[str], days: int = 30, min_ratio: float = 5.0, limit: int = 4,
                    allow_dryrun: bool = False, db_path: Optional[str] = None) -> HookHints:
    """후킹 패턴이 추출된 최근 고배수 영상 상위 limit개 (같은 템플릿/유형 중복은 줄여 다양하게).
    업종 전용 패턴이 MIN_VERTICAL_PATTERNS개 미만이면 viral 범용 패턴으로 그 개수까지 보충"""
    if not vertical:
        return HookHints(vertical="")
    candidates = [(db_path, False)] if db_path else [(store.DEFAULT_DB, False)]
    if allow_dryrun and not db_path:
        candidates.append((store.DRYRUN_DB, True))
    for path, is_dry in candidates:
        if not path or not os.path.exists(path):
            continue
        conn = store.connect(path)
        try:
            rows = store.recent_outliers(conn, vertical, days, min_ratio, limit=50)
            viral_rows = (store.recent_outliers(conn, VIRAL, days, min_ratio_for(VIRAL, min_ratio), limit=50)
                          if vertical != VIRAL else [])
        finally:
            conn.close()
        seen_tpl, type_count = set(), {}
        picked = _pick(_usable(rows, allow_dryrun), limit, seen_tpl, type_count)
        if len(picked) < MIN_VERTICAL_PATTERNS:
            picked += _pick(_usable(viral_rows, allow_dryrun), MIN_VERTICAL_PATTERNS - len(picked), seen_tpl, type_count)
        if picked:
            return HookHints(vertical=vertical, references=picked, source_db=path,
                             is_dryrun=is_dry or any(r.get("hook_source") == "mock" for r in picked))
    return HookHints(vertical=vertical)


def _usable(rows: List[Dict[str, Any]], allow_dryrun: bool) -> List[Dict[str, Any]]:
    """적합성 판정을 통과한 후킹만 (예능·애니 클립, 범용화 실패, 판정 이전 레거시 행은 제외)"""
    return [r for r in rows if r.get("pattern_template") and r.get("hook_relevant")
            and (allow_dryrun or r.get("hook_source") != "mock")]


def _pick(rows: List[Dict[str, Any]], n: int, seen_tpl: set, type_count: Dict[str, int]) -> List[Dict[str, Any]]:
    """배수순으로 n개 (같은 템플릿 중복 금지, 같은 유형은 2개까지)"""
    picked = []
    for r in rows:
        if len(picked) >= n:
            break
        key = _norm(r["pattern_template"])
        if key in seen_tpl or type_count.get(r["hook_type"], 0) >= 2:
            continue
        seen_tpl.add(key)
        type_count[r["hook_type"]] = type_count.get(r["hook_type"], 0) + 1
        picked.append(r)
    return picked


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", (text or "").lower())


def copies_reference(text: str, hints: Optional[HookHints]) -> Optional[str]:
    """후킹이 참고 영상의 원문 대사/자막을 그대로 가져왔으면 그 원문을 반환"""
    if not hints or not text:
        return None
    t = _norm(text)
    if len(t) < 6:
        return None
    for r in hints.references:
        for original in (r.get("spoken_line"), r.get("on_screen_text")):
            o = _norm(original or "")
            if len(o) >= 6 and (t == o or (len(t) >= 8 and (t in o or o in t))):
                return original
    return None
