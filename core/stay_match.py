"""
core/stay_match.py - 숙소 제휴 2~4단계(데이터 쪽): 여기어때 상세 매칭 · 1박 요금/인원/시설 파싱 · 1인당 체감가 · 제휴 링크 · 고정 댓글 문구

무차단 하이브리드: 여기어때 페이지를 읽을 수 있으면 거기서, 막히면(403 등) 네이버 블로그·웹문서 요약문에서 같은 정보를 뽑는다. 어느 쪽도 못 찾으면
숫자를 지어내지 않고 비워 둔다 (대본은 가격 없이 시설·뷰 중심 문구로 바뀜).
"""
import html
import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("ad_factory.stay_match")

YEOGI_SEARCH = "https://www.yeogi.com/domestic-accommodations?keyword={q}"
YEOGI_DETAIL = "https://www.yeogi.com/domestic-accommodations/{pid}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MIN_NIGHT, MAX_NIGHT = 40_000, 3_000_000          # 1박 요금으로 볼 수 있는 범위 (바비큐 세트·입장료 같은 소액 제외)
DEFAULT_PEOPLE = 4

# 대본 후킹에 쓸 핵심 시설 (표기 → 대표 이름). 앞에 있을수록 우선
FACILITY_PATTERNS = [
    ("개별 온수풀", r"개별\s*(?:온수\s*)?(?:수영장|풀)|프라이빗\s*(?:온수\s*)?(?:수영장|풀)|온수\s*풀|온수\s*수영장|개인\s*풀"),
    ("인피니티풀", r"인피니티"),
    ("노천 자쿠지", r"노천\s*(?:자쿠지|스파|욕조)|야외\s*(?:자쿠지|스파)|자쿠지"),
    ("히노끼탕", r"히노끼|편백\s*(?:탕|욕조)"),
    ("통창 뷰", r"통창|파노라마|통유리"),
    ("오션뷰", r"오션\s*뷰|바다\s*(?:가\s*보이|뷰)|씨\s*뷰"),
    ("리버뷰", r"리버\s*뷰|강\s*뷰|북한강|남한강|강이\s*보이"),
    ("마운틴뷰", r"마운틴\s*뷰|산\s*뷰|숲\s*뷰"),
    ("불멍", r"불멍|화로대"),
    ("바비큐", r"바비큐|바베큐|BBQ|숯불"),
    ("조식 무료", r"조식\s*(?:무료|제공|포함)|무료\s*조식"),
]
VIEW_NAMES = ("오션뷰", "리버뷰", "마운틴뷰", "통창 뷰")
WATER_NAMES = ("개별 온수풀", "인피니티풀", "노천 자쿠지", "히노끼탕")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


_PROVINCES = {"서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"}


def place_token(region: str) -> str:
    """'경기도 가평군' → '가평', '강원특별자치도 강릉시' → '강릉', '서울 마포구' → '마포' (시·도 이름은 건너뜀. 하나뿐이면 그것)"""
    toks = (region or "").split()
    def strip(t):
        return re.sub(r"(특별자치도|특별자치시|특별시|광역시|도|시|군|구)$", "", t) or t
    for t in toks:
        if t in _PROVINCES or re.sub(r"(특별자치도|특별자치시|특별시|광역시|도)$", "", t) in _PROVINCES and len(toks) > 1:
            continue
        return strip(t)
    return strip(toks[0]) if toks else ""


def extract_facilities(text: str) -> List[str]:
    """텍스트에 실제로 나온 시설만 대표 이름으로 (중복 제거, FACILITY_PATTERNS 순서)"""
    t = text or ""
    return [name for name, pat in FACILITY_PATTERNS if re.search(pat, t, flags=re.I)]


# ---------------------------------------------------------------- 가격·인원
_PRICE_RES = [
    re.compile(r"(\d{1,3}(?:,\d{3})+)\s*원"),
    re.compile(r"(\d{1,3})\s*만\s*(?:(\d)\s*천\s*)?원"),
]
_CONTEXT = re.compile(r"1\s*박|객실|숙박|평일|주말|최저가?|부터|~|원\s*/\s*박|비수기|성수기")


def parse_night_price(text: str) -> Optional[int]:
    """1박 최저 요금(원). '1박·객실·숙박·평일·주말·최저가·부터' 같은 말 가까이에 나온 40,000~3,000,000원만 인정하고 그중 가장 낮은 값"""
    t = _clean(text)
    found: List[int] = []
    for rx in _PRICE_RES:
        for m in rx.finditer(t):
            if rx is _PRICE_RES[0]:
                v = int(m.group(1).replace(",", ""))
            else:
                v = int(m.group(1)) * 10_000 + (int(m.group(2)) * 1_000 if m.group(2) else 0)
            ctx = t[max(0, m.start() - 24): m.end() + 24]
            if MIN_NIGHT <= v <= MAX_NIGHT and _CONTEXT.search(ctx):
                found.append(v)
    return min(found) if found else None


def parse_capacity(text: str) -> Dict[str, Optional[int]]:
    """'기준 4인', '기준인원 2명', '최대 6인', '4~6인' → {"base", "max"} (모르면 None)"""
    t = _clean(text)
    base = max_ = None
    m = re.search(r"기준\s*(?:인원)?\s*(\d{1,2})\s*(?:인|명)", t)
    if m:
        base = int(m.group(1))
    m = re.search(r"최대\s*(?:인원)?\s*(\d{1,2})\s*(?:인|명)", t)
    if m:
        max_ = int(m.group(1))
    m = re.search(r"(\d{1,2})\s*[~\-]\s*(\d{1,2})\s*(?:인|명)", t)
    if m:
        base = base or int(m.group(1))
        max_ = max_ or int(m.group(2))
    ok = lambda v: v if (v and 1 <= v <= 30) else None      # noqa: E731
    return {"base": ok(base), "max": ok(max_)}


def per_person_text(price: Optional[int], people: int) -> str:
    """1박 요금 ÷ 인원 → '4만 원대'. 요금을 모르면 빈 문자열 (지어내지 않음)"""
    if not price or people < 1:
        return ""
    return f"{max(1, int(price / people // 10_000))}만 원대"


def stay_alias(region: str, category: str, facilities: List[str]) -> str:
    """상단 워터마크용 특징 별칭 (숙소 본명 대신): '가평 리버뷰 독채풀빌라' 형태. 근거 없는 수식어는 넣지 않음"""
    reg = place_token(region)
    view = next((v for v in facilities if v in ("오션뷰", "리버뷰", "마운틴뷰")), "")
    cat = category or ""
    kind = "풀빌라" if ("풀빌라" in cat or "개별 온수풀" in facilities) else "한옥" if "한옥" in cat else "글램핑" if "글램핑" in cat \
        else "카라반" if "카라반" in cat else "펜션" if "펜션" in cat else "숙소"
    if "독채" in cat or ("독채" in kind):
        kind = "독채" + kind if kind != "숙소" else "독채 숙소"
    return " ".join(x for x in (reg, view, kind) if x).strip()


def build_stay_facts(texts: Dict[str, str], stay: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    출처별 텍스트({"yeogi": 상세 페이지 글, "blog": 블로그·웹 요약문 …}) → 가격·인원·시설 팩트.
    가격·인원은 출처 우선순위(yeogi → blog)대로 처음 찾은 값, 시설은 모든 출처의 합집합.
    → {nightly_price, price_source, base_people, max_people, people_used, people_estimated, per_person, facilities, signature, view, alias, sources}
    """
    stay = stay or {}
    price = src_price = None
    cap = {"base": None, "max": None}
    sources: List[str] = []
    facilities: List[str] = []
    for src in ("yeogi", "blog"):
        txt = texts.get(src) or ""
        if not txt:
            continue
        sources.append(src)
        if price is None:
            price = parse_night_price(txt)
            src_price = src if price else None
        c = parse_capacity(txt)
        cap = {"base": cap["base"] or c["base"], "max": cap["max"] or c["max"]}
        for f in extract_facilities(txt):
            if f not in facilities:
                facilities.append(f)
    for f in stay.get("charms") or []:                       # 큐레이션 단계에서 이미 확인된 시설어도 합침
        mapped = extract_facilities(f)
        for m in mapped:
            if m not in facilities:
                facilities.append(m)
    order = [n for n, _ in FACILITY_PATTERNS]
    facilities.sort(key=order.index)
    people = cap["base"] or DEFAULT_PEOPLE
    signature = next((f for f in facilities if f in WATER_NAMES), "") or next((f for f in facilities if f in VIEW_NAMES), "")
    return {
        "nightly_price": price, "price_source": src_price, "base_people": cap["base"], "max_people": cap["max"],
        "people_used": people, "people_estimated": cap["base"] is None,
        "per_person": per_person_text(price, people), "facilities": facilities, "signature": signature,
        "view": next((f for f in facilities if f in VIEW_NAMES), ""),
        "alias": stay_alias(stay.get("region", ""), stay.get("category", ""), facilities), "sources": sources,
    }


# ---------------------------------------------------------------- 여기어때 매칭 (읽을 수 있을 때만, 막히면 조용히 폴백)
def _norm(s: str) -> str:
    return re.sub(r"[\s·,.\-_()\[\]]", "", s or "").lower()


def yeogi_search_url(name: str) -> str:
    return YEOGI_SEARCH.format(q=urllib.parse.quote(name.strip()))


def find_yeogi(name: str, timeout: int = 5) -> Dict[str, Any]:
    """
    여기어때 검색 결과 페이지에서 숙소 상세 URL을 찾는다 → {"url": 검증된 상세 URL 또는 "", "search_url", "blocked": 접속 막힘, "error"}.
    검색 결과 링크 근처에 숙소 이름이 실제로 있을 때만 '검증됨'으로 인정 (엉뚱한 숙소 링크 금지)
    """
    out = {"url": "", "search_url": yeogi_search_url(name), "blocked": False, "error": ""}
    try:
        r = requests.get(out["search_url"], headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}, timeout=timeout)
    except Exception as e:
        out.update(blocked=True, error=f"여기어때에 연결하지 못했어요 ({type(e).__name__})")
        return out
    if r.status_code != 200:
        out.update(blocked=True, error=f"여기어때가 접속을 막았어요 (HTTP {r.status_code}) → 네이버 자료로 대신해요")
        return out
    page = r.text or ""
    key = _norm(name)
    hits = list(re.finditer(r"/domestic-accommodations/(\d{3,})", page))
    for n, m in enumerate(hits):
        end = hits[n + 1].start() if n + 1 < len(hits) else m.start() + 1500
        around = _norm(_clean(page[m.start(): min(end, m.start() + 1500)]))        # 이 링크 카드 안(다음 링크 앞까지)의 이름만 인정
        if key and key in around:
            out["url"] = YEOGI_DETAIL.format(pid=m.group(1))
            return out
    out["error"] = "여기어때 검색 결과에서 같은 이름의 숙소를 확인하지 못했어요"
    return out


def fetch_yeogi_text(url: str, timeout: int = 6) -> str:
    """여기어때 상세 페이지 글 (스크립트·태그 제거). 막히거나 실패하면 빈 문자열"""
    if not url:
        return ""
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}, timeout=timeout)
    except Exception:
        return ""
    if r.status_code != 200:
        return ""
    body = re.sub(r"<(script|style)\b(?![^>]*__NEXT_DATA__)[^>]*>.*?</\1>", " ", r.text or "", flags=re.S | re.I)
    return _clean(body)[:60_000]


def collect_stay_facts(stay: Dict[str, Any], cid: str, secret: str, yeogi_url: str = "") -> Dict[str, Any]:
    """선택한 숙소의 팩트 수집: 여기어때 상세(가능하면) + 네이버 블로그·웹 요약문 → build_stay_facts"""
    from core.store_research import naver_blog_snippets
    name = stay.get("name", "")
    region_tokens = [place_token(stay.get("region", "") or stay.get("address", ""))]
    texts = {"yeogi": fetch_yeogi_text(yeogi_url)}
    snippets = naver_blog_snippets(name, cid, secret, display=20, region_tokens=region_tokens or None) if (cid and secret) else []
    texts["blog"] = " ".join(snippets)
    return build_stay_facts(texts, stay)


# ---------------------------------------------------------------- 제휴 링크 · 고정 댓글
def stay_hashtags(region: str, alias: str, facts: Dict[str, Any]) -> List[str]:
    """고정 댓글 해시태그: 지역·유형·확인된 시설·여기어때 (근거 없는 태그 없음)"""
    reg = place_token(region)
    kind = (alias or "").split()[-1] if alias else ""
    tags = ["#여기어때"] + ([f"#{reg}숙소", f"#{reg}여행"] if reg else []) + ([f"#{kind}"] if kind and kind != "숙소" else [])
    tags += [f"#{f.replace(' ', '')}" for f in (facts.get("facilities") or [])[:3]] + ["#국내여행", "#주말여행", "#숙소추천"]
    out: List[str] = []
    for t in tags:
        if t not in out:
            out.append(t)
    return out


def build_affiliate_link(template: str, partner_id: str, target_url: str) -> Dict[str, str]:
    """
    사용자가 넣은 트래킹 템플릿/파트너 ID로 예약 링크 생성. 치환자: {url}(주소 전체 인코딩) {url_raw} {id}/{partner_id}.
    템플릿이 없으면 파트너 ID가 있어도 어떤 파라미터에 넣는지 알 수 없어 지어내지 않고 일반 링크로 둔다 (수익 추적 안 됨 경고)
    → {"link", "tracked": "1"/"", "note"}
    """
    tpl = (template or "").strip()
    pid = (partner_id or "").strip()
    url = (target_url or "").strip()
    if not url:
        return {"link": "", "tracked": "", "note": "예약 페이지 주소가 없어 링크를 만들지 못했어요. 숙소 링크를 직접 붙여 넣어 주세요."}
    if tpl:
        link = (tpl.replace("{url_raw}", url).replace("{url}", urllib.parse.quote(url, safe=""))
                .replace("{partner_id}", pid).replace("{id}", pid))
        return {"link": link, "tracked": "1", "note": ""}
    return {"link": url, "tracked": "", "note": "사이드바에 제휴 트래킹 템플릿을 넣지 않아 일반 링크예요 (수익이 추적되지 않아요)."}


def comment_package(alias: str, facts: Dict[str, Any], link: str, hashtags: List[str]) -> str:
    """고정 댓글 문구: 숙소 특징 요약 + 제휴 예약 링크 + 해시태그 (가격·시설은 확인된 것만)"""
    bits = []
    if facts.get("per_person"):
        est = " (4인 기준)" if facts.get("people_estimated") else f" ({facts.get('people_used')}인 기준)"
        bits.append(f"1인 {facts['per_person']}{est}")
    if facts.get("facilities"):
        bits.append(" · ".join(facts["facilities"][:4]))
    summary = f"📍 {alias}" + (f" — {' / '.join(bits)}" if bits else "")
    from core.photo_safety import SOURCE_NOTICE
    lines = [summary, f"🔗 여기어때 최저가 예약: {link or '(예약 링크를 입력해 주세요)'}",
             "※ 이 영상은 제휴(어필리에이트) 활동의 일환으로, 링크로 예약하시면 일정액의 수수료를 받을 수 있습니다.", SOURCE_NOTICE]
    tags = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags if h)
    if tags:
        lines.append(tags)
    return "\n".join(lines)
