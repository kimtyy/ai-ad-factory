"""썸네일 자동 생성(후킹 오버레이·영상 첫 0.3초) + 유튜브 쇼츠 업로드(가짜 API: 업로드·썸네일·첫 댓글·채널 확인·실패 처리)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import fixture, tmp, finish  # noqa: E402
import os
import subprocess
import logging
logging.disable(logging.WARNING)

import httplib2
import imageio_ffmpeg
import numpy as np
from googleapiclient.errors import HttpError
from PIL import Image

import core.youtube_upload as yu
from core.compositor import INTRO_SEC, AdCompositor
from ui.youtube_panel import default_texts

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


work = tmp("yt_")
comp = AdCompositor(output_dir=work)

# ---------------------------------------------------------------- 1. 썸네일
frame = np.array(Image.open(fixture("photo_pool.jpg")).convert("RGB"))
thumb = comp.render_thumbnail(frame, "여기 진짜 한국 맞아요?", os.path.join(work, "final_thumb.png"))
ti = np.array(Image.open(thumb).convert("RGB")).astype(int)
yellow = lambda a: int(((a[..., 0] > 200) & (a[..., 1] > 170) & (a[..., 2] < 90)).sum())
check(ti.shape == (1920, 1080, 3) and yellow(ti[:int(1920 * 0.4)]) > 5000 and yellow(ti[int(1920 * 0.5):]) < 500,
      f"썸네일 1080x1920 PNG: 상단에 노란 후킹 타이틀 ({yellow(ti[:768])}px), 아래는 사진")
bare = comp.render_thumbnail(frame, "", os.path.join(work, "bare.png"))
check(yellow(np.array(Image.open(bare).convert("RGB")).astype(int)) < 500, "후킹 문구가 없으면 사진만 (지어낸 문구 없음)")
long_t = comp.render_thumbnail(frame, "솔직히 이 가격에 오션뷰 인피니티 풀까지 있는 숙소는 처음 봤어요 진짜로요", os.path.join(work, "long.png"))
check(os.path.exists(long_t), "긴 후킹 문구도 3줄 안으로 글자 축소")

# 2MB 넘는 PNG → 업로드용 JPEG 사본
noisy = os.path.join(work, "noisy_thumb.png")
Image.fromarray(np.random.default_rng(0).integers(0, 255, (1920, 1080, 3), dtype=np.uint8)).save(noisy)
up = yu.thumbnail_for_upload(noisy)
small = os.path.join(work, "small_thumb.png")
Image.fromarray(np.zeros((1920, 1080, 3), dtype=np.uint8)).save(small)
check(os.path.getsize(noisy) > yu.THUMB_MAX_BYTES and up.endswith("_upload.jpg") and os.path.getsize(up) <= yu.THUMB_MAX_BYTES
      and Image.open(up).size == (1080, 1920) and yu.thumbnail_for_upload(small) == small,
      "썸네일 2MB 한도: 큰 PNG는 같은 크기 JPEG 사본으로 등록 (원본 PNG 유지)")

# ---------------------------------------------------------------- 2. 영상 첫 0.3초 = 썸네일
ff = imageio_ffmpeg.get_ffmpeg_exe()
vid = os.path.join(work, "s1.mp4")
aud = os.path.join(work, "s1.m4a")
subprocess.run([ff, "-y", "-loglevel", "error", "-loop", "1", "-i", fixture("photo_room.jpg"), "-t", "2", "-r", "24",
                "-vf", "scale=1080:1920,setsar=1", "-pix_fmt", "yuv420p", vid], check=True)
subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", "-c:a", "aac", aud], check=True)
man = [{"scene_index": 1, "video_path": vid, "audio_path": aud, "caption": "자막"}]
plain = AdCompositor(output_dir=os.path.join(work, "a")).assemble_ad(man, output_filename="plain.mp4", mode="travel")
intro = AdCompositor(output_dir=os.path.join(work, "b")).assemble_ad(man, output_filename="intro.mp4", mode="travel", intro_image=thumb)
from moviepy.editor import VideoFileClip
with VideoFileClip(intro) as ci, VideoFileClip(plain) as cp:
    f0 = ci.get_frame(0.05).astype(int)
    f_late = ci.get_frame(INTRO_SEC + 0.2).astype(int)
    p_late = cp.get_frame(INTRO_SEC + 0.2).astype(int)
    d_intro, d_plain = ci.duration, cp.duration
    a_ok = ci.audio is not None and abs(ci.audio.duration - cp.audio.duration) < 0.1
check(np.abs(f0 - ti).mean() < 12 and yellow(f0[:768]) > 3000, f"영상 첫 프레임 = 썸네일 (차이 {np.abs(f0 - ti).mean():.1f})")
check(np.abs(f_late - p_late).mean() < 6 and abs(d_intro - d_plain) < 0.05 and a_ok,
      f"0.3초 뒤에는 원래 화면, 길이·나레이션 그대로 ({d_intro:.2f}s = {d_plain:.2f}s)")

# ---------------------------------------------------------------- 3. 설명·댓글 기본값
dt = default_texts("travel", "여기 진짜 한국 맞아요?", "https://www.agoda.com/x.html?cid=1234567", "이 영상은 아고다 제휴 … 수수료를 받을 수 있습니다.")
check("cid=1234567" in dt["desc"] and "수수료" in dt["desc"] and "#Shorts" in dt["desc"] and "cid=1234567" in dt["comment"]
      and dt["title"] == "여기 진짜 한국 맞아요?", "숙소: 설명란·첫 댓글에 아고다 제휴 링크 + 공정위 고지, #Shorts")
do = default_texts("owner", "가평 솔바람 펜션 아직도 모르세요?", "", "", "가평 솔바람 펜션", "가평 솔바람")
check("네이버에서 '가평 솔바람' 검색" in do["desc"] and "http" not in do["desc"], "사장님: 링크 없이 네이버 검색 안내")

# ---------------------------------------------------------------- 4. 업로드 (가짜 YouTube API)
calls = []


class Req:
    def __init__(self, name, result=None, err=None, chunks=None):
        self.name, self.result, self.err, self.chunks = name, result, err, chunks or []

    def execute(self):
        if self.err:
            raise self.err
        return self.result

    def next_chunk(self):
        if self.chunks:
            return type("S", (), {"progress": lambda s, p=self.chunks.pop(0): p})(), None
        return None, self.result


def http_err(code):
    return HttpError(httplib2.Response({"status": code}), b"{}")


class Fake:
    def __init__(self, channel="여행을 떠나자", privacy_back=None, thumb_err=None, comment_err=None):
        self.channel, self.privacy_back, self.thumb_err, self.comment_err = channel, privacy_back, thumb_err, comment_err

    def channels(self):
        return type("X", (), {"list": lambda s, **k: Req("channels", {"items": [{"id": "UC1", "snippet": {"title": self.channel}}]})})()

    def videos(self):
        def insert(s, **k):
            calls.append(("insert", k))
            pv = self.privacy_back or k["body"]["status"]["privacyStatus"]
            return Req("insert", {"id": "VID123", "status": {"privacyStatus": pv}}, chunks=[0.5])
        return type("X", (), {"insert": insert})()

    def thumbnails(self):
        def set_(s, **k):
            calls.append(("thumb", k))
            return Req("thumb", {}, err=self.thumb_err)
        return type("X", (), {"set": set_})()

    def commentThreads(self):
        def insert(s, **k):
            calls.append(("comment", k))
            return Req("comment", {"id": "C1"}, err=self.comment_err)
        return type("X", (), {"insert": insert})()


prog = []
yu._service_factory = lambda: Fake()
res = yu.upload_short(intro, "여기 진짜 한국 맞아요?", dt["desc"], thumb_path=noisy, comment=dt["comment"], privacy="public",
                      tags=["숙소추천"], expected_channel="여행을 떠나자", progress=prog.append)
ins = [c for c in calls if c[0] == "insert"][0][1]
th = [c for c in calls if c[0] == "thumb"][0][1]
cm = [c for c in calls if c[0] == "comment"][0][1]
check(res["video_id"] == "VID123" and res["url"] == "https://youtube.com/shorts/VID123" and res["thumbnail_ok"] and res["comment_ok"]
      and not res["warnings"] and prog == [0.5], f"업로드 → 썸네일 등록 → 첫 댓글 순서대로 성공 {res['warnings']}")
check(ins["body"]["snippet"]["title"] == "여기 진짜 한국 맞아요? #Shorts" and ins["body"]["status"]["privacyStatus"] == "public"
      and ins["body"]["snippet"]["categoryId"] == "19" and "cid=1234567" in ins["body"]["snippet"]["description"],
      "제목에 #Shorts, 여행 카테고리, 설명란에 제휴 링크")
check(th["videoId"] == "VID123" and th["media_body"]._filename.endswith("_upload.jpg")
      and cm["body"]["snippet"]["topLevelComment"]["snippet"]["textOriginal"] == dt["comment"].strip(),
      "thumbnails.set에 2MB 이하 썸네일, commentThreads.insert에 첫 댓글")

calls.clear()
yu._service_factory = lambda: Fake(channel="다른 채널")
try:
    yu.upload_short(intro, "t", "d", expected_channel="여행을 떠나자")
    check(False, "다른 채널")
except yu.YouTubeUploadError as e:
    check("다른 채널" in str(e) and not [c for c in calls if c[0] == "insert"], "연결 채널이 '여행을 떠나자'가 아니면 업로드하지 않음")

yu._service_factory = lambda: Fake(privacy_back="private", thumb_err=http_err(403), comment_err=http_err(403))
res2 = yu.upload_short(intro, "t", "d", thumb_path=thumb, comment="링크", privacy="public", expected_channel="")
check(res2["video_id"] == "VID123" and not res2["thumbnail_ok"] and not res2["comment_ok"] and len(res2["warnings"]) == 3
      and any("감사" in w for w in res2["warnings"]) and any("썸네일" in w for w in res2["warnings"]),
      "썸네일·댓글이 거절돼도 영상 업로드는 성공 + 경고 (미감사 프로젝트 비공개 잠김 안내 포함)")
try:
    yu.upload_short(intro, "", "d")
    check(False, "빈 제목")
except yu.YouTubeUploadError:
    check(True, "빈 제목 → 업로드 안 함")
yu._service_factory = None

finish(fails)
