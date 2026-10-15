"""Railway 배포: 컨테이너 스케줄러(캘린더 10분·레이더 07:00) · 서버 데이터 가져오기(zip 안전 풀기) · 리눅스 호환 · 배포 설정 파일"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import zipfile

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


work = tmp()

# ---------------------------------------------------------------- 1. 컨테이너 스케줄러
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("rs", os.path.join(ROOT, "scripts", "railway_scheduler.py"))
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)
rs.STATE = os.path.join(work, "sched.json")
KST = rs.KST
ran = []
fake = lambda cmd, timeout: (ran.append(" ".join(cmd[1:3])), 0)[1]  # noqa: E731
t0 = dt.datetime(2026, 10, 13, 6, 50, tzinfo=KST).astimezone(dt.timezone.utc)
st = rs.tick(t0, {}, runner=fake)
check(ran == ["scripts/calendar_runner.py"], f"첫 주기: 캘린더 실행, 07:00 전이라 레이더는 아직 {ran}")
ran.clear()
st = rs.tick(t0 + dt.timedelta(minutes=5), st, runner=fake)
check(ran == [], "캘린더는 10분 간격 (5분 뒤엔 안 돎)")
st = rs.tick(t0 + dt.timedelta(minutes=10), st, runner=fake)
check(ran[0] == "scripts/calendar_runner.py" and "-m core.radar.run" in ran[1] and "-m core.radar.trends" in ran[2]
      and "scripts/post_pending_comments.py" in ran[3], f"07:00 → 캘린더 + 레이더 수집·트렌드·대기 첫 댓글 {ran}")
ran.clear()
st = rs.tick(t0 + dt.timedelta(hours=3), st, runner=fake)
check(ran == ["scripts/calendar_runner.py"] and json.load(open(rs.STATE))["radar_day"] == "2026-10-13",
      "레이더는 하루 한 번 (상태 파일에 날짜 기록)")
late = rs.radar_due(dt.datetime(2026, 10, 14, 15, 0, tzinfo=KST), st)
check(late, "07시에 서버가 꺼져 있었으면 켜진 뒤 그날 한 번 돈다")
os.environ["SCHED_CALENDAR"] = "0"
ran.clear()
rs.tick(t0 + dt.timedelta(hours=4), st, runner=fake)
os.environ.pop("SCHED_CALENDAR")
check(ran == [], "SCHED_CALENDAR=0 이면 캘린더 끔")

# ---------------------------------------------------------------- 2. 서버 데이터 가져오기 (zip)
from ui import server_data as sd  # noqa: E402
b = io.BytesIO()
with zipfile.ZipFile(b, "w") as z:
    z.writestr("secrets/token_store.json", '{"token": "x"}')
    z.writestr("assets/bgm/Neon Skyline_5-2.m4a", b"audio")
    z.writestr("outputs/radar/radar.db", b"db")
    z.writestr("../evil.py", "x")
    z.writestr("app.py", "overwrite")
    z.writestr("secrets/../../escape.txt", "x")
res = sd.extract_seed(b.getvalue(), root=work)
check(sorted(res["saved"]) == ["assets/bgm/Neon Skyline_5-2.m4a", "outputs/radar/radar.db", "secrets/token_store.json"]
      and len(res["skipped"]) == 3 and not os.path.exists(os.path.join(work, "app.py"))
      and not os.path.exists(os.path.join(os.path.dirname(work), "evil.py")),
      "zip은 허용 폴더(secrets·assets/bgm·outputs 일부)에만, ../ 탈출·코드 덮어쓰기는 건너뜀")
check(sd.inventory(work) == {"토큰·설정": 1, "BGM": 1, "레이더 DB": 1}, "서버 데이터 현황 표시")
_env = {k: os.environ.get(k) for k in ("DATA_DIR", "RAILWAY_ENVIRONMENT")}
for k in _env:
    os.environ.pop(k, None)
local_hidden = not sd.on_server()
os.environ["RAILWAY_ENVIRONMENT"] = "production"
check(local_hidden and sd.on_server(), "가져오기 메뉴는 서버(Railway)에서만")
for k, v in _env.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v

# ---------------------------------------------------------------- 3. 리눅스 호환
import core.scheduler as sch  # noqa: E402
_run, _name = sch.subprocess.run, sch.os.name
sch.os.name = "posix"
sch.subprocess.run = lambda cmd, **k: type("R", (), {"stdout": "COMMAND\npython\nffmpeg\n" if cmd[0] == "ps" else ""})()
busy = sch.render_busy()
sch.subprocess.run = lambda cmd, **k: type("R", (), {"stdout": "COMMAND\npython\nstreamlit\n"})()
idle = sch.render_busy()
sch.subprocess.run, sch.os.name = _run, _name
check(busy and not idle, "렌더 중 확인: 리눅스는 ps로 ffmpeg 찾기 (Windows는 tasklist)")
from core.render import bgm  # noqa: E402
bdir = os.path.join(work, "bgm")
os.makedirs(bdir)
open(os.path.join(bdir, "Neon Skyline_5-2.m4a"), "wb").write(b"x")
os.environ["BGM_BOOK_TRACK"] = "Neon Skyline_5-2.wav"
check(bgm.find_track("Neon Skyline_5-2.wav", bdir).endswith(".m4a") and bgm.pick_track("Neon Skyline_5-2.wav", bdir)
      and bgm.book_default_track(bdir) == "Neon Skyline_5-2.m4a",
      "서버는 BGM을 m4a로 → .env의 .wav 곡 이름 그대로 찾음 (확장자 무시)")
os.environ.pop("BGM_BOOK_TRACK")

# ---------------------------------------------------------------- 4. 배포 설정 파일
df = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
rj = json.load(open(os.path.join(ROOT, "railway.json"), encoding="utf-8"))
di = open(os.path.join(ROOT, ".dockerignore"), encoding="utf-8").read()
sh = open(os.path.join(ROOT, "scripts", "railway_start.sh"), "rb").read()
check("ffmpeg" in df and "fonts-noto-color-emoji" in df and "fonts-noto-cjk" in df and "TZ=Asia/Seoul" in df
      and "RENDER_ENCODER=libx264" in df, "Dockerfile: ffmpeg·한글/컬러 이모지 글꼴·서울 시간대·소프트웨어 인코더")
check(rj["build"]["builder"] == "DOCKERFILE" and rj["deploy"]["healthcheckPath"] == "/_stcore/health"
      and rj["deploy"]["numReplicas"] == 1, "railway.json: Dockerfile 빌드·헬스체크·인스턴스 1개(볼륨)")
check(all(x in di for x in (".env", "secrets/", "outputs/", "assets/bgm/*")), ".dockerignore: 비밀·대용량은 이미지에 안 넣음")
check(b"\r\n" not in sh and b'--server.port "${PORT:-8501}"' in sh and b"--server.address 0.0.0.0" in sh
      and b'ln -sfn "$DATA/$d"' in sh, "시작 스크립트: LF 줄바꿈·Railway PORT·0.0.0.0·볼륨 연결")
bash = shutil.which("bash") or shutil.which("sh")
if bash:
    rc = subprocess.run([bash, "-n", os.path.join(ROOT, "scripts", "railway_start.sh")]).returncode
    check(rc == 0, "시작 스크립트 셸 문법 확인")
ga = open(os.path.join(ROOT, ".gitattributes"), encoding="utf-8").read()
check("*.sh text eol=lf" in ga, ".gitattributes: 셸 스크립트는 항상 LF (Windows에서 커밋해도 리눅스에서 실행)")
finish(fails)
