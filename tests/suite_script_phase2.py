"""대본 엔진 Phase 2: clamp 비문 방지·조사 자동 보정·1컨셉 연관성/씬 간 반복·훅 해요체·상투구 금지·예시 잔재 소거"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, finish  # noqa: E402
import os
import re
import logging
logging.disable(logging.WARNING)

from core.mode_policy import (OWNER_POLICY, TRAVEL_POLICY, COMMERCE_POLICY, clamp_line_to_policy, create_length_violation_checker)
from core.script_guard import (with_josa, josa, has_batchim, find_concept_drift, concept_coverage_issues, repeated_line_issues,
                               find_banned_style, is_hook_echo, to_haeyo, script_violation_finder, concept_fallback_line)

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


# 1. clamp: 어미를 지어 붙이지 않고, 완결 문장만 남긴다
long_ok = "접었을 때도 깔끔하게 정리되는 모습이라 짐 걱정이 없어요!"                     # 32자: 정상 문장은 그대로
check(clamp_line_to_policy(long_ok, COMMERCE_POLICY) == long_ok, "32자 정상 문장은 자르지 않음 (예전엔 '짐 있어요!'로 난도질)")
for t in ("비 오는 밤에도 자리 잡고 둘러앉아 따뜻하게 쉬어요!", "접이식 화로대라 캠핑장에서 3초 만에 펴져서 편해요!"):
    check(clamp_line_to_policy(t, COMMERCE_POLICY) == t, f"정책 한도 안의 문장 보존: {t}")
over = "캠핑장에 도착해서 짐을 풀자마자 접이식 화로대를 3초 만에 펴고 바로 불멍을 시작하면 따뜻하게 밤을 보낼 수 있어요!"
out = clamp_line_to_policy(over, COMMERCE_POLICY)
check("있어요" not in out.replace("수 있어요", "") and out == over, f"완결되게 줄일 수 없으면 원문 보존 (어미 땜질 없음): {out[:30]}…")
two = "텐트 치기 전에 화로대부터 펴요. 3초면 불 피울 준비가 끝나서 마음이 한결 편해져요!"
check(clamp_line_to_policy(two, COMMERCE_POLICY) in ("텐트 치기 전에 화로대부터 펴요.", two), "문장 부호 단위로 완결 문장만 남김")
check(OWNER_POLICY.max_chars == 30 and COMMERCE_POLICY.max_chars == 32 and TRAVEL_POLICY.max_chars == 30, "정책 글자 수: 사장님 본론 문장 20~28자(최대 30자, 자막 2줄 덩어리), 쇼핑 32·숙소 30")
chk = create_length_violation_checker(COMMERCE_POLICY)
check(chk("가" * 31) is None and chk("가" * 33) is not None, "길이 검사기: 32자까지 허용, 초과 시 재작성 유도")

# 2. 조사 자동 보정
check(with_josa("나무손 공방", "을/를") == "나무손 공방을" and with_josa("오늘식탁", "을/를") == "오늘식탁을", "상호 뒤 을/를")
check(with_josa("스타벅스", "이/가") == "스타벅스가" and with_josa("카페", "이/가") == "카페가" and with_josa("공방", "이/가") == "공방이", "이/가")
check(with_josa("서울", "으로/로") == "서울로" and with_josa("공방", "으로/로") == "공방으로", "으로/로 (ㄹ 받침은 '로')")
check(with_josa("3초", "은/는") == "3초는" and with_josa("10분", "은/는") == "10분은" and has_batchim("100") and not has_batchim("5") and has_batchim("7"), "숫자 읽는 소리 기준 받침 (백·칠은 받침 O, 오는 X)")

# 3. 1컨셉 연관성 / 씬 간 반복
C = "주문 즉시 무쳐내는 신선한 겉절이"
check(find_concept_drift("주차장도 넓어요", C) is not None and find_concept_drift("주문하면 바로 무쳐요", C) is None, "컨셉에 없는 곁가지 자랑(주차) 차단, 컨셉 문장은 통과")
check(find_concept_drift("주차장도 넓어요", C, usp="주차 가능") is None, "사용자가 컨셉·USP에 쓴 주제는 허용")
off = ["주차가 편해요", "커피가 맛있어요", "의자가 푹신해요", "창가 자리가 좋아요"]
check(len(concept_coverage_issues(off, C)) >= 2, "컨셉 키워드가 전혀 없는 대본 → 연관성 위반 탐지")
on = ["주문하면 바로 무쳐요", "아삭한 식감이 살아 있어요", "신선한 채소만 써요", "밥이랑 잘 어울려요"]
check(concept_coverage_issues(on, C) == {}, "컨셉 키워드를 반영한 대본은 통과 (뒷받침 문장 허용)")
A = ["미리 안 만들고, 주문하시면 바로 무쳐드려요!", "주문 들어오면, 주방에서 바로 무쳐내고 있어요!", "싱싱한 채소라 아삭한 식감이 살아 있어요!",
     "주문하시면, 바로 무쳐서 신선하게 드려요!"]
rep = repeated_line_issues(A)
check(1 in rep and 3 in rep and 2 not in rep, f"같은 말 반복 탐지 (식당 A 케이스): {sorted(rep)}")
check(repeated_line_issues(["3초면 펴져요", "접으면 가방에 쏙 들어가요", "비 오는 날에도 불이 잘 붙어요"]) == {}, "서로 다른 내용은 반복으로 보지 않음")
vf = script_violation_finder(A, core_concept=C, hook="주문하면 그때 무쳐요", rhythm=False)
check(vf(A[1]) is not None and vf(A[2]) is None, "violation finder에 반복·컨셉 검사 통합")

# 4. 훅 해요체·중복·상투구
check(to_haeyo("주문하면 그때 무칩니다") == "주문하면 그때 무쳐요", "훅 합쇼체 → 해요체")
check(is_hook_echo("주문하면 그때 무쳐요", "미리 안 만들고 주문 들어오면 바로 무쳐드려요"), "활용형이 달라도 훅 반복 탐지")
check(not is_hook_echo("캠핑장 불멍 준비 끝", "이 화로대는 3초 만에 펴져서 바로 불을 피워요"), "훅과 다른 대사는 통과")
for bad in ("한 땀 한 땀 깎아요", "정성껏 만들어요", "정성 가득 담았어요", "바로 맛보실 수 있어요", "따라서 빨라요", "또한 편해요"):
    check(find_banned_style(bad) is not None, f"상투구/설명조 금지: {bad}")
check(find_banned_style("주문 들어오면 바로 무쳐요") is None, "정상 문장은 금지 패턴에 안 걸림")

check(find_banned_style("그 차이가 확실히예요!") is not None and find_banned_style("드셔 보실 수 있는 자리예요") is not None,
      "부사+'예요' 비문·'~보실 수 있어요' 존대 설명조 금지")
from core.mode_policy import complete_owner_sentence
check(complete_owner_sentence("그 차이가 확실히!") == "그 차이가 확실해요!", "부사 '~히'에 '예요'를 붙이지 않음 (확실히예요 → 확실해요)")

# 5. 안전 문구는 컨셉을 보존
fb = concept_fallback_line(C, 0)
check(C in fb and not find_banned_style(fb), f"컨셉 안전 문구: {fb}")

# 6. 하드코딩 예시 잔재 소거 + 쇼핑 CTA는 행동 1개
src = ""
for rel in ("core/mode_policy.py", "core/planners/owner.py", "core/analyzer.py"):
    src += open(os.path.join(ROOT, rel), encoding="utf-8").read()
for word in ("지역화폐", "상비약", "도 준비돼 있어요", "과자 매대", "든든한 아침"):
    check(word not in src, f"예시 잔재 소거: '{word}'")
from core.planners.commerce import COMMERCE_CTA
from core.planners.commerce_bundle import BUNDLE_CTA
for name, cta in (("쇼핑", COMMERCE_CTA), ("묶음", BUNDLE_CTA)):
    check("고정 댓글" in cta and len(re.findall(r"[.!?]", cta)) == 1 and "남겨 둘게요" not in cta, f"{name} CTA: 이유+행동 1문장 → {cta}")

# 7. 직가공 CTA 조사
from core.analyzer import plan_video_overlay_storyboard
clip = os.path.join(ROOT, "tests", "_fixtures", "phone_landscape.mp4")
sb = plan_video_overlay_storyboard(clip, core_concept="30년 손기술로 깎아내는 맞춤 원목 도마", store_name="나무손 공방", mock=True)
check(sb[-1]["voiceover"] == "네이버에서 나무손 공방을 검색해 보세요!", f"직가공 CTA 조사 보정: {sb[-1]['voiceover']}")
bodies = [s["voiceover"] for s in sb[:-1]]
check(len(set(bodies)) == len(bodies), "직가공 mock 본문이 같은 문장을 반복하지 않음")
finish(fails)
