"""
core/store_research.py - 상호명만으로 매장 정보 자동 수집 (크롤링 없음: 공식 API + 웹 검색 그라운딩)

수집 순서와 합치기
  (2순위·공식) 네이버 지역 검색 + 블로그 검색 API(NAVER API HUB, 서버 차단 없음) → 도로명 주소·업종, 블로그 요약문 20건
  (1순위)      Gemini google_search 그라운딩 → 웹 문서·언론·맛집 사이트에 실제로 있는 메뉴명·후기 명사·주소·상권
  병렬로 부른 뒤 합친다 (한쪽이 실패해도 나머지로 진행).

할루시네이션 방지 (핵심)
  - 두 출처가 서로 확인해 준 항목만 '확인됨(confirmed)': 블로그 요약문 글자 안에 실제로 있는 메뉴·명사, 또는 그라운딩 메타데이터(검색 근거 링크)가
    있는 응답. 근거를 못 찾은 항목은 '미확인(unconfirmed)'으로 따로 두고 기본으로는 대본에 쓰지 않는다 (화면에 보여 주고 사장님이 확인).
  - 항목마다 출처(source)가 없는 것은 버린다. 형용사는 뽑지 않는다 (블로그 명사는 core.store_facts의 결정적 추출기 사용).
  - 주소는 공식 지역 검색 결과를 우선한다.
"""
import html
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from core.store_facts import extract_review_nouns, extract_special_menus, location_context

logger = logging.getLogger("ad_factory.store_research")

BLOG_SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/blog"      # NAVER API HUB (지역 검색과 같은 키)
LEGACY_BLOG_URL = "https://openapi.naver.com/v1/search/blog.json"             # 예전 developers.naver.com 키가 있을 때만
GROUNDING_MODEL = "gemini-3.6-flash"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


# ---------------------------------------------------------------- 네이버 블로그 검색 (공식 API)
SEARCH_KINDS = ("blog", "webkr", "cafearticle", "news")      # NAVER API HUB 검색 종류: 블로그·웹문서·카페글·뉴스 (앱에서 켠 것만 응답, 나머지는 건너뜀)


def naver_blog_snippets(name: str, client_id: str, client_secret: str, display: int = 20, timeout: int = 6,
                        region_tokens: Optional[List[str]] = None) -> List[str]:
    """
    '{상호명} …' 네이버 검색(블로그, 앱에서 켜 둔 웹문서·카페글·뉴스) 요약문(description) 모음. 키가 없거나 실패하면 빈 목록(예외 없음).
    같은 이름의 다른 지역·다른 가게 글은 제외: 상호가 제목·요약에 있고, 지역을 알면 그 지역 말도 있어야 함
    """
    if not (client_id and client_secret and (name or "").strip()):
        return []
    out: List[str] = []
    for kind in SEARCH_KINDS:
        attempts = [(f"https://naverapihub.apigw.ntruss.com/search/v1/{kind}", {"X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": client_secret})]
        if kind == "blog":
            attempts.append((LEGACY_BLOG_URL, {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}))
        for suffix in (("메뉴 솔직후기", "후기", "맛집") if kind == "blog" else ("메뉴",)):         # 검색어를 바꿔 요약문을 더 모음
            q = f"{name.strip()} {suffix}"
            hit = False
            for url, headers in attempts:
                try:
                    r = requests.get(url, params={"query": q, "display": max(1, min(display, 100))}, headers=headers, timeout=timeout)
                except Exception as e:
                    logger.info(f"네이버 {kind} 검색 실패({url.split('/')[2]}): {type(e).__name__}")
                    continue
                if r.status_code == 200:
                    hit = True
                    for it in (r.json().get("items") or []):
                        d = _clean(it.get("description", ""))      # 제목은 검색 노출용 문구라 요약에서 제외
                        whole = _norm(_clean(it.get("title", "")) + d)
                        if not d or _norm(name) not in whole:
                            continue
                        if region_tokens and not any(t in whole for t in region_tokens):
                            continue
                        if d not in out:
                            out.append(d)
                    break
            if not hit and kind != "blog":
                break                                              # 이 검색 종류는 앱에서 켜지 않음 → 다음 종류로
    return out[:display * 3]


# ---------------------------------------------------------------- Gemini 웹 검색 그라운딩
def _grounding_prompt(name: str, region: str) -> str:
    q = f"{name} {region} 대표메뉴 시그니처 메뉴 밑반찬 주차 손님 후기 특징".replace("  ", " ")
    return f"""[역할: 실시간 웹 검색 기반 매장 팩트 수집기]
반드시 Google Search 검색 결과에서 실제로 확인된 매장("{name}")의 정보만 추출하라.
검색 결과에서 확인되지 않는 메뉴나 반찬 이름을 추측해서 적는 것을 엄격히 금지한다. 모르면 빈 목록으로 두어라.
검색어: "{q}"

1. 주소 및 상권(address / location_context): 검색된 정확한 도로명 주소 및 주변 지리적 특징
2. 시그니처 및 식사 메뉴(special_menus): 검색 결과에 실제로 등장하는 대표 메뉴 및 특이 식사 메뉴명
3. 손님 후기 핵심 명사(review_nouns): 블로그·웹 후기에서 반복 언급되는 조리 특징, 반찬명, 식기, 편의시설 명사 (형용사 제외)
항목마다 근거가 된 사이트 이름을 source에 적어라 (출처 없는 항목은 버려진다).
JSON만 출력: {{"address": "...", "location_context": "...", "special_menus": [{{"name": "...", "source": "..."}}], "review_nouns": [{{"noun": "...", "source": "..."}}]}}"""


def _parse_json_text(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    m = re.search(r"\{.*\}", t, flags=re.S)
    return json.loads(m.group(0)) if m else {}


def gemini_grounded(name: str, region: str, api_key: str) -> Dict[str, Any]:
    """
    Gemini + google_search 도구로 매장 팩트를 받는다 → {"address","location_context","special_menus":[{name,source}],"review_nouns":[...],
    "grounded": 검색 근거 메타데이터가 있었는지, "sources": [사이트명]}. 실패하면 빈 값 + error
    """
    out: Dict[str, Any] = {"address": "", "location_context": "", "special_menus": [], "review_nouns": [], "grounded": False, "sources": []}
    if not (api_key and (name or "").strip()):
        return out
    try:
        from google import genai
        from google.genai import types
        from core.guardrail import validate_model_call
        client = genai.Client(api_key=api_key)
        res = client.models.generate_content(
            model=validate_model_call(GROUNDING_MODEL, category="llm"), contents=_grounding_prompt(name, region),
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.1))
        data = _parse_json_text(res.text or "")
        gm = getattr(res.candidates[0], "grounding_metadata", None) if getattr(res, "candidates", None) else None
        chunks = list(getattr(gm, "grounding_chunks", None) or []) if gm else []
        out["grounded"] = bool(chunks)
        out["sources"] = [str(getattr(getattr(c, "web", None), "title", "") or "") for c in chunks][:10]
        out["address"] = _clean(str(data.get("address") or ""))
        out["location_context"] = _clean(str(data.get("location_context") or ""))
        for key, field in (("special_menus", "name"), ("review_nouns", "noun")):
            for it in data.get(key) or []:
                if isinstance(it, dict):
                    val, src = _clean(str(it.get(field) or "")), _clean(str(it.get("source") or ""))
                    if 2 <= len(val) <= 24 and src and val not in [x[field] for x in out[key]]:      # 출처 없는 항목은 버림
                        out[key].append({field: val, "source": src})
    except Exception as e:
        logger.warning(f"Gemini 웹 검색 그라운딩 실패: {type(e).__name__}: {str(e)[:160]}")
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return out


# ---------------------------------------------------------------- 합치기 (확인됨 / 미확인)
def _norm(s: str) -> str:
    return re.sub(r"[\s·,.]", "", s or "")


_SEO_WORDS = {"맛집", "내돈내산", "추천", "후기", "리뷰", "솔직", "현지인", "블로그", "포스팅", "방문", "주문", "메뉴", "가격", "위치", "영업시간",
              "전화", "예약", "맛스타그램", "먹스타그램", "존맛", "핫플", "여행", "코스", "주말", "나들이", "근처", "주변", "인근", "가볼만한곳"}


def _noise_words(name: str, region: str, address: str) -> set:
    """매장명·지역명·검색용 문구(SEO) — 블로그 글에서 명사로 잘못 뽑히면 안 되는 말"""
    words = set(_SEO_WORDS)
    for src in (name, region, address):
        for tok in re.findall(r"[가-힣A-Za-z0-9]{2,}", src or ""):
            words.add(tok)
            stem = re.sub(r"(특별시|광역시|특별자치시|특별자치도|도|시|군|구|면|읍|동)$", "", tok)
            if len(stem) >= 2:
                words.add(stem)
    return words


def _drop_noise(phrases: List[str], noise: set, name: str) -> List[str]:
    nn = _norm(name)
    core = re.sub(r"(갈비|식당|맛집|횟집|한식|고기집|카페|본점|[0-9]+호점)", "", nn)          # 상호의 고유 부분 ('효정명인갈비' → '효정명인')
    chunks = [core[i:i + 2] for i in range(0, max(len(core) - 1, 0), 2)] if len(core) >= 3 else []
    out = []
    for p in phrases:
        toks = p.split()
        if any(t in noise for t in toks) or (nn and (nn in _norm(p) or _norm(p) in nn)) or any(c in _norm(p) for c in chunks):
            continue
        if p not in out:
            out.append(p)
    return out


def merge_research(local: List[Dict[str, Any]], blog_snippets: List[str], grounded: Dict[str, Any], name: str = "") -> Dict[str, Any]:
    """세 출처를 합쳐 {address, road_address, category, confirmed, unconfirmed, sources, steps}. 지어내기 방지: 근거 없는 항목은 unconfirmed"""
    blog_text = "\n".join(blog_snippets or [])
    blog_norm = _norm(blog_text)
    confirmed = {"special_menus": [], "review_nouns": [], "location_context": ""}
    unconfirmed = {"special_menus": [], "review_nouns": [], "location_context": ""}

    # 공식 지역 검색: 상호가 같은 첫 결과의 주소·업종
    pick = next((it for it in (local or []) if _norm(it.get("name", "")) == _norm(name)), (local or [None])[0])
    address = (pick or {}).get("road_address") or (pick or {}).get("address") or ""

    # 블로그 요약문에서 결정적으로 뽑은 명사 (형용사 삭제 + 2회 이상 반복) — 글에 실제로 있는 말이라 바로 '확인됨'
    noise = _noise_words(name, "", address)
    for m in _drop_noise(extract_special_menus(blog_text), noise, name):
        if m not in confirmed["special_menus"]:
            confirmed["special_menus"].append(m)
    for n in _drop_noise(extract_review_nouns(blog_text), noise, name):
        if n not in confirmed["review_nouns"]:
            confirmed["review_nouns"].append(n)

    # Gemini 그라운딩: 검색 근거 링크가 있거나(진짜 검색), 블로그 요약문에도 나오면 확인됨. 그 외는 미확인
    is_grounded = bool(grounded.get("grounded"))
    for key, field in (("special_menus", "name"), ("review_nouns", "noun")):
        for it in grounded.get(key) or []:
            val = it.get(field, "")
            if not it.get("source"):
                continue                                  # 출처 없는 항목은 어떤 경로로 와도 버림
            also_in_blog = bool(val) and _norm(val) in blog_norm
            bucket = confirmed if (is_grounded or also_in_blog) else unconfirmed
            if val and val not in confirmed[key] and val not in bucket[key]:
                bucket[key].append(val)
    # 상권: 주소 기반 표(결정적)는 확인됨, Gemini가 말한 주변 특징은 근거 링크가 있을 때만
    region_ctx = location_context(address or grounded.get("address", ""))
    confirmed["location_context"] = region_ctx
    g_ctx = grounded.get("location_context", "")
    if g_ctx and g_ctx != region_ctx:
        (confirmed if is_grounded and not region_ctx else unconfirmed)["location_context"] = g_ctx

    g_addr = grounded.get("address", "")
    return {
        "address": address, "road_address": (pick or {}).get("road_address", ""), "category": (pick or {}).get("category", ""),
        "business_type": (pick or {}).get("business_type", ""), "address_from": "naver" if address else ("gemini" if g_addr else ""),
        "address_candidate": g_addr if not address else "",
        "confirmed": confirmed, "unconfirmed": unconfirmed,
        "sources": [s for s in dict.fromkeys(grounded.get("sources") or []) if s],
        "steps": {"naver_local": bool(local), "naver_blog": len(blog_snippets or []), "gemini": bool(grounded.get("special_menus") or grounded.get("review_nouns")),
                  "grounded": is_grounded},
        "error": grounded.get("error", ""),
    }


def research_store(name: str, address: str = "", *, region: str = "", gemini_api_key: str = "", naver_id: str = "", naver_secret: str = "",
                   local_fn: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
                   blog_fn: Optional[Callable[[str], List[str]]] = None,
                   gemini_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    상호명(+선택 주소·지역)으로 공식 API 2종과 Gemini 웹 검색을 병렬 호출해 합친다. 각 단계는 실패해도 예외 없이 건너뛴다 (테스트용 *_fn 주입 가능).
    """
    import os
    name = (name or "").strip()
    region = (region or "").strip()
    if not name or os.getenv("STORE_RESEARCH_DISABLE", "").strip() == "1":       # 테스트·오프라인: 외부 호출 없이 빈 결과
        return merge_research([], [], {}, name)
    if local_fn is None and naver_id and naver_secret:
        from core.naver_local import NaverAPIError, NaverLocalSearch
        nl = NaverLocalSearch(naver_id, naver_secret)

        def local_fn(q: str) -> List[Dict[str, Any]]:
            try:
                return nl.search(q)
            except NaverAPIError:
                return []
    rtokens = [t for t in {re.sub(r"(특별시|광역시|특별자치시|특별자치도|도|시|군|구|면|읍|동)$", "", w) for w in re.findall(r"[가-힣]{2,}", f"{region} {address}")}
               if len(t) >= 2]
    if blog_fn is None:
        blog_fn = (lambda q: naver_blog_snippets(q, naver_id, naver_secret, region_tokens=rtokens)) if (naver_id and naver_secret) else (lambda q: [])
    if gemini_fn is None:
        gemini_fn = (lambda q, r: gemini_grounded(q, r, gemini_api_key)) if gemini_api_key else (lambda q, r: {})
    local_fn = local_fn or (lambda q: [])

    def safe(fn, *args, default):
        try:
            return fn(*args)
        except Exception as e:
            logger.warning(f"매장 정보 수집 단계 실패 ({getattr(fn, '__name__', 'fn')}): {type(e).__name__}")
            return default

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_local = ex.submit(safe, local_fn, f"{region} {name}".strip() if region and region not in name else name, default=[])
        f_blog = ex.submit(safe, blog_fn, name, default=[])
        f_gem = ex.submit(safe, gemini_fn, name, region, default={})
        local, blog, grounded = f_local.result(), f_blog.result(), f_gem.result() or {}
    return merge_research(local or [], blog or [], grounded, name)
