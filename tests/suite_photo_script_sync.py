import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import re

from PIL import Image

import core.llm as llm
import core.planners.owner as ow
from core import venue_context as vc
from core.script_guard import photo_subject_issues

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


NAME = "설맥 가평현리점"
TAGS = ["눈꽃맥주 잔", "골뱅이소면 한 접시", "과일 화채", "먹태", "반건조 오징어", "안주 모둠", "매장 홀", "매장 입구 간판"]
PATHS = [f"/m/{i}.jpg" for i in range(8)]
TAGMAP = dict(zip(PATHS, TAGS))

# ---------------------------------------------------------------- 1. 업종 현실 맥락
check(vc.detect(NAME, "눈꽃맥주, 골뱅이소면, 먹태", TAGS) == "bar", "설맥(생맥주·안주 사진) → 맥주집 맥락")
check(vc.detect("효정명인갈비", "숯불 양념 생갈비", ["갈비 구이", "냉면"]) == "meat" and vc.detect("모카하우스", "아메리카노 케이크", ["라떼", "케이크 조각", "커피"]) == "cafe", "고깃집·카페도 맥락 판별")
check(all(vc.find_violation(f"{w} 가서 한 잔", "bar") for w in ("나들이", "관광", "여행길", "드라이브")) and vc.find_violation("주말 외식 장소", "bar") and not vc.find_violation("나들이 가기 좋은 곳", "meat"),
      "맥주집에는 나들이·관광·여행길·드라이브·주말 외식 금지 (고깃집은 해당 없음)")
cta = ow.owner_cta("설맥 가평현리점", "경기 가평군", "food", "bar")
check("주말 외식" not in cta and "한 잔" in cta and cta.endswith("검색해 보세요!"), f"맥주집 CTA에 '주말 외식 장소' 없음: {cta}")
fb = vc.fallback_concepts("bar", NAME, TAGS)
blob = " ".join(f"{c['core_concept']} {c['description']}" for c in fb)
check(len(fb) == 3 and "2차" in fb[0]["badge"] and "1차" in fb[1]["badge"] and "라인업" in fb[2]["core_concept"] and not vc.find_violation(blob, "bar") and "눈꽃맥주" in blob,
      f"맥주집 기본 컨셉 3종: 저녁 먹고 2차 / 퇴근 후 1차 / 안주 라인업 — {[c['core_concept'] for c in fb]}")

# ---------------------------------------------------------------- 2. 컨셉 제안: 사진 전체 + 업종 맥락 프롬프트, 맥락 위반 컨셉 교체
seen = {}
_gj = llm.generate_json


def fake_concepts(contents, system, **kw):
    seen["text"] = [c for c in contents if isinstance(c, str)]
    return ({"concepts": [{"id": "a", "angle": "x", "badge": "x", "headline": "나들이 가서 한 잔?", "core_concept": "가평 나들이 길에 시원한 맥주", "description": "관광"},
                          {"id": "b", "angle": "y", "badge": "y", "headline": "밥 대신 안주", "core_concept": "퇴근 후 밥 대신 안주에 시원한 맥주", "description": "1차"},
                          {"id": "c", "angle": "z", "badge": "z", "headline": "라인업", "core_concept": "눈꽃맥주와 안주 라인업", "description": "사진 전체"}]}, "claude")


import core.planners.owner as ow2
_pv = ow2._media_preview_image
ow2._media_preview_image = lambda p, d=None: Image.new("RGB", (64, 64), (120, 90, 60))
llm.generate_json = fake_concepts
try:
    res = ow.suggest_ad_concepts(NAME, "food", PATHS, "눈꽃맥주 골뱅이소면", api_key="k", mock=False, photo_tags=TAGMAP)
finally:
    llm.generate_json, ow2._media_preview_image = _gj, _pv
prompt = " ".join(seen["text"])
check(all(t in prompt for t in TAGS) and "업종 현실 맥락" in prompt and "나들이" in prompt and "저녁 먹고 2차" in prompt,
      "프롬프트에 확정된 사진 8장 전체의 피사체 + 맥주집 방문 맥락(2차·1차)·금지어 포함")
check(len(res) == 3 and not any(vc.find_violation(f"{c['headline']} {c['core_concept']} {c['description']}", "bar") for c in res) and res[1]["core_concept"].startswith("퇴근 후"),
      f"'나들이' 컨셉은 방문 맥락에 맞는 기본 컨셉으로 교체, 나머지는 유지: {[c['core_concept'] for c in res]}")
mock_res = ow.suggest_ad_concepts(NAME, "food", PATHS, "눈꽃맥주", mock=True, photo_tags=TAGMAP)
check(mock_res[0]["badge"].startswith("🌙") and "우리 매장" not in str(mock_res), "Mock/실패 시에도 맥주집 맞춤 기본 컨셉")

# ---------------------------------------------------------------- 3. 대본: 사진 N장 = 문장 N개, 문장마다 그 사진의 피사체
def good_lines(n):
    base = ["저녁 먹고 아쉬울 때, 살얼음 눈꽃맥주 한 잔 어때요?", "매콤새콤 골뱅이소면에 첫 잔을 시원하게 들이켜요.", "입안이 상큼해지는 과일 화채로 청량감을 더해요.",
            "바삭한 먹태는 생맥주랑 찰떡궁합이에요.", "쫄깃한 반건조 오징어도 맥주를 부르는 맛이에요.", "안주 모둠이라 여럿이 나눠 먹기 좋아요.", "홀도 넓어서 퇴근길에 편하게 들르기 좋아요."]
    return base[:n - 1]


def fake_script(lines_fn):
    def _f(contents, system, **kw):
        n = len([c for c in contents if isinstance(c, str) and c.startswith("[씬")]) or 8
        return ({"core_concept": "저녁 먹고 한 잔 더, 눈꽃맥주와 찰떡궁합 안주", "story": "s",
                 "scenes": [{"scene_index": i + 1, "role": "signature", "hook_title": "한 잔 더?" if i == 0 else "", "voiceover": t}
                            for i, t in enumerate(lines_fn() + ["지금 저장하고 가평 설맥 가평현리점을 검색해 보세요!"])]}, "claude")
    return _f


ow2._media_preview_image = lambda p, d=None: Image.new("RGB", (64, 64), (120, 90, 60))
_ready = llm.script_llm_ready
_rep = llm.repair_scene_lines
llm.script_llm_ready = lambda k=None: True
llm.repair_scene_lines = lambda d, c, s_, r, **kw: (d, [])        # 보정용 재요청은 실제 API를 부르지 않게 막음 (검증은 계획 단계의 사진·맥락 가드가 함)


def plan(paths, tags, lines, **kw):
    llm.generate_json = fake_script(lambda: lines)
    try:
        return ow.plan_owner_storyboard(NAME, "눈꽃맥주, 골뱅이소면, 먹태", "food", NAME, paths, api_key="k", mock=False, region="경기 가평군",
                                        core_concept="저녁 먹고 한 잔 더, 눈꽃맥주와 찰떡궁합 안주", photo_tags=tags, keep_order=True, **kw)
    finally:
        llm.generate_json = _gj


RAP = ["살얼음 눈꽃맥주 한 잔!", "매콤새콤 골뱅이소면부터!", "시원한 과일 화채로 입가심!", "바삭한 먹태는 맥주 짝꿍!", "쫄깃한 반건조 오징어까지!", "안주 모둠이라 푸짐하게!",
       "홀도 넓어 편하게 한 잔!", "하이볼 한 잔도 시원하게!", "소주잔 세트로 분위기 업!"]
BEAT = ["저녁 먹고 아쉬울 때, 살얼음 눈꽃맥주 한 잔 어때요?", "안주 모둠이라 여럿이 나눠 먹기 좋아요.",
        "매콤새콤 골뱅이소면부터 시원한 과일 화채, 바삭한 먹태와 쫄깃한 오징어까지 안주 라인업도 완벽해요!", "홀도 넓어서 퇴근길에 편하게 들르기 좋아요."]

try:
    # --- B형 랩형: 사진 N장 = 숏카피 N줄 (1:1), 한 줄 2초
    sc8 = plan(PATHS, TAGMAP, RAP[:7], tempo="rap")
    check(len(sc8) == 8 and all(not s.get("media_group") or len(s["media_group"]) == 1 for s in sc8), f"랩형: 사진 8장 → 줄 8개 (사진을 쪼개 붙이지 않음): {len(sc8)}씬")
    bad = [i for i in range(7) if photo_subject_issues([sc8[i]["voiceover"]], [TAGS[i]])]
    check(not bad and "설맥" in sc8[-1]["voiceover"] and "주말 외식" not in sc8[-1]["voiceover"] and sc8[-1]["voiceover"].startswith("오늘 밤 시원한 맥주 생각나면"),
          f"랩형: 줄마다 그 사진의 피사체, 마지막은 '상호로 바로 오세요': {[s['voiceover'] for s in sc8[:2]]} … {sc8[-1]['voiceover']}")
    check(all(len(s["voiceover"]) <= 26 for s in sc8[:-1]) and all(abs(s["duration_sec"] - 2.0) < 0.01 for s in sc8) and abs(sum(s["duration_sec"] for s in sc8) - 16.0) < 0.5,
          f"랩형: 줄당 26자 이내·2초, 합계 {sum(s['duration_sec'] for s in sc8):.1f}초 (16~18초 템포, 30초로 늘어지지 않음)")
    # 사진-대본 불일치(설맥 영상 결함): 화채·먹태 화면에서 맥주 거품 이야기만
    mism = list(RAP[:7])
    mism[2] = "봉긋 솟아오른 크림 거품!"
    mism[3] = "맥주 거품과 목넘김 부드럽게!"
    sc_m = plan(PATHS, TAGMAP, mism, tempo="rap")
    check(photo_subject_issues([sc_m[2]["voiceover"]], [TAGS[2]]) == {} and photo_subject_issues([sc_m[3]["voiceover"]], [TAGS[3]]) == {} and "거품" not in sc_m[2]["voiceover"] + sc_m[3]["voiceover"],
          f"화채·먹태 화면에서 맥주 거품 이야기를 하면 그 사진을 말하는 줄로 교체: '{sc_m[2]['voiceover']}' / '{sc_m[3]['voiceover']}'")
    ctx_bad = list(RAP[:7])
    ctx_bad[0] = "나들이 가서 눈꽃맥주 한 잔!"
    sc_c = plan(PATHS, TAGMAP, ctx_bad, tempo="rap")
    check("나들이" not in " ".join(s["voiceover"] for s in sc_c), "맥주집 대본에 '나들이'가 들어가면 걸러냄")
    garble = list(RAP[:7])
    garble[3] = "튀긴 건어물 한 잔!"
    garble[1] = "맥주잔 개와 골뱅이소면!"
    sc_g = plan(PATHS, TAGMAP, garble, tempo="rap")
    check("건어물 한 잔" not in sc_g[3]["voiceover"] and "개와" not in sc_g[1]["voiceover"], f"깨진 문구('튀긴 건어물 한 잔', '맥주잔 개와') 차단 → '{sc_g[1]['voiceover']}' / '{sc_g[3]['voiceover']}'")
    p10 = [f"/m/{i}.jpg" for i in range(10)]
    t10 = dict(zip(p10, TAGS + ["하이볼 잔", "소주잔 세트"]))
    sc10 = plan(p10, t10, RAP[:9], tempo="rap")
    check(len(sc10) == 10 and sc10[-1]["role"] == "cta" and abs(sum(s["duration_sec"] for s in sc10) - 20.0) < 0.5, f"랩형 사진 10장 → 줄 10개, 마지막은 CTA ({len(sc10)}씬, {sum(s['duration_sec'] for s in sc10):.0f}초)")

    # --- A형 비트 리듬형: 사진 6~10장도 5문장, 안주 컷은 한 문장 안에서 고속 전환
    grp = ow.beat_groups(PATHS, TAGMAP)
    check(grp == [[0], [5], [1, 2, 3, 4], [6], [7]], f"비트형 묶음: 후킹(1)·모둠(1)·안주 4컷·홀(1)·간판(1) = {grp}")
    scb = plan(PATHS, TAGMAP, BEAT, tempo="beat")
    check(len(scb) == 5 and [len(s.get("media_group") or [0]) for s in scb] == [1, 1, 4, 1, 1] and scb[-1]["role"] == "cta",
          f"비트형: 사진 8장 → 5문장, 안주 소개 문장에 컷 4장: {[len(s.get('media_group') or [0]) for s in scb]}")
    snack = scb[2]["voiceover"]
    order = [snack.find(w) for w in ("골뱅이", "화채", "먹태", "오징어")]
    check(all(o >= 0 for o in order) and order == sorted(order), f"안주 문장이 사진 순서대로 골뱅이→화채→먹태→오징어를 호명: {snack}")
    tot = sum(s["duration_sec"] for s in scb)
    cut_sec = scb[2]["duration_sec"] / 4
    check(15 <= tot <= 21 and 1.2 <= cut_sec <= 1.6, f"비트형: 합계 {tot:.1f}초(≈18초), 안주 컷 {cut_sec:.2f}초 간격 전환")
    check(photo_subject_issues([snack], [scb[2].get("photo_tag") or " / ".join(TAGS[1:5])]) == {}, "비트형: 안주 문장이 4컷 피사체와 모두 맞음")
    # 안주 문장이 일부 안주만 말하거나 엉뚱하면 4컷을 모두 호명하는 안전 문구로 교체
    off = ow.photo_fallback_line(" / ".join(["골뱅이소면 한 접시", "과일 화채", "먹태", "반건조 오징어"]))
    check(all(w in off for w in ("골뱅이", "화채", "먹태", "오징어")) and "안주 라인업" in off, f"안전 문구도 4컷 안주를 순서대로 호명: {off}")
    sc5 = plan(PATHS[:5], dict(zip(PATHS[:5], TAGS[:5])), BEAT + ["x"], tempo="beat")
    check(len(sc5) == 5 and all(not s.get("media_group") or len(s["media_group"]) == 1 for s in sc5), f"사진 5장 이하는 비트형도 1장 = 1문장 ({len(sc5)})")
    scb10 = plan(p10, t10, BEAT, tempo="beat")
    check(len(scb10) == 5 and sum(len(s.get("media_group") or [0]) for s in scb10) == 10, f"비트형: 사진 10장도 5문장에 전부 사용 ({[len(s.get('media_group') or [0]) for s in scb10]})")
finally:
    llm.script_llm_ready = _ready
    llm.repair_scene_lines = _rep
    ow2._media_preview_image = _pv

# ---------------------------------------------------------------- 4. 렌더 경로: 씬마다 자기 사진 1장 (문장 중간 컷 전환 없음)
import shutil
import core.pipeline as pl
from core.job import JobContext
W = tmp("photo_sync_")
job = JobContext.create(app="photo_sync")
job.root = W
job = JobContext(job.job_id, root=W)
ups = []
for i in range(8):
    dst = _os.path.join(job.uploads_dir, f"u{i}.jpg")
    shutil.copy(fixture("photo_pool.jpg" if i % 2 == 0 else "photo_room.jpg"), dst)
    ups.append(dst)
spec = pl.JobSpec(mode="owner", name=NAME, usp="눈꽃맥주 골뱅이소면", business_type="food", search_keyword=NAME, address="경기 가평군", media_paths=ups, mock=True, keep_photo_order=True)
spec.tempo = "rap"
scenes = pl.plan(spec, job)
check(len(scenes) == 8 and all(not (s.get("extra_media")) for s in scenes) and [s.get("image_path") for s in scenes] == ups,
      f"렌더 입력: 씬 8개가 업로드 사진 8장과 1:1 (추가 컷 없음) — 각 사진은 자기 문장 구간 동안만 나옴")

spec.tempo = "beat"
sb = pl.plan(spec, job)
check(len(sb) == 5 and sum(1 + len(s.get("extra_media") or []) for s in sb) == 8 and max(len(s.get("extra_media") or []) for s in sb) >= 1,
      f"비트형 렌더 입력: 사진 8장이 5문장에 모두 배정, 일부 문장은 컷 여러 장: {[1 + len(s.get('extra_media') or []) for s in sb]}")

finish(fails)
