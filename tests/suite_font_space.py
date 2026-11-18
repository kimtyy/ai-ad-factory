import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from core.compositor import AdCompositor
from core.render import fonts

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


TEXT = "신선한 수산물 가게 앞의 설렘."
comp = AdCompositor(output_dir=tmp("fontspace_"))


def ink(font, s):
    im = Image.new("L", (1600, 260), 0)
    ImageDraw.Draw(im).text((20, 20), s, font=font, fill=255)
    return int((np.asarray(im) > 0).sum())


# ---------------------------------------------------------------- 1. 3종 폰트 모두: 공백은 빈 여백, 공백 자리에 네모(☒) 픽셀 0개
for key, label in (("impact", "임팩트 고딕"), ("serif", "감성 명조"), ("pop", "둥근 예능 팝체")):
    spec = fonts.resolve(key, comp)
    f = fonts.load(spec, 96)
    words = TEXT.split(" ")
    # 공백 위치 픽셀: 어절을 따로 그린 합계와 한 줄로 그린 합계가 같아야 한다 (공백이 네모를 그리면 그만큼 늘어남)
    w_sum = sum(ink(f, w) for w in words)
    one = ink(f, TEXT)
    check(fonts.space_is_blank(spec) and ink(f, " ") == 0 and abs(one - w_sum) <= max(40, w_sum * 0.01),
          f"{label}({os.path.basename(spec[0])}): 공백이 빈 여백 — 공백 단독 잉크 {ink(f, ' ')}px, 한 줄 {one} vs 어절 합 {w_sum}")
    check(fonts.glyphs_ok(spec), f"{label}: 한글·숫자·문장부호 글리프 + 공백 검사 통과")

# ---------------------------------------------------------------- 2. 공백 글리프가 없는 폰트는 불합격 → 보강하거나 고딕으로 대체
raw = os.path.join(ROOT, "fonts", "GmarketSansBold.otf")
if os.path.exists(raw):
    rawspec = (raw, 0)
    check(not fonts.space_is_blank(rawspec) and not fonts._loads(rawspec), "원본 Gmarket Sans: 공백이 네모로 찍혀 불합격 처리")
    fixed = fonts.with_space(raw)
    check(bool(fixed) and fonts._loads((fixed, 0)), "공백 글리프를 보강하면 합격 (팝체 유지)")
    pop = fonts.pop_font(os.path.join(ROOT, "fonts"))
    check(pop is not None and fonts.space_is_blank(pop), "pop_font()은 항상 공백이 정상인 폰트를 돌려줌")
else:
    print("SKIP 원본 Gmarket Sans 없음")

# ---------------------------------------------------------------- 3. 비표준 공백 정규화 (NBSP·전각 공백·탭·줄바꿈 → 일반 공백, 폭 없는 문자는 제거)
check(fonts.clean_text("가게\u00a0앞의\u3000설렘\t.\r\n끝") == "가게 앞의 설렘 . 끝", "NBSP·전각·탭·개행 → 일반 공백 한 칸")
check(fonts.clean_text("신선\u200b한\ufeff 수산물") == "신선한 수산물", "폭 없는 문자(U+200B·FEFF) 제거")

# ---------------------------------------------------------------- 4. 실제 자막 렌더 경로: 비표준 공백이 섞여도 일반 공백과 같은 그림, 팝체 자막에 네모 없음
for key in ("impact", "serif", "pop"):
    comp.caption_font = key
    a = comp.create_overlay_image(caption_text="신선한 수산물 가게", hook_title=None, width=1080, height=1920, mode="owner")
    b = comp.create_overlay_image(caption_text="신선한\u00a0수산물\u3000가게", hook_title=None, width=1080, height=1920, mode="owner")
    c = comp.create_overlay_image(caption_text="신선한\u200b 수산물 가게", hook_title=None, width=1080, height=1920, mode="owner")
    check(np.array_equal(a, b) and np.array_equal(a, c), f"자막 렌더({key}): NBSP·전각·폭없는 문자가 섞여도 일반 공백과 같은 그림")
comp.caption_font = "pop"
whole = int((comp.create_overlay_image(caption_text="신선한 수산물", hook_title=None, width=1080, height=1920, mode="owner")[..., 3] > 0).sum())
parts = sum(int((comp.create_overlay_image(caption_text=w, hook_title=None, width=1080, height=1920, mode="owner")[..., 3] > 0).sum()) for w in ("신선한", "수산물"))
check(whole <= parts * 1.03, f"팝체 자막 전체 픽셀 {whole} ≤ 어절별 합 {parts}×1.03 (공백 자리에 네모가 있으면 훨씬 커짐)")

finish(fails)
