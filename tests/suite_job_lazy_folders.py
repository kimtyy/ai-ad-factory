"""작업 폴더 지연 생성 + 빈 폴더 자동 정리 테스트 (단위 + Streamlit AppTest)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import shutil
import sys
import tempfile
import time

os.chdir(ROOT)
sys.path.insert(0, ROOT)
import logging
logging.disable(logging.WARNING)
from core.job import JobContext, cleanup_old_jobs, is_empty_job

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


# ---------------------------------------------------------------- 1. 지연 생성
tmp = tempfile.mkdtemp()
job = JobContext.create(root=tmp, app="test")
check(not os.path.exists(job.workdir) and not job.exists, "create(): ID만 발급, 디스크에 폴더 없음")
_ = job.job_id, job.workdir, job.plan_path, job.meta_path, job.load_plan()
check(not os.path.exists(job.workdir), "ID/경로 조회·빈 기획 로드만으로는 폴더 생성 안 됨")
a = job.assets_dir
check(os.path.isdir(a) and os.path.exists(job.meta_path) and not os.path.exists(os.path.join(job.workdir, "uploads")),
      "assets_dir 첫 요청 시 workdir + assets/ + job.json만 생성 (필요한 하위 폴더만)")
job2 = JobContext.create(root=tmp)
job2.save_plan([{"scene_index": 1}])
check(os.path.exists(job2.plan_path) and os.path.exists(job2.meta_path), "save_plan() 첫 저장 시 폴더 + job.json 생성")
check(JobContext.load(job2.job_id, root=tmp).load_plan() == [{"scene_index": 1}], "load(): 기존 작업 이어서 열기")
try:
    JobContext.load(JobContext.create(root=tmp).job_id, root=tmp)
    check(False, "저장 전 작업 load 거부")
except FileNotFoundError:
    check(True, "아직 저장된 적 없는 작업은 load 불가 (존재하지 않음)")

# ---------------------------------------------------------------- 2. 정리 (빈 폴더만 / 나이 기준)
root = tempfile.mkdtemp()


def make(job_id, hours_old, files=()):
    j = JobContext(job_id=job_id, root=root)
    j.assets_dir   # 폴더 + job.json 생성
    for f in files:
        p = os.path.join(j.workdir, f)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write("x")
    t = time.time() - hours_old * 3600
    for dp, dn, fn in os.walk(j.workdir):
        for n in dn + fn:
            os.utime(os.path.join(dp, n), (t, t))
    os.utime(j.workdir, (t, t))
    return j


make("old-empty", 30)
make("recent-empty", 2)
make("old-full", 30, ["assets/scene_01.jpg", "storyboard.json"])
make("keep-me", 30)
os.makedirs(os.path.join(root, "not a job!"))
check(is_empty_job(os.path.join(root, "old-empty")) and not is_empty_job(os.path.join(root, "old-full")), "빈 작업 판정 (job.json·빈 하위 폴더만 = 빈 작업)")
removed = cleanup_old_jobs(root=root, empty_only=True, max_age_hours=24, keep=["keep-me"])
left = sorted(os.listdir(root))
check(removed == ["old-empty"] and left == ["keep-me", "not a job!", "old-full", "recent-empty"],
      f"24시간+ 빈 폴더만 삭제, 최근 빈 폴더·결과물 있는 폴더·keep·비작업 폴더 보존 → 삭제 {removed}")
# 오래된 폴더라도 안쪽 파일이 최근 수정됐으면 보존 (전체 모드)
j = make("old-dir-new-file", 30, ["final/final.mp4"])
os.utime(os.path.join(j.workdir, "final", "final.mp4"), None)
removed2 = cleanup_old_jobs(root=root, max_age_hours=24, keep=["keep-me"])
check("old-dir-new-file" not in removed2 and "old-full" in removed2, f"전체 모드: 안쪽 최근 수정 파일 기준으로 나이 판단 → 삭제 {removed2}")

# ---------------------------------------------------------------- 3. Streamlit: 접속만으로 폴더 생성 안 됨 + 시작 시 정리
from streamlit.testing.v1 import AppTest

jobs_root = os.path.join(ROOT, "outputs", "jobs")
os.makedirs(jobs_root, exist_ok=True)
before = set(os.listdir(jobs_root))
stale = JobContext(job_id="20200101-000000-deadbeef", root=jobs_root)
stale.assets_dir
t = time.time() - 48 * 3600
for dp, dn, fn in os.walk(stale.workdir):
    for n in dn + fn:
        os.utime(os.path.join(dp, n), (t, t))
os.utime(stale.workdir, (t, t))
created = []
try:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=300).run()
    check(not at.exception, "앱 실행 정상")
    check(not os.path.exists(stale.workdir), "앱 시작 시 48시간 지난 빈 작업 폴더 자동 정리")
    check(set(os.listdir(jobs_root)) == before, f"접속 + 화면 렌더만으로는 새 작업 폴더 생성 안 됨 (기존 {len(before)}개 그대로)")
    at.session_state.page_switch = "radar"
    at.run()
    at.session_state.page_switch = "make"
    at.run()
    check(set(os.listdir(jobs_root)) == before, "레이더 화면 왕복해도 폴더 생성 안 됨")
    job = at.session_state.job
    at.text_input(key="owner_name").input("가평 솔바람 펜션").run()
    at.session_state["mock_mode"] = True; at.run()   # Mock
    [b for b in at.button if "대본 만들기" in b.label][0].click().run()
    created.append(job.workdir)
    check(os.path.exists(job.plan_path) and os.path.exists(job.meta_path), "첫 저장(콘티 기획) 시점에 작업 폴더 + storyboard.json + job.json 생성")
    check(set(os.listdir(jobs_root)) - before == {job.job_id}, "새로 생긴 폴더는 실제로 저장한 세션의 1개뿐")
finally:
    for p in created:
        shutil.rmtree(p, ignore_errors=True)
    shutil.rmtree(stale.workdir, ignore_errors=True)
check(set(os.listdir(jobs_root)) == before, "테스트 후 원래 상태 복원 (사용자 기존 폴더 보존)")
finish(fails)