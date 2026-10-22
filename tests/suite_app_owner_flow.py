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
    check(any(b.label == "⚙️ 설정" for b in at.sidebar.button) and not any("브랜드" in m.value for m in at.sidebar.markdown), "좌측 바에는 주메뉴와 ⚙️ 설정만 (브랜드 오버레이는 설정 팝업 안)")

    # 숙소 제휴 / 쇼핑 제휴 모드 분기
    for label, mode, expect in (("🧳 숙소 제휴 쇼츠", "travel", "숙소명 (대본에서는 숨김 처리)"),
                                ("🛒 쇼핑 제휴 쇼츠", "commerce", "홍보 제품명")):
        at.session_state.mode_switch = label
        at.session_state.production_mode = mode
        at.run()
        lb = labels(at)
        check(not at.exception and any(expect in l for l in lb) and any("URL" in l for l in lb)
              ,
              f"{label}: '{expect}' 입력 + URL 크롤링 행")

    # 사장님 모드: 업로드 미디어 4개 → 기획 → 씬 배정 → 렌더
    at.session_state.mode_switch = "🏠 사장님 매장 홍보"
    at.session_state.production_mode = "owner"
    at.run()
    job = at.session_state.job
    srcs = [fixture("photo_pool.jpg"), fixture("photo_room.jpg"), fixture("phone_portrait.mov"), fixture("phone_landscape.mp4")]
    up = [shutil.copy(p, os.path.join(job.uploads_dir, f"u{i}{os.path.splitext(p)[1]}")) for i, p in enumerate(srcs)]
    at.session_state.uploaded_paths = up
    at.session_state["mock_mode"] = True  # Mock 모드 (API 과금 없이)
    at.text_input(key="owner_name").input("가평 솔바람 펜션")
    at.selectbox(key="owner_type").select("stay")
    at.text_input(key="owner_usp").input("계곡 바로 앞, 개별 바베큐장")
    at.run()
    [b for b in at.button if "대본 만들기" in b.label][0].click().run()
    check(not at.exception, f"사장님 기획 예외 없음 {at.exception}")
    sb = at.session_state.storyboard
    check(len(sb) == 4 and at.session_state.storyboard_mode == "owner", f"업로드 4개 → 4씬 콘티 ({len(sb)}씬)")
    check("지금 저장하고" in sb[-1]["voiceover"] and sb[-1]["voiceover"].endswith("가평 솔바람 펜션을 검색해 보세요!") and "가평 솔바람 펜션" in sb[0]["hook_title"],
          f"상호명 보존 + 네이버 검색 CTA: hook='{sb[0]['hook_title']}', 마지막='{sb[-1]['voiceover']}'")
    check(sb[2].get("media", {}).get("type") == "video" and sb[0].get("image_path") == up[0], "업로드 순서대로 씬 키 배정 (씬3=영상)")
    check(os.path.exists(job.plan_path), "콘티가 job/storyboard.json에 저장")

    check(sb[0].get("script_engine") == "template" and any("업종 기본 문구" in (w.value or "") for w in at.warning),
          "템플릿(Mock) 대본이면 화면에 'AI가 아니라 업종 기본 문구' 경고 표시")
    # 카드형 편집기: 표(data_editor) 없이 씬별 대사·지속시간만 노출, 수정값은 즉시 세션·저장본에 반영, 내부 필드는 보존
    check(len(at.get("data_editor")) == 0 and len([t for t in at.text_area if t.key and t.key.startswith("sb_text_")]) == 4,
          "스토리보드 편집: 표 없이 씬별 카드(대사 입력 4개)")
    hook0, role0 = sb[0]["hook_title"], sb[0].get("role")
    at.text_area(key="sb_text_1").input("수정한 첫 문장이에요")
    at.number_input(key="sb_dur_1").set_value(3.5)
    at.run()
    sb2 = at.session_state.storyboard
    check(sb2[0]["voiceover"] == sb2[0]["subtitle"] == sb2[0]["caption"] == "수정한 첫 문장이에요" and sb2[0]["duration_sec"] == 3.5
          and sb2[0]["hook_title"] == hook0 and sb2[0].get("role") == role0 and sb2[3]["voiceover"] == sb[3]["voiceover"],
          "카드 수정 즉시 반영: 대사=자막 일치, 지속시간 반영, 숨은 필드(hook_title·role)와 다른 씬 보존")

    # 메뉴판·리뷰 붙여넣기 → 고유 팩트 표시 (자동 추출, 붙여 넣은 글에 있는 말만)
    at.session_state.owner_menu_text = "눈꽃버섯 갈비탕 15,000원 / 돼지갈비 14,000"
    at.session_state.owner_review_text = "격자 칼집이 인상적이에요. 격자 칼집 덕분에 양념이 잘 배요. 통새송이버섯 나와요. 통새송이버섯 좋아요."
    at.run()
    sf = [x.value for x in at.success if "뽑힌 고유 팩트" in (x.value or "")]
    check(not at.exception and sf and "눈꽃버섯 갈비탕" in sf[0] and "격자 칼집" in sf[0] and "통새송이버섯" in sf[0],
          "메뉴판·리뷰 붙여넣기 → 특이 메뉴·리뷰 반복 명사 자동 추출 표시")

    # 📌 내가 올린 사진 순서 그대로 유지 (AI 자동 정렬 끄기): 기본 해제, 켜면 재기획 안내
    kp = at.checkbox(key="keep_photo_order")
    check(kp.value is False and "AI 자동 정렬 끄기" in kp.label, "'내가 올린 사진 순서 그대로 유지' 체크박스: 기본값 해제(자동 정렬 사용)")
    at.checkbox(key="keep_photo_order").set_value(True).run()
    check(at.session_state.keep_photo_order is True and any("사진·영상 구성이 바뀌었어요" in (i.value or "") for i in at.info),
          "순서 유지를 켜면 값이 저장되고 재기획 안내가 나옴")
    at.checkbox(key="keep_photo_order").set_value(False).run()

    # 화자 시점 ↔ 성우·말투 프리셋 연동 (시점을 바꿀 때 적용, 이후 수동 변경은 유지)
    at.radio(key="script_style_radio").set_value("review").run()
    check(not at.exception and at.session_state.selected_actor == "female_20s" and at.session_state.voice_tone == "lively",
          "화자 시점 '손님 꿀팁 리뷰형' → 20대 여성(발랄) + 활기찬 말투로 자동 설정")
    at.radio(key="script_style_radio").set_value("official").run()
    check(at.session_state.selected_actor == "female_30s" and at.session_state.voice_tone == "calm",
          "화자 시점 '가게 공식 추천형' → 30대 여성(신뢰) + 차분한 말투로 자동 설정")
    at.selectbox(key="voice_actor_select_box").set_value("20대 남성 (에너지)").run()
    check(at.session_state.selected_actor == "male_20s", "연동 뒤에도 성우 드롭다운에서 직접 바꿀 수 있음")
    at.run()
    check(at.session_state.selected_actor == "male_20s", "직접 바꾼 성우는 같은 시점에서 다시 실행해도 유지")

    # 1컨셉에 맞는 촬영 가이드 (업로드 영역 상단)
    at.session_state.selected_core_concept = "누수 원인부터 수리까지 한 번에 해결"
    at.run()
    gi = [i.value for i in at.info if "추천 8컷 촬영 순서" in (i.value or "")]
    check(gi and "설비·해결형" in gi[0] and "비포" in gi[0] and "애프터" in gi[0], "컨셉(누수·수리) → 설비·해결형 촬영 가이드 표시")
    at.session_state.selected_core_concept = "신상 1+1 행사 한눈에"
    at.run()
    gi = [i.value for i in at.info if "추천 8컷 촬영 순서" in (i.value or "")]
    check(gi and "편의점·신상형" in gi[0] and "신상품 패키지" in gi[0], "컨셉(신상·1+1) → 편의점·신상형 촬영 가이드 표시")

    # 모드 전환 시 이전 모드의 콘티·업로드·입력값은 즉시 폐기 (캐시 오염 차단: reset_mode_session_state)
    at.session_state.mode_switch = "🧳 숙소 제휴 쇼츠"
    at.session_state.production_mode = "travel"
    at.run()
    check(not at.session_state.storyboard and not at.session_state.uploaded_paths,
          "다른 모드로 전환하면 이전 모드 콘티·업로드가 초기화됨 (다른 모드 콘티가 렌더되지 않음)")
    # 직접 modes 불일치 상태를 만들어도 렌더는 차단 (안전망)
    at.session_state.storyboard = sb
    at.session_state.storyboard_mode = "owner"
    at.run()
    [b for b in at.button if "영상 만들기" in b.label][0].click().run()
    check(any("다른 제작 모드" in e.value for e in at.error), "다른 모드에서 기획한 콘티는 렌더 차단 (안전망)")

    # 사장님 모드로 복귀 → 입력·업로드·기획을 다시 수행
    at.session_state.mode_switch = "🏠 사장님 매장 홍보"
    at.session_state.production_mode = "owner"
    at.run()
    up = [shutil.copy(p, os.path.join(at.session_state.job.uploads_dir, f"r{i}{os.path.splitext(p)[1]}")) for i, p in enumerate(srcs)]
    at.session_state.uploaded_paths = up
    at.text_input(key="owner_name").input("가평 솔바람 펜션")
    at.selectbox(key="owner_type").select("stay")
    at.text_input(key="owner_usp").input("계곡 바로 앞, 개별 바베큐장")
    at.run()
    [b for b in at.button if "대본 만들기" in b.label][0].click().run()
    check(not at.exception and len(at.session_state.storyboard) == 4, "복귀 후 재기획 4씬")
    job = at.session_state.job

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