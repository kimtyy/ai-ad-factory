"""
core/render/thumbnail.py - 🎉 모임·행사·추억 감성 기념 썸네일 (1080×1920)

인스타그램 릴스 커버·유튜브 쇼츠 썸네일·카카오톡 단톡방 공유용. 매장 광고 썸네일(노랑→빨강 큰 글씨)과 달리 차분한 기념 사진 느낌:
- 대표 사진(영상 첫 프레임) 꽉 채움 + 아래쪽이 어두워지는 부드러운 그라데이션 (사진은 그대로 보이게 위쪽은 거의 건드리지 않음)
- 모임·행사 이름: 감성 명조(따뜻한·기록·기념) 또는 둥근 팝체(유쾌한), 흰 글씨 + 얇은 그림자. 최대 3줄, 글자는 폭에 맞게 자동 축소
- 톤별 따뜻한 서브 카피 한 줄(가는 선 아래). 광고·판촉 문구는 쓰지 않는다
"""
import logging
import os
from typing import List, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from core.render import fonts

logger = logging.getLogger("ad_factory.render.thumbnail")

W, H = 1080, 1920
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


def _wrap(text: str, draw: ImageDraw.ImageDraw, font, max_w: int) -> List[str]:
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
    for lines_n in range(2, min(TITLE_MAX_LINES, n) + 1):
        # 어절 경계 조합을 모두 시험 (어절 수가 적어 충분히 빠름)
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


def _fit_title(text: str, comp, key: str):
    spec = fonts.resolve(key, comp)
    probe = ImageDraw.Draw(Image.new("RGBA", (W, H)))
    size = int(W * TITLE_SIZE_MAX)
    while True:
        font = fonts.load(spec, size)
        lines = _wrap(text, probe, font, int(W * TEXT_WIDTH))
        widths = [_line_width(font, ln) for ln in lines] or [0]
        if (len(lines) <= TITLE_MAX_LINES and max(widths) <= W * TEXT_WIDTH) or size <= int(W * TITLE_SIZE_MIN):
            return spec, font, lines, size
        size = int(size * 0.94)


def render_memory_thumbnail(frame: np.ndarray, title: str, tone: str, out_path: str, comp, sub_text: str = "") -> str:
    """frame: 대표 사진(RGB 배열). title: 모임·행사 이름(비면 sub만 크게). 반환: 저장한 PNG 경로"""
    key = FONT_BY_TONE.get(tone, "serif")
    title = " ".join(str(title or "").split())
    sub = " ".join(str(sub_text or "").split())
    for w in BANNED:                              # 상업적 문구는 어디서 와도 넣지 않음
        title, sub = title.replace(w, ""), sub.replace(w, "")

    bg = Image.fromarray(frame).convert("RGB")
    scale = max(W / bg.width, H / bg.height)
    bg = bg.resize((max(W, round(bg.width * scale)), max(H, round(bg.height * scale))), Image.LANCZOS)
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = ImageEnhance.Color(bg.crop((left, top, left + W, top + H))).enhance(1.06)
    img = bg.convert("RGBA")

    # 아래쪽 부드러운 어둠 (글자 가독성) + 위쪽은 아주 살짝
    shade = np.zeros((H, W, 4), dtype=np.uint8)
    s0 = int(H * SHADE_START)
    shade[s0:, :, 3] = (np.linspace(0.0, 1.0, H - s0) ** 1.35 * SHADE_ALPHA)[:, None].astype(np.uint8)
    top_h = int(H * 0.16)
    shade[:top_h, :, 3] = (np.linspace(1.0, 0.0, top_h) ** 1.5 * 70)[:, None].astype(np.uint8)
    img.alpha_composite(Image.fromarray(shade))

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    spec, tfont, lines, tsize = _fit_title(title, comp, key) if title else (fonts.resolve(key, comp), None, [], int(W * TITLE_SIZE_MAX))
    sfont = fonts.load(spec, int(W * SUB_SIZE))
    line_h = int(tsize * 1.22)
    sub_h = int(W * SUB_SIZE * 1.2) if sub else 0
    gap = int(W * 0.035) if (lines and sub) else 0
    rule_h = int(H * 0.012) if (lines and sub) else 0
    block_h = line_h * len(lines) + gap + rule_h + gap + sub_h if lines else sub_h
    y = int(H * BLOCK_BOTTOM) - block_h
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    stroke = max(2, int(tsize * (0.045 if key == "pop" else 0.012)))
    for ln in lines:
        x = (W - _line_width(tfont, ln)) // 2
        base = y + int(tsize * 0.95)                       # 글자 기준선 (anchor ls)
        _draw_line(sd, x + 3, base + 5, ln, tfont, fill=(0, 0, 0, 170), stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
        _draw_line(d, x, base, ln, tfont, fill=(255, 255, 255, 255), stroke_width=stroke, stroke_fill=(20, 14, 10, 235 if key == "pop" else 150))
        y += line_h
    if lines and sub:
        y += gap
        d.rounded_rectangle((W // 2 - int(W * 0.07), y, W // 2 + int(W * 0.07), y + max(3, rule_h // 3)), radius=2, fill=(255, 255, 255, 215))
        y += rule_h + gap
    if sub:
        x = (W - _line_width(sfont, sub)) // 2
        base = y + int(W * SUB_SIZE * 0.95)
        _draw_line(sd, x + 2, base + 3, sub, sfont, fill=(0, 0, 0, 160))
        _draw_line(d, x, base, sub, sfont, fill=(255, 244, 226, 245))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))
    img.alpha_composite(layer)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    logger.info(f"추억 썸네일 저장: {out_path} (제목 {lines} · 서브 '{sub}' · 폰트 {key})")
    return out_path
