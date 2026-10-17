"""매장 모드(이마트24) 실영상 피드백 4건: ① 씬 1 말토막 헤드라인 자막 제거(본문 단일 자막) ② 독립 커버 이미지(thumbnail.jpg)
③ 해시태그 단일 소스·업종 가드(편의점에 식당 태그 금지) ④ 첫 댓글의 클릭 불가 지도 URL 제거(검색 안내+주소)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402
import json
import os
import shutil
import subprocess
import logging
logging.disable(logging.WARNING)

for _k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ[_k] = ""

import imageio_ffmpeg
import numpy as np
from PIL import Image

from core.hashtags import BLOCKED, build_tag_set, detect_industry, guard_tags, replace_hashtag_line
from core.seo_generator import generate_seo_metadata
from core.shorts_meta import build_first_comment, make_shorts_copy, build_shorts_description
from core import pipeline as pl
from core import youtube_upload as yu

FF = imageio_ffmpeg.get_ffmpeg_exe()
fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


NAME = "이마트24 덕계메디프라자점"
USP = "양주사랑카드 한 장이면 1+1 행사 상품을 알뜰하게"
CONCEPT = "양주사랑카드 한 장이면 매번 알뜰"
ADDR = "경기 양주시 덕계로 123"
RESTAURANT_WORDS = ("맛집", "데이트", "카페", "핫플", "먹방")

# ---------------------------------------------------------------- ③ 해시태그: 업종 판별 + 가드 + 단일 소스
check(detect_industry("owner", "shop", NAME, USP, CONCEPT) == "retail", "이마트24·1+1 → 편의점/소매(retail)로 판별")
check(detect_industry("owner", "food", "오늘식탁", "겉절이", "신선") == "restaurant" and detect_industry("owner", "food", "블루보틀 카페", "", "") == "cafe"
      and detect_industry("owner", "stay", "솔바람 펜션", "", "") == "stay" and detect_industry("commerce") == "commerce",
      "식당·카페·숙소·쇼핑 업종 판별")
check(detect_industry("owner", "food", "덕계 국밥집", "저녁 1+1 행사", "") == "restaurant", "식당 문맥의 '행사'는 편의점으로 오판하지 않음")

ts = build_tag_set("owner", business_type="shop", name=NAME, usp=USP, concept=CONCEPT, region="경기 양주시")
core_words = " ".join(ts.core + ts.pool)
check(ts.industry == "retail" and not any(w in core_words for w in RESTAURANT_WORDS), f"편의점: 식당·카페 태그 없음 {ts.core}")
check(ts.core[0] == "#이마트24덕계메디프라자점" and "#이마트24" in ts.core and "#편의점" in ts.core and "#원플러스원" in ts.core
      and ts.core[-1] == "#shorts" and 5 <= len(ts.core) <= 7, "편의점 핵심 태그: 상호·브랜드·업종·행사·#shorts")
check("#양주편의점" in ts.core, "지역+업종 태그 (#양주편의점)")
check(ts.comment == ts.core[:3] and ts.pool[:len(ts.core)] == [t.lstrip("#") for t in ts.core] and 15 <= len(ts.pool) <= 20,
      f"단일 풀: 댓글 태그 = 핵심의 앞 3개, 검색 태그 = 핵심이 앞인 15~20개 ({len(ts.pool)}개)")
rs = build_tag_set("owner", business_type="food", name="오늘식탁", usp="주문 즉시 무쳐내는 겉절이", concept="신선한 겉절이", region="서울 성동구")
check(any("맛집" in t for t in rs.core) and not any("편의점" in t or "원플러스원" in t for t in rs.core + rs.pool),
      f"식당은 맛집 태그, 편의점 태그 없음 {rs.core}")
check(guard_tags(["로컬맛집", "데이트코스", "숨은맛집", "편의점", "이마트24"], "retail") == ["편의점", "이마트24"], "guard_tags: 편의점에서 식당 태그 제거")
check(all(isinstance(BLOCKED[k], list) for k in ("retail", "restaurant", "stay")), "업종별 금지 사전 존재")

# 카피(설명란·댓글) / SEO(설명란·검색 태그) / 업로드 텍스트가 같은 태그를 쓴다
copy = make_shorts_copy("owner", hook="결제할 때 카드 한 장", usp=USP, product_name=NAME, mock=True, region="경기 양주시",
                        core_concept=CONCEPT, business_type="shop", signature="양주사랑카드")
check(copy["hashtags"] == ts.core or (copy["hashtags"][0] == ts.core[0] and set(copy["hashtags"]) == set(ts.core)),
      f"쇼츠 카피 해시태그 = 같은 핵심 태그 {copy['hashtags']}")
seo = generate_seo_metadata(mode="owner", product_name=NAME, usp=USP, search_keyword=NAME, address=ADDR, mock=True,
                            business_type="shop", core_concept=CONCEPT, tag_set=copy["tag_set"])
last_line = seo["description"].splitlines()[-1].split()
check(last_line == copy["hashtags"] and seo["tags_list"][:len(copy["hashtags"])] == [t.lstrip("#") for t in copy["hashtags"]],
      f"SEO 설명란 해시태그 줄 = 카피 핵심 태그, 검색 태그는 그 풀이 앞 ({len(seo['tags_list'])}개)")
check(not any(any(w in t for w in RESTAURANT_WORDS) for t in seo["tags_list"]), f"검색 태그에 식당·카페 키워드 없음 {seo['tags_list']}")

spec = pl.JobSpec(mode="owner", name=NAME, usp=USP, business_type="shop", search_keyword=NAME, address=ADDR, core_concept=CONCEPT)
texts = pl.upload_texts(spec, copy, hook="결제할 때 카드 한 장")
desc_tags = texts["desc"].splitlines()[-1].split()
comment_tags = texts["comment"].splitlines()[-1].split()
check(desc_tags == copy["hashtags"] and comment_tags == copy["hashtags"][:3] and texts["tags"][:len(desc_tags)] == [t.lstrip("#") for t in desc_tags],
      f"업로드 텍스트: 설명란={desc_tags[:3]}…, 댓글={comment_tags}, 검색 태그 {len(texts['tags'])}개가 같은 풀")

# 모델(LLM)이 식당 태그를 내도 설명란·검색 태그에서 걸러진다
import google.genai as gg
bad_reply = {"title": "양주 주민은 이 카드 모르면 손해 #shorts",
             "description": "카드 한 장이면 편의점이 알뜰해져요.\n\n📌 매장 위치 및 방문 꿀팁은 고정 댓글을 확인해주세요!\n\n#로컬맛집 #데이트코스 #숨은맛집",
             "tags": "로컬맛집, 데이트코스, 숨은맛집, 양주사랑카드, 편의점, 맛집추천"}


class _Models:
    def generate_content(self, **kw):
        return type("R", (), {"text": json.dumps(bad_reply, ensure_ascii=False)})()


_orig = gg.Client
gg.Client = lambda api_key=None: type("C", (), {"models": _Models()})()
try:
    seo_llm = generate_seo_metadata(mode="owner", product_name=NAME, usp=USP, search_keyword=NAME, address=ADDR, api_key="fake",
                                    business_type="shop", core_concept=CONCEPT, tag_set=copy["tag_set"])
finally:
    gg.Client = _orig
check(seo_llm["source"] == "gemini" and not any(any(w in t for w in RESTAURANT_WORDS) for t in seo_llm["tags_list"])
      and "로컬맛집" not in seo_llm["description"] and seo_llm["description"].splitlines()[-1].split() == copy["hashtags"],
      f"LLM 태그에서 식당 키워드 제거, 설명란 해시태그 줄은 핵심 태그로 통일 {seo_llm['tags_list'][:8]}")
check(replace_hashtag_line("본문\n\n#a #b", ["#x", "#y"]) == "본문\n\n#x #y", "replace_hashtag_line: 기존 해시태그 줄을 핵심 태그로 교체")

# ---------------------------------------------------------------- ④ 첫 댓글: 클릭 안 되는 지도 URL 대신 검색 안내 + 주소
cm = build_first_comment("owner", "여기 가면 제일 먼저 담을 꿀템은? 👇", copy["hashtags"][:3], search_keyword=NAME, business_type="shop",
                         usp=USP, address=ADDR)
check("http" not in cm and "map.naver.com" not in cm and "🗺️" not in cm, "첫 댓글에 지도 URL 없음")
check(f"네이버 지도나 카카오맵에서 '{NAME}'을 검색해 보세요" in cm and f"주소: {ADDR}" in cm, f"검색 안내 + 주소 텍스트:\n{cm}")
cm2 = build_first_comment("owner", "", copy["hashtags"][:3], search_keyword="오늘식탁")
check("http" not in cm2 and "'오늘식탁'을 검색해 보세요" in cm2 and "주소" not in cm2, "주소가 없으면 주소 줄 생략, 조사도 받침에 맞게")
cm_b = build_first_comment("book", "", [], search_keyword="시골책방", address="강원 평창군", items=[{"short": "「시」", "link": "https://aladin.co.kr/x"}])
check("map.naver.com" not in cm_b and "카카오맵" in cm_b and "https://aladin.co.kr/x" in cm_b, "시골책방도 지도 URL 제거 (구매 링크는 유지)")

# ---------------------------------------------------------------- ② 독립 커버 이미지 thumbnail.jpg
# ---------------------------------------------------------------- 서버 재시작 없이도 새 코드가 반영되는지 (모듈 새로고침 목록)
import re as _re
app_src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
reload_block = app_src[app_src.index("RELOAD_MODULES = ("):app_src.index("for _m in RELOAD_MODULES:")]
listed = set(_re.findall(r'"((?:core|ui)\.[A-Za-z0-9_.]+)"', reload_block))
imported = set(_re.findall(r"^from ((?:core|ui)\.[A-Za-z0-9_.]+) import", app_src, flags=_re.M))
NO_RELOAD = {"core.longform", "core.llm.router", "core.media.audio_stt"}      # 레지스트리·주입점을 가진 모듈
logic_modules = {m for m in imported if m not in NO_RELOAD}
# 화면 로직·렌더 로직·카피/태그/업로드 계층은 반드시 목록에 있어야 함 (빠지면 옛 코드가 서버에 남음)
must = {"core.render.bgm", "core.render.cover_clip", "core.render.ffmpeg_assemble", "core.compositor", "core.asset_generator", "core.hashtags", "core.shorts_meta",
        "core.seo_generator", "core.mode_policy", "core.pipeline", "ui.youtube_panel", "ui.longform_page"}
check(must <= listed, f"서버 새로고침 목록에 핵심 모듈 포함 (누락 {sorted(must - listed)})")
missing = sorted(m for m in logic_modules if m not in listed and m.split(".")[0] in ("core", "ui")
                 and m in {"core.pipeline", "core.compositor", "core.asset_generator", "core.analyzer", "core.script_guard",
                           "core.shorts_meta", "core.seo_generator", "ui.youtube_panel", "core.tts"})
check(not missing, f"app.py가 import하는 로직 모듈이 새로고침 목록에서 빠지지 않음 {missing}")

# 패널 호출은 모듈이 옛 버전이라도 TypeError 없이 (지원하는 인자만 전달)
ns = {"logging": logging}
exec(app_src[app_src.index("def _call_supported"):app_src.index("# 로깅 설정")], ns)
got = {}
ns["_call_supported"](lambda a, b, hook="": got.update(a=a, b=b, hook=hook), 1, 2, hook="h", address="주소", core_concept="x")
check(got == {"a": 1, "b": 2, "hook": "h"}, "옛 시그니처 함수에도 모르는 인자는 빼고 호출 (address 인자 불일치 TypeError 방지)")

# ---------------------------------------------------------------- ② 인트로 커버 제거 + 씬 1 자막 단일화 (실제 사장님 모드 mock 렌더)
import shutil as _sh
from core.job import JobContext
import core.mode_policy as _mp
W2 = tmp("store_fix2_")
try:
    job = JobContext.create(app="store_fix")
    job.root = W2
    job = JobContext(job.job_id, root=W2)
    ups = []
    for i, nm in enumerate(("photo_pool.jpg", "photo_room.jpg", "phone_portrait.mov", "phone_landscape.mp4")):
        srcp = fixture(nm)
        dst = os.path.join(job.uploads_dir, f"u{i}{os.path.splitext(srcp)[1]}")
        _sh.copy(srcp, dst)
        ups.append(dst)
    spec2 = pl.JobSpec(mode="owner", name=NAME, usp=USP, business_type="shop", search_keyword=NAME, address=ADDR, media_paths=ups, mock=True,
                       core_concept=CONCEPT, audio_mode="voice_and_sub")
    sc = pl.plan(spec2, job)
    check(sc[0].get("hook_title") and sc[0]["hook_title"] != sc[0]["voiceover"], f"(전제) 씬 1에 후킹 헤드라인이 있음: '{sc[0].get('hook_title')}'")
    hook_words = sc[0]["hook_title"].replace("?", "").split()

    def orange_px(path, t):
        fp = os.path.join(W2, f"o_{t}.png")
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", path, "-frames:v", "1", fp], check=True)
        a = np.asarray(Image.open(fp).convert("RGB")).astype(int)
        return int(((a[:, :, 0] > 220) & (a[:, :, 1] > 90) & (a[:, :, 1] < 200) & (a[:, :, 2] < 80)).sum()), a

    res_on = pl.render(spec2, job, sc)                          # 기본값: 0초 인트로 커버 ON
    cov = [orange_px(res_on.final_path, t)[0] for t in (0.0, 0.2, 0.4)]
    after = orange_px(res_on.final_path, 0.7)[0]
    check(all(c > 2000 for c in cov) and after < 200,
          f"기본값: 0.0~0.4초는 썸네일 커버(주황·노랑 큰 글자 {cov}px), 0.7초부터는 본문 화면 ({after}px)")
    wp = []
    for t in (0.7, 1.0, 1.6):
        _, arr = orange_px(res_on.final_path, t)
        wp.append(int((arr[int(1920 * 0.40):int(1920 * 0.60)].min(axis=2) > 235).sum()))
    check(all(w > 1500 for w in wp) and max(wp) < 2.0 * min(wp),
          f"커버 이후 씬 1 자막: 0.7/1.0/1.6초 모두 같은 본문 자막 (흰 글자 {wp}px, 후킹 조각으로 바뀌지 않음)")
    check(res_on.cover_path and os.path.basename(res_on.cover_path) == "thumbnail.jpg" and os.path.exists(res_on.cover_path),
          "업로드용 독립 커버 thumbnail.jpg도 함께 생성")
    on_copy = os.path.join(W2, "on_copy.mp4")           # 같은 job 폴더라 두 번째 렌더가 final.mp4를 덮어쓰므로 ON 결과를 따로 보관
    _sh.copy(res_on.final_path, on_copy)
    _mp.INTRO_COVER_FORCE = False
    try:
        res_off = pl.render(spec2, job, sc)
    finally:
        _mp.INTRO_COVER_FORCE = None
    off0, _ = orange_px(res_off.final_path, 0.0)
    from core.media.normalize import probe_media as _pm
    d_on, d_off = _pm(on_copy).duration, _pm(res_off.final_path).duration
    check(off0 < 200 and abs(d_on - d_off - 0.5) < 0.15,
          f"(대조) INTRO_COVER=0 이면 첫 프레임에 커버 없음 ({off0}px), 커버 ON은 앞에 0.5초만 더 붙음 ({d_on:.2f}s vs {d_off:.2f}s)")
    # 0초 커버는 완성 후 후처리: 본문(커버 뒤)은 커버 없는 렌더와 프레임·길이가 같고 나레이션도 0.5초 밀릴 뿐 그대로
    diffs = []
    for t in (0.1, 0.6, 1.2):
        _, a_on = orange_px(on_copy, t + 0.5)
        _, a_off = orange_px(res_off.final_path, t)
        diffs.append(float(np.abs(a_on - a_off).mean()))
    check(max(diffs) < 4.0, f"커버 뒤 본문 프레임 = 커버 없는 렌더와 동일 (차이 {[round(d, 1) for d in diffs]}) → 씬 1 자막·훅 오염 없음")
    check(os.path.exists(os.path.join(os.path.dirname(res_on.final_path), "final_clean.mp4")),
          "커버 붙이기 전 깨끗한 원본이 final_clean.mp4로 보관됨")
finally:
    shutil.rmtree(W2, ignore_errors=True)


W = tmp("store_fix_")
try:
    big = os.path.join(W, "final_thumb.png")
    Image.fromarray(np.random.default_rng(1).integers(0, 255, (1920, 1080, 3), dtype=np.uint8)).save(big)
    cover = yu.export_cover_jpg(big)
    check(os.path.basename(cover) == "thumbnail.jpg" and os.path.dirname(cover) == os.path.dirname(big) and os.path.getsize(cover) <= yu.THUMB_MAX_BYTES
          and Image.open(cover).format == "JPEG" and Image.open(cover).size == (1080, 1920) and os.path.exists(big),
          f"독립 커버 이미지: {os.path.basename(cover)} ({os.path.getsize(cover) // 1024}KB, 원본 PNG 유지)")
    check(yu.thumbnail_for_upload(big) == cover and yu.thumbnail_for_upload(cover) == cover, "업로드는 항상 이 커버 이미지를 사용")

    # 업로드 모듈 바인딩: thumbnails.set에 실제로 전달되는 파일 (업로드 스위트가 전 구간을 가짜 서비스로 검증)
    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(yu.thumbnail_for_upload(big))
    check(media._filename.endswith("thumbnail.jpg"), "thumbnails.set 미디어 = thumbnail.jpg")

    # 롱폼 쇼츠 커버: 후보 창 중간 프레임 + 컨셉 카피가 얹힌 이미지
    from core.longform import make_cover
    from core.planners.highlight_selector import HighlightCandidate
    src = os.path.join(W, "src.mp4")
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=15:d=30", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", src], check=True)
    cand = HighlightCandidate(start_sec=5.0, end_sec=22.0, cta_sec=3.0, track="A", coverage=0.8, caption_scenes=[], flags=[],
                              music_suspected=False)
    out_dir = os.path.join(W, "final")
    cv = make_cover(src, cand, out_dir, concept="공구 없이 손으로 빠른 연마제 교체", store_name="HKG")
    plain = os.path.join(W, "plain.png")
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-ss", "13.5", "-i", src, "-frames:v", "1", "-vf",
                    "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", plain], check=True)
    a = np.asarray(Image.open(cv).convert("RGB"), dtype=np.int16)
    b = np.asarray(Image.open(plain).convert("RGB").resize((a.shape[1], a.shape[0])), dtype=np.int16)
    check(cv and os.path.basename(cv) == "thumbnail.jpg" and Image.open(cv).size == (1080, 1920) and os.path.exists(os.path.join(out_dir, "final_thumb.png"))
          and float(np.abs(a - b).mean()) > 6.0, f"롱폼 커버: 원본 프레임에 카피 오버레이가 적용된 thumbnail.jpg (평균 차이 {float(np.abs(a - b).mean()):.1f})")

    # 롱폼 쇼츠도 0초 커버 + 같은 길이 (화면만 덮음)
    from core.longform import render_candidate
    cand2 = HighlightCandidate(start_sec=5.0, end_sec=21.0, cta_sec=3.0, track="A", coverage=0.8,
                               caption_scenes=[{"start": 0.3, "end": 7.0, "text": "주문하면 바로 무쳐요"}], flags=[], music_suspected=False)
    lf_out = os.path.join(W, "lf", "short.mp4")
    r_lf = render_candidate(src, cand2, lf_out, store_name="오늘식탁", concept="주문 즉시 무쳐내는 겉절이", voice_fn=lambda sc: None,
                            work_dir=W)

    def fr(path, t):
        fp = os.path.join(W, f"lf_{t}.png")
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", path, "-frames:v", "1", fp], check=True)
        return np.asarray(Image.open(fp).convert("RGB"), dtype=np.int16)

    f0, f7 = fr(lf_out, 0.0), fr(lf_out, 0.8)
    cover_img = np.asarray(Image.open(r_lf["cover_path"]).convert("RGB").resize((1080, 1920)), dtype=np.int16)
    check(r_lf["cover_path"] and float(np.abs(f0 - cover_img).mean()) < 12 and float(np.abs(f0 - f7).mean()) > 20,
          f"롱폼: 0초 프레임이 커버 이미지와 같고 0.8초부터 본문 (커버 차이 {float(np.abs(f0 - cover_img).mean()):.1f}, 전환 차이 {float(np.abs(f0 - f7).mean()):.1f})")

    # 업로드 패널 안내: 쇼츠는 0초 프레임이 대표 썸네일
    from streamlit.testing.v1 import AppTest

    def _panel():
        import os as _o
        from ui.youtube_panel import render_youtube_panel
        render_youtube_panel(_o.environ["_SF_VIDEO"], _o.environ["_SF_THUMB"], "owner", hook="", link="", disclosure="",
                             business_name="오늘식탁", search_kw="오늘식탁")

    os.environ["_SF_VIDEO"], os.environ["_SF_THUMB"] = lf_out, os.path.join(os.path.dirname(lf_out), "final_thumb.png")
    at = AppTest.from_function(_panel).run()
    msg_on = " ".join(i.value for i in at.info)
    check("첫 프레임(0초)이 대표 썸네일로 자동 지정" in msg_on and "0.5초" in msg_on and not at.exception,
          "패널 안내(커버 ON): 쇼츠는 첫 프레임(0초)이 대표 썸네일, 첫 0.5초에 커버가 들어감")
    import core.mode_policy as _mp2
    _mp2.INTRO_COVER_FORCE = False
    try:
        at2 = AppTest.from_function(_panel).run()
    finally:
        _mp2.INTRO_COVER_FORCE = None
    check("인트로 커버가 꺼져" in " ".join(w.value for w in at2.warning), "패널 경고(커버 OFF): 첫 화면이 그대로 대표 화면이 됨")

    # ---------------------------------------------------------------- ① 직가공 렌더러도 훅 분리 없음: 본문 자막만 0.0초부터
    from core.renderers.video_overlay_renderer import render_video_overlay
    black = os.path.join(W, "black.mp4")
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=15:d=6", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", black], check=True)       # 노란 후킹 픽셀을 정확히 세기 위해 단색 배경 사용
    out = os.path.join(W, "overlay.mp4")
    render_video_overlay(black, [{"start_sec": 0.0, "end_sec": 4.0, "hook_title": "이거 모르면 손해예요", "caption": "결제할 때 카드 한 장이면 돼요"}],
                         out, audio_mode="mute", fit_mode="crop")
    fr = os.path.join(W, "f.png")
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-ss", "0.3", "-i", out, "-frames:v", "1", fr], check=True)
    arr = np.asarray(Image.open(fr).convert("RGB"))
    yellow = int(((arr[:, :, 0] > 220) & (arr[:, :, 1] > 180) & (arr[:, :, 2] < 70)).sum())
    white = int((arr[int(1920 * 0.40):int(1920 * 0.60)].min(axis=2) > 235).sum())
    check(white > 1500 and yellow < 300, f"직가공: 0.3초에 후킹 헤드라인 없이 본문 자막이 바로 나옴 (흰 {white}px, 노랑 {yellow}px)")

finally:
    shutil.rmtree(W, ignore_errors=True)
finish(fails)
