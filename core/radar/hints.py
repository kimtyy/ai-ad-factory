"""
core/radar/hints.py - 레이더 후킹 패턴 → 플래너 씬 1 후킹 힌트

레이더 DB에 쌓인 최근 고배수 쇼츠의 '후킹 구조(pattern_template)'를 골라 대본 프롬프트용 힌트 블록을 만듭니다.
- 기본적으로 실데이터(radar.db)만 사용합니다. dry-run(가짜) 패턴은 allow_dryrun=True일 때만 (테스트용)
- 프롬프트에는 구조(템플릿·유형·이유)만 넘기고, 원문 대사/자막을 그대로 베낀 후킹은 copies_reference로 걸러냅니다.
"""
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.radar import store
from core.radar.hooks import HOOK_TYPE_KO

# 제작 모드/업종 → 레이더 vertical
MODE_TO_VERTICAL = {"travel": "stay"}
OWNER_TYPE_TO_VERTICAL = {"stay": "stay", "food": "food"}   # 'shop'(매장·기타)은 아직 레이더 키워드 세트가 없음


@dataclass
class HookHints:
    vertical: str
    references: List[Dict[str, Any]] = field(default_factory=list)
    source_db: str = ""
    is_dryrun: bool = False

    def __bool__(self) -> bool:
        return bool(self.references)

    @property
    def prompt_block(self) -> str:
        if not self.references:
            return ""
        lines = [f"[최근 떡상 쇼츠 후킹 패턴 참고 - 구독자 대비 조회수 배수가 높은 {len(self.references)}개 영상의 첫 3초 구조]"]
        for i, r in enumerate(self.references, 1):
            lines.append(f"{i}. {HOOK_TYPE_KO.get(r['hook_type'], r['hook_type'])} ({r['outlier_ratio']:.1f}배): "
                         f"구조 \"{r['pattern_template']}\"" + (f" / 먹힌 이유: {r['why_it_works']}" if r.get("why_it_works") else ""))
        lines.append("[힌트 사용 규칙] 씬 1 hook_title(과 필요하면 첫 대사)은 위 패턴 중 이번 사진·사실에 가장 맞는 하나의 '구조'를 골라 "
                     "새 문장으로 쓰세요. 톤앤매너 예시보다 이 패턴 구조가 우선입니다. 문구를 그대로 베끼지 말고, "
                     "{장소}/{숫자} 같은 자리에는 이번 영상의 실제 사진·사실만 넣으세요(사실이 아닌 가격·숫자·순위 금지). "
                     "고른 패턴 번호를 씬 1의 \"hook_pattern_used\" 필드에 정수로 적고, 사실과 맞는 패턴이 하나도 없을 때만 0을 적으세요.")
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
                 "pattern_template": r["pattern_template"]} for r in self.references]


def vertical_for(mode: str, business_type: Optional[str] = None) -> Optional[str]:
    if mode == "owner":
        return OWNER_TYPE_TO_VERTICAL.get(business_type or "")
    return MODE_TO_VERTICAL.get(mode)


def load_hook_hints(vertical: Optional[str], days: int = 30, min_ratio: float = 5.0, limit: int = 4,
                    allow_dryrun: bool = False, db_path: Optional[str] = None) -> HookHints:
    """후킹 패턴이 추출된 최근 고배수 영상 상위 limit개 (같은 템플릿/유형 중복은 줄여 다양하게)"""
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
        finally:
            conn.close()
        # 업종 적합성 판정을 통과한 후킹만 (예능·애니 클립, 판정 이전 레거시 행은 제외)
        rows = [r for r in rows if r.get("pattern_template") and r.get("hook_relevant")
                and (allow_dryrun or r.get("hook_source") != "mock")]
        picked, seen_tpl, type_count = [], set(), {}
        for r in rows:
            key = _norm(r["pattern_template"])
            if key in seen_tpl or type_count.get(r["hook_type"], 0) >= 2:
                continue
            seen_tpl.add(key)
            type_count[r["hook_type"]] = type_count.get(r["hook_type"], 0) + 1
            picked.append(r)
            if len(picked) >= limit:
                break
        if picked:
            return HookHints(vertical=vertical, references=picked, source_db=path,
                             is_dryrun=is_dry or any(r.get("hook_source") == "mock" for r in picked))
    return HookHints(vertical=vertical)


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
