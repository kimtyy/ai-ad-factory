"""시골책방 책 정보 자동 수집: 카카오 책 검색 + 국립중앙도서관 + 쿠팡 파트너스(표지·제휴 링크) → 캘린더 자동 제작 연결 (가짜 응답)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402
import io
import os

from PIL import Image

import core.book_info as bi

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


def jpeg(w, h):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (40, 60, 90)).save(b, "JPEG", quality=90)
    return b.getvalue()


IMAGES = {"https://kakao/thumb.jpg": jpeg(120, 174), "https://nl/cover.jpg": jpeg(300, 450),
          "https://coupang/img.jpg": jpeg(500, 750), "https://coupang/other.jpg": jpeg(900, 900)}
KAKAO_DOCS = [
    {"title": "하늘과 바람과 별과 시 (필사본)", "authors": ["윤동주"], "publisher": "A", "isbn": "111 9791100000001",
     "contents": "필사본", "thumbnail": "https://kakao/thumb.jpg", "datetime": "2020-01-01T00:00:00"},
    {"title": "하늘과 바람과 별과 시", "authors": ["윤동주"], "publisher": "B출판", "isbn": "8900000000 9788900000002",
     "contents": "윤동주의 유고 시집을 &lt;초판본&gt; 그대로...", "thumbnail": "https://kakao/thumb.jpg",
     "datetime": "2016-02-01T00:00:00"}]
NL_DOC = {"TITLE": "하늘과 바람과 별과 시", "AUTHOR": "윤동주 지음", "PUBLISHER": "B출판", "EA_ISBN": "9788900000002",
          "TITLE_URL": "https://nl/cover.jpg",
          "BOOK_INTRODUCTION": "<p>1948년 첫 출간된 윤동주의 유고 시집. 서시, 별 헤는 밤, 자화상 등 31편을 담았다.</p>",
          "BOOK_SUMMARY": "식민지 청년의 고뇌와 부끄러움을 맑은 언어로 노래한 시집."}
calls = []


class Resp:
    def __init__(self, status=200, js=None, content=b""):
        self.status_code, self._js, self.content = status, js, content

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def fake_get(url, headers=None, params=None, timeout=None):
    calls.append((url, params, headers))
    if url == bi.KAKAO_URL:
        if headers.get("Authorization") != "KakaoAK test-kakao":
            return Resp(401)
        return Resp(js={"documents": KAKAO_DOCS if "별과" in params["query"] or params["target"] == "isbn" else []})
    if url == bi.NL_URL:
        return Resp(js={"PAGE_NO": "1", "TOTAL_COUNT": "1", "docs": [NL_DOC] if params["isbn"] == "9788900000002" else []})
    if url in IMAGES:
        return Resp(content=IMAGES[url])
    return Resp(404)


class FakeCoupang:
    enabled = True

    def __init__(self, items):
        self.items, self.searched = items, []

    def search(self, kw, limit=10):
        self.searched.append(kw)
        return self.items

    def deeplink(self, url):
        return "https://link.coupang.com/a/BOOK"


_get, _env = bi.requests.get, {k: os.environ.get(k) for k in ("KAKAO_REST_API_KEY", "NL_CERT_KEY")}
bi.requests.get = fake_get
os.environ["KAKAO_REST_API_KEY"], os.environ["NL_CERT_KEY"] = "test-kakao", "test-nl"
work = tmp()
try:
    cp = FakeCoupang([{"product_id": "9", "name": "무선 청소기", "image_url": "https://coupang/other.jpg", "partner_url": "x"},
                      {"product_id": "7", "name": "하늘과 바람과 별과 시 - 윤동주 시집", "image_url": "https://coupang/img.jpg",
                       "partner_url": "https://link.coupang.com/re/raw"}])
    info = bi.collect_book("하늘과 바람과 별과 시", out_dir=work, coupang_client=cp)
    check(info.title == "하늘과 바람과 별과 시" and info.publisher == "B출판" and info.isbn13 == "9788900000002" and info.author == "윤동주",
          f"카카오: 제목이 정확히 같은 책 선택 (필사본 등 다른 판 제외) · ISBN13 추출 ({info.isbn13})")
    check("1948년 첫 출간" in info.description and "<p>" not in info.description and "서시, 별 헤는 밤" in info.description
          and "부끄러움" in info.description, "국립중앙도서관 책소개·서평 원문을 대본 근거로 (HTML 제거)")
    check(info.cover_source == "coupang" and info.cover_size == (500, 750) and os.path.exists(info.cover_path),
          f"표지: 출처별로 받아 가장 큰 것 (쿠팡 500x750 > 도서관 300x450 > 카카오 120x174) {info.cover_size}")
    check(info.purchase_link == "https://link.coupang.com/a/BOOK" and info.link_source == "coupang"
          and info.sources == ["카카오 책 검색", "국립중앙도서관", "쿠팡 파트너스"], "구매 링크: 쿠팡 파트너스 딥링크 자동 생성")
    check(not any("other.jpg" in c[0] for c in calls), "쿠팡 검색 결과 중 제목이 다른 상품(무선 청소기) 이미지는 표지로 안 씀")

    calls.clear()
    info2 = bi.collect_book("9788900000002", aladin_link="https://www.aladin.co.kr/shop/wproduct.aspx?ItemId=1&ttbkey=x",
                            out_dir=work, use_coupang=False)
    check(info2.title == "하늘과 바람과 별과 시" and info2.link_source == "aladin" and info2.cover_source == "nl"
          and any(c[1] and c[1].get("target") == "isbn" for c in calls if c[0] == bi.KAKAO_URL),
          "ISBN으로도 조회 · 알라딘 제휴 링크를 넣으면 그 링크 우선")
    try:
        bi.collect_book("없는 책 제목", out_dir=work, use_coupang=False)
        check(False, "없는 책")
    except bi.BookInfoError as e:
        check("찾지 못했어요" in str(e), "어디서도 못 찾으면 지어내지 않고 오류")
    os.environ["KAKAO_REST_API_KEY"], os.environ["NL_CERT_KEY"] = "", ""
    try:
        bi.collect_book("하늘과 바람과 별과 시", out_dir=work, use_coupang=False)
        check(False, "키 없음")
    except bi.BookInfoError as e:
        check("KAKAO_REST_API_KEY" in str(e) and "NL_CERT_KEY" in str(e), "키가 없으면 어떤 키가 필요한지 안내")
    os.environ["KAKAO_REST_API_KEY"] = "wrong"
    try:
        bi.collect_book("하늘과 바람과 별과 시", out_dir=work, use_coupang=False)
        check(False, "잘못된 키")
    except bi.BookInfoError as e:
        check("올바르지 않아요" in str(e), "카카오 키가 틀리면(401) 바로 안내")
    os.environ["KAKAO_REST_API_KEY"], os.environ["NL_CERT_KEY"] = "test-kakao", ""
    small = bi.collect_book("하늘과 바람과 별과 시", out_dir=work, use_coupang=False)
    check(small.cover_source == "kakao" and any("표지가 작아요" in w for w in small.warnings)
          and any("구매 링크 없음" in w for w in small.warnings), "작은 표지·링크 없음은 경고로 알려 줌 (카카오만 있을 때)")
    check(bi.looks_like_isbn("978-89-000-0000-2") and bi.looks_like_isbn("8900000000") and not bi.looks_like_isbn("하늘과 바람"),
          "ISBN 판별 (하이픈 허용)")

    # 판본 매칭 (실제 쿠팡 검색 결과 상품명 그대로)
    REAL = ["하늘과 바람과 별과 시(초판본):1955년 정음사 오리지널 초판본 표지 디자인", "하늘과 바람과 별과 시(특별판 육필원고본)",
            "하늘과 바람과 별과 시", "하늘과 바람과 별과 시 블랙 연필 문구 세트(미니북+블랙연필+크라프트노트)",
            "하늘과 바람과 별과 시:윤동주 전 시집｜서거 77주년, 탄생 105주년 기념 '하늘과 바람과 별과 시' 뉴 에디션",
            "하늘과 바람과 별과 시 (양장)"]
    REF = ("하늘과 바람과 별과 시 스타북스 2022년 윤동주 서거 77주년과 탄생 105주년을 맞아 새롭게 편집한 『윤동주 전 시집』 "
           "2017년 윤동주 탄생 100주년을 기념해")
    real_cp = FakeCoupang([{"product_id": str(k), "name": n, "image_url": "https://coupang/img.jpg", "partner_url": "x"}
                           for k, n in enumerate(REAL)])
    got = bi.coupang_book("하늘과 바람과 별과 시", "윤동주", real_cp, reference=REF, publisher="스타북스")
    check(got and got["product_id"] == "4" and real_cp.searched[0] == "하늘과 바람과 별과 시 스타북스",
          "판본 매칭: 출판사로 먼저 검색 → 책소개와 단서(77주년·105주년)가 맞는 판본 선택 (초판본·특별판·세트·양장 제외)")
    rej = []
    amb = FakeCoupang([{"product_id": str(k), "name": n, "image_url": "https://coupang/img.jpg", "partner_url": "x"}
                       for k, n in enumerate([REAL[0], REAL[1], REAL[2]])])
    check(bi.coupang_book("하늘과 바람과 별과 시", "윤동주", amb, reference=REF, rejected=rej) is None
          and any("판본 확인 불가" in r for r in rej) and any("초판본" in r for r in rej),
          "다른 판본이 섞여 있고 맞는 판본이 없으면 제목뿐인 상품도 안 씀 (판본 확인 불가)")
    one = FakeCoupang([{"product_id": "1", "name": "어떤 책", "image_url": "https://coupang/img.jpg", "partner_url": "x"}])
    check(bi.coupang_book("어떤 책", "", one, reference="어떤 책 출판사")["product_id"] == "1", "판본이 하나뿐인 책(제목뿐 상품만)은 그대로 사용")
    calls.clear()
    os.environ["NL_CERT_KEY"] = "test-nl"
    fb = bi.collect_book("하늘과 바람과 별과 시", out_dir=work, coupang_client=amb)
    check(fb.cover_source == "nl" and not fb.purchase_link and any("다른 판본이라" in w and "직접 넣어" in w for w in fb.warnings),
          "판본이 어긋나면 쿠팡 표지·링크 대신 기본 표지(도서관) + 링크 직접 입력 안내")
    padded = Image.new("RGB", (600, 600), (255, 255, 255))
    padded.paste(Image.new("RGB", (400, 600), (90, 120, 80)), (100, 0))
    trimmed = bi.trim_margins(padded)
    full = bi.trim_margins(Image.new("RGB", (400, 600), (90, 120, 80)))
    check(trimmed.size == (400, 600) and full.size == (400, 600),
          "쇼핑몰 정사각 이미지의 흰 여백은 잘라 표지만 (여백 없는 표지는 그대로)")
    # ---------------------------------------------------------------- 캘린더 자동 제작 연결
    from core import scheduler as sch  # noqa: E402
    os.environ["KAKAO_REST_API_KEY"], os.environ["NL_CERT_KEY"] = "test-kakao", "test-nl"
    _collect = bi.collect_book
    bi.collect_book = lambda q, author="", aladin_link="", out_dir=".", **k: _collect(q, author=author, aladin_link=aladin_link,
                                                                                    out_dir=work, coupang_client=cp)
    spec = sch.spec_from_event({"mode": "book", "keyword": "윤동주"}, {"책": "하늘과 바람과 별과 시"}, None, mock=True)
    bi.collect_book = _collect
    check(spec.book_author == "윤동주" and "1948년" in spec.usp and spec.book_link == "https://link.coupang.com/a/BOOK"
          and spec.media_paths and spec.media_paths[0].endswith("book_cover_coupang.jpg") and spec.book_template == "reflect",
          "캘린더에 '책:' 한 줄 → 저자·책소개·표지(1번째 사진)·쿠팡 링크 자동, 원문 없으면 책소개로 사유 스토리텔링")
    check(any("자동 수집" in n and "국립중앙도서관" in n and "쿠팡 파트너스" in n for n in spec.notes),
          "결과 블록에 자동 수집 출처 안내")
    from core import pipeline  # noqa: E402
    from core.planners import COUPANG_DISCLOSURE  # noqa: E402
    check(pipeline.disclosure_short(spec) == COUPANG_DISCLOSURE, "쿠팡 링크로 만든 시골책방 영상은 쿠팡 파트너스 표시")

    # ---------------------------------------------------------------- 대시보드 [책 정보 자동 채우기]
    import shutil  # noqa: E402
    from streamlit.testing.v1 import AppTest  # noqa: E402
    cover_src = os.path.join(work, "cover_for_app.jpg")
    Image.new("RGB", (500, 750), (30, 40, 70)).save(cover_src)
    harness = (
        "import core.book_info as bi\n"
        "def fake(q, author='', aladin_link='', out_dir='.', **k):\n"
        "    import shutil, os\n"
        f"    p = os.path.join(out_dir, 'book_cover_coupang.jpg'); os.makedirs(out_dir, exist_ok=True); shutil.copy({cover_src!r}, p)\n"
        "    return bi.BookInfo(title='하늘과 바람과 별과 시', author='윤동주', publisher='B출판', isbn13='9788900000002',\n"
        "                       description='1948년 첫 출간된 윤동주의 유고 시집.', cover_path=p, cover_source='coupang', cover_size=(500, 750),\n"
        "                       purchase_link='https://link.coupang.com/a/BOOK', link_source='coupang', sources=['카카오 책 검색', '쿠팡 파트너스'])\n"
        "bi.collect_book = fake\n"
        f"exec(open({os.path.join(ROOT, 'app.py')!r}, encoding='utf-8').read(), {{'__name__': '__main__'}})\n")
    at = AppTest.from_string(harness, default_timeout=120).run()
    jobdir = at.session_state.job.workdir
    at.session_state.mode_switch = "📚 시골책방 도서 큐레이션"
    at.session_state.production_mode = "book"
    at.run()
    at.text_input(key="book_title").input("하늘과 바람과 별과").run()
    at.text_input(key="book_author").input("윤동주(직접)").run()
    [b for b in at.button if "책 정보 자동 채우기" in b.label][0].click().run()
    check(not at.exception and at.text_input(key="book_title").value == "하늘과 바람과 별과 시"
          and at.text_input(key="book_author").value == "윤동주(직접)" and "1948년" in at.text_area(key="book_info").value
          and at.text_input(key="book_link").value == "https://link.coupang.com/a/BOOK"
          and at.session_state.uploaded_paths[0].endswith("book_cover_coupang.jpg")
          and any("책 정보를 채웠어요" in (x.value or "") for x in at.success),
          "대시보드 [책 정보 자동 채우기]: 빈 칸만 채움(직접 쓴 저자 유지)·표지를 1번째 사진으로·쿠팡 링크")
    shutil.rmtree(jobdir, ignore_errors=True)
finally:
    bi.requests.get = _get
    for k, v in _env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
finish(fails)
