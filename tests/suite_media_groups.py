import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import shutil
import subprocess
import numpy as np
import imageio_ffmpeg
from PIL import Image

import core.pipeline as pl
import core.mode_policy as mp
from core.job import JobContext
from core.planners.owner import media_groups, plan_owner_storyboard

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


# ---------------------------------------------------------------- 1. 묶음 규칙: 5문장 : 6~8컷 (비대칭)
check(media_groups(8) == [[0, 1], [2], [3, 4], [5, 6], [7]] and media_groups(7) == [[0, 1], [2], [3, 4], [5], [6]]
      and media_groups(6) == [[0, 1], [2], [3], [4], [5]] and media_groups(5) == [[0], [1], [2], [3], [4]] and media_groups(3) == [[0], [1], [2]],
      "컷 묶음: 8컷=2·1·2·2·1 (5문장), 7·6컷도 5문장, 5컷 이하는 1컷=1문장")

media8 = [fixture("photo_pool.jpg" if i % 2 == 0 else "photo_room.jpg") for i in range(8)]
sc = plan_owner_storyboard("효정명인갈비", "숯불 양념 생갈비, 밑반찬 11가지", "food", "", media8, mock=True, tempo="rap", region="경기 가평군")
check(len(sc) == 8 and sc[-1]["role"] == "cta" and not any(len(s.get("media_group") or [0]) > 1 for s in sc),
      f"기획(매장 랩형): 사진 8장 → 문장 8개, 사진마다 자기 문장 1:1 (5문장으로 쪼개 붙이지 않음) ({len(sc)}씬)")
sc4 = plan_owner_storyboard("효정명인갈비", "", "food", "", media8[:4], mock=True)
check(len(sc4) == 4 and not any("media_group" in s for s in sc4), "4컷은 1컷 = 1문장 (묶음 없음)")

# ---------------------------------------------------------------- 2. 배정 + 실제 렌더 (mock 음성)
mp.INTRO_COVER_FORCE = False          # 0초 커버가 타임라인을 밀지 않게 (장면 시각 계산용)
W = tmp("media_groups_")
try:
    job = JobContext.create(app="media_groups")
    job.root = W
    job = JobContext(job.job_id, root=W)
    ups = []
    for i, p in enumerate(media8):
        dst = os.path.join(job.uploads_dir, f"u{i}.jpg")
        shutil.copy(p, dst)
        ups.append(dst)
    spec = pl.JobSpec(mode="owner", name="효정명인갈비", usp="숯불 양념 생갈비, 밑반찬 11가지", business_type="food", search_keyword="효정명인갈비",
                      address="경기 가평군 설악면", media_paths=ups, mock=True, core_concept="밑반찬 11가지 한 상", audio_mode="voice_and_sub", tempo="rap")
    scenes8 = pl.plan(spec, job)
    check(len(scenes8) == 8 and [s.get("image_path") for s in scenes8] == ups and not any(s.get("extra_media") for s in scenes8),
          "배정(매장): 씬 8개 = 업로드 사진 8장 1:1, 문장 중간 컷 전환 없음")
    # 한 문장에 컷 여러 장을 잇는 조립 기능(숙소 [1,2,2,1,1] 등이 쓰는 경로)은 그대로 동작해야 함: 앞 5씬을 묶음으로 다시 배정해 렌더
    scenes = scenes8[:5]
    for sc_i, grp in zip(scenes, [[0, 1], [2], [3, 4], [5, 6], [7]]):
        sc_i["media_group"] = grp
        sc_i["duration_sec"] = max(2.5, 1.35 * len(grp))         # 비트형: 컷 한 장당 1.2~1.5초
        sc_i.pop("extra_media", None)
    scenes[-1]["role"] = "cta"
    pl.assign_uploads_to_scenes(job, scenes, ups, cycle=False)
    ex0 = scenes[0].get("extra_media") or []
    check(len(scenes) == 5 and scenes[0].get("image_path") == ups[0] and len(ex0) == 1 and ex0[0]["image_path"] == ups[1]
          and len(scenes[2].get("extra_media") or []) == 1 and scenes[1].get("extra_media") in (None, []) and scenes[1].get("image_path") == ups[2] and scenes[2].get("image_path") == ups[3]
          and scenes[4].get("image_path") == ups[7],
          "배정(묶음 경로): 씬 1 = 사진 1(대표) + 사진 2(추가 컷), 씬 2 = 사진 3, 씬 3 = 사진 4·5, 씬 4 = 사진 6·7, 씬 5 = 사진 8")
    spec.tempo = "beat"            # 묶음(컷 여러 장) 경로는 비트형 타임라인으로 렌더
    res = pl.render(spec, job, scenes)
    final = res.final_path
    err = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", final], capture_output=True, text=True).stderr
    import re
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", err)
    total = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    ff = imageio_ffmpeg.get_ffmpeg_exe()

    def frame(t):
        raw = subprocess.run([ff, "-v", "error", "-ss", f"{t:.3f}", "-i", final, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)

    # 씬 1(컷 2장): 문장 앞쪽과 뒤쪽 화면이 다른 사진, 씬 2(컷 1장): 앞뒤가 같은 사진(모션만)
    d1 = float(np.abs(frame(0.4) - frame(2.3)).mean())
    d2 = float(np.abs(frame(3.9) - frame(5.3)).mean())
    check(d1 > 3 * max(d2, 3.0) and d1 > 25, f"렌더: 씬 1은 문장 중간에 다른 사진으로 전환 (앞뒤 차이 {d1:.1f}), 컷 1장인 씬 2는 같은 사진 (차이 {d2:.1f})")
    check(total > 10 and not [f for f in os.listdir(os.path.dirname(final)) if f.startswith("_ffmpeg")], f"최종 영상 생성 ({total:.1f}초), 임시 폴더 정리")
finally:
    mp.INTRO_COVER_FORCE = None
    shutil.rmtree(W, ignore_errors=True)

finish(fails)
