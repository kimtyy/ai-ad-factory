"""
core/planners/shop.py - 네이버 쇼핑(쇼핑커넥트·스마트스토어) 제휴 쇼츠: '1컨셉 문제해결형 18초 5문장' 대본

스펙(재질·무게·크기)을 나열하는 홈쇼핑 설명서 대본은 쓰지 않는다. 5문장 공식 (문장 순서 고정):
  1 (0~3초)   Hook      생활 속 불편·빡침을 저격하는 질문
  2 (3~7초)   공감      기존 방식의 한계
  3 (7~11초)  해결책    이 상품의 '단 하나의 킬러 기능' (상품명·카테고리·후기 요약에 있는 말만)
  4 (11~15초) 체감가성비 최저가(lprice)를 그대로 말하지 않고 '한 번/한 달 환산' 조건문으로 + 후기 증명
  5 (15~18초) CTA       정확히 SHOP_CTA (고정 댓글의 네이버 최저가 링크로 유도)
사진은 core/naver_shop.curate_photo_items 순서(실사용 → 작동 디테일 → 공간 연출 → 구성품, 마지막은 공식 대표 썸네일)를 그대로 5문장에 묶는다.
지어내기 방지: 가격은 환산 문구 하나만, 수치·효능·인증은 상품명·후기 요약에 있는 것만. 어긋나면 팩트 기반 템플릿 문장으로 교체한다.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from core.planners.owner import _scene
from core.render.commerce_layout import COMMERCE_CAPTION_CENTER_Y, SHOT_SEQUENCE

logger = logging.getLogger("ad_factory.planners.shop")

SHOP_CTA = "품절되기 전에 고정 댓글 네이버 최저가 링크부터 확인해 보세요!"
SCENE_SECONDS = [3.0, 4.0, 4.0, 4.0, 3.0]            # 0~3 / 3~7 / 7~11 / 11~15 / 15~18초
ROLES = ["hook", "empathy", "solution", "proof", "cta"]
ROLE_LABELS_KO = {"hook": "🔥 후킹", "empathy": "🙋 공감", "solution": "💡 해결책", "proof": "🧾 체감 가성비", "cta": "📍 CTA"}
MAX_LEN = 46
SHOP_END_CARD_TEXT = "네이버 최저가 링크는 [고정 댓글] 확인!"

SHOP_SYSTEM = """당신은 네이버 쇼핑 제휴 쇼츠 18초 광고 카피라이터입니다. 규칙:
- 5문장이 모두 [생활 속 불편 → 이 상품의 단 하나의 킬러 기능] 1컨셉 문제해결형으로 이어집니다.
- 스펙(재질·무게·크기·용량·소비전력)을 나열하는 홈쇼핑 설명서 대본은 절대 금지입니다.
- 입력된 상품명·카테고리·후기 요약에 없는 기능·수치·효능·인증·후기·순위는 만들지 않습니다. 숫자는 입력에 있는 것만 씁니다.
- 최저가를 그대로 말하지 말고, 입력된 '체감 가격 환산 문구'를 문장 4에 그대로 씁니다.
- 해요체, 문장당 공백 포함 46자 이내, 이모티콘·웃음 표기 금지. JSON만 출력합니다."""

# 스펙 나열(홈쇼핑 설명서) 판별: 단위가 붙은 수치, 재질·무게·크기 같은 스펙 단어
_SPEC_UNIT = re.compile(r"\d[\d.,]*\s*(?:g|kg|mg|cm|mm|m|ml|l|리터|w|와트|v|볼트|mah|인치|cc|gb|mb|rpm|db|㎝|㎏)\b", re.I)
_SPEC_WORDS = ("재질", "무게", "크기", "사이즈", "용량", "소비전력", "스펙", "규격", "원산지", "소재는", "전압")
_PRICE_WON = re.compile(r"\d[\d,]*\s*원")


def _josa(word: str, a: str, b: str) -> str:
    ch = (word or " ")[-1]
    return a if ("가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28) else b


def short_name(name: str, keyword: str = "") -> str:
    """대본에서 부를 짧은 이름: 사용자가 검색한 키워드가 있으면 그것, 없으면 상품명에서 브랜드·모델번호를 뺀 앞 2~3어절"""
    kw = (keyword or "").strip()
    if kw and not kw.lower().startswith("http"):
        return kw[:16]
    words = [w for w in re.sub(r"[\[\(\{].*?[\]\)\}]", " ", name or "").split() if not re.search(r"\d", w) or len(w) <= 3]
    return " ".join(words[:3])[:16] or "이 제품"


def find_spec_listing(text: str) -> Optional[str]:
    """스펙 나열 표현: 단위가 붙은 수치 또는 재질·무게·크기 같은 스펙 단어가 2개 이상"""
    t = text or ""
    units = _SPEC_UNIT.findall(t)
    words = [w for w in _SPEC_WORDS if w in t]
    if len(units) + len(words) >= 2:
        return ", ".join(units + words)[:30]
    return None


def evidence_text(ctx: Dict[str, Any]) -> str:
    parts = [ctx.get("name", ""), ctx.get("brand", ""), ctx.get("maker", ""), ctx.get("category", ""), ctx.get("usp", ""),
             " ".join(ctx.get("review_nouns") or []), " ".join(ctx.get("snippets") or []), ctx.get("short", "")]
    return " ".join(p for p in parts if p)


def template_lines(ctx: Dict[str, Any]) -> List[str]:
    """팩트(상품명·후기 명사·환산 문구)만으로 만든 5문장 — LLM 실패·검증 탈락 시 대체. 없는 팩트는 일반 표현으로"""
    short = ctx.get("short") or "이 제품"
    nouns = ctx.get("review_nouns") or []
    s1 = f"{short}, 아직도 예전 방식 그대로 쓰세요?"
    s2 = "매번 같은 불편 참고 쓰다 보면 시간만 날리잖아요."
    if nouns:
        s3 = f"이건 후기에서도 {nouns[0]}{_josa(nouns[0], '이', '가')} 자주 나올 만큼 그 불편을 줄이는 쪽에 집중했어요."
    else:
        s3 = f"이건 {short}{_josa(short, '을', '를')} 쓸 때 겪던 그 불편을 줄이는 쪽에 집중한 제품이에요."
    s4 = ctx.get("value_phrase") or "후기에서 자주 보이는 이유가 있어요."
    return [s1, s2, s3, s4, SHOP_CTA]


def validate_line(i: int, text: str, ctx: Dict[str, Any]) -> str:
    """문장 i(0~3)가 통과하면 '' , 아니면 탈락 이유. 스펙 나열·근거 없는 주장·최저가 노출·홍보 어투·판에 박힌 패턴을 막는다"""
    from core.script_guard import find_banned_style, find_promo_tone, find_unsupported_claim, find_unsupported_spec
    t = (text or "").strip()
    if not t or len(t) > MAX_LEN:
        return "길이"
    ev = evidence_text(ctx)
    if find_spec_listing(t):
        return f"스펙 나열 ({find_spec_listing(t)})"
    if i == 3:
        # 문장 4: 환산 문구의 '약 N원꼴'과 조건문('쓴다고 치면')이 있어야 하고, 그 외 가격 숫자는 금지 (최저가 원문 노출 방지)
        per = f"{int(ctx.get('per_unit_won') or 0):,}원꼴"
        flat = t.replace(" ", "")
        if ctx.get("per_unit_won") and (per not in flat and per.replace(",", "") not in flat.replace(",", "")):
            return "체감 가격 환산 문구 누락"
        if ctx.get("per_unit_won") and "쓴다고 치면" not in t:
            return "환산 조건문 누락"
        rest = _PRICE_WON.sub("", t.replace(per, "")) if ctx.get("per_unit_won") else t
        if _PRICE_WON.search(rest) or (ctx.get("lprice") and f"{int(ctx['lprice']):,}" in t) or (ctx.get("lprice") and str(int(ctx["lprice"])) in t):
            return "최저가 직접 노출"
    else:
        claim = find_unsupported_claim(t, usp=ev, check_amenities=False)
        if claim:
            return f"근거 없는 주장 ({claim})"
    spec = find_unsupported_spec(t, ev)
    if spec and i != 3:
        return f"근거 없는 수치·효능 ({spec})"
    promo = find_promo_tone(t)
    if promo and i != 0:                                         # 후킹의 '~세요?' 질문은 허용
        return f"홍보 어투 ({promo})"
    banned = find_banned_style(t)
    if banned:
        return f"판에 박힌 문구 ({banned})"
    return ""


def shop_groups(n_photos: int) -> List[List[int]]:
    """
    사진 번호(0부터, 마지막 번호 = 공식 대표 썸네일)를 5문장에 묶음. 마지막 문장(CTA)은 항상 공식 썸네일 1장.
    실사용 계열 m = n-1장: 4장이면 문장 1~4에 1장씩, 5~7장이면 앞 문장부터 2컷으로 이어 붙임. 4장 미만이면 돌려 씀 (앵글은 호출 쪽에서 바꿈)
    """
    if n_photos <= 0:
        return []
    if n_photos == 1:
        return [[0]] * 5
    m = n_photos - 1
    last = [n_photos - 1]
    if m < 4:
        return [[k % m] for k in range(4)] + [last]
    sizes = {4: [1, 1, 1, 1], 5: [1, 2, 1, 1], 6: [1, 2, 2, 1]}.get(m, [2, 2, 2, 1])
    out, k = [], 0
    for sz in sizes:
        out.append(list(range(k, k + sz)))
        k += sz
    return out + [last]


def build_context(product: Dict[str, Any], pack: Optional[Dict[str, Any]], usp: str = "", value: Optional[Dict[str, Any]] = None,
                  keyword: str = "") -> Dict[str, Any]:
    pack = pack or {}
    rv = pack.get("reviews") or {}
    keyword = keyword or pack.get("keyword") or ""
    return {"name": product.get("name", ""), "brand": product.get("brand", ""), "maker": product.get("maker", ""),
            "category": product.get("category", ""), "usp": usp or "", "lprice": int(product.get("lprice") or 0),
            "short": short_name(product.get("name", ""), keyword),
            "review_nouns": list(rv.get("nouns") or []), "snippets": list(rv.get("snippets") or []),
            "review_count": int(rv.get("count") or 0),
            "value_phrase": (value or {}).get("phrase", ""), "per_unit_won": int((value or {}).get("per_unit_won") or 0)}


def plan_shop_storyboard(product: Dict[str, Any], pack: Optional[Dict[str, Any]] = None, media_paths: Optional[List[str]] = None,
                         media_roles: Optional[List[str]] = None, usp: str = "", value: Optional[Dict[str, Any]] = None,
                         api_key: Optional[str] = None, mock: bool = False, output_plan_path: Optional[str] = None,
                         keyword: str = "", core_concept: str = "", thumb_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    쇼핑 18초 5문장 콘티. media_paths는 curate 순서(실사용 컷들 → 마지막 공식 썸네일), media_roles는 같은 순서의 컷 역할('usage'…'official').
    value는 core.naver_shop.value_assumption 결과 (문장 4의 체감 가격 환산 근거)
    """
    if not (product.get("name") or "").strip():
        raise ValueError("상품을 먼저 골라 주세요. 검색하거나 인기 꿀템 카드를 눌러 상품을 선택해야 기획할 수 있어요.")
    ctx = build_context(product, pack, usp, value, keyword)
    media = list(media_paths or [])[:8]
    groups = shop_groups(len(media))
    lines = template_lines(ctx)
    engine, hook_llm = "template", ""

    from core.llm import script_llm_ready
    if not mock and script_llm_ready(api_key):
        try:
            from core.llm import generate_json
            prompt = f"""[상품 팩트 — 이 안에 있는 것만 말하세요]
- 상품명(판매 페이지 표기): {ctx['name']} / 영상에서 부를 짧은 이름: {ctx['short']}
- 카테고리: {ctx['category'] or '(모름)'} / 브랜드: {ctx['brand'] or '(모름)'}
- 블로그 후기 {ctx['review_count']}건에서 자주 나온 말: {', '.join(ctx['review_nouns']) or '(없음 — 후기 내용을 단정하지 마세요)'}
- 직접 적은 특장점: {ctx['usp'] or '(없음)'}
- 체감 가격 환산 문구(문장 4에 그대로, 정확히 이 표현): "{ctx['value_phrase'] or '(환산 불가 — 가격 숫자를 쓰지 마세요)'}"
- 이번 영상의 1컨셉: {core_concept or '생활 속 불편을 해결하는 단 하나의 킬러 기능'}

[5문장 공식 — 문장 순서 고정, 홈쇼핑 스펙 설명서 금지]
1(0~3초) Hook: 생활 속 불편·빡침 저격 질문. 예: "배수구 머리카락 아직도 손으로 건지세요?" / "캠핑장 얼음 다 녹아서 미지근한 맥주 마셨다면 주목!"
2(3~7초) 공감: 기존 방식의 한계. 예: "매번 고무장갑 끼고 닦아도 냄새 올라오고 돈만 날리잖아요."
3(7~11초) 해결책: 이 상품의 '단 하나의 킬러 기능'. 상품명·카테고리·후기에 있는 기능만. 숫자·시간("3초 만에" 등)은 입력에 없으면 쓰지 마세요.
4(11~15초) 체감 가성비: 위 환산 문구를 그대로 쓰고, 후기에서 자주 나온 말이 있으면 한마디 덧붙이세요 (최저가 숫자는 절대 쓰지 말 것).
5(15~18초) CTA: 정확히 이 문장: "{SHOP_CTA}"
JSON만 출력: {{"scenes": [{{"scene_index": 1, "hook_title": "8~12자 헤드라인", "voiceover": "..."}}, ... 5개]}}"""
            data, eng = generate_json([prompt], SHOP_SYSTEM, temperature=0.6, gemini_api_key=api_key, purpose="쇼핑 18초 대본")
            sc = (data.get("scenes") if isinstance(data, dict) else data) or []
            got = [str((s or {}).get("voiceover") or "").strip() for s in sc if isinstance(s, dict)]
            for i in range(4):
                if i < len(got):
                    why = validate_line(i, got[i], ctx)
                    if why:
                        logger.info(f"[쇼핑 대본 검증 탈락] 문장 {i + 1}: {why} ('{got[i]}') → 팩트 템플릿 문장")
                    else:
                        lines[i] = got[i]
            hook_llm = str(((sc[0] if sc else {}) or {}).get("hook_title") or "").strip() if sc else ""
            engine = eng
        except Exception as e:
            logger.warning(f"쇼핑 대본 엔진 실패, 팩트 템플릿으로 대체: {e}")
            hook_llm = ""
    for i in range(4):                                       # 템플릿 문장도 같은 검증을 통과해야 함 (환산 문구 없음 등 예외 방어)
        if validate_line(i, lines[i], ctx):
            lines[i] = template_lines(ctx)[i]
    from core.script_guard import find_banned_style, find_unsupported_claim
    hook = hook_llm if (hook_llm and len(hook_llm) <= 14 and not find_banned_style(hook_llm)
                        and not find_unsupported_claim(hook_llm, usp=evidence_text(ctx), check_amenities=False)) else f"{ctx['short']} 아직도?"[:14]

    uses: Dict[int, int] = {}
    scenes = []
    for i in range(5):
        sc = _scene(i, ROLES[i], lines[i], hook if i == 0 else "")
        sc["duration_sec"] = SCENE_SECONDS[i]
        if groups:
            sc["media_group"] = list(groups[i])
            first = groups[i][0]
            n = uses.get(first, 0)
            uses[first] = n + 1
            sc["shot"] = SHOT_SEQUENCE[n % len(SHOT_SEQUENCE)] if n else "full"     # 같은 사진이 또 나오면 앵글을 바꿈
        sc["layout"] = "card" if (i == 4 and groups) else "full"                  # 실사용 컷은 전체 화면, 마지막 공식 썸네일(흰 배경 누끼)은 상품 카드
        if i == 4:
            sc["caption_center_y"] = COMMERCE_CAPTION_CENTER_Y                     # 카드 위쪽 자막
        scenes.append(sc)
    scenes[0]["script_engine"] = engine
    scenes[0]["core_concept"] = (core_concept or f"{ctx['short']}: 생활 속 불편을 해결하는 단 하나의 킬러 기능").strip()
    scenes[0]["shop_auto"] = True
    scenes[0]["shop"] = {
        "product": ctx["name"], "short": ctx["short"], "lprice": ctx["lprice"], "mall": product.get("mall", ""),
        "value": ctx["value_phrase"], "review_nouns": ctx["review_nouns"], "review_count": ctx["review_count"],
        "cut_roles": list(media_roles or []), "photos": len(media),
        "warn": ("실사용 사진이 부족해 같은 사진을 다른 앵글로 재사용해요. 직접 찍은 사진을 추가하면 더 좋아요." if len(media) < 5 else ""),
    }
    if output_plan_path:
        with open(output_plan_path, "w", encoding="utf-8") as f:
            json.dump(scenes, f, ensure_ascii=False, indent=2)
    logger.info(f"쇼핑 18초 콘티: 사진 {len(media)}장 → 5문장, 엔진={engine}, 환산={ctx['value_phrase'] or '없음'}")
    return scenes
