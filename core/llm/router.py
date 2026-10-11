"""
core/llm/router.py - 대본 생성 LLM 라우터: Claude → OpenAI → Gemini 자동 백업(Fallback)

- 1순위 Claude (anthropic SDK, ANTHROPIC_API_KEY, 모델 ANTHROPIC_MODEL 기본 claude-haiku-5-5)
  ※ 요청하신 Claude 3.5 Haiku(claude-3-5-haiku-20241022)는 2026-02-19 서비스 종료 → 호출하면 매번 오류.
     현행 Haiku인 claude-haiku-5-5(입력 $0.10 / 출력 $0.50 per 1M tokens)를 기본값으로 사용
- 2순위 OpenAI (openai SDK, OPENAI_API_KEY, 모델 OPENAI_MODEL 기본 gpt-4o-mini)
- 3순위 Gemini (기존 엔진, GEMINI_API_KEY) - 앞의 둘이 모두 실패하거나 키가 없을 때 마지막 안전망
- API 오류·잔액 부족·Rate Limit·네트워크·응답 JSON 파싱 실패·안전 거절이면 SDK 재시도 1회 뒤 즉시 다음 엔진으로
- contents: 기존 Gemini 호출과 같은 형식 (문자열과 PIL 이미지를 순서대로 섞은 리스트) → 엔진별 형식으로 변환
- Gemini 전용 객체(업로드한 레퍼런스 영상 등)가 섞여 있으면 Gemini만 사용
- 모든 모델은 core.guardrail.validate_model_call 허용 목록을 통과해야 호출 (고가 모델 무단 호출 차단)
"""
import base64
import io
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("ad_factory.llm")

DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
PROVIDER_ORDER = ("claude", "openai", "gemini")
PROVIDER_LABEL = {"claude": "Claude", "openai": "OpenAI", "gemini": "Gemini"}
IMAGE_MAX_SIDE = 1024          # 이미지는 긴 변 1024px로 줄여 보냄 (토큰·시간 절약, 장면 판단엔 충분)
REQUEST_TIMEOUT = 90.0
SDK_RETRIES = 1                # SDK 자체 재시도 1회 뒤 다음 엔진으로 (즉시 전환)


class LLMUnavailable(RuntimeError):
    """모든 엔진이 실패 (각 엔진의 실패 사유를 errors에 담음)"""

    def __init__(self, errors: List[str]):
        super().__init__(" | ".join(errors) or "사용 가능한 LLM 키가 없습니다")
        self.errors = errors


def _is_image(x: Any) -> bool:
    try:
        from PIL import Image
        return isinstance(x, Image.Image)
    except Exception:
        return False


def _image_b64(img) -> str:
    im = img.convert("RGB")
    if max(im.size) > IMAGE_MAX_SIDE:
        im.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def parse_json_text(text: str) -> Any:
    """응답 텍스트 → JSON (코드 블록 표시·앞뒤 설명이 섞여도 첫 JSON 객체/배열을 찾아 파싱)"""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"[\[{]", t)
        if not m:
            raise
        return json.JSONDecoder().raw_decode(t[m.start():])[0]


# ---------------------------------------------------------------- 엔진별 호출
def _call_claude(contents: List[Any], system: str, temperature: float, max_tokens: int) -> str:
    import anthropic
    from core.guardrail import validate_model_call
    model = validate_model_call(os.getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL).strip(), category="llm")
    blocks: List[Dict[str, Any]] = []
    for c in contents:
        if isinstance(c, str):
            if c.strip():
                blocks.append({"type": "text", "text": c})
        elif _is_image(c):
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _image_b64(c)}})
    client = anthropic.Anthropic(max_retries=SDK_RETRIES, timeout=REQUEST_TIMEOUT)
    # Haiku 5.5: 사고(thinking)는 기본 켜짐·끌 수 없음, 샘플링 값(temperature)은 기본값만 허용 → 보내지 않음. 깊이는 effort로 조절
    resp = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                  messages=[{"role": "user", "content": blocks}],
                                  output_config={"effort": "medium"})
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"Claude 안전 거절 ({getattr(resp.stop_details, 'category', None)})")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Claude 응답이 max_tokens에서 잘림")
    return "".join(b.text for b in resp.content if b.type == "text")


def _call_openai(contents: List[Any], system: str, temperature: float, max_tokens: int) -> str:
    import openai
    from core.guardrail import validate_model_call
    model = validate_model_call(os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip(), category="llm")
    parts: List[Dict[str, Any]] = []
    for c in contents:
        if isinstance(c, str):
            if c.strip():
                parts.append({"type": "text", "text": c})
        elif _is_image(c):
            parts.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _image_b64(c)}})
    client = openai.OpenAI(max_retries=SDK_RETRIES, timeout=REQUEST_TIMEOUT)
    resp = client.chat.completions.create(
        model=model, temperature=temperature, max_tokens=max_tokens, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": parts}])
    choice = resp.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("OpenAI 응답이 max_tokens에서 잘림")
    if getattr(choice.message, "refusal", None):
        raise RuntimeError(f"OpenAI 거절: {choice.message.refusal}")
    return choice.message.content or ""


def _call_gemini(contents: List[Any], system: str, temperature: float, max_tokens: int, api_key: Optional[str]) -> str:
    from google import genai
    from google.genai import types
    from core.guardrail import validate_model_call
    client = genai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY"))   # 변수로 잡아 둬야 호출 중에 닫히지 않음
    res = client.models.generate_content(
        model=validate_model_call(DEFAULT_GEMINI_MODEL, category="llm"), contents=contents,
        config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                           temperature=temperature))
    return res.text


def available_providers(gemini_api_key: Optional[str] = None, contents: Optional[List[Any]] = None) -> List[str]:
    """키가 있는 엔진 (순서 유지). Gemini 전용 객체가 섞이면 Gemini만"""
    gemini_only = any(not isinstance(c, str) and not _is_image(c) for c in (contents or []))
    keys = {"claude": os.getenv("ANTHROPIC_API_KEY", "").strip(), "openai": os.getenv("OPENAI_API_KEY", "").strip(),
            "gemini": (gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()}
    order = ["gemini"] if gemini_only else list(PROVIDER_ORDER)
    return [p for p in order if keys[p]]


def script_llm_ready(gemini_api_key: Optional[str] = None) -> bool:
    """대본 엔진을 쓸 수 있는지: 넘겨받은 Gemini 키 또는 Claude·OpenAI 키 중 하나라도 있으면 True"""
    return bool((gemini_api_key or "").strip() or os.getenv("ANTHROPIC_API_KEY", "").strip()
                or os.getenv("OPENAI_API_KEY", "").strip())


# 테스트에서 엔진 호출을 바꿔 끼우는 지점: {"claude": fn, ...} (fn(contents, system, temperature, max_tokens) -> str)
_overrides: Dict[str, Callable[..., str]] = {}


def generate_json(contents: List[Any], system: str, *, temperature: float = 0.6, max_tokens: int = 8000,
                  gemini_api_key: Optional[str] = None, purpose: str = "대본") -> Tuple[Any, str]:
    """
    → (파싱된 JSON, 실제로 응답한 엔진 'claude'|'openai'|'gemini')
    Claude부터 차례로 시도, 실패하면 사유를 남기고 다음 엔진. 전부 실패하면 LLMUnavailable
    """
    errors: List[str] = []
    providers = available_providers(gemini_api_key, contents)
    if not providers:
        raise LLMUnavailable(["ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY 가 모두 비어 있음"])
    for p in providers:
        try:
            if p in _overrides:
                text = _overrides[p](contents, system, temperature, max_tokens)
            elif p == "claude":
                text = _call_claude(contents, system, temperature, max_tokens)
            elif p == "openai":
                text = _call_openai(contents, system, temperature, max_tokens)
            else:
                text = _call_gemini(contents, system, temperature, max_tokens, gemini_api_key)
            data = parse_json_text(text)
            if errors:
                logger.warning(f"[{purpose}] {PROVIDER_LABEL[p]}로 자동 전환해 생성 (앞선 실패: {' | '.join(errors)})")
            else:
                logger.info(f"[{purpose}] {PROVIDER_LABEL[p]}로 생성")
            return data, p
        except Exception as e:      # 엔진별 API 오류(잔액 부족·Rate Limit·5xx·네트워크)·거절·JSON 오류 → 다음 엔진
            reason = f"{PROVIDER_LABEL[p]}: {type(e).__name__}: {str(e)[:160]}"
            logger.warning(f"[{purpose}] {reason} → 다음 엔진으로 전환")
            errors.append(reason)
    raise LLMUnavailable(errors)


# ---------------------------------------------------------------- 대본 규칙 위반 줄 다시 쓰기 (한 번)
def repair_scene_lines(data: Any, find_violation: Callable[[str], Optional[str]], system: str, rules: str, *,
                       gemini_api_key: Optional[str] = None, field: str = "voiceover") -> Tuple[Any, List[str]]:
    """
    생성된 JSON의 scenes[*].voiceover 중 규칙 위반 줄만 모아 같은 엔진 체인으로 한 번 다시 쓰게 함.
    → (고친 data, 기록). 고친 줄도 위반이면 그대로 두어 플래너의 기존 안전 대체 로직이 처리
    """
    scenes = data.get("scenes") if isinstance(data, dict) else data
    if not isinstance(scenes, list):
        return data, []
    bad = [(i, str(s.get(field) or ""), find_violation(str(s.get(field) or ""))) for i, s in enumerate(scenes) if isinstance(s, dict)]
    bad = [(i, t, v) for i, t, v in bad if t and v]
    if not bad:
        return data, []
    listing = "\n".join(f"- scene_index {i + 1}: \"{t}\" (위반: {v})" for i, t, v in bad)
    prompt = (f"아래 숏폼 대사들이 규칙을 어겼습니다. 뜻과 사실은 그대로 두고, 규칙에 맞게 각 줄을 다시 쓰세요.\n{rules}\n\n"
              f"[고칠 대사]\n{listing}\n\n"
              'JSON만 출력: {"fixes": [{"scene_index": 1, "voiceover": "..."}]}')
    try:
        fixed, provider = generate_json([prompt], system, temperature=0.5, max_tokens=2000, gemini_api_key=gemini_api_key,
                                        purpose="대본 규칙 보정")
    except Exception as e:
        return data, [f"규칙 보정 실패: {e}"]
    log = []
    for f in (fixed.get("fixes") if isinstance(fixed, dict) else None) or []:
        try:
            k = int(f.get("scene_index")) - 1
        except (TypeError, ValueError):
            continue
        new = str(f.get("voiceover") or "").strip()
        if 0 <= k < len(scenes) and new and isinstance(scenes[k], dict):
            log.append(f"씬 {k + 1}: '{scenes[k].get(field)}' → '{new}' ({PROVIDER_LABEL.get(provider, provider)})")
            scenes[k][field] = new
    return data, log
