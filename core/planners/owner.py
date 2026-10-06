"""
core/planners/owner.py - 트랙 B: 사장님(펜션/음식점/매장) 직영 홍보 숏폼 플래너

제휴 쇼츠(트랙 A)와 반대 원칙으로 동작합니다.
- 상호명 보존: 대본·후킹에 상호명을 그대로 노출 (트랙 A는 숙소명 은닉)
- 미디어 우선(Asset-First): 사장님이 올린 사진/영상 N개를 순서대로 보고 씬 N개의 대사를 작성
  (트랙 A는 대본을 먼저 쓰고 크롤링 사진을 매칭하는 Script-First)
- 엔딩 CTA: '댓글에 여행' 대신 네이버 플레이스 검색 유도
- 허위 방지: 가격/할인/순위 등 사장님이 입력한 USP에 없는 수치·혜택 표현은 자동 차단
"""
import io
import json
import logging
import os
from typing import Any, Dict, List, Optional

from core.script_guard import (COPY_TONE_RULES, VO_LIMIT, VO_MAX, find_promo_tone, find_unsupported_claim, is_hook_echo,
                                shorten_line, strip_brochure_words, to_haeyo)
from core.radar.hints import HookHints, copies_reference

logger = logging.getLogger("ad_factory.planners.owner")

BUSINESS_TYPES = {
    "stay": {"label": "🏡 펜션·숙소", "roles": ["exterior", "room", "facility", "view", "detail"]},
    "food": {"label": "🍽 음식점·카페", "roles": ["exterior", "signature", "cooking", "interior", "detail"]},
    "shop": {"label": "🛍 매장·기타", "roles": ["exterior", "product", "interior", "detail", "service"]},
}
DEFAULT_BUSINESS_TYPE = "food"

ROLE_LABELS_KO = {
    "exterior": "외관·입구", "room": "객실", "facility": "부대시설", "view": "주변 풍경", "detail": "디테일",
    "signature": "대표 메뉴", "cooking": "조리 장면", "interior": "실내 분위기", "product": "대표 상품",
    "service": "서비스", "cta": "검색 유도",
}

MOCK_LINES = {
    "stay": {"exterior": "도착하자마자 설레는 우리 숙소!", "room": "객실 들어오면 다들 일단 눕고 봐요!",
             "facility": "놀 거리 가득한 부대시설까지!", "view": "창밖 풍경만 봐도 힐링돼요!", "detail": "작은 것 하나까지 신경 썼어요!"},
    "food": {"exterior": "골목 안 숨은 맛집, 여기예요!", "signature": "이게 바로 저희 대표 메뉴!",
             "cooking": "주문 즉시 바로 만들어 드려요!", "interior": "편하게 머물다 가는 공간이에요!", "detail": "재료부터 정성껏 준비해요!"},
    "shop": {"exterior": "찾아오시기 쉬운 우리 매장!", "product": "가장 사랑받는 대표 상품!",
             "interior": "천천히 둘러보기 좋은 공간!", "detail": "디테일까지 꼼꼼하게 골랐어요!", "service": "친절하게 안내해 드릴게요!"},
}

# 사장님이 입력하지 않은 수치/혜택/순위 주장 차단 규칙은 core.script_guard.PRICE_CLAIM_PATTERNS에서 관리


def owner_cta(search_keyword: str) -> str:
    kw = (search_keyword or "").strip()
    return f"네이버에서 {kw} 검색해 보세요!" if kw else "네이버에서 검색해 보세요!"


def _roles_for(business_type: str, count: int) -> List[str]:
    base = BUSINESS_TYPES.get(business_type, BUSINESS_TYPES[DEFAULT_BUSINESS_TYPE])["roles"]
    roles = [base[i % len(base)] for i in range(max(count - 1, 0))]
    return roles + ["cta"]


def _unsupported_claim(text: str, usp: str) -> Optional[str]:
    # 사장님 모드는 Gemini가 사진을 직접 보고 쓰므로 시설 묘사는 허용, 가격·할인·순위는 USP에 적힌 것만 허용
    return find_unsupported_claim(text, usp=usp, check_amenities=False)


def _media_preview_image(path: str, thumb_dir: Optional[str]):
    """사진은 그대로, 영상은 대표 프레임을 추출해 512px 미리보기 PIL 이미지로 반환"""
    from PIL import Image
    from core.media import is_video_file, extract_thumbnail
    src = path
    if is_video_file(path):
        if not thumb_dir:
            return None
        src = os.path.join(thumb_dir, f"plan_{os.path.splitext(os.path.basename(path))[0]}.jpg")
        if not os.path.exists(src):
            extract_thumbnail(path, src, at=0.8)
    img = Image.open(src).convert("RGB")
    img.thumbnail((512, 512))
    return img


def _scene(i: int, role: str, text: str, hook: str = "") -> Dict[str, Any]:
    motions = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_down", "pan_up"]
    return {
        "scene_index": i + 1, "scene_number": i + 1, "role": role,
        "target_visual_role": role, "visual_keywords": [ROLE_LABELS_KO.get(role, role)],
        "duration_sec": 2.5, "hook_title": hook, "voiceover": text, "subtitle": text, "caption": text,
        "visual_description": ROLE_LABELS_KO.get(role, role), "image_prompt_en": "",
        "video_motion_prompt_en": motions[i % len(motions)], "focus_bottom": False,
    }


def plan_owner_storyboard(
    business_name: str,
    usp: str = "",
    business_type: str = DEFAULT_BUSINESS_TYPE,
    search_keyword: str = "",
    media_paths: Optional[List[str]] = None,
    num_scenes: Optional[int] = None,
    tone_instruction: str = "",
    api_key: Optional[str] = None,
    mock: bool = False,
    thumb_dir: Optional[str] = None,
    output_plan_path: Optional[str] = None,
    hook_hints: Optional[HookHints] = None,
) -> List[Dict[str, Any]]:
    """
    사장님 홍보 콘티 생성.
    - media_paths가 있으면 씬 수 = 미디어 수 (2~8), i번째 씬 = i번째 미디어. 없으면 업종 템플릿으로 num_scenes개.
    - 마지막 씬은 항상 네이버 검색 유도 CTA (마지막 미디어 위에 재생).
    """
    business_name = (business_name or "").strip()
    search_keyword = (search_keyword or "").strip() or business_name
    media = list(media_paths or [])[:8]
    count = len(media) if len(media) >= 2 else max(int(num_scenes or 4), 2)
    roles = _roles_for(business_type, count)
    cta_line = owner_cta(search_keyword)
    lines = MOCK_LINES.get(business_type, MOCK_LINES[DEFAULT_BUSINESS_TYPE])
    default_hook = f"{business_name} 가봤어요?" if business_name else "여기 진짜 가볼 만해요!"

    llm = None
    llm_story = ""
    if api_key and not mock:
        try:
            llm = _plan_with_gemini(business_name, usp, business_type, roles, media, cta_line,
                                    tone_instruction, api_key, thumb_dir, hook_hints)
            llm_story = str(getattr(llm, "story", "") or "")
        except Exception as e:
            logger.warning(f"사장님 플래너 Gemini 호출 실패, 업종 템플릿으로 대체: {e}")

    scenes = []
    for i, role in enumerate(roles):
        is_last = i == len(roles) - 1
        item = (llm or [])[i] if llm and i < len(llm) else {}
        text = cta_line if is_last else str(item.get("voiceover") or "").strip() or lines.get(role, lines["exterior"])
        if not is_last:
            text = to_haeyo(text)   # 말투 통일: 합쇼체 → 해요체
            claim = _unsupported_claim(text, usp)
            if claim:
                logger.info(f"[허위 차단] 씬 {i + 1}: USP에 없는 표현 '{claim}' → 템플릿 대사로 교체")
                text = lines.get(role, lines["exterior"])
            promo = find_promo_tone(text)
            if promo:
                logger.info(f"[홍보 어투 차단] 씬 {i + 1}: {promo} (대사: {text}) → 템플릿 대사로 교체")
                text = lines.get(role, lines["exterior"])
            text = shorten_line(strip_brochure_words(text), VO_LIMIT, VO_MAX)
        if llm and i < len(llm) and llm[i].get("role") in ROLE_LABELS_KO and not is_last:
            role = llm[i]["role"]
        hook = ""
        if i == 0:
            hook = str((llm[0].get("hook_title") if llm else "") or "").strip() or default_hook
            if _unsupported_claim(hook, usp):
                hook = default_hook
            if hook_hints:
                if copies_reference(hook, hook_hints):
                    logger.info(f"[후킹 원문 복제 차단] '{hook}' → 기본 후킹")
                    hook = default_hook
                if copies_reference(text, hook_hints):
                    text = lines.get(role, lines["exterior"])
            if is_hook_echo(hook, text):
                logger.info(f"[후킹 반복 차단] 헤드라인 '{hook}' ≈ 서브 '{text}' → 템플릿 대사로 교체")
                text = lines.get(role, lines["exterior"])
        scenes.append(_scene(i, "cta" if is_last else role, text, hook))
        if i == 0 and hook_hints:
            scenes[0]["hook_reference"] = hook_hints.summary()
            used = hook_hints.pattern_used((llm[0] if llm else {}).get("hook_pattern_used"))
            scenes[0]["hook_pattern_used"] = {"video_id": used["video_id"], "hook_type": used["hook_type"],
                                              "pattern_template": used["pattern_template"]} if used else None

    if llm_story and scenes:
        scenes[0]["story"] = llm_story
    if output_plan_path:
        with open(output_plan_path, "w", encoding="utf-8") as f:
            json.dump(scenes, f, ensure_ascii=False, indent=2)
    logger.info(f"사장님 콘티 완료: '{business_name}' {len(scenes)}개 씬 (미디어 {len(media)}개, Gemini={'Y' if llm else 'N'})")
    return scenes


def _plan_with_gemini(business_name, usp, business_type, roles, media, cta_line, tone_instruction, api_key, thumb_dir,
                      hook_hints=None):
    from google import genai
    from google.genai import types
    from core.guardrail import validate_model_call

    type_label = BUSINESS_TYPES.get(business_type, BUSINESS_TYPES[DEFAULT_BUSINESS_TYPE])["label"]
    contents: List[Any] = []
    for i, path in enumerate(media[:len(roles)]):
        img = _media_preview_image(path, thumb_dir)
        if img is not None:
            contents += [f"[씬 {i + 1} 미디어]", img]
    n = len(roles)
    role_hint = ", ".join(f"씬{i + 1}={ROLE_LABELS_KO.get(r, r)}" for i, r in enumerate(roles))
    contents.append(f"""
[가게 정보]
- 상호명(대본에 자연스럽게 노출 가능): {business_name}
- 업종: {type_label}
- 사장님이 직접 적은 특장점(USP): {usp or '(없음)'}
- 톤 참고: {tone_instruction or '친근하고 담백한 사장님 직접 홍보 톤'}

[작성 규칙]
1. 총 {n}개 씬. 위에 붙은 [씬 i 미디어]가 있으면 i번째 씬 대사는 그 사진/영상 속 공간·메뉴에 대한 이야기여야 합니다
   (사진에 없는 걸 지어내지 말되, 보이는 걸 나열해 읽지도 말고 '그래서 뭐가 좋은지'를 말하세요).
   미디어가 없는 씬은 업종 기본 구성({role_hint})을 따르세요.
2. 먼저 사장님이 손님 한 명에게 가게 자랑 썰을 푸는 독백(story)을 쓰고, 순서대로 잘라 씬 1~{n - 1}의 voiceover로 나누세요.
   각 voiceover는 공백 포함 15~{VO_LIMIT}자. 특수문자/이모지 금지(쉼표·느낌표·물음표만 허용).
3. 가격, 할인, 무료, 순위(1위 등), 수상, 이벤트처럼 USP에 없는 수치·혜택은 절대 쓰지 마세요.
4. 씬 1의 hook_title은 스크롤을 멈추게 하는 8~12자 헤드라인 (상호명 포함 가능, 읽는 데 2초 이내). 나레이터가 헤드라인을 먼저 읽고
   씬 1 voiceover를 이어 읽으므로, voiceover는 헤드라인을 받아 이야기를 시작하는 첫 문장으로 쓰세요.
   헤드라인 단어를 voiceover에서 그대로 반복하지 말고 바로 구체적인 이유·감탄으로 받으세요.
{COPY_TONE_RULES.replace("유튜버가 시청자 1명에게 꿀팁 썰 푸는", "사장님이 손님 1명에게 가게 자랑 썰 푸는")}
   (사장님 말투 예: '솔직히 저희 집은 창문 열면 계곡 소리부터 들어오거든요.' → '근데 진짜 대박인 건 저녁에 마당 불멍인데,')
{hook_hints.prompt_block if hook_hints else ""}
5. 마지막 씬 {n}의 voiceover는 정확히: "{cta_line}"
6. role은 다음 중 하나: {", ".join(k for k in ROLE_LABELS_KO if k != "cta")} (마지막 씬은 "cta")

JSON만 출력: {{"story": "...", "scenes": [{{"scene_index": 1, "role": "...", "hook_title": "...", "voiceover": "..."}}]}}
""")
    client = genai.Client(api_key=api_key)
    res = client.models.generate_content(
        model=validate_model_call("gemini-3.6-flash", category="llm"),
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction="당신은 동네 가게 사장님이 직접 올리는 숏폼(릴스/쇼츠) 홍보 영상 카피라이터입니다. "
                               "과장 없이 사진에 보이는 사실만으로 가게의 매력을 전달합니다.",
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


class _ScenesWithStory(list):
    """씬 목록 + Gemini가 먼저 쓴 전체 독백(story)"""
    story = ""
