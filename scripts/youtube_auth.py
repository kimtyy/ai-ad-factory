"""
scripts/youtube_auth.py - 제작 모드별 유튜브 채널 연결 (채널마다 처음 한 번, 이 PC에서 실행)

  python scripts/youtube_auth.py --mode travel     # 숙소 제휴  → secrets/token_travel.json   (YOUTUBE_CHANNEL_TRAVEL)
  python scripts/youtube_auth.py --mode store      # 사장님     → secrets/token_store.json    (YOUTUBE_CHANNEL_STORE)
  python scripts/youtube_auth.py --mode shopping   # 쇼핑 제휴  → secrets/token_shopping.json (YOUTUBE_CHANNEL_SHOPPING)
  python scripts/youtube_auth.py                   # 모드를 물어봄

1) secrets/client_secret.json (Google Cloud 콘솔 > OAuth 클라이언트 ID '데스크톱 앱' JSON)이 있어야 함
2) 브라우저가 열리면 Google 계정 → 그 모드의 채널을 고르고 허용
3) 고른 채널명이 .env의 기대 채널명과 같을 때만 토큰 저장 (다르면 저장하지 않고 종료 코드 1)
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from core.youtube_upload import (CHANNEL_KEYS, CHANNEL_LABEL, YouTubeUploadError, authorize_interactive,  # noqa: E402
                                 client_secrets_path, expected_channel)


def ask_mode() -> str:
    print("어느 채널을 연결할까요?")
    for i, k in enumerate(CHANNEL_KEYS, 1):
        print(f"  {i}) {k:<9} {CHANNEL_LABEL[k]} → '{expected_channel(k) or '(.env에 채널명 없음)'}'")
    choice = input("번호 또는 이름: ").strip().lower()
    if choice.isdigit() and 1 <= int(choice) <= len(CHANNEL_KEYS):
        return CHANNEL_KEYS[int(choice) - 1]
    if choice in CHANNEL_KEYS:
        return choice
    print("[실패] travel / store / shopping 중에서 골라 주세요.")
    sys.exit(2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="제작 모드별 유튜브 채널 연결")
    ap.add_argument("--mode", choices=CHANNEL_KEYS, help="travel(숙소) / store(사장님) / shopping(쇼핑)")
    args = ap.parse_args(argv)
    mode = args.mode or ask_mode()
    expected = expected_channel(mode)
    print(f"[{CHANNEL_LABEL[mode]}] 기대 채널: '{expected or '(없음)'}' → 브라우저에서 이 채널을 골라 주세요.")
    try:
        ch = authorize_interactive(mode)
    except YouTubeUploadError as e:
        print(f"[실패] {e}")
        if "OAuth 클라이언트" in str(e):
            print(f"  OAuth 클라이언트 JSON 위치: {client_secrets_path()}")
        return 1
    print(f"[연결 완료] {CHANNEL_LABEL[mode]} → 채널 '{ch['title']}' (id {ch['id']}), 토큰: {ch['saved_to']}")
    if not ch.get("comment_scope", True):
        print("[주의] 동의 화면에서 '댓글 관리(youtube.force-ssl)' 권한이 빠졌어요 → 업로드는 되지만 첫 댓글 자동 등록이 안 돼요. "
              "다시 실행해 모든 권한을 허용해 주세요.")
    if not expected:
        print(f"[주의] .env에 YOUTUBE_CHANNEL_{mode.upper()}={ch['title']} 를 적어야 업로드할 수 있어요 (채널 확인 안전 장치).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
