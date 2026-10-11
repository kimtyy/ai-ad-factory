"""
core/radar/hooks.py - 떡상 쇼츠의 첫 3초 후킹 구조 추출

- 영상 파일을 내려받지 않고, 공개 YouTube URL을 Gemini에 직접 전달하며 0~3초 구간만 분석합니다
  (YouTube 약관상 다운로드 불가, 타인 영상 자막은 API로 제공되지 않음).
- 결과는 '패턴'으로 저장합니다: 원문 대사는 참고용으로만 두고, 재사용은 {장소}/{숫자} 같은 자리표시자
  템플릿으로 하여 남의 카피를 그대로 가져다 쓰지 않도록 합니다.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ad_factory.radar.hooks")

HOOK_TYPES = ("question", "number", "pov", "reveal", "before_after", "loss_warning", "superlative",
              "price_shock", "list", "emotion", "reversal", "other")
HOOK_TYPE_KO = {
    "question": "질문형", "number": "숫자형", "pov": "POV 몰입형", "reveal": "정체 공개형", "before_after": "비포/애프터",
    "loss_warning": "손실·경고형", "superlative": "최상급 과장형", "price_shock": "가격 충격형", "list": "리스트형",
    "emotion": "감정 리액션형", "reversal": "반전형", "other": "기타",
}
HOOK_TYPE_GUIDE = '"reversal" = a counter-intuitive claim that flips common sense (e.g. "{장소}은 이상하게 {시기} 지나야 더 좋습니다")'

# viral 범용 스켈레톤에 허용하는 자리표시자 (어떤 업종에도 끼워 맞출 수 있는 추상 변수만)
VIRAL_PLACEHOLDERS = ("주체", "대상", "행동", "장소", "숫자", "가격", "시기", "결과")

# 업종별 '진짜 리뷰' 판정 기준 (예능·연예인 토크·애니 짜깁기 클립은 후킹 구조가 캐릭터/유명인 덕분이라 광고에 재사용 불가)
RELEVANCE_CRITERIA = {
    "stay": "a real accommodation/travel content: hotel, pension, pool villa, resort or space review, room tour, or travel vlog "
            "showing the actual place. Pet-friendly stays (e.g. a dog-friendly pension or pool villa) ARE relevant when the "
            "video clearly reviews or tours the accommodation space itself, even if a pet appears",
    "food": "a real restaurant/cafe/food content: actual food or venue review, mukbang at a real place, or cafe/restaurant vlog",
    "book": "a real book or bookstore content: book recommendation or review, reading a passage/quote from a book, "
            "bookstore visit or tour, or a reading vlog showing actual books",
}

HOOK_PROMPT = """
You are analyzing ONLY the first 3 seconds of a viral YouTube Short (Korean market).
First decide relevance: is this {criteria}?
NOT relevant: celebrity talk-show or TV variety clips, animation/cartoon clips, memes, pet-centered clips where the
animal (snacks, tricks, cute moments) is the subject rather than the place,
compilations of broadcast footage, or anything where the hook works only because of a famous person or character.
Return JSON only with these keys:
- "relevant": true or false (by the rule above)
- "content_type": one of review, room_tour, vlog, food_review, tv_clip, celebrity_talk, animation, meme, pets, other
- "spoken_line": the exact narration/speech in the first 3 seconds (Korean as spoken; "" if none)
- "on_screen_text": text overlays visible in the first 3 seconds ("" if none)
- "visual_hook": what is shown in Korean, one short phrase (e.g. "문 열자마자 인피니티 풀 공개")
- "hook_type": one of {hook_types}; {hook_type_guide}
- "pattern_template": a REUSABLE Korean template generalized from the hook with placeholders like {{장소}}, {{숫자}}, {{가격}}, {{대상}}.
   Do not copy distinctive phrases verbatim; keep only the structure.
- "why_it_works": one Korean sentence on why it stops the scroll
"""


VIRAL_PROMPT = """
You are analyzing ONLY the first 3 seconds of a viral YouTube Short (Korean market) to extract a hook SKELETON that any
business (accommodation, restaurant, shop, product) could reuse in its own ad.
First decide transferability: does the hook structure still stop the scroll after the original subject (the specific
game, celebrity, character, brand, song, news event or fandom) is completely removed?
NOT transferable: hooks that work only because of a famous person/character/fandom, music or dance performances,
breaking news, or shock/gross content that cannot be used in an ad.
Return JSON only with these keys:
- "relevant": true if transferable, false otherwise
- "content_type": one of howto, tips, story, challenge, review, comedy, game, celebrity_talk, animation, music, news, meme, other
- "specific_terms": JSON list of the specific names/items in the original (game titles, people, brands, products, places)
   exactly as they appear in Korean or English ([] if none)
- "spoken_line": the exact narration/speech in the first 3 seconds (Korean as spoken; "" if none)
- "on_screen_text": text overlays visible in the first 3 seconds ("" if none)
- "visual_hook": what is shown in Korean, one short phrase
- "hook_type": one of {hook_types}; {hook_type_guide}
- "pattern_template": a Korean SKELETON using ONLY these placeholders: {placeholders}.
   It must contain at least one placeholder and none of the specific_terms. Bleach the original subject completely.
   Example: "{{주체}}가 절대 {{행동}}하지 않는 이유"
- "why_it_works": one Korean sentence on why the STRUCTURE stops the scroll (not the original subject)
"""

# dry-run(viral)용 유형별 스켈레톤 (실제 영상 분석 아님)
MOCK_VIRAL_SKELETONS = {
    "question": "{주체}가 {행동}하면 생기는 일?", "number": "{숫자}초 만에 {결과}되는 {대상}",
    "pov": "POV: {장소}에서 {행동}하는 순간", "loss_warning": "절대 {행동}하지 마세요, {결과}",
    "reveal": "{대상}만 아는 {장소} 공개", "other": "{주체}가 {행동}하는 진짜 이유",
}


def build_prompt(vertical: str) -> str:
    if vertical == "viral":
        return VIRAL_PROMPT.format(hook_types=", ".join(HOOK_TYPES), hook_type_guide=HOOK_TYPE_GUIDE,
                                   placeholders=" ".join("{" + p + "}" for p in VIRAL_PLACEHOLDERS))
    return HOOK_PROMPT.format(criteria=RELEVANCE_CRITERIA.get(vertical, RELEVANCE_CRITERIA["stay"]),
                              hook_types=", ".join(HOOK_TYPES), hook_type_guide=HOOK_TYPE_GUIDE)


def skeleton_problem(template: str, specific_terms: List[str]) -> Optional[str]:
    """viral 스켈레톤 검증: 허용 자리표시자만, 1개 이상, 원본 고유명사 잔존 금지. 문제 있으면 사유 반환"""
    found = re.findall(r"\{([^{}]+)\}", template or "")
    if not found:
        return "자리표시자 없음"
    bad = sorted({p for p in found if p not in VIRAL_PLACEHOLDERS})
    if bad:
        return "허용되지 않은 변수 " + ", ".join("{" + p + "}" for p in bad)
    body = re.sub(r"\{[^{}]+\}", " ", template).lower()
    leaked = [t for t in specific_terms if len(t.strip()) >= 2 and t.strip().lower() in body]
    if leaked:
        return "원본 고유명사 잔존: " + ", ".join(leaked[:3])
    return None


def to_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def extract_hook(video_id: str, api_key: str, model: str = "gemini-3.6-flash", seconds: int = 3,
                 vertical: str = "stay") -> Dict[str, Any]:
    """Gemini로 공개 영상의 0~seconds초 구간을 분석 (+ 업종 적합성 판정)"""
    from google import genai
    from google.genai import types
    from core.guardrail import validate_model_call

    client = genai.Client(api_key=api_key)
    res = client.models.generate_content(
        model=validate_model_call(model, category="llm"),
        contents=types.Content(parts=[
            types.Part(file_data=types.FileData(file_uri=to_watch_url(video_id)),
                       video_metadata=types.VideoMetadata(start_offset="0s", end_offset=f"{seconds}s")),
            types.Part(text=build_prompt(vertical)),
        ]),
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
    )
    data = json.loads(res.text)
    if isinstance(data, list):
        data = data[0] if data else {}
    return _normalize(data, source=f"gemini:{model}", vertical=vertical)


def mock_hook(title: str, vertical: str = "stay") -> Dict[str, Any]:
    """dry-run용: 제목에서 후킹 유형/패턴을 규칙으로 추정 (실제 영상 분석 아님)"""
    t = re.sub(r"^\[[^\]]*\]\s*", "", title or "")
    if t.upper().startswith("POV"):
        htype = "pov"
    elif "?" in t:
        htype = "question"
    elif re.search(r"\d", t):
        htype = "number"
    elif any(k in t for k in ("손해", "모르면", "절대")):
        htype = "loss_warning"
    elif "공개" in t:
        htype = "reveal"
    else:
        htype = "other"
    if vertical == "viral":
        template = MOCK_VIRAL_SKELETONS.get(htype, MOCK_VIRAL_SKELETONS["other"])
    else:
        template = re.sub(r"\d+\s*(시간|분|초|만원|원|곳|가지)?", lambda m: "{숫자}" + (m.group(1) or ""), t)
    return _normalize({"spoken_line": "", "on_screen_text": t, "visual_hook": "(dry-run: 제목 기반 추정)",
                       "hook_type": htype, "pattern_template": template, "relevant": True, "content_type": "mock",
                       "specific_terms": [], "why_it_works": "(dry-run: 실제 영상 분석 아님)"}, source="mock", vertical=vertical)


def _normalize(data: Dict[str, Any], source: str, vertical: str = "stay") -> Dict[str, Any]:
    htype = str(data.get("hook_type", "other")).strip().lower()
    terms = data.get("specific_terms") or []
    terms = [str(t) for t in terms] if isinstance(terms, list) else [str(terms)]
    out = {
        "spoken_line": str(data.get("spoken_line", "") or "")[:200],
        "on_screen_text": str(data.get("on_screen_text", "") or "")[:200],
        "visual_hook": str(data.get("visual_hook", "") or "")[:120],
        "hook_type": htype if htype in HOOK_TYPES else "other",
        "pattern_template": str(data.get("pattern_template", "") or "")[:160],
        "why_it_works": str(data.get("why_it_works", "") or "")[:200],
        "relevant": data.get("relevant") is True or str(data.get("relevant")).lower() == "true",
        "content_type": str(data.get("content_type", "other") or "other")[:30],
        "note": "",
        "source": source,
    }
    # viral: 범용 스켈레톤 조건을 못 맞추면 힌트로 쓰지 않음 (특정 소재가 남은 템플릿은 다른 업종에 못 씀)
    if vertical == "viral" and out["relevant"]:
        problem = skeleton_problem(out["pattern_template"], terms)
        if problem:
            out["relevant"], out["note"] = False, problem
    return out


def try_extract_hook(video_id: str, title: str, api_key: Optional[str], dry_run: bool,
                     vertical: str = "stay") -> Optional[Dict[str, Any]]:
    if dry_run or not api_key:
        return mock_hook(title, vertical)
    try:
        return extract_hook(video_id, api_key, vertical=vertical)
    except Exception as e:
        logger.warning(f"후킹 추출 실패 ({video_id}): {e}")
        return None
