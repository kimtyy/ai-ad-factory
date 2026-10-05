"""
core/radar/scoring.py - 떡상 판정: 구독자 대비 조회수 배수(Outlier Ratio) + 노이즈 필터

outlier_ratio = 조회수 / 구독자 수
  - 구독자 수가 너무 작으면 배수가 쉽게 부풀려지므로 최소 구독자 수(기본 300) 미만은 제외
    (숙소·맛집은 소형 리뷰 크리에이터가 많아 1,000에서 300으로 완화, 대신 최소 조회수로 노이즈 방어)
  - 예능·애니·코미디 등 클립성 카테고리는 실제 리뷰가 아니므로 제외
  - 구독자 비공개 채널은 배수를 계산할 수 없어 제외
  - API의 구독자 수는 유효숫자 3자리로 반올림된 값이라 배수는 근사치
velocity = 업로드 후 하루 평균 조회수 (같은 배수라도 더 빨리 오른 영상을 위로)
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

SHORTS_MAX_SEC = 180

# YouTube 동영상 카테고리 (videos.list snippet.categoryId)
CATEGORY_NAMES = {"1": "영화/애니메이션", "2": "자동차", "10": "음악", "15": "동물", "17": "스포츠", "19": "여행/이벤트",
                  "20": "게임", "22": "인물/블로그", "23": "코미디", "24": "엔터테인먼트", "25": "뉴스/정치",
                  "26": "노하우/스타일", "27": "교육", "28": "과학기술"}
# 실제 리뷰·브이로그가 아닌 클립성 카테고리 (방송·애니 짜깁기, 코미디, 음악, 게임)
CLIP_CATEGORIES = ("1", "10", "20", "23", "24")

# 업종별 필터 기본값. viral은 원본 소재를 지우고 구조만 쓰므로 클립성 카테고리도 허용하되
# 음악(10)은 노래·안무로 터져 추상화할 훅이 없어 제외. 배수는 15배 이상만 (사용자가 더 낮게 줘도 하한 유지)
VERTICAL_FILTER_DEFAULTS = {"viral": {"min_ratio": 15.0, "excluded_categories": ("10",)}}


def min_ratio_for(vertical: str, requested: float) -> float:
    return max(float(requested), VERTICAL_FILTER_DEFAULTS.get(vertical, {}).get("min_ratio", 0.0))


def filters_for(vertical: str, min_ratio: float = 5.0, min_subscribers: int = 300, min_views: int = 30_000) -> "RadarFilters":
    d = VERTICAL_FILTER_DEFAULTS.get(vertical, {})
    return RadarFilters(min_ratio=min_ratio_for(vertical, min_ratio), min_subscribers=min_subscribers, min_views=min_views,
                        excluded_categories=d.get("excluded_categories", CLIP_CATEGORIES))


@dataclass
class RadarFilters:
    min_ratio: float = 5.0
    min_subscribers: int = 300
    min_views: int = 30_000
    max_duration_sec: int = SHORTS_MAX_SEC
    excluded_categories: tuple = CLIP_CATEGORIES


@dataclass
class VideoRecord:
    video_id: str
    title: str
    channel_id: str
    channel_title: str
    published_at: datetime
    duration_sec: int
    views: int
    likes: int
    comments: int
    subscribers: Optional[int]
    subscribers_hidden: bool
    keyword: str
    vertical: str
    category_id: Optional[str] = None
    outlier_ratio: Optional[float] = None
    velocity_per_day: Optional[float] = None
    reject_reason: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/shorts/{self.video_id}"


def parse_iso_duration(value: str) -> int:
    """ISO 8601 기간(PT1M5S, PT45S, PT1H2M) → 초"""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def score(record: VideoRecord, filters: RadarFilters, now: Optional[datetime] = None) -> VideoRecord:
    """배수/속도 계산 후 탈락 사유(reject_reason)를 기록. 통과하면 reject_reason=None"""
    now = now or datetime.now(timezone.utc)
    age_days = max((now - record.published_at).total_seconds() / 86400, 1 / 24)
    record.velocity_per_day = round(record.views / age_days, 1)
    if record.subscribers and not record.subscribers_hidden:
        record.outlier_ratio = round(record.views / record.subscribers, 2)

    if record.duration_sec > filters.max_duration_sec:
        record.reject_reason = f"Shorts 아님 ({record.duration_sec}초 > {filters.max_duration_sec}초)"
    elif record.category_id in filters.excluded_categories:
        record.reject_reason = f"클립성 카테고리 ({CATEGORY_NAMES.get(record.category_id, record.category_id)})"
    elif record.subscribers_hidden or not record.subscribers:
        record.reject_reason = "구독자 수 비공개/없음"
    elif record.subscribers < filters.min_subscribers:
        record.reject_reason = f"소형 채널 (구독자 {record.subscribers:,} < {filters.min_subscribers:,})"
    elif record.views < filters.min_views:
        record.reject_reason = f"조회수 부족 ({record.views:,} < {filters.min_views:,})"
    elif record.outlier_ratio < filters.min_ratio:
        record.reject_reason = f"배수 미달 ({record.outlier_ratio}x < {filters.min_ratio}x)"
    else:
        record.reject_reason = None
    return record


def rank(records: List[VideoRecord]) -> List[VideoRecord]:
    """통과 영상을 배수 → 속도 순으로 정렬"""
    passed = [r for r in records if r.reject_reason is None]
    return sorted(passed, key=lambda r: (r.outlier_ratio or 0, r.velocity_per_day or 0), reverse=True)
