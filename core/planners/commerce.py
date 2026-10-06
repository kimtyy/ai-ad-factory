"""
core/planners/commerce.py - 트랙 A: 쇼핑 제휴(쿠팡 파트너스) 숏폼 플래너

숙소 제휴·사장님 모드에서 다듬은 엔진을 쇼핑에 이식합니다.
- 실물 우선(Asset-First): 판매 페이지에서 받은 상품 사진·사용자가 올린 사진/영상만 씁니다 (AI 이미지 생성 없음).
  Gemini가 사진을 직접 보고 씬 대사를 쓰므로 화면에 없는 상품·장면을 지어내지 않습니다.
- 대본: 직접 써 본 사람이 친구에게 추천 썰을 푸는 이야기 하나를 씬별 완결 문장으로 (해요체, 홍보 어투·개조식 금지)
- 허위 방지: 가격·할인·순위와 효능·수치·인증 주장은 판매 페이지(USP)에 적힌 것만. 상품 종류와 무관한 표현(화장품 문구 등) 금지
- 엔딩: 고정 댓글 링크 안내 CTA + 쿠팡 파트너스 제휴 표시(공정위 추천·보증 심사지침)
"""
import json
import logging
from typing import Any, Dict, List, Optional

from core.radar.hints import HookHints, copies_reference
from core.script_guard import (COPY_TONE_RULES, VO_HARD_MAX, VO_LIMIT, complete_sentence, diversify_exclamations,
                               find_promo_tone, find_unsupported_claim, find_unsupported_spec, is_hook_echo,
                               shorten_line, strip_brochure_words, to_haeyo)

logger = logging.getLogger("ad_factory.planners.commerce")

# 쿠팡 파트너스 고지 문구: 영상 화면용(짧게) / 영상 설명·고정 댓글용(쿠팡 안내 원문)
COUPANG_DISCLOSURE = "쿠팡 파트너스 활동의 일환으로 일정액의 수수료를 제공받습니다"
COUPANG_DISCLOSURE_FULL = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
COMMERCE_CTA = "제품 정보는 고정 댓글 링크에 남겨 둘게요!"
DEFAULT_HOOK = "이거 써 보셨어요?"

MIN_SCENES = 4     # 후킹 + 특징 2 + CTA (사진이 적으면 같은 사진을 다른 카메라 모션으로 재사용)
MAX_SCENES = 8
ROLE_CYCLE = ("feature", "detail", "usage", "feature")
ROLE_LABELS_KO = {"hero": "제품 첫인상", "feature": "핵심 기능", "detail": "디테일", "usage": "사용 장면",
                  "package": "구성품", "cta": "구매 링크"}

# 상품 종류를 몰라도 성립하는 템플릿 대사 (사실 주장 없음, 해요체 완결 문장) - Gemini 실패·차단 시 대체용
TEMPLATE_LINES = {
    "hero": ("솔직히 이거 써 보고 생각이 좀 바뀌었거든요.", "처음엔 반신반의했는데 써 보니까 다르더라고요."),
    "feature": ("제일 마음에 든 건 쓰기가 진짜 편하다는 거예요.", "기능이 복잡하지 않아서 누구나 바로 쓸 수 있어요."),
    "detail": ("가까이서 보면 마감도 꼼꼼하게 돼 있더라고요.", "작은 부분까지 신경 쓴 게 눈에 보여요."),
    "usage": ("매일 쓰다 보니까 손이 제일 자주 가요.", "자리도 많이 안 차지해서 어디 두기도 좋아요."),
    "package": ("구성도 깔끔하게 들어 있어서 좋았어요.", "받자마자 바로 쓸 수 있게 다 들어 있어요."),
}


def _template_line(role: str, used: set) -> str:
    """역할별 템플릿 대사 중 이 영상에서 아직 안 쓴 것 (같은 대체 문장이 두 씬에 반복되지 않게)"""
    pool = list(TEMPLATE_LINES.get(role, TEMPLATE_LINES["feature"])) + [l for r, ls in TEMPLATE_LINES.items() if r != role for l in ls]
    line = next((l for l in pool if l not in used), pool[0])
    used.add(line)
    return line


def commerce_roles(count: int) -> List[str]:
    count = max(MIN_SCENES, min(MAX_SCENES, int(count or MIN_SCENES)))
    return ["hero"] + [ROLE_CYCLE[i % len(ROLE_CYCLE)] for i in range(count - 2)] + ["cta"]


def _scene(i: int, role: str, text: str, hook: str = "") -> Dict[str, Any]:
    motions = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_down", "pan_up"]
    caption = text.rstrip("!") if role == "cta" else text
    return {
        "scene_index": i + 1, "scene_number": i + 1, "role": role,
        "target_visual_role": role, "visual_keywords": [ROLE_LABELS_KO.get(role, role)],
        "duration_sec": 2.5, "hook_title": hook, "voiceover": text, "subtitle": caption, "caption": caption,
        "visual_description": ROLE_LABELS_KO.get(role, role), "image_prompt_en": "",
        "video_motion_prompt_en": motions[i % len(motions)], "focus_bottom": False,
    }


def _claim(text: str, usp: str) -> Optional[str]:
    """판매 페이지(USP)에 근거 없는 가격·할인·순위 또는 효능·수치·인증 주장"""
    return find_unsupported_claim(text, usp=usp, check_amenities=False) or find_unsupported_spec(text, usp)


def plan_commerce_storyboard(
    product_name: str,
    usp: str = "",
    media_paths: Optional[List[str]] = None,
    num_scenes: Optional[int] = None,
    api_key: Optional[str] = None,
    mock: bool = False,
    thumb_dir: Optional[str] = None,
    output_plan_path: Optional[str] = None,
    hook_hints: Optional[HookHints] = None,
) -> List[Dict[str, Any]]:
    """
    쇼핑 제휴 콘티. 씬 수 = 사진·영상 수 (MIN_SCENES~MAX_SCENES, 부족하면 앱에서 사진을 돌려 씀).
    i번째 씬 = i번째 미디어 (마지막 씬은 CTA). 상품명은 판매 페이지/사용자 입력값 그대로 (지어내지 않음)
    """
    product_name = (product_name or "").strip()
    if not product_name:
        raise ValueError("상품명이 비어 있습니다. 판매 페이지 링크를 넣거나 상품명을 직접 입력해 주세요.")
    media = list(media_paths or [])[:MAX_SCENES]
    roles = commerce_roles(len(media) if len(media) >= MIN_SCENES else (num_scenes or MIN_SCENES))

    llm, llm_story = None, ""
    if api_key and not mock:
        try:
            llm = _plan_with_gemini(product_name, usp, roles, media, api_key, thumb_dir, hook_hints)
            llm_story = getattr(llm, "story", "")
        except Exception as e:
            logger.warning(f"쇼핑 플래너 Gemini 호출 실패, 템플릿 대사로 대체: {e}")

    scenes, used_templates = [], set()
    for i, role in enumerate(roles):
        is_last = i == len(roles) - 1
        item = (llm or [])[i] if llm and i < len(llm) else {}
        if is_last:
            scenes.append(_scene(i, "cta", COMMERCE_CTA))
            continue
        fallback = lambda: _template_line(role, used_templates)   # noqa: E731 (필요할 때만 하나씩 꺼냄)
        text = to_haeyo(str(item.get("voiceover") or "").strip()) or fallback()
        for check, label in ((lambda t: _claim(t, usp), "근거 없는 주장"), (find_promo_tone, "홍보 어투")):
            hit = check(text)
            if hit:
                logger.info(f"[{label} 차단] 씬 {i + 1}: '{hit}' (대사: {text}) → 템플릿 대사")
                text = fallback()
        hook = ""
        if i == 0:
            hook = str((llm[0].get("hook_title") if llm else "") or "").strip() or DEFAULT_HOOK
            if _claim(hook, usp) or find_promo_tone(hook):
                hook = DEFAULT_HOOK
            if hook_hints and copies_reference(hook, hook_hints):
                hook = DEFAULT_HOOK
            if is_hook_echo(hook, text):
                logger.info(f"[후킹 반복 차단] '{hook}' ≈ '{text}' → 템플릿 대사")
                text = fallback()
        text = strip_brochure_words(text)
        if len(text) > VO_HARD_MAX:
            text = shorten_line(text, VO_HARD_MAX - 3, VO_HARD_MAX)
        done = complete_sentence(text)
        if done is None:
            logger.info(f"[미완성 문장 차단] 씬 {i + 1}: '{text}' → 템플릿 대사")
            done = fallback()
        scenes.append(_scene(i, role, done, hook))

    body = scenes[:-1]
    varied, changes = diversify_exclamations([s["voiceover"] for s in body], reserved=scenes[0]["hook_title"])
    for s, vo in zip(body, varied):
        s["voiceover"] = s["subtitle"] = s["caption"] = vo
    for c in changes:
        logger.info(f"[감탄사 중복 분산] {c}")
    if llm_story:
        scenes[0]["story"] = llm_story
    if hook_hints:
        scenes[0]["hook_reference"] = hook_hints.summary()

    if output_plan_path:
        with open(output_plan_path, "w", encoding="utf-8") as f:
            json.dump(scenes, f, ensure_ascii=False, indent=2)
    logger.info(f"쇼핑 콘티 완료: '{product_name}' {len(scenes)}개 씬 (미디어 {len(media)}개, Gemini={'Y' if llm else 'N'})")
    return scenes


class _ScenesWithStory(list):
    story = ""


def _plan_with_gemini(product_name, usp, roles, media, api_key, thumb_dir, hook_hints=None):
    from google import genai
    from google.genai import types
    from core.guardrail import validate_model_call
    from core.planners.owner import _media_preview_image

    contents: List[Any] = []
    for i, path in enumerate(media[:len(roles)]):
        img = _media_preview_image(path, thumb_dir)
        if img is not None:
            contents += [f"[씬 {i + 1} 상품 사진]", img]
    n = len(roles)
    role_hint = ", ".join(f"씬{i + 1}={ROLE_LABELS_KO.get(r, r)}" for i, r in enumerate(roles))
    persona_rules = COPY_TONE_RULES.replace("유튜버가 시청자 1명에게 꿀팁 썰 푸는", "직접 써 본 사람이 친구 1명에게 추천 썰 푸는")
    contents.append(f"""
[상품 정보]
- 상품명(판매 페이지 표기): {product_name}
- 판매 페이지에 적힌 특징(USP, 여기 있는 사실만 근거로 사용): {usp or '(없음 - 사진에 보이는 것만 말하세요)'}

[작성 규칙]
1. 먼저 사진과 상품명으로 이 상품이 어떤 종류인지 파악하세요. 대사는 반드시 그 상품 종류에 맞는 말이어야 합니다
   (예: 히터라면 따뜻함·안전·크기 이야기. 화장품이 아닌데 '보습·피부' 같은 말을 쓰면 안 됩니다).
2. 총 {n}개 씬. [씬 i 상품 사진]이 있으면 i번째 씬 대사는 그 사진에 보이는 부분(외관·기능·디테일·사용 장면)에 대한 이야기여야 합니다.
   사진이 없는 씬은 기본 구성({role_hint})을 따르세요.
3. 먼저 직접 써 본 사람이 친구 한 명에게 추천 썰을 푸는 독백(story)을 정확히 {n - 1}개의 완결 문장으로 쓰고,
   문장 하나씩 씬 1~{n - 1}의 voiceover로 넣으세요. 각 voiceover는 공백 포함 15~{VO_LIMIT}자, 해요체 종결 어미로 끝나는 완결 문장.
4. 효능·수치·인증·순위·가격·할인(예: 48시간, 2배, 특허, 1위, 30% 할인)은 위 USP에 그대로 적힌 것만 쓰세요. 없으면 쓰지 마세요.
   써 보니 편했다·따뜻했다 같은 사용감과 사진에 보이는 사실로 이야기하세요.
5. 상품명 전체를 읽지 말고 '이 히터', '이 제품'처럼 짧게 부르세요. 특수문자/이모지 금지(쉼표·느낌표·물음표만).
6. 씬 1의 hook_title은 스크롤을 멈추게 하는 8~12자 헤드라인입니다. 나레이터가 헤드라인을 먼저 읽고 씬 1 voiceover를 이어 읽으므로,
   voiceover는 헤드라인 단어를 반복하지 말고 바로 구체적인 이유로 받으세요.
{hook_hints.prompt_block if hook_hints else ""}
7. 마지막 씬 {n}의 voiceover는 정확히: "{COMMERCE_CTA}"
{persona_rules}

JSON만 출력: {{"story": "...", "scenes": [{{"scene_index": 1, "hook_title": "...", "voiceover": "..."}}]}}
""")
    client = genai.Client(api_key=api_key)
    res = client.models.generate_content(
        model=validate_model_call("gemini-3.6-flash", category="llm"),
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction="당신은 쇼핑 제휴 숏폼(릴스/쇼츠)의 리뷰 카피라이터입니다. 사진과 판매 페이지에 있는 사실만으로, "
                               "직접 써 본 사람의 말투로 상품의 매력을 전달합니다. 과장·지어낸 효능은 절대 쓰지 않습니다.",
            response_mime_type="application/json",
            temperature=0.6,
        ),
    )
    data = json.loads(res.text)
    scenes = data.get("scenes", data) if isinstance(data, dict) else data
    if not isinstance(scenes, list) or not scenes:
        raise ValueError(f"예상치 못한 응답: {res.text[:200]}")
    out = _ScenesWithStory(scenes)
    out.story = str(data.get("story") or "").strip() if isinstance(data, dict) else ""
    return out
