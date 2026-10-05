# 회귀 테스트

레포 루트에서 실행합니다.

```bash
python tests/run_all.py            # 오프라인 전체 (약 10분, 실호출 구간은 SKIP)
python tests/run_all.py --fast     # 렌더 스위트 2개 제외 (약 3분)
python tests/run_all.py --live     # Gemini 실호출 + 실제 숙소 페이지 크롤링 포함 (GEMINI_API_KEY·네트워크 필요)
python tests/run_all.py -k radar   # 이름으로 골라 실행
python tests/suite_script_guard.py # 스위트 하나만 직접 실행
```

| 스위트 | 검증 내용 |
|---|---|
| `suite_ugc_normalize.py` | 스마트폰 영상 정규화: 회전 메타·가변 프레임·HDR 톤매핑·가로 영상 블러 채움·짧은 클립 슬로모션/반복 |
| `suite_job_lazy_folders.py` | 작업 폴더 지연 생성, 24시간 넘은 빈 폴더 자동 정리 (결과물 있는 폴더 보존) |
| `suite_travel_golden7.py` | 숙소 7씬 황금 비율(수영장3·객실2·부대시설2), 비전 슬롯 배정, 엔딩 야경 뷰, BGM 제외 |
| `suite_script_guard.py` | 어절 경계 길이 맞춤, 사진·USP 근거 없는 주장 차단 |
| `suite_radar_core.py` | 쇼츠 레이더: 점수·필터, API 래퍼(파라미터·배치·쿼터 예산), 수집, SQLite, CLI dry-run |
| `suite_radar_hints.py` | 레이더 후킹 패턴 → 씬 1 힌트 주입, 원문 복제 차단 |
| `suite_owner_planner.py` | 사장님 플래너: 상호명 보존, 네이버 검색 CTA, 가격·할인 주장 차단 |
| `suite_app_radar_ui.py` | Streamlit 레이더 화면, 화면 전환 시 설정 유지, 기획 연동 |
| `suite_app_travel_flow.py` | Streamlit 숙소 제휴 흐름 (실제 페이지 크롤링: `--live`에서만) |
| `suite_stage3_render_e2e.py` | 작업 공간 격리 + 사진·영상·브랜드 오버레이 실제 렌더 (느림) |
| `suite_app_owner_flow.py` | Streamlit 3개 모드 분기 + 사장님 기획→렌더 (느림) |

## 원칙
- **픽스처는 실행 시 생성**합니다 (`tests/_support.py` → `tests/_fixtures/`, git 제외). 합성 이미지와 ffmpeg 테스트 패턴만 쓰며, 크롤링한 실제 숙소 사진 같은 저작권 자료는 레포에 넣지 않습니다.
- **사용자 데이터를 건드리지 않습니다.** 임시 폴더를 쓰고, 레이더 DB는 임시 경로로 돌리며, 테스트가 만든 작업 폴더만 정리합니다.
- **실호출은 선택**입니다. 기본 실행은 API 키·네트워크 없이 돌아가며, `--live`일 때만 Gemini와 외부 사이트를 호출합니다 (소량 과금 발생).
- 각 스위트는 `PASS`/`FAIL`/`SKIP` 줄과 종료 코드(실패 시 1)로 결과를 알려 CI에 그대로 연결할 수 있습니다.
