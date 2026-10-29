"""
core/social_packages.py - 매장 홍보 영상 1개 → 4대 숏폼 플랫폼(유튜브 쇼츠·인스타 릴스·네이버 클립·틱톡) 업로드 텍스트 패키지

입력은 1단계 매장 정보(상호·지역·주소·플레이스 링크·대표 메뉴)와 2~3단계 대본뿐이다. 업소별 하드코딩은 없고,
업종 맥락(core.venue_context)에 따라 '술집/맛집/카페' 해시태그와 저장 유도 문구만 달라진다.
확인되지 않은 정보(영업시간 등)는 지어내지 않고 플레이스 안내 문구로 대신한다.
"""
import re
from typing import Any, Dict, List, Optional

from core.shorts_meta import naver_map_link
from core.venue_context import detect

PLATFORMS = ("youtube", "instagram", "naver", "tiktok")
LABELS = {"youtube": "🔴 유튜브 쇼츠", "instagram": "📸 인스타그램 릴스", "naver": "🟢 네이버 클립", "tiktok": "🎵 틱톡"}

_KIND_WORD = {"bar": "술집", "cafe": "카페", "meat": "맛집", "general": "맛집"}
_KIND_SAVE = {
    "bar": "오늘 저녁 시원하게 한잔 생각날 때 꼭 저장해두고 찾아가 보세요! 📌",
    "cafe": "여유로운 커피 한 잔 생각날 때 꼭 저장해두고 찾아가 보세요! 📌",
    "meat": "오늘 뭐 먹지 고민될 때 꼭 저장해두고 찾아가 보세요! 📌",
    "general": "나중에 또 생각날 때 꼭 저장해두고 찾아가 보세요! 📌",
}
_KIND_TAGS = {
    "bar": ["술스타그램", "맥주스타그램", "안주맛집"],
    "cafe": ["카페스타그램", "카페투어", "디저트맛집"],
    "meat": ["먹스타그램", "점심추천", "저녁추천"],
    "general": ["먹스타그램", "동네맛집", "맛집탐방"],
}
_TIKTOK_END = {"bar": "오늘 저녁 한잔은 여기서 해결!", "cafe": "오늘 커피는 여기서 해결!"}


def _region_word(address: str) -> str:
    """주소 → 해시태그용 지역명 (경기 가평군 … → 가평, 서울 성동구 … → 성동)"""
    parts = (address or "").replace(",", " ").split()
    if not parts:
        return ""
    skip = {"서울", "서울특별시", "부산", "부산광역시", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "경기도", "강원", "강원도", "충북", "충남", "전북", "전남", "경북", "경남", "제주", "제주도"}
    rest = [p for p in parts if p not in skip and not re.search(r"(특별시|광역시|특별자치\S*|도)$", p)]
    word = (rest[0] if rest else parts[0]).strip()
    m = re.match(r"^(\S{2,}?)(시|군|구)$", word)
    return m.group(1) if m else word


def _tag(s: str) -> str:
    return "#" + re.sub(r"[\s#,]+", "", s or "")


def _sentences(scenes: List[Dict[str, Any]]) -> List[str]:
    return [re.sub(r"\s+", " ", str(s.get("voiceover") or "")).strip() for s in (scenes or []) if str(s.get("voiceover") or "").strip()]


def _hook(scenes: List[Dict[str, Any]], name: str) -> str:
    first = (scenes or [{}])[0]
    h = str(first.get("hook_title") or "").replace("\n", " ").strip()
    if h:
        return h
    ls = _sentences(scenes)
    return ls[0] if ls else f"{name}, 지금 가봐야 할 곳"


def _dedupe(tags: List[str]) -> List[str]:
    seen, out = set(), []
    for t in tags:
        t = t.strip()
        if len(t) > 1 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def build_social_packages(*, name: str, address: str = "", scenes: Optional[List[Dict[str, Any]]] = None, business_type: str = "",
                          usp: str = "", place_link: str = "", menus: Optional[List[str]] = None, hours: str = "",
                          core_concept: str = "") -> Dict[str, Dict[str, Any]]:
    """{플랫폼: {title, body, comment, tags, tip}} — body는 그대로 붙여넣는 본문 전체(제목은 따로)"""
    name = (name or "").strip()
    scenes = scenes or []
    kind = detect(name=name, usp=f"{usp} {core_concept}", tags=menus or [])
    region = _region_word(address)
    word = _KIND_WORD[kind]
    link = (place_link or "").strip() or naver_map_link(name)
    hook = _hook(scenes, name)
    lines = _sentences(scenes)
    summary = " ".join(lines)
    short3 = "\n".join(lines[:3]) if lines else (usp or "").strip()
    menu = (menus or [""])[0].strip() if menus else ""
    addr = (address or "").strip() or "네이버 플레이스에서 확인해 주세요"
    hours_line = (hours or "").strip() or "네이버 플레이스에서 확인해 주세요"
    region_loc = region or "동네"
    name_tag = _tag(name)

    # ---------------- 유튜브 쇼츠
    yt_tags = _dedupe(["#Shorts", _tag(f"{region}맛집") if region else "", _tag(f"{word}추천") if word != "맛집" else "", name_tag])
    yt_title = f"{hook} | {name}"
    shown = " ".join(t for t in ["#Shorts", _tag(f"{region}맛집") if region else ""] if t)
    yt_title = f"{yt_title} {shown}".strip()
    if len(yt_title) > 100:
        room = 100 - len(f" | {name} {shown}")
        yt_title = f"{hook[:max(room - 1, 8)].rstrip()}… | {name} {shown}".strip()
    yt_body = "\n".join([summary or hook, "", f"📍 위치: {addr}", f"⏰ 영업시간: {hours_line}", f"🔗 네이버 지도: {link}", "", " ".join(yt_tags)])
    yt_comment = "\n".join([f"📌 {name} 매장 정보", f"• 주소: {addr}", f"• 네이버 플레이스에서 메뉴와 길찾기를 확인해 보세요! 👉 {link}"])

    # ---------------- 인스타 릴스
    ig_tags = _dedupe([_tag(f"{region}맛집") if region else "", _tag(f"{region}{word}") if region and word != "맛집" else "", name_tag, _tag(menu) if menu else "",
                       "#릴스추천", "#핫플추천", "#먹스타그램", "#동네맛집", _tag(f"{word}추천"),
                       _tag(f"{region}가볼만한곳") if region else "", _tag(f"{region}데이트") if region else "", "#맛집추천", "#맛집탐방",
                       "#오늘뭐먹지", "#릴스", "#맛스타그램"] + [_tag(t) for t in _KIND_TAGS[kind]])[:16]
    ig_body = "\n".join([f"{hook} ✨", "", short3, "", _KIND_SAVE[kind], "", f"📍 {name} ({region_loc})", f"• 주소: {addr}", "", ".", ".", " ".join(ig_tags)])
    ig_comment = ""

    # ---------------- 네이버 클립
    point = menu or (usp or "").strip().split("\n")[0][:20] or hook
    nv_title = " ".join(x for x in [region, name] if x) + f" - {point}"
    nv_tags = _dedupe(["#네이버클립", "#클립크리에이터", _tag(f"{region}가볼만한곳") if region else "", _tag(f"{region}맛집") if region else "", name_tag])
    nv_body = "\n".join([summary or hook, "", f"네이버 지도에서 '{name}'을(를) 검색하시면 자세한 위치와 혜택을 보실 수 있습니다!", "", " ".join(nv_tags)])
    nv_tip = ("💡 \"네이버 앱에서 클립 등록 시 꼭 [📍 장소] 버튼을 눌러 우리 매장 플레이스를 스티커로 붙여주세요! "
              "영상 시청자가 즉시 길찾기·예약으로 이어져 플레이스 순위가 올라갑니다.\"")

    # ---------------- 틱톡
    tk_tags = _dedupe(["#틱톡푸드", "#맛집추천", "#먹스타", "#kfood", _tag(f"{region}맛집") if region else "", name_tag, "#fyp"])
    tk_end = _TIKTOK_END.get(kind, "오늘 한 끼는 여기서 해결!")
    tk_body = "\n".join([f"{hook} 🤤🔥", "", f"{region_loc} {name}에서 {tk_end}", "", " ".join(tk_tags)])

    return {
        "youtube": {"title": yt_title, "body": yt_body, "comment": yt_comment, "tags": yt_tags, "tip": ""},
        "instagram": {"title": "", "body": ig_body, "comment": ig_comment, "tags": ig_tags, "tip": ""},
        "naver": {"title": nv_title, "body": nv_body, "comment": "", "tags": nv_tags, "tip": nv_tip},
        "tiktok": {"title": "", "body": tk_body, "comment": "", "tags": tk_tags, "tip": ""},
    }
