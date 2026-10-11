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

# ---------------------------------------------------------------- 1. 썸네일 (프로급: 큰 그라데이션 메인 카피 + 서브 박스 + 비네트)
frame = np.array(Image.open(fixture("photo_pool.jpg")).convert("RGB"))
thumb = comp.render_thumbnail(frame, "여길 안 간다고?", os.path.join(work, "final_thumb.png"), sub_text="오션뷰 인피니티 풀", accent="😱")
ti = np.array(Image.open(thumb).convert("RGB")).astype(int)
yellow = lambda a: int(((a[..., 0] > 200) & (a[..., 1] > 170) & (a[..., 2] < 90)).sum())
orange_red = lambda a: int(((a[..., 0] > 200) & (a[..., 1] < 140) & (a[..., 2] < 80)).sum())
white_box = lambda a: int(((a[..., 0] > 245) & (a[..., 1] > 245) & (a[..., 2] > 245)).sum())
top, mid = ti[:int(1920 * 0.42)], ti[int(1920 * 0.52):int(1920 * 0.68)]
check(ti.shape == (1920, 1080, 3) and yellow(top) > 20000 and orange_red(top) > 20000,
      f"메인 카피: 노랑→주황→빨강 그라데이션 큰 글자 (노랑 {yellow(top)}px · 주황/빨강 {orange_red(top)}px)")
check(white_box(mid) > 40000 and yellow(ti[int(1920 * 0.5):]) < 2000, f"서브 카피: 중간~하단 흰 하이라이트 박스 ({white_box(mid)}px)")
plain_bg = comp.render_thumbnail(frame, "", os.path.join(work, "bare.png"))
pb = np.array(Image.open(plain_bg).convert("RGB")).astype(int)
src = np.array(Image.open(fixture("photo_pool.jpg")).convert("RGB").resize((1080, 1920))).astype(int)
check(yellow(pb) < 500 and pb[:80].mean() < src[:80].mean() - 30 and pb[-80:].mean() < src[-80:].mean() - 25,
      "문구가 없으면 사진만 + 상단·하단 비네트로 어둡게 (지어낸 문구 없음)")
old_px = 0.115 * 1080
bb = np.argwhere((ti[:768, :, 0] > 200) & (ti[:768, :, 2] < 90))
glyph_h = (bb[:, 0].max() - bb[:, 0].min()) if len(bb) else 0
check(glyph_h > old_px * 2.5, f"메인 카피 크기: 기존 글자 높이({old_px:.0f}px)의 2.5배 이상 영역 ({glyph_h}px)")
long_t = comp.render_thumbnail(frame, "솔직히 이 가격에 오션뷰 인피니티 풀까지", os.path.join(work, "long.png"), accent="ㄷㄷ")
check(os.path.exists(long_t), "긴 카피는 2줄 안으로 글자 축소, 'ㄷㄷ'은 마지막 어절에 붙임")
em = comp._emoji_image("🔥", 100)
check(em is None or (em.height == 100 and len(set(em.convert("RGB").getdata())) > 20), "포인트 이모지 컬러 렌더 (글꼴 없으면 생략)")

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

# ---------------------------------------------------------------- 3. 쇼츠 카피·설명란·첫 댓글
from core.shorts_meta import (PINNED_NOTICE, build_shorts_description, find_hype_claim, hashtags_for, make_shorts_copy)
import core.shorts_meta as sm
USP = "오션뷰 인피니티 풀, 객실 통창 바다 전망"
cp_t = make_shorts_copy("travel", hook="여기 진짜 한국 맞아요?", usp=USP, product_name="St John's Hotel", mock=True, seed=3)
check(cp_t["title"].endswith(" #Shorts") and len(cp_t["title"]) <= 70 and "St John" not in cp_t["title"]
      and any(w in cp_t["title"] for w in ("손해", "?", "ㄷㄷ", "🔥")) and not find_hype_claim(cp_t["title"], USP),
      f"제목: 손실 회피·호기심형 + 끝에 #Shorts, 숙소명 숨김, 근거 없는 주장 없음 '{cp_t['title']}'")
check(5 <= len(cp_t["hashtags"]) <= 7 and "#오션뷰숙소" in cp_t["hashtags"] and "#수영장숙소" in cp_t["hashtags"]
      and all(h.startswith("#") and " " not in h for h in cp_t["hashtags"]), f"해시태그 5~7개, USP 키워드 우선 {cp_t['hashtags']}")
check(find_hype_claim("예약 폭주하는 인생 숙소") == "예약 폭주" and find_hype_claim("최저가 풀빌라") == "최저가"
      and find_hype_claim("국내 끝판왕 풀빌라 ㄷㄷ") is None and find_hype_claim("예약 폭주 숙소", "예약 폭주 중") is None,
      "예약 폭주·최저가 같은 사실 주장은 USP에 있을 때만, '끝판왕 ㄷㄷ' 같은 감탄은 허용")
check("#내돈내산" not in hashtags_for("commerce", "과열 방지") and all(t not in hashtags_for("commerce", "과열 방지") for t in ("#숙소추천",)),
      "쇼핑 해시태그: 제휴 영상과 모순되는 #내돈내산 금지, 다른 모드 태그 섞이지 않음")


class GemModels:
    def generate_content(self, **kw):
        return type("R", (), {"text": json.dumps(gem_reply, ensure_ascii=False)})()


import json
import google.genai as gg
_orig_client = gg.Client
gg.Client = lambda api_key=None: type("C", (), {"models": GemModels()})()
gem_reply = {"title": "모르면 무조건 손해! 예약 폭주하는 인생 숙소", "thumb_main": "이게 숨어 있었다고?", "thumb_sub": "St John's Hotel 오션뷰",
             "accent": "💥", "hashtags": ["#감성숙소", "#품절임박숙소", "#풀빌라"]}
cp_g = make_shorts_copy("travel", hook="h", usp=USP, product_name="St John's Hotel", api_key="fake", seed=3)
gem_reply = {"title": "강릉에 이런 오션뷰 숙소가 숨겨져 있었다니 ㄷㄷ", "thumb_main": "이게 숨어 있었다고?", "thumb_sub": "오션뷰 인피니티 풀",
             "accent": "🔥", "hashtags": ["#감성숙소", "#오션뷰"]}
cp_ok = make_shorts_copy("travel", hook="h", usp=USP, product_name="St John's Hotel", api_key="fake", seed=3)
gg.Client = _orig_client
check(cp_g["title"] == cp_t["title"] and cp_g["thumb_main"] == "이게 숨어 있었다고?" and "St John" not in cp_g["thumb_sub"]
      and cp_g["accent"] == "💥" and "#품절임박숙소" not in cp_g["hashtags"] and len(cp_g["rejected"]) == 2,
      f"Gemini 카피 검증: '예약 폭주' 제목·숙소명 노출 서브·'품절' 태그는 버리고 안전한 템플릿 유지 {cp_g['rejected']}")
check(cp_ok["title"] == "강릉에 이런 오션뷰 숙소가 숨겨져 있었다니 ㄷㄷ #Shorts" and cp_ok["source"] == "gemini" and "#감성숙소" in cp_ok["hashtags"],
      "Gemini 카피가 규칙을 지키면 그대로 사용 (#Shorts 자동)")

DISC_SHORT = "※ 제휴 링크 포함 · 예약 시 수수료를 받을 수 있어요"
dt = default_texts("travel", cp_ok, "https://www.agoda.com/x.html?cid=1234567", "이 영상은 아고다 제휴 … 수수료를 받을 수 있습니다.", DISC_SHORT)
lines = dt["desc"].split("\n")
check(lines[0] == "【 강릉에 이런 오션뷰 숙소가 숨겨져 있었다니 ㄷㄷ 】" and lines[1] == DISC_SHORT
      and PINNED_NOTICE["travel"] in lines and lines[-1].count("#") >= 5 and "cid=" not in dt["desc"],
      "설명란: 1줄 제목 → 제휴 고지 → 📌 첫 댓글 안내 → 해시태그 (링크는 설명란에 안 넣음)")
check("cid=1234567" in dt["comment"] and "수수료" in dt["comment"] and dt["title"] == cp_ok["title"],
      "첫 댓글: 아고다 제휴 링크 + 공정위 고지 전문, 제목 = 쇼츠 카피")
do = default_texts("owner", make_shorts_copy("owner", usp="숙성 삼겹살", product_name="가평 솔바람", mock=True, seed=1), "", "", "",
                   "가평 솔바람 펜션", "가평 솔바람")
check(PINNED_NOTICE["owner"] in do["desc"] and "http" not in do["desc"] and "네이버에서 '가평 솔바람' 검색" in do["comment"],
      "사장님: 링크 없이 첫 댓글에 네이버 검색 안내")

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


# 모드별 채널 (테스트 동안 .env 값·토큰 폴더를 임시로)
CH = {"YOUTUBE_CHANNEL_TRAVEL": "여행을 떠나자", "YOUTUBE_CHANNEL_STORE": "샵스토리", "YOUTUBE_CHANNEL_SHOPPING": ""}
_saved_env = {k: os.environ.get(k) for k in CH}
os.environ.update(CH)
_secrets = yu.SECRETS_DIR
yu.SECRETS_DIR = os.path.join(work, "secrets")
CHANNEL_OF = {"travel": "여행을 떠나자", "store": "샵스토리", "shopping": "쇼핑 전용"}
seen_keys = []


def factory(**kw):
    def f(key):
        seen_keys.append(key)
        return Fake(channel=kw.get("channel", CHANNEL_OF.get(key, "?")), **{k: v for k, v in kw.items() if k != "channel"})
    return f


check([yu.channel_key(m) for m in ("travel", "owner", "commerce", "store", "shopping")] == ["travel", "store", "shopping", "store", "shopping"]
      and yu.token_path("owner").endswith(os.path.join("secrets", "token_store.json"))
      and yu.token_path("commerce").endswith("token_shopping.json") and yu.token_path("travel").endswith("token_travel.json"),
      "제작 모드 → 채널 토큰: 숙소=token_travel, 사장님=token_store, 쇼핑=token_shopping")
check(yu.expected_channel("travel") == "여행을 떠나자" and yu.expected_channel("owner") == "샵스토리" and yu.expected_channel("commerce") == "",
      "모드별 기대 채널명 (.env YOUTUBE_CHANNEL_TRAVEL/STORE/SHOPPING)")
try:
    yu.channel_key("radar")
    check(False, "알 수 없는 모드")
except yu.YouTubeUploadError:
    check(True, "알 수 없는 모드는 거부")

prog = []
yu._service_factory = factory()
res = yu.upload_short(intro, "여기 진짜 한국 맞아요?", dt["desc"] + "\n" + dt["comment"], mode="travel", thumb_path=noisy, comment=dt["comment"],
                      privacy="public", tags=["숙소추천"], progress=prog.append)
ins = [c for c in calls if c[0] == "insert"][0][1]
th = [c for c in calls if c[0] == "thumb"][0][1]
cm = [c for c in calls if c[0] == "comment"][0][1]
check(res["video_id"] == "VID123" and res["url"] == "https://youtube.com/shorts/VID123" and res["thumbnail_ok"] and res["comment_ok"]
      and not res["warnings"] and prog == [0.5] and res["channel"] == "여행을 떠나자" and seen_keys[-1] == "travel",
      f"숙소 모드 → travel 토큰으로 '여행을 떠나자'에 업로드 → 썸네일 → 첫 댓글 {res['warnings']}")
check(ins["body"]["snippet"]["title"] == "여기 진짜 한국 맞아요? #Shorts" and ins["body"]["status"]["privacyStatus"] == "public"
      and ins["body"]["snippet"]["categoryId"] == "19" and "cid=1234567" in ins["body"]["snippet"]["description"],
      "제목에 #Shorts, 여행 카테고리, 설명란에 제휴 링크")
check(th["videoId"] == "VID123" and th["media_body"]._filename.endswith("_upload.jpg")
      and cm["body"]["snippet"]["topLevelComment"]["snippet"]["textOriginal"] == dt["comment"].strip(),
      "thumbnails.set에 2MB 이하 썸네일, commentThreads.insert에 첫 댓글")

res_s = yu.upload_short(intro, "가게 소개", "d", mode="owner")
check(res_s["channel"] == "샵스토리" and res_s["channel_key"] == "store" and seen_keys[-1] == "store", "사장님 모드 → store 토큰 → '샵스토리'")

calls.clear()
yu._service_factory = factory(channel="샵스토리")          # travel 토큰 자리에 다른 채널 토큰이 들어간 상황
try:
    yu.upload_short(intro, "t", "d", mode="travel")
    check(False, "다른 채널")
except yu.YouTubeUploadError as e:
    check("업로드 중단" in str(e) and "샵스토리" in str(e) and "--mode travel" in str(e) and not [c for c in calls if c[0] == "insert"],
          "숙소 모드인데 실제 채널이 '샵스토리' → 업로드 직전 중단 (영상 전송 안 함)")
calls.clear()
try:
    yu.upload_short(intro, "t", "d", mode="commerce")
    check(False, "빈 기대 채널")
except yu.YouTubeUploadError as e:
    check("YOUTUBE_CHANNEL_SHOPPING" in str(e) and not calls, "쇼핑 채널명이 비어 있으면 비교 기준이 없으니 업로드 안 함 (API 호출도 안 함)")

yu._service_factory = factory(privacy_back="private", thumb_err=http_err(403), comment_err=http_err(403))
res2 = yu.upload_short(intro, "t", "d", mode="travel", thumb_path=thumb, comment="링크", privacy="public")
check(res2["video_id"] == "VID123" and not res2["thumbnail_ok"] and not res2["comment_ok"] and len(res2["warnings"]) == 3
      and any("감사" in w for w in res2["warnings"]) and any("썸네일" in w for w in res2["warnings"]),
      "썸네일·댓글이 거절돼도 영상 업로드는 성공 + 경고 (미감사 프로젝트 비공개 잠김 안내 포함)")
try:
    yu.upload_short(intro, "", "d", mode="travel")
    check(False, "빈 제목")
except yu.YouTubeUploadError:
    check(True, "빈 제목 → 업로드 안 함")

# 인증: 고른 채널이 기대 채널과 다르면 토큰을 저장하지 않음 (브라우저 OAuth는 가짜)
import google_auth_oauthlib.flow as gflow
open(os.path.join(work, "client_secret.json"), "w").write("{}")
os.environ["YOUTUBE_CLIENT_SECRETS"] = os.path.join(work, "client_secret.json")
_orig_flow = gflow.InstalledAppFlow


class FakeCreds:
    def to_json(self):
        return '{"token": "x"}'


class FakeFlow:
    @classmethod
    def from_client_secrets_file(cls, *a, **k):
        return cls()

    def run_local_server(self, **k):
        return FakeCreds()


gflow.InstalledAppFlow = FakeFlow
yu._service_factory = factory(channel="샵스토리")
try:
    yu.authorize_interactive("travel")
    check(False, "인증 채널 불일치")
except yu.YouTubeUploadError as e:
    check("저장하지 않았어요" in str(e) and not os.path.exists(yu.token_path("travel")), "인증: travel에 '샵스토리'를 고르면 토큰 저장 안 함")
yu._service_factory = factory()
ok = yu.authorize_interactive("store")
check(ok["title"] == "샵스토리" and os.path.exists(yu.token_path("store")) and not os.path.exists(yu.token_path("travel"))
      and yu.is_authorized("owner") and not yu.is_authorized("travel"), "인증: store → secrets/token_store.json에만 저장")
shop = yu.authorize_interactive("shopping")
check(shop["title"] == "쇼핑 전용" and shop["expected"] == "" and os.path.exists(yu.token_path("shopping")),
      "인증: 쇼핑 채널명이 .env에 없으면 저장하고 채널명을 알려 줌 (.env에 적기 전엔 업로드 차단)")

import importlib.util
spec = importlib.util.spec_from_file_location("youtube_auth", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "youtube_auth.py"))
auth_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth_cli)
os.remove(yu.token_path("store"))
rc_ok = auth_cli.main(["--mode", "store"])
yu._service_factory = factory(channel="여행을 떠나자")
rc_bad = auth_cli.main(["--mode", "store"])
check(rc_ok == 0 and rc_bad == 1, "auth 스크립트 --mode: 일치하면 0, 다른 채널이면 1 (저장 안 함)")

gflow.InstalledAppFlow = _orig_flow
os.environ.pop("YOUTUBE_CLIENT_SECRETS", None)
yu._service_factory = None
yu.SECRETS_DIR = _secrets
for k, v in _saved_env.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v

finish(fails)
