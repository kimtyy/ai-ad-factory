"""
core/radar/keywords.py - 업종(vertical)별 레이더 기본 검색 키워드
"""
from typing import Dict, List

VERTICAL_KEYWORDS: Dict[str, List[str]] = {
    # 리뷰·투어 의도 키워드 5개(정확도) + 대형 트래픽 키워드 2개(모수). 대형 키워드에 섞이는 예능·애니 클립은
    # 카테고리 필터(수집 단계)와 Gemini 적합성 판정(후킹 단계)이 걸러냄
    "stay": ["숙소 브이로그", "풀빌라 투어", "호텔 룸투어", "숙소 리뷰", "감성숙소 추천", "호캉스", "숙소 추천"],
    "food": ["맛집 추천", "가성비 맛집", "숨은 맛집", "웨이팅 맛집", "카페 추천"],
}

VERTICAL_LABELS: Dict[str, str] = {"stay": "🏨 숙소/여행", "food": "🍽 맛집/카페"}

# 기본 수집 기간: 14일은 누적 조회수가 부족해 떡상 판정 모수가 작음 → 30일
DEFAULT_DAYS = 30


def keywords_for(vertical: str) -> List[str]:
    if vertical not in VERTICAL_KEYWORDS:
        raise ValueError(f"알 수 없는 vertical: {vertical!r} (가능: {', '.join(VERTICAL_KEYWORDS)})")
    return list(VERTICAL_KEYWORDS[vertical])
