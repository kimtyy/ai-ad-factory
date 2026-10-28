"""
app_store.py - 🏪 샵스토리: AI 사장님 매장 홍보 영상 제작소 (단독 전용 진입점)

Streamlit Cloud에서 기존 app.py와 별개의 앱으로 배포한다 (New app → 같은 저장소·deploy 브랜치 → Main file path: app_store.py).
화면과 엔진은 app.py·core/ 를 그대로 쓰되 'STORE_APP' 스위치로 매장 홍보 1~5단계만 보이게 한다:
  - 모드 선택·홈 없음, 좌측 바(스마트폰은 하단 바)에는 [🏠 처음으로]와 [⚙️ 설정]만
  - Edge-TTS는 씬을 하나씩 순차 호출(TTS_WORKERS=1)하고, 실패하면 전 씬을 백업 성우(gTTS)로 일괄 다시 만든다 (TTS_BACKUP_ALL=1)
기존 app.py(멀티 모드 통합 앱)는 이 파일이 있어도 전혀 달라지지 않는다 (환경변수는 이 프로세스에만 적용).
"""
import os
import runpy

os.environ["STORE_APP"] = "1"
os.environ.setdefault("TTS_WORKERS", "1")
os.environ.setdefault("TTS_BACKUP_ALL", "1")

_APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
runpy.run_path(_APP, run_name="__main__")
