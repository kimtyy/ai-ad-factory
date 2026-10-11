"""쇼핑 3단 묶음 큐레이션: 대본 구조(훅 3초 → 상품 1·2·3 각 7초 → CTA 3초)·어미 리듬·상품별 근거 + 고정 바 + 첫 댓글 구매 좌표"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import json
import os
import shutil
import subprocess

import numpy as np
from PIL import Image

import core.llm.router as rt
from core.compositor import (AdCompositor, BUNDLE_BAR_X, BUNDLE_BOTTOM_CY, BUNDLE_CAPTION_CY, BUNDLE_TITLE_CY)
from core.planners.commerce_bundle import (BUNDLE_CTA, DEFAULT_BOTTOM_BAR, DEFAULT_HOOK, ORDINALS, TEMPLATE_TITLE,
                                           _item_media, bundle_title_ok, plan_bundle_storyboard, short_name)
from core.script_guard import ending_kind, find_banned_style
from core.shorts_meta import COUPANG_DISCLOSURE_FULL, NAVER_DISCLOSURE_FULL, build_first_comment, shop_link_kind

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


P1, P2 = fixture("photo_room.jpg"), fixture("photo_pool.jpg")
ITEMS = [{"name": "[한일] 미니 무선 핸디청소기 HC-2000 1개", "usp": "무선, 500g 초경량", "link": "https://link.coupang.com/a/aaa"},
         {"name": "LED 무드등 수면등", "usp": "3단 밝기 조절", "link": "https://smartstore.naver.com/shop/products/1"},
         {"name": "접이식 빨래 건조대 대형", "usp": "접이식", "link": "https://link.coupang.com/a/ccc"}]

# ---------------------------------------------------------------- 1. 상품명·사진 배정
check(short_name(ITEMS[0]["name"]) == "미니 무선 핸디청소기" and short_name("LED 무드등 수면등") == "LED 무드등 수면등",
      f"긴 쿠팡 상품명 → 짧은 이름 (브랜드 괄호·모델 코드·수량 제거) '{short_name(ITEMS[0]['name'])}'")
check(_item_media([{"media": ["c1.jpg"]}, {}, {}], ["c1.jpg", "u1.jpg", "u2.jpg", "u3.jpg"]) == [["c1.jpg"], ["u1.jpg", "u3.jpg"], ["u2.jpg"]],
      "사진 배정: 쿠팡으로 추가한 상품은 그 사진, 나머지 업로드 사진은 사진 없는 상품에 순서대로")
check(_item_media([{}, {}, {}], ["a", "b", "c"]) == [["a"], ["b"], ["c"]], "사진 3장 → 상품 1·2·3에 1장씩")

# ---------------------------------------------------------------- 2. 대본 (Claude 가짜 응답)
os.environ["ANTHROPIC_API_KEY"] = "test-claude"
seen = {"prompts": [], "n": 0}
reply = {"title_bar": "자취생 삶의 질 수직 상승템 3가지", "hook_title": "자취방 필수템 3가지",
         "scenes": [{"scene_index": 1, "voiceover": "이거 없이 어떻게 살았나 싶죠?"},
                    {"scene_index": 2, "voiceover": "첫 번째, 퇴근 후 바닥 먼지가 눈에 밟힐 때 딱이죠!"},
                    {"scene_index": 3, "voiceover": "500g이라 한 손으로 들고 구석까지 금방 끝나요."},
                    {"scene_index": 4, "voiceover": "두 번째, 잠들기 전 눈이 편한 조명이 필요할 때예요."},
                    {"scene_index": 5, "voiceover": "최저가에 업계 1위라 지금 사야 이득이에요."},           # 근거 없는 주장 → 템플릿
                    {"scene_index": 6, "voiceover": "마지막, 원룸에 빨래 널 자리가 없을 때 어때요?"},
                    {"scene_index": 7, "voiceover": "접어서 틈새에 쏙 넣으면 방이 넓어 보여요."}]}


def fake_claude(contents, system, temperature, max_tokens):
    seen["n"] += 1
    seen.setdefault("labels", []).append([c for c in contents if isinstance(c, str) and c.startswith("[상품")])
    seen["prompts"].append(contents[-1] if isinstance(contents[-1], str) else "")
    if "규칙을 어겼습니다" in str(contents[-1]):
        fix = [{"scene_index": 4, "voiceover": "두 번째, 잠들기 전 눈이 편한 조명 하나쯤 있으면 좋겠죠?"}] if "scene_index 4:" in str(contents[-1]) else []
        return json.dumps({"fixes": fix}, ensure_ascii=False)
    return json.dumps(reply, ensure_ascii=False)


rt._overrides = {"claude": fake_claude}
items = [dict(it) for it in ITEMS]
sb = plan_bundle_storyboard("자취생 삶의 질", items, media_paths=[P1, P2, P1], api_key=None)
vo = [s["voiceover"] for s in sb]
plan_prompt = seen["prompts"][0]
check(len(sb) == 8 and [s["role"] for s in sb] == ["hook", "feature", "detail", "feature", "detail", "feature", "detail", "cta"]
      and [s.get("item_index") for s in sb] == [None, 1, 1, 2, 2, 3, 3, None] and vo[-1] == BUNDLE_CTA,
      "구조: 훅 1씬 → 상품 1·2·3 × 2문장 → 구매 좌표 CTA (8씬)")
check(sb[0]["script_engine"] == "claude" and sb[0]["hook_title"] == "자취방 필수템 3가지"
      and sb[0]["bundle_title"] == "자취생 삶의 질 수직 상승템 3가지", "Claude 대본 + 훅 헤드라인 + 상단 타이틀 바 문구")
check(all(vo[1 + 2 * k].startswith(ORDINALS[k]) for k in range(3)), f"상품 첫 문장은 '첫 번째/두 번째/마지막'으로 시작 {vo[1::2]}")
check("최저가" not in vo[4] and "1위" not in vo[4] and not any(find_banned_style(v) for v in vo),
      f"상품 2의 USP에 없는 '최저가·1위' 문장은 템플릿으로 교체 ('{vo[4]}')")
check([s.get("source_path") for s in sb] == [P1, P1, P1, P2, P2, P1, P1, P1] and [s.get("shot") for s in sb[1:3]] == ["full", "center"],
      "상품별 사진만 그 상품 씬에 (사진 1장이면 풀샷 → 중앙 줌인), 훅 = 상품 1 사진, CTA = 마지막 상품 사진 (전 씬 지정)")
check(seen["labels"][0] == ["[상품 1 사진]", "[상품 2 사진]", "[상품 3 사진]"], "상품마다 사진을 엔진에 함께 전달 (상품 번호 표시)")
check("0~3초" in plan_prompt and "상품당 7초" in plan_prompt and "'첫 번째', '두 번째', '마지막'" in plan_prompt
      and "~어때요?" in plan_prompt and "~딱이죠!" in plan_prompt and "500g 초경량" in plan_prompt,
      "프롬프트: 3초 훅 → 상품당 7초 → CTA 구조 + 어미 리듬 규칙(질문형·감탄형) + 상품별 USP")
kinds = [ending_kind(v) for v in vo[:-1]]
check("question" in kinds and "exclaim" in kinds and not any(a == b == "plain" for a, b in zip(kinds, kinds[1:])),
      f"어미 리듬: 질문형·감탄형 포함 + 템플릿이 끼어도 평서 연속 없음 {kinds}")
names = [it["short"] for it in sb[0]["bundle_items"]]
check(names == ["미니 무선 핸디청소기", "LED 무드등 수면등", "접이식 빨래 건조대 대형"] and sb[0]["bundle_items"][1]["link"].startswith("https://smartstore"),
      "콘티에 상품별 짧은 이름·링크 기록 (첫 댓글 구매 좌표용)")

# 단조로운 대본 → 리듬 보정 요청 / 근거 없는 타이틀 → 템플릿 타이틀
flat = dict(reply, title_bar="최저가 자취템 3가지 품절 임박",
            scenes=[{"scene_index": i + 1, "voiceover": v} for i, v in enumerate([
                "자취하면 꼭 필요한 것들이에요.", "첫 번째, 퇴근 후 바닥 먼지가 신경 쓰일 때 좋아요.", "가벼워서 한 손으로 들기 편해요.",
                "두 번째, 잠들기 전 은은한 조명이 필요할 때예요.", "밝기를 세 단계로 바꿀 수 있어요.",
                "마지막, 원룸에 빨래 널 자리가 없을 때 좋아요.", "접어서 틈새에 넣을 수 있어요."])])
reply_backup, reply = reply, flat
seen.update(prompts=[], n=0)
sb2 = plan_bundle_storyboard("자취생", [dict(it) for it in ITEMS], media_paths=[P1, P2, P1], api_key=None)
reply = reply_backup
repair = [p for p in seen["prompts"] if "규칙을 어겼습니다" in p]
check(repair and "질문형" in repair[0] and "감탄·확신형" in repair[0], "질문·감탄 없는 '~요.' 일색 → 어미 방향을 지정해 다시 쓰게 함")
check(sb2[0]["bundle_title"] == TEMPLATE_TITLE.format(theme="자취생"), f"'최저가·품절' 타이틀은 버리고 템플릿 '{sb2[0]['bundle_title']}'")

# 엔진이 모두 실패하면 템플릿 (구조 유지)
rt._overrides = {"claude": lambda *a: (_ for _ in ()).throw(RuntimeError("rate limit"))}
os.environ["OPENAI_API_KEY"], os.environ["GEMINI_API_KEY"] = "", ""
sb3 = plan_bundle_storyboard("자취생", [dict(it) for it in ITEMS], media_paths=[P1, P2, P1], api_key=None)
rt._overrides = {}
os.environ["ANTHROPIC_API_KEY"] = ""
check(sb3[0]["script_engine"] == "template" and len(sb3) == 8 and sb3[0]["hook_title"] == DEFAULT_HOOK
      and all(sb3[1 + 2 * k]["voiceover"].startswith(ORDINALS[k]) for k in range(3))
      and not any(a == b == "plain" for a, b in zip(*[[ending_kind(s["voiceover"]) for s in sb3[:-1]][o:] for o in (0, 1)])),
      f"엔진 전부 실패 → 서수가 붙은 템플릿 대본 (구조 유지, 평서 연속 없음) {[ending_kind(s['voiceover']) for s in sb3[:-1]]}")
try:
    plan_bundle_storyboard("x", ITEMS[:2], media_paths=[P1, P2], mock=True)
    check(False, "상품 2개")
except ValueError as e:
    check("3개" in str(e), "상품이 3개가 아니면 기획 안 함")

# ---------------------------------------------------------------- 3. 고정 바 (상단 타이틀 / 하단 구매 안내)
comp = AdCompositor(output_dir=tmp())
W, H = 1080, 1920
L = np.array(comp.render_bundle_bars("자취생 삶의 질 수직 상승템 3가지", DEFAULT_BOTTOM_BAR, W, H))
ty, by = int(H * BUNDLE_TITLE_CY), int(H * BUNDLE_BOTTOM_CY)
edge = int(W * BUNDLE_BAR_X[0]) + 12
check(tuple(L[ty, edge][:3]) == (255, 214, 10) and L[ty, edge][3] > 200, "상단 타이틀 바: 노란 바 (화면 위쪽 13.8%)")
check(L[by, edge][3] > 180 and max(L[by, edge][:3]) < 40, "하단 구매 안내 바: 검은 바 (상품 카드 아래 75%)")
no_emoji = np.array(comp.render_bundle_bars("자취생 삶의 질 수직 상승템 3가지", DEFAULT_BOTTOM_BAR.replace("🛒 ", ""), W, H))
check(comp._emoji_image("🛒", 40) is not None and not np.array_equal(L[by - 30:by + 30], no_emoji[by - 30:by + 30]),
      "하단 바 맨 앞 🛒는 이모지 글꼴로 그림 (빈 네모 아님)")
check(L[H // 2, W // 2][3] == 0 and L[int(H * 0.05), W // 2][3] == 0, "상품 카드·맨 위(제휴 표시 자리)는 비움")
check(bundle_title_ok("🛒 최저가 구매 좌표는 고정 댓글 확인!") == "최저가" and bundle_title_ok(DEFAULT_BOTTOM_BAR) is None,
      "하단 바 기본 문구엔 근거 없는 '최저가' 없음 (입력하면 경고)")

# ---------------------------------------------------------------- 4. 첫 댓글 구매 좌표 + 제휴 고지
cm = build_first_comment("commerce", "셋 중 뭐부터 살래요? 👇", ["#자취템", "#살림템", "#꿀템", "#x"], items=sb[0]["bundle_items"])
blocks = cm.split("\n\n")
check(blocks[0] == "셋 중 뭐부터 살래요? 👇" and blocks[1].splitlines() == [
    "🛒 구매 좌표", "1️⃣ 미니 무선 핸디청소기 👉 https://link.coupang.com/a/aaa",
    "2️⃣ LED 무드등 수면등 👉 https://smartstore.naver.com/shop/products/1", "3️⃣ 접이식 빨래 건조대 대형 👉 https://link.coupang.com/a/ccc"],
      "첫 댓글: 참여 질문 → 상품별 번호 + 짧은 이름 + 제휴 링크")
check(blocks[2].splitlines() == [COUPANG_DISCLOSURE_FULL, NAVER_DISCLOSURE_FULL] and blocks[3] == "#자취템 #살림템 #꿀템"
      and COUPANG_DISCLOSURE_FULL.startswith("이 포스팅은 쿠팡 파트너스 활동의 일환으로"),
      "링크 종류별 공정위 고지 자동 (쿠팡 파트너스 원문 + 스마트스토어) 한 번씩 → 해시태그")
check([shop_link_kind(u) for u in ("https://link.coupang.com/a/x", "https://coupa.ng/abc", "https://smartstore.naver.com/a",
                                    "https://naver.me/x", "https://example.com")] == ["coupang", "coupang", "naver", "naver", "other"],
      "링크 종류 판별 (쿠팡·쿠팡 단축 / 스마트스토어·naver.me / 기타)")
only_cp = build_first_comment("commerce", "q?", [], items=[{"short": "a", "link": "https://link.coupang.com/a/1"}])
check(NAVER_DISCLOSURE_FULL not in only_cp and COUPANG_DISCLOSURE_FULL in only_cp, "쿠팡 링크만 있으면 쿠팡 고지만")

# ---------------------------------------------------------------- 5. 앱: 묶음 구성 → 기획 → 렌더 (Mock, 고정 바가 실제 영상에)
from streamlit.testing.v1 import AppTest  # noqa: E402
os.environ["YOUTUBE_API_KEY"] = ""
os.environ["COUPANG_ACCESS_KEY"] = ""
jobs = []
try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=600).run()
    jobs.append(at.session_state.job.workdir)
    at.session_state.mode_switch = "🛒 쇼핑 제휴 쇼츠"
    at.session_state.production_mode = "commerce"
    at.run()
    at.sidebar.checkbox[0].check().run()                                   # Mock
    at.radio(key="commerce_layout").set_value("bundle").run()
    check(any(t.key == "bundle_name_2" for t in at.text_input) and not any(t.key == "product_name_input" for t in at.text_input),
          "쇼핑 구성 스위치 → 묶음 상품 3칸 (단일 상품 입력칸 대신)")
    at.text_input(key="bundle_theme").input("자취생 삶의 질").run()
    for k, it in enumerate(ITEMS):
        at.text_input(key=f"bundle_name_{k}").input(it["name"]).run()
        at.text_input(key=f"bundle_usp_{k}").input(it["usp"]).run()
        at.text_input(key=f"bundle_link_{k}").input(it["link"]).run()
    _t = tmp()
    ups = [shutil.copy(P1, os.path.join(_t, "a.jpg")), shutil.copy(P2, os.path.join(_t, "b.jpg")), shutil.copy(P1, os.path.join(_t, "c.jpg"))]
    at.session_state.uploaded_paths = ups
    at.run()
    [b for b in at.button if "영상 만들기" in b.label][0].click().run()
    jobs.append(at.session_state.job.workdir)
    sbx = at.session_state.storyboard
    final = at.session_state.final_video_path
    check(not at.exception and len(sbx) == 8 and sbx[0].get("bundle_title") and bool(final) and os.path.exists(final),
          f"[영상 만들기] → 묶음 기획 → 렌더 완료 ({len(sbx)}씬)")
    check([s.get("image_path") for s in sbx] == [ups[0], ups[0], ups[0], ups[1], ups[1], ups[2], ups[2], ups[2]],
          "앱에서도 씬 사진 = 그 상품 사진 (업로드 순번 배정으로 섞이지 않음)")
    if final and os.path.exists(final):
        from core.render.ffmpeg_assemble import ffmpeg_exe, probe
        png = os.path.join(tmp(), "f.png")
        subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", "8", "-i", final, "-frames:v", "1", png], check=True)
        fr = np.array(Image.open(png).convert("RGB"))
        fh, fw = fr.shape[:2]
        px = fr[int(fh * BUNDLE_TITLE_CY), int(fw * BUNDLE_BAR_X[0]) + 8]
        pb = fr[int(fh * BUNDLE_BOTTOM_CY), int(fw * BUNDLE_BAR_X[0]) + 8]
        dur = probe(final)["duration"]
        check(px[0] > 200 and px[1] > 170 and px[2] < 90 and max(pb) < 60,
              f"완성 영상 8초 지점에 상단 노란 타이틀 바·하단 검은 구매 바가 고정 {tuple(px)} {tuple(pb)}")
        check(20 <= dur <= 40, f"영상 길이 {dur:.1f}초 (Mock 음성 기준, 실제 대본은 약 30초)")
    codes = " ".join(c.value or "" for c in at.code)
    check("1️⃣" in codes and "smartstore" in codes and "쿠팡 파트너스 활동의 일환으로" in codes and "스마트스토어 제휴" in codes,
          "완료 화면: 고정 댓글용 구매 좌표 + 링크별 고지 복사 칸")
    at.radio(key="commerce_layout").set_value("single").run()
    [b for b in at.button if "영상 만들기" in b.label][0].click().run()
    check(any("다시 기획" in (e.value or "") for e in at.error), "묶음으로 기획한 콘티를 단일 상품 구성으로 렌더하면 막고 재기획 안내")
finally:
    for j in set(jobs):
        shutil.rmtree(j, ignore_errors=True)
finish(fails)
