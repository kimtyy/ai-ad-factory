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
import re
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


_ODD_SPACES = re.compile(r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000\t\r\n\v\f]+")
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
PROBE_CHARS = "한가0!?.,"                  # 글리프가 실제로 그려져야 하는 글자 (한글·숫자·문장부호)
SPACE_ADVANCE = 0.26                       # 보강하는 공백 글리프의 폭 (글자 크기 대비)


def clean_text(text: str) -> str:
    """자막·썸네일 문구의 공백 정규화: 폭 없는 문자(U+200B 등)는 지우고, NBSP·전각 공백·탭·줄바꿈은 일반 공백 한 칸으로 (폰트가 못 그리는 공백이 네모로 찍히는 것 방지)"""
    return " ".join(_ODD_SPACES.sub(" ", _ZERO_WIDTH.sub("", str(text or ""))).split())


def _ink(font, ch: str) -> int:
    from PIL import Image, ImageDraw
    im = Image.new("L", (240, 240), 0)
    ImageDraw.Draw(im).text((20, 20), ch, font=font, fill=255)
    return int(sum(1 for v in im.getdata() if v))


def space_is_blank(spec: Optional[Spec]) -> bool:
    """공백(U+0020)이 '빈 여백'으로 그려지는지: 글리프가 없으면 .notdef 네모(☒)가 찍혀 잉크가 생긴다. 폭도 0보다 커야 한다"""
    try:
        f = ImageFont.truetype(spec[0], 96, index=spec[1])
        return _ink(f, " ") == 0 and f.getlength(" ") > 0
    except Exception:
        return False


def glyphs_ok(spec: Optional[Spec]) -> bool:
    """한글·숫자·문장부호가 그려지고, 공백이 빈 여백인 폰트인지 (두부·빈 글리프 방지)"""
    try:
        f = ImageFont.truetype(spec[0], 96, index=spec[1])
        return all(_ink(f, ch) > 0 for ch in PROBE_CHARS) and space_is_blank(spec)
    except Exception:
        return False


def add_space_glyph(src: str, dst: str) -> bool:
    """공백(U+0020)·NBSP(U+00A0) 글리프가 없는 폰트(둥근 팝체 Gmarket Sans 등)에 빈 공백 글리프를 넣어 dst(.otf/.ttf)로 저장. 실패하면 False"""
    try:
        from fontTools.ttLib import TTFont
        t = TTFont(src)
        if "CFF " in t:
            from fontTools.misc.psCharStrings import T2CharString
            cff = t["CFF "].cff
            top = cff.topDictIndex[0]
            pd = top.Private
            w = int(t["head"].unitsPerEm * SPACE_ADVANCE)
            cs = T2CharString(program=[w - pd.nominalWidthX, "endchar"], private=pd, globalSubrs=cff.GlobalSubrs)
            top.CharStrings.charStringsIndex.append(cs)
            top.CharStrings.charStrings["space"] = len(top.CharStrings.charStringsIndex) - 1
            t.setGlyphOrder(t.getGlyphOrder() + ["space"])
            if isinstance(top.charset, list):
                top.charset = list(top.charset) + ["space"]
        else:
            from fontTools.pens.ttGlyphPen import TTGlyphPen
            w = int(t["head"].unitsPerEm * SPACE_ADVANCE)
            t["glyf"]["space"] = TTGlyphPen(None).glyph()
            t.setGlyphOrder(t.getGlyphOrder() + ["space"])
        t["hmtx"].metrics["space"] = (w, 0)
        for st in t["cmap"].tables:
            if st.isUnicode():
                st.cmap[0x20] = "space"
                st.cmap[0xA0] = "space"
        t.flavor = None
        t.save(dst)
        return True
    except Exception as e:
        logger.warning(f"공백 글리프 보강 실패 ({os.path.basename(src)}): {type(e).__name__}: {e}")
        return False


def with_space(path: str) -> Optional[str]:
    """공백 글리프가 없는 폰트는 보강본(<이름>_sp.otf)을 만들어 그 경로를, 이미 정상이면 원래 경로를, 보강도 안 되면 None(불합격)"""
    if space_is_blank((path, 0)):
        return path
    fixed = os.path.splitext(path)[0] + "_sp.otf"
    if (os.path.exists(fixed) and os.path.getmtime(fixed) >= os.path.getmtime(path) and space_is_blank((fixed, 0)))             or (add_space_glyph(path, fixed) and space_is_blank((fixed, 0))):
        return fixed
    return None


def _loads(spec: Optional[Spec]) -> bool:
    """실제로 열리고 한글 '한'이 그려지는 폰트인지 (두부·빈 글리프 방지). 공백이 네모로 찍히는 폰트는 불합격"""
    if not spec or not os.path.exists(spec[0]):
        return False
    try:
        f = ImageFont.truetype(spec[0], 48, index=spec[1])
        b = f.getbbox("한")
        return bool(b and (b[2] - b[0]) > 10) and glyphs_ok(spec)
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
    def usable(p: str) -> Optional[Spec]:
        fixed = with_space(p)               # 공백 글리프가 없으면 보강, 보강도 안 되면 불합격(→ 고딕 볼드로 대체)
        return (fixed, 0) if fixed and _loads((fixed, 0)) else None
    for name in POP_FILES:
        p = os.path.abspath(os.path.join(font_dir, name))
        if os.path.exists(p) and os.path.getsize(p) > MIN_BYTES and usable(p):
            return usable(p)
    for name, url in POP_URLS:
        p = os.path.abspath(os.path.join(font_dir, name))
        if _download(url, p) and usable(p):
            return usable(p)
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


def break_long_tokens(text: str, font, max_w: float, pad: float = 0.0) -> str:
    """공백 없이 이어진 긴 한 덩어리('가평현리설맥눈꽃생맥주전문점본점')가 가장 작은 글자 크기에서도 max_w를 넘으면 글자 수 기준으로 균등하게 나눠
    사이에 공백을 넣는다 (그대로 두면 좌우가 잘림). 어절이 여러 개인 일반 이름에는 영향이 없다. pad: 외곽선 등으로 늘어나는 폭"""
    import math
    out = []
    for tok in (text or "").split(" "):
        try:
            w = float(font.getlength(tok)) + pad
        except Exception:
            w = 0.0
        if not tok or w <= max_w:
            out.append(tok)
            continue
        n = max(2, math.ceil(w / max(1.0, max_w)))
        per = math.ceil(len(tok) / n)
        out.append(" ".join(tok[i:i + per] for i in range(0, len(tok), per)))
    return " ".join(out)


def load(spec: Spec, size: int):
    try:
        return ImageFont.truetype(spec[0], size, index=spec[1])
    except Exception:
        return ImageFont.load_default()
