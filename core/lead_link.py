"""
core/lead_link.py - 랜딩페이지 접수 리드 → 작업실(app_store.py) 자동 연동

/admin 의 [🎬 작업실 열기] 버튼이 붙여 주는 주소 파라미터(store, address, place_id, menu, font, voice, bgm, memo)를 읽어
① 1단계 입력칸(상호·주소·우리 가게 자랑·메뉴판·리뷰) ② 자막 폰트·성우·BGM 초기값 으로 옮긴다.
주소는 누구나 만들 수 있는 외부 입력이므로 길이·문자를 제한한다 (place_id는 숫자만). 네트워크 호출은 하지 않는다 (순수 함수 — 테스트 쉬움).
"""
import hashlib
import re
from typing import Any, Dict, List, Mapping, Optional

MAX_LEN = {"store": 60, "address": 120, "place_id": 15, "menu": 60, "font": 30, "voice": 30, "bgm": 20, "memo": 100}
_CTRL = re.compile(r"[\x00-\x1f\x7f]")

# 랜딩페이지 선택지(site/src/components/PlanBottomSheet.tsx) → 작업실 값
FONT_KEYS = (("임팩트 고딕", "impact"), ("감성 명조", "serif"), ("둥근 예능 팝체", "pop"), ("단단한 붓글씨", "impact"))      # 붓글씨는 작업실에 없어 가장 단단한 고딕으로
VOICE_ACTORS = (("밝은 여성", "female_20s"), ("차분한 남성", "male_30s"))
NO_NARRATION = "나레이션 없음"
BGM_MOODS = (("신나는", "업템포"), ("따뜻한", "어쿠스틱"), ("세련된", "미드템포"))                                           # 'AI 추천'은 그대로 (랜덤)


def _clean(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", _CTRL.sub(" ", str(value if value is not None else ""))).strip()[:limit]


def _first(params: Mapping[str, Any], key: str) -> str:
    v = params.get(key) if hasattr(params, "get") else None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    return v if isinstance(v, str) else ("" if v is None else str(v))


def parse_lead_params(params: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    """주소 파라미터 → 정리된 리드 dict. 상호명(store)이 없으면 None (일반 접속)"""
    try:
        lead = {k: _clean(_first(params, k), n) for k, n in MAX_LEN.items()}
    except Exception:
        return None
    if not lead["store"]:
        return None
    if not re.fullmatch(r"\d{6,15}", lead["place_id"]):
        lead["place_id"] = ""
    return lead


def lead_signature(lead: Mapping[str, str]) -> str:
    """같은 리드로 다시 열었을 때(새로고침·rerun) 자동 조회를 반복하지 않기 위한 식별값"""
    raw = "|".join(lead.get(k, "") for k in MAX_LEN)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def style_from_lead(lead: Mapping[str, str]) -> Dict[str, Optional[str]]:
    """{"font": 'impact'|'serif'|'pop'|None, "actor": 'female_20s'|'male_30s'|None, "audio": 'bgm_only'|None, "bgm_mood": '업템포'…|None} — 모르는 값은 None(기본값 유지)"""
    font = next((key for label, key in FONT_KEYS if (lead.get("font") or "").startswith(label)), None)
    voice = lead.get("voice") or ""
    actor = next((key for label, key in VOICE_ACTORS if voice.startswith(label)), None)
    audio = "bgm_only" if voice.startswith(NO_NARRATION) else None
    mood = next((m for label, m in BGM_MOODS if (lead.get("bgm") or "").startswith(label)), None)
    return {"font": font, "actor": actor, "audio": audio, "bgm_mood": mood}


def voted_keywords(home: Optional[Mapping[str, Any]], limit: int = 3) -> List[str]:
    return [v["keyword"] for v in ((home or {}).get("voted") or [])[:limit] if v.get("keyword")]


def compose_usp(*, official_summary: str = "", memo: str = "", menu: str = "", strengths: Optional[List[str]] = None, limit: int = 400) -> str:
    """'✨ 우리 가게 자랑(USP)' 한 줄: 점주 요청 메모 + 주인공 메뉴 + 네이버 플레이스 공식 특징 + 손님이 꼽은 강점. 있는 것만, 지어내지 않는다.
    다른 매장 정보가 섞이지 않도록 입력은 모두 해당 place_id 의 공식정보·점주가 직접 적은 값이어야 한다"""
    parts: List[str] = []
    if memo:
        parts.append(f"사장님이 강조: {memo}")
    if menu:
        parts.append(f"주인공 메뉴: {menu}")
    if official_summary:
        parts.append(official_summary)
    if strengths:
        parts.append("손님이 꼽은 강점: " + ", ".join(strengths))
    text = " / ".join(parts)
    return text if len(text) <= limit else text[: limit - 1].rstrip(" /,") + "…"


def review_text_from_home(home: Optional[Mapping[str, Any]], limit_each: int = 160) -> str:
    """이 매장(place_id)에 연결된 블로그 후기만 → '손님 리뷰 글' 칸 (한 줄에 후기 하나, 제목 중복 제거)"""
    lines: List[str] = []
    for r in ((home or {}).get("reviews") or [])[:5]:
        body = (r.get("contents") or "").strip()
        title = (r.get("title") or "").strip()
        if body.startswith(title):
            body = body[len(title):].strip()
        text = (f"{title}: {body}" if body else title)[:limit_each].strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def banner_text(lead: Mapping[str, str]) -> str:
    menu = lead.get("menu") or "대표 메뉴"
    return f"📋 접수 리드 자동 연동됨: {lead.get('store', '')} (주인공 메뉴: {menu})"
