"""시골책방(도서 큐레이션) 모드: 한 권 깊게 호흡·책 속 원문 인용·근거 없는 주장 차단 + 명조체·종이 인용 카드·2:3 표지 카드 +
성우 -5%·디졸브 0.6초 + 첫 댓글(책방 지도·알라딘 링크·알라딘 제휴 고지) 대기열"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import json
import os
import shutil
import subprocess

import numpy as np
from PIL import Image, ImageDraw

import core.llm.router as rt
from core.compositor import (AdCompositor, BOOK_CAPTION_CY, BOOK_LABEL_CY, BOOK_PAPER, QUOTE_CARD_CY, XFADE_BY_MODE, DUCK_BY_MODE)
from core.planners.book import (CTA_TEMPLATE, DEFAULT_HOOK, QUOTE_MAX, book_hype, check_quote, plan_book_storyboard,
                                split_book_media)
from core.render.commerce_layout import BOOK_CARD_BOX
from core.script_guard import ending_kind, find_banned_style
from core.shorts_meta import ALADIN_DISCLOSURE_FULL, build_first_comment, shop_link_kind

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


work = tmp()
# 테스트용 표지 그림 (실제 앱은 직접 찍은 표지 사진만 씀)
COVER = os.path.join(work, "cover.png")
im = Image.new("RGB", (800, 1200), (32, 52, 88))
ImageDraw.Draw(im).rectangle([80, 300, 720, 520], fill=(240, 220, 170))
im.save(COVER)
STORE1, STORE2 = fixture("photo_room.jpg"), fixture("photo_pool.jpg")
QUOTE = "죽는 날까지 하늘을 우러러 한 점 부끄럼이 없기를,"          # 윤동주 「서시」 (공유 저작물)
INFO = "윤동주 유고 시집, 1948년 첫 출간"
LINK = "https://www.aladin.co.kr/shop/wproduct.aspx?ItemId=123456"

# ---------------------------------------------------------------- 1. 사진 나누기·인용문 검사
check(split_book_media(["c", "s1", "s2"], 1) == (["c"], ["s1", "s2"]) and split_book_media(["c", "p", "s1"], 2) == (["c", "p"], ["s1"]),
      "올린 순서: 앞 1~2장 = 책(표지·펼친 면), 나머지 = 책방 사진")
check(check_quote(" “" + QUOTE + "” ") == QUOTE, "인용문은 따옴표·공백만 정리 (원문 그대로)")
try:
    check_quote("가" * (QUOTE_MAX + 1))
    check(False, "긴 인용")
except ValueError as e:
    check("자르지 않아요" in str(e), f"{QUOTE_MAX}자 넘는 인용은 임의로 자르지 않고 줄여 달라고 안내")
check(book_hype("올해 최고의 베스트셀러예요.", INFO) == "베스트셀러" and book_hype("수상작이에요", "2020년 문학상 수상") is None,
      "베스트셀러·수상 같은 말은 책 소개에 있을 때만")

# ---------------------------------------------------------------- 2. 대본 (Claude 가짜 응답)
os.environ["ANTHROPIC_API_KEY"] = "test-claude"
seen = {"prompts": [], "labels": []}
reply = {"hook_title": "부끄럼 없는 밤의 시",
         "scenes": [{"scene_index": 1, "voiceover": "마음이 복잡한 밤, 어떤 책 펼쳐요?"},
                    {"scene_index": 2, "voiceover": "하루를 돌아보고 싶은 밤, 한 편씩 천천히 읽기 좋은 시집이에요."},
                    {"scene_index": 3, "voiceover": "올해 최고의 베스트셀러라 다들 읽는 책이죠!"},       # 근거 없음 → 템플릿
                    {"scene_index": 4, "voiceover": "창가 자리에 앉으면 시 한 편이 더 오래 남아요."}]}


def fake_claude(contents, system, temperature, max_tokens):
    seen["prompts"].append(contents[-1] if isinstance(contents[-1], str) else "")
    seen["labels"].append([c for c in contents if isinstance(c, str) and c.startswith("[")])
    if "규칙을 어겼습니다" in str(contents[-1]):
        return json.dumps({"fixes": []}, ensure_ascii=False)
    return json.dumps(reply, ensure_ascii=False)


rt._overrides = {"claude": fake_claude}
sb = plan_book_storyboard("시골책방", "하늘과 바람과 별과 시", "윤동주", INFO, QUOTE, media_paths=[COVER, STORE1, STORE2], api_key=None)
vo = [s["voiceover"] for s in sb]
check([s["role"] for s in sb] == ["hook", "book", "book", "quote", "store", "cta"]
      and [s["layout"] for s in sb] == ["book_card", "book_card", "book_card", "book_quote", "full", "full"],
      "구조: 훅 → 이런 날 이 책 2문장 → 책 속 한 문장 → 책방 분위기 → CTA (표지 카드 / 인용 카드 / 책방 전체 화면)")
check([s.get("source_path") for s in sb] == [COVER, COVER, COVER, COVER, STORE1, STORE2] and sb[2]["shot"] == "center",
      "책 장면은 표지 사진(펼친 면이 없으면 중앙 줌인 앵글), 책방 장면은 책방 사진")
check(sb[3]["voiceover"] == QUOTE and sb[3]["quote_card"] == QUOTE and sb[3]["quote_source"] == "「하늘과 바람과 별과 시」, 윤동주",
      "인용 씬: 입력한 원문 그대로 낭독 + 인용 카드 + 출처")
check(sb[0]["script_engine"] == "claude" and sb[0]["hook_title"] == "부끄럼 없는 밤의 시"
      and sb[0]["book"]["label"] == "📚 시골책방의 한 권" and vo[-1] == CTA_TEMPLATE.format(store="시골책방"),
      "Claude 대본 + 라벨 바 '📚 시골책방의 한 권' + CTA (책방 방문 + 구매 링크는 댓글)")
check("베스트셀러" not in vo[2] and not any(find_banned_style(v) for v in vo), f"근거 없는 '베스트셀러' 문장은 템플릿으로 ('{vo[2]}')")
prompt = seen["prompts"][0]
check("결말" in prompt and "억지로 의문문·감탄문을 만들지 마세요" in prompt and INFO in prompt and "인용과 CTA는 따로" in prompt
      and seen["labels"][0][:3] == ["[책 사진]", "[책방 사진]", "[책방 사진]"],
      "프롬프트: 책 소개 사실만·줄거리/결말 금지·평서문 기본(억지 의문문 금지) + 책·책방 사진 전달 (인용문은 AI에 맡기지 않음)")
kinds = [ending_kind(v) for v in [vo[0], vo[1], vo[2], vo[4]]]
check(all(v.rstrip().endswith((".", "!", "?")) for v in [vo[0], vo[1], vo[2], vo[4]]),
      f"어미를 강제로 바꾸지 않음 (질문·감탄 의무 없음, 템플릿 줄의 '.'→'!' 변환도 하지 않음) {kinds}")
check(all(s.get("caption_center_y") == BOOK_CAPTION_CY for s in sb[:4]) and sb[4].get("caption_center_y") is None,
      "자막 위치: 표지 카드 씬은 라벨 바 아래, 책방 씬은 화면 가운데")

repair = [p for p in seen["prompts"] if "규칙을 어겼습니다" in p]
check(repair and "너무 짧음" in repair[0] and "30~38자" in repair[0] and "scene_index 4:" in repair[0] and "scene_index 1:" not in repair[0],
      "분량: 30자 미만인 '이런 날·책방' 문장은 30~38자로 다시 쓰게 함 (훅 줄 제외) → 약 35초 호흡")
sb_noq = plan_book_storyboard("시골책방", "하늘과 바람과 별과 시", "윤동주", INFO, "", media_paths=[COVER, STORE1], api_key=None)
rt._overrides = {}
os.environ["ANTHROPIC_API_KEY"] = ""
check([s["role"] for s in sb_noq] == ["hook", "book", "book", "store", "cta"] and sb_noq[-1]["source_path"] == STORE1,
      "책 속 문장을 비우면 인용 씬 없이 (책방 사진 1장이면 CTA도 같은 사진)")
try:
    plan_book_storyboard("시골책방", "책", media_paths=[], api_key=None)
    check(False, "사진 없음")
except ValueError as e:
    check("표지" in str(e), "책 사진이 없으면 기획 안 함 (AI 표지 생성 없음)")
sb_t = plan_book_storyboard("시골책방", "책", media_paths=[COVER], mock=True)
check(sb_t[0]["hook_title"] == DEFAULT_HOOK and sb_t[0]["script_engine"] == "template" and sb_t[-2]["layout"] == "book_card",
      "엔진 없음 → 템플릿 대본, 책방 사진이 없으면 책방 장면도 표지 카드")

# ---------------------------------------------------------------- 3. 화면: 명조체·라벨 바·종이 인용 카드·2:3 표지 카드
comp = AdCompositor(output_dir=work)
W, H = 1080, 1920
check(os.path.basename(comp.serif_font_path) == "NotoSerifKR-Bold.otf" and os.path.getsize(comp.serif_font_path) > 1_000_000,
      "Noto Serif KR 명조체 확보 (fonts/, SIL OFL)")
serif = np.array(comp.create_overlay_image("한 장씩 넘기기 좋은 책이에요.", None, W, H, mode="book", center_y=BOOK_CAPTION_CY))
sans = np.array(comp.create_overlay_image("한 장씩 넘기기 좋은 책이에요.", None, W, H, mode="owner", center_y=BOOK_CAPTION_CY))
check(serif[..., 3].sum() > 0 and not np.array_equal(serif, sans), "시골책방 자막은 명조체 (고딕 자막과 다른 글꼴)")
lab = np.array(comp.render_book_label("📚 시골책방의 한 권", W, H))
ly = int(H * BOOK_LABEL_CY)
px = lab[ly, int(W * 0.2)]
check(px[3] > 200 and all(abs(int(px[c]) - BOOK_PAPER[c]) < 30 for c in range(3)), f"상단 라벨 바: 종이색 {tuple(px)}")
qc = np.array(comp.render_quote_card(QUOTE, "「하늘과 바람과 별과 시」, 윤동주", W, H))
qy = int(H * QUOTE_CARD_CY)
edge = qc[qy, int(W * 0.1)]
region = qc[qy - 40:qy + 40, int(W * 0.1):int(W * 0.2), :3].astype(int)
check(edge[3] > 200 and all(abs(int(edge[c]) - BOOK_PAPER[c]) < 40 for c in range(3)) and region.std() > 1.0,
      f"인용 카드: 종이색 + 질감(균일하지 않음) {tuple(edge)} std={region.std():.1f}")
dark = (qc[..., :3].sum(-1) < 300) & (qc[..., 3] > 200)
check(dark[int(H * 0.55):int(H * 0.8)].sum() > 2000 and qc[int(H * 0.3), W // 2][3] == 0, "인용 문장(짙은 글씨)은 카드 안, 위쪽 표지 카드 자리는 비움")

from core.asset_generator import AssetGenerator, MODE_RATE  # noqa: E402
gen = AssetGenerator(mode="book", cache_dir=work, mock=True)
red = os.path.join(work, "red.png")
Image.new("RGB", (1200, 900), (210, 40, 40)).save(red)          # 가로 사진도 2:3 카드에 맞춰 채움
clip = os.path.join(work, "card.mp4")
gen.generate_video({"scene_index": 2, "layout": "book_card", "video_motion_prompt_en": "zoom_in", "duration_sec": 1.0}, red, force=True)
made = [f for f in os.listdir(work) if f.startswith("scene_02") and f.endswith(".mp4")]
from core.render.ffmpeg_assemble import ffmpeg_exe, probe  # noqa: E402
fr_png = os.path.join(work, "card.png")
subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", os.path.join(work, made[0]), "-frames:v", "1", fr_png], check=True)
fr = np.array(Image.open(fr_png).convert("RGB")).astype(int)
mask = (fr[..., 0] > 180) & (fr[..., 1] < 90)
ys, xs = np.where(mask)
cw, ch = xs.max() - xs.min(), ys.max() - ys.min()
check(abs(cw / ch - 2 / 3) < 0.03 and abs(xs.min() / fr.shape[1] - BOOK_CARD_BOX[0]) < 0.02,
      f"책 표지 카드 2:3 고정 비율 (가로 사진도 채움) {cw}x{ch} = {cw / ch:.3f}")
check(MODE_RATE.get("book") == "+0%" and XFADE_BY_MODE.get("book") == 0.6 and DUCK_BY_MODE.get("book", 1) < 0.32,
      "호흡 프리셋: 성우 기본 속도(인용만 -5%), 디졸브 0.6초, BGM 덕킹 더 낮게")
import core.render.ffmpeg_assemble as fa  # noqa: E402
_orig_asm, got = fa.assemble, {}
fa.assemble = lambda comp_, manifest, out, **kw: (got.update(kw), out)[1]
comp.assemble_ad([{"x": 1}], output_filename="x.mp4", mode="book", label="📚 시골책방의 한 권")
comp.assemble_ad([{"x": 1}], output_filename="y.mp4", mode="owner")
fa.assemble = _orig_asm
check(got.get("xfade") == 0.35 and comp.xfade == 0.35, "다른 모드는 기존 디졸브 0.35초 그대로")
fa.assemble = lambda comp_, manifest, out, **kw: (got.update(kw), out)[1]
comp.assemble_ad([{"x": 1}], output_filename="x.mp4", mode="book", label="📚 시골책방의 한 권")
fa.assemble = _orig_asm
check(got.get("xfade") == 0.6 and got.get("label") == "📚 시골책방의 한 권", "시골책방 렌더: 디졸브 0.6초 + 라벨 바 전달")
rates = []
gen2 = AssetGenerator(mode="book", cache_dir=work, mock=False)
gen2._speak_checked = lambda text, path, rate, pitch, n, keep_commas=False: (rates.append(rate), path)[1]
for sc in ({"scene_index": 1}, {"scene_index": 4, "tts_rate": "-5%"}):
    try:
        gen2._synthesize("천천히 읽기 좋은 책이에요.", os.path.join(work, f"v{sc['scene_index']}.mp3"), sc, True)
    except Exception:
        pass
check(rates[:2] == ["+0%", "-5%"] and sb[3].get("tts_rate") == "-5%" and not sb[1].get("tts_rate"),
      f"듀얼 템포: 훅·상황은 기본 속도, 책 속 문장 낭독 씬만 -5%로 합성 요청 {rates[:2]}")

# ---------------------------------------------------------------- 4. 첫 댓글: 책방 지도 + 알라딘 링크 + 알라딘 제휴 고지 → 대기열
cm = build_first_comment("book", "이 책, 어떤 밤에 읽고 싶어요? 👇", ["#책추천", "#동네책방", "#시집", "#x"], search_keyword="시골책방",
                         items=[{"short": "「하늘과 바람과 별과 시」", "link": LINK}])
blocks = cm.split("\n\n")
check(blocks[0].startswith("이 책") and "네이버 지도나 카카오맵에서 '시골책방'을 검색해 보세요" in blocks[1] and "map.naver.com" not in cm
      and blocks[2] == f"📖 「하늘과 바람과 별과 시」 구매 👉 {LINK}" and blocks[3] == ALADIN_DISCLOSURE_FULL
      and blocks[4] == "#책추천 #동네책방 #시집", "첫 댓글: 참여 질문 → 지도 앱 검색 안내(URL 없음) → 「책」 알라딘 구매 → 알라딘 제휴 고지 → 해시태그")
check(ALADIN_DISCLOSURE_FULL == "이 포스팅은 알라딘 제휴 마케팅 활동의 일환으로, 도서 구매 시 일정액의 수수료를 제공받습니다."
      and [shop_link_kind(u) for u in (LINK, "https://aladin.kr/p/abc", "https://link.coupang.com/a/x")] == ["aladin", "aladin", "coupang"],
      "알라딘 제휴 고지 문구(지정 원문) + 알라딘 링크 판별")
import core.youtube_upload as yu  # noqa: E402
_drafts = yu.DRAFTS_PATH
yu.DRAFTS_PATH = os.path.join(work, "drafts.json")
yu.queue_comment_draft("book", os.path.join(work, "a.mp4"), cm, job_id="J1")
yu.queue_comment_draft("book", os.path.join(work, "a.mp4"), cm + "!", job_id="J1")
d = json.load(open(yu.DRAFTS_PATH, encoding="utf-8"))
check(len(d) == 1 and d[0]["text"].endswith("!") and d[0]["mode"] == "book" and d[0]["status"] == "draft",
      "첫 댓글 대기열(업로드 전 초안): 같은 영상은 덮어씀")

# ---------------------------------------------------------------- 5. 앱: 시골책방 모드 → 기획 → 렌더 (Mock) → 화면·대기열
from streamlit.testing.v1 import AppTest  # noqa: E402
os.environ["YOUTUBE_API_KEY"] = ""
jobs = []
try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=600).run()
    jobs.append(at.session_state.job.workdir)
    at.session_state.mode_switch = "📚 시골책방 도서 큐레이션"
    at.session_state.production_mode = "book"
    at.run()
    at.session_state["mock_mode"] = True; at.run()                                   # Mock
    for k, v in (("book_store", "시골책방"), ("book_title", "하늘과 바람과 별과 시"), ("book_author", "윤동주"), ("book_link", LINK)):
        at.text_input(key=k).input(v).run()
    at.text_area(key="book_info").input(INFO).run()
    at.text_area(key="book_quote").input(QUOTE).run()
    ups = [shutil.copy(COVER, os.path.join(work, "u_cover.png")), shutil.copy(STORE1, os.path.join(work, "u_s1.jpg")),
           shutil.copy(STORE2, os.path.join(work, "u_s2.jpg"))]
    at.session_state.uploaded_paths = ups
    at.run()
    check(not at.exception and not any(t.key == "product_name_input" for t in at.text_input), "시골책방 입력 칸 (상품 입력 칸 대신)")
    [b for b in at.button if "대본 만들기" in b.label][0].click().run()
    sbx = at.session_state.storyboard
    check(len(sbx) == 6 and all("ai_images" in (s.get("image_path") or "") for s in sbx[:-1]) and sbx[-1]["image_path"] == ups[0]
          and sbx[-1]["layout"] == "book_quote", "기획 → 씬 1~5는 AI 일러스트, 마지막(책 공개) 씬만 진짜 표지")
    yu.DRAFTS_PATH = os.path.join(work, "drafts_app.json")
    [b for b in at.button if "영상 만들기" in b.label][0].click().run()
    jobs.append(at.session_state.job.workdir)
    final = at.session_state.final_video_path
    check(not at.exception and bool(final) and os.path.exists(final), "렌더 완료 (로컬 저장)")
    if final and os.path.exists(final):
        def _cover_offset():          # 0초 커버(완성 후 앞에 붙는 정지 클립)만큼 본편이 뒤로 밀림
            from core.mode_policy import INTRO_COVER_SEC, intro_cover_enabled
            return INTRO_COVER_SEC if intro_cover_enabled() else 0.0
        q_start = sum(probe(os.path.join(os.path.dirname(final), "..", "assets", f"scene_0{i}.mp3"))["duration"] or 0 for i in range(1, 4))
        png = os.path.join(work, "q.png")
        subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", f"{q_start + 2.0 + _cover_offset():.2f}", "-i", final, "-frames:v", "1", png], check=True)
        f = np.array(Image.open(png).convert("RGB")).astype(int)
        fh, fw = f.shape[:2]
        lp, qp = f[int(fh * BOOK_LABEL_CY), int(fw * 0.2)], f[int(fh * 0.52), int(fw * 0.1)]   # 인용 카드: 전체 화면 그림 위 가운데
        check(all(abs(lp[c] - BOOK_PAPER[c]) < 45 for c in range(3)) and all(abs(qp[c] - BOOK_PAPER[c]) < 65 for c in range(3)),
              f"완성 영상 인용 장면: 종이색 라벨 바 + 종이 인용 카드 {tuple(lp)} {tuple(qp)}")
    texts = " ".join(t.value or "" for t in at.text_area)
    d2 = json.load(open(yu.DRAFTS_PATH, encoding="utf-8")) if os.path.exists(yu.DRAFTS_PATH) else []
    check(ALADIN_DISCLOSURE_FULL in texts and LINK in texts and "카카오맵" in texts and "map.naver.com" not in texts,
          "완료 화면: 첫 댓글 초안 (지도 앱 검색 안내·알라딘 링크·알라딘 제휴 고지, 지도 URL 없음)")
    check(len(d2) == 1 and d2[0]["mode"] == "book" and LINK in d2[0]["text"] and os.path.samefile(d2[0]["video_path"], final),
          "렌더 직후 첫 댓글 텍스트가 대기열에 자동 저장 (업로드 연결 전)")
    check(not any("유튜브 쇼츠 업로드" in (b.label or "") for b in at.button) and any("연결 전" in (i.value or "") for i in at.info),
          "유튜브 업로드 버튼 없음 (채널 연결 제외)")
finally:
    yu.DRAFTS_PATH = _drafts
    for j in set(jobs):
        shutil.rmtree(j, ignore_errors=True)

# ---------------------------------------------------------------- 6. 우화·사유 스토리텔링 템플릿 (도서 레이더 우화형 벤치마크)
from core.planners.book_fable import FABLE_CTA, FABLE_QUOTE_MAX, QUOTE_CHUNK_MAX, QUOTE_CY, split_quote  # noqa: E402
FABLE_Q = ("쥐들이 모여 회의를 했다. 고양이 목에 방울을 달자는 의견이 나왔고, 모두가 손뼉을 쳤다. 그런데 한 늙은 쥐가 물었다. "
           "그렇다면 누가 방울을 달 것인가? 아무도 대답하지 못했다. 좋은 생각보다 어려운 것은, 그 생각을 실행할 용기다.")
chunks = split_quote(FABLE_Q)
long_sent = "가" * 50 + ", " + "나" * 50 + ", " + "다" * 30 + "."
check(" ".join(chunks) == FABLE_Q and all(len(c) <= QUOTE_CHUNK_MAX for c in chunks) and len(chunks) == 2
      and all(len(c) <= QUOTE_CHUNK_MAX for c in split_quote(long_sent)) and " ".join(split_quote(long_sent)) == long_sent,
      f"원문 분할: 문장 경계(길면 쉼표)에서만 나눔, 글자는 그대로 {[len(c) for c in chunks]}")
try:
    plan_book_storyboard("시골책방", "이솝 우화", "이솝", "", "가" * (FABLE_QUOTE_MAX + 1), media_paths=[COVER], mock=True, template="fable")
    check(False, "350자 초과")
except ValueError as e:
    check(f"{FABLE_QUOTE_MAX}자" in str(e), "우화 원문은 350자까지 (넘으면 자르지 않고 안내)")
try:
    plan_book_storyboard("시골책방", "이솝 우화", media_paths=[COVER], mock=True, template="fable")
    check(False, "원문 없음")
except ValueError:
    check(True, "우화 템플릿은 원문 필수")

os.environ["ANTHROPIC_API_KEY"] = "test-claude"
seen.update(prompts=[], labels=[])
reply = {"hook_title": "듣기 좋은 말만 들으면 손해예요",
         "scenes": [{"scene_index": 1, "voiceover": "다들 회의에선 좋은 말만 하죠?"},
                    {"scene_index": 2, "voiceover": "이솝 우화처럼 말만 앞서는 회의, 오늘도 그냥 넘어갔잖아요."},   # 제목 노출 → 템플릿
                    {"scene_index": 3, "voiceover": "작은 실천 하나가 백 마디 계획보다 큰 용기가 된다는 걸 기억해요."}]}
rt._overrides = {"claude": fake_claude}
PAGE = os.path.join(work, "page.png")
Image.new("RGB", (1200, 800), (235, 228, 210)).save(PAGE)
fb = plan_book_storyboard("시골책방", "이솝 우화", "이솝", "고대 그리스 우화 모음집", FABLE_Q,
                          media_paths=[COVER, PAGE, STORE1, STORE2], book_photo_count=2, api_key=None, template="fable")
rt._overrides = {}
os.environ["ANTHROPIC_API_KEY"] = ""
roles = [s["role"] for s in fb]
check(roles == ["hook", "situation", "quote", "quote", "comfort", "cta"] and fb[0]["book"]["template"] == "fable",
      f"우화 구조: 훅·상황 → 원문 카드 여러 장 → 위로 → 책 공개 CTA {roles}")
check([s["voiceover"] for s in fb if s["role"] == "quote"] == chunks and all(s["quote_source"] == "" and s["quote_cy"] == QUOTE_CY
                                                                             for s in fb if s["role"] == "quote"),
      "원문 낭독: 나눈 원문 그대로, 카드 출처 숨김(제목 미공개), 카드는 화면 가운데")
check([s.get("tts_rate") for s in fb] == [None, None, "-5%", "-5%", None, None] and fb[3].get("hold_sec") == 1.5,
      "듀얼 템포: 원문 낭독 씬만 -5%, 마지막 원문 카드 뒤 1.5초 여운")
check(fb[-1]["voiceover"] == FABLE_CTA.format(title="이솝 우화", store="시골책방")
      == "오늘 마음을 다독여준 이 책은 『이솝 우화』입니다. 시골책방 서가에서 직접 펼쳐보세요."
      and fb[-1]["source_path"] == COVER and fb[-1]["layout"] == "book_quote" and fb[-1]["caption_center_y"] == 0.68,
      "엔딩 CTA(지정 문구)에서 책 제목 첫 공개 + 표지 카드")
check(COVER not in [s.get("source_path") for s in fb[:-1]] and not any("이솝" in s["voiceover"] for s in fb[:-1])
      and "이솝" not in fb[0]["hook_title"], "마지막 장면 전에는 표지·제목·저자가 안 나옴 (제목이 든 AI 대사는 템플릿으로)")
check(fb[0]["hook_title"] == "듣기 좋은 말만 들으면 손해예요" and fb[0]["script_engine"] == "claude", "경고형 훅 헤드라인 (해요체)")
fprompt = seen["prompts"][0]
check("마지막에만 공개" in fprompt and "경고형" in fprompt and "리스트형" in fprompt and FABLE_Q in fprompt
      and not any(l == "[책 사진]" for l in seen["labels"][0]),
      "우화 프롬프트: 제목은 마지막 공개·경고/리스트형 훅·원문은 낭독용으로만, 책 사진은 안 보냄")
basic_q = [s for s in sb if s["role"] == "quote"][0]
check(basic_q.get("tts_rate") == "-5%" and basic_q["quote_source"], "기본 큐레이션도 인용 낭독만 -5% (출처 표시는 유지)")

# 우화 카드 위치·업로드 카피 제목 숨김·BGM 기본값
mid = np.array(comp.render_quote_card(chunks[0], "", W, H, cy=QUOTE_CY))
ys = np.where(mid[..., 3] > 200)[0]
check(abs((ys.min() + ys.max()) / 2 / H - QUOTE_CY) < 0.03, "우화 인용 카드는 화면 가운데")
import google.genai as gg  # noqa: E402
from core.shorts_meta import make_shorts_copy  # noqa: E402
_oc = gg.Client
gem = {"title": "이솝 우화 속 쥐들의 회의가 알려 준 것", "thumb_main": "이 말 듣고 멈췄어요", "thumb_sub": "마음을 다독이는 책",
       "accent": "🔥", "hashtags": ["#이솝우화", "#책추천", "#위로"], "comment_question": "요즘 미뤄 둔 일 있나요? 👇"}
gg.Client = lambda api_key=None: type("C", (), {"models": type("M", (), {"generate_content": lambda self, **kw: type(
    "R", (), {"text": json.dumps(gem, ensure_ascii=False)})()})()})()
cp = make_shorts_copy("book", hook="h", usp="", product_name="시골책방", api_key="fake", signature="마음을 다독이는 책",
                      hidden_terms=["이솝 우화", "이솝"])
gg.Client = _oc
check("이솝" not in cp["title"] and "#이솝우화" not in cp["hashtags"] and any("마지막 공개 전" in r for r in cp["rejected"]),
      f"우화: 업로드 제목·해시태그에도 책 제목 숨김 ({cp['title']})")
import core.render.bgm as bgm_mod  # noqa: E402
bdir = os.path.join(work, "bgm")
os.makedirs(bdir, exist_ok=True)
open(os.path.join(bdir, "calm piano.wav"), "wb").write(b"0")
_env = os.environ.get("BGM_BOOK_TRACK")
os.environ["BGM_BOOK_TRACK"] = "calm piano.wav"
ok_track = bgm_mod.book_default_track(bdir)
os.environ["BGM_BOOK_TRACK"] = "없는 곡.wav"
missing = bgm_mod.book_default_track(bdir)
if _env is None:
    os.environ.pop("BGM_BOOK_TRACK", None)
else:
    os.environ["BGM_BOOK_TRACK"] = _env
fa.assemble = lambda comp_, manifest, out, **kw: out
comp.assemble_ad([{"x": 1}], output_filename="z.mp4", mode="book")
fa.assemble = _orig_asm
check(ok_track == "calm piano.wav" and missing is None and comp.bgm_from_start,
      "시골책방 기본 BGM = .env BGM_BOOK_TRACK (없는 파일이면 끔), 잔잔한 곡은 드롭이 아니라 처음부터")

finish(fails)
