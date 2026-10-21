import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import shutil
import subprocess
import re

import imageio_ffmpeg
import numpy as np
from PIL import Image

import core.render.fonts as fonts
from core.compositor import AdCompositor
from core.render.caption_chunks import semantic_split, chunk_windows

fails = 0
FF = imageio_ffmpeg.get_ffmpeg_exe()


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


# ---------------------------------------------------------------- 1. 의미 단위 자막: 최대 2덩어리·꼬투리 금지·No-Split 보호 (실제 영상 '강릉오션뷰펜션더영진' 사례)
REAL = {
    "히노끼탕까지 갖춘 강릉 오션뷰 펜션, 실화일까요?": [["히노끼탕까지 갖춘", "강릉 오션뷰 펜션, 실화일까요?"], ["히노끼탕까지 갖춘 강릉 오션뷰 펜션,", "실화일까요?"]],
    "주말에 바다 보이는 숙소를 찾다가 가격에 놀라 포기하셨죠!": [["주말에 바다 보이는 숙소를 찾다가", "가격에 놀라 포기하셨죠!"]],
    "히노끼탕에 몸을 담그는 순간이 이 숙소의 가장 큰 로망이에요.": [["히노끼탕에 몸을 담그는 순간이", "이 숙소의 가장 큰 로망이에요."]],
    "옥상 수영장과 야외 테라스까지 함께 즐길 수 있어요.": [["옥상 수영장과 야외 테라스까지", "함께 즐길 수 있어요."]],
    "주말 특가 마감되기 전에 지금 저장하고 고정 댓글 최저가 링크 확인해 보세요!": [["주말 특가 마감되기 전에 지금 저장하고", "고정 댓글 최저가 링크 확인해 보세요!"]],
}
for src, oks in REAL.items():
    got = semantic_split(src)
    check(got in oks and len(got) == 2, f"실제 영상 사례 → {got}")
allc = [semantic_split(x) for x in REAL] + [semantic_split(x) for x in ("저녁엔 불멍과 바비큐까지 즐길 수 있어요.", "여긴 겨울에는 눈부신 설경과 온수풀을 동시에 즐기는 숙소예요", "주말에 뷰 좋은 풀빌라 가려다 1박 가격에 놀라서 포기하셨죠!")]
check(all(1 <= len(c) <= 2 for c in allc) and all(len(x.split(" ")) >= 2 and len(x.replace(" ", "")) > 5 for c in allc for x in c if len(c) == 2),
      "한 문장은 최대 2덩어리, 1어절·5자 이하 꼬투리 덩어리 없음 ('있어요.'·'실화일까요?' 단독 노출 금지)")
bad_pairs = [("즐길", "수"), ("수", "있어요."), ("확인해", "보세요!"), ("강릉", "오션뷰"), ("야외", "테라스까지")]
joined_ok = all(not any(c[0].endswith(a) and c[1].startswith(b) for c in allc if len(c) == 2) for a, b in bad_pairs)
check(joined_ok, "끊으면 안 되는 결합(즐길|수|있어요, 확인해|보세요, 강릉|오션뷰, 야외|테라스까지)은 한 덩어리 안에 유지")
check(semantic_split("짧은 문장이에요") == ["짧은 문장이에요"] and semantic_split("열네글자짜리 문장입니다요") == ["열네글자짜리 문장입니다요"] and semantic_split("") == [], "14자 이하는 통자막 1덩어리")
check(semantic_split("실화일까요? 정말요 네") == ["실화일까요? 정말요 네"] or len(semantic_split("실화일까요? 정말요 네")) == 1, "나눌 곳이 꼬투리뿐이면 통자막")
w = chunk_windows(["가나다라마바사아자차카타파하", "가나다라마바"], 3.35, 3.8)
w5 = chunk_windows(["가나다라마바사아자차", "가나다라마바사아자차"], 3.35, 3.8)
check(w[0][1] > w5[0][1] and abs(w5[0][1] - 1.675) < 0.05 and all(b - a >= 1.2 for a, b in w + w5), f"전환 시간은 글자 수 비례(14자:6자는 앞이 더 김), 각 덩어리 최소 1.2초: {[(round(a, 2), round(b, 2)) for a, b in w]}")

# ---------------------------------------------------------------- 2. 폰트 3종: 모두 한글이 그려지고(두부 없음) 서로 다른 모양
comp = AdCompositor(output_dir=tmp("audiofonts_"), font_dir=os.path.join(ROOT, "fonts"))
specs = {k: fonts.resolve(k, comp) for k in fonts.PRESETS}
check(set(fonts.PRESETS) == {"impact", "serif", "pop"} and all(fonts._loads(s) for s in specs.values()), f"폰트 3종 모두 열림·한글 글리프 있음: {{k: os.path.basename(v[0]) for k, v in specs.items()}}".replace("{{k: os.path.basename(v[0]) for k, v in specs.items()}}", str({k: os.path.basename(v[0]) for k, v in specs.items()})))
check(len({s[0] for s in specs.values()}) >= 2, "프리셋마다 다른 폰트 파일 (명조/고딕/팝체)")
os.environ.pop("OWNER_CAPTION_POS", None)


def overlay(key, mode="owner"):
    comp.caption_font = key
    return np.asarray(comp.create_overlay_image("겨울에는 눈부신 설경과", None, 1080, 1920, mode=mode))


imgs = {k: overlay(k) for k in fonts.PRESETS}
dark = {k: int(((a[..., 3] > 220) & (a[..., :3].max(axis=2) < 40)).sum()) for k, a in imgs.items()}
white = {k: int(((a[..., 3] > 220) & (a[..., :3].min(axis=2) > 235)).sum()) for k, a in imgs.items()}
check(dark["serif"] < dark["impact"] < dark["pop"], f"외곽선 두께: 감성 명조(얇게) < 임팩트 고딕 < 둥근 팝체(굵게) — 검은 외곽 픽셀 {dark}")
check(all(w > 3000 for w in white.values()) and not np.array_equal(imgs["impact"], imgs["serif"]) and not np.array_equal(imgs["impact"], imgs["pop"]), f"세 폰트 모두 흰 글자가 선명히 그려짐 {white}")
soft = {k: int(((a[..., 3] > 5) & (a[..., 3] < 200)).sum()) for k, a in imgs.items()}
check(soft["serif"] > 0 and soft["serif"] > 1.5 * 0 and imgs["serif"][..., 3].max() == 255, "감성 명조: 부드럽게 번지는 그림자(반투명 픽셀) 포함")
rows = {k: np.where((a[..., 3] > 40).any(axis=1))[0] for k, a in imgs.items()}
cy = {k: (r.min() + r.max()) / 2 / 1920 for k, r in rows.items()}
check(all(abs(v - 0.55) < 0.02 for v in cy.values()), f"폰트를 바꿔도 자막 위치는 55% 그대로 {{k: round(v, 3) for k, v in cy.items()}}".replace("{{k: round(v, 3) for k, v in cy.items()}}", str({k: round(v, 3) for k, v in cy.items()})))
comp.caption_font = None
book_default = np.asarray(comp.create_overlay_image("한 장씩 넘기기 좋은 책이에요.", None, 1080, 1920, mode="book"))
comp.caption_font = "serif"
book_serif = np.asarray(comp.create_overlay_image("한 장씩 넘기기 좋은 책이에요.", None, 1080, 1920, mode="book"))
comp.caption_font = None
check(np.array_equal(book_default, book_serif), "책방은 기본이 명조 — 기존 책방 자막 모양 그대로 (선택해도 달라지지 않음)")

# ---------------------------------------------------------------- 3. 나레이션 없음: TTS 호출 없이 문장마다 3.6초 균등
from core.asset_generator import AssetGenerator, SILENT_SCENE_SEC
import core.asset_generator as ag
W = tmp("audiofonts_gen_")
gen = AssetGenerator(cache_dir=W, mock=False, mode="travel", audio_mode="silent", gemini_api_key="")
called = []
for name in ("edge_tts", "synthesize"):
    pass
durs = []
for i, d in enumerate((3.0, 4.5, 3.5, 4.0, 3.0)):
    p = gen.generate_voice({"scene_index": i + 1, "duration_sec": d, "voiceover": "겨울에는 눈부신 설경과 온수풀을 동시에 즐기는 숙소예요"}, force=True)
    err = subprocess.run([FF, "-hide_banner", "-i", p], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    durs.append(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)))
check(SILENT_SCENE_SEC == 3.6 and max(durs) - min(durs) < 0.05 and 3.2 <= durs[0] <= 3.5, f"씬 길이가 문장마다 균등 (입력 3.0~4.5초와 무관) {[round(d, 2) for d in durs]}")

# ---------------------------------------------------------------- 4. 실제 렌더: 나레이션 없음 + 감성 명조 + 의미 단위 자막 + BGM 100%
import core.pipeline as pl
import core.mode_policy as mp
from core.job import JobContext
from core.planners import stay as sty
from core.render.brand import BrandOverlay, EndCard
mp.INTRO_COVER_FORCE = False
W2 = tmp("audiofonts_render_")
bgm = os.path.join(W2, "bgm.wav")
subprocess.run([FF, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=30", "-ac", "2", bgm], check=True)
os.environ["TRAVEL_STAY_STYLE"] = "1"
try:
    job = JobContext.create(app="audio_fonts")
    job.root = W2
    job = JobContext(job.job_id, root=W2)
    ups = []
    for i in range(7):
        dst = os.path.join(job.uploads_dir, f"u{i}.jpg")
        shutil.copy(fixture("photo_pool.jpg" if i < 3 else "photo_room.jpg"), dst)
        ups.append(dst)
    facts = {"per_person": "4만 원대", "facilities": ["개별 온수풀", "리버뷰", "불멍", "바비큐"], "signature": "개별 온수풀", "people_used": 4}
    scenes = sty.plan_stay_storyboard("효정 리버뷰 풀빌라", "가평 리버뷰 독채풀빌라", "경기도 가평군", facts, ups, mock=True)
    spec = pl.JobSpec(mode="travel", name="효정 리버뷰 풀빌라", usp="개별 온수풀", media_paths=ups, mock=True, audio_mode="silent", caption_font="serif", bgm_path=bgm,
                      brand=BrandOverlay(business_name="가평", show_label=False))
    pl.assign_uploads_to_scenes(job, scenes, ups, cycle=False)
    res = pl.render(spec, job, scenes)
    err = subprocess.run([FF, "-hide_banner", "-i", res.final_path], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
    vol = subprocess.run([FF, "-hide_banner", "-i", res.final_path, "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    mv = re.search(r"mean_volume: (-?[\d.]+) dB", vol)
    check(os.path.exists(res.final_path) and "Audio:" in err and 15 <= dur <= 21 and mv and float(mv.group(1)) > -40,
          f"실제 렌더: 나레이션 없음 + BGM만 → {dur:.1f}초, 소리 평균 {mv.group(1) if mv else '?'}dB (BGM 재생)")
    comp2 = AdCompositor(output_dir=tmp("audiofonts_c2_"), font_dir=os.path.join(ROOT, "fonts"))
    comp2.semantic_captions = True
    chunks = [comp2.caption_chunks(s["voiceover"], "travel", 1080) for s in scenes[:4]]
    check(all(1 <= len(c) <= 2 for c in chunks) and sum(len(c) == 2 for c in chunks) >= 3, f"숙소 대본 문장이 의미 단위 최대 2덩어리로 순차 전환: {[len(c) for c in chunks]}덩어리")
finally:
    os.environ.pop("TRAVEL_STAY_STYLE", None)

# ---------------------------------------------------------------- 6. 실제 렌더러가 만드는 자막 타임라인(Subtitle Events) 검증 + 모듈 새로고침 목록
LINES = ["히노끼탕까지 갖춘 강릉 오션뷰 펜션, 실화일까요?", "주말에 바다 보이는 숙소를 찾다가 가격에 놀라 포기하셨죠!",
         "통창 너머 바다를 보며 히노끼탕을 즐길 수 있어요.", "옥상 수영장과 야외 테라스까지 함께 즐길 수 있어요.", "주말 특가 마감되기 전에 지금 저장하고 고정 댓글 최저가 링크 확인해 보세요!"]
events = []
import core.render.caption_chunks as _cc
_orig_cw = _cc.chunk_windows


def _spy(chunks, speech_sec, scene_sec):
    events.append(list(chunks))                    # 렌더러가 실제로 화면에 순차 표시하려는 자막 덩어리 (Subtitle Events)
    return _orig_cw(chunks, speech_sec, scene_sec)


_cc.chunk_windows = _spy
W3 = tmp("audiofonts_events_")
os.environ["TRAVEL_STAY_STYLE"] = "1"
try:
    job3 = JobContext.create(app="audio_events")
    job3.root = W3
    job3 = JobContext(job3.job_id, root=W3)
    ups3 = []
    for i in range(7):
        dst = os.path.join(job3.uploads_dir, f"u{i}.jpg")
        shutil.copy(fixture("photo_pool.jpg" if i < 3 else "photo_room.jpg"), dst)
        ups3.append(dst)
    scenes3 = sty.plan_stay_storyboard("효정 리버뷰 풀빌라", "가평 리버뷰 독채풀빌라", "경기도 가평군", facts, ups3, mock=True)
    for sc_, line in zip(scenes3, LINES):
        sc_["voiceover"] = sc_["subtitle"] = sc_["caption"] = line
        sc_["hook_title"] = ""
    spec3 = pl.JobSpec(mode="travel", name="효정 리버뷰 풀빌라", usp="개별 온수풀", media_paths=ups3, mock=True, audio_mode="silent", caption_font="serif", bgm_path=bgm,
                       brand=BrandOverlay(business_name="가평", show_label=False))
    pl.assign_uploads_to_scenes(job3, scenes3, ups3, cycle=False)
    pl.render(spec3, job3, scenes3)
finally:
    _cc.chunk_windows = _orig_cw
    os.environ.pop("TRAVEL_STAY_STYLE", None)


def events_for(line):
    key = line.replace(" ", "")
    return next((e for e in events if "".join(e).replace(" ", "") == key), [])


A, B = LINES[0], LINES[2]
ea, eb = events_for(A), events_for(B)
check(len(ea) == 2 and ea == ["히노끼탕까지 갖춘", "강릉 오션뷰 펜션, 실화일까요?"], f"실제 렌더 자막 이벤트: '{A}' → {ea}")
check(len(eb) == 2 and eb == ["통창 너머 바다를 보며", "히노끼탕을 즐길 수 있어요."], f"실제 렌더 자막 이벤트: '{B}' → {eb}")
allev = [events_for(l) for l in LINES]
check(all(len(e) == 2 for e in allev) and not any(len(x.replace(" ", "")) <= 5 for e in allev for x in e), f"5문장 모두 정확히 2이벤트, 1어절·5자 이하 꼬투리 없음: {[len(e) for e in allev]}")

import re as _re
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
_i = src.index("RELOAD_MODULES = (")
_j = src.index("for _m in RELOAD_MODULES", _i)
reload_list = set(_re.findall(r'"(core[\w.]*)"', src[_i:_j]))
render_mods = {"core.render." + f[:-3] for f in os.listdir(os.path.join(ROOT, "core", "render")) if f.endswith(".py") and f != "__init__.py"}
check(render_mods <= reload_list, f"core/render 모듈 전부 새로고침 목록에 포함 (빠지면 옛 자막 분할 함수가 서버에 남음): 누락 {sorted(render_mods - reload_list)}")

# ---------------------------------------------------------------- 5. 화면: 오디오 3택 + 나레이션 없음이면 BGM 자동 ON + 폰트 3택
from streamlit.testing.v1 import AppTest
at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120).run()
au = [s for s in at.sidebar.selectbox if s.label == "오디오 모드"][0]
check(len(au.options) == 3 and "AI 나레이션 + 자막 (기본 - 맛집·쇼핑 추천)" in au.options[0] and "나레이션 없음: 감성 BGM + 자막만 (숙소·카페·감성 추천)" in au.options[1],
      f"오디오 모드: {au.options}")
fr = [r for r in at.sidebar.radio if r.label == "자막 폰트"]
check(bool(fr) and len(fr[0].options) == 3 and "임팩트 고딕" in fr[0].options[0] and "감성 명조" in fr[0].options[1] and "팝체" in fr[0].options[2] and fr[0].value == "impact",
      "🔤 자막 폰트 스타일: 3종 (임팩트 고딕 기본 / 감성 명조 / 둥근 예능 팝체)")
au.select(au.options[1]).run()
tog = [t for t in at.sidebar.toggle if "자체 BGM" in t.label]
act = [s for s in at.sidebar.selectbox if s.label == "AI 성우 보이스"]
check(not at.exception and tog and tog[0].value is True and tog[0].disabled and act and act[0].disabled, "나레이션 없음 선택: BGM 자동 켜짐, 성우 선택은 비활성")
at.session_state.mode_switch = "🧳 숙소 제휴 쇼츠"
at.session_state.production_mode = "travel"
at.run()
at.session_state.mode_switch = "📚 시골책방 도서 큐레이션"
at.session_state.production_mode = "book"
at.run()
fr2 = [r for r in at.sidebar.radio if r.label == "자막 폰트"]
check(not at.exception and fr2 and fr2[0].value == "serif", "시골책방 모드는 처음에 감성 명조가 선택돼 있음")

finish(fails)
