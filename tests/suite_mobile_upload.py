import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import io
import os

from PIL import Image
from streamlit.testing.v1 import AppTest

from core.media.upload import reject_reason, sniff

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


def img_bytes(fmt, size=(120, 160), color=(200, 80, 60)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, fmt)
    return b.getvalue()


JPG, PNG, WEBP = img_bytes("JPEG"), img_bytes("PNG"), img_bytes("WEBP")
HEIC_HEAD = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 40         # 삼성·아이폰 HEIC 머리글
MP4_HEAD = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 40
MOV_HEAD = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  " + b"\x00" * 40

# ---------------------------------------------------------------- 1. 머리글 판별 (확장자는 믿지 않음)
check(sniff(JPG, "IMG_0001.JPG") == ("image", ".jpg"), "대문자 확장자 .JPG → 사진")
check(sniff(JPG, "") == ("image", ".jpg") and sniff(JPG, "1000012345") == ("image", ".jpg"), "확장자 없는 파일(content:// 숫자 이름) JPEG → 사진")
check(sniff(PNG, "스크린샷") == ("image", ".png"), "확장자 없는 PNG → 사진")
check(sniff(WEBP, "a.bin") == ("image", ".webp"), "엉뚱한 확장자의 WebP → 사진")
check(sniff(HEIC_HEAD, "photo.HEIC") == ("image", ".heic") and sniff(HEIC_HEAD, "") == ("image", ".heic"), "HEIC 머리글 → 사진(.heic), 확장자 없어도 인식")
check(sniff(MP4_HEAD, "VID_1.MP4") == ("video", ".mp4") and sniff(MP4_HEAD, "") == ("video", ".mp4"), "MP4 머리글 → 영상, 확장자 없어도 인식")
check(sniff(MOV_HEAD, "x") == ("video", ".mov"), "QuickTime(.mov) 머리글 → 영상")
check(sniff(JPG, "photo.mp4") == ("image", ".jpg"), "확장자가 거짓이어도 내용이 JPEG면 사진")
check(sniff(b"%PDF-1.7 ...", "a.pdf") is None and sniff(b"%PDF-1.7", "a.jpg") is None, "PDF는 거절 (확장자가 .jpg여도)")
check(sniff(b"PK\x03\x04zipdata", "photos.zip") is None and sniff(b"", "a.jpg") is None, "ZIP·빈 파일은 거절")
check(sniff(b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00", "a.avif") is None and "AVIF" in reject_reason(b"\x00\x00\x00\x1cftypavif", "a.avif"), "AVIF는 거절하고 이유 안내")
check("PDF" in reject_reason(b"%PDF-1.4", "a.pdf") and "빈 파일" in reject_reason(b"", "a.jpg"), "거절 안내 문구에 이유가 들어감")

# ---------------------------------------------------------------- 2. 실제 화면: type=None, 업로드 → 즉시 완료 안내·목록 반영, 거절은 경고
os.environ["STORE_APP"] = "1"
at = AppTest.from_file(os.path.join(ROOT, "app_store.py"), default_timeout=120).run()
up = at.get("file_uploader")
check(up and list(up[0].proto.type) == [], f"사진 업로더에 확장자 필터 없음 (accept 목록 {list(up[0].proto.type) if up else None})")
check(not at.exception, f"첫 화면 오류 없음 {[e.value[:100] for e in at.exception]}")

at.session_state["uploaded_paths"] = []
fu = [u for u in at.get("file_uploader") if "사진·영상" in u.proto.label][0]
fu.set_value([("IMG_20261118_1.JPG", JPG, "image/jpeg"), ("1000012345", PNG, "application/octet-stream"),
              ("heic사진.HEIC", HEIC_HEAD, "image/heic"), ("메뉴판.pdf", b"%PDF-1.7 menu", "application/pdf")])
at.run()
paths = at.session_state["uploaded_paths"]
msgs = [x.value for x in at.success] + [x.value for x in at.warning]
check(not at.exception, f"업로드 후 오류 없음 {[e.value[:120] for e in at.exception]}")
check(len(paths) == 2 and all(os.path.exists(p) for p in paths), f"사진 2개(JPG·확장자 없는 PNG)만 저장, PDF는 제외 — {len(paths)}개")
check(any("업로드 완료: IMG_20261118_1.JPG" in m and "KB" in m for m in msgs), "화면에 '업로드 완료: 파일명 (용량)'")
check(any("업로드 완료: 1000012345" in m for m in msgs), "확장자 없는 파일도 완료 표시")
check(any("올리지 못했어요: 메뉴판.pdf" in m and "PDF" in m for m in msgs), "지원하지 않는 PDF는 이유와 함께 경고 (조용히 버리지 않음)")
check(any("heic사진.HEIC" in m for m in msgs), f"가짜(깨진) HEIC는 저장 실패 시에도 안내가 남음 — {[m[:50] for m in msgs if 'HEIC' in m]}")
def _count_images(node):
    n = 0
    for c in getattr(node, "children", {}).values():
        n += (type(c).__name__ == "Image") + _count_images(c)
    return n


_imgs = _count_images(at.main)
check(_imgs == 2 and len(at.session_state["uploaded_paths"]) == 2, f"목록에 썸네일 미리보기가 바로 표시 (이미지 {_imgs}개)")

# 같은 업로더 상태에서 다시 실행해도 중복 추가·재안내 없음 (일부만 올라온 상태에서 위젯이 초기화되지 않음)
before = list(at.session_state["uploaded_paths"])
at.run()
check(at.session_state["uploaded_paths"] == before and not at.exception, "다시 실행해도 같은 파일을 두 번 넣지 않음")
key_before = [u.key for u in at.get("file_uploader")]
at.run()
check([u.key for u in at.get("file_uploader")] == key_before, f"업로더 키가 올릴 때마다 바뀌지 않음 {key_before}")

# ---------------------------------------------------------------- 3. 진짜 HEIC(삼성·아이폰 갤러리) → 올리면 JPG로 변환 저장 (pillow-heif가 있는 환경)
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    _hb = io.BytesIO()
    Image.new("RGB", (160, 120), (30, 120, 200)).save(_hb, "HEIF")
    REAL_HEIC = _hb.getvalue()
except Exception as e:
    REAL_HEIC = None
    print(f"SKIP 진짜 HEIC 변환 (pillow-heif 사용 불가: {type(e).__name__})")
if REAL_HEIC:
    check(sniff(REAL_HEIC, "") == ("image", ".heic"), "진짜 HEIC 바이트도 머리글로 사진 판별")
    at.session_state["uploaded_paths"] = []
    fu = [u for u in at.get("file_uploader") if "사진·영상" in u.proto.label][0]
    fu.set_value([("PHOTO_1.HEIC", REAL_HEIC, "image/heic")])
    at.run()
    hp = at.session_state["uploaded_paths"]
    ok_heic = len(hp) == 1 and hp[0].lower().endswith(".jpg") and Image.open(hp[0]).size == (160, 120)
    check(ok_heic and not at.exception, f"HEIC 업로드 → JPG로 변환 저장 {hp[-1:] and os.path.basename(hp[-1])}")

finish(fails)
