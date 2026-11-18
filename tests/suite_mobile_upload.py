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
check(sum("준비 완료" in x.value for x in at.success) == 1 and any("총 2개 준비 완료" in m for m in msgs), "성공 안내는 맨 위에 딱 1줄 '총 N개 준비 완료' (파일마다 박스 없음)")
check(not any("업로드 완료:" in m for m in msgs), "파일마다 '업로드 완료: 파일명' 박스를 만들지 않음")
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

# ---------------------------------------------------------------- 4. 모바일 전용 컴포넌트(core/ui/mobile_uploader) 수신: 디코딩·중복 방지·깨진 데이터 방어
import base64
import subprocess
import shutil

from core.media.upload import decode_batch


def b64(b):
    return base64.b64encode(b).decode()


batch = {"batch_id": "b-1", "files": [{"name": "a.jpg", "type": "image/jpeg", "size": len(JPG), "data_b64": b64(JPG)},
                                     {"name": "clip.mp4", "type": "video/mp4", "size": len(MP4_HEAD), "data_b64": b64(MP4_HEAD)},
                                     {"name": "p.heic", "type": "image/heic", "data_b64": "data:image/heic;base64," + b64(HEIC_HEAD)}],
         "skipped": [{"name": "big.mp4", "reason": "영상이 40MB보다 커요"}]}
bid, blobs, probs = decode_batch(batch)
check(bid == "b-1" and [b_.name for b_ in blobs] == ["a.jpg", "clip.mp4", "p.heic"] and blobs[0].getvalue() == JPG and blobs[2].getvalue() == HEIC_HEAD,
      "batch_id·파일 3개 디코딩 (data: 머리말이 붙어도 OK)")
check(probs == [("big.mp4", "영상이 40MB보다 커요")], "브라우저가 제외한 파일은 이유와 함께 전달")
check(blobs[0].file_id != blobs[1].file_id and blobs[0].name == "a.jpg" and blobs[0].size == len(JPG), "UploadedBlob: 파일마다 고유 file_id·이름·크기")
check(decode_batch(None) == (None, [], []) and decode_batch("x") == (None, [], []) and decode_batch({"files": []}) == (None, [], []) and decode_batch({"batch_id": ""}) == (None, [], []),
      "값이 없거나 형식이 틀리면 (None, [], []) — 예외 없음")
_, bl2, pr2 = decode_batch({"batch_id": "b-2", "files": [{"name": "깨짐.jpg", "data_b64": "@@@###"}, {"name": "빈.jpg", "data_b64": ""}, {"name": "없음.jpg"}, "문자열", {"name": "ok.png", "data_b64": b64(PNG)}]})
check([x.name for x in bl2] == ["ok.png"] and {n for n, _ in pr2} == {"깨짐.jpg", "빈.jpg", "없음.jpg", "파일"}, f"깨진·빈·누락 데이터는 그 파일만 제외하고 나머지는 받음 {pr2}")
_, bl3, pr3 = decode_batch({"batch_id": "b-3", "files": [{"name": "n.jpg", "data_b64": b64(JPG)}] * 45})
check(len(bl3) == 40 and any("40개" in r for _, r in pr3), "한 번에 40개 초과분은 이유와 함께 제외")

# 화면 경로: 컴포넌트가 값을 돌려주는 것처럼 대체해서 저장·완료 안내·중복 방지·rerun 안전 확인
import core.ui.mobile_uploader as mu_mod
_orig = mu_mod.mobile_uploader
state = {"value": None, "calls": []}


def fake_component(**kw):
    state["calls"].append(kw)
    return state["value"]


mu_mod.mobile_uploader = fake_component
try:
    at = AppTest.from_file(os.path.join(ROOT, "app_store.py"), default_timeout=120).run()
    check(not at.exception and state["calls"] and state["calls"][-1]["key"].startswith("mobile_uploader_") and state["calls"][-1]["max_files"] >= 1,
          f"첫 화면에 모바일 업로더가 그려짐 (key {state['calls'][-1]['key'] if state['calls'] else None})")
    at.session_state["uploaded_paths"] = []
    state["value"] = {"batch_id": "phone-1", "files": [{"name": "IMG_1.jpg", "type": "image/jpeg", "data_b64": b64(JPG)},
                                                      {"name": "IMG_2.jpg", "type": "image/jpeg", "data_b64": b64(img_bytes("JPEG", (200, 100), (10, 90, 160)))},
                                                      {"name": "망가짐.jpg", "type": "image/jpeg", "data_b64": "###"}],
                      "skipped": [{"name": "큰영상.mp4", "reason": "영상이 40MB보다 커요 (아래 PC용 업로더를 쓰세요)"}]}
    at.run()
    msgs = [x.value for x in at.success] + [x.value for x in at.warning]
    check(not at.exception and len(at.session_state["uploaded_paths"]) == 2 and all(os.path.exists(p) for p in at.session_state["uploaded_paths"]),
          f"컴포넌트로 받은 사진 2개 저장 (깨진 1개·브라우저 제외 1개 빼고) {len(at.session_state['uploaded_paths'])}개 {[e.value[:80] for e in at.exception]}")
    check(any("총 2개 준비 완료" in m for m in msgs) and any("올리지 못했어요: 망가짐.jpg" in m for m in msgs) and any("큰영상.mp4" in m for m in msgs),
          "1줄 요약 + 거절 안내 표시 (깨진 데이터·큰 영상도 조용히 버리지 않음)")
    check(at.session_state["last_mobile_upload_batch_id"] == "phone-1", "처리한 batch_id 기록")
    before = list(at.session_state["uploaded_paths"])
    at.run(); at.run()
    check(at.session_state["uploaded_paths"] == before, "같은 batch_id가 rerun마다 돌아와도 중복 저장하지 않음")
    # 사용자가 하나 지운 뒤 같은 묶음이 또 돌아와도 다시 살아나지 않음
    at.session_state["uploaded_paths"] = before[:1]
    at.run()
    check(at.session_state["uploaded_paths"] == before[:1], "지운 사진이 같은 묶음 재수신으로 되살아나지 않음")
    state["value"] = {"batch_id": "phone-2", "files": [{"name": "IMG_3.png", "type": "image/png", "data_b64": b64(PNG)}], "skipped": []}
    at.run()
    check(len(at.session_state["uploaded_paths"]) == 2 and at.session_state["last_mobile_upload_batch_id"] == "phone-2", "새 batch_id는 목록 뒤에 추가")
    state["value"] = {"batch_id": "", "files": "엉망"}
    at.run()
    check(not at.exception and any("형식이 올바르지 않아요" in c.value for c in at.caption), "엉뚱한 반환값에도 앱이 죽지 않고 상태줄에 원인 표시")
    pcs = [u for u in at.get("file_uploader") if "사진·영상" in u.proto.label]
    check(len(pcs) == 1 and list(pcs[0].proto.type) == [] and any("PC용 끌어다 놓기" in e.label for e in at.expander), "PC용 끌어다 놓기는 보조 영역(접힌 칸)으로 유지, 확장자 필터 없음")
    # 컴포넌트 자체가 로드되지 않아도 기본 업로더로 계속 사용 가능
    def broken(**kw):
        raise RuntimeError("component missing")
    mu_mod.mobile_uploader = broken
    at2 = AppTest.from_file(os.path.join(ROOT, "app_store.py"), default_timeout=120).run()
    check(not at2.exception and any("모바일 업로더 로드 실패" in c.value for c in at2.caption) and at2.get("file_uploader"), "컴포넌트 로드 실패 시에도 기본 업로더로 계속 사용 + 원인 표시")
finally:
    mu_mod.mobile_uploader = _orig

# 실제 컴포넌트 등록(declare_component) 가능 + index.html은 외부 CDN 없이 프로토콜만 사용
html_path = os.path.join(ROOT, "core", "ui", "mobile_uploader", "index.html")
html = open(html_path, encoding="utf-8").read()
check(os.path.exists(html_path) and "http://" not in html and "https://" not in html and "<script src" not in html, "index.html 외부 CDN·원격 스크립트 의존성 0개")
check("streamlit:setComponentValue" in html and "streamlit:componentReady" in html and 'accept="image/*,video/*' in html and "multiple" in html, "Streamlit 컴포넌트 프로토콜·image/video accept·multiple")
# ---------------------------------------------------------------- 5. 모바일 스크롤 압축: 4열 정사각형 바둑판 CSS(단독 앱에도 주입) + 보안 설정 복원
at.session_state["uploaded_paths"] = [p for p in at.session_state["uploaded_paths"] if os.path.exists(p)]
at.run()
css = " ".join(m.value for m in at.markdown if "st-key-media_grid" in (m.value or ""))
check("grid-template-columns: repeat(4, minmax(0, 1fr))" in css and "aspect-ratio: 1 / 1" in css and "object-fit: cover" in css,
      "단독 매장 앱에도 영상 소스 4열 정사각형 바둑판 CSS가 주입됨 (모바일 NAV_CSS에만 있으면 샵스토리에서는 안 먹음)")
check("st-key-photo_grid" in css and "calc(25% - .3rem)" in css, "후보 사진 그리드도 모바일 4열")
tag_ = lambda p: __import__("hashlib").md5(p.encode()).hexdigest()[:8]
_keys = {b_.key for b_ in at.button}
_p = at.session_state["uploaded_paths"]
check(len(_p) == 2 and all(f"media_{k}_{tag_(p)}" in _keys for p in _p for k in ("up", "down", "del")), "사진마다 ◀ ▶ 🗑 버튼이 바둑판 항목 안에 있음")
check(not any(s_.key and s_.key.startswith("media_pos_") for s_ in at.selectbox), "항목마다 '순서' 선택칸(세로를 늘리던 것)은 없음")
cfg = open(os.path.join(ROOT, ".streamlit", "config.toml"), encoding="utf-8").read()
import tomllib
srv = tomllib.loads(cfg).get("server", {})
check(srv.get("maxUploadSize") == 500 and srv.get("enableXsrfProtection", True) is True and srv.get("enableCORS", True) is True and "false" not in cfg.lower().split("[server]")[1].split("[browser]")[0].replace("headless = true", ""),
      f"config.toml: XSRF·CORS 보호 복원(끄는 설정 없음), maxUploadSize=500 유지 — {srv}")


def _find(node, name):
    n = 0
    for c in getattr(node, "children", {}).values():
        n += (type(c).__name__ == name) + _find(c, name)
    return n


at_real = AppTest.from_file(os.path.join(ROOT, "app_store.py"), default_timeout=120).run()      # 진짜 컴포넌트(가짜로 바꾸지 않음)
check(not at_real.exception and _find(at_real.main, "UnknownElement") >= 1, "실제 앱에서 declare_component(로컬 폴더)가 등록·렌더됨 (커스텀 컴포넌트 요소 있음)")

# JS 로직 (노드가 있으면): 프로토콜 메시지·1440px 리사이즈·JPEG 0.88·HEIC 원본 전송·개수/용량 제한
node = shutil.which("node")
if node:
    r = subprocess.run([node, os.path.join(ROOT, "tests", "mobile_uploader_js_test.js")], capture_output=True, text=True, encoding="utf-8", timeout=60)
    for line in r.stdout.splitlines():
        if line.startswith(("PASS ", "FAIL ")):
            check(line.startswith("PASS "), "JS: " + line[5:])
    check(r.returncode == 0, f"JS 테스트 종료 코드 {r.returncode} {r.stderr[:120]}")
else:
    print("SKIP JS 테스트 (node 없음)")

finish(fails)
