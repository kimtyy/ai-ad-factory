"""
scripts/post_pending_comments.py - 비공개로 올려 못 단 첫 댓글을, 공개·일부 공개로 바뀐 영상에 등록

  python scripts/post_pending_comments.py

유튜브는 비공개 영상에 댓글을 받지 않아서, 업로드 때 첫 댓글은 outputs/youtube/pending_comments.json에 대기.
스튜디오에서 공개 범위를 바꾼 뒤 이 스크립트(또는 완료 화면의 [첫 댓글 등록] 버튼, 매일 아침 레이더 작업)가 등록.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.youtube_upload import post_pending_comments  # noqa: E402

STATE_KO = {"posted": "등록", "waiting": "아직 비공개", "kids": "아동용(댓글 막힘)", "gone": "영상 없음 → 대기열에서 뺌", "failed": "실패"}


def main() -> int:
    results = post_pending_comments()
    if not results:
        print("대기 중인 첫 댓글이 없어요.")
    for r in results:
        print(f"{r['video_id']}: {STATE_KO.get(r['state'], r['state'])} {r['comment_url'] or r['warning']}".rstrip())
    return 1 if any(r["state"] == "failed" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
