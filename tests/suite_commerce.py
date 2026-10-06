"""쇼핑 제휴(쿠팡 파트너스) 파이프라인: 실물 사진 전용(AI 이미지 없음), 리뷰 썰 대본·근거 없는 효능 차단, 제휴 표시, 상품 사진 수집"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import io
import json
import os
import logging

logging.disable(logging.WARNING)
import numpy as np
from PIL import Image

import core.planners.commerce as cm
from core.planners import COMMERCE_CTA, COUPANG_DISCLOSURE, plan_commerce_storyboard
from core.script_guard import find_promo_tone, find_unsupported_spec, has_hapsyo, is_complete_sentence

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


HEATER = "유파 PTC 전기히터 TSK-5318"
HEATER_USP = "과열 방지, 2단 온도 조절, 저소음"

# ---------------------------------------------------------------- 1. 오프라인(mock) 기획
sc = plan_commerce_storyboard(HEATER, HEATER_USP, [], mock=True)
check([s["role"] for s in sc] == ["hero", "feature", "detail", "cta"] and sc[-1]["voiceover"] == COMMERCE_CTA
      and sc[0]["hook_title"], "사진 없음(mock) → 최소 4씬 (후킹·특징 2·CTA), 고정 댓글 CTA")
from core.script_guard import complete_sentence
check(all(complete_sentence(l) == l and not find_promo_tone(l) and not find_unsupported_spec(l) for ls in cm.TEMPLATE_LINES.values() for l in ls)
      and all(is_complete_sentence(s["voiceover"]) and not find_promo_tone(s["voiceover"]) for s in sc[:-1]),
      "템플릿 대사: 해요체 완결 문장, 홍보 어투 없음, 효능·수치 주장 없음")
media6 = [fixture("photo_pool.jpg")] * 6
check(len(plan_commerce_storyboard(HEATER, "", media6, mock=True)) == 6, "사진 6장 → 6씬 (사진 수 기준)")
try:
    plan_commerce_storyboard("", "", [], mock=True)
    check(False, "상품명 없음")
except ValueError:
    check(True, "상품명이 비면 기획 거부 (지어내지 않음)")

# ---------------------------------------------------------------- 2. Gemini 응답 후처리 (가짜 응답)
check(find_unsupported_spec("48시간 내내 따뜻해요", HEATER_USP) == "48시간" and find_unsupported_spec("2단 온도 조절이 돼요", HEATER_USP) is None
      and find_unsupported_spec("특허받은 기술이에요", HEATER_USP) == "특허", "효능·수치·인증: 판매 페이지(USP)에 있는 것만 허용")
sent = {}
reply = {"story": "…", "scenes": [
    {"hook_title": "겨울 난방비 고민 끝", "voiceover": "솔직히 이 히터 하나로 방이 금방 따뜻해지거든요."},
    {"voiceover": "48시간 틀어 놔도 전기세가 거의 안 나옵니다."},                 # USP에 없는 수치 + 합쇼체
    {"voiceover": "과열 방지 기능이 있어서 밤새 켜 둬도 안심이 되고,"},           # 연결 어미 → 종결
    {"voiceover": "2단 온도 조절이 돼서 상황 따라 쓰기 편하거든요."},             # USP 근거 있는 수치 → 유지
    {"voiceover": "이번 겨울엔 꼭 사 보세요!"},                                   # 홍보 어투
    {"voiceover": "x"}]}


def fake_gemini(*args, **kw):
    sent["args"] = args
    out = cm._ScenesWithStory(reply["scenes"])
    out.story = reply["story"]
    return out


_orig = cm._plan_with_gemini
cm._plan_with_gemini = fake_gemini
g = plan_commerce_storyboard(HEATER, HEATER_USP, [fixture("photo_pool.jpg")] * 6, api_key="fake")
cm._plan_with_gemini = _orig
for s in g:
    print(f"     쇼핑 씬{s['scene_index']} [{s['role']}]: {s['voiceover']}")
check(g[0]["voiceover"] == "솔직히 이 히터 하나로 방이 금방 따뜻해지거든요." and g[0]["hook_title"] == "겨울 난방비 고민 끝",
      "정상 대사·후킹 유지")
check("48시간" not in g[1]["voiceover"] and g[1]["voiceover"] in cm.TEMPLATE_LINES["feature"], "USP에 없는 '48시간' → 템플릿 대사로 교체")
check(len({s["voiceover"] for s in g}) == len(g), "대체된 씬끼리도 같은 문장 반복 없음")
check(g[2]["voiceover"].endswith("안심이 되고요.") and g[3]["voiceover"] == "2단 온도 조절이 돼서 상황 따라 쓰기 편하거든요.",
      "연결 어미 종결 보정, USP 근거 있는 '2단'은 유지")
check(not find_promo_tone(g[4]["voiceover"]) and g[-1]["voiceover"] == COMMERCE_CTA and g[-1]["caption"] == COMMERCE_CTA.rstrip("!"),
      "홍보 어투 교체, CTA 고정")
check(all(not has_hapsyo(s["voiceover"]) and is_complete_sentence(s["voiceover"]) for s in g[:-1]) and g[0].get("story") == "…",
      "전 씬 해요체 완결 문장 + 전체 독백(story) 보관")

# 프롬프트: 상품 종류 맞춤, USP 근거, 사진 첨부
captured = {}


class FakeModels:
    def generate_content(self, **kw):
        captured.update(kw)
        return type("R", (), {"text": json.dumps(reply, ensure_ascii=False)})()


import google.genai as gg
_orig_client = gg.Client
gg.Client = lambda api_key=None: type("C", (), {"models": FakeModels()})()
plan_commerce_storyboard(HEATER, HEATER_USP, [fixture("photo_pool.jpg"), fixture("photo_room.jpg")], api_key="fake",
                         thumb_dir=tmp("commerce_thumb_"))
gg.Client = _orig_client
prompt = [c for c in captured["contents"] if isinstance(c, str)][-1]
images = [c for c in captured["contents"] if isinstance(c, Image.Image)]
check(len(images) == 2 and "[씬 1 상품 사진]" in captured["contents"], "Gemini에 실물 상품 사진을 씬 순서대로 첨부")
check("상품 종류에 맞는 말" in prompt and "화장품이 아닌데 '보습·피부'" in prompt and "USP에 그대로 적힌 것만" in prompt
      and COMMERCE_CTA in prompt and "직접 써 본 사람이 친구 1명에게 추천 썰" in prompt and HEATER_USP in prompt,
      "프롬프트: 상품 종류 맞춤(화장품 고정 예시 없음), 효능·수치는 USP 근거만, 리뷰 썰 톤, 고정 CTA")

# ---------------------------------------------------------------- 3. AI 이미지 생성 제거
from core.asset_generator import REAL_MEDIA_MODES, AssetGenerator
src = open(os.path.join(ROOT, "core", "asset_generator.py"), encoding="utf-8").read()
check("commerce" in REAL_MEDIA_MODES and "generate_images" not in src and "imagen-3.0" not in src,
      "쇼핑도 실물 전용 모드, Imagen 호출 코드 제거")
gen = AssetGenerator(cache_dir=tmp("commerce_gen_"), mode="commerce")
try:
    gen.generate_image({"scene_index": 1, "image_prompt_en": "a heater"})
    check(False, "사진 없는 씬")
except ValueError as e:
    check("실물 사진" in str(e), "사진 없는 쇼핑 씬 → AI 생성 대신 중단 (업로드 요청)")
try:
    from core.analyzer import plan_storyboard
    plan_storyboard(product_name=HEATER, usp="", mode="commerce", mock=True)
    check(False, "구 커머스 경로")
except ValueError:
    check(True, "예전 화장품 고정 커머스 프롬프트 경로 차단")

# ---------------------------------------------------------------- 4. 쿠팡 파트너스 표시
from core.compositor import DISCLOSURE_Y, AdCompositor
comp = AdCompositor(output_dir=tmp("commerce_comp_"))
layer = np.array(comp.render_disclosure(COUPANG_DISCLOSURE, 1080, 1920))
rows = np.where(layer[:, :, 3].max(axis=1) > 0)[0] / 1920
cols = np.where(layer[:, :, 3].max(axis=0) > 0)[0] / 1080
check(len(rows) and 0.07 < rows.min() and rows.max() < 0.15 and cols.min() > 0.08 and cols.max() < 0.92,
      f"제휴 표시: 화면 위쪽 가장자리 안쪽 작은 글씨 ({rows.min():.3f}~{rows.max():.3f}, 가운데 자막 50%와 분리)")
check("쿠팡 파트너스" in COUPANG_DISCLOSURE and "수수료" in COUPANG_DISCLOSURE, "표시 문구: 쿠팡 파트너스 + 수수료 고지")

# ---------------------------------------------------------------- 5. 상품 사진 수집 (가짜 응답)
import core.scraper as scr


def img_bytes(w, h, fmt="JPEG", seed=0):
    rng = np.random.default_rng(seed)
    buf = io.BytesIO()
    Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8)).save(buf, fmt, quality=90)
    return buf.getvalue()


ROUTE = {"https://cdn/x/main.jpg": img_bytes(800, 800, seed=1), "https://cdn/x/icon.png": img_bytes(64, 64, "PNG"),
         "https://cdn/x/banner.jpg": img_bytes(1500, 300, seed=2), "https://cdn/x/dup.jpg": img_bytes(800, 800, seed=1),
         "https://cdn/x/side.jpg": img_bytes(700, 900, seed=3)}


class R:
    def __init__(self, data):
        self.status_code, self.content = (200, data) if data else (404, b"")


_orig_get = scr.requests.get
scr.requests.get = lambda u, **kw: R(ROUTE.get(u))
got = scr.download_product_images([{"url": u} for u in ROUTE], tmp("commerce_dl_"), referer=None)
scr.requests.get = _orig_get
check(len(got) == 2 and all(os.path.exists(p) for p in got) and os.path.basename(got[0]).startswith("product_01"),
      f"상품 사진 수집: 아이콘·가로 배너·중복 제외하고 실물 사진만 {len(got)}장 저장")
finish(fails)
