"""
core/radar - 쇼츠 레이더: 최근 떡상 쇼츠(구독자 대비 조회수 배수) 수집 + 첫 3초 후킹 구조 추출
  CLI: python -m core.radar.run --vertical stay --days 14  (키 없으면 자동 dry-run)
"""
from core.radar.keywords import VERTICAL_KEYWORDS, keywords_for
from core.radar.scoring import RadarFilters, VideoRecord, rank, score
