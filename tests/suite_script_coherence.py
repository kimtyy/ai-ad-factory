import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import re

import core.llm as llm
import core.planners.owner as ow
from core import food_profiles as FP
from core.script_guard import find_unphotographed_menu, listing_issues, photo_subject_issues, script_violation_finder

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


# 단어 묶음: 음식 종류와 어울리지 않는 식감 (설맥치킨에 '시원 달콤', 눈꽃빙수에 '바삭 쫄깃'이 붙었던 결함)
COLD_SWEET = ("시원", "달콤", "사르르", "얼음", "차갑")
CRISPY_CHEW = ("바삭", "쫄깃", "튀김", "노릇", "고소")
HOT_SOUP = ("뜨끈", "뜨거", "얼큰")

# ---------------------------------------------------------------- 1. 결함 1 재현: 씬 번호로 형용사를 돌려 쓰면 음식과 형용사가 뒤바뀜
for short in (True, False):
    lines_chicken = [ow.photo_fallback_line("설맥치킨", i, short=short) for i in range(8)]
    lines_ice = [ow.photo_fallback_line("눈꽃빙수", i, short=short) for i in range(8)]
    check(not any(w in l for l in lines_chicken for w in COLD_SWEET), f"설맥치킨 안전 문구({'짧은' if short else '긴'} 형)에는 시원·달콤·사르르가 없음 (씬 0~7 모두): {lines_chicken[2]}")
    check(not any(w in l for l in lines_ice for w in ("바삭", "쫄깃", "튀김", "노릇")), f"눈꽃빙수 안전 문구({'짧은' if short else '긴'} 형)에는 바삭·쫄깃이 없음 (씬 0~7 모두): {lines_ice[3]}")
check(ow.photo_fallback_line("설맥치킨", 2, short=True) != "설맥치킨, 시원 달콤한 감칠맛!" and ow.photo_fallback_line("눈꽃빙수", 3, short=True) != "눈꽃빙수, 바삭 쫄깃한 이 식감!",
      "보고된 두 문장(씬 3 설맥치킨·씬 4 눈꽃빙수)이 더는 나오지 않음")
check(any(w in ow.photo_fallback_line("설맥치킨", 2, short=True) for w in ("바삭", "고소")) and any(w in ow.photo_fallback_line("눈꽃빙수", 3, short=True) for w in ("시원", "사르르", "달콤")),
      "치킨은 바삭·고소, 빙수는 시원·사르르·달콤 — 음식에 맞는 표현")

# 음식 종류별: 서로 어울리지 않는 표현이 섞이지 않음
CASES = {"설맥치킨": (CRISPY_CHEW, COLD_SWEET), "눈꽃빙수": (("시원", "사르르", "달콤", "얼음"), ("바삭", "쫄깃", "뜨끈", "노릇")), "버터먹은먹태": (("노릇", "고소", "씹을수록", "짭짤"), COLD_SWEET + HOT_SOUP),
         "코다리냉면": (("시원", "차갑", "육수", "면발"), ("뜨끈", "노릇", "바삭")), "설맥명태코다리찜": (("푹", "양념", "부드러", "쫀득"), COLD_SWEET + ("바삭",)),
         "김치찌개": (("뜨끈", "국물", "진한"), COLD_SWEET + ("바삭",)), "골뱅이 무침": (("새콤", "매콤", "양념"), ("바삭", "뜨끈", "노릇")),
         "눈꽃맥주": (("시원", "청량", "한 모금", "넘어가"), ("바삭", "뜨끈", "노릇"))}
bad = []
for subj, (want, forbid) in CASES.items():
    for short in (True, False):
        for i in range(6):
            t = ow.photo_fallback_line(subj, i, short=short)
            if any(w in t for w in forbid) or (i == 0 and not any(w in t for w in want)):
                bad.append((subj, i, t))
check(not bad, f"음식 종류 8가지 × 긴·짧은 형 × 씬 6개: 어울리지 않는 식감 없음, 첫 문구는 그 음식에 맞는 표현 {bad[:2]}")
unk = [ow.photo_fallback_line("알수없는요리", i, short=s) for i in range(6) for s in (True, False)]
check(all(not re.search(r"바삭|쫄깃|시원|달콤|노릇|고소|뜨끈|새콤|매콤|사르르|촉촉|부드러", t) for t in unk) and all("알수없는요리" in t for t in unk),
      f"종류를 모르는 피사체는 감각 형용사 없이 중립 문구만 (없는 맛을 지어내지 않음): {unk[0]}")
check(all(len(ow.photo_fallback_line(s, 0, short=True)) <= 24 for s in list(CASES) + ["매장 홀", "알수없는요리"]), "랩형(2초 컷) 안전 문구는 24자 이내 (공간 사진 포함)")
check(FP.profile_for("설맥치킨").key == "fried" and FP.profile_for("눈꽃빙수").key == "shaved_ice" and FP.profile_for("코다리냉면").key == "cold_noodle" and FP.profile_for("설맥명태코다리찜").key == "steamed"
      and FP.profile_for("티라미수").key == "dessert" and FP.profile_for("도토리묵사발") is None, "음식 종류 판별: 치킨·빙수·냉면(찜보다 먼저)·찜·디저트, 모르면 None")

# ---------------------------------------------------------------- 2. 좋은 LLM 대사를 버리지 않음 (메뉴 이름 없이 그 음식을 말해도 같은 사진)
check(photo_subject_issues(["입에서 사르르 녹는 살얼음이 시원해요."], ["눈꽃빙수"]) == {} and photo_subject_issues(["바삭한 한 조각에 맥주가 술술 넘어가요."], ["설맥치킨"]) == {},
      "빙수 사진에 '살얼음', 치킨 사진에 '바삭'으로 말해도 1:1 일치 (좋은 줄을 안전 문구로 바꾸지 않음)")
check(photo_subject_issues(["입에서 사르르 녹는 살얼음이 시원해요."], ["설맥치킨"]) and photo_subject_issues(["봉긋 솟아오른 크림 거품!"], ["과일 화채"]), "엉뚱한 사진 이야기(치킨 사진에 살얼음, 화채 사진에 거품)는 여전히 불일치")
check(photo_subject_issues(["퇴근 후 밥 대신 한 잔 생각나죠?"], ["골뱅이 무침"], hook_first=True) == {} and photo_subject_issues(["이 비주얼이면 못 참죠."], ["골뱅이 무침"], hook_first=True) == {},
      "훅(첫 줄)은 메뉴 이름 없이 컨셉의 상황을 말하거나 '이 비주얼'로 화면을 가리켜도 통과")
check(photo_subject_issues(["퇴근 후 치킨 한 입이죠."], ["골뱅이 무침"], hook_first=True) and photo_subject_issues(["퇴근 후 밥 대신 한 잔 생각나죠?"], ["골뱅이 무침"]),
      "훅이어도 다른 사진의 메뉴(치킨)를 말하면 불일치, 훅이 아닌 줄은 메뉴를 말해야 함")
check(find_unphotographed_menu("퇴근 후엔 맥주 생각나죠.", ["눈꽃맥주"], "골뱅이 무침 먹태") is None and find_unphotographed_menu("시원한 눈꽃맥주 한 잔!", ["눈꽃맥주"], "골뱅이 무침 먹태") == "눈꽃맥주"
      and find_unphotographed_menu("바삭한 치킨이에요.", ["눈꽃치킨"], "골뱅이 무침") == "눈꽃치킨", "공식 메뉴 '눈꽃맥주'가 사진에 없어도 일반 음료어 '맥주'는 허용 (메뉴 이름 자체·고유 메뉴는 계속 차단)")
check(find_unphotographed_menu("소주 한 잔 어때요?", ["소주"], "골뱅이 무침") == "소주", "메뉴 이름이 '소주' 자체면 사진에 없을 때 차단")

# ---------------------------------------------------------------- 3. 결함 2: 마지막 CTA는 18~24자
NAME = "설맥 가평현리점"
cta = ow.rap_cta(NAME, "bar")
check(18 <= len(cta) <= 24 and NAME in cta and cta.endswith("바로 오세요!"), f"랩형 CTA는 18~24자: '{cta}' ({len(cta)}자)")
check(ow.rap_cta(NAME, "") == "오늘은 설맥 가평현리점으로 바로 오세요!" and len(ow.rap_cta(NAME, "")) <= 24, "업종 맥락이 없어도 24자 이내")
check(all(len(ow.rap_cta(n, k)) <= 24 for n in ("효정명인갈비", "어니언 안국", "설맥 가평현리점", "맛있는 가을 하늘 식당") for k in ("bar", "cafe", "")), "여러 상호·맥락에서 24자 이내")
long_name = "아주아주긴이름의명품한우전문점 강남본점"
long_cta = ow.rap_cta(long_name, "bar")
check(long_name in long_cta and long_cta.endswith("오세요!") and "바로" not in long_cta, f"상호가 아주 길면 가장 짧은 형태로 (상호 공개가 우선): '{long_cta}'")
check(ow.valid_rap_cta("오늘 밤 설맥 가평현리점으로 바로 오세요!", NAME) and not ow.valid_rap_cta("오늘 밤 시원한 맥주 한 잔이 생각나시면 설맥 가평현리점으로 지금 바로 오세요 기다릴게요!", NAME)
      and len("오늘 밤 시원한 맥주 한 잔이 생각나시면 설맥 가평현리점으로 지금 바로 오세요 기다릴게요!") >= 44, "LLM이 길게 쓴 CTA(46자 안팎)는 무효, 24자 CTA는 유효")
check(not ow.valid_rap_cta("설맥 가평현리점으로 저장하세요!", NAME) and not ow.valid_rap_cta("오늘 밤 다른가게로 바로 오세요!", NAME), "저장 유도·상호 없는 CTA는 무효")

# ---------------------------------------------------------------- 4. 플래너 전체: 가짜 LLM으로 결함 1·2·3 재현/방지
PATHS = [f"/m/{i}.jpg" for i in range(5)]
TAGS = ["골뱅이 무침", "버터먹은먹태", "설맥치킨", "눈꽃빙수", "매장 외관 간판"]
TAGMAP = dict(zip(PATHS, TAGS))
CONCEPT = "저녁 먹고 들어가기 아쉬울 때, 동네에서 시원한 맥주 한 잔 더"
USP = "눈꽃맥주, 골뱅이 무침, 버터먹은먹태, 설맥치킨, 눈꽃빙수"
FACTS = {"official_menus": ["골뱅이 무침", "버터먹은먹태", "설맥치킨", "눈꽃빙수", "눈꽃맥주"]}

_gj, _ready, _rep, _pv = llm.generate_json, llm.script_llm_ready, llm.repair_scene_lines, ow._media_preview_image
llm.script_llm_ready = lambda k=None: True
llm.repair_scene_lines = lambda d, c, s_, r, **kw: (d, [])
ow._media_preview_image = lambda p, d=None: None
seen = {}


def fake(lines, hook="퇴근 후 한 잔 생각날 때"):
    def _f(contents, system, **kw):
        seen["text"] = "\n".join(c for c in contents if isinstance(c, str))
        seen["system"] = system
        return ({"core_concept": CONCEPT, "story": "퇴근 후 2차, 안주 하나씩, 마무리는 빙수",
                 "scenes": [{"scene_index": i + 1, "role": "signature", "hook_title": hook if i == 0 else "", "voiceover": t} for i, t in enumerate(lines)]}, "claude")
    return _f


def plan(lines, mock=False, **kw):
    llm.generate_json = fake(lines)
    try:
        return ow.plan_owner_storyboard(NAME, USP, "food", NAME, PATHS, api_key="k", mock=mock, region="경기 가평군", core_concept=CONCEPT, store_facts=FACTS,
                                        photo_tags=TAGMAP, keep_order=True, tempo="rap", **kw)
    finally:
        llm.generate_json = _gj


try:
    GOOD = ["퇴근 후 밥 대신 골뱅이 무침이죠!", "쫄깃한 먹태가 맥주 안주로 딱이죠.", "바삭한 한 조각에 맥주가 술술 넘어가요.", "마무리는 사르르 녹는 살얼음으로 시원하게!",
            "오늘 밤 시원한 맥주 한 잔이 생각나시면 설맥 가평현리점으로 지금 바로 오세요 기다릴게요!"]
    sc = plan(GOOD)
    v = [s["voiceover"] for s in sc]
    check(len(sc) == 5 and v[:4] == GOOD[:4], f"좋은 LLM 대사 4줄(빙수는 '살얼음'으로 말함)을 안전 문구로 바꾸지 않고 그대로 사용: {v[:4]}")
    check(len(v[-1]) <= 24 and "설맥 가평현리점" in v[-1] and v[-1].endswith("!"), f"결함 2: LLM이 긴 CTA({len(GOOD[-1])}자)를 써도 마지막 줄은 24자 이내: '{v[-1]}' ({len(v[-1])}자)")
    check(all(14 <= len(x) <= 26 for x in v[:4]), f"중간 줄 길이 14~26자: {[len(x) for x in v[:4]]}")

    # 결함 1: LLM이 엉뚱한 줄을 내서 안전 문구로 교체돼도 음식과 형용사가 맞아야 함
    off = ["어쩌다 보니 이런 날이죠!", "아무 말 대잔치예요!", "그냥 좋은 날이에요!", "아무 얘기나 해요!", "오늘 밤 설맥 가평현리점으로 바로 오세요!"]
    sc_o = plan(off)
    vo = [s["voiceover"] for s in sc_o]
    check(not any(w in vo[2] for w in COLD_SWEET) and not any(w in vo[3] for w in ("바삭", "쫄깃", "노릇", "튀김")) and "설맥치킨" in vo[2] and "눈꽃빙수" in vo[3],
          f"결함 1: 안전 문구로 교체돼도 치킨 줄에 시원·달콤, 빙수 줄에 바삭·쫄깃이 없음: '{vo[2]}' / '{vo[3]}'")
    sc_mock = ow.plan_owner_storyboard(NAME, USP, "food", NAME, PATHS, api_key=None, mock=True, region="경기 가평군", core_concept=CONCEPT, store_facts=FACTS, photo_tags=TAGMAP, keep_order=True, tempo="rap")
    vm = [s["voiceover"] for s in sc_mock]
    check(not any(w in vm[2] for w in COLD_SWEET) and not any(w in vm[3] for w in ("바삭", "쫄깃", "노릇", "튀김")) and len(vm[-1]) <= 24, f"Mock/엔진 실패 경로도 마찬가지, CTA 24자 이내: {vm[2]} / {vm[3]} / {vm[-1]}")

    # 끊긴 비문 금지: 26자를 넘는 줄은 어절을 뚝 자르지 않고 완결된 안전 문구로
    longline = list(GOOD)
    longline[2] = "바삭한 치킨 먹고 맥주 한 모금 쭉 마시면 속까지 달게 넘어가는 기분이에요"
    sc_l = plan(longline)
    t2 = sc_l[2]["voiceover"]
    check(len(t2) <= 26 and "달게!" not in t2 and "설맥치킨" in t2 and not any(w in t2 for w in COLD_SWEET), f"너무 긴 줄('…달게!'로 끊기던 것)은 완결된 안전 문구로: '{t2}'")

    # 훅: 메뉴 이름 없이 컨셉 상황을 말해도 밋밋한 안전 문구로 바뀌지 않음
    hookline = list(GOOD)
    hookline[0] = "퇴근 후 한 잔 생각나는 밤 있죠?"
    sc_h = plan(hookline)
    check(sc_h[0]["voiceover"] == hookline[0], f"컨셉 훅(메뉴 이름 없음)을 그대로 사용: {sc_h[0]['voiceover']}")
    # 다른 사진의 메뉴를 말하는 훅은 여전히 교체
    hookbad = list(GOOD)
    hookbad[0] = "바삭한 치킨 한 입이면 퇴근길이 즐겁죠!"
    sc_hb = plan(hookbad)
    check("치킨" not in sc_hb[0]["voiceover"], f"1번 사진(골뱅이 무침)인데 치킨을 말하는 훅은 교체: {sc_hb[0]['voiceover']}")

    # 결함 3: 프롬프트가 '1컨셉 1메시지 = 한 줄 스토리'로 이어지게 요구
    P = seen["text"]
    S = seen["system"]
    check("한 줄 스토리" in S and "메뉴 이름 + 형용사만 나열" in S and "앞 줄을 받아" in S, "시스템 프롬프트: 한 줄 스토리·메뉴 이름+형용사 나열 금지·앞 줄 받기")
    check("이 영상의 한 줄 스토리" in P and CONCEPT in P and "배경 분위기" not in P and "톤앤매너" not in P, "점주 선택 컨셉이 '배경 톤'이 아니라 영상 전체의 한 줄 스토리로 전달됨")
    check("컨셉 상황과 이어지는 이유" in P and "18~24자" in P and "한 줄 스토리 구조" in P and '"story": "한 줄 스토리' in P, "줄 규칙: 사진 1:1 + 컨셉 이어지는 이유, 마지막 줄 18~24자, story 출력 명세")
    check("1번 씬의 대표 메뉴를 다른 씬에 복붙하지 마세요" in P and "음식과 정반대의 식감" in S, "다른 메뉴 복붙 금지·음식에 맞는 형용사 규칙 유지")
    check("사물 단순 나열 금지" in P and "한 줄 스토리" in P, "공용 1컨셉 지침(사물 나열 금지)과 충돌하지 않고 같은 방향")
finally:
    llm.generate_json, llm.script_llm_ready, llm.repair_scene_lines, ow._media_preview_image = _gj, _ready, _rep, _pv

# ---------------------------------------------------------------- 5. 나열형 대본 탐지 (메뉴 이름 + 형용사만 이어지는 줄)
lst = ["퇴근 후 한 잔 생각날 때", "버터먹은먹태, 노릇하고 고소한 풍미!", "설맥치킨, 바삭 고소한 한 조각!", "눈꽃빙수, 사르르 녹는 시원함!"]
tags = ["골뱅이 무침", "버터먹은먹태", "설맥치킨", "눈꽃빙수"]
li = listing_issues(lst, tags)
check(len(li) == 2 and 0 not in li and all("메뉴 이름 + 형용사만 나열" in m for m in li.values()), f"나열형(메뉴, 형용사!)이 2줄 이상이면 재작성 대상, 훅은 제외 → 줄 {sorted(li)}")
story = ["퇴근 후 밥 대신 골뱅이 무침이죠!", "쫄깃한 먹태가 맥주 안주로 딱이죠.", "바삭한 한 조각에 맥주가 술술 넘어가요.", "마무리는 사르르 녹는 살얼음으로 시원하게!"]
check(listing_issues(story, tags) == {} and listing_issues(lst[:2], tags[:2]) == {}, "스토리로 이어진 줄은 통과, 나열형이 1줄뿐이면 통과")
fn = script_violation_finder(lst, core_concept=CONCEPT, hook=lst[0], usp=USP, lock_topic=True, line_tags=tags)
check(fn(lst[1]) is not None and "나열" in (fn(lst[1]) or ""), "대본 보정 탐지기가 나열형 줄을 LLM 재작성 대상으로 지적")


# ---------------------------------------------------------------- 6. 프롬프트가 가드와 충돌하지 않음 (권장 어미·예시가 금지 패턴이면 모델이 지시대로 써도 버려져 안전 문구가 남발됨)
from core.script_guard import find_banned_style

good_part = ow.FEWSHOT_BLOCK.split("[Good")[1]
good_examples = re.findall(r'"([^"]+)"', good_part)
check(len(good_examples) == 5 and all(find_banned_style(x.replace("[상호]", "설맥 가평현리점")) is None for x in good_examples),
      f"Few-Shot 'Good' 예시 5줄이 대본 가드의 금지 패턴에 걸리지 않음 {[x for x in good_examples if find_banned_style(x.replace('[상호]', '설맥 가평현리점'))]}")
all_prompt_rules = ow.LOCKIN_BLOCK + "\n" + seen.get("text", "")
recommend = [ln for ln in all_prompt_rules.splitlines() if "거든요" in ln and "금지" not in ln]
check(not recommend, f"프롬프트가 '~거든요'를 권장 어미로 안내하지 않음 (금지 어미로만 언급) {recommend[:1]}")

finish(fails)
