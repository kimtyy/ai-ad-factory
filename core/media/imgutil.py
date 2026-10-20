"""
core/media/imgutil.py - 고해상도 사진을 메모리에 풀로 올리지 않는 이미지 열기 (Streamlit Cloud 1GB 한도 대응)

스마트폰 원본(12MP 이상)을 그대로 열어 RGB로 바꾸면 한 장이 40MB 이상, 모션 확대본까지 더하면 장당 수백 MB가 된다.
영상 규격(1080x1920 + 줌·팬 여유)에는 긴 변 2560px이면 충분하므로, JPEG는 열 때부터 축소 디코딩(draft)하고
그 밖의 형식도 바로 줄여서 돌려준다.
"""
from typing import Any, Union

from PIL import Image, ImageOps

MAX_SIDE = 2560          # 렌더·합성용: 1080x1920 + Ken Burns 확대 여유(1.2배)를 덮는 크기
PREVIEW_SIDE = 768       # 화면 미리보기·비전 분석용


def open_rgb_downscaled(src: Union[str, Any], max_side: int = MAX_SIDE) -> Image.Image:
    """
    경로 또는 파일 객체의 이미지를 EXIF 방향을 바로잡은 RGB로 열되, 긴 변이 max_side를 넘으면 줄여서 반환한다.
    JPEG는 draft 모드로 디코딩 단계에서부터 줄여 원본 크기만큼 메모리를 쓰지 않는다.
    """
    with Image.open(src) as im:
        w, h = im.size
        if max(w, h) > max_side:
            s = max_side / float(max(w, h))
            try:
                im.draft("RGB", (max(1, int(w * s)), max(1, int(h * s))))          # JPEG: 축소 디코딩 (그 밖의 형식은 무시됨)
            except Exception:
                pass
        img = ImageOps.exif_transpose(im).convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return img
