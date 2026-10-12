"""시골책방 ③ 사유 스토리텔링(책소개 기반, 원문 없음) + ④ 씬별 AI 이미지 생성(대본 → 영문 프롬프트 → 9:16 일러스트, 마지막 씬만 표지)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402
import json
import os
import shutil
import subprocess

import numpy as np
from PIL import Image, ImageDraw

import core.llm.router as rt
from core import scene_images as si
from core.planners.book import REFLECT_MIN_INFO, plan_book_storyboard
from core.planners.book_fable import FABLE_CTA
from core.planners.book_reflect import MESSAGE_HEADER

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


work = tmp()
COVER = os.path.join(work, "cover.jpg")
im = Image.new("RGB", (600, 900), (230, 220, 200))
ImageDraw.Draw(im).text((60, 120), "TITLE TEXT", fill=(10, 10, 10))
im.save(COVER)
S1, S2 = fixture("photo_room.jpg"), fixture("photo_pool.jpg")
INFO = ("2022년 윤동주 서거 77주년과 탄생 105주년을 맞아 새롭게 편집한 윤동주 전 시집. 윤동주 시인의 시와 산문 전체, "
        "그를 위해 쓰인 서문과 발문까지 한 권에 담았다. 식민지 시대 청년의 부끄러움과 성찰을 맑은 언어로 노래한다.")

# ---------------------------------------------------------------- ③ 사유 스토리텔링 (가짜 Claude)
os.environ["ANTHROPIC_API_KEY"] = "test-claude"
seen = {"prompts": [], "labels": []}
reply = {"hook_title": "부끄럼이 많은 날의 책",
         "scenes": [{"scene_index": 1, "voiceover": "괜히 나만 뒤처진 것 같은 밤 있죠?"},
                    {"scene_index": 2, "voiceover": "남들 앞에선 웃었는데 집에 오면 하루가 부끄럽게만 느껴지는 순간이 있어요."},
                    {"scene_index": 3, "voiceover": "이 시집은 한 청년이 스스로를 돌아보며 부끄러움을 성찰로 바꿔 가는 과정을 담았어요."},
                    {"scene_index": 4, "voiceover": "\"별을 노래하는 마음\"처럼 맑은 언어가 이어지는 윤동주의 시집이죠!"},   # 따옴표+저자 노출 → 템플릿
                    {"scene_index": 5, "voiceover": "시뿐 아니라 산문과 그를 위해 쓰인 글까지 한 권에 모여 있어 천천히 곁에 두기 좋아요."},
                    {"scene_index": 6, "voiceover": "오늘의 부끄러움도 내일을 바르게 걷게 하는 마음일지 몰라요, 천천히 가도 돼요."}]}


def fake_claude(contents, system, temperature, max_tokens):
    seen["prompts"].append(contents[-1] if isinstance(contents[-1], str) else "")
    seen["labels"].append([c for c in contents if isinstance(c, str) and c.startswith("[")])
    if "규칙을 어겼습니다" in str(contents[-1]):
        return json.dumps({"fixes": []}, ensure_ascii=False)
    if "Narration lines" in str(contents[-1]):
        return json.dumps({"style": "gouache illustration, muted palette, soft dusk light",
                           "scenes": [{"scene_index": i + 1, "prompt": f"A lone figure seen from behind, scene {i + 1}, quiet hill at dusk"}
                                      for i in range(6)]})
    return json.dumps(reply, ensure_ascii=False)


rt._overrides = {"claude": fake_claude}
sb = plan_book_storyboard("시골책방", "하늘과 바람과 별과 시", "윤동주", INFO, "", media_paths=[COVER, S1, S2], api_key=None,
                          template="reflect")
vo = [s["voiceover"] for s in sb]
check([s["role"] for s in sb] == ["hook", "situation", "message", "message", "message", "comfort", "cta"]
      and sb[0]["book"]["template"] == "reflect", "사유 구조: 훅·상황 → 이 책이 건네는 이야기 3 → 위로 → 책 공개")
msgs = [s for s in sb if s["role"] == "message"]
check(all(s["card_header"] == MESSAGE_HEADER and s["quote_source"] == "" and s["quote_card"] == s["voiceover"] for s in msgs),
      "이 책이 건네는 이야기: 종이 카드 + 머리말, 출처 없음 (책 속 문장 인용이 아님)")
check(not any(ch in v for v in vo[:-1] for ch in "\"“”「」") and "윤동주" not in " ".join(vo[:-1]) and "하늘과 바람" not in " ".join(vo[:-1]),
      f"따옴표 인용·제목·저자가 든 대사는 템플릿으로 (마지막 장면 전 미공개) '{vo[3]}'")
check(vo[-1] == FABLE_CTA.format(title="하늘과 바람과 별과 시", store="시골책방") and all(not s.get("tts_rate") for s in sb),
      "엔딩은 지정 CTA로 책 공개, 낭독 원문이 없으니 전 장면 기본 속도")
p0 = seen["prompts"][0]
check(INFO in p0 and "따옴표로 인용하지 마세요" in p0 and "마지막에만 공개" in p0 and "[책 사진]" not in seen["labels"][0],
      "사유 프롬프트: 책소개 사실만·인용 금지·제목 마지막 공개, 책 사진은 안 보냄")
try:
    plan_book_storyboard("시골책방", "책", "", "짧은 소개", "", media_paths=[COVER], mock=True, template="reflect")
    check(False, "책소개 부족")
except ValueError as e:
    check("책소개" in str(e), f"책소개가 {REFLECT_MIN_INFO}자 미만이면 사유 템플릿을 시작하지 않음")

# ---------------------------------------------------------------- ④ 씬별 AI 이미지 생성
lines = [{"scene_index": s_["scene_index"], "role": s_["role"], "text": s_["voiceover"]} for s_ in sb if s_["role"] != "cta"]
plan = si.build_prompts(lines, {"info": INFO}, api_key=None)
pp = seen["prompts"][-1]
check(plan["engine"] == "claude" and plan["style"].startswith("gouache") and plan["prompts"][1].startswith("A lone figure")
      and len(plan["prompts"]) == 6 and "never a specific real person's face" in pp and "no text" in pp and INFO[:30] in pp,
      "대본 6줄 → 대본 엔진이 공통 화풍 + 씬별 영문 프롬프트 (실존 인물 얼굴·책 표지·글자 금지 지시)")
fp = si.full_prompt(plan["style"], plan["prompts"][1])
check("9:16" in fp and "no text" in fp and "no book covers" in fp and "no recognizable real people" in fp,
      "모든 이미지 프롬프트 끝에 9:16·글자/로고/책 표지/실존 인물 금지 고정 문구")

sent = []


class FakeImgClient:
    class models:
        @staticmethod
        def generate_content(model, contents, config):
            import io as _io
            sent.append(("content", model, config))
            b = _io.BytesIO()
            Image.new("RGB", (1536, 2752), (90, 80, 70)).save(b, "JPEG")
            part = type("P", (), {"inline_data": type("D", (), {"data": b.getvalue(), "mime_type": "image/jpeg"})()})()
            return type("R", (), {"candidates": [type("C", (), {"content": type("Ct", (), {"parts": [part]})()})()]})()

        @staticmethod
        def generate_images(model, prompt, config):
            import io as _io
            sent.append(("images", model, config))
            b = _io.BytesIO()
            Image.new("RGB", (768, 1344), (10, 20, 30)).save(b, "JPEG")
            img = type("I", (), {"image": type("X", (), {"image_bytes": b.getvalue()})()})()
            return type("R", (), {"generated_images": [img]})()


_client = si._client
si._client = lambda api_key=None: FakeImgClient()
out1 = si.generate_one("test prompt", os.path.join(work, "g1.jpg"))
kind, model, cfg = sent[-1]
check(kind == "content" and model == "gemini-3.1-flash-image" and cfg.image_config.aspect_ratio == "9:16"
      and cfg.image_config.image_size == "1K" and os.path.exists(out1),
      "기본 모델: 기존 GEMINI_API_KEY로 gemini-3.1-flash-image (Imagen 3는 이 키로 404), 9:16·1K(768x1376, 비용·속도)")
from core import pipeline as _pl  # noqa: E402
from core.shorts_meta import AI_ILLUSTRATION_NOTICE, make_shorts_copy  # noqa: E402
_bd = _pl.upload_texts(_pl.JobSpec(mode="book", name="시골책방"), make_shorts_copy("book", mock=True))["desc"]
_cd = _pl.upload_texts(_pl.JobSpec(mode="commerce", name="x", link="https://link.coupang.com/a/x"),
                       make_shorts_copy("commerce", mock=True))["desc"]
check(AI_ILLUSTRATION_NOTICE in _bd and _bd.splitlines()[1] == AI_ILLUSTRATION_NOTICE and AI_ILLUSTRATION_NOTICE not in _cd,
      "유튜브 설명란: 시골책방(AI 일러스트)은 'AI 생성 일러스트 사용' 자동, 실물 사진만 쓰는 쇼핑·사장님은 안 붙임")
os.environ["IMAGE_MODEL"] = "imagen-3.0-generate-002"
si.generate_one("test prompt", os.path.join(work, "g2.jpg"))
check(sent[-1][0] == "images" and sent[-1][1] == "imagen-3.0-generate-002" and sent[-1][2].aspect_ratio == "9:16",
      ".env IMAGE_MODEL=imagen-* 로 바꾸면 Imagen API 경로로 호출")
os.environ["IMAGE_MODEL"] = "dall-e-3"
try:
    si.generate_one("x", os.path.join(work, "g3.jpg"))
    check(False, "허용 안 된 모델")
except Exception as e:
    check("dall-e-3" in str(e) or "허용" in str(e) or "GUARDRAIL" in str(e).upper(), "허용 목록 밖 이미지 모델은 가드레일이 차단")
os.environ.pop("IMAGE_MODEL", None)
si._client = _client

gen_dir = os.path.join(work, "ai")
r1 = si.generate_scene_images(sb, gen_dir, {"info": INFO}, mock=True)
mt = {k: os.path.getmtime(v) for k, v in r1["images"].items()}
r2 = si.generate_scene_images(sb, gen_dir, {"info": INFO}, mock=True)
check(sorted(r1["images"]) == [1, 2, 3, 4, 5, 6] and all(Image.open(v).size == (768, 1376) for v in r1["images"].values())
      and {k: os.path.getmtime(v) for k, v in r2["images"].items()} == mt
      and os.path.exists(os.path.join(gen_dir, "ai_scene_prompts.json")),
      "CTA를 뺀 씬마다 이미지 1장 (같은 프롬프트는 다시 안 만듦) + 프롬프트 기록 파일")
rt._overrides = {}
os.environ["ANTHROPIC_API_KEY"] = ""

# ---------------------------------------------------------------- 캘린더 템플릿 자동 선택
from core import scheduler as sch  # noqa: E402
auto = lambda f: sch.spec_from_event({"mode": "book", "keyword": "x"}, {"책": "x", "자동수집": "끔", **f}, None, mock=True)  # noqa: E731
a1, a2, a3 = auto({"문장": "원문"}), auto({"소개": INFO}), auto({})
a4 = auto({"템플릿": "우화", "소개": INFO})
check((a1.book_template, a2.book_template, a3.book_template, a4.book_template) == ("fable", "reflect", "basic", "reflect")
      and any("사유 스토리텔링으로" in n for n in a4.notes),
      "캘린더 템플릿 자동: 원문 → 우화 / 책소개 → 사유 / 둘 다 없음 → 기본 (우화 요청인데 원문 없으면 사유로 + 안내)")

# ---------------------------------------------------------------- 엔진 렌더 (Mock): 사유 카드가 실제 영상에
from core import pipeline  # noqa: E402
from core.job import JobContext  # noqa: E402
import core.youtube_upload as yu  # noqa: E402
_d = yu.DRAFTS_PATH
yu.DRAFTS_PATH = os.path.join(work, "drafts.json")
job = JobContext.create(app="test")
try:
    spec = pipeline.JobSpec(mode="book", name="시골책방", book_title="하늘과 바람과 별과 시", book_author="윤동주", usp=INFO,
                            book_publisher="B출판",
                            book_template="reflect", media_paths=[shutil.copy(COVER, os.path.join(work, "c2.jpg"))], mock=True)
    sc = pipeline.plan(spec, job)
    res = pipeline.render(spec, job, sc)
    from core.render.ffmpeg_assemble import ffmpeg_exe, probe  # noqa: E402
    starts, t = [], 0.0
    for s in sc:
        starts.append(t)
        t += (probe(os.path.join(job.assets_dir, f"scene_{s['scene_index']:02d}.mp3"))["duration"] or 0) + 0.2
    png = os.path.join(work, "m.png")
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", f"{starts[2] + 1.6:.2f}", "-i", res.final_path,
                    "-frames:v", "1", png], check=True)
    f = np.array(Image.open(png).convert("RGB")).astype(int)
    px = f[int(f.shape[0] * 0.52), int(f.shape[1] * 0.12)]
    check(os.path.exists(res.final_path) and abs(px[0] - 246) < 30 and abs(px[2] - 224) < 35,
          f"엔진 렌더: 사유 장면에 종이 카드가 실제 영상 가운데 {tuple(px)}")
    non_cta = [x for x in sc if x["role"] != "cta"]
    cta = sc[-1]
    check(all("ai_images" in x["source_path"] and x["layout"] == "full" and x["media_kind"] == "ai_image" for x in non_cta)
          and cta["source_path"].endswith("c2.jpg") and cta["layout"] == "book_quote" and cta["info_line"] == "윤동주 · B출판"
          and "AI 일러스트 6장" in sc[0]["book"]["visuals"][0],
          "씬 1~6 = 생성한 AI 일러스트(전체 화면), 마지막 씬만 진짜 표지 카드 + 책 정보(저자·출판사)")
    # 일부 생성 실패 → 앞 장면 그림 재사용
    _gen = si.generate_scene_images
    si.generate_scene_images = lambda scenes, out_dir, **kw: {**_gen(scenes, out_dir, **kw),
                                                              "images": {k: v for k, v in _gen(scenes, out_dir, **kw)["images"].items() if k != 3},
                                                              "errors": ["씬 3: 안전 필터"]}
    sc2 = json.loads(json.dumps(sc))
    pipeline.apply_book_visuals(spec, job, sc2)
    si.generate_scene_images = _gen
    check(sc2[2]["source_path"] == sc2[1]["source_path"] and "실패 1장" in sc2[0]["book"]["visuals"][0],
          "이미지 일부가 실패하면 앞 장면 그림을 다시 쓰고 기록")
finally:
    yu.DRAFTS_PATH = _d
    shutil.rmtree(job.workdir, ignore_errors=True)
finish(fails)
