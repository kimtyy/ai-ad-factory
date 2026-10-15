"""
scripts/railway_seed.py - PC에만 있는 데이터를 서버(Railway)로 옮길 '초기 데이터 묶음' 만들기 (PC에서 한 번)

  python scripts/railway_seed.py        → outputs/railway_seed.zip

담는 것 (git에 안 올라가는 것들):
  secrets/*.json          유튜브·캘린더 OAuth 토큰, OAuth 클라이언트, 전용 캘린더 정보
  assets/bgm/*            자체 음원 → 서버용 m4a(AAC 256k)로 변환 (wav 그대로면 600MB 넘음, 곡 이름은 그대로)
  outputs/radar/radar.db  떡상 레이더 수집·트렌드 DB
  outputs/youtube/*.json  첫 댓글 대기열·초안
  outputs/scheduler/state.json  캘린더 일정 처리 기록 (같은 일정을 서버에서 다시 만들지 않게)
대시보드(서버) 사이드바 '🛠 서버 데이터 가져오기'에 이 zip을 올리면 볼륨(/data)에 풀림. 토큰이 들어 있으니 다른 곳에 공유하지 마세요.
"""
import os
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "outputs", "railway_seed.zip")
FILES = ["outputs/radar/radar.db", "outputs/youtube/pending_comments.json", "outputs/youtube/comment_drafts.json",
         "outputs/scheduler/state.json"]


def main() -> int:
    from core.render.bgm import BGM_EXTS
    from core.render.ffmpeg_assemble import ffmpeg_exe
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="seed_")
    n = {"secrets": 0, "bgm": 0, "data": 0}
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        sdir = os.path.join(ROOT, "secrets")
        for f in sorted(os.listdir(sdir)) if os.path.isdir(sdir) else []:
            if f.endswith(".json"):
                z.write(os.path.join(sdir, f), f"secrets/{f}")
                n["secrets"] += 1
        bdir = os.path.join(ROOT, "assets", "bgm")
        for f in sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []:
            src = os.path.join(bdir, f)
            if not (os.path.isfile(src) and f.lower().endswith(BGM_EXTS)):
                continue
            stem = os.path.splitext(f)[0]
            dst = os.path.join(tmp, stem + ".m4a")
            if f.lower().endswith((".m4a", ".mp3")):
                z.write(src, f"assets/bgm/{f}")
            else:
                subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", src, "-c:a", "aac", "-b:a", "256k", "-vn", dst],
                               check=True)
                z.write(dst, f"assets/bgm/{stem}.m4a")
            n["bgm"] += 1
        for rel in FILES:
            p = os.path.join(ROOT, rel)
            if os.path.exists(p):
                z.write(p, rel)
                n["data"] += 1
    size = os.path.getsize(OUT) / 1e6
    print(f"만들었어요: {OUT} ({size:.1f}MB) · 토큰·설정 {n['secrets']}개 · BGM {n['bgm']}곡(m4a) · 데이터 {n['data']}개")
    print("서버 대시보드 사이드바 '🛠 서버 데이터 가져오기'에 올려 주세요. (OAuth 토큰이 들어 있으니 공유 금지)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
