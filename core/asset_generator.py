"""
core/asset_generator.py - Step 2: 실물 사진·영상 키프레임, 로컬 2D Ken Burns 모션 비디오, Edge-TTS 에셋 생성 및 캐싱 관리
(AI 이미지 생성은 쓰지 않음: 숙소·매장·상품 모두 실물만)
"""
import os
import re
import time
import logging
import subprocess
import tempfile
from typing import List, Dict, Any, Optional
from PIL import Image, ImageDraw
import numpy as np
import cv2

# Google GenAI SDK (Imagen 3 전용)
from google import genai
from google.genai import types
from gtts import gTTS
import imageio_ffmpeg
import json

from core.guardrail import validate_model_call
from core.media import normalize_ugc_clip, extract_thumbnail
from core.media.normalize import DEFAULT_TRIM_START
from core.render.commerce_layout import (COMMERCE_BG_BLUR, COMMERCE_BG_DIM, COMMERCE_BG_ZOOM, COMMERCE_CARD_BOX,
                                         COMMERCE_CARD_RADIUS, COMMERCE_CARD_ZOOM, shot_crop)

logger = logging.getLogger("ad_factory.asset_generator")

# 실물 사진/영상만 허용하는 모드 (AI 생성 숙소·매장·상품 사진은 허위광고)
# 쇼핑도 포함: AI 이미지(Imagen)로 장면을 만들면 화면 속 물건이 실제 상품과 달라짐 (전기히터 링크 → 세럼 장면). Imagen 호출은 제거됨
REAL_MEDIA_MODES = ("travel", "owner", "commerce")

# Ken Burns 모션 강도: 줌 1.0 ↔ 1.20배 (기존 1.12배 계획이 5초 클립 중 앞 3초만 노출돼 실제 약 1.07배로 정지 화면처럼 보였음)
KB_ZOOM = 1.20
KB_PAN_ZOOM = 1.06          # 패닝 중 함께 밀고 들어가는 줌 (평면 슬라이드 느낌 제거)
KB_PAN_MARGIN_X = 1.32      # 좌우 패닝 이동 폭 (프레임 폭 대비 원본 여유)
KB_PAN_MARGIN_Y = 1.25      # 상하 패닝 이동 폭

# 씬 중간 클립(Ken Burns·상품 카드) 인코딩: 최종 조립에서 한 번 더 인코딩하므로 화질 손실이 거의 없는 CRF 16에
# 가장 빠른 프리셋 (측정: 실사진 4초 클립 6.5초 → 3.5초. 파이썬 프레임 계산은 1.6초뿐이고 나머지가 파이프·인코딩).
# ffmpeg zoompan은 원본 해상도에선 흔들림 2.3배, 흔들림을 잡으려 2배 키우면 15초로 더 느려 채택하지 않음.
# 씬 클립 동시 생성도 측정해 봤으나 x264가 이미 멀티스레드라 7씬 22.4초 → 22.0초로 이득이 없어 순차 유지
CLIP_ENCODE_PARAMS = ["-preset", "ultrafast", "-crf", "16"]

# 씬 1 후킹 단독 노출 구간: 후킹 헤드라인만 화면에 띄우는 최소 시간 (그 뒤 서브 문장 자막·음성)
HOOK_SOLO_SEC = 1.5
HOOK_GAP_SEC = 0.15         # 후킹 음성이 1.5초보다 길면 끝나고 이만큼 쉬고 서브 문장 시작
HOOK_RATE = "+25%"          # 후킹은 짧고 빠르게 (기본 나레이션 +12%보다 빠름)
# Edge-TTS 결과의 앞(약 0.2초)·뒤(약 0.85초) 무음 → 씬마다 1초 넘는 공백이 생겨 이야기가 끊겨 들림. 이만큼만 남기고 잘라냄
TTS_KEEP_LEAD_SEC = 0.05
TTS_KEEP_TAIL_SEC = 0.12
# Edge-TTS가 가끔 앞부분만 담긴 잘린 파일(예: 26자 대사에 0.36초)을 정상 응답처럼 돌려줌 → 글자 수 대비 너무 짧으면 재시도
TTS_MIN_SEC_PER_CHAR = 0.06
TTS_RETRIES = 2
# 씬 음성 동시 생성 수: Edge-TTS는 네트워크 대기라 스레드로 겹치면 빨라짐
# (실측 7씬: 순차 12.9초 → 2개 5.0초 → 4개 3.5초 → 6개 3.4초, 음성 길이·후킹 경계 동일, 차단·잘림 없음)
TTS_WORKERS = 4


def tts_too_short(text: str, seconds: Optional[float]) -> bool:
    chars = len(re.sub(r"[\s\W_]", "", text or ""))
    return chars >= 4 and (seconds or 0.0) < max(0.6, chars * TTS_MIN_SEC_PER_CHAR)


def trim_silence(src: str, dst: str) -> str:
    """앞뒤 무음 제거 (-40dB 기준, 앞 TTS_KEEP_LEAD_SEC·뒤 TTS_KEEP_TAIL_SEC만 남김). 실패하면 원본 복사"""
    th = "-40dB"
    af = (f"silenceremove=start_periods=1:start_threshold={th}:start_silence={TTS_KEEP_LEAD_SEC},areverse,"
          f"silenceremove=start_periods=1:start_threshold={th}:start_silence={TTS_KEEP_TAIL_SEC},areverse")
    res = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-af", af,
                          "-c:a", "libmp3lame", "-q:a", "2", dst], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if res.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 500:
        logger.warning(f"무음 제거 실패, 원본 사용 ({src}): {res.stderr[-200:]}")
        import shutil
        shutil.copyfile(src, dst)
    return dst


def kb_ease(t: float) -> float:
    """ease-out (2차): 시작 속도가 가장 빠르고 끝에서 안착 → 씬 시작 순간부터 움직임이 보임"""
    t = min(max(t, 0.0), 1.0)
    return 1.0 - (1.0 - t) ** 2


def _audio_seconds(path: str) -> Optional[float]:
    """오디오 길이(초). ffmpeg 출력의 Duration을 읽음 (없거나 실패하면 None)"""
    if not path or not os.path.exists(path):
        return None
    try:
        res = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, text=True,
                             encoding="utf-8", errors="ignore", timeout=20)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr or "")
        if m:
            sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            return sec if sec > 0 else None
    except Exception as e:
        logger.debug(f"오디오 길이 확인 실패 ({path}): {e}")
    return None


def num_to_sino_korean(num: int) -> str:
    """숫자를 광고 구어체에 적합한 한자어 발음으로 변환 (예: 48 -> 사십팔, 3 -> 삼)"""
    digits = ['', '일', '이', '삼', '사', '오', '육', '칠', '팔', '구']
    units = ['', '십', '백', '천']

    if num == 0:
        return '영'
    if num == 100:
        return '백'

    snum = str(num)
    length = len(snum)
    res = ''

    if num >= 10000:
        man = num // 10000
        rest = num % 10000
        res += (num_to_sino_korean(man) if man > 1 else '') + '만 '
        if rest > 0:
            res += num_to_sino_korean(rest)
        return res.strip()

    for i, ch in enumerate(snum):
        n = int(ch)
        pos = length - i - 1
        if n > 0:
            if n == 1 and pos > 0:
                res += units[pos]
            else:
                res += digits[n] + units[pos]
    return res


def clean_tts_text(text: str) -> str:
    """
    TTS 음성 합성용 텍스트 정제기:
    1. 물결표(~)는 쉼표로 치환하여 '물결표'라고 소리 내어 읽는 현상 원천 차단
    2. 슬래시(/), 샵(#), 별표(*), 따옴표 등 발음 방해 특수문자 제거
    3. 기호 치환 (+ -> 플러스, % -> 퍼센트)
    4. 숫자(48시간 -> 사십팔 시간, 3초 -> 삼 초) 한자어 발음 변환
    """
    if not text:
        return ""

    # 1. 물결표(~)는 자연스러운 쉼표 호흡으로 치환
    text = re.sub(r'~+', ', ', text)

    # 2. 발음 훼손 특수기호 치환 및 제거
    for ch in ['/', '#', '*', '@', '^', '"', "'", '`', '(', ')', '[', ']', '<', '>', '{', '}', '•', '·', '…']:
        text = text.replace(ch, ' ')
    text = text.replace('+', ' 플러스 ').replace('%', ' 퍼센트 ').replace('&', ' 그리고 ')

    # 3. 숫자 -> 세련된 한자어 발음 변환
    def replace_num(match):
        num_str = match.group(1)
        try:
            val = int(num_str)
            return ' ' + num_to_sino_korean(val) + ' '
        except ValueError:
            return match.group(0)

    text = re.sub(r'(\d+)', replace_num, text)

    # 4. 조사 결합 및 불필요한 연속 공백 정리, 문장 부호(!, ., ?) 완전 제거 (자연스러운 연음 유도)
    text = text.replace('!', ' ').replace('.', ' ').replace('?', ' ').replace(',', ' ')
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'(\S)\s+([을를이가은는])', r'\1\2', text)
    return text.strip()


VOICE_ACTOR_PROFILES = {
    "female_20s": {
        "name": "20대 여성 (발랄)",
        "voice": "ko-KR-SunHiNeural",
        "gender": "female",
        "rate": "+5%",
        "pitch": "+0Hz",
        "pitch_ratio": 1.05,
        "speed": 1.05,
        "desc": "밝고 자연스러운 20대 여성 톤 (SunHi)"
    },
    "male_20s": {
        "name": "20대 남성 (에너지)",
        "voice": "ko-KR-InJoonNeural",
        "gender": "male",
        "rate": "+5%",
        "pitch": "+0Hz",
        "pitch_ratio": 1.05,
        "speed": 1.05,
        "desc": "자신감 있고 자연스러운 20대 남성 톤 (InJoon)"
    },
    "female_30s": {
        "name": "30대 여성 (신뢰)",
        "voice": "ko-KR-SunHiNeural",
        "gender": "female",
        "rate": "+5%",
        "pitch": "+0Hz",
        "pitch_ratio": 1.00,
        "speed": 1.05,
        "desc": "차분하고 전문적인 신뢰감 있는 30대 여성 톤 (SunHi)"
    },
    "male_30s": {
        "name": "30대 남성 (중저음)",
        "voice": "ko-KR-InJoonNeural",
        "gender": "male",
        "rate": "+5%",
        "pitch": "+0Hz",
        "pitch_ratio": 0.95,
        "speed": 1.05,
        "desc": "깊이 있고 진중한 중저음 30대 남성 톤 (InJoon)"
    }
}


class AssetGenerator:
    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        tts_lang: str = "ko",
        audio_speed: float = 1.15, # 쇼츠 광고 최적의 자연스럽고 경쾌한 배속(1.1~1.15배속)
        voice_actor: str = "female_20s",
        audio_mode: str = "voice_and_sub",
        cache_dir: Optional[str] = None,
        mock: bool = False,
        mode: str = "commerce",
        readings: Optional[Dict[str, str]] = None
    ):
        if not cache_dir:
            raise ValueError("cache_dir가 필요합니다. JobContext.assets_dir을 전달하세요.")
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
        self.tts_lang = tts_lang
        self.audio_speed = audio_speed
        self.voice_actor = voice_actor
        self.audio_mode = audio_mode
        self.cache_dir = cache_dir
        self.mock = mock
        self.mode = mode
        self.readings = {k: v for k, v in (readings or {}).items() if k and v}   # 상호명·지명 읽는 법 (TTS에만, 자막은 원문)

        os.makedirs(self.cache_dir, exist_ok=True)

        # Google GenAI 클라이언트 초기화 (Mock이 아닐 때만)
        self.genai_client = None   # AI 이미지 생성(Imagen) 제거: 모든 모드가 실물 사진·영상만 사용

    def is_cached(self, file_path: str) -> bool:
        """파일이 존재하고 0바이트 이상인지 검사하여 캐시 여부 확인"""
        return os.path.exists(file_path) and os.path.getsize(file_path) > 0

    # -------------------------------------------------------------
    # 1. 이미지 생성 (Google Imagen 3 & Multi-tier Fallback & 갤러리 1:1 매핑)
    # -------------------------------------------------------------
    def generate_image(
        self,
        scene: Dict[str, Any],
        force: bool = False,
        gallery_image_path: Optional[str] = None
    ) -> str:
        scene_num = int(scene.get("scene_index") or scene.get("scene_number", 1))
        out_path = os.path.join(self.cache_dir, f"scene_{scene_num:02d}.png")

        # 스마트폰 직접 촬영 영상 씬: 대표 프레임을 키프레임(갤러리/누끼 합성용)으로 사용
        video_media = self.scene_video_media(scene)
        if video_media:
            thumb_path = os.path.join(self.cache_dir, f"scene_{scene_num:02d}_thumb.jpg")
            if force or not self.is_cached(thumb_path) or os.path.getmtime(thumb_path) < os.path.getmtime(video_media["path"]):
                extract_thumbnail(video_media["path"], thumb_path, at=float(video_media.get("trim_start", DEFAULT_TRIM_START)) + 0.3)
            return thumb_path

        # 여행 상품 모드: 수집된 갤러리 컷 1:1 매핑 (rembg 누끼 및 Imagen 3 완전 바이패스)
        if gallery_image_path and os.path.exists(gallery_image_path):
            if not force and self.is_cached(out_path):
                img_mtime = os.path.getmtime(gallery_image_path)
                png_mtime = os.path.getmtime(out_path)
                if png_mtime >= img_mtime:
                    logger.info(f"[Cache Hit] 씬 {scene_num} 갤러리 이미지 이미 존재: {out_path}")
                    return out_path
            logger.info(f"[Gallery Mapping] 씬 {scene_num}: 갤러리 컷 원본 1:1 매핑 ({gallery_image_path}) -> {out_path}")
            try:
                with Image.open(gallery_image_path) as g_img:
                    g_img.convert("RGB").save(out_path, "PNG")
                return out_path
            except Exception as ge:
                logger.warning(f"갤러리 이미지 변환 실패 ({ge}). 원본 경로를 직접 사용합니다.")
                return gallery_image_path

        # 실물 사진만 허용: AI로 숙소·매장·상품 사진을 지어내면 허위광고가 되므로 생성 대신 중단
        if self.mode in REAL_MEDIA_MODES and not self.mock:
            raise ValueError(
                f"씬 {scene_num}에 매칭된 실물 사진이 없습니다. 사진을 교체하거나 직접 업로드해 주세요."
            )

        # 여기까지 왔으면 mock(테스트) 실행: 실제 사진 대신 자리표시 그래픽 (AI 이미지 생성은 하지 않음)
        if not force and self.is_cached(out_path):
            logger.info(f"[Cache Hit] 씬 {scene_num} 이미지 이미 존재: {out_path}")
            return out_path
        self._create_mock_image(scene, out_path)
        logger.info(f"[Mock Image] 씬 {scene_num} 자리표시 이미지 생성: {out_path}")
        return out_path

    # -------------------------------------------------------------
    # 2. 오디오 생성 (Google TTS + 성우 다변화 + 무음 모드 제어)
    # -------------------------------------------------------------
    def generate_voice(self, scene: Dict[str, Any], force: bool = False) -> str:
        """
        Edge-TTS 나레이션 (또는 무음/mock). 실사 모드(travel/owner) 씬 1은 '후킹 헤드라인 → 서브 문장' 2단 음성:
        후킹을 먼저 읽고(최소 HOOK_SOLO_SEC초 구간) 이어서 서브 문장을 읽어, 화면 자막도 같은 시점에 바뀌게 합니다.
        경계 시점은 scene['hook_sec']에 기록 (컴포지터가 후킹 자막 → 서브 자막 전환에 사용)
        """
        scene_num = int(scene.get("scene_index") or scene.get("scene_number", 1))
        out_path = os.path.join(self.cache_dir, f"scene_{scene_num:02d}.mp3")
        hook = self._spoken_hook(scene)

        if self.audio_mode == "silent":
            # 무음 모드: 소리는 없지만 자막 전환 시점은 동일하게 유지
            if hook:
                scene["hook_sec"] = HOOK_SOLO_SEC
            if not force and self.is_cached(out_path):
                return out_path
            dur = float(scene.get("duration_sec", 3.0)) + (HOOK_SOLO_SEC if hook else 0.0)
            self._create_silent_audio(dur, out_path)
            logger.info(f"[Silent Mode] 씬 {scene_num} 무음 오디오 생성 완료 ({dur:.2f}초): {out_path}")
            return out_path

        if not hook:
            if not force and self.is_cached(out_path):
                logger.info(f"[Cache Hit] 씬 {scene_num} 오디오 이미 존재: {out_path}")
                return out_path
            self._synthesize(scene.get("voiceover", ""), out_path, scene, force)
            return out_path

        hook_path = os.path.join(self.cache_dir, f"scene_{scene_num:02d}_hook.mp3")
        body_path = os.path.join(self.cache_dir, f"scene_{scene_num:02d}_body.mp3")
        if force or not (self.is_cached(out_path) and self.is_cached(hook_path)):
            self._synthesize(hook, hook_path, {**scene, "duration_sec": 1.2}, force, rate=HOOK_RATE)
            self._synthesize(scene.get("voiceover", ""), body_path, scene, force)
            hook_sec = max(HOOK_SOLO_SEC, (_audio_seconds(hook_path) or 0.0) + HOOK_GAP_SEC)
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            fmt = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
            res = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", hook_path, "-i", body_path,
                                  "-filter_complex", f"[0:a]{fmt},apad=whole_dur={hook_sec:.3f}[h];[1:a]{fmt}[b];"
                                                     f"[h][b]concat=n=2:v=0:a=1[a]",
                                  "-map", "[a]", "-c:a", "libmp3lame", "-q:a", "2", out_path],
                                 capture_output=True, text=True, encoding="utf-8", errors="ignore")
            if res.returncode != 0:
                raise RuntimeError(f"씬 {scene_num} 후킹+서브 음성 결합 실패: {res.stderr[-300:]}")
            logger.info(f"씬 {scene_num} 2단 음성: 후킹 '{hook}' ({hook_sec:.2f}초) → 서브 '{scene.get('voiceover', '')}'")
        else:
            hook_sec = max(HOOK_SOLO_SEC, (_audio_seconds(hook_path) or 0.0) + HOOK_GAP_SEC)
            logger.info(f"[Cache Hit] 씬 {scene_num} 2단 음성 이미 존재 (후킹 {hook_sec:.2f}초): {out_path}")
        scene["hook_sec"] = round(hook_sec, 2)
        return out_path

    def generate_voices(self, scenes: List[Dict[str, Any]], force: bool = False,
                        on_done: Optional[Any] = None) -> List[str]:
        """
        여러 씬 음성을 TTS_WORKERS개씩 동시에 생성 → scenes와 같은 순서의 경로. 씬마다 파일·scene['hook_sec']가 따로라 안전.
        on_done(완료 수, 전체 수): 진행률 표시용 (호출한 스레드에서 실행)
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        if len(scenes) <= 1 or self.mock or self.audio_mode == "silent":
            out = []
            for i, sc in enumerate(scenes, 1):
                out.append(self.generate_voice(sc, force=force))
                if on_done:
                    on_done(i, len(scenes))
            return out
        results: List[Optional[str]] = [None] * len(scenes)
        with ThreadPoolExecutor(max_workers=min(TTS_WORKERS, len(scenes))) as ex:
            futs = {ex.submit(self.generate_voice, sc, force): i for i, sc in enumerate(scenes)}
            for done, fut in enumerate(as_completed(futs), 1):
                results[futs[fut]] = fut.result()
                if on_done:
                    on_done(done, len(scenes))
        logger.info(f"씬 음성 {len(scenes)}개 동시 생성 완료 (최대 {TTS_WORKERS}개씩)")
        return [r for r in results if r is not None]

    def _speak_checked(self, text: str, path: str, rate: str, pitch: str, scene_num: int, keep_commas: bool = False) -> str:
        """Edge-TTS 합성 + 잘린 응답이면 재시도 (TTS_RETRIES회)"""
        from core.tts import generate_speech
        for attempt in range(TTS_RETRIES + 1):
            generate_speech(text=text, output_path=path, voice_actor=self.voice_actor, rate=rate, pitch=pitch, force=True,
                            keep_commas=keep_commas)
            sec = _audio_seconds(path)
            if not tts_too_short(text, sec):
                return path
            logger.warning(f"씬 {scene_num} Edge-TTS 잘린 응답 ({sec or 0:.2f}초, {len(text)}자) → 재시도 {attempt + 1}/{TTS_RETRIES}")
        raise RuntimeError(f"Edge-TTS가 {TTS_RETRIES + 1}회 연속 잘린 음성을 반환 ('{text}')")

    def _spoken_hook(self, scene: Dict[str, Any]) -> str:
        """씬 1 후킹 헤드라인을 먼저 읽을지 (실사 모드에서 hook_title이 있을 때만. 커머스는 기존 상단 후킹 바 유지)"""
        if self.mode not in REAL_MEDIA_MODES:
            return ""
        return str(scene.get("hook_title") or "").strip()

    def _synthesize(self, text: str, out_path: str, scene: Dict[str, Any], force: bool = False,
                    rate: Optional[str] = None) -> str:
        scene_num = int(scene.get("scene_index") or scene.get("scene_number", 1))
        actor_info = VOICE_ACTOR_PROFILES.get(self.voice_actor, VOICE_ACTOR_PROFILES["female_20s"])
        # Edge-TTS 파라미터: rate="+12%"(지루함 제거), pitch="+3Hz"(발랄한 톤), 여성 SunHi / 남성 InJoon
        rate = rate or actor_info.get("rate", "+12%")
        pitch = actor_info.get("pitch", "+3Hz")
        logger.info(f"[Edge-TTS] 씬 {scene_num} 한국어 음성 생성 시작 (성우={self.voice_actor}, Rate={rate}, Pitch={pitch}): '{text}'")

        if self.mock:
            duration_sec = float(scene.get("duration_sec", 2.5))
            self._create_mock_audio(duration_sec, out_path)
            logger.info(f"[Mock Audio] 씬 {scene_num} 더미 오디오 생성 완료 ({duration_sec:.2f}초): {out_path}")
            return out_path

        from core.tts import apply_readings, end_rise_percent, question_variants
        spoken = apply_readings(text, self.readings)
        if spoken != text:
            logger.info(f"씬 {scene_num} 읽는 법 적용: '{text}' → '{spoken}'")
            text = spoken
        try:
            raw_path = os.path.splitext(out_path)[0] + ".raw.mp3"
            variants = question_variants(text)
            if len(variants) > 1:
                # 의문문: 숨 쉼표 위치가 다른 후보를 합성해 말끝이 가장 많이 올라가는 것을 사용 (자막은 원문 그대로)
                scored = []
                for vi, variant in enumerate(variants):
                    cand_path = os.path.splitext(out_path)[0] + f".q{vi}.mp3"
                    self._speak_checked(variant, cand_path, rate, pitch, scene_num, keep_commas=True)
                    scored.append((end_rise_percent(cand_path), vi, variant, cand_path))
                best = max(scored, key=lambda x: (x[0] if x[0] is not None else -999.0, -x[1]))
                import shutil
                shutil.copyfile(best[3], raw_path)
                logger.info(f"씬 {scene_num} 의문문 억양 선택: '{best[2]}' (말끝 {best[0] if best[0] is not None else 0:+.0f}%) / 후보 "
                            + ", ".join(f"'{v}' {r if r is not None else 0:+.0f}%" for r, _, v, _ in scored))
            else:
                self._speak_checked(text, raw_path, rate, pitch, scene_num)
            trim_silence(raw_path, out_path)
            logger.info(f"씬 {scene_num} Edge-TTS 음성 생성 완료 (앞뒤 무음 제거: {_audio_seconds(raw_path) or 0:.2f}초 → "
                        f"{_audio_seconds(out_path) or 0:.2f}초) -> {out_path}")
        except Exception as e:
            logger.warning(f"씬 {scene_num} Edge-TTS 음성 생성 실패: {e}. 더미 오디오로 안전 복구합니다.")
            self._create_mock_audio(float(scene.get("duration_sec", 2.5)), out_path)
        return out_path

    generate_audio = generate_voice

    def _create_silent_audio(self, duration_sec: float, out_path: str):
        """ffmpeg lavfi 필터를 사용해 완벽한 무음 오디오 파일 생성"""
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ffmpeg_exe, "-y",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", str(duration_sec),
            "-q:a", "9",
            out_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise RuntimeError(f"무음 오디오 생성 실패: {res.stderr.decode('utf-8', errors='ignore')}")

    def _apply_voice_transformation(self, in_file: str, out_file: str, pitch_ratio: float = 1.0, speed: float = 1.08):
        """
        쇼츠 광고에 최적화된 자연스러운 1.05~1.1배속 및 피치 시프팅:
        1차: ffmpeg 내장 librubberband (음질 왜곡 및 속도 간섭 없는 독립적 피치/템포 변환)
        2차 Fallback: aresample 정규화 -> asetrate -> aresample -> atempo 역보정
        """
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        
        # 1차 시도: librubberband
        try:
            cmd_rubber = [
                ffmpeg_exe,
                "-y",
                "-i", in_file,
                "-filter:a", f"rubberband=pitch={pitch_ratio:.4f}:tempo={speed:.4f}",
                "-vn",
                out_file
            ]
            res = subprocess.run(cmd_rubber, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0 and os.path.exists(out_file) and os.path.getsize(out_file) > 0:
                return
        except Exception:
            pass

        # 2차 시도: sample rate 정규화 기반 asetrate + atempo 역보정
        # 주의: gTTS 출력(24kHz 등)과 asetrate 기준(44.1kHz) 불일치로 인한 배속 중첩을 방지하기 위해 반드시 선행 aresample=44100 수행
        base_rate = 44100
        new_rate = int(base_rate * pitch_ratio)
        tempo_comp = speed / pitch_ratio
        tempo_comp = max(0.5, min(2.0, tempo_comp))

        filter_str = f"aresample={base_rate},asetrate={new_rate},aresample={base_rate},atempo={tempo_comp:.4f}"
        cmd = [
            ffmpeg_exe,
            "-y",
            "-i", in_file,
            "-filter:a", filter_str,
            "-vn",
            out_file
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            # 3차 긴급 Fallback: 단순 atempo 단독 적용
            cmd_fallback = [
                ffmpeg_exe, "-y", "-i", in_file,
                "-filter:a", f"atempo={speed:.4f}", "-vn", out_file
            ]
            subprocess.run(cmd_fallback, check=True)

    def _apply_atempo(self, in_file: str, out_file: str, speed: float = 1.08):
        """하위 호환용 단순 속도 변경 메서드"""
        self._apply_voice_transformation(in_file, out_file, pitch_ratio=1.0, speed=speed)

    # -------------------------------------------------------------
    # 3. 비디오 생성 (로컬 2D Ken Burns 팬앤줌 모션 렌더링 전용)
    # -------------------------------------------------------------
    def _create_ken_burns_video(
        self,
        image_path: str,
        duration_sec: float,
        out_path: str,
        fps: int = 24,
        motion_type: str = "zoom_in",
        focus_bottom: bool = False,
        motion_sec: Optional[float] = None
    ) -> str:
        """
        Pillow, OpenCV, ffmpeg를 사용해 2D 키프레임 이미지를 역동적인 9:16 세로형(1080x1920)
        Ken Burns(팬앤줌: 줌인, 줌아웃, 상하/좌우 패닝) MP4 클립으로 합성.
        - motion_sec: 모션이 끝까지 진행되는 시간 (= 실제 화면에 나가는 시간). 그 뒤 프레임은 끝 구도 유지.
          클립을 넉넉히 렌더해도 모션이 화면 노출 시간 안에 다 보이도록 해, 줌 폭이 잘려 정지 화면처럼 보이지 않게 함
        - 줌인/줌아웃은 1.0 ↔ KB_ZOOM(1.20)배, 패닝은 이동하면서 KB_PAN_ZOOM(1.06)배까지 밀고 들어가 깊이감 추가
        - ease-out 곡선: 첫 프레임부터 빠르게 움직이고 끝에서 안착 (씬 전환 직후 정체감 제거)
        focus_bottom=True일 경우 건물 상단의 간판/로고가 프레임 밖으로 벗어나도록 하단 중심 크롭 적용.
        """
        img = Image.open(image_path).convert("RGB")
        target_w, target_h = 1080, 1920
        orig_w, orig_h = img.size
        motion_sec = min(float(motion_sec), duration_sec) if motion_sec else duration_sec

        # 1080x1920 (9:16) 프레임을 여백 없이 채우면서 모션에 필요한 여유 폭을 확보하는 커버 스케일
        if focus_bottom:
            # 씬 1 건물 외관 상단의 간판 로고는 프레임 밖으로 나가도록 세로 높이를 25% 추가 확보
            scale = max((target_h * max(1.25, KB_ZOOM)) / orig_h, (target_w * KB_ZOOM) / orig_w)
        elif motion_type in ["pan_left", "pan_right"]:
            scale = max(target_h / orig_h, (target_w * KB_PAN_MARGIN_X) / orig_w)
        elif motion_type in ["pan_down", "pan_up"]:
            scale = max(target_w / orig_w, (target_h * KB_PAN_MARGIN_Y) / orig_h)
        else:
            scale = max((target_w * KB_ZOOM) / orig_w, (target_h * KB_ZOOM) / orig_h)

        mw = int(round(orig_w * scale))
        mh = int(round(orig_h * scale))
        base_img = img.resize((mw, mh), Image.Resampling.LANCZOS)

        base_np = np.array(base_img)
        total_frames = max(1, int(round(duration_sec * fps)))

        writer = imageio_ffmpeg.write_frames(
            out_path,
            size=(target_w, target_h),
            fps=fps,
            codec="libx264",
            pix_fmt_in="rgb24",
            macro_block_size=1,
            ffmpeg_log_level="error",
            output_params=CLIP_ENCODE_PARAMS,
        )
        writer.send(None)

        for i in range(total_frames):
            t = min(1.0, (i / float(fps)) / motion_sec) if motion_sec > 0 else 1.0
            e = kb_ease(t)

            if motion_type in ("pan_right", "pan_left", "pan_down", "pan_up"):
                z = 1.0 + (KB_PAN_ZOOM - 1.0) * e
                crop_w = int(round(target_w / z))
                crop_h = int(round(target_h / z))
                p = e if motion_type in ("pan_right", "pan_down") else 1.0 - e
                if motion_type in ("pan_right", "pan_left"):
                    x1 = int(round((mw - crop_w) * p))
                    y1 = (mh - crop_h) // 2
                else:
                    x1 = (mw - crop_w) // 2
                    y1 = int(round((mh - crop_h) * p))
            else:
                # zoom_in: 1.0 → KB_ZOOM / zoom_out: KB_ZOOM → 1.0 (확대 배율 = 기준 크롭 / 현재 크롭)
                z = 1.0 + (KB_ZOOM - 1.0) * (1.0 - e if motion_type == "zoom_out" else e)
                crop_w = int(round((target_w * KB_ZOOM) / z))
                crop_h = int(round((target_h * KB_ZOOM) / z))
                x1 = mw // 2 - crop_w // 2
                y1 = mh // 2 - crop_h // 2

            crop_w, crop_h = min(crop_w, mw), min(crop_h, mh)
            x1 = max(0, min(mw - crop_w, x1))
            if focus_bottom:
                # 씬 1 건물 외관 상단 간판 로고 배제: Y 좌표를 하단 중심(85% 지점)으로 배치하여 상단 영역을 화면 밖으로 완전히 배제
                y1 = max(0, min(mh - crop_h, int((mh - crop_h) * 0.85)))
            else:
                y1 = max(0, min(mh - crop_h, y1))
            x2 = x1 + crop_w
            y2 = y1 + crop_h

            cropped_frame = base_np[y1:y2, x1:x2]
            resized_frame = cv2.resize(cropped_frame, (target_w, target_h), interpolation=cv2.INTER_AREA
                                       if crop_w > target_w else cv2.INTER_LINEAR)
            writer.send(resized_frame.tobytes())

        writer.close()
        return out_path

    def _create_product_card_video(
        self,
        image_path: str,
        duration_sec: float,
        out_path: str,
        fps: int = 24,
        motion_type: str = "zoom_in",
        motion_sec: Optional[float] = None,
        shot: str = "full",
        product_box=None
    ) -> str:
        """
        shot: 대표 사진 1장으로 여러 씬을 만들 때의 앵글 (full 전체 / center 중앙 줌인 / top·bottom 디테일, commerce_layout.shot_crop)
        쇼핑 전용 9:16 클립: 흐린 배경(같은 사진) + 중앙~하단 상품 카드(원본 비율 그대로, 잘림 없이) + 카드 안 Ken Burns.
        자막은 컴포지터가 카드 위쪽(COMMERCE_CAPTION_CENTER_Y)에 올리므로 상품을 가리지 않음.
        """
        from PIL import ImageFilter, ImageEnhance
        W, H = 1080, 1920
        motion_sec = min(float(motion_sec), duration_sec) if motion_sec else duration_sec
        full_img = Image.open(image_path).convert("RGB")
        img = shot_crop(full_img, shot, product_box)          # 카드에 들어갈 앵글 (배경은 원본 전체 사진으로 깔아 같은 상품임을 유지)
        iw, ih = img.size
        fw, fh = full_img.size

        # 배경: 화면을 덮도록(+천천히 확대할 여유) 키운 뒤 크게 흐리고 어둡게
        bw0, bh0 = int(W * COMMERCE_BG_ZOOM), int(H * COMMERCE_BG_ZOOM)
        cover = max(bw0 / fw, bh0 / fh)
        bg = full_img.resize((int(fw * cover) + 1, int(fh * cover) + 1), Image.Resampling.LANCZOS)
        bg = bg.crop(((bg.width - bw0) // 2, (bg.height - bh0) // 2, (bg.width - bw0) // 2 + bw0, (bg.height - bh0) // 2 + bh0))
        bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(COMMERCE_BG_BLUR))).enhance(COMMERCE_BG_DIM)
        bg_np = np.array(bg)

        # 상품 카드: 영역 안에 원본 비율로 '담기'(contain) → 상품이 잘리지 않음
        x0, y0, x1, y1 = (int(COMMERCE_CARD_BOX[0] * W), int(COMMERCE_CARD_BOX[1] * H),
                          int(COMMERCE_CARD_BOX[2] * W), int(COMMERCE_CARD_BOX[3] * H))
        fit = min((x1 - x0) / iw, (y1 - y0) / ih)
        cw, ch = int(iw * fit), int(ih * fit)
        cx, cy = (W - cw) // 2, y0 + ((y1 - y0) - ch) // 2
        src = np.array(img.resize((int(cw * COMMERCE_CARD_ZOOM), int(ch * COMMERCE_CARD_ZOOM)), Image.Resampling.LANCZOS))
        sh, sw = src.shape[:2]

        radius = int(W * COMMERCE_CARD_RADIUS)
        mask_img = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(mask_img).rounded_rectangle([0, 0, cw - 1, ch - 1], radius=radius, fill=255)
        mask = (np.array(mask_img.filter(ImageFilter.GaussianBlur(1))).astype(np.float32) / 255.0)[..., None]
        shadow_img = Image.new("L", (W, H), 0)
        ImageDraw.Draw(shadow_img).rounded_rectangle([cx, cy + 14, cx + cw, cy + ch + 14], radius=radius, fill=150)
        shadow = (np.array(shadow_img.filter(ImageFilter.GaussianBlur(26))).astype(np.float32) / 255.0)[..., None]

        writer = imageio_ffmpeg.write_frames(out_path, size=(W, H), fps=fps, codec="libx264", pix_fmt_in="rgb24",
                                             macro_block_size=1, ffmpeg_log_level="error", output_params=CLIP_ENCODE_PARAMS)
        writer.send(None)
        total = max(1, int(round(duration_sec * fps)))
        for i in range(total):
            e = kb_ease(min(1.0, (i / float(fps)) / motion_sec) if motion_sec > 0 else 1.0)
            # 배경: 1.0 → COMMERCE_BG_ZOOM 천천히 확대
            bz = 1.0 + (COMMERCE_BG_ZOOM - 1.0) * e
            bcw, bch = int(bw0 / bz), int(bh0 / bz)
            bx, by = (bw0 - bcw) // 2, (bh0 - bch) // 2
            frame = cv2.resize(bg_np[by:by + bch, bx:bx + bcw], (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32)
            frame *= (1.0 - shadow * 0.85)
            # 카드 안 Ken Burns: 줌인/줌아웃, 패닝은 같은 폭 안에서 좌우·상하로 살짝 이동
            if motion_type == "zoom_out":
                z = COMMERCE_CARD_ZOOM - (COMMERCE_CARD_ZOOM - 1.0) * e
            elif motion_type.startswith("pan_"):
                z = 1.0 + (COMMERCE_CARD_ZOOM - 1.0) * 0.7
            else:
                z = 1.0 + (COMMERCE_CARD_ZOOM - 1.0) * e
            kw, kh = int(sw / z), int(sh / z)
            slack_x, slack_y = sw - kw, sh - kh
            px, py = slack_x // 2, slack_y // 2
            if motion_type in ("pan_right", "pan_left"):
                px = int(slack_x * (e if motion_type == "pan_right" else 1.0 - e))
            elif motion_type in ("pan_down", "pan_up"):
                py = int(slack_y * (e if motion_type == "pan_down" else 1.0 - e))
            card = cv2.resize(src[py:py + kh, px:px + kw], (cw, ch), interpolation=cv2.INTER_AREA).astype(np.float32)
            region = frame[cy:cy + ch, cx:cx + cw]
            frame[cy:cy + ch, cx:cx + cw] = card * mask + region * (1.0 - mask)
            writer.send(np.clip(frame, 0, 255).astype(np.uint8).tobytes())
        writer.close()
        return out_path

    def _create_mock_video(self, image_path: str, duration_sec: float, out_path: str):
        """키프레임 이미지를 바탕으로 Ken Burns 모션 비디오 클립 생성 (기존 호환 래퍼)"""
        return self._create_ken_burns_video(image_path, duration_sec, out_path)

    def generate_video(self, scene: Dict[str, Any], image_path: str, force: bool = False) -> str:
        """
        외부 유료 비디오 생성 API(Veo 등)를 영구 차단하고,
        오직 로컬 OpenCV/Pillow/ffmpeg 기반의 2D 이미지 Ken Burns(줌인/줌아웃/좌우/상하 패닝) 합성으로 비디오를 생성합니다.
        """
        scene_num = int(scene.get("scene_index") or scene.get("scene_number", 1))
        out_path = os.path.join(self.cache_dir, f"scene_{scene_num:02d}.mp4")

        video_media = self.scene_video_media(scene)
        if video_media:
            return self._generate_ugc_video(scene, video_media, out_path, force)

        if not force and self.is_cached(out_path):
            img_mtime = os.path.getmtime(image_path) if os.path.exists(image_path) else 0
            voice_path = os.path.join(self.cache_dir, f"scene_{scene_num:02d}.mp3")
            voice_mtime = os.path.getmtime(voice_path) if os.path.exists(voice_path) else 0
            vid_mtime = os.path.getmtime(out_path)
            # 사진이나 나레이션(모션 길이 기준)이 바뀌었으면 다시 렌더
            if vid_mtime >= max(img_mtime, voice_mtime):
                logger.info(f"[Cache Hit] 씬 {scene_num} 비디오 이미 존재: {out_path}")
                return out_path

        # 모션은 실제 화면 노출 시간(나레이션 길이 + 컴포지터 테일 0.2초) 안에 끝까지 진행되도록 하고,
        # 클립은 마지막 씬 테일(+1.0초)까지 덮도록 조금 더 길게 렌더 (넘는 부분은 끝 구도 유지)
        audio_path = os.path.join(self.cache_dir, f"scene_{scene_num:02d}.mp3")
        voice_sec = _audio_seconds(audio_path)
        motion_sec = (voice_sec or float(scene.get("duration_sec", 2.5))) + 0.2
        duration_sec = motion_sec + 1.0

        # 씬 번호 및 모션 프롬프트에 따라 부드러운 줌인, 줌아웃, 상하/좌우 패닝 교차 배정
        motion_prompt = str(scene.get("video_motion_prompt_en", "")).lower()
        if "right" in motion_prompt:
            motion_type = "pan_right"
        elif "left" in motion_prompt:
            motion_type = "pan_left"
        elif "out" in motion_prompt or "pull" in motion_prompt:
            motion_type = "zoom_out"
        elif "down" in motion_prompt:
            motion_type = "pan_down"
        elif "up" in motion_prompt:
            motion_type = "pan_up"
        elif "in" in motion_prompt or "push" in motion_prompt:
            motion_type = "zoom_in"
        else:
            # 부드러운 줌인, 줌아웃, 상하/좌우 패닝 교차 패턴
            patterns = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_down", "pan_up"]
            motion_type = patterns[(scene_num - 1) % len(patterns)]

        logger.info(f"[Local Video Gen] 씬 {scene_num} 2D Ken Burns 모션 합성 시작 (모션: {motion_type}, 모션 {motion_sec:.1f}초 / "
                    f"클립 {duration_sec:.1f}초, 원본: {image_path})")
        # 제휴(travel) 모드만 씬 1 상단 간판을 프레임 밖으로 밀어 숙소명을 숨김. 사장님(owner) 모드는 간판 노출이 목적
        is_focus_bottom = bool(scene.get("focus_bottom") or (self.mode == "travel" and scene_num == 1))
        if self.mode == "commerce":
            # 쇼핑: 상품을 가리지 않는 '상단 자막 + 중앙~하단 상품 카드' 배치
            self._create_product_card_video(image_path=image_path, duration_sec=duration_sec, out_path=out_path, fps=24,
                                            motion_type=motion_type, motion_sec=motion_sec, shot=scene.get("shot") or "full",
                                            product_box=scene.get("product_box"))
            logger.info(f"씬 {scene_num} 상품 카드 클립 렌더링 완료 -> {out_path}")
            return out_path
        self._create_ken_burns_video(
            image_path=image_path,
            duration_sec=duration_sec,
            out_path=out_path,
            fps=24,
            motion_type=motion_type,
            focus_bottom=is_focus_bottom,
            motion_sec=motion_sec
        )
        logger.info(f"씬 {scene_num} Ken Burns 비디오 렌더링 완료 -> {out_path}")
        return out_path

    # -------------------------------------------------------------
    # 3-1. 스마트폰 직접 촬영 영상(UGC) 클립
    # -------------------------------------------------------------
    @staticmethod
    def scene_video_media(scene: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """씬에 업로드 영상이 배정되어 있으면 media dict 반환 ({'type': 'video', 'path', 'trim_start', 'fit'})"""
        media = scene.get("media")
        if isinstance(media, dict) and media.get("type") == "video" and media.get("path") and os.path.exists(media["path"]):
            return media
        return None

    def _generate_ugc_video(self, scene: Dict[str, Any], media: Dict[str, Any], out_path: str, force: bool) -> str:
        """업로드 영상을 씬 길이에 맞춰 트림/정규화(1080x1920, 24fps CFR, 무음)하여 Ken Burns 클립 자리에 배치"""
        scene_num = int(scene.get("scene_index") or scene.get("scene_number", 1))
        # Ken Burns와 동일하게 나레이션 + 테일 마진을 덮는 길이로 생성 (compositor가 오디오 길이에 맞춰 컷)
        duration_sec = max(float(scene.get("duration_sec", 3.0)) + 2.0, 5.0)
        signature = {
            "src": os.path.abspath(media["path"]),
            "src_mtime": os.path.getmtime(media["path"]),
            "trim_start": float(media.get("trim_start", DEFAULT_TRIM_START)),
            "fit": media.get("fit", "auto"),
            "duration": duration_sec,
        }
        sig_path = out_path + ".json"
        if not force and self.is_cached(out_path) and os.path.exists(sig_path):
            try:
                with open(sig_path, "r", encoding="utf-8") as f:
                    if json.load(f) == signature:
                        logger.info(f"[Cache Hit] 씬 {scene_num} UGC 클립 이미 존재: {out_path}")
                        return out_path
            except Exception:
                pass

        logger.info(f"[UGC Video] 씬 {scene_num} 직접 촬영 영상 정규화 (시작 {signature['trim_start']:.1f}초, 길이 {duration_sec:.1f}초)")
        normalize_ugc_clip(
            src=media["path"],
            dst=out_path,
            duration=duration_sec,
            start=signature["trim_start"],
            fps=24,
            fit=signature["fit"]
        )
        with open(sig_path, "w", encoding="utf-8") as f:
            json.dump(signature, f)
        return out_path

    # -------------------------------------------------------------
    # 배치 파이프라인 실행
    # -------------------------------------------------------------
    def generate_all(
        self,
        scenes: List[Dict[str, Any]],
        force: bool = False,
        gallery_images: Optional[List[str]] = None
    ) -> List[Dict[str, str]]:
        results = []
        total = len(scenes)

        prepared = []
        for i, scene in enumerate(scenes, 1):
            s_num = int(scene.get("scene_index") or scene.get("scene_number", i))
            logger.info(f"===== [에셋 생성 {i}/{total}] 씬 {s_num} 사진 준비 =====")

            # 1. 키프레임 이미지 (씬에 기록된 실물 사진 image_path 1:1 매핑)
            # 순번 기반 매핑은 다운로드 실패 시 이후 씬이 밀리므로, 씬 키(image_path)를 우선 사용합니다.
            # gallery_images는 씬 수와 정확히 같은 길이의 명시적 순서 목록일 때만 사용합니다 (모듈로 순환 금지).
            g_path = scene.get("image_path")
            if not g_path and gallery_images and len(gallery_images) == total:
                g_path = gallery_images[i - 1]
            prepared.append((scene, s_num, self.generate_image(scene, force=force, gallery_image_path=g_path)))

        # 2. 보이스오버 음성 (Edge-TTS, 씬 여러 개 동시)
        audio_paths = self.generate_voices([sc for sc, _, _ in prepared], force=force)

        # 3. 비디오 클립 (로컬 2D Ken Burns 팬앤줌 합성, 음성 길이 = 모션 길이)
        for (scene, s_num, img_path), audio_path in zip(prepared, audio_paths):
            video_path = self.generate_video(scene, image_path=img_path, force=force)
            results.append({
                "scene_number": s_num,
                "scene_index": s_num,
                "image_path": img_path,
                "audio_path": audio_path,
                "video_path": video_path,
                "caption": scene.get("caption") or scene.get("voiceover", ""),
                "hook_title": scene.get("hook_title", ""),
                "hook_sec": scene.get("hook_sec"),
            })
            logger.info(f"씬 {s_num} 에셋 준비 완료.\n")

        return results

    # -------------------------------------------------------------
    # 도우미 함수들
    # -------------------------------------------------------------
    def _create_mock_image(self, scene: Dict[str, Any], out_path: str):
        """Pillow를 사용해 1024x1792(9:16) 규격의 고품질 키프레임 이미지 생성"""
        width, height = 1024, 1792
        scene_num = int(scene.get("scene_index") or scene.get("scene_number", 1))
        
        color_palettes = [
            ((20, 24, 40), (88, 101, 242)),
            ((15, 32, 39), (44, 83, 100)),
            ((34, 40, 49), (238, 114, 20)),
            ((26, 26, 46), (233, 69, 96)),
        ]
        top_color, bottom_color = color_palettes[(scene_num - 1) % len(color_palettes)]

        base = Image.new("RGB", (width, height))
        top_arr = np.array(top_color, dtype=float)
        bot_arr = np.array(bottom_color, dtype=float)
        gradient_arr = np.zeros((height, width, 3), dtype=np.uint8)
        for y in range(height):
            ratio = y / height
            color = (1.0 - ratio) * top_arr + ratio * bot_arr
            gradient_arr[y, :, :] = color.astype(np.uint8)

        base = Image.fromarray(gradient_arr)
        draw = ImageDraw.Draw(base)

        box_y1 = int(height * 0.35)
        box_y2 = int(height * 0.65)
        draw.rectangle([(80, box_y1), (width - 80, box_y2)], fill=(0, 0, 0, 160), outline=(255, 255, 255), width=3)

        badge_text = f"[ SCENE {scene_num:02d} ]"
        draw.text((120, box_y1 + 40), badge_text, fill=(255, 215, 0))

        info_lines = [
            f"Voiceover: {scene.get('voiceover', '')[:40]}...",
            f"Caption: {scene.get('caption', '')[:35]}...",
            f"Duration: {scene.get('duration_sec', 2.5)} sec",
            f"Image Prompt: {scene.get('image_prompt_en', '')[:60]}...",
            f"Motion: {scene.get('video_motion_prompt_en', '')[:60]}...",
            "Engine: Google Imagen 3 (9:16 Vertical)"
        ]

        y_offset = box_y1 + 120
        for line in info_lines:
            draw.text((120, y_offset), line, fill=(240, 240, 240))
            y_offset += 40

        base.save(out_path, "PNG")

    def _create_mock_audio(self, duration_sec: float, out_path: str):
        """MoviePy/numpy로 실제 재생 가능한 MP3 오디오 생성"""
        from moviepy.audio.AudioClip import AudioArrayClip
        
        sample_rate = 44100
        total_samples = int(sample_rate * duration_sec)
        t = np.linspace(0, duration_sec, total_samples, endpoint=False)
        
        waveform = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.1 * np.sin(2 * np.pi * 880 * t)
        fade_samples = min(int(sample_rate * 0.1), total_samples // 4)
        fade_in = np.linspace(0, 1, fade_samples)
        fade_out = np.linspace(1, 0, fade_samples)
        waveform[:fade_samples] *= fade_in
        waveform[-fade_samples:] *= fade_out

        stereo = np.stack([waveform, waveform], axis=1)
        audio_clip = AudioArrayClip(stereo, fps=sample_rate)
        audio_clip.write_audiofile(out_path, fps=sample_rate, verbose=False, logger=None)
        audio_clip.close()
