"""
core/photo_safety.py - 자동 수집 사진의 저작권·초상권 4중 방어 (전 모드 공통: 매장·숙소·쇼핑·책방이 같은 수집기를 씀)

① 도메인: 개인 블로거 촬영 도메인(blogfiles.naver.net 등)은 기본 수집에서 제외(또는 맨 뒤 + 경고), 업체 공식 도메인(네이버 플레이스·여기어때·쇼핑)을 1순위로
② 비전: 사람 얼굴·신체(손·뒷모습 포함), 블로거 서명·개인 워터마크·타사 로고, 영수증·글씨 위주 메뉴판이 있으면 무조건 자동 제외
③ 시각 변형: 원본 그대로가 아니라 테두리 크롭 + 미세 색감 보정 + 상하단 그라데이션 비네팅으로 편집 저작물 프레임으로 변환 (켄번스 줌 모션은 렌더러가 추가)
④ 출처·대가성 문구: 제휴 쇼츠 업로드 텍스트에 자동 포함 (SOURCE_NOTICE)
이 필터는 분쟁 위험을 '줄이는' 장치이지 권리 확인을 대신하지 않는다: 공개 업로드 전 점주 동의 확인은 여전히 필요하다.
"""
import logging
import urllib.parse
from typing import Any, Dict, List, Optional

from PIL import Image, ImageEnhance

logger = logging.getLogger("ad_factory.photo_safety")

BLOG_DOMAINS = ("blogfiles.naver.net", "postfiles.naver.net", "mblogthumb-phinf.pstatic.net", "blogthumb.pstatic.net",
                "cafeptthumb-phinf.pstatic.net", "cafefiles.naver.net", "tistory.com", "tistory.co.kr")
OFFICIAL_DOMAINS = ("ldb-phinf.pstatic.net", "image.goodchoice.kr", "shop-phinf.pstatic.net", "image.withstatic.com", "cdn.yeogi.com")
SOURCE_NOTICE = "※ 본 영상은 업체 공식 안내 이미지를 활용하여 제작되었으며, 제휴 마케팅 활동의 일환으로 수수료를 제공받을 수 있습니다."
FLAG_LABELS = {"person": "사람 얼굴·신체", "watermark": "워터마크·로고", "text": "영수증·글씨 위주"}

CROP_TOTAL = 0.11                 # 가로·세로 각각 전체의 11%를 테두리에서 잘라냄 (가장자리 양쪽 5.5%씩 = 중앙 89% 사용)
TOP_VIGNETTE, BOTTOM_VIGNETTE = 0.14, 0.20        # 그라데이션이 차지하는 높이 비율
VIGNETTE_STRENGTH = 0.34


def host_of(url: str) -> str:
    return urllib.parse.urlparse((url or "").strip()).netloc.lower().split(":")[0]


def _match(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) or host.endswith(d) for d in domains)


def source_kind(url: str) -> str:
    """'official'(업체 공식 홍보 이미지) / 'blog'(개인 블로거 사진) / 'other'(그 밖의 출처: 업체 홈페이지 등)"""
    h = host_of(url)
    if _match(h, OFFICIAL_DOMAINS):
        return "official"
    if _match(h, BLOG_DOMAINS):
        return "blog"
    return "other"


def rank_sources(items: List[Dict[str, Any]], official_only: bool = True) -> List[Dict[str, Any]]:
    """각 항목에 source를 달고 공식 → 기타 → 블로그 순으로 (원래 순서 유지). official_only면 블로그 사진은 빼고 돌려줌"""
    order = {"official": 0, "other": 1, "blog": 2}
    tagged = [{**it, "source": source_kind(it.get("link") or it.get("thumbnail") or "")} for it in items]
    if official_only:
        tagged = [it for it in tagged if it["source"] != "blog"]
    return sorted(tagged, key=lambda it: order[it["source"]])


# ---------------------------------------------------------------- 시각 변형 (편집 저작물 프레임)
def transform_photo(img: Image.Image) -> Image.Image:
    """① 테두리 크롭(중앙 89%) ② 따뜻한 색감 + 채도·대비 소폭 상향 ③ 상하단 그라데이션 비네팅. 입력 크기가 작아도 안전"""
    img = img.convert("RGB")
    w, h = img.size
    dx, dy = int(w * CROP_TOTAL / 2), int(h * CROP_TOTAL / 2)
    if w - 2 * dx >= 64 and h - 2 * dy >= 64:
        img = img.crop((dx, dy, w - dx, h - dy))
    r, g, b = img.split()
    r = r.point(lambda v: min(255, int(v * 1.04 + 1)))
    b = b.point(lambda v: int(v * 0.96))
    img = Image.merge("RGB", (r, g, b))
    img = ImageEnhance.Color(img).enhance(1.12)
    img = ImageEnhance.Contrast(img).enhance(1.05)
    import numpy as np
    arr = np.asarray(img).astype("float32")
    hh = arr.shape[0]
    shade = np.ones((hh, 1, 1), dtype="float32")
    top_n, bot_n = max(1, int(hh * TOP_VIGNETTE)), max(1, int(hh * BOTTOM_VIGNETTE))
    shade[:top_n, 0, 0] = 1 - VIGNETTE_STRENGTH * (1 - np.linspace(0, 1, top_n)) ** 1.5
    shade[hh - bot_n:, 0, 0] = 1 - VIGNETTE_STRENGTH * np.linspace(0, 1, bot_n) ** 1.5
    return Image.fromarray(np.clip(arr * shade, 0, 255).astype("uint8"))


# ---------------------------------------------------------------- 비전: 초상권·워터마크·글씨 차단
def screen_flags(images: List[Image.Image], api_key: Optional[str] = None, context: str = "") -> List[str]:
    """
    사진마다 위반 사유 → ['', 'person', 'watermark', 'text', ...] (''이면 통과). 비전을 못 쓰면(키 없음·실패) 전부 ''(= 검사 못 함).
    ① 사람의 얼굴·신체(손·뒷모습 포함) ② 블로거 서명·개인 워터마크·타사 로고 ③ 영수증·글씨 위주 메뉴판 → 무조건 제외 대상
    """
    flags = [""] * len(images)
    if not images:
        return flags
    try:
        from core.llm import generate_json
        contents: List[Any] = []
        for i, im in enumerate(images):
            thumb = im.copy()
            thumb.thumbnail((512, 512))
            contents += [f"[사진 {i + 1}]", thumb]
        contents.append(f"""[역할: 광고 영상용 사진 저작권·초상권 검수관] {context}
사진 {len(images)}장을 각각 검사해 아래에 하나라도 해당하면 flag에 사유를 쓰세요. 애매하면 통과가 아니라 제외입니다.
- person: 사람의 얼굴·신체가 보임 (손, 팔, 다리, 뒷모습, 멀리 있는 사람 포함)
- watermark: 블로거 서명, 개인 워터마크, 업체 이외의 로고·아이디 글씨가 박혀 있음
- text: 영수증·글씨 위주 메뉴판·안내문·캡처 화면
해당 없으면 "". JSON만 출력: {{"photos": [{{"index": 1, "flag": "" | "person" | "watermark" | "text"}}, ...]}}""")
        data, _ = generate_json(contents, "당신은 사진에 보이는 사실만 판정하는 꼼꼼한 검수관입니다. 응답은 JSON만 출력합니다.",
                                temperature=0.0, gemini_api_key=api_key, purpose="사진 초상권 검수")
        for item in (data.get("photos") if isinstance(data, dict) else None) or []:
            k = int(item.get("index") or 0) - 1
            f = str(item.get("flag") or "").strip().lower()
            if 0 <= k < len(flags) and f in FLAG_LABELS:
                flags[k] = f
    except Exception as e:
        logger.warning(f"사진 초상권·워터마크 검수 실패 (검수 없이 진행): {e}")
    return flags
