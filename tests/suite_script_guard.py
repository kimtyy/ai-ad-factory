import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import sys

os.chdir(ROOT)
sys.path.insert(0, ROOT)
import logging
logging.disable(logging.WARNING)
from core.script_guard import shorten_line, find_unsupported_claim, safe_line_for
from core.analyzer import plan_storyboard
import core.analyzer as an

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


# 1. 어절 경계 길이 맞춤
cases = [
    "침대 추가해서 온 가족이 함께 즐겨보세요!",          # 지난 실측에서 '즐겨보!'로 잘린 문장
    "탁 트인 창밖 뷰를 보며 하루 종일 힐링하고 싶어지는 객실!",
    "사계절 내내 따뜻한, 야외 온수 수영장에서 물놀이 즐겨보세요!",
    "여기진짜한국맞아요정말믿을수없는풍경이에요!",           # 띄어쓰기 없는 덩어리
    "아직도 이 숙소 모르세요?",                            # 이미 짧음
]
for c in cases:
    out = shorten_line(c)
    print(f"     '{c}' ({len(c)}자) → '{out}' ({len(out)}자)")
outs = [shorten_line(c) for c in cases]
check(all(len(o) <= 22 for o in outs), "모두 22자 이내")
check(outs[0] == "침대 추가해서 온 가족이 함께!" or not outs[0].endswith(("보!", "즐겨!")), f"단어 중간 절단 없음: '{outs[0]}'")
check(not outs[1].rstrip("!").endswith(("하고", "보며", "해서")), f"끝에 연결어미 어절 남지 않음: '{outs[1]}'")
check(not outs[2].rstrip("!").rstrip(",").endswith(("따뜻한", "하는", "있는")), f"끝에 관형형(~한/~는) 남지 않음: '{outs[2]}'")
check(outs[4] == cases[4] and outs[4].endswith("?"), "짧은 문장은 그대로, 물음표 유지")

# 2. 근거 기반 주장 판정
indoor = "수영장 사진: 실내 수영장 | Spacious indoor pool with glass atrium"
check(find_unsupported_claim("따뜻한 온수풀에서 힐링!", [indoor]) == "온수", "실내 수영장 사진에 '온수' → 차단")
check(find_unsupported_claim("따뜻한 온수풀에서 힐링!", [indoor], usp="사계절 온수풀") is None, "USP에 '온수' 명시 → 허용")
check(find_unsupported_claim("자쿠지에서 물멍!", ["수영장 사진: 자쿠지 | jacuzzi"]) is None, "자쿠지 태그 사진 → '자쿠지' 허용")
check(find_unsupported_claim("침대 추가해서 온 가족 함께!", ["객실 사진: 침대, 마운틴 뷰 | twin beds"]) is not None, "근거 없는 '침대 추가' 차단")
check(find_unsupported_claim("레스토랑에서 여유로운 식사!", ["부대시설 사진: 레스토랑 | Elegant restaurant"]) is None,
      "비전 확인된 레스토랑 사진 → '레스토랑' 허용 (기존 고정 단어 목록은 차단했음)")
check(find_unsupported_claim("오늘만 30% 할인!", [indoor]) == "30%", "USP에 없는 할인율 차단")
check(find_unsupported_claim("오션뷰 객실!", ["객실 사진: 침대, 마운틴 뷰 | mountain view"]) == "오션", "마운틴 뷰 사진에 '오션뷰' 차단")
check(safe_line_for("bedroom_or_view", "전경·뷰 사진: 마운틴 뷰", 0) == "눈앞에 펼쳐지는 그림 같은 뷰!", "대체 문구는 실제 사진 카테고리(뷰) 기준")

# 3. plan_storyboard 후처리: Gemini 응답을 가짜로 주입해 실측 사례 재현
class FakeResp:
    text = '''{"scenes": [
      {"voiceover": "아직도 휴가 숙소 고민하면 진짜 손해!", "hook_title": "여기 진짜 한국 맞아요?"},
      {"voiceover": "따뜻한 온수풀에서 하루 종일 물놀이!"},
      {"voiceover": "탁 트인 실내 수영장도 정말 최고입니다!"},
      {"voiceover": "마운틴 뷰 객실에서 꿀잠 자고 오세요!"},
      {"voiceover": "침대 추가해서 온 가족이 함께 즐겨보세요!"},
      {"voiceover": "우아한 레스토랑에서 여유로운 한 끼!"},
      {"voiceover": "아무거나"}]}'''


class FakeModels:
    def generate_content(self, **kw):
        return FakeResp()


class FakeClient:
    def __init__(self, api_key=None):
        self.models = FakeModels()


an.genai.Client = FakeClient
notes = ["수영장 사진: 야외 수영장 | outdoor pool", "수영장 사진: 야외 수영장 | outdoor pool", indoor,
         "객실 사진: 침대, 객실 내부, 마운틴 뷰 | twin beds", "객실 사진: 침대, 마운틴 뷰 | balcony view",
         "부대시설 사진: 레스토랑, 라운지 | Elegant restaurant", "전경·뷰 사진: 외관, 야경 | night exterior"]
inv = {"pool": 3, "bedroom": 2, "facility": 2, "view": 1}
sb = plan_storyboard(product_name="힐튼 경주", usp="", mode="travel", api_key="fake", photo_inventory=inv,
                     slot_photo_notes=notes, force=True)
for s in sb:
    print(f"     씬{s['scene_index']}: {s['voiceover']}")
check(sb[1]["voiceover"] == "물빛만 봐도 힐링되는 수영장!", f"씬2 근거 없는 '온수' → 중립 문구: {sb[1]['voiceover']}")
check("침대 추가" not in sb[4]["voiceover"] and not sb[4]["voiceover"].endswith("보!"), f"씬5 '침대 추가' 차단 + 잘림 없음: {sb[4]['voiceover']}")
check("레스토랑" in sb[5]["voiceover"], f"씬6 비전 확인 레스토랑 대사 유지: {sb[5]['voiceover']}")
check(sb[-1]["voiceover"] == "숙소 정보는 댓글에 '여행' 남겨주세요!", "엔딩 CTA 고정 유지")
check(all(s["subtitle"] == s["voiceover"] or s is sb[-1] for s in sb), "자막 = 나레이션 동기화")
finish(fails)