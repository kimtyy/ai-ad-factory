"""
core/ui/mobile_uploader - 모바일 전용 경량 업로더 (Streamlit 커스텀 컴포넌트)

안드로이드 크롬 + Streamlit Cloud iframe에서는 기본 st.file_uploader(PUT /_stcore/upload_file)가 파일을 서버로 보내지 못하는 경우가 있다.
이 컴포넌트는 그 업로드 경로를 쓰지 않는다: 브라우저에서 사진을 줄이고(긴 변 1440px·JPEG) base64로 만들어
Streamlit 컴포넌트 채널(setComponentValue)로 파이썬에 직접 전달한다. 외부 서버·CDN 의존성 없음 (index.html 한 장).

반환값: None 또는 {"batch_id": str, "files": [{"name", "type", "size", "data_b64"}], "skipped": [{"name", "reason"}]}
"""
import os
from typing import Any, Dict, Optional

import streamlit.components.v1 as components

_DIR = os.path.dirname(os.path.abspath(__file__))
_component = None


def mobile_uploader(*, key: str, label: str = "📸 내 폰 사진·영상 고르기 (여러 장 가능)", max_files: int = 10, max_dim: int = 1440,
                    quality: float = 0.88, max_video_mb: int = 40, disabled: bool = False) -> Optional[Dict[str, Any]]:
    """컴포넌트를 그리고 방금 보낸 묶음(batch)을 돌려준다. 아무것도 안 골랐으면 None. 같은 묶음은 rerun마다 다시 돌아오므로 batch_id로 중복을 걸러야 한다"""
    global _component
    if _component is None:
        _component = components.declare_component("mobile_uploader", path=_DIR)
    return _component(label=label, max_files=int(max_files), max_dim=int(max_dim), quality=float(quality),
                      max_video_mb=int(max_video_mb), disabled=bool(disabled), key=key, default=None)
