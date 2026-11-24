"""
core/render/thumbnail.py - 쇼츠 썸네일 3종 스타일 엔진 (1080×1920)

1. intense (🔥 강렬한 먹방형 — 기본값):
   - 상단 큰 노랑→주황→빨강 그라데이션 볼드 타이틀 + 두꺼운 검정 외곽선 + 입체 돌출 + 그림자 + -4도 기울기
   - 하단 흰색 알약 라벨 (시그니처 [메뉴명])
   - 고기집·치킨·호프·매운 음식 등 자극적이고 직관적인 쇼츠에 적합

2. magazine (✨ 감성 매거진형):
   - 음식/매장 중앙을 가리지 않는 하단 다크 그라데이션
   - 하단에 깔끔한 화이트 명조/고딕 타이틀 + 얇은 가로 구분선(divider) + 작은 상단/하단 보조 라벨(지역/상호)
   - 감성 카페·베이커리·브런치·와인바·디저트에 적합

3. navy_premium (💎 네이비 프리미엄 카드형):
   - 브랜드 메인 컬러인 딥 네이비(#0F2744) + 골드(#F5C451)·화이트 포인트
   - 불투명/반투명 네이비 카드 바 + 골드 포인트 소제목 + 선명한 흰색 메인 타이틀
   - 한정식·오마카세·파인다이닝·가족외식·프리미엄 매장에 적합
"""
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from core.render import fonts

logger = logging.getLogger("ad_factory.render.thumbnail")

W, H = 1080, 1920

# 썸네일 스타일 상수
STYLE_INTENSE = "intense"
STYLE_MAGAZINE = "magazine"
STYLE_NAVY_PREMIUM = "navy_premium"
THUMBNAIL_STYLES = (STYLE_INTENSE, STYLE_MAGAZINE, STYLE_NAVY_PREMIUM)

THUMBNAIL_STYLE_META: Dict[str, Dict[str, str]] = {
    STYLE_INTENSE: {
        "label": "🔥 강렬한 먹방형 (기본값)",
        "desc": "상단 큰 노랑-주황 그라데이션 볼드 타이틀 + 하단 시그니처 메뉴 라벨 (고기집·치킨·호프·매운 음식 등)",
    },
    STYLE_MAGAZINE: {
        "label": "✨ 감성 매거진형",
        "desc": "하단 다크 그라데이션 + 깔끔한 명조/고딕 타이틀 + 얇은 가로선 (감성 카페·베이커리·브런치·와인바)",
    },
    STYLE_NAVY_PREMIUM: {
        "label": "💎 네이비 프리미엄 카드형",
        "desc": "딥 네이비(#0F2744) 바 + 골드 포인트 + 화이트 타이틀 (한정식·오마카세·파인다이닝·가족외식)",
    },
}

FONT_BY_TONE = {"lively": "pop", "warm": "serif", "formal": "serif"}       # 유쾌: 둥근 팝체 / 따뜻·기록: 감성 명조
TITLE_MAX_LINES = 3
TEXT_WIDTH = 0.84                 # 글자 영역 폭 (화면 폭 대비)
TITLE_SIZE_MAX, TITLE_SIZE_MIN = 0.100, 0.060       # 글자 크기 (화면 폭 대비)
SUB_SIZE = 0.040
BLOCK_BOTTOM = 0.80               # 글자 덩어리의 아래 끝 (높이 대비): 쇼츠 하단 UI(채널명·제목) 위
SHADE_START, SHADE_ALPHA = 0.46, 205
BANNED = ("특가", "할인", "손해", "무료", "최저가", "이벤트", "?!")


def _gap(font) -> int:
    """어절 사이 간격. 둥근 팝체(Gmarket Sans)처럼 공백 글리프가 없는 폰트는 □로 나오므로 공백을 글자로 그리지 않고 직접 띄운다"""
    return max(6, int(getattr(font, "size", 60) * 0.30))


def _line_width(font, line: str) -> int:
    words = line.split(" ")
    return int(sum(font.getlength(w) for w in words) + _gap(font) * (len(words) - 1))


def _draw_line(draw: ImageDraw.ImageDraw, x: int, y: int, line: str, font, **kw) -> None:
    gap = _gap(font)
    for w in line.split(" "):
        draw.text((x, y), w, font=font, anchor="ls", **kw)
        x += int(font.getlength(w)) + gap


def _wrap(text: str, draw: ImageDraw.ImageDraw, font, max_w: int, max_lines: int = TITLE_MAX_LINES) -> List[str]:
    """어절 단위 줄바꿈: 줄 수가 가장 적고 줄 폭이 고른 조합 (어절은 자르지 않음)"""
    words = (text or "").split()
    if not words:
        return []

    def width(s: str) -> int:
        return _line_width(font, s)

    if width(" ".join(words)) <= max_w:
        return [" ".join(words)]
    best: Optional[List[str]] = None
    n = len(words)
    for lines_n in range(2, min(max_lines, n) + 1):
        from itertools import combinations
        for cuts in combinations(range(1, n), lines_n - 1):
            parts = [" ".join(words[a:b]) for a, b in zip((0,) + cuts, cuts + (n,))]
            ws = [width(p) for p in parts]
            if max(ws) <= max_w:
                score = max(ws) - min(ws)
                if best is None or score < best[0]:
                    best = (score, parts)
        if best is not None:
            return best[1]
    return [" ".join(words)]            # 한 어절이 너무 길면 자르지 않고 그대로 (글자 축소로 맞춤)


def _fit_title(text: str, comp: Any, key: str, max_size_ratio: float = TITLE_SIZE_MAX,
               min_size_ratio: float = TITLE_SIZE_MIN, max_w_ratio: float = TEXT_WIDTH,
               max_lines: int = TITLE_MAX_LINES, width: int = W, height: int = H):
    spec = fonts.resolve(key, comp)
    probe = ImageDraw.Draw(Image.new("RGBA", (width, height)))
    size = int(width * max_size_ratio)
    target_w = int(width * max_w_ratio)
    min_size = int(width * min_size_ratio)
    text = fonts.break_long_tokens(text, fonts.load(spec, min_size), target_w)         # 공백 없는 긴 이름이 가장 작은 크기에서도 넘으면 글자 수로 나눔 (좌우 잘림 방지)
    while True:
        font = fonts.load(spec, size)
        lines = _wrap(text, probe, font, target_w, max_lines=max_lines)
        widths = [_line_width(font, ln) for ln in lines] or [0]
        if (len(lines) <= max_lines and max(widths) <= target_w) or size <= min_size:
            return spec, font, lines, size
        size = int(size * 0.94)


def _prepare_base_frame(frame: np.ndarray, width: int = W, height: int = H,
                        color_enhance: float = 1.06, contrast_enhance: float = 1.0) -> Image.Image:
    """대표 프레임을 1080×1920 해상도에 맞춰 중앙 크롭 및 색감 보정"""
    bg = Image.fromarray(frame).convert("RGB")
    scale = max(width / bg.width, height / bg.height)
    bg = bg.resize((max(width, round(bg.width * scale)), max(height, round(bg.height * scale))), Image.LANCZOS)
    left, top = (bg.width - width) // 2, (bg.height - height) // 2
    cropped = bg.crop((left, top, left + width, top + height))
    if color_enhance != 1.0:
        cropped = ImageEnhance.Color(cropped).enhance(color_enhance)
    if contrast_enhance != 1.0:
        cropped = ImageEnhance.Contrast(cropped).enhance(contrast_enhance)
    return cropped


# ---------------------------------------------------------------- 스타일 1: intense (강렬한 먹방형)
def render_thumbnail_intense(comp: Any, frame: np.ndarray, main_text: str, out_path: str,
                             sub_text: str = "", accent: str = "", width: int = W, height: int = H, **kw) -> str:
    """🔥 강렬한 먹방형: 상단 볼드 그라데이션 타이틀 + -4도 기울기 + 하단 흰색 알약 라벨"""
    if comp is not None and hasattr(comp, "render_thumbnail_intense_raw"):
        return comp.render_thumbnail_intense_raw(frame, main_text, out_path, sub_text=sub_text, accent=accent,
                                                 width=width, height=height)
    # compositor 인스턴스가 없을 때의 독립 렌더러
    from core.compositor import AdCompositor
    temp_comp = AdCompositor(output_dir=os.path.dirname(os.path.abspath(out_path)))
    return temp_comp.render_thumbnail_intense_raw(frame, main_text, out_path, sub_text=sub_text, accent=accent,
                                                  width=width, height=height)


# ---------------------------------------------------------------- 스타일 2: magazine (감성 매거진형)
def render_thumbnail_magazine(comp: Any, frame: np.ndarray, main_text: str, out_path: str,
                              sub_text: str = "", accent: str = "", width: int = W, height: int = H,
                              tone: str = "warm", font_key: Optional[str] = None, **kw) -> str:
    """✨ 감성 매거진형: 음식/매장 중앙을 가리지 않는 하단 다크 그라데이션 + 깔끔한 화이트 명조/고딕 타이틀 + 얇은 가로선 + 서브 라벨"""
    title = fonts.clean_text(main_text)
    sub = fonts.clean_text(sub_text)
    if sub in ("가게", "매장", "상점", "None", "null", "undefined"):
        sub = ""

    bg = _prepare_base_frame(frame, width, height, color_enhance=1.08, contrast_enhance=1.04)
    img = bg.convert("RGBA")

    # 중앙을 가리지 않는 하단 다크 그라데이션 (46% 아래로만 어두워짐) + 상단 아주 옅은 비네트
    shade = np.zeros((height, width, 4), dtype=np.uint8)
    s0 = int(height * SHADE_START)
    shade[s0:, :, 3] = (np.linspace(0.0, 1.0, height - s0) ** 1.35 * SHADE_ALPHA)[:, None].astype(np.uint8)
    top_h = int(height * 0.16)
    shade[:top_h, :, 3] = (np.linspace(1.0, 0.0, top_h) ** 1.5 * 65)[:, None].astype(np.uint8)
    img.alpha_composite(Image.fromarray(shade))

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    key = font_key or ("pop" if tone == "lively" else "serif")
    spec, tfont, lines, tsize = _fit_title(title, comp, key, width=width, height=height) if title else (fonts.resolve(key, comp), None, [], int(width * TITLE_SIZE_MAX))
    sfont = fonts.load(spec, int(width * SUB_SIZE))
    line_h = int(tsize * 1.22)
    sub_h = int(width * SUB_SIZE * 1.2) if sub else 0
    gap = int(width * 0.035) if (lines and sub) else 0
    rule_h = int(height * 0.008) if (lines and sub) else 0
    block_h = line_h * len(lines) + gap + rule_h + gap + sub_h if lines else sub_h
    y = int(height * BLOCK_BOTTOM) - block_h

    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    stroke = max(2, int(tsize * (0.045 if key == "pop" else 0.012)))

    for ln in lines:
        x = (width - _line_width(tfont, ln)) // 2
        base = y + int(tsize * 0.95)
        _draw_line(sd, x + 3, base + 5, ln, tfont, fill=(0, 0, 0, 170), stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
        _draw_line(d, x, base, ln, tfont, fill=(255, 255, 255, 255), stroke_width=stroke, stroke_fill=(20, 14, 10, 235 if key == "pop" else 150))
        y += line_h

    if lines and sub:
        y += gap
        rule_w = int(width * 0.14)
        d.rounded_rectangle((width // 2 - rule_w // 2, y, width // 2 + rule_w // 2, y + max(2, rule_h // 2)),
                            radius=1, fill=(255, 255, 255, 215))
        y += rule_h + gap

    if sub:
        x = (width - _line_width(sfont, sub)) // 2
        base = y + int(width * SUB_SIZE * 0.95)
        _draw_line(sd, x + 2, base + 3, sub, sfont, fill=(0, 0, 0, 160))
        _draw_line(d, x, base, sub, sfont, fill=(255, 246, 230, 245))

    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))
    img.alpha_composite(layer)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    logger.info(f"감성 매거진 썸네일 저장: {out_path} (메인 '{title}' / 서브 '{sub}')")
    return out_path


# ---------------------------------------------------------------- 스타일 3: navy_premium (네이비 프리미엄 카드형)
def render_thumbnail_navy_premium(comp: Any, frame: np.ndarray, main_text: str, out_path: str,
                                  sub_text: str = "", accent: str = "", width: int = W, height: int = H, **kw) -> str:
    """💎 네이비 프리미엄 카드형: 딥 네이비(#0F2744) 바 + 골드(#F5C451) 포인트 + 선명한 화이트 메인 타이틀"""
    title = fonts.clean_text(main_text)
    sub = fonts.clean_text(sub_text)
    if sub in ("가게", "매장", "상점", "None", "null", "undefined"):
        sub = ""

    bg = _prepare_base_frame(frame, width, height, color_enhance=1.12, contrast_enhance=1.06)
    img = bg.convert("RGBA")

    # 부드러운 상하단 비네트
    shade = np.zeros((height, width, 4), dtype=np.uint8)
    top_h = int(height * 0.28)
    shade[:top_h, :, 3] = (np.linspace(1.0, 0.0, top_h) ** 1.3 * 110)[:, None].astype(np.uint8)
    bot_s = int(height * 0.48)
    shade[bot_s:, :, 3] = (np.linspace(0.0, 1.0, height - bot_s) ** 1.3 * 160)[:, None].astype(np.uint8)
    img.alpha_composite(Image.fromarray(shade))

    # 프리미엄 네이비 카드 바 영역 계산
    card_w = int(width * 0.88)
    card_x = (width - card_w) // 2
    inner_text_w = card_w - int(width * 0.10)

    # 폰트 로드: 임팩트 고딕 (Pretendard-Black / Noto Sans CJK Bold)
    spec = fonts.resolve("impact", comp)
    probe = ImageDraw.Draw(Image.new("RGBA", (width, height)))

    # 타이틀 줄바꿈 및 크기 피팅 (1~2줄, 최대 3줄)
    tsize = int(width * 0.076)
    min_tsize = int(width * 0.052)
    title = fonts.break_long_tokens(title, fonts.load(spec, min_tsize), inner_text_w)       # 공백 없는 긴 이름은 글자 수로 나눠 카드 밖으로 안 나가게
    while True:
        tfont = fonts.load(spec, tsize)
        lines = _wrap(title, probe, tfont, inner_text_w, max_lines=2)
        widths = [_line_width(tfont, ln) for ln in lines] or [0]
        if (len(lines) <= 2 and max(widths) <= inner_text_w) or tsize <= min_tsize:
            break
        tsize = int(tsize * 0.94)

    # 소제목: 양옆 장식(◆)은 글꼴에 ✦ 글리프가 없어 □로 깨졌으므로 글자가 아니라 도형으로 그린다. 이름이 없으면 중립 문구(브랜드명·오타 문구 금지)
    sub_label = sub or "SIGNATURE"
    sub_size = int(width * 0.036)
    sub_font = fonts.load(spec, sub_size)
    deco_w = int(sub_size * 0.55)
    deco_gap = int(sub_size * 0.45)
    while _line_width(sub_font, sub_label) + 2 * (deco_w + deco_gap) > inner_text_w and sub_size > int(width * 0.024):      # 긴 소제목은 카드 폭에 맞춰 줄임
        sub_size = int(sub_size * 0.94)
        sub_font = fonts.load(spec, sub_size)
        deco_w, deco_gap = int(sub_size * 0.55), int(sub_size * 0.45)
    while _line_width(sub_font, sub_label) + 2 * (deco_w + deco_gap) > inner_text_w and len(sub_label) > 4:
        sub_label = sub_label[:-2].rstrip() + "…"

    pad_y = int(width * 0.046)
    sub_h = int(sub_size * 1.25)
    gap1 = int(width * 0.016)
    rule_h = 2
    gap2 = int(width * 0.020)
    title_lh = int(tsize * 1.22)
    title_total_h = len(lines) * title_lh if lines else int(tsize * 1.1)

    card_h = pad_y + sub_h + gap1 + rule_h + gap2 + title_total_h + pad_y
    card_cy = int(height * 0.67)      # 화면 67% 지점 (중앙 요리 사진 노출 + 쇼츠 하단 UI 회피)
    card_y = card_cy - card_h // 2

    # 1) 카드 드롭 섀도우
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((card_x + 4, card_y + 12, card_x + card_w + 4, card_y + card_h + 12),
                         radius=28, fill=(0, 0, 0, 185))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(16)))

    # 2) 네이비 카드 바 배경 & 골드 테두리 (#0F2744 / #F5C451)
    card_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card_layer)
    NAVY_FILL = (15, 39, 68, 240)       # 딥 네이비 94% 불투명도
    GOLD_BORDER = (245, 196, 81, 155)    # 샴페인 골드 테두리
    cd.rounded_rectangle((card_x, card_y, card_x + card_w, card_y + card_h),
                         radius=26, fill=NAVY_FILL, outline=GOLD_BORDER, width=3)

    # 3) 상단 골드 소제목
    cur_y = card_y + pad_y
    label_w = _line_width(sub_font, sub_label)
    total_w = label_w + 2 * (deco_w + deco_gap)
    sub_x = (width - total_w) // 2 + deco_w + deco_gap
    _draw_line(cd, sub_x, cur_y + int(sub_size * 0.92), sub_label, sub_font,
               fill=(245, 196, 81, 255))
    mid_y = cur_y + int(sub_size * 0.52)
    for dx in ((width - total_w) // 2 + deco_w // 2, (width + total_w) // 2 - deco_w // 2):        # 골드 다이아몬드(◆) 2개
        r = max(3, deco_w // 2)
        cd.polygon([(dx, mid_y - r), (dx + r, mid_y), (dx, mid_y + r), (dx - r, mid_y)], fill=(245, 196, 81, 255))
    cur_y += sub_h + gap1

    # 4) 골드 미세 구분선
    rule_w = int(card_w * 0.36)
    cd.line((width // 2 - rule_w // 2, cur_y, width // 2 + rule_w // 2, cur_y),
            fill=(245, 196, 81, 110), width=rule_h)
    cur_y += rule_h + gap2

    # 5) 선명한 화이트 메인 타이틀
    for ln in lines:
        tx = (width - _line_width(tfont, ln)) // 2
        base = cur_y + int(tsize * 0.95)
        # 미세 글자 그림자
        _draw_line(cd, tx + 1, base + 2, ln, tfont, fill=(0, 0, 0, 130))
        # 순백색 텍스트
        _draw_line(cd, tx, base, ln, tfont, fill=(255, 255, 255, 255))
        cur_y += title_lh

    img.alpha_composite(card_layer)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    logger.info(f"네이비 프리미엄 썸네일 저장: {out_path} (메인 '{title}' / 골드라벨 '{sub_label}')")
    return out_path


# ---------------------------------------------------------------- 통합 디스패처
def render_styled_thumbnail(comp: Any, frame: np.ndarray, main_text: str, out_path: str,
                            sub_text: str = "", accent: str = "", style: str = STYLE_INTENSE,
                            width: int = W, height: int = H, **kwargs) -> str:
    """
    3가지 스타일 중 선택하여 1080×1920 썸네일 생성
    - intense: 🔥 강렬한 먹방형 (기본값)
    - magazine: ✨ 감성 매거진형
    - navy_premium: 💎 네이비 프리미엄 카드형
    """
    st = (style or STYLE_INTENSE).strip().lower()
    if st == STYLE_MAGAZINE:
        return render_thumbnail_magazine(comp, frame, main_text, out_path, sub_text=sub_text,
                                        accent=accent, width=width, height=height, **kwargs)
    elif st == STYLE_NAVY_PREMIUM:
        return render_thumbnail_navy_premium(comp, frame, main_text, out_path, sub_text=sub_text,
                                             accent=accent, width=width, height=height, **kwargs)
    else:
        return render_thumbnail_intense(comp, frame, main_text, out_path, sub_text=sub_text,
                                        accent=accent, width=width, height=height, **kwargs)


# ---------------------------------------------------------------- 기존 호환 (추억 모드)
def render_memory_thumbnail(frame: np.ndarray, title: str, tone: str, out_path: str, comp: Any, sub_text: str = "") -> str:
    """기존 모임·행사·추억 쇼츠 전용 썸네일 함수와의 100% 하위 호환성 유지"""
    clean_title = " ".join(str(title or "").split())
    clean_sub = " ".join(str(sub_text or "").split())
    for w in BANNED:
        clean_title, clean_sub = clean_title.replace(w, ""), clean_sub.replace(w, "")
    return render_thumbnail_magazine(comp, frame, clean_title, out_path, sub_text=clean_sub, tone=tone)
