"""
core/planners/commerce_bundle.py - 쇼핑 '3단 묶음 큐레이션' 숏폼 플래너 (상품 3개를 30초 안에 치고 빠지기)

구조 (나레이션 기준, 실측 0.123초/자):
  씬 1        [0~3초]  문제 제기·호기심 훅 (헤드라인 + 짧은 한 줄)
  씬 2~7      [각 7초] 아이템 1·2·3 × 2문장 (첫 문장: 서수 + 언제 필요한지 / 둘째 문장: 핵심 셀링포인트)
  씬 8        [마지막 3초] 구매 좌표 댓글 유도 CTA
- 대본 엔진: Claude → GPT → Gemini 자동 백업 (core/llm), 어미 리듬(질문형·감탄형 최소 1개, 평서 연속 금지)과
  판에 박힌 패턴 금지는 단일 상품 모드와 같은 규칙으로 생성 직후 한 번 보정
- 허위 방지: 아이템마다 그 아이템의 판매 페이지 특징(USP)에 있는 사실만. 최저가·1위·품절 같은 주장 금지
- 사진: 아이템 k의 사진만 그 아이템 씬에 (첫 문장은 전체 풀샷, 둘째 문장은 중앙 줌인 앵글로 같은 사진 재사용 가능)
- 화면 고정 바: 상단 타이틀 바(title_bar) + 하단 구매 안내 바 (core/compositor.render_bundle_bars)
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from core.llm import script_llm_ready
from core.planners.commerce import TEMPLATE_LINES, _claim, _scene
from core.script_guard import (COPY_TONE_RULES, complete_sentence, diversify_exclamations, find_banned_style,
                               find_promo_tone, ending_kind, shorten_line, strip_brochure_words, to_haeyo)

logger = logging.getLogger("ad_factory.planners.commerce_bundle")

BUNDLE_SIZE = 3
LINES_PER_ITEM = 2
BUNDLE_CTA = "구매 좌표는 고정 댓글에 남겨 둘게요!"
DEFAULT_BOTTOM_BAR = "🛒 구매 좌표는 고정 댓글 확인!"
DEFAULT_HOOK = "이거 모르면 손해예요"
DEFAULT_HOOK_LINE = "이 세 가지면 충분하지 않을까요?"
HOOK_TITLE_MAX = 12
HOOK_LINE_RANGE = (10, 18)            # 헤드라인(약 1.5초) + 이 줄 = 약 3초
ITEM_LINE_RANGE = (22, 27)            # 2문장 × (약 3.1초 + 씬 전환 여유 0.55초) ≈ 아이템당 7초
TITLE_BAR_MAX = 20
ORDINALS = ("첫 번째", "두 번째", "마지막")
ITEM_SHOTS = ("full", "center")

# 템플릿 대사 (AI 실패·차단 시): 서수 + 사실 주장 없는 상황 문장 / 셀링포인트 자리는 단일 상품 템플릿 재사용
ITEM_OPENERS = ("{ord}, 매일 손이 가는 자리에 두기 좋은 템이에요.", "{ord}, 귀찮은 일을 확 줄여 주는 템이죠!",
                "{ord}, 이런 거 하나쯤 있으면 어때요?")
TEMPLATE_TITLE = "{theme} 꿀템 3가지"


def short_name(name: str, limit: int = 18) -> str:
    """쿠팡식 긴 상품명 → 화면·댓글용 짧은 이름 (옵션(쉼표 뒤)·괄호·모델 코드·수량 제거 후 앞 어절)"""
    t = re.sub(r"[\[\(【].*?[\]\)】]", " ", (name or "").split(",")[0])
    t = re.sub(r"\b[A-Z]{1,5}-?\d[\w-]*\b", " ", t)          # 모델 코드 (TSK-5318 등)
    words = [w for w in re.split(r"\s+", t.strip()) if w and not re.fullmatch(r"[\d,]+(개|매|入|ml|g|kg|L|cm|mm)?", w)]
    out = ""
    for w in words:
        if len(out) + len(w) + (1 if out else 0) > limit:
            break
        out = f"{out} {w}".strip()
    return out or (name or "").strip()[:limit]


def _item_media(items: List[Dict[str, Any]], media_paths: List[str]) -> List[List[str]]:
    """
    아이템별 사진: 아이템에 media(쿠팡 검색으로 추가한 대표 사진 등)가 있으면 그것.
    없는 아이템끼리는 나머지 업로드 사진을 순서대로 나눔 (아이템 2개가 비었으면 1번째·3번째… / 2번째·4번째…)
    """
    owned = {p for it in items for p in (it.get("media") or []) if p}
    pool = [p for p in media_paths if p and p not in owned]
    missing = [k for k, it in enumerate(items) if not [p for p in (it.get("media") or []) if p]]
    out = []
    for k, it in enumerate(items):
        own = [p for p in (it.get("media") or []) if p]
        out.append(own or pool[missing.index(k)::len(missing)])
    return out


def bundle_title_ok(text: str, usp_all: str = "") -> Optional[str]:
    """타이틀 바·하단 바 문구 검사 → 문제 설명 (없으면 None). 근거 없는 최저가·1위·품절 같은 주장 금지"""
    from core.shorts_meta import find_hype_claim
    t = (text or "").strip()
    if not t:
        return "빈 값"
    return find_hype_claim(t, usp_all) or _claim(t, usp_all)


def plan_bundle_storyboard(
    theme: str,
    items: List[Dict[str, Any]],
    media_paths: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    mock: bool = False,
    thumb_dir: Optional[str] = None,
    output_plan_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    items: [{name, usp, link, media?}] × 3 (상품명은 판매 페이지·사용자 입력 그대로). theme: 묶음 주제 (예: 자취생 삶의 질)
    → 씬 8개 (훅 1 + 아이템 3×2 + CTA 1). scenes[0]에 bundle_title(상단 타이틀 바)·bundle_items 기록
    """
    items = [dict(it) for it in (items or []) if (it.get("name") or "").strip()][:BUNDLE_SIZE]
    if len(items) < BUNDLE_SIZE:
        raise ValueError(f"묶음 큐레이션은 상품 {BUNDLE_SIZE}개가 필요해요 (지금 {len(items)}개). 상품명을 모두 적어 주세요.")
    theme = (theme or "").strip()
    per_item = _item_media(items, list(media_paths or []))
    if not mock and any(not m for m in per_item):
        raise ValueError("상품마다 사진이 1장 이상 필요해요. 상품 사진을 3장 이상 올려 주세요 (올린 순서 = 상품 1·2·3).")
    usp_all = ", ".join((it.get("usp") or "") for it in items)

    llm = None
    if not mock and script_llm_ready(api_key):
        try:
            llm = _plan_with_llm(theme, items, per_item, api_key, thumb_dir)
        except Exception as e:
            logger.warning(f"묶음 큐레이션 대본 엔진 모두 실패, 템플릿 대사로 대체: {e}")
    llm_scenes = (llm or {}).get("scenes") or []

    def llm_line(i: int) -> str:
        return to_haeyo(str((llm_scenes[i] if i < len(llm_scenes) and isinstance(llm_scenes[i], dict) else {})
                            .get("voiceover") or "").strip())

    used: set = set()
    templated: set = set()          # 템플릿으로 바뀐 씬 번호 (마지막에 어미 리듬만 손봄)

    def clean(text: str, usp: str, fallback, i: int, max_len: int) -> str:
        for check, label in ((lambda t: _claim(t, usp), "근거 없는 주장"), (find_promo_tone, "홍보 어투")):
            hit = check(text) if text else None
            if hit:
                logger.info(f"[{label} 차단] 씬 {i + 1}: '{hit}' (대사: {text}) → 템플릿 대사")
                text = ""
        if not text:
            templated.add(i)
        text = strip_brochure_words(text or fallback())
        if len(text) > max_len:
            text = shorten_line(text, max_len - 3, max_len)
        done = complete_sentence(text)
        if done is None or find_banned_style(done):
            logger.info(f"[대사 규칙 위반] 씬 {i + 1}: '{text}' → 템플릿 대사")
            done = complete_sentence(fallback()) or fallback()
            templated.add(i)
        return done

    scenes: List[Dict[str, Any]] = []
    # 씬 1: 훅
    hook = str((llm or {}).get("hook_title") or "").strip() or DEFAULT_HOOK
    if len(hook) > HOOK_TITLE_MAX + 4 or bundle_title_ok(hook, usp_all) or find_promo_tone(hook):
        hook = DEFAULT_HOOK
    first = clean(llm_line(0), usp_all, lambda: DEFAULT_HOOK_LINE, 0, HOOK_LINE_RANGE[1] + 8)
    scenes.append(_scene(0, "hook", first, hook))
    if per_item[0]:      # 훅 화면 = 상품 1 사진 (모든 씬에 사진을 지정해야 앱이 순번 배정으로 덮어쓰지 않음)
        scenes[0].update({"source_path": per_item[0][0], "shot": "full", "product_box": None, "media_kind": "overview"})
    # 씬 2~7: 아이템 × 2문장
    for k, it in enumerate(items):
        for j in range(LINES_PER_ITEM):
            i = len(scenes)
            if j == 0:
                fb = lambda k=k: ITEM_OPENERS[k % len(ITEM_OPENERS)].format(ord=ORDINALS[k])    # noqa: E731
            else:
                fb = lambda: _pick_template(used)                                             # noqa: E731
            line = clean(llm_line(i), it.get("usp") or "", fb, i, ITEM_LINE_RANGE[1] + 12)
            if j == 0 and not line.startswith(ORDINALS[k]):
                line = f"{ORDINALS[k]}, {line}" if len(line) + len(ORDINALS[k]) + 2 <= ITEM_LINE_RANGE[1] + 12 else line
            sc = _scene(i, "feature" if j == 0 else "detail", line)
            sc.update({"item_index": k + 1, "item_name": it["name"].strip(), "item_short": short_name(it["name"])})
            paths = per_item[k]
            if paths:
                sc.update({"source_path": paths[j % len(paths)], "shot": ITEM_SHOTS[j] if len(paths) == 1 else "full",
                           "product_box": None, "media_kind": "overview" if j == 0 else "feature"})
            scenes.append(sc)
    scenes.append(_scene(len(scenes), "cta", BUNDLE_CTA))
    if per_item[-1]:     # CTA 화면 = 마지막 상품 사진 (상품 3 설명에서 자연스럽게 이어짐)
        scenes[-1].update({"source_path": per_item[-1][0], "shot": "full", "product_box": None, "media_kind": "usage"})

    body = scenes[:-1]
    varied, changes = diversify_exclamations([s["voiceover"] for s in body], reserved=hook)
    for s, vo in zip(body, varied):
        s["voiceover"] = s["subtitle"] = s["caption"] = vo
    for c in changes:
        logger.info(f"[감탄사 중복 분산] {c}")
    # 템플릿 대사가 끼어 평서 '~요.'가 연속되면 그 템플릿 줄을 감탄형('~요!')으로 (질문형 강제 변환은 비문 위험이라 안 함)
    for i in sorted(templated):
        kinds = [ending_kind(s["voiceover"]) for s in body]
        vo = body[i]["voiceover"] if i < len(body) else ""
        near_plain = (i > 0 and kinds[i - 1] == "plain") or (i + 1 < len(kinds) and kinds[i + 1] == "plain")
        if kinds[i] == "plain" and near_plain and vo.endswith("요."):
            body[i]["voiceover"] = body[i]["subtitle"] = body[i]["caption"] = vo[:-1] + "!"

    title = str((llm or {}).get("title_bar") or "").strip().strip("[]【】 ")
    if not title or len(title) > TITLE_BAR_MAX + 4 or bundle_title_ok(title, usp_all):
        title = TEMPLATE_TITLE.format(theme=theme or "일상").strip()
    scenes[0].update({"bundle_title": title[:TITLE_BAR_MAX + 4], "bundle_theme": theme,
                      "bundle_items": [{"name": it["name"].strip(), "short": short_name(it["name"]),
                                        "link": (it.get("link") or "").strip()} for it in items],
                      "script_engine": (llm or {}).get("_engine") or "template"})
    if output_plan_path:
        with open(output_plan_path, "w", encoding="utf-8") as f:
            json.dump(scenes, f, ensure_ascii=False, indent=2)
    logger.info(f"묶음 큐레이션 콘티 완료: '{title}' {len(scenes)}개 씬 (LLM={scenes[0]['script_engine']})")
    return scenes


def _pick_template(used: set) -> str:
    pool = [l for r in ("feature", "usage", "detail") for l in TEMPLATE_LINES[r]]
    line = next((l for l in pool if l not in used), pool[0])
    used.add(line)
    return line


BUNDLE_SYSTEM = ("당신은 쇼핑 제휴 숏폼(쇼츠)의 3자 큐레이터 카피라이터입니다. 상품 3개를 30초 안에 빠르게 소개하는 "
                 "'묶음 큐레이션' 대본을 씁니다. 사진과 판매 페이지에 있는 사실만 쓰고, 지어낸 사용 후기는 쓰지 않습니다. "
                 "반드시 JSON 하나만 출력합니다.")


def _plan_with_llm(theme, items, per_item, api_key, thumb_dir) -> Dict[str, Any]:
    from core.llm import generate_json, repair_scene_lines
    from core.planners.commerce import _scene_preview
    from core.script_guard import STYLE_REPAIR_RULES, script_violation_finder

    contents: List[Any] = []
    for k, (it, paths) in enumerate(zip(items, per_item)):
        for p in paths[:1]:
            img = _scene_preview({"path": p, "shot": "full", "box": None}, thumb_dir)
            if img is not None:
                contents += [f"[상품 {k + 1} 사진]", img]
    listing = "\n".join(f"- 상품 {k + 1}: {it['name'].strip()} / 판매 페이지 특징(USP, 이 사실만 근거): {(it.get('usp') or '(없음 - 사진에 보이는 것만)').strip()}"
                        for k, it in enumerate(items))
    contents.append(f"""
[묶음 주제] {theme or '(없음 - 세 상품의 공통 상황으로 정하세요)'}
[상품 3개]
{listing}

[구조 - 총 30초, 나레이션 기준]
- 씬 1 (0~3초): hook_title(8~{HOOK_TITLE_MAX}자 헤드라인, 문제 제기·호기심) + voiceover 한 줄(공백 포함 {HOOK_LINE_RANGE[0]}~{HOOK_LINE_RANGE[1]}자).
  예: hook_title "자취방 필수템 3가지" → voiceover "이거 없이 어떻게 살았나 싶죠?"
- 씬 2~7 (상품당 7초): 상품 1·2·3 순서로 상품마다 2문장. 각 voiceover는 공백 포함 {ITEM_LINE_RANGE[0]}~{ITEM_LINE_RANGE[1]}자.
  · 첫 문장(씬 2·4·6): '첫 번째', '두 번째', '마지막'으로 시작 + 그 상품이 필요한 순간(언제·누구에게)
  · 둘째 문장(씬 3·5·7): 그 상품 USP·사진에 있는 핵심 셀링포인트 하나
  · 상품 이름은 길게 읽지 말고 '이 무선 청소기'처럼 짧게. 그 상품 사진·USP에 없는 기능은 쓰지 마세요
- 마지막 CTA는 따로 붙이므로 쓰지 마세요.
- title_bar: 영상 상단에 계속 떠 있는 묶음 타이틀 ({TITLE_BAR_MAX}자 이내, 예: "자취생 삶의 질 수직 상승템 3가지").
  최저가·1위·품절·할인율 같은 사실 주장 금지.
{COPY_TONE_RULES}

JSON만 출력: {{"title_bar": "...", "hook_title": "...", "scenes": [{{"scene_index": 1, "voiceover": "..."}}, ... 씬 7까지]}}
""")
    data, engine = generate_json(contents, BUNDLE_SYSTEM, temperature=0.6, gemini_api_key=api_key, purpose="묶음 큐레이션 대본")
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
        raise ValueError(f"예상치 못한 응답: {str(data)[:200]}")
    lines = [str(s.get("voiceover") or "") for s in data["scenes"] if isinstance(s, dict)]
    data, fixes = repair_scene_lines(data, script_violation_finder(lines), BUNDLE_SYSTEM, STYLE_REPAIR_RULES,
                                     gemini_api_key=api_key)
    for f in fixes:
        logger.info(f"[대본 규칙 보정] {f}")
    data["_engine"] = engine
    return data
