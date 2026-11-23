"""
tests/suite_ai_motion.py - 작업실 1단계 사진 ➔ AI 모션 클립(Image-to-Video, Google Veo) 검증

SDK 검증은 클라이언트 전체를 가짜로 바꾸지 않고 '네트워크 계층(BaseApiClient.request / download_file)'만 가로챈다.
그래야 google-genai 가 실제로 만드는 요청 본문·응답 파싱·다운로드 경로를 그대로 통과한다 (예전 테스트는 Client를 통째로 가짜로 바꿔 SDK 사양 위반을 못 잡았다).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import json
import os
import shutil
from types import SimpleNamespace as NS
from unittest import mock

from PIL import Image

import google.genai._api_client as AC
from google.genai import errors as gerr

import core.ai_motion as am
import core.pipeline as pl
from core.compositor import AdCompositor
from core.job import JobContext

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


W = tmp("ai_motion_")
IMG = os.path.join(W, "food.jpg")
Image.new("RGB", (720, 1280), (220, 100, 80)).save(IMG)

# ---------------------------------------------------------------- 1. 프리셋·프롬프트·고지 기본값
p, d = am.build_motion_prompt("sizzle")
check("steam" in p.lower() and not d, "시즐감 프리셋: 김·지글 프롬프트, 고지 기본 꺼짐")
p, d = am.build_motion_prompt("mukbang")
check("korean customer" in p.lower() and d, "먹방 프리셋: 인물 연출이라 고지 기본 켜짐")
check(am.build_motion_prompt("custom", "보글보글 끓는 해물탕")[0] == "보글보글 끓는 해물탕" and am.build_motion_prompt("sizzle", "추가 디테일")[0].endswith("추가 디테일"), "직접 입력·추가 지시문")

# ---------------------------------------------------------------- 2. 모델 체인·길이 규칙
with mock.patch.dict(os.environ, {}, clear=False):
    os.environ.pop("VEO_MODEL", None)
    check(am.get_veo_model() == am.DEFAULT_VEO_MODEL == "veo-3.1-fast-generate-001" and am.candidate_models() == list(am.VEO_FALLBACK_MODELS), "기본 모델은 Veo 3.1 fast, 폴백 체인 3.1 fast → 3.1 fast preview → 3.0 fast → 2.0")
    os.environ["VEO_MODEL"] = "veo-3.0-generate-001"
    check(am.candidate_models()[0] == "veo-3.0-generate-001" and am.candidate_models()[1:] == list(am.VEO_FALLBACK_MODELS) and am.get_veo_model() == "veo-3.0-generate-001",
          "VEO_MODEL로 지정한 모델을 먼저 시도하고 그다음 폴백 체인 (중복 제거)")
    os.environ["VEO_MODEL"] = "models/veo-2.0-generate-001"
    check(am.candidate_models()[0] == "veo-2.0-generate-001" and am.candidate_models().count("veo-2.0-generate-001") == 1, "'models/' 접두사 제거·중복 제거")
    os.environ.pop("VEO_MODEL", None)
check([am.duration_for_model("veo-3.1-fast-generate-001", x) for x in (3, 4, 5, 6, 7, 8, 12)] == [4, 4, 6, 6, 8, 8, 8], "Veo 3.x는 4·6·8초만: 5초 요청 → 6초")
check([am.duration_for_model("veo-2.0-generate-001", x) for x in (3, 5, 6, 8, 12)] == [5, 5, 6, 8, 8], "Veo 2는 5~8초")

# ---------------------------------------------------------------- 3. 오류 분류: 400 전부를 '결제 필요'로 뭉개지 않음
cl = am.classify_api_error
check(cl(404, "NOT_FOUND", "Model is not found") == "not_found" and cl(403, "PERMISSION_DENIED", "x") == "billing" and cl(429, "RESOURCE_EXHAUSTED", "quota") == "quota"
      and cl(400, "FAILED_PRECONDITION", "billing is not enabled") == "billing" and cl(400, "INVALID_ARGUMENT", "durationSeconds must be 6") == "invalid" and cl(500, "INTERNAL", "boom") == "other",
      "오류 분류: 404 모델없음 / 403 결제 / 429 한도 / 400 FAILED_PRECONDITION 결제 / 400 INVALID_ARGUMENT 잘못된 값 / 그 외")


# ---------------------------------------------------------------- 4. SDK 사양 준수: 실제 google-genai 코드가 만드는 요청·응답·다운로드
def done_body(model, uri="https://generativelanguage.googleapis.com/v1beta/files/xyz:download?alt=media"):
    return {"name": f"models/{model}/operations/abc", "done": True,
            "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": uri}}]}}}


class FakeNet:
    """BaseApiClient.request/download_file 가로채기. script: 모델별 POST 동작 {model: 'ok'|'404'|'403'|'400'|'429'|'opfail'|'rai'|'empty'}"""

    def __init__(self, script=None):
        self.script = script or {}
        self.posts, self.gets, self.downloads = [], [], []

    def request(self, client, http_method, path, request_dict, http_options=None):
        model = path.split("models/")[1].split(":")[0].split("/")[0]
        if http_method.lower() == "post":
            self.posts.append((model, json.loads(json.dumps(request_dict))))
            mode = self.script.get(model, "ok")
            if mode in ("404", "403", "400", "429"):
                code = int(mode)
                status = {404: "NOT_FOUND", 403: "PERMISSION_DENIED", 400: "INVALID_ARGUMENT", 429: "RESOURCE_EXHAUSTED"}[code]
                raise gerr.ClientError(code, {"error": {"code": code, "message": f"{status} for {model}", "status": status}})
            return NS(body=json.dumps({"name": f"models/{model}/operations/abc", "done": False}), headers={})
        self.gets.append(path)
        mode = self.script.get(model, "ok")
        if mode == "opfail":
            return NS(body=json.dumps({"name": f"models/{model}/operations/abc", "done": True, "error": {"code": 8, "message": "Resource has been exhausted (e.g. check quota)."}}), headers={})
        if mode == "rai":
            return NS(body=json.dumps({"name": f"models/{model}/operations/abc", "done": True,
                                       "response": {"generateVideoResponse": {"raiMediaFilteredCount": 1, "raiMediaFilteredReasons": ["people not allowed"]}}}), headers={})
        if mode == "empty":
            return NS(body=json.dumps({"name": f"models/{model}/operations/abc", "done": True, "response": {"generateVideoResponse": {}}}), headers={})
        return NS(body=json.dumps(done_body(model)), headers={})

    def download_file(self, client, path, http_options=None):
        self.downloads.append(path)
        return b"\x00\x00\x00\x18ftypmp42FAKE-VIDEO"


def run_motion(script=None, **kw):
    net = FakeNet(script)
    out = tmp("ai_run_")
    san = lambda a, b, max_duration=5.0: (open(b, "wb").write(open(a, "rb").read()), b)[1]        # noqa: E731  ffmpeg 후처리는 별도 검증, 여기선 파일 복사
    with mock.patch.object(AC.BaseApiClient, "request", lambda self, *a, **k: net.request(self, *a, **k)), \
            mock.patch.object(AC.BaseApiClient, "download_file", lambda self, *a, **k: net.download_file(self, *a, **k)), \
            mock.patch("time.sleep", lambda s: None), mock.patch.object(am, "_sanitize_video_with_ffmpeg", san):
        try:
            res = am.generate_ai_motion_clip(IMG, preset="sizzle", output_dir=out, api_key="k", mock=False, **kw)
            err = None
        except Exception as e:  # noqa: BLE001
            res, err = None, e
    return net, res, err


os.environ.pop("VEO_MODEL", None)
net, res, err = run_motion(duration_sec=5)
check(err is None and res and res["model"] == "veo-3.1-fast-generate-001" and os.path.exists(res["video_path"]), f"실제 SDK 경로로 영상 생성 성공 ({err})")
model, body = net.posts[0]
params = body.get("parameters", {})
check("generateAudio" not in json.dumps(body) and "generate_audio" not in json.dumps(body), "Gemini API 요청에 generate_audio를 넣지 않음 (넣으면 SDK가 ValueError — 예전 코드는 항상 실패했음)")
check(params.get("aspectRatio") == "9:16" and params.get("durationSeconds") == 6 and params.get("personGeneration") == "allow_adult", f"요청 파라미터: 9:16·Veo 3.1은 6초로 보정·allow_adult {params}")
inst = body["instances"][0]
check("prompt" in inst and "image" in inst and (inst["image"].get("bytesBase64Encoded") or inst["image"].get("inlineData")), "프롬프트 + 이미지(base64) 입력")
check(len(net.gets) >= 1 and len(net.downloads) == 1 and "files/xyz" in net.downloads[0] and open(res["video_path"], "rb").read().endswith(b"FAKE-VIDEO"), "작업 폴링 후 files.download(Video 객체)로 결과 파일 저장")
check(res["duration_sec"] == 6.0 and res["ai_disclosure"] is False, "결과 메타: 실제 쓴 길이·고지 여부")

# 모델 폴백: 404면 다음 모델로
net, res, err = run_motion({"veo-3.1-fast-generate-001": "404", "veo-3.1-fast-generate-preview": "404"}, duration_sec=5)
check(err is None and res["model"] == "veo-3.0-fast-generate-001" and [m for m, _ in net.posts] == ["veo-3.1-fast-generate-001", "veo-3.1-fast-generate-preview", "veo-3.0-fast-generate-001"],
      f"모델이 404면 다음 모델로 폴백: {[m for m, _ in net.posts]} → {res and res['model']}")
net, res, err = run_motion({m: "404" for m in am.VEO_FALLBACK_MODELS[:-1]}, duration_sec=5)
check(err is None and res["model"] == "veo-2.0-generate-001" and net.posts[-1][1]["parameters"]["durationSeconds"] == 5, "Veo 2까지 내려가면 길이는 5초(2.0 허용 범위)로 다시 맞춤")
net, res, err = run_motion({m: "404" for m in am.VEO_FALLBACK_MODELS}, duration_sec=5)
check(isinstance(err, am.AiMotionError) and "Veo 영상 모델을 찾지 못했어요" in str(err) and all(m in str(err) for m in am.VEO_FALLBACK_MODELS), f"전부 404면 시도한 모델 목록과 함께 안내: {str(err)[:80]}")
os.environ["VEO_MODEL"] = "veo-3.0-generate-001"
net, res, err = run_motion(duration_sec=5)
check(res and res["model"] == "veo-3.0-generate-001" and net.posts[0][0] == "veo-3.0-generate-001", "VEO_MODEL로 지정한 모델을 가장 먼저 호출")
os.environ.pop("VEO_MODEL", None)

# 오류 안내
net, res, err = run_motion({"veo-3.1-fast-generate-001": "403"})
check(isinstance(err, am.AiMotionError) and am.BILLING_REQUIRED_MSG in str(err) and len(net.posts) == 1, "403(권한·결제) → 유료 결제 안내, 다른 모델로 헛돌지 않음")
net, res, err = run_motion({"veo-3.1-fast-generate-001": "429"})
check(isinstance(err, am.AiMotionError) and am.QUOTA_MSG in str(err), "429 → 호출 한도 안내")
net, res, err = run_motion({"veo-3.1-fast-generate-001": "400"})
check(isinstance(err, am.AiMotionError) and "요청이 거절" in str(err) and am.BILLING_REQUIRED_MSG not in str(err), f"400(잘못된 값)은 결제 문제로 오인하지 않고 원인을 그대로: {str(err)[:70]}")
net, res, err = run_motion({"veo-3.1-fast-generate-001": "opfail"})
check(isinstance(err, am.AiMotionError) and am.QUOTA_MSG in str(err), "작업 자체가 한도 오류로 실패하면 한도 안내")
net, res, err = run_motion({"veo-3.1-fast-generate-001": "rai"})
check(isinstance(err, am.AiMotionError) and "안전 필터" in str(err) and "people not allowed" in str(err), "안전 필터로 비디오가 없으면 이유와 함께 안내")
net, res, err = run_motion({"veo-3.1-fast-generate-001": "empty"})
check(isinstance(err, am.AiMotionError) and "생성된 비디오가 없습니다" in str(err), "응답에 비디오가 없으면 안내")
with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
    try:
        am.generate_ai_motion_clip(IMG, api_key="", mock=False)
        raised = False
    except am.AiMotionError as e:
        raised = "GEMINI_API_KEY" in str(e)
check(raised, "키가 없으면 안내")
try:
    am.generate_ai_motion_clip(os.path.join(W, "없는사진.jpg"), mock=True)
    raised = False
except FileNotFoundError:
    raised = True
check(raised, "원본 사진이 없으면 FileNotFoundError")

# ---------------------------------------------------------------- 5. 후처리(실제 ffmpeg): 소리 제거·9:16·길이 제한
mock_clip = am.generate_ai_motion_clip(IMG, preset="mukbang", output_dir=W, mock=True, duration_sec=3)
check(os.path.exists(mock_clip["video_path"]) and mock_clip["ai_disclosure"] is True and mock_clip["preset"] == "mukbang", "Mock 모드: 9:16 모션 클립 생성, 먹방은 고지 기본 켜짐")
import subprocess
import imageio_ffmpeg
FF = imageio_ffmpeg.get_ffmpeg_exe()
with_audio = os.path.join(W, "with_audio.mp4")
subprocess.run([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24:duration=8", "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", with_audio], check=True)
clean = am._sanitize_video_with_ffmpeg(with_audio, os.path.join(W, "clean.mp4"), max_duration=6.0)
info = subprocess.run([FF, "-hide_banner", "-i", clean], capture_output=True, text=True).stderr
import re
check("Audio:" not in info and "1080x1920" in info and re.search(r"Duration: 00:00:0[56]", info), "후처리: 소리 제거(Veo 3.x가 만든 소리 포함)·1080x1920·6초로 자름")

# ---------------------------------------------------------------- 6. AI 연출 고지: 종단 간 (화면 체크값 → JobSpec → 렌더 매니페스트 → 최종 MP4 프레임)
job0 = JobContext.create(app="aimotion")
job0.root = W
job = JobContext(job0.job_id, root=W)
photos = []
for i in range(3):
    pth = os.path.join(job.uploads_dir, f"p{i}.jpg")
    shutil.copy(fixture("photo_pool.jpg" if i % 2 == 0 else "photo_room.jpg"), pth)
    photos.append(pth)
clip = am.generate_ai_motion_clip(photos[1], preset="mukbang", output_dir=job.assets_dir, mock=True, duration_sec=4)
vpath = clip["video_path"]
media = [photos[0], vpath, photos[2]]                                    # 화면: uploaded_paths[1]을 AI 영상으로 교체
base = dict(mode="owner", name="효정명인갈비", usp="숯불 양념 생갈비", business_type="food", search_keyword="효정명인갈비", address="경기 가평군 설악면",
            media_paths=media, mock=True, audio_mode="voice_and_sub")

sc_probe = {"media": {"type": "video", "path": vpath}}
check(pl.scene_ai_disclosure(pl.JobSpec(**base, ai_disclosure_map={vpath: True}), sc_probe, img=None) is True, "영상 씬(image_path 없음)도 media.path로 고지 여부를 찾음")
check(pl.scene_ai_disclosure(pl.JobSpec(**base, ai_disclosure_map={vpath: True}), {"image_path": photos[0]}) is False and pl.scene_ai_disclosure(pl.JobSpec(**base), sc_probe) is False
      and pl.scene_ai_disclosure(pl.JobSpec(**base, ai_disclosure_map={vpath: False}), sc_probe) is False, "다른 컷·고지 끔·맵 없음은 False")
check(pl.scene_ai_disclosure(pl.JobSpec(**base, ai_disclosure_map={vpath.upper(): True}), sc_probe) is (os.name == "nt"), "경로 대소문자 차이는 파일시스템 규칙(윈도우는 무시)대로 비교")
check(pl.scene_ai_disclosure(pl.JobSpec(**base, ai_disclosure_map={os.path.relpath(vpath): True}), sc_probe) is True, "상대경로로 저장돼 있어도 같은 파일이면 인식")
check(pl.scene_ai_disclosure(pl.JobSpec(**base, ai_disclosure_map={vpath: True}), {"image_path": photos[0], "extra_media": [{"source_path": vpath, "media": {"type": "video", "path": vpath}}]}) is True,
      "한 문장에 여러 컷이 묶인 씬은 추가 컷(extra_media)이 AI 영상이어도 고지")

spec = pl.JobSpec(**base, ai_disclosure_map={vpath: True})
scenes = pl.plan(spec, job)
import core.render.ffmpeg_assemble as fa
seen = {}
_asm = fa.assemble


def spy(comp, manifest, out_path, *a, **k):
    seen["flags"] = [bool(m.get("ai_disclosure")) for m in manifest]
    return _asm(comp, manifest, out_path, *a, **k)


fa.assemble = spy
try:
    rr = pl.render(spec, job, scenes)
finally:
    fa.assemble = _asm
check(seen.get("flags") == [False, True, False], f"렌더 매니페스트: AI 모션 씬(2번)만 고지 켜짐 {seen.get('flags')}")
check(os.path.exists(rr.final_path), "최종 MP4 생성")
dur = sum(float(x) * f for x, f in zip(re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", subprocess.run([FF, "-hide_banner", "-i", rr.final_path], capture_output=True, text=True).stderr).groups(), (3600, 60, 1)))
n = len(scenes)
frames = {}
for label, t in (("s1", dur * 0.5 / n), ("s2", dur * 1.5 / n), ("s3", dur * 2.5 / n)):
    out = os.path.join(W, f"{label}.png")
    subprocess.run([FF, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", rr.final_path, "-frames:v", "1", out], check=True)
    frames[label] = Image.open(out).convert("RGB")


def badge_pixels(im):
    """배지 자리(좌측 상단 5%·높이 4.5~8%)에서 '어두운 반투명 알약 + 흰 글자' 흔적: 그 영역을 같은 사진의 오른쪽 대칭 영역과 비교"""
    w, h = im.size
    left = im.crop((int(w * 0.05), int(h * 0.05), int(w * 0.20), int(h * 0.071)))
    right = im.crop((int(w * 0.80), int(h * 0.05), int(w * 0.95), int(h * 0.071)))
    lb, rb = sum(sum(p) for p in left.getdata()) / (left.size[0] * left.size[1]), sum(sum(p) for p in right.getdata()) / (right.size[0] * right.size[1])
    return abs(lb - rb)


check(badge_pixels(frames["s2"]) > 40 and badge_pixels(frames["s1"]) < badge_pixels(frames["s2"]) and badge_pixels(frames["s3"]) < badge_pixels(frames["s2"]),
      f"최종 MP4 프레임: AI 모션 씬에만 'AI 연출 포함' 배지가 찍힘 (차이 {badge_pixels(frames['s1']):.0f} / {badge_pixels(frames['s2']):.0f} / {badge_pixels(frames['s3']):.0f})")

# ---------------------------------------------------------------- 7. 배지 위치: 상호 배지·제휴 표시·상단 자막·훅 제목과 겹치지 않음
import numpy as np
from core.render.brand import BrandOverlay

comp = AdCompositor(output_dir=W)
Wd, Hd = 1080, 1920


def bbox(layer):
    a = np.asarray(layer.split()[-1])
    ys, xs = np.where(a > 8)
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else None


def overlap(a, b):
    return bool(a and b and not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]))


ai_top = comp.render_ai_disclosure(Wd, Hd)
ai_low = comp.render_ai_disclosure(Wd, Hd, y_frac=0.09)
check(ai_top.size == (Wd, Hd) and ai_top.mode == "RGBA" and bbox(ai_top), "배지: 1080×1920 투명 레이어에 알약 1개")
br = bbox(BrandOverlay(business_name="설맥 가평현리점", position="top_right").render_watermark(Wd, Hd, comp.font_path))
bl = bbox(BrandOverlay(business_name="설맥 가평현리점", position="top_left").render_watermark(Wd, Hd, comp.font_path))
check(not overlap(bbox(ai_top), br) and overlap(bbox(ai_top), bl) and not overlap(bbox(ai_low), bl), "상호 배지가 우측 상단이면 겹치지 않고, 좌측 상단이면 배지를 아래로 내려 겹침을 피함")
disc_box = bbox(comp.render_disclosure("제휴 링크 포함 · 구매 시 수수료를 받을 수 있어요", Wd, Hd))
check(not overlap(bbox(ai_top), disc_box) and not overlap(bbox(ai_low), disc_box), "제휴 표시 문구와 겹치지 않음")
for pos in ("mid", "top"):
    os.environ["OWNER_CAPTION_POS"] = pos
    cap = comp.create_overlay_image(caption_text="바삭한 설맥치킨 한 조각에 맥주가 시원해요!", hook_title="2차는 가평 설맥에서!", width=Wd, height=Hd, mode="owner")
    cb = bbox((Image.fromarray(cap) if not hasattr(cap, "split") else cap).convert("RGBA"))
    check(not overlap(bbox(ai_top), cb) and not overlap(bbox(ai_low), cb), f"자막 위치 '{pos}'에서 훅 제목·자막과 겹치지 않음 {cb}")
os.environ.pop("OWNER_CAPTION_POS", None)
# 조립 단계가 상호 배지 위치에 따라 높이를 고르는지 (소스 연결 확인)
src = open(os.path.join(ROOT, "core", "render", "ffmpeg_assemble.py"), encoding="utf-8").read()
check('getattr(brand, "position", "") == "top_left"' in src and "y_frac=0.09 if below_brand else 0.05" in src, "조립 단계: 상호 배지가 좌측 상단이면 AI 배지를 아래 높이(9%)로")

# ---------------------------------------------------------------- 8. 화면(app_store.py): 카드 노출·대상 범위(플레이스 체크 후보 포함)·교체·모드 제한
from streamlit.testing.v1 import AppTest

for _k in ("GEMINI_API_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
    os.environ[_k] = ""
os.environ["STORE_APP"] = "1"


def jpg_bytes(color=(120, 80, 60)):
    import io
    b = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(b, "JPEG")
    return b.getvalue()


class _Resp:
    def __init__(self, data):
        self.status_code, self.content, self.headers = 200, data, {"Content-Type": "image/jpeg"}


def at_store(mock_mode=True):
    at = AppTest.from_file(os.path.join(ROOT, "app_store.py"), default_timeout=180)
    at.session_state["mock_mode"] = mock_mode
    return at


at = at_store().run()
exp = [e for e in at.expander if "AI 모션 영상" in e.label]
check(not at.exception and exp, f"app_store.py(단독 작업실) 1단계에 AI 모션 변환 카드 노출 {[e.label for e in at.expander][:3]}")
check(any("변환할 사진이 없습니다" in i.value for i in at.info), "사진이 없으면 안내 (플레이스 사진을 체크하라는 안내 포함)")

cands = [{"title": f"설맥 가평현리점 {i}", "link": f"https://ldb-phinf.pstatic.net/x{i}.jpg", "thumbnail": f"https://ldb-phinf.pstatic.net/t{i}.jpg", "source": "official", "width": 900, "height": 900} for i in range(4)]
cands[3]["flag"] = "face"                                    # 사람 얼굴이 감지된 후보는 대상이 될 수 없음
at = at_store()
at.session_state["store_photo_candidates"] = cands
at.session_state["uploaded_paths"] = []
at.run()
sel = [s for s in at.selectbox if s.key == "ai_motion_target_idx"]
opts = list(sel[0].options) if sel else []
check(len(opts) == 3 and all("플레이스 사진" in o and "아직 영상 소스에 안 넣음" in o for o in opts), f"영상 소스에 넣기 전이라도 체크된 플레이스 후보가 변환 대상에 나옴 (얼굴 감지 후보 제외) {opts}")
at.session_state["webpick_1"] = False                         # 갤러리에서 체크를 해제하면 대상에서 빠짐
at.run()
sel = [s for s in at.selectbox if s.key == "ai_motion_target_idx"]
check(sel and len(sel[0].options) == 2 and not any("사진 2 " in o for o in sel[0].options), "갤러리에서 체크를 해제한 후보는 대상에서 빠짐")

# 후보 1장을 변환 → 영상 소스 맨 뒤에 MP4로 추가, 고지 맵·원본 기록
with mock.patch("requests.get", lambda url, **kw: _Resp(jpg_bytes())):
    at.session_state["ai_motion_target_idx"] = 0
    at.run()
    at.button(key="btn_generate_ai_motion").click().run()
ss = at.session_state
vids = [p for p in ss["uploaded_paths"] if p.endswith(".mp4")]
check(not at.exception and len(vids) == 1 and os.path.exists(vids[0]), f"체크된 플레이스 후보 → AI 모션 MP4가 영상 소스에 추가됨 {[os.path.basename(p) for p in ss['uploaded_paths']]} {[e.value[:80] for e in at.exception]}")
check(vids and vids[0] in ss["ai_disclosure_map"] and ss["ai_motion_backups"].get(vids[0]) and os.path.exists(ss["ai_motion_backups"][vids[0]]), "고지 맵(영상 경로)과 복구용 원본 사진이 기록됨")
spec_probe = pl.JobSpec(mode="owner", name="x", media_paths=list(ss["uploaded_paths"]), ai_disclosure_map=dict(ss["ai_disclosure_map"]))
check(pl.scene_ai_disclosure(spec_probe, {"media": {"type": "video", "path": vids[0]}}) is bool(ss["ai_disclosure_map"][vids[0]]), "화면이 저장한 고지 값을 파이프라인이 그 영상 씬에서 그대로 읽어 냄")

# 영상 소스의 사진 교체 + 이미 기획된 스토리보드 재배정 + 되돌리기
ph = [os.path.join(job.uploads_dir, f"p{i}.jpg") for i in range(3)]
at2 = at_store()
at2.session_state["uploaded_paths"] = list(ph)
at2.session_state["storyboard"] = [{"scene_number": 1, "scene_index": 1, "image_path": ph[0], "voiceover": "a"}, {"scene_number": 2, "scene_index": 2, "image_path": ph[1], "voiceover": "b"},
                                   {"scene_number": 3, "scene_index": 3, "image_path": ph[2], "voiceover": "c"}]
at2.run()
at2.session_state["ai_motion_target_idx"] = 1
at2.run()
at2.button(key="btn_generate_ai_motion").click().run()
s2 = at2.session_state
v2 = s2["uploaded_paths"][1]
check(not at2.exception and v2.endswith(".mp4") and s2["uploaded_paths"][0] == ph[0] and s2["uploaded_paths"][2] == ph[2], "목록의 2번 사진이 같은 자리에서 AI 영상으로 교체됨")
check(s2["storyboard"][1].get("media", {}).get("path") == v2 and "image_path" not in s2["storyboard"][1], "이미 기획된 스토리보드의 2번 씬도 새 영상으로 다시 배정됨 (_media_changed 경유)")
at2.button(key="btn_revert_ai_motion").click().run()
s2 = at2.session_state
check(s2["uploaded_paths"][1] == ph[1] and v2 not in s2["ai_disclosure_map"] and s2["storyboard"][1].get("image_path") == ph[1], "되돌리기: 원본 사진 복구 + 고지 맵 정리 + 스토리보드 재배정")

# 모임·행사·추억 모드에서는 카드를 숨김 (실제 인물 사진)
at3 = at_store()
at3.run()
at3.session_state["store_kind"] = "memory"
at3.run()
check(not any("AI 모션 영상" in e.label for e in at3.expander), "모임·행사·추억 모드에서는 AI 모션 카드를 숨김 (실제 인물 사진을 AI로 움직이지 않음)")

finish(fails)
