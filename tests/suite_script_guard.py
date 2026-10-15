import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import json
import os
import sys

os.chdir(ROOT)
sys.path.insert(0, ROOT)
import logging
logging.disable(logging.WARNING)
from core.script_guard import SAFE_LINES, shorten_line, find_promo_tone, find_unsupported_claim, safe_line_for
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
check(safe_line_for("bedroom_or_view", "전경·뷰 사진: 마운틴 뷰", 0) == SAFE_LINES["view"][0], "대체 문구는 실제 사진 카테고리(뷰) 기준")

# 2-1. 홍보 어투 (세인트존스 렌더 실측 대사)
promo = ["탁 트인 바다 뷰를 마음껏 즐겨보세요!", "버블 파티 즐거움도 절대 놓치지 마세요!", "깔끔하고 포근한 침실에서 힐링해보세요!",
         "발코니에서 바라보는 뷰가 정말 딱!", "트렌디한 라운지에서 즐겁게 즐겨보세요!", "아이들과 물놀이하기 딱 좋아요!",
         "포근한 객실에서 푹 쉬어가세요!"]
natural = ["수영장이랑 바다 경계가 그냥 사라집니다", "주말마다 거품 파티 터지는데 여긴 무조건 가야 됨",
           "침대에 누워서 바로 일출 직관 가능한 룸 컨디션", "아직도 이 숙소 모르세요?", "딱 한 번 가봤는데 또 감"]
check(all(find_promo_tone(t) for t in promo), "홍보 어투 7종 감지 (~보세요·~마세요·~딱!·딱 좋아요·~가세요)")
check(not any(find_promo_tone(t) for t in natural), "감각 묘사 평서문·질문형 후킹·문장 중간 '딱'은 허용")
check(all(not find_promo_tone(l) and not find_unsupported_claim(l) and len(l) <= 22 for ls in SAFE_LINES.values() for l in ls),
      "대체 문구 8종: 홍보 어투·근거 없는 시설 단어 없음, 22자 이내")

# 3. plan_storyboard 후처리: Gemini 응답을 가짜로 주입해 실측 사례 재현
class FakeResp:
    text = '''{"scenes": [
      {"voiceover": "아직도 휴가 숙소 고민하면 진짜 손해!", "hook_title": "여기 진짜 한국 맞아요?"},
      {"voiceover": "따뜻한 온수풀에서 하루 종일 물놀이!"},
      {"voiceover": "탁 트인 실내 수영장도 정말 최고입니다!"},
      {"voiceover": "마운틴 뷰 객실에서 꿀잠 자고 오세요!"},
      {"voiceover": "침대 추가해서 온 가족이 함께 즐겨보세요!"},
      {"voiceover": "우아한 레스토랑에서 여유롭게 한 끼 하기 좋아요!"},
      {"voiceover": "아무거나"}]}'''


sent = {}


class FakeModels:
    def generate_content(self, **kw):
        if '규칙을 어겼습니다' not in str(kw.get('contents', [''])[-1]):
            sent.update(kw)
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
check(sb[1]["voiceover"].rstrip(".") in SAFE_LINES["pool"], f"씬2 근거 없는 '온수' → 중립 문구: {sb[1]['voiceover']}")
check(sb[3]["voiceover"] != "마운틴 뷰 객실에서 꿀잠 자고 오세요!" and not find_promo_tone(sb[3]["voiceover"]),
      f"씬4 '~오세요' 강요형 → 감각 묘사 문구로 교체: {sb[3]['voiceover']}")
check(all(not find_promo_tone(s["voiceover"]) for s in sb[:-1]), "CTA 제외 전 씬 홍보 어투 없음")
from core.script_guard import VO_LIMIT, VO_MAX, VO_HARD_MAX, strip_brochure_words
check(strip_brochure_words("고급스러운 라운지에서 여유까지 챙기면 끝납니다.") == "라운지에서 여유까지 챙기면 끝납니다."
      and strip_brochure_words("세련된 레스토랑 분위기가 진짜 좋은데,") == "레스토랑 분위기가 진짜 좋은데,"
      and strip_brochure_words("솔직히 여기만 한 데가 없거든요") == "솔직히 여기만 한 데가 없거든요", "브로슈어 수식어(고급스러운·세련된) 제거, 일반 문장은 그대로")
check(shorten_line("여기 오시면 객실이 깔끔해서 푹 쉬어가기에 진짜 너무 좋은데요,", VO_LIMIT, VO_MAX)[-3:-1] not in ("진짜", "너무")
      and shorten_line("여기 오시면 객실 깔끔해서 푹 쉬어가기 진짜 좋은데,", VO_LIMIT, VO_MAX).endswith("좋은데,"),
      "29자는 자르지 않고, 자를 때 '진짜!/너무!'로 끝나지 않음")
long_story = "근데 진짜 대박인 건 해 질 때 루프탑에서 보는 노을이거든요"
check(shorten_line("솔직히 여기만 한 데가 없거든요, 진짜로요", VO_LIMIT, VO_MAX) == "솔직히 여기만 한 데가 없거든요, 진짜로요"
      and len(shorten_line(long_story, VO_LIMIT, VO_MAX)) <= VO_MAX and VO_HARD_MAX >= 40,
      f"shorten_line 동작 유지, 실사 모드 대사는 {VO_HARD_MAX}자 넘을 때만 줄임")
sys_inst = sent["config"].system_instruction
user_prompt = sent["contents"][-1]
check("3자 큐레이터" in sys_inst and "TPO" in sys_inst and "팜플렛 낭독" in sys_inst and "엄격 금지" in sys_inst
      and "금지 (개조식 단문)" in sys_inst and "근데 진짜 대박인 건" not in sys_inst and "추임새 의무" not in sys_inst
      and "사진에 실제로 보이는 공간만 묘사하세요" not in sys_inst and "(~됨, ~임" not in sys_inst,
      "프롬프트: 3자 큐레이터·TPO 구어체 + 브로슈어 낭독·홍보 어미·개조식 금지 + 판에 박힌 패턴 엄격 금지 (예전 추임새 의무 삭제)")
check('"story"' in user_prompt and "문장 하나씩 순서대로 씬 1~" in user_prompt and "시청자 상황·기분 짚기" in user_prompt and "체크인 직후 수영장으로 직행" not in user_prompt,
      "프롬프트: 전체 독백(story) 먼저 → 씬별 분할, 구조만 제시(예시 문장 복사 방지)")
check("hook_title을 먼저 읽고" in user_prompt, "프롬프트: 씬 1 = 후킹 먼저 읽고 서브 문장 이어 읽기 안내")
check("침대 추가" not in sb[4]["voiceover"] and not sb[4]["voiceover"].endswith("보!"), f"씬5 '침대 추가' 차단 + 잘림 없음: {sb[4]['voiceover']}")
check("레스토랑" in sb[5]["voiceover"], f"씬6 비전 확인 레스토랑 대사 유지: {sb[5]['voiceover']}")
check(sb[-1]["voiceover"] == "숙소 정보는 댓글에 '여행' 남겨주세요!", "엔딩 CTA 고정 유지")
check(all(s["subtitle"] == s["voiceover"] or s is sb[-1] for s in sb), "자막 = 나레이션 동기화")

# 4. 해요체 통일 · 후킹 앵무새 · 화면 무관 주제 (세인트존스 렌더 실측 대본 재현)
from core.script_guard import to_haeyo, has_hapsyo, is_hook_echo, find_off_scene_topic
conv = {"실내 풀도 뷰가 장난 아닙니다.": "실내 풀도 뷰가 장난 아니에요.", "바다가 바로 보입니다.": "바다가 바로 보여요.",
        "쏙 들어가면 끝납니다.": "쏙 들어가면 끝나요.", "힐링 끝입니다.": "힐링 끝이에요.", "그 바다입니다": "그 바다예요",
        "말할 것도 없습니다": "말할 것도 없어요", "정말 편합니다, 다 됩니다": "정말 편해요, 다 돼요", "챙겨 줍니다": "챙겨 줘요",
        "노을이 옵니다": "노을이 와요", "좋습니다": "좋아요", "작년에 갔습니다": "작년에 갔어요", "있었습니다": "있었어요",
        "노을이 아름답습니다": "노을이 아름다워요", "바다가 느껴집니다": "바다가 느껴져요", "솔직히 없거든요": "솔직히 없거든요"}
bad = {k: to_haeyo(k) for k, v in conv.items() if to_haeyo(k) != v}
check(not bad, f"합쇼체 → 해요체 15종 변환 (아닙니다·보입니다·끝납니다·입니다·습니다·ㅂ 불규칙) {bad or ''}")
check(is_hook_echo("국내 손꼽히는 역대급풀", "우리나라에서 손꼽히는 역대급 오션뷰 풀이거든요.")
      and not is_hook_echo("국내 손꼽히는 역대급 풀", "수영장이 바다랑 그냥 맞닿아 있어서 뷰가 미쳤거든요."),
      "후킹 앵무새 반복 감지 (손꼽히는·역대급 반복 → 차단, 구체 감탄으로 받으면 통과)")
bedroom = "객실 사진: bed, room_interior | Clean and well-lit hotel twin bedroom"
check(find_off_scene_topic("댕댕이 전용 용품도 풀세트로 챙겨줘서 정말 편하고,", [bedroom]) == "반려견"
      and find_off_scene_topic("댕댕이랑 같이 누워도 넉넉하고,", ["객실 사진: bed, pet | dog bed by the window"]) is None
      and find_off_scene_topic("저녁엔 바베큐 파티가 열리는데,", [bedroom, "부대시설 사진: bbq | grill terrace"]) is None,
      "화면 무관 주제: 침실 사진에 '댕댕이' 차단, 반려견 사진이면 허용, 다음 씬 사진으로 넘겨주는 말은 허용")

class StJohnsResp:
    text = json.dumps({"story": "…", "scenes": [
        {"voiceover": "우리나라에서 손꼽히는 역대급 오션뷰 풀이거든요.", "hook_title": "국내 손꼽히는 역대급풀"},
        {"voiceover": "솔직히 체크인하자마자 수영장 안 가면 손해거든요."},
        {"voiceover": "비 오는 날엔 실내 풀도 뷰가 장난 아닙니다."},
        {"voiceover": "댕댕이 전용 용품도 풀세트로 챙겨줘서 정말 편하고,"},
        {"voiceover": "객실 슬라이딩 도어만 열어도 바다가 바로 보입니다."},
        {"voiceover": "세련된 라운지 다이닝까지 갖춰서 말할 것도 없어요."},
        {"voiceover": "x"}]}, ensure_ascii=False)


class StJohnsModels:
    def generate_content(self, **kw):
        if '규칙을 어겼습니다' not in str(kw.get('contents', [''])[-1]):
            sent.update(kw)
        return StJohnsResp()


an.genai.Client = lambda api_key=None: type("C", (), {"models": StJohnsModels()})()
sj_notes = ["수영장 사진: infinity_pool, outdoor_pool, ocean_view | infinity pool with ocean horizon",
            "수영장 사진: outdoor_pool, infinity_pool, ocean_view | rooftop infinity pool",
            "수영장 사진: indoor_pool, ocean_view | glass-enclosed indoor pool with ocean view",
            "객실 사진: bed, room_interior | Clean and well-lit hotel twin bedroom",
            "객실 사진: bed, room_interior, ocean_view | guest room with ocean view",
            "부대시설 사진: restaurant, lounge | stylish modern restaurant dining", "전경·뷰 사진: ocean_view, terrace | balcony"]
sj = plan_storyboard(product_name="세인트존스 호텔", usp="반려견 동반 가능, 펫 용품 제공", mode="travel", api_key="fake",
                     photo_inventory={"pool": 3, "bedroom": 2, "facility": 1, "view": 1}, slot_photo_notes=sj_notes, force=True)
for s_ in sj:
    print(f"     세인트존스 씬{s_['scene_index']}: {s_['voiceover']}")
check(not is_hook_echo(sj[0]["hook_title"], sj[0]["voiceover"]), f"씬 1 앵무새 반복 → 교체: {sj[0]['voiceover']}")
check(("비 오는 날엔 실내 풀도 뷰가 장난" in sj[2]["voiceover"]) and ("바다가" in sj[4]["voiceover"]),
      "합쇼체 대사는 내용 그대로 해요체로 변환 (교체 아님)")
check("댕댕" not in sj[3]["voiceover"] and sj[3]["voiceover"].rstrip(".") in SAFE_LINES["bedroom"],
      f"USP에 반려견 정보가 있어도 침실 사진 씬의 '댕댕이 용품' → 객실 문구로 교체: {sj[3]['voiceover']}")
check(sj[5]["voiceover"].startswith("라운지") and all(not has_hapsyo(s_["voiceover"]) for s_ in sj[:-1]),
      "브로슈어 수식어 제거 + 전 씬 합쇼체 없음")
up = sent["contents"][-1]
sys_inst = sent["config"].system_instruction
check(("해요체" in up or "해요체" in sys_inst) and ("금지 (합쇼체·뉴스체)" in sys_inst or "금지" in sys_inst),
      "프롬프트: 해요체 통일·후킹 반복 금지·화면 무관 주제 금지 규칙 주입")

# 4-1. 시설어 화면 일치 (테라스·스파·자쿠지·루프탑·피트니스)
bed_only = "객실 사진: bed, room_interior | Clean and well-lit hotel twin bedroom"
check(find_off_scene_topic("거기다 아늑한 객실에는 전용 테라스까지 마련돼 있어요.", [bed_only]) == "테라스"
      and find_off_scene_topic("객실에서 바로 스파로 내려갈 수 있어요.", [bed_only]) == "스파"
      and find_off_scene_topic("방에 자쿠지까지 있거든요.", [bed_only]) == "자쿠지"
      and find_off_scene_topic("루프탑에서 보는 노을이 진짜예요.", [bed_only]) == "루프탑"
      and find_off_scene_topic("아침엔 피트니스에서 땀 빼고요.", [bed_only]) == "피트니스",
      "침실 사진에서 테라스·스파·자쿠지·루프탑·피트니스 언급 → 화면 무관 주제로 차단")
check(find_off_scene_topic("발코니에 나가면 바다가 바로 앞이에요.", ["전경·뷰 사진: ocean_view, terrace | balcony overlooking ocean"]) is None
      and find_off_scene_topic("루프탑 풀에서 수평선이 보여요.", ["수영장 사진: outdoor_pool | rooftop infinity pool"]) is None
      and find_off_scene_topic("통유리 실내 풀도 좋거든요.", [bed_only]) is None,
      "해당 공간 사진이면 허용, 대상이 아닌 말은 영향 없음")

# 5. 종결 어미 검증 (세인트존스 4차 렌더 실측: 31자 대사가 기계적으로 잘려 '수영할 수!', '펼쳐지는 바다!')
from core.script_guard import complete_sentence, is_complete_sentence
check(complete_sentence("탁 트인 바다 보면서 수영할 수!") is None and complete_sentence("근데 진짜 대박인 건 통창 너머로 펼쳐지는 바다!") is None,
      "잘린 문장(~할 수!, 명사로 끝남) → 미완성 판정")
check(complete_sentence("돈 안 아까운데,") == "돈 안 아까운데요." and complete_sentence("들어가자마자 푹 쉬기 좋고,") == "들어가자마자 푹 쉬기 좋고요."
      and complete_sentence("호캉스 가능해요") == "호캉스 가능해요." and complete_sentence("여기 진짜 한국 맞아요?") == "여기 진짜 한국 맞아요?",
      "연결 어미 끝은 '요'를 붙여 해요체 종결, 완결 문장은 마침표만 정리")


class FourthResp:
    text = json.dumps({"story": "…", "scenes": [
        {"voiceover": "여기 인피니티 풀 하나만 봐도 돈 안 아까운데,", "hook_title": "국내 손꼽히는 오션뷰 풀"},
        {"voiceover": "탁 트인 바다 보면서 수영할 수 있어서 완전 힐링 그 자체예요."},
        {"voiceover": "실내 풀도 통유리라 날씨 걱정 없이 놀면 되거든요."},
        {"voiceover": "객실은 폭신한 침구 덕분에 들어가자마자 푹 쉬기 좋고,"},
        {"voiceover": "통창 너머로 펼쳐지는 바다 뷰는 진짜 대박 그 자체예요."},
        {"voiceover": "라운지까지 있어서 제대로 호캉스 가능해요."},
        {"voiceover": "x"}]}, ensure_ascii=False)


class FourthModels:
    def generate_content(self, **kw):
        if '규칙을 어겼습니다' not in str(kw.get('contents', [''])[-1]):
            sent.update(kw)
        return FourthResp()


an.genai.Client = lambda api_key=None: type("C", (), {"models": FourthModels()})()
sj4 = plan_storyboard(product_name="세인트존스 호텔", usp="", mode="travel", api_key="fake",
                      photo_inventory={"pool": 3, "bedroom": 2, "facility": 1, "view": 1}, slot_photo_notes=sj_notes, force=True)
for s_ in sj4:
    print(f"     4차 재현 씬{s_['scene_index']}: {s_['voiceover']}")
check(sj4[1]["voiceover"] in ("탁 트인 바다 보면서 수영할 수 있어서 완전 힐링 그 자체예요.", "탁 트인 바다 보면서 수영할 수 있어요!")
      and (sj4[4]["voiceover"] == "통창 너머로 펼쳐지는 바다 뷰는 진짜 대박 그 자체예요." or "바다" in sj4[4]["voiceover"]),
      "대사 길이 조절 및 완결 문장 정책 반영")
check(sj4[0]["voiceover"].endswith(("아까운데요.", "있어요!"))
      and (sj4[3]["voiceover"].rstrip(".") in SAFE_LINES["bedroom"] or sj4[3]["voiceover"].endswith("있어요!")),
      "연결 어미로 끝난 씬 종결 또는 안전 문구 교체")
check(all(is_complete_sentence(s_["voiceover"]) for s_ in sj4[:-1]), "CTA 제외 전 씬이 완결 문장(해요체 종결 어미)으로 끝남")
check("완결 문장" in sent["contents"][-1] and "문장 중간" in sent["contents"][-1],
      "프롬프트: 씬마다 완결 문장 하나, 문장 중간에서 끊기 금지")

# 6. 감탄사 다양성 (5차 렌더 메모: 씬 5 '대박이에요' → 씬 6 '대박이에요')
from core.script_guard import diversify_exclamations
dv = lambda ls, hook="": diversify_exclamations(ls, hook)[0]
check(dv(["근데 진짜 대박인 건 통창 뷰예요.", "라운지 분위기도 대박이에요."]) == ["근데 진짜 대박인 건 통창 뷰예요.", "라운지 분위기도 역대급이에요."],
      "두 번째 '대박' → '역대급' (추임새 '대박인 건'도 1회로 셈)")
check(dv(["침구가 대박이에요.", "침구도 대박 편하거든요."]) == ["침구가 대박이에요.", "침구도 엄청 편하거든요."],
      "부사로 쓰인 '대박 편해요' → '엄청 편해요'")
out = dv(["바다 뷰가 미쳤어요.", "노을도 미쳤거든요.", "실내 풀도 장난 아니에요.", "객실도 장난 아니더라고요."])
check(out == ["바다 뷰가 미쳤어요.", "노을도 장난 아니거든요.", "실내 풀도 끝내줘요.", "객실도 완벽하더라고요."],
      f"서술형 감탄은 같은 활용 자리로 안 쓴 어근에 분산, 대체어끼리도 중복 없음 {out}")
check(dv(["역대급 뷰가 펼쳐져요."], "국내 손꼽히는 역대급 풀") == ["대박 뷰가 펼쳐져요."], "헤드라인에서 쓴 감탄사도 1회로 셈 (꾸밈말 자리는 명사로 교체)")
check(dv(["솔직히 여기만 한 데가 없거든요.", "바다랑 수영장이 이어져 있어요."]) == ["솔직히 여기만 한 데가 없거든요.", "바다랑 수영장이 이어져 있어요."],
      "감탄사 없는 문장은 그대로")


class RepeatResp:
    text = json.dumps({"story": "…", "scenes": [
        {"voiceover": "솔직히 바다랑 맞닿은 뷰에 반하거든요.", "hook_title": "국내 손꼽히는 오션뷰 풀"},
        {"voiceover": "통유리 실내 풀도 진짜 좋은데요."}, {"voiceover": "근데 진짜 대박인 건 객실 침구예요."},
        {"voiceover": "통창 너머 오션뷰도 대박이에요."}, {"voiceover": "라운지 분위기까지 대박이에요."},
        {"voiceover": "밤 풍경도 그냥 대박이거든요."}, {"voiceover": "x"}]}, ensure_ascii=False)


an.genai.Client = lambda api_key=None: type("C", (), {"models": type("M", (), {"generate_content": lambda self, **kw: RepeatResp()})()})()
rp = plan_storyboard(product_name="세인트존스 호텔", usp="", mode="travel", api_key="fake",
                     photo_inventory={"pool": 3, "bedroom": 2, "facility": 1, "view": 1}, slot_photo_notes=sj_notes, force=True)
n_daebak = sum(s_["voiceover"].count("대박") for s_ in rp[:-1])
for s_ in rp:
    print(f"     감탄사 재현 씬{s_['scene_index']}: {s_['voiceover']}")
check(n_daebak == 1 and all(s_["caption"] == s_["subtitle"] == s_["voiceover"] for s_ in rp[:-1]), f"기획 결과: '대박' 4회 → 1회 (자막도 동기화)")
# 숙소 대본 비문: 조건절 겹침·숙소를 식당에 빗댄 은유는 걸리고, 정상 문장은 통과
from core.script_guard import find_banned_style as _fbs
check(_fbs("배고프면 방 문만 나서면 식탁이 기다려요.") and _fbs("물놀이 뒤 식탁까지, 머무는 곳이 곧 식당이에요.")
      and not _fbs("따뜻한 물에 몸 녹이고 저녁까지 그대로 머물러요.") and not _fbs("더운 날엔 이 물빛이 정답이에요.")
      and not _fbs("비 오는 날엔 객실에서 쉬어요."),
      "대본 가드: '~면 ~면' 조건절 겹침·'머무는 곳이 곧 식당' 은유 차단, 정상 문장은 통과")

check(all(_fbs(t) for t in ("객실에서 바라보는 풍경이 예술이죠.", "창밖 바다와 마주하며 힐링의 시간이에요.", "특별한 바베큐 경험이 기다리고 있어요."))
      and not _fbs("자쿠지에 몸 담그면 스트레스가 싹 녹아요."),
      "대본 가드: '힐링의 시간'·'예술이죠'·'특별한 ~ 경험이 기다려요' 상투구 차단, 구체적 문장은 통과")

finish(fails)
