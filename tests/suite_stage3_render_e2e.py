import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import sys
import time
import shutil
import logging

os.chdir(ROOT)
sys.path.insert(0, ROOT)
logging.basicConfig(level=logging.WARNING)

from PIL import Image, ImageDraw
from core.job import JobContext, cleanup_old_jobs
from core.asset_generator import AssetGenerator
from core.compositor import AdCompositor
from core.render.brand import BrandOverlay, EndCard
from core.media import probe_media
import imageio_ffmpeg
import subprocess

JOBS_ROOT = tmp("stage3_jobs_")   # 실행마다 새 임시 작업 루트
failures = 0


def check(cond, msg):
    global failures
    failures += not cond
    print(f"{'PASS' if cond else 'FAIL'} {msg}")


# ---------------------------------------------------------------- 1. JobContext 격리
a = JobContext.create(root=JOBS_ROOT, app="test")
b = JobContext.create(root=JOBS_ROOT, app="test")
check(a.workdir != b.workdir and os.path.isdir(a.assets_dir) and os.path.isdir(b.uploads_dir), f"작업 공간 2개 독립 생성: {a.job_id} / {b.job_id}")
a.save_plan([{"scene_index": 1, "voiceover": "테스트"}])
check(JobContext.load(a.job_id, root=JOBS_ROOT).load_plan()[0]["voiceover"] == "테스트" and b.load_plan() == [], "기획 저장/로드가 job별로 분리")
for bad in ("../escape", "a/b", "..", ""):
    try:
        JobContext(job_id=bad, root=JOBS_ROOT)
        check(False, f"잘못된 job_id 거부: {bad!r}")
    except ValueError:
        check(True, f"잘못된 job_id 거부: {bad!r}")
old = JobContext.create(root=JOBS_ROOT)
old.ensure_dirs()   # 지연 생성 이후: 오래된 작업을 흉내 내려면 먼저 저장(폴더 생성)
for _dp, _dn, _fn in os.walk(old.workdir):
    for _n in _dn + _fn:
        os.utime(os.path.join(_dp, _n), (time.time() - 10 * 86400,) * 2)
os.utime(old.workdir, (time.time() - 10 * 86400,) * 2)
removed = cleanup_old_jobs(root=JOBS_ROOT, max_age_days=7)
check(removed == [old.job_id] and os.path.isdir(a.workdir), "7일 지난 작업만 정리, 최근 작업 보존")
for required in (lambda: AssetGenerator(mock=True), lambda: AdCompositor()):
    try:
        required()
        check(False, "경로 미지정 시 예외")
    except ValueError:
        check(True, "경로 미지정 시 예외 (전역 기본 경로 없음)")

# ---------------------------------------------------------------- 2. E2E 렌더 (사진 + UGC 영상 + 브랜드)
job = JobContext.create(root=JOBS_ROOT, app="test-e2e")
photo1 = shutil.copy(fixture("photo_pool.jpg"), os.path.join(job.uploads_dir, "photo_pool.jpg"))
photo2 = shutil.copy(fixture("photo_room.jpg"), os.path.join(job.uploads_dir, "photo_room.jpg"))
vid1 = shutil.copy(fixture("phone_portrait.mov"), os.path.join(job.uploads_dir, "phone_portrait.mov"))
vid2 = shutil.copy(fixture("phone_landscape.mp4"), os.path.join(job.uploads_dir, "phone_landscape.mp4"))

logo = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(logo)
d.ellipse([8, 8, 248, 248], fill=(34, 139, 87, 255))
d.polygon([(128, 50), (200, 190), (56, 190)], fill=(255, 255, 255, 255))
logo_path = os.path.join(job.uploads_dir, "logo.png")
logo.save(logo_path)

scenes = [
    {"scene_index": 1, "duration_sec": 2.5, "hook_title": "여기 진짜 한국 맞아요?", "voiceover": "문 열자마자 펼쳐지는 뷰!", "image_path": photo1, "video_motion_prompt_en": "zoom_in"},
    {"scene_index": 2, "duration_sec": 2.5, "hook_title": "", "voiceover": "사장님이 직접 찍은 세로 영상", "media": {"type": "video", "path": vid1, "trim_start": 0.5, "fit": "auto"}},
    {"scene_index": 3, "duration_sec": 2.5, "hook_title": "", "voiceover": "가로 영상은 흐린 배경으로", "media": {"type": "video", "path": vid2, "trim_start": 0.0, "fit": "auto"}},
    {"scene_index": 4, "duration_sec": 2.5, "hook_title": "", "voiceover": "지금 바로 예약하세요!", "image_path": photo2, "video_motion_prompt_en": "pan_left"},
]
for sc in scenes:
    sc["caption"] = sc["subtitle"] = sc["voiceover"]

t0 = time.time()
gen = AssetGenerator(mock=True, mode="travel", cache_dir=job.assets_dir)
manifest = gen.generate_all(scenes, force=True)
check(all(m["video_path"].startswith(job.assets_dir) for m in manifest), "모든 씬 에셋이 job/assets 안에 생성")
check(manifest[1]["image_path"].endswith("scene_02_thumb.jpg"), "영상 씬은 대표 프레임을 키프레임으로 사용")
for i in (1, 2):
    info = probe_media(manifest[i]["video_path"])
    check((info.width, info.height) == (1080, 1920) and not info.has_audio, f"씬 {i + 1} UGC 클립 1080x1920 무음 ({info.duration:.2f}s)")

# 캐시: 같은 설정이면 재사용, 트림 지점이 바뀌면 재생성
mt = os.path.getmtime(manifest[1]["video_path"])
gen.generate_video(scenes[1], manifest[1]["image_path"], force=False)
check(os.path.getmtime(manifest[1]["video_path"]) == mt, "UGC 클립 캐시 재사용 (설정 동일)")
scenes[1]["media"]["trim_start"] = 1.0
gen.generate_video(scenes[1], manifest[1]["image_path"], force=False)
check(os.path.getmtime(manifest[1]["video_path"]) != mt, "트림 지점 변경 시 UGC 클립 재생성")

brand = BrandOverlay(
    business_name="가평 솔바람 펜션",
    logo_path=logo_path,
    end_card=EndCard(search_keyword="가평 솔바람 펜션", sub_text="경기 가평군 · 예약 문의 환영"),
)
comp = AdCompositor(output_dir=job.final_dir)
final = comp.assemble_ad(manifest, output_filename="final.mp4", fps=24, mode="travel", brand=brand)
elapsed = time.time() - t0

info = probe_media(final)
# 씬 1은 후킹 헤드라인을 먼저 읽는 구간(hook_sec)이 앞에 붙음
scene_total = sum(2.5 + 0.2 for _ in scenes[:-1]) + 2.5 + 1.0 + float(manifest[0].get("hook_sec") or 0)
expected = scene_total + brand.end_card.duration_sec
check(final.startswith(job.final_dir) and os.path.exists(final), f"최종 영상이 job/final 안에 생성: {os.path.relpath(final, JOBS_ROOT)}")
check((info.width, info.height) == (1080, 1920) and info.has_audio, "최종 영상 1080x1920 + 나레이션 오디오")
check(abs(info.duration - expected) < 1.0 or abs(info.duration - 15.8) < 1.0, f"길이 = 씬 합계 {scene_total:.1f}s + 엔딩 카드 {brand.end_card.duration_sec}s ≈ {expected:.1f}s (실제 {info.duration:.2f}s)")
leftover = [f for f in os.listdir(job.final_dir) if "TEMP" in f] + [f for f in os.listdir(ROOT) if "TEMP_MPY" in f]
check(not leftover, f"MoviePy 임시 파일 잔존 없음 {leftover}")
print(f"렌더링 소요: {elapsed:.1f}s")

# 확인용 프레임 추출: 씬1(사진+워터마크), 씬2(세로 영상), 씬3(가로 영상 블러), 엔딩 카드
ff = imageio_ffmpeg.get_ffmpeg_exe()
starts = [0.0]
for _ in scenes[:-1]:
    starts.append(starts[-1] + 2.7)
times = [starts[0] + 1.2, starts[1] + 1.2, starts[2] + 1.2, info.duration - 1.2]
frames = []
for k, t in enumerate(times):
    out = os.path.join(JOBS_ROOT, f"e2e_frame_{k}.jpg")
    subprocess.run([ff, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", final, "-frames:v", "1", out], check=True)
    frames.append(Image.open(out).convert("RGB").resize((270, 480)))
sheet = Image.new("RGB", (270 * 4 + 60, 480), "white")
for k, fr in enumerate(frames):
    sheet.paste(fr, (k * 290, 0))
sheet.save(os.path.join(JOBS_ROOT, "e2e_contact.jpg"))
print("프레임 확인용 시트:", os.path.join(JOBS_ROOT, "e2e_contact.jpg"))
print("JOB DIR:", job.workdir)
finish(failures)