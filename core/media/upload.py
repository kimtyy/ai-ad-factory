"""
core/media/upload.py - 업로드 파일 판별 (확장자 필터 없이 올라온 파일을 서버에서 검사)

안드로이드 크롬(삼성 갤러리·Photo Picker)은 file input의 accept 확장자 목록이 걸려 있으면 content:// 파일이나
확장자 없는 파일, 대문자·특이 확장자 파일을 조용히 막는 경우가 있다. 그래서 위젯은 type=None으로 열어 두고,
파일이 서버로 넘어온 뒤 파일 머리글(magic bytes)로 사진·영상 여부를 판별한다 (머리글을 모르면 확장자로).
"""
import os
from typing import List, Optional, Tuple

# 머리글에서 못 알아볼 때만 쓰는 확장자 표
IMAGE_EXTS = {".jpg": ".jpg", ".jpeg": ".jpg", ".jpe": ".jpg", ".png": ".png", ".webp": ".webp",
              ".heic": ".heic", ".heif": ".heic", ".heics": ".heic", ".jfif": ".jpg"}
VIDEO_EXTS = {".mp4": ".mp4", ".m4v": ".m4v", ".mov": ".mov", ".qt": ".mov", ".3gp": ".mp4"}
HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}
QT_BRANDS = {b"qt  "}
UNSUPPORTED_BRANDS = {b"avif", b"avis"}


def sniff(data: bytes, name: str = "") -> Optional[Tuple[str, str]]:
    """(종류 'image'|'video', 저장 확장자) 또는 지원하지 않는 파일이면 None. 머리글 우선, 모르면 확장자"""
    head = bytes(data[:32])
    if head[:3] == b"\xff\xd8\xff":
        return "image", ".jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image", ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", ".webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in UNSUPPORTED_BRANDS:
            return None
        if brand in HEIF_BRANDS:
            return "image", ".heic"
        return "video", ".mov" if brand in QT_BRANDS else ".mp4"
    if head[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip"):      # ftyp 없이 시작하는 옛 QuickTime
        return "video", ".mov"
    ext = os.path.splitext(name or "")[-1].lower()
    if ext in IMAGE_EXTS and len(data) > 0 and head[:4] not in (b"%PDF", b"PK\x03\x04"):
        return "image", IMAGE_EXTS[ext]
    if ext in VIDEO_EXTS and len(data) > 0 and head[:4] not in (b"%PDF", b"PK\x03\x04"):
        return "video", VIDEO_EXTS[ext]
    return None


def human_size(n: int) -> str:
    return f"{n / 1_048_576:.1f}MB" if n >= 1_048_576 else f"{max(1, n // 1024)}KB"


def reject_reason(data: bytes, name: str = "") -> str:
    if not data:
        return "빈 파일이에요 (올라가는 중 끊겼을 수 있어요)"
    ext = os.path.splitext(name or "")[-1].lower()
    if data[:4] == b"%PDF":
        return "PDF는 쓸 수 없어요 (사진·영상만 가능)"
    if bytes(data[4:12]).startswith(b"ftyp") and bytes(data[8:12]) in UNSUPPORTED_BRANDS:
        return "AVIF 사진은 지원하지 않아요 (JPG·PNG·HEIC로 올려 주세요)"
    return f"사진(JPG·PNG·WebP·HEIC)이나 영상(MP4·MOV)이 아니에요{f' ({ext})' if ext else ''}"


# ---------------------------------------------------------------- 모바일 컴포넌트(core/ui/mobile_uploader)가 보낸 묶음 받기
import base64
import binascii

MAX_BATCH_FILES = 40
MAX_FILE_BYTES = 120 * 1024 * 1024


class UploadedBlob:
    """컴포넌트로 받은 파일을 st.file_uploader의 UploadedFile처럼 다루게 하는 얇은 껍데기 (name · getvalue · file_id)"""

    def __init__(self, name: str, data: bytes, batch_id: str, index: int):
        self.name = name
        self._data = data
        self.size = len(data)
        self.file_id = f"{batch_id}:{index}"

    def getvalue(self) -> bytes:
        return self._data


def decode_batch(value) -> Tuple[Optional[str], List["UploadedBlob"], List[Tuple[str, str]]]:
    """컴포넌트 반환값 → (batch_id, 받은 파일들, [(이름, 못 받은 이유)]). 값이 없거나 형식이 틀리면 (None, [], []) — 절대 예외를 내지 않는다.
    data_b64가 깨졌거나 비었거나 너무 크면 그 파일만 이유와 함께 제외한다 (data: URL 머리말은 허용)"""
    if not isinstance(value, dict):
        return None, [], []
    bid = value.get("batch_id")
    if not isinstance(bid, str) or not bid.strip():
        return None, [], []
    blobs: List[UploadedBlob] = []
    problems: List[Tuple[str, str]] = []
    for s in (value.get("skipped") or []):
        if isinstance(s, dict):
            problems.append((str(s.get("name") or "파일")[:80], str(s.get("reason") or "제외됨")[:80]))
    files = value.get("files")
    if not isinstance(files, list):
        return bid, [], problems
    for i, f in enumerate(files[:MAX_BATCH_FILES]):
        if not isinstance(f, dict):
            problems.append(("파일", "전송 형식이 올바르지 않아요"))
            continue
        name = str(f.get("name") or "").strip()[:120] or f"upload_{i + 1}"
        b64 = f.get("data_b64")
        if not isinstance(b64, str) or not b64:
            problems.append((name, "내용이 비어 있어요 (전송 중 끊겼을 수 있어요)"))
            continue
        if "," in b64[:100] and b64.lstrip().lower().startswith("data:"):
            b64 = b64.split(",", 1)[1]
        if len(b64) > MAX_FILE_BYTES * 4 // 3 + 16:
            problems.append((name, "파일이 너무 커요"))
            continue
        try:
            data = base64.b64decode("".join(b64.split()), validate=True)
        except (binascii.Error, ValueError):
            problems.append((name, "깨진 데이터예요 (다시 올려 주세요)"))
            continue
        if not data:
            problems.append((name, "내용이 비어 있어요"))
            continue
        blobs.append(UploadedBlob(name, data, bid, i))
    if len(files) > MAX_BATCH_FILES:
        problems.append(("초과분", f"한 번에 {MAX_BATCH_FILES}개까지만 받아요"))
    return bid, blobs, problems
