"""
core/scraper.py - 쇼핑몰(스마트스토어, 쿠팡, 올리브영, 일반 커머스) URL 크롤러
제품명(Title), 핵심 소구점(Description/USP), 대표 상품 이미지(OG Image/Main Image) 자동 추출
"""
import os
import re
import logging
import urllib.parse
from typing import Dict, Any, Optional, List
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("ad_factory.scraper")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}


def clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    # 특수 노이즈 제거
    text = text.replace("\r", "").replace("\n", "").replace("\t", "").strip()
    return text


from PIL import Image, ImageDraw
import numpy as np



import json
import io
import hashlib

from core.guardrail import validate_model_call
from core.coupang import CoupangPartners, is_coupang_url

# -------------------------------------------------------------
# [숙소 갤러리 이미지 카테고리 정의]
# 특정 숙소 고유명사(시설 브랜드명, 지명 등)는 넣지 않습니다. 범용 키워드만 유지.
# 한글 키워드는 부분 문자열, 영문 키워드는 토큰(단어) 단위로 매칭합니다.
# -------------------------------------------------------------
PHOTO_CATEGORY_KEYWORDS = {
    "pool": [
        "수영장", "야외 수영장", "야외수영장", "실내 수영장", "실내수영장", "인피니티", "인피니티풀",
        "풀장", "온수풀", "개별풀", "프라이빗풀", "자쿠지", "노천탕", "스파", "물놀이",
        "pool", "spa", "jacuzzi"
    ],
    "view": [
        "대표 사진", "대표사진", "외관", "전경", "야경", "호수", "오션뷰", "마운틴뷰",
        "테라스", "루프탑", "조망", "풍경", "exterior", "view", "lake",
        "ocean", "mountain", "night", "terrace", "rooftop", "landscape", "scenery"
    ],
    "facility": [
        "라운지", "로비", "레스토랑", "부대시설", "바베큐", "바비큐", "카페", "피트니스",
        "헬스장", "산책로", "정원", "가든", "사우나", "키즈", "조식", "뷔페", "식음",
        "lounge", "lobby", "facility", "restaurant", "cafe", "fitness", "garden", "amenity"
    ],
    "bedroom": [
        "객실", "침실", "거실", "스위트", "디럭스", "온돌", "베드", "룸", "room", "bed", "suite", "bedroom", "living"
    ]
}

# 분위기를 깨는 네거티브 필터 (자동 탈락 대상)
# - 한글: 부분 문자열 매칭 ('벽난로' 같은 장점 시설이 걸리지 않도록 '벽' 단독은 제외)
# - 영문: 토큰 단위 매칭 ('jacuzzi'의 'cu', 'secure'의 'cu', '2024'의 '24' 오탐 방지)
NEGATIVE_KEYWORDS_KO = [
    "자판기", "무인", "키오스크", "이마트", "편의점", "세븐일레븐", "상가",
    "안마당", "중정", "외벽", "콘크리트", "복도", "통로", "주차장",
    "화장실", "세면대", "변기", "엘리베이터", "승강기", "도면", "약도", "지도",
    "비상구", "계단", "기계실", "간판", "출입구", "입구", "표지판", "안내판", "안내도",
    "비품", "소화기"
]
NEGATIVE_TOKENS_EN = {
    "kiosk", "vending", "emart", "emart24", "corridor", "hallway", "parking",
    "toilet", "restroom", "elevator", "lift", "stair", "stairs", "staircase",
    "floorplan", "map", "convenience", "cu", "gs25", "seveneleven"
}


def _tokenize(text: str) -> List[str]:
    return [t for t in re.split(r"[^0-9a-z가-힣]+", (text or "").lower()) if t]


def _keyword_hit(kw: str, text: str, tokens: set) -> bool:
    """한글 키워드는 부분 문자열, 영문 키워드는 토큰 일치로 판정"""
    if re.search(r"[가-힣]", kw):
        return kw in text
    return kw.lower() in tokens


def is_negative_image(text: str = "", url: str = "") -> bool:
    """제목/alt 텍스트(text)와 이미지 URL(url)에서 분위기를 깨는 네거티브 키워드 포함 여부 판정"""
    t = (text or "").lower()
    u = urllib.parse.unquote(url or "").lower()
    for kw in NEGATIVE_KEYWORDS_KO:
        if kw in t or kw in u:
            return True
    tokens = set(_tokenize(t)) | set(_tokenize(u))
    return bool(tokens & NEGATIVE_TOKENS_EN)


def classify_photo_metadata(title: str = "", alt: str = "", url_str: str = "") -> str:
    """텍스트 메타데이터(타이틀, alt 태그, URL 키워드)를 기반으로 1차 카테고리 판별 (네거티브 필터 탑재)"""
    text = f"{title} {alt}".lower()
    if is_negative_image(text, url_str):
        return "negative"
    combined = f"{text} {urllib.parse.unquote(url_str or '').lower()}"
    tokens = set(_tokenize(combined))
    for cat in ["pool", "view", "facility", "bedroom"]:
        for kw in PHOTO_CATEGORY_KEYWORDS[cat]:
            if _keyword_hit(kw, combined, tokens):
                return cat
    return "unknown"


def page_origin(url: str) -> str:
    p = urllib.parse.urlparse(url or "")
    return f"{p.scheme}://{p.netloc}/" if p.scheme and p.netloc else ""


def cdn_headers_for(image_url: str, referer: Optional[str] = None) -> Dict[str, str]:
    """이미지 CDN 다운로드 헤더. Referer는 원본 상세페이지(없으면 이미지 호스트) 기준으로 설정"""
    headers = dict(CDN_DOWNLOAD_HEADERS)
    ref = referer or page_origin(image_url)
    if ref:
        headers["Referer"] = ref
    return headers


# -------------------------------------------------------------
# [비전 게이트: 사진 풀 분류]
# 텍스트 메타데이터만으로는 '로비'라는 제목의 자판기 사진, '외관'이라는 제목의 아파트형 외벽 사진을
# 걸러낼 수 없으므로, 사진 풀 후보를 실제 픽셀 기준으로 1회 분류(카테고리·광고 적합도·태그)하고
# 그 결과로 (1) 대본 기획용 사진 인벤토리를 만들고 (2) 씬 슬롯 비율(예: 수영장3/객실2/부대시설2)에 맞춰 배정합니다.
# 분류는 이미지 해시 기준으로 캐시되어 재선별·재기획 시 다시 호출하지 않습니다.
# -------------------------------------------------------------
VISION_MIN_FIT = 6            # 이 적합도 미만은 배정 대상에서 제외
VISION_POOL_BUDGET = 24       # 최초 기획 시 사진 풀에서 비전 분류할 최대 장수
VISION_REMATCH_BUDGET = 8     # 재선별 1회당 추가로 분류할 최대 장수

VISION_TAGS = (
    "outdoor_pool", "infinity_pool", "indoor_pool", "jacuzzi", "spa", "water_view", "mountain_view",
    "ocean_view", "night", "sunset", "bed", "room_interior", "living_room", "bbq", "cafe", "firepit", "lounge",
    "restaurant", "garden", "terrace", "exterior",
)

VISION_TAG_KO = {
    "outdoor_pool": "야외 수영장", "infinity_pool": "인피니티 풀", "indoor_pool": "실내 수영장", "jacuzzi": "자쿠지",
    "spa": "스파", "water_view": "물가 뷰", "mountain_view": "마운틴 뷰", "ocean_view": "오션 뷰", "night": "야경", "sunset": "노을",
    "bed": "침대", "room_interior": "객실 내부", "living_room": "거실", "bbq": "바베큐", "cafe": "카페",
    "firepit": "불멍", "lounge": "라운지", "restaurant": "레스토랑", "garden": "정원", "terrace": "테라스", "exterior": "외관",
}
CATEGORY_KO = {"pool": "수영장", "view": "전경·뷰", "facility": "부대시설", "bedroom": "객실"}


def describe_photo_for_script(verdict: Optional[Dict[str, Any]]) -> Optional[str]:
    """대본 작성용 사진 설명 (예: '수영장 사진: 실내 수영장 | Spacious indoor pool with glass atrium')"""
    if not verdict or verdict.get("category") not in CATEGORY_KO:
        return None
    tags = ", ".join(VISION_TAG_KO.get(t, t) for t in verdict.get("tags", []))
    return f"{CATEGORY_KO[verdict['category']]} 사진: {tags or '특징 태그 없음'} | {verdict.get('reason', '')}".strip(" |")


# 슬롯 역할 → 허용 카테고리
ROLE_ACCEPTED_CATEGORIES = {
    "view": {"view"},
    "pool": {"pool"},
    "facility": {"facility"},
    "bedroom": {"bedroom"},
    "bedroom_or_view": {"bedroom", "view"},
    "facility_or_night_view": {"facility", "view"},
    "view_or_night": {"view", "facility"},
}
# 카테고리 목표 수를 계산할 때 슬롯 역할이 소비하는 대표 카테고리
ROLE_PRIMARY_CATEGORY = {"bedroom_or_view": "bedroom", "view_or_night": "view", "facility_or_night_view": "facility"}
# 역할별로 특정 카테고리를 '조건부'로만 허용: 엔딩 슬롯의 뷰 사진은 노을/야경/테라스일 때만 (평범한 낮 풍경 제외)
ROLE_CATEGORY_REQUIRED_TAGS = {"facility_or_night_view": {"view": {"night", "sunset", "terrace"}}}
ROLE_CATEGORY_REQUIRED_TEXT = {"facility_or_night_view": {"view": ("야경", "노을", "석양", "테라스", "루프탑", "night", "sunset")}}

VISION_CLASSIFY_PROMPT = (
    "You are curating real photos for a short-form (Reels/Shorts) accommodation ad.\n"
    "1) category - exactly one of:\n"
    "   - 'pool': swimming pool (outdoor/indoor/infinity), jacuzzi, spa, open-air bath, water play area\n"
    "   - 'view': attractive building exterior, panoramic landscape, lake/ocean/mountain view, terrace, night view\n"
    "   - 'facility': lounge, lobby, cafe, restaurant, BBQ area, fire pit, garden, fitness, kids zone\n"
    "   - 'bedroom': guest room interior, bed, living room\n"
    "   - 'reject': anything that ruins the mood. ALWAYS reject: vending machines, convenience store or kiosk corners, "
    "parking lots, corridors, stairs, elevators, toilets, plain concrete or apartment-like facades with no scenery, "
    "construction, maps, floor plans, signboards, text-heavy images, blurry or dark photos.\n"
    "2) fit - 0..10: how strong this photo is as an ad cut (wow factor, composition, sharpness, lighting).\n"
    f"3) tags - up to 4 that clearly apply, chosen ONLY from: {', '.join(VISION_TAGS)}.\n"
    'Respond with JSON only: {"category": "...", "fit": 0, "tags": ["..."], "reason": "short reason"}'
)


def classify_photo_with_vision(img_bytes: bytes, client: Any, cache: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Gemini Flash 비전으로 사진 1장을 분류합니다. 반환: {"category", "fit", "tags", "reason"} / 실패 시 None.
    결과는 이미지 해시 기준으로 cache에 저장됩니다 (역할과 무관하게 1회만 호출).
    """
    if client is None:
        return None
    cache_key = f"{hashlib.md5(img_bytes).hexdigest()}:classify"
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    try:
        from google.genai import types
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((512, 512))
        res = client.models.generate_content(
            model=validate_model_call("gemini-3.6-flash", category="llm"),
            contents=[img, VISION_CLASSIFY_PROMPT],
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
        )
        data = json.loads(res.text)
        if isinstance(data, list):
            data = data[0] if data else {}
        verdict = {
            "category": str(data.get("category", "reject")).lower().strip(),
            "fit": int(float(data.get("fit", 0))),
            "tags": [t for t in (data.get("tags") or []) if t in VISION_TAGS][:4],
            "reason": str(data.get("reason", ""))[:160],
        }
    except Exception as e:
        logger.warning(f"비전 분류 실패: {e}")
        return None
    if cache is not None:
        cache[cache_key] = verdict
    return verdict


def is_space_photo_url(url: str) -> bool:
    """숙소 공간(외관, 객실, 수영장, 뷰 등) 사진인지 필터링 (아이콘, 배너, 지도 등 제외)"""
    if not url:
        return False
    lower = url.lower()
    bad_tokens = [
        "icon", "logo", "badge", "banner", "emblem", "event", "marker",
        "btn", "blank", "pixel", "avatar", ".svg", ".gif", "static_maps",
        "map", "footer", "app_download", "categoryevent", "membership"
    ]
    if any(b in lower for b in bad_tokens):
        return False
    if lower.startswith("data:image"):
        return False
    return True


CDN_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}  # Referer는 cdn_headers_for()에서 원본 페이지 기준으로 설정


COMMERCE_MAX_IMAGES = 6
PRODUCT_IMAGE_MIN_BYTES = 15000
PRODUCT_IMAGE_MIN_SIDE = 400


def download_product_images(candidates: List[Dict[str, Any]], output_dir: str, referer: Optional[str],
                            limit: int = COMMERCE_MAX_IMAGES, prefix: str = "product") -> List[str]:
    """
    상품 사진 후보를 순서대로 받아 실물 사진만 저장 (작은 아이콘·가로 배너·중복 제외). 저장 경로 목록 반환
    prefix: 파일 이름 앞부분 (묶음 큐레이션은 상품마다 달리 줘야 같은 폴더에서 서로 덮어쓰지 않음)
    """
    os.makedirs(output_dir, exist_ok=True)
    saved, seen = [], set()
    for cand in candidates:
        if len(saved) >= limit:
            break
        u = cand.get("url", "")
        try:
            resp = requests.get(u, headers=cdn_headers_for(u, referer), timeout=10)
            data = resp.content or b""
            if resp.status_code != 200 or len(data) < PRODUCT_IMAGE_MIN_BYTES:
                continue
            digest = hashlib.md5(data).hexdigest()
            if digest in seen:
                continue
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
                fmt = (im.format or "JPEG").lower()
            if min(w, h) < PRODUCT_IMAGE_MIN_SIDE or max(w, h) / max(1, min(w, h)) > 2.5:
                continue
            seen.add(digest)
            ext = ".png" if fmt == "png" else (".webp" if fmt == "webp" else ".jpg")
            path = os.path.join(output_dir, f"{prefix}_{len(saved) + 1:02d}{ext}")
            with open(path, "wb") as f:
                f.write(data)
            saved.append(os.path.abspath(path))
        except Exception as e:
            logger.debug(f"상품 사진 다운로드 건너뜀 ({u}): {e}")
    return saved


# 공유 링크가 판매 페이지가 아닌 검색 결과로 이어지는 경우 (검색 페이지는 크롤링 금지 대상이라 열지 않음)
SEARCH_RESULT_HOSTS = ("google.", "search.naver.com", "search.daum.net", "bing.com")


def resolve_link(url: str, max_hops: int = 6, timeout: int = 8) -> Dict[str, Any]:
    """
    단축·공유 링크(share.google, naver.me, link.coupang.com 등)를 본문을 받지 않고 한 홉씩 따라가 최종 주소를 확인.
    반환: {"url": 최종 주소, "search_query": 검색 결과 페이지면 검색어(아니면 ""), "is_search": bool}
    """
    current = url
    for _ in range(max_hops):
        host = urllib.parse.urlparse(current).netloc.lower()
        path = urllib.parse.urlparse(current).path
        if any(h in host for h in SEARCH_RESULT_HOSTS) and (path.startswith("/search") or "search." in host):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(current).query).get("q") or \
                urllib.parse.parse_qs(urllib.parse.urlparse(current).query).get("query") or [""]
            return {"url": current, "search_query": clean_text(q[0]), "is_search": True}
        try:
            resp = requests.get(current, headers={"User-Agent": DEFAULT_HEADERS.get("User-Agent", "Mozilla/5.0")},
                                timeout=timeout, allow_redirects=False, stream=True)
            location = resp.headers.get("Location")
            resp.close()
        except Exception:
            break
        if resp.status_code in (301, 302, 303, 307, 308) and location:
            current = urllib.parse.urljoin(current, location)
            continue
        break
    return {"url": current, "search_query": "", "is_search": False}


def _scrape_coupang(url: str, output_dir: str, result: Dict[str, Any]) -> Dict[str, Any]:
    cp = CoupangPartners()
    if not cp.enabled:
        result["error"] = ("쿠팡은 자동 수집을 막고 있어 상품 정보를 가져올 수 없어요. 상품명·특장점을 직접 입력하고 상품 사진을 올려 주세요 "
                           "(쿠팡 파트너스 API 키를 .env에 넣으면 자동 조회됩니다)")
        return result
    found = cp.find_product(url)
    result["affiliate_link"] = found.get("partner_link") or ""
    product = found.get("product")
    if not product:
        result["error"] = ("쿠팡 상품 페이지는 직접 열 수 없어 공식 파트너스 API로 찾아봤는데, 이 링크의 상품을 찾지 못했어요. "
                           "아래 '쿠팡 상품 검색'에서 상품을 고르거나 상품명·사진을 직접 넣어 주세요")
        return result
    result["product_name"] = clean_text(product["name"])
    result["missing"].append("usp")            # 파트너스 API에는 상품 설명이 없음 → 특장점은 판매 페이지를 보고 직접 입력
    images = list(dict.fromkeys(found.get("images") or [product["image_url"]]))
    result["product_images"] = download_product_images([{"url": u} for u in images], output_dir, None)
    result["image_path"] = result["product_images"][0] if result["product_images"] else ""
    result["success"] = True
    return result


def _scrape_ota(url: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    아고다·트립닷컴: 숙소 페이지를 열지 않고 링크 주소에서 숙소 ID·영문 이름만 + 제휴 링크 생성 (core/affiliate_ota.py)
    사진은 수집하지 않음 (약관상 자동 수집 금지·호텔/OTA 저작물) → 화면에서 직접 올리도록 missing에 'photos'
    """
    from core.affiliate_ota import OTA_LABEL, ota_info
    info = ota_info(url)
    label = OTA_LABEL.get(info.get("ota"), "")
    result.update({
        "success": True, "url": info["url"], "ota": info["ota"], "affiliate_program": info["ota"],
        "product_name": info.get("name", ""), "hotel_id": info.get("hotel_id", ""),
        "affiliate_link": info.get("affiliate_link", ""), "affiliate_missing_keys": info.get("missing_keys", []),
        "image_pool": [], "category_counts": {},
    })
    result["missing"] = (["product_name"] if not result["product_name"] else []) + ["usp", "photos"]
    logger.info(f"{label} 링크: 페이지 미접속, 숙소 ID={result['hotel_id'] or '-'}, 이름='{result['product_name']}', "
                f"제휴 링크={'생성' if result['affiliate_link'] else '키 없음 ' + str(result['affiliate_missing_keys'])}")
    return result


def scrape_product_info(
    url: str,
    output_image_dir: Optional[str] = None,
    mode: str = "commerce",
    max_gallery_images: int = 7
) -> Dict[str, Any]:
    """
    쇼핑몰 또는 숙소/여행 상품 URL을 분석하여 제품명, USP(소구점 설명), 대표 이미지 URL 및
    여행 상품 모드용 다중 갤러리 컷(5~7장)을 추출하여 로컬 저장 경로를 반환합니다.
    """
    if not output_image_dir:
        raise ValueError("output_image_dir가 필요합니다. JobContext.assets_dir을 전달하세요.")
    os.makedirs(output_image_dir, exist_ok=True)
    logger.info(f"URL 크롤링 시작 (모드={mode}): {url}")
    print(f"\n[Scraper] URL 크롤링 시작: {url} (모드={mode})")

    result = {
        "success": False,
        "url": url,
        "mode": mode,
        "product_name": "",
        "usp": "",
        "image_url": "",
        "image_path": "",
        "gallery_images": [],
        "gallery_urls": [],
        "product_images": [],   # 쇼핑 모드: 다운로드한 실물 상품 사진 경로
        "affiliate_link": "",   # 쿠팡 파트너스 링크 (영상 설명·고정 댓글용)
        "missing": [],          # 페이지에서 못 찾은 항목 (화면에서 직접 입력 요청). 지어낸 기본값은 절대 넣지 않음
        "product_name_hint": "",
        "error": None
    }

    try:
        # 쿠팡은 서버 자동 접속을 403으로 막음(약관상 우회 금지) → 페이지를 열지 않고 공식 파트너스 API로만 조회
        if mode == "commerce" and is_coupang_url(url):
            return _scrape_coupang(url, output_image_dir, result)
        # 아고다·트립닷컴 (숙소 제휴): 페이지를 열지 않고 링크만 분석 + 제휴 수익 링크
        from core.affiliate_ota import detect_ota, is_short_ota_link
        if mode == "travel" and (detect_ota(url) or is_short_ota_link(url)):
            return _scrape_ota(url, result)
        link = resolve_link(url)
        if link["is_search"]:
            result["product_name_hint"] = link["search_query"]
            result["error"] = ("공유 링크가 상품 페이지가 아니라 검색 결과 페이지로 연결됩니다. "
                               "쿠팡 등 실제 판매 페이지 링크를 넣거나, 상품 사진을 직접 올려 주세요")
            logger.info(f"검색 결과 링크 감지 → 크롤링 중단 (검색어 '{link['search_query']}'): {link['url'][:120]}")
            return result
        if link["url"] != url:
            logger.info(f"공유 링크 최종 주소: {link['url'][:120]}")
            url = link["url"]
            result["url"] = url

        headers = dict(DEFAULT_HEADERS)
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        origin = page_origin(url)
        if origin:
            headers["Referer"] = origin

        response = requests.get(url, headers=headers, timeout=12)
        response.raise_for_status()

        # 인코딩 안전 디코딩 (utf-8 우선)
        html = response.content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")

        # Next.js __NEXT_DATA__ JSON 파싱 시도 (여기어때, 야놀자 등 현대 웹앱 완벽 대응)
        next_data = None
        next_script = soup.find("script", id="__NEXT_DATA__")
        if next_script and next_script.string:
            try:
                next_data = json.loads(next_script.string)
                print("[Scraper] Next.js __NEXT_DATA__ 파싱 성공")
            except Exception as jse:
                logger.debug(f"__NEXT_DATA__ 파싱 예외: {jse}")

        # 1. 제품명 / 여행지명 (Title) 추출
        title = None

        # 1-1. Next.js accommodationInfo 메타데이터 탐색
        if next_data:
            page_props = next_data.get("props", {}).get("pageProps", {})
            acc_info = page_props.get("accommodationInfo", {})
            if isinstance(acc_info, dict):
                meta = acc_info.get("meta", {})
                if meta.get("name"):
                    title = clean_text(meta["name"])
                elif meta.get("title"):
                    title = clean_text(meta["title"])

        # 1-2. 메타 태그 탐색
        if not title:
            og_title = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "og:title"})
            if og_title and og_title.get("content"):
                title = clean_text(og_title["content"])

        if not title:
            tw_title = soup.find("meta", attrs={"name": "twitter:title"})
            if tw_title and tw_title.get("content"):
                title = clean_text(tw_title["content"])

        if not title:
            h1 = soup.find("h1")
            if h1:
                title = clean_text(h1.get_text())

        if not title and soup.title:
            title = clean_text(soup.title.get_text())

        if title:
            for suffix in [
                " : 네이버 쇼핑", " : 네이버페이", " : 네이버 스마트스토어", " | 올리브영", " - 쿠팡", 
                " - 스마트스토어", " : 여기어때", " - 야놀자", " | 에어비앤비", " - 아고다", " | 마이리얼트립"
            ]:
                if suffix in title:
                    title = title.split(suffix)[0].strip()
            title = re.sub(r"\[.*?\]|\(.*?\)", "", title).strip() or title

        # 못 찾으면 비워 두고 사용자 입력을 요청 (예전처럼 화장품 이름 같은 가짜 기본값을 넣으면
        # 엉뚱한 상품 영상이 만들어짐: 전기히터 링크 → 수분 앰플 광고)
        if not title:
            result["missing"].append("product_name")
        result["product_name"] = title or ""
        print(f"[Scraper] 추출된 제품/숙소명: {title}")

        # 2. 핵심 소구점 / 상세 설명 (USP) 추출
        desc = None

        # 2-1. Next.js accommodationInfo 상세 시설/테마에서 특장점 추출
        if next_data and mode == "travel":
            page_props = next_data.get("props", {}).get("pageProps", {})
            acc_info = page_props.get("accommodationInfo", {})
            if isinstance(acc_info, dict):
                usp_candidates = []
                for d in acc_info.get("details", []):
                    if isinstance(d, dict) and d.get("contents"):
                        for c in d["contents"]:
                            c_str = clean_text(c)
                            if any(k in c_str for k in ["침대", "욕조", "스파", "풀", "오션뷰", "스타일러", "PC", "안마", "조식", "어메니티", "넷플릭스", "루프탑", "호수"]):
                                usp_candidates.append(c_str)
                            if len(usp_candidates) >= 3:
                                break
                if usp_candidates:
                    desc = ", ".join(usp_candidates[:3])

        # 2-2. 메타 태그 탐색
        if not desc:
            og_desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "og:description"})
            if og_desc and og_desc.get("content"):
                desc = clean_text(og_desc["content"])

        if not desc:
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc and meta_desc.get("content"):
                desc = clean_text(meta_desc["content"])

        # 특장점도 지어내지 않음: 가짜 USP는 대본의 '근거'로 쓰여 없는 시설·효능 주장을 통과시킴
        if not desc:
            result["missing"].append("usp")
        result["usp"] = desc or ""
        print(f"[Scraper] 추출된 USP: {desc}")

        # 3. 고화질 이미지 수집 및 메타데이터 태깅
        candidate_items = []

        # 3-1. Next.js accommodationInfo 내 신규 고해상도 이미지(newImages, images, rooms)
        if next_data:
            page_props = next_data.get("props", {}).get("pageProps", {})
            acc_info = page_props.get("accommodationInfo", {})
            if isinstance(acc_info, dict):
                meta = acc_info.get("meta", {})
                for item in meta.get("newImages", []):
                    if isinstance(item, dict) and item.get("image"):
                        t = item.get("title", "")
                        candidate_items.append({"url": item["image"], "title": t, "source": "newImages"})
                for item in meta.get("images", []):
                    if isinstance(item, dict) and item.get("image"):
                        t = item.get("title", "")
                        candidate_items.append({"url": item["image"], "title": t, "source": "images"})
                    elif isinstance(item, str):
                        candidate_items.append({"url": item, "title": "", "source": "images"})
                for room in acc_info.get("rooms", []):
                    if isinstance(room, dict):
                        r_name = room.get("name") or room.get("title") or "객실"
                        for r_img in room.get("images", []):
                            if isinstance(r_img, dict) and r_img.get("image"):
                                candidate_items.append({"url": r_img["image"], "title": r_name, "source": "rooms"})
                            elif isinstance(r_img, str):
                                candidate_items.append({"url": r_img, "title": r_name, "source": "rooms"})

        # 3-2. HTML 원문 정규식 매칭 (여기어때 withstatic CDN, 야놀자 yaimg CDN, cdn.yeogi 등 이중 매칭)
        yeogi_ws = re.findall(r"https?://image\.withstatic\.com/[^\s\"\'<>\\]+\.(?:jpg|jpeg|png|webp)", html)
        for m in yeogi_ws:
            candidate_items.append({"url": m.split("?")[0], "title": "", "source": "yeogi_ws"})

        yanolja_ws = re.findall(r"https?://yaimg\.yanolja\.com/[^\s\"\'<>\\]+\.(?:jpg|jpeg|png|webp)", html)
        for m in yanolja_ws:
            candidate_items.append({"url": m.split("?")[0], "title": "", "source": "yanolja_ws"})

        cdn_ws = re.findall(r"https?://cdn\.yeogi\.com/[^\s\"\'<>\\]+\.(?:jpg|jpeg|png|webp)", html)
        for m in cdn_ws:
            candidate_items.append({"url": m.split("?")[0], "title": "", "source": "cdn_ws"})

        # 일반 숙소/호텔 고화질 이미지 URL 정규식 패턴 추가 탐색
        general_photos = re.findall(r"https?://[^\s\"\'<>\\]+/(?:images?|photo|gallery|room|hotel|adimg)[^\s\"\'<>\\]+\.(?:jpg|jpeg|png|webp)", html)
        for gp in general_photos:
            candidate_items.append({"url": gp.split("?")[0], "title": "", "source": "general_photos"})

        # 3-3. 메타 태그 (og:image, twitter:image)
        og_image = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if og_image and og_image.get("content"):
            candidate_items.append({"url": og_image["content"].strip(), "title": "대표 사진", "source": "og:image"})

        tw_image = soup.find("meta", attrs={"name": "twitter:image"})
        if tw_image and tw_image.get("content"):
            candidate_items.append({"url": tw_image["content"].strip(), "title": "대표 사진", "source": "twitter:image"})

        # 3-4. 상세페이지 내 img 태그 (src, data-src, data-original, data-lazy-src)
        for img in soup.find_all("img"):
            for attr in ["src", "data-src", "data-original", "data-lazy-src"]:
                val = img.get(attr)
                if val:
                    val = val.strip()
                    if is_space_photo_url(val):
                        alt_text = img.get("alt") or img.get("title") or ""
                        candidate_items.append({"url": val, "title": alt_text, "source": "html_img"})
                        break

        # 중복 제거 및 URL 절대 경로화, 비공간(아이콘/배너) 엄격 필터링 및 1차 카테고리 태깅
        seen_urls = set()
        clean_candidates = []
        for c in candidate_items:
            u = c.get("url", "")
            full_u = urllib.parse.urljoin(url, u).split("?")[0]
            if full_u.startswith("http") and is_space_photo_url(full_u):
                if full_u not in seen_urls:
                    seen_urls.add(full_u)
                    c_obj = dict(c)
                    c_obj["url"] = full_u
                    c_obj["referer"] = page_origin(url)
                    cat = classify_photo_metadata(
                        title=c_obj.get("title", ""),
                        url_str=full_u
                    )
                    # 분위기를 깨는 네거티브 이미지는 풀링 단계에서 원천 차단
                    if cat == "negative":
                        continue
                    c_obj["category"] = cat
                    clean_candidates.append(c_obj)

        print(f"[Scraper] 발견된 고유 공간 사진 후보: {len(clean_candidates)}개")

        # [Step 1 원칙 준수: 실물 이미지 파일(.jpg)을 미리 다운로드하지 않음]
        # (네트워크 트래픽 및 디스크 낭비 방지 - 대본 기획 후 필요한 N장만 3단계에서 핀포인트 다운로드)
        result["image_pool"] = clean_candidates
        result["gallery_urls"] = [c["url"] for c in clean_candidates]
        result["gallery_images"] = []
        result["category_counts"] = {
            "pool": sum(1 for c in clean_candidates if c.get("category") == "pool"),
            "view": sum(1 for c in clean_candidates if c.get("category") == "view"),
            "facility": sum(1 for c in clean_candidates if c.get("category") == "facility"),
            "bedroom": sum(1 for c in clean_candidates if c.get("category") == "bedroom"),
            "unknown": sum(1 for c in clean_candidates if c.get("category") == "unknown"),
        }

        if mode == "commerce":
            # 쇼핑 제휴: 대표(og:image) 우선으로 실물 상품 사진을 최대 COMMERCE_MAX_IMAGES장 받아 씬 사진으로 사용
            # (AI 이미지 생성 없음). 아이콘·배너·작은 썸네일은 크기·비율로 거름
            ordered = sorted(clean_candidates, key=lambda c: 0 if c.get("source") in ("og:image", "twitter:image") else 1)
            result["product_images"] = download_product_images(ordered, output_image_dir, page_origin(url))
            if result["product_images"]:
                result["image_path"] = result["product_images"][0]
            print(f"[Scraper] 상품 사진 {len(result['product_images'])}장 다운로드")

        result["success"] = True
        logger.info(
            f"크롤링 성공 (모드={mode}): 제품/숙소명='{result['product_name']}', "
            f"메타데이터 풀={len(result['image_pool'])}개 후보 확보"
        )
        print(f"[Scraper] 메타데이터 풀링 완료: 총 {len(clean_candidates)}개 후보 (Pool: {result['category_counts']['pool']}, View: {result['category_counts']['view']}, Fac: {result['category_counts']['facility']}, Bed: {result['category_counts']['bedroom']})")
        return result

    except Exception as e:
        logger.error(f"URL 크롤링 에러 ({url}): {e}")
        print(f"[CRITICAL ERROR] URL 크롤링 중 예외 발생 ({url}): {e}")
        result["error"] = str(e)
        result["success"] = False
        return result


# =============================================================================
# [Step 3. 사진 풀 비전 분류 + 슬롯 비율 배정]
#   classify_image_pool      : 필요한 카테고리 위주로 후보를 골라 다운로드(100KB+) + 비전 분류 (해시 캐시)
#   vision_inventory          : 대본 기획용 카테고리별 '실제로 쓸 수 있는' 사진 수
#   download_targeted_scene_assets : 씬 슬롯(역할+선호 태그)에 사진을 1:1 배정 (희소한 슬롯부터, 중복 없음)
# =============================================================================
MIN_ASSET_BYTES = 100000
CATEGORIES = ("pool", "view", "facility", "bedroom")
DEFAULT_CATEGORY_QUOTA = {"pool": 3, "bedroom": 2, "facility": 2, "view": 1}   # 7씬 황금 비율 + 대체용 뷰 1


def _scene_number(scene: Dict[str, Any], default: int) -> int:
    return int(scene.get("scene_index") or scene.get("scene_number") or default)


def _scene_keywords(scene: Dict[str, Any]) -> List[str]:
    raw_keywords = scene.get("visual_keywords") or []
    if isinstance(raw_keywords, str):
        return [k.strip().lower() for k in re.split(r'[,/ ]+', raw_keywords) if k.strip()]
    return [str(k).strip().lower() for k in raw_keywords if str(k).strip()]


def score_candidate_for_scene(cand: Dict[str, Any], target_role: str, keywords: List[str]) -> int:
    """텍스트 메타데이터 기반 점수 (비전 분류할 후보 선정 및 비전 미사용 시 배정에 사용)"""
    cand_cat = str(cand.get("category") or "unknown").lower()
    cand_title = str(cand.get("title") or "").lower()
    cand_alt = str(cand.get("alt") or "").lower()
    cand_url = urllib.parse.unquote(cand.get("url") or "").lower()
    combined_text = f"{cand_title} {cand_alt} {cand_url}"

    score = 0
    accepted = ROLE_ACCEPTED_CATEGORIES.get(target_role, {target_role})
    if cand_cat == target_role:
        score += 30
    elif cand_cat in accepted:
        score += 25

    for kw in keywords:
        if kw in cand_title:
            score += 15
        elif kw in cand_alt:
            score += 12
        elif kw in cand_url:
            score += 8

    if target_role in ("view", "view_or_night", "bedroom_or_view"):
        if any(k in combined_text for k in ["호수", "바다", "물멍", "오션", "하늘", "전경", "외관", "마운틴", "exterior", "lake", "ocean"]):
            score += 40
    if target_role == "facility":
        if any(k in combined_text for k in ["카페", "라운지", "통창", "바베큐", "바비큐", "불멍", "cafe", "lounge", "bbq", "레스토랑", "로비"]):
            score += 40
    if target_role == "pool":
        if any(k in combined_text for k in ["야외", "인피니티", "자쿠지", "outdoor", "infinity", "온수"]):
            score += 40
    if "night" in target_role or any(k in ("야경", "night") for k in keywords):
        if any(k in combined_text for k in ["야경", "night", "dusk", "sunset", "조명"]):
            score += 40
    return score


def _usable_url(cand: Dict[str, Any], excluded: set) -> bool:
    url = cand.get("url")
    return bool(url) and url not in excluded and cand.get("category") != "negative" \
        and not is_negative_image(f"{cand.get('title', '')} {cand.get('alt', '')}", url)


def _vision_ok(cand: Dict[str, Any]) -> bool:
    v = cand.get("vision")
    return bool(v) and v.get("category") in CATEGORIES and int(v.get("fit", 0)) >= VISION_MIN_FIT \
        and not cand.get("duplicate_of") and bool(cand.get("local_path")) and os.path.exists(cand["local_path"])


def candidate_category(cand: Dict[str, Any]) -> str:
    """비전 분류 결과가 있으면 그것을, 없으면 텍스트 메타 카테고리를 사용"""
    v = cand.get("vision")
    if v:
        return v.get("category", "reject")
    return str(cand.get("category") or "unknown")


def vision_inventory(image_pool: List[Dict[str, Any]]) -> Dict[str, int]:
    """대본 기획용 사진 인벤토리. 비전 분류된 후보가 있으면 '적합도 통과 사진' 기준, 없으면 텍스트 메타 기준"""
    counts = {c: 0 for c in CATEGORIES}
    if any(c.get("vision") for c in image_pool):
        for c in image_pool:
            if _vision_ok(c):
                counts[c["vision"]["category"]] += 1
        counts["source"] = "vision"
    else:
        for c in image_pool:
            if c.get("category") in counts:
                counts[c["category"]] += 1
        counts["source"] = "text"
    return counts


def _create_client(api_key: Optional[str]):
    if not api_key:
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception as e:
        logger.warning(f"Gemini 클라이언트 초기화 실패, 비전 분류 없이 진행: {e}")
        return None


def _select_for_classification(image_pool, excluded, quota, budget):
    """분류할 후보 선정: 카테고리별 목표 수 x3 만큼 텍스트 점수 상위 후보를 라운드로빈으로 섞고, 남는 예산은 미분류(unknown)로 채움"""
    fresh = [c for c in image_pool if not c.get("vision_checked") and _usable_url(c, excluded)]
    queues = []
    for cat, need in quota.items():
        if need <= 0:
            continue
        ranked = sorted(fresh, key=lambda c: score_candidate_for_scene(c, cat, []), reverse=True)
        queues.append(ranked[: need * 3])
    picked, seen = [], set()
    while len(picked) < budget and any(queues):
        for q in queues:
            while q and q[0]["url"] in seen:
                q.pop(0)
            if q and len(picked) < budget:
                c = q.pop(0)
                picked.append(c)
                seen.add(c["url"])
    for c in fresh:
        if len(picked) >= budget:
            break
        if c["url"] not in seen:
            picked.append(c)
            seen.add(c["url"])
    return picked


def classify_image_pool(
    image_pool: List[Dict[str, Any]],
    output_dir: Optional[str] = None,
    api_key: Optional[str] = None,
    vision_cache: Optional[Dict[str, Any]] = None,
    exclude_urls: Optional[set] = None,
    quota: Optional[Dict[str, int]] = None,
    budget: int = VISION_POOL_BUDGET,
) -> Dict[str, int]:
    """
    사진 풀 후보를 다운로드(100KB+)하고 비전 분류 결과를 후보 dict에 기록합니다 (vision, local_path, image_hash).
    image_pool의 dict를 직접 갱신하므로 세션에 보관된 풀에 결과가 누적됩니다. 반환: vision_inventory.
    """
    if not output_dir:
        raise ValueError("output_dir가 필요합니다. JobContext.assets_dir을 전달하세요.")
    client = _create_client(api_key)
    if client is None:
        return vision_inventory(image_pool)
    pool_dir = os.path.join(output_dir, "pool")
    os.makedirs(pool_dir, exist_ok=True)
    excluded = set(exclude_urls or set())
    seen_hashes = {c["image_hash"]: c["url"] for c in image_pool if c.get("image_hash") and not c.get("duplicate_of")}
    targets = _select_for_classification(image_pool, excluded, quota or DEFAULT_CATEGORY_QUOTA, budget)
    print(f"[AssetManager] [비전 분류] 사진 풀 {len(image_pool)}개 중 {len(targets)}개 분류 시작 (목표 {quota or DEFAULT_CATEGORY_QUOTA})")

    for cand in targets:
        cand["vision_checked"] = True
        try:
            resp = requests.get(cand["url"], headers=cdn_headers_for(cand["url"], cand.get("referer")), timeout=12)
            content = resp.content if resp.status_code == 200 else b""
        except Exception as e:
            logger.warning(f"후보 다운로드 예외 ({cand['url']}): {e}")
            continue
        if len(content) < MIN_ASSET_BYTES:
            cand["vision"] = {"category": "reject", "fit": 0, "tags": [], "reason": f"100KB 미만 ({len(content):,} bytes)"}
            continue
        h = hashlib.md5(content).hexdigest()
        cand["image_hash"] = h
        if h in seen_hashes and seen_hashes[h] != cand["url"]:
            cand["duplicate_of"] = seen_hashes[h]   # 같은 사진의 다른 해상도/URL 변형
            continue
        seen_hashes[h] = cand["url"]
        local = os.path.join(pool_dir, f"{h}.jpg")
        if not os.path.exists(local):
            with open(local, "wb") as f:
                f.write(content)
        cand["local_path"] = local
        verdict = classify_photo_with_vision(content, client, vision_cache)
        if verdict:
            cand["vision"] = verdict
            print(f"[AssetManager]   - [{verdict['category']}/{verdict['fit']}] {verdict['tags']} {cand.get('title', '')} | {verdict['reason']}")

    inv = vision_inventory(image_pool)
    print(f"[AssetManager] [비전 분류 완료] 사용 가능 인벤토리: {inv}")
    return inv


def _slot_score(cand: Dict[str, Any], role: str, tags: List[str], keywords: List[str]) -> float:
    v = cand.get("vision")
    if v:
        return int(v.get("fit", 0)) * 10 + 15 * len(set(v.get("tags", [])) & set(tags)) \
            + score_candidate_for_scene(cand, role, keywords) / 20
    return 50 + score_candidate_for_scene(cand, role, keywords) / 2


def download_targeted_scene_assets(
    scenes: List[Dict[str, Any]],
    image_pool: List[Dict[str, Any]],
    output_dir: Optional[str] = None,
    api_key: Optional[str] = None,
    exclude_urls: Optional[set] = None,
    vision_cache: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    [씬 슬롯 비율 배정 엔진]
    - 전달된 씬만 처리합니다 (단일 씬 재선별 가능). 결과는 각 scene dict에 키로 기록됩니다:
      image_path, matched_image_url, asset_status('matched'|'missing'), vision_verdict, asset_fallback
    - api_key가 있으면 사진 풀을 비전 분류(캐시)한 뒤, 슬롯 역할(target_visual_role)에 맞는 카테고리 중
      적합도 + 선호 태그(visual_tags) 점수가 가장 높은 사진을 배정합니다. 후보가 가장 적은 슬롯부터 배정해
      수영장3/객실2/부대시설2 같은 비율이 희소 사진 경쟁으로 깨지지 않게 합니다.
    - 해당 카테고리 사진이 없으면 analyzer와 같은 대체 순서로 채우고 asset_fallback에 표시, 그래도 없으면 'missing'.
    - exclude_urls: 사용자가 거부한 URL 및 다른 씬이 이미 사용 중인 URL
    """
    if not output_dir:
        raise ValueError("output_dir가 필요합니다. JobContext.assets_dir을 전달하세요.")
    os.makedirs(output_dir, exist_ok=True)
    excluded = set(exclude_urls or set())

    # 0. 이 씬들의 이전 결과 및 파생 캐시만 삭제 (다른 씬 파일은 건드리지 않음)
    for s_idx, scene in enumerate(scenes, 1):
        s_num = _scene_number(scene, s_idx)
        for key in ("image_path", "matched_image_url", "vision_verdict", "media", "asset_fallback"):
            scene.pop(key, None)
        scene["asset_status"] = "missing"
        for suffix in (".jpg", ".png", ".mp4", ".mp4.json", "_thumb.jpg"):
            stale = os.path.join(output_dir, f"scene_{s_num:02d}{suffix}")
            if os.path.exists(stale):
                try:
                    os.remove(stale)
                except Exception:
                    pass

    # 1. 필요한 카테고리 수 계산 → 비전 분류 (이미 분류된 사진은 캐시 재사용)
    need = {}
    for scene in scenes:
        role = str(scene.get("target_visual_role") or "view").lower().strip()
        cat = ROLE_PRIMARY_CATEGORY.get(role, role)
        need[cat] = need.get(cat, 0) + 1
    # 이미 분류된 사용 가능 사진으로 충분한 카테고리는 다시 분류하지 않음 (부족분만 추가 분류)
    have = {}
    for c in image_pool:
        if _vision_ok(c) and _usable_url(c, excluded):
            have[c["vision"]["category"]] = have.get(c["vision"]["category"], 0) + 1
    shortage = {cat: n - have.get(cat, 0) for cat, n in need.items() if n - have.get(cat, 0) > 0}
    if shortage:
        budget = VISION_POOL_BUDGET if len(scenes) > 1 else VISION_REMATCH_BUDGET
        classify_image_pool(image_pool, output_dir, api_key, vision_cache, excluded, shortage, budget)
    use_vision = any(c.get("vision") for c in image_pool)

    def eligible(scene, roles_ok, used_urls, used_hashes):
        out = []
        for cand in image_pool:
            if not _usable_url(cand, excluded) or cand["url"] in used_urls or cand.get("image_hash") in used_hashes:
                continue
            role_i = str(scene.get("target_visual_role") or "").lower().strip()
            if use_vision:
                if _vision_ok(cand) and cand["vision"]["category"] in roles_ok:
                    need_tags = ROLE_CATEGORY_REQUIRED_TAGS.get(role_i, {}).get(cand["vision"]["category"])
                    if not need_tags or need_tags & set(cand["vision"].get("tags", [])):
                        out.append(cand)
            elif candidate_category(cand) in roles_ok:
                need_text = ROLE_CATEGORY_REQUIRED_TEXT.get(role_i, {}).get(candidate_category(cand))
                if not need_text or any(t in f"{cand.get('title', '')} {cand.get('url', '')}".lower() for t in need_text):
                    out.append(cand)
        return out

    # 2. 희소한 슬롯부터 배정
    pending = list(enumerate(scenes, 1))
    used_urls, used_hashes, downloaded_paths = set(), set(), []
    print(f"\n[AssetManager] [슬롯 배정] {len(scenes)}개 씬 (비전={'ON' if use_vision else 'OFF'}, 제외 {len(excluded)}개)")
    while pending:
        infos = []
        for s_idx, scene in pending:
            role = str(scene.get("target_visual_role") or "view").lower().strip()
            accepted = ROLE_ACCEPTED_CATEGORIES.get(role, {role})
            tags_i, kw_i = list(scene.get("visual_tags") or []), _scene_keywords(scene)
            infos.append((s_idx, scene, role, accepted, tags_i, kw_i, eligible(scene, accepted, used_urls, used_hashes)))
        # 후보가 0~1장인 슬롯을 먼저 확정(희소 카테고리 보호), 그 외에는 전체 (슬롯, 사진) 쌍 중 최고 점수부터 배정
        forced = [i for i in infos if len(i[6]) <= 1]
        if forced:
            s_idx, scene, role, accepted, tags, keywords, cands = min(forced, key=lambda i: (len(i[6]), i[0]))
        else:
            best = max(((_slot_score(c, i[2], i[4], i[5]), -i[0], k, j) for k, i in enumerate(infos) for j, c in enumerate(i[6])))
            s_idx, scene, role, accepted, tags, keywords, cands = infos[best[2]]
            cands = [cands[best[3]]] + [c for j, c in enumerate(cands) if j != best[3]]
        pending = [(i, s) for i, s in pending if i != s_idx]
        s_num = _scene_number(scene, s_idx)

        fallback_cat = None
        if not cands:
            # 대체: core.analyzer.SLOT_FALLBACKS와 같은 순서 (수영장→뷰→부대시설 등)
            for fb in {"pool": ["view", "facility"], "bedroom": ["view"], "bedroom_or_view": ["facility"],
                       "facility": ["view", "bedroom"], "view": ["facility", "bedroom"], "view_or_night": ["bedroom"],
                       "facility_or_night_view": ["bedroom"]}.get(role, []):
                cands = eligible(scene, {fb}, used_urls, used_hashes)
                if cands:
                    fallback_cat = fb
                    break
        if fallback_cat or forced:
            cands.sort(key=lambda c: _slot_score(c, role, tags, keywords), reverse=True)
        else:
            cands = cands[:1] + sorted(cands[1:], key=lambda c: _slot_score(c, role, tags, keywords), reverse=True)

        chosen, content = None, None
        for cand in cands:
            if cand.get("local_path") and os.path.exists(cand["local_path"]):
                with open(cand["local_path"], "rb") as f:
                    content = f.read()
            else:   # 비전 미사용(API 키 없음): 배정 시점에 다운로드 + 크기/중복 검증
                try:
                    resp = requests.get(cand["url"], headers=cdn_headers_for(cand["url"], cand.get("referer")), timeout=12)
                    content = resp.content if resp.status_code == 200 else b""
                except Exception:
                    content = b""
                if len(content) < MIN_ASSET_BYTES or hashlib.md5(content).hexdigest() in used_hashes:
                    used_urls.add(cand["url"])
                    continue
                cand["image_hash"] = hashlib.md5(content).hexdigest()
            chosen = cand
            break

        if chosen is None:
            print(f"[AssetManager] [매칭 실패] 씬 {s_num} ({role}): 적합한 사진이 없습니다. 수동 교체 또는 업로드가 필요합니다.")
            continue

        save_path = os.path.abspath(os.path.join(output_dir, f"scene_{s_num:02d}.jpg"))
        with open(save_path, "wb") as f:
            f.write(content)
        used_urls.add(chosen["url"])
        used_hashes.add(chosen.get("image_hash") or hashlib.md5(content).hexdigest())
        scene["image_path"] = save_path
        scene["matched_image_url"] = chosen["url"]
        scene["asset_status"] = "matched"
        scene["vision_verdict"] = chosen.get("vision")
        if fallback_cat:
            scene["asset_fallback"] = fallback_cat
        downloaded_paths.append(save_path)
        v = chosen.get("vision") or {}
        print(f"[AssetManager] [배정] 씬 {s_num} [{role}{' → ' + fallback_cat if fallback_cat else ''}] "
              f"{v.get('category', candidate_category(chosen))}/{v.get('fit', '-')} {v.get('tags', [])} (선호 {tags}) <- {chosen.get('title', '')}")

    print(f"[AssetManager] [슬롯 배정 완료] {len(downloaded_paths)}/{len(scenes)}개 씬\n")
    return downloaded_paths


# --- 단색 더미 이미지 생성 로직 전면 삭제 (실물 크롤링 및 수동 업로드만 허용) ---
def generate_sample_travel_gallery(*args, **kwargs) -> list:
    """
    단색 더미 생성 로직 전면 삭제:
    어떠한 예외 상황에서도 단색 이미지를 생성하거나 저장하지 않고 빈 리스트를 반환합니다.
    """
    print("[Scraper] 단색 더미 이미지 생성 로직이 전면 삭제되었습니다. 실물 사진만 허용됩니다.")
    return []




