"""
core/ai_motion.py - 작업실 1단계 사진 ➔ AI 모션 클립(Image-to-Video) 변환 엔진

Google GenAI SDK (Veo)를 사용하여 정지 사진을 9:16 세로형 모션 비디오(MP4)로 변환합니다.
- API 키: 기존 GEMINI_API_KEY 환경변수 재사용
- 모델: VEO_MODEL 환경변수로 오버라이드 가능 (기본: veo-2.0-generate-001)
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

DEFAULT_VEO_MODEL = "veo-2.0-generate-001"

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


class AiMotionError(Exception):
    """AI 모션 영상 생성 중 발생하는 비즈니스/사용자 안내 예외"""
    pass


def get_veo_model() -> str:
    """사용할 Veo 모델명 반환 (VEO_MODEL 환경변수로 오버라이드 가능)"""
    return os.getenv("VEO_MODEL", "").strip() or DEFAULT_VEO_MODEL


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

    veo_model = model or get_veo_model()
    logger.info(f"[AI Motion] 모델 {veo_model}로 영상 생성 시작... 프리셋={preset}, 프롬프트='{prompt[:60]}...'")

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)

        # 이미지 로드
        image_obj = types.Image.from_file(location=image_path)

        # 9:16 세로형 설정
        config = types.GenerateVideosConfig(
            aspect_ratio="9:16",
            duration_seconds=duration_sec,
            person_generation="allow_adult",
            generate_audio=False,
        )

        # 영상 생성 작업 요청 (비동기 LRO)
        operation = client.models.generate_videos(
            model=veo_model,
            prompt=prompt,
            image=image_obj,
            config=config,
        )

        logger.info(f"[AI Motion] 비디오 생성 작업 접수됨 (작업명: {operation.name}). 완료 대기 중...")

        # 폴링 대기 (최대 3분)
        poll_start = time.time()
        timeout = 180
        while not operation.done:
            if time.time() - poll_start > timeout:
                raise TimeoutError("영상 생성 요청이 3분을 초과했습니다. 잠시 후 다시 시도해 주세요.")
            time.sleep(5)
            operation = client.operations.get(operation)

        if operation.error:
            err_msg = str(operation.error)
            logger.error(f"[AI Motion] 생성 작업 실패: {err_msg}")
            # 유료 결제/할당량 관련 오류 체크
            if any(term in err_msg.lower() for term in ["quota", "resource_exhausted", "permission_denied", "billing", "403", "429"]):
                raise AiMotionError(BILLING_REQUIRED_MSG)
            raise AiMotionError(f"AI 영상 생성에 실패했습니다: {err_msg}")

        # 응답 비디오 추출 및 다운로드
        response = operation.response
        if not response or not getattr(response, "generated_videos", None) or len(response.generated_videos) == 0:
            raise AiMotionError("AI 영상 생성 응답에 생성된 비디오가 없습니다.")

        video_item = response.generated_videos[0].video
        raw_video_tmp = os.path.join(out_dir, f"_raw_{ts}.mp4")

        # video_bytes 또는 파일 다운로드 처리
        if hasattr(video_item, "video_bytes") and video_item.video_bytes:
            with open(raw_video_tmp, "wb") as f:
                f.write(video_item.video_bytes)
        elif hasattr(video_item, "uri") and video_item.uri:
            # client.files.download 또는 URI 직접 다운로드
            downloaded_bytes = client.files.download(file=video_item.uri)
            with open(raw_video_tmp, "wb") as f:
                f.write(downloaded_bytes)
        else:
            raise AiMotionError("생성된 비디오 데이터를 읽을 수 없습니다.")

        # 후처리 (무음 처리 및 9:16 1080x1920 정제)
        _sanitize_video_with_ffmpeg(raw_video_tmp, out_video_path, max_duration=float(duration_sec))
        if os.path.exists(raw_video_tmp):
            try:
                os.remove(raw_video_tmp)
            except Exception:
                pass

        logger.info(f"[AI Motion] 영상 생성 완료: {out_video_path}")
        return {
            "video_path": out_video_path,
            "preset": preset,
            "prompt": prompt,
            "model": veo_model,
            "duration_sec": float(duration_sec),
            "ai_disclosure": disclosure_final,
        }

    except Exception as e:
        err_str = str(e).lower()
        logger.error(f"[AI Motion Error] {e}")
        # 유료 티어 미연결 또는 무료 티어 제한 시 친절한 한국어 안내
        if any(term in err_str for term in ["403", "400", "429", "quota", "resource_exhausted", "permission_denied", "billing"]):
            raise AiMotionError(BILLING_REQUIRED_MSG)
        if isinstance(e, (AiMotionError, TimeoutError, FileNotFoundError)):
            raise
        raise AiMotionError(f"AI 모션 영상 생성 중 오류가 발생했습니다: {e}")
