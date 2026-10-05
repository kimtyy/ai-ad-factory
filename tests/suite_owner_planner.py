import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import sys
import logging

os.chdir(ROOT)
sys.path.insert(0, ROOT)
logging.basicConfig(level=logging.INFO, format="%(message)s")
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from core.planners import plan_owner_storyboard
from core.planners.owner import _unsupported_claim

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


media = [fixture("photo_pool.jpg"), fixture("photo_room.jpg"), fixture("phone_portrait.mov"), fixture("photo_pool.jpg")]
thumbs = tmp("owner_thumbs_")

# 1. 오프라인(mock): 업종별 템플릿, 상호명 보존, 네이버 CTA
for btype in ("stay", "food", "shop"):
    sc = plan_owner_storyboard("가평 솔바람 펜션", "계곡 바로 앞, 개별 바베큐", btype, "", media, mock=True)
    check(len(sc) == 4 and sc[-1]["role"] == "cta" and sc[-1]["voiceover"] == "네이버에서 가평 솔바람 펜션 검색해 보세요!",
          f"[{btype}] 미디어 4개 → 4씬, 마지막 네이버 검색 CTA")
    check("가평 솔바람 펜션" in sc[0]["hook_title"] and all("비밀 힐링" not in s["voiceover"] for s in sc), f"[{btype}] 상호명 보존 (은닉 치환 없음)")
    check(all(s["focus_bottom"] is False for s in sc), f"[{btype}] 간판 크롭(focus_bottom) 비활성")
sc = plan_owner_storyboard("성수 불판", "", "food", "성수 불판 성수점", [], num_scenes=5, mock=True)
check(len(sc) == 5 and sc[-1]["voiceover"] == "네이버에서 성수 불판 성수점 검색해 보세요!" and sc[1]["role"] == "signature",
      "미디어 없음 → 업종 템플릿 5씬 + 검색어 별도 지정")

# 2. 허위 주장 차단
check(_unsupported_claim("오늘만 30% 할인해요!", "") is not None, "USP에 없는 '30% 할인' 차단")
check(_unsupported_claim("런치 9,900원 세트!", "런치 9,900원") is None, "USP에 있는 가격은 허용")

# 3. 실제 Gemini: 업로드 미디어(사진 3 + 영상 1)를 보고 씬별 대사 작성
if not live_skip("실제 Gemini: 업로드 미디어 기반 사장님 콘티"):
    sc = plan_owner_storyboard("가평 솔바람 펜션", "계곡 바로 앞, 개별 바베큐장", "stay", "", media,
                               api_key=os.getenv("GEMINI_API_KEY"), thumb_dir=thumbs)
    for s in sc:
        print(f"   씬{s['scene_index']} [{s['role']}] hook='{s['hook_title']}' | {s['voiceover']}")
    check(len(sc) == 4 and sc[-1]["voiceover"].startswith("네이버에서 가평 솔바람 펜션"), "Gemini 플랜: 4씬 + 고정 CTA")
    check(all(len(s["voiceover"]) <= 22 for s in sc[:-1]), "Gemini 플랜: 대사 길이 22자 이내")
    check(all(not _unsupported_claim(s["voiceover"], "계곡 바로 앞, 개별 바베큐장") for s in sc), "Gemini 플랜: 허위 수치/혜택 없음")
finish(fails)