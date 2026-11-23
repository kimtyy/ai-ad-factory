"""
tests/suite_ai_motion.py - 작업실 1단계 사진 ➔ AI 모션 클립(Image-to-Video) 기능 단위 테스트
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from PIL import Image

# 레포 루트 경로 추가
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.ai_motion import (
    build_motion_prompt,
    get_veo_model,
    generate_ai_motion_clip,
    AiMotionError,
    BILLING_REQUIRED_MSG,
    DEFAULT_VEO_MODEL,
    MOTION_PRESETS,
)
from core.compositor import AdCompositor
import core.pipeline as pipeline


class TestAiMotionSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = os.path.join(ROOT, "scratch", "test_ai_motion")
        os.makedirs(cls.test_dir, exist_ok=True)
        cls.sample_img = os.path.join(cls.test_dir, "sample_food.jpg")
        img = Image.new("RGB", (720, 1280), color=(220, 100, 80))
        img.save(cls.sample_img)

    def test_01_build_motion_prompts_and_disclosure(self):
        """1. 프리셋별 프롬프트 생성 및 기본 AI 연출 고지 여부 검증"""
        # 시즐감
        p_sizzle, disc_sizzle = build_motion_prompt("sizzle")
        self.assertIn("steam", p_sizzle.lower())
        self.assertIn("sizzle", p_sizzle.lower())
        self.assertFalse(disc_sizzle)

        # 먹방 (기본 AI 연출 고지 True)
        p_mukbang, disc_mukbang = build_motion_prompt("mukbang")
        self.assertIn("korean customer", p_mukbang.lower())
        self.assertTrue(disc_mukbang)

        # 시네마틱
        p_cine, disc_cine = build_motion_prompt("cinematic")
        self.assertIn("camera orbit", p_cine.lower())
        self.assertFalse(disc_cine)

        # 직접 입력
        p_custom, disc_custom = build_motion_prompt("custom", "보글보글 끓는 해물탕")
        self.assertEqual(p_custom, "보글보글 끓는 해물탕")
        self.assertFalse(disc_custom)

        # 커스텀 텍스트 덧붙임
        p_extra, _ = build_motion_prompt("sizzle", "추가 디테일")
        self.assertTrue(p_extra.endswith("추가 디테일"))

    def test_02_veo_model_override(self):
        """2. 모델명 환경변수 오버라이드 검증"""
        # 기본값
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_veo_model(), DEFAULT_VEO_MODEL)

        # VEO_MODEL 오버라이드
        with patch.dict(os.environ, {"VEO_MODEL": "veo-3.1-fast-generate-001"}):
            self.assertEqual(get_veo_model(), "veo-3.1-fast-generate-001")

    def test_03_billing_required_error_guidance(self):
        """3. 유료 결제 미연결 / 무료 티어 / 쿼터 초과 시 한국어 안내 메시지 검증"""
        # mock genai client throwing 403 quota error
        mock_client = MagicMock()
        mock_client.models.generate_videos.side_effect = Exception("403 Forbidden: Resource has been exhausted (e.g. check quota or billing disabled)")

        with patch("google.genai.Client", return_value=mock_client):
            with self.assertRaises(AiMotionError) as ctx:
                generate_ai_motion_clip(
                    image_path=self.sample_img,
                    preset="sizzle",
                    api_key="fake_free_key",
                    mock=False,
                )
            self.assertIn(BILLING_REQUIRED_MSG, str(ctx.exception))
            self.assertIn("Google AI Studio 유료 결제", str(ctx.exception))

    def test_04_mock_motion_clip_generation(self):
        """4. Mock 모드에서 9:16 세로형 모션 비디오 클립 생성 및 메타데이터 검증"""
        res = generate_ai_motion_clip(
            image_path=self.sample_img,
            preset="mukbang",
            output_dir=self.test_dir,
            mock=True,
            duration_sec=3,
        )
        self.assertTrue(os.path.exists(res["video_path"]))
        self.assertTrue(res["video_path"].endswith(".mp4"))
        self.assertEqual(res["preset"], "mukbang")
        self.assertTrue(res["ai_disclosure"])  # mukbang 기본 고지 True
        self.assertEqual(res["duration_sec"], 3.0)

    def test_05_scene_media_swap_and_revert_logic(self):
        """5. 1단계 씬 목록 사진 ➔ MP4 비디오 자동 교체 및 원본 복구 시뮬레이션"""
        paths = [self.sample_img, "dummy_02.jpg", "dummy_03.jpg"]
        backups = {}
        disclosure_map = {}

        # AI 모션 변환 생성 (Mock)
        res = generate_ai_motion_clip(paths[0], preset="sizzle", mock=True)
        vpath = res["video_path"]

        # 교체 반영
        target_idx = 0
        backups[vpath] = paths[target_idx]
        disclosure_map[vpath] = res["ai_disclosure"]
        paths[target_idx] = vpath

        self.assertEqual(paths[0], vpath)
        self.assertTrue(paths[0].endswith(".mp4"))
        self.assertEqual(backups[vpath], self.sample_img)

        # 원본 사진으로 복원 시뮬레이션
        paths[target_idx] = backups[vpath]
        self.assertEqual(paths[0], self.sample_img)

    def test_06_ai_disclosure_badge_rendering(self):
        """6. AdCompositor AI 연출 고지 반투명 배지 렌더링 검증"""
        comp = AdCompositor(output_dir=self.test_dir)
        badge = comp.render_ai_disclosure(width=1080, height=1920, text="AI 연출 포함")
        self.assertEqual(badge.size, (1080, 1920))
        self.assertEqual(badge.mode, "RGBA")
        # 배지가 투명하지 않은 픽셀을 포함하는지 확인
        extrema = badge.getextrema()
        alpha_max = extrema[3][1]
        self.assertGreater(alpha_max, 0, "AI 연출 배지에 불투명 픽셀이 렌더링되어야 합니다.")


if __name__ == "__main__":
    unittest.main()
