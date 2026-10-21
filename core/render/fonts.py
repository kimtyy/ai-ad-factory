"""
core/render/fonts.py - 쇼츠·릴스 자막 폰트 3종 프리셋 (전 모드 공통 코어)

  impact  🔥 임팩트 고딕 (기본)   Pretendard-Black → Noto Sans CJK KR Bold  | 굵은 검은 외곽선 + 그림자 (맛집·쇼핑: 시선 강탈·정보 전달)
  serif   ✨ 감성 명조           Noto Serif KR Bold → Noto Serif CJK KR Bold  | 얇은 외곽선(약 2px) + 부드러운 번짐 그림자 (숙소·카페·책방)
  pop     ⚡ 둥근 예능 팝체       Gmarket Sans Bold → Cafe24 서라운드 → Noto Sans CJK KR Bold | 매우 굵은 외곽선 + 경쾌한 그림자 (꿀템·핫플·젊은 타깃)

네모 박스(두부) 방지: 파일이 없으면 받아 오고(fonts/), 실패하면 리눅스에 설치된 Noto CJK(패키지 fonts-noto-cjk) 또는 윈도 시스템 한글 폰트로 대체한다.
.ttc 묶음 폰트는 한국어 서브 폰트(index 1)를 지정해 연다.
"""
import logging
import os
import urllib.request
from typing import Dict, List, Optional, Tuple

from PIL import ImageFont

logger = logging.getLogger("ad_factory.render.fonts")

Spec = Tuple[str, int]                       # (파일 경로, ttc 인덱스)
DEFAULT_KEY = "impact"
PRESETS: Dict[str, Dict[str, str]] = {
    "impact": {"label": "🔥 임팩트 고딕 (기본)", "use": "맛집·쇼핑 — 굵은 외곽선으로 시선 강탈·정보 전달"},
    "serif": {"label": "✨ 감성 명조 (숙소·카페 추천)", "use": "숙소·풀빌라·카페·책방 — 얇은 테두리 + 부드러운 그림자로 고급스러운 감성"},
    "pop": {"label": "⚡ 둥근 예능 팝체", "use": "가성비 꿀템·핫플·젊은 타깃 리뷰 — 굵은 외곽선 + 경쾌한 팝 스타일"},
}
# 외곽선·그림자 스타일 (impact = None: 모드 정책의 기존 값 그대로)
STYLES: Dict[str, Optional[Dict[str, float]]] = {
    "impact": None,
    "serif": {"stroke_ratio": 0.0022, "shadow_alpha": 185, "shadow_blur": 0.013, "passes": 1, "offset": 0.004},
    "pop": {"stroke_ratio": 0.0105, "shadow_alpha": 255, "shadow_blur": 0.007, "passes": 2, "offset": 0.008},
}

SANS_SYSTEM: List[Spec] = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 1), ("/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc", 1),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", 1), ("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", 1),
    ("C:/Windows/Fonts/malgunbd.ttf", 0), ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
]
SERIF_SYSTEM: List[Spec] = [
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 1), ("/usr/share/fonts/truetype/noto/NotoSerifCJK-Bold.ttc", 1),
    ("/usr/share/fonts/noto-cjk/NotoSerifCJK-Bold.ttc", 1), ("C:/Windows/Fonts/HANBatangB.ttf", 0), ("C:/Windows/Fonts/batang.ttc", 0),
]
POP_FILES = ("GmarketSansBold.otf", "GmarketSansBold.woff", "Cafe24Ssurround.woff")
POP_URLS = (("GmarketSansBold.woff", "https://cdn.jsdelivr.net/gh/projectnoonnu/noonfonts_2001@1.1/GmarketSansBold.woff"),
            ("Cafe24Ssurround.woff", "https://cdn.jsdelivr.net/gh/projectnoonnu/noonfonts_2105_2@1.0/Cafe24Ssurround.woff"))
MIN_BYTES = 100_000


def _loads(spec: Optional[Spec]) -> bool:
    """실제로 열리고 한글 '한'이 그려지는 폰트인지 (두부·빈 글리프 방지)"""
    if not spec or not os.path.exists(spec[0]):
        return False
    try:
        f = ImageFont.truetype(spec[0], 48, index=spec[1])
        b = f.getbbox("한")
        return bool(b and (b[2] - b[0]) > 10)
    except Exception:
        return False


def first_usable(cands: List[Spec]) -> Optional[Spec]:
    return next((c for c in cands if _loads(c)), None)


def _download(url: str, target: str, timeout: int = 40) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if len(data) < MIN_BYTES:
            return False
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        logger.warning(f"폰트 다운로드 실패 ({url}): {type(e).__name__}")
        return False


def pop_font(font_dir: str) -> Optional[Spec]:
    """둥근 예능 팝체: fonts/에 있으면 그대로, 없으면 받아 옴. 안 되면 None (호출자가 고딕 볼드로 대체)"""
    for name in POP_FILES:
        p = os.path.abspath(os.path.join(font_dir, name))
        if os.path.exists(p) and os.path.getsize(p) > MIN_BYTES and _loads((p, 0)):
            return (p, 0)
    for name, url in POP_URLS:
        p = os.path.abspath(os.path.join(font_dir, name))
        if _download(url, p) and _loads((p, 0)):
            return (p, 0)
    return None


def resolve(key: str, comp) -> Spec:
    """프리셋 → (경로, ttc 인덱스). comp(AdCompositor)는 impact(font_path)·serif(serif_font_path) 기본 폰트를 제공. 절대 실패하지 않음(마지막은 PIL 기본 폰트 경로)"""
    key = key if key in PRESETS else DEFAULT_KEY
    base: Spec = (comp.font_path, 0)
    sans = base if _loads(base) else (first_usable(SANS_SYSTEM) or base)
    if key == "impact":
        return sans
    if key == "serif":
        p = comp.serif_font_path
        if p and p != comp.font_path and _loads((p, 0)):
            return (p, 0)
        return first_usable(SERIF_SYSTEM) or sans
    return pop_font(comp.font_dir) or first_usable(SANS_SYSTEM) or sans


def load(spec: Spec, size: int):
    try:
        return ImageFont.truetype(spec[0], size, index=spec[1])
    except Exception:
        return ImageFont.load_default()
