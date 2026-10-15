"""
core/analyzer.py - Step 1: Gemini Vision 레퍼런스 분석 & 콘티 역기획 (자연스러운 한국어 구어체 & 자막 텍스트 특화)
"""
import os
import re
import time
import json
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from core.guardrail import validate_model_call
from core.script_guard import COPY_TONE_RULES, VO_LIMIT, VO_HARD_MAX, complete_sentence, diversify_exclamations, shorten_line, strip_brochure_words, to_haeyo, is_hook_echo, \
    find_off_scene_topic, find_promo_tone, find_unsupported_claim, safe_line_for
from core.radar.hints import HookHints, copies_reference
from core.mode_policy import TRAVEL_POLICY, clamp_line_to_policy, create_length_violation_checker

logger = logging.getLogger("ad_factory.analyzer")

class ScenePlan(BaseModel):
    scene_number: int = Field(..., description="씬 번호 (1부터 순차 증가)")
    duration_sec: float = Field(..., description="씬 지속 시간(초, 2.0~3.5초 권장)")
    hook_title: Optional[str] = Field(None, description="씬 1에서 스크롤을 즉시 멈추게 만드는 초강력 상단 후킹 헤드라인 (씬 1 필수, 10~15자 내외)")
    voiceover: str = Field(..., description="성우 나레이션 대사 (자연스러운 한국어 구어체, 오탈자 없음, 발음하기 편한 문장)")
    caption: str = Field(..., description="화면 중앙 하단(65~70% 지점)에 표시할 굵은 핵심 한글 자막 (15자 내외, 임팩트 있는 구어체)")
    visual_description: str = Field(..., description="장면 연출 상세 설명")
    image_prompt_en: str = Field(
        ..., 
        description="Google Imagen 3용 영문 프롬프트 (9:16 vertical ratio, photorealistic, commercial lighting, 8k)"
    )
    video_motion_prompt_en: str = Field(
        ..., 
        description="로컬 2D Ken Burns 카메라 모션 연출 프롬프트 (e.g., dynamic zoom-in, smooth slow pan)"
    )


TONE_PRESETS = {
    "aggro": {
        "name": "🔥 어그로 / 손실회피형",
        "instruction": "🔥 [어그로 / 손실회피형]: 시청자의 결핍과 손실을 강력하게 자극하는 도발적 후킹 ('아직도 OO 쓰시나요?', '이거 모르고 예약하면 손해', '다들 여기만 몰래 감'). 위기감 조성 후 즉각적인 반전과 해결책 제시."
    },
    "expert": {
        "name": "👩‍⚕️ 전문가 / 신뢰형",
        "instruction": "👩‍⚕️ [전문가 / 신뢰형]: 피부 과학, 임상 데이터, 핵심 유효 성분의 원리와 논리적 근거를 바탕으로 신뢰를 주는 차분하고 전문적인 어조 ('피부과 전문의가 주목한', '임상 시험 입증 48시간 고보습', '성분표 1위의 비밀')."
    },
    "review": {
        "name": "💬 SNS 리얼 후기형",
        "instruction": "💬 [SNS 리얼 후기형]: '언니들 저 드디어 인생템 찾았어요!', '광고 아니고 찐 내돈내산 후기' 느낌의 1인칭 친근한 구어체, 실제 경험담 위주의 공감대 형성 및 감탄사 활용."
    },
    "speed15": {
        "name": "⚡ 15초 초스피드형",
        "instruction": "⚡ [15초 초스피드형]: 군더더기 없는 초압축 타격형 카피. 1초 문제 제기 -> 2초 제품 등장 -> 3초 즉각 효능 입증 -> 바로 구매 유도. 빠른 템포와 직격탄 카피."
    }
}

DURATION_PRESETS = {
    "10s": {"target_sec": 10.0, "default_scenes": 2, "scene_range": (2, 3), "name": "⚡ 10초 이내 (씬 2~3개)"},
    "15s": {"target_sec": 15.0, "default_scenes": 3, "scene_range": (3, 4), "name": "⏱️ 15초 (씬 3~4개)"},
    "30s": {"target_sec": 30.0, "default_scenes": 5, "scene_range": (5, 6), "name": "⏳ 30초 (씬 5~6개)"},
    "60s": {"target_sec": 60.0, "default_scenes": 8, "scene_range": (8, 10), "name": "🎬 60초 (씬 8~10개)"},
}


def generate_mock_storyboard(
    product_name: str,
    usp: str,
    num_scenes: int = 2,
    tone_and_manner: str = "aggro"
) -> List[dict]:
    """테스트 및 검증용 Mock 콘티 데이터 생성 (전략가 에이전트 후킹 타이틀 & 오탈자 없는 구어체)"""
    templates = [
        {
            "scene_number": 1,
            "duration_sec": 2.5,
            "hook_title": f"아직도 {product_name} 모르세요?",
            "voiceover": f"아직도 피부 고민 때문에 스트레스 받으시나요? 이제 {product_name} 하나로 해결하세요!",
            "caption": "피부 고민, 아직도 방치 중?",
            "visual_description": f"소비자의 시선을 단번에 사로잡는 {product_name} 클로즈업 컷",
            "image_prompt_en": f"Cinematic vertical 9:16 commercial beauty shot featuring the same 20s Korean female model with clear glass skin and shoulder-length soft wave hair, looking concerned about dry skin",
            "video_motion_prompt_en": "Slow dramatic camera push-in towards the product, soft light flare, cinematic 24fps"
        },
        {
            "scene_number": 2,
            "duration_sec": 3.0,
            "hook_title": "",
            "voiceover": f"{usp}! 직접 경험해보면 확실히 달라진 피부를 바로 느끼실 수 있습니다.",
            "caption": f"{usp}! 확실한 차이를 경험하세요",
            "visual_description": f"{usp}의 효능을 생생하게 보여주는 시연 및 제품 풀 샷",
            "image_prompt_en": f"Vertical 9:16 macro close-up shot of clear ampoule texture on smooth glowing skin, delicate droplet falling on skin, rich viscosity, no full face, professional luxury studio lighting",
            "video_motion_prompt_en": "Smooth fluid motion showing dynamic angle shift, gentle soft lighting, seamless transition"
        },
        {
            "scene_number": 3,
            "duration_sec": 2.5,
            "hook_title": "",
            "voiceover": f"지금 특별 혜택으로 {product_name}을(를) 만나보세요! 놓치면 후회합니다.",
            "caption": "지금 공식 스토어에서 특별 혜택 확인!",
            "visual_description": f"{product_name}의 풀 패키징과 함께 매력적인 CTA 연출",
            "image_prompt_en": f"Vertical 9:16 high-fashion commercial display of {product_name} with minimalist podium, natural lighting, vibrant aesthetic, 8k resolution",
            "video_motion_prompt_en": "Elegant rotating camera orbit around the product with gentle upward tilt, commercial dynamic finish"
        }
    ]
    
    selected_scenes = []
    for i in range(num_scenes):
        idx = i % len(templates)
        scene_data = dict(templates[idx])
        scene_data["scene_number"] = i + 1
        selected_scenes.append(scene_data)
        
    return selected_scenes


SCRIPT_STYLE_PRESETS = {
    "info": {
        "name": "📢 정보/후킹형 (홍보·추천 스타일)",
        "instruction": (
            "📢 [3자 큐레이터형 (TPO 추천)]:\n"
            "- 큐레이터가 시청자에게 '이런 날·이런 사람에게 여기'를 짚어 주듯, 방문 상황(언제·누구와·어떤 기분)을 중심으로 호기심을 끌고 가세요.\n"
            "- 흐름 구조: [시청자의 상황·기분을 짚는 말] → [그 상황에 맞는 이 숙소의 구체적 공간 한 곳] → [그 공간에서의 순간 감각]. "
            "문장은 이 숙소의 입력 정보와 사진에서 새로 쓰고, 예시 문구를 그대로 쓰지 마세요."
        )
    },
    "experience": {
        "name": "✈️ 1인칭 경험형 (생생한 투숙 후기 스타일)",
        "instruction": (
            "✈️ [상황 몰입형 (방문 장면 묘사)]:\n"
            "- 시청자가 그 숙소에 있는 순간을 상상하게 만드는 장면 묘사형. 지어낸 체험담('제가 가 봤는데')은 쓰지 말고 '~할 때' 장면으로 말하세요.\n"
            "- 예시 흐름: '퇴근하고 바로 떠나는 금요일 밤이라면?' → '로비 문 열자마자 통창 뷰가 먼저 반겨 줘요.' "
            "→ '체크아웃 시간이 야속해지는 방이에요.'\n"
            "- 씬당 공백 포함 15~26자 구어체를 지키세요."
        )
    }
}


# -------------------------------------------------------------
# [씬별 시각 역할 계획 - 숙소 제휴(트랙 A) 템플릿]
# 특정 숙소를 가정하지 않고, 실제 확보된 사진 인벤토리(카테고리별 장수)에 맞춰 씬 역할을 배정합니다.
# 인벤토리에 없는 시설(예: 수영장이 없는 펜션)은 씬 역할로 배정하지 않아 대본이 지어내지 못하게 합니다.
# -------------------------------------------------------------
PHOTO_ROLES = ("view", "pool", "facility", "bedroom")
ROLE_TEMPLATE = ["view", "pool", "pool", "facility", "bedroom"]   # classic 템플릿 (기존 6씬)
ROLE_FALLBACK_ORDER = ["view", "facility", "bedroom", "pool"]
ROLE_KEYWORDS = {
    "view": ["전경", "외관", "뷰", "테라스"],
    "pool": ["수영장", "풀", "자쿠지", "스파"],
    "facility": ["라운지", "로비", "카페", "부대시설"],
    "bedroom": ["객실", "침실", "침구", "거실"],
    "bedroom_or_view": ["객실", "창밖", "마운틴뷰", "오션뷰"],
    "facility_or_night_view": ["불멍", "바베큐", "야경", "노을", "테라스"],
    "view_or_night": ["야경", "전경", "테라스", "뷰"],
}
ROLE_LABELS_KO = {
    "view": "외관/전경/뷰",
    "pool": "수영장/자쿠지/스파",
    "facility": "라운지/로비/카페 등 부대시설",
    "bedroom": "객실/침실",
    "bedroom_or_view": "객실 또는 창밖 마운틴·오션 뷰",
    "facility_or_night_view": "부대시설 또는 노을·야경·테라스 뷰",
    "view_or_night": "야경 또는 전경 (엔딩)",
}
# 비전 게이트(core.scraper)가 사진에 붙이는 태그 중, 각 역할에서 우선 매칭할 태그 (기본값)
ROLE_DEFAULT_TAGS = {
    "view": ["water_view", "mountain_view", "ocean_view", "exterior", "terrace"],
    "pool": ["outdoor_pool", "infinity_pool", "jacuzzi"],
    "facility": ["bbq", "cafe", "firepit", "lounge"],
    "bedroom": ["bed", "room_interior"],
    "bedroom_or_view": ["mountain_view", "ocean_view", "bed"],
    "facility_or_night_view": ["firepit", "night", "sunset", "terrace", "bbq"],
    "view_or_night": ["night", "water_view", "terrace"],
}
USP_ROLE_HINTS = {
    "pool": ["수영장", "풀", "자쿠지", "스파", "노천탕", "pool", "spa"],
    "facility": ["라운지", "로비", "카페", "레스토랑", "바베큐", "바비큐", "정원", "사우나", "불멍"],
}
TRAVEL_MOTIONS = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_down", "pan_up"]
TRAVEL_CTA_VOICEOVER = "숙소 정보는 댓글에 '여행' 남겨주세요!"
TRAVEL_CTA_SUBTITLE = "숙소 정보는 댓글에 '여행' 남겨주세요"

# 7씬 황금 비율 (수영장 3 / 객실 2 / 부대시설 2, 약 20~25초)
#   01~03 물 후킹 → 04~05 머물고 싶은 객실·뷰 → 06~07 시그니처 부대시설 + 엔딩 CTA
GOLDEN7_SLOTS = [
    {"role": "pool", "desc": "야외 온수풀·인피니티 풀 (첫 1초 강력 후킹)", "tags": ["infinity_pool", "outdoor_pool", "water_view"]},
    {"role": "pool", "desc": "수영장 물빛·풀사이드 (후킹 유지)", "tags": ["outdoor_pool", "infinity_pool", "night"]},
    {"role": "pool", "desc": "자쿠지·스파·물멍", "tags": ["jacuzzi", "spa", "indoor_pool", "water_view"]},
    {"role": "bedroom", "desc": "감성 객실 내부·침실", "tags": ["bed", "room_interior", "living_room"]},
    {"role": "bedroom_or_view", "desc": "객실 창밖 마운틴·오션 뷰 (머물고 싶은 욕구)", "tags": ["mountain_view", "ocean_view", "water_view", "bed"]},
    {"role": "facility", "desc": "시그니처 바베큐·카페·불멍 등 독보적 부대시설", "tags": ["bbq", "cafe", "firepit", "lounge"]},
    {"role": "facility_or_night_view", "desc": "불멍·바베큐 부대시설 또는 노을·야경 뷰 + 엔딩 댓글 CTA",
     "tags": ["firepit", "night", "sunset", "terrace", "bbq"]},
]
TRAVEL_TEMPLATES = {
    "golden7": {"name": "🏆 7씬 황금 비율 (수영장3·객실2·부대시설2, 20~25초)", "length": "20~25초", "scenes": len(GOLDEN7_SLOTS)},
    "classic": {"name": "기본 6씬 (뷰·수영장·부대시설·객실, 15~18초)", "length": "15~18초", "scenes": 6},
}
DEFAULT_TRAVEL_TEMPLATE = "golden7"
# 인벤토리 부족 시 대체 순서 (대체되면 대본도 대체 역할 기준으로 작성되어 사진과 어긋나지 않음)
SLOT_FALLBACKS = {
    "pool": ["view", "facility"],
    "bedroom": ["bedroom_or_view", "view"],
    "bedroom_or_view": ["view", "facility"],
    "facility_or_night_view": ["view", "bedroom"],
    "facility": ["view", "bedroom"],
    "view": ["facility", "bedroom"],
}


def _available_roles(photo_inventory: Optional[Dict[str, int]], usp: str) -> set:
    if photo_inventory:
        avail = {r for r in PHOTO_ROLES if int(photo_inventory.get(r, 0) or 0) > 0}
        if avail:
            return avail
    # 인벤토리를 모를 때: 숙소라면 대부분 갖춘 뷰/객실만 가정하고, 그 외 시설은 USP에 명시된 경우만 허용
    avail = {"view", "bedroom"}
    usp_l = (usp or "").lower()
    for role, hints in USP_ROLE_HINTS.items():
        if any(h in usp_l for h in hints):
            avail.add(role)
    return avail


def build_travel_slots(
    num_scenes: Optional[int] = None,
    photo_inventory: Optional[Dict[str, int]] = None,
    usp: str = "",
    template: str = DEFAULT_TRAVEL_TEMPLATE
) -> List[Dict[str, Any]]:
    """
    씬별 슬롯 계획: [{"role", "desc", "tags", "fallback_from"}].
    - golden7: 7씬 황금 비율 고정 (num_scenes 무시). 사진이 부족한 카테고리는 SLOT_FALLBACKS로 대체
    - classic: 기존 역할 순환 템플릿 (num_scenes개)
    """
    if template != "golden7":
        return [{"role": r, "desc": ROLE_LABELS_KO.get(r, r), "tags": ROLE_DEFAULT_TAGS.get(r, []), "fallback_from": None}
                for r in _build_classic_roles(num_scenes or 6, photo_inventory, usp)]

    avail = _available_roles(photo_inventory, usp)
    remaining = None
    if photo_inventory and any(int(photo_inventory.get(r, 0) or 0) > 0 for r in PHOTO_ROLES):
        remaining = {r: int(photo_inventory.get(r, 0) or 0) for r in PHOTO_ROLES}

    def take(role: str) -> bool:
        cats = {"bedroom_or_view": ("bedroom", "view"), "facility_or_night_view": ("facility", "view")}.get(role, (role,))
        for cat in cats:
            if cat not in avail:
                continue
            if remaining is not None:
                if remaining.get(cat, 0) <= 0:
                    continue
                remaining[cat] -= 1
            return True
        return False

    slots = []
    for slot in GOLDEN7_SLOTS:
        role = slot["role"]
        if take(role):
            slots.append({**slot, "fallback_from": None})
            continue
        sub = next((r for r in SLOT_FALLBACKS.get(role, []) if take(r)), None)
        if sub is None:
            # 사진 수보다 씬이 많으면 보유 카테고리를 반복 사용 (매칭 단계에서 서로 다른 사진이 선택됨)
            sub = next((r for r in [role] + SLOT_FALLBACKS.get(role, []) if r in avail or r == "bedroom_or_view"), "view")
        if sub == role:
            slots.append({**slot, "fallback_from": None})
        else:
            slots.append({"role": sub, "desc": f"{ROLE_LABELS_KO.get(sub, sub)} (원래 '{slot['desc']}' 자리, 사진 부족으로 대체)",
                          "tags": ROLE_DEFAULT_TAGS.get(sub, []), "fallback_from": role})
    return slots


def _build_classic_roles(num_scenes: int, photo_inventory: Optional[Dict[str, int]], usp: str) -> List[str]:
    total = max(int(num_scenes or 1), 1)
    avail = _available_roles(photo_inventory, usp)
    remaining = None
    if photo_inventory and any(int(photo_inventory.get(r, 0) or 0) > 0 for r in PHOTO_ROLES):
        remaining = {r: int(photo_inventory.get(r, 0) or 0) for r in PHOTO_ROLES}

    def take(role: str) -> bool:
        if role not in avail:
            return False
        if remaining is not None:
            if remaining.get(role, 0) <= 0:
                return False
            remaining[role] -= 1
        return True

    roles = []
    for i in range(total - 1):
        wanted = ROLE_TEMPLATE[i % len(ROLE_TEMPLATE)]
        if take(wanted):
            roles.append(wanted)
            continue
        fallback = next((r for r in ROLE_FALLBACK_ORDER if take(r)), None)
        roles.append(fallback or next((r for r in ROLE_FALLBACK_ORDER if r in avail), "view"))

    if "view" in avail or "facility" in avail:
        roles.append("view_or_night")
    else:
        roles.append(next((r for r in ROLE_FALLBACK_ORDER if r in avail), "view"))
    return roles[:total]


def build_visual_role_plan(
    num_scenes: int,
    photo_inventory: Optional[Dict[str, int]] = None,
    usp: str = "",
    template: str = "classic"
) -> List[str]:
    """씬별 target_visual_role 목록 (하위 호환용). 슬롯 상세는 build_travel_slots 사용."""
    return [s["role"] for s in build_travel_slots(num_scenes, photo_inventory, usp, template)]


def narrative_role_for(index: int, total: int, visual_role: str) -> str:
    if index == 0:
        return "hook"
    if index == total - 1:
        return "cta"
    return {"bedroom": "room", "bedroom_or_view": "room", "facility": "facility"}.get(visual_role, "feature")


MOCK_TRAVEL_LINES = {
    "info": {
        "hook_title": "여기 진짜 한국 맞아요?",
        "view": "문 열자마자 역대급 뷰가 펼쳐져요!",
        "pool": "물놀이 시작하면 시간이 순삭돼요.",
        "facility": "분위기 좋은 공간에서 여유 한 잔 하기 좋아요.",
        "bedroom": "침구가 푹신해서 꿀잠 예약이에요!",
        "bedroom_or_view": "창밖 풍경 보면서 늦잠 자고 싶어져요.",
        "view_or_night": "밤이 되면 풍경이 더 예뻐져요!",
    },
    "experience": {
        "hook_title": "문 열자마자 입이 떡 벌어짐!",
        "view": "도착하자마자 뷰 보고 입이 떡 벌어졌어요!",
        "pool": "물놀이하다 보니 시간 가는 줄 몰랐어요!",
        "facility": "여기서 커피 마시니까 세상 행복하더라고요!",
        "bedroom": "침구가 너무 좋아서 체크아웃하기 싫었어요!",
        "bedroom_or_view": "창밖 뷰 보다가 하루가 다 갔어요!",
        "view_or_night": "밤 풍경까지 완벽한 하루였어요!",
    },
}
# 황금 비율 슬롯별 Mock 대사 (같은 역할이 연속돼도 대사가 반복되지 않도록 슬롯 위치별로 구분)
MOCK_GOLDEN7_LINES = {
    "info": ["더운 날 물에 뛰어들고 싶을 때, 여기예요.", "풀 뷰 하나로 하루가 정리돼요.", "물멍하다 보면 시간이 순삭돼요.",
             "늘어지고 싶은 주말엔 이 객실이죠.", "창밖 뷰가 그림처럼 펼쳐져요.", "같이 온 사람 모두 만족하는 공간이에요."],
    "experience": ["수영장 보자마자 입이 떡 벌어졌어요!", "풀 뷰 보면서 사진만 백 장 찍었어요!", "물멍하다 보니 시간이 순삭됐어요!",
                   "객실 들어가자마자 감성이 폭발하더라고요!", "창밖 뷰 보다가 늦잠까지 잤어요!", "마지막까지 꽉 찬 하루였어요!"],
}


def generate_mock_travel_storyboard(
    product_name: str,
    usp: str,
    num_scenes: int = 6,
    tone_and_manner: str = "aggro",
    script_style: str = "info",
    photo_inventory: Optional[Dict[str, int]] = None,
    template: str = DEFAULT_TRAVEL_TEMPLATE,
    core_concept: str = "",
) -> List[dict]:
    """
    [숙소 제휴 Mock 콘티 - 1컨셉 1메시지 & Script-First 규격]
    - 선택된 1컨셉(core_concept)이 있는 경우 전체 스토리를 해당 1컨셉 관통으로 전개
    - 시설 단순 나열 배제 및 비전-스토리 맥락화(Contextual Bridging)
    - 숙소 이름 철저 은닉 (Curiosity Gap), 엔딩 댓글 유도 CTA 자동 삽입
    """
    lines = MOCK_TRAVEL_LINES.get(script_style, MOCK_TRAVEL_LINES["info"])
    golden_lines = MOCK_GOLDEN7_LINES.get(script_style, MOCK_GOLDEN7_LINES["info"])
    slots = build_travel_slots(num_scenes, photo_inventory, usp, template)
    total = len(slots)
    concept_clean = (core_concept or "").strip()
    scenes = []
    for i, slot in enumerate(slots):
        v_role = slot["role"]
        is_last = (i == total - 1)
        if is_last:
            text = TRAVEL_CTA_VOICEOVER
        elif concept_clean:
            # 1컨셉 절대 종속 & 비전-스토리 맥락화 (Contextual Bridging) Mock 대사
            if i == 0:
                text = f"{concept_clean} 즐기기 완벽한 비밀 스테이예요!"
            elif v_role in ("pool", "view", "view_or_night"):
                text = f"탁 트인 전망을 감상하며 {concept_clean}의 여유를 만끽할 수 있어요."
            elif v_role in ("bedroom", "bedroom_or_view"):
                text = f"포근한 공간에서 푹 쉬고 일어나 맞이하는 든든한 하루예요."
            elif v_role in ("facility", "lounge"):
                text = f"분위기 좋은 공간에서 펼쳐지는 풍성한 경험을 놓치지 마세요."
            else:
                text = f"{concept_clean}의 매력에 푹 빠져드는 힐링의 시간이에요."
        elif template == "golden7" and not slot["fallback_from"] and i < len(golden_lines):
            text = golden_lines[i]
        else:
            text = lines.get(v_role, lines["view"])
        
        hook_text = ""
        if i == 0:
            if concept_clean:
                hook_text = f"{concept_clean[:10]} 보러 갈래요?" if len(concept_clean) > 5 else f"{concept_clean} 끝판왕?"
            else:
                hook_text = lines["hook_title"]

        scenes.append({
            "scene_index": i + 1,
            "scene_number": i + 1,
            "role": narrative_role_for(i, total, v_role),
            "duration_sec": 2.5,
            "hook_title": hook_text,
            "voiceover": text,
            "subtitle": text,
            "caption": text,
            "target_visual_role": v_role,
            "visual_keywords": list(ROLE_KEYWORDS.get(v_role, ROLE_KEYWORDS["view"])),
            "visual_tags": list(slot["tags"]),
            "visual_description": slot["desc"],
            "image_prompt_en": "",
            "video_motion_prompt_en": TRAVEL_MOTIONS[i % len(TRAVEL_MOTIONS)],
            **({"focus_bottom": True} if i == 0 else {}),
        })
    return scenes


def plan_storyboard(
    reference_video_path: Optional[str] = None,
    product_name: str = "올인데이 하이드라 크림",
    usp: str = "72시간 보습 지속, 100% 무자극 비건 포뮬러",
    num_scenes: Optional[int] = None,
    tone_and_manner: str = "aggro",
    duration_preset: str = "15s",
    audio_mode: str = "voice_and_sub",
    output_plan_path: Optional[str] = None,
    mock: bool = False,
    force: bool = False,
    api_key: Optional[str] = None,
    mode: str = "commerce",
    script_style: str = "info",
    photo_inventory: Optional[Dict[str, int]] = None,
    travel_template: str = DEFAULT_TRAVEL_TEMPLATE,
    slot_photo_notes: Optional[List[Optional[str]]] = None,
    hook_hints: Optional[HookHints] = None,
    core_concept: str = ""
) -> List[dict]:
    """
    Step 1: 레퍼런스 영상 분석 및 씬별 콘티 역기획 실행.
    - 일반 커머스 모드: 숏폼 퍼포먼스 마케팅 디렉터, 씬별 앵글 다변화, 제품 및 제형 매크로 시연
    - 여행 상품 모드: 대한민국 1등 여행/숙소 숏폼 크리에이티브 디렉터, 숙소명 은닉, 시각적 공간감/물멍, 엔딩 댓글 CTA
    """
    if mode == "owner":
        raise ValueError("사장님(owner) 모드는 core.planners.plan_owner_storyboard를 사용하세요 (트랙 A 제휴 플래너와 분리).")
    if mode == "commerce":
        # 예전 커머스 프롬프트는 화장품 전용 예시 + AI 이미지(Imagen) 전제라 어떤 상품이든 화장품 광고처럼 나왔음
        raise ValueError("쇼핑 제휴(commerce) 모드는 core.planners.plan_commerce_storyboard를 사용하세요 (실물 상품 사진 기반).")
    preset_info = DURATION_PRESETS.get(duration_preset, DURATION_PRESETS["15s"])
    target_total_sec = preset_info["target_sec"]
    if mode == "travel" and travel_template == "golden7":
        num_scenes = len(GOLDEN7_SLOTS)   # 황금 비율은 7씬 고정
    elif num_scenes is None or num_scenes <= 0:
        num_scenes = TRAVEL_TEMPLATES["classic"]["scenes"] if mode == "travel" else preset_info["default_scenes"]

    tone_info = TONE_PRESETS.get(tone_and_manner, TONE_PRESETS["aggro"])
    # 여행 모드: 실제 사진 인벤토리 기반 씬별 시각 역할 계획 (특정 숙소 가정 없음)
    slot_plan = build_travel_slots(num_scenes, photo_inventory, usp, travel_template) if mode == "travel" else []
    role_plan = [sl["role"] for sl in slot_plan]
    target_length = TRAVEL_TEMPLATES.get(travel_template, TRAVEL_TEMPLATES["classic"])["length"]
    logger.info(
        f"Step 1 시작 (모드={mode}): 제품명/여행지='{product_name}', USP='{usp}', 코어컨셉='{core_concept}', 목표 씬={num_scenes}, "
        f"톤앤매너='{tone_info['name']}', 길이='{preset_info['name']}', 오디오='{audio_mode}', force={force}"
    )
    
    if force and output_plan_path and os.path.exists(output_plan_path):
        logger.info(f"기존 기획 파일 '{output_plan_path}' 삭제 후 재생성 진행.")
        os.remove(output_plan_path)

    if not force and output_plan_path and os.path.exists(output_plan_path) and os.path.getsize(output_plan_path) > 0 and not mock:
        try:
            with open(output_plan_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
            if isinstance(existing_data, list) and len(existing_data) == num_scenes:
                logger.info(f"기존 기획 파일 감지 ({len(existing_data)}개 씬 일치). 기존 기획을 유지합니다.")
                return existing_data
            else:
                logger.info(f"기존 기획 씬 수가 요청 씬 수({num_scenes})와 달라 재생성을 진행합니다.")
                if os.path.exists(output_plan_path):
                    os.remove(output_plan_path)
        except Exception as e:
            logger.warning(f"기존 기획 파일 로드 실패: {e}. 재생성을 진행합니다.")
            if os.path.exists(output_plan_path):
                os.remove(output_plan_path)

    if mock or not api_key:
        logger.info(f"[Mock Mode] Gemini API 호출을 건너뛰고 모의 역기획 콘티를 생성합니다. (모드={mode}, 스타일={script_style})")
        if mode == "travel":
            scenes = generate_mock_travel_storyboard(
                product_name=product_name,
                usp=usp,
                num_scenes=num_scenes,
                tone_and_manner=tone_and_manner,
                script_style=script_style,
                photo_inventory=photo_inventory,
                template=travel_template,
                core_concept=core_concept,
            )
        else:
            scenes = generate_mock_storyboard(
                product_name=product_name,
                usp=usp,
                num_scenes=num_scenes,
                tone_and_manner=tone_and_manner
            )
        if audio_mode == "silent":
            per_scene_dur = round(target_total_sec / len(scenes), 1)
            for sc in scenes:
                sc["duration_sec"] = per_scene_dur

        if output_plan_path:
            with open(output_plan_path, "w", encoding="utf-8") as f:
                json.dump(scenes, f, ensure_ascii=False, indent=2)
        logger.info(f"Mock 콘티가 '{output_plan_path}'에 저장되었습니다 ({len(scenes)}개 씬).")
        return scenes

    client = genai.Client(api_key=api_key)

    uploaded_file = None
    if reference_video_path and os.path.exists(reference_video_path):
        logger.info(f"레퍼런스 비디오 업로드 중: {reference_video_path}")
        uploaded_file = client.files.upload(file=reference_video_path)
        logger.info(f"비디오 업로드 완료 (URI: {uploaded_file.uri}). 처리 대기 중...")
        
        while uploaded_file.state == "PROCESSING":
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)
            
        if uploaded_file.state != "ACTIVE":
            raise RuntimeError(f"레퍼런스 영상 처리 실패: {uploaded_file.state}")
        logger.info("레퍼런스 영상 처리 완료 (ACTIVE 상태).")
    else:
        logger.warning("레퍼런스 영상 파일이 없거나 찾을 수 없어 텍스트 기반 광고 역기획을 수행합니다.")

    # -------------------------------------------------------------
    # 모드별 시스템 프롬프트 및 사용자 프롬프트 분기
    # -------------------------------------------------------------
    if mode == "travel":
        style_preset = SCRIPT_STYLE_PRESETS.get(script_style, SCRIPT_STYLE_PRESETS["info"])
        style_instruction = style_preset["instruction"]

        concept_clean = (core_concept or "").strip()
        concept_rule = ""
        if concept_clean:
            concept_rule = (
                f"[★ 확정된 단 하나의 코어 컨셉 (Core Concept) - 100% 절대 관통]:\n"
                f"   - 이번 30초 숏폼 전체를 관통할 단 하나의 핵심 혜택/메시지: '{concept_clean}'\n"
                f"   - 씬 1의 훅 헤드라인(hook_title)부터 본문 전체와 마지막 CTA 직전까지 오직 이 '단 하나의 1컨셉'만을 집중 관통하세요!\n"
                f"   - [단순 시설 나열 절대 금지]: 수영장 나왔다고 수영장 얘기, 침대 나왔다고 침대 얘기 등 시설을 백화점식으로 나열하는 것은 엄격히 금지됩니다.\n"
                f"   - [비전-스토리 맥락화 (Contextual Bridging)]: 사진 속 공간과 풍경이 무엇이든, "
                f"그 공간을 이 코어 컨셉('{concept_clean}')을 누리고 증명하는 감성적 무대와 배경으로 자연스럽게 맥락화하세요.\n"
                f"     * 풍경/뷰 컷 ➔ 탁 트인 시야와 함께 온전히 누리는 감성적 여유\n"
                f"     * 객실/실내 컷 ➔ 안락한 공간 속에서 편안하게 마주하는 힐링의 순간\n"
                f"     * 특화/공용 공간 컷 ➔ 머무는 시간의 가치를 더해주는 특별한 경험\n"
            )

        system_instruction = (
            "당신은 인스타그램 릴스, 틱톡, 유튜브 쇼츠에서 수백만 조회수와 폭발적인 댓글 참여율을 이끌어내는 "
            "'대한민국 1등 여행/숙소 숏폼 크리에이티브 디렉터'입니다.\n\n"
            "\"쇼츠는 광고다. 광고는 1컨셉 1메시지다.\"\n"
            "당신의 미션은 시청자가 1초 만에 시선을 빼앗기고, 영상이 끝날 때까지 단 하나의 킬러 혜택에 몰입하며, 댓글에 정보를 묻지 않고는 못 배기게 만드는 숏폼 여행 콘텐츠를 기획하는 것입니다.\n"
            "다음 핵심 원칙을 철저히 준수해야 합니다:\n"
            + (f"0. {concept_rule}\n" if concept_rule else "")
            + f"1. [선택된 대본 기획 스타일 준수 ({style_preset['name']})]:\n"
            f"   {style_instruction}\n"
            f"2. [영상 씬 듀레이션 {TRAVEL_POLICY.min_scene_sec}~{TRAVEL_POLICY.max_scene_sec}초 & 씬당 대본 공백 포함 {TRAVEL_POLICY.min_chars}~{TRAVEL_POLICY.max_chars}자]:\n"
            f"   - 각 씬의 voiceover는 공백 포함 {TRAVEL_POLICY.min_chars}~{TRAVEL_POLICY.max_chars}자의 감성 구어체로 쓰세요. 절대 {TRAVEL_POLICY.max_chars}자를 초과하지 마세요.\n"
            "   - 먼저 영상 전체 대본(story)을 유튜버가 시청자 한 명에게 썰 푸는 독백 하나로 쓰고, 그것을 순서대로 잘라 씬별 voiceover로 나누세요. "
            "팜플렛 낭독체·홍보 어미·개조식 단문은 전면 금지입니다 (아래 [대본 톤] 참조).\n"
            "3. [대본과 실제 사진 에셋의 정합성 & 비전-스토리 맥락화 (Contextual Bridging)]:\n"
            "   - 각 씬은 사용자 프롬프트에 배정된 시각 역할의 '실제 숙소 사진'과 함께 송출됩니다.\n"
            "   - 그 씬 대사는 화면 속 공간을 '코어 컨셉의 맥락'으로 풀어내는 감각적인 문장이어야 합니다(그 공간에서 코어 컨셉을 어떻게 누리는지, 어떤 기분인지). 사진에 보이는 걸 기계적으로 나열해 읽지 마세요.\n"
            "   - 시설 단순 설명('수영장이 있어요', '침대가 푹신해요')을 멈추고, 선택된 1컨셉의 가치를 증명하는 배경으로 사진을 활용하세요.\n"
            "4. [정보 은닉(Curiosity Gap) 절대 원칙 - 숙소명 직접 언급 엄격 금지]:\n"
            f"   - 크롤링된 실제 숙소명/상호명('{product_name}')을 대본 나레이션(voiceover) 및 자막(caption) 어디에도 직접 언급하는 것을 엄격히 금지합니다!\n"
            "   - 숙소명 대신 '문 열자마자 감탄 나오는 뷰', '나만 알고 싶은 비밀 숙소', '귀빈들만 찾던 비밀 스테이' 등 시각적 공간감과 경험 중심의 표현으로 100% 치환하세요.\n"
            "5. [엔딩 CTA 필수 규격 강제]:\n"
            f"   - 마지막 엔딩 씬(Scene {num_scenes})의 나레이션과 자막은 반드시 아래 지정된 문구를 글자 하나 틀리지 않고 그대로 작성해야 합니다:\n"
            "     * voiceover: \"숙소 정보는 댓글에 '여행' 남겨주세요!\"\n"
            "     * caption/subtitle: \"숙소 정보는 댓글에 '여행' 남겨주세요\"\n"
            "6. [2D Ken Burns 풍경 카메라 모션 교차 배정]:\n"
            "   - 각 씬마다 부드러운 줌인, 줌아웃, 상하/좌우 패닝을 교차 배정하세요. (zoom_in, pan_right, zoom_out, pan_left, pan_down, pan_up)\n\n"
            + COPY_TONE_RULES
        )

        if photo_inventory and any(int(photo_inventory.get(r, 0) or 0) > 0 for r in PHOTO_ROLES):
            inventory_text = ", ".join(f"{ROLE_LABELS_KO[r]} {int(photo_inventory.get(r, 0) or 0)}장" for r in PHOTO_ROLES)
        else:
            inventory_text = "사진 분류 정보 없음 (특장점(USP)에 명시된 시설만 언급 가능)"
        notes = list(slot_photo_notes or [])
        hook_hint_block = ("   " + hook_hints.prompt_block.replace("\n", "\n   ")) if hook_hints else ""
        scene_role_lines = "\n".join(
            f"     * 씬 {i + 1} ({narrative_role_for(i, len(slot_plan), sl['role'])}): target_visual_role='{sl['role']}' [{sl['desc']}], "
            f"visual_keywords={json.dumps(ROLE_KEYWORDS.get(sl['role'], []), ensure_ascii=False)}"
            + (f"\n         → 이 씬에 실제로 나갈 사진: {notes[i]}" if i < len(notes) and notes[i] else "")
            for i, sl in enumerate(slot_plan)
        )
        last_i = len(role_plan) - 1
        schema_example = json.dumps({
            "title": "<숙소명이 드러나지 않는 영상 제목>",
            "story": f"<씬 1~{last_i} voiceover를 순서대로 이어 붙인 전체 독백 (유튜버 썰 풀기 한 단락)>",
            "scenes": [
                {
                    "scene_index": i + 1,
                    "role": narrative_role_for(i, len(role_plan), r),
                    "voiceover": TRAVEL_CTA_VOICEOVER if i == last_i else (
                        f"<story의 첫 문장: 헤드라인을 받아 썰을 시작 ({slot_plan[i]['desc']} 화면), 해요체 종결 어미로 끝나는 완결 문장 15~{TRAVEL_POLICY.max_chars}자>" if i == 0 else
                        f"<story의 {i + 1}번째 문장: 앞 문장을 이음말로 받아 이어감 ({slot_plan[i]['desc']} 화면), 해요체 종결 어미로 끝나는 완결 문장 15~{TRAVEL_POLICY.max_chars}자>"),
                    "subtitle": TRAVEL_CTA_SUBTITLE if i == last_i else "<voiceover와 동일>",
                    "target_visual_role": r,
                    "visual_keywords": ROLE_KEYWORDS.get(r, []),
                    "hook_title": "<8~12자 후킹 헤드라인>" if i == 0 else "",
                    "duration_sec": TRAVEL_POLICY.default_scene_sec,
                    "video_motion_prompt_en": TRAVEL_MOTIONS[i % len(TRAVEL_MOTIONS)],
                }
                for i, r in enumerate(role_plan)
            ],
        }, ensure_ascii=False, indent=2)

        concept_prompt_block = ""
        if concept_clean:
            concept_prompt_block = f"""
[★ 확정된 단 하나의 코어 컨셉 (Core Concept) - 100% 절대 관통]
이번 영상은 아래 단 하나의 핵심 혜택/가치만을 30초 내내 증명하고 관통해야 합니다:
👉 확정 코어 컨셉: "{concept_clean}"
- 씬 1의 훅(hook_title)부터 본문 씬들, 마지막 CTA 직전까지 모든 대사는 오직 이 1컨셉으로 수렴해야 합니다!
- [단순 시설 나열 절대 금지]: 수영장, 침대, 라운지 등 시설을 백화점식으로 나열하지 마세요.
- [비전-스토리 맥락화 (Contextual Bridging)]: 사진 속 공간들을 이 코어 컨셉을 경험하는 감성적 무대와 배경으로 자연스럽게 맥락화하세요!
  (각 사진 공간의 분위기를 살려, 그곳에서 코어 컨셉의 핵심 가치를 어떻게 체감하는지 자연스럽게 연결하세요)
"""

        prompt = f"""
[여행/숙소 콘텐츠 타깃 정보]
- 여행/숙소 상품명(대본 내 직접 언급 절대 금지 대상): {product_name}
- 숙소 및 여행지 특장점/무드: {usp}
- 확보된 실제 사진 구성: {inventory_text}
- 선택된 대본 기획 스타일: {style_preset['name']}
  * 스타일 지침:
{style_instruction}
- 선택된 톤앤매너: {tone_info['name']}
  * 지침: {tone_info['instruction']}
- 목표 영상 길이: 약 {target_length} (총 {num_scenes}개 씬, 템플릿: {TRAVEL_TEMPLATES.get(travel_template, TRAVEL_TEMPLATES['classic'])['name']})
- 목표 씬 수: 총 {num_scenes}개 씬 (대본 선기획 -> 씬별 타겟 에셋 매칭용)
- 포맷: 9:16 세로형 풀스크린 숏폼 영상 (TikTok, Reels, Shorts)
- 오디오 모드: {audio_mode}

{concept_prompt_block}

[대본 선기획 작성 요구사항]
1. 반드시 아래 지정된 JSON 오브젝트 포맷({{"title": "...", "story": "...", "scenes": [...]}})으로 정확히 출력하세요.
2. [대본 = 3자 큐레이터의 25~30초 숏폼 하나를 씬별로 자른 것]:
   - 먼저 'story'에 씬 1~{last_i}에 해당하는 전체 대사를 쓰세요. 큐레이터가 '이런 순간엔 여기'를 짚어 주는 구어체입니다.
   - story는 정확히 {last_i}개의 완결 문장으로 쓰고, 문장 하나씩 순서대로 씬 1~{last_i}의 voiceover에 넣으세요 (각 공백 포함 15~{VO_LIMIT}자).
     voiceover를 이어 붙이면 story와 같아야 하고, 각 voiceover는 해요체 완결 문장(~예요, ~어요, ~죠, ~네요, ~까요?)이어야 합니다.
     문장 중간('~할 수', '~는데,', '~좋고,')에서 씬을 끊지 마세요.
   - 시설을 순서대로 나열하지 말고, 씬마다 방문 상황(TPO: 언제·누구와·어떤 기분일 때)을 하나씩 그리고 그 씬 화면 공간이 왜 답인지 말하세요.
   - 어미: 해요체 평서문을 기본으로 하고, 질문형·감탄형은 내용상 어울릴 때만 쓰세요(문장 모양을 맞추려 억지로 의문문을 만들지 말 것).
   - 구조만 참고하고 문구는 이 숙소 정보로 새로 쓰세요: [시청자 상황·기분 짚기] → [그 상황에 맞는 구체적 공간 하나] → [그 공간의 순간 감각].
     예시 문장을 그대로 베끼거나 어느 숙소에나 붙는 일반 문장을 쓰지 마세요.
   - 판에 박힌 광고 상투구 금지: '~의 시간이에요'(힐링의 시간), '풍경이 예술이죠', '특별한 ~ 경험이 기다리고 있어요', '잊지 못할 추억'.
   - 엄격 금지: '~거든요', '~하거든요', '~고요', '~되고요' 어미, '솔직히', '근데'. 지어낸 체험담('제가 가 봤는데', '먹어 보니').
   - 금지: 사진 낭독체('~가 보여요', '~도 있어서', '고급스러운·세련된 ~'), '~해보세요/~마세요/~딱!' 홍보 어미, '~가능함/~보임/~됨/~임' 개조식,
     '~입니다/~습니다/~아닙니다' 합쇼체.
   - 숙소는 끝까지 '숙소'입니다: 머무는 곳을 식당·카페·레스토랑에 빗대지 마세요 ('머무는 곳이 곧 식당이에요' 금지).
     조식·식사가 컨셉이어도 '숙소에서 즐기는 식사'로 말하고, 식당처럼 광고하지 마세요.
   - 한 문장에는 조건('~면')을 하나만 쓰세요. '배고프면 방 문만 나서면 ~'처럼 겹치면 누가 어디서 무엇을 하는지 흐려집니다.
     주어와 동선이 분명한 짧은 문장으로 쓰세요 ('아침엔 방에서 나와 바로 식탁에 앉아요').
   - 같은 핵심 명사(식탁·풍경 등)는 최대 2씬까지만 쓰고, 씬마다 다른 구체적 대상(공간·감각·시간대)을 말하세요.
   - 입력한 특장점·사진에 없는 시설, 메뉴, 서비스, 거리·시간을 지어내지 마세요 (라운지·루프탑·조식 뷔페 등).
3. [씬별 시각 역할 맥락화 (Contextual Bridging)]:
{scene_role_lines}
   - 각 씬의 voiceover는 그 씬 화면에 나오는 공간을 코어 컨셉('{concept_clean or "특별한 혜택"}')과 연결하여 이야기해야 합니다 (화면과 엉뚱한 동떨어진 멘트 금지, 사진 나열 낭독도 금지).
   - [비전-스토리 맥락화]: 사진에 특정 주제(음식, 뷔페, 조식 등)가 직접 없더라도, 그 공간(호수, 테라스, 라운지, 객실 등)을 코어 컨셉을 만끽하는 감각적인 배경으로 맥락화하세요.
4. [정보 은닉 (Curiosity Gap) 강제]:
   - 크롤링된 숙소명('{product_name}')을 대본 나레이션 및 자막에 직접 언급하는 것을 엄격히 금지합니다.
5. 씬 1 (Hook): 반드시 'hook_title'에 스크롤을 즉시 멈추게 하는 강력한 헤드라인(공백 포함 8~12자, 읽는 데 2초 이내)을 포함하세요!
   - 코어 컨셉('{concept_clean or "특별한 매력"}')에 직결되는 강렬한 질문이나 호기심을 유발하는 헤드라인으로 작성하세요.
     (예: 조식 뷔페 컨셉인 경우: "호수 뷰 보며 먹는 조식?", "아침부터 힐링 뷔페?")
   - 헤드라인은 첫 1.5~2초 동안 단독으로 읽히고 보이므로 짧고 강해야 합니다.
   - 씬 1은 나레이터가 hook_title을 먼저 읽고(이때 화면엔 헤드라인만), 이어서 voiceover를 읽습니다(이때 화면엔 voiceover 자막만).
     voiceover는 헤드라인을 받아 코어 컨셉의 이야기를 시작하는 첫 문장으로 쓰고, 헤드라인과 무관한 상투 경고('모르면 평생 손해')는 쓰지 마세요.
   - 씬 1 voiceover에서 헤드라인 단어를 앵무새처럼 반복하지 마세요. 헤드라인이 던진 말을 바로 구체적인 감탄·이유로 받으세요.
{hook_hint_block}
6. 마지막 씬 {num_scenes} (엔딩 CTA 강제):
   - 마지막 씬 voiceover는 반드시 정확히: "{TRAVEL_CTA_VOICEOVER}"
   - 마지막 씬 subtitle은 반드시 정확히: "{TRAVEL_CTA_SUBTITLE}"
7. 'video_motion_prompt_en'은 2D Ken Burns 카메라 모션(zoom_in, pan_right, zoom_out, pan_left, pan_down, pan_up)을 교차 지정하세요.

[JSON 출력 스키마 규격 (꺾쇠 <> 부분을 실제 내용으로 채우세요)]
{schema_example}
"""
    else:
        # 일반 커머스 모드
        system_instruction = (
            "당신은 숏폼(틱톡, 릴스, 유튜브 쇼츠)에서 스크롤을 즉시 멈추게 만들고 100만 뷰와 압도적인 전환율(CVR)을 이끌어내는 "
            "'최고의 숏폼 퍼포먼스 마케팅 디렉터(Short-form Performance Marketing Director)'입니다.\n\n"
            "당신의 미션은 시청자가 0~3초 만에 이탈하지 않고 구매 욕구를 느끼도록 기획하는 것이며, 다음 4대 핵심 원칙을 철저히 준수해야 합니다:\n"
            "1. [씬 1 초강력 후킹 헤드라인 (hook_title) 필수]:\n"
            "   - 씬 1에는 시청자의 손가락을 즉시 멈추게(Thumb-stopping) 만드는 10~15자 내외의 강렬한 상단 헤드라인 카피를 무조건 작성하세요.\n"
            "   - 예시: '아직도 건조함 참으세요?', '피부과 원장도 놀란 보습의 비밀', '3초 만에 끝내는 수분 충전', '모공 고민? 딱 1분만 보세요'\n"
            "   - (씬 2 이후는 필요 없으면 빈 문자열 \"\" 또는 생략 가능)\n"
            "2. [완벽한 한국어 구어체 & TTS 발음 친화 규칙 준수]:\n"
            "   - 모든 나레이션 대사('voiceover')는 실제 한국인이 일상에서 친근하고 감각적으로 듣는 자연스러운 구어체로 작성하세요.\n"
            "   - [특수문자/이모지 절대 금지]: 나레이션 대사에는 물결표(~), 빗금(/), 샵(#), 별표(*) 등 TTS가 문자 그대로 읽어버리는 특수문자나 이모지를 절대로 포함하지 마세요. (쉼표, 마침표, 느낌표, 물음표만 허용)\n"
            "   - [숫자의 한글 발음 표기]: 나레이션 대사의 숫자는 고유어가 아닌 세련된 한자어 발음(예: 48시간 -> '사십팔 시간', 24시간 -> '이십사 시간', 3초 -> '삼 초', 100% -> '백 퍼센트')을 기준으로 소리 나는 대로 작성하세요.\n"
            "   - 기계 번역투, 딱딱한 문어체, 맞춤법 오류, 어색한 어휘를 엄격히 금지합니다.\n"
            "3. [제품명과 USP의 자연스러운 문법 결합]:\n"
            "   - 제품명과 핵심 소구점(USP)에 적절한 한국어 조사(은/는, 이/가, 을/를)를 문법에 정확하게 결합하여 매끄럽게 전달하세요.\n"
            "4. [화면 표출 핵심 자막 (caption)]:\n"
            "   - 숏폼 UI 세이프존(하단 78~80% 지점)에 배치될 15자 내외의 임팩트 있는 핵심 자막을 매 씬마다 별도로 제공하세요.\n"
            "   - 자막('caption')은 모바일 가독성을 위해 아라비아 숫자(예: '48시간', '3초')를 직관적으로 유지하세요.\n"
            "5. [프로페셔널 광고 문법: 씬별 카메라 앵글 다변화 및 헤어/외모 일관성]:\n"
            "   - 모든 씬에 모델 전면 얼굴이 연속으로 나오면 지루하고 AI 인물 불일치가 두드러지므로, 전문 뷰티/커머스 광고 문법에 따라 씬별 앵글을 철저히 다변화하세요:\n"
            "     * 씬 1 (Hook): 피부 고민을 겪는 모델 얼굴 클로즈업. 헤어스타일과 인물은 반드시 'the same 20s Korean female model with clear glass skin and medium brown shoulder-length hair'로 통일하세요.\n"
            "     * 씬 2 (Demonstration/USP): [중요: 전면 얼굴 노출 금지] 모델 얼굴 대신 손등이나 뺨에 맑은 앰플 제형이 떨어져 흡수되는 초근접 매크로 시연 컷으로 연출하세요.\n"
            "     * 씬 3 이후 (CTA/Result): 촉촉해진 피부 광채 부위 클로즈업 또는 제품 단독 패키지/포디움 컷으로 구성하세요.\n"
            "   - 모델 얼굴이 등장할 때는 반드시 동일한 헤어스타일과 20대 한국 여성 모델 프로필을 통일하세요."
        )

        prompt = f"""
[광고 타깃 정보]
- 홍보 제품명: {product_name}
- 핵심 소구점(USP): {usp}
- 선택된 톤앤매너: {tone_info['name']}
  * 지침: {tone_info['instruction']}
- 목표 영상 길이: 약 {target_total_sec}초 ({preset_info['name']})
- 목표 씬 수: 총 {num_scenes}개 씬
- 포맷: 9:16 세로형 숏폼 영상 (TikTok, Reels, Shorts)
- 오디오 모드: {audio_mode}

[작성 요구사항]
1. 총 {num_scenes}개의 씬으로 구성된 JSON 배열을 생성하세요.
2. 선택된 톤앤매너('{tone_info['name']}')의 분위기와 호흡을 나레이션(voiceover)과 자막(caption)에 완벽히 살려 작성하세요.
3. 씬 1 (Hook): 반드시 'hook_title'에 스크롤을 즉시 멈추게 하는 강력한 헤드라인(10~15자)을 포함하세요!
4. 씬 2 (Demonstration): [프로페셔널 앵글 분할: 전면 얼굴 금지] 매크로 제형 시연 컷으로 기획하세요.
5. 씬 3 이후 (CTA): 지금 바로 사야 하는 명확한 이유 제시 및 촉촉해진 피부 광채 클로즈업 또는 제품 패키지 연출.
6. 'caption' 및 'subtitle' 필드는 시청자 몰입을 위해 반드시 'voiceover'(음성 대본)와 토씨 하나 틀리지 않고 100% 동일한 텍스트로 작성하세요.
7. 'image_prompt_en'은 Google Imagen 3에 최적화된 9:16 세로형 포토리얼리스틱 상업 광고 영문 프롬프트로 작성하세요.
8. 'video_motion_prompt_en'은 로컬 2D Ken Burns 카메라 모션 연출(줌인/줌아웃/팬)을 제어할 영문 프롬프트로 작성하세요.

JSON 출력 스키마 예시:
[
  {{
    "scene_number": 1,
    "duration_sec": 2.5,
    "hook_title": "아직도 건조함 참으세요?",
    "voiceover": "매일 피부가 땅기고 화장도 뜨나요? 이제 걱정 끝입니다!",
    "caption": "건조한 피부, 아직도 방치 중?",
    "visual_description": "건조함으로 고민하는 여성 모델의 클로즈업",
    "image_prompt_en": "Vertical 9:16 commercial beauty shot featuring the same 20s Korean female model with clear glass skin and medium brown shoulder-length hair, looking concerned about dry skin, luxury cosmetic...",
    "video_motion_prompt_en": "Dramatic camera push-in..."
  }},
  {{
    "scene_number": 2,
    "duration_sec": 3.0,
    "hook_title": "",
    "voiceover": "바르는 즉시 속당김 해결! 사십팔 시간 동안 촉촉함이 지속됩니다.",
    "caption": "48시간 촉촉! 속당김 끝",
    "visual_description": "매끄러운 피부 위에 투명한 앰플 제형이 방울져 떨어지는 초근접 매크로 시연",
    "image_prompt_en": "Vertical 9:16 macro close-up shot of clear ampoule texture on smooth glowing skin, delicate droplet falling on skin, rich viscosity, no full face, professional luxury studio lighting",
    "video_motion_prompt_en": "Smooth fluid motion showing dynamic droplet spread..."
  }}
]
"""

    contents = []
    if uploaded_file:
        contents.append(uploaded_file)
    contents.append(prompt)

    # 대본 엔진: Claude → OpenAI → Gemini 자동 백업 (core/llm/router.py). 레퍼런스 영상(Gemini 업로드 파일)이 있으면 Gemini만
    from core.llm import generate_json, repair_scene_lines
    from core.script_guard import find_banned_style
    data, script_engine = generate_json(contents, system_instruction, temperature=0.7, gemini_api_key=api_key, purpose="숙소 대본")
    _t_scenes = (data.get("scenes") if isinstance(data, dict) else data) or []
    _t_lines = [str(x.get("voiceover") or "") for x in _t_scenes[:-1] if isinstance(x, dict)]
    _t_hook = str((_t_scenes[0] or {}).get("hook_title") or "") if _t_scenes and isinstance(_t_scenes[0], dict) else ""
    from core.script_guard import script_violation_finder as _svf
    travel_violation_checker = create_length_violation_checker(
        TRAVEL_POLICY, _svf(_t_lines, core_concept=(core_concept or "").strip(), hook=_t_hook))
    data, fixes = repair_scene_lines(data, travel_violation_checker, system_instruction, TRAVEL_POLICY.repair_instruction, gemini_api_key=api_key)
    for f in fixes:
        logger.info(f"[travel 대본 규칙 보정] {f}")
    response = type("LLMText", (), {"text": json.dumps(data, ensure_ascii=False)})()

    try:
        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()
        
        parsed_json = json.loads(raw_text)
        
        story = ""
        if isinstance(parsed_json, dict) and "scenes" in parsed_json:
            scenes = parsed_json["scenes"]
            story = str(parsed_json.get("story") or "").strip()
        elif isinstance(parsed_json, list):
            scenes = parsed_json
        else:
            raise ValueError(f"예상치 못한 JSON 구조: {parsed_json}")

        # 무음 모드일 경우 목표 총 시간에 맞춰 균등 분배
        silent_per_scene = round(target_total_sec / len(scenes), 2) if scenes else 3.0

        for idx, sc in enumerate(scenes):
            sc["scene_index"] = idx + 1
            sc["scene_number"] = idx + 1
            sc["script_engine"] = script_engine

            # [음성 대본(voiceover)과 자막(subtitle/caption) 100% 일치 강제]
            # 시청자 몰입을 위해 음성과 화면 자막은 토씨 하나 틀리지 않고 100% 동일한 텍스트로 동기화
            vo_text = (sc.get("voiceover") or sc.get("subtitle") or sc.get("caption") or "").strip()
            sc["voiceover"] = vo_text
            sc["subtitle"] = vo_text
            sc["caption"] = vo_text

            if mode == "travel":
                # 여행 상품 모드: 사진 인벤토리 기반 시각 역할 강제 (씬 수가 계획과 다르면 재계산)
                if len(slot_plan) != len(scenes):
                    slot_plan = build_travel_slots(len(scenes), photo_inventory, usp,
                                                   travel_template if len(scenes) == len(GOLDEN7_SLOTS) else "classic")
                v_role = slot_plan[idx]["role"]
                sc["target_visual_role"] = v_role
                sc["visual_keywords"] = list(ROLE_KEYWORDS.get(v_role, ROLE_KEYWORDS["view"]))
                sc["visual_tags"] = list(slot_plan[idx]["tags"])
                sc["visual_description"] = slot_plan[idx]["desc"]
                sc["role"] = narrative_role_for(idx, len(scenes), v_role)

            if audio_mode == "silent":
                sc["duration_sec"] = silent_per_scene
            elif "duration_sec" not in sc:
                sc["duration_sec"] = 2.5
            
            # 씬 1 초강력 후킹 헤드라인 보장
            if idx == 0 and not sc.get("hook_title"):
                sc["hook_title"] = "여기 진짜 한국 맞아요?" if mode == "travel" else (sc.get("caption") or f"아직도 {product_name} 고민하세요?")
            elif "hook_title" not in sc:
                sc["hook_title"] = ""

            # 2D Ken Burns 카메라 모션 보장
            if mode == "travel":
                if not sc.get("video_motion_prompt_en"):
                    sc["video_motion_prompt_en"] = TRAVEL_MOTIONS[idx % len(TRAVEL_MOTIONS)]

        # 이야기형 대본 검토용: Gemini가 먼저 쓴 전체 독백을 씬 1에 보관 (씬별 대사는 이를 잘라 쓴 것)
        if story and scenes:
            scenes[0]["story"] = story
            logger.info(f"[대본 전체 독백] {story}")

        # 여행 상품 모드: 숙소명 철저 은닉(Curiosity Gap), 뷔페/조식 허위 차단, 15초 규격 초압축, 엔딩 CTA 고정
        if mode == "travel" and scenes:
            # 1. 숙소명 은닉 후처리 가드레일 (대본/자막 내 숙소 상호명 직접 노출 시 자동 감지 및 치환)
            forbidden_tokens = set()
            if product_name and product_name.strip():
                raw_name = product_name.strip()
                forbidden_tokens.add(raw_name)
                generic_words = {"호텔", "리조트", "펜션", "풀빌라", "숙소", "모텔", "게스트하우스", "스테이"}
                for word in raw_name.split():
                    word_clean = word.strip()
                    if len(word_clean) >= 2 and word_clean not in generic_words:
                        forbidden_tokens.add(word_clean)

            # 2. 근거 없는 주장 차단: 그 씬에 실제 배정된 사진 설명 또는 USP에 근거가 없는 시설·속성·가격 표현은
            #    사진 중립 문구로 교체 (예: 실내 수영장 사진에 '온수', 근거 없는 '침대 추가')
            photo_notes = list(slot_photo_notes or [])
            replaced = 0

            for idx, sc in enumerate(scenes):
                vo = sc.get("voiceover", "")
                cap = sc.get("caption", "")

                for token in forbidden_tokens:
                    if token and token in vo:
                        vo = vo.replace(token, "비밀 힐링 스팟")
                    if token and token in cap:
                        cap = cap.replace(token, "비밀 힐링 스팟")

                note = photo_notes[idx] if idx < len(photo_notes) else None
                if idx < len(scenes) - 1:
                    # 말투 통일: 합쇼체(~입니다/~습니다) → 해요체 (내용·흐름은 유지)
                    haeyo = to_haeyo(vo)
                    if haeyo != vo:
                        logger.info(f"[해요체 통일] 씬 {idx + 1}: '{vo}' → '{haeyo}'")
                        vo = haeyo
                    # 이야기형 대본은 다음 씬 공간을 미리 언급하기도 하므로(예: '날씨 쌀쌀할 땐 실내 풀로'), 시설 근거는 이 영상의 전체 사진 + USP 기준
                    claim = find_unsupported_claim(vo, photo_notes or [note], usp)
                    if claim:
                        logger.info(f"[근거 없는 주장 차단] 씬 {idx + 1}: '{claim}' (대사: {vo}) → 사진 중립 문구로 교체")
                        vo = safe_line_for(sc.get("target_visual_role", ""), note, replaced)
                        replaced += 1
                    # 화면 무관 주제 난입: 숙소 정보에 있어도 이 씬(과 이어받는 다음 씬) 사진에 없으면 교체 (예: 침실 사진에 '댕댕이 용품')
                    next_note = photo_notes[idx + 1] if idx + 1 < len(photo_notes) else None
                    off = find_off_scene_topic(vo, [note, next_note])
                    if off:
                        logger.info(f"[화면 무관 주제 차단] 씬 {idx + 1}: '{off}' (대사: {vo}, 사진: {note}) → 사진 중립 문구로 교체")
                        vo = safe_line_for(sc.get("target_visual_role", ""), note, replaced)
                        replaced += 1
                    hook = to_haeyo(sc.get("hook_title") or "")      # 훅도 합쇼체 금지
                    sc["hook_title"] = hook
                    if idx == 0 and hook and find_unsupported_claim(hook, [note], usp):
                        sc["hook_title"] = "여기 진짜 한국 맞아요?"
                    promo = find_promo_tone(vo)
                    if promo:
                        logger.info(f"[홍보 어투 차단] 씬 {idx + 1}: {promo} (대사: {vo}) → 감각 묘사 문구로 교체")
                        vo = safe_line_for(sc.get("target_visual_role", ""), note, replaced)
                        replaced += 1
                    if idx == 0 and hook_hints:
                        copied = copies_reference(sc.get("hook_title", ""), hook_hints)
                        if copied:
                            logger.info(f"[후킹 원문 복제 차단] '{sc['hook_title']}' ≈ 참고 영상 원문 '{copied}' → 기본 후킹")
                            sc["hook_title"] = "여기 진짜 한국 맞아요?"
                        if copies_reference(vo, hook_hints):
                            vo = safe_line_for(sc.get("target_visual_role", ""), note, replaced)
                            replaced += 1
                        sc["hook_reference"] = hook_hints.summary()
                        used = hook_hints.pattern_used(sc.get("hook_pattern_used"))
                        sc["hook_pattern_used"] = {"video_id": used["video_id"], "hook_type": used["hook_type"],
                                                   "pattern_template": used["pattern_template"]} if used else None
                    # 씬 1 앵무새 반복: 후킹을 읽은 직후 같은 단어를 다시 읽으면 늘어짐 → 교체
                    if idx == 0 and is_hook_echo(sc.get("hook_title", ""), vo):
                        logger.info(f"[후킹 반복 차단] 헤드라인 '{sc.get('hook_title')}' ≈ 서브 '{vo}' → 사진 중립 문구로 교체")
                        vo = safe_line_for(sc.get("target_visual_role", ""), note, replaced)
                        replaced += 1

                # 3. 템플릿 길이 준수: 감성 구어체 15~20자 Hard Limit 준수 (TRAVEL_POLICY)
                sc["duration_sec"] = TRAVEL_POLICY.default_scene_sec
                if idx < len(scenes) - 1:
                    vo = strip_brochure_words(vo)
                    vo = clamp_line_to_policy(vo, TRAVEL_POLICY)
                    done = complete_sentence(vo)
                    if done is None:
                        logger.info(f"[미완성 문장 차단] 씬 {idx + 1}: '{vo}' → 사진 중립 문구로 교체")
                        done = safe_line_for(sc.get("target_visual_role", ""), note, replaced) + "."
                        replaced += 1
                    elif done != vo:
                        logger.info(f"[종결 어미 보정] 씬 {idx + 1}: '{vo}' → '{done}'")
                    if find_banned_style(done):
                        logger.info(f"[판에 박힌 패턴 차단] 씬 {idx + 1}: {find_banned_style(done)} (대사: {done}) → 사진 중립 문구로 교체")
                        done = safe_line_for(sc.get("target_visual_role", ""), note, replaced) + "."
                        replaced += 1
                    vo = clamp_line_to_policy(done, TRAVEL_POLICY)
                cap = vo   # 자막 = 나레이션 (앱에서도 동일하게 동기화)

                sc["voiceover"] = vo
                sc["caption"] = cap
                sc["subtitle"] = cap

            # 3-1. 감탄사 다양성: '대박' 등 같은 감탄 표현은 영상 전체(헤드라인 포함) 1회, 이후 등장은 안 쓴 유의어로
            body = scenes[:-1]
            varied, changes = diversify_exclamations([sc["voiceover"] for sc in body], reserved=scenes[0].get("hook_title", ""))
            for sc, vo in zip(body, varied):
                sc["voiceover"] = sc["caption"] = sc["subtitle"] = vo
            for c in changes:
                logger.info(f"[감탄사 중복 분산] {c}")

            # 4. 씬 1 건물 외관 상단 로고 프레임 밖 제외 플래그 주입
            if scenes:
                scenes[0]["focus_bottom"] = True

            # 5. 마지막 씬 엔딩 CTA 규격 100% 강제 고정
            last_scene = scenes[-1]
            last_scene["voiceover"] = TRAVEL_CTA_VOICEOVER
            last_scene["subtitle"] = TRAVEL_CTA_SUBTITLE
            last_scene["caption"] = TRAVEL_CTA_SUBTITLE
            last_scene["duration_sec"] = 2.5

        if output_plan_path:
            with open(output_plan_path, "w", encoding="utf-8") as f:
                json.dump(scenes, f, ensure_ascii=False, indent=2)

        logger.info(f"스토리보드 기획 완료 (모드={mode})! 총 {len(scenes)}개 씬이 '{output_plan_path}'에 저장되었습니다.")
        return scenes

    except Exception as e:
        logger.error(f"Gemini 응답 파싱 실패: {e}\n원본 응답:\n{response.text}")
        raise


# ==============================================================================
# 촬영 영상 원본 직가공 쇼츠 (Video Overlay Pipeline) 전용 분석 및 대본 생성
# ==============================================================================

def split_video_timeline(total_duration: float) -> List[Dict[str, Any]]:
    """
    영상 전체 길이에 맞춰 씬 타임라인(시작초~종료초)을 최적 분할합니다.
    - Scene 1: 0~3.0초 (오프닝 & 후킹)
    - Scene 2~N: 영상 중간 본문 (각 3.0~4.5초)
    - Scene End: 마지막 3.0~4.0초 (CTA)
    """
    dur = max(float(total_duration or 10.0), 5.0)

    if dur <= 7.0:
        # 매우 짧은 영상: 2씬 (후킹 3초, CTA 나머지)
        s1_dur = min(3.0, dur * 0.5)
        return [
            {"scene_index": 1, "role": "hook", "start_sec": 0.0, "end_sec": s1_dur, "duration": s1_dur},
            {"scene_index": 2, "role": "cta", "start_sec": s1_dur, "end_sec": dur, "duration": dur - s1_dur},
        ]

    # 오프닝 훅: 3.0초 고정 (또는 전체의 25% 이내)
    hook_sec = 3.0
    # 엔딩 CTA: 3.5초 (또는 전체의 25% 이내)
    cta_sec = 3.5 if dur >= 12.0 else max(2.5, dur * 0.3)
    body_total = dur - hook_sec - cta_sec

    # 중간 본문 씬 수 결정
    if body_total <= 5.0:
        num_body = 1
    elif body_total <= 10.0:
        num_body = 2
    elif body_total <= 16.0:
        num_body = 3
    elif body_total <= 24.0:
        num_body = 4
    else:
        num_body = min(6, int(body_total // 4.5))

    body_slot_dur = round(body_total / num_body, 2)

    slots: List[Dict[str, Any]] = []
    # Scene 1: Hook
    slots.append({
        "scene_index": 1,
        "role": "hook",
        "start_sec": 0.0,
        "end_sec": hook_sec,
        "duration": hook_sec,
    })

    # Scene 2 ~ N: Body
    curr_t = hook_sec
    for i in range(num_body):
        end_t = round(curr_t + body_slot_dur, 2) if i < num_body - 1 else round(dur - cta_sec, 2)
        slots.append({
            "scene_index": len(slots) + 1,
            "role": "body",
            "start_sec": curr_t,
            "end_sec": end_t,
            "duration": round(end_t - curr_t, 2),
        })
        curr_t = end_t

    # Scene End: CTA
    slots.append({
        "scene_index": len(slots) + 1,
        "role": "cta",
        "start_sec": curr_t,
        "end_sec": round(dur, 2),
        "duration": round(dur - curr_t, 2),
    })

    return slots


def extract_video_frames(video_path: str, num_frames: int = 4, work_dir: Optional[str] = None) -> List[str]:
    """비디오에서 시각 분석용 주요 프레임 이미지를 균등 간격으로 추출합니다."""
    from core.media.normalize import probe_media, _ffmpeg
    info = probe_media(video_path)
    dur = info.duration

    if not work_dir:
        work_dir = os.path.join(os.path.dirname(os.path.abspath(video_path)), "_frames_tmp")
    os.makedirs(work_dir, exist_ok=True)

    # 0.5초, 25%, 50%, 75% 등 균등 지점 계산
    timestamps = []
    if num_frames == 1:
        timestamps = [min(0.5, dur * 0.5)]
    else:
        step = (dur - 1.0) / max(1, num_frames - 1)
        timestamps = [round(max(0.2, min(dur - 0.2, 0.5 + i * step)), 2) for i in range(num_frames)]

    frame_paths = []
    for idx, ts in enumerate(timestamps):
        out_jpg = os.path.join(work_dir, f"frame_{idx:02d}_{ts:.1f}s.jpg")
        cmd = [
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{ts:.3f}", "-i", video_path,
            "-frames:v", "1", "-vf", "scale='min(720,iw)':-2", "-q:v", "3", out_jpg
        ]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode == 0 and os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 0:
            frame_paths.append(out_jpg)

    return frame_paths


def analyze_video_for_overlay(
    video_path: str,
    api_key: str = "",
    mock: bool = False
) -> Dict[str, Any]:
    """
    단일 비디오 클립의 시각적 맥락(피사체, 동작, 분위기)을 분석하고
    '1컨셉 1메시지' 쇼츠용 추천 3대 떡상 컨셉을 도출합니다.
    """
    from core.media.normalize import probe_media
    info = probe_media(video_path)

    if mock or not api_key:
        logger.info("[Mock/NoKey] 비디오 시각 분석 Mock 결과 반환")
        return {
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "visual_summary": "매장 내부와 시그니처 메뉴/서비스가 역동적으로 담긴 촬영 영상",
            "detected_objects": ["매장 인테리어", "인기 메뉴", "직접 이용/제조 장면"],
            "suggested_concepts": [
                {
                    "title": "압도적 비주얼 갓성비",
                    "core_concept": "이 가격에 이 퀄리티 실화? 가성비 끝판왕",
                    "hook_headline": "아직도 여기 모르면 손해!",
                    "target_reason": "영상 속 풍성한 구성과 퀄리티를 직관적으로 전달"
                },
                {
                    "title": "현지인 단골 찐맛집",
                    "core_concept": "동네 주민들이 숨겨두고 가는 찐 로컬 핫플",
                    "hook_headline": "웨이팅 없이 먹는 비밀?",
                    "target_reason": "영상 속 자연스러운 매장 분위기와 신뢰감 강조"
                },
                {
                    "title": "원픽 꿀조합 강추",
                    "core_concept": "사장님이 몰래 알려주는 200% 맛있게 즐기는 법",
                    "hook_headline": "무조건 이렇게 시키세요!",
                    "target_reason": "영상 속 움직임과 디테일한 포인트를 즉각 후킹"
                }
            ]
        }

    # 프레임 추출
    frames = extract_video_frames(video_path, num_frames=4)
    if not frames:
        return analyze_video_for_overlay(video_path, mock=True)

    from PIL import Image
    from google import genai
    from core.guardrail import validate_model_access

    validate_model_access("gemini-3.6-flash", "llm")
    client = genai.Client(api_key=api_key)

    pil_images = []
    for fp in frames:
        try:
            pil_images.append(Image.open(fp).convert("RGB"))
        except Exception:
            pass

    prompt = f"""
당신은 대한민국 최고의 유튜브 쇼츠 / 틱톡 바이럴 영상 디렉터입니다.
첨부된 {len(pil_images)}장의 스틸 프레임은 사용자가 직접 스마트폰으로 촬영한 영상(총 {info.duration:.1f}초)의 타임라인별 주요 순간입니다.

[분석 요구사항]
1. 영상의 시각적 피사체, 동작, 매장/상품 분위기(visual_summary)를 1~2문장으로 요약하세요.
2. 이 영상을 100% 바이럴 떡상시킬 '1컨셉 1메시지' 추천 광고 전략 3가지를 도출하세요.
   - 각 컨셉은 쇼츠 첫 2초에 시선을 낚아챌 킬러 훅(hook_headline, 8~12자)과 단 하나의 명확한 핵심 가치(core_concept)를 가져야 합니다.
3. 반드시 아래 JSON 형식으로만 응답하세요:
{{
  "visual_summary": "...",
  "detected_objects": ["...", "..."],
  "suggested_concepts": [
    {{
      "title": "컨셉 1 제목 (예: 압도적 갓성비)",
      "core_concept": "핵심 1컨셉 1메시지 한 줄",
      "hook_headline": "첫 2초 강력 헤드라인",
      "target_reason": "추천 이유"
    }},
    {{
      "title": "컨셉 2 제목",
      "core_concept": "...",
      "hook_headline": "...",
      "target_reason": "..."
    }},
    {{
      "title": "컨셉 3 제목",
      "core_concept": "...",
      "hook_headline": "...",
      "target_reason": "..."
    }}
  ]
}}
"""
    try:
        res = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=[*pil_images, prompt],
        )
        txt = (res.text or "").strip()
        if "```json" in txt:
            txt = txt.split("```json")[1].split("```")[0].strip()
        elif "```" in txt:
            txt = txt.split("```")[1].split("```")[0].strip()
        data = json.loads(txt)
        data["duration"] = info.duration
        data["width"] = info.width
        data["height"] = info.height
        return data
    except Exception as e:
        logger.warning(f"[Video Analysis] Gemini 분석 실패 → Mock으로 대체: {e}")
        return analyze_video_for_overlay(video_path, mock=True)


# 직가공 mock/반복 차단 시 컨셉 문구와 번갈아 쓰는 중립 문구 (사실 주장 없음)
_OVERLAY_REPAIR_RULES = ("[다시 쓰기 규칙] 영상에서 보이는 동작·결과를 해요체 완결 문장 한 줄로. 원래 뜻과 사실을 그대로 두고 새로운 사실·상황·"
                         "용도(나들이, 선물, 가격 등)를 절대 더하지 마세요. 앞 문장과 다른 장면·근거로 말하고, 접속사·'한 땀 한 땀'·'정성껏'은 쓰지 마세요. "
                         "공백 포함 20~30자.")
_OVERLAY_NEUTRAL_LINES = ("지금 보시는 장면이 바로 그 순간이에요.", "눈으로 보면 설명이 필요 없어요.", "여기서 한 번 더 봐 주세요.")


def plan_video_overlay_storyboard(
    video_path: str,
    core_concept: str = "",
    store_name: str = "",
    business_type: str = "",
    usp: str = "",
    script_style: str = "info",
    tone_manner: str = "energetic",
    api_key: str = "",
    mock: bool = False,
    total_sec: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    업로드된 비디오의 전체 길이에 정확히 맞춰 씬별 시작초~종료초 타임라인 대본을 생성합니다.
    - Scene 1 (0~3.0초): 시선 집중 후킹 타이틀 & 오프닝 나레이션
    - Scene 2~N (중간 본문): 영상 실제 동작에 맞춘 가치 전달 멘트
    - Scene End (마지막 3~5초): CTA 멘트 및 자막
    """
    if total_sec is None:                  # 롱폼 하이라이트는 창 길이(+CTA)를 직접 넘긴다 (원본 전체 길이가 아님)
        from core.media.normalize import probe_media
        total_sec = probe_media(video_path).duration

    # 1. 타임라인 분할 슬롯 도출
    slots = split_video_timeline(total_sec)
    num_scenes = len(slots)

    concept_clean = (core_concept or "").strip() or f"{store_name or '이곳'}의 특별한 킬러 혜택"
    store_display = (store_name or "").strip() or "이곳"
    from core.script_guard import (with_josa, concept_fallback_line, repeated_line_issues, find_banned_style,
                                   script_violation_finder)
    # 행동은 단 하나: 상호가 있으면 '검색', 없으면 '지도 저장' (조사는 받침에 맞춰 자동 보정)
    video_cta_line = (f"네이버에서 {with_josa(store_name.strip(), '을/를')} 검색해 보세요!" if (store_name or "").strip()
                      else "네이버 지도에 저장해 두세요!")

    if mock or not api_key:
        logger.info("[Mock/NoKey] Video Overlay 대본 Mock 생성")
        out_scenes = []
        for sl in slots:
            idx = sl["scene_index"]
            role = sl["role"]
            dur = sl["duration"]
            if role == "hook":
                h_title = "끝까지 보면 알아요"
                vo = concept_fallback_line(concept_clean, 0)
            elif role == "cta":
                h_title = ""
                vo = video_cta_line
            else:
                h_title = ""
                # 같은 문장을 되풀이하지 않도록 컨셉 문구와 중립 문구를 번갈아 사용
                neutral = _OVERLAY_NEUTRAL_LINES[(idx // 2) % len(_OVERLAY_NEUTRAL_LINES)]
                vo = concept_fallback_line(concept_clean, idx) if idx % 2 == 0 else neutral

            sc = dict(sl)
            sc["hook_title"] = h_title
            sc["voiceover"] = vo
            sc["caption"] = vo
            sc["subtitle"] = vo
            sc["script_engine"] = "mock"
            sc["core_concept"] = (core_concept or "").strip()
            out_scenes.append(sc)
        return out_scenes

    # 2. Gemini 대본 생성 (프롬프트 구성)
    slot_desc_lines = []
    for sl in slots:
        idx = sl["scene_index"]
        role = sl["role"]
        st_t = sl["start_sec"]
        ed_t = sl["end_sec"]
        d = sl["duration"]
        # 초당 한국어 약 3.2~3.8음절 권장
        max_chars = int(d * 3.5)
        role_label = "오프닝 후킹" if role == "hook" else ("엔딩 CTA" if role == "cta" else "본문 가치 전달")
        slot_desc_lines.append(f"- Scene {idx} ({role_label}, {st_t:.1f}초~{ed_t:.1f}초, 길이 {d:.1f}초): 글자 수 공백 포함 {max(8, max_chars - 3)}~{max_chars + 2}자 이내")

    slot_guide_text = "\n".join(slot_desc_lines)

    system_instruction = (
        "당신은 10~60초 단일 숏폼 영상에 딱 맞게 자막과 나레이션을 기획하는 쇼츠 전문 카피라이터입니다.\n"
        "주어진 타임라인의 각 씬 길이(초)에 맞춰 음성이 절대 넘치지 않도록 글자 수를 철저히 통제하고,\n"
        "선택된 '단 하나의 코어 컨셉'으로 처음부터 끝까지 집중 관통하세요."
    )

    user_prompt = f"""
[촬영 영상 원본 직가공 쇼츠 대본 기획]
- 영상 총 길이: {total_sec:.1f}초 (총 {num_scenes}개 씬)
- 매장/브랜드명: {store_display}
- 업종/분야: {business_type or '매장/핫플'}
- 매장 특장점: {usp or '특별한 경험과 혜택'}
- ★ 단 하나의 확정 코어 컨셉 (100% 절대 관통): "{concept_clean}"

[씬별 타임라인 규격]:
{slot_guide_text}

[작성 규칙]:
1. Scene 1 (0~3초, Hook):
   - 'hook_title': 시청자의 시선을 사로잡을 핵심 헤드라인 (8~12자, 담백한 팩트와 궁금증 중심)
   - 'voiceover': 헤드라인에 이어지는 담백하고 명확한 오프닝 한 문장
2. Scene 2~{num_scenes - 1} (중간 본문):
   - 영상의 실제 움직임과 장면을 코어 컨셉('{concept_clean}')과 직결시켜 가치를 전달하는 구어체 완결 문장
3. Scene {num_scenes} (마지막 엔딩 CTA):
   - 단 하나의 명확한 행동 유도 (정확히: "{video_cta_line}")
4. 각 씬의 voiceover(음성 대본)와 caption(화면 자막)은 토씨 하나 틀리지 않고 100% 동일해야 합니다.
5. 허위 과장 표현("SNS 난리 난", "200% 맛있게", "웨이팅 없이" 등)은 전면 금지하며, 현장의 진솔한 매력에 집중하세요.
6. 씬마다 새로운 장면·근거를 말하세요. 앞 씬이나 헤드라인에 쓴 말을 바꿔 되풀이하면 안 됩니다.
7. 상투구 금지: '한 땀 한 땀', '정성껏', '맛보실 수 있어요', '따라서', '그러므로', '또한', '그리고'. 해요체 구어체로 짧게 말하세요.
8. 입력에 없는 사실(연차·수상·가격·고객 반응)을 지어내지 마세요. 컨셉과 영상에서 보이는 것만 말하세요.
9. 반드시 아래 JSON 포맷으로 출력하세요:
{{
  "scenes": [
    {{
      "scene_index": 1,
      "role": "hook",
      "hook_title": "...",
      "voiceover": "...",
      "caption": "..."
    }},
    ...
  ]
}}
"""
    from core.llm import generate_json
    from core.script_guard import to_haeyo, strip_brochure_words
    from core.mode_policy import clamp_line_to_policy, OWNER_POLICY
    contents = [user_prompt]

    try:
        data, script_engine = generate_json(
            contents,
            system_instruction,
            temperature=0.7,
            gemini_api_key=api_key,
            purpose="촬영 영상 직가공 대본"
        )
        # 규칙 위반(판에 박힌 표현·씬 간 반복·컨셉 이탈) 줄은 같은 엔진으로 1회 재작성
        if isinstance(data, dict):
            try:
                from core.llm import repair_scene_lines
                scs = data.get("scenes") or []
                body_lines = [str(x.get("voiceover") or "") for x in scs[:-1] if isinstance(x, dict)]
                hook_line = str((scs[0] or {}).get("hook_title") or "") if scs else ""
                data, fixes = repair_scene_lines(
                    data, script_violation_finder(body_lines, core_concept=(core_concept or "").strip(), hook=hook_line, rhythm=False),
                    system_instruction, _OVERLAY_REPAIR_RULES, gemini_api_key=api_key)
                for f in fixes:
                    logger.info(f"[video_overlay 대본 규칙 보정] {f}")
            except Exception as re_err:
                logger.warning(f"[video_overlay 대본 규칙 보정 건너뜀] {re_err}")
        parsed_scenes = data.get("scenes", []) if isinstance(data, dict) else data

        out_scenes = []
        body_texts: List[str] = []
        for i, sl in enumerate(slots):
            ps = parsed_scenes[i] if i < len(parsed_scenes) else {}
            sc = dict(sl)
            is_last = sl["role"] == "cta"
            raw_vo = (ps.get("voiceover") or ps.get("caption") or "").strip()
            hk = to_haeyo((ps.get("hook_title") or "").strip()) if sl["role"] == "hook" else ""   # 훅도 합쇼체 금지

            if is_last:
                vo = video_cta_line
            else:
                if not raw_vo or find_banned_style(raw_vo):
                    if raw_vo:
                        logger.info(f"[video_overlay 판에 박힌 표현 차단] 씬 {i + 1}: {find_banned_style(raw_vo)} (대사: {raw_vo})")
                    raw_vo = concept_fallback_line(concept_clean, i)
                vo = strip_brochure_words(to_haeyo(raw_vo))
                vo = clamp_line_to_policy(vo, OWNER_POLICY)
                body_texts.append(vo)

            if sl["role"] == "hook" and (not hk or find_banned_style(hk)):
                hk = "끝까지 보면 알아요"

            sc["hook_title"] = hk
            sc["voiceover"] = vo
            sc["caption"] = vo
            sc["subtitle"] = vo
            sc["script_engine"] = script_engine
            sc["core_concept"] = (core_concept or "").strip()
            out_scenes.append(sc)

        # 씬 간 앵무새 반복(훅 포함) 차단 - 겹치는 씬은 컨셉 안전 문구/중립 문구로 교체
        hook_text = next((x["hook_title"] for x in out_scenes if x.get("hook_title")), "")
        body_scenes = [x for x in out_scenes if x["role"] != "cta"]
        for j, why in repeated_line_issues([x["voiceover"] for x in body_scenes], hook_text).items():
            new_line = concept_fallback_line(concept_clean, j) if j % 2 == 0 else _OVERLAY_NEUTRAL_LINES[j % len(_OVERLAY_NEUTRAL_LINES)]
            logger.info(f"[video_overlay 씬 간 반복 차단] 씬 {body_scenes[j]['scene_index']}: {why}")
            body_scenes[j]["voiceover"] = body_scenes[j]["caption"] = body_scenes[j]["subtitle"] = new_line

        logger.info(f"[Video Overlay 대본 기획 완료] 총 {len(out_scenes)}개 씬 생성")
        return out_scenes

    except Exception as e:
        logger.warning(f"[Video Overlay 대본 생성 실패] LLM 오류로 Mock 대체: {e}")
        return plan_video_overlay_storyboard(video_path, core_concept, store_name, business_type, usp, mock=True,
                                            total_sec=total_sec)

