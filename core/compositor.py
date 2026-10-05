"""
core/compositor.py - Step 3: MoviePy 기반 비디오-오디오 싱크 매칭, 고가독성 자막 오버레이 & 최종 렌더링
"""
import os
import re
import logging
from typing import List, Dict, Any, Optional
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from moviepy.editor import (
    VideoFileClip,
    AudioFileClip,
    ImageClip,
    CompositeVideoClip,
    concatenate_videoclips
)

import urllib.request

logger = logging.getLogger("ad_factory.compositor")


class AdCompositor:
    def __init__(self, output_dir: str = "outputs/final", font_dir: str = "fonts"):
        self.output_dir = output_dir
        self.font_dir = font_dir
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.font_dir, exist_ok=True)

        # 숏폼 전용 극강의 상업용 무료 볼드 폰트 자동 확보
        self.font_path = self._ensure_bold_font()

    def _ensure_bold_font(self) -> str:
        """
        가변 폰트가 아닌 순수 고정 볼드(Pretendard-Black, Weight 900) 폰트 자동 다운로드 및 절대 경로 확보.
        실패 시 Windows 시스템 볼드 폰트로 안전 fallback.
        """
        target_font = os.path.abspath(os.path.join(self.font_dir, "Pretendard-Black.ttf"))
        if os.path.exists(target_font) and os.path.getsize(target_font) > 1000000:
            logger.info(f"숏폼 전용 고정 Black 폰트 로드: {target_font}")
            return target_font

        # Pretendard-Black (Static TrueType) 다운로드 시도
        font_urls = [
            "https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/public/static/alternative/Pretendard-Black.ttf",
            "https://raw.githubusercontent.com/orioncactus/pretendard/main/packages/pretendard/dist/public/static/alternative/Pretendard-Black.ttf"
        ]
        for url in font_urls:
            try:
                logger.info(f"Pretendard-Black 고정 볼드 폰트 다운로드 중... ({url})")
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as response, open(target_font, "wb") as f:
                    f.write(response.read())
                logger.info(f"Pretendard-Black 폰트 다운로드 완료 ({os.path.getsize(target_font)} bytes): {target_font}")
                return target_font
            except Exception as e:
                logger.warning(f"폰트 다운로드 시도 실패 ({url}): {e}")

        # 시스템 볼드 폰트 탐색
        fallback_candidates = [
            "C:/Windows/Fonts/malgunbd.ttf",
            "C:/Windows/Fonts/malgun.ttf",
            "C:/Windows/Fonts/gulim.ttc"
        ]
        for path in fallback_candidates:
            if os.path.exists(path):
                return os.path.abspath(path)

        return target_font

    def _wrap_text(self, text: str, draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, max_width: int) -> List[str]:
        """
        [의미 단위 어절 줄바꿈 시스템 (word-break: keep-all)]
        - 글자 수 기반 강제 줄바꿈 배제.
        - 어절(단어) 단위 분리 및 단어 쪼개짐 절대 방지.
        - 2줄 자막 생성 시 글자 수 중심이 아닌 '가장 중앙에 가까운 띄어쓰기 위치'를 기준으로 분할.
        - 1줄 너비(max_width) 이내인 경우 1줄로 깔끔하게 유지.
        """
        if not text:
            return []
        import re
        clean = re.sub(r'\s+', ' ', text).strip()
        if not clean:
            return []

        # 1. 전체 텍스트가 1줄에 들어가는 경우 1줄 유지
        bbox = draw.textbbox((0, 0), clean, font=font)
        total_w = bbox[2] - bbox[0]
        if total_w <= max_width:
            return [clean]

        # 2. 띄어쓰기 위치 목록 추출 (어절 단위 분리)
        space_indices = [i for i, ch in enumerate(clean) if ch == ' ']
        if not space_indices:
            # 띄어쓰기가 없는 단일 단어인 경우 단어를 임의로 자르지 않고 그대로 반환 (keep-all)
            return [clean]

        # 3. 2줄 분할 시: 텍스트 중앙에 가장 가까운 띄어쓰기 위치를 기준으로 분할
        mid = len(clean) / 2.0
        valid_candidates = []

        for idx in space_indices:
            line1 = clean[:idx].strip()
            line2 = clean[idx + 1:].strip()
            w1 = draw.textbbox((0, 0), line1, font=font)[2] - draw.textbbox((0, 0), line1, font=font)[0]
            w2 = draw.textbbox((0, 0), line2, font=font)[2] - draw.textbbox((0, 0), line2, font=font)[0]

            if w1 <= max_width and w2 <= max_width:
                # 중앙 지점과의 거리
                dist = abs(idx - mid)
                valid_candidates.append((dist, line1, line2))

        if valid_candidates:
            # 중앙과 가장 가까운 띄어쓰기 위치 분할 선택
            valid_candidates.sort(key=lambda x: x[0])
            return [valid_candidates[0][1], valid_candidates[0][2]]

        # 4. 2줄로 모두 수용되지 않는 아주 긴 문장의 경우: 어절 단위 keep-all 순차 래핑 (단어 분리 방지)
        words = clean.split()
        lines = []
        current_line = ""
        for word in words:
            test_line = f"{current_line} {word}".strip()
            line_w = draw.textbbox((0, 0), test_line, font=font)[2] - draw.textbbox((0, 0), test_line, font=font)[0]
            if line_w <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
        return lines

    def create_overlay_image(
        self,
        caption_text: Optional[str],
        hook_title: Optional[str],
        width: int,
        height: int,
        mode: str = "commerce",
        **kwargs
    ) -> np.ndarray:
        """
        숏폼 전용 고가독성 자막 오버레이 시스템:
        - [여행 상품 모드 - 런비(Learnbee) 스타일]:
          * 자막 위치: 시선이 가장 집중되는 화면 중앙 (상단 기준 46%, 45%~48% 범위)
          * 폰트 및 스타일: Pretendard-Black, 순백색 텍스트 + 두꺼운 검은색 외곽선(Stroke 6px) + 반투명 라운드 필 (밝은 배경에서도 100% 가독성 보장)
          * 씬 1 후킹 바가 존재할 경우: 상단 17% 지점에 형광 옐로우 후킹 바를 노출하고, 중앙 46%에는 런비 나레이션 자막 동시 노출
        - [일반 커머스 모드]:
          * 씬 1 (hook_title 존재 시): 상단 17% 지점 후킹 바 단독 노출
          * 일반 씬: 하단 62% 지점(쇼츠 UI 세이프존)에 볼드 자막 노출
        """
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # -------------------------------------------------------------
        # Case 1: 상단 초강력 후킹 바 (씬 1 등 hook_title 존재 시)
        # -------------------------------------------------------------
        if hook_title and hook_title.strip():
            hook_text = hook_title.strip()
            hook_font_size = int(width * 0.078)
            try:
                hook_font = ImageFont.truetype(self.font_path, hook_font_size)
            except Exception:
                hook_font = ImageFont.load_default()

            hook_lines = self._wrap_text(hook_text, draw, hook_font, max_width=int(width * 0.82))
            if hook_lines:
                hook_line_height = int(hook_font_size * 1.35)
                total_hook_height = len(hook_lines) * hook_line_height

                # [모바일 세이프존] 화면 상단 17% 지점
                center_y_hook = int(height * 0.17)
                start_y_hook = center_y_hook - (total_hook_height // 2)

                padding_x_hook = 42
                padding_y_hook = 22

                for i, line in enumerate(hook_lines):
                    bbox = draw.textbbox((0, 0), line, font=hook_font)
                    w = bbox[2] - bbox[0]
                    h = bbox[3] - bbox[1]
                    x = (width - w) // 2
                    y = start_y_hook + (i * hook_line_height)

                    # 후킹 박스: 깊은 반투명 블랙 둥근 박스
                    draw.rounded_rectangle(
                        [(x - padding_x_hook, y - padding_y_hook // 2),
                         (x + w + padding_x_hook, y + h + padding_y_hook)],
                        radius=20,
                        fill=(0, 0, 0, 220)
                    )

                    # 후킹 텍스트: 두터운 형광 옐로우 본체 + 선명한 외곽선
                    draw.text(
                        (x, y),
                        line,
                        font=hook_font,
                        fill=(255, 235, 0),
                        stroke_width=4,
                        stroke_fill=(0, 0, 0)
                    )

            if mode != "travel":
                # 커머스 모드는 씬 1에서 후킹 바 단독 노출
                return np.array(img)

        # -------------------------------------------------------------
        # Case 2: 나레이션 자막 (여행 상품 모드: 중앙 46% 런비 스타일 / 커머스 모드: 하단 62%)
        # -------------------------------------------------------------
        if caption_text and caption_text.strip():
            sub_text = caption_text.strip()

            if mode == "travel":
                # [여행 상품 전용 런비 스타일]: 시선 집중 중앙(46% 지점), 순백색(#FFFFFF) + 굵은 검은색 테두리(5px) + 드롭 섀도우
                sub_font_size = int(width * 0.072)
                center_y_sub = int(height * 0.46)
                stroke_width = 5
            else:
                sub_font_size = int(width * 0.064)
                center_y_sub = int(height * 0.62)
                stroke_width = 5

            try:
                sub_font = ImageFont.truetype(self.font_path, sub_font_size)
            except Exception:
                sub_font = ImageFont.load_default()

            sub_lines = self._wrap_text(sub_text, draw, sub_font, max_width=int(width * 0.85))
            if sub_lines:
                sub_line_height = int(sub_font_size * 1.35)
                total_sub_height = len(sub_lines) * sub_line_height
                start_y_sub = center_y_sub - (total_sub_height // 2)

                for i, line in enumerate(sub_lines):
                    bbox = draw.textbbox((0, 0), line, font=sub_font)
                    w = bbox[2] - bbox[0]
                    x = (width - w) // 2
                    y = start_y_sub + (i * sub_line_height)

                    # 1. 드롭 섀도우 (Drop Shadow): 약간 우하단 오프셋(+4, +4), 반투명 블랙 그림자
                    shadow_offset_x = 4
                    shadow_offset_y = 4
                    draw.text(
                        (x + shadow_offset_x, y + shadow_offset_y),
                        line,
                        font=sub_font,
                        fill=(0, 0, 0, 160),
                        stroke_width=stroke_width,
                        stroke_fill=(0, 0, 0, 160)
                    )

                    # 2. 순수 타이포그래피: 흰색 폰트(#FFFFFF) + 굵은 검은색 테두리(Stroke 5px)
                    # (검은색 반투명 사각형 Bounding Box는 완전 삭제하여 배경 영상을 가리지 않음)
                    draw.text(
                        (x, y),
                        line,
                        font=sub_font,
                        fill=(255, 255, 255, 255),
                        stroke_width=stroke_width,
                        stroke_fill=(0, 0, 0, 255)
                    )

        return np.array(img)

    def create_subtitle_image(self, text: str, width: int, height: int, mode: str = "commerce", **kwargs) -> np.ndarray:
        """기존 하위 호환성을 위한 단일 자막 생성 래퍼"""
        return self.create_overlay_image(caption_text=text, hook_title=None, width=width, height=height, mode=mode, **kwargs)

    def assemble_ad(
        self,
        asset_manifest: List[Dict[str, Any]],
        output_filename: str = "final_ad.mp4",
        fps: int = 24,
        bgm_path: Optional[str] = None,
        audio_mode: str = "voice_and_sub",
        mode: str = "commerce",
        **kwargs
    ) -> str:
        """
        Step 3: 각 씬별 비디오와 오디오를 오디오 길이에 맞춰 컷팅하고,
        상단 후킹 바 및 런비 스타일 중앙 자막을 합성한 뒤 최종 광고 비디오 결합.
        audio_mode가 'voice_and_bgm'이거나 bgm_path가 주어지면 15% 볼륨으로 BGM 동시 믹싱.
        """
        final_output_path = os.path.join(self.output_dir, output_filename)
        logger.info(f"Step 3 시작 (모드={mode}): 타임라인 조립, 자막 오버레이 합성 진행 (총 {len(asset_manifest)}개 씬, 오디오 모드: {audio_mode})")

        processed_clips = []
        opened_clips = []

        try:
            for idx, item in enumerate(asset_manifest, 1):
                scene_num = item.get("scene_index") or item.get("scene_number", idx)
                video_path = item.get("video_path")
                audio_path = item.get("audio_path")
                caption_text = item.get("subtitle") or item.get("caption") or item.get("voiceover", "")
                hook_title = item.get("hook_title", "")

                if not video_path or not os.path.exists(video_path):
                    raise FileNotFoundError(f"씬 {scene_num} 비디오 파일 누락: {video_path}")
                if not audio_path or not os.path.exists(audio_path):
                    raise FileNotFoundError(f"씬 {scene_num} 오디오 파일 누락: {audio_path}")

                # 1. 오디오 로드 (절대 중간에 자르지 않고 나레이션 음성 전량 보존)
                audio_clip = AudioFileClip(audio_path)
                opened_clips.append(audio_clip)
                audio_duration = audio_clip.duration
                logger.info(f"씬 {scene_num}: 나레이션 오디오 원본 길이 = {audio_duration:.2f}초")

                # 2. 비디오 길이 안전 연장 (엔딩 나레이션 잘림 원천 차단: 마지막 씬 +1.0초, 일반 씬 +0.2초 여유 마진)
                is_last_scene = (idx == len(asset_manifest))
                tail_margin = 1.0 if is_last_scene else 0.2
                scene_target_duration = audio_duration + tail_margin
                logger.info(f"씬 {scene_num} (마지막={is_last_scene}): 오디오 {audio_duration:.2f}초 + 테일 마진 {tail_margin:.2f}초 -> 비디오 목표 재생 시간: {scene_target_duration:.2f}초")

                # 3. 비디오 로드 및 목표 재생 시간에 맞춤
                video_clip = VideoFileClip(video_path)
                opened_clips.append(video_clip)
                video_duration = video_clip.duration

                if video_duration >= scene_target_duration:
                    synced_video = video_clip.subclip(0, scene_target_duration)
                else:
                    diff = scene_target_duration - video_duration
                    last_frame_clip = video_clip.to_ImageClip(t=max(0, video_duration - 0.05)).set_duration(diff)
                    opened_clips.append(last_frame_clip)
                    synced_video = concatenate_videoclips([video_clip, last_frame_clip], method="compose")

                # 4. 상단 후킹 바 & 런비 스타일 중앙 자막 오버레이 생성
                w, h = synced_video.size
                if caption_text or hook_title:
                    if mode == "travel":
                        logger.info(f"씬 {scene_num} [런비 스타일] 중앙 자막 오버레이 렌더링 (화면 46%): '{caption_text}'")
                    elif hook_title and hook_title.strip():
                        logger.info(f"씬 {scene_num} 상단 후킹 바 단독 렌더링 (모바일 세이프존 17%): '{hook_title}'")
                    else:
                        logger.info(f"씬 {scene_num} 하단 나레이션 자막 단독 렌더링 (쇼츠 UI 세이프존 62%): '{caption_text}'")

                    overlay_img_arr = self.create_overlay_image(
                        caption_text=caption_text,
                        hook_title=hook_title,
                        width=w,
                        height=h,
                        mode=mode
                    )
                    sub_clip = ImageClip(overlay_img_arr, ismask=False).set_duration(scene_target_duration)
                    opened_clips.append(sub_clip)
                    # 비디오 위에 오버레이 클립 합성
                    synced_video = CompositeVideoClip([synced_video, sub_clip])

                # 5. 오디오 결합 (오디오가 완전히 끝난 뒤에도 화면이 0.3~0.5초간 머물도록 비디오 길이에 맞춤)
                synced_video = synced_video.set_audio(audio_clip)

                # 6. 마지막 씬은 오디오 끝난 후 0.4초간 자연스러운 페이드아웃 적용
                if is_last_scene:
                    try:
                        synced_video = synced_video.fadeout(0.4)
                    except Exception as fe:
                        logger.debug(f"페이드아웃 적용 예외 (무시): {fe}")

                processed_clips.append(synced_video)
                opened_clips.append(synced_video)

            if not processed_clips:
                raise ValueError("합성할 유효한 비디오 클립이 없습니다.")

            # 전체 씬 결합
            logger.info(f"전체 {len(processed_clips)}개 클립 타임라인 결합 중...")
            final_composite = concatenate_videoclips(processed_clips, method="compose")
            opened_clips.append(final_composite)

            # 비디오 전체 타임라인 길이가 '모든 씬 오디오 총합 + 엔딩 여유 마진 1.0초'보다 짧지 않도록 강제 보장
            # (인위적인 subclip 잘림을 영구 제거하여 마지막 씬 "댓글에 '여행' 남겨주세요"가 절대 잘리지 않음)
            total_duration = final_composite.duration
            logger.info(f"최종 비디오 타임라인 총 재생 시간: {total_duration:.2f}초 (인위적 subclip 절단 없이 전량 보존)")
            logger.info(f"최종 영상 총 재생 시간: {total_duration:.2f}초")

            # 6. BGM 믹싱 처리 (voice_and_bgm 모드)
            if (audio_mode == "voice_and_bgm" or bgm_path) and bgm_path and os.path.exists(bgm_path):
                try:
                    from moviepy.editor import CompositeAudioClip
                    logger.info(f"배경음악(BGM) 믹싱 진행 (볼륨: 15% 덕킹): {bgm_path}")
                    bgm_clip = AudioFileClip(bgm_path).volumex(0.15)
                    if bgm_clip.duration < total_duration:
                        # 반복 재생
                        from moviepy.audio.fx.all import audio_loop
                        bgm_clip = audio_loop(bgm_clip, duration=total_duration)
                    else:
                        bgm_clip = bgm_clip.subclip(0, total_duration)
                    opened_clips.append(bgm_clip)
                    
                    mixed_audio = CompositeAudioClip([final_composite.audio, bgm_clip])
                    opened_clips.append(mixed_audio)
                    final_composite = final_composite.set_audio(mixed_audio)
                    logger.info("BGM 오디오 믹싱 완료.")
                except Exception as be:
                    logger.warning(f"BGM 믹싱 실패: {be}. 나레이션 단독 오디오로 계속 진행합니다.")

            logger.info(f"최종 렌더링 시작 -> {final_output_path} (FPS: {fps}, Codec: libx264, Audio: aac)")

            final_composite.write_videofile(
                final_output_path,
                fps=fps,
                codec="libx264",
                audio_codec="aac",
                preset="medium",
                threads=4,
                logger="bar"
            )

            logger.info(f"광고 영상 렌더링 완료! 파일 저장 경로: {final_output_path}")
            return final_output_path

        finally:
            logger.info("MoviePy 리소스 해제 중...")
            for c in opened_clips:
                try:
                    c.close()
                except Exception:
                    pass
