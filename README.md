# AI Short-form Ad Video Factory (자동 광고 영상 파이프라인)

경쟁사/레퍼런스 숏폼 광고 영상을 분석하여 우리 제품의 USP에 맞게 역기획하고,  
**Google Imagen 3(이미지) → 로컬 Ken Burns(2D 모션 비디오 합성) → Google TTS(음성) → MoviePy(합성)**를 연결해  
최종 9:16 광고 영상(MP4) 및 편집 에셋을 자동 양산하는 대시보드 및 CLI 파이프라인입니다.  
*(Veo 등 고가 비디오 모델을 원천 차단하고 100% 로컬 2D 모션 합성으로 안전 구동)*

---

## 1. 아키텍처 개요

```text
[Reference Video] + [제품명 & USP]
        │
        ▼ (Step 1: core/analyzer.py)
[Gemini 3.6 Flash Vision API] ──► storyboard_plan.json (씬별 역기획)
        │
        ▼ (Step 2: core/asset_generator.py - 캐싱 & 배치 생성)
  ├─► Google Imagen 3 (imagen-3.0-generate-002, 9:16 세로형 키프레임 PNG)
  ├─► Google TTS (gTTS 한국어 나레이션 음성 MP3)
  └─► 로컬 Ken Burns 모션 엔진 (OpenCV/Pillow/ffmpeg 기반 2D 팬앤줌 비디오 클립 MP4)
        │
        ▼ (Step 3: core/compositor.py)
[MoviePy 타임라인 조립] ──► 나레이션 길이에 맞춘 영상 자동 컷팅, 상단 후킹 바 & 세이프존 자막 오버레이
        │
        ▼
[outputs/final/final_ad.mp4] (최종 9:16 세로형 광고 영상 완제품)
```

---

## 2. 디렉토리 구조

```text
.
├── .env.example              # API 환경변수 설정 예시
├── requirements.txt          # 파이썬 의존성 패키지 목록
├── main.py                   # 메인 CLI 파이프라인 실행 진입점
├── app.py                    # Streamlit 웹 대시보드
├── storyboard_plan.json      # 씬별 콘티 기획 데이터 (중간 검토 및 수정 가능)
├── core/
│   ├── __init__.py
│   ├── guardrail.py          # API 호출 보안 가드레일 (고가 비디오 모델 무단 호출 차단)
│   ├── analyzer.py           # Step 1: Gemini 3.6 Flash 레퍼런스 분석 & 콘티 역기획
│   ├── asset_generator.py    # Step 2: Imagen 3, 로컬 Ken Burns, Google TTS 생성 및 캐싱
│   ├── compositor.py         # Step 3: MoviePy 기반 오디오 싱크 컷 & 최종 렌더링
│   ├── image_processor.py    # 제품 누끼(배경 제거) 및 합성 모듈
│   └── scraper.py            # 스마트스토어/쿠팡 상세페이지 크롤링
└── outputs/
    ├── cache/                # 씬별 개별 에셋 (scene_XX.png, .mp3, .mp4)
    └── final/                # 최종 완성된 광고 영상 (final_ad.mp4)
```

---

## 3. 환경 변수 설정 (`.env`)

`.env.example` 파일을 복사하여 `.env` 파일을 생성하고 Google Gemini API 키만 입력하면 모든 기능이 동작합니다.

```bash
cp .env.example .env
```

```env
GEMINI_API_KEY=your_gemini_api_key_here
```

---

## 4. 반응형 Streamlit 웹 대시보드 실행 가이드 (추천)

비개발자 마케터도 브라우저 UI에서 쇼핑몰 URL을 넣고 원클릭으로 광고 영상을 제작할 수 있습니다.

```bash
streamlit run app.py
```
브라우저에서 `http://localhost:8501`로 자동 열립니다:
- 🛒 **쇼핑몰 URL 크롤링**: 스마트스토어/쿠팡/올리브영 URL 입력 시 제품명, USP, 대표 이미지 자동 추출
- ✂️ **rembg AI 누끼 추출**: 제품 배경을 자동으로 투명화하여 AI 연출 배경 위에 합성
- 🎭 **4대 톤앤매너**: 어그로형, 전문가형, SNS 후기형, 15초 초스피드형
- ⏱️ **영상 길이 프리셋**: 10초 이내, 15초, 30초, 60초 (무음 모드 지원)
- 🎙️ **4종 성우 보이스**: 20대 여성(발랄), 30대 여성(신뢰), 20대 남성(에너지), 30대 남성(중저음)
- 📝 **실시간 대본 편집 (`st.data_editor`)**: 기획된 씬별 대사/자막을 표에서 직접 클릭하여 수정
- 🎬 **9:16 비디오 플레이어 & 원클릭 MP4 다운로드**

---

## 5. CLI 파이프라인 사용법

### 5.1 Mock(테스트) 모드로 즉시 검증 (API 비용 0원)
```bash
python main.py --mock --tone review --duration 10s --actor female_20s
```

### 5.2 쇼핑몰 URL 기반 실제 광고 영상 양산 실행
```bash
python main.py --force --url "https://smartstore.naver.com/..." --tone aggro --duration 15s --actor female_20s
```

### 5.3 직접 제품명 & USP를 지정하여 실행
```bash
python main.py --force --product "히알루론산 수분 앰플" --usp "속당김 즉각 해결, 48시간 고보습 지속" --scenes 2
```

### 5.4 단계별 분할 실행 지원
- **Step 1만 실행 (콘티 기획안 생성 및 검토)**:
  ```bash
  python main.py --step analyze --product "제품명" --usp "소구점"
  ```
- **Step 2만 실행 (수정된 콘티 기반 에셋 양산)**:
  ```bash
  python main.py --step generate
  ```
- **Step 3만 실행 (에셋 재조립 및 렌더링)**:
  ```bash
  python main.py --step compose
  ```

