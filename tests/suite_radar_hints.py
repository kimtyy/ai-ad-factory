"""레이더 → 플래너 씬 1 후킹 힌트 주입 테스트 (가짜 Gemini로 프롬프트 캡처 + 실제 Gemini 1~2회)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.chdir(ROOT)
sys.path.insert(0, ROOT)
import logging
logging.disable(logging.WARNING)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from core.radar import store
from core.radar.hints import HookHints, load_hook_hints, copies_reference, vertical_for
from core.radar.run import execute
from core.radar.scoring import VideoRecord, RadarFilters, score
import core.analyzer as an
import core.planners.owner as ow
import google.genai as gg
REAL_CLIENT = gg.Client   # 가짜로 바꾸기 전 원본 보관 (테스트 후 반드시 복원)

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


# ---------------------------------------------------------------- 1. 매핑 / 로딩
check(vertical_for("travel") == "stay" and vertical_for("owner", "food") == "food" and vertical_for("owner", "stay") == "stay"
      and vertical_for("owner", "shop") is None and vertical_for("commerce") is None, "모드/업종 → 레이더 vertical 매핑")

dry_db = os.path.join(tempfile.mkdtemp(), "dry.db")
execute(vertical="all", dry_run=True, db_path=dry_db, hooks=8)
check(not load_hook_hints("stay", db_path=dry_db), "dry-run 패턴은 기본 차단 (실제 대본에 가짜 패턴 주입 안 함)")
h_dry = load_hook_hints("stay", db_path=dry_db, allow_dryrun=True)
types = [r["hook_type"] for r in h_dry.references]
check(h_dry and h_dry.is_dryrun and len(h_dry.references) <= 4 and all(types.count(t) <= 2 for t in types),
      f"allow_dryrun=True: 상위 {len(h_dry.references)}개, 유형당 최대 2개 {types}")

# '실데이터' 형태의 DB (Gemini 추출 후킹) 직접 구성
live_db = os.path.join(tempfile.mkdtemp(), "live.db")
conn = store.connect(live_db)
rid = store.start_run(conn, "live", "stay", 14, ["숙소 추천"])
now = datetime.now(timezone.utc)
refs = [
    ("v1", "이 가격에 이 뷰가 나온다고?", 32.6, "question", "이 가격에 {대상}이 나온다고?", "가성비 기대를 깨는 반전 질문"),
    ("v2", "3초 만에 예약 마감된 숙소", 21.0, "number", "{숫자}초 만에 {결과}된 {장소}", "구체적 숫자로 희소성 강조"),
    ("v3", "문 열자마자 펼쳐지는 바다", 18.4, "pov", "문 열자마자 펼쳐지는 {풍경}", "1인칭 시점 몰입"),
]
recs = []
for vid, title, ratio, *_ in refs:
    r = VideoRecord(video_id=vid, title=title, channel_id="c" + vid, channel_title="ch", published_at=now - timedelta(days=2),
                    duration_sec=30, views=int(10_000 * ratio), likes=0, comments=0, subscribers=10_000,
                    subscribers_hidden=False, keyword="숙소 추천", vertical="stay")
    recs.append(score(r, RadarFilters(), now))
store.save_records(conn, rid, recs)
for vid, title, ratio, htype, tpl, why in refs:
    store.save_hook(conn, vid, {"source": "gemini:gemini-3.6-flash", "spoken_line": title, "on_screen_text": title,
                                "visual_hook": "", "hook_type": htype, "pattern_template": tpl, "why_it_works": why})
conn.close()
hints = load_hook_hints("stay", db_path=live_db)
check(len(hints.references) == 3 and not hints.is_dryrun and hints.references[0]["outlier_ratio"] == 32.6, "실데이터 패턴 3개 로드 (배수순)")
block = hints.prompt_block
check("이 가격에 {대상}이 나온다고?" in block and "베끼지" in block and "이 가격에 이 뷰가 나온다고?" not in block,
      "힌트 블록: 구조 템플릿만 포함, 원문 대사는 미포함")
check(copies_reference("이 가격에 이 뷰가 나온다고?!", hints) and not copies_reference("이 가격에 이 수영장 실화?", hints),
      "원문 복제 판정 (그대로 → 차단, 구조만 차용 → 허용)")

# ---------------------------------------------------------------- 2. 프롬프트 주입 (가짜 Gemini)
captured = {}


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, reply):
        self.reply = reply

    def generate_content(self, **kw):
        captured["contents"] = kw.get("contents")
        return FakeResp(self.reply)


def fake_client(reply):
    class C:
        def __init__(self, api_key=None):
            self.models = FakeModels(reply)
    return C


an.genai.Client = fake_client('{"scenes": [{"voiceover": "이 가격에 이 뷰가 나온다고?", "hook_title": "이 가격에 이 뷰가 나온다고?"},'
                              '{"voiceover":"물빛이 정말 예쁜 수영장!"},{"voiceover":"자"},{"voiceover":"자"},{"voiceover":"자"},{"voiceover":"자"},{"voiceover":"끝"}]}')
inv = {"pool": 3, "bedroom": 2, "facility": 2, "view": 1}
sb = an.plan_storyboard(product_name="힐튼 경주", usp="", mode="travel", api_key="fake", photo_inventory=inv, force=True, hook_hints=hints)
prompt_text = captured["contents"][-1]
check("[최근 떡상 쇼츠 후킹 패턴 참고" in prompt_text and "이 가격에 {대상}이 나온다고?" in prompt_text, "숙소 7씬 프롬프트에 힌트 블록 주입")
check(sb[0]["hook_title"] == "여기 진짜 한국 맞아요?" and sb[0]["voiceover"] != "이 가격에 이 뷰가 나온다고?",
      f"원문 복제 후킹/대사 차단 → hook='{sb[0]['hook_title']}', 대사='{sb[0]['voiceover']}'")
check(sb[0].get("hook_reference") and sb[0]["hook_reference"][0]["video_id"] == "v1", "씬 1에 참고 패턴 출처 기록")
sb_no = an.plan_storyboard(product_name="힐튼 경주", usp="", mode="travel", api_key="fake", photo_inventory=inv, force=True)
check("[최근 떡상 쇼츠 후킹 패턴 참고" not in captured["contents"][-1] and "hook_reference" not in sb_no[0], "힌트 없으면 프롬프트/출처 변화 없음")

gg.Client = fake_client('{"scenes": [{"role":"exterior","hook_title":"3초 만에 예약 마감된 숙소","voiceover":"도착하자마자 설레는 풍경!"},{"role":"cta","voiceover":"x"}]}')
osb = ow.plan_owner_storyboard("가평 솔바람 펜션", "", "stay", "", [], num_scenes=2, api_key="fake", hook_hints=hints)
owner_prompt = [c for c in captured["contents"] if isinstance(c, str)][-1]
check("[최근 떡상 쇼츠 후킹 패턴 참고" in owner_prompt, "사장님 플래너 프롬프트에 힌트 블록 주입")
check(osb[0]["hook_title"] == "가평 솔바람 펜션 가봤어요?" and osb[0].get("hook_reference"), f"사장님: 원문 복제 후킹 차단 → '{osb[0]['hook_title']}' + 출처 기록")
gg.Client = REAL_CLIENT

# ---------------------------------------------------------------- 3. 실제 Gemini: 구조는 쓰되 베끼지 않는지
if not live_skip("실제 Gemini: 레이더 패턴 적용 후킹 (숙소 7씬 / 사장님)"):
    assert gg.Client is REAL_CLIENT
    key = os.getenv("GEMINI_API_KEY")
    notes = ["수영장 사진: 야외 수영장 | outdoor pool with mountain view", "수영장 사진: 야외 수영장 | outdoor pool",
             "수영장 사진: 실내 수영장 | indoor pool", "객실 사진: 침대, 마운틴 뷰 | twin beds", "객실 사진: 침대 | king bed",
             "부대시설 사진: 라운지 | lounge", "전경·뷰 사진: 외관, 야경 | night exterior"]
    real = an.plan_storyboard(product_name="힐튼 경주", usp="보문호수 인근 리조트", mode="travel", api_key=key,
                              photo_inventory=inv, slot_photo_notes=notes, force=True, hook_hints=hints)
    print(f"     [실제 Gemini · 숙소 7씬] hook='{real[0]['hook_title']}' / 씬1='{real[0]['voiceover']}' / 적용 패턴={real[0].get('hook_pattern_used')}")
    check(real[0].get("hook_pattern_used") and real[0]["hook_title"] != "여기 진짜 한국 맞아요?"
          and not copies_reference(real[0]["hook_title"], hints),
          "실제 Gemini(숙소): 레이더 패턴 1개를 골라 새 후킹 작성 (기본값·원문 복제 아님)")
    owner_real = ow.plan_owner_storyboard("가평 솔바람 펜션", "계곡 바로 앞, 개별 바베큐장", "stay", "",
                                          [fixture("photo_pool.jpg"), fixture("photo_room.jpg")],
                                          api_key=key, hook_hints=hints, thumb_dir=tempfile.mkdtemp())
    print(f"     [실제 Gemini · 사장님] hook='{owner_real[0]['hook_title']}' / 적용 패턴={owner_real[0].get('hook_pattern_used')}")
    check(owner_real[0].get("hook_pattern_used") and owner_real[0]["hook_title"] != "가평 솔바람 펜션 가봤어요?"
          and not copies_reference(owner_real[0]["hook_title"], hints),
          "실제 Gemini(사장님): 레이더 패턴 적용 후킹 (기본값·원문 복제 아님)")
finish(fails)