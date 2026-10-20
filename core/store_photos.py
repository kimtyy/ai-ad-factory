"""
core/store_photos.py - 상호명으로 매장 사진 후보 자동 수집 (네이버 공식 이미지 검색 API, 크롤링 없음)

점주에게 사진을 못 받은 상태에서도 샘플 쇼츠를 만들 수 있게 후보 사진을 모아 보여 준다. 사용자가 눈으로 골라(체크) 영상 소스로 넣는다.
- 검색: "{지역} {상호}" (부족하면 "{상호} 대표메뉴"), display=20, sort=sim, filter=large (8장 미만이면 filter=all로 다시)
- 정확도: 제목에 상호 핵심 어절이 있고 가로·세로가 모두 400px 이상인 것만, 최대 12장
- 다운로드: User-Agent·Referer 헤더, 타임아웃 3초, 원본이 안 되면 썸네일로 대체, 받은 뒤 긴 변 2560px 이하로 줄여 JPEG 저장
주의: 검색에 나오는 사진은 블로거·방문자 사진일 수 있어 저작권·초상권 확인이 필요하다 (화면에서 경고하고, 사용한 영상은 업로드 패널에서 다시 알림).
"""
import hashlib
import html
import io
import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("ad_factory.store_photos")

HUB_IMAGE_URL = "https://naverapihub.apigw.ntruss.com/search/v1/image"
LEGACY_IMAGE_URL = "https://openapi.naver.com/v1/search/image"
MIN_SIDE = 400
MAX_CANDIDATES = 12
DEFAULT_CHECKED = 8
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
DL_HEADERS = {"User-Agent": UA, "Referer": "https://search.naver.com"}
NOT_ENABLED_HINT = ("이 NAVER API HUB 앱에 '이미지 검색'이 추가되어 있지 않아요. NCP 콘솔 → NAVER API HUB → 앱 설정에서 이미지 검색을 추가해 주세요 "
                    "(키는 그대로 쓰면 돼요).")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


def _norm(s: str) -> str:
    return re.sub(r"[\s·,.\-_()\[\]]", "", s or "").lower()


def name_keys(name: str) -> List[str]:
    """제목에 있어야 하는 상호 핵심 어절: 상호 전체, 업종어(갈비·식당 등)를 뺀 고유 부분, 공백으로 나뉜 긴 어절"""
    n = _norm(name)
    keys: List[str] = []
    if len(n) >= 2:
        keys.append(n)
    core = re.sub(r"(갈비|식당|맛집|횟집|한식|고기집|카페|본점|[0-9]+호점)", "", n)
    if len(core) >= 3 and core not in keys:
        keys.append(core)
    for tok in re.findall(r"[가-힣A-Za-z0-9]{3,}", name or ""):
        k = _norm(tok)
        if k not in keys:
            keys.append(k)
    return keys


def _fetch(query: str, cid: str, secret: str, filter_: str, timeout: int) -> Dict[str, Any]:
    """네이버 이미지 검색 1회 → {"items": [...], "error": 안내문}. API HUB(키 공용) 우선, 예전 developers 키는 대체"""
    attempts = [(HUB_IMAGE_URL, {"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": secret}),
                (LEGACY_IMAGE_URL, {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret})]
    err = ""
    for url, headers in attempts:
        try:
            r = requests.get(url, params={"query": query, "display": 20, "sort": "sim", "filter": filter_}, headers=headers, timeout=timeout)
        except Exception as e:
            err = f"네이버 이미지 검색 서버에 연결하지 못했어요 ({type(e).__name__})"
            continue
        if r.status_code == 200:
            try:
                return {"items": r.json().get("items") or [], "error": ""}
            except ValueError:
                err = "이미지 검색 응답을 읽지 못했어요"
                continue
        if r.status_code == 401 and url == HUB_IMAGE_URL:
            err = NOT_ENABLED_HINT
        elif r.status_code in (403, 429):
            err = "네이버 이미지 검색 호출 한도를 넘었거나 권한이 없어요. 잠시 뒤 다시 시도해 주세요."
        elif not err:
            err = f"네이버 이미지 검색 오류 (HTTP {r.status_code})"
    return {"items": [], "error": err}


def search_store_images(name: str, region: str = "", client_id: str = "", client_secret: str = "", timeout: int = 6) -> Dict[str, Any]:
    """
    상호명으로 사진 후보 → {"items": [{title, link, thumbnail, width, height}] (최대 12), "error": 안내문}.
    제목에 상호 핵심 어절이 있고 가로·세로 400px 이상인 것만. 결과가 8장 미만이면 검색어·필터를 바꿔 더 찾는다
    """
    name = (name or "").strip()
    if not (name and client_id and client_secret):
        return {"items": [], "error": "" if name else "상호명을 먼저 입력해 주세요."}
    keys = name_keys(name)
    seen: set = set()
    out: List[Dict[str, Any]] = []
    error = ""

    def collect(query: str, filter_: str) -> None:
        nonlocal error
        res = _fetch(query, client_id, client_secret, filter_, timeout)
        error = res["error"] or error
        for it in res["items"]:
            title = _clean(it.get("title", ""))
            try:
                w, h = int(it.get("sizewidth") or 0), int(it.get("sizeheight") or 0)
            except (TypeError, ValueError):
                continue
            link = it.get("link") or ""
            if not link or link in seen or min(w, h) < MIN_SIDE:
                continue
            if not any(k and k in _norm(title) for k in keys):
                continue
            seen.add(link)
            out.append({"title": title, "link": link, "thumbnail": it.get("thumbnail") or "", "width": w, "height": h})

    loc = f"{region} {name}".strip() if region and region not in name else name
    for query, filter_ in ((loc, "large"), (f"{name} 대표메뉴", "large"), (loc, "all"), (f"{name} 대표메뉴", "all")):
        if len(out) >= DEFAULT_CHECKED:
            break
        collect(query, filter_)
        if error == NOT_ENABLED_HINT:
            break
    return {"items": out[:MAX_CANDIDATES], "error": error if not out else ""}


def _get_image_bytes(url: str, timeout: int) -> Optional[bytes]:
    if not url:
        return None
    try:
        r = requests.get(url, headers=DL_HEADERS, timeout=timeout)
        if r.status_code == 200 and r.content and len(r.content) <= 20 * 1024 * 1024 and "image" in (r.headers.get("Content-Type", "image") or "image"):
            return r.content
    except Exception as e:
        logger.info(f"사진 다운로드 실패({type(e).__name__}): {url[:60]}")
    return None


def download_store_image(item: Dict[str, Any], dest_dir: str, timeout: int = 3) -> Optional[str]:
    """
    후보 사진 1장 저장 → 경로(JPEG, 긴 변 2560px 이하) 또는 None. 원본이 403·시간 초과면 썸네일로 대체.
    사진이 아니거나(HTML 등) 열 수 없으면 None
    """
    from core.media.imgutil import open_rgb_downscaled
    os.makedirs(dest_dir, exist_ok=True)
    for url in (item.get("link"), item.get("thumbnail")):
        data = _get_image_bytes(url or "", timeout)
        if not data:
            continue
        try:
            img = open_rgb_downscaled(io.BytesIO(data))
            if min(img.size) < 200:
                continue
        except Exception:
            continue
        path = os.path.join(dest_dir, f"web_{hashlib.md5(data).hexdigest()[:12]}.jpg")
        img.save(path, quality=92)
        return path
    return None
