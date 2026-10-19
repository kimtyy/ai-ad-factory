"""
tests/run_all.py - 회귀 테스트 러너 (CI·리팩토링 후 재검증용)

  python tests/run_all.py            # 오프라인 전체 (네트워크·Gemini 실호출 구간은 SKIP)
  python tests/run_all.py --fast     # 느린 렌더 스위트(각 3~5분) 제외
  python tests/run_all.py --live     # Gemini 실호출 + 실제 숙소 페이지 크롤링 구간까지 (GEMINI_API_KEY, 네트워크 필요)
  python tests/run_all.py -k radar   # 이름에 'radar'가 들어간 스위트만

각 스위트는 독립 실행 가능한 스크립트(tests/suite_*.py)이며 PASS/FAIL/SKIP 줄과 종료 코드로 결과를 알립니다.
종료 코드: 모든 스위트 통과 시 0, 하나라도 실패하면 1.
"""
import argparse
import os
import re
import subprocess
import sys
import time

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (파일, 설명, 느림 여부)
SUITES = [
    ("suite_video_overlay.py", "촬영 영상 직가공: 나레이션 겹침 재배치·9:16 크롭/블러·20% 더킹/음소거·특수문자 자막·HDR/세로", False),
    ("suite_script_phase2.py", "대본 Phase 2: clamp 비문 방지·조사 보정·1컨셉 연관성/씬 간 반복·훅 해요체·상투구·예시 잔재·CTA 단일 행동", False),
    ("suite_audio_stt.py", "롱폼 전사 엔진: 가짜 전사기 주입·공용 캐시(적중/무효화/깨짐)·16kHz 추출·긴 문장 분할·지연 임포트·AGPL 금지", False),
    ("suite_highlight_selector.py", "롱폼 하이라이트 선정: 창 15~22초·총 ≤25초·비겹침 3개·트랙 A/C/B·무음 스냅/마진/틈 클램프·LLM ID만·경고 플래그·음악 휴리스틱", False),
    ("suite_highlight_render.py", "롱폼 하이라이트 렌더 연결: window 단일 인코딩·창+CTA 길이·CTA 연장/정지·트랙 A/C/B 오디오·자막 상대시간·HDR 창 정규화·선정→렌더", False),
    ("suite_visual_scorer.py", "시각 품질 스코어러: 선명도·조도·급패닝·흔들림·컷 감점/원천 제외, 후보 창 샘플링, highlight_selector 하드 필터·가중 합산", False),
    ("suite_app_longform.py", "Streamlit 롱폼 하이라이트 화면: 입력 검증·백그라운드 분석 진행률·후보 카드(썸네일·트랙·점수·경고)·렌더·오류 안내·화면 이동 후 유지", True),
    ("suite_longform_fixes.py", "롱폼 실영상 피드백: 시작/끝 장면 전환 잔상 제거(컷 가드·J-cut 홀드)·원본 비율 유지 레이아웃/자막 위치·TTS 영문 약어 발음", False),
    ("suite_store_facts.py", "매장 고유 팩트 4종: 특이 메뉴명·리뷰 반복 명사(형용사 삭제)·사진별 피사체 태깅·주소 상권 → 대본 프롬프트 연결", False),
    ("suite_media_groups.py", "5문장 : 6~8컷 비대칭 묶음: 후킹·핵심 증거·후기 검증 문장 중간 컷 전환 (기획·배정·ffmpeg 조립)", False),
    ("suite_endcard_overlap.py", "엔딩 카드 자막 겹침 방지: 카드 페이드인 시점에 마지막 씬 자막 종료 (ffmpeg·MoviePy 경로, 카드 배경에 자막 안 비침)", False),
    ("suite_store_fixes.py", "매장 모드 실영상 피드백: 씬 1 본문 단일 자막·독립 커버 thumbnail.jpg·해시태그 단일 소스/업종 가드·첫 댓글 지도 URL 제거", False),
    ("suite_ugc_normalize.py", "스마트폰 영상 정규화 (회전·VFR·HDR·가로·짧은 클립)", False),
    ("suite_job_lazy_folders.py", "작업 폴더 지연 생성 + 빈 폴더 자동 정리", False),
    ("suite_travel_golden7.py", "숙소 7씬 황금 비율 + 비전 슬롯 배정 + BGM 제외", False),
    ("suite_coupang.py", "쿠팡 파트너스 API: 서명·검색 캐시/한도·딥링크·쿠팡 페이지 미접속", False),
    ("suite_commerce.py", "쇼핑 제휴: 실물 사진 전용·리뷰 썰·근거 없는 효능 차단·쿠팡 파트너스 표시", False),
    ("suite_scraper_input.py", "크롤링 입력 정직성 (가짜 기본값 없음·공유 링크 추적)", False),
    ("suite_script_guard.py", "대본 길이(어절 경계) + 근거 없는 주장·홍보 어투 차단", False),
    ("suite_render_visuals.py", "중앙 자막·씬 1 후킹 시간차·TTS 무음 제거·잘림 재시도 + Ken Burns", False),
    ("suite_radar_core.py", "쇼츠 레이더 수집·점수·쿼터·저장·CLI", False),
    ("suite_radar_hints.py", "레이더 후킹 패턴 → 씬 1 힌트 주입", False),
    ("suite_llm_router.py", "대본 LLM 자동 백업 (Claude → GPT → Gemini)·요청 형식·가드레일·판에 박힌 패턴 금지·플래너 연결", False),
    ("suite_radar_meta.py", "떡상 레이더 → 업로드 메타데이터 자동 주입 (트렌드 다이제스트·필수 맥락·레이더 태그·표시) + 첫 0.5초 커버", False),
    ("suite_bgm.py", "자체 음원 BGM: 나레이션 32% 덕킹·드롭 구간 시작·페이드·반복·음량 보정·영상 무손실·실패 처리·스위치·완료 표시", False),
    ("suite_railway.py", "Railway 배포: 컨테이너 스케줄러(캘린더 10분·레이더 07:00)·서버 데이터 가져오기(zip 안전)·리눅스 호환·배포 설정 파일", False),
    ("suite_youtube_upload.py", "썸네일 자동 생성(영상 첫 0.3초) + 유튜브 쇼츠 업로드(썸네일·첫 댓글 패키지·아동용 아님 확인·비공개 댓글 대기열·채널 확인)", False),
    ("suite_app_owner_hybrid.py", "사장님 하이브리드: 영상 소스 관리·네이버 지역 검색 API·사진 기반 매장 특징·기획 연동", False),
    ("suite_owner_planner.py", "사장님(트랙 B) 플래너", False),
    ("suite_app_auth.py", "외부 접속 PIN 잠금 (터널·다른 기기)", False),
    ("suite_app_radar_ui.py", "Streamlit 레이더 화면 + 기획 연동", False),
    ("suite_app_travel_flow.py", "Streamlit 숙소 제휴 흐름 (실크롤링, --live 전용)", False),
    ("suite_stage3_render_e2e.py", "작업 공간 격리 + 사진/영상/브랜드 실제 렌더", True),
    ("suite_app_commerce_flow.py", "Streamlit 쇼핑 원클릭: [영상 만들기] → 자동 기획 → 렌더 (사진 1장 다각도)", True),
    ("suite_app_owner_flow.py", "Streamlit 모드 분기 + 사장님 기획→렌더", True),
    ("suite_commerce_bundle.py", "쇼핑 3단 묶음 큐레이션: 훅 3초→상품 3×7초→CTA·어미 리듬·상품별 근거·고정 바·구매 좌표 첫 댓글·렌더", True),
    ("suite_book_mode.py", "시골책방: 한 권 호흡·원문 인용·근거 없는 주장 차단·명조체·종이 인용 카드·2:3 표지·성우 -5%·디졸브 0.6초·알라딘 첫 댓글 대기열", True),
    ("suite_pipeline.py", "공용 제작 엔진(화면 없이 기획→렌더→업로드 문구·첫 댓글) + 구글 캘린더 연동 기초(제목 해석·전용 캘린더)", True),
    ("suite_book_info.py", "시골책방 책 정보 자동 수집: 카카오·국립중앙도서관·쿠팡 표지/링크·알라딘 우선·캘린더 연결 (가짜 응답)", False),
    ("suite_book_reflect.py", "시골책방 사유 스토리텔링(책소개 기반·인용 아님 카드)·씬별 이미지 매칭(라이브러리·AI 배정·흐린 표지)·템플릿 자동 선택", True),
    ("suite_calendar_scheduler.py", "구글 캘린더 스케줄러: 일정 해석·처리 대상·비공개 업로드·결과 기록·하루 한도·재실행 (가짜 캘린더)", True),
    ("suite_ota_affiliate.py", "아고다·트립닷컴: 링크 판별·제휴 수익 링크·페이지 미접속·링크→기획→렌더→복사", True),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI 광고 공장 회귀 테스트 러너")
    ap.add_argument("--live", action="store_true", help="Gemini 실호출·실제 크롤링 구간 포함")
    ap.add_argument("--fast", action="store_true", help="느린 렌더 스위트 제외")
    ap.add_argument("-k", dest="pattern", default=None, help="스위트 파일명 필터")
    args = ap.parse_args(argv)

    env = dict(os.environ, PYTHONIOENCODING="utf-8", RUN_LIVE_TESTS="1" if args.live else "0")
    selected = [s for s in SUITES if (not args.fast or not s[2]) and (not args.pattern or args.pattern in s[0])]
    print(f"🧪 회귀 테스트 {len(selected)}개 스위트 ({'LIVE' if args.live else '오프라인'}{', 빠른 모드' if args.fast else ''})\n", flush=True)

    totals = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    failed_suites = []
    t_all = time.time()
    for name, desc, _ in selected:
        t0 = time.time()
        proc = subprocess.run([sys.executable, os.path.join(HERE, name)], cwd=ROOT, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = proc.stdout + proc.stderr
        counts = {k: len(re.findall(rf"^{k} ", out, flags=re.M)) for k in totals}
        for k in totals:
            totals[k] += counts[k]
        ok = proc.returncode == 0 and counts["FAIL"] == 0 and "Traceback" not in out
        if not ok:
            failed_suites.append(name)
        mark = "✅" if ok else "❌"
        print(f"{mark} {name:<30} PASS {counts['PASS']:>3}  FAIL {counts['FAIL']:>2}  SKIP {counts['SKIP']:>2}  "
              f"({time.time() - t0:5.1f}s)  {desc}", flush=True)
        if not ok:
            for line in [l for l in out.splitlines() if l.startswith("FAIL ") or "Error" in l or "Traceback" in l][:8]:
                print(f"      {line}", flush=True)

    print(f"\n합계: PASS {totals['PASS']} · FAIL {totals['FAIL']} · SKIP {totals['SKIP']} · {time.time() - t_all:.0f}s", flush=True)
    if failed_suites:
        print("실패 스위트: " + ", ".join(failed_suites), flush=True)
        return 1
    print("모든 스위트 통과", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
