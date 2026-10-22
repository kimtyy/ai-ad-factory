import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish, fixture  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import re
import shutil

from core.compositor import AdCompositor
import core.render.caption_chunks as cc
from core.render.caption_chunks import CAPTION_ENGINE_VERSION, enforce_max_chunks, is_orphan

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


# ---------------------------------------------------------------- 1. 마지막 방어선: 어떤 분할기가 만든 덩어리든 문장당 최대 2덩어리·1어절 꼬투리 금지
CASES = [
    (["통창 너머 바다를", "보며 히노끼탕을 즐길 수", "있어요."], 2),
    (["히노끼탕까지 갖춘 강릉", "오션뷰 펜션,", "실화일까요?"], 2),
    (["주말에 오션뷰 펜션", "찾다가 가격 보고", "포기하셨죠!"], 2),
    (["히노끼탕까지 갖춘", "강릉 오션뷰 펜션, 실화일까요?"], 2),
    (["한 문장 통째로 길게 쓴 자막입니다"], 1),
    (["하나", "둘 셋 넷 다섯 여섯"], 1),
]
for chunks, want in CASES:
    out = enforce_max_chunks(chunks)
    ok = len(out) == want and " ".join(out).split() == " ".join(chunks).split() and not (len(out) > 1 and any(is_orphan(c) for c in out))
    check(ok, f"enforce_max_chunks {chunks} → {out}")
check(is_orphan("있어요.") and is_orphan("강릉 오션뷰 펜션") is False and is_orphan("가나 다라") and is_orphan("오션뷰 펜션"), "꼬투리 판별: 1어절·공백 제외 5자 이하")

# ---------------------------------------------------------------- 2. 옛 분할기(3등분)가 남아 있어도 실제 화면 덩어리는 규칙대로 (서버에 옛 모듈이 남던 사고 재현)
LINES = ["히노끼탕까지 갖춘 강릉 오션뷰 펜션, 실화일까요?", "통창 너머 바다를 보며 히노끼탕을 즐길 수 있어요.", "주말에 오션뷰 펜션 찾다가 가격 보고 포기하셨죠!",
         "히노끼탕과 해변 야경을 한숙소에서 함께 누려요.", "주말 특가 마감되기 전에 지금 저장하고 고정 댓글 최저가 링크 확인해 보세요!"]
OLD_SPLITS = {
    LINES[0]: ["히노끼탕까지 갖춘 강릉", "오션뷰 펜션,", "실화일까요?"],
    LINES[1]: ["통창 너머 바다를", "보며 히노끼탕을 즐길 수", "있어요."],
    LINES[2]: ["주말에 오션뷰 펜션", "찾다가 가격 보고", "포기하셨죠!"],
}
_orig = cc.semantic_split
cc.semantic_split = lambda text, fits=None, **k: OLD_SPLITS.get(text) or _orig(text, fits, **k)
os.environ["TRAVEL_STAY_STYLE"] = "1"
try:
    comp = AdCompositor(output_dir=tmp("capeng_"), font_dir=os.path.join(ROOT, "fonts"))
    comp.semantic_captions = True
    got = [comp.caption_chunks(t, "travel", 1080) for t in LINES]
    check(all(len(g) <= 2 for g in got) and all(len(g) == 2 for g in got[:3]), f"옛 3등분 분할기가 남아 있어도 화면 덩어리는 최대 2개: {[len(g) for g in got]}")
    check(not any(is_orphan(x) for g in got for x in g if len(g) > 1), f"1어절 꼬투리 없음: {got[:3]}")
    tl = comp.caption_timeline(LINES[1], "travel", 1080, 3.4, 3.6)
    check(len(tl) == 2 and tl[0][0] == 0.0 and abs(tl[-1][1] - 3.6) < 1e-6 and all(e - s >= 1.2 for s, e, _ in tl) and tl[0][1] == tl[1][0],
          f"caption_timeline: 글자 수 비례, 각 1.2초 이상, 마지막은 씬 끝까지: {[(round(s, 2), round(e, 2), c) for s, e, c in tl]}")
    check(comp.caption_timeline("짧은 문장이에요.", "travel", 1080, 3.4, 3.6) == [], "한 덩어리 문장은 타임라인 없음(통자막)")
finally:
    cc.semantic_split = _orig
    os.environ.pop("TRAVEL_STAY_STYLE", None)

# ---------------------------------------------------------------- 3. 단위 테스트가 검사하는 함수 = 실제 렌더러가 부르는 함수 (경로 일치)
src_ff = open(os.path.join(ROOT, "core", "render", "ffmpeg_assemble.py"), encoding="utf-8").read()
src_cp = open(os.path.join(ROOT, "core", "compositor.py"), encoding="utf-8").read()
check("comp.caption_timeline(" in src_ff and "caption_chunks(caption" not in src_ff and "chunk_windows" not in src_ff,
      "ffmpeg 조립: 자막 타임라인을 caption_timeline()으로만 만든다 (분할·시간 계산 직접 호출 없음)")
mp = src_cp[src_cp.index("def _assemble_moviepy"):]
check("self.caption_timeline(" in mp and "self.caption_chunks(caption_text" not in mp and "chunk_windows" not in mp,
      "MoviePy 조립: 같은 caption_timeline() 사용")
check(re.fullmatch(r"v\d+-\d{4}-\d{2}-\d{2}", CAPTION_ENGINE_VERSION) is not None, f"자막 엔진 버전 표시: {CAPTION_ENGINE_VERSION}")

# ---------------------------------------------------------------- 4. 실제 렌더(ffmpeg·MoviePy 두 경로)의 자막 이벤트 = 문장당 최대 2덩어리
import core.pipeline as pl
from core.job import JobContext
from core.planners import stay as sty
from core.render.brand import BrandOverlay
import core.render.cover_clip as ccv
ccv.INTRO_COVER_FORCE = False
import core.mode_policy as mp_mod
mp_mod.INTRO_COVER_FORCE = False

events = {}
orig_cw = cc.chunk_windows                      # pipeline.render가 compositor를 다시 읽으므로 클래스가 아니라 이 함수(렌더러가 부르는 시간 계산)를 감시


def spy(chunks, speech_sec, scene_sec):
    events.setdefault(os.environ.get("RENDER_ENGINE", "ffmpeg"), []).append(list(chunks))
    return orig_cw(chunks, speech_sec, scene_sec)


cc.chunk_windows = spy
facts = {"per_person": "4만 원대", "facilities": ["개별 온수풀", "리버뷰", "불멍", "바비큐"], "signature": "개별 온수풀", "people_used": 4}
try:
    for engine in ("ffmpeg", "moviepy"):
        os.environ["RENDER_ENGINE"] = engine
        os.environ["TRAVEL_STAY_STYLE"] = "1"
        W = tmp(f"capeng_{engine}_")
        job = JobContext.create(app="caption_engine")
        job.root = W
        job = JobContext(job.job_id, root=W)
        ups = []
        for i in range(5):
            dst = os.path.join(job.uploads_dir, f"u{i}.jpg")
            shutil.copy(fixture("photo_pool.jpg" if i % 2 == 0 else "photo_room.jpg"), dst)
            ups.append(dst)
        scenes = sty.plan_stay_storyboard("효정 리버뷰 풀빌라", "가평 리버뷰 독채풀빌라", "경기도 가평군", facts, ups, mock=True)
        for sc_, line in zip(scenes, LINES):
            sc_["voiceover"] = sc_["subtitle"] = sc_["caption"] = line
            sc_["hook_title"] = ""
        spec = pl.JobSpec(mode="travel", name="효정 리버뷰 풀빌라", usp="개별 온수풀", media_paths=ups, mock=True, audio_mode="silent", caption_font="serif",
                          brand=BrandOverlay(business_name="가평", show_label=False))
        pl.assign_uploads_to_scenes(job, scenes, ups, cycle=False)
        res = pl.render(spec, job, scenes)
        ev = events.get(engine, [])
        check(os.path.exists(res.final_path), f"[{engine}] 실제 렌더 완료")
        check(len(ev) >= 4 and all(len(e) == 2 for e in ev) and not any(is_orphan(x) for e in ev for x in e),
              f"[{engine}] 실제 렌더가 만든 자막 이벤트: 긴 문장 {len(ev)}개 모두 정확히 2덩어리·꼬투리 없음 → {ev[:2]}")
finally:
    cc.chunk_windows = orig_cw
    os.environ.pop("RENDER_ENGINE", None)
    os.environ.pop("TRAVEL_STAY_STYLE", None)

finish(fails)
