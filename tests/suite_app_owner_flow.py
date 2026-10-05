"""AppTest: 3개 모드 파이프라인 분기 + 사장님 모드 기획→렌더 전체 흐름 (mock 음성)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import shutil
import subprocess
import time

from streamlit.testing.v1 import AppTest

os.chdir(ROOT)
jobs_root = os.path.join(ROOT, "outputs", "jobs")
fails = 0
created = []


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


def labels(at):
    return [w.label for w in at.text_input] + [w.label for w in at.selectbox]


try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=600).run()
    check(not at.exception, f"초기 실행 예외 없음 {at.exception}")
    created.append(at.session_state.job.job_id)
    check(at.session_state.production_mode == "owner", "기본 모드 = 사장님(owner)")
    lb = labels(at)
    check(any("상호명 (영상에 그대로 노출)" in l for l in lb) and "업종" in lb and not any("URL" in l for l in lb),
          "사장님 모드: 상호명/업종/검색어 입력, URL 크롤링 행 없음")
    check(any("브랜드" in m.value for m in at.sidebar.markdown), "사장님 모드: 사이드바 브랜드 오버레이 옵션 표시")

    # 숙소 제휴 / 쇼핑 제휴 모드 분기
    for label, mode, expect in (("🧳 숙소 제휴 쇼츠", "travel", "숙소명 (대본에서는 숨김 처리)"),
                                ("🛒 쇼핑 제휴 쇼츠", "commerce", "홍보 제품명")):
        at.session_state.mode_switch = label
        at.session_state.production_mode = mode
        at.run()
        lb = labels(at)
        check(not at.exception and any(expect in l for l in lb) and any("URL" in l for l in lb)
              and not any("브랜드" in m.value for m in at.sidebar.markdown),
              f"{label}: '{expect}' 입력 + URL 크롤링 행, 브랜드 오버레이 없음")

    # 사장님 모드: 업로드 미디어 4개 → 기획 → 씬 배정 → 렌더
    at.session_state.mode_switch = "🏠 사장님 매장 홍보"
    at.session_state.production_mode = "owner"
    at.run()
    job = at.session_state.job
    srcs = [fixture("photo_pool.jpg"), fixture("photo_room.jpg"), fixture("phone_portrait.mov"), fixture("phone_landscape.mp4")]
    up = [shutil.copy(p, os.path.join(job.uploads_dir, f"u{i}{os.path.splitext(p)[1]}")) for i, p in enumerate(srcs)]
    at.session_state.uploaded_paths = up
    at.sidebar.checkbox[0].check()  # Mock 모드 (API 과금 없이)
    at.text_input(key="owner_name").input("가평 솔바람 펜션")
    at.selectbox(key="owner_type").select("stay")
    at.text_input(key="owner_usp").input("계곡 바로 앞, 개별 바베큐장")
    at.run()
    [b for b in at.button if "스토리보드 자동 기획" in b.label][0].click().run()
    check(not at.exception, f"사장님 기획 예외 없음 {at.exception}")
    sb = at.session_state.storyboard
    check(len(sb) == 4 and at.session_state.storyboard_mode == "owner", f"업로드 4개 → 4씬 콘티 ({len(sb)}씬)")
    check(sb[-1]["voiceover"] == "네이버에서 가평 솔바람 펜션 검색해 보세요!" and "가평 솔바람 펜션" in sb[0]["hook_title"],
          f"상호명 보존 + 네이버 검색 CTA: hook='{sb[0]['hook_title']}', 마지막='{sb[-1]['voiceover']}'")
    check(sb[2].get("media", {}).get("type") == "video" and sb[0].get("image_path") == up[0], "업로드 순서대로 씬 키 배정 (씬3=영상)")
    check(os.path.exists(job.plan_path), "콘티가 job/storyboard.json에 저장")

    # 모드 불일치 렌더 차단 (콘티는 owner, 현재 모드는 travel)
    at.session_state.mode_switch = "🧳 숙소 제휴 쇼츠"
    at.session_state.production_mode = "travel"
    at.run()
    [b for b in at.button if "영상 만들기" in b.label][0].click().run()
    check(any("다른 제작 모드" in e.value for e in at.error), "다른 모드에서 기획한 콘티는 렌더 차단")
    at.session_state.mode_switch = "🏠 사장님 매장 홍보"
    at.session_state.production_mode = "owner"
    at.run()

    # 렌더 (mock 음성) → 브랜드 워터마크 + 엔딩 카드 자동 적용
    t0 = time.time()
    [b for b in at.button if "영상 만들기" in b.label][0].click().run()
    check(not at.exception and not at.error, f"사장님 모드 렌더 예외 없음 {[e.value for e in at.error]}")
    final = at.session_state.final_video_path
    check(bool(final) and os.path.exists(final) and final.startswith(job.final_dir), f"최종 영상 job/final 생성 ({time.time() - t0:.0f}s)")
    if final and os.path.exists(final):
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        info = subprocess.run([ff, "-hide_banner", "-i", final], capture_output=True).stderr.decode("utf-8", "ignore")
        dur = [l for l in info.splitlines() if "Duration" in l][0].strip()[:24]
        print("     ", dur)
        FRAMES = tmp("owner_frames_")
        print("      프레임 확인:", FRAMES)
        for t, name in ((1.2, "owner_f1"), (6.5, "owner_f3"), (-1.0, "owner_end")):
            args = [ff, "-y", "-loglevel", "error"] + (["-sseof", "-1.0"] if t < 0 else ["-ss", str(t)]) + ["-i", final, "-frames:v", "1", os.path.join(FRAMES, f"{name}.jpg")]
            subprocess.run(args, check=True)
finally:
    # 이 테스트가 만든 작업 폴더 정리 (사용자 기존 작업 폴더는 건드리지 않음)
    for jid in set(created + [at.session_state.job.job_id]):
        shutil.rmtree(os.path.join(jobs_root, jid), ignore_errors=True)
finish(fails)