"""
core/radar/keywords.py - 업종(vertical)별 레이더 기본 검색 키워드
"""
from typing import Dict, List

VERTICAL_KEYWORDS: Dict[str, List[str]] = {
    "stay": ["숙소 추천", "감성 숙소", "풀빌라 추천", "가성비 호텔", "호캉스"],
    "food": ["맛집 추천", "가성비 맛집", "숨은 맛집", "웨이팅 맛집", "카페 추천"],
}

VERTICAL_LABELS: Dict[str, str] = {"stay": "🏨 숙소/여행", "food": "🍽 맛집/카페"}


def keywords_for(vertical: str) -> List[str]:
    if vertical not in VERTICAL_KEYWORDS:
        raise ValueError(f"알 수 없는 vertical: {vertical!r} (가능: {', '.join(VERTICAL_KEYWORDS)})")
    return list(VERTICAL_KEYWORDS[vertical])
