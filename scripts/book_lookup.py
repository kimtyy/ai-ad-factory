"""
scripts/book_lookup.py - 책 정보 자동 수집 확인 (카카오 책 검색 + 국립중앙도서관 + 쿠팡 파트너스)

  python scripts/book_lookup.py "하늘과 바람과 별과 시"
  python scripts/book_lookup.py 9788900000002 --no-coupang
  python scripts/book_lookup.py "책 제목" --aladin "https://www.aladin.co.kr/..."   # 알라딘 제휴 링크 우선

표지는 outputs/books/ 에 저장. 필요한 키: .env KAKAO_REST_API_KEY, NL_CERT_KEY (쿠팡은 기존 COUPANG_ACCESS/SECRET_KEY)
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from core.book_info import BookInfoError, collect_book  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="책 정보 자동 수집 확인")
    ap.add_argument("query", help="책 제목 또는 ISBN")
    ap.add_argument("--author", default="")
    ap.add_argument("--aladin", default="", help="알라딘 제휴 링크 (있으면 구매 링크로 우선)")
    ap.add_argument("--no-coupang", action="store_true", help="쿠팡 파트너스 검색 안 함 (검색 한도 아끼기)")
    args = ap.parse_args(argv)
    try:
        info = collect_book(args.query, author=args.author, aladin_link=args.aladin,
                            out_dir=os.path.join(ROOT, "outputs", "books"), use_coupang=not args.no_coupang)
    except BookInfoError as e:
        print(f"[실패] {e}")
        return 1
    print(info.summary())
    print(f"출처: {', '.join(info.sources)}")
    print(f"표지: {info.cover_path or '(없음)'}")
    print(f"구매 링크: {info.purchase_link or '(없음)'}")
    print(f"책소개 ({len(info.description)}자): {info.description[:300]}{'…' if len(info.description) > 300 else ''}")
    for w in info.warnings:
        print(f"⚠️ {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
