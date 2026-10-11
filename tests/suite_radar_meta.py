"""떡상 레이더 → 업로드 메타데이터 자동 주입: 트렌드 다이제스트(DB 캐시), 카피 프롬프트 필수 맥락, 레이더 해시태그, 표시 + 첫 0.5초 커버 프레임"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import fixture, tmp, finish  # noqa: E402
import json
import os
import subprocess
import logging
logging.disable(logging.WARNING)
from datetime import datetime, timedelta, timezone

import imageio_ffmpeg
import numpy as np
from PIL import Image

from core.radar import store, trends
from core.radar.scoring import VideoRecord
import core.shorts_meta as sm

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


work = tmp("radar_meta_")
db = os.path.join(work, "radar.db")
conn = store.connect(db)
now = datetime.now(timezone.utc)


def add(run_id, vid, title, channel, ratio, passed=True, days_ago=3, vertical="food"):
    rec = VideoRecord(video_id=vid, title=title, channel_id=channel, channel_title=channel, published_at=now - timedelta(days=days_ago),
                      duration_sec=30, views=int(ratio * 10000), likes=10, comments=1, subscribers=10000, subscribers_hidden=False,
                      keyword="맛집 추천", vertical=vertical, outlier_ratio=ratio, velocity_per_day=ratio * 1000,
                      reject_reason=None if passed else "배수 미달")
    store.save_records(conn, run_id, [rec])


run = store.start_run(conn, "live", "food", 30, ["맛집 추천"])
add(run, "v1", "여기 모르면 진짜 손해 가평 고기집 #가평맛집 #고기맛집 #설맥", "chA", 40)
add(run, "v2", "현지인만 아는 숨은 맛집 이유 #가평맛집 #숨은맛집", "chB", 25)
add(run, "v3", "웨이팅 없이 먹는 법 무조건 알아두세요 #고기맛집 #숨은맛집", "chC", 12)
add(run, "v4", "평범한 일상 브이로그 #일상 #가평맛집", "chD", 1.2, passed=False)
add(run, "v5", "브랜드 단독 리뷰 #브랜드전용태그", "chA", 30)
add(run, "v6", "브랜드 단독 리뷰2 #브랜드전용태그", "chA", 28)
store.save_hook(conn, "v1", {"source": "gemini", "hook_type": "loss_warning", "pattern_template": "{장소} 모르면 손해인 이유",
                             "why_it_works": "손실 회피", "relevant": 1, "spoken_line": "", "on_screen_text": ""}, "food")
store.finish_run(conn, run, 500, 6, 5)
vrun = store.start_run(conn, "live", "viral", 30, ["꿀팁"])
add(vrun, "z1", "아무도 모르는 꿀팁 #꿀팁 #드라마이름", "chX", 50, vertical="viral")
add(vrun, "z2", "이거 몰랐다면 손해 #꿀팁 #드라마이름", "chY", 20, vertical="viral")
store.finish_run(conn, vrun, 200, 2, 2)


class FakeModels:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def generate_content(self, **kw):
        self.calls.append(kw)
        return type("R", (), {"text": json.dumps(self.reply, ensure_ascii=False)})()


import google.genai as gg
_orig = gg.Client
pat_reply = {"patterns": [
    {"template": "{지역} 사람들만 아는 {메뉴} 맛집", "hook_type": "reveal", "why": "정보 공백", "source_ratio": 40},
    {"template": "여기 모르면 진짜 손해 가평 고기집", "hook_type": "loss_warning", "why": "원문 그대로", "source_ratio": 40},
    {"template": "{장소} 모르면 손해인 이유", "hook_type": "loss_warning", "why": "손실 회피", "source_ratio": 25}]}
fm = FakeModels(pat_reply)
gg.Client = lambda api_key=None: type("C", (), {"models": fm})()
d = trends.build_digest(conn, "food", api_key="fake")
trends.save_digest(conn, d)
trends.save_digest(conn, trends.build_digest(conn, "viral", api_key=None))
gg.Client = _orig
conn.close()

# ---------------------------------------------------------------- 1. 트렌드 다이제스트
tags = [t["tag"] for t in d["hashtag_pool"]]
check(d["n_outliers"] == 5 and d["top_ratio"] == 40.0 and d["n_videos"] == 6, f"떡상 {d['n_outliers']}개/수집 {d['n_videos']}개, 최고 {d['top_ratio']}배")
check(tags[:2] == ["#가평맛집", "#고기맛집"] or set(tags[:3]) >= {"#가평맛집", "#고기맛집", "#숨은맛집"},
      f"해시태그 풀: 떡상 배수 가중 순 {tags}")
check("#브랜드전용태그" not in tags and "#설맥" not in tags, "한 채널만 쓴 태그(남의 상호·브랜드)는 풀에서 제외")
words = [w["word"] for w in d["emotion_words"]]
check(any("손해" in w for w in words) and any("무조건" in w or "숨은" in w for w in words), f"감정·호기심 단어 {words}")
check([p["template"] for p in d["title_patterns"]] == ["{지역} 사람들만 아는 {메뉴} 맛집", "{장소} 모르면 손해인 이유"],
      "제목 패턴: Gemini가 구조로 추상화, 원문 제목을 그대로 옮긴 패턴은 버림")
check(d["first3s_hooks"] and d["first3s_hooks"][0]["template"] == "{장소} 모르면 손해인 이유", "첫 3초 후킹 템플릿도 포함")
ld = trends.load_digest("food", db)
check(ld and ld["age_days"] < 0.1 and not ld["stale"] and ld["fallback_from"] is None, "DB에서 최신 다이제스트 로드 (신선도 표시)")
lv = trends.load_digest("stay", db)
check(lv and lv["vertical"] == "viral" and lv["fallback_from"] == "stay", "업종 데이터가 없으면 범용(viral) 다이제스트로 대체")
check(trends.digest_for("owner", "food", db)["vertical"] == "food" and trends.digest_for("owner", "shop", db)["vertical"] == "viral",
      "제작 모드·업종 → 다이제스트 (사장님 음식점 = food, 기타 매장 = 범용)")
check("#드라마이름" in [t["tag"] for t in lv["hashtag_pool"]] and sm.trend_tag_pool(lv) == [] and "#드라마이름" not in sm.radar_block(lv)
      and "문장 구조'만 참고" in sm.radar_block(lv), "범용(viral) 데이터는 제목 구조만 참고, 해시태그(드라마·연예인 등 다른 주제)는 안 씀")

# ---------------------------------------------------------------- 2. 카피 생성기에 필수 맥락으로 주입
block = sm.radar_block(ld)
check("필수 반영" in block and "{지역} 사람들만 아는 {메뉴} 맛집" in block and "#가평맛집" in block and "40.0배" in block
      and "여기 모르면 진짜 손해 가평 고기집" not in block, "프롬프트 블록: 제목 패턴·배수·감정어·태그 풀 (원문 제목 없음)")
gen = FakeModels({"title": "가평 사람들만 아는 숙성 삼겹살 맛집", "thumb_main": "여길 모른다고?", "thumb_sub": "숙성 삼겹살",
                  "accent": "🔥", "hashtags": ["#설맥가평현리점", "#숙성삼겹살"], "pattern_used": 1})
gg.Client = lambda api_key=None: type("C", (), {"models": gen})()
c = sm.make_shorts_copy("owner", hook="", usp="숙성 삼겹살, 통창 계곡 뷰", product_name="설맥 가평현리점", api_key="fake",
                        trend=ld, region="경기 가평군", signature="숙성 삼겹살")
prompt = gen.calls[-1]["contents"][0]
check("[떡상 레이더 최신 데이터 - 필수 반영]" in prompt and "지역: 경기 가평군" in prompt and "시그니처(대표 메뉴·특징): 숙성 삼겹살" in prompt,
      "렌더 때 레이더 최신 데이터 + 지역 + 시그니처가 프롬프트에 자동으로 들어감")
check(c["title"] == "가평 사람들만 아는 숙성 삼겹살 맛집 #Shorts" and c["radar"]["pattern"]["template"] == "{지역} 사람들만 아는 {메뉴} 맛집",
      f"결과: 지역 + 시그니처 + 레이더 패턴 결합 제목, 사용한 패턴 기록 '{c['title']}'")
from_pool = [t for t in c["hashtags"] if t in tags]
check(len(from_pool) >= sm.RADAR_TAGS_MIN and "#숙성삼겹살" in c["hashtags"] and 5 <= len(c["hashtags"]) <= 7,
      f"해시태그: 레이더 풀 {from_pool} + 가게·메뉴 고유 태그 (모델이 풀 태그를 안 줘도 보충) {c['hashtags']}")
gen.reply = {"title": "예약 폭주하는 가평 맛집", "thumb_main": "x", "thumb_sub": "y", "accent": "🔥", "hashtags": [], "pattern_used": 2}
c2 = sm.make_shorts_copy("owner", usp="숙성 삼겹살", product_name="설맥", api_key="fake", trend=ld)
check("예약 폭주" not in c2["title"] and c2["radar"]["pattern"] is None, "레이더를 넣어도 근거 없는 주장(예약 폭주)은 여전히 차단, 버린 제목의 패턴은 기록 안 함")
gen.reply = {"title": "세인트존스호텔 가면 꼭 하는 것", "thumb_main": "x", "thumb_sub": "y", "accent": "🔥",
             "hashtags": ["#세인트존스호텔", "#오션뷰숙소"], "pattern_used": 9}
c3 = sm.make_shorts_copy("travel", usp="오션뷰", product_name="세인트존스호텔", api_key="fake", trend=ld)
check("세인트존스" not in c3["title"] and not any("세인트존스" in t for t in c3["hashtags"]) and c3["radar"]["pattern"] is None,
      "숙소 모드: 레이더를 넣어도 숙소명 노출 금지(제목·태그), 범위 밖 패턴 번호 무시")
gg.Client = _orig
m = sm.make_shorts_copy("owner", usp="숙성 삼겹살", product_name="설맥", trend=ld, mock=True)
check(m["radar"] and any(t in tags for t in m["hashtags"]) and m["radar"]["pattern"] is None,
      "키 없음·mock: 제목은 안전한 기본 훅, 해시태그에는 레이더 풀 반영 (자리표시자 기계 치환 안 함)")

# ---------------------------------------------------------------- 3. 업로드 패널 표시
from streamlit.testing.v1 import AppTest


def _panel(copy):
    import os
    import streamlit as st
    from ui.youtube_panel import render_youtube_panel
    render_youtube_panel(os.environ["_RM_VIDEO"], None, "owner", hook="", link="", disclosure="", business_name="설맥",
                         search_kw="설맥", copy=copy)


video = os.path.join(work, "v.mp4")
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x568:d=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", video], check=True)
os.environ["_RM_VIDEO"] = video
at = AppTest.from_function(_panel, args=(c,)).run()
info = " ".join(i.value for i in at.info)
check("떡상 레이더 반영" in info and "{지역} 사람들만 아는 {메뉴} 맛집" in info and "#가평맛집" in info, "업로드 패널: 반영한 레이더 패턴·태그·데이터 기준일 표시")
at2 = AppTest.from_function(_panel, args=({**c, "radar": None},)).run()
check(any("레이더 데이터가 없어" in w.value for w in at2.warning), "레이더 데이터가 없으면 경고 + 자동 수집 확인 안내")

# ---------------------------------------------------------------- 4. 첫 0.5초 커버 프레임 (PC 스튜디오 업로드 대응)
from core.compositor import INTRO_SEC, AdCompositor
ff = imageio_ffmpeg.get_ffmpeg_exe()
comp = AdCompositor(output_dir=work)
thumb = comp.render_thumbnail(np.array(Image.open(fixture("photo_pool.jpg")).convert("RGB")), "여길 모른다고?", os.path.join(work, "t.png"),
                              sub_text="숙성 삼겹살", accent="🔥")
vid, aud = os.path.join(work, "s.mp4"), os.path.join(work, "s.m4a")
subprocess.run([ff, "-y", "-v", "error", "-loop", "1", "-i", fixture("photo_room.jpg"), "-t", "2", "-r", "24",
                "-vf", "scale=1080:1920,setsar=1", "-pix_fmt", "yuv420p", vid], check=True)
subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.4", "-c:a", "aac", aud], check=True)
out = comp.assemble_ad([{"scene_index": 1, "video_path": vid, "audio_path": aud, "caption": "자막"}], output_filename="cover.mp4",
                       mode="owner", intro_image=thumb)
ti = np.array(Image.open(thumb).convert("RGB")).astype(int)


def frame_n(n):
    raw = subprocess.run([ff, "-v", "error", "-i", out, "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)


d0, d11, d14 = (float(np.abs(frame_n(k) - ti).mean()) for k in (0, 11, 14))
check(INTRO_SEC == 0.5 and d0 < 6 and d11 < 6 and d14 > 20,
      f"커버: 0~0.5초(프레임 0~11) = 썸네일 (차이 {d0:.1f}·{d11:.1f}), 0.58초부터 본편 ({d14:.1f})")
probe = subprocess.run([ff, "-hide_banner", "-i", out, "-vf", "select=eq(n\\,0),showinfo", "-frames:v", "1", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
check("iskey:1" in probe and "pts_time:0 " in probe.replace("pts_time:0\t", "pts_time:0 "),
      "첫 프레임은 시각 0의 키프레임 (스튜디오·플레이어가 첫 화면으로 커버를 잡음)")
finish(fails)
