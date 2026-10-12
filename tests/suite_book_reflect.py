"""시골책방 ③ 사유 스토리텔링(책소개 기반, 원문 없음) + ④ 씬별 이미지 매칭(책방 사진 라이브러리·AI 배정·흐린 표지 배경)"""
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
from core import book_scenes as bs
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
    if "장면 대사" in str(contents[-1]):
        return json.dumps({"assign": [{"scene": i + 1, "photo": [2, 1, 2, 1, 2, 1][i]} for i in range(6)]})
    if "사진에 보이는 것" in str(contents[-1]):
        return json.dumps({"photos": [{"index": 1, "desc": "나무 책장이 있는 방", "mood": "아늑함"},
                                      {"index": 2, "desc": "파란 수영장", "mood": "시원함"}]}, ensure_ascii=False)
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

# ---------------------------------------------------------------- ④ 씬별 이미지 매칭
_lib, _cache = os.environ.get("BOOK_LIBRARY_DIR"), bs.cache_path
os.environ["BOOK_LIBRARY_DIR"] = os.path.join(work, "lib_empty")
bs.cache_path = lambda: os.path.join(work, "tags.json")
scenes = json.loads(json.dumps(sb))
log = bs.assign_visuals(scenes, cover=COVER, page=None, event_photos=[], api_key=None, mock=True, out_dir=work)
bg = scenes[0]["source_path"]
moodim = np.array(Image.open(bg).convert("L")).astype(float)
check("흐리게" in log[0] and bg.endswith("book_mood_bg.jpg") and all(s["source_path"] == bg for s in scenes[:-1])
      and scenes[-1]["source_path"] == COVER and moodim.std() < 6,
      "책방 사진이 없으면 표지를 아주 흐리게 깐 배경 (글자 안 읽힘), 책 공개 장면만 진짜 표지")
lib = os.path.join(work, "lib")
os.makedirs(lib)
L1, L2 = shutil.copy(S1, os.path.join(lib, "01_서가.jpg")), shutil.copy(S2, os.path.join(lib, "02_창가.jpg"))
os.environ["BOOK_LIBRARY_DIR"] = lib
scenes = json.loads(json.dumps(sb))
log = bs.assign_visuals(scenes, cover=COVER, page=None, event_photos=[], api_key=None, out_dir=work)
paths = [s["source_path"] for s in scenes[:-1]]
check(paths == [L2, L1, L2, L1, L2, L1] and "AI가 대사에 맞춰 배정" in log[0] and "라이브러리 2장" in log[0],
      f"라이브러리 사진을 AI가 장면 대사에 맞춰 배정 {[os.path.basename(p) for p in paths]}")
tags = json.load(open(os.path.join(work, "tags.json"), encoding="utf-8"))
n_calls = len(seen["prompts"])
bs.describe_photos([L1, L2], api_key=None)
check(len(tags) == 2 and len(seen["prompts"]) == n_calls and "책장" in json.dumps(tags, ensure_ascii=False),
      "사진 설명은 한 번만 (캐시) → 다시 부르지 않음")
check(bs._no_repeats([0, 0, 1, 1], 2) == [0, 1, 0, 1], "같은 사진 연달아 금지")
PAGE = os.path.join(work, "page.jpg")
Image.new("RGB", (800, 600), (240, 235, 220)).save(PAGE)
fable = plan_book_storyboard("시골책방", "하늘과 바람과 별과 시", "윤동주", INFO, "죽는 날까지 하늘을 우러러 한 점 부끄럼이 없기를,",
                             media_paths=[COVER, PAGE], book_photo_count=2, mock=True, template="fable")
bs.assign_visuals(fable, cover=COVER, page=PAGE, event_photos=[], api_key=None, mock=True, out_dir=work)
check([s["source_path"] for s in fable if s["role"] == "quote"] == [PAGE] and fable[-1]["source_path"] == COVER
      and all(s["source_path"] in (L1, L2) for s in fable if s["role"] in ("hook", "situation", "comfort")),
      "펼친 면 사진(인용 배경)·책 공개 표지는 그대로, 책방·분위기 장면만 매칭")
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
    check(os.path.exists(res.final_path) and sc[0]["book"].get("visuals") and abs(px[0] - 246) < 30 and abs(px[2] - 224) < 35,
          f"엔진 렌더: 사유 장면에 종이 카드가 실제 영상 가운데 {tuple(px)} · 이미지 매칭 기록 {sc[0]['book'].get('visuals')}")
finally:
    yu.DRAFTS_PATH = _d
    bs.cache_path = _cache
    if _lib is None:
        os.environ.pop("BOOK_LIBRARY_DIR", None)
    else:
        os.environ["BOOK_LIBRARY_DIR"] = _lib
    shutil.rmtree(job.workdir, ignore_errors=True)
finish(fails)
