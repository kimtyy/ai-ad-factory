"""
core/memory_pack.py - 🎉 모임·행사·추억 쇼츠의 제목·썸네일 카피·해시태그·공유 문구

매장 홍보용 shorts_meta/seo_generator/social_packages는 가게 추천·위치·메뉴 기준이라 쓰지 않고, 추억 영상 전용으로 따로 만든다.
- 카카오톡 단톡방 공유 멘트가 첫 탭 (가족·친구에게 보내는 영상이 기본 용도)
- 유튜브·인스타·네이버 클립·틱톡 탭: 제목(있으면 모임 이름) + 짧은 본문 + 해시태그. 위치·검색 유도·판촉 문구 없음
"""
import re
from typing import Any, Dict, List, Optional

from core.planners.memory import DEFAULT_TONE, TONES, tone_of
from core.shorts_meta import fit_copy, THUMB_SUB_MAX, TITLE_BODY_MAX

THUMB_MAIN_MAX = 18               # 모임 이름은 두 줄(줄당 약 9자)로 들어가 뜻이 잘리지 않게 (매장 썸네일은 14자). 넘으면 단어 경계에서 자름

KAKAO_TEMPLATE = "우리 {name} 영상 하나 만들어봤어! 다들 너무 반가웠고 다음 모임 때 또 보자~ 🎬✨"
KAKAO_NAMELESS = "우리 모임 영상 하나 만들어봤어! 다들 너무 반가웠고 다음 모임 때 또 보자~ 🎬✨"

THUMB_BY_TONE = {                 # 모임 이름이 없을 때 썸네일 큰 글씨 (사실 주장 없음)
    "lively": ("우리 모임 하이라이트", "웃음 가득한 하루"),
    "warm": ("함께한 하루", "오래 남을 추억"),
    "formal": ("오늘의 기록", "함께해서 뜻깊은 날"),
}
TAGS_BASE = ["#추억", "#모임", "#우리끼리", "#일상기록", "#쇼츠"]
TAGS_BY_TONE = {
    "lively": ["#친구모임", "#동창회", "#웃음가득", "#단체사진"],
    "warm": ["#가족모임", "#가족여행", "#힐링", "#소중한순간"],
    "formal": ["#동네행사", "#마을축제", "#기록", "#함께해요"],
}
PLATFORM_TIPS = {
    "youtube": "가족·친구 영상은 '일부 공개'로 올리면 링크를 아는 사람만 볼 수 있어요. 사진에 나온 분들께 올려도 되는지 먼저 물어봐 주세요.",
    "instagram": "스토리·릴스로 올릴 땐 사진 속 분들께 동의를 받아 주세요. 아이 얼굴이 나오면 비공개나 친구 공개를 권해요.",
    "naver": "네이버 클립은 공개 영상이에요. 행사 참가자 모두에게 공개해도 되는지 확인해 주세요.",
    "tiktok": "틱톡은 공개 범위를 '친구'로 두면 아는 사람만 볼 수 있어요.",
}
LABELS = {"kakao": "💬 카카오톡 단톡방", "youtube": "▶ 유튜브 쇼츠", "instagram": "📸 인스타 릴스", "naver": "🟢 네이버 클립", "tiktok": "🎵 틱톡"}
PLATFORMS = ("kakao", "youtube", "instagram", "naver", "tiktok")


def _tag(s: str) -> str:
    return "#" + re.sub(r"[^0-9A-Za-z가-힣]", "", s or "")


def title_tags(title: str) -> List[str]:
    """모임 이름의 단어를 해시태그로 (2~8자 단어 최대 2개)"""
    out = []
    for w in re.findall(r"[가-힣A-Za-z]{2,8}", title or ""):
        t = _tag(w)
        if len(t) > 2 and t not in out:
            out.append(t)
    return out[:2]


def hashtags_for(title: str, tone: str) -> List[str]:
    tags: List[str] = []
    for t in title_tags(title) + TAGS_BY_TONE[tone_of(tone)] + TAGS_BASE:
        if t not in tags:
            tags.append(t)
    return tags[:7]


def kakao_message(title: str = "") -> str:
    name = (title or "").strip()
    return KAKAO_TEMPLATE.format(name=name) if name else KAKAO_NAMELESS


def memory_copy(title: str = "", tone: str = DEFAULT_TONE, scenes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """make_shorts_copy와 같은 모양: title/thumb_main/thumb_sub/accent/hashtags/tag_set (렌더 썸네일·업로드 문구용)"""
    tone = tone_of(tone)
    name = (title or "").strip()
    main_default, sub_default = THUMB_BY_TONE[tone]
    thumb_main = fit_copy(name, THUMB_MAIN_MAX) if name else main_default
    thumb_sub = sub_default if name else TONES[tone]["label"].split(" ", 1)[1]
    video_title = (name or main_default)
    tags = hashtags_for(name, tone)
    return {
        "title": re.sub(r"\s*#shorts\b", "", f"{video_title}", flags=re.I)[:TITLE_BODY_MAX].rstrip() + " #Shorts",
        "thumb_main": thumb_main, "thumb_sub": fit_copy(thumb_sub, THUMB_SUB_MAX), "accent": "",
        "hashtags": tags, "comment_question": "", "tag_set": {"core": tags, "pool": tags}, "source": "memory",
    }


def seo_metadata(title: str, tone: str, scenes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """app이 읽는 SEO 필드(title/description/tags)만 채운 메타데이터"""
    c = memory_copy(title, tone)
    lines = [str(s.get("voiceover") or "").strip() for s in (scenes or []) if s.get("voiceover")]
    desc = "\n".join([f"【 {(title or '').strip() or c['thumb_main']} 】", *([" / ".join(lines[:3])] if lines else []), "", " ".join(c["hashtags"])]).strip()
    return {"title": c["title"], "description": desc, "tags": ", ".join(t.lstrip("#") for t in c["hashtags"])}


def build_memory_packages(title: str = "", tone: str = DEFAULT_TONE, scenes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Dict[str, str]]:
    """탭별 {title, body, comment, tip}: 카카오톡 단톡방 멘트가 첫 탭"""
    c = memory_copy(title, tone, scenes)
    tags = " ".join(c["hashtags"])
    name = (title or "").strip() or c["thumb_main"]
    pk: Dict[str, Dict[str, str]] = {
        "kakao": {"title": "", "body": kakao_message(title), "comment": "",
                  "tip": "영상을 갤러리에 저장한 뒤 단톡방에 올리고, 이 멘트를 붙여 넣어 보내세요."},
    }
    for k in PLATFORMS[1:]:
        body = f"{name}\n함께한 순간을 담았어요.\n\n{tags}"
        pk[k] = {"title": c["title"] if k == "youtube" else name, "body": body, "comment": "", "tip": PLATFORM_TIPS[k]}
    return pk
