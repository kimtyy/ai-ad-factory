"""
core/compositor.py - Step 3: MoviePy 기반 비디오-오디오 싱크 매칭, 고가독성 자막 오버레이 & 최종 렌더링
"""
import os
import re
import logging
from typing import List, Dict, Any, Optional
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from moviepy.editor import (
    VideoFileClip,
    AudioFileClip,
    ImageClip,
    CompositeVideoClip,
    concatenate_videoclips
)

import urllib.request

from core.render.brand import BrandOverlay
from core.render.commerce_layout import COMMERCE_CAPTION_CENTER_Y

logger = logging.getLogger("ad_factory.compositor")

# 하단 단일 자막 블록 스타일을 쓰는 모드 (실사 공간 영상: 수영장·객실 등 사진 핵심을 가리지 않음)
#   씬 1은 후킹 헤드라인 단독(0~hook_sec) → 서브 자막 순서로 시간차 노출, 위치는 화면 정중앙 한곳 (배경 박스 없음)
#   쇼핑(commerce)도 실물 상품 사진만 쓰게 되어 같은 레이아웃 사용 (AI 배경 위 누끼 합성 방식은 제거됨)
CENTER_CAPTION_MODES = ("travel", "owner", "commerce")
XFADE_SEC = 0.35            # 씬 전환 디졸브 길이 (초)
INTRO_SEC = 0.8             # 영상 첫 화면 커버(썸네일) 길이: PC 스튜디오 쇼츠 썸네일은 영상 프레임 중에서 고르므로
                            # 0~0.8초를 '시그니처 비주얼 + 헤드카피' 커버로 고정 (첫 프레임 = 커버, IDR 키프레임)
THUMB_FONT_RATIO = 0.26     # 썸네일 메인 카피 최대 글자 크기 (폭 대비, 기존 0.115의 약 2.3배 - 짧은 카피면 2줄 안에서 이 크기)
THUMB_TEXT_WIDTH = 0.90
THUMB_TEXT_TOP = 0.085      # 메인 카피 첫 줄 위치 (높이 대비): 쇼츠 상단 UI 아래
THUMB_TILT_DEG = 4          # 메인 카피 기울기 (반시계 4도 = 오른쪽이 올라가는 -4도 연출)
THUMB_GRADIENT = ((255, 238, 70), (255, 150, 10), (232, 38, 28))   # 노랑 → 주황 → 빨강
THUMB_SUB_FONT_RATIO = 0.085
THUMB_SUB_CENTER_Y = 0.60   # 서브 카피 박스 세로 위치 (중간~하단, 쇼츠 하단 UI 위)
THUMB_VIGNETTE_TOP = 215
THUMB_VIGNETTE_BOTTOM = 190
# 자막 블록 세로 중심 (높이 비율). 쇼츠 세이프 에어리어: 하단 약 35%는 채널명·제목·구독 버튼·사운드명,
# 우측은 좋아요·댓글 버튼이 덮음 → 화면 정중앙(48~52%)에 둬야 모바일에서 가려지지 않음 (하단 68%는 UI에 가려졌음)
CAPTION_CENTER_Y = 0.50
CAPTION_MAX_WIDTH = 0.72         # 줄 폭 상한 (가운데 14~86%): 우측 좋아요·댓글 버튼 열(약 84~100%)과 겹치지 않게 줄바꿈
HOOK_HEADLINE_COLOR = (255, 214, 10)
HOOK_FONT_RATIO = 0.092          # 씬 1 후킹 단독 노출이라 서브 자막보다 크게
CAPTION_FONT_RATIO = 0.066
CAPTION_STROKE_RATIO = 0.0065    # 1080px 기준 외곽선 약 7px
# 제휴 광고 표시(쿠팡 파트너스 등): 화면 위쪽 가장자리 안쪽(쇼츠 상단 아이콘 줄 아래)에 작고 옅게
DISCLOSURE_Y = 0.085
DISCLOSURE_FONT_RATIO = 0.027
HOOK_SOLO_SEC = 1.5              # hook_sec 정보가 없을 때 후킹 단독 노출 시간 (asset_generator.HOOK_SOLO_SEC와 동일)


class AdCompositor:
    def __init__(self, output_dir: Optional[str] = None, font_dir: str = "fonts"):
        if not output_dir:
            raise ValueError("output_dir가 필요합니다. JobContext.final_dir을 전달하세요.")
        self.output_dir = output_dir
        self.font_dir = font_dir
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.font_dir, exist_ok=True)

        # 숏폼 전용 극강의 상업용 무료 볼드 폰트 자동 확보
        self.font_path = self._ensure_bold_font()

    def _ensure_bold_font(self) -> str:
        """
        가변 폰트가 아닌 순수 고정 볼드(Pretendard-Black, Weight 900) 폰트 자동 다운로드 및 절대 경로 확보.
        실패 시 Windows 시스템 볼드 폰트로 안전 fallback.
        """
        target_font = os.path.abspath(os.path.join(self.font_dir, "Pretendard-Black.ttf"))
        if os.path.exists(target_font) and os.path.getsize(target_font) > 1000000:
            logger.info(f"숏폼 전용 고정 Black 폰트 로드: {target_font}")
            return target_font

        # Pretendard-Black (Static TrueType) 다운로드 시도
        font_urls = [
            "https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/public/static/alternative/Pretendard-Black.ttf",
            "https://raw.githubusercontent.com/orioncactus/pretendard/main/packages/pretendard/dist/public/static/alternative/Pretendard-Black.ttf"
        ]
        for url in font_urls:
            try:
                logger.info(f"Pretendard-Black 고정 볼드 폰트 다운로드 중... ({url})")
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as response, open(target_font, "wb") as f:
                    f.write(response.read())
                logger.info(f"Pretendard-Black 폰트 다운로드 완료 ({os.path.getsize(target_font)} bytes): {target_font}")
                return target_font
            except Exception as e:
                logger.warning(f"폰트 다운로드 시도 실패 ({url}): {e}")

        # 시스템 볼드 폰트 탐색
        fallback_candidates = [
            "C:/Windows/Fonts/malgunbd.ttf",
            "C:/Windows/Fonts/malgun.ttf",
            "C:/Windows/Fonts/gulim.ttc"
        ]
        for path in fallback_candidates:
            if os.path.exists(path):
                return os.path.abspath(path)

        return target_font

    def _wrap_text(self, text: str, draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, max_width: int) -> List[str]:
        """
        [의미 단위 어절 줄바꿈 (word-break: keep-all)]
        - 어절(띄어쓰기 단위) 안에서는 절대 자르지 않음, 한 줄에 들어가면 1줄 유지
        - 가장 적은 줄 수로 나누는 모든 경우 중 '줄 폭 균형 + 의미 덩어리 점수(break_penalty)'가 가장 좋은 것을 선택
          (가운데 띄어쓰기에서 기계적으로 자르면 '수영장 안 / 가보면', '바다 / 뷰가'처럼 의미가 끊김)
        """
        if not text:
            return []
        clean = re.sub(r'\s+', ' ', text).strip()
        if not clean:
            return []

        def width(line: str) -> int:
            bb = draw.textbbox((0, 0), line, font=font)
            return bb[2] - bb[0]

        if width(clean) <= max_width:
            return [clean]
        words = clean.split(" ")
        if len(words) == 1:
            return [clean]   # 띄어쓰기 없는 한 덩어리는 자르지 않음 (keep-all)

        from itertools import combinations
        word_w = {}

        def best_for(n_lines):
            best = None
            for cuts in combinations(range(1, len(words)), n_lines - 1):
                bounds = (0,) + cuts + (len(words),)
                lines = [" ".join(words[a:b]) for a, b in zip(bounds, bounds[1:])]
                ws = [word_w.setdefault(ln, width(ln)) for ln in lines]
                if max(ws) > max_width:
                    continue
                score = (max(ws) - min(ws)) / max_width + sum(break_penalty(words[c - 1], words[c]) for c in cuts)
                if best is None or score < best[0]:
                    best = (score, lines)
            return best

        for n_min in range(2, len(words) + 1):
            first = best_for(n_min)
            if first:
                # 한 줄 더 쓰면 의미가 확실히 덜 끊길 때만 늘림 (예: '바다 보고 / 있으면' → '누워서 / 바다 보고 있으면 / …')
                more = best_for(n_min + 1) if n_min + 1 <= min(3, len(words)) else None
                if more and more[0] + EXTRA_LINE_COST < first[0]:
                    return more[1]
                return first[1]
        return words


    def _caption_block(self, caption_text: Optional[str], hook_title: Optional[str], width: int, height: int,
                       center_y: float = CAPTION_CENTER_Y) -> np.ndarray:
        """
        travel/owner 공통 하단 자막 (배경 박스 없음):
        - 글자 = Pretendard-Black + 선명한 검은 외곽선 + 짙은 드롭섀도우(블러) → 배경 영상은 시원하게 보이고 밝은 하늘·바다 위에서도 읽힘
        - 후킹 헤드라인(노랑, 크게)과 서브 자막(흰색)은 보통 따로 호출됨 (씬 1은 시간차로 하나씩 노출).
          둘 다 주어지면 위아래로 쌓음 (하위 호환)
        - 블록 세로 중심 = 높이의 CAPTION_CENTER_Y (화면 정중앙, 쇼츠 하단·우측 UI 영역 회피)
        """
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        def font(size):
            try:
                return ImageFont.truetype(self.font_path, size)
            except Exception:
                return ImageFont.load_default()

        groups = []   # (줄 목록, 폰트, 글자색, 줄 높이)
        if hook_title and hook_title.strip():
            hf = font(int(width * HOOK_FONT_RATIO))
            lines = self._wrap_text(hook_title.strip(), draw, hf, max_width=int(width * CAPTION_MAX_WIDTH))
            if lines:
                groups.append((lines, hf, HOOK_HEADLINE_COLOR + (255,), int(hf.size * 1.2)))
        if caption_text and caption_text.strip():
            cf = font(int(width * CAPTION_FONT_RATIO))
            lines = self._wrap_text(caption_text.strip(), draw, cf, max_width=int(width * CAPTION_MAX_WIDTH))
            if lines:
                groups.append((lines, cf, (255, 255, 255, 255), int(cf.size * 1.28)))
        if not groups:
            return np.array(img)

        gap = int(width * 0.025) if len(groups) > 1 else 0
        text_h = sum(len(ls) * lh for ls, _, _, lh in groups) + gap
        y = int(height * center_y) - text_h // 2
        placed = []   # (x, y, 줄, 폰트, 색)
        for gi, (lines, f, color, lh) in enumerate(groups):
            if gi:
                y += gap
            for ln in lines:
                bb = draw.textbbox((0, 0), ln, font=f)
                x = (width - (bb[2] - bb[0])) // 2 - bb[0]
                placed.append((x, y + (lh - (bb[3] - bb[1])) // 2 - bb[1], ln, f, color))
                y += lh

        stroke = max(3, int(width * CAPTION_STROKE_RATIO))
        # 짙은 드롭섀도우: 아래로 살짝 내린 검은 글자를 블러 → 두 번 겹쳐 농도 확보
        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        off = max(3, int(width * 0.006))
        for x, ty, ln, f, _ in placed:
            sd.text((x + off // 2, ty + off), ln, font=f, fill=(0, 0, 0, 255), stroke_width=stroke + 2, stroke_fill=(0, 0, 0, 255))
        blurred = shadow.filter(ImageFilter.GaussianBlur(max(4, int(width * 0.009))))
        img.alpha_composite(blurred)
        img.alpha_composite(blurred)
        draw = ImageDraw.Draw(img)
        for x, ty, ln, f, color in placed:
            draw.text((x, ty), ln, font=f, fill=color, stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        return np.array(img)

    def render_thumbnail(self, frame: np.ndarray, main_text: str, out_path: str, sub_text: str = "", accent: str = "",
                         width: int = 1080, height: int = 1920) -> str:
        """
        쇼츠 썸네일 (1080x1920 PNG) - 영상 첫 0.3초·대시보드 미리보기·다운로드 공용
        - 배경: 씬 1 원본 컷(자막 없음) 꽉 채움 + 채도·대비 살짝 올림 + 상단·하단 비네트(어두운 그라데이션)
        - 메인 카피: 폭의 최대 26%(기존 11.5%의 약 2.3배) 글자, 노랑→주황→빨강 세로 그라데이션, 두꺼운 검은 외곽선 +
          아래로 밀린 입체(돌출) 레이어 + 블러 그림자, -4도 기울기(오른쪽 위로), 끝에 포인트(🔥💥😱🤯·ㄷㄷ·?!)
        - 서브 카피: 흰 하이라이트 박스(자막 바) 안에 굵은 검정 글씨, 화면 중간~하단
        """
        W, H = width, height
        bg = Image.fromarray(frame).convert("RGB")
        scale = max(W / bg.width, H / bg.height)
        bg = bg.resize((max(W, round(bg.width * scale)), max(H, round(bg.height * scale))), Image.LANCZOS)
        left, top = (bg.width - W) // 2, (bg.height - H) // 2
        bg = bg.crop((left, top, left + W, top + H))
        bg = ImageEnhance.Contrast(ImageEnhance.Color(bg).enhance(1.18)).enhance(1.06)
        img = bg.convert("RGBA")

        # 비네트: 위(0~46%)와 아래(56~100%)를 어둡게 → 글자 가독성
        shade = np.zeros((H, W, 4), dtype=np.uint8)
        top_h, bot_s = int(H * 0.46), int(H * 0.56)
        shade[:top_h, :, 3] = (np.linspace(1.0, 0.0, top_h) ** 1.25 * THUMB_VIGNETTE_TOP)[:, None].astype(np.uint8)
        shade[bot_s:, :, 3] = (np.linspace(0.0, 1.0, H - bot_s) ** 1.4 * THUMB_VIGNETTE_BOTTOM)[:, None].astype(np.uint8)
        img.alpha_composite(Image.fromarray(shade))

        text = (main_text or "").strip()
        accent = (accent or "").strip()
        emoji = accent if accent and not re.match(r"^[ㄱ-ㅎ?!]+$", accent) else ""
        if accent and not emoji and not text.endswith(accent):
            text = f"{text}{accent}".strip()       # 'ㄷㄷ'·'?!'는 마지막 어절에 붙여 한 줄로 따로 떨어지지 않게
        if text:
            img.alpha_composite(self._thumb_main_layer(text, emoji, W, H))
        if sub_text and sub_text.strip():
            img.alpha_composite(self._thumb_sub_layer(sub_text.strip(), W, H))

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        img.convert("RGB").save(out_path, "PNG", optimize=True)
        logger.info(f"썸네일 저장: {out_path} (메인 '{text}' {emoji} / 서브 '{sub_text}')")
        return out_path

    def _font(self, size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype(self.font_path, size)
        except Exception:
            return ImageFont.load_default()

    def _thumb_main_layer(self, text: str, emoji: str, W: int, H: int) -> Image.Image:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        probe = ImageDraw.Draw(layer)
        emoji_ratio = 0.95 if emoji else 0.0
        size = int(W * THUMB_FONT_RATIO)
        while True:
            font = self._font(size)
            stroke = max(6, int(size * 0.12))
            lines = self._wrap_text(text, probe, font, max_width=int(W * THUMB_TEXT_WIDTH))
            widths = [probe.textbbox((0, 0), ln, font=font, stroke_width=stroke)[2] for ln in lines]
            if widths:
                widths[-1] += int(size * emoji_ratio)
            if (len(lines) <= 2 and max(widths or [0]) <= W * THUMB_TEXT_WIDTH) or size <= int(W * 0.11):
                break
            size = int(size * 0.93)
        line_h = int(size * 1.12)
        y0 = int(H * THUMB_TEXT_TOP)
        placed = []
        for i, ln in enumerate(lines):
            bb = probe.textbbox((0, 0), ln, font=font, stroke_width=stroke)
            lw = bb[2] - bb[0] + (int(size * emoji_ratio) if i == len(lines) - 1 else 0)
            placed.append(((W - lw) // 2 - bb[0], y0 + i * line_h - bb[1], ln, bb))

        # 1) 블러 그림자
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        for x, y, ln, _ in placed:
            sd.text((x + int(size * 0.05), y + int(size * 0.10)), ln, font=font, fill=(0, 0, 0, 190), stroke_width=stroke,
                    stroke_fill=(0, 0, 0, 190))
        layer.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(max(4, int(size * 0.07)))))
        # 2) 입체(돌출): 외곽선 덩어리를 아래·오른쪽으로 겹겹이 (짙은 적갈색 → 검정)
        d = ImageDraw.Draw(layer)
        depth = max(4, int(size * 0.07))
        for k in range(depth, 0, -1):
            c = (int(70 * (1 - k / depth)), 0, 0, 255)
            for x, y, ln, _ in placed:
                d.text((x + k // 2, y + k), ln, font=font, fill=c, stroke_width=stroke, stroke_fill=c)
        # 3) 두꺼운 검은 외곽선 (바깥 + 안쪽 두 겹)
        for x, y, ln, _ in placed:
            d.text((x, y), ln, font=font, fill=(0, 0, 0, 255), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        # 4) 노랑→주황→빨강 그라데이션 글자 (줄마다 위→아래)
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        grad = np.zeros((H, W, 3), dtype=np.uint8)
        stops = np.array(THUMB_GRADIENT, dtype=float)
        for x, y, ln, bb in placed:
            md.text((x, y), ln, font=font, fill=255, stroke_width=max(1, int(size * 0.012)), stroke_fill=255)
            ty0, ty1 = max(0, y + bb[1]), min(H, y + bb[3])
            n = max(1, ty1 - ty0)
            t = np.linspace(0, 1, n)[:, None]
            seg = np.where(t < 0.5, stops[0] + (stops[1] - stops[0]) * (t / 0.5), stops[1] + (stops[2] - stops[1]) * ((t - 0.5) / 0.5))
            grad[ty0:ty1, :, :] = seg[:, None, :].astype(np.uint8).repeat(W, axis=1)[:, :W, :]
        fill = Image.fromarray(grad).convert("RGBA")
        fill.putalpha(mask)
        layer.alpha_composite(fill)
        # 5) 포인트 이모지 (마지막 줄 끝)
        if emoji:
            em = self._emoji_image(emoji, int(size * 0.92))
            if em is not None:
                x, y, ln, bb = placed[-1]
                ex = x + bb[2] + int(size * 0.04)
                ey = y + bb[1] + ((bb[3] - bb[1]) - em.height) // 2
                esh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                esh.paste((0, 0, 0, 170), (ex + 8, ey + 12), em.split()[3])
                layer.alpha_composite(esh.filter(ImageFilter.GaussianBlur(8)))
                layer.alpha_composite(em, (max(0, ex), max(0, ey)))
        # 6) -4도 기울기 (오른쪽이 올라가게 = 반시계 방향), 글자 덩어리 가운데 기준
        cy = (placed[0][1] + placed[-1][1] + placed[-1][3][3]) // 2
        return layer.rotate(THUMB_TILT_DEG, resample=Image.BICUBIC, center=(W // 2, cy))

    def _thumb_sub_layer(self, text: str, W: int, H: int) -> Image.Image:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        size = int(W * THUMB_SUB_FONT_RATIO)
        while True:
            font = self._font(size)
            bb = d.textbbox((0, 0), text, font=font)
            if bb[2] - bb[0] <= W * 0.80 or size <= int(W * 0.05):
                break
            size = int(size * 0.94)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        pad_x, pad_y = int(size * 0.45), int(size * 0.30)
        bw, bh = tw + pad_x * 2, th + pad_y * 2
        bx, by = (W - bw) // 2, int(H * THUMB_SUB_CENTER_Y) - bh // 2
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((bx + 8, by + 14, bx + bw + 8, by + bh + 14), radius=int(size * 0.22), fill=(0, 0, 0, 150))
        layer.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((bx, by, bx + bw, by + bh), radius=int(size * 0.22), fill=(255, 255, 255, 245))
        d.text((bx + pad_x - bb[0], by + pad_y - bb[1]), text, font=font, fill=(10, 10, 10, 255),
               stroke_width=max(1, int(size * 0.02)), stroke_fill=(10, 10, 10, 255))
        return layer

    @staticmethod
    def _emoji_image(emoji: str, target_h: int) -> Optional[Image.Image]:
        """컬러 이모지 (Windows Segoe UI Emoji / 리눅스 Noto Color Emoji). 글꼴이 없으면 None"""
        for path, base in ((r"C:\Windows\Fonts\seguiemj.ttf", 160), ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", 109),
                           ("/System/Library/Fonts/Apple Color Emoji.ttc", 160)):
            if not os.path.exists(path):
                continue
            try:
                f = ImageFont.truetype(path, base)
                canvas = Image.new("RGBA", (base * 3 * len(emoji), base * 2), (0, 0, 0, 0))
                ImageDraw.Draw(canvas).text((10, 10), emoji, font=f, embedded_color=True)
                box = canvas.getbbox()
                if not box:
                    continue
                em = canvas.crop(box)
                return em.resize((max(1, round(em.width * target_h / em.height)), target_h), Image.LANCZOS)
            except Exception:
                continue
        return None

    def render_disclosure(self, text: str, width: int, height: int) -> Image.Image:
        """제휴 광고 표시 레이어: 위쪽 가운데 작은 글씨(반투명 흰색 + 얇은 외곽선), 폭 넘으면 2줄"""
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        if not (text and text.strip()):
            return layer
        draw = ImageDraw.Draw(layer)
        try:
            font = ImageFont.truetype(self.font_path, max(14, int(width * DISCLOSURE_FONT_RATIO)))
        except Exception:
            font = ImageFont.load_default()
        lines = self._wrap_text(text.strip(), draw, font, max_width=int(width * 0.78))
        line_h = int(font.size * 1.3)
        y = int(height * DISCLOSURE_Y)
        for ln in lines:
            bb = draw.textbbox((0, 0), ln, font=font)
            x = (width - (bb[2] - bb[0])) // 2 - bb[0]
            draw.text((x, y - bb[1]), ln, font=font, fill=(255, 255, 255, 215), stroke_width=2, stroke_fill=(0, 0, 0, 170))
            y += line_h
        return layer

    def create_overlay_image(
        self,
        caption_text: Optional[str],
        hook_title: Optional[str],
        width: int,
        height: int,
        mode: str = "commerce",
        **kwargs
    ) -> np.ndarray:
        """
        숏폼 전용 고가독성 자막 오버레이:
        - [여행/사장님 모드]: _caption_block - 화면 정중앙(50%), 외곽선 + 드롭섀도우 글자 (배경 박스 없음)
        - [일반 커머스 모드]:
          * 씬 1 (hook_title 존재 시): 상단 17% 지점 후킹 바 단독 노출
          * 일반 씬: 하단 62% 지점(쇼츠 UI 세이프존)에 볼드 자막 노출
        """
        if mode in CENTER_CAPTION_MODES:
            # 쇼핑은 상품 카드(화면 31~71%)를 가리지 않도록 카드 위쪽에 자막
            center = COMMERCE_CAPTION_CENTER_Y if mode == "commerce" else CAPTION_CENTER_Y
            return self._caption_block(caption_text, hook_title, width, height, center_y=center)
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # -------------------------------------------------------------
        # Case 1: 상단 초강력 후킹 바 (씬 1 등 hook_title 존재 시)
        # -------------------------------------------------------------
        if hook_title and hook_title.strip():
            hook_text = hook_title.strip()
            hook_font_size = int(width * 0.078)
            try:
                hook_font = ImageFont.truetype(self.font_path, hook_font_size)
            except Exception:
                hook_font = ImageFont.load_default()

            hook_lines = self._wrap_text(hook_text, draw, hook_font, max_width=int(width * 0.82))
            if hook_lines:
                hook_line_height = int(hook_font_size * 1.35)
                total_hook_height = len(hook_lines) * hook_line_height

                # [모바일 세이프존] 화면 상단 17% 지점
                center_y_hook = int(height * 0.17)
                start_y_hook = center_y_hook - (total_hook_height // 2)

                padding_x_hook = 42
                padding_y_hook = 22

                for i, line in enumerate(hook_lines):
                    bbox = draw.textbbox((0, 0), line, font=hook_font)
                    w = bbox[2] - bbox[0]
                    h = bbox[3] - bbox[1]
                    x = (width - w) // 2
                    y = start_y_hook + (i * hook_line_height)

                    # 후킹 박스: 깊은 반투명 블랙 둥근 박스
                    draw.rounded_rectangle(
                        [(x - padding_x_hook, y - padding_y_hook // 2),
                         (x + w + padding_x_hook, y + h + padding_y_hook)],
                        radius=20,
                        fill=(0, 0, 0, 220)
                    )

                    # 후킹 텍스트: 두터운 형광 옐로우 본체 + 선명한 외곽선
                    draw.text(
                        (x, y),
                        line,
                        font=hook_font,
                        fill=(255, 235, 0),
                        stroke_width=4,
                        stroke_fill=(0, 0, 0)
                    )

            # 커머스 모드는 씬 1에서 후킹 바 단독 노출
            return np.array(img)

        # -------------------------------------------------------------
        # Case 2: 커머스 나레이션 자막 (하단 62%)
        # -------------------------------------------------------------
        if caption_text and caption_text.strip():
            sub_text = caption_text.strip()
            sub_font_size = int(width * 0.064)
            center_y_sub = int(height * 0.62)
            stroke_width = 5

            try:
                sub_font = ImageFont.truetype(self.font_path, sub_font_size)
            except Exception:
                sub_font = ImageFont.load_default()

            sub_lines = self._wrap_text(sub_text, draw, sub_font, max_width=int(width * 0.85))
            if sub_lines:
                sub_line_height = int(sub_font_size * 1.35)
                total_sub_height = len(sub_lines) * sub_line_height
                start_y_sub = center_y_sub - (total_sub_height // 2)

                for i, line in enumerate(sub_lines):
                    bbox = draw.textbbox((0, 0), line, font=sub_font)
                    w = bbox[2] - bbox[0]
                    x = (width - w) // 2
                    y = start_y_sub + (i * sub_line_height)

                    # 1. 드롭 섀도우 (Drop Shadow): 약간 우하단 오프셋(+4, +4), 반투명 블랙 그림자
                    shadow_offset_x = 4
                    shadow_offset_y = 4
                    draw.text(
                        (x + shadow_offset_x, y + shadow_offset_y),
                        line,
                        font=sub_font,
                        fill=(0, 0, 0, 160),
                        stroke_width=stroke_width,
                        stroke_fill=(0, 0, 0, 160)
                    )

                    # 2. 순수 타이포그래피: 흰색 폰트(#FFFFFF) + 굵은 검은색 테두리(Stroke 5px)
                    # (검은색 반투명 사각형 Bounding Box는 완전 삭제하여 배경 영상을 가리지 않음)
                    draw.text(
                        (x, y),
                        line,
                        font=sub_font,
                        fill=(255, 255, 255, 255),
                        stroke_width=stroke_width,
                        stroke_fill=(0, 0, 0, 255)
                    )

        return np.array(img)

    def create_subtitle_image(self, text: str, width: int, height: int, mode: str = "commerce", **kwargs) -> np.ndarray:
        """기존 하위 호환성을 위한 단일 자막 생성 래퍼"""
        return self.create_overlay_image(caption_text=text, hook_title=None, width=width, height=height, mode=mode, **kwargs)

    def assemble_ad(
        self,
        asset_manifest: List[Dict[str, Any]],
        output_filename: str = "final.mp4",
        fps: int = 24,
        audio_mode: str = "voice_and_sub",
        mode: str = "commerce",
        brand: Optional[BrandOverlay] = None,
        disclosure: Optional[str] = None,
        intro_image: Optional[str] = None,
        bgm_path: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        최종 조립. 기본은 ffmpeg filter_complex 한 번(core/render/ffmpeg_assemble.py: 자막 PNG overlay + xfade + 하드웨어 인코딩),
        실패하면 MoviePy 경로로 자동 대체. RENDER_ENGINE=moviepy 로 MoviePy 강제 (디자인·타임라인 규칙은 같음)
        bgm_path: 자체 음원(assets/bgm). 완성 영상에 나레이션 덕킹·페이드로 섞음 (core/render/bgm.py).
        실패하면 BGM 없이 두고 self.last_bgm_error에 사유를 남김
        """
        final_output_path = os.path.join(self.output_dir, output_filename)
        self.last_bgm_error = None
        self.last_bgm_info = None      # 실제로 섞인 BGM {track, start, gain_db, duck} (없거나 실패하면 None)
        out = None
        if os.getenv("RENDER_ENGINE", "ffmpeg").strip().lower() != "moviepy":
            from core.render import ffmpeg_assemble
            try:
                out = ffmpeg_assemble.assemble(self, asset_manifest, final_output_path, fps=fps, mode=mode, brand=brand,
                                               disclosure=disclosure, intro_image=intro_image, xfade=XFADE_SEC,
                                               intro_sec=INTRO_SEC, hook_solo_sec=HOOK_SOLO_SEC,
                                               center_modes=CENTER_CAPTION_MODES)
            except FileNotFoundError:
                raise
            except Exception as e:
                logger.warning(f"ffmpeg 조립 실패 → MoviePy로 대체: {e}")
        if out is None:
            out = self._assemble_moviepy(asset_manifest, output_filename=output_filename, fps=fps, audio_mode=audio_mode,
                                         mode=mode, brand=brand, disclosure=disclosure, intro_image=intro_image)
        if bgm_path:
            try:
                self._mix_bgm(out, asset_manifest, bgm_path, narration=(audio_mode != "silent"))
            except Exception as e:
                self.last_bgm_error = str(e)
                logger.warning(f"BGM 믹싱 실패 → BGM 없이 저장: {e}")
        return out

    def _mix_bgm(self, video_path: str, manifest: List[Dict[str, Any]], bgm_path: str, narration: bool = True) -> str:
        """씬 음성 길이로 타임라인(조립과 같은 규칙)을 다시 계산해 나레이션 구간에만 BGM 덕킹"""
        from core.render import bgm as bgm_mod
        from core.render.ffmpeg_assemble import build_timeline, probe
        a_durs = [probe(it["audio_path"])["duration"] or 0.0 for it in manifest]
        durs = [a + (1.0 if i == len(a_durs) - 1 else 0.2 + XFADE_SEC) for i, a in enumerate(a_durs)]
        starts, _ = build_timeline(durs, XFADE_SEC)
        intervals = bgm_mod.narration_intervals(zip(starts, a_durs)) if narration else []
        voice_db = None
        if narration:
            levels = [bgm_mod.mean_volume_db(it["audio_path"]) for it in manifest[:3]]
            levels = [v for v in levels if v is not None and v > -60]
            voice_db = sum(levels) / len(levels) if levels else None
        info: Dict[str, Any] = {}
        out = bgm_mod.mix_bgm(video_path, video_path, bgm_path, intervals, voice_db=voice_db, info=info)
        self.last_bgm_info = info
        return out

    def _assemble_moviepy(
        self,
        asset_manifest: List[Dict[str, Any]],
        output_filename: str = "final.mp4",
        fps: int = 24,
        audio_mode: str = "voice_and_sub",
        mode: str = "commerce",
        brand: Optional[BrandOverlay] = None,
        disclosure: Optional[str] = None,
        intro_image: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Step 3: 각 씬별 비디오와 오디오를 오디오 길이에 맞춰 컷팅하고,
        자막 오버레이(실사 모드: 하단 자막, 씬 1 후킹→서브 시간차)를 합성한 뒤 최종 광고 비디오 결합.
        오디오는 AI 나레이션(VO). 자체 음원 BGM은 assemble_ad(bgm_path=)가 완성 후 섞음 (이 함수는 나레이션만)
        (플랫폼 인앱 트렌딩 음원·자사 유통 음원을 업로드 단계에서 태그하는 정책).
        brand(BrandOverlay)가 주어지면 전 씬 워터마크 + (end_card 설정 시) 엔딩 카드 클립을 추가.
        disclosure(제휴 광고 표시 문구, 예: 쿠팡 파트너스)가 주어지면 전 씬 위쪽에 작게 표시.
        """
        final_output_path = os.path.join(self.output_dir, output_filename)
        logger.info(f"Step 3 시작 (모드={mode}): 타임라인 조립, 자막 오버레이 합성 진행 (총 {len(asset_manifest)}개 씬, 오디오 모드: {audio_mode})")

        processed_clips = []
        opened_clips = []
        watermark_cache = {}
        has_end_card = bool(brand and brand.end_card)

        try:
            for idx, item in enumerate(asset_manifest, 1):
                scene_num = item.get("scene_index") or item.get("scene_number", idx)
                video_path = item.get("video_path")
                audio_path = item.get("audio_path")
                caption_text = item.get("subtitle") or item.get("caption") or item.get("voiceover", "")
                hook_title = item.get("hook_title", "")

                if not video_path or not os.path.exists(video_path):
                    raise FileNotFoundError(f"씬 {scene_num} 비디오 파일 누락: {video_path}")
                if not audio_path or not os.path.exists(audio_path):
                    raise FileNotFoundError(f"씬 {scene_num} 오디오 파일 누락: {audio_path}")

                # 1. 오디오 로드 (절대 중간에 자르지 않고 나레이션 음성 전량 보존)
                audio_clip = AudioFileClip(audio_path)
                opened_clips.append(audio_clip)
                audio_duration = audio_clip.duration
                logger.info(f"씬 {scene_num}: 나레이션 오디오 원본 길이 = {audio_duration:.2f}초")

                # 2. 비디오 길이 안전 연장 (엔딩 나레이션 잘림 원천 차단: 마지막 씬 +1.0초, 일반 씬 +0.2초 여유 마진)
                is_last_scene = (idx == len(asset_manifest))
                # 다음 씬과 XFADE_SEC만큼 겹쳐 디졸브: 겹치는 만큼 꼬리를 늘려 나레이션 간격(0.2초)·전체 길이는 그대로,
                # 겹침 구간에는 앞 씬의 무음 꼬리만 깔리므로 두 나레이션이 섞이지 않음
                tail_margin = 1.0 if is_last_scene else 0.2 + XFADE_SEC
                scene_target_duration = audio_duration + tail_margin
                logger.info(f"씬 {scene_num} (마지막={is_last_scene}): 오디오 {audio_duration:.2f}초 + 테일 마진 {tail_margin:.2f}초 -> 비디오 목표 재생 시간: {scene_target_duration:.2f}초")

                # 3. 비디오 로드 및 목표 재생 시간에 맞춤
                video_clip = VideoFileClip(video_path)
                opened_clips.append(video_clip)
                video_duration = video_clip.duration

                if video_duration >= scene_target_duration:
                    synced_video = video_clip.subclip(0, scene_target_duration)
                else:
                    diff = scene_target_duration - video_duration
                    last_frame_clip = video_clip.to_ImageClip(t=max(0, video_duration - 0.05)).set_duration(diff)
                    opened_clips.append(last_frame_clip)
                    synced_video = concatenate_videoclips([video_clip, last_frame_clip], method="compose")

                # 4. 자막 오버레이 (실사 모드 씬 1: 후킹 단독 0~hook_sec → 서브 자막, 나레이션 2단 음성과 같은 시점)
                w, h = synced_video.size
                if caption_text or hook_title or brand or disclosure:
                    split_hook = mode in CENTER_CAPTION_MODES and bool(hook_title and hook_title.strip())
                    if split_hook:
                        hook_sec = float(item.get("hook_sec") or HOOK_SOLO_SEC)
                        hook_sec = max(0.5, min(hook_sec, scene_target_duration - 0.5))
                        segments = [(0.0, hook_sec, None, hook_title), (hook_sec, scene_target_duration - hook_sec, caption_text, None)]
                        logger.info(f"씬 {scene_num} 후킹 단독 0~{hook_sec:.2f}초 '{hook_title}' → 서브 자막 '{caption_text}'")
                    else:
                        segments = [(0.0, scene_target_duration, caption_text, hook_title)]
                        if mode in CENTER_CAPTION_MODES:
                            logger.info(f"씬 {scene_num} 중앙 자막 렌더링 (화면 50%): '{caption_text}'")
                        elif hook_title and hook_title.strip():
                            logger.info(f"씬 {scene_num} 상단 후킹 바 단독 렌더링 (모바일 세이프존 17%): '{hook_title}'")
                        else:
                            logger.info(f"씬 {scene_num} 하단 나레이션 자막 단독 렌더링 (쇼츠 UI 세이프존 62%): '{caption_text}'")

                    layers = [synced_video]
                    for seg_start, seg_dur, seg_caption, seg_hook in segments:
                        overlay_img_arr = self.create_overlay_image(
                            caption_text=seg_caption,
                            hook_title=seg_hook,
                            width=w,
                            height=h,
                            mode=mode
                        )
                        if brand or disclosure:
                            merged = Image.fromarray(overlay_img_arr).convert("RGBA")
                            if brand:
                                if (w, h) not in watermark_cache:
                                    watermark_cache[(w, h)] = brand.render_watermark(w, h, font_path=self.font_path)
                                merged.alpha_composite(watermark_cache[(w, h)])
                            if disclosure:
                                key = ("disclosure", w, h)
                                if key not in watermark_cache:
                                    watermark_cache[key] = self.render_disclosure(disclosure, w, h)
                                merged.alpha_composite(watermark_cache[key])
                            overlay_img_arr = np.array(merged)
                        sub_clip = ImageClip(overlay_img_arr, ismask=False).set_start(seg_start).set_duration(seg_dur)
                        opened_clips.append(sub_clip)
                        layers.append(sub_clip)
                    # 비디오 위에 오버레이 클립 합성
                    synced_video = CompositeVideoClip(layers).set_duration(scene_target_duration)

                # 5. 오디오 결합 (오디오가 완전히 끝난 뒤에도 화면이 0.3~0.5초간 머물도록 비디오 길이에 맞춤)
                synced_video = synced_video.set_audio(audio_clip)

                # 6. 마지막 씬은 오디오 끝난 후 0.4초간 자연스러운 페이드아웃 적용 (엔딩 카드가 있으면 카드에서 페이드아웃)
                if is_last_scene and not has_end_card:
                    try:
                        synced_video = synced_video.fadeout(0.4)
                    except Exception as fe:
                        logger.debug(f"페이드아웃 적용 예외 (무시): {fe}")

                processed_clips.append(synced_video)
                opened_clips.append(synced_video)

            if not processed_clips:
                raise ValueError("합성할 유효한 비디오 클립이 없습니다.")

            # 브랜드 엔딩 카드: 마지막 장면의 마지막 프레임을 흐리게 깔고 상호명 + 검색 유도 그래픽 표시
            if has_end_card:
                last_clip = processed_clips[-1]
                last_frame = last_clip.get_frame(max(0, last_clip.duration - 0.05))
                card_arr = brand.render_end_card(last_frame, font_path=self.font_path)
                card_clip = ImageClip(card_arr).set_duration(float(brand.end_card.duration_sec) + XFADE_SEC).fadeout(0.4)
                opened_clips.append(card_clip)
                processed_clips.append(card_clip)
                logger.info(f"브랜드 엔딩 카드 추가 ({brand.end_card.duration_sec:.1f}초): '{brand.business_name}' / 검색어 '{brand.end_card.search_keyword}'")

            # 첫 INTRO_SEC초는 썸네일 화면 (타임라인·나레이션은 그대로, 화면만 덮음 → 첫 프레임 = 썸네일)
            # 씬 1 클립에만 덮음: 전체 타임라인을 한 번 더 합성하면 모든 프레임 합성 비용이 두 배가 됨
            if intro_image and os.path.exists(intro_image) and processed_clips:
                first = processed_clips[0]
                fw, fh = first.size
                intro_arr = np.array(Image.open(intro_image).convert("RGB").resize((fw, fh), Image.LANCZOS))
                intro_clip = ImageClip(intro_arr).set_start(0).set_duration(min(INTRO_SEC, first.duration))
                first_with_intro = CompositeVideoClip([first, intro_clip]).set_duration(first.duration).set_audio(first.audio)
                opened_clips += [intro_clip, first_with_intro]
                processed_clips[0] = first_with_intro
                logger.info(f"첫 {INTRO_SEC}초 썸네일 화면 삽입 (씬 1): {intro_image}")

            # 전체 씬 결합
            logger.info(f"전체 {len(processed_clips)}개 클립 타임라인 결합 중...")
            # 씬 전환: 하드 컷 대신 XFADE_SEC 디졸브 (다음 씬이 앞 씬 위로 서서히 겹쳐 나타남, 검은 화면 없음)
            if len(processed_clips) > 1 and XFADE_SEC > 0:
                processed_clips = [processed_clips[0]] + [c.crossfadein(XFADE_SEC) for c in processed_clips[1:]]
                opened_clips.extend(processed_clips[1:])
                logger.info(f"씬 전환 디졸브 {XFADE_SEC:.2f}초 x {len(processed_clips) - 1}곳")
            final_composite = concatenate_videoclips(processed_clips, method="compose",
                                                     padding=-XFADE_SEC if len(processed_clips) > 1 else 0)
            if len(processed_clips) > 1 and XFADE_SEC > 0:
                # MoviePy는 마지막 클립 뒤에도 padding을 빼서 끝 XFADE_SEC가 잘림 → 마지막 클립 끝까지 복원 (엔딩 여유·페이드아웃 보존)
                final_composite = final_composite.set_duration(final_composite.duration + XFADE_SEC)
            opened_clips.append(final_composite)

            # 비디오 전체 타임라인 길이가 '모든 씬 오디오 총합 + 엔딩 여유 마진 1.0초'보다 짧지 않도록 강제 보장
            # (인위적인 subclip 잘림을 영구 제거하여 마지막 씬 "댓글에 '여행' 남겨주세요"가 절대 잘리지 않음)
            total_duration = final_composite.duration
            logger.info(f"최종 비디오 타임라인 총 재생 시간: {total_duration:.2f}초 (인위적 subclip 절단 없이 전량 보존)")
            logger.info(f"최종 영상 총 재생 시간: {total_duration:.2f}초")

            # BGM은 여기서 넣지 않음: 자체 음원은 assemble_ad가 완성 영상에 후처리로 믹싱 (core/render/bgm.py)

            logger.info(f"최종 렌더링 시작 -> {final_output_path} (FPS: {fps}, Codec: libx264, Audio: aac)")

            # MoviePy 임시 오디오 파일을 작업 폴더 안에 생성 (CWD 오염 및 세션 간 충돌 방지)
            temp_audio_path = os.path.join(self.output_dir, os.path.splitext(output_filename)[0] + "_TEMP_MPY_audio.m4a")
            final_composite.write_videofile(
                final_output_path,
                temp_audiofile=temp_audio_path,
                remove_temp=True,
                fps=fps,
                codec="libx264",
                audio_codec="aac",
                preset="medium",
                threads=4,
                logger="bar"
            )

            logger.info(f"광고 영상 렌더링 완료! 파일 저장 경로: {final_output_path}")
            return final_output_path

        finally:
            logger.info("MoviePy 리소스 해제 중...")
            for c in opened_clips:
                try:
                    c.close()
                except Exception:
                    pass


# ---------------------------------------------------------------- 자막 줄바꿈: 의미 덩어리 점수
_PARTICLES = ("에서", "으로", "에게", "까지", "부터", "처럼", "이랑", "은", "는", "이", "가", "을", "를", "에", "도", "로", "랑", "와", "과", "의", "만")
_CLAUSE_ENDS = ("는데", "거든요", "지만", "니까", "면서", "는데요", "고요", "어서", "아서", "해서", "하고", "면", "고", "서", "며")
_AUX_HEADS = ("있", "없", "않", "싶", "주", "줘", "버", "놓", "두", "보", "봐", "봤", "드")
_AUX_PREV_ENDS = ("고", "어", "아", "여", "게", "지", "해", "워")
_ADNOMINAL_ENDS = ("한", "던", "운", "된", "인", "할", "볼", "갈", "올", "될", "낼", "을")
# 구를 닫는 1음절 의존명사·축약 ('대박인 건 / ~', '나쁠 땐 / ~') - 1음절이어도 뒤에서 끊기 좋은 자리
_PHRASE_CLOSERS = ("건", "게", "데", "땐", "걸", "거", "곳", "때")
EXTRA_LINE_COST = 1.3


def _core(word: str) -> str:
    """문장부호를 떼고, 끝 조사를 하나 뗀 어간 (예: '뷰가' → '뷰', '것도' → '것')"""
    w = re.sub(r"[^\w]", "", word)
    for p in _PARTICLES:
        if len(w) > len(p) and w.endswith(p):
            return w[: -len(p)]
    return w


def break_penalty(prev: str, nxt: str) -> float:
    """prev 뒤에서 줄을 바꿀 때의 감점 (음수 = 끊기 좋은 자리)"""
    pen = 0.0
    p = re.sub(r"[^\w]", "", prev)
    if p in _PHRASE_CLOSERS:
        pen -= 0.4          # '대박인 건 / 객실 침구가': 구가 닫히는 자리
    elif len(p) == 1:
        pen += 1.5          # '안 / 가보면', '더 / 좋아요': 1음절 부사·관형사는 뒤 말에 붙음
    if len(_core(nxt)) == 1:
        pen += 1.2          # '바다 / 뷰가', '실내 / 풀로', '좋은 / 건': 1음절 명사는 앞 말과 한 덩어리
    if p.endswith(_AUX_PREV_ENDS) and nxt.startswith(_AUX_HEADS):
        pen += 1.0          # '보고 / 있으면', '들어가 / 보면': 본용언 + 보조용언
    if len(p) >= 2 and p.endswith(_ADNOMINAL_ENDS):
        pen += 0.6          # '완벽한 / 뷰', '볼 / 수': 꾸미는 말과 꾸밈 받는 말
    if prev.rstrip().endswith((",", ".", "!", "?")):
        pen -= 0.8          # 쉼표·문장부호 뒤는 숨 쉬는 자리
    elif p.endswith(_CLAUSE_ENDS):
        pen -= 0.6          # '~는데 / ~', '~고 / ~': 절이 끝나는 자리
    elif p.endswith(_PARTICLES) and len(p) >= 3:
        pen -= 0.3          # '수영장은 / ~', '바다에서 / ~': 명사구가 끝나는 자리
    return pen
