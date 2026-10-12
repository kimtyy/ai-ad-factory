"""
core/guardrail.py - API 호출 보안 가드레일 (고가 모델 무단 호출 원천 차단)
"""
import sys
import logging

logger = logging.getLogger("ad_factory.guardrail")

# 허용 모델 화이트리스트
ALLOWED_LLM_MODELS = frozenset([
    "gemini-3.6-flash",
    # 대본 엔진 자동 백업 (core/llm/router.py): 1순위 Claude Haiku, 2순위 OpenAI 경량 모델
    "claude-haiku-5-5",
    "claude-haiku-4-5",
    "gpt-4o-mini",
])

ALLOWED_IMAGE_MODELS = frozenset([
    "imagen-3.0-generate-001",
    "imagen-3.0-generate-002",
    # 2026-10: 이 Gemini 키로는 Imagen이 404 → Gemini 네이티브 이미지 생성 (시골책방 씬 일러스트, core/scene_images.py)
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite-image",
])

# 비디오 생성 API 모델은 전면 차단 (EMPTY - 일체 허용하지 않음)
ALLOWED_VIDEO_MODELS = frozenset([])

# 명시적 금지 키워드 (대소문자 무시)
BLOCKED_KEYWORDS = (
    "veo",
    "video",
    "sora",
    "runway",
    "pika",
    "gemini-2.5",
    "gemini-1.5",
    "gemini-pro",
    "preview",
)


class GuardrailViolationError(PermissionError):
    """허가되지 않은 모델 호출 시 즉각 발생하는 보안 예외"""
    pass


def validate_model_call(model_name: str, category: str = "llm") -> str:
    """
    Google API 엔드포인트 호출 직전에 모델명을 검증하고 콘솔에 출력합니다.
    허용 목록에 없거나 금지 키워드가 포함된 고가 모델 호출 시도시 즉각 Exception을 발생시켜 중단합니다.

    Args:
        model_name: 호출하려는 모델 식별자 (예: 'gemini-3.6-flash')
        category: 'llm', 'image', 'video' 중 하나

    Returns:
        검증 완료된 모델명 문자열

    Raises:
        GuardrailViolationError: 비인가 모델이거나 금지 키워드가 포함된 경우
    """
    clean_model = model_name.strip()
    lower_model = clean_model.lower()

    # 표준 콘솔 및 로거에 즉시 요청 출력
    print(f"\n[API GUARDRAIL CHECK] 요청 모델: '{clean_model}' (카테고리: {category})", file=sys.stderr, flush=True)

    # 1. 명시적 금지 키워드 탐색
    for kw in BLOCKED_KEYWORDS:
        if kw in lower_model:
            err_msg = (
                f"\n❌ [API GUARDRAIL VIOLATION] 고가/미지원 모델 호출 차단!\n"
                f"   - 감지된 금지 키워드: '{kw}'\n"
                f"   - 요청 모델명: '{clean_model}'\n"
                f"   - 사유: Veo 등 고가 비디오 모델 및 비인가 모델은 무단 과금 방지를 위해 영구 차단되었습니다."
            )
            print(err_msg, file=sys.stderr, flush=True)
            logger.critical(err_msg)
            raise GuardrailViolationError(err_msg)

    # 2. 카테고리별 화이트리스트 검증
    if category == "llm":
        if clean_model not in ALLOWED_LLM_MODELS:
            err_msg = (
                f"\n❌ [API GUARDRAIL VIOLATION] 허용되지 않은 LLM 모델 호출 차단: '{clean_model}'\n"
                f"   - 허용 목록: {list(ALLOWED_LLM_MODELS)}"
            )
            print(err_msg, file=sys.stderr, flush=True)
            logger.critical(err_msg)
            raise GuardrailViolationError(err_msg)

    elif category == "image":
        if clean_model not in ALLOWED_IMAGE_MODELS:
            err_msg = (
                f"\n❌ [API GUARDRAIL VIOLATION] 허용되지 않은 이미지 모델 호출 차단: '{clean_model}'\n"
                f"   - 허용 목록: {list(ALLOWED_IMAGE_MODELS)}"
            )
            print(err_msg, file=sys.stderr, flush=True)
            logger.critical(err_msg)
            raise GuardrailViolationError(err_msg)

    elif category == "video":
        err_msg = (
            f"\n❌ [API GUARDRAIL VIOLATION] 모든 원격 비디오 생성 API는 전면 차단되었습니다!\n"
            f"   - 요청 모델명: '{clean_model}'\n"
            f"   - 비디오 생성은 오직 로컬 Ken Burns(2D 모션 합성)만 허용됩니다."
        )
        print(err_msg, file=sys.stderr, flush=True)
        logger.critical(err_msg)
        raise GuardrailViolationError(err_msg)

    else:
        err_msg = f"❌ [API GUARDRAIL VIOLATION] 알 수 없는 카테고리: '{category}'"
        print(err_msg, file=sys.stderr, flush=True)
        logger.critical(err_msg)
        raise GuardrailViolationError(err_msg)

    # 3. 통과 시 콘솔 승인 출력
    print(f"✅ [API GUARDRAIL PASSED] 허용 모델 호출 승인: '{clean_model}'\n", file=sys.stderr, flush=True)
    logger.info(f"API 가드레일 승인: 모델 '{clean_model}' (카테고리: {category})")
    return clean_model
