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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (파일, 설명, 느림 여부)
SUITES = [
    ("suite_ugc_normalize.py", "스마트폰 영상 정규화 (회전·VFR·HDR·가로·짧은 클립)", False),
    ("suite_job_lazy_folders.py", "작업 폴더 지연 생성 + 빈 폴더 자동 정리", False),
    ("suite_travel_golden7.py", "숙소 7씬 황금 비율 + 비전 슬롯 배정 + BGM 제외", False),
    ("suite_scraper_input.py", "크롤링 입력 정직성 (가짜 기본값 없음·공유 링크 추적)", False),
    ("suite_script_guard.py", "대본 길이(어절 경계) + 근거 없는 주장·홍보 어투 차단", False),
    ("suite_render_visuals.py", "중앙 자막·씬 1 후킹 시간차·TTS 무음 제거·잘림 재시도 + Ken Burns", False),
    ("suite_radar_core.py", "쇼츠 레이더 수집·점수·쿼터·저장·CLI", False),
    ("suite_radar_hints.py", "레이더 후킹 패턴 → 씬 1 힌트 주입", False),
    ("suite_owner_planner.py", "사장님(트랙 B) 플래너", False),
    ("suite_app_auth.py", "외부 접속 PIN 잠금 (터널·다른 기기)", False),
    ("suite_app_radar_ui.py", "Streamlit 레이더 화면 + 기획 연동", False),
    ("suite_app_travel_flow.py", "Streamlit 숙소 제휴 흐름 (실크롤링, --live 전용)", False),
    ("suite_stage3_render_e2e.py", "작업 공간 격리 + 사진/영상/브랜드 실제 렌더", True),
    ("suite_app_owner_flow.py", "Streamlit 모드 분기 + 사장님 기획→렌더", True),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI 광고 공장 회귀 테스트 러너")
    ap.add_argument("--live", action="store_true", help="Gemini 실호출·실제 크롤링 구간 포함")
    ap.add_argument("--fast", action="store_true", help="느린 렌더 스위트 제외")
    ap.add_argument("-k", dest="pattern", default=None, help="스위트 파일명 필터")
    args = ap.parse_args(argv)

    env = dict(os.environ, PYTHONIOENCODING="utf-8", RUN_LIVE_TESTS="1" if args.live else "0")
    selected = [s for s in SUITES if (not args.fast or not s[2]) and (not args.pattern or args.pattern in s[0])]
    print(f"🧪 회귀 테스트 {len(selected)}개 스위트 ({'LIVE' if args.live else '오프라인'}{', 빠른 모드' if args.fast else ''})\n")

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
              f"({time.time() - t0:5.1f}s)  {desc}")
        if not ok:
            for line in [l for l in out.splitlines() if l.startswith("FAIL ") or "Error" in l or "Traceback" in l][:8]:
                print(f"      {line}")

    print(f"\n합계: PASS {totals['PASS']} · FAIL {totals['FAIL']} · SKIP {totals['SKIP']} · {time.time() - t_all:.0f}s")
    if failed_suites:
        print("실패 스위트: " + ", ".join(failed_suites))
        return 1
    print("모든 스위트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
