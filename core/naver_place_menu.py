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
                          gemini_fn: Optional[Callable[[str, str], List[str]]] = None) -> Dict[str, Any]:
    """
    상호명(+지역) → {"names": [공식 메뉴 이름...], "menus": [...], "keywords": [...], "place_id","url","place_name","source": "naver_place"|"gemini"|"", "error"}
    names에는 메뉴판 메뉴 + 사장님이 등록한 대표 키워드(예: 눈꽃맥주)를 중복 없이 담는다.
    """
    out: Dict[str, Any] = {"names": [], "menus": [], "keywords": [], "place_id": "", "url": "", "place_name": "", "source": "", "error": ""}
    name, region = (name or "").strip(), (region or "").strip()
    if not name or os.getenv("STORE_RESEARCH_DISABLE", "").strip() == "1":
        return out
    get = get or _http_get
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
