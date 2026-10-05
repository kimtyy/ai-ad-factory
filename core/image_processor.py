"""
core/image_processor.py - rembg 기반 제품 누끼(투명 PNG) 추출 및 AI 연출 배경과의 자연스러운 합성
"""
import os
import logging
from typing import Optional, Tuple
from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger("ad_factory.image_processor")


def extract_product_cutout(
    input_image_path: str,
    output_cutout_path: str = "outputs/cache/product_cutout.png"
) -> str:
    """
    rembg 라이브러리를 사용하여 제품 원본 이미지에서 배경을 제거하고 투명 PNG(RGBA)를 생성합니다.
    rembg 모듈이 없거나 실패할 경우 PIL 기반의 부드러운 알파 마스크 폴백을 제공합니다.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_cutout_path)), exist_ok=True)
    logger.info(f"제품 누끼 추출 시작: {input_image_path} -> {output_cutout_path}")

    if not os.path.exists(input_image_path):
        raise FileNotFoundError(f"원본 이미지 파일을 찾을 수 없습니다: {input_image_path}")

    try:
        from rembg import remove
        with open(input_image_path, "rb") as input_file:
            input_bytes = input_file.read()
            output_bytes = remove(input_bytes)
        
        with open(output_cutout_path, "wb") as output_file:
            output_file.write(output_bytes)
            
        logger.info(f"rembg 기반 고품질 누끼 추출 성공: {output_cutout_path}")
        return os.path.abspath(output_cutout_path)

    except Exception as e:
        logger.warning(f"rembg 누끼 추출 실패 또는 모델 다운로드 미완료 ({e}). PIL 마스크 폴백으로 전환합니다.")
        # 폴백: 흰색/밝은 배경 부드러운 투명화 처리
        img = Image.open(input_image_path).convert("RGBA")
        datas = img.getdata()
        new_data = []
        for item in datas:
            # 배경이 거의 흰색인 경우 투명하게 변환
            if item[0] > 240 and item[1] > 240 and item[2] > 240:
                new_data.append((255, 255, 255, 0))
            else:
                new_data.append(item)
        img.putdata(new_data)
        img.save(output_cutout_path, "PNG")
        logger.info(f"PIL 폴백 누끼 이미지 저장 완료: {output_cutout_path}")
        return os.path.abspath(output_cutout_path)


def composite_cutout_on_background(
    background_path: str,
    cutout_path: str,
    output_path: str,
    scale_ratio: float = 0.50,
    y_center_ratio: float = 0.60
) -> str:
    """
    AI 생성 배경 이미지 위에 제품 투명 누끼를 자연스러운 드롭 섀도우(그림자)와 함께 합성합니다.
    - background_path: 9:16 배경 이미지 (예: 1080x1920)
    - cutout_path: 배경이 제거된 제품 투명 PNG
    - output_path: 최종 합성된 이미지 경로
    - scale_ratio: 배경 너비 대비 제품 너비 비율 (기본 0.50)
    - y_center_ratio: 화면 세로 높이 기준 제품 중심 Y 위치 비율 (기본 0.60, 중앙 하단부)
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    
    bg = Image.open(background_path).convert("RGBA")
    cutout = Image.open(cutout_path).convert("RGBA")

    bg_w, bg_h = bg.size

    # 제품 이미지 적응형 리사이즈 (가로세로 비율 보존)
    target_w = int(bg_w * scale_ratio)
    aspect = cutout.height / cutout.width
    target_h = int(target_w * aspect)

    # 너무 길 경우 높이 제한 (화면의 50%를 넘지 않도록)
    max_h = int(bg_h * 0.50)
    if target_h > max_h:
        target_h = max_h
        target_w = int(target_h / aspect)

    cutout_resized = cutout.resize((target_w, target_h), Image.Resampling.LANCZOS)

    # 위치 계산
    pos_x = (bg_w - target_w) // 2
    pos_y = int(bg_h * y_center_ratio) - (target_h // 2)

    # 1. 자연스러운 드롭 섀도우 (그림자) 생성
    shadow = Image.new("RGBA", (bg_w, bg_h), (0, 0, 0, 0))
    # 누끼의 알파 채널을 따서 반투명 검은색 실루엣 생성
    alpha_mask = cutout_resized.split()[-1]
    shadow_color = Image.new("RGBA", cutout_resized.size, (0, 0, 0, 110))
    shadow.paste(shadow_color, (pos_x + 8, pos_y + 16), mask=alpha_mask)
    # 가우시안 블러로 부드러운 그림자 효과
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=18))

    # 2. 배경 + 그림자 + 제품 누끼 레이어 블렌딩
    combined = Image.alpha_composite(bg, shadow)
    combined.paste(cutout_resized, (pos_x, pos_y), mask=cutout_resized.split()[-1])

    # 3. 최종 RGB 저장
    final_img = combined.convert("RGB")
    final_img.save(output_path, quality=95)
    logger.info(f"누끼 배경 합성 완료 -> {output_path}")
    return os.path.abspath(output_path)
