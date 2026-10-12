"""
core/scene_images.py - 씬별 AI 이미지 생성 (문학·역사·철학 등 장르 확장용 일러스트)

- 키: 기존 .env GEMINI_API_KEY 하나 (추가 키 없음)
- 모델: .env IMAGE_MODEL (기본 gemini-3.1-flash-image). 2026-10 확인 결과 이 키로는 Imagen(imagen-3.0-generate-002·
  imagen-4.0)이 404(NOT_FOUND)라 Gemini 네이티브 이미지 생성을 씀. IMAGE_MODEL을 imagen-* 로 바꾸면 Imagen 호출 경로로 동작
- 9:16, 해상도 .env IMAGE_SIZE (기본 1K = 768x1376 - 쇼츠 모바일 화면에 충분하고 빠르고 저렴, 2K = 1536x2752)
- 흐름: 대본(씬별 대사·분위기) → 대본 엔진(Claude→GPT→Gemini)이 영상 전체 공통 화풍 + 씬별 영문 프롬프트 작성 → 씬마다 이미지 생성(동시 3장)
- 안전: 글자·로고·실존 인물 얼굴·책 표지·실제 매장 묘사 금지 (책 표지·책방은 진짜 사진만 - 표지는 마지막 씬 실물)
- 같은 프롬프트는 다시 만들지 않음 (작업 폴더 캐시)
"""
import hashlib
import io
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFilter

logger = logging.getLogger("ad_factory.scene_images")

DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
ASPECT = "9:16"
WORKERS = 3
SAFETY_SUFFIX = ("vertical 9:16 composition, no text, no letters, no words, no captions, no logos, no watermark, "
                 "no book covers, no recognizable real people or faces of real persons")
DEFAULT_STYLE = "warm painterly illustration, soft cinematic light, muted earthy palette, gentle grain, contemplative mood"


def image_model() -> str:
    return os.getenv("IMAGE_MODEL", "").strip() or DEFAULT_IMAGE_MODEL


def image_size() -> str:
    return os.getenv("IMAGE_SIZE", "").strip() or "1K"


class ImageGenError(RuntimeError):
    pass


# ---------------------------------------------------------------- 1) 대본 → 영문 프롬프트
PROMPT_SYSTEM = ("You are an art director for vertical short-form videos about books (literature, history, philosophy). "
                 "You turn Korean narration lines into English image-generation prompts. Output one JSON object only.")


def build_prompts(lines: List[Dict[str, str]], book: Dict[str, str], api_key: Optional[str] = None,
                  mock: bool = False) -> Dict[str, Any]:
    """
    lines: [{scene_index, role, text}] (CTA 제외) → {"style": 공통 화풍, "prompts": {scene_index: 영문 프롬프트}, "engine": ...}
    책 제목·저자는 화면에 글자로 나오면 안 되므로 프롬프트에 넣지 않음 (주제·시대·정서만)
    """
    from core.llm import script_llm_ready
    base = {"style": DEFAULT_STYLE, "engine": "template",
            "prompts": {l["scene_index"]: _fallback_prompt(l) for l in lines}}
    if mock or not script_llm_ready(api_key):
        return base
    from core.llm import generate_json
    listing = "\n".join(f"scene {l['scene_index']} ({l['role']}): {l['text']}" for l in lines)
    prompt = f"""
Book genre/theme (facts, do not render as text): {book.get('genre') or ''} / {book.get('info', '')[:600]}
Narration lines (Korean):
{listing}

Write:
- "style": ONE shared visual style for the whole video (medium, palette, light) so all scenes look like one series.
  Prefer illustration/painting styles over photorealism. Match the book's era and mood.
- "scenes": for each scene, an English prompt (25~60 words) describing a concrete visual metaphor for that line:
  setting, subject, light, camera framing. Show emotions through objects, places, nature, silhouettes or people seen
  from behind - never a specific real person's face, never a book cover, never a shop interior that claims to be a real store,
  no text or letters anywhere.
JSON only: {{"style": "...", "scenes": [{{"scene_index": 1, "prompt": "..."}}]}}
"""
    try:
        data, engine = generate_json([prompt], PROMPT_SYSTEM, temperature=0.6, max_tokens=2500, gemini_api_key=api_key,
                                     purpose="씬 이미지 프롬프트")
        out = dict(base)
        out["style"] = str(data.get("style") or DEFAULT_STYLE)[:300]
        for s in data.get("scenes") or []:
            try:
                k = int(s.get("scene_index"))
            except (TypeError, ValueError):
                continue
            p = re.sub(r"\s+", " ", str(s.get("prompt") or "")).strip()
            if k in out["prompts"] and len(p) > 20:
                out["prompts"][k] = p[:600]
        out["engine"] = engine
        return out
    except Exception as e:
        logger.warning(f"이미지 프롬프트 작성 실패 → 기본 프롬프트: {e}")
        return base


ROLE_SCENES = {
    "hook": "a lone figure seen from behind at a rain-streaked window at night, city lights blurred",
    "situation": "an empty bus seat by a window at dusk, headlights trailing, quiet fatigue",
    "message": "a single lamp glowing on a wooden desk beside an open notebook, pages lifting in a soft breeze",
    "quote": "a quiet hillside under a vast starry sky, wind moving through tall grass",
    "comfort": "first morning light spilling across a calm field, a path leading forward",
    "book": "a cozy reading nook by a window, warm tea, soft afternoon light",
    "store": "sunlit wooden shelves and a reading chair, dust motes in the light",
}


def _fallback_prompt(line: Dict[str, str]) -> str:
    return ROLE_SCENES.get(line.get("role", ""), ROLE_SCENES["message"])


def full_prompt(style: str, scene_prompt: str) -> str:
    return f"{scene_prompt}. Style: {style}. {SAFETY_SUFFIX}."


# ---------------------------------------------------------------- 2) 이미지 생성
def _client(api_key: Optional[str]):
    from google import genai
    key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise ImageGenError("GEMINI_API_KEY가 없어요 (.env). 이미지 생성은 기존 Gemini 키 하나로 해요.")
    return genai.Client(api_key=key)


def generate_one(prompt: str, out_path: str, api_key: Optional[str] = None, model: Optional[str] = None) -> str:
    """프롬프트 하나 → 9:16 이미지 파일. 모델 이름이 imagen-* 이면 Imagen API, 아니면 Gemini 네이티브 이미지 생성"""
    from google.genai import types
    from core.guardrail import validate_model_call
    model = validate_model_call(model or image_model(), category="image")
    client = _client(api_key)
    data = None
    last = None
    for attempt in (1, 2):
        try:
            if model.startswith("imagen"):
                r = client.models.generate_images(model=model, prompt=prompt,
                                                  config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio=ASPECT))
                if r.generated_images:
                    data = r.generated_images[0].image.image_bytes
            else:
                r = client.models.generate_content(
                    model=model, contents=[prompt],
                    config=types.GenerateContentConfig(response_modalities=["IMAGE"],
                                                       image_config=types.ImageConfig(aspect_ratio=ASPECT, image_size=image_size())))
                for part in (r.candidates[0].content.parts if r.candidates and r.candidates[0].content else []) or []:
                    if getattr(part, "inline_data", None) and part.inline_data.data:
                        data = part.inline_data.data
                        break
            if data:
                break
            last = "이미지가 비어 있음 (안전 필터로 막혔을 수 있음)"
        except Exception as e:
            last = str(e)[:300]
    if not data:
        raise ImageGenError(f"이미지 생성 실패 ({model}): {last}")
    with Image.open(io.BytesIO(data)) as im:
        im.convert("RGB").save(out_path, "JPEG", quality=94)
    return out_path


def _mock_image(prompt: str, out_path: str) -> str:
    """테스트용 자리표시 그림 (API 호출 없음): 프롬프트마다 다른 색 그라데이션"""
    h = int(hashlib.md5(prompt.encode()).hexdigest()[:6], 16)
    c1 = ((h >> 16) & 255, (h >> 8) & 255, h & 255)
    im = Image.new("RGB", (768, 1376), c1)
    d = ImageDraw.Draw(im)
    for y in range(0, 1376, 8):
        k = y / 1376
        d.rectangle([0, y, 768, y + 8], fill=tuple(int(c * (1 - 0.5 * k)) for c in c1))
    im.filter(ImageFilter.GaussianBlur(2)).save(out_path, "JPEG", quality=90)
    return out_path


def generate_scene_images(scenes: List[Dict[str, Any]], out_dir: str, book: Optional[Dict[str, str]] = None,
                          api_key: Optional[str] = None, mock: bool = False, skip_roles=("cta",),
                          on_done: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:
    """
    CTA를 뺀 씬마다 AI 이미지 → {"images": {scene_index: path}, "prompts": {...}, "style": ..., "model": ..., "errors": [...]}
    같은 프롬프트로 이미 만든 파일이 있으면 재사용. 실패한 씬은 errors에 (호출부가 대체 처리)
    """
    os.makedirs(out_dir, exist_ok=True)
    lines = [{"scene_index": int(s["scene_index"]), "role": s.get("role", ""), "text": s.get("voiceover", "")}
             for s in scenes if s.get("role") not in skip_roles]
    plan = build_prompts(lines, book or {}, api_key, mock)
    model = "mock" if mock else image_model()
    jobs = []
    for l in lines:
        fp = full_prompt(plan["style"], plan["prompts"][l["scene_index"]])
        tag = hashlib.md5(f"{model}|{image_size()}|{fp}".encode()).hexdigest()[:10]
        jobs.append((l["scene_index"], fp, os.path.join(out_dir, f"ai_scene_{l['scene_index']:02d}_{tag}.jpg")))
    images, errors, done = {}, [], [0]

    def run(job):
        k, fp, path = job
        if not os.path.exists(path):
            (_mock_image if mock else lambda p, o: generate_one(p, o, api_key))(fp, path)
        return k, path

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(run, j): j for j in jobs}
        for fut in futs:
            k = futs[fut][0]
            try:
                images[k] = fut.result()[1]
            except Exception as e:
                errors.append(f"씬 {k}: {e}")
            done[0] += 1
            if on_done:
                on_done(done[0], len(jobs))
    with open(os.path.join(out_dir, "ai_scene_prompts.json"), "w", encoding="utf-8") as f:
        json.dump({"model": model, "size": image_size(), "style": plan["style"], "engine": plan["engine"],
                   "prompts": {str(k): fp for k, fp, _ in jobs}}, f, ensure_ascii=False, indent=2)
    logger.info(f"씬 이미지 {len(images)}/{len(jobs)}장 생성 ({model}, {image_size()}, 프롬프트 {plan['engine']})")
    return {"images": images, "prompts": {k: fp for k, fp, _ in jobs}, "style": plan["style"], "model": model,
            "errors": errors, "engine": plan["engine"]}
