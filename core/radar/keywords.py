"""
core/radar/keywords.py - 업종(vertical)별 레이더 기본 검색 키워드
"""
from typing import Dict, List

VERTICAL_KEYWORDS: Dict[str, List[str]] = {
    # 리뷰·투어 의도 키워드 5개(정확도) + 대형 트래픽 키워드 2개(모수). 대형 키워드에 섞이는 예능·애니 클립은
    # 카테고리 필터(수집 단계)와 Gemini 적합성 판정(후킹 단계)이 걸러냄
    "stay": ["숙소 브이로그", "풀빌라 투어", "호텔 룸투어", "숙소 리뷰", "감성숙소 추천", "호캉스", "숙소 추천"],
    "food": ["맛집 추천", "가성비 맛집", "숨은 맛집", "웨이팅 맛집", "카페 추천"],
    # 업종 무관 바이럴 트리거 키워드: 알고리즘을 터뜨린 구조만 범용 스켈레톤으로 추출 (mostPopular 차트는
    # 사전 측정 결과 Shorts 5.5%·비음악 Shorts 최고 1.6배라 제외)
    "viral": ["꿀팁", "비밀", "이유", "방법", "절대"],
    # 시골책방(도서 큐레이션): 책 추천·책 속 문장 낭독·책방 숏폼
    "book": ["도서 추천", "인생 책", "시골책방", "책 속 문장"],
}

VERTICAL_LABELS: Dict[str, str] = {"stay": "🏨 숙소/여행", "food": "🍽 맛집/카페", "viral": "🔥 전체 떡상 랭킹 (Viral)",
                                   "book": "📚 도서/책방"}

# 영상 기획(씬 1 힌트)에 직접 연결되는 업종. viral은 이 업종들의 패턴이 부족할 때 보조 힌트로만 쓰임
PLANNER_VERTICALS = ("stay", "food")

# 기본 수집 기간: 14일은 누적 조회수가 부족해 떡상 판정 모수가 작음 → 30일
DEFAULT_DAYS = 30


def keywords_for(vertical: str) -> List[str]:
    if vertical not in VERTICAL_KEYWORDS:
        raise ValueError(f"알 수 없는 vertical: {vertical!r} (가능: {', '.join(VERTICAL_KEYWORDS)})")
    return list(VERTICAL_KEYWORDS[vertical])
