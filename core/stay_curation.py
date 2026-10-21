"""
core/stay_curation.py - 숙소 제휴 1단계: '인기 숙소 TOP 5' 자동 큐레이션 (하드코딩 목록 없음)

데이터랩은 키워드 '상대 검색량'만 주므로 두 단계로 합친다.
  ① 네이버 데이터랩 검색어트렌드(POST /search-trend/v1/search): 테마 키워드(예: '가평 풀빌라')의 최근 30일 일별 비율
     → 시즌 급상승 = 최근 7일 평균 ÷ 앞선 23일 평균, 스테디 = 30일 평균. 점수가 높은 테마 키워드 2개를 고른다.
  ② 네이버 지역 검색 + 블로그 검색: 고른 키워드로 실제 숙박업소(상호·주소)를 찾고, 블로그 후기 수(총량·최근 30일)로 순위를 매겨 TOP 5.
     핵심 매력 한 줄은 블로그 요약문에 실제로 나온 시설 단어(개별 온수풀·불멍 등)만으로 만든다.
API가 꺼져 있거나 실패하면 지어낸 목록 대신 이유를 돌려준다 (트렌드만 실패하면 테마 기본 키워드 순서로 지역·블로그 검색은 계속).
"""
import datetime as dt
import html
import logging
import re
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("ad_factory.stay_curation")

HUB = "https://naverapihub.apigw.ntruss.com"
TREND_URL = HUB + "/search-trend/v1/search"
LEGACY_TREND_URL = "https://openapi.naver.com/v1/datalab/search"
TREND_NOT_ENABLED = ("이 NAVER API HUB 앱에 '검색어트렌드 API(Data Lab)'가 추가되어 있지 않아요. NCP 콘솔 → NAVER API HUB → 앱 수정에서 "
                     "'검색어트렌드'를 체크해 주세요 (키는 그대로). 지금은 테마 기본 키워드 순서로 찾았어요.")

STAY_WORDS = ("숙박", "펜션", "호텔", "모텔", "게스트하우스", "캠핑", "글램핑", "리조트", "민박", "스테이", "풀빌라", "한옥", "카라반", "독채", "빌라")
NOT_STAY_WORDS = ("음식점", "카페", "식당", "주차장", "편의점", "미용", "병원", "약국", "학원", "마트")

# 지역/테마 드롭다운: 각 항목은 데이터랩에 넣을 '지역 테마' 키워드(최대 5개). 순위는 검색량으로 정해지고 이 목록은 후보 풀일 뿐이다.
PRESETS: Dict[str, List[str]] = {
    "전체(전국 핫플)": ["가평 풀빌라", "강릉 오션뷰 숙소", "경주 한옥 스테이", "양평 글램핑", "제주 풀빌라"],
    "가평·춘천 풀빌라/펜션": ["가평 풀빌라", "가평 독채 펜션", "춘천 풀빌라", "춘천 독채 펜션", "가평 감성 숙소"],
    "강릉·속초 오션뷰/자쿠지": ["강릉 오션뷰 펜션", "강릉 자쿠지 숙소", "속초 오션뷰 숙소", "속초 자쿠지 펜션", "강릉 풀빌라"],
    "경주·전주 한옥/독채": ["경주 한옥 스테이", "경주 독채 숙소", "전주 한옥 스테이", "전주 독채 한옥", "경주 풀빌라"],
    "서울 근교 글램핑/카라반": ["양평 글램핑", "가평 글램핑", "포천 카라반", "가평 카라반", "양평 독채 펜션"],
}
DEFAULT_PRESET = "전체(전국 핫플)"
MODES = {"season": "🔥 시즌 급상승 숙소", "steady": "👑 스테디 인기 숙소"}

# 블로그 요약문에서 핵심 매력으로 뽑는 시설 단어 (한 줄 요약은 이 단어가 실제로 나온 것만)
CHARM_WORDS = ("개별 온수풀", "인피니티풀", "온수풀", "노천 자쿠지", "자쿠지", "히노끼탕", "야외 스파", "통창", "오션뷰", "리버뷰", "마운틴뷰",
               "불멍", "바비큐", "조식", "독채", "수영장", "사우나", "테라스", "한옥", "글램핑", "카라반")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


def _hub_headers(cid: str, secret: str) -> Dict[str, str]:
    return {"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": secret}


# ---------------------------------------------------------------- ① 데이터랩 트렌드
def fetch_trends(keywords: List[str], cid: str, secret: str, today: Optional[dt.date] = None, timeout: int = 8) -> Dict[str, Any]:
    """
    키워드(최대 5개)의 최근 30일 일별 비율 → {"ok", "error", "stats": {키워드: {"steady": 30일 평균, "avg7": 최근 7일 평균, "surge": 급상승 배수}}}
    surge = 최근 7일 평균 ÷ 앞선 23일 평균 (앞선 평균이 0이면 최근 평균 자체를 점수로). 비율은 같은 호출 안에서만 비교 가능 (최대값 = 100)
    """
    if not (cid and secret):
        return {"ok": False, "error": "네이버 검색 API 키가 없어요", "stats": {}}
    end = (today or dt.date.today()) - dt.timedelta(days=1)
    start = end - dt.timedelta(days=29)
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": "date",
            "keywordGroups": [{"groupName": k, "keywords": [k]} for k in keywords[:5]]}
    attempts = [(TREND_URL, _hub_headers(cid, secret)),
                (LEGACY_TREND_URL, {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret})]
    err = ""
    for url, headers in attempts:
        try:
            r = requests.post(url, json=body, headers={**headers, "Content-Type": "application/json"}, timeout=timeout)
        except Exception as e:
            err = f"데이터랩에 연결하지 못했어요 ({type(e).__name__})"
            continue
        if r.status_code == 200:
            try:
                results = r.json().get("results") or []
            except ValueError:
                err = "데이터랩 응답을 읽지 못했어요"
                continue
            stats: Dict[str, Dict[str, float]] = {}
            for res in results:
                vals = [float(d.get("ratio") or 0) for d in (res.get("data") or [])]
                if not vals:
                    continue
                recent, prior = vals[-7:], vals[:-7]
                avg7 = sum(recent) / len(recent)
                avg_prior = (sum(prior) / len(prior)) if prior else 0.0
                stats[str(res.get("title") or "")] = {"steady": round(sum(vals) / len(vals), 2), "avg7": round(avg7, 2),
                                                      "surge": round(avg7 / avg_prior, 2) if avg_prior > 0 else round(avg7, 2)}
            return {"ok": bool(stats), "error": "" if stats else "데이터랩에 해당 키워드 검색량이 없어요", "stats": stats}
        if r.status_code in (401, 403) and url == TREND_URL:
            err = TREND_NOT_ENABLED
        elif r.status_code == 429:
            err = "데이터랩 호출 한도를 넘었어요. 잠시 뒤 다시 시도해 주세요."
        elif not err:
            err = f"데이터랩 오류 (HTTP {r.status_code})"
    return {"ok": False, "error": err, "stats": {}}


def rank_keywords(keywords: List[str], stats: Dict[str, Dict[str, float]], mode: str = "season") -> List[str]:
    """시즌 급상승(최근 7일 ÷ 앞선 23일) 또는 스테디(30일 평균) 순으로 키워드 정렬. 통계가 없는 키워드는 뒤에 (원래 순서)"""
    key = "surge" if mode == "season" else "steady"
    have = [k for k in keywords if k in stats]
    have.sort(key=lambda k: (-(stats[k].get(key) or 0), -(stats[k].get("steady") or 0)))
    return have + [k for k in keywords if k not in stats]


# ---------------------------------------------------------------- ② 지역·블로그 검색으로 실제 숙소 TOP 5
def _get(kind: str, query: str, cid: str, secret: str, display: int, timeout: int, sort: str = "sim") -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(f"{HUB}/search/v1/{kind}", params={"query": query, "display": display, "sort": sort},
                         headers=_hub_headers(cid, secret), timeout=timeout)
    except Exception as e:
        logger.info(f"네이버 {kind} 검색 실패: {type(e).__name__}")
        return None
    if r.status_code != 200:
        logger.info(f"네이버 {kind} 검색 HTTP {r.status_code}")
        return None
    try:
        return r.json()
    except ValueError:
        return None


def is_stay(category: str, name: str = "") -> bool:
    text = f"{category} {name}"
    return any(w in text for w in STAY_WORDS) and not any(w in category for w in NOT_STAY_WORDS)


def charm_words(text: str) -> List[str]:
    """요약문에 실제로 나온 시설 단어 (겹치는 단어는 긴 것 우선: '개별 온수풀'이 있으면 '온수풀' 제외)"""
    found = [w for w in CHARM_WORDS if w in (text or "")]
    return [w for w in found if not any(w != o and w in o for o in found)]


def _short_region(address: str) -> str:
    parts = (address or "").split()
    return " ".join(parts[:2]) if len(parts) >= 2 else (address or "")


def curate_top5(preset: str = DEFAULT_PRESET, mode: str = "season", cid: str = "", secret: str = "",
                today: Optional[dt.date] = None, timeout: int = 6) -> Dict[str, Any]:
    """
    → {"items": [{name, address, region, category, link, blog_total, recent, charms, highlight, trend_keyword}] (최대 5),
       "trend": {키워드: 통계}, "picked": [고른 키워드], "notes": [안내문], "error": 실패 이유}
    """
    keywords = PRESETS.get(preset) or PRESETS[DEFAULT_PRESET]
    out: Dict[str, Any] = {"items": [], "trend": {}, "picked": [], "notes": [], "error": ""}
    if not (cid and secret):
        out["error"] = "네이버 검색 API 키(NAVER_CLIENT_ID / NAVER_CLIENT_SECRET)가 없어 인기 숙소를 찾을 수 없어요. 아래에 숙소 이름이나 링크를 직접 입력해 주세요."
        return out
    tr = fetch_trends(keywords, cid, secret, today=today)
    if tr["ok"]:
        out["trend"] = tr["stats"]
    else:
        out["notes"].append(tr["error"])
    ranked = rank_keywords(keywords, tr["stats"] if tr["ok"] else {}, mode)
    out["picked"] = ranked[:2]

    seen: Dict[str, Dict[str, Any]] = {}
    hub_error = ""
    for kw in out["picked"]:
        for q in (kw, f"{kw} 추천", f"{kw} 후기"):
            data = _get("local", q, cid, secret, 5, timeout)
            if data is None:
                hub_error = "네이버 지역 검색을 호출하지 못했어요 (키 또는 API 설정을 확인해 주세요)"
                continue
            for it in data.get("items") or []:
                name = _clean(it.get("title", ""))
                cat = _clean(it.get("category", ""))
                if not name or name in seen or not is_stay(cat, name):
                    continue
                addr = _clean(it.get("roadAddress", "")) or _clean(it.get("address", ""))
                seen[name] = {"name": name, "address": addr, "region": _short_region(addr), "category": cat,
                              "link": it.get("link", ""), "trend_keyword": kw}
    if not seen:
        out["error"] = hub_error or "이 조건에서 숙박업소를 찾지 못했어요. 다른 지역·테마를 고르거나 직접 입력해 주세요."
        return out

    today_d = today or dt.date.today()
    cutoff = (today_d - dt.timedelta(days=30)).strftime("%Y%m%d")
    for st in seen.values():
        data = _get("blog", st["name"], cid, secret, 30, timeout, sort="date") or {}
        items = data.get("items") or []
        mine = [i for i in items if st["name"].replace(" ", "") in (_clean(i.get("title", "")) + _clean(i.get("description", ""))).replace(" ", "")]
        st["blog_total"] = int(data.get("total") or 0)
        st["recent"] = sum(1 for i in mine if str(i.get("postdate") or "0") >= cutoff)
        text = " ".join(_clean(i.get("description", "")) for i in mine)
        st["charms"] = charm_words(text)[:3]
        loc = st["region"] or "국내"
        st["highlight"] = (" · ".join(st["charms"]) + " 후기가 많은 " + st["category"].split(">")[-1].strip()) if st["charms"] \
            else f"{loc} {st['category'].split('>')[-1].strip() or '숙소'} · 블로그 후기 {st['blog_total']:,}건"
    key = (lambda s: (s["recent"], s["blog_total"])) if mode == "season" else (lambda s: (s["blog_total"], s["recent"]))
    out["items"] = sorted(seen.values(), key=key, reverse=True)[:5]
    return out
