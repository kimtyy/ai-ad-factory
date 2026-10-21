"""
core/render/brand.py - B2B(펜션/매장) 브랜드 오버레이

트랙 A(제휴 쇼츠)는 숙소명을 숨기지만, 트랙 B(사장님 SaaS)는 반대로 상호를 노출하고
네이버 플레이스 검색을 유도해야 합니다. 이 모듈은 도메인과 무관한 렌더링 레이어로,
compositor에 BrandOverlay 객체를 넘기면 적용되고 넘기지 않으면 아무 영향이 없습니다.

- 워터마크: 전 씬 상단 모서리에 로고 + 상호명 라벨 (투명도 조절)
- 엔딩 카드: 마지막 장면을 흐리게 깐 배경 위에 상호명 + 검색창 모양 그래픽 + 검색 유도 문구
  (플랫폼 로고/상표 이미지는 사용하지 않고 텍스트로만 안내)
"""
import os
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance, ImageOps

RGB = Tuple[int, int, int]


@dataclass
class EndCard:
    search_keyword: str                          # 검색창에 표시할 검색어 (보통 상호명)
    headline: str = ""                           # 비우면 BrandOverlay.business_name 사용
    cta_text: str = "네이버 플레이스에서 검색해 보세요"
    sub_text: str = ""                           # 예: '예약 문의 010-0000-0000', '경기 가평군'
    duration_sec: float = 2.5
    show_search_bar: bool = True                 # False면 검색창 그래픽 없이 문구만 (숙소 제휴: 포털 검색 대신 고정 댓글 링크로 유도)


@dataclass
class BrandOverlay:
    business_name: str
    logo_path: Optional[str] = None
    position: str = "top_right"                  # 'top_left' | 'top_right'
    opacity: float = 0.92
    show_label: bool = True
    accent_color: RGB = (3, 199, 90)             # 검색창 테두리/포인트 색
    end_card: Optional[EndCard] = None

    # ---------------------------------------------------------
    # 공통 유틸
    # ---------------------------------------------------------
    @staticmethod
    def _font(font_path: Optional[str], size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        except Exception:
            return ImageFont.load_default()

    def _load_logo(self, target_h: int) -> Optional[Image.Image]:
        if not self.logo_path or not os.path.exists(self.logo_path):
            return None
        try:
            with Image.open(self.logo_path) as source:
                logo = ImageOps.exif_transpose(source).convert("RGBA")
        except Exception:
            return None
        scale = target_h / max(logo.height, 1)
        return logo.resize((max(1, int(logo.width * scale)), target_h), Image.Resampling.LANCZOS)

    # ---------------------------------------------------------
    # 1. 워터마크 (씬마다 자막 오버레이 위에 합성)
    # ---------------------------------------------------------
    def render_watermark(self, width: int, height: int, font_path: Optional[str] = None) -> Image.Image:
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        if not (self.show_label and self.business_name) and not self.logo_path:
            return layer
        draw = ImageDraw.Draw(layer)

        margin = int(width * 0.04)
        pad_x, pad_y = int(width * 0.022), int(width * 0.014)
        logo_h = int(width * 0.06)
        font = self._font(font_path, int(width * 0.034))

        logo = self._load_logo(logo_h)
        label = self.business_name.strip() if (self.show_label and self.business_name) else ""
        tb = draw.textbbox((0, 0), label, font=font) if label else (0, 0, 0, 0)
        text_w, text_h = tb[2] - tb[0], tb[3] - tb[1]
        gap = int(width * 0.012) if (logo and label) else 0
        content_w = (logo.width if logo else 0) + gap + text_w
        content_h = max(logo_h if logo else 0, text_h)
        box_w, box_h = content_w + pad_x * 2, content_h + pad_y * 2

        x0 = width - margin - box_w if self.position == "top_right" else margin
        y0 = int(height * 0.045)
        draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=box_h // 2, fill=(0, 0, 0, 120))

        cx = x0 + pad_x
        if logo:
            layer.alpha_composite(logo, (cx, y0 + (box_h - logo.height) // 2))
            cx += logo.width + gap
        if label:
            draw.text((cx, y0 + (box_h - text_h) // 2 - tb[1]), label, font=font, fill=(255, 255, 255, 255),
                      stroke_width=2, stroke_fill=(0, 0, 0, 200))

        if self.opacity < 1.0:
            alpha = layer.split()[-1].point(lambda a: int(a * max(0.0, self.opacity)))
            layer.putalpha(alpha)
        return layer

    # ---------------------------------------------------------
    # 2. 엔딩 카드 (영상 마지막에 별도 클립으로 추가)
    # ---------------------------------------------------------
    def render_end_card(self, background: np.ndarray, font_path: Optional[str] = None) -> np.ndarray:
        """background: 마지막 씬의 마지막 프레임 (H, W, 3). 반환: 엔딩 카드 RGB 프레임."""
        card = self.end_card or EndCard(search_keyword=self.business_name)
        bg = Image.fromarray(background.astype(np.uint8)).convert("RGB")
        width, height = bg.size
        bg = bg.filter(ImageFilter.GaussianBlur(radius=max(8, width // 40)))
        bg = ImageEnhance.Brightness(bg).enhance(0.45)
        canvas = bg.convert("RGBA")
        draw = ImageDraw.Draw(canvas)

        def center_text(text: str, y: int, font: ImageFont.ImageFont, fill, stroke: int = 0) -> int:
            if not text:
                return y
            tb = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
            draw.text(((width - (tb[2] - tb[0])) // 2 - tb[0], y - tb[1]), text, font=font, fill=fill,
                      stroke_width=stroke, stroke_fill=(0, 0, 0))
            return y + (tb[3] - tb[1])

        y = int(height * 0.30)
        logo = self._load_logo(int(width * 0.16))
        if logo:
            canvas.alpha_composite(logo, ((width - logo.width) // 2, y))
            y += logo.height + int(height * 0.025)

        headline = (card.headline or self.business_name).strip()
        y = center_text(headline, y, self._font(font_path, int(width * 0.085)), (255, 255, 255), stroke=4)
        y += int(height * 0.045)

        # 검색창 모양 그래픽
        bar_w, bar_h = int(width * 0.80), int(width * 0.14)
        bx, by = (width - bar_w) // 2, y
        if not card.show_search_bar:
            for line in (card.cta_text or "").split(chr(10)):
                y = center_text(line, y, self._font(font_path, int(width * 0.060)), (255, 235, 0), stroke=3) + int(height * 0.012)
            y += int(height * 0.02)
            center_text(card.sub_text, y, self._font(font_path, int(width * 0.036)), (235, 235, 235), stroke=2)
            return np.array(canvas.convert("RGB"))
        draw.rounded_rectangle([bx, by, bx + bar_w, by + bar_h], radius=bar_h // 2,
                               fill=(255, 255, 255, 255), outline=self.accent_color + (255,), width=max(4, width // 160))
        kw_font = self._font(font_path, int(width * 0.052))
        kw = card.search_keyword.strip() or self.business_name
        tb = draw.textbbox((0, 0), kw, font=kw_font)
        draw.text((bx + int(bar_h * 0.5), by + (bar_h - (tb[3] - tb[1])) // 2 - tb[1]), kw, font=kw_font, fill=(20, 20, 20))
        # 돋보기 아이콘
        r = int(bar_h * 0.20)
        icx, icy = bx + bar_w - int(bar_h * 0.62), by + bar_h // 2 - int(r * 0.25)
        lw = max(4, width // 180)
        draw.ellipse([icx - r, icy - r, icx + r, icy + r], outline=self.accent_color + (255,), width=lw)
        draw.line([icx + int(r * 0.7), icy + int(r * 0.7), icx + int(r * 1.6), icy + int(r * 1.6)],
                  fill=self.accent_color + (255,), width=lw + 2)
        y = by + bar_h + int(height * 0.04)

        y = center_text(card.cta_text, y, self._font(font_path, int(width * 0.052)), (255, 235, 0), stroke=3)
        y += int(height * 0.02)
        center_text(card.sub_text, y, self._font(font_path, int(width * 0.036)), (235, 235, 235), stroke=2)

        return np.array(canvas.convert("RGB"))
