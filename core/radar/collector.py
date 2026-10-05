"""
core/radar/collector.py - 검색 → 통계 보강 → 채널 구독자 결합 → 점수화

쿼터 순서: search(키워드 x 페이지, 100 units씩) → videos(50개 배치, 1 unit) → channels(50개 배치, 1 unit)
같은 영상이 여러 키워드에 걸리면 처음 걸린 키워드로 한 번만 보강합니다.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from core.radar.scoring import RadarFilters, VideoRecord, parse_iso_duration, rank, score
from core.radar.youtube import QuotaBudgetExceeded, YouTubeClient

logger = logging.getLogger("ad_factory.radar.collector")


def collect(
    client: YouTubeClient,
    vertical: str,
    keywords: List[str],
    days: int = 14,
    pages: int = 1,
    filters: Optional[RadarFilters] = None,
    now: Optional[datetime] = None,
) -> Tuple[List[VideoRecord], Dict[str, int]]:
    """반환: (점수가 매겨진 전체 영상 목록, 통계). 통과 영상은 rank()로 정렬해 사용"""
    filters = filters or RadarFilters()
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    stats = {"searched_keywords": 0, "search_calls": 0, "candidates": 0, "budget_stopped": 0}

    # 1. 검색 (가장 비싼 단계: 예산이 모자라면 남은 키워드는 건너뜀)
    first_keyword: Dict[str, str] = {}
    for kw in keywords:
        token = None
        try:
            for _ in range(pages):
                res = client.search_shorts(kw, since, page_token=token)
                stats["search_calls"] += 1
                for item in res.get("items", []):
                    vid = (item.get("id") or {}).get("videoId")
                    if vid and vid not in first_keyword:
                        first_keyword[vid] = kw
                token = res.get("nextPageToken")
                if not token:
                    break
            stats["searched_keywords"] += 1
        except QuotaBudgetExceeded as e:
            logger.warning(f"쿼터 예산 도달, 남은 키워드 건너뜀: {e}")
            stats["budget_stopped"] = 1
            break
    stats["candidates"] = len(first_keyword)
    if not first_keyword:
        return [], stats

    # 2. 통계 보강 + 3. 채널 구독자
    videos = client.videos(list(first_keyword))
    channel_ids = [v["snippet"]["channelId"] for v in videos]
    channels = {c["id"]: c for c in client.channels(channel_ids)}

    records = []
    for v in videos:
        sn, st, cd = v["snippet"], v.get("statistics", {}), v.get("contentDetails", {})
        ch = channels.get(sn["channelId"], {}).get("statistics", {})
        hidden = bool(ch.get("hiddenSubscriberCount"))
        rec = VideoRecord(
            video_id=v["id"], title=sn.get("title", ""), channel_id=sn["channelId"], channel_title=sn.get("channelTitle", ""),
            published_at=datetime.strptime(sn["publishedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
            duration_sec=parse_iso_duration(cd.get("duration", "")),
            views=int(st.get("viewCount", 0) or 0), likes=int(st.get("likeCount", 0) or 0),
            comments=int(st.get("commentCount", 0) or 0),
            subscribers=None if hidden else int(ch.get("subscriberCount", 0) or 0) or None,
            subscribers_hidden=hidden, keyword=first_keyword[v["id"]], vertical=vertical,
            category_id=str(sn.get("categoryId")) if sn.get("categoryId") else None,
        )
        records.append(score(rec, filters, now))
    stats["outliers"] = len(rank(records))
    return records, stats
