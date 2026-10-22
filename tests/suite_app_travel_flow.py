"""AppTest: 숙소 제휴 모드 크롤링 → 7씬 황금 비율 기획(mock) → 사진 배정, BGM 옵션 제거, 업로드 팁"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import shutil

from streamlit.testing.v1 import AppTest

fails = 0
jobs = []
if live_skip("숙소 제휴 앱 흐름 전체 (실제 숙소 페이지 크롤링, 네트워크 필요)"):
    finish(0)


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=600).run()
    jobs.append(at.session_state.job.workdir)
    audio = [s for s in at.selectbox if s.label == "오디오 모드"][0]
    check(audio.options == ["🎙️ AI 나레이션 + 자막 (기본)", "🔇 무음 + 자막 전용 (유튜브 음원용)"], f"오디오 모드에 BGM 없음: {audio.options}")

    at.session_state.mode_switch = "🧳 숙소 제휴 쇼츠"
    at.session_state.production_mode = "travel"
    at.run()
    tpl = [s for s in at.selectbox if s.label == "숙소 제휴 씬 템플릿"]
    check(bool(tpl) and tpl[0].value == "golden7", "숙소 제휴 모드: 템플릿 기본값 = 7씬 황금 비율")

    at.text_input[[w.label for w in at.text_input].index("상품 페이지 URL 입력")].input("https://www.yeogi.com/domestic-accommodations/9854")
    [b for b in at.button if "숙소 정보 크롤링" in b.label][0].click().run()
    jobs.append(at.session_state.job.workdir)
    pool = at.session_state.image_pool
    check(not at.exception and len(pool) > 20, f"크롤링 → 사진 풀 {len(pool)}개")

    at.session_state["mock_mode"] = True; at.run()   # Mock (API 미호출: 텍스트 메타 기준 배정)
    [b for b in at.button if "대본 만들기" in b.label][0].click().run()
    sb = at.session_state.storyboard
    check(not at.exception and len(sb) == 7, f"기획 → 7씬 ({len(sb)}씬) {[e.value for e in at.error]}")
    check([s["target_visual_role"] for s in sb][:3] == ["pool"] * 3 and sb[-1]["voiceover"] == "숙소 정보는 댓글에 '여행' 남겨주세요!",
          f"역할 {[s['target_visual_role'] for s in sb]} / 엔딩 CTA")
    assigned = [s for s in sb if s.get("image_path") and os.path.exists(s["image_path"])]
    check(len(assigned) == 7 and all(s["image_path"].startswith(at.session_state.job.assets_dir) for s in assigned),
          f"사진 배정 {len(assigned)}/7 (job/assets 내)")
    check(len({s.get("matched_image_url") for s in assigned}) == len(assigned), "씬 간 사진 중복 없음")
    check(at.session_state.job.plan_path and os.path.exists(at.session_state.job.plan_path), "콘티 job/storyboard.json 저장")

    # 렌더 완료 화면: 업로드 팁
    at.session_state.final_video_path = fixture("final.mp4")
    at.run()
    check(any("트렌딩 음원을 함께 태그" in i.value for i in at.info), "렌더 완료 하단 트렌딩 음원 태그 안내 노출")
finally:
    for j in jobs:
        shutil.rmtree(j, ignore_errors=True)
finish(fails)