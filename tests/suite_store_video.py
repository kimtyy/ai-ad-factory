import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import re
import shutil
import subprocess

import imageio_ffmpeg

import core.media.normalize as mp
import core.pipeline as pl
from core.job import JobContext

fails = 0
FF = imageio_ffmpeg.get_ffmpeg_exe()


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


def make_video(path, seconds):
    subprocess.run([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=540x960:rate=24:duration={seconds}",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", path], check=True)
    return path


def duration(path):
    err = subprocess.run([FF, "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", err)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def new_job(prefix):
    W = tmp(prefix)
    job = JobContext.create(app=prefix)
    job.root = W
    return JobContext(job.job_id, root=W)


def spec_for(paths, **kw):
    return pl.JobSpec(mode="owner", name="효정명인갈비", usp="숯불 양념 생갈비", business_type="food", search_keyword="효정명인갈비",
                      address="경기 가평군 설악면", media_paths=paths, mock=True, audio_mode="voice_and_sub", **kw)


mp.INTRO_COVER_FORCE = False

# ---------------------------------------------------------------- 문장 수: 영상 길이에 맞춘 가변 (15초로 못박지 않음)
check([pl.single_video_sentences(x) for x in (10, 12.5, 15, 17, 18, 23, 24, 30, 45, 80)] == [4, 4, 4, 4, 5, 5, 5, 7, 8, 8],
      f"영상 길이별 문장 수: {[pl.single_video_sentences(x) for x in (10, 12.5, 15, 17, 18, 23, 24, 30, 45, 80)]}")

# ---------------------------------------------------------------- A. 단일 영상: 실제 길이 100% 보존
job = new_job("sv_a_")
v18 = make_video(_os.path.join(job.uploads_dir, "v18.mp4"), 18)
sc = pl.plan(spec_for([v18]), job)
trims = [s["media"]["trim_start"] for s in sc]
check(len(sc) == 5 and all(s["media"]["type"] == "video" and s["media"]["path"] == v18 for s in sc), f"18초 영상 1개 → 5문장 (사진 1장 버그 없음): {len(sc)}씬")
seg = 18 / 5
check(all(abs(t - seg * i) < 0.01 for i, t in enumerate(trims)) and abs(sc[-1]["media"]["trim_start"] + seg - 18) < 0.05,
      f"각 문장이 영상의 이어지는 구간(처음부터 끝까지, 15초 컷 없음): 시작 {trims}")
check(all(_os.path.exists(s["source_path"]) for s in sc) and sc[0]["single_video"]["length_sec"] == 18.0, "구간별 대표 프레임을 뽑아 판독·카드 미리보기에 사용")
check(all(s["voiceover"] for s in sc) and sc[-1]["role"] == "cta", f"문장마다 1줄 카피 + 마지막은 CTA: {[s['voiceover'][:10] for s in sc]}")
res = pl.render(spec_for([v18]), job, sc)
total = duration(res.final_path)
check(17.6 <= total <= 20.5, f"렌더 결과 길이 ≥ 업로드 영상 길이 (영상이 잘리지 않음, 끝 카드·마지막 여운 약 1~2초 포함): 18.0초 → {total:.1f}초")

job = new_job("sv_b_")
v125 = make_video(_os.path.join(job.uploads_dir, "v125.mp4"), 12.5)
sc125 = pl.plan(spec_for([v125]), job)
check(len(sc125) == 4 and abs(sum(s["min_sec"] for s in sc125) - 12.5) < 0.6, f"12.5초 영상 → 4문장, 구간 합 ≈ 12.5초 ({sum(s['min_sec'] for s in sc125):.2f})")

# ---------------------------------------------------------------- B. 사진 + 짧은 영상 혼합: 1에셋 = 1컷
job = new_job("sv_c_")
ph = [_os.path.join(job.uploads_dir, f"p{i}.jpg") for i in range(3)]
for i, p in enumerate(ph):
    shutil.copy(fixture("photo_pool.jpg" if i % 2 == 0 else "photo_room.jpg"), p)
v4a = make_video(_os.path.join(job.uploads_dir, "c1.mp4"), 4)
v4b = make_video(_os.path.join(job.uploads_dir, "c2.mp4"), 3)
order = [ph[0], v4a, ph[1], v4b, ph[2]]
scm = pl.plan(spec_for(order), job)
kinds = ["video" if (s.get("media") or {}).get("type") == "video" else "photo" for s in scm]
check(len(scm) == 5 and kinds == ["photo", "video", "photo", "video", "photo"], f"업로드 순서 그대로 1에셋 = 1컷: {kinds}")
mins = [s.get("min_sec") for s in scm]
check(mins[1] == 4.0 and mins[3] == 3.0 and mins[0] is None and all(scm[i]["media"]["trim_start"] == 0.0 for i in (1, 3)), f"영상 컷은 실제 시간만큼(4초·3초) 처음부터 재생, 사진 컷은 기본 길이: {mins}")
resm = pl.render(spec_for(order), job, scm)
tm = duration(resm.final_path)
check(8.0 <= tm <= 19.0 and tm >= 4 + 3 + 2 * 3 - 2.5, f"렌더 길이: 사진 3컷(약 2~3초) + 영상 4초·3초 ≈ {tm:.1f}초")

mp.INTRO_COVER_FORCE = None
finish(fails)
