"""
core/affiliate_ota.py - 아고다(Agoda)·트립닷컴(Trip.com) 숙소 링크 → 숙소 정보 + 제휴(어필리에이트) 수익 링크

원칙 (쿠팡·네이버와 같음):
- 숙소 페이지는 열지 않음. 두 사이트 모두 이용 약관상 자동 수집을 금지하고, 트립닷컴 robots.txt는 숙소 상세
  (/hotels/detail/?hotelId=)와 사진·정보를 주는 내부 API(/restapi/)를 막아 둠. 숙소 사진은 호텔·OTA 저작물
  → 링크 주소 자체에서 알 수 있는 것(숙소 ID, 영문 숙소명)만 쓰고, 사진은 직접 올린 것만 사용
- 단축·공유 링크는 리다이렉트 주소(Location 헤더)만 따라감 (본문은 받지 않음)
- 제휴 링크: .env의 AGODA_CID / TRIP_AID / TRIP_SID를 붙임. 날짜·인원·세션 파라미터는 지워 링크가 오래 유효하게
"""
import os
import re
import urllib.parse
from typing import Any, Dict, Optional

AGODA_HOSTS = ("agoda.com",)
TRIP_HOSTS = ("trip.com",)
# 공유용 단축 도메인 (리다이렉트만 따라가 실제 숙소 주소를 얻음)
SHORT_HOSTS = ("agoda.tw", "agoda.to", "trip.tp.st", "tp.st")

OTA_LABEL = {"agoda": "아고다", "trip": "트립닷컴"}
# 영상 화면 고지 (짧게) / 설명·고정 댓글 고지 (공정위 추천·보증 심사지침: 경제적 이해관계 명시)
OTA_DISCLOSURE = "제휴 링크 포함 · 예약 시 수수료를 받을 수 있어요"


def ota_disclosure_full(ota: str) -> str:
    name = OTA_LABEL.get(ota, "숙소 예약")
    return f"이 영상은 {name} 제휴(어필리에이트) 활동의 일환으로, 아래 링크로 예약하시면 일정액의 수수료를 받을 수 있습니다."


def _host(url: str) -> str:
    return urllib.parse.urlparse((url or "").strip()).netloc.lower().split(":")[0]


def _is_host(host: str, roots) -> bool:
    return any(host == r or host.endswith("." + r) for r in roots)


def detect_ota(url: str) -> Optional[str]:
    """'agoda' / 'trip' / None (PC·모바일·국가별 하위 도메인 모두: www.agoda.com, m.agoda.com, kr.trip.com, m.trip.com …)"""
    host = _host(url)
    if _is_host(host, AGODA_HOSTS):
        return "agoda"
    if _is_host(host, TRIP_HOSTS):
        return "trip"
    return None


def is_short_ota_link(url: str) -> bool:
    host = _host(url)
    path = urllib.parse.urlparse(url).path
    # trip.com/w/xxxx, agoda.com/sp/xxxx 같은 공유 링크도 실제 숙소 주소로 풀어야 숙소 ID가 나옴
    return _is_host(host, SHORT_HOSTS) or (detect_ota(url) == "trip" and path.startswith("/w/")) \
        or (detect_ota(url) == "agoda" and path.startswith("/sp/"))


def _slug_to_name(slug: str) -> str:
    """URL 슬러그 → 영문 숙소명 ('st-john-s-hotel' → "St John's Hotel"). 한글 이름은 링크에 없으므로 화면에서 고쳐 쓰게 함"""
    s = urllib.parse.unquote(slug or "").strip("-_/ ")
    if not s or s.isdigit():
        return ""
    words = [w for w in re.split(r"[-_+]+", s) if w]
    out = []
    for w in words:
        if w.lower() == "s" and out:
            out[-1] += "'s"
        else:
            out.append(w.capitalize() if w.isascii() else w)
    return " ".join(out)


def _q(url: str) -> Dict[str, str]:
    return {k.lower(): v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items() if v}


def parse_agoda(url: str) -> Dict[str, str]:
    """
    https://www.agoda.com/ko-kr/st-john-s-hotel/hotel/gangneung-si-kr.html?cid=…&checkIn=… → slug·도시
    https://www.agoda.com/partners/partnersearch.aspx?hid=123 / ?selectedproperty=123 → 숙소 ID
    """
    p = urllib.parse.urlparse(url)
    q = _q(url)
    out = {"hotel_id": q.get("hid") or q.get("hotel_id") or q.get("selectedproperty") or q.get("hotelid") or "",
           "slug": "", "city": "", "name": ""}
    m = re.search(r"/(?:[a-z]{2}-[a-z]{2}/)?([^/]+)/hotel/(?:all/)?([^/]+?)(?:\.html)?/?$", p.path, re.I)
    if m:
        out["slug"], out["city"] = m.group(1), m.group(2)
        out["name"] = _slug_to_name(m.group(1))
    return out


def parse_trip(url: str) -> Dict[str, str]:
    """
    https://www.trip.com/hotels/gangneung-hotel-detail-1234567/st-johns-hotel/ → ID·slug
    https://kr.trip.com/hotels/detail/?hotelId=1234567 , https://m.trip.com/html5/hotel/HotelDetail/1234567.html → ID
    """
    p = urllib.parse.urlparse(url)
    q = _q(url)
    out = {"hotel_id": q.get("hotelid") or q.get("hotel") or "", "slug": "", "city": "", "name": ""}
    m = re.search(r"/hotels/([a-z0-9-]+?)-hotel-detail-(\d+)(?:/([^/?#]+))?", p.path, re.I)
    if m:
        out["city"], out["hotel_id"] = m.group(1), out["hotel_id"] or m.group(2)
        if m.group(3):
            out["slug"] = m.group(3)
            out["name"] = _slug_to_name(m.group(3))
    m = re.search(r"/HotelDetail/(\d+)", p.path, re.I)
    if m and not out["hotel_id"]:
        out["hotel_id"] = m.group(1)
    return out


# 제휴 링크에서 지울 파라미터: 기존 제휴 ID(남의 ID 포함)·날짜·인원·세션·추적값
_AGODA_KEEP = {"hid", "hotel_id", "selectedproperty", "hotelid"}
_TRIP_KEEP = {"hotelid", "hotel", "locale", "curr"}


def affiliate_link(url: str, ota: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    숙소 URL → 제휴 링크. 반환 {link, ota, missing_keys}
    - 아고다: ?cid=AGODA_CID
    - 트립닷컴: ?Allianceid=TRIP_AID&SID=TRIP_SID
    키가 없으면 link=""이고 missing_keys에 빠진 이름 (지어낸 ID를 넣지 않음)
    """
    env = env if env is not None else os.environ
    ota = ota or detect_ota(url)
    if ota not in ("agoda", "trip"):
        return {"link": "", "ota": ota, "missing_keys": []}
    p = urllib.parse.urlparse(url.strip())
    keep = _AGODA_KEEP if ota == "agoda" else _TRIP_KEEP
    params = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=False) if k.lower() in keep]
    if ota == "agoda":
        need = {"AGODA_CID": (env.get("AGODA_CID") or "").strip()}
        params.append(("cid", need["AGODA_CID"]))
    else:
        need = {"TRIP_AID": (env.get("TRIP_AID") or "").strip(), "TRIP_SID": (env.get("TRIP_SID") or "").strip()}
        params += [("Allianceid", need["TRIP_AID"]), ("SID", need["TRIP_SID"]), ("trip_sub1", "shorts")]
    missing = [k for k, v in need.items() if not v]
    if missing:
        return {"link": "", "ota": ota, "missing_keys": missing}
    host = p.netloc.lower()
    if ota == "agoda" and host.startswith("m."):
        host = "www." + host[2:]          # 모바일 주소도 PC 주소로 통일 (아고다가 기기에 맞게 다시 보냄)
    link = urllib.parse.urlunparse(("https", host, p.path, "", urllib.parse.urlencode(params), ""))
    return {"link": link, "ota": ota, "missing_keys": []}


def ota_info(url: str, resolve=None) -> Dict[str, Any]:
    """
    링크 → {ota, url(최종 주소), hotel_id, name(영문, 없으면 ''), city, affiliate_link, missing_keys}
    resolve: 단축 링크 → 최종 주소 함수 (기본 core.scraper.resolve_link, Location 헤더만 따라감)
    """
    u = (url or "").strip()
    if is_short_ota_link(u):
        if resolve is None:
            from core.scraper import resolve_link as resolve
        u = resolve(u).get("url") or u
    ota = detect_ota(u)
    if not ota:
        return {"ota": None, "url": u}
    info = parse_agoda(u) if ota == "agoda" else parse_trip(u)
    aff = affiliate_link(u, ota)
    return {"ota": ota, "url": u, **info, "affiliate_link": aff["link"], "missing_keys": aff["missing_keys"]}
