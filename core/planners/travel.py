"""
core/planners/travel.py - 트랙 A: 숙소 제휴 쇼츠 기획 오케스트레이션 (사진 확정 → 대본 작성)

순서가 핵심입니다.
  1) 크롤링 사진 풀을 비전 분류 (카테고리·적합도·태그, 해시 캐시)
  2) 실제 사용 가능한 사진 수로 씬 슬롯 계획 (기본: 7씬 황금 비율 수영장3/객실2/부대시설2)
  3) 슬롯에 사진을 먼저 배정
  4) 각 씬에 '실제로 배정된 사진의 설명'을 붙여 대본 생성
     → 슬롯 이름(예: '자쿠지·스파')만 보고 대사를 쓰다가 실제 사진(예: 실내 수영장)과 어긋나는 문제를 방지
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from core.analyzer import ROLE_KEYWORDS, build_travel_slots, plan_storyboard, DEFAULT_TRAVEL_TEMPLATE
from core.scraper import classify_image_pool, describe_photo_for_script, download_targeted_scene_assets

logger = logging.getLogger("ad_factory.planners.travel")

ASSIGNMENT_KEYS = ("image_path", "matched_image_url", "asset_status", "vision_verdict", "asset_fallback")


def plan_travel_storyboard(
    product_name: str,
    usp: str,
    image_pool: List[Dict[str, Any]],
    assets_dir: str,
    api_key: Optional[str] = None,
    vision_cache: Optional[Dict[str, Any]] = None,
    exclude_urls: Optional[set] = None,
    travel_template: str = DEFAULT_TRAVEL_TEMPLATE,
    mock: bool = False,
    core_concept: str = "",
    **plan_kwargs,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    숙소 제휴 콘티 + 씬별 사진 배정을 함께 반환합니다: (scenes, photo_inventory)
    plan_kwargs는 core.analyzer.plan_storyboard로 그대로 전달 (tone_and_manner, script_style, output_plan_path 등)
    """
    vision_key = None if mock else api_key
    inventory = classify_image_pool(image_pool, assets_dir, vision_key, vision_cache, exclude_urls)

    # 대본과 같은 슬롯 계획으로 사진부터 배정 (build_travel_slots는 같은 입력에 같은 결과를 내는 순수 함수)
    slots = build_travel_slots(None, inventory, usp, travel_template)
    skeleton = [
        {"scene_index": i + 1, "target_visual_role": s["role"], "visual_tags": list(s["tags"]),
         "visual_keywords": list(ROLE_KEYWORDS.get(s["role"], []))}
        for i, s in enumerate(slots)
    ]
    download_targeted_scene_assets(skeleton, image_pool, assets_dir, api_key=vision_key,
                                   exclude_urls=exclude_urls, vision_cache=vision_cache)
    notes = [describe_photo_for_script(sk.get("vision_verdict")) for sk in skeleton]

    scenes = plan_storyboard(
        product_name=product_name, usp=usp, mode="travel", api_key=api_key, mock=mock,
        photo_inventory=inventory, travel_template=travel_template, slot_photo_notes=notes,
        force=True, core_concept=core_concept, **plan_kwargs,
    )
    for sc, sk in zip(scenes, skeleton):
        for k in ASSIGNMENT_KEYS:
            if k in sk:
                sc[k] = sk[k]
    logger.info(f"숙소 제휴 콘티 + 사진 배정 완료: {sum(1 for s in scenes if s.get('image_path'))}/{len(scenes)}씬, 인벤토리 {inventory}")
    return scenes, inventory
