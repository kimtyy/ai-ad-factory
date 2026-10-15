"""
core/seo_generator.py - 쇼츠 최적화 SEO 메타데이터 자동 생성 엔진
- title: 클릭률(CTR) 높은 쇼츠 제목 (후킹 문구 + 키워드 + #shorts)
- description: 2~3줄 요약 + 고정 댓글 유도 멘트 + 핵심 해시태그
- tags: 검색 유입용 쉼표 구분 태그 목록 (최대 15~20개)
"""
import json
import logging
import re
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ad_factory.seo")

# 모드별 기본 검색 태그 풀 (15~20개 채우기용)
DEFAULT_TAG_POOLS = {
    "owner": [
        "동네가게", "가게추천", "가볼만한곳", "로컬맛집", "핫플", "동네핫플",
        "데이트코스", "맛집추천", "골목상권", "소상공인", "단골가게", "숨은맛집",
        "일상", "브이로그", "쇼츠", "shorts", "추천", "인기"
    ],
    "travel": [
        "숙소추천", "호텔추천", "펜션추천", "감성숙소", "국내여행", "여행지추천",
        "주말여행", "힐링여행", "오션뷰", "풀빌라", "호캉스", "여행브이로그",
        "가볼만한곳", "데이트코스", "국내여행지", "쇼츠", "shorts", "여행"
    ],
    "commerce": [
        "추천템", "살림템", "꿀템", "인기템", "생활꿀팁", "쇼핑추천",
        "내돈내산", "리뷰", "상품리뷰", "가성비템", "신박템", "자취템",
        "쿠팡추천", "스마트스토어", "쇼핑", "쇼츠", "shorts", "필수템"
    ],
    "book": [
        "책추천", "도서추천", "오늘의책", "책방", "동네책방", "독립서점",
        "베스트셀러", "독서", "북스타그램", "책읽기", "힐링도서", "마음치유",
        "문장수집", "인생책", "독서기록", "쇼츠", "shorts", "독서모임"
    ],
}

PINNED_COMMENT_PROMPTS = {
    "owner": "📌 매장 위치 및 방문 꿀팁은 고정 댓글을 확인해주세요!",
    "travel": "📌 숙소 상세 정보 및 예약 링크는 고정 댓글을 확인해주세요!",
    "commerce": "📌 제품 상세 정보 및 구매 링크는 고정 댓글을 확인해주세요!",
    "book": "📌 도서 상세 정보 및 구매 링크는 고정 댓글을 확인해주세요!",
}


def _clean_tag(tag: str) -> str:
    """태그 문자열 정제: # 기호 및 불필요한 특수문자 제거"""
    t = re.sub(r"[#\s]+", "", tag.strip())
    return t


def _tag_set_for(mode: str, product_name: str, usp: str, search_keyword: str, address: str, business_type: str,
                 core_concept: str, tag_set: Optional[Dict[str, Any]]):
    """영상 1개당 단일 태그 풀 (core/hashtags.py). copy에서 이미 만든 tag_set이 있으면 그대로, 없으면 같은 입력으로 결정적으로 생성"""
    from core.hashtags import TagSet, build_tag_set
    ts = TagSet.from_dict(tag_set)
    if ts is not None:
        return ts
    from core.naver_local import region_of
    return build_tag_set(mode, business_type=business_type, name=product_name, usp=usp, concept=core_concept,
                         region=region_of(address) if address else "", search_keyword=search_keyword)


def _build_fallback_metadata(
    mode: str,
    product_name: str,
    usp: str = "",
    search_keyword: str = "",
    scenes: Optional[List[Dict[str, Any]]] = None,
    address: str = "",
    business_type: str = "",
    core_concept: str = "",
    tag_set: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Gemini API 호출 불가 시 안전한 고품질 템플릿 메타데이터 생성"""
    name = (product_name or search_keyword or "여기").strip()
    kw = (search_keyword or name).strip()
    mode_key = mode if mode in DEFAULT_TAG_POOLS else "owner"

    # 1. Title
    if mode == "owner":
        title = f"퇴근길에 여길 모르면 무조건 손해! {name} #shorts"
    elif mode == "travel":
        title = f"사진보다 실물이 훨씬 예쁜 {name} 반전 후기 #shorts"
    elif mode == "commerce":
        title = f"이거 모르고 사면 무조건 손해! {name} 솔직 후기 #shorts"
    elif mode == "book":
        title = f"오늘 밤 마음이 지칠 때 읽기 좋은 책, {name} #shorts"
    else:
        title = f"모르면 무조건 손해! {name} 핵심 정리 #shorts"

    # 2. Description
    summary_lines = []
    if usp:
        first_usp = usp.split("\n")[0].strip()
        summary_lines.append(f"{name}에서 즐기는 특별한 순간!")
        summary_lines.append(first_usp[:80] + ("..." if len(first_usp) > 80 else ""))
    else:
        summary_lines.append(f"많은 분들이 극찬하는 {name}의 매력을 한눈에 확인해보세요.")
        summary_lines.append("직접 가보고 경험해보면 왜 인기 있는지 바로 알 수 있어요!")

    pinned_prompt = PINNED_COMMENT_PROMPTS.get(mode_key, "📌 상세 정보는 고정 댓글을 확인해주세요!")
    
    ts = _tag_set_for(mode, product_name, usp, search_keyword, address, business_type, core_concept, tag_set)
    hash_tags = list(ts.core)                      # 설명란·첫 댓글·검색 태그가 같은 풀을 쓴다 (core/hashtags.py)

    desc = "\n".join([
        *summary_lines,
        "",
        pinned_prompt,
        "",
        " ".join(hash_tags)
    ])

    # 3. 검색 태그 15~20개: 핵심 태그가 앞, 뒤는 같은 업종 사전 (core/hashtags.py)
    selected_tags = list(ts.pool)

    tags_str = ", ".join(selected_tags)

    return {
        "title": title,
        "description": desc,
        "tags": tags_str,
        "tags_list": selected_tags,
        "source": "template"
    }


def generate_seo_metadata(
    scenes: Optional[List[Dict[str, Any]]] = None,
    mode: str = "owner",
    product_name: str = "",
    usp: str = "",
    search_keyword: str = "",
    address: str = "",
    api_key: Optional[str] = None,
    mock: bool = False,
    business_type: str = "",
    core_concept: str = "",
    tag_set: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    영상 씬 대본 및 메타 정보를 바탕으로 Gemini 3.6 Flash를 호출하여
    쇼츠 최적화 메타데이터(title, description, tags)를 자동 생성합니다.
    """
    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY")

    if mock or not api_key:
        logger.info("[SEO] Mock 모드 또는 API 키 부재로 템플릿 메타데이터 생성")
        return _build_fallback_metadata(mode, product_name, usp, search_keyword, scenes, address=address,
                                        business_type=business_type, core_concept=core_concept, tag_set=tag_set)

    # 씬 대본 요약 구성
    script_texts = []
    hook_title = ""
    if scenes and isinstance(scenes, list):
        for idx, sc in enumerate(scenes):
            if not hook_title and sc.get("hook_title"):
                hook_title = str(sc.get("hook_title")).strip()
            vo = sc.get("voiceover") or sc.get("subtitle") or sc.get("caption") or ""
            if vo:
                script_texts.append(f"씬 {idx+1}: {vo.strip()}")

    script_summary = "\n".join(script_texts) if script_texts else "(대본 요약 없음)"

    mode_label = {
        "owner": "소상공인/사장님 매장 홍보",
        "travel": "숙소/호텔/여행지 추천",
        "commerce": "쇼핑/상품 리뷰",
        "book": "동네 책방/도서 큐레이션"
    }.get(mode, "숏폼 영상 광고")

    pinned_guide = PINNED_COMMENT_PROMPTS.get(mode, "📌 상세 정보는 고정 댓글을 확인해주세요!")

    prompt = f"""당신은 유튜브 쇼츠 및 인스타그램 릴스 전문 숏폼 바이럴 마케터입니다.
다음 영상 정보를 정밀 분석하여, 유튜브 알고리즘과 검색(SEO) 최적화에 맞는 메타데이터 3종을 작성해주세요.

[영상 정보]
- 제작 모드: {mode_label} ({mode})
- 상호명/제품명: {product_name or search_keyword or '미지정'}
- 핵심 키워드/지역: {search_keyword or address or '미지정'}
- 특징(USP): {usp or '영상 대본 참조'}
- 영상 후킹 헤드라인: {hook_title or '미지정'}
- 영상 씬별 나레이션:
{script_summary}

[작성 요구사항]
1. title:
   - 스마트폰 화면에서 한눈에 꽂히는 강력한 후킹 제목 (클릭률 CTR 극대화)
   - 극단적 호기심 유발 또는 손실 회피형 카피 + 핵심 키워드 포함
   - 반드시 문장 끝에 ' #shorts' 를 붙여주세요. (60자 이내)
   - 예시: "퇴근길에 여길 모르면 무조건 손해! {product_name} 꿀조합 #shorts"

2. description:
   - 1) 영상의 핵심 매력을 2~3줄로 읽기 쉽게 요약
   - 2) 고정 댓글 유도 멘트 1줄 포함 (예: "{pinned_guide}")
   - 3) 관련 핵심 해시태그 5~7개 (예: #{product_name.replace(' ', '') if product_name else '추천'} #맛집추천 #shorts ...)

3. tags:
   - 유튜브 검색 유입(SEO)을 극대화할 수 있는 검색 태그 목록
   - '#' 기호 없이 키워드만 쉼표(,)와 공백으로 구분하여 나열
   - 반드시 15개 이상 20개 이하로 풍부하게 작성하세요.
   - 예시: "키워드1, 키워드2, 키워드3, ..., 키워드18"

반드시 아래 JSON 형식으로만 응답하세요:
{{
  "title": "클릭률 높은 쇼츠 제목 #shorts",
  "description": "2~3줄 요약\\n\\n{pinned_guide}\\n\\n#해시태그...",
  "tags": "태그1, 태그2, 태그3, ..., 태그18"
}}
"""

    try:
        from google import genai
        from google.genai import types
        from core.guardrail import validate_model_call

        client = genai.Client(api_key=api_key)
        model_name = validate_model_call("gemini-3.6-flash", category="llm")
        res = client.models.generate_content(
            model=model_name,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7
            )
        )
        raw = json.loads(res.text)

        title = str(raw.get("title") or "").strip()
        if not re.search(r"#shorts\b", title, re.I):
            title = f"{title} #shorts".strip()

        desc = str(raw.get("description") or "").strip()
        tags_raw = raw.get("tags")

        # 태그 목록 정리
        tags_list = []
        if isinstance(tags_raw, list):
            tags_list = [_clean_tag(str(t)) for t in tags_raw if _clean_tag(str(t))]
        elif isinstance(tags_raw, str):
            tags_list = [_clean_tag(t) for t in tags_raw.split(",") if _clean_tag(t)]

        # 단일 태그 풀: 설명란 해시태그 줄과 검색 태그가 같은 핵심 태그를 쓰고, LLM이 낸 태그는 업종 가드를 통과한 것만 뒤에 보탠다
        from core.hashtags import guard_tags, replace_hashtag_line
        ts = _tag_set_for(mode, product_name, usp, search_keyword, address, business_type, core_concept, tag_set)
        merged = list(ts.pool)
        for t in guard_tags(tags_list, ts.industry):
            if t.lower() not in [m.lower() for m in merged]:
                merged.append(t)
        tags_list = merged[:20]
        desc = replace_hashtag_line(desc, ts.core)
        tags_str = ", ".join(tags_list)

        logger.info(f"[SEO 메타데이터 생성 성공] Title: '{title}', Tags: {len(tags_list)}개")
        return {
            "title": title,
            "description": desc,
            "tags": tags_str,
            "tags_list": tags_list,
            "source": "gemini"
        }

    except Exception as e:
        logger.warning(f"[SEO 메타데이터 생성 실패 -> 템플릿 대체]: {e}")
        return _build_fallback_metadata(mode, product_name, usp, search_keyword, scenes, address=address,
                                        business_type=business_type, core_concept=core_concept, tag_set=tag_set)
