"""
core/book_info.py - 시골책방: 책 제목(또는 ISBN) 하나로 책 정보·표지·구매 링크 자동 수집

출처 (모두 공식 API, 책 페이지 크롤링 없음):
  1) 카카오(Daum) 책 검색  dapi.kakao.com/v3/search/book   .env KAKAO_REST_API_KEY   → 제목·저자·출판사·ISBN·책소개 일부·작은 표지
  2) 국립중앙도서관 ISBN 서지정보  nl.go.kr/seoji/SearchApi.do   .env NL_CERT_KEY   → 책소개·서평 원문(BOOK_INTRODUCTION/SUMMARY)·표지
  3) 쿠팡 파트너스 (이미 연동)  → 같은 책의 고화질 상품 이미지(표지) + 파트너스 제휴 링크
- 알라딘 OpenAPI는 2026-10-30 서비스 종료(신규 키 2026-09-04 마감)라 쓰지 않음. 알라딘 제휴 링크는 직접 붙여 넣으면 그것을 우선
- 수집한 책소개는 대본 근거(사실)로만 쓰고 화면에 그대로 옮기지 않음. 책 속 원문(인용)은 API가 주지 않으므로 계속 직접 입력
- 키가 없는 출처는 건너뛰고, 무엇이 비었는지 warnings로 알려 줌
"""
import html
import io
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests
from PIL import Image, ImageOps

logger = logging.getLogger("ad_factory.book_info")

KAKAO_URL = "https://dapi.kakao.com/v3/search/book"
NL_URL = "https://www.nl.go.kr/seoji/SearchApi.do"
TIMEOUT = 12
COVER_MIN_SIDE = 300          # 이보다 작은 표지는 2:3 카드(512x768)에서 흐려짐 → 경고
DESC_MAX = 2000


@dataclass
class BookInfo:
    title: str = ""
    author: str = ""
    publisher: str = ""
    isbn13: str = ""
    pub_date: str = ""
    description: str = ""                      # 대본 근거로 쓸 책소개 (여러 출처 합침, HTML 제거)
    cover_path: str = ""                       # 내려받은 표지 (가장 큰 것)
    cover_source: str = ""
    cover_size: tuple = (0, 0)
    purchase_link: str = ""
    link_source: str = ""                      # aladin(직접 입력) / coupang(자동)
    sources: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"「{self.title}」 {self.author} · {self.publisher} · ISBN {self.isbn13 or '?'} · 책소개 {len(self.description)}자 · "
                f"표지 {self.cover_source or '없음'} {self.cover_size[0]}x{self.cover_size[1]} · 링크 {self.link_source or '없음'}")


def _clean(text: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", t).strip()


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def _isbn13(s: str) -> str:
    """'89xxxx 978xxxx' 같은 카카오 isbn 필드나 입력에서 13자리 ISBN"""
    m = re.findall(r"97[89]\d{10}", re.sub(r"[-\s]", "", s or ""))
    return m[0] if m else ""


def looks_like_isbn(q: str) -> bool:
    return bool(re.fullmatch(r"(97[89])?\d{9}[\dXx]", re.sub(r"[-\s]", "", q or "")))


# ---------------------------------------------------------------- 1) 카카오 책 검색
def kakao_search(query: str, key: Optional[str] = None, size: int = 5) -> List[Dict[str, Any]]:
    key = key if key is not None else os.getenv("KAKAO_REST_API_KEY", "").strip()
    if not key:
        return []
    target = "isbn" if looks_like_isbn(query) else "title"
    r = requests.get(KAKAO_URL, headers={"Authorization": f"KakaoAK {key}"},
                     params={"query": query, "target": target, "size": size}, timeout=TIMEOUT)
    if r.status_code == 401:
        raise BookInfoError("카카오 REST API 키가 올바르지 않아요 (.env KAKAO_REST_API_KEY).")
    r.raise_for_status()
    return r.json().get("documents") or []


def pick_kakao(docs: List[Dict[str, Any]], title: str, author: str = "") -> Optional[Dict[str, Any]]:
    """제목이 정확히 같은 책 우선 (+저자), 없으면 제목을 포함하는 첫 결과"""
    nt, na = _norm(title), _norm(author)
    if looks_like_isbn(title):                       # ISBN 조회: ISBN이 같은 책만
        q = re.sub(r"[-\s]", "", title)
        return next((d for d in docs if q in re.sub(r"[-\s]", "", d.get("isbn") or "")), None)

    def score(d):
        dt_, da = _norm(d.get("title")), _norm(" ".join(d.get("authors") or []))
        return (dt_ == nt) * 4 + (nt in dt_) * 2 + (bool(na) and na in da) * 1
    ranked = sorted(docs, key=score, reverse=True)
    return ranked[0] if ranked and score(ranked[0]) > 0 else None


# ---------------------------------------------------------------- 2) 국립중앙도서관 ISBN 서지정보
def nl_lookup(isbn13: str, key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    key = key if key is not None else os.getenv("NL_CERT_KEY", "").strip()
    if not key or not isbn13:
        return None
    r = requests.get(NL_URL, params={"cert_key": key, "result_style": "json", "page_no": 1, "page_size": 1, "isbn": isbn13},
                     timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if str(data.get("RESULT") or "").upper() == "ERROR" or data.get("ERR_CODE"):
        raise BookInfoError(f"국립중앙도서관 API 오류 ({data.get('ERR_CODE') or data.get('ERR_MESSAGE')}). .env NL_CERT_KEY를 확인해 주세요.")
    docs = data.get("docs") or []
    return docs[0] if docs else None


# ---------------------------------------------------------------- 3) 쿠팡 파트너스 (표지·제휴 링크)
# 판본을 가르는 표시. 강한 표시가 쿠팡 상품명에 있는데 기준 판본(카카오 제목·출판사·책소개)에 없으면 다른 판본
STRONG_MARKERS = ("초판본", "초판", "필사", "육필", "개정판", "개정증보", "증보판", "특별판", "한정판", "리커버", "양장", "반양장",
                  "합본", "큰글씨", "큰글자", "영문판", "영한", "세트", "오디오북", "ebook", "전자책", "만화", "그림책", "컬러링",
                  "미니북", "포켓", "사진판", "필사본", "낭독")
WEAK_MARKERS = ("에디션", "edition", "기념판", "뉴")          # 같은 판본 홍보 문구에도 자주 붙음 → 판정에 안 씀


def edition_mismatch(product_name: str, reference: str) -> List[str]:
    """쿠팡 상품명의 강한 판본 표시 중 기준 판본에 없는 것 (빈 목록 = 판본이 어긋나는 표시 없음)"""
    pn, ref = _norm(product_name), _norm(reference)
    return [m for m in STRONG_MARKERS if _norm(m) in pn and _norm(m) not in ref]


def _extra_tokens(product_name: str, title: str, author: str) -> List[str]:
    """상품명에서 제목·저자를 뺀 나머지 낱말 (2자 이상) - 판본을 구별하는 단서"""
    rest = product_name
    for w in [title, author] + re.split(r"\s+", title):
        if w:
            rest = rest.replace(w, " ")
    toks = [t for t in re.findall(r"[가-힣A-Za-z0-9]+", rest) if len(t) >= 2]
    return [t for t in toks if _norm(t) not in {_norm(m) for m in WEAK_MARKERS}]


def score_edition(product_name: str, title: str, author: str, reference: str) -> Dict[str, Any]:
    """판본 일치 점수: 기준 판본에도 나오는 낱말 수 - 안 나오는 낱말 수 - 강한 표시 불일치×3"""
    ref = _norm(reference)
    toks = _extra_tokens(product_name, title, author)
    hit = [t for t in toks if _norm(t) in ref]
    miss = [t for t in toks if _norm(t) not in ref]
    bad = edition_mismatch(product_name, reference)
    return {"score": len(hit) - len(miss) - 3 * len(bad), "hit": hit, "miss": miss, "bad": bad, "plain": not toks}


def coupang_book(title: str, author: str = "", client=None, reference: str = "",
                 rejected: Optional[List[str]] = None, publisher: str = "") -> Optional[Dict[str, Any]]:
    """
    쿠팡에서 같은 판본 찾기 (다른 상품·다른 판본을 표지·구매 링크로 쓰지 않게). reference = 카카오 판본의 제목·출판사·책소개
    - 판본 단서가 기준 판본과 맞는 상품(점수 ≥ 1) 중 최고점
    - 상품명이 제목뿐인 상품은, 검색 결과에 다른 판본이 보이면(여러 판본) 판본을 확인할 수 없어 쓰지 않음.
      모든 후보가 제목뿐이면(판본이 하나뿐인 책) 첫 번째를 씀
    rejected: 건너뛴 상품과 이유를 담아 돌려줌
    """
    from core.coupang import CoupangPartners
    client = client or CoupangPartners()
    if not client.enabled:
        return None
    nt = _norm(title)
    # 출판사를 붙여 먼저 찾고(같은 판본이 잘 잡힘), 없으면 저자로 (검색 한도 1시간 10회라 최대 2번)
    queries = list(dict.fromkeys(q.strip() for q in (f"{title} {publisher}" if publisher else "", f"{title} {author}") if q.strip()))
    cands, seen = [], set()
    for q in queries:
        for it in client.search(q, limit=10):
            if it.get("product_id") not in seen and nt and nt in _norm(it.get("name")) and it.get("image_url"):
                seen.add(it.get("product_id"))
                cands.append(it)
        if any(score_edition(it.get("name", ""), title, author, reference or title)["score"] >= 1 for it in cands):
            break
    scored = [(score_edition(it.get("name", ""), title, author, reference or title), it) for it in cands]
    good = sorted([x for x in scored if x[0]["score"] >= 1 and not x[0]["bad"]], key=lambda x: -x[0]["score"])
    if good:
        return good[0][1]
    many_editions = any(not sc["plain"] for sc, _ in scored)
    plain = [it for sc, it in scored if sc["plain"]]
    if plain and not many_editions:
        return plain[0]
    if rejected is not None:
        for sc, it in scored[:3]:
            why = (f"판본 표시 {', '.join(sc['bad'])}" if sc["bad"] else "제목뿐이라 판본 확인 불가" if sc["plain"]
                   else f"판본 단서 불일치 {', '.join(sc['miss'][:3])}")
            rejected.append(f"'{it.get('name', '')[:30]}' ({why})")
    return None


class BookInfoError(RuntimeError):
    pass


def trim_margins(im, thresh: int = 242, min_gain: float = 0.04):
    """가장자리의 거의 흰색(또는 단색) 여백을 잘라냄. 잘라낼 게 4% 미만이면 그대로"""
    from PIL import ImageChops
    bg = im.getpixel((0, 0))
    if min(bg) < thresh:                 # 모서리가 흰색이 아니면 여백 없는 표지로 봄
        return im
    diff = ImageChops.difference(im, Image.new("RGB", im.size, bg)).convert("L").point(lambda v: 255 if v > 18 else 0)
    box = diff.getbbox()
    if not box:
        return im
    w, h = im.size
    if (box[2] - box[0]) * (box[3] - box[1]) > w * h * (1 - min_gain):
        return im
    return im.crop(box)


def _download(url: str, out_path: str) -> Optional[tuple]:
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or len(r.content) < 500:
            return None
        with Image.open(io.BytesIO(r.content)) as source:
            im = trim_margins(ImageOps.exif_transpose(source).convert("RGB"))  # EXIF 보정 후 흰 여백 → 표지만
            im.save(out_path, "JPEG", quality=95)
            return im.size
    except Exception as e:
        logger.info(f"표지 내려받기 실패 ({url}): {e}")
        return None


def collect_book(query: str, author: str = "", aladin_link: str = "", out_dir: str = ".", use_coupang: bool = True,
                 coupang_client=None) -> BookInfo:
    """
    책 제목(또는 ISBN) → BookInfo. 표지는 내려받아 가장 큰 것(세로 표지 비율 우선), 링크는 알라딘 직접 입력 > 쿠팡 파트너스
    아무 출처에서도 책을 못 찾으면 BookInfoError (지어내지 않음)
    """
    from core.shorts_meta import shop_link_kind
    info = BookInfo()
    query = (query or "").strip()
    if not query:
        raise BookInfoError("책 제목이나 ISBN을 적어 주세요.")
    # 1) 카카오
    try:
        doc = pick_kakao(kakao_search(query), query, author)
    except BookInfoError:
        raise
    except Exception as e:
        doc = None
        info.warnings.append(f"카카오 책 검색 실패: {e}")
    if not os.getenv("KAKAO_REST_API_KEY", "").strip():
        info.warnings.append("카카오 키 없음 (.env KAKAO_REST_API_KEY) → 카카오 책 검색 건너뜀")
    covers: List[tuple] = []        # (url, source)
    if doc:
        info.title = _clean(doc.get("title"))
        info.author = ", ".join(doc.get("authors") or [])
        info.publisher = doc.get("publisher") or ""
        info.isbn13 = _isbn13(doc.get("isbn"))
        info.pub_date = (doc.get("datetime") or "")[:10]
        if doc.get("contents"):
            info.description = _clean(doc["contents"])
        if doc.get("thumbnail"):
            covers.append((doc["thumbnail"], "kakao"))
        info.sources.append("카카오 책 검색")
    elif looks_like_isbn(query):
        info.isbn13 = _isbn13(query) or re.sub(r"[-\s]", "", query)
    # 2) 국립중앙도서관 (ISBN으로)
    try:
        nl = nl_lookup(info.isbn13)
    except BookInfoError:
        raise
    except Exception as e:
        nl = None
        info.warnings.append(f"국립중앙도서관 조회 실패: {e}")
    if not os.getenv("NL_CERT_KEY", "").strip():
        info.warnings.append("국립중앙도서관 키 없음 (.env NL_CERT_KEY) → 책소개 원문·표지 보강 건너뜀")
    if nl:
        info.title = info.title or _clean(nl.get("TITLE"))
        info.author = info.author or _clean(nl.get("AUTHOR"))
        info.publisher = info.publisher or _clean(nl.get("PUBLISHER"))
        intro = " ".join(_clean(nl.get(k)) for k in ("BOOK_INTRODUCTION", "BOOK_SUMMARY") if nl.get(k))
        if len(intro) > len(info.description):
            info.description = (intro + (" " + info.description if info.description and info.description not in intro else "")).strip()
        if nl.get("TITLE_URL"):
            covers.insert(0, (nl["TITLE_URL"], "nl"))
        info.sources.append("국립중앙도서관")
    if not info.title:
        raise BookInfoError(f"'{query}' 책을 찾지 못했어요. 제목을 정확히 적거나 ISBN(13자리)으로 적어 주세요."
                            + (" (" + " / ".join(info.warnings) + ")" if info.warnings else ""))
    info.description = info.description[:DESC_MAX]
    # 3) 쿠팡 파트너스: 고화질 표지 + 제휴 링크
    cp = None
    if use_coupang:
        try:
            skipped: List[str] = []
            ref = " ".join([info.title, info.publisher, info.description[:400]])
            cp = coupang_book(info.title, info.author, coupang_client, reference=ref, rejected=skipped,
                              publisher=info.publisher)
            if skipped and not cp:
                info.warnings.append("쿠팡 검색 결과가 다른 판본이라 표지·구매 링크로 쓰지 않았어요: " + "; ".join(skipped[:2])
                                     + " → 기본 표지를 쓰고, 구매 링크는 직접 넣어 주세요 (알라딘 제휴 링크 등)")
        except Exception as e:
            info.warnings.append(f"쿠팡 파트너스 검색 실패: {e}")
        if cp:
            covers.insert(0, (cp["image_url"], "coupang"))
            info.sources.append("쿠팡 파트너스")
    # 표지: 모두 내려받아 짧은 변이 가장 긴 것 (같으면 앞 순서 = 쿠팡 > 도서관 > 카카오)
    os.makedirs(out_dir, exist_ok=True)
    best = None
    for k, (url, src) in enumerate(covers):
        path = os.path.join(out_dir, f"book_cover_{src}.jpg")
        size = _download(url, path)
        if size and (best is None or min(size) > min(best[1])):
            best = (path, size, src)
    if best:
        info.cover_path, info.cover_size, info.cover_source = best[0], best[1], best[2]
        if min(best[1]) < COVER_MIN_SIDE:
            info.warnings.append(f"표지가 작아요 ({best[1][0]}x{best[1][1]}) → 화면에서 흐릴 수 있어요. 책 표지를 직접 찍어 올리면 더 선명해요.")
    else:
        info.warnings.append("표지를 찾지 못했어요 → 책 표지 사진을 직접 올려 주세요.")
    # 구매 링크: 직접 넣은 알라딘 링크 우선, 없으면 쿠팡 파트너스
    if aladin_link.strip():
        info.purchase_link, info.link_source = aladin_link.strip(), shop_link_kind(aladin_link)
    elif cp and cp.get("partner_url"):
        link = cp["partner_url"]
        try:
            from core.coupang import CoupangPartners
            link = (coupang_client or CoupangPartners()).deeplink(f"https://www.coupang.com/vp/products/{cp['product_id']}") or link
        except Exception:
            pass
        info.purchase_link, info.link_source = link, "coupang"
    else:
        info.warnings.append("구매 링크 없음 (알라딘 제휴 링크를 붙여 넣거나 쿠팡 파트너스 키를 설정해 주세요)")
    logger.info(f"책 정보 수집: {info.summary()} · 출처 {', '.join(info.sources)}")
    return info
