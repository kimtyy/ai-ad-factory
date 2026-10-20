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
check(len(sc) == 5 and "눈꽃버섯 갈비탕" in p and "격자 칼집" in p and "사진 1번" in p and "사진 8번" in p and "문장 4 구간 화면" in p
      and "참숯 불판 위 노릇한 갈비" in p and sc[0].get("photo_tags"),
      "기획: 자동 사진 태깅 → 팩트·사진 번호별 피사체가 프롬프트에 들어가고 문장 번호와 맞물림, 화면 확인용 photo_tags 저장")
# ---------------------------------------------------------------- 상호명은 마지막 CTA에서만: LLM이 1~4문장에 상호를 써도 제거, 가짜 메뉴는 안전 문구로
from core.planners.owner import plan_owner_storyboard


def claude_leaky(contents, system, temperature, max_tokens):
    return json.dumps({"story": "...", "scenes": [{"scene_index": i + 1, "role": "exterior", "hook_title": "가평 갈비 가는 길이에요" if i == 0 else "",
                                                    "voiceover": ["가평 효정명인갈비, 이름부터 믿음직하죠!", "촘촘한 칼집 사이로 달큰한 양념이 쏙 배어들어요.",
                                                                  "양념게장이 한 상 가득 나와요.", "시뻘건 참숯 불향에 노릇하게 구워내요.", "x"][i]} for i in range(5)]},
                      ensure_ascii=False)


rt._overrides = {"claude": claude_leaky}
sl = plan_owner_storyboard("효정명인갈비", "숯불 양념 생갈비, 칼집, 참숯 불판", "food", "효정명인갈비", ups[:5], api_key="g", region="경기 가평군",
                           store_facts={"special_menus": [], "review_nouns": [], "location_context": ""}, photo_tags={})
rt._overrides = {}
body = [x["voiceover"] for x in sl[:-1]]
check(not any("효정명인갈비" in v or "효정" in v for v in body) and "효정명인갈비" in sl[-1]["voiceover"] and sl[-1]["role"] == "cta"
      and not any("양념게장" in v for v in body) and any("칼집" in v for v in body),
      f"상호명은 마지막 CTA에서만 공개 (1~4문장의 상호 제거), 입력에 없는 '양념게장'은 안전 문구로, 칼집·양념 미각 묘사는 유지: {body[:2]}")
# ---------------------------------------------------------------- 1컨셉 1메시지: 컨셉을 보여 주는 사진이 앞쪽 컷, 외관은 마지막, 마지막 문장은 요약+저장 CTA
from core.planners.owner import order_media_for_concept
tags8 = {ups[0]: "가게 외관 간판", ups[1]: "참깨 뿌린 물냉면", ups[2]: "수제 양념에 재운 갈비 원육", ups[3]: "상차림 반찬", ups[4]: "불판 위 수제 양념 갈비",
         ups[5]: "통버섯", ups[6]: "수제 양념 칼집 낸 생갈비", ups[7]: "공기밥"}
ordr = order_media_for_concept(ups, tags8, "직접 재운 수제양념갈비")
check(ordr[:3] == [2, 4, 6] and ordr[-1] == 0 and sorted(ordr) == list(range(8)) and order_media_for_concept(ups, {}, "수제양념갈비") == list(range(8))
      and order_media_for_concept(ups, tags8, "") == [1, 2, 3, 4, 5, 6, 7, 0],
      f"사진 배치: 컨셉(수제 양념 갈비)을 보여 주는 사진이 앞쪽 컷, 외관은 마지막(CTA) 컷, 태그·컨셉이 없으면 올린 순서 그대로(외관만 맨 뒤) {ordr}")


def claude_concept(contents, system, temperature, max_tokens):
    return json.dumps({"core_concept": "직접 재운 수제양념갈비", "story": "...", "scenes": [{"scene_index": i + 1, "role": "exterior", "hook_title": "수제 양념 갈비" if i == 0 else "",
                       "voiceover": ["똑같은 돼지갈비인데 왜 굽자마자 타버릴까요?", "단맛만 강한 양념은 겉면만 태워요.", "직접 재운 수제 양념이 칼집 속까지 스며들어요.",
                                     "마지막 한 점까지 은은한 감칠맛이 돌아요.", "제대로 재운 수제양념갈비 찾으신다면 지금 저장하고 가평 효정명인갈비를 검색해 보세요!"][i]}
                      for i in range(5)]}, ensure_ascii=False)


rt._overrides = {"claude": claude_concept}
sc_c = plan_owner_storyboard("효정명인갈비", "수제 양념 갈비", "food", "효정명인갈비", ups, api_key="g", region="경기 가평군", core_concept="직접 재운 수제양념갈비",
                             store_facts={"special_menus": [], "review_nouns": [], "location_context": ""}, photo_tags=tags8)
rt._overrides = {}
check(sc_c[-1]["voiceover"].startswith("제대로 재운 수제양념갈비 찾으신다면") and sc_c[-1]["voiceover"].endswith("지금 저장하고 가평 효정명인갈비를 검색해 보세요!")
      and sc_c[0].get("photo_reordered") and [s["media_group"] for s in sc_c] == [[2, 4], [6], [1, 3], [5, 7], [0]],
      f"1컨셉 5문장: 마지막 문장은 1메시지 요약 + 저장 CTA를 채택, 사진은 컨셉 순서로 재배치(올린 순서 번호로 기록): {[s.get('media_group') for s in sc_c]}")
# ---------------------------------------------------------------- 5단계 버킷 정렬: 무작위 업로드도 1→5 순서, 외관은 마지막 컷에만 (여분의 외관은 제외)
from core.planners.owner import media_groups
bk = {ups[0]: 5, ups[1]: 4, ups[2]: 1, ups[3]: 3, ups[4]: 2, ups[5]: 1, ups[6]: 4, ups[7]: 5}          # 외관이 첫 번째로 올라온 무작위 순서 + 외관 2장
tg = {ups[0]: "가게 외관 간판", ups[1]: "물냉면", ups[2]: "노릇하게 구워진 갈비", ups[3]: "석쇠 위 갈비", ups[4]: "생갈비 칼집", ups[5]: "고기 자르는 장면",
      ups[6]: "밑반찬", ups[7]: "주차장 바닥"}
o5 = order_media_for_concept(ups, tg, "수제 양념 갈비", bk)
check([bk[ups[k]] for k in o5] == [1, 1, 2, 3, 4, 4, 5] and len(o5) == 7 and o5[-1] == 0 and 7 not in o5 and len(media_groups(len(o5))) == 5,
      f"5단계 버킷 정렬: 외관이 먼저 올라와도 1→5단계 순서, 외관은 마지막 컷 한 장만(남는 외관 제외), 사진 7장 → 5문장 {[bk[ups[k]] for k in o5]}")
sc_b = plan_owner_storyboard("효정명인갈비", "수제 양념 갈비", "food", "효정명인갈비", ups, mock=True, region="경기 가평군", core_concept="수제 양념 갈비",
                             photo_tags=tg, photo_buckets=bk)
check(sc_b[0]["media_group"] == [o5[0], o5[1]] and sc_b[-1]["media_group"] == [0] and sc_b[0].get("photo_reordered"),
      "기획: 첫 문장(0~3초) 컷은 버킷 1(먹음직한 고기), 마지막 문장 컷은 가게 외관")
rt._overrides = {"claude": lambda contents, system, temperature, max_tokens: json.dumps({"photos": [
    {"index": i + 1, "subject": "고기", "bucket": [5, 1, 3][i % 3]} for i in range(sum(1 for c in contents if isinstance(c, str) and c.startswith("[사진")))]}, ensure_ascii=False)}
from core.store_facts import analyze_photos
calls_b = []
_orig_gj = rt.generate_json


def _count_gj(*a, **k):
    calls_b.append(1)
    return _orig_gj(*a, **k)


rt.generate_json = _count_gj
import core.llm as _llm_pkg
_llm_pkg.generate_json = _count_gj
cache_b = os.path.join(work, "buckets.json")
t1, b1 = analyze_photos(ups[:3], business_name="효정명인갈비", api_key="g", thumb_dir=work, cache_path=cache_b)
t2, b2 = analyze_photos(ups[:3], business_name="효정명인갈비", api_key="g", thumb_dir=work, cache_path=cache_b)
rt._overrides = {}
rt.generate_json = _orig_gj
_llm_pkg.generate_json = _orig_gj
check(list(b1.values()) == [5, 1, 3] and b1 == b2 and len(calls_b) == 1, f"비전이 사진마다 버킷(1~5)을 매기고 같은 사진은 캐시 사용 ({list(b1.values())}, 호출 {len(calls_b)}회)")
# ---------------------------------------------------------------- 날고기(굽기 전) vs 다 구워진 고기: 1번(첫 3초·썸네일)에는 익은 고기만
from core.store_facts import fix_bucket
check(fix_bucket(1, "은색 석쇠 위에 막 올린 양념 생갈비", "raw") == 2 and fix_bucket(1, "접시에 담긴 양념 갈비", "") == 2
      and fix_bucket(1, "참숯 불 위에서 노릇하게 구워진 갈비", "cooked") == 1 and fix_bucket(1, "가위로 고기 자르는 장면", "") == 1
      and fix_bucket(1, "석쇠 위 갈비", "grilling") == 3 and fix_bucket(3, "접시에 담긴 갈비", "raw") == 3 and fix_bucket(1, "양념 갈비", "") == 1,
      "버킷 보정: 굽기 전 생고기·접시 원육은 1번에서 2번으로, 구워지는 중은 3번으로 내리고 다 익은 고기·자르는 장면만 1번")
seen_v = {}


def claude_doneness(contents, system, temperature, max_tokens):
    last = contents[-1] if isinstance(contents[-1], str) else ""
    seen_v["prompt"] = last
    n = sum(1 for c in contents if isinstance(c, str) and c.startswith("[사진"))
    rows = [{"index": 1, "subject": "은색 석쇠 위에 막 올린 양념 생갈비", "bucket": 1, "doneness": "raw"},
            {"index": 2, "subject": "참숯 불 위에서 노릇하게 구워진 갈비", "bucket": 1, "doneness": "cooked"},
            {"index": 3, "subject": "접시에 담긴 양념 갈비", "bucket": 1, "doneness": ""}]
    return json.dumps({"photos": rows[:n]}, ensure_ascii=False)


rt._overrides = {"claude": claude_doneness}
cache_d = os.path.join(work, "doneness.json")
td, bd = analyze_photos(ups[:3], business_name="효정명인갈비", api_key="g", thumb_dir=work, cache_path=cache_d)
rt._overrides = {}
pv = seen_v.get("prompt", "")
check([bd[ups[k]] for k in range(3)] == [2, 1, 2] and "전 업종 로컬 매장 미디어 5단계 시각 정렬기" in pv and "unready_raw_meat" in pv and "ready_to_eat" in pv
      and "모둠회, 물회, 초밥, 육회, 생맥주, 케이크, 샐러드" in pv and "readiness" in pv,
      f"비전 분류: 굽기 전 생갈비·접시 원육이 1번으로 오면 2번으로 보정, 익은 고기만 1번, 프롬프트에 구분 기준 포함 {[bd[ups[k]] for k in range(3)]}")
json.dump({**json.load(open(cache_d, encoding="utf-8")), "stale": {}}, open(cache_d, "w", encoding="utf-8"))
old_cache = {k: {"s": v["s"], "b": 1, "asked": True} for k, v in json.load(open(cache_d, encoding="utf-8")).items() if isinstance(v, dict) and v.get("s")}
json.dump(old_cache, open(cache_d, "w", encoding="utf-8"))
calls_d = []
rt._overrides = {"claude": lambda c, sy, t, m: (calls_d.append(1), claude_doneness(c, sy, t, m))[1]}
td2, bd2 = analyze_photos(ups[:3], business_name="효정명인갈비", api_key="g", thumb_dir=work, cache_path=cache_d)
rt._overrides = {}
check(len(calls_d) == 1 and [bd2[ups[k]] for k in range(3)] == [2, 1, 2], "기준이 바뀌기 전 옛 캐시(버전 없음)는 다시 분류해서 잘못된 1번을 바로잡음")
# ---------------------------------------------------------------- 전 업종 범용: 날것으로 먹는 완성 메뉴(회·육회·초밥·생맥주·디저트)는 1번 유지
check(all(fix_bucket(1, s, r) == 1 for s, r in (("모둠회 한 접시", "ready_to_eat"), ("물회", "raw"), ("육회 비빔밥", ""), ("살얼음 생맥주", "none"), ("딸기 케이크", ""),
                                                  ("연어 초밥", "raw"), ("과일 샐러드", ""), ("회덮밥", "")))
      and fix_bucket(1, "굽기 전 양념 생삼겹살", "unready_raw_meat") == 2 and fix_bucket(1, "생버섯", "") == 2 and fix_bucket(1, "조리 전 반죽", "") == 2
      and fix_bucket(1, "석쇠 위에서 구워지는 삼겹살", "cooking") == 3 and fix_bucket(1, "접시에 담긴 생닭", "") == 2,
      "범용 보정: 회·물회·초밥·육회·생맥주·케이크·샐러드 같은 완성 메뉴는 날것이어도 1번 유지, 가열 전 식재료(생갈비·생삼겹·생닭·생버섯·반죽)만 2번, 조리 중은 3번")
# ---------------------------------------------------------------- 📌 내가 올린 순서 그대로 유지: AI 정렬·외관 제외·비전 분류 호출 모두 생략
sc_k = plan_owner_storyboard("효정명인갈비", "수제 양념 갈비", "food", "효정명인갈비", ups, mock=True, region="경기 가평군", core_concept="수제 양념 갈비",
                             photo_tags=tg, photo_buckets=bk, keep_order=True)
check([s["media_group"] for s in sc_k] == [[0, 1], [2], [3, 4], [5, 6], [7]] and not sc_k[0].get("photo_reordered"),
      "순서 유지: 버킷·태그가 있어도 올린 순서 그대로 (첫 번째 사진이 첫 컷, 외관 사진도 제외하지 않고 8장 모두 사용)")
vision_calls = []


def claude_count(contents, system, temperature, max_tokens):
    last = contents[-1] if isinstance(contents[-1], str) else ""
    if '"photos"' in last:
        vision_calls.append(1)
        return fake_claude(contents, system, temperature, max_tokens)
    return claude_concept(contents, system, temperature, max_tokens)


rt._overrides = {"claude": claude_count}
J2 = JobContext.create(app="keep_order")
J2.root = work
J2 = JobContext(J2.job_id, root=work)
ups2 = []
for i in range(8):
    dst = os.path.join(J2.uploads_dir, f"k{i}.jpg")
    shutil.copy(fixture("photo_pool.jpg" if i % 2 == 0 else "photo_room.jpg"), dst)
    ups2.append(dst)
spec_k = pl.JobSpec(mode="owner", name="효정명인갈비", usp="수제 양념 갈비", business_type="food", search_keyword="효정명인갈비", address="경기 가평군",
                    media_paths=ups2, mock=False, api_key="g", core_concept="직접 재운 수제양념갈비", keep_photo_order=True)
sc_pk = pl.plan(spec_k, J2)
n_keep = len(vision_calls)
spec_a = pl.JobSpec(mode="owner", name="효정명인갈비", usp="수제 양념 갈비", business_type="food", search_keyword="효정명인갈비", address="경기 가평군",
                    media_paths=ups2, mock=False, api_key="g", core_concept="직접 재운 수제양념갈비")
J3 = JobContext.create(app="auto_order")
J3.root = work
J3 = JobContext(J3.job_id, root=work)
pl.plan(spec_a, J3)
rt._overrides = {}
check(n_keep == 0 and len(vision_calls) == 1 and [s.get("image_path") for s in sc_pk if s.get("image_path")][:1] == [ups2[0]],
      f"순서 유지를 켜면 사진 분류(비전) 호출이 0회로 빨라지고 첫 컷이 첫 번째로 올린 사진, 끄면 자동 정렬을 위해 1회 호출 ({n_keep} → {len(vision_calls)})")
shutil.rmtree(work, ignore_errors=True)
finish(fails)
