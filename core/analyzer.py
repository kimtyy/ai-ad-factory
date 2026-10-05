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
from core.script_guard import COPY_TONE_RULES, shorten_line, find_promo_tone, find_unsupported_claim, safe_line_for
from core.radar.hints import HookHints, copies_reference

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
            "📢 [정보·후킹형 (기본 런비 스타일)]:\n"
            "- 시청자의 호기심(Curiosity Gap)을 극대화하고 숙소의 핵심 비주얼과 특장점을 직격으로 추천하는 타격형 카피로 작성하세요.\n"
            "- 예시: '호수 뷰가 객실 통창에 그대로 걸려 있음', '한겨울에도 김 모락모락 나는 야외 온수풀', '애들이 수영장에서 나올 생각을 안 함'"
        )
    },
    "experience": {
        "name": "✈️ 1인칭 경험형 (생생한 투숙 후기 스타일)",
        "instruction": (
            "✈️ [1인칭 경험/체류 후기형 (인스타/쇼츠 브이로그 감성)]:\n"
            "- 여행자가 숙소에 직접 머무르며 온몸으로 체감한 1인칭 리얼 투숙 후기 톤앤매너로 작성하세요.\n"
            "- 예시: '로비 들어서자마자 통창 뷰 보고 입이 떡 벌어졌어요', '김 모락모락 나는 온수풀에 누워있으니 피로가 싹 풀림', '체크아웃하기 진짜 싫었던 침구 맛집!'\n"
            "- 진정성 있는 1인칭 개인 경험과 감탄을 살리되, 씬당 15~20자 이내의 간결한 구어체를 준수하세요."
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
        "view": "문 열자마자 펼쳐지는 역대급 뷰!",
        "pool": "물놀이 시작하면 시간 순삭되는 수영장",
        "facility": "분위기 좋은 공간에서 여유 한 잔!",
        "bedroom": "푹신한 침구에서 꿀잠 예약!",
        "bedroom_or_view": "창밖 풍경 보며 늦잠 자고 싶은 방!",
        "view_or_night": "밤이 되면 더 예뻐지는 풍경!",
    },
    "experience": {
        "hook_title": "문 열자마자 입이 떡 벌어짐!",
        "view": "도착하자마자 뷰 보고 입 떡 벌어짐!",
        "pool": "물놀이하다 보니 시간 가는 줄 몰랐어요!",
        "facility": "여기서 커피 마시니 세상 행복!",
        "bedroom": "체크아웃하기 진짜 싫었던 침구 맛집!",
        "bedroom_or_view": "창밖 뷰 보다가 하루가 다 갔어요!",
        "view_or_night": "밤 풍경까지 완벽했던 하루!",
    },
}
# 황금 비율 슬롯별 Mock 대사 (같은 역할이 연속돼도 대사가 반복되지 않도록 슬롯 위치별로 구분)
MOCK_GOLDEN7_LINES = {
    "info": ["물빛부터 다른 수영장 실화?", "끝없이 이어지는 풀 뷰에 감탄!", "물멍하며 완벽하게 힐링!",
             "감성 가득한 객실에서 하룻밤!", "창밖으로 펼쳐지는 그림 같은 뷰!", "분위기 끝내주는 공간까지!"],
    "experience": ["수영장 보자마자 입 떡 벌어짐!", "풀 뷰 보면서 사진만 백 장 찍음!", "물멍하다 보니 시간 순삭!",
                   "객실 들어가자마자 감성 폭발!", "창밖 뷰 보다가 늦잠까지 완벽!", "마지막까지 꽉 찬 하루!"],
}


def generate_mock_travel_storyboard(
    product_name: str,
    usp: str,
    num_scenes: int = 6,
    tone_and_manner: str = "aggro",
    script_style: str = "info",
    photo_inventory: Optional[Dict[str, int]] = None,
    template: str = DEFAULT_TRAVEL_TEMPLATE
) -> List[dict]:
    """
    [숙소 제휴 Mock 콘티 - Script-First 규격]
    - 템플릿(golden7/classic) + 사진 인벤토리 기반 씬 역할 배정 (특정 숙소 고유 정보 미포함)
    - 숙소 이름 철저 은닉 (Curiosity Gap), 엔딩 댓글 유도 CTA 자동 삽입
    """
    lines = MOCK_TRAVEL_LINES.get(script_style, MOCK_TRAVEL_LINES["info"])
    golden_lines = MOCK_GOLDEN7_LINES.get(script_style, MOCK_GOLDEN7_LINES["info"])
    slots = build_travel_slots(num_scenes, photo_inventory, usp, template)
    total = len(slots)
    scenes = []
    for i, slot in enumerate(slots):
        v_role = slot["role"]
        is_last = (i == total - 1)
        if is_last:
            text = TRAVEL_CTA_VOICEOVER
        elif template == "golden7" and not slot["fallback_from"] and i < len(golden_lines):
            text = golden_lines[i]
        else:
            text = lines.get(v_role, lines["view"])
        scenes.append({
            "scene_index": i + 1,
            "scene_number": i + 1,
            "role": narrative_role_for(i, total, v_role),
            "duration_sec": 2.5,
            "hook_title": lines["hook_title"] if i == 0 else "",
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
    hook_hints: Optional[HookHints] = None
) -> List[dict]:
    """
    Step 1: 레퍼런스 영상 분석 및 씬별 콘티 역기획 실행.
    - 일반 커머스 모드: 숏폼 퍼포먼스 마케팅 디렉터, 씬별 앵글 다변화, 제품 및 제형 매크로 시연
    - 여행 상품 모드: 대한민국 1등 여행/숙소 숏폼 크리에이티브 디렉터, 숙소명 은닉, 시각적 공간감/물멍, 엔딩 댓글 CTA
    """
    if mode == "owner":
        raise ValueError("사장님(owner) 모드는 core.planners.plan_owner_storyboard를 사용하세요 (트랙 A 제휴 플래너와 분리).")
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
        f"Step 1 시작 (모드={mode}): 제품명/여행지='{product_name}', USP='{usp}', 목표 씬={num_scenes}, "
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
                template=travel_template
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

        # 숙소 제휴 모드: 숙소명 철저 은닉(Curiosity Gap), 템플릿 길이(씬당 15~20자), 사진 에셋 1:1 매칭 (뷔페 언급 금지)
        system_instruction = (
            "당신은 인스타그램 릴스, 틱톡, 유튜브 쇼츠에서 수백만 조회수와 폭발적인 댓글 참여율을 이끌어내는 "
            "'대한민국 1등 여행/숙소 숏폼 크리에이티브 디렉터'입니다.\n\n"
            "당신의 미션은 시청자가 1초 만에 시선을 빼앗기고, 영상이 끝날 때까지 몰입하며, 댓글에 정보를 묻지 않고는 못 배기게 만드는 숏폼 여행 콘텐츠를 기획하는 것입니다.\n"
            "다음 핵심 원칙을 철저히 준수해야 합니다:\n"
            f"1. [선택된 대본 기획 스타일 준수 ({style_preset['name']})]:\n"
            f"   {style_instruction}\n"
            f"2. [영상 길이 {target_length} 규격 & 씬당 대본 15~20자 초압축 절대 준수]:\n"
            "   - 각 씬의 voiceover(나레이션)는 반드시 '공백 포함 15~20자 이내'의 간결한 구어체 딱 한 줄로만 작성하세요!\n"
            f"   - 20자를 초과하면 TTS 재생 시간이 늘어나 전체 영상이 {target_length} 규격을 초과하므로 절대 금지합니다.\n"
            "   - 문장은 친구에게 비밀 장소를 자랑하듯 감각을 툭 던지는 구어체 평서문으로 쓰세요. "
            "'~해보세요', '~마세요', '~딱!' 같은 권유·강요형 홍보 어미는 전면 금지입니다 (아래 [카피 톤] 참조).\n"
            "3. [대본과 실제 사진 에셋의 1:1 시각적 정합성 보장]:\n"
            "   - 각 씬은 사용자 프롬프트에 배정된 시각 역할의 '실제 숙소 사진'과 함께 송출됩니다.\n"
            "   - 대사는 그 씬 사진에 실제로 보이는 공간만 묘사하세요. 확보된 사진 구성과 특장점(USP)에 없는 시설·경관(수영장, 호수, 바다, 라운지 등)을 지어내면 허위광고가 됩니다.\n"
            "   - 사진에 없는 '조식 뷔페', '식사' 등 허위 정보는 언급하지 마세요.\n"
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
            "scenes": [
                {
                    "scene_index": i + 1,
                    "role": narrative_role_for(i, len(role_plan), r),
                    "voiceover": TRAVEL_CTA_VOICEOVER if i == last_i else f"<{slot_plan[i]['desc']} 사진을 묘사하는 15~20자 대사>",
                    "subtitle": TRAVEL_CTA_SUBTITLE if i == last_i else "<voiceover와 동일>",
                    "target_visual_role": r,
                    "visual_keywords": ROLE_KEYWORDS.get(r, []),
                    "hook_title": "<10~15자 후킹 헤드라인>" if i == 0 else "",
                    "duration_sec": 2.5,
                    "video_motion_prompt_en": TRAVEL_MOTIONS[i % len(TRAVEL_MOTIONS)],
                }
                for i, r in enumerate(role_plan)
            ],
        }, ensure_ascii=False, indent=2)

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

[대본 선기획 작성 요구사항]
1. 반드시 아래 지정된 JSON 오브젝트 포맷({{"title": "...", "scenes": [...]}})으로 정확히 출력하세요.
2. [{target_length} 규격 준수 - 씬당 나레이션 15~20자 이내]:
   - voiceover는 공백 포함 15~20자 이내의 짧은 구어체 한 문장으로 작성하세요.
   - '~해보세요', '~마세요', '~딱!', '~딱 좋아요' 어미 금지. 직접 가본 사람이 본 장면·느낀 감각을 말하듯 쓰세요.
     (예: "수영장이랑 바다 경계가 그냥 사라집니다", "침대에 누워서 바로 일출 직관 가능한 룸 컨디션")
3. [씬별 시각 역할은 아래 배정을 그대로 사용 (변경 금지)]:
{scene_role_lines}
   - 각 씬의 voiceover는 그 씬에 배정된 사진 종류만 묘사하세요.
   - '이 씬에 실제로 나갈 사진' 설명이 있는 씬은 그 설명에 있는 것만 묘사하세요. 슬롯 이름보다 실제 사진 설명이 우선입니다.
     (예: 슬롯이 '자쿠지·스파'여도 실제 사진이 '실내 수영장'이면 실내 수영장을 묘사)
   - 사진 설명과 특장점(USP)에 없는 시설·경관·속성(자쿠지, 온수, 인피니티, 호수, 바다, 조식 등)은 절대 지어내거나 단정하지 마세요.
4. [정보 은닉 (Curiosity Gap) 강제]:
   - 크롤링된 숙소명('{product_name}')을 대본 나레이션 및 자막에 직접 언급하는 것을 엄격히 금지합니다.
5. 씬 1 (Hook): 반드시 'hook_title'에 스크롤을 즉시 멈추게 하는 강력한 헤드라인(10~15자)을 포함하세요! (예: '여기 진짜 한국 맞아요?', '나만 알고 싶은 비밀 숙소')
   - 씬 1의 hook_title과 voiceover는 화면 한 곳에 위아래로 붙어 함께 나갑니다. voiceover는 헤드라인을 받아 사진 속 장면으로
     이어지는 한 문장으로 쓰고, 헤드라인과 무관한 상투 경고('모르면 평생 손해', '아직도 여기 모르면')로 따로 놀지 않게 하세요.
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

    # LLM 모델 단일 고정 (gemini-3.6-flash) 및 호출 직전 가드레일 검증
    gemini_model = validate_model_call("gemini-3.6-flash", category="llm")
    logger.info(f"Gemini 호출 중... (모델: {gemini_model})")
    response = client.models.generate_content(
        model=gemini_model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            temperature=0.7,
        )
    )

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
        
        if isinstance(parsed_json, dict) and "scenes" in parsed_json:
            scenes = parsed_json["scenes"]
        elif isinstance(parsed_json, list):
            scenes = parsed_json
        else:
            raise ValueError(f"예상치 못한 JSON 구조: {parsed_json}")

        # 무음 모드일 경우 목표 총 시간에 맞춰 균등 분배
        silent_per_scene = round(target_total_sec / len(scenes), 2) if scenes else 3.0

        for idx, sc in enumerate(scenes):
            sc["scene_index"] = idx + 1
            sc["scene_number"] = idx + 1

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
                    claim = find_unsupported_claim(vo, [note], usp)
                    if claim:
                        logger.info(f"[근거 없는 주장 차단] 씬 {idx + 1}: '{claim}' (대사: {vo}) → 사진 중립 문구로 교체")
                        vo = safe_line_for(sc.get("target_visual_role", ""), note, replaced)
                        replaced += 1
                    hook = sc.get("hook_title") or ""
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

                # 3. 템플릿 길이 준수: 씬당 2.5초 기준, 나레이션은 어절 경계에서 15~20자로 (실제 길이는 TTS 길이 기준)
                sc["duration_sec"] = 2.5
                if idx < len(scenes) - 1:
                    vo = shorten_line(vo)
                cap = vo   # 자막 = 나레이션 (앱에서도 동일하게 동기화)

                sc["voiceover"] = vo
                sc["caption"] = cap
                sc["subtitle"] = cap

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
