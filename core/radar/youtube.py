"""
core/radar/youtube.py - YouTube Data API v3 최소 래퍼 (requests 기반, 추가 의존성 없음)

쿼터 비용 (기본 일일 한도 10,000 units, 실제 한도는 Google Cloud 콘솔에서 확인):
  search.list   100 units / 호출   ← 가장 비쌈: 키워드당 페이지 수를 최소화
  videos.list     1 unit  / 호출 (id 최대 50개 배치)
  channels.list   1 unit  / 호출 (id 최대 50개 배치)
호출 전에 예상 비용을 예산(budget)과 비교해 초과하면 QuotaBudgetExceeded를 던져 과금/한도 초과를 막습니다.
"""
import hashlib
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests

logger = logging.getLogger("ad_factory.radar.youtube")

API_BASE = "https://www.googleapis.com/youtube/v3"
QUOTA_COST = {"search": 100, "videos": 1, "channels": 1}
BATCH_SIZE = 50


class QuotaBudgetExceeded(RuntimeError):
    """이번 실행에 허용한 쿼터 예산을 넘는 호출을 시도함 (호출하지 않고 중단)"""


class YouTubeAPIError(RuntimeError):
    pass


def _chunks(items: List[str], size: int = BATCH_SIZE) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


class YouTubeClient:
    def __init__(self, api_key: str, budget_units: int = 3000, timeout: int = 15, max_retries: int = 3):
        if not api_key:
            raise ValueError("YOUTUBE_API_KEY가 필요합니다 (.env). 키가 없으면 --dry-run으로 실행하세요.")
        self.api_key = api_key
        self.budget_units = budget_units
        self.used_units = 0
        self.timeout = timeout
        self.max_retries = max_retries
        self.mode = "live"

    # ------------------------------------------------------------- 공통 호출
    def _get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        cost = QUOTA_COST[endpoint]
        if self.used_units + cost > self.budget_units:
            raise QuotaBudgetExceeded(f"{endpoint} 호출 시 예산 초과 ({self.used_units}+{cost} > {self.budget_units} units)")
        q = {**params, "key": self.api_key}
        for attempt in range(self.max_retries):
            resp = requests.get(f"{API_BASE}/{endpoint}", params=q, timeout=self.timeout)
            if resp.status_code == 200:
                self.used_units += cost
                return resp.json()
            reason = ""
            try:
                reason = resp.json()["error"]["errors"][0].get("reason", "")
            except Exception:
                pass
            if resp.status_code == 403 and reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                raise QuotaBudgetExceeded(f"YouTube API 일일 쿼터 소진: {reason}")
            if resp.status_code >= 500 and attempt < self.max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise YouTubeAPIError(f"{endpoint} HTTP {resp.status_code} {reason}: {resp.text[:200]}")
        raise YouTubeAPIError(f"{endpoint} 재시도 초과")

    # ------------------------------------------------------------- 엔드포인트
    def search_shorts(self, keyword: str, published_after: datetime, max_results: int = 50,
                      page_token: Optional[str] = None, region: str = "KR", language: str = "ko") -> Dict[str, Any]:
        """최근 업로드된 짧은 영상 검색 (videoDuration=short: 4분 미만 → 이후 180초 이하로 다시 거름)"""
        params = {
            "part": "snippet", "q": keyword, "type": "video", "videoDuration": "short",
            "publishedAfter": published_after.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "order": "viewCount", "regionCode": region, "relevanceLanguage": language,
            "maxResults": max_results,
        }
        if page_token:
            params["pageToken"] = page_token
        return self._get("search", params)

    def videos(self, video_ids: List[str]) -> List[Dict[str, Any]]:
        items = []
        for chunk in _chunks(list(dict.fromkeys(video_ids))):
            items += self._get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(chunk)}).get("items", [])
        return items

    def channels(self, channel_ids: List[str]) -> List[Dict[str, Any]]:
        items = []
        for chunk in _chunks(list(dict.fromkeys(channel_ids))):
            items += self._get("channels", {"part": "snippet,statistics", "id": ",".join(chunk)}).get("items", [])
        return items


class MockYouTubeClient(YouTubeClient):
    """
    키 없이 파이프라인 전체를 검증하기 위한 결정적(seed 고정) 가짜 클라이언트 (dry-run).
    실제 응답과 같은 JSON 구조를 돌려주며, 필터 검증용 잡음(소형 채널, 구독자 비공개, 긴 영상)을 섞습니다.
    """

    def __init__(self, budget_units: int = 3000, seed: int = 7):
        self.budget_units = budget_units
        self.used_units = 0
        self.mode = "dry-run"
        self._rng = random.Random(seed)
        self._videos: Dict[str, Dict[str, Any]] = {}
        self._channels: Dict[str, Dict[str, Any]] = {}

    def _spend(self, endpoint: str):
        cost = QUOTA_COST[endpoint]
        if self.used_units + cost > self.budget_units:
            raise QuotaBudgetExceeded(f"{endpoint} 호출 시 예산 초과 ({self.used_units}+{cost} > {self.budget_units} units)")
        self.used_units += cost

    def search_shorts(self, keyword, published_after, max_results=50, page_token=None, region="KR", language="ko"):
        self._spend("search")
        now = datetime.now(timezone.utc)
        span_h = max(1, int((now - published_after).total_seconds() // 3600))
        items = []
        for i in range(min(max_results, 12)):
            vid = "mock" + hashlib.md5(f"{keyword}{page_token}{i}".encode()).hexdigest()[:7]
            ch = "UCmock" + hashlib.md5(f"{keyword}{i % 7}".encode()).hexdigest()[:8]
            r = self._rng.random()
            subs = self._rng.choice([450, 2_300, 8_100, 15_000, 42_000, 120_000, 610_000])
            # 약 30%는 구독자 대비 폭발(5~40배), 나머지는 평범(0.05~2배)
            ratio = self._rng.uniform(5, 40) if r < 0.3 else self._rng.uniform(0.05, 2.0)
            published = now - timedelta(hours=self._rng.randint(6, span_h))
            duration = self._rng.choice([14, 22, 31, 45, 58, 59, 75]) if i % 9 else 240   # 일부는 Shorts 기준(180초) 초과
            title = f"[{keyword}] " + self._rng.choice([
                "여기 진짜 한국 맞아요?", "이 가격에 이게 된다고?", "현지인만 아는 곳 공개", "웨이팅 2시간 실화",
                "모르면 손해 보는 곳", "POV: 문 열자마자", "3초 만에 예약 마감된 이유"])
            category = self._rng.choice(["19", "19", "22", "22", "26", "24"])   # 약 1/6은 엔터테인먼트(클립성)
            self._videos[vid] = {
                "id": vid,
                "snippet": {"title": title, "channelId": ch, "channelTitle": f"채널{ch[-4:]}", "categoryId": category,
                            "publishedAt": published.strftime("%Y-%m-%dT%H:%M:%SZ"), "description": "#shorts"},
                "statistics": {"viewCount": str(int(subs * ratio)), "likeCount": str(int(subs * ratio * 0.04)),
                               "commentCount": str(int(subs * ratio * 0.002))},
                "contentDetails": {"duration": f"PT{duration // 60}M{duration % 60}S" if duration >= 60 else f"PT{duration}S"},
            }
            self._channels.setdefault(ch, {
                "id": ch, "snippet": {"title": f"채널{ch[-4:]}"},
                "statistics": {"subscriberCount": str(subs), "hiddenSubscriberCount": i % 11 == 10, "videoCount": "120"},
            })
            items.append({"id": {"videoId": vid}, "snippet": self._videos[vid]["snippet"]})
        return {"items": items, "nextPageToken": None if page_token else f"{keyword}-p2"}

    def videos(self, video_ids):
        ids = list(dict.fromkeys(video_ids))
        for _ in _chunks(ids):
            self._spend("videos")
        return [self._videos[v] for v in ids if v in self._videos]

    def channels(self, channel_ids):
        ids = list(dict.fromkeys(channel_ids))
        for _ in _chunks(ids):
            self._spend("channels")
        return [self._channels[c] for c in ids if c in self._channels]
