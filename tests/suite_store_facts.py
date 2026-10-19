import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import json
import os
import shutil

import core.llm.router as rt
os.environ["ANTHROPIC_API_KEY"] = "test-key"        # 라우터가 Claude 경로를 쓰게 (실제 호출은 아래 가짜 응답으로 대체)
from core.store_facts import (build_store_facts, extract_review_nouns, extract_special_menus, facts_to_points, location_context, tag_photos)
import core.pipeline as pl
from core.job import JobContext

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


MENU = """눈꽃버섯 갈비탕 15,000원
돼지갈비 14,000
굴돌솥비빔밥 12,000
한돈짜글이 (2인) 20,000원
공기밥 1,000
물냉면 8,000
유기그릇 물냉면 9,000"""
REVIEWS = """갈비가 정말 맛있어요. 두툼한 격자 칼집이 인상적이고 통새송이버섯이 같이 나와요.
유기그릇 물냉면 시원하고 좋았어요. 반찬 리필도 친절하게 해주세요.
통새송이버섯이 고기랑 잘 어울려요. 격자 칼집 덕분에 양념이 잘 배어 있어요. 대형 주차장 있어서 편해요.
유기그릇 물냉면 꼭 드세요. 반찬 리필 가능해서 좋아요. 대형 주차장도 넓고 좋아요. 사장님 친절해요 최고예요"""

# ---------------------------------------------------------------- ① 특이 메뉴명 / ② 리뷰 반복 명사 / ④ 상권
sm = extract_special_menus(MENU)
check(sm == ["눈꽃버섯 갈비탕", "굴돌솥비빔밥", "한돈짜글이", "유기그릇 물냉면"] and "돼지갈비" not in sm and "물냉면" not in sm and "공기밥" not in sm,
      f"① 특이 메뉴: 수식어가 붙은 메뉴명만 (돼지갈비·물냉면·공기밥 같은 흔한 이름 제외) {sm}")
rn = extract_review_nouns(REVIEWS)
check({"격자 칼집", "유기그릇 물냉면", "반찬 리필", "대형 주차장", "통새송이버섯"} <= set(rn)
      and not any(w in " ".join(rn) for w in ("맛있", "친절", "최고", "좋", "사장님", "인상")),
      f"② 리뷰: 형용사 삭제 후 2회 이상 반복된 명사만 {rn}")
check(extract_review_nouns("국물이 시원해요. 칼집이 한 번 나왔어요.") == [], "② 한 번만 나온 말은 뽑지 않음 (지어내기·우연 방지)")
check("가평" in location_context("경기 가평군 설악면") and location_context("서울 성동구 성수일로") == "" and "주차" not in location_context("경기 가평군"),
      "④ 주소 상권: 관광지 지역만 나들이·여행 외식 상황, 주차 같은 사실은 쓰지 않음, 모르면 빈 값")
facts = build_store_facts(menu_text=MENU, review_text=REVIEWS, address="경기 가평군 설악면")
pts = facts_to_points(facts)
check(any("눈꽃버섯 갈비탕" == p for p in pts) and any(p.startswith("손님 리뷰에서 반복된 것") and "격자 칼집" in p for p in pts)
      and any("가평" in p for p in pts), f"가게 자랑(USP)에 보탤 구절: {pts}")

# ---------------------------------------------------------------- ③ 사진별 피사체 태깅 (비전, 번호별 1:1) + 캐시
work = tmp("store_facts_")
calls = []


def fake_claude(contents, system, temperature, max_tokens):
    n_img = sum(1 for c in contents if isinstance(c, str) and c.startswith("[사진"))
    calls.append(n_img)
    return json.dumps({"photos": [{"index": i + 1, "subject": ["참숯 불판 위 노릇한 갈비", "살얼음 뜬 유기그릇 물냉면"][i % 2]} for i in range(n_img)]},
                      ensure_ascii=False)


rt._overrides = {"claude": fake_claude}
media = [fixture("photo_pool.jpg"), fixture("photo_room.jpg")]
cache = os.path.join(work, "photo_tags.json")
tags = tag_photos(media, business_name="효정명인갈비", api_key="g", thumb_dir=work, cache_path=cache)
tags2 = tag_photos(media, business_name="효정명인갈비", api_key="g", thumb_dir=work, cache_path=cache)
check(tags == {media[0]: "참숯 불판 위 노릇한 갈비", media[1]: "살얼음 뜬 유기그릇 물냉면"} and tags2 == tags and len(calls) == 1,
      f"③ 사진별 피사체: 사진마다 1:1 태그, 같은 사진은 캐시로 다시 부르지 않음 (호출 {len(calls)}회)")
check(tag_photos(media, mock=True) == {}, "Mock 모드에서는 비전 호출 없음")

# ---------------------------------------------------------------- 기획 연결: 사진 태그·팩트가 대본 프롬프트로, 사진 번호와 문장 번호가 맞물림
sent = {}


def claude_plan(contents, system, temperature, max_tokens):
    last = contents[-1] if isinstance(contents[-1], str) else ""
    if '"photos"' in last:                       # 사진 피사체 태깅 호출
        return fake_claude(contents, system, temperature, max_tokens)
    if '"scenes"' in last:
        sent["prompt"] = last
    return json.dumps({"story": "...", "scenes": [{"scene_index": i + 1, "role": "exterior", "hook_title": "가평 갈비 가는 길이에요" if i == 0 else "",
                                                    "voiceover": ["가평 가족 외식 고민이면 여기예요.", "참숯 불판 위 갈비가 노릇하게 익어가요.", "눈꽃버섯 갈비탕으로 속이 든든해요.",
                                                                  "유기그릇 물냉면이 살얼음 떠서 시원해요.", "x"][i]} for i in range(5)]}, ensure_ascii=False)


rt._overrides = {"claude": claude_plan}
J = JobContext.create(app="store_facts")
J.root = work
J = JobContext(J.job_id, root=work)
ups = []
for i in range(8):
    dst = os.path.join(J.uploads_dir, f"u{i}.jpg")
    shutil.copy(fixture("photo_pool.jpg" if i % 2 == 0 else "photo_room.jpg"), dst)
    ups.append(dst)
spec = pl.JobSpec(mode="owner", name="효정명인갈비", usp="참숯 갈비, 눈꽃버섯 갈비탕", business_type="food", search_keyword="효정명인갈비",
                  address="경기 가평군 설악면", media_paths=ups, mock=False, api_key="g", store_facts=facts)
sc = pl.plan(spec, J)
rt._overrides = {}
p = sent.get("prompt", "")
check(len(sc) == 5 and "눈꽃버섯 갈비탕" in p and "격자 칼집" in p and "사진 1번" in p and "사진 8번" in p and "문장 4에서 나옴" in p
      and "참숯 불판 위 노릇한 갈비" in p and sc[0].get("photo_tags"),
      "기획: 자동 사진 태깅 → 팩트·사진 번호별 피사체가 프롬프트에 들어가고 문장 번호와 맞물림, 화면 확인용 photo_tags 저장")
shutil.rmtree(work, ignore_errors=True)
finish(fails)
