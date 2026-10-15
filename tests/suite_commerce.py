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
    {"hook_title": "겨울 난방비 고민 끝", "voiceover": "외풍 심한 겨울 아침, 이 히터 하나면 방이 금방 따뜻해져요."},
    {"voiceover": "48시간 틀어 놔도 전기세가 거의 안 나옵니다."},                 # USP에 없는 수치 + 합쇼체
    {"voiceover": "과열 방지 기능이 있어서 밤새 켜 둬도 안심이 되는데,"},         # 연결 어미 → 종결
    {"voiceover": "2단 온도 조절이 돼서 상황 따라 쓰기 편해요."},                 # USP 근거 있는 수치 → 유지
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
check(g[0]["voiceover"] in ("이 히터 하나면 방이 금방 따뜻해져요!", "외풍 심한 겨울 아침, 이 히터 하나면 방이 금방 따뜻해져요.") and g[0]["hook_title"] == "겨울 난방비 고민 끝",
      "정상 대사·후킹 유지")
check("48시간" not in g[1]["voiceover"], "USP에 없는 '48시간' → 템플릿 대사로 교체")
check(len({s["voiceover"] for s in g}) == len(g), "대체된 씬끼리도 같은 문장 반복 없음")
check((g[2]["voiceover"].endswith("안심이 되는데요.") or g[2]["voiceover"].endswith("있어요!")) and g[3]["voiceover"] == "2단 온도 조절이 돼서 상황 따라 쓰기 편해요.",
      "연결 어미 종결 보정, USP 근거 있는 '2단'은 유지")
check(not find_promo_tone(g[4]["voiceover"]) and g[-1]["voiceover"] == COMMERCE_CTA and g[-1]["caption"] == COMMERCE_CTA.rstrip("!"),
      "홍보 어투 교체, CTA 고정")
check(all(not has_hapsyo(s["voiceover"]) and is_complete_sentence(s["voiceover"]) for s in g[:-1]) and g[0].get("story") == "…",
      "전 씬 해요체 완결 문장 + 전체 독백(story) 보관")

# 프롬프트: 상품 종류 맞춤, USP 근거, 사진 첨부
captured = {}


class FakeModels:
    def generate_content(self, **kw):
        if '규칙을 어겼습니다' not in str(kw.get('contents', [''])[-1]):
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
check(len(images) >= 4 and len(images) == sum(1 for c in captured["contents"] if isinstance(c, str) and c.startswith("[씬 ")) and any(isinstance(c, str) and c.startswith("[씬 1 상품 사진") for c in captured["contents"]), "Gemini에 실물 상품 사진을 씬마다 1장씩(앵글 컷 포함) 첨부")
check("상품 종류에 맞는 말" in prompt and "화장품이 아닌데 '보습·피부'" in prompt and "USP에 그대로 적힌 것만" in prompt
      and COMMERCE_CTA in prompt and "3자 큐레이터" in prompt and "엄격 금지" in prompt and HEATER_USP in prompt,
      "프롬프트: 상품 종류 맞춤(화장품 고정 예시 없음), 효능·수치는 USP 근거만, 3자 큐레이터·TPO 톤, 고정 CTA")

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

# ---------------------------------------------------------------- 4-1. 상품 카드 레이아웃 (상단 자막 + 중앙~하단 상품, 흐린 배경)
import imageio_ffmpeg
import cv2
from core.render.commerce_layout import COMMERCE_CARD_BOX


def card_photo(path, w, h):
    rng = np.random.default_rng(5)
    arr = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)               # 질감이 많은 '상품' 사진 (선명도 측정용)
    Image.fromarray(arr).save(path, "JPEG", quality=95)
    return path


def frames(path):
    r = imageio_ffmpeg.read_frames(path)
    next(r)
    return [np.frombuffer(f, np.uint8).reshape(1920, 1080, 3) for f in r]


def sharp_bbox(fr):
    """선명한(고주파) 영역의 범위 = 상품 카드 (배경은 크게 흐림)"""
    lap = np.abs(cv2.Laplacian(cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY), cv2.CV_32F))
    rows = np.where((lap > 30).mean(axis=1) > 0.2)[0] / 1920
    cols = np.where((lap > 30).mean(axis=0) > 0.1)[0] / 1080
    return rows.min(), rows.max(), cols.min(), cols.max()


cgen = AssetGenerator(cache_dir=tmp("commerce_card_"), mock=True, mode="commerce")
for label, (w, h) in (("세로 사진", (1200, 1500)), ("가로 사진", (1600, 900))):
    clip = os.path.join(cgen.cache_dir, f"card_{w}x{h}.mp4")
    cgen._create_product_card_video(card_photo(os.path.join(cgen.cache_dir, f"p_{w}.jpg"), w, h), 2.0, clip, motion_sec=1.5)
    fs = frames(clip)
    r0, r1, c0, c1 = sharp_bbox(fs[0])
    check(COMMERCE_CARD_BOX[1] - 0.01 <= r0 and r1 <= COMMERCE_CARD_BOX[3] + 0.01 and COMMERCE_CARD_BOX[0] - 0.01 <= c0
          and c1 <= COMMERCE_CARD_BOX[2] + 0.01,
          f"{label}: 상품 카드가 화면 {r0:.2f}~{r1:.2f}(세로)·{c0:.2f}~{c1:.2f}(가로) 안에 (자막 띠·하단 UI·우측 버튼 회피)")
    bg_lap = np.abs(cv2.Laplacian(cv2.cvtColor(fs[0][:int(0.28 * 1920)], cv2.COLOR_RGB2GRAY), cv2.CV_32F)).mean()
    card_lap = np.abs(cv2.Laplacian(cv2.cvtColor(fs[0][int(r0 * 1920):int(r1 * 1920), int(c0 * 1080):int(c1 * 1080)],
                                                 cv2.COLOR_RGB2GRAY), cv2.CV_32F)).mean()
    moved = float(np.abs(fs[0][int(r0 * 1920):int(r1 * 1920), int(c0 * 1080):int(c1 * 1080)].astype(int)
                         - fs[35][int(r0 * 1920):int(r1 * 1920), int(c0 * 1080):int(c1 * 1080)].astype(int)).mean())
    check(card_lap > bg_lap * 5 and moved > 5 and sharp_bbox(fs[35])[:2] == (r0, r1),
          f"{label}: 상품은 선명(배경 대비 {card_lap / max(bg_lap, 1e-6):.0f}배) + 카드 안 Ken Burns(변화 {moved:.0f}), 카드 위치 고정")

# ---------------------------------------------------------------- 4-2. 대표 사진 1장 → 다각도 (Smart Crop)
from PIL import ImageDraw
from core.render.commerce_layout import SHOT_SEQUENCE, assign_shots, product_bbox, shot_crop
prod = Image.new("RGB", (1000, 1000), (255, 255, 255))
ImageDraw.Draw(prod).rectangle([350, 150, 650, 850], fill=(230, 110, 60))         # 흰 배경 위 세로로 긴 상품
bx = product_bbox(prod)
check(all(abs(v - e) <= 12 for v, e in zip(bx, (350, 150, 651, 851))), f"상품 영역 감지 (흰 배경) {bx}")
crops = {s_: shot_crop(prod, s_) for s_ in SHOT_SEQUENCE}
check(crops["full"].size[1] > crops["center"].size[1] and abs(crops["top"].size[1] - 0.55 * 700) < 15
      and abs(crops["bottom"].size[1] - 0.55 * 700) < 15,
      f"앵글별 자르기: 전체 {crops['full'].size} > 중앙 줌인 {crops['center'].size}, 상단·하단 디테일 각 상품 높이 55%")
check(assign_shots(["a", "a", "a", "a", "a", "b"]) == ["full", "center", "top", "bottom", "full", "full"],
      "같은 사진 재사용 → 전체 → 중앙 줌인 → 상단 → 하단 순서, 새 사진은 전체 샷부터")
cdir = tmp("commerce_shot_")
prod.save(os.path.join(cdir, "prod.jpg"), quality=95)
sizes = {}
for s_ in ("full", "center"):
    clip = os.path.join(cdir, f"{s_}.mp4")
    cgen._create_product_card_video(os.path.join(cdir, "prod.jpg"), 1.0, clip, motion_sec=1.0, shot=s_)
    fr = frames(clip)[0]
    orange = (fr[:, :, 0] > 180) & (fr[:, :, 1] < 150) & (fr[:, :, 2] < 110)
    sizes[s_] = int(orange.sum())
check(sizes["center"] > sizes["full"] * 1.5, f"중앙 줌인 씬은 상품이 더 크게 보임 (상품 픽셀 {sizes['full']} → {sizes['center']})")

# 광고 문구가 박힌 대표 사진: 배경 판별로는 사진 전체가 잡힘 → Gemini가 준 상품 본체 위치(box)로 자름
ad = Image.new("RGB", (1000, 1000), (60, 30, 20))
dr = ImageDraw.Draw(ad)
dr.rectangle([120, 50, 460, 950], fill=(240, 240, 235))                            # 상품(왼쪽)
dr.rectangle([560, 300, 960, 700], fill=(255, 200, 0))                             # 광고 글씨 덩어리(오른쪽)
by_box = shot_crop(ad, "center", box=(0.12, 0.05, 0.46, 0.95))
yellow = sum(1 for px in by_box.getdata() if px[0] > 240 and px[1] > 180 and px[2] < 40)
check(yellow == 0 and by_box.size[0] < 500, f"상품 위치(box) 기준 자르기: 광고 글씨 영역 미포함 ({by_box.size})")
top = shot_crop(ad, "top", box=(0.12, 0.05, 0.46, 0.95))
check(top.size[0] / top.size[1] <= 1.31, f"상단 디테일이 가로로 긴 띠가 되지 않음 ({top.size})")
check(cm._valid_box([50, 120, 950, 460]) == (0.12, 0.05, 0.46, 0.95) and cm._valid_box([0, 0, 10, 10]) is None
      and cm._valid_box("x") is None, "Gemini 상품 위치 [ymin,xmin,ymax,xmax] 0~1000 → 비율 변환, 이상값은 무시")

# ---------------------------------------------------------------- 4-3. 특장점 자동 정리 (상품명·사진 근거만)
points_reply = {"product_box": [50, 120, 950, 460], "points": [{"text": "저소음 설계", "source": "name"}, {"text": "48시간 연속 보온", "source": "name"},
                           {"text": "특허받은 PTC 기술", "source": "name"}, {"text": "한 손에 드는 크기", "source": "photo"},
                           {"text": "꼭 사 보세요", "source": "name"}]}


class PtsModels:
    def generate_content(self, **kw):
        captured["pts"] = kw
        return type("R", (), {"text": json.dumps(points_reply, ensure_ascii=False)})()


gg.Client = lambda api_key=None: type("C", (), {"models": PtsModels()})()
pts = cm.suggest_selling_points("유파 PTC 전기히터 TSK-5318 저소음", fixture("photo_pool.jpg"), api_key="fake")
gg.Client = _orig_client
check(pts == ["저소음 설계", "한 손에 드는 크기"], f"특장점 자동 정리: 상품명에 없는 '48시간'·'특허'·홍보 어투는 버림 {pts}")
ptxt = [c for c in captured["pts"]["contents"] if isinstance(c, str)][-1]
check("상품명에 없는 숫자" in ptxt and any(isinstance(c, Image.Image) for c in captured["pts"]["contents"]),
      "특장점 프롬프트: 상품명·사진 근거만 + 대표 사진 첨부")
gg.Client = lambda api_key=None: type("C", (), {"models": PtsModels()})()
ana = cm.analyze_product_photo("유파 PTC 전기히터 TSK-5318 저소음", fixture("photo_pool.jpg"), api_key="fake")
gg.Client = _orig_client
check(ana["box"] == (0.12, 0.05, 0.46, 0.95) and ana["points"] == pts, "한 번의 호출로 특장점 + 상품 본체 위치")
check(cm.suggest_selling_points("유파 히터", None, api_key=None) == [] and cm.suggest_selling_points("유파 히터", mock=True, api_key="x") == [],
      "API 키 없음·mock → 빈값 (지어내지 않음)")

# ---------------------------------------------------------------- 4-4. Gemini 비전: 사진 종류 판정 → 씬 역할 1:1 매칭
from core.planners.commerce import classify_product_media, commerce_roles, match_scene_media
ana = [{"kind": "usage", "shows": "거실에 놓인 히터", "box": None}, {"kind": "detail", "shows": "리모컨 클로즈업", "box": None},
       {"kind": "overview", "shows": "히터 전체", "box": (0.1, 0.0, 0.5, 1.0)}]
m4 = match_scene_media(commerce_roles(4), ["u", "d", "o"], ana)
check([(m["path"], m["shot"]) for m in m4] == [("o", "full"), ("d", "full"), ("d", "center"), ("u", "full")]
      and m4[0]["box"] == (0.1, 0.0, 0.5, 1.0),
      "역할 매칭: 1번 전체 소개 → 2·3번 디테일(같은 사진 다른 앵글) → 4번 실사용 (올린 순서와 무관)")
m6 = match_scene_media(commerce_roles(6), ["u", "d", "o"], ana)
check(len({(m["path"], m["shot"]) for m in m6}) == 6 and m6[-1]["path"] == "u", "씬이 많으면 같은 컷 반복 없이 앵글을 바꿔 재사용, 마지막은 실사용")
check([m["path"] for m in match_scene_media(commerce_roles(4), ["a", "b"], [])] == ["a", "b", "a", "b"],
      "판정 없음(키 없음·mock) → 올린 순서대로 돌려 쓰기")


class KindModels:
    def generate_content(self, **kw):
        if '규칙을 어겼습니다' not in str(kw.get('contents', [''])[-1]):
            captured.setdefault("calls", []).append(kw)
        txt = [c for c in kw["contents"] if isinstance(c, str)][-1]
        if "어떤 컷으로 쓰기 좋은지" in txt:
            body = {"images": [{"index": 1, "kind": "usage", "shows": "거실에 놓인 히터", "product_box": [0, 100, 1000, 500]},
                               {"index": 2, "kind": "overview", "shows": "히터 정면 전체", "product_box": [0, 0, 1000, 1000]},
                               {"index": 3, "kind": "nonsense", "shows": "?", "product_box": "x"}]}
        else:
            body = reply
        return type("R", (), {"text": json.dumps(body, ensure_ascii=False)})()


gg.Client = lambda api_key=None: type("C", (), {"models": KindModels()})()
media3 = [fixture("photo_pool.jpg"), fixture("photo_room.jpg"), fixture("phone_landscape.mp4")]
kinds = classify_product_media(HEATER, media3, api_key="fake", thumb_dir=tmp("kind_thumb_"))
captured["calls"] = []
vplan = plan_commerce_storyboard(HEATER, HEATER_USP, media3, api_key="fake", thumb_dir=tmp("kind_thumb2_"))
gg.Client = _orig_client
check([k["kind"] for k in kinds] == ["usage", "overview", None] and kinds[0]["box"] == (0.1, 0.0, 0.5, 1.0) and kinds[2]["box"] is None,
      "사진 종류 판정 파싱 (이상한 종류·위치 값은 무시)")
check(vplan[0]["source_path"] == media3[1] and vplan[0]["media_kind"] == "overview" and vplan[-1]["source_path"] == media3[0]
      and vplan[0]["visual_description"] == "히터 정면 전체" and all(s.get("shot") for s in vplan),
      "콘티: 씬 1 = 전체 모습 사진, 마지막 = 실사용 사진, 씬마다 원본·앵글·화면 설명 기록")
plan_call = captured["calls"][-1]
labels = [c for c in plan_call["contents"] if isinstance(c, str) and c.startswith("[씬 ")]
check(labels and labels[0].startswith("[씬 1 상품 사진 - 전체 모습") and "1:1 매칭" in [c for c in plan_call["contents"] if isinstance(c, str)][-1],
      f"대본 프롬프트: 씬마다 실제로 나갈 화면(종류·앵글·설명)을 붙여 1:1로 쓰게 함 {labels[:2]}")

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
