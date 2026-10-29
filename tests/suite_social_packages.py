import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import subprocess

import imageio_ffmpeg
from streamlit.testing.v1 import AppTest

from core.social_packages import build_social_packages, PLATFORMS, LABELS

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


SC = [{"hook_title": "가평에서 만난 시원한 한 잔", "voiceover": "퇴근하고 한 잔 생각날 때!"},
      {"voiceover": "바삭한 먹태가 안주로 딱이에요."}, {"voiceover": "설맥 가평현리점에서 만나요!"}]
ADDR = "경기 가평군 가평읍 현리 123"
pk = build_social_packages(name="설맥 가평현리점", address=ADDR, scenes=SC, business_type="food", usp="생맥주와 먹태", menus=["먹태"],
                           place_link="https://naver.me/abc123")

check(set(pk) == set(PLATFORMS) and len(LABELS) == 4, "4개 플랫폼 패키지 생성")
yt = pk["youtube"]
check(len(yt["title"]) <= 100 and "| 설맥 가평현리점" in yt["title"] and "#Shorts" in yt["title"] and "#가평맛집" in yt["title"], f"유튜브 제목 ≤100자·양식: {yt['title']}")
check("📍 위치: " + ADDR in yt["body"] and "⏰ 영업시간: 네이버 플레이스에서 확인해 주세요" in yt["body"] and "🔗 네이버 지도: https://naver.me/abc123" in yt["body"]
      and "#설맥가평현리점" in yt["body"], "유튜브 설명란: 위치·영업시간(지어내지 않음)·링크·해시태그")
check("📌 설맥 가평현리점 매장 정보" in yt["comment"] and f"• 주소: {ADDR}" in yt["comment"] and "https://naver.me/abc123" in yt["comment"], "유튜브 첫 댓글")
ig = pk["instagram"]
check("✨" in ig["body"] and "📍 설맥 가평현리점 (가평)" in ig["body"] and "#릴스추천" in ig["body"] and "#먹태" in ig["body"] and 10 <= len(ig["tags"]) <= 16,
      f"릴스 캡션: 해시태그 {len(ig['tags'])}개")
nv = pk["naver"]
check(nv["title"] == "가평 설맥 가평현리점 - 먹태" and "'설맥 가평현리점'" in nv["body"] and "#네이버클립" in nv["body"] and "[📍 장소]" in nv["tip"], f"클립: {nv['title']}")
tk = pk["tiktok"]
check("🤤🔥" in tk["body"] and "#fyp" in tk["body"] and "가평 설맥 가평현리점에서" in tk["body"], "틱톡 본문")
check(len(set(pk["instagram"]["tags"])) == len(pk["instagram"]["tags"]), "해시태그 중복 없음")
nl = build_social_packages(name="이름만", scenes=[])
check("naver.com" in nl["youtube"]["body"] and "네이버 플레이스에서 확인해 주세요" in nl["youtube"]["comment"], "링크·주소 없으면 지도 검색 링크/안내로 대체")
long_hook = build_social_packages(name="매우긴상호명가게", address=ADDR, scenes=[{"hook_title": "아주 긴 후크 " * 20, "voiceover": "x"}])
check(len(long_hook["youtube"]["title"]) <= 100, f"긴 훅도 제목 100자 이하 ({len(long_hook['youtube']['title'])})")

# ---------------------------------------------------------------- 5단계 화면
W = tmp("social_")
vid = _os.path.join(W, "final.mp4")
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=2",
                "-pix_fmt", "yuv420p", vid], check=True)
for entry in ("app_store.py",):
    at = AppTest.from_file(_os.path.join(ROOT, entry), default_timeout=300).run()
    at.session_state["owner_name"] = "설맥 가평현리점"
    at.session_state["owner_search_kw"] = "설맥 가평현리점"
    at.session_state["owner_address"] = ADDR
    at.session_state["place_link"] = "https://naver.me/abc123"
    at.session_state["storyboard"] = SC
    at.session_state["final_video_path"] = vid
    at.run()
    check(not at.exception, f"{entry}: 5단계 렌더 오류 없음 {[e.value[:100] for e in at.exception]}")
    check(any("[5단계]" in m.value for m in at.markdown), "5단계 표시")
    check([t.label for t in at.tabs] == [LABELS[k] for k in PLATFORMS], f"4개 탭: {[t.label for t in at.tabs]}")
    codes = [c.value for c in at.code]
    check(any("설맥 가평현리점" in c and "#Shorts" in c for c in codes) and any("#릴스추천" in c for c in codes) and any("#네이버클립" in c for c in codes) and any("#fyp" in c for c in codes),
          "탭별 본문에 설맥 정보가 양식대로 채워짐")
    check(any("[📍 장소]" in i.value for i in at.info), "네이버 클립 점주 가이드 박스")
    check(len([b for b in at.get("download_button")]) >= 1, "MP4 저장 대형 다운로드 버튼")
finish(fails)
