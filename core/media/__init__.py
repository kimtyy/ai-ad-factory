"""
core/media - 업로드 미디어(스마트폰 영상 등) 정규화
"""
from core.media.normalize import (
    VIDEO_EXTENSIONS,
    MediaInfo,
    is_video_file,
    probe_media,
    normalize_ugc_clip,
    extract_thumbnail,
)
