"""
core/media/library.py - 씬 소스(사진·영상) 목록 관리 (추가·삭제·순서 변경)

화면의 '영상 소스' 목록 = 씬 순서. 자동으로 가져온 사진(판매 페이지 등)과 사장님이 직접 올린 사진·영상을
같은 목록에서 다룹니다. 업로드 위젯이 목록 전체를 덮어쓰지 않도록 '추가'만 하고, 중복은 넣지 않습니다.
"""
from typing import Iterable, List

MAX_MEDIA = 10


def add_media(paths: List[str], new: Iterable[str], limit: int = MAX_MEDIA) -> List[str]:
    """뒤에 추가 (이미 있는 파일은 건너뜀, 최대 limit개)"""
    out = list(paths)
    for p in new:
        if p and p not in out and len(out) < limit:
            out.append(p)
    return out


def move_media(paths: List[str], index: int, delta: int) -> List[str]:
    """index번째를 delta만큼 이동 (-1: 위로, +1: 아래로). 범위 밖이면 그대로"""
    out = list(paths)
    j = index + delta
    if 0 <= index < len(out) and 0 <= j < len(out):
        out[index], out[j] = out[j], out[index]
    return out


def remove_media(paths: List[str], index: int) -> List[str]:
    out = list(paths)
    if 0 <= index < len(out):
        out.pop(index)
    return out
