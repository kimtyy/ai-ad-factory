"""
tests/_support.py - 회귀 테스트 공용 도우미

- ROOT: 레포 루트 (어느 위치에서 실행해도 동일)
- fixture(name): 테스트 미디어를 필요할 때 생성해 tests/_fixtures/에 캐시 (git에 바이너리·저작권 사진을 올리지 않음)
    photo_pool.jpg / photo_room.jpg : 합성 이미지 (100KB+)
    phone_portrait.mov : HEVC, 회전 메타데이터(90°), 가변 프레임(VFR), 오디오 포함 (아이폰 세로 촬영 모사)
    phone_landscape.mp4 : 가로 2초 (슬로모션/반복 재생 경로)
    phone_hdr.mov : HLG 10bit HDR (톤매핑 경로)
    final.mp4 : 1080x1920 완성 영상 자리표시자 (렌더 완료 화면 테스트용)
- LIVE: RUN_LIVE_TESTS=1일 때만 네트워크·Gemini 실호출 구간 실행 (run_all.py --live)
"""
import io
import logging
import os
import random
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "_fixtures")
LIVE = os.getenv("RUN_LIVE_TESTS") == "1"

os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def tmp(prefix: str = "adf_test_") -> str:
    return tempfile.mkdtemp(prefix=prefix)


def live_skip(what: str) -> bool:
    """LIVE가 아니면 SKIP 줄을 출력하고 True 반환 (해당 구간 건너뛰기)"""
    if not LIVE:
        print(f"SKIP {what} (실호출 구간: python tests/run_all.py --live)")
        return True
    return False


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args):
    res = subprocess.run([_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.decode("utf-8", "ignore")[-400:])


def _synthetic_photo(path: str, seed: int, top, bottom):
    from PIL import Image, ImageDraw
    rnd = random.Random(seed)
    w, h = 1200, 900
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / h
        base = [int(top[c] * (1 - t) + bottom[c] * t) for c in range(3)]
        for x in range(w):
            n = rnd.randint(-18, 18)   # 노이즈: JPEG 용량 100KB+ 확보 (매칭 엔진의 최소 용량 검증 통과용)
            px[x, y] = tuple(max(0, min(255, v + n)) for v in base)
    d = ImageDraw.Draw(img)
    for _ in range(6):
        x0, y0 = rnd.randint(0, w - 200), rnd.randint(0, h - 150)
        d.rectangle([x0, y0, x0 + rnd.randint(80, 200), y0 + rnd.randint(60, 150)], outline=(255, 255, 255), width=4)
    img.save(path, "JPEG", quality=92)


def _build(name: str, path: str):
    if name == "photo_pool.jpg":
        _synthetic_photo(path, 1, (120, 190, 235), (20, 120, 200))
    elif name == "photo_room.jpg":
        _synthetic_photo(path, 2, (235, 228, 215), (150, 120, 95))
    elif name == "phone_portrait.mov":
        raw = path + ".raw.mov"
        _run(["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "4",
              "-vf", "select='not(mod(n\\,3))+gt(n\\,60)'", "-fps_mode", "vfr", "-c:v", "libx265", "-tag:v", "hvc1",
              "-x265-params", "log-level=error", "-c:a", "aac", raw])
        _run(["-display_rotation:v:0", "90", "-i", raw, "-c", "copy", path])
        os.remove(raw)
    elif name == "phone_landscape.mp4":
        _run(["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30", "-t", "2", "-c:v", "libx264", path])
    elif name == "phone_hdr.mov":
        _run(["-f", "lavfi", "-i", "smptehdbars=size=1080x1920:rate=30", "-t", "3",
              "-vf", "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc",
              "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
              "-x265-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:log-level=error",
              "-tag:v", "hvc1", path])
    elif name == "final.mp4":
        _run(["-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=24", "-f", "lavfi", "-i", "sine=frequency=330",
              "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path])
    else:
        raise KeyError(f"알 수 없는 픽스처: {name}")


# 테스트는 가짜 Gemini로 대본을 검증하므로 실제 Claude·OpenAI 키는 비움 (.env의 load_dotenv는 이미 있는 값을 덮지 않음)
if "--live" not in sys.argv:
    os.environ["ANTHROPIC_API_KEY"] = ""
    os.environ["OPENAI_API_KEY"] = ""


def fixture(name: str) -> str:
    """픽스처 경로 (없으면 생성)"""
    os.makedirs(FIXTURES, exist_ok=True)
    path = os.path.join(FIXTURES, name)
    if not os.path.exists(path):
        _build(name, path)
    return path


def finish(fails: int):
    """마지막 요약 줄 + 종료 코드 (CI에서 실패 감지)"""
    print("FAILURES:", fails)
    sys.exit(1 if fails else 0)
