"""
tests/suite_thumbnail_styles.py - 쇼츠 썸네일 스타일 3종 (intense, magazine, navy_premium) 단위 테스트
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402

import os
import shutil
import tempfile
import numpy as np
from PIL import Image

from core.compositor import AdCompositor
from core.render.thumbnail import (
    STYLE_INTENSE,
    STYLE_MAGAZINE,
    STYLE_NAVY_PREMIUM,
    THUMBNAIL_STYLES,
    THUMBNAIL_STYLE_META,
    render_styled_thumbnail,
    render_memory_thumbnail,
)
from core import pipeline
from core.job import JobContext

def run_tests():
    work = tempfile.mkdtemp(prefix="thumb_test_")
    print(f"작업 디렉토리: {work}")
    comp = AdCompositor(output_dir=work)

    # 1. 테스트용 기본 이미지 프레임 생성 (1080x1920)
    img_src = Image.new("RGB", (1080, 1920), color=(120, 80, 60))
    # 중앙에 밝은 음식 느낌의 원 추가
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img_src)
    draw.ellipse((300, 700, 780, 1180), fill=(220, 160, 100))
    frame = np.array(img_src)

    # 2. 3종 스타일 기본 렌더링 검증
    for style in THUMBNAIL_STYLES:
        out_p = os.path.join(work, f"thumb_{style}.png")
        res = comp.render_thumbnail(
            frame,
            main_text="숙성 삼겹살과 된장찌개",
            out_path=out_p,
            sub_text="가평 현리 · 설맥",
            accent="🔥",
            style=style,
        )
        assert os.path.exists(res), f"{style} 썸네일 파일이 생성되지 않음"
        with Image.open(res) as im:
            assert im.size == (1080, 1920), f"{style} 해상도가 1080x1920이 아님: {im.size}"
            assert im.mode == "RGB", f"{style} 포맷이 RGB가 아님: {im.mode}"
        size_bytes = os.path.getsize(res)
        assert size_bytes > 20_000, f"{style} 파일 크기가 너무 작음 ({size_bytes} bytes)"
        print(f"[{style}] 렌더 성공: {size_bytes:,} bytes, 1080x1920")

    # 3. 스타일별 고유 시각적 특성 검증
    # 3-1. intense (상단 그라데이션 노랑/빨강 + 하단 흰색 알약 박스)
    ti = np.array(Image.open(os.path.join(work, "thumb_intense.png")).convert("RGB")).astype(int)
    yellow = int(((ti[:800, :, 0] > 200) & (ti[:800, :, 1] > 160) & (ti[:800, :, 2] < 100)).sum())
    white_box = int(((ti[900:1500, :, 0] > 240) & (ti[900:1500, :, 1] > 240) & (ti[900:1500, :, 2] > 240)).sum())
    assert yellow > 10_000, f"intense 상단 노랑 픽셀 부족: {yellow}"
    assert white_box > 10_000, f"intense 하단 흰색 박스 픽셀 부족: {white_box}"
    print(f"[intense 특성 통과] 노랑픽셀: {yellow:,}, 흰색박스: {white_box:,}")

    # 3-2. magazine (중앙 피사체 보존 + 하단 부드러운 다크 그라데이션)
    tm = np.array(Image.open(os.path.join(work, "thumb_magazine.png")).convert("RGB")).astype(int)
    # 상단/중앙(30~45%)은 어둡지 않고 밝게 유지
    center_brightness = tm[int(1920 * 0.35):int(1920 * 0.45), 300:780].mean()
    # 최하단(85~95%)은 짙은 그라데이션으로 어두움
    bottom_brightness = tm[int(1920 * 0.85):int(1920 * 0.95), :].mean()
    assert bottom_brightness < center_brightness - 30, f"magazine 하단 다크 그라데이션 미반영: {bottom_brightness} vs {center_brightness}"
    print(f"[magazine 특성 통과] 중앙 밝기 {center_brightness:.1f} vs 하단 다크 {bottom_brightness:.1f}")

    # 3-3. navy_premium (딥 네이비 #0F2744 + 골드 #F5C451)
    tn = np.array(Image.open(os.path.join(work, "thumb_navy_premium.png")).convert("RGB")).astype(int)
    # 네이비 색상 (R: 10~30, G: 30~55, B: 60~90)
    navy_pixels = int(((tn[..., 0] >= 5) & (tn[..., 0] <= 35) &
                       (tn[..., 1] >= 25) & (tn[..., 1] <= 60) &
                       (tn[..., 2] >= 50) & (tn[..., 2] <= 95)).sum())
    # 골드 색상 (R > 210, G > 165, B < 120)
    gold_pixels = int(((tn[..., 0] > 210) & (tn[..., 1] > 165) & (tn[..., 2] < 120)).sum())
    assert navy_pixels > 20_000, f"navy_premium 네이비 카드 바 픽셀 부족: {navy_pixels}"
    assert gold_pixels > 1_000, f"navy_premium 골드 포인트 픽셀 부족: {gold_pixels}"
    print(f"[navy_premium 특성 통과] 네이비픽셀: {navy_pixels:,}, 골드픽셀: {gold_pixels:,}")

    # 4. 특수 입력 및 엣지 케이스 검증
    # 4-1. 특수 공백/폭 없는 문자/긴 텍스트 줄바꿈
    weird_text = "이 곳은 절대로 놓쳐서는 안 되는 진짜  최고의 맛집​"
    edge_out = comp.render_thumbnail(frame, weird_text, os.path.join(work, "thumb_edge.png"), style="navy_premium")
    assert os.path.exists(edge_out) and os.path.getsize(edge_out) > 20_000, "특수 공백 텍스트 렌더 실패"

    # 4-2. 빈 텍스트 폴백
    bare_out = comp.render_thumbnail(frame, "", os.path.join(work, "thumb_bare.png"), style="magazine")
    assert os.path.exists(bare_out), "빈 텍스트 폴백 렌더 실패"

    # 4-3. JobSpec 파라미터 전달 및 파이프라인 연동 검증
    spec_navy = pipeline.JobSpec(
        mode="owner",
        name="테스트 매장",
        media_paths=[],
        thumbnail_style="navy_premium",
        mock=True,
    )
    assert spec_navy.thumbnail_style == "navy_premium", "JobSpec thumbnail_style 필드 반영 실패"

    spec_mag = pipeline.JobSpec(
        mode="owner",
        name="테스트 카페",
        media_paths=[],
        thumbnail_style="magazine",
        mock=True,
    )
    assert spec_mag.thumbnail_style == "magazine", "JobSpec magazine 스타일 반영 실패"

    shutil.rmtree(work, ignore_errors=True)
    print("\n✅ 모든 썸네일 스타일 3종 단위 테스트 완료!")

if __name__ == "__main__":
    run_tests()
