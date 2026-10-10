"""
scripts/youtube_auth.py - 유튜브 업로드용 채널 연결 (처음 한 번, 이 PC에서 실행)

  python scripts/youtube_auth.py

1) secrets/client_secret.json (Google Cloud 콘솔 > OAuth 클라이언트 ID '데스크톱 앱' JSON)이 있어야 함
2) 브라우저가 열리면 Google 계정 → 업로드할 채널('여행을 떠나자')을 고르고 허용
3) 토큰이 secrets/youtube_token.json에 저장되고, 연결된 채널 이름을 보여 줌 (git에는 올라가지 않음)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from core.youtube_upload import YouTubeUploadError, authorize_interactive, client_secrets_path  # noqa: E402

if __name__ == "__main__":
    try:
        ch = authorize_interactive()
    except YouTubeUploadError as e:
        print(f"[실패] {e}")
        print(f"  OAuth 클라이언트 JSON 위치: {client_secrets_path()}")
        sys.exit(1)
    print(f"[연결 완료] 채널: {ch['title']} (id {ch['id']})")
    expected = os.getenv("YOUTUBE_EXPECTED_CHANNEL", "").strip()
    if expected and ch["title"].strip() != expected:
        print(f"[주의] .env의 YOUTUBE_EXPECTED_CHANNEL='{expected}'와 다릅니다. 업로드가 막히니 다시 실행해 올바른 채널을 고르세요.")
