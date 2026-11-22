"""
core/naver_place_menu.py - 네이버 플레이스 '공식 메뉴판' 수집 (m.place.naver.com/<업종>/<place_id>/menu/list)

상호명 → 네이버 검색 결과에서 플레이스 ID를 찾고 → 메뉴 페이지에 실려 있는 메뉴 데이터(이름·가격·설명·대표 여부)와 사장님이 등록한 대표 키워드를 읽는다.
플레이스에서 못 찾으면 Gemini 웹 검색으로 '<상호> 네이버 플레이스 메뉴판' 목록을 시도한다 (출처 없는 항목은 버림).
수집한 메뉴 이름은 (1) 사진 분석 때 '이 사진에 찍힌 공식 메뉴'를 판별하는 기준, (2) 사진에 없는 메뉴를 대본에서 막는 기준으로 쓴다.
모든 외부 호출은 실패해도 예외 없이 빈 결과로 돌아온다 (get 주입으로 오프라인 테스트).
"""
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
      "Version/16.0 Mobile/15E148 Safari/604.1")
SEARCH_URL = "https://m.search.naver.com/search.naver"
MENU_URL = "https://m.place.naver.com/{cat}/{pid}/menu/list"
_PLACE_LINK = re.compile(r"place\.naver\.com/(restaurant|cafe|place|bar|hairshop|beauty|accommodation)/(\d{6,})")
_APOLLO = re.compile(r"__APOLLO_STATE__\s*=\s*(\{.*?\});\s*\n", re.S)


def _http_get(url: str, params: Optional[Dict[str, str]] = None, timeout: int = 8) -> str:
    import requests
    r = requests.get(url, params=params, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}, timeout=timeout)
    r.raise_for_status()
    return r.content.decode("utf-8", errors="replace")


def normalize_menu_name(name: str) -> str:
    """'골뱅이 무침(기본)' → '골뱅이 무침' (괄호 옵션 제거, 공백 정리)"""
    t = re.sub(r"[\(\[（].*?[\)\]）]", " ", name or "")
    return re.sub(r"\s+", " ", t).strip(" -·,/")


def find_place_ids(query: str, get: Optional[Callable[..., str]] = None, limit: int = 4) -> List[Tuple[str, str]]:
    """검색 결과 문서에 처음 나오는 순서대로 (카테고리, place_id)"""
    get = get or _http_get
    html = get(SEARCH_URL, params={"query": query})
    seen, out = set(), []
    for cat, pid in _PLACE_LINK.findall(html or ""):
        if pid not in seen:
            seen.add(pid)
            out.append((cat, pid))
        if len(out) >= limit:
            break
    return out


def parse_menu_page(html: str) -> Dict[str, Any]:
    """메뉴 페이지 HTML → {"place_name","menus":[{name,price,desc,group,rep}],"keywords":[...]} (데이터가 없으면 빈 값)"""
    out: Dict[str, Any] = {"place_name": "", "menus": [], "keywords": []}
    m = _APOLLO.search(html or "")
    state: Dict[str, Any] = {}
    if m:
        try:
            state = json.loads(m.group(1))
        except ValueError:
            state = {}
    groups: Dict[str, Dict[str, Any]] = {}
    items: Dict[str, Dict[str, Any]] = {}
    for key, val in state.items():
        if not isinstance(val, dict):
            continue
        tname = str(val.get("__typename") or key.split(":")[0])
        if "Menu" in tname and "Group" in tname:
            groups[key] = val
        elif "Menu" in tname and val.get("name"):
            items[key] = val
        elif tname.startswith("PlaceDetailBase") and not out["place_name"]:
            out["place_name"] = str(val.get("name") or "")
    group_of: Dict[str, str] = {}
    group_order: Dict[str, int] = {}
    for g in sorted(groups.values(), key=lambda x: x.get("order") or 0):
        for ref in g.get("menus") or []:
            rk = ref.get("__ref") if isinstance(ref, dict) else None
            if rk and rk not in group_of:
                group_of[rk] = str(g.get("name") or "")
                group_order[rk] = int(g.get("order") or 0)
    seen = set()
    for key, it in sorted(items.items(), key=lambda kv: (group_order.get(kv[0], 99), kv[1].get("order") or 0)):
        name = normalize_menu_name(str(it.get("name") or ""))
        if len(name) < 2 or name in seen:
            continue
        seen.add(name)
        price = re.sub(r"[^\d]", "", str(it.get("price") or ""))
        out["menus"].append({"name": name, "price": int(price) if price else 0, "desc": re.sub(r"\s+", " ", str(it.get("desc") or "")).strip()[:120],
                             "group": group_of.get(key, ""), "rep": bool(it.get("recommend") or it.get("isRecommended") or it.get("isRepresentative")
                                                                         or group_of.get(key, "") in ("대표메뉴", "대표 메뉴", "SIGNATURE", "시그니처"))})
    km = re.search(r'"keywordList":\[(.*?)\]', html or "")
    if km:
        try:
            out["keywords"] = [normalize_menu_name(x) for x in json.loads("[" + km.group(1) + "]") if isinstance(x, str) and len(x.strip()) >= 2]
        except ValueError:
            pass
    return out


def _gemini_menu(name: str, region: str, api_key: str) -> List[str]:
    """Gemini 웹 검색 그라운딩으로 '<상호> 네이버 플레이스 메뉴판 전체 목록' (출처가 있는 항목만)"""
    if not (api_key and name):
        return []
    try:
        from google import genai
        from google.genai import types
        from core.guardrail import validate_model_call
        from core.store_research import GROUNDING_MODEL, _parse_json_text
        client = genai.Client(api_key=api_key)
        prompt = (f'Google Search로 "{name} {region} 네이버 플레이스 메뉴판 전체 목록"을 찾아, 실제로 확인된 메뉴 이름만 모두 나열하라. '
                  f'확인되지 않는 메뉴는 절대 적지 마라. JSON만 출력: {{"menus": [{{"name": "...", "source": "..."}}]}}')
        res = client.models.generate_content(model=validate_model_call(GROUNDING_MODEL, category="llm"), contents=prompt,
                                             config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.1))
        data = _parse_json_text(res.text or "")
        names = []
        for it in data.get("menus") or []:
            if isinstance(it, dict) and it.get("source"):
                n = normalize_menu_name(str(it.get("name") or ""))
                if 2 <= len(n) <= 24 and n not in names:
                    names.append(n)
        return names
    except Exception as e:
        logger.warning(f"Gemini 메뉴판 검색 실패: {type(e).__name__}: {str(e)[:120]}")
        return []


def collect_official_menu(name: str, region: str = "", *, get: Optional[Callable[..., str]] = None, api_key: str = "",
                          gemini_fn: Optional[Callable[[str, str], List[str]]] = None, place_id: str = "") -> Dict[str, Any]:
    """
    상호명(+지역) → {"names": [공식 메뉴 이름...], "menus": [...], "keywords": [...], "place_id","url","place_name","source": "naver_place"|"gemini"|"", "error"}
    names에는 메뉴판 메뉴 + 사장님이 등록한 대표 키워드(예: 눈꽃맥주)를 중복 없이 담는다.
    place_id를 알면(랜딩페이지에서 고른 지점) 검색 없이 그 매장의 공식 메뉴판만 읽는다 — 같은 이름의 다른 지점이 섞이지 않는다.
    """
    out: Dict[str, Any] = {"names": [], "menus": [], "keywords": [], "place_id": "", "url": "", "place_name": "", "source": "", "error": ""}
    name, region = (name or "").strip(), (region or "").strip()
    if os.getenv("STORE_RESEARCH_DISABLE", "").strip() == "1" or not (name or place_id):
        return out
    get = get or _http_get
    if place_id:
        bundle = fetch_place_bundle(place_id, get=get)
        if bundle["source"]:
            return bundle
        out["error"] = bundle.get("error", "")
        if not name:
            return out
    first = re.sub(r"[^가-힣A-Za-z0-9]", "", name.split()[0]) if name.split() else name
    best: Optional[Dict[str, Any]] = None
    try:
        for cat, pid in find_place_ids(f"{region} {name}".strip() if region and region not in name else name, get=get):
            url = MENU_URL.format(cat=cat, pid=pid)
            try:
                page = parse_menu_page(get(url))
            except Exception as e:
                out["error"] = f"{type(e).__name__}"
                continue
            if not (page["menus"] or page["keywords"]):
                continue
            page.update(place_id=pid, url=url)
            if first and first in re.sub(r"\s", "", page.get("place_name") or ""):
                best = page
                break
            best = best or page
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:80]}"
    if best:
        out.update(menus=best["menus"], keywords=best["keywords"], place_id=best["place_id"], url=best["url"], place_name=best.get("place_name", ""), source="naver_place")
        names = [m["name"] for m in best["menus"]]
        for k in best["keywords"]:
            if k not in names:
                names.append(k)
        out["names"] = names
    else:
        gnames = (gemini_fn or (lambda n, r: _gemini_menu(n, r, api_key)))(name, region)
        if gnames:
            out.update(names=gnames, menus=[{"name": n, "price": 0, "desc": "", "group": "", "rep": False} for n in gnames], source="gemini")
    return out


def menu_text_lines(result: Dict[str, Any]) -> str:
    """화면 편집칸에 보여줄 텍스트: 한 줄에 메뉴 하나 ('이름 · 9,000원'), 대표 메뉴는 ⭐"""
    lines, shown = [], set()
    for m in result.get("menus") or []:
        price = f" · {m['price']:,}원" if m.get("price") else ""
        lines.append(f"{'⭐ ' if m.get('rep') else ''}{m['name']}{price}")
        shown.add(m["name"])
    for k in result.get("keywords") or []:
        if k not in shown:
            lines.append(f"⭐ {k}")
            shown.add(k)
    for n in result.get("names") or []:
        if n not in shown:
            lines.append(n)
            shown.add(n)
    return "\n".join(lines)


def parse_menu_text(text: str) -> List[str]:
    """편집칸 텍스트 → 메뉴 이름 목록 (⭐·가격·기호 제거, 중복 제거)"""
    out: List[str] = []
    for ln in (text or "").splitlines():
        t = re.sub(r"^[\s⭐★\-•*]+", "", ln)
        t = re.split(r"\s*[·|:]\s*|\s+\d[\d,]*\s*원", t)[0]
        t = normalize_menu_name(t)
        if 2 <= len(t) <= 24 and t not in out:
            out.append(t)
    return out


# ---------------------------------------------------------------- place_id 기반 공식정보 (랜딩페이지 site/server/naver.js 와 같은 규칙)
HOME_URL = "https://m.place.naver.com/place/{pid}/home"
GENERIC_MENU_URL = "https://m.place.naver.com/place/{pid}/menu/list"
PHOTO_PAGE_URL = "https://m.place.naver.com/place/{pid}/photo"            # 업종(restaurant 등)을 몰라도 네이버가 알맞은 주소로 넘겨준다
_PLACE_ID = re.compile(r"^\d{6,15}$")
_REGION_SUFFIX = re.compile(r"(특별시|광역시|특별자치시|특별자치도|도|시|군|구|면|읍|동|리)$")
_PROVINCES = {"경기", "서울", "인천", "부산", "대구", "광주", "대전", "울산", "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"}


def _apollo_state(html: str) -> Dict[str, Any]:
    m = _APOLLO.search(html or "")
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _clean_text(s: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(s or ""))).strip()


def region_tokens(address: str) -> List[str]:
    """주소 → 지역 핵심어 ('경기 가평군 조종면 …' → ['가평', '조종', ...]). 광역 이름(경기·서울…)은 변별력이 없어 뺀다"""
    out: List[str] = []
    for w in re.findall(r"[가-힣]{2,}", address or ""):
        t = _REGION_SUFFIX.sub("", w)
        if len(t) >= 2 and t not in out and t not in _PROVINCES:
            out.append(t)
    return out[:6]


def address_score(candidate_addr: str, want_addr: str) -> float:
    want = region_tokens(want_addr)
    if not want:
        return 0.0
    return sum(1 for t in want if t in (candidate_addr or "")) / len(want)


def _name_key(s: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", s or "").lower()


def name_score(a: str, b: str) -> float:
    x, y = _name_key(a), _name_key(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    if x in y or y in x:
        return 0.8
    return 0.4 if x[:2] and x[:2] in y else 0.0


def parse_place_home(html: str, expect_id: str = "") -> Optional[Dict[str, Any]]:
    """플레이스 홈 페이지 → 매장 기본정보·대표 키워드·좌석·편의시설·방문자가 고른 키워드·이 매장에 연결된 블로그 후기·사장님 공식 사진. 못 읽으면 None"""
    state = _apollo_state(html)
    if not state:
        return None
    base_key = next((k for k in state if k.startswith("PlaceDetailBase:") and (not expect_id or k == f"PlaceDetailBase:{expect_id}")), None)
    base = state.get(base_key) if base_key else None
    if not isinstance(base, dict):
        return None
    keywords: List[str] = []
    km = re.search(r'"keywordList":\[(.*?)\]', html or "")
    if km:
        try:
            keywords = [x.strip() for x in json.loads("[" + km.group(1) + "]") if isinstance(x, str) and len(x.strip()) >= 2][:8]
        except ValueError:
            keywords = []
    seats: List[str] = []
    reviews: List[Dict[str, str]] = []
    voted: List[Dict[str, Any]] = []
    photos: List[Dict[str, Any]] = []
    for key, val in state.items():
        if not isinstance(val, dict):
            continue
        tname = str(val.get("__typename") or key.split(":")[0])
        if tname == "RestaurantSeatItems" and val.get("name"):
            seats.append(_clean_text(val.get("name")) + (f" {_clean_text(val.get('description'))}" if val.get("description") else ""))
        elif tname == "FsasReview" and val.get("type") == "blog" and val.get("title"):
            reviews.append({"title": _clean_text(val.get("title")), "contents": _clean_text(val.get("contents")), "url": str(val.get("url") or "")})
        elif tname == "VisitorReviewStatsResult":
            details = ((val.get("analysis") or {}).get("votedKeyword") or {}).get("details")
            if isinstance(details, list):
                voted = sorted(({"keyword": _clean_text(d.get("displayName")), "count": int(d.get("count") or 0)}
                                for d in details if isinstance(d, dict) and d.get("displayName") and d.get("count")), key=lambda d: -d["count"])[:5]
        elif tname == "PlaceDetailTopPhotoItem" and val.get("originalUrl") and val.get("mediaSource") == "business" and (val.get("mediaFormat") in (None, "image")):
            photos.append({"url": str(val["originalUrl"]), "width": int(val.get("width") or 0), "height": int(val.get("height") or 0)})
    conv = base.get("conveniences")
    return {
        "place_id": str(base.get("id") or expect_id or ""), "name": _clean_text(base.get("name")), "category": _clean_text(base.get("category")),
        "road_address": _clean_text(base.get("roadAddress")), "address": _clean_text(base.get("address")),
        "conveniences": [_clean_text(c) for c in conv if _clean_text(c)] if isinstance(conv, list) else [],
        "keywords": keywords, "seats": seats[:4], "voted": voted, "reviews": reviews[:5], "photos": photos[:24],
    }


def build_official_summary(home: Optional[Dict[str, Any]]) -> str:
    """플레이스에 실제로 적힌 항목만 이어 붙인 '공식 매장 특징' 한 덩어리 (없는 항목은 건너뜀 — 지어내지 않음)"""
    if not home:
        return ""
    parts: List[str] = []
    if home.get("category"):
        parts.append(home["category"].replace(",", "·"))
    if home.get("keywords"):
        parts.append("대표 키워드: " + ", ".join(home["keywords"][:5]))
    if home.get("seats"):
        parts.append("좌석: " + ", ".join(home["seats"]))
    if home.get("conveniences"):
        parts.append("편의시설: " + ", ".join(home["conveniences"][:6]))
    if home.get("voted"):
        parts.append("방문자가 꼽은 점: " + ", ".join(f"{v['keyword']}({v['count']})" for v in home["voted"][:3]))
    return " / ".join(parts)


def fetch_place_home(place_id: str, get: Optional[Callable[..., str]] = None) -> Optional[Dict[str, Any]]:
    if not _PLACE_ID.match(str(place_id or "")):
        return None
    try:
        return parse_place_home((get or _http_get)(HOME_URL.format(pid=place_id)), str(place_id))
    except Exception as e:
        logger.warning(f"플레이스 홈 수집 실패 {place_id}: {type(e).__name__}")
        return None


def resolve_place_id(name: str, address: str = "", get: Optional[Callable[..., str]] = None) -> str:
    """상호명+주소로 정확한 place_id: 검색 후보마다 플레이스 주소를 읽어 주소가 맞는 곳만 고른다 (같은 이름의 다른 지점이 섞이지 않게).
    주소를 모르면 검색 1순위, 주소가 맞는 곳이 없으면 '' (엉뚱한 지점보다 못 찾는 편이 낫다)"""
    name = (name or "").strip()
    if not name:
        return ""
    get = get or _http_get
    tokens = [t for t in region_tokens(address) if t not in name]
    query = " ".join([name] + tokens[:2])
    try:
        ids = [pid for _cat, pid in find_place_ids(query, get=get, limit=4)]
    except Exception as e:
        logger.warning(f"플레이스 검색 실패: {type(e).__name__}")
        return ""
    if not ids:
        return ""
    if not address:
        return ids[0]
    best, best_score = "", 0.0
    for pid in ids:
        home = fetch_place_home(pid, get=get)
        if not home:
            continue
        score = max(address_score(home["road_address"], address), address_score(home["address"], address)) * 2 + name_score(home["name"], name)
        if score > best_score:
            best, best_score = pid, score
    return best if best_score >= 1.2 else ""


def fetch_place_bundle(place_id: str, get: Optional[Callable[..., str]] = None) -> Dict[str, Any]:
    """place_id → 공식 메뉴판 + 홈 정보를 한 번에. collect_official_menu 와 같은 모양({"names","menus","keywords","place_id","url","place_name","source"})에
    "home"(홈 정보)·"official_summary"·"photos"를 더한다. 못 읽은 부분은 빈 값 (예외 없음)"""
    out: Dict[str, Any] = {"names": [], "menus": [], "keywords": [], "place_id": "", "url": "", "place_name": "", "source": "", "error": "",
                           "home": None, "official_summary": "", "photos": [], "road_address": "", "category": ""}
    pid = str(place_id or "")
    if not _PLACE_ID.match(pid):
        out["error"] = "place_id 형식 오류"
        return out
    get = get or _http_get
    home = fetch_place_home(pid, get=get)
    page: Dict[str, Any] = {"menus": [], "keywords": [], "place_name": ""}
    url = GENERIC_MENU_URL.format(pid=pid)
    try:
        page = parse_menu_page(get(url))
    except Exception as e:
        out["error"] = type(e).__name__
    menus = page["menus"]
    keywords = page["keywords"] or ((home or {}).get("keywords") or [])
    if not (menus or keywords or home):
        return out
    names = [m["name"] for m in menus]
    for k in keywords:
        if k not in names:
            names.append(k)
    out.update(menus=menus, keywords=keywords, names=names, place_id=pid, url=url, source="naver_place",
               place_name=(home or {}).get("name") or page.get("place_name", ""), home=home, official_summary=build_official_summary(home),
               photos=(home or {}).get("photos") or [], road_address=((home or {}).get("road_address") or (home or {}).get("address") or ""),
               category=(home or {}).get("category", ""))
    return out
