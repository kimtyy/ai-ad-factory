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
# [비전 검증 게이트]
# 텍스트 메타데이터만으로는 '로비'라는 제목의 자판기 사진, '외관'이라는 제목의 아파트형 외벽 사진을
# 걸러낼 수 없으므로, 텍스트 점수 상위 후보를 실제 픽셀 기준으로 재판정합니다.
# -------------------------------------------------------------
VISION_MIN_FIT = 6          # 이 점수 미만은 탈락
VISION_GOOD_FIT = 8         # 이 점수 이상이면 즉시 채택
VISION_MAX_CHECKS = 8       # 씬당 최대 비전 판정 횟수

ROLE_DESCRIPTIONS = {
    "view": "숙소 외관/전경/경관 뷰 (첫 장면 후킹용, 시원하고 매력적인 풍경)",
    "pool": "수영장/자쿠지/스파/물놀이 공간",
    "facility": "라운지/로비/카페/레스토랑 등 분위기 좋은 부대시설",
    "bedroom": "객실 내부 (침실/거실)",
    "view_or_night": "엔딩용 야경 또는 감성적인 전경/테라스",
}

ROLE_ACCEPTED_CATEGORIES = {
    "view": {"view"},
    "pool": {"pool"},
    "facility": {"facility"},
    "bedroom": {"bedroom"},
    "view_or_night": {"view", "facility"},
}

VISION_VERDICT_PROMPT = (
    "You are screening photos for a short-form accommodation ad.\n"
    "The scene needs: {role_desc}. Script keywords: {keywords}.\n"
    "1) Classify the photo into exactly one category:\n"
    "   - 'pool': swimming pool, jacuzzi, spa, water play area\n"
    "   - 'view': building exterior that looks attractive, panoramic landscape, lake/ocean/mountain view, terrace, night view\n"
    "   - 'facility': lounge, lobby, cafe, restaurant, garden, fitness, kids zone\n"
    "   - 'bedroom': guest room interior, bed, living room\n"
    "   - 'reject': anything that ruins the mood. ALWAYS reject: vending machines, convenience store or kiosk corners, "
    "parking lots, corridors, stairs, elevators, toilets, plain concrete or apartment-like facades with no scenery, "
    "construction, maps, floor plans, signboards, text-heavy images, blurry or dark photos.\n"
    "2) Rate 'fit' 0-10: how well this photo matches the needed scene and how appealing it is in an ad.\n"
    'Respond with JSON only: {{"category": "...", "fit": 0, "reason": "short reason"}}'
)


def verify_scene_photo_with_vision(
    img_bytes: bytes,
    target_role: str,
    keywords: List[str],
    client: Any,
    cache: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Gemini Flash 비전으로 사진의 실제 내용을 판정합니다.
    반환: {"category", "fit", "reason"} / 비전 사용 불가 또는 호출 실패 시 None.
    판정 결과는 (이미지 해시, 역할) 기준으로 cache에 저장되어 재선별 시 재호출하지 않습니다.
    """
    if client is None:
        return None
    cache_key = f"{hashlib.md5(img_bytes).hexdigest()}:{target_role}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    try:
        from google.genai import types
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((512, 512))
        prompt = VISION_VERDICT_PROMPT.format(
            role_desc=ROLE_DESCRIPTIONS.get(target_role, target_role),
            keywords=", ".join(keywords) if keywords else "-"
        )
        res = client.models.generate_content(
            model=validate_model_call("gemini-3.6-flash", category="llm"),
            contents=[img, prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
        )
        data = json.loads(res.text)
        if isinstance(data, list):
            data = data[0] if data else {}
        verdict = {
            "category": str(data.get("category", "reject")).lower().strip(),
            "fit": int(float(data.get("fit", 0))),
            "reason": str(data.get("reason", ""))[:160],
        }
    except Exception as e:
        logger.warning(f"비전 판정 실패 (텍스트 점수로 대체): {e}")
        return None
    if cache is not None:
        cache[cache_key] = verdict
    return verdict


def classify_photo_with_gemini_vision(
    image_url: str,
    api_key: Optional[str] = None
) -> str:
    """Gemini Flash 멀티모달 비전 모델로 숙소 사진을 pool / view / facility / bedroom 중 하나로 초고속 분류 (방안 B)"""
    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "unknown"
    try:
        from google import genai
        from PIL import Image

        resp = requests.get(image_url, headers=cdn_headers_for(image_url), timeout=8)
        if resp.status_code != 200 or len(resp.content) < 5000:
            return "unknown"

        img = Image.open(io.BytesIO(resp.content))
        img.thumbnail((512, 512))

        model_name = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        client = genai.Client(api_key=api_key)
        prompt = (
            "Classify this accommodation photo into exactly one category:\n"
            "- 'pool': Swimming pool (outdoor or indoor), jacuzzi, water play area, spa\n"
            "- 'view': Exterior building, panoramic landscape, lake/ocean view, terrace, night view\n"
            "- 'facility': Lounge, lobby, restaurant, cafe, garden, fitness center, kids zone\n"
            "- 'bedroom': Hotel room interior, bed, living room\n"
            "Respond with only the category word (pool, view, facility, or bedroom)."
        )
        res = client.models.generate_content(
            model=model_name,
            contents=[img, prompt]
        )
        raw_cat = res.text.strip().lower()
        for cat in ["pool", "view", "facility", "bedroom"]:
            if cat in raw_cat:
                logger.info(f"[Gemini Vision 분류 성공] {image_url[:50]}... -> {cat}")
                print(f"[Scraper] [Gemini Vision] {image_url[:55]}... -> {cat}")
                return cat
        return "unknown"
    except Exception as e:
        logger.debug(f"Gemini Vision 분류 예외: {e}")
        return "unknown"


def select_balanced_travel_gallery(
    candidates: List[Dict[str, Any]],
    target_count: int = 7,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    [황금 비율 카테고리별 분산 선별 로직]
    총 6~7장의 최종 에셋을 선택할 때 아래 비율 강제:
    ① 수영장 / 자쿠지 / 노천탕 / 물 관련 공간: 2장 이상 (핵심 후킹)
    ② 외관 전경 / 야경 / 오션·마운틴 뷰 / 테라스: 2장 이상
    ③ 시그니처 부대시설 (라운지, 로비, 레스토랑, 바베큐장 등): 1~2장
    ④ 객실 내부 (침실/거실): 최대 1~2장으로 엄격 제한 (침실 독점 차단)
    
    숏폼 연출 최적 시퀀싱:
    Cut 1: 외관 전경/호수 뷰 (view) - 후킹 & 호기심 유발 (상단 로고 은닉 크롭 적용)
    Cut 2: 야외 수영장 (pool) - 2~3초 시점 강력한 비주얼 후킹
    Cut 3: 실내 수영장 / 자쿠지 (pool) - 사계절 힐링 물놀이 공간
    Cut 4: 시그니처 라운지 / 로비 (facility) - 5성급 럭셔리 공간감
    Cut 5: 아늑한 럭셔리 침실 (bedroom) - 단 1장만 배치
    Cut 6: 루프탑 테라스 / 산책로 / 야경 전경 (view/facility) - 엔딩 무드 & CTA
    Cut 7 (선택 시): 추가 테라스/외관/야경(view) 또는 추가 수영장(pool)
    """
    # 1. 1차 메타데이터 분류
    pools = [c for c in candidates if c.get("category") == "pool"]
    views = [c for c in candidates if c.get("category") == "view"]
    facs = [c for c in candidates if c.get("category") == "facility"]
    rooms = [c for c in candidates if c.get("category") == "bedroom"]
    unknowns = [c for c in candidates if c.get("category") == "unknown"]

    print(f"[Scraper] [1차 메타 분류] Pool={len(pools)}장, View={len(views)}장, Fac={len(facs)}장, Bedroom={len(rooms)}장, Unknown={len(unknowns)}장")

    # 2. 필수 카테고리(pool >= 2, view >= 2) 부족 시 Gemini Vision 분류 가동 (방안 B)
    if (len(pools) < 2 or len(views) < 2) and (unknowns or facs):
        print("[Scraper] [Gemini Vision 가동] 수영장 또는 전경/뷰 컷 보충을 위해 멀티모달 비전 분류를 진행합니다...")
        vision_candidates = unknowns + facs
        for c in vision_candidates[:10]:
            if len(pools) >= 2 and len(views) >= 2:
                break
            v_cat = classify_photo_with_gemini_vision(c["url"], api_key=api_key)
            if v_cat in ["pool", "view", "facility", "bedroom"]:
                c["category"] = v_cat
                if v_cat == "pool" and c not in pools:
                    pools.append(c)
                elif v_cat == "view" and c not in views:
                    views.append(c)

    # 시각적 임팩트 및 후킹 최적화를 위한 카테고리 내부 정렬
    # - pool: 야외 수영장, 인피니티풀 등 임팩트 큰 컷을 1순위로
    pools.sort(key=lambda x: 0 if any(k in f"{x.get('title','')} {x['url']}".lower() for k in ["야외", "인피니티", "outdoor", "infinity"]) else 1)
    # - view: 대표 사진/외관을 1순위로, 테라스/야경을 2순위로
    views.sort(key=lambda x: 0 if any(k in f"{x.get('title','')} {x['url']}".lower() for k in ["대표", "외관", "exterior"]) else 1)
    # - facs: 시그니처 라운지/로비를 1순위로
    facs.sort(key=lambda x: 0 if any(k in f"{x.get('title','')} {x['url']}".lower() for k in ["라운지", "로비", "lounge", "lobby"]) else 1)

    print(f"[Scraper] [비전 보정 후] Pool={len(pools)}장, View={len(views)}장, Fac={len(facs)}장, Bedroom={len(rooms)}장")

    final_selected = []
    used_urls = set()

    def add_candidate(cand: Dict[str, Any]) -> bool:
        if cand and cand["url"] not in used_urls:
            final_selected.append(cand)
            used_urls.add(cand["url"])
            return True
        return False

    # [황금 시퀀싱 배치]
    # Cut 1: 외관 전경 / 호수 전경 (view)
    if views:
        add_candidate(views[0])
    elif facs:
        add_candidate(facs[0])

    # Cut 2: 메인 야외 수영장 (pool - 핵심 후킹)
    if pools:
        add_candidate(pools[0])

    # Cut 3: 실내 수영장 또는 서브 온수풀 / 자쿠지 (pool)
    if len(pools) > 1:
        add_candidate(pools[1])
    elif len(views) > 1:
        add_candidate(views[1])

    # Cut 4: 시그니처 라운지 / 로비 / 부대시설 (facility)
    for f in facs:
        if add_candidate(f):
            break

    # Cut 5: 럭셔리 침실 (bedroom - 침실 독점 차단을 위해 단 1장만 배치!)
    if rooms:
        add_candidate(rooms[0])

    # Cut 6: 루프탑 테라스 / 산책로 / 야경 전경 (view 또는 facility)
    added_cut6 = False
    for v in views:
        if add_candidate(v):
            added_cut6 = True
            break
    if not added_cut6:
        for f in facs:
            if add_candidate(f):
                added_cut6 = True
                break

    # Cut 7 (선택 시): 6~7장 규격 충족용 (수영장, 전경, 부대시설 순으로 보충, 침실은 추가 배제)
    if len(final_selected) < target_count:
        for pool_cand in pools:
            if len(final_selected) >= target_count:
                break
            add_candidate(pool_cand)
        for view_cand in views:
            if len(final_selected) >= target_count:
                break
            add_candidate(view_cand)
        for fac_cand in facs:
            if len(final_selected) >= target_count:
                break
            add_candidate(fac_cand)

    print(f"[Scraper] [황금 비율 선별 완료] 총 {len(final_selected)}장 선정:")
    for order, c in enumerate(final_selected, 1):
        print(f"  - 씬 {order}: [{c.get('category', 'unknown').upper()}] {c.get('title', '')} | {c['url']}")

    return final_selected


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


def scrape_product_info(
    url: str,
    output_image_dir: str = "outputs/cache",
    mode: str = "commerce",
    max_gallery_images: int = 7
) -> Dict[str, Any]:
    """
    쇼핑몰 또는 숙소/여행 상품 URL을 분석하여 제품명, USP(소구점 설명), 대표 이미지 URL 및
    여행 상품 모드용 다중 갤러리 컷(5~7장)을 추출하여 로컬 저장 경로를 반환합니다.
    """
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
        "error": None
    }

    try:
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

        if not title:
            title = "제주 애월 프라이빗 감성 풀빌라" if mode == "travel" else "인기 뷰티 수분 앰플"
        result["product_name"] = title
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

        if not desc:
            desc = (
                "전 객실 파노라마 오션뷰, 야외 온수 인피니티 풀, 프라이빗 자쿠지 및 숲속 힐링 공간"
                if mode == "travel"
                else "속당김 즉각 해결, 48시간 고보습 지속 및 피부 장벽 강화"
            )
        result["usp"] = desc
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
            # 일반 커머스 모드: 대표 단일 이미지 다운로드 (누끼용)
            if clean_candidates:
                first_url = clean_candidates[0]["url"]
                img_ext = ".png" if ".png" in first_url.lower() else ".jpg"
                local_img_path = os.path.join(output_image_dir, f"scraped_product{img_ext}")
                try:
                    img_resp = requests.get(first_url, headers=cdn_headers_for(first_url, page_origin(url)), timeout=10)
                    content_len = len(img_resp.content) if img_resp.content else 0
                    if img_resp.status_code == 200 and content_len >= 3000:
                        with open(local_img_path, "wb") as f:
                            f.write(img_resp.content)
                        result["image_path"] = os.path.abspath(local_img_path)
                        print(f"[Scraper] 대표 상품 이미지 다운로드 성공: {local_img_path} ({content_len:,} bytes)")
                    else:
                        print(f"[CRITICAL ERROR] 대표 상품 이미지 다운로드 실패: HTTP {img_resp.status_code}, 크기={content_len}")
                except Exception as ie:
                    logger.warning(f"대표 이미지 다운로드 실패: {ie}")
                    print(f"[CRITICAL ERROR] 대표 이미지 다운로드 예외: {ie}")

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
# [Step 3. 핀포인트 타겟 에셋 다운로드]
# 기획된 씬의 태그/키워드와 이미지 풀을 대조하여 텍스트 점수로 후보를 정렬하고,
# 상위 후보를 비전 검증 게이트로 실제 픽셀 기준 재판정한 뒤 씬별로 1장씩 다운로드합니다 (100KB+ 검증).
# =============================================================================
MIN_ASSET_BYTES = 100000


def _scene_number(scene: Dict[str, Any], default: int) -> int:
    return int(scene.get("scene_index") or scene.get("scene_number") or default)


def _scene_keywords(scene: Dict[str, Any]) -> List[str]:
    raw_keywords = scene.get("visual_keywords") or []
    if isinstance(raw_keywords, str):
        return [k.strip().lower() for k in re.split(r'[,/ ]+', raw_keywords) if k.strip()]
    return [str(k).strip().lower() for k in raw_keywords if str(k).strip()]


def score_candidate_for_scene(cand: Dict[str, Any], target_role: str, keywords: List[str]) -> int:
    """텍스트 메타데이터 기반 1차 점수 (비전 게이트 전 후보 정렬용)"""
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

    if target_role in ("view", "view_or_night"):
        if any(k in combined_text for k in ["호수", "바다", "물멍", "오션", "하늘", "전경", "외관", "exterior", "lake", "ocean"]):
            score += 40
        if any(k in combined_text for k in ["대표", "메인", "main"]):
            score += 20
    if target_role == "facility":
        if any(k in combined_text for k in ["카페", "라운지", "통창", "소파", "테이블", "cafe", "lounge", "lobby", "레스토랑", "restaurant", "로비"]):
            score += 40
    if target_role == "pool":
        if any(k in combined_text for k in ["야외", "인피니티", "outdoor", "infinity", "온수"]):
            score += 40
    if "night" in target_role or any(k in ("야경", "night") for k in keywords):
        if any(k in combined_text for k in ["야경", "night", "dusk", "sunset", "조명"]):
            score += 40
    return score


def download_targeted_scene_assets(
    scenes: List[Dict[str, Any]],
    image_pool: List[Dict[str, Any]],
    output_dir: str = "outputs/cache",
    api_key: Optional[str] = None,
    exclude_urls: Optional[set] = None,
    vision_cache: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    [Step 3. 핀포인트 타겟 에셋 다운로드 엔진]
    - 전달된 씬만 처리합니다 (단일 씬 재선별 가능). 결과는 각 scene dict에 키로 기록됩니다:
      image_path, matched_image_url, asset_status('matched'|'missing'), vision_verdict
    - exclude_urls: 사용자가 거부한 URL 및 다른 씬이 이미 사용 중인 URL (재선별 시 같은 사진 반복 방지)
    - api_key가 있으면 텍스트 상위 후보를 Gemini 비전으로 재판정하여 reject/저적합 사진을 탈락시킵니다.
      비전 판정을 통과한 사진이 없으면 그 씬은 'missing'으로 남기고 억지로 아무 사진이나 채우지 않습니다.
    """
    os.makedirs(output_dir, exist_ok=True)
    excluded = set(exclude_urls or set())
    used_urls = set()
    used_hashes = set()
    downloaded_paths = []

    client = None
    if api_key:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
        except Exception as e:
            logger.warning(f"Gemini 클라이언트 초기화 실패, 비전 검증 없이 진행: {e}")
    if client is None:
        print("[AssetManager] [경고] API 키가 없어 비전 검증 게이트를 건너뜁니다 (텍스트 점수만 사용).")

    print(f"\n[AssetManager] [Step 3 시작] {len(scenes)}개 씬 타겟 에셋 매칭 (풀 {len(image_pool)}개, 제외 {len(excluded)}개, 비전={'ON' if client else 'OFF'})")

    for s_idx, scene in enumerate(scenes, 1):
        s_num = _scene_number(scene, s_idx)
        target_role = str(scene.get("target_visual_role") or "view").lower().strip()
        keywords = _scene_keywords(scene)

        # 0. 이 씬의 이전 결과 및 파생 캐시만 삭제 (다른 씬 파일은 건드리지 않음)
        for key in ("image_path", "matched_image_url", "vision_verdict"):
            scene.pop(key, None)
        scene["asset_status"] = "missing"
        for ext in (".jpg", ".png", ".mp4"):
            stale = os.path.join(output_dir, f"scene_{s_num:02d}{ext}")
            if os.path.exists(stale):
                try:
                    os.remove(stale)
                except Exception:
                    pass

        print(f"[AssetManager] 씬 {s_num} 매칭 탐색: target_role='{target_role}', keywords={keywords}")

        scored_candidates = []
        for cand in image_pool:
            url = cand.get("url")
            if not url or url in used_urls or url in excluded:
                continue
            if cand.get("category") == "negative" or is_negative_image(f"{cand.get('title', '')} {cand.get('alt', '')}", url):
                continue
            scored_candidates.append((score_candidate_for_scene(cand, target_role, keywords), cand))
        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        accepted_cats = ROLE_ACCEPTED_CATEGORIES.get(target_role, {target_role})
        best = None  # (fit, text_score, cand, content, verdict)
        vision_checks = 0

        for score, cand in scored_candidates:
            if client is not None and vision_checks >= VISION_MAX_CHECKS:
                break
            img_u = cand["url"]
            try:
                resp = requests.get(img_u, headers=cdn_headers_for(img_u, cand.get("referer")), timeout=12)
            except Exception as e:
                logger.warning(f"후보 다운로드 예외 ({img_u}): {e}")
                continue
            content = resp.content if resp.status_code == 200 else b""
            if len(content) < MIN_ASSET_BYTES:
                continue
            if hashlib.md5(content).hexdigest() in used_hashes:
                continue  # 같은 사진의 다른 해상도/URL 변형

            if client is None:
                best = (None, score, cand, content, None)
                break

            vision_checks += 1
            verdict = verify_scene_photo_with_vision(content, target_role, keywords, client, vision_cache)
            if verdict is None:
                # 비전 호출 실패 시 텍스트 점수 1순위로 대체하되 미검증 표시
                best = best or (None, score, cand, content, None)
                continue
            ok = verdict["category"] in accepted_cats and verdict["fit"] >= VISION_MIN_FIT
            print(f"[AssetManager]   - 비전 판정 [{verdict['category']}/{verdict['fit']}] {'채택후보' if ok else '탈락'}: {cand.get('title', '')} | {verdict['reason']}")
            if not ok:
                continue
            if best is None or best[0] is None or verdict["fit"] > best[0]:
                best = (verdict["fit"], score, cand, content, verdict)
            if verdict["fit"] >= VISION_GOOD_FIT:
                break

        if best is None:
            print(f"[AssetManager] [매칭 실패] 씬 {s_num}: 적합한 사진이 없습니다. 수동 교체 또는 업로드가 필요합니다.")
            continue

        _, score, cand, content, verdict = best
        save_path = os.path.abspath(os.path.join(output_dir, f"scene_{s_num:02d}.jpg"))
        with open(save_path, "wb") as f:
            f.write(content)

        used_urls.add(cand["url"])
        used_hashes.add(hashlib.md5(content).hexdigest())
        scene["image_path"] = save_path
        scene["matched_image_url"] = cand["url"]
        scene["asset_status"] = "matched"
        scene["vision_verdict"] = verdict
        downloaded_paths.append(save_path)
        v_tag = f"vision {verdict['category']}/{verdict['fit']}" if verdict else "vision 미검증"
        print(f"[AssetManager] [다운로드 성공] 씬 {s_num}: [{str(cand.get('category', 'unknown')).upper()}] {cand.get('title', '')} (text {score}, {v_tag}) -> {save_path} ({len(content)/1024:.1f} KB)")

    print(f"[AssetManager] [Step 3 완료] {len(downloaded_paths)}/{len(scenes)}개 씬 매칭 완료\n")
    return downloaded_paths


# --- 단색 더미 이미지 생성 로직 전면 삭제 (실물 크롤링 및 수동 업로드만 허용) ---
def generate_sample_travel_gallery(*args, **kwargs) -> list:
    """
    단색 더미 생성 로직 전면 삭제:
    어떠한 예외 상황에서도 단색 이미지를 생성하거나 저장하지 않고 빈 리스트를 반환합니다.
    """
    print("[Scraper] 단색 더미 이미지 생성 로직이 전면 삭제되었습니다. 실물 사진만 허용됩니다.")
    return []




