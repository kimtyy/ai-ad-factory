"""
core/venue_context.py - 업종별 '실제 손님 방문 맥락' (컨셉 제안·대본이 지명만 보고 관광지 식당처럼 쓰는 것을 막는다)

예: 가평에 있는 동네 생맥주집을 '나들이 가서 시원한 맥주 한 잔', '주말 외식 장소'처럼 쓰면 현실과 어긋난다. 이 가게의 손님은
'저녁 먹고 2차' 또는 '퇴근 후 밥 대신 안주에 한 잔'이다. 업종을 판별해 (1) 컨셉 제안 프롬프트에 방문 맥락을 강제하고 (2) 금지어가 컨셉·대본에 들어가면 걸러낸다.

  bar    맥주집·호프·포차·이자카야·술집 — 저녁 먹고 2차 / 퇴근 후 1차(밥 대신 안주). 금지: 나들이·관광·여행길·드라이브·주말 외식
  meat   고깃집·식당·밥집 — 든든한 저녁 식사 / 가족 외식·회식 / 밥도둑 메뉴
  cafe   카페·베이커리 — 식후 커피·디저트 타임 / 여유로운 대화·분위기
  general 그 밖 (맥락 강제 없음)
"""
import re
from typing import Any, Dict, Iterable, List, Optional

ALCOHOL = ("맥주", "호프", "포차", "이자카야", "술집", "펍", "생맥", "소주", "막걸리", "하이볼", "와인", "칵테일", "위스키", "사케", "안주", "먹태", "골뱅이")
BAR_NAME = ("호프", "포차", "이자카야", "술집", "펍", "맥주", "주점", "bar", "pub")
MEAT = ("고기", "갈비", "삼겹", "구이", "불판", "한식", "식당", "백반", "찌개", "국밥", "냉면", "밥집", "한상", "정식", "쌈")
CAFE = ("카페", "커피", "베이커리", "디저트", "케이크", "빵", "라떼", "브런치", "마카롱", "아메리카노")

CONTEXTS: Dict[str, Dict[str, Any]] = {
    "bar": {
        "label": "맥주집·술집",
        "visits": ["저녁 먹고 2차: 저녁 먹고 그냥 들어가기 아쉬울 때, 동네에서 시원하게 한 잔 더",
                   "퇴근 후 1차: 밥 대신 맛있는 안주에 시원한 맥주 한 잔으로 하루를 마무리"],
        "banned": ("나들이", "관광", "여행길", "드라이브", "주말 외식", "외식 장소", "가족 외식", "여행 가", "여행 중", "나들이객"),
    },
    "meat": {
        "label": "고깃집·식당",
        "visits": ["든든한 저녁 식사", "가족 외식·회식", "밥도둑 메뉴로 밥 한 공기 뚝딱"],
        "banned": (),
    },
    "cafe": {
        "label": "카페·베이커리",
        "visits": ["식후 커피·디저트 타임", "여유로운 대화·분위기"],
        "banned": (),
    },
    "general": {"label": "일반 매장", "visits": [], "banned": ()},
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def detect(name: str = "", usp: str = "", tags: Optional[Iterable[str]] = None, extra: str = "") -> str:
    """상호·특장점·사진 속 피사체·메뉴 텍스트에서 업종 맥락 판별 → 'bar' / 'meat' / 'cafe' / 'general'"""
    tag_text = " ".join(tags or [])
    whole = _norm(f"{name} {usp} {tag_text} {extra}")
    name_n = _norm(name)
    alc = sum(1 for w in ALCOHOL if w in whole)
    meat = sum(1 for w in MEAT if w in whole)
    cafe = sum(1 for w in CAFE if w in whole)
    if any(w in name_n for w in BAR_NAME) or (alc >= 2 and alc >= meat) or (alc >= 1 and "맥주" in whole and meat == 0 and cafe == 0):
        return "bar"
    if cafe >= 2 and cafe > meat:
        return "cafe"
    if meat >= 1:
        return "meat"
    return "general"


def banned_words(kind: str) -> tuple:
    return tuple(CONTEXTS.get(kind, CONTEXTS["general"])["banned"])


def find_violation(text: str, kind: str) -> Optional[str]:
    """이 업종에서 현실과 어긋나는 방문 맥락어가 문장에 있으면 그 말 (없으면 None)"""
    t = text or ""
    return next((w for w in banned_words(kind) if w in t), None)


def prompt_block(kind: str) -> str:
    """컨셉 제안·대본 프롬프트에 넣는 '업종 현실 맥락' 블록 (일반 매장이면 빈 문자열)"""
    c = CONTEXTS.get(kind)
    if not c or kind == "general":
        return ""
    lines = [f"[업종 현실 맥락 — 최우선 규칙] 이 가게는 '{c['label']}'입니다. 지명(예: 가평·강릉)만 보고 관광지 식당처럼 쓰지 말고, 실제 손님이 찾는 순간만 쓰세요."]
    lines += [f"- 실제 방문 맥락: {v}" for v in c["visits"]]
    if c["banned"]:
        lines.append("- 절대 쓰지 말 것: " + ", ".join(c["banned"]))
    return "\n".join(lines)


def signature_drink(tags: Optional[Iterable[str]] = None, usp: str = "") -> str:
    """사진·특장점에 나온 대표 술 이름 (예: 눈꽃맥주). 없으면 '시원한 맥주'"""
    text = " ".join(tags or []) + " " + (usp or "")
    m = re.search(r"([가-힣A-Za-z]{0,6}(?:생맥주|맥주|막걸리|하이볼|소주|사케|와인))", text)
    return m.group(1) if m else "시원한 맥주"


def fallback_concepts(kind: str, name: str = "", tags: Optional[Iterable[str]] = None, usp: str = "") -> Optional[List[Dict[str, str]]]:
    """AI 호출이 안 될 때(Mock·실패)의 업종 맞춤 기본 컨셉 3개. 일반 매장은 None (호출자의 기본안을 씀)"""
    if kind == "bar":
        drink = signature_drink(tags, usp)
        return [
            {"id": "second_round", "angle": "저녁 먹고 2차", "badge": "🌙 저녁 먹고 2차", "headline": "그냥 들어가긴 아쉬운 밤",
             "core_concept": f"저녁 먹고 들어가기 아쉬울 때, 동네에서 시원한 {drink} 한 잔 더",
             "description": "저녁 식사 뒤 2차로 가볍게 들르는 동네 술집의 맥락"},
            {"id": "after_work_first", "angle": "퇴근 후 1차", "badge": "🍻 퇴근 후 밥 대신 1차", "headline": "밥 대신 안주 어때요?",
             "core_concept": f"퇴근 후 밥 대신 푸짐한 안주에 시원한 {drink} 한 잔",
             "description": "저녁 대신 안주로 배를 채우며 하루를 마무리하는 1차 맥락"},
            {"id": "lineup", "angle": "찰떡궁합 안주 라인업", "badge": "🍢 안주 라인업", "headline": "이 맥주엔 이 안주죠",
             "core_concept": f"{drink}와 찰떡궁합인 안주 라인업",
             "description": "올려 주신 사진 속 안주들로 100% 뒷받침되는 조합 컨셉"},
        ]
    return None
