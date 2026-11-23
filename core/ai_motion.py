"""
core/ai_motion.py - 작업실 1단계 사진 ➔ AI 모션 클립(Image-to-Video) 변환 엔진

Google GenAI SDK (Veo)를 사용하여 정지 사진을 9:16 세로형 모션 비디오(MP4)로 변환합니다.
- API 키: 기존 GEMINI_API_KEY 환경변수 재사용
- 모델: VEO_MODEL 환경변수로 지정 가능. 지정한 모델(또는 기본 veo-3.1-fast)이 이 API 키에서 없으면(404) 다음 모델로 자동 폴백 (VEO_FALLBACK_MODELS)
- 프리셋:
  1) sizzle: 음식 시즐감 (김 모락모락 · 지글지글)
  2) mukbang: 맛있게 먹는 먹방 연출 (AI 모델 시식)
  3) cinematic: 부드러운 시네마틱 무빙
  4) custom: 직접 입력
- 공정위 지침 대응: AI 연출 고지 플래그 지원
- 후처리: 오디오 음소거(-an), 9:16 규격화
"""
import os
import re
import time
import shutil
import logging
import subprocess
from typing import Optional, Dict, Any, Tuple
from PIL import Image

logger = logging.getLogger("ad_factory.ai_motion")

# 모델 이름은 Google이 바꾸는 일이 잦아 '있으면 쓰고 없으면(404) 다음 것'으로 시도한다. 앞쪽일수록 최신·빠른(fast) 모델.
# Veo 3.x는 영상 길이가 4·6·8초만 되고 소리를 항상 함께 만든다(우리가 ffmpeg로 제거), Veo 2는 5~8초·무음.
DEFAULT_VEO_MODEL = "veo-3.1-fast-generate-001"
VEO_FALLBACK_MODELS = ("veo-3.1-fast-generate-001", "veo-3.1-fast-generate-preview", "veo-3.0-fast-generate-001", "veo-2.0-generate-001")

MOTION_PRESETS = {
    "sizzle": {
        "label": "🔥 음식 시즐감 (김 모락모락 · 지글지글)",
        "prompt": "Cinematic close-up of this delicious food, gentle steam rising smoothly, glistening rich sauce texture, delicate sizzle, subtle appetizing macro push-in camera motion, photo realistic, 4k high quality, keep original food intact and recognizable",
        "default_disclosure": False,
    },
    "mukbang": {
        "label": "😋 맛있게 먹는 먹방 연출 (AI 모델 시식)",
        "prompt": "A friendly Korean customer taking a delicious appetizing bite of this exact food with chopsticks or spoon, smiling happily in cozy restaurant, realistic casual vlog style, natural authentic motion, 4k high quality",
        "default_disclosure": True,
    },
    "cinematic": {
        "label": "🎥 부드러운 시네마틱 무빙",
        "prompt": "Smooth elegant cinematic camera orbit around the food and table, warm soft lighting, shallow depth of field, premium commercial b-roll, high resolution, fluid stable motion",
        "default_disclosure": False,
    },
    "custom": {
        "label": "✍️ 직접 입력",
        "prompt": "",
        "default_disclosure": False,
    },
}

BILLING_REQUIRED_MSG = (
    "Google AI Studio 유료 결제(Pay-as-you-go)가 연결된 GEMINI_API_KEY가 필요합니다. "
    "(무료 티어 키에서는 영상 생성이 제한됩니다)"
)
QUOTA_MSG = "Google AI 영상 생성 호출 한도를 넘었어요. 잠시 뒤 다시 시도하거나, AI Studio에서 결제·한도 설정을 확인해 주세요."
NO_MODEL_MSG = ("이 API 키로 쓸 수 있는 Veo 영상 모델을 찾지 못했어요 (시도: {tried}). 보통 유료 결제가 연결되지 않은 키이거나 Veo 접근 권한이 없는 경우입니다. "
                "AI Studio에서 결제 연결을 확인하거나, 쓸 수 있는 모델 이름을 환경변수 VEO_MODEL에 적어 주세요.")


class AiMotionError(Exception):
    """AI 모션 영상 생성 중 발생하는 비즈니스/사용자 안내 예외"""
    pass


def get_veo_model() -> str:
    """사용할 Veo 모델명 반환 (VEO_MODEL 환경변수로 오버라이드 가능)"""
    return os.getenv("VEO_MODEL", "").strip() or DEFAULT_VEO_MODEL


def candidate_models(model: Optional[str] = None) -> list:
    """시도할 모델 순서: 호출자·VEO_MODEL이 지정한 모델 → 기본 폴백 목록 (중복 제거)"""
    first = (model or os.getenv("VEO_MODEL", "")).strip()
    out = []
    for m in ([first] if first else []) + list(VEO_FALLBACK_MODELS):
        m = m.replace("models/", "")
        if m and m not in out:
            out.append(m)
    return out


def duration_for_model(model: str, requested: int) -> int:
    """Veo 3.x는 4·6·8초만, Veo 2는 5~8초만 받는다 → 요청 길이에 가장 가까운(같거나 긴) 허용값"""
    r = int(requested or 5)
    if "veo-3" in (model or ""):
        return next((d for d in (4, 6, 8) if d >= r), 8)
    return max(5, min(8, r))


def classify_api_error(code: Optional[int], status: str, message: str) -> str:
    """Google API 오류 → 'not_found' | 'billing' | 'quota' | 'invalid' | 'other'. 400 하나를 전부 '결제 필요'로 보지 않는다 (잘못된 요청 값은 그대로 알려야 고칠 수 있음)"""
    text = f"{status} {message}".lower()
    if code == 404 or "not_found" in text or "is not found" in text:
        return "not_found"
    if code == 429 or "resource_exhausted" in text or "quota" in text:
        return "quota"
    if code == 403 or "permission_denied" in text or "billing" in text or "failed_precondition" in text or "paid" in text or "free tier" in text:
        return "billing"
    if code == 400 or "invalid_argument" in text:
        return "invalid"
    return "other"


def build_motion_prompt(preset: str = "sizzle", custom_prompt: str = "") -> Tuple[str, bool]:
    """프리셋 키와 커스텀 프롬프트를 결합해 (최종 영문 지시문, 기본 AI 연출 고지 여부) 반환"""
    cfg = MOTION_PRESETS.get(preset, MOTION_PRESETS["sizzle"])
    base_prompt = cfg["prompt"]
    default_disclosure = cfg["default_disclosure"]

    if preset == "custom":
        user_text = (custom_prompt or "").strip()
        final_prompt = user_text if user_text else MOTION_PRESETS["sizzle"]["prompt"]
        return final_prompt, False

    if custom_prompt and custom_prompt.strip():
        return f"{base_prompt}, {custom_prompt.strip()}", default_disclosure
    return base_prompt, default_disclosure


def _sanitize_video_with_ffmpeg(input_video_path: str, output_video_path: str, max_duration: float = 5.0) -> str:
    """
    생성된 비디오를 ffmpeg로 정제:
    1) 자체 오디오 음소거(-an): 샵스토리의 나레이션/BGM과 겹침 방지
    2) 9:16 (1080x1920) 규격화
    3) yuv420p 인코딩
    """
    from core.render.ffmpeg_assemble import ffmpeg_exe
    os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
    exe = ffmpeg_exe()

    # 오디오 제거 + 최대 길이 제한 + 1080x1920 세로형 보장
    cmd = [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-i", input_video_path,
        "-t", f"{max_duration:.2f}",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        output_video_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0 or not os.path.exists(output_video_path):
        logger.warning(f"ffmpeg sanitize warning: {res.stderr}. Using raw video.")
        shutil.copy2(input_video_path, output_video_path)
    return output_video_path


def _create_mock_motion_video(image_path: str, output_path: str, duration_sec: float = 4.0) -> str:
    """오프라인 / 테스트용 mock 9:16 모션 비디오 생성 (정지 사진에 미세한 줌인 효과 적용)"""
    from core.render.ffmpeg_assemble import ffmpeg_exe
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    exe = ffmpeg_exe()

    # 사진을 1080x1920 세로형으로 미세 줌인하며 4초 영상 생성
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"zoompan=z='min(zoom+0.0015,1.15)':d={int(duration_sec*24)}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=24"
    )
    cmd = [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-i", image_path,
        "-t", f"{duration_sec:.2f}",
        "-vf", vf,
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        output_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0 or not os.path.exists(output_path):
        # 초경량 폴백: 단순 정지영상 클립
        cmd_simple = [
            exe, "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-i", image_path,
            "-t", f"{duration_sec:.2f}",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-an",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            output_path
        ]
        subprocess.run(cmd_simple, capture_output=True, text=True, timeout=30)
    return output_path


def generate_ai_motion_clip(
    image_path: str,
    preset: str = "sizzle",
    custom_prompt: str = "",
    ai_disclosure: Optional[bool] = None,
    output_dir: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    mock: bool = False,
    duration_sec: int = 5,
) -> Dict[str, Any]:
    """
    정지 사진(image_path)을 9:16 AI 모션 MP4 비디오 클립으로 변환합니다.

    반환값:
    {
        "video_path": str,          # 생성된 mp4 파일 경로
        "preset": str,              # 적용된 프리셋
        "prompt": str,              # 전달된 프롬프트
        "model": str,               # 사용된 모델
        "duration_sec": float,      # 영상 길이
        "ai_disclosure": bool,      # AI 연출 고지 여부
    }
    """
    if not image_path or not os.path.exists(image_path):
        raise FileNotFoundError(f"변환할 원본 사진을 찾을 수 없습니다: {image_path}")

    # 프롬프트 및 AI 연출 고지 플래그 결정
    prompt, default_disc = build_motion_prompt(preset, custom_prompt)
    disclosure_final = ai_disclosure if ai_disclosure is not None else default_disc

    # 출력 디렉터리 준비
    out_dir = output_dir or os.path.join(os.path.dirname(os.path.abspath(image_path)), "ai_motion")
    os.makedirs(out_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    ts = int(time.time() * 1000)
    out_video_path = os.path.join(out_dir, f"motion_{base_name}_{preset}_{ts}.mp4")

    # Mock 모드 처리
    if mock or os.getenv("MOCK_AI_MOTION", "").strip() == "1":
        logger.info(f"[Mock AI Motion] 사진 '{image_path}' ➔ Mock 모션 클립 생성: {out_video_path}")
        _create_mock_motion_video(image_path, out_video_path, duration_sec=float(duration_sec))
        return {
            "video_path": out_video_path,
            "preset": preset,
            "prompt": prompt,
            "model": "mock-veo",
            "duration_sec": float(duration_sec),
            "ai_disclosure": disclosure_final,
        }

    # API 키 확인
    key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise AiMotionError("GEMINI_API_KEY가 설정되지 않았습니다. 환경변수 또는 설정에 API 키를 입력해 주세요.")

    try:
        from google import genai
        from google.genai import errors as genai_errors
        from google.genai import types
    except ImportError as e:
        raise AiMotionError(f"google-genai 패키지를 불러오지 못했어요: {e}")

    client = genai.Client(api_key=key)
    tried: list = []
    last_error = ""
    for veo_model in candidate_models(model):
        dur = duration_for_model(veo_model, duration_sec)
        tried.append(veo_model)
        logger.info(f"[AI Motion] 모델 {veo_model}로 영상 생성 시작... 프리셋={preset}, {dur}초, 프롬프트='{prompt[:60]}...'")
        try:
            raw_video_tmp = os.path.join(out_dir, f"_raw_{ts}.mp4")
            _veo_generate_to_file(client, types, veo_model, prompt, image_path, dur, raw_video_tmp)
        except _ModelNotFound as e:
            logger.warning(f"[AI Motion] 모델 '{veo_model}'을(를) 이 키에서 찾을 수 없어 다음 모델로 넘어갑니다: {e}")
            last_error = str(e)
            continue
        except genai_errors.APIError as e:
            kind = classify_api_error(getattr(e, "code", None), str(getattr(e, "status", "") or ""), str(getattr(e, "message", "") or e))
            logger.error(f"[AI Motion] API 오류({kind}): {e}")
            if kind == "not_found":
                last_error = str(e)
                continue
            raise _to_motion_error(kind, str(getattr(e, "message", "") or e))
        except (AiMotionError, TimeoutError, FileNotFoundError):
            raise
        except Exception as e:
            logger.error(f"[AI Motion Error] {type(e).__name__}: {e}")
            raise AiMotionError(f"AI 모션 영상 생성 중 오류가 발생했습니다: {e}")

        # 후처리 (무음 처리 및 9:16 1080x1920 정제 — Veo 3.x가 함께 만든 소리도 여기서 제거)
        _sanitize_video_with_ffmpeg(raw_video_tmp, out_video_path, max_duration=float(dur))
        try:
            os.remove(raw_video_tmp)
        except OSError:
            pass
        logger.info(f"[AI Motion] 영상 생성 완료: {out_video_path} ({veo_model})")
        return {
            "video_path": out_video_path,
            "preset": preset,
            "prompt": prompt,
            "model": veo_model,
            "duration_sec": float(dur),
            "ai_disclosure": disclosure_final,
        }
    raise AiMotionError(NO_MODEL_MSG.format(tried=", ".join(tried)))


class _ModelNotFound(Exception):
    pass


def _to_motion_error(kind: str, message: str) -> "AiMotionError":
    if kind == "billing":
        return AiMotionError(BILLING_REQUIRED_MSG)
    if kind == "quota":
        return AiMotionError(QUOTA_MSG)
    if kind == "invalid":
        return AiMotionError(f"영상 생성 요청이 거절됐어요: {message[:200]}")
    return AiMotionError(f"AI 영상 생성에 실패했습니다: {message[:200]}")


def _veo_generate_to_file(client, types, model: str, prompt: str, image_path: str, duration: int, out_path: str, poll_sec: float = 5.0, timeout_sec: int = 240) -> None:
    """Gemini API(Veo) 이미지→영상: generate_videos → 작업(operation) 완료까지 폴링 → 결과 Video를 files.download로 받아 저장.
    Gemini API는 generate_audio·seed·fps 등을 받지 않는다 (넘기면 SDK가 요청 전에 ValueError) — 소리는 Veo 3.x가 만들더라도 ffmpeg 후처리에서 제거한다."""
    image_obj = types.Image.from_file(location=image_path)
    config = types.GenerateVideosConfig(aspect_ratio="9:16", duration_seconds=int(duration), person_generation="allow_adult")
    from google.genai import errors as genai_errors
    try:
        operation = client.models.generate_videos(model=model, prompt=prompt, image=image_obj, config=config)
    except genai_errors.APIError as e:
        if classify_api_error(getattr(e, "code", None), str(getattr(e, "status", "") or ""), str(getattr(e, "message", "") or e)) == "not_found":
            raise _ModelNotFound(str(getattr(e, "message", "") or e)[:160])
        raise
    logger.info(f"[AI Motion] 비디오 생성 작업 접수됨 (작업명: {getattr(operation, 'name', '')}). 완료 대기 중...")
    start = time.time()
    while not operation.done:
        if time.time() - start > timeout_sec:
            raise TimeoutError(f"영상 생성 요청이 {timeout_sec // 60}분을 초과했습니다. 잠시 후 다시 시도해 주세요.")
        time.sleep(poll_sec)
        operation = client.operations.get(operation)

    if operation.error:                       # 작업 자체가 실패 (dict: code·message)
        err = operation.error if isinstance(operation.error, dict) else {"message": str(operation.error)}
        msg = str(err.get("message") or err)
        kind = classify_api_error(err.get("code") if isinstance(err.get("code"), int) else None, str(err.get("status") or ""), msg)
        logger.error(f"[AI Motion] 생성 작업 실패({kind}): {msg}")
        raise _to_motion_error("other" if kind == "not_found" else kind, msg)

    response = operation.response
    videos = getattr(response, "generated_videos", None) or []
    if not videos:
        reasons = list(getattr(response, "rai_media_filtered_reasons", None) or [])
        if getattr(response, "rai_media_filtered_count", 0) or reasons:
            raise AiMotionError("Google 안전 필터에 걸려 영상이 만들어지지 않았어요 (사람·미성년자·유명인이 보이는 사진 등). 다른 사진으로 시도해 주세요."
                                + (f" ({str(reasons[0])[:120]})" if reasons else ""))
        raise AiMotionError("AI 영상 생성 응답에 생성된 비디오가 없습니다.")

    video = videos[0].video
    data = getattr(video, "video_bytes", None)
    if not data:
        data = client.files.download(file=video)      # Video 객체를 그대로 넘기는 것이 SDK 방식 (uri 문자열이 아님). 반환값은 bytes
    if not data:
        raise AiMotionError("생성된 비디오 데이터를 읽을 수 없습니다.")
    with open(out_path, "wb") as f:
        f.write(data)
