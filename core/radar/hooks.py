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
from typing import Any, Dict, Optional

logger = logging.getLogger("ad_factory.radar.hooks")

HOOK_TYPES = ("question", "number", "pov", "reveal", "before_after", "loss_warning", "superlative",
              "price_shock", "list", "emotion", "other")
HOOK_TYPE_KO = {
    "question": "질문형", "number": "숫자형", "pov": "POV 몰입형", "reveal": "정체 공개형", "before_after": "비포/애프터",
    "loss_warning": "손실·경고형", "superlative": "최상급 과장형", "price_shock": "가격 충격형", "list": "리스트형",
    "emotion": "감정 리액션형", "other": "기타",
}

HOOK_PROMPT = f"""
You are analyzing ONLY the first 3 seconds of a viral YouTube Short (Korean market).
Return JSON only with these keys:
- "spoken_line": the exact narration/speech in the first 3 seconds (Korean as spoken; "" if none)
- "on_screen_text": text overlays visible in the first 3 seconds ("" if none)
- "visual_hook": what is shown in Korean, one short phrase (e.g. "문 열자마자 인피니티 풀 공개")
- "hook_type": one of {", ".join(HOOK_TYPES)}
- "pattern_template": a REUSABLE Korean template generalized from the hook with placeholders like {{장소}}, {{숫자}}, {{가격}}, {{대상}}.
   Do not copy distinctive phrases verbatim; keep only the structure.
- "why_it_works": one Korean sentence on why it stops the scroll
"""


def to_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def extract_hook(video_id: str, api_key: str, model: str = "gemini-3.6-flash", seconds: int = 3) -> Dict[str, Any]:
    """Gemini로 공개 영상의 0~seconds초 구간을 분석"""
    from google import genai
    from google.genai import types
    from core.guardrail import validate_model_call

    client = genai.Client(api_key=api_key)
    res = client.models.generate_content(
        model=validate_model_call(model, category="llm"),
        contents=types.Content(parts=[
            types.Part(file_data=types.FileData(file_uri=to_watch_url(video_id)),
                       video_metadata=types.VideoMetadata(start_offset="0s", end_offset=f"{seconds}s")),
            types.Part(text=HOOK_PROMPT),
        ]),
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
    )
    data = json.loads(res.text)
    if isinstance(data, list):
        data = data[0] if data else {}
    return _normalize(data, source=f"gemini:{model}")


def mock_hook(title: str) -> Dict[str, Any]:
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
    template = re.sub(r"\d+\s*(시간|분|초|만원|원|곳|가지)?", lambda m: "{숫자}" + (m.group(1) or ""), t)
    return _normalize({"spoken_line": "", "on_screen_text": t, "visual_hook": "(dry-run: 제목 기반 추정)",
                       "hook_type": htype, "pattern_template": template,
                       "why_it_works": "(dry-run: 실제 영상 분석 아님)"}, source="mock")


def _normalize(data: Dict[str, Any], source: str) -> Dict[str, Any]:
    htype = str(data.get("hook_type", "other")).strip().lower()
    return {
        "spoken_line": str(data.get("spoken_line", "") or "")[:200],
        "on_screen_text": str(data.get("on_screen_text", "") or "")[:200],
        "visual_hook": str(data.get("visual_hook", "") or "")[:120],
        "hook_type": htype if htype in HOOK_TYPES else "other",
        "pattern_template": str(data.get("pattern_template", "") or "")[:160],
        "why_it_works": str(data.get("why_it_works", "") or "")[:200],
        "source": source,
    }


def try_extract_hook(video_id: str, title: str, api_key: Optional[str], dry_run: bool) -> Optional[Dict[str, Any]]:
    if dry_run or not api_key:
        return mock_hook(title)
    try:
        return extract_hook(video_id, api_key)
    except Exception as e:
        logger.warning(f"후킹 추출 실패 ({video_id}): {e}")
        return None
