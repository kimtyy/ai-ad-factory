"""
app.py - AI Short-form Ad Video Factory 반응형 Streamlit 웹 대시보드
쇼핑몰 크롤링, 누끼 추출, 톤앤매너/길이/성우 선택, 실시간 콘티 편집 및 9:16 광고 영상 원클릭 렌더링
"""
import os
import re
import sys

# Streamlit Community Cloud(메모리 약 1GB)에서는 numpy·OpenCV·onnx가 코어 수만큼 스레드를 만들어 메모리를 키우므로 1개로 제한 (numpy import보다 먼저)
if os.path.exists("/mount/src") or os.getenv("RENDER_LOW_MEMORY", "").strip() == "1":
    for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "OPENCV_FOR_THREADS_NUM"):
        os.environ.setdefault(_v, "1")
import html
import json
import math
import hashlib
import logging
import requests
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Windows 터미널 한글/유니코드 인코딩 안전화
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 환경 변수 로드
load_dotenv()

import importlib
import core                                   # noqa: F401 - 아래 코드가 `core.scraper` 같은 점 표기로 접근하므로 이름과 하위 모듈을 바인딩해 둠
import core.scraper, core.compositor, core.analyzer, core.asset_generator, core.tts, core.audio, core.seo_generator  # noqa: E401,F401
import core.mode_policy, core.pipeline, core.planners, core.planners.owner, core.planners.travel, core.planners.commerce  # noqa: E401,F401
import core.renderers, core.renderers.video_overlay_renderer, core.script_guard  # noqa: E401,F401

# ---- 개발 중 서버를 재시작하지 않아도 코드 수정이 바로 반영되도록, 로직 모듈을 의존 순서대로 다시 읽는다.
# (Streamlit은 한 번 import한 모듈을 세션 사이에 유지한다. 목록에서 빠진 모듈은 옛 코드가 남아, app.py만 새 인자를 넘기다가
#  TypeError가 나거나 일부 모듈만 새 동작을 하는 문제(훅 자막·인트로 커버가 되살아남)가 생긴다.)
# 제외: core.longform(백그라운드 분석 작업 레지스트리 보관), core.llm.router·core.media.audio_stt(테스트 주입점·캐시 상태),
# core.youtube_upload(채널 토큰·경로 설정과 서비스 팩토리를 런타임에 교체하는 설정 모듈 — 코드를 바꾸면 서버를 재시작)
RELOAD_MODULES = (
    # 렌더 보조 모듈: 자막 분할·폰트·브랜드 카드·이미지 유틸은 컴포지터가 호출 시점에 import하므로, 목록에서 빠지면 옛 분할 함수가 그대로 쓰임 (실제 영상에서 확인된 문제)
    "core.affiliate_ota", "core.naver_local", "core.media.imgutil", "core.media.normalize",
    "core.render.caption_chunks", "core.render.fonts", "core.render.brand", "core.render.commerce_layout", "core.shoot_guide", "core.scene_images",
    "core.script_guard", "core.store_facts", "core.store_research", "core.photo_safety", "core.store_photos", "core.stay_curation", "core.stay_match", "core.stay_photos", "core.planners.stay", "core.concepts", "core.naver_shop", "core.planners.shop", "core.tts", "core.mode_policy", "core.hashtags", "core.shorts_meta", "core.seo_generator",
    "core.scraper", "core.render.bgm", "core.render.cover_clip", "core.compositor", "core.render.ffmpeg_assemble", "core.analyzer", "core.asset_generator", "core.audio",
    "core.planners.owner", "core.planners.travel", "core.planners.commerce", "core.planners.commerce_bundle",
    "core.planners.book_fable", "core.planners.book_reflect", "core.planners.book", "core.planners",
    "core.pipeline", "core.renderers.video_overlay_renderer", "core.renderers.highlight_renderer", "core.renderers",
    "ui.youtube_panel", "ui.longform_page",
)
for _m in RELOAD_MODULES:
    try:
        importlib.reload(importlib.import_module(_m))
    except Exception as _e:          # 한 모듈의 import 오류가 앱 전체를 막지 않게 (원래 import 단계에서 다시 드러남)
        import logging as _lg
        _lg.getLogger("ad_factory.app").warning(f"[모듈 새로고침 실패] {_m}: {_e}")

from core.analyzer import (plan_storyboard, TONE_PRESETS, DURATION_PRESETS, TRAVEL_TEMPLATES, DEFAULT_TRAVEL_TEMPLATE,
                           plan_video_overlay_storyboard, analyze_video_for_overlay, split_video_timeline)
from core.renderers.video_overlay_renderer import render_video_overlay
from core.seo_generator import generate_seo_metadata
from core.asset_generator import AssetGenerator, VOICE_ACTOR_PROFILES
from core.compositor import AdCompositor
from core.scraper import scrape_product_info
from core.job import JobContext, cleanup_old_jobs
from core.media import is_video_file, probe_media, extract_thumbnail
from core.coupang import SEARCH_LIMIT_PER_HOUR, CoupangAPIError, CoupangPartners
from core.media.library import MAX_MEDIA, add_media, move_media, remove_media, reorder_media
from core.tts import owner_readings
from core.naver_local import NaverAPIError, NaverLocalSearch, is_naver_link, place_id_from_link, region_of, fetch_place_description
from core.affiliate_ota import OTA_DISCLOSURE, OTA_LABEL, ota_disclosure_full
from ui.copy_button import copy_button
from ui.youtube_panel import render_youtube_panel
from core.render import bgm as bgm_mod
from core.shorts_meta import make_shorts_copy
from core.radar.trends import digest_for
from core.planners import (BUSINESS_TYPES, COUPANG_DISCLOSURE, COUPANG_DISCLOSURE_FULL, DEFAULT_BUSINESS_TYPE,
                           analyze_product_photo, plan_commerce_storyboard, plan_owner_storyboard, suggest_store_points,
                           suggest_ad_concepts)
from core.render.commerce_layout import SHOT_LABELS, assign_shots
from core.planners.book import TEMPLATES as BOOK_TEMPLATES, plan_book_storyboard
from core.planners.commerce_bundle import (BUNDLE_SIZE, DEFAULT_BOTTOM_BAR, bundle_title_ok, plan_bundle_storyboard,
                                           short_name as bundle_short_name)
from core.planners.travel import plan_travel_storyboard
from core.radar.hints import load_hook_hints, vertical_for
from core.radar.hooks import HOOK_TYPE_KO
from ui.radar_page import render_radar_page, RADAR_PERSIST_KEYS, init_radar_state, hints_enabled, allow_dryrun_hints
from ui.longform_page import render_longform_page, LONGFORM_PERSIST_KEYS
from core.planners.owner import ROLE_LABELS_KO as OWNER_ROLE_LABELS
from core.media.normalize import DEFAULT_TRIM_START
from core.render.brand import BrandOverlay, EndCard
from core import pipeline

def _call_supported(fn, *args, **kwargs):
    """fn이 받지 않는 키워드 인자는 빼고 호출 (옛 버전 모듈이 남은 서버에서도 TypeError 대신 동작). 빠진 인자는 로그로 남김"""
    import inspect
    try:
        params = inspect.signature(fn).parameters
        if not any(p.kind == p.VAR_KEYWORD for p in params.values()):
            dropped = [k for k in kwargs if k not in params]
            if dropped:
                logging.getLogger("ad_factory.app").warning(f"{getattr(fn, '__name__', fn)}가 받지 않는 인자 생략(서버 재시작 필요): {dropped}")
                kwargs = {k: v for k, v in kwargs.items() if k in params}
    except (TypeError, ValueError):
        pass
    return fn(*args, **kwargs)


# 로깅 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ad_factory.app")

# Streamlit 와이드 반응형 레이아웃 설정
st.set_page_config(
    page_title="AI 광고 공장 - 숏폼 자동 제작 대시보드",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="auto"  # 데스크톱은 펼침, 모바일은 접힘 (모바일에서 펼치면 본문을 덮음)
)

# 색상/폰트는 .streamlit/config.toml 테마가 담당. 아래 CSS는 key로 지정한 요소(st-key-*)의 레이아웃만 다룸
st.markdown("""<style>
.st-key-scene_carousel{overflow-x:auto;scroll-snap-type:x mandatory;flex-wrap:nowrap!important;padding-bottom:8px}
.st-key-scene_carousel>div{flex:0 0 240px!important;width:240px!important;min-width:240px;scroll-snap-align:start}
@media (max-width:640px){.st-key-scene_carousel>div{flex-basis:78vw!important;width:78vw!important;min-width:78vw}}
.st-key-render_cta button, .st-key-place_fetch button, .st-key-download_cta button, div[data-testid="stButton"] button[kind="primary"] {
    min-height: 50px;
    font-size: 1.05rem !important;
    font-weight: 800 !important;
    border-radius: 12px !important;
    background: linear-gradient(135deg, #FF4B4B 0%, #FF6B35 100%) !important;
    border: none !important;
    color: #ffffff !important;
    box-shadow: 0 4px 14px rgba(255, 75, 75, 0.35) !important;
    transition: all 0.2s ease-in-out !important;
}
.st-key-render_cta button:hover, .st-key-place_fetch button:hover, .st-key-download_cta button:hover, div[data-testid="stButton"] button[kind="primary"]:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 6px 20px rgba(255, 75, 75, 0.5) !important;
}
.st-key-phone_frame{max-width:340px;margin:0 auto;border:3px solid rgba(139,92,246,.45);border-radius:32px;padding:14px 10px;background:#000}
</style>""", unsafe_allow_html=True)

# Streamlit 전역 단축키 핸들러의 key 없는 KeyboardEvent(자동완성·IME·확장) toLowerCase 에러 차단 (PIN 입력창보다 먼저)
from ui.key_guard import install_key_guard
install_key_guard()

# 외부 접속(Cloudflare 터널·다른 기기)은 PIN 통과 전까지 아래 앱(작업 폴더 생성·API 호출 포함)을 실행하지 않음
from ui.auth import require_pin
from ui.server_data import render_server_data_panel
require_pin()

# 세션 상태 초기화
if "storyboard" not in st.session_state:
    st.session_state.storyboard = []
if "scraped_data" not in st.session_state:
    st.session_state.scraped_data = None
if "cutout_path" not in st.session_state:
    st.session_state.cutout_path = None
# 숙소·쇼핑 상품명/특장점 입력칸 값 (위젯 key와 직접 연결: value= 인자만 바꾸면 브라우저가 보낸 기존 빈 값이 이겨서 반영 안 됐음)
st.session_state.setdefault("product_name_input", "")
st.session_state.setdefault("product_usp_input", "")
# 촬영 영상 직가공 모드 전용 세션 기본값 보장 (다른 모드에서도 안전하게 기본 문자열 보유)
st.session_state.setdefault("video_audio_opt", "배경음으로 작게 유지 (볼륨 20%)")
st.session_state.setdefault("video_sound_option", "배경음으로 작게 유지 (볼륨 20%)")
st.session_state.setdefault("video_fit_opt", "9:16 중앙 포커스 크롭 (기본)")
st.session_state.setdefault("crop_option", "9:16 중앙 포커스 크롭 (기본)")
st.session_state.setdefault("video_overlay_name", "")
st.session_state.setdefault("video_overlay_usp", "")
st.session_state.setdefault("video_overlay_type", DEFAULT_BUSINESS_TYPE)


def set_product_info(data):
    """크롤링·쿠팡 선택 결과를 저장하고 상품명·특장점 입력칸에 바로 채움 (입력칸이 그려지기 전에 호출해야 함)"""
    st.session_state.scraped_data = data
    st.session_state["product_name_input"] = (data or {}).get("product_name", "") or ""
    st.session_state["product_usp_input"] = (data or {}).get("usp", "") or ""
if "image_pool" not in st.session_state:
    st.session_state.image_pool = []
if "gallery_images" not in st.session_state:
    st.session_state.gallery_images = []
if "production_mode" not in st.session_state:
    st.session_state.production_mode = "owner"
if "final_video_path" not in st.session_state:
    st.session_state.final_video_path = None
if "thumb_path" not in st.session_state:
    st.session_state.thumb_path = None        # 렌더와 함께 만든 썸네일 (final_thumb.png)
if "bgm_status" not in st.session_state:
    st.session_state.bgm_status = None        # 마지막 렌더에 실제로 들어간 BGM (완료 화면 표시용)
if "shorts_copy" not in st.session_state:
    st.session_state.shorts_copy = None       # 업로드 제목·썸네일 카피 (core/shorts_meta.py)
if "seo_metadata" not in st.session_state:
    st.session_state.seo_metadata = None      # 쇼츠 SEO 메타데이터 (제목·설명·태그 목록, core/seo_generator.py)
if "generated_images" not in st.session_state:
    st.session_state.generated_images = []
if "script_style" not in st.session_state:
    st.session_state.script_style = "info"
if "rejected_urls" not in st.session_state:
    st.session_state.rejected_urls = set()   # 사용자가 거부한 사진 URL (재선별 시 재등장 금지)
if "vision_cache" not in st.session_state:
    st.session_state.vision_cache = {}       # 비전 판정 캐시 (이미지 해시:역할 -> 판정)
if "affiliate_link" not in st.session_state:
    st.session_state.affiliate_link = ""     # 쿠팡 파트너스·아고다·트립닷컴 제휴 링크 (영상 설명·고정 댓글용)
if "affiliate_program" not in st.session_state:
    st.session_state.affiliate_program = ""  # 'agoda' / 'trip' (숙소 제휴 고지·링크 표시용)
if "coupang_results" not in st.session_state:
    st.session_state.coupang_results = []
if "uploaded_paths" not in st.session_state:
    st.session_state.uploaded_paths = []     # 일괄 업로드 사진/영상 (씬 1부터 순서대로 배정)
@st.cache_resource(show_spinner=False)
def _startup_cleanup():
    """서버 프로세스 시작 시 1회: 24시간 넘게 아무것도 저장되지 않은 빈 작업 폴더 정리 (결과물이 있는 폴더는 보존)"""
    return cleanup_old_jobs(empty_only=True, max_age_hours=24)


_startup_cleanup()

if "job" not in st.session_state:
    # 브라우저 세션마다 독립 작업 공간 (outputs/jobs/{job_id}/) - 동시 사용자 간 파일 충돌 방지
    # 폴더는 첫 저장 시점에 생성됨 (접속만 한 세션은 빈 폴더를 남기지 않음)
    st.session_state.job = JobContext.create(app="streamlit")

UPLOAD_TYPES = ["jpg", "jpeg", "png", "mp4", "mov", "m4v"]


def current_job() -> JobContext:
    return st.session_state.job


def start_new_job(**meta) -> JobContext:
    """새 작업 공간을 만들고 이전 작업의 씬/에셋 상태를 비움 (이전 작업 파일은 그대로 보존)"""
    st.session_state.job = JobContext.create(app="streamlit", **meta)
    st.session_state.storyboard = []
    st.session_state.uploaded_paths = []
    st.session_state.rejected_urls = set()
    st.session_state.final_video_path = None
    st.session_state.thumb_path = None
    st.session_state.shorts_copy = None
    st.session_state.seo_metadata = None
    st.session_state.bgm_status = None
    st.session_state.generated_images = []
    st.session_state.cutout_path = None
    st.session_state.affiliate_link = ""      # 이전 상품의 파트너스 링크가 남지 않게
    st.session_state.affiliate_program = ""
    st.session_state.usp_auto = False
    st.session_state.product_boxes = {}
    for k in [k for k in st.session_state.keys() if str(k).startswith("applied_upload_")]:
        del st.session_state[k]
    return st.session_state.job


def _safe_int(value, default: int) -> int:
    """data_editor 신규 행의 None/NaN도 안전하게 정수로 변환"""
    try:
        f = float(value)
        return default if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return default


def _scene_num(scene: dict, default: int) -> int:
    return _safe_int(scene.get("scene_index") or scene.get("scene_number"), default)


def invalidate_scene_derivatives(s_num: int):
    """씬 사진/영상이 바뀌면 그 씬의 파생 키프레임/클립 캐시만 삭제 (core/pipeline.py)"""
    pipeline.invalidate_scene_derivatives(current_job(), s_num)


def save_uploaded_file(up_file) -> str:
    """업로드 사진/영상을 작업 공간 uploads/에 내용 해시 기반 고유 파일명으로 저장 (스마트폰 세로 사진 EXIF 자동 정방향 회전)"""
    if hasattr(up_file, "getvalue"):
        data = up_file.getvalue()
    elif hasattr(up_file, "read"):
        data = up_file.read()
        if hasattr(up_file, "seek"):
            up_file.seek(0)
    elif isinstance(up_file, (bytes, bytearray)):
        data = bytes(up_file)
    else:
        data = b""

    fname = getattr(up_file, "name", "") or ""
    ext = (os.path.splitext(fname)[-1] or "").lower() or ".jpg"
    path = os.path.join(current_job().uploads_dir, f"upload_{hashlib.md5(data).hexdigest()[:12]}{ext}")
    if not os.path.exists(path):
        if ext in (".jpg", ".jpeg", ".png", ".webp"):
            try:
                from PIL import Image, ImageOps
                import io
                from core.media.imgutil import open_rgb_downscaled
                resized = open_rgb_downscaled(io.BytesIO(data))        # 올리는 즉시 긴 변 2560px 이하로 줄여 저장 (12MP 원본이 메모리·디스크에 쌓이지 않게)
                if ext == ".png":
                    resized.save(path, optimize=False)
                else:
                    resized.save(path, quality=92)
            except Exception:
                with open(path, "wb") as f:
                    f.write(data)
        else:
            with open(path, "wb") as f:
                f.write(data)
    return path


scene_has_media = pipeline.scene_has_media


def assign_media_to_scene(scene: dict, path: str, source: str, matched_url: str = None):
    """씬 dict에 사진 또는 스마트폰 영상을 키로 기록 (core/pipeline.py)"""
    pipeline.assign_media_to_scene(current_job(), scene, path, source, matched_url)


def assign_uploads_to_scenes(scenes: list, paths: list, cycle: bool = False):
    """i번째 미디어 → i번째 씬. cycle=True(쇼핑)면 사진이 씬보다 적을 때 앵글을 바꿔 돌려 씀 (core/pipeline.py)"""
    pipeline.assign_uploads_to_scenes(current_job(), scenes, paths, cycle=cycle,
                                      product_boxes=st.session_state.get("product_boxes", {}))


def _media_changed(new_paths: list):
    """영상 소스 목록 변경 반영: 이미 기획된 콘티가 있으면 씬 사진을 새 순서로 다시 배정"""
    st.session_state.uploaded_paths = new_paths
    if st.session_state.storyboard:
        assign_uploads_to_scenes(st.session_state.storyboard, new_paths, cycle=st.session_state.production_mode == "commerce")
        st.session_state.media_stale = st.session_state.production_mode == "owner"


def _media_thumb(path: str):
    """목록용 미리보기: 사진은 EXIF 정방향 보정, 영상은 대표 프레임(+길이)"""
    if not is_video_file(path):
        try:
            from core.media.imgutil import PREVIEW_SIDE, open_rgb_downscaled
            return open_rgb_downscaled(path, PREVIEW_SIDE), ""
        except Exception:
            return path, ""
    thumb = os.path.splitext(path)[0] + "_thumb.jpg"
    try:
        if not os.path.exists(thumb):
            extract_thumbnail(path, thumb, at=0.8)
        return thumb, f"🎬 영상 {probe_media(path).duration:.1f}초"
    except Exception:
        return None, "🎬 영상"


def render_media_manager():
    """
    영상 소스(사진·영상) 관리: 위에서부터 씬 순서. 자동으로 가져온 사진과 직접 올린 사진·영상을 한 목록에서
    ⬆⬇ 순서 변경 · 🗑 삭제 · ➕ 추가 (업로드는 목록을 덮어쓰지 않고 뒤에 붙임)
    """
    paths = [p for p in st.session_state.uploaded_paths if os.path.exists(p)]
    if paths != st.session_state.uploaded_paths:
        st.session_state.uploaded_paths = paths
    with st.container(border=True, key="media_manager"):
        if st.session_state.get("production_mode", "owner") == "owner":
            st.checkbox("📌 내가 올린 사진 순서 그대로 유지 (AI 자동 정렬 끄기)", key="keep_photo_order",
                        on_change=lambda: st.session_state.__setitem__("media_stale", True),
                        help="끄면(기본): 사진을 어떤 순서로 올려도 AI가 먹음직한 하이라이트를 첫 컷에, 가게 외관을 마지막 컷에 자동으로 정렬해요. "
                             "켜면: 올린 순서(1번~N번) 그대로 컷을 만들고, 첫 번째 사진이 첫 컷과 썸네일이 돼요. 사진 분류 호출도 생략해 더 빨라요.")
        if st.session_state.get("production_mode", "owner") == "owner" and st.session_state.get("selected_core_concept", "").strip():
            from core.shoot_guide import shoot_guide
            _g = shoot_guide(st.session_state.get("selected_core_concept", ""), st.session_state.get("owner_type", ""),
                             st.session_state.get("owner_name", ""), st.session_state.get("owner_usp", ""))
            if _g:
                st.info(_g["text"])
        st.markdown(f"**📸 영상 소스 {len(paths)}개** · 위에서부터 씬 순서 (최대 {MAX_MEDIA}개)")
        if st.session_state.pop("media_stale", False):
            st.info("사진·영상 구성이 바뀌었어요. 씬 수까지 맞추려면 [🚀 스토리보드 자동 기획]을 다시 눌러 주세요.")
        for i, p in enumerate(paths):
            tag = hashlib.md5(p.encode()).hexdigest()[:8]
            c_pos, c_img, c_info, c_up, c_down, c_del = st.columns([0.85, 1.1, 2.3, 0.45, 0.45, 0.45], vertical_alignment="center")
            order_choice = c_pos.selectbox("순서", options=list(range(1, len(paths) + 1)), index=i,
                                           format_func=lambda x: str(x or ""),
                                           key=f"media_pos_{tag}_{i}", label_visibility="collapsed",
                                           help=f"현재 {i+1}번째. 원하는 순서 번호를 선택하면 즉시 이동합니다.")
            if order_choice - 1 != i:
                _media_changed(reorder_media(paths, i, order_choice - 1))
                st.rerun()
            thumb, label = _media_thumb(p)
            if thumb:
                c_img.image(thumb, width=88)
            c_info.caption(label or os.path.basename(p)[:28])
            if c_up.button("⬆", key=f"media_up_{tag}", disabled=i == 0, help="한 칸 앞으로"):
                _media_changed(move_media(paths, i, -1))
                st.rerun()
            if c_down.button("⬇", key=f"media_down_{tag}", disabled=i == len(paths) - 1, help="한 칸 뒤로"):
                _media_changed(move_media(paths, i, +1))
                st.rerun()
            if c_del.button("🗑", key=f"media_del_{tag}", help="목록에서 빼기"):
                _media_changed(remove_media(paths, i))
                st.rerun()
        nonce = st.session_state.get("media_uploader_nonce", 0)
        curr_mode = st.session_state.get("production_mode", "owner")
        new_files = st.file_uploader(
            "➕ 내 폰 사진·영상 추가 (JPG/PNG/MP4/MOV)", accept_multiple_files=True, type=UPLOAD_TYPES,
            key=f"file_uploader_{curr_mode}_{nonce}", disabled=len(paths) >= MAX_MEDIA,
            help="영상은 자동으로 9:16 세로 규격화·무음 처리되어 그대로 영상 장면으로 들어갑니다. 올린 파일은 목록 맨 뒤에 붙어요."
        )
        if new_files:
            saved = [save_uploaded_file(f) for f in new_files]
            added = add_media(paths, saved)
            st.session_state.media_uploader_nonce = nonce + 1      # 업로더 비우기 (다음 실행에서 같은 파일을 다시 넣지 않게)
            if added != paths:
                _media_changed(added)
                st.toast(f"📱 사진·영상 {len(added) - len(paths)}개 추가!", icon="✨")
            st.rerun()


def rematch_scenes(target_scenes: list, api_key: str):
    """지정 씬만 재선별. 거부 목록 + 다른 씬이 쓰는 사진은 제외하여 같은 사진 반복을 막음"""
    target_ids = {id(s) for s in target_scenes}
    in_use = {
        s.get("matched_image_url") for s in st.session_state.storyboard
        if id(s) not in target_ids and s.get("matched_image_url")
    }
    return core.scraper.download_targeted_scene_assets(
        scenes=target_scenes,
        image_pool=st.session_state.get("image_pool", []),
        output_dir=current_job().assets_dir,
        api_key=api_key,
        exclude_urls=set(st.session_state.rejected_urls) | in_use,
        vision_cache=st.session_state.vision_cache
    )


# ==============================================================================
# 제작 모드 = 비즈니스 파이프라인
#   owner    (트랙 B) 사장님 직영 홍보: 업로드 미디어 기반, 상호명 노출, 네이버 검색 CTA, 브랜드 오버레이
#   travel   (트랙 A) 숙소 제휴 쇼츠: URL 크롤링 + 비전 매칭, 숙소명 은닉, 댓글 유도 CTA
#   commerce (트랙 A) 쇼핑 제휴 쇼츠(쿠팡 파트너스): 판매 페이지 실물 사진/직접 올린 사진 + 리뷰 썰 대본 + 제휴 표시
# ==============================================================================
# 🎯 점주 시연 모드: 매장 홍보 탭만 보이는 단일 목적 앱 (점주 미팅·강의 시연용). 켜는 법: 사이드바 하단 체크박스 / 환경변수 DEMO_MODE=1 / 주소 ?demo=1
try:
    _demo_q = str(st.query_params.get("demo", "")).lower() in ("1", "true", "on", "yes")
except Exception:
    _demo_q = False
st.session_state.setdefault("demo_mode", os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true", "on", "yes") or _demo_q)
DEMO_MODE = bool(st.session_state.get("demo_mode"))
MODES = {"🏠 사장님 매장 홍보": "owner", "🧳 숙소 제휴 쇼츠": "travel", "🛒 쇼핑 제휴 쇼츠": "commerce",
         "📚 시골책방 도서 큐레이션": "book", "📹 촬영 영상 원본 직가공": "video_overlay"}
if DEMO_MODE:
    MODES = {k: v for k, v in MODES.items() if v == "owner"}
    st.session_state.production_mode = "owner"
    st.session_state.mode_switch = next(iter(MODES))
MODE_LABELS = {v: k for k, v in MODES.items()}
SCENE_ROLE_LABELS = {**OWNER_ROLE_LABELS, "hook": "🔥 후킹", "feature": "✨ 특장점", "facility": "☕ 부대시설",
                     "room": "🛏 객실", "cta": "📍 CTA", "hero": "🛒 첫인상", "usage": "🙌 사용 장면", "body": "💡 본문"}


# ---- 모드별 최적 스타일 프리셋: 모드 탭을 누르는 순간 오디오·폰트·자막 위치가 자동으로 맞춰진다 (사이드바를 열 필요 없음)
# 사용자가 직접 바꾸면 그 모드의 선택으로 기억해 두고, 다음에 그 모드로 돌아오면 프리셋 대신 기억한 값을 쓴다
MODE_PRESETS = {
    "owner": {"audio": "voice_and_sub", "font": "impact", "pos": "mid"},        # 🏪 AI 나레이션 + 임팩트 고딕 + 중단 55%
    "travel": {"audio": "bgm_only", "font": "serif", "pos": "top"},             # 🏨 BGM + 감성 명조 + 상단 24%
    "commerce": {"audio": "voice_and_sub", "font": "pop", "pos": "mid"},        # 🛍️ AI 나레이션 + 둥근 팝체 + 중단 55%
    "book": {"audio": "bgm_only", "font": "serif", "pos": "mid"},               # 📚 BGM + 감성 명조 + 중단 55%
    "video_overlay": {"audio": "voice_and_sub", "font": "impact", "pos": "mid"},
}


def _style_now() -> dict:
    ss = st.session_state
    return {"audio": ss.get("audio_choice"), "font": ss.get("caption_font_choice"), "pos": ss.get("caption_pos")}


def _remember_style():
    """사용자가 오디오·폰트·자막 위치를 직접 바꿨을 때 (on_change): 지금 모드의 선택으로 저장"""
    ss = st.session_state
    ss.setdefault("style_by_mode", {})[ss.get("production_mode", "owner")] = _style_now()


def _apply_mode_style(mode: str):
    """그 모드에서 사용자가 고른 값이 있으면 그대로, 없으면 프리셋을 오디오·폰트·자막 위치 위젯 값으로 장착"""
    ss = st.session_state
    st_ = (ss.get("style_by_mode") or {}).get(mode) or MODE_PRESETS.get(mode) or MODE_PRESETS["owner"]
    ss["audio_choice"], ss["caption_font_choice"], ss["caption_pos"] = st_["audio"], st_["font"], st_["pos"]


def reset_mode_session_state(prev_mode: str, new_mode: str):
    """
    제작 모드 전환 시 이전 모드의 업로드 파일, 비전 분석, 1.5단계 AI 컨셉, 스토리보드,
    고유 입력 폼 데이터를 완전히 초기화하여 캐시 오염을 원천 차단합니다.
    """
    logger.info(f"[Mode Switch] {prev_mode} -> {new_mode}: 세션 캐시 및 업로드 상태 완전 초기화")

    # 1. 업로드된 미디어 및 파일 업로더 초기화 (배너로 직가공 모드 전환 시엔 방금 올린 영상을 유지)
    if st.session_state.pop("_keep_uploads_on_switch", False):
        st.session_state.vision_cache = {}
    else:
        st.session_state.uploaded_paths = []
        st.session_state["uploaded_images"] = []
        st.session_state.image_pool = []
        st.session_state.gallery_images = []
        st.session_state.rejected_urls = set()
        st.session_state.vision_cache = {}
        st.session_state.media_uploader_nonce = st.session_state.get("media_uploader_nonce", 0) + 1

    # 2. Vision 분석 결과 및 1.5단계 AI 컨셉 제안/선택값 초기화
    st.session_state.suggested_concepts = []
    st.session_state.selected_concept_idx = 0
    st.session_state.selected_core_concept = ""
    st.session_state.custom_core_concept = ""
    st.session_state.store_points = []
    st.session_state.pop("ad_concepts", None)
    st.session_state.pop("selected_concept", None)
    st.session_state.pop("concept_radio_picker", None)

    # 3. 기존 생성된 대본, 스토리보드 및 렌더링 결과 초기화
    st.session_state.storyboard = []
    st.session_state.shorts_copy = None
    st.session_state.final_video_path = None
    st.session_state.thumb_path = None
    st.session_state.seo_metadata = None
    st.session_state.bgm_status = None
    st.session_state.generated_images = []
    st.session_state.book_comment = ""
    st.session_state.scraped_data = None
    st.session_state.affiliate_link = ""
    st.session_state.affiliate_program = ""
    st.session_state.coupang_results = []

    # 4. 이전 모드의 고유 폼 입력 필드들 안전 초기화 (None 방지)
    keys_to_clear = [
        # owner
        "owner_name", "owner_search_kw", "owner_usp", "owner_address", "owner_reading", "place_query", "naver_place_url", "store_research",
        # commerce
        "product_name_input", "product_usp_input", "commerce_layout", "bundle_theme", "bundle_bottom", "product_boxes",
        *[f"bundle_{f}_{k}" for f in ("name", "usp", "link") for k in range(3)],
        # book
        "book_store", "book_search_kw", "book_title", "book_author", "book_info", "book_quote", "book_link",
        "book_photo_count", "book_template", "book_publisher", "book_genre",
        # travel
        "travel_template", "target_url", "stay_ctx", "stay_top5", "stay_alias_input", "stay_price_input", "stay_people_input", "stay_manual_url",
        # video_overlay
        "video_overlay_name", "video_overlay_usp", "video_sound_option", "crop_option"
    ]
    for k in keys_to_clear:
        st.session_state.pop(k, None)
    for k in ("product_name_input", "product_usp_input", "owner_name", "owner_search_kw", "owner_usp", "owner_address", "place_query",
              "video_overlay_name", "video_overlay_usp", "book_store", "book_title", "book_author", "book_quote", "book_link", "shop_query", "shop_connect_link",
              "stay_manual_name", "stay_manual_link"):
        st.session_state[k] = ""                              # 이전 모드에서 입력한 상호·상품명이 다른 모드 입력칸에 남지 않게 (브라우저 위젯 값까지)
    for k in ("shop_item", "shop_cards", "shop_results", "shop_msg", "shop_link_pending", "stay_ctx", "stay_top5", "store_photo_candidates", "web_photo_paths"):
        st.session_state.pop(k, None)
    _apply_mode_style(new_mode)                               # 새 모드의 최적 스타일(오디오·폰트·자막 위치) 자동 장착

    # radio 위젯 등 문자열 연산 필요한 키들은 안전 기본값으로 재할당
    st.session_state["video_audio_opt"] = "배경음으로 작게 유지 (볼륨 20%)"
    st.session_state["video_sound_option"] = "배경음으로 작게 유지 (볼륨 20%)"
    st.session_state["video_fit_opt"] = "9:16 중앙 포커스 크롭 (기본)"
    st.session_state["crop_option"] = "9:16 중앙 포커스 크롭 (기본)"
    st.session_state["video_overlay_type"] = DEFAULT_BUSINESS_TYPE
    st.session_state["video_overlay_name"] = ""
    st.session_state["video_overlay_usp"] = ""


def _on_mode_change():
    # segmented_control은 선택된 항목을 다시 누르면 선택 해제(None)되므로 직전 모드로 복원
    current_sw = st.session_state.get("mode_switch")
    if not current_sw or current_sw not in MODES:
        st.session_state.mode_switch = MODE_LABELS.get(st.session_state.production_mode, list(MODES.keys())[0])
    else:
        new_mode = MODES.get(st.session_state.mode_switch, "owner")
        prev_mode = st.session_state.get("production_mode")
        if new_mode != prev_mode:
            st.session_state.production_mode = new_mode
            reset_mode_session_state(prev_mode, new_mode)


if st.session_state.production_mode not in MODE_LABELS:   # 이전 버전 세션 값("여행 상품 모드" 등) 정리
    st.session_state.production_mode = "travel" if "여행" in str(st.session_state.production_mode) else "owner"
if st.session_state.get("mode_switch") not in MODES:
    st.session_state.mode_switch = MODE_LABELS.get(st.session_state.production_mode, list(MODES.keys())[0])
pipeline_mode = st.session_state.production_mode
if "style_init" not in st.session_state:
    st.session_state.style_init = True
    _apply_mode_style(pipeline_mode)

# [안전장치] 콜백 타이밍 외에도 모드 변경이 감지되면 즉시 캐시 초기화
if "_prev_pipeline_mode" in st.session_state and st.session_state._prev_pipeline_mode != pipeline_mode:
    reset_mode_session_state(st.session_state._prev_pipeline_mode, pipeline_mode)
st.session_state._prev_pipeline_mode = pipeline_mode

# Streamlit은 이번 실행에 그려지지 않은 위젯의 상태를 지우므로, 다른 모드를 잠깐 다녀오면 사장님 입력값이 사라짐.
# 매 실행 시작에 키를 재할당해 정리 대상에서 제외 (Streamlit 공식 권장 방식)
for _k, _default in (("owner_type", DEFAULT_BUSINESS_TYPE), ("brand_watermark", True), ("brand_end_card", True),
                     ("page_switch", "make")):
    st.session_state.setdefault(_k, _default)   # 위젯 인자 대신 세션에서 기본값 설정 (세션 재할당과 함께 써도 경고 없음)
init_radar_state()
for _k in ("owner_name", "owner_type", "owner_search_kw", "owner_usp", "owner_address", "owner_reading", "place_query", "bgm_on", "bgm_choice",
           "owner_menu_text", "owner_review_text", "photo_tags", "photo_buckets", "store_research", "use_unconfirmed_facts", "keep_photo_order", "owner_tempo", "store_photo_candidates", "web_photo_paths", "official_only", "demo_mode", "caption_font_choice", "caption_font_book_init", "api_key_input", "mock_mode", "force_render", "brand_logo_path", "duration_choice",
           "voice_tone", "voice_actor_select_box", "audio_choice", "script_style_radio", "travel_template", "show_home", "stay_aff_template", "stay_partner_id", "stay_ctx", "stay_top5", "stay_mode", "stay_preset", "stay_aff_template", "stay_partner_id",
           "stay_watermark", "stay_alias_input", "stay_price_input", "stay_people_input", "stay_manual_url", "caption_pos",
           "product_name_input", "product_usp_input", "commerce_layout", "bundle_theme", "bundle_bottom",
           "book_store", "book_search_kw", "book_title", "book_author", "book_info", "book_quote", "book_link",
           "book_photo_count", "book_template",
           *[f"bundle_{f}_{k}" for f in ("name", "usp", "link") for k in range(3)],
           "brand_watermark", "brand_end_card", "brand_position", "brand_sub", "page_switch") + RADAR_PERSIST_KEYS + LONGFORM_PERSIST_KEYS:
    if _k in st.session_state:
        st.session_state[_k] = st.session_state[_k]

is_owner_mode = pipeline_mode == "owner"
is_travel_mode = pipeline_mode == "travel"
is_commerce_mode = pipeline_mode == "commerce"
is_book_mode = pipeline_mode == "book"
is_video_overlay_mode = pipeline_mode == "video_overlay"
uses_scene_media = pipeline_mode in ("owner", "travel", "commerce", "book")   # 씬별 실사 사진/영상 배정 UI를 쓰는 모드 (전 모드 실물만)

# ==============================================================================
# 주메뉴 (💻 PC: 좌측 바 / 📱 스마트폰: 화면 최하단 고정 바) + 홈 + ⚙️ 설정 팝업
#   좌측 바에는 홈·제작 모드·설정만 둔다. 작업 단계별 옵션(화자 시점·길이·오디오·성우·BGM·폰트·자막 위치)은 본문 1~5단계 안에 있다.
# ==============================================================================
NAV_ITEMS = [("home", "🏠 홈", "🏠"), ("owner", "🏪 매장 홍보", "🏪 매장"), ("travel", "🏨 숙소 홍보", "🏨 숙소"), ("commerce", "🛍️ 쇼핑 쇼츠", "🛍️ 쇼핑"),
             ("book", "📚 시골책방", "📚 책방"), ("video_overlay", "🎬 원본 직가공", "🎬 직가공"), ("longform", "🎞️ 롱폼 하이라이트", "🎞️ 롱폼"),
             ("radar", "🔥 떡상 레이더", "🔥 레이더")]
NAV_CSS = """
<style>
/* 스마트폰(≤768px): 좌측 바를 숨기고 같은 주메뉴를 화면 맨 아래 고정 바로 */
.st-key-nav_bottom { display: none; }
@media (max-width: 768px) {
  [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"] { display: none !important; }
  .st-key-nav_bottom { display: block; position: fixed; left: 0; right: 0; bottom: 0; z-index: 1000; padding: 4px 6px 8px 6px;
                       background: var(--background-color, #0e1117); border-top: 1px solid rgba(128,128,128,.35); }
  .st-key-nav_bottom [data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: nowrap !important; gap: 4px !important; overflow-x: auto; }
  .st-key-nav_bottom [data-testid="stColumn"] { min-width: 0 !important; flex: 1 1 0 !important; width: auto !important; }
  .st-key-nav_bottom button { padding: 6px 2px !important; min-height: 2.6rem; font-size: .78rem; white-space: nowrap; }
  .stMainBlockContainer { padding-bottom: 5.5rem !important; }
  /* 영상 소스 목록: 한 줄(순서·사진·이름) + 버튼 줄로 촘촘하게 (버튼이 세로로 길게 쌓이지 않게) */
  .st-key-media_manager [data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: wrap !important; gap: .3rem !important; align-items: center; }
  .st-key-media_manager [data-testid="stColumn"] { flex: 0 0 auto !important; min-width: 0 !important; width: auto !important; }
  .st-key-media_manager [data-testid="stColumn"]:nth-child(1) { flex: 0 0 4.2rem !important; }
  .st-key-media_manager [data-testid="stColumn"]:nth-child(3) { flex: 1 1 30% !important; }
  .st-key-media_manager [data-testid="stColumn"]:nth-child(n+4) button { min-height: 2.4rem; padding: 0 .6rem; }
  /* 후보 사진 그리드: 1열로 길게 늘어지지 않고 3열 바둑판 */
  .st-key-photo_grid [data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: wrap !important; gap: .4rem !important; }
  .st-key-photo_grid [data-testid="stColumn"] { flex: 0 0 calc(33.333% - .4rem) !important; min-width: calc(33.333% - .4rem) !important; width: auto !important; }
}
/* PC: 설정 버튼을 좌측 바 맨 아래로 */
.st-key-nav_side { display: flex; flex-direction: column; min-height: calc(100vh - 7rem); }
.st-key-nav_settings_box { margin-top: auto; }
</style>
"""


def _nav_items():
    return [it for it in NAV_ITEMS if it[0] in ("home", "owner")] if DEMO_MODE else NAV_ITEMS


def _nav_active() -> str:
    ss = st.session_state
    if ss.get("show_home"):
        return "home"
    page = ss.get("page_switch", "make")
    return page if page in ("radar", "longform") else ss.get("production_mode", "owner")


def _nav_go(view: str):
    """주메뉴 클릭: 홈 = 새 작업 + 첫 화면 / 제작 모드 = 모드 전환(이전 모드 입력 정리) / 롱폼·레이더 = 해당 화면"""
    ss = st.session_state
    if view == "home":
        start_new_job()
        ss.show_home = True
        ss.page_switch = "make"
        return
    ss.show_home = False
    if view in ("radar", "longform"):
        ss.page_switch = view
        return
    ss.page_switch = "make"
    ss.mode_switch = MODE_LABELS.get(view, ss.get("mode_switch"))
    _on_mode_change()


def _save_brand_logo():
    f = st.session_state.get("brand_logo")
    st.session_state.brand_logo_path = save_uploaded_file(f) if f else None


@st.dialog("⚙️ 설정")
def _settings_dialog():
    """API 키·Mock·강제 재생성·브랜드 오버레이·서버 데이터·버전 (작업 단계 옵션은 본문 1~5단계에 있어요)"""
    st.text_input("🔑 Gemini API Key (선택)", key="api_key_input", type="password",
                  placeholder="서버 키 사용 중 (입력 시 이 키로 대체)" if os.getenv("GEMINI_API_KEY", "") else "API 키를 입력하세요",
                  help="비워두면 서버 .env에 등록된 키를 사용합니다. 서버 키는 화면으로 전송되지 않습니다.")
    st.checkbox("🧪 Mock 모드 (API 과금 없이 초고속 테스트)", key="mock_mode")
    st.checkbox("🔄 강제 재생성 (기존 캐시 무시)", key="force_render")
    st.markdown("**🏷️ 브랜드 오버레이** (매장 홍보 영상)")
    st.checkbox("상호명 워터마크", key="brand_watermark")
    st.checkbox("네이버 검색 유도 엔딩 카드", key="brand_end_card")
    st.file_uploader("로고 (PNG 권장, 선택)", type=["png", "jpg", "jpeg"], key="brand_logo", on_change=_save_brand_logo)
    if st.session_state.get("brand_logo_path"):
        st.caption("✅ 로고가 저장돼 있어요")
    st.radio("워터마크 위치", ["top_right", "top_left"], key="brand_position", horizontal=True,
             format_func=lambda x: "우측 상단" if x == "top_right" else "좌측 상단")
    st.text_input("엔딩 카드 보조 문구 (선택)", key="brand_sub", placeholder="예: 경기 가평군 · 예약 문의 환영")
    with st.expander("🗄️ 서버 데이터 (Railway)", expanded=False):
        render_server_data_panel()     # 서버(Railway)에서만: PC 토큰·BGM·레이더 DB 묶음 가져오기
    _url_file = os.path.join("outputs", "tunnel_url.txt")       # scripts/watchdog.ps1이 터널을 재시작하면 새 주소를 기록
    if os.path.exists(_url_file):
        st.caption(f"📱 모바일 접속 주소: {open(_url_file, encoding='utf-8-sig').read().strip()}")
    from core.render.caption_chunks import CAPTION_ENGINE_VERSION
    st.caption(f"📁 작업 ID `{current_job().job_id}` · 자막 엔진 {CAPTION_ENGINE_VERSION} · AI Short-form Ad Video Factory v2.0")


def _render_nav(where: str):
    ss = st.session_state
    active = _nav_active()
    items = _nav_items()
    if where == "side":
        with st.container(key="nav_side"):
            for view, label, _short in items:
                st.button(label, key=f"nav_{view}", use_container_width=True, type="primary" if view == active else "secondary",
                          on_click=_nav_go, args=(view,))
            with st.container(key="nav_settings_box"):
                if st.button("⚙️ 설정", key="nav_settings", use_container_width=True):
                    _settings_dialog()
    else:
        with st.container(key="nav_bottom"):
            cols = st.columns(len(items) + 1)
            for col, (view, _label, short) in zip(cols, items):
                with col:
                    st.button(short, key=f"navb_{view}", use_container_width=True, type="primary" if view == active else "secondary",
                              on_click=_nav_go, args=(view,))
            with cols[-1]:
                if st.button("⚙️", key="navb_settings", use_container_width=True):
                    _settings_dialog()


def _render_home():
    st.title("🎬 AI 광고 공장")
    st.caption("사진 몇 장으로 인스타 릴스·유튜브 쇼츠를 만들어요. 만들 영상을 고르세요 (왼쪽 메뉴·모바일은 아래 바에서도 이동돼요).")
    cards = [("owner", "🏪 매장 홍보", "내 가게 사진·영상으로 상호와 네이버 검색 유도가 들어간 홍보 영상"),
             ("travel", "🏨 숙소 홍보", "인기 숙소를 골라 1인당 가성비 18초 제휴 쇼츠 + 예약 링크 댓글"),
             ("commerce", "🛍️ 쇼핑 쇼츠", "네이버 쇼핑 꿀템으로 문제해결형 18초 쇼츠 + 쇼핑커넥트 댓글"),
             ("book", "📚 시골책방", "책 한 권을 차분하게, 책 속 한 문장 인용 카드")]
    cols = st.columns(len(cards))
    for col, (view, title, desc) in zip(cols, [c for c in cards if (not DEMO_MODE or c[0] == "owner")] or cards):
        with col:
            with st.container(border=True):
                st.markdown(f"**{title}**")
                st.caption(desc)
                st.button("시작하기", key=f"home_{view}", use_container_width=True, type="primary", on_click=_nav_go, args=(view,))


st.markdown(NAV_CSS, unsafe_allow_html=True)
with st.sidebar:
    _render_nav("side")
_render_nav("bottom")
page = st.session_state.get("page_switch", "make")
if st.session_state.get("show_home"):
    _render_home()
    st.stop()
if page == "radar":
    render_radar_page()
    st.stop()
if page == "longform":
    render_longform_page()
    st.stop()

# ==============================================================================
# 설정 스냅샷: 작업 단계별 옵션 위젯은 본문 1~5단계에서 그려지지만, 값은 맨 위에서 필요하므로 세션 값에서 읽는다 (위젯 기본값과 같은 기본값)
# ==============================================================================
# 화자 시점 → 어울리는 성우·말투 프리셋 (시점을 바꿀 때만 적용하고, 이후 드롭다운에서 직접 바꾼 값은 그대로 둠)
VOICE_PRESET_BY_STYLE = {
    "official": {"actor": "female_30s", "tone": "calm", "why": "차분하고 신뢰감 있는 30대 톤"},
    "review": {"actor": "female_20s", "tone": "lively", "why": "발랄하고 생생한 20대 톤"},
}
VOICE_TONE_PRESETS = {
    "lively": {"name": "⚡ 활기차고 씩씩하게 (기본)", "instruction": "밝고 씩씩하며 에너지 넘치는 말투. 빠른 리듬감과 자신감 있는 톤으로 전달합니다."},
    "calm": {"name": "🌿 차분하고 신뢰감 있게", "instruction": "차분하고 정돈된 신뢰감 있는 말투. 조근조근하지만 또렷하게 핵심 혜택을 전달합니다."},
    "witty": {"name": "😄 위트 있고 친근하게", "instruction": "위트 있고 친근한 말투. 동네 단골에게 말하듯 편안하고 생동감 있게 전달합니다."},
}
POV_OPTIONS = {
    "owner": ({"official": "🏢 가게 공식 추천형 (점주·매장 입장의 당당하고 진정성 있는 혜택 안내)", "review": "🙋 손님 꿀팁 리뷰형 (단골·손님 시점의 생생한 추천 및 발견)"},
              "🏢 가게 공식 추천형: 점주 입장의 당당하고 진정성 있는 혜택 안내.\n🙋 손님 꿀팁 리뷰형: 손님이 직접 발견한 꿀팁 시점의 추천."),
    "travel": ({"official": "🏡 숙소 공식 큐레이션 (공식 가이드·추천 코스 안내)", "review": "✈️ 여행자 꿀팁 리뷰형 (생생한 투숙 후기 및 꿀팁)"},
               "🏡 숙소 공식 큐레이션: 숙소 공식 추천 가이드.\n✈️ 여행자 꿀팁 리뷰형: 인스타/쇼츠 브이로그 감성의 후기."),
    "commerce": ({"official": "🛍️ 브랜드 공식 추천 (핵심 기능 및 혜택 안내)", "review": "💬 내돈내산 솔직 리뷰 (실제 구매자 꿀팁 리뷰)"},
                 "🛍️ 브랜드 공식 추천: 핵심 기능 중심 안내.\n💬 내돈내산 솔직 리뷰: 구매자 시점 솔직 리뷰."),
    "book": ({"official": "📚 책방지기 추천사 (진정성 있는 큐레이션)", "review": "📖 독자 감성 리뷰 (독자의 시선에서 본 문장)"},
             "📚 책방지기 추천사: 책방 주인의 정성 어린 추천.\n📖 독자 감성 리뷰: 독자의 감상 중심."),
}
_default_pov = ({"official": "📢 공식 추천형", "review": "💬 리뷰/체험형"}, "공식 추천형 또는 리뷰형 화자 시점을 선택합니다.")
script_style_options, script_style_help = POV_OPTIONS.get(pipeline_mode, _default_pov)


def _apply_voice_preset():
    preset = VOICE_PRESET_BY_STYLE.get(st.session_state.get("script_style_radio"))
    if not preset:
        return
    st.session_state.selected_actor = preset["actor"]
    st.session_state.voice_actor_select_box = VOICE_ACTOR_PROFILES[preset["actor"]]["name"]
    st.session_state.voice_tone = preset["tone"]
    st.session_state.voice_preset_note = preset["why"]


# -- 기본·보안: 서버 키를 입력창 기본값으로 넣지 않음 (설정 팝업의 입력값 또는 .env)
env_api_key = os.getenv("GEMINI_API_KEY", "")
api_key = (st.session_state.get("api_key_input") or "").strip() or env_api_key
st.session_state.setdefault("mock_mode", False)
st.session_state.setdefault("force_render", True)
mock_mode = bool(st.session_state.mock_mode)
force_render = bool(st.session_state.force_render)

# -- 화자 시점 / 말투 / 길이
script_style_keys = [str(k) for k in script_style_options if k]
saved_style = st.session_state.get("script_style", "official")
default_style_idx = (script_style_keys.index(saved_style) if saved_style in script_style_keys
                     else 1 if (saved_style in ("experience", "review") and len(script_style_keys) > 1) else 0)
if "script_style_radio" not in st.session_state or st.session_state.script_style_radio not in script_style_keys:
    st.session_state.script_style_radio = script_style_keys[default_style_idx]
if not st.session_state.get("voice_preset_seeded"):
    _apply_voice_preset()                                  # 첫 진입: 기본 화자 시점에 맞는 성우·말투로 시작
    st.session_state.voice_preset_seeded = True
selected_script_style = st.session_state.script_style_radio
st.session_state.script_style = selected_script_style

travel_keys = [str(k) for k in TRAVEL_TEMPLATES if k]
travel_template = st.session_state.get("travel_template") if st.session_state.get("travel_template") in travel_keys else DEFAULT_TRAVEL_TEMPLATE

tone_keys = [str(k) for k in VOICE_TONE_PRESETS if k]
if st.session_state.get("voice_tone") not in tone_keys:
    st.session_state.voice_tone = tone_keys[0]
selected_tone = st.session_state.voice_tone
selected_tone_label = VOICE_TONE_PRESETS[selected_tone]["name"]
selected_tone_instruction = VOICE_TONE_PRESETS[selected_tone]["instruction"]

duration_keys = [str(k) for k in DURATION_PRESETS if k]
if st.session_state.get("duration_choice") not in duration_keys:
    st.session_state.duration_choice = duration_keys[1] if len(duration_keys) > 1 else duration_keys[0]
selected_duration = st.session_state.duration_choice

# -- 오디오·성우·BGM
audio_mode_options = {
    "voice_and_sub": "🎙️ AI 나레이션 + 자막 (기본 - 맛집·쇼핑 추천)",
    "bgm_only": "🎵 나레이션 없음: 감성 BGM + 자막만 (숙소·카페·감성 추천)",
    "silent": "🔇 무음 + 자막 전용 (유튜브 음원용)",
}
audio_mode_keys = list(audio_mode_options)
st.session_state.setdefault("audio_choice", "voice_and_sub")
if st.session_state.audio_choice not in audio_mode_keys:
    st.session_state.audio_choice = "voice_and_sub"
selected_audio_choice = st.session_state.audio_choice
bgm_only_mode = selected_audio_choice == "bgm_only"
# 나레이션 없음(BGM+자막)과 무음은 렌더 엔진에서 같은 경로(TTS 호출 없음, 문장당 3.6초 균등, 자막은 의미 단위 1줄). 차이는 BGM을 자동으로 켜는지뿐
selected_audio_mode = "silent" if selected_audio_choice in ("bgm_only", "silent") else selected_audio_choice

actor_keys = [str(k) for k in VOICE_ACTOR_PROFILES if k]
actor_labels = [str(VOICE_ACTOR_PROFILES[k]["name"]) for k in actor_keys if k in VOICE_ACTOR_PROFILES]
if st.session_state.get("voice_actor_select_box") not in actor_labels:
    _cur_actor = st.session_state.get("selected_actor")
    st.session_state.voice_actor_select_box = actor_labels[actor_keys.index(_cur_actor)] if _cur_actor in actor_keys else actor_labels[0]
selected_actor_label = st.session_state.voice_actor_select_box
selected_actor = actor_keys[actor_labels.index(selected_actor_label)]
st.session_state.selected_actor = selected_actor

bgm_tracks = bgm_mod.ordered_tracks()
st.session_state.setdefault("bgm_on", bool(bgm_tracks))      # 자체 음원이 있으면 기본 ON
_book_track = bgm_mod.book_default_track() if is_book_mode else None      # 시골책방: 잔잔한 피아노 BGM 기본 (.env BGM_BOOK_TRACK)
if _book_track and not st.session_state.get("bgm_book_init"):
    st.session_state.bgm_on, st.session_state.bgm_choice, st.session_state.bgm_book_init = True, _book_track, True
if bgm_only_mode and bgm_tracks:
    st.session_state["bgm_on"] = True                         # 나레이션 없음 모드는 BGM이 주인공: 자동으로 켬
bgm_on = bool(st.session_state.get("bgm_on"))
bgm_choice = None
if bgm_tracks and bgm_on:
    _bgm_valid = [str(bgm_mod.RANDOM)] + [bgm_mod.mood_choice(m) for m in bgm_mod.MOODS] + [os.path.basename(t) for t in bgm_tracks if t]
    if st.session_state.get("bgm_choice") not in _bgm_valid:
        st.session_state.bgm_choice = str(bgm_mod.RANDOM)
    bgm_choice = st.session_state.bgm_choice

# -- 자막 폰트·위치 (렌더 단계의 모드 정책이 읽는 환경 값도 여기서)
from core.render.fonts import PRESETS as FONT_PRESETS
font_keys = list(FONT_PRESETS)
st.session_state.setdefault("caption_font_choice", "impact")
if st.session_state.get("caption_font_choice") not in font_keys:
    st.session_state.caption_font_choice = "impact"
caption_font_key = st.session_state.caption_font_choice
st.session_state.setdefault("caption_pos", "mid")
if is_owner_mode or (is_travel_mode and st.session_state.get("stay_ctx")) or (is_commerce_mode and st.session_state.get("shop_item")):
    os.environ["OWNER_CAPTION_POS"] = st.session_state.get("caption_pos", "mid")          # 렌더 단계의 모드 정책(자막 높이·가로 사진 위치)이 읽음
else:
    os.environ.pop("OWNER_CAPTION_POS", None)
if is_travel_mode and st.session_state.get("stay_ctx"):
    os.environ["TRAVEL_STAY_STYLE"] = "1"                   # 숙소 자동 수집 영상: 자막 55%·2줄 덩어리·18초 5문장 정책
else:
    os.environ.pop("TRAVEL_STAY_STYLE", None)

# -- 브랜드 오버레이 (설정 팝업에서 켜고 끔, 매장 홍보 영상에만 적용)
brand_opts = None
if is_owner_mode:
    brand_opts = {"logo_path": st.session_state.get("brand_logo_path"), "position": st.session_state.get("brand_position", "top_right"),
                  "watermark": bool(st.session_state.get("brand_watermark", True)), "end_card": bool(st.session_state.get("brand_end_card", True)),
                  "sub": (st.session_state.get("brand_sub") or "").strip()}


# ---- 본문 단계 안에서 그리는 옵션 위젯 (값은 위 스냅샷과 같은 세션 키)
def _w_pov():
    st.radio("화자 시점", script_style_keys, key="script_style_radio", format_func=lambda x: str(script_style_options.get(x, x or "")),
             help=script_style_help, on_change=_apply_voice_preset, horizontal=True)
    if st.session_state.get("voice_preset_note"):
        st.caption(f"🔗 성우·말투를 이 시점에 맞춰 두었어요 ({st.session_state.voice_preset_note}). 4단계에서 바꿀 수 있어요.")


def _w_duration():
    st.selectbox("⏱️ 영상 길이", duration_keys, key="duration_choice", format_func=lambda k: str(DURATION_PRESETS[k]["name"]))


def _w_travel_template():
    st.selectbox("숙소 제휴 씬 템플릿", travel_keys, key="travel_template", format_func=lambda k: str(TRAVEL_TEMPLATES.get(k, {}).get("name", k or "")),
                 help="7씬 황금 비율: 01~03 수영장·자쿠지 후킹 → 04~05 객실·창밖 뷰 → 06~07 바베큐·카페·불멍 + 댓글 CTA")


def _w_audio_block():
    """4단계: 🔊 오디오 & 성우 · 🔤 자막 디자인 (폰트 3종 + 자막 위치)"""
    st.markdown("**🔊 오디오 & 성우**")
    st.selectbox("오디오 모드", audio_mode_keys, key="audio_choice", format_func=lambda x: str(audio_mode_options.get(x, x or "")), on_change=_remember_style)
    if is_travel_mode and selected_audio_choice == "voice_and_sub":
        st.caption("💡 숙소·카페 영상은 나레이션 없이 **🎵 감성 BGM + 자막만** 쓰면 더 고급스러워요 (TTS 비용·시간 절약).")
    if bgm_only_mode:
        st.caption("🎵 TTS를 부르지 않고 문장마다 3.6초(5문장 약 18초)로 나눠 BGM을 100% 볼륨으로 깔아요. 자막은 의미 단위로 한 줄씩 전환돼요.")
    else:
        v1, v2 = st.columns(2)
        with v1:
            st.selectbox("AI 성우 보이스", actor_labels, key="voice_actor_select_box", disabled=(selected_audio_mode == "silent"))
        with v2:
            st.selectbox("성우 낭독 말투", tone_keys, key="voice_tone", format_func=lambda k: VOICE_TONE_PRESETS[k]["name"], disabled=(selected_audio_mode == "silent"))
    if bgm_only_mode and not bgm_tracks:
        st.warning("🎵 assets/bgm 폴더에 음원(mp3·wav·m4a)이 없어 BGM이 안 나와요. 직접 만들었거나 권리를 가진 음원을 넣어 주세요.")
    st.toggle("🎵 자체 BGM 넣기", key="bgm_on", disabled=(not bgm_tracks) or bgm_only_mode,
              help="직접 만들었거나 권리를 가진 음원만 assets/bgm 폴더에 넣어 주세요. 나레이션이 나올 땐 20%로 낮춰 섞습니다.")
    if bgm_tracks and bgm_on:
        _mood_cnt = {m: len(bgm_mod.tracks_by_mood(m)) for m in bgm_mod.MOODS}
        _sig = set(bgm_mod.signature_tracks())

        def _bgm_label(v):
            if v == bgm_mod.RANDOM:
                return "🎲 랜덤 (렌더마다 무작위)"
            if str(v).startswith(bgm_mod.MOOD_PREFIX):
                m = str(v)[len(bgm_mod.MOOD_PREFIX):]
                return (f"🎼 {m} 분위기 중 랜덤 ({_mood_cnt[m]}곡)" if _mood_cnt[m] else f"🎼 {m} (태그된 곡 없음 → 전체에서 랜덤)")
            _m = bgm_mod.track_mood(str(v))
            return (f"⭐ {v}" if v in _sig else str(v or "")) + (f"  · {_m}" if _m else "")
        _opts = [str(bgm_mod.RANDOM)] + [bgm_mod.mood_choice(m) for m in bgm_mod.MOODS] + [os.path.basename(t) for t in bgm_tracks if t]
        st.selectbox("BGM 음원", _opts, key="bgm_choice", format_func=_bgm_label,
                     help="분위기를 고르면 그 분위기 곡 중 하나가 렌더마다 선택돼요. 곡의 분위기는 assets/bgm/moods.json에 적습니다. 인트로는 건너뛰고 비트가 들어오는 지점부터 붙어요.")
    elif not bgm_tracks:
        st.caption("🎵 자체 BGM: `assets/bgm/` 폴더에 mp3·wav·m4a 음원을 넣으면 켤 수 있어요.")
    st.markdown("**🔤 자막 디자인**")
    st.radio("자막 폰트", font_keys, key="caption_font_choice", format_func=lambda k: FONT_PRESETS[k]["label"], horizontal=True, on_change=_remember_style,
             help="\n".join(f"{v['label']}: {v['use']}" for v in FONT_PRESETS.values()))
    from core.mode_policy import CAPTION_POS_OPTIONS
    if is_owner_mode or (is_travel_mode and st.session_state.get("stay_ctx")) or (is_commerce_mode and st.session_state.get("shop_item")):
        st.radio("📐 자막 위치", list(CAPTION_POS_OPTIONS), key="caption_pos", format_func=CAPTION_POS_OPTIONS.get, horizontal=True, on_change=_remember_style,
                 help="실제 유튜브 쇼츠 앱은 화면 아래쪽(60~90%)에 채널명·제목·AI 라벨이 올라와 자막과 겹쳐요. 기본(55%)은 그 바로 위, 상단(24%)은 사진을 전혀 안 가려요.")


def _w_stay_settings():
    """숙소: 여기어때 제휴 링크 템플릿·워터마크 (숙소 선택 영역 안)"""
    st.text_input("여기어때 제휴 트래킹 템플릿 / 파트너 ID", key="stay_aff_template", placeholder="예: https://…?partner=내ID&url={url}",
                  help="치환자: {url}(예약 주소, 자동 인코딩) {url_raw} {partner_id}. 파트너 센터에서 받은 링크 형식을 그대로 붙여 넣고 주소 자리에 {url}을 쓰세요. 비워 두면 일반 링크가 나와 수익이 추적되지 않아요.")
    st.text_input("파트너 ID (템플릿의 {partner_id} 자리)", key="stay_partner_id")
    st.radio("상단 워터마크", ["alias", "off"], key="stay_watermark", horizontal=True, format_func=lambda x: "특징 별칭" if x == "alias" else "끄기",
             help="숙소 본명은 영상에 넣지 않아요 (포털 검색 이탈 방지).")


mode_opts = [str(m) for m in MODES if m]
production_mode = MODE_LABELS.get(pipeline_mode, mode_opts[0] if mode_opts else "owner")

target_url, crawl_btn = "", False
if not (is_owner_mode or is_book_mode):
    col_url, col_btn = st.columns([4, 1])
    with col_url:
        url_placeholder = (
            "숙소 페이지 URL (아고다·트립닷컴 링크는 제휴 수익 링크 자동 생성, 야놀자·여기어때 등)"
            if is_travel_mode
            else "쇼핑몰 상품 페이지 URL (https://smartstore.naver.com/..., 올리브영, 쿠팡 등)"
        )
        target_url = st.text_input("상품 페이지 URL 입력", placeholder=url_placeholder, label_visibility="collapsed")
    with col_btn:
        btn_label = "⚡ 숙소 정보 크롤링" if is_travel_mode else "⚡ 제품 정보 크롤링"
        crawl_btn = st.button(btn_label, use_container_width=True, type="primary")

# 크롤링 실행
if crawl_btn:
    if not target_url or not target_url.strip():
        st.warning("⚠️ 상품 페이지 URL을 입력해 주세요.")
    else:
        # 새 상품/숙소 = 새 작업 공간 (이전 작업의 씬·사진·거부 목록과 완전히 분리)
        start_new_job(source_url=target_url.strip(), mode=pipeline_mode)
        st.session_state.gallery_images = []
        set_product_info(None)

        # 최신 크롤러 모듈 강제 핫 리로드 (Streamlit 장시간 구동 시 모듈 캐시 갱신 보장)
        import importlib
        import core.scraper
        importlib.reload(core.scraper)

        crawl_spinner_msg = (
            "🕷️ 여행 상세페이지 분석 및 고화질 갤러리 컷(5~7장) 수집 중..."
            if is_travel_mode
            else "🕷️ 쇼핑몰 페이지 분석 및 대표 상품 이미지 수집 중..."
        )
        with st.spinner(crawl_spinner_msg):
            clean_url = target_url.strip()

            scraped = core.scraper.scrape_product_info(
                url=clean_url,
                output_image_dir=current_job().assets_dir,
                mode="travel" if is_travel_mode else "commerce",
                max_gallery_images=7
            )
            print(f"[app.py] scrape_product_info 반환 결과: success={scraped.get('success')}, gallery_images={scraped.get('gallery_images')}")
            st.session_state.affiliate_link = scraped.get("affiliate_link", "")
            st.session_state.affiliate_program = scraped.get("affiliate_program", "")

            if scraped.get("success"):
                set_product_info(scraped)
                missing_ko = {"product_name": "이름", "usp": "특장점"}
                missing_shown = [m for m in scraped.get("missing", []) if m in missing_ko]
                if missing_shown:
                    st.warning("⚠️ 페이지에서 " + ", ".join(missing_ko[m] for m in missing_shown)
                               + "을(를) 찾지 못했습니다. 아래 칸에 직접 입력해 주세요 (사실만 입력).")
                st.success(f"✅ 크롤링 성공! '{scraped['product_name'] or '(이름 없음)'}' 정보를 불러왔습니다.")
                
                if is_travel_mode and scraped.get("ota"):
                    # 아고다·트립닷컴: 페이지·사진을 가져오지 않음 → 직접 올린 사진으로 씬 구성 (사진 매니저)
                    ota_ko = OTA_LABEL.get(scraped["ota"], "")
                    st.session_state.image_pool = []
                    st.session_state.gallery_images = []
                    st.session_state.cutout_path = None
                    if scraped.get("affiliate_link"):
                        st.info(f"🔗 {ota_ko} 제휴 수익 링크를 만들었어요 (렌더링 후 복사 버튼): {scraped['affiliate_link']}")
                    else:
                        st.warning(f"🔗 {ota_ko} 제휴 ID가 .env에 없어 수익 링크를 못 만들었어요: "
                                   + ", ".join(scraped.get("affiliate_missing_keys", [])) + " 를 넣고 다시 눌러 주세요.")
                    st.info(f"📷 {ota_ko} 숙소 사진은 가져오지 않아요 (이용 약관상 자동 수집 금지·호텔 사진 저작권). "
                            "직접 찍은 사진이나 호텔에서 사용 허락을 받은 사진 6~10장(수영장·객실·뷰 등)을 아래에서 올려 주세요. "
                            "AI가 사진을 보고 씬에 맞게 배치합니다.")
                elif is_travel_mode:
                    # [Step 1 원칙: 실물 파일(.jpg)을 미리 다운로드하지 않고 메타데이터 풀만 적재]
                    image_pool = scraped.get("image_pool", [])
                    st.session_state.image_pool = image_pool
                    st.session_state.gallery_images = []
                    st.session_state.cutout_path = None

                    cat_counts = scraped.get("category_counts", {})
                    st.info(
                        f"📸 **[Step 1. 대본 선기획 메타데이터 풀 확보 완료]** 총 **{len(image_pool)}개** 고유 공간 사진 메타데이터 확보\n\n"
                        f"- 🏊 수영장/물놀이: **{cat_counts.get('pool', 0)}개**\n"
                        f"- 🏞️ 외관/전경/호수뷰: **{cat_counts.get('view', 0)}개**\n"
                        f"- ☕ 라운지/부대시설: **{cat_counts.get('facility', 0)}개**\n"
                        f"- 🛏️ 객실/침실: **{cat_counts.get('bedroom', 0)}개**\n\n"
                        "💡 *Script-First 원칙: 네트워크 및 디스크 낭비를 방지하기 위해 실물 이미지는 미리 다운로드하지 않고, 2단계 대본 기획 후 각 씬에 필요한 사진만 핀포인트 다운로드(100KB+)됩니다.*"
                    )
                    st.toast(f"메타데이터 풀 {len(image_pool)}개 확보! 2단계 대본 기획을 진행해 주세요.", icon="📋")
                else:
                    # 쇼핑 제휴: 판매 페이지의 실물 상품 사진을 씬 사진으로 사용 (AI 이미지 생성 없음)
                    st.session_state.gallery_images = []
                    st.session_state.cutout_path = None
                    product_imgs = [p for p in scraped.get("product_images", []) if os.path.exists(p)]
                    st.session_state.uploaded_paths = product_imgs
                    if "usp" in scraped.get("missing", []) and scraped.get("product_name") and not mock_mode:
                        with st.spinner("🤖 상품명과 사진으로 특장점을 정리하는 중..."):
                            ana = analyze_product_photo(scraped["product_name"], product_imgs[0] if product_imgs else None, api_key)
                        pts = ana["points"]
                        if ana["box"] and product_imgs:
                            st.session_state.product_boxes = {product_imgs[0]: ana["box"]}
                        if pts:
                            set_product_info({**scraped, "usp": ", ".join(pts)})
                            st.session_state.usp_auto = True
                    if product_imgs:
                        st.toast(f"🛒 상품 사진 {len(product_imgs)}장 확보!", icon="📸")
                    else:
                        st.warning("⚠️ 판매 페이지에서 상품 사진을 가져오지 못했습니다 (쿠팡처럼 자동 수집을 막는 쇼핑몰). "
                                   "아래에서 상품 사진을 직접 올려 주세요.")
            else:
                # 실패해도 링크에서 확실히 알 수 있는 상품명(검색어)은 입력 칸에 제안으로 채움 (특장점은 비움)
                set_product_info({"product_name": scraped.get("product_name_hint", ""), "usp": ""}
                                 if scraped.get("product_name_hint") else None)
                st.error(f"❌ 크롤링 실패: {scraped.get('error')}.")
                if scraped.get("affiliate_link"):
                    st.info(f"🔗 제휴 링크는 만들어 두었어요: {scraped['affiliate_link']} (영상 설명·고정 댓글용)")

# 쇼핑 영상 구성: 단일 상품 / 3단 묶음 큐레이션 (상품 3개를 30초 안에)
BUNDLE_LAYOUTS = {"single": "🛍️ 단일 상품", "bundle": f"🧺 {BUNDLE_SIZE}단 묶음 큐레이션 (상품 {BUNDLE_SIZE}개 · 30초)"}
st.session_state.setdefault("commerce_layout", "single")
st.session_state.setdefault("bundle_bottom", DEFAULT_BOTTOM_BAR)
for _k in range(BUNDLE_SIZE):
    st.session_state.setdefault(f"bundle_media_{_k}", [])
if is_commerce_mode:
    b_layouts = [str(k) for k in BUNDLE_LAYOUTS if k]
    cur_layout = st.session_state.get("commerce_layout")
    layout_idx = b_layouts.index(cur_layout) if cur_layout in b_layouts else 0
    st.radio(str("쇼핑 영상 구성" or ""), b_layouts, index=layout_idx,
             format_func=lambda k: str(BUNDLE_LAYOUTS.get(k, k or "")),
             key="commerce_layout", horizontal=True)
is_bundle = is_commerce_mode and st.session_state.commerce_layout == "bundle"


def _book_autofill():
    """[책 정보 자동 채우기] 콜백: 위젯이 그려지기 전에 입력칸 값을 채움 (core/book_info.py)"""
    from core.book_info import BookInfoError, collect_book
    ss = st.session_state
    query = (ss.get("book_title") or "").strip()
    try:
        info = collect_book(query, author=(ss.get("book_author") or "").strip(), aladin_link=(ss.get("book_link") or "").strip(),
                            out_dir=current_job().assets_dir)
    except BookInfoError as e:
        ss.book_autofill_msg = ("error", str(e), [])
        return
    ss.book_title = info.title or query
    ss.book_publisher = info.publisher
    for key, val in (("book_author", info.author), ("book_info", info.description), ("book_link", info.purchase_link)):
        if val and not (ss.get(key) or "").strip():
            ss[key] = val
    if info.cover_path and info.cover_path not in ss.uploaded_paths:
        ss.uploaded_paths = [info.cover_path] + list(ss.uploaded_paths)
        ss.book_photo_count = 1
        ss.storyboard = []
    ss.book_autofill_msg = ("ok", "책 정보를 채웠어요: " + info.summary() + " · 출처 " + ", ".join(info.sources),
                            [w for w in info.warnings if "키 없음" not in w or not info.sources])


def book_comment_text(copy) -> str:
    """시골책방 첫 댓글 (core/pipeline.book_comment)"""
    return pipeline.book_comment(pipeline.JobSpec(
        mode="book", name=(st.session_state.get("book_store") or "").strip(),
        book_title=st.session_state.get("book_title") or "", book_link=st.session_state.get("book_link") or "",
        book_search_kw=st.session_state.get("book_search_kw") or ""), copy)


def bundle_disclosure_short(items: list) -> str:
    """화면·설명란용 짧은 제휴 표시 (core/pipeline.disclosure_short)"""
    return pipeline.disclosure_short(pipeline.JobSpec(mode="commerce", layout="bundle", bundle_items=items)) or ""


def bundle_items_now() -> list:
    """묶음 상품 입력칸 현재 값 [{name, short, usp, link, media}] (이름이 빈 칸 포함, 순서 = 상품 1·2·3)"""
    out = []
    for k in range(BUNDLE_SIZE):
        name = (st.session_state.get(f"bundle_name_{k}") or "").strip()
        out.append({"name": name, "short": bundle_short_name(name) if name else "",
                    "usp": (st.session_state.get(f"bundle_usp_{k}") or "").strip(),
                    "link": (st.session_state.get(f"bundle_link_{k}") or "").strip(),
                    "media": list(st.session_state.get(f"bundle_media_{k}") or [])})
    return out


# ---- 네이버 쇼핑 제휴 (공식 오픈 API: 쇼핑·블로그·이미지 검색, 봇 차단 없음) — 쇼핑 모드의 메인 화면
def _shop_client():
    from core.naver_shop import NaverShop
    return NaverShop()


def _shop_link_now() -> str:
    """고정 댓글에 들어갈 링크: 사용자가 붙여 넣은 쇼핑커넥트 링크 > (없으면) 네이버 쇼핑 상품 링크"""
    ss = st.session_state
    item = (ss.get("shop_item") or {}).get("product") or {}
    return (ss.get("shop_connect_link") or "").strip() or (item.get("link") or "")


def _pick_shop_product(product: dict, user_query: str = "", link: str = ""):
    """카드·검색 결과의 [이 상품으로 바로 기획] → 새 작업 + 후기·사진 수집(공식 이미지·초상권 필터) + 컷 5장(마지막 = 공식 대표 썸네일) + 자동 기획 예약"""
    from core import naver_shop as ns
    ss = st.session_state
    start_new_job(source_url=link or product.get("link", ""), mode="commerce")
    ss.suggested_concepts, ss.selected_core_concept = [], ""      # 새 상품 이름으로 기본 컨셉을 다시 만들도록
    ss.pop("concept_radio_picker", None)
    shop = _shop_client()
    with st.spinner(f"🛍️ '{product['name'][:24]}' 후기·사진을 모으는 중... (네이버 공식 API)"):
        pack = ns.collect_pack(product, user_query, shop, official_only=bool(ss.get("official_only", True)))
        items = ns.curate_photo_items(pack)
        got = ns.download_pack_photos(items, current_job().uploads_dir, api_key if not mock_mode else None)
    off = [d for d in got if d.get("cut_role") == "official"][:1]
    body, seen_p = [], {d["path"] for d in off}
    for d in got:                                  # 같은 사진이 두 번 내려와도 한 번만 (공식 썸네일과 같으면 본문 컷에서 뺌)
        if d.get("cut_role") != "official" and d["path"] not in seen_p:
            seen_p.add(d["path"])
            body.append(d)
    paths = [d["path"] for d in body + off]
    roles = [d.get("cut_role") for d in body + off]
    value = ns.value_assumption(product["lprice"], ss.get("shop_months") or None, ss.get("shop_uses") or None)
    ss.uploaded_paths, ss.web_photo_paths = paths, list(paths)
    ss.shop_item = {"product": product, "pack": pack, "value": value, "cut_roles": roles, "keyword": pack.get("keyword", ""),
                    "role_by_path": {d["path"]: d.get("cut_role") or "usage" for d in body + off}, "official_path": off[0]["path"] if off else ""}
    used_links = {d.get("link") for d in got}
    cand = [{**it, "cut_role": role} for role, its in (pack.get("bucket_images") or {}).items() for it in its if it.get("link") not in used_links]
    if cand and api_key and not mock_mode:
        import core.store_photos as _sph
        cand = _sph.screen_candidates(cand[:12], api_key)
    ss.store_photo_candidates, ss.store_photo_msg = cand[:12], ""          # 1단계 하단 그리드: 자동 선정 컷 외에 더 넣고 싶은 후보
    nouns = (pack.get("reviews") or {}).get("nouns") or []
    set_product_info({"product_name": product["name"], "usp": ", ".join(nouns[:4])})
    ss.shop_connect_link = link or ""
    ss.affiliate_program, ss.affiliate_link = "naver_shop", _shop_link_now()
    ss.shop_msg = " / ".join(pack.get("errors") or []) if not paths else ""
    if not paths:
        ss.shop_msg = ss.shop_msg or "사진을 내려받지 못했어요. 직접 올려 주세요."
    ss.auto_plan_now = bool(paths)               # 사진이 모이면 5문장 대본까지 자동 기획


def _shop_card(prod: dict, key: str, user_query: str = "", link: str = "", badge: str = ""):
    from core.naver_shop import price_bucket
    c1, c2, c3 = st.columns([1, 4, 1.8], vertical_alignment="center")
    with c1:
        st.image(prod.get("image") or "", width=64)
    with c2:
        st.markdown(f"**{html.escape(prod['name'][:46])}**  \n{prod['lprice']:,}원 ({price_bucket(prod['lprice'])}) · {html.escape(prod.get('mall') or '')}{badge}")
    with c3:
        if st.button("🚀 바로 기획", key=key, use_container_width=True, type="primary"):
            _pick_shop_product(prod, user_query, link)
            st.rerun()


def _render_shop_pipeline():
    """네이버 쇼핑: 트렌드 꿀템 TOP 5 카드 / 키워드·스마트스토어 URL 검색 → 한 번에 사진·후기 수집 + 자동 기획 (쿠팡은 아래 보조 검색)"""
    from core import naver_shop as ns
    ss = st.session_state
    shop = _shop_client()
    with st.container(border=True, key="shop_box"):
        st.markdown("**🔥 네이버 쇼핑 꿀템 — 카드를 누르거나 검색해서 바로 만들기**")
        f1, f2 = st.columns([3, 1.4], vertical_alignment="bottom")
        with f1:
            st.selectbox("카테고리", list(ns.TREND_CATEGORIES), key="shop_category")
        with f2:
            if st.button("🔍 꿀템 TOP 5", key="shop_find", type="primary", use_container_width=True):
                with st.spinner("📈 데이터랩 급상승 키워드 + 네이버 쇼핑으로 찾는 중..."):
                    ss.shop_cards = ns.trending_cards(ss.get("shop_category") or ns.DEFAULT_CATEGORY, shop)
        cards = ss.get("shop_cards")
        if cards:
            for n in cards.get("notes") or []:
                st.info(n)
            if cards.get("error") and not cards.get("cards"):
                st.warning(cards["error"])
            for i, c in enumerate(cards.get("cards") or []):
                sg = c.get("surge")
                _shop_card(c, f"shop_pick_{i}", user_query=c.get("keyword", ""), badge=f" · 🔥 최근 7일 {sg:.1f}배" if sg else "")
        s1, s2 = st.columns([4, 1.4], vertical_alignment="bottom")
        with s1:
            q_in = st.text_input("상품 검색 (상품명 또는 스마트스토어·쇼핑커넥트 링크)", key="shop_query", placeholder='예: 캠핑 제빙기 · 배수구 거품 클리너 · https://smartstore.naver.com/...')
        with s2:
            q_btn = st.button("🔎 검색", key="shop_search", use_container_width=True)
        if q_btn and q_in.strip():
            res = ns.resolve_input(q_in.strip(), shop)
            ss.shop_link_pending = res.get("link", "")
            ss.shop_msg = res.get("note", "")
            ss.shop_results = []
            if res.get("query"):
                try:
                    ss.shop_results = shop.search_products(res["query"], display=5)
                    ss.shop_query_used = res["query"]
                    if not ss.shop_results:
                        ss.shop_msg = "검색 결과가 없어요. 다른 상품명으로 찾아 주세요."
                except ns.NaverShopError as e:
                    ss.shop_msg = str(e)
        if ss.get("shop_msg"):
            st.info(ss.shop_msg)
        for i, p in enumerate(ss.get("shop_results") or []):
            _shop_card(p, f"shop_res_{i}", user_query=ss.get("shop_query_used", ""), link=ss.get("shop_link_pending", ""))
    if ss.get("shop_item"):
        item = ss.shop_item["product"]
        with st.container(border=True, key="shop_picked_box"):
            st.markdown(f"**📦 선택한 상품: {item['name'][:40]}** · 네이버 최저가 {item['lprice']:,}원 · {item.get('mall', '')}")
            st.text_input("🔗 네이버 쇼핑커넥트 링크 (고정 댓글에 들어가요)", key="shop_connect_link", placeholder="쇼핑커넥트에서 만든 내 링크를 붙여 넣으세요 (없으면 상품 링크)")
            with st.expander("⚙️ 체감 가격 계산 가정 (선택)", expanded=False):
                m1, m2 = st.columns(2)
                with m1:
                    st.number_input("몇 달 쓴다고 칠까요 (0 = 자동)", min_value=0, max_value=36, step=1, key="shop_months")
                with m2:
                    st.number_input("한 달에 몇 번 쓰나요 (소모품, 0 = 자동)", min_value=0, max_value=60, step=1, key="shop_uses")
            ss.shop_item = {**ss.shop_item, "value": ns.value_assumption(item["lprice"], ss.get("shop_months") or None, ss.get("shop_uses") or None)}
            st.caption("💬 " + (ss.shop_item["value"].get("phrase") or "체감 가격 계산 불가") + " (계산 가정이에요, 사실 주장이 아니에요)")
            ss.affiliate_program, ss.affiliate_link = "naver_shop", _shop_link_now()


if is_commerce_mode and not is_bundle:
    _render_shop_pipeline()

# 쿠팡 상품 검색 (파트너스 공식 API, 보조): 상품명·고해상도 대표 사진·파트너스 링크를 한 번에
if is_commerce_mode and CoupangPartners().enabled:
    if "coupang_results" not in st.session_state:
        st.session_state.coupang_results = []
    with st.expander("🔎 (보조) 쿠팡 상품 검색 — 쿠팡 파트너스 링크를 쓸 때", expanded=False):
        cp_client = CoupangPartners()
        k1, k2 = st.columns([4, 1])
        with k1:
            cp_kw = st.text_input("쿠팡 검색어", key="coupang_kw", placeholder="예: PTC 전기히터", label_visibility="collapsed")
        with k2:
            cp_search = st.button("검색", key="coupang_search_btn", use_container_width=True)
        st.caption(f"검색은 1시간에 {SEARCH_LIMIT_PER_HOUR}번까지 가능해요 (남은 횟수 {cp_client.searches_left()}번, 같은 검색어는 다시 세지 않음).")
        if cp_search and cp_kw.strip():
            try:
                st.session_state.coupang_results = cp_client.search(cp_kw.strip())
                if not st.session_state.coupang_results:
                    st.info("검색 결과가 없어요. 다른 검색어로 찾아 주세요.")
            except CoupangAPIError as ce:
                st.error(str(ce))
        for item in st.session_state.get("coupang_results", []):
            c_img, c_txt, c_btn = st.columns([1, 4, 1.4], vertical_alignment="center")
            with c_img:
                st.image(item["image_url"], width=72)
            with c_txt:
                price = f"{int(item['price']):,}원" if str(item.get("price") or "").isdigit() else ""
                st.markdown(f"**{html.escape(item['name'])}**  \n{price}{' · 로켓배송' if item.get('is_rocket') else ''}")
            with c_btn:
                if is_bundle and st.button("묶음에 추가", key=f"coupang_add_{item['product_id']}_{item['image_url'][-12:]}",
                                           use_container_width=True):
                    slot = next((k for k in range(BUNDLE_SIZE) if not (st.session_state.get(f"bundle_name_{k}") or "").strip()), None)
                    if slot is None:
                        st.warning(f"묶음 {BUNDLE_SIZE}칸이 다 찼어요. 아래 묶음 상품 칸에서 하나를 비운 뒤 다시 눌러 주세요.")
                    else:
                        imgs = core.scraper.download_product_images([{"url": item["image_url"]}], current_job().assets_dir, None,
                                                                    prefix=f"bundle{slot + 1}")     # 상품끼리 덮어쓰지 않게
                        st.session_state[f"bundle_name_{slot}"] = item["name"]
                        st.session_state[f"bundle_link_{slot}"] = (cp_client.deeplink(f"https://www.coupang.com/vp/products/{item['product_id']}")
                                                                   or item["partner_url"])
                        st.session_state[f"bundle_media_{slot}"] = imgs
                        st.session_state.uploaded_paths = list(dict.fromkeys(st.session_state.uploaded_paths + imgs))
                        if not mock_mode and imgs:
                            pts = analyze_product_photo(item["name"], imgs[0], api_key)["points"]
                            st.session_state[f"bundle_usp_{slot}"] = ", ".join(pts)
                        st.session_state.storyboard = []
                        st.toast(f"상품 {slot + 1}에 추가했어요: {bundle_short_name(item['name'])}", icon="🧺")
                        st.rerun()
                if not is_bundle and st.button("이 상품 사용", key=f"coupang_pick_{item['product_id']}_{item['image_url'][-12:]}", use_container_width=True):
                    # 같은 상품 번호의 검색 결과(옵션·판매처별)에 다른 이미지가 있으면 모두 모음 (파트너스 API는 상품당 대표 이미지 1장)
                    same = [it["image_url"] for it in st.session_state.get("coupang_results", []) if it["product_id"] == item["product_id"]]
                    start_new_job(source_url=item["partner_url"], mode="commerce")
                    imgs = core.scraper.download_product_images([{"url": u} for u in dict.fromkeys(same)], current_job().assets_dir, None)
                    st.session_state.uploaded_paths = imgs
                    ana = {"points": [], "box": None} if mock_mode else analyze_product_photo(item["name"], imgs[0] if imgs else None, api_key)
                    pts = ana["points"]
                    st.session_state.product_boxes = {imgs[0]: ana["box"]} if (imgs and ana["box"]) else {}
                    set_product_info({"product_name": item["name"], "usp": ", ".join(pts)})
                    st.session_state.usp_auto = bool(pts)
                    st.session_state.affiliate_link = (cp_client.deeplink(f"https://www.coupang.com/vp/products/{item['product_id']}")
                                                       or item["partner_url"])
                    st.session_state.coupang_results = []
                    st.toast("상품명·사진·파트너스 링크를 불러왔어요. 특장점은 판매 페이지를 보고 사실만 적어 주세요.", icon="🛒")
                    st.rerun()

def _fill_store_points(api_key_: str, mock_: bool):
    """'사진으로 매장 특징 정리' 버튼 콜백 (위젯 값 변경은 콜백에서 해야 같은 실행에서 오류가 나지 않음)"""
    existing = st.session_state.get("owner_usp", "").strip()
    from core.store_facts import analyze_photos, build_store_facts, facts_to_points
    facts = _merged_store_facts()
    fact_pts = [p for p in facts_to_points(facts) if p and p not in existing]
    if not mock_ and st.session_state.uploaded_paths:
        # 사진마다 실제로 보이는 피사체를 적어 둠 (대본이 화면 사진과 어긋나지 않게)
        tags, buckets = analyze_photos(st.session_state.uploaded_paths, business_name=st.session_state.get("owner_name", ""), api_key=api_key_,
                                       thumb_dir=current_job().assets_dir, cache_path=current_job().asset_path("photo_tags.json"))
        st.session_state.photo_tags = tags
        st.session_state.photo_buckets = buckets
    pts = suggest_store_points(st.session_state.get("owner_name", ""), st.session_state.get("owner_type", DEFAULT_BUSINESS_TYPE),
                               st.session_state.uploaded_paths, existing, api_key=api_key_, thumb_dir=current_job().assets_dir,
                               mock=mock_)
    pts = fact_pts + [p for p in pts if p not in fact_pts]
    if pts:
        st.session_state.owner_usp = " / ".join([x for x in [existing] + pts if x])
        n_tag = len(st.session_state.get("photo_tags") or {})
        st.session_state.store_points_msg = (f"🤖 매장 특징 {len(pts)}개를 보탰어요" + (f" (사진 {n_tag}장의 피사체도 기록)" if n_tag else "")
                                             + ". 사실과 다르면 고쳐 주세요.")
    else:
        st.session_state.store_points_msg = ("사진이 없거나(먼저 사진·영상을 올려 주세요) 정리할 특징을 찾지 못했어요."
                                             if not mock_ else "Mock 모드에서는 AI 특징 정리를 건너뜁니다.")


def _fill_place(it: dict):
    """네이버 검색 결과 1건 → 상호명·업종·주소·검색어·소개글(USP) 자동 주입"""
    st.session_state.owner_name = it["name"]
    st.session_state.owner_type = it["business_type"]
    st.session_state.owner_address = it["road_address"] or it["address"]
    st.session_state.owner_search_kw = it["name"]   # 네이버 검색어 상호명 기본값 자동 주입
    # 네이버 플레이스 소개글을 우리가게 자랑(USP)에 기본값으로 자동 채움
    place_desc = (it.get("description") or "").strip() or fetch_place_description(st.session_state.get("place_id", ""))
    if place_desc:
        st.session_state.owner_usp = place_desc
    st.session_state.place_results = []


def _merged_store_facts() -> dict:
    """자동 수집(네이버 공식 API·웹 검색)에서 확인된 팩트 + 붙여 넣은 메뉴판·리뷰 글에서 뽑은 팩트 (+ 켠 경우 미확인 항목). 매번 다시 합침"""
    from core.store_facts import build_store_facts, merge_facts
    auto = st.session_state.get("store_research") or {}
    pasted = build_store_facts(menu_text=st.session_state.get("owner_menu_text", ""), review_text=st.session_state.get("owner_review_text", ""),
                               address=st.session_state.get("owner_address", ""))
    facts = merge_facts(auto.get("confirmed") or {}, pasted)
    if st.session_state.get("use_unconfirmed_facts"):
        facts = merge_facts(facts, auto.get("unconfirmed") or {})
    return facts


def _search_store_photos(name: str, address: str = "", quiet: bool = False):
    """상호명으로 매장 사진 후보를 찾아 세션에 보관 (네이버 공식 이미지 검색). quiet=True면 실패해도 조용히 (가게 정보 가져오기 뒤 자동 시도용)"""
    if mock_mode or not (name or "").strip() or os.getenv("STORE_RESEARCH_DISABLE", "").strip() == "1":
        return
    import core.store_photos as _sp
    from core.planners.owner import short_region
    region = short_region(region_of(address)) if address else ""
    with st.spinner("🖼️ 매장 사진 후보를 찾는 중... (네이버 이미지 검색)"):
        res = _sp.search_store_images(name.strip(), region, os.getenv("NAVER_CLIENT_ID", ""), os.getenv("NAVER_CLIENT_SECRET", ""),
                                      official_only=bool(st.session_state.get("official_only", True)))
        if res["items"] and api_key:
            res["items"] = _sp.screen_candidates(res["items"], api_key)          # 사람·워터마크·글씨 사진은 자동 제외 표시
    for k in [k for k in st.session_state.keys() if str(k).startswith("webpick_")]:
        del st.session_state[k]                                     # 새 검색이면 이전 체크 상태 초기화 (상위 8장 기본 체크)
    st.session_state.store_photo_candidates = res["items"]
    st.session_state.store_photo_msg = res["error"] if (res["error"] and not quiet) else ("" if res["items"] else ("" if quiet else "조건에 맞는 사진을 찾지 못했어요. 상호명에 지역을 붙여 다시 찾거나 직접 올려 주세요."))


def _plan_stay_scenes():
    """숙소 자동 수집 영상: 사진 비전 분류(블랙리스트 제외) → 3-2-2 정렬 → 1컨셉 18초 5문장 대본 + 문장별 사진 묶음"""
    import core.stay_photos as sp
    from core.planners.stay import plan_stay_storyboard
    ss = st.session_state
    ctx = ss.stay_ctx
    job = current_job()
    paths = [p for p in ss.uploaded_paths if os.path.exists(p) and not is_video_file(p)]
    tags, buckets = sp.classify_stay_photos(paths, stay_name=ctx["stay"]["name"], api_key=api_key, thumb_dir=job.assets_dir,
                                            cache_path=job.asset_path("stay_photo_tags.json"), mock=mock_mode)
    arr = sp.order_stay_photos(paths, buckets)
    ordered = arr["ordered"] or paths[:7]
    ss.uploaded_paths = ordered + arr["unused"]
    ss.stay_arrange_note = (f"🎞️ 사진 정렬: 물·스파 → 통창 뷰 → 바비큐·야경 순 {len(ordered)}장 사용"
                            + (f", 제외 {len(arr['excluded'])}장(화장실·복도·주차장 등)" if arr["excluded"] else "")
                            + ("" if buckets else " (비전 분류를 못 해 올린 순서 그대로)"))
    facts = _stay_facts_now()
    alias = (ss.get("stay_alias_input") or ctx.get("alias") or "").strip()
    return plan_stay_storyboard(real_name=ctx["stay"]["name"], alias=alias, region=ctx["stay"].get("address") or ctx["stay"].get("region", ""),
                                facts=facts, media_paths=ordered, photo_tags=tags, api_key=api_key, mock=mock_mode,
                                output_plan_path=job.plan_path, usp=ss.get("product_usp_input", ""))


def _naver_keys():
    return os.getenv("NAVER_CLIENT_ID", ""), os.getenv("NAVER_CLIENT_SECRET", "")


def _stay_facts_now() -> dict:
    """숙소 팩트 + 화면에서 고친 1박 요금·인원 (직접 입력한 값이 우선) → 1인 체감가 다시 계산"""
    import core.stay_match as sm
    ctx = st.session_state.get("stay_ctx") or {}
    f = dict(ctx.get("facts") or {})
    price = int(st.session_state.get("stay_price_input") or 0)
    people = int(st.session_state.get("stay_people_input") or 0)
    if price > 0:
        f["nightly_price"], f["price_source"] = price, "manual"
    if people > 0:
        f["people_used"], f["people_estimated"] = people, False
    f["per_person"] = sm.per_person_text(f.get("nightly_price"), int(f.get("people_used") or sm.DEFAULT_PEOPLE))
    return f


def _stay_link_now() -> dict:
    """사이드바 제휴 템플릿 + (직접 붙여 넣은 > 자동 매칭된 > 검색 결과) 여기어때 주소 → 예약 링크"""
    import core.stay_match as sm
    ctx = st.session_state.get("stay_ctx") or {}
    y = ctx.get("yeogi") or {}
    target = (st.session_state.get("stay_manual_url") or "").strip() or y.get("url") or y.get("search_url") or ""
    return sm.build_affiliate_link(st.session_state.get("stay_aff_template", ""), st.session_state.get("stay_partner_id", ""), target)


def _find_stay_top5():
    import core.stay_curation as sc
    cid, sec = _naver_keys()
    with st.spinner("📈 데이터랩 트렌드와 네이버 지역·블로그 검색으로 인기 숙소를 찾는 중..."):
        st.session_state.stay_top5 = sc.curate_top5(st.session_state.get("stay_preset", sc.DEFAULT_PRESET),
                                                    st.session_state.get("stay_mode", "season"), cid, sec)


def _pick_stay(item: dict, manual_url: str = ""):
    """TOP 5 카드/직접 입력 → 새 작업 공간 + 여기어때 매칭 + 가격·인원·시설 수집 + 사진 후보 갤러리 (링크 복사 없이 한 번에)"""
    import core.stay_match as sm
    import core.stay_photos as sp
    ss = st.session_state
    cid, sec = _naver_keys()
    start_new_job(source_url=manual_url or item.get("link", "") or item["name"], mode="travel")
    ss.suggested_concepts, ss.selected_core_concept = [], ""
    ss.pop("concept_radio_picker", None)
    ss.gallery_images, ss.image_pool, ss.cutout_path = [], [], None
    with st.spinner(f"🏨 '{item['name']}' 요금·시설·사진을 모으는 중... (여기어때 → 막히면 네이버 자료로 대체)"):
        y = {"url": manual_url, "search_url": sm.yeogi_search_url(item["name"]), "blocked": False, "error": ""} if manual_url else sm.find_yeogi(item["name"])
        facts = sm.collect_stay_facts(item, cid, sec, y.get("url", ""))
        cands = sp.search_stay_candidates(item["name"], item.get("region", ""), cid, sec, official_only=bool(ss.get("official_only", True)))
        if cands["items"] and api_key:
            import core.store_photos as _sph
            cands["items"] = _sph.screen_candidates(cands["items"], api_key)
    usp = ", ".join(facts["facilities"]) + (f", 1박 {facts['nightly_price']:,}원~" if facts.get("nightly_price") else "")
    set_product_info({"product_name": item["name"], "usp": usp})
    ss.stay_ctx = {"stay": item, "yeogi": y, "facts": facts, "alias": facts["alias"] or item.get("region", "") or "숙소"}
    ss.stay_alias_input = ss.stay_ctx["alias"]
    ss.stay_price_input = int(facts.get("nightly_price") or 0)
    ss.stay_people_input = int(facts.get("people_used") or 4)
    ss.stay_manual_url = manual_url
    for k in [k for k in ss.keys() if str(k).startswith("webpick_")]:
        del ss[k]
    ss.store_photo_candidates = cands["items"]
    ss.web_photo_paths = []
    ss.store_photo_msg = cands["error"] or ("" if cands["items"] else "사진 후보를 찾지 못했어요. 직접 올려 주세요.")
    link = _stay_link_now()
    ss.affiliate_program, ss.affiliate_link = "yeogi", link["link"]
    from core.photo_safety import FLAG_LABELS as _FL            # noqa: F401
    from core.store_photos import DEFAULT_CHECKED as _DC
    ok_ = [it for it in ss.store_photo_candidates if not it.get("flag") and it.get("source") != "blog"][:_DC]
    if ok_:
        _use_selected_web_photos(ok_, keep_gallery=True)          # 고르자마자 공식 사진 상위 8장을 영상 소스로 (갤러리에서 다시 고를 수 있어요)
        ss.auto_plan_now = bool(ss.get("uploaded_paths"))


def _render_stay_pipeline():
    """숙소 제휴 1~4단계 화면: 인기 TOP 5 큐레이션 → 원클릭 선택 → 수집 결과(요금·1인가·시설·별칭) → 3-2-2 사진 갤러리"""
    import core.stay_curation as sc
    ss = st.session_state
    with st.container(border=True, key="stay_box"):
        st.markdown("**🔥 인기 숙소 TOP 5 — 링크 복사 없이 골라서 바로 만들기**")
        with st.expander("🔗 제휴 링크·워터마크 설정", expanded=False):
            _w_stay_settings()
        f1, f2, f3 = st.columns([2, 2.6, 1.4], vertical_alignment="bottom")
        with f1:
            st.radio("랭킹 기준", list(sc.MODES), format_func=sc.MODES.get, key="stay_mode", horizontal=False)
        with f2:
            st.selectbox("지역·테마", list(sc.PRESETS), key="stay_preset")
        with f3:
            find_btn = st.button("🔍 인기 숙소 찾기", key="stay_find", type="primary", use_container_width=True)
        if find_btn:
            _find_stay_top5()
        top = ss.get("stay_top5")
        if top:
            for n in top.get("notes") or []:
                st.info(n)
            if top.get("error") and not top.get("items"):
                st.warning(top["error"])
            if top.get("trend") and top.get("picked"):
                tr = top["trend"]
                st.caption("📈 데이터랩 기준 상위 키워드: " + " · ".join(
                    f"{k}(" + (f"최근7일 {tr[k]['surge']:.1f}배" if ss.get("stay_mode", "season") == "season" else f"30일 평균 {tr[k]['steady']:.0f}") + ")" for k in top["picked"] if k in tr))
            for i, it in enumerate(top.get("items") or []):
                with st.container(border=True):
                    c1, c2 = st.columns([4, 1.6], vertical_alignment="center")
                    with c1:
                        st.markdown(f"**{i + 1}. {it['name']}**  \n📍 {it['address'] or it['region']}  \n✨ {it['highlight']}")
                        st.caption(f"블로그 후기 {it['blog_total']:,}건 · 최근 30일 {it['recent']}건 · 테마 '{it['trend_keyword']}'")
                    with c2:
                        if st.button("🚀 이 숙소 선택 및 자동 수집 시작", key=f"stay_pick_{i}", use_container_width=True, type="primary"):
                            _pick_stay(it)
                            st.rerun()
        st.checkbox("🛡️ 공식 홍보 이미지만 수집 (블로그 개인 사진 제외)", value=True, key="official_only",
                    help="네이버 플레이스·여기어때 공식 사진을 우선하고 개인 블로거 사진은 뺍니다. 사람 얼굴·워터마크·글씨가 있는 사진은 이 옵션과 상관없이 항상 자동 제외돼요.")
        with st.expander("✍️ 직접 입력 (숙소 이름 또는 여기어때 링크)", expanded=False):
            m1, m2, m3 = st.columns([2.4, 2.6, 1.4], vertical_alignment="bottom")
            with m1:
                man_name = st.text_input("숙소 이름", key="stay_manual_name", placeholder="예: 가평 ○○ 풀빌라")
            with m2:
                man_url = st.text_input("여기어때 상세 링크 (선택)", key="stay_manual_link", placeholder="https://www.yeogi.com/domestic-accommodations/...")
            with m3:
                man_btn = st.button("🚀 자동 수집 시작", key="stay_manual_go", use_container_width=True)
            if man_btn and man_name.strip():
                from core.naver_local import NaverLocalSearch, region_of
                found = {}
                try:
                    hits = NaverLocalSearch().search(man_name.strip(), 1) if NaverLocalSearch().enabled else []
                    found = hits[0] if hits else {}
                except Exception:
                    found = {}
                addr = found.get("road_address") or found.get("address") or ""
                _pick_stay({"name": man_name.strip(), "address": addr, "region": " ".join(addr.split()[:2]), "category": found.get("category", ""),
                            "link": ""}, manual_url=man_url.strip())
                st.rerun()
    ctx = ss.get("stay_ctx")
    if not ctx:
        return
    f = _stay_facts_now()
    y = ctx.get("yeogi") or {}
    with st.container(border=True, key="stay_facts_box"):
        st.markdown(f"**📋 수집 결과 — {ctx['stay']['name']}**")
        if y.get("url"):
            st.caption(f"✅ 여기어때 상세 매칭: {y['url']}")
        else:
            st.caption("ℹ️ " + (y.get("error") or "여기어때 상세 페이지를 확인하지 못했어요") + " — 네이버 블로그·웹 자료로 요금·시설을 찾았어요. 아래에 여기어때 링크를 붙여 넣으면 제휴 링크가 정확해져요.")
        k1, k2, k3 = st.columns(3)
        with k1:
            st.number_input("1박 최저 요금(원)", min_value=0, step=10000, key="stay_price_input",
                            help="자동으로 못 찾았으면 0이에요. 사실만 직접 입력하면 1인 체감가가 계산돼요. 0이면 대본에 가격 숫자를 쓰지 않아요.")
        with k2:
            st.number_input("기준 인원(명)", min_value=1, max_value=30, step=1, key="stay_people_input",
                            help="기준 인원을 못 찾으면 4인으로 계산해요 (추정).")
        with k3:
            st.metric("1인 체감가", f["per_person"] or "미확인", help="1박 요금 ÷ 기준 인원")
        st.caption(("💰 요금 출처: " + {"yeogi": "여기어때", "blog": "네이버 블로그 요약", "manual": "직접 입력"}.get(f.get("price_source") or "", "없음"))
                   + (" · 인원은 4인 추정" if f.get("people_estimated") else "") + " · 시설: " + (" · ".join(f["facilities"]) or "확인된 시설 없음"))
        st.text_input("상단 워터마크 별칭 (숙소 본명 대신)", key="stay_alias_input", help="시청자가 포털에서 숙소명을 직접 검색해 제휴 링크를 건너뛰지 않도록 본명 대신 특징 별칭을 써요. 사이드바에서 끌 수도 있어요.")
        st.text_input("여기어때 상세 링크 (직접 붙여 넣기)", key="stay_manual_url", placeholder="자동 매칭이 안 됐을 때만")
        lk = _stay_link_now()
        st.session_state.affiliate_program, st.session_state.affiliate_link = "yeogi", lk["link"]
        if lk["note"]:
            st.warning(lk["note"])


def _shop_roles_for(paths: list) -> list:
    """쇼핑 사진 역할(usage·detail·space·package·official): 직접 추가한 사진은 실사용 컷으로"""
    rb = (st.session_state.get("shop_item") or {}).get("role_by_path") or {}
    return [rb.get(p, "usage") for p in paths]


def _shop_official_last():
    """쇼핑: 공식 대표 썸네일은 항상 마지막 컷 (CTA 씬: 댓글 링크로 들어간 상품 페이지에서 바로 알아보게)"""
    ss = st.session_state
    off = (ss.get("shop_item") or {}).get("official_path")
    paths = [p for p in ss.uploaded_paths if os.path.exists(p)]
    if off and off in paths and paths[-1] != off:
        paths.remove(off)
        paths.append(off)
        ss.uploaded_paths = paths


def _use_selected_web_photos(checked: list, keep_gallery: bool = False):
    """체크한 후보 사진을 내려받아(원본 → 안 되면 썸네일) 영상 소스 목록에 직접 올린 사진처럼 추가"""
    import core.store_photos as _sp
    saved = []
    with st.spinner(f"선택한 사진 {len(checked)}장을 가져오는 중..."):
        for it in checked:
            p = _sp.download_store_image(it, current_job().uploads_dir)
            if p:
                saved.append(p)
                si = st.session_state.get("shop_item")
                if si is not None:
                    si.setdefault("role_by_path", {})[p] = it.get("cut_role") or it.get("bucket") or "usage"
    if not saved:
        st.session_state.store_photo_msg = "사진을 내려받지 못했어요 (원본·썸네일 모두 실패). 다른 사진을 골라 보거나 직접 올려 주세요."
        return
    paths = [p for p in st.session_state.uploaded_paths if os.path.exists(p)]
    added = add_media(paths, saved)
    st.session_state.web_photo_paths = list(dict.fromkeys(list(st.session_state.get("web_photo_paths", [])) + saved))
    _media_changed(added)
    _shop_official_last()
    n_new = len(added) - len(paths)
    if not keep_gallery:
        st.session_state.store_photo_candidates = []
    st.session_state.store_photo_msg = (f"✅ 사진 {n_new}장을 영상 소스에 넣었어요" + (f" (내려받기 실패 {len(checked) - len(saved)}장 제외)" if len(saved) < len(checked) else "")
                                        + ". 아래 '영상 소스'에서 순서를 바꾸거나 빼고, 스토리보드를 기획하세요.")


def _render_photo_gallery():
    """수집된 후보 사진 최대 12장을 4열 갤러리로: 각 사진 밑 '✅ 사용' 체크(상위 8장 기본), 사람 얼굴·영수증·메뉴판 글자 사진은 체크 해제"""
    cands = st.session_state.get("store_photo_candidates") or []
    if st.session_state.get("store_photo_msg"):
        st.info(st.session_state.store_photo_msg)
    if not cands:
        return
    from core.store_photos import DEFAULT_CHECKED
    st.caption("⚠️ 웹에서 찾은 사진은 블로거·방문자가 찍은 사진일 수 있어요. 샘플 확인용으로만 쓰고, **공개 업로드 전에는 점주·숙소 측 동의를 받거나 직접 촬영한 사진으로 교체**해 주세요. "
               "사람 얼굴(초상권)·영수증 종이·텍스트 메뉴판이 보이는 사진은 체크를 해제하세요.")
    from core.photo_safety import FLAG_LABELS
    ok_idx = [i for i, it in enumerate(cands) if not it.get("flag") and it.get("source") != "blog"]
    default_on = set(ok_idx[:DEFAULT_CHECKED])                       # 기본 체크: 검수를 통과한 공식·기타 사진 위쪽 8장 (블로그 사진은 직접 켜야 함)
    n_flag = sum(1 for it in cands if it.get("flag"))
    if n_flag:
        st.caption(f"🛡️ 사람 얼굴·워터마크·글씨가 감지된 사진 {n_flag}장은 자동으로 제외했어요 (선택할 수 없어요).")
    with st.container(key="photo_grid"):
        cols = st.columns(4)
        for i, it in enumerate(cands):
            with cols[i % 4]:
                try:
                    st.image(it.get("thumbnail") or it.get("link"), use_container_width=True)
                except Exception:
                    st.caption("(미리보기 불가)")
                flag = it.get("flag")
                if flag:
                    st.checkbox(f"🚫 제외: {FLAG_LABELS.get(flag, flag)}", value=False, key=f"webflag_{i}", disabled=True)
                    continue
                if it.get("source") == "blog":
                    st.caption("⚠️ 개인 블로그 사진 (저작권 위험)")
                elif it.get("source") == "official":
                    st.caption("🛡️ 업체 공식 사진")
                st.checkbox("✅ 사용", value=i in default_on, key=f"webpick_{i}", help=(it.get("title") or "")[:60])
    checked = [it for i, it in enumerate(cands) if not it.get("flag") and st.session_state.get(f"webpick_{i}", i in default_on)]
    st.button(f"📥 선택한 사진 {len(checked)}장을 영상 소스에 넣기", key="store_photo_use", type="primary", disabled=not checked,
              on_click=_use_selected_web_photos, args=(checked,))


def _run_store_research(name: str, address: str = ""):
    """상호명(+주소)으로 공식 API·웹 검색 그라운딩을 병렬 수집 → 주소(비어 있을 때)·가게 자랑(USP)·🔎 고유 팩트에 자동 반영. Mock 모드에서는 건너뜀"""
    if mock_mode or not (name or "").strip():
        return
    import core.store_research as _sr
    from core.planners.owner import short_region
    from core.store_facts import facts_to_points
    region = short_region(region_of(address)) if address else ""
    with st.spinner("🌐 상호명으로 주소·대표 메뉴·후기 팩트를 모으는 중... (네이버 공식 API + 웹 검색, 10~20초)"):
        res = _sr.research_store(name.strip(), address, region=region, gemini_api_key=api_key,
                                 naver_id=os.getenv("NAVER_CLIENT_ID", ""), naver_secret=os.getenv("NAVER_CLIENT_SECRET", ""))
    st.session_state.store_research = res
    if not st.session_state.get("owner_address", "").strip():
        addr = res.get("address") or res.get("address_candidate") or ""
        if addr:
            st.session_state.owner_address = addr
            st.session_state.research_addr_note = ("공식 지역 검색 결과" if res.get("address_from") == "naver" else "AI 웹 검색 결과 (맞는지 확인해 주세요)")
    if not st.session_state.get("owner_type_touched") and res.get("business_type"):
        st.session_state.owner_type = res["business_type"]
    existing = st.session_state.get("owner_usp", "").strip()
    pts = [p for p in facts_to_points(res.get("confirmed") or {}) if p and p not in existing]
    if pts:
        st.session_state.owner_usp = " / ".join([x for x in [existing] + pts if x])
    _search_store_photos(name, st.session_state.get("owner_address", "") or address, quiet=True)      # 가게 정보와 함께 후보 사진 12장도 한 번에 수집


# ==============================================================================
# 1단계: 매장 정보 & 사진 (또는 상품·숙소·도서 정보·직가공 영상)
# ==============================================================================
single_v_detected = bool(st.session_state.uploaded_paths and len(st.session_state.uploaded_paths) == 1 and is_video_file(st.session_state.uploaded_paths[0]))

if single_v_detected and not is_video_overlay_mode:
    with st.container(border=True):
        b_c1, b_c2 = st.columns([3.8, 1.2], vertical_alignment="center")
        with b_c1:
            st.markdown("📹 **직접 촬영한 단일 동영상이 감지되었습니다!** 영상 움직임을 100% 살리는 **[📹 촬영 영상 원본 직가공]** 모드로 작업하시겠습니까?")
        with b_c2:
            def _switch_to_overlay():
                # 콜백은 다음 실행 전에 돌아 이미 만들어진 위젯(mode_switch) 값도 바꿀 수 있음
                st.session_state._keep_uploads_on_switch = True
                st.session_state.production_mode = "video_overlay"
                st.session_state.mode_switch = MODE_LABELS["video_overlay"]
            st.button("📹 직가공 모드로 전환", key="btn_switch_overlay_banner", type="primary",
                      use_container_width=True, on_click=_switch_to_overlay)

if is_owner_mode:
    _oh1, _oh2 = st.columns([5, 1.6], vertical_alignment="center")
    with _oh1:
        st.markdown("### [1단계] 🏢 매장 정보 & 사진·영상 준비")
    with _oh2:
        st.toggle("🎯 점주 시연 모드", key="demo_mode", help="소상공인 점주 미팅·강의 시연용: 매장 홍보만 보이는 단일 목적 앱으로 바꿔요. 항상 켜려면 환경변수 DEMO_MODE=1 또는 주소 뒤 ?demo=1")
elif is_travel_mode:
    st.markdown("### [1단계] 🏨 숙소 정보 & 사진·영상 준비")
elif is_commerce_mode:
    st.markdown("### [1단계] 🛍️ 상품 정보 & 사진·영상 준비")
elif is_book_mode:
    st.markdown("### [1단계] 📚 도서 정보 & 사진 준비")
elif is_video_overlay_mode:
    st.markdown("### 📹 1. 촬영 영상 정보 & 직가공 옵션")
    st.caption("사용자가 직접 촬영한 10~60초 영상의 원래 움직임을 100% 살려 '1컨셉 대본 + TTS 나레이션 + 자막/뱃지'를 직가공 합성합니다.")

if is_travel_mode:
    _render_stay_pipeline()

# 사장님: 네이버 플레이스 링크·상호명 → 공식 지역 검색 API로 상호명·업종·주소·소개글 자동 주입
if is_owner_mode:
    naver = NaverLocalSearch()
    with st.container(border=True, key="place_box"):
        st.markdown("**🔗 네이버 플레이스·지도 링크 또는 상호명으로 가게 정보 가져오기**")
        q1, q2 = st.columns([3.5, 1.5])
        with q1:
            place_q = st.text_input("플레이스 링크 또는 상호명", key="place_query", label_visibility="collapsed",
                                    placeholder="예: https://naver.me/xxxx 또는 성수 불판")
        with q2:
            place_btn = st.button("🏪 가게 정보 가져오기", key="place_fetch", use_container_width=True, type="primary", help="가게 정보(상호·업종·주소·대표 메뉴)와 후보 사진 12장을 한 번에 가져와요")
        if place_btn and place_q.strip():
            q = place_q.strip()
            link_mode = is_naver_link(q)
            if link_mode:
                st.session_state.place_link = q
                st.session_state.place_id = place_id_from_link(q)
            elif not st.session_state.get("owner_name", "").strip():
                st.session_state.owner_name = q
            if not naver.enabled:
                st.info("네이버 검색 API 키(NCP NAVER API HUB의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET)를 .env에 넣으면 "
                        "상호명·업종·주소가 자동으로 채워져요. "
                        "지금은 아래에 직접 입력해 주세요." + ("" if link_mode else " (상호명은 입력하신 값으로 채워 두었어요)"))
                _run_store_research(st.session_state.get("owner_name", "").strip() or q, st.session_state.get("owner_address", ""))
            else:
                query = st.session_state.get("owner_name", "").strip() if link_mode else q
                if not query:
                    st.warning("링크만으로는 상호명을 알 수 없어요 (네이버 페이지는 열지 않습니다). 아래 상호명 칸을 채우고 다시 눌러 주세요.")
                else:
                    try:
                        found = naver.search(query)
                        st.session_state.place_results = found
                        if not found:
                            st.info("검색 결과가 없어요. 상호명에 지역을 붙여(예: 성수 불판) 다시 찾거나 아래에 직접 입력해 주세요.")
                            _run_store_research(query, "")          # 지역 검색에 없어도 웹 검색으로 메뉴·후기 팩트는 시도
                        elif len(found) == 1:
                            # 결과가 하나면 고를 필요 없이 바로 채움 (한 번 클릭으로 끝)
                            _fill_place(found[0])
                            st.success(f"'{found[0]['name']}' 정보를 채웠어요 ({found[0]['category']}). 사진·영상은 아래에서 직접 올려 주세요.")
                            _run_store_research(found[0]["name"], st.session_state.get("owner_address", ""))
                        else:
                            _run_store_research(query, "")          # 여러 곳이면 고르기 전에도 이름만으로 웹 검색 (고른 뒤 주소로 다시 수집)
                    except NaverAPIError as ne:
                        st.error(str(ne))
        for i, it in enumerate(st.session_state.get("place_results", [])):
            r1, r2 = st.columns([4, 1.3], vertical_alignment="center")
            with r1:
                st.markdown(f"**{html.escape(it['name'])}** · {html.escape(it['category'])}  \n"
                            f"{html.escape(it['road_address'] or it['address'])}")
            with r2:
                if st.button("이 가게로 채우기", key=f"place_pick_{i}", use_container_width=True):
                    _fill_place(it)
                    _run_store_research(it["name"], st.session_state.get("owner_address", ""))
                    st.toast("상호명·업종·주소·소개글·대표 메뉴를 채웠어요. 사진·영상은 아래에서 직접 올려 주세요.", icon="🏢")
                    st.rerun()
        st.checkbox("🛡️ 공식 홍보 이미지만 수집 (블로그 개인 사진 제외)", value=True, key="official_only",
                    help="개인 블로거가 찍은 사진(blogfiles·postfiles·mblogthumb 등)은 저작권·초상권 분쟁 위험이 커서 기본으로 뺍니다. 끄면 블로그 사진도 후보에 나오지만 맨 뒤에 경고와 함께 표시돼요.")

# 상품/가게 정보 입력 폼 (모드별)
business_type, owner_search_kw = None, ""
# ==============================================================================
# 전 모드 공통 표준 1~5단계 (접이식 없음): [1]대상·정보·사진 → [2]컨셉 → [3]대본 → [4]소리·자막 + 영상 만들기 → [5]확인·업로드
# ==============================================================================
SIMPLE_UX = False
edit_box = st.container()


with edit_box:
    if is_owner_mode:
        o1, o2 = st.columns([1.2, 1])
        with o1:
            product_name = st.text_input("🏢 상호명 (영상에 그대로 노출)", key="owner_name", placeholder="예: 성수 불판 성수점")
        with o2:
            biz_keys = [str(k) for k in BUSINESS_TYPES if k]
            cur_bt = st.session_state.get("owner_type")
            bt_idx = biz_keys.index(cur_bt) if cur_bt in biz_keys else 0
            business_type = st.selectbox(
                str("업종" or ""),
                biz_keys, index=bt_idx, key="owner_type",
                format_func=lambda k: str(BUSINESS_TYPES.get(k, {}).get("label", k or ""))
            )
        o3, o4 = st.columns([1, 2])
        with o3:
            owner_search_kw = st.text_input("🔍 네이버 검색어", key="owner_search_kw", placeholder="기본값: 상호명 사용",
                                            help="영상 엔딩에서 네이버 검색을 유도할 키워드입니다. 상호명이 기본값으로 들어갑니다.")
        with o4:
            usp_desc = st.text_input("✨ 우리 가게 자랑 (USP)", key="owner_usp",
                                     placeholder="예: 성수역 2분 / 단체석 30명 / 숙성 삼겹살",
                                     help="네이버 플레이스 소개글이 기본값으로 들어가며, 필요시 자유롭게 수정해 주세요.")
        if len(re.findall(r"[가-힣A-Za-z0-9]{2,}", usp_desc or "")) < 4:
            st.caption("💡 대본의 고유함은 여기 적은 사실에서 나와요. 반찬·메뉴 이름, 손질·숙성 방식, 손님 리뷰 문구, 숫자(가짓수·두께)를 "
                       "구체적으로 적을수록 '어느 가게나 쓰는 문구' 대신 우리 가게만의 대본이 나옵니다. (적지 않은 사실은 지어내지 않아요)")
        with st.expander("📋 메뉴판·손님 리뷰 글 붙여넣기 (선택) — 대본이 우리 가게 고유 메뉴·리뷰 단어로 쓰여요", expanded=False):
            st.caption("상호명으로 자동 수집한 결과 위에 더 보탤 때 쓰세요. 사장님이 보시는 메뉴판 글, 스마트플레이스의 영수증 리뷰를 "
                       "그대로 복사해 붙여 넣으면 ① 독특한 메뉴명(눈꽃버섯 갈비탕처럼 수식어가 붙은 것) ② 리뷰에서 형용사를 빼고 2번 이상 반복된 "
                       "명사만 뽑아 대본에 써요. 붙여 넣은 글에 없는 말은 지어내지 않아요.")
            st.text_area("메뉴판 글", key="owner_menu_text", height=110, placeholder="예: 눈꽃버섯 갈비탕 15,000원 / 굴돌솥비빔밥 12,000원 … (한 줄에 메뉴 하나)")
            st.text_area("영수증 리뷰 글 (여러 개 붙여 넣기)", key="owner_review_text", height=140, placeholder="리뷰 문장을 그대로 붙여 넣어 주세요 (3~10개 정도면 충분해요)")
        _mf = _merged_store_facts()
        if _mf["special_menus"] or _mf["review_nouns"] or _mf["location_context"]:
            st.success("🔎 뽑힌 고유 팩트 — 특이 메뉴: " + (", ".join(_mf["special_menus"]) or "없음") + " · 리뷰 반복 명사: "
                       + (", ".join(_mf["review_nouns"]) or "없음") + " · 방문 상황: " + (_mf["location_context"] or "없음"))
        _sr_state = st.session_state.get("store_research") or {}
        _unc = _sr_state.get("unconfirmed") or {}
        if _sr_state:
            _stp = _sr_state.get("steps") or {}
            st.caption("🌐 자동 수집: " + " · ".join([
                "네이버 지역 검색 " + ("✅" if _stp.get("naver_local") else "—"),
                f"블로그 요약 {_stp.get('naver_blog', 0)}건",
                "웹 검색(Gemini) " + ("✅ 출처 확인" if _stp.get("grounded") else ("✅ 요약만" if _stp.get("gemini") else "—")),
            ]) + (f" · 주소: {st.session_state.get('research_addr_note')}" if st.session_state.get("research_addr_note") else ""))
        if _unc.get("special_menus") or _unc.get("review_nouns") or _unc.get("location_context"):
            st.checkbox("확인 못 한 검색 요약도 대본에 쓰기 (근거 링크·블로그에서 다시 확인되지 않은 AI 요약 항목)", key="use_unconfirmed_facts")
            st.caption("❔ 미확인(출처 확인 전): " + (", ".join((_unc.get("special_menus") or []) + (_unc.get("review_nouns") or [])) or "없음")
                       + (f" · 주변: {_unc.get('location_context')}" if _unc.get("location_context") else "")
                       + " — 사실이 맞으면 위 체크를 켜거나 가게 자랑 칸에 직접 적어 주세요.")
        o5, o6 = st.columns([2, 1], vertical_alignment="bottom")
        with o5:
            owner_address = st.text_input("📍 주소·지역 (선택, 엔딩 카드에 표시)", key="owner_address", placeholder="예: 서울 성동구 성수일로")
        with o6:
            st.button("🤖 사진으로 매장 특징 정리", key="store_points_btn", use_container_width=True, on_click=_fill_store_points,
                      args=(api_key, mock_mode), help="올린 사진·영상을 보고 손님 눈길을 끌 특징을 자랑 칸에 보탭니다 (숫자·가격은 넣지 않음)")
        if st.session_state.get("store_points_msg"):
            st.caption(st.session_state.pop("store_points_msg"))
        # 영문·숫자·기호가 섞인 상호는 TTS가 철자로 읽거나 엉킴 → 소리 나는 대로 적으면 나레이션에만 적용 (자막·워터마크는 원래 표기)
        tricky_name = bool(re.search(r"[A-Za-z0-9&'·|]", product_name or ""))
        st.text_input("🗣️ 상호명 읽는 법 (선택, 나레이션에만 적용)", key="owner_reading",
                      placeholder="예: 세인트존스 호텔" if tricky_name else "예: 성수 불판 (영문·숫자가 섞인 상호일 때 적어 주세요)",
                      help="자막·워터마크는 상호명 그대로, AI 성우만 이 발음으로 읽어요. 예: 'St. John's' → '세인트존스'")
        if tricky_name and not st.session_state.get("owner_reading", "").strip():
            st.caption("💡 상호명에 영문·숫자가 있어요. 성우가 철자로 읽을 수 있으니 '읽는 법'에 한글 발음을 적어 주시면 깔끔해요.")
        owner_search_kw = owner_search_kw.strip() or product_name.strip()
    elif is_video_overlay_mode:
        # 촬영 영상 원본 직가공 쇼츠 전용 간이 옵션
        vo_c1, vo_c2 = st.columns([1, 1])
        vo_audio_opts = [str(opt) for opt in ["배경음으로 작게 유지 (볼륨 20%)", "음소거 (TTS 나레이션만)"] if opt]
        cur_vo_aud = st.session_state.get("video_audio_opt") or st.session_state.get("video_sound_option")
        vo_aud_idx = vo_audio_opts.index(cur_vo_aud) if cur_vo_aud in vo_audio_opts else 0
        with vo_c1:
            chosen_aud = st.radio(
                str("🔊 원본 사운드 유지 여부" or ""),
                vo_audio_opts, index=vo_aud_idx,
                key="video_audio_opt", horizontal=True,
                format_func=lambda x: str(x or ""),
                help="촬영 현장음을 배경음으로 20% 깔아줄지, 아니면 깔끔하게 음소거하고 성우 목소리만 넣을지 선택합니다."
            )
            st.session_state["video_sound_option"] = chosen_aud

        vo_fit_opts = [str(opt) for opt in ["9:16 중앙 포커스 크롭 (기본)", "상하 블러 패딩"] if opt]
        cur_vo_fit = st.session_state.get("video_fit_opt") or st.session_state.get("crop_option")
        vo_fit_idx = vo_fit_opts.index(cur_vo_fit) if cur_vo_fit in vo_fit_opts else 0
        with vo_c2:
            chosen_fit = st.radio(
                str("📐 영상 화면 비율 맞춤 (가로 영상 시)" or ""),
                vo_fit_opts, index=vo_fit_idx,
                key="video_fit_opt", horizontal=True,
                format_func=lambda x: str(x or ""),
                help="가로(16:9) 영상일 때 중앙을 크롭해 꽉 채울지, 원본 전체를 보여주며 상하 블러를 넣을지 선택합니다."
            )
            st.session_state["crop_option"] = chosen_fit

        vo_i1, vo_i2 = st.columns([1.2, 1])
        with vo_i1:
            product_name = st.text_input("🏢 매장/브랜드명 또는 영상 제목", key="video_overlay_name", placeholder="예: 이마트24 덕계메디프라자점")
        with vo_i2:
            vo_biz_keys = [str(k) for k in BUSINESS_TYPES if k]
            cur_vo_bt = st.session_state.get("video_overlay_type")
            vo_bt_idx = vo_biz_keys.index(cur_vo_bt) if cur_vo_bt in vo_biz_keys else 0
            business_type = st.selectbox(
                str("업종/분야" or ""),
                vo_biz_keys, index=vo_bt_idx, key="video_overlay_type",
                format_func=lambda k: str(BUSINESS_TYPES.get(k, {}).get("label", k or ""))
            )
        usp_desc = st.text_input("💡 핵심 매력/특장점 (USP, 사실만)", key="video_overlay_usp",
                                 placeholder="예: 1+1 행사 상품 가득, 24시간 언제든 편하게 이용")

        single_v_file = st.session_state.uploaded_paths[0] if (st.session_state.uploaded_paths and is_video_file(st.session_state.uploaded_paths[0])) else None
        if single_v_file:
            v_info = probe_media(single_v_file)
            st.success(f"📹 **감지된 원본 영상:** `{os.path.basename(single_v_file)}` ({v_info.width}x{v_info.height}, {v_info.duration:.1f}초, 현장음: {'있음' if v_info.has_audio else '없음'})")
            st.video(single_v_file)
        else:
            st.info("💡 아래 미디어 업로더에서 직접 촬영하신 동영상(MP4/MOV) 1개를 업로드해 주세요.")
    elif is_book_mode:
        st.session_state.setdefault("book_photo_count", 1)
        st.session_state.setdefault("book_template", "basic")
        book_tpl_map = {
            "basic": "📖 기본 큐레이션 (한 권 · 약 30~35초)",
            "fable": "🕯️ 우화 (원문 낭독 · 50~75초)",
            "reflect": "🌙 사유 (책소개 기반 · 원문 없이 50~60초)"
        }
        book_tpl_keys = [str(k) for k in BOOK_TEMPLATES if k]
        cur_book_tpl = st.session_state.get("book_template")
        book_tpl_idx = book_tpl_keys.index(cur_book_tpl) if cur_book_tpl in book_tpl_keys else 0
        st.radio(
            str("시골책방 템플릿" or ""),
            book_tpl_keys, index=book_tpl_idx, key="book_template", horizontal=True,
            format_func=lambda k: str(book_tpl_map.get(k, k or ""))
        )
        _fable = st.session_state.book_template == "fable"
        k1, k2 = st.columns([1, 1])
        with k1:
            st.text_input("책방 이름 (라벨 바·CTA)", key="book_store", placeholder="예: 시골책방")
        with k2:
            st.text_input("네이버 검색어 (첫 댓글 지도 링크)", key="book_search_kw", placeholder="비우면 책방 이름으로 검색")
        k3, k4 = st.columns([1.4, 1])
        with k3:
            st.text_input("책 제목", key="book_title", placeholder="예: 작별하지 않는다")
        with k4:
            st.text_input("저자", key="book_author", placeholder="예: 한강")
        st.button("📚 책 정보 자동 채우기 (카카오·국립중앙도서관·쿠팡 표지/링크)", key="book_autofill", on_click=_book_autofill,
                  help="책 제목(또는 ISBN)으로 저자·책소개·고화질 표지·구매 링크를 채워요. 이미 적은 칸은 그대로 두고, "
                       "표지는 올린 사진 맨 앞(1번째 = 책 표지)에 넣어요.")
        _af = st.session_state.pop("book_autofill_msg", None)
        if _af:
            (st.error if _af[0] == "error" else st.success)(_af[1])
            for _w in _af[2]:
                st.caption(f"⚠️ {_w}")
        st.text_area("책 소개 (출판사 소개·장르 등 사실만 - 대본 근거)", key="book_info", height=80,
                     placeholder="예: 장편소설. 제주 4·3을 다룬 이야기. (베스트셀러·수상 같은 말은 여기 있을 때만 대본에 들어가요)")
        if _fable:
            st.text_area("📖 책 속 우화·문장 (원문 그대로, 필수 · 350자 이내 · 문장 단위로 카드 여러 장)", key="book_quote", height=140,
                         help="AI가 문장을 만들지 않아요. 원문을 문장 경계에서 나눠 카드로 넘기며 천천히(-5%) 낭독해요. "
                              "책 제목은 마지막 장면에서 처음 공개돼요 (앞 장면엔 표지·출처를 안 보여 줘요).")
            _ql = len((st.session_state.get("book_quote") or "").strip())
            st.caption(f"원문 {_ql}/350자 · 예상 영상 길이 약 {int(24 + _ql * 0.13)}초 "
                       "(실측: 원문 200자 ≈ 50초, 350자 ≈ 70초 → 50~75초 호흡은 원문 200~350자)")
        else:
            st.text_area("📖 책 속 한 문장 (원문 그대로, 선택 · 90자 이내)", key="book_quote", height=70,
                         help="AI가 문장을 만들지 않아요. 비워 두면 인용 장면 없이 약 28초로 만들어요.")
        st.text_input("알라딘 구매 링크 (알라딘 제휴 링크 그대로 붙여 넣기)", key="book_link",
                      placeholder="https://www.aladin.co.kr/shop/wproduct.aspx?ItemId=...")
        from core.shorts_meta import shop_link_kind
        _bl = (st.session_state.get("book_link") or "").strip()
        if _bl and shop_link_kind(_bl) != "aladin":
            st.warning("알라딘 링크가 아니에요. 그대로 쓰면 그 링크 종류의 제휴 고지가 붙어요.")
        cur_bpc = st.session_state.get("book_photo_count", 1)
        bpc_opts = [1, 2]
        bpc_idx = bpc_opts.index(cur_bpc) if cur_bpc in bpc_opts else 0
        st.radio(
            str("올린 사진 중 책 사진" or ""),
            bpc_opts, index=bpc_idx, key="book_photo_count", horizontal=True,
            format_func=lambda n: "1번째 = 책 표지" if n == 1 else "1번째 = 표지, 2번째 = 펼친 면"
        )
        if _fable and not bgm_mod.book_default_track():
            st.caption("🎹 우화 템플릿은 잔잔한 피아노 BGM을 권장해요. 피아노 음원을 assets/bgm에 넣고 .env에 "
                       "`BGM_BOOK_TRACK=파일이름`을 적으면 시골책방에서 자동으로 켜져요.")
        product_name = (st.session_state.get("book_store") or "").strip()
        usp_desc = (st.session_state.get("book_info") or "").strip()
    elif is_bundle:
        st.markdown(f"##### 🧺 묶음 상품 {BUNDLE_SIZE}개 (판매 페이지에 있는 사실만 · 링크는 쿠팡 파트너스/스마트스토어 제휴 링크)")
        b1, b2 = st.columns([1, 1])
        with b1:
            st.text_input("묶음 주제 (상단 타이틀 바 재료)", key="bundle_theme", placeholder="예: 자취생 삶의 질")
        with b2:
            st.text_input("하단 구매 안내 바 (영상 내내 고정)", key="bundle_bottom")
            _bad_bar = bundle_title_ok(st.session_state.bundle_bottom)
            if _bad_bar and _bad_bar != "빈 값":
                st.warning(f"⚠️ '{_bad_bar}'는 판매 페이지 근거가 있을 때만 쓸 수 있어요 (표시광고법·제휴 정책). 근거 없이 쓰면 계정 정지 사유예요.")
        for _k in range(BUNDLE_SIZE):
            c_n, c_u, c_l = st.columns([1.3, 1.6, 1.4])
            with c_n:
                st.text_input(f"상품 {_k + 1} 이름", key=f"bundle_name_{_k}", placeholder="쿠팡 검색 → [묶음에 추가] 또는 직접 입력")
            with c_u:
                st.text_input(f"상품 {_k + 1} 특징 (USP)", key=f"bundle_usp_{_k}", placeholder="예: 무선, 500g")
            with c_l:
                st.text_input(f"상품 {_k + 1} 구매 링크", key=f"bundle_link_{_k}", placeholder="https://link.coupang.com/...")
            if st.session_state.get(f"bundle_media_{_k}"):
                st.caption(f"📸 상품 {_k + 1} 사진: 쿠팡 대표 사진 {len(st.session_state[f'bundle_media_{_k}'])}장")
        st.caption("💡 사진은 쿠팡 검색으로 추가하면 상품별로 자동 연결돼요. 직접 올리면 올린 순서대로 사진이 없는 상품 1·2·3에 나눠 배정됩니다.")
        _items = bundle_items_now()
        product_name = st.session_state.get("bundle_theme", "").strip() or "묶음 큐레이션"
        usp_desc = ", ".join(it["usp"] for it in _items if it["usp"])
    else:
        # 예시 문구는 placeholder로만 보여 주고 값으로 넣지 않음 (예전 기본값 '파노라마 오션뷰…'가 그대로 대본 근거로 쓰였음)
        col_prod, col_usp = st.columns([1, 2])
        with col_prod:
            prod_label = "숙소명 (대본에서는 숨김 처리)" if is_travel_mode else "홍보 제품명"
            product_name = st.text_input(prod_label, key="product_name_input",
                                         placeholder="예: 강릉 세인트존스 호텔" if is_travel_mode else "예: 유파 PTC 전기히터 TSK-5318")
        with col_usp:
            usp_label = "숙소 특장점 (USP, 사실만)" if is_travel_mode else "핵심 소구점 (USP, 판매 페이지에 있는 사실만)"
            usp_desc = st.text_input(usp_label, key="product_usp_input",
                                     placeholder="예: 오션뷰 인피니티 풀, 실내 수영장" if is_travel_mode else "예: 과열 방지, 2단 온도 조절")
            if is_commerce_mode and st.session_state.get("usp_auto"):
                st.caption("🤖 AI가 상품명·사진으로 정리한 특장점이에요 (상품명에 없는 수치·인증·효능은 자동 제외). 사실과 다르면 고쳐 주세요.")

    # 사장님 모드 브랜드 오버레이: 상호명/검색어는 본문 입력값을 사용
    brand_cfg = None
    if is_travel_mode and st.session_state.get("stay_ctx"):
        from core.planners.stay import STAY_END_CARD_TEXT
        _alias = (st.session_state.get("stay_alias_input") or "").strip()
        brand_cfg = BrandOverlay(business_name=_alias or "숙소", show_label=(st.session_state.get("stay_watermark", "alias") == "alias" and bool(_alias)),
                                 end_card=EndCard(search_keyword="", headline=_alias, cta_text=STAY_END_CARD_TEXT.replace(" [고정", chr(10) + "[고정"), show_search_bar=False))
    if is_owner_mode and brand_opts and product_name.strip():
        brand_cfg = BrandOverlay(
            business_name=product_name.strip(),
            logo_path=brand_opts["logo_path"],
            position=brand_opts["position"],
            show_label=brand_opts["watermark"],
            end_card=EndCard(search_keyword=owner_search_kw,
                             sub_text=brand_opts["sub"] or region_of(st.session_state.get("owner_address", "")))
            if brand_opts["end_card"] else None,
        )

    if uses_scene_media or is_video_overlay_mode:
        img_pool = st.session_state.get("image_pool", [])
        if is_video_overlay_mode:
            st.caption("💡 10초~60초 분량의 단일 동영상(MP4/MOV)을 올려 주세요. 업로드된 영상의 원래 움직임을 100% 살려 직가공합니다.")
        elif is_travel_mode and img_pool:
            st.markdown(
                f"ℹ️ **이미지 메타데이터 풀:** 총 {len(img_pool)}개의 고유 사진 후보가 대기 중입니다. "
                f"아래 **[🚀 스토리보드 자동 기획]**을 실행하면 씬별로 필요한 컷만 핀포인트 다운로드됩니다."
            )
        elif is_owner_mode:
            st.caption("💡 가게 외관, 대표 메뉴·상품, 내부 분위기를 찍은 사진·영상을 순서대로 2~8개 올려 주세요. 올린 순서대로 씬이 만들어집니다.")
        elif is_book_mode:
            st.caption("💡 책 표지(필요하면 펼친 면)를 먼저, 그다음 책방 서가·창가·외관 사진을 올려 주세요. "
                       "책 장면은 2:3 표지 카드로, 책방 장면은 전체 화면으로 나와요. AI로 표지나 책방 사진을 만들지 않아요.")
        elif is_commerce_mode:
            st.caption("💡 판매 페이지에서 가져온 사진과 직접 찍은 사진·영상을 함께 쓸 수 있어요. 위에서부터 씬 순서이며, "
                       "사진이 씬보다 적으면 앵글을 바꿔 돌려 씁니다. AI로 상품 이미지를 만들지 않습니다.")
        else:
            st.caption("💡 숙소 URL을 크롤링하거나, 아래에서 사진·영상을 직접 올릴 수 있습니다.")
        if is_owner_mode or is_travel_mode or (is_commerce_mode and not is_bundle):
            _render_photo_gallery()                 # 자동 수집 후보 사진 12장 (체크로 고르고 영상 소스에 넣기)
            st.markdown("**📤 내 폰·PC 사진 및 직접 찍은 영상(JPG/PNG/MP4/MOV) 추가 업로드** — 자동 사진과 섞어 쓰거나 내 사진·영상만으로도 만들 수 있어요")
        render_media_manager()

st.markdown("---")

# ==============================================================================
# [광고의 핵심] 메인 화면: 시각 팩트 기반 전 업종 AI 떡상 1컨셉 제안 & 선택 엔진
# ==============================================================================
with edit_box:
    st.markdown("### [2단계] 🎯 컨셉 잡기")
    _w_pov()
    st.caption("💡 광고는 1컨셉 1메시지: 영상 전체가 입증할 단 하나의 핵심 이유를 고르세요 (기본안이 자동 선택돼 있어요).")

    st.session_state.setdefault("suggested_concepts", [])
    st.session_state.setdefault("selected_concept_idx", 0)
    st.session_state.setdefault("custom_core_concept", "")
    st.session_state.setdefault("selected_core_concept", "")

    analyze_concept_btn = st.button(
        "🔍 사진 팩트 기반 3대 떡상 컨셉 도출하기",
        type="primary",
        use_container_width=True,
        help="사진 속 보이는 사실과 입력 정보를 교차 분석해 3가지 결핍 앵글 1컨셉을 제안받아요."
    )

    if analyze_concept_btn:
        with st.spinner("👁️ 시각 팩트와 매장 정보를 분석하여 3대 결핍 앵글 1컨셉을 도출하는 중..."):
            single_v_path = st.session_state.uploaded_paths[0] if (st.session_state.uploaded_paths and is_video_file(st.session_state.uploaded_paths[0])) else None
            if is_video_overlay_mode and single_v_path:
                v_analysis = analyze_video_for_overlay(single_v_path, api_key=api_key, mock=mock_mode)
                c_list = []
                icons = ["🔥", "👑", "💡"]
                for i, sc in enumerate(v_analysis.get("suggested_concepts", [])):
                    c_list.append({
                        "title": sc.get("title", f"컨셉 {i+1}"),
                        "badge": f"{icons[i % len(icons)]} {sc.get('title', '')}",
                        "core_concept": sc.get("core_concept", ""),
                        "headline": sc.get("hook_headline", ""),
                        "description": sc.get("target_reason", "")
                    })
                st.session_state.suggested_concepts = c_list
                if c_list:
                    curr_sel = min(st.session_state.get("selected_concept_idx", 0), len(c_list) - 1)
                    st.session_state.selected_core_concept = c_list[curr_sel]["core_concept"]
                    st.toast("동영상 시각 분석 기반 1컨셉 3종이 도출되었습니다!", icon="🎯")
            else:
                c_list = suggest_ad_concepts(
                    business_name=product_name,
                    business_type=business_type,
                    media_paths=list(st.session_state.uploaded_paths),
                    usp=usp_desc,
                    api_key=api_key,
                    thumb_dir=current_job().assets_dir,
                    mock=mock_mode,
                    photo_tags=dict(st.session_state.get("photo_tags") or {}) or None,
                    cache_path=current_job().asset_path("photo_tags.json"),       # 확정된 사진 전체를 보고 업종 맥락에 맞게 제안 (대본 기획과 같은 분석 캐시)
                )
                st.session_state.suggested_concepts = c_list
                if c_list:
                    curr_sel = min(st.session_state.get("selected_concept_idx", 0), len(c_list) - 1)
                    st.session_state.selected_core_concept = c_list[curr_sel]["core_concept"]
                    st.toast("사진 시각 팩트 기반 1컨셉 3종이 도출되었습니다!", icon="🎯")

    # 제안 목록이 비어 있으면 모드별 기본 3종 자동 생성 (매장 문구('우리 매장에서 …')가 쇼핑·숙소 탭에 뜨지 않게 모드 전용)
    if not st.session_state.suggested_concepts:
        if is_owner_mode:
            st.session_state.suggested_concepts = suggest_ad_concepts(
                business_name=product_name,
                business_type=business_type,
                media_paths=list(st.session_state.uploaded_paths),
                usp=usp_desc,
                api_key=None,
                thumb_dir=None,
                mock=True,
            )
        else:
            from core.concepts import default_concepts
            st.session_state.suggested_concepts = default_concepts(pipeline_mode, product_name)
        if st.session_state.suggested_concepts and not st.session_state.selected_core_concept:
            st.session_state.selected_core_concept = st.session_state.suggested_concepts[0]["core_concept"]

    concepts = st.session_state.suggested_concepts or []
    raw_concept_labels = []
    for c in concepts:
        c_badge = str(c.get('badge') or '🎯')
        c_headline = str(c.get('headline') or '')
        c_core = str(c.get('core_concept') or '')
        raw_concept_labels.append(f"{c_badge} | **{c_headline}** ➔ {c_core}")
    raw_concept_labels.append("✍️ 직접 입력 (점주 맞춤형 한 줄 카피)")

    concept_options = [str(opt) for opt in raw_concept_labels if opt]

    cur_concept_val = st.session_state.get("concept_radio_picker")
    if not cur_concept_val or cur_concept_val not in concept_options:
        cur_idx = min(max(0, st.session_state.get("selected_concept_idx", 0) or 0), max(0, len(concept_options) - 1))
        cur_concept_val = concept_options[cur_idx] if concept_options else ""
        st.session_state.concept_radio_picker = cur_concept_val

    chosen_concept = st.radio(
        str("이번 숏폼 광고에서 손님의 뇌리에 꽂을 1컨셉 선택" or ""),
        options=concept_options,
        key="concept_radio_picker",   # 값은 위에서 session_state로 이미 지정 (index 병행 시 Streamlit 경고)
    )

    if chosen_concept == "✍️ 직접 입력 (점주 맞춤형 한 줄 카피)":
        st.session_state.selected_concept_idx = len(concepts)
        custom_label = str("직접 입력할 1컨셉 한 줄 카피" or "")
        custom_val = str(st.session_state.get("custom_core_concept") or "")
        custom_input = st.text_input(
            custom_label,
            value=custom_val,
            placeholder="예: 24시간 언제든 편하게 이용하는 우리동네 안심 매장",
            help="30초 전체를 관통할 단 하나의 핵심 가치를 15~25자 내외로 입력해 주세요."
        )
        st.session_state.custom_core_concept = str(custom_input or "").strip()
        st.session_state.selected_core_concept = str(custom_input or "").strip()
        if custom_input and custom_input.strip():
            st.info(f"🎯 **직접 입력 1컨셉:** {custom_input.strip()}")
    else:
        chosen_idx = concept_options.index(chosen_concept) if chosen_concept in concept_options else 0
        st.session_state.selected_concept_idx = chosen_idx
        if chosen_idx < len(concepts):
            active_c = concepts[chosen_idx]
            st.session_state.selected_core_concept = str(active_c.get("core_concept") or "")
            st.info(f"🎯 **선택된 코어 컨셉:** {st.session_state.selected_core_concept}\n\n💡 *전략 방향:* {str(active_c.get('description') or '')}")

st.markdown("---")

# ==============================================================================
# 2단계: AI 대본 기획 및 실시간 편집 (st.data_editor)
# ==============================================================================
with edit_box:
    st.markdown("### [3단계] 📝 대본 만들기")
    _w_duration()
    if is_travel_mode:
        _w_travel_template()

    generate_rap_btn = False
    if is_owner_mode:
        st.caption("사진을 6~10장 올려도 18초 쇼츠 템포를 지켜요. 두 스타일을 각각 뽑아 비교해 보세요.")
        _b1, _b2 = st.columns(2)
        with _b1:
            generate_plan_btn = st.button("🎵 비트 리듬형 (18초) 대본 만들기 — 추천", use_container_width=True, type="primary", key="generate_plan_btn",
                                          help="5문장 호흡. 안주 소개 구간에서 사진이 1.2~1.5초 간격으로 빠르게 넘어가요.")
        with _b2:
            generate_rap_btn = st.button("⚡ 1컷 1카피 랩형 (18초) 대본 만들기", use_container_width=True, key="generate_rap_btn",
                                         help="사진 N장 = 2초 숏카피 N줄. 컷마다 그 사진에 딱 맞는 한 줄.")
        if generate_plan_btn:
            st.session_state.owner_tempo = "beat"
        elif generate_rap_btn:
            st.session_state.owner_tempo = "rap"
        col_plan_desc = st.container()
    else:
        col_plan_btn, col_plan_desc = st.columns([1.2, 2.8])
        with col_plan_btn:
            generate_plan_btn = st.button("🚀 선택된 컨셉으로 대본 만들기", use_container_width=True, type="primary")
    # 레이더 후킹 패턴 → 씬 1 후킹 힌트 (숙소 제휴 / 사장님 숙소·음식점)
    hook_vertical = vertical_for(pipeline_mode, business_type)
    hook_hints = load_hook_hints(hook_vertical, allow_dryrun=allow_dryrun_hints()) if (hints_enabled() and hook_vertical) else None
    with col_plan_desc:
        if hook_hints:
            avg = sum(r["outlier_ratio"] for r in hook_hints.references) / len(hook_hints.references)
            st.caption(f"🔥 레이더 후킹 패턴 {len(hook_hints.references)}개를 씬 1 힌트로 사용 (평균 {avg:.1f}배)"
                       + (" · ⚠️ Dry-run 테스트 데이터" if hook_hints.is_dryrun else ""))
        elif hook_vertical and hints_enabled():
            st.caption("🔥 레이더 패턴 없음 → 기본 후킹 규칙으로 기획 (🔥 실시간 떡상 레이더 화면에서 수집)")
        concept_badge = f" | 🎯 1컨셉: **{st.session_state.get('selected_core_concept', '')}**" if st.session_state.get('selected_core_concept') else ""
        st.caption(f"모드: **{production_mode}** | 화자: **{script_style_options.get(selected_script_style, '')}** | 말투: **{selected_tone_label}**{concept_badge}")

def _owner_store_facts() -> dict:
    """붙여 넣은 메뉴판·리뷰 글과 주소에서 뽑은 매장 고유 팩트 (글을 고치면 바로 반영되도록 매번 다시 뽑음)"""
    return _merged_store_facts()


def current_spec(bgm_path=None) -> pipeline.JobSpec:
    """지금 화면 입력값 → 공용 제작 엔진 입력 (캘린더 스케줄러는 같은 JobSpec을 일정 설명란에서 채움)"""
    ss = st.session_state
    core_concept_val = ss.get("selected_core_concept", "").strip()
    return pipeline.JobSpec(
        mode=pipeline_mode, name=product_name, usp=usp_desc, media_paths=list(ss.uploaded_paths),
        num_scenes=DURATION_PRESETS[selected_duration]["default_scenes"] + 1,
        business_type=business_type, search_keyword=owner_search_kw, reading=ss.get("owner_reading", ""),
        address=ss.get("owner_address", ""), brand=brand_cfg, caption_font=ss.get("caption_font_choice", ""),
        layout=ss.get("commerce_layout", "single"), link=ss.get("affiliate_link", ""),
        product_boxes=ss.get("product_boxes", {}), bundle_theme=ss.get("bundle_theme", ""),
        bundle_bottom=ss.get("bundle_bottom", ""), bundle_items=bundle_items_now() if is_bundle else [],
        book_title=ss.get("book_title", ""), book_author=ss.get("book_author", ""), book_quote=ss.get("book_quote", ""),
        book_link=ss.get("book_link", ""), book_search_kw=ss.get("book_search_kw", ""),
        book_photo_count=ss.get("book_photo_count", 1), book_template=ss.get("book_template", "basic"),
        book_publisher=ss.get("book_publisher", ""),
        affiliate_program=ss.get("affiliate_program", ""),
        shop_item=({**ss.shop_item, "cut_roles": _shop_roles_for(list(ss.uploaded_paths))} if (is_commerce_mode and not is_bundle and ss.get("shop_item")) else {}),
        core_concept=core_concept_val,
        store_facts=_owner_store_facts() if is_owner_mode else {}, photo_tags=dict(ss.get("photo_tags") or {}) if is_owner_mode else {},
        photo_buckets=dict(ss.get("photo_buckets") or {}) if is_owner_mode else {},
        keep_photo_order=bool(ss.get("keep_photo_order")) if is_owner_mode else False,
        tempo=(ss.get("owner_tempo") or "beat") if is_owner_mode else "beat",
        tone_instruction=selected_tone_instruction,
        voice_actor=ss.get("selected_actor", selected_actor), audio_mode=selected_audio_mode, bgm_path=bgm_path,
        api_key=api_key, mock=mock_mode, hook_hints=hook_hints if (is_owner_mode or is_travel_mode) else None)


# 원클릭(쇼핑): 콘티 없이 '영상 만들기'를 누르면 기획부터 자동으로 하고 이어서 렌더
auto_flow = bool(st.session_state.pop("auto_plan_then_render", False))
auto_plan_only = bool(st.session_state.pop("auto_plan_now", False))      # 대상 선택(카드·검색) 직후 자동 기획 (렌더는 따로)
if generate_plan_btn or generate_rap_btn or auto_flow or auto_plan_only:
    plan_spinner_msg = {
        "owner": "🧠 올린 사진·영상을 보고 우리 가게 홍보 대본을 쓰고 있습니다...",
        "travel": f"🧠 숙소 제휴 스토리보드({script_style_options[selected_script_style]})를 기획하고 있습니다...",
        "commerce": "🧠 쇼핑 제휴 스토리보드를 기획하고 있습니다...",
        "book": "🧠 책 한 권을 차분하게 소개하는 대본을 쓰고 있습니다...",
        "video_overlay": "🧠 촬영 영상의 타임라인과 움직임에 맞춘 1컨셉 대본을 기획하고 있습니다...",
    }[pipeline_mode]
    with st.spinner(plan_spinner_msg):
        try:
            if is_travel_mode and not product_name.strip():
                raise ValueError("숙소명을 입력해 주세요. 링크에서 찾지 못한 정보는 지어내지 않습니다.")
            # 여행 모드: 크롤링 사진 인벤토리를 넘겨 실제 없는 시설을 대본에 쓰지 못하게 함
            photo_inventory = None
            travel_photo_first = is_travel_mode and bool(st.session_state.get("image_pool")) and not st.session_state.uploaded_paths
            selected_concept = st.session_state.get("selected_core_concept", "").strip()
            if is_video_overlay_mode:
                single_v = st.session_state.uploaded_paths[0] if (st.session_state.uploaded_paths and is_video_file(st.session_state.uploaded_paths[0])) else None
                if not single_v:
                    raise ValueError("촬영된 동영상(MP4/MOV)을 먼저 1개 업로드해 주세요.")
                scenes = plan_video_overlay_storyboard(
                    video_path=single_v,
                    core_concept=selected_concept,
                    store_name=product_name,
                    business_type=business_type,
                    usp=usp_desc,
                    script_style=selected_script_style,
                    tone_manner=selected_tone,
                    api_key=api_key,
                    mock=mock_mode,
                )
            elif is_travel_mode and st.session_state.get("stay_ctx") and st.session_state.uploaded_paths:
                scenes = _plan_stay_scenes()
            elif pipeline_mode in pipeline.PIPELINE_MODES:
                # 사장님·쇼핑(단일/묶음)·시골책방: 공용 제작 엔진이 기획 + 씬 사진 배정 (캘린더 스케줄러와 같은 코드)
                scenes = pipeline.plan(current_spec(), current_job())
            elif travel_photo_first:
                # 트랙 A 숙소 제휴: 사진 풀 비전 분류 → 1컨셉 맥락화 대본 생성
                with st.spinner(f"👁️ 사진을 비전으로 분석하고 1컨셉('{selected_concept or '핵심 매력'}') 중심 대본을 쓰는 중..."):
                    scenes, photo_inventory = plan_travel_storyboard(
                        product_name=product_name,
                        usp=usp_desc,
                        image_pool=st.session_state.image_pool,
                        assets_dir=current_job().assets_dir,
                        api_key=api_key,
                        vision_cache=st.session_state.vision_cache,
                        exclude_urls=set(st.session_state.rejected_urls),
                        travel_template=travel_template,
                        mock=mock_mode,
                        tone_and_manner=selected_tone,
                        duration_preset=selected_duration,
                        audio_mode=selected_audio_mode,
                        script_style=selected_script_style,
                        output_plan_path=current_job().plan_path,
                        hook_hints=hook_hints,
                        core_concept=selected_concept,
                    )
            else:
                # 트랙 A: 제휴(숙소/쇼핑) 대본 선기획
                scenes = plan_storyboard(
                    product_name=product_name,
                    usp=usp_desc,
                    tone_and_manner=selected_tone,
                    duration_preset=selected_duration,
                    audio_mode=selected_audio_mode,
                    mock=mock_mode,
                    force=True,
                    api_key=api_key,
                    mode=pipeline_mode,
                    script_style=selected_script_style,
                    photo_inventory=photo_inventory,
                    output_plan_path=current_job().plan_path,
                    travel_template=travel_template,
                    hook_hints=hook_hints if is_travel_mode else None,
                    core_concept=selected_concept,
                )
            st.session_state.storyboard = scenes
            st.session_state.storyboard_mode = pipeline_mode
            st.session_state.storyboard_layout = st.session_state.commerce_layout if is_commerce_mode else ""

            # [씬별 에셋 배정] 엔진 모드는 plan()에서 배정 완료. 숙소 제휴: 업로드 우선 → 나머지 씬만 핀포인트 매칭
            if uses_scene_media:
                if pipeline_mode not in pipeline.PIPELINE_MODES:
                    assign_uploads_to_scenes(scenes, st.session_state.uploaded_paths, cycle=False)
                pending = [s for s in scenes if not scene_has_media(s)]
                if is_travel_mode and pending and st.session_state.get("image_pool"):
                    with st.spinner("🎯 씬별 최적 사진을 매칭하고 비전 검증 중 (자판기·복도·외벽 등 자동 탈락)..."):
                        rematch_scenes(pending, api_key)
                matched = sum(1 for s in scenes if scene_has_media(s))
                if matched:
                    st.toast(f"🎯 {matched}/{len(scenes)}개 씬 사진·영상 배정 완료!", icon="✨")
                if matched < len(scenes):
                    st.warning(f"⚠️ {len(scenes) - matched}개 씬에 사진·영상이 없습니다. 아래 씬 카드에서 업로드해 주세요.")

            st.success(f"🎉 총 {len(scenes)}개 씬 콘티가 기획되었습니다! ({production_mode})")
            if is_travel_mode and st.session_state.get("stay_ctx") and st.session_state.get("stay_arrange_note"):
                st.caption(st.session_state.stay_arrange_note)
            # 쇼츠 최적화 SEO 메타데이터 자동 생성
            try:
                _sp = current_spec()
                _seo = generate_seo_metadata(
                    scenes=scenes,
                    mode=_sp.mode,
                    product_name=_sp.name,
                    usp=_sp.usp,
                    search_keyword=_sp.search_keyword,
                    address=_sp.address,
                    api_key=_sp.api_key,
                    mock=_sp.mock,
                    business_type=_sp.business_type or "",
                    core_concept=getattr(_sp, "core_concept", "") or ""
                )
                current_job().save_metadata(_seo)
                st.session_state.seo_metadata = _seo
            except Exception as se:
                logger.warning(f"기획 단계 SEO 메타데이터 생성 건너뜀: {se}")

            if auto_flow:
                st.session_state.auto_render_now = True
        except Exception as pe:
            st.error(f"기획 실패: {pe}")

# 스토리보드 실시간 편집기 (씬별 카드: 대사 + 지속시간만 노출, 내부용 필드는 숨기고 그대로 보존)
with edit_box:
    if st.session_state.storyboard:
        _ref = (st.session_state.storyboard[0] or {}).get("hook_reference") if st.session_state.storyboard else None
        if _ref:
            _used = st.session_state.storyboard[0].get("hook_pattern_used")
            st.caption("🔥 씬 1 후킹: " + (f"레이더 패턴 적용 [{HOOK_TYPE_KO.get(_used['hook_type'], _used['hook_type'])}] {_used['pattern_template']}"
                                         if _used else "레이더 패턴 참고했으나 사실에 맞는 패턴이 없어 기본 규칙 적용")
                       + " · 참고 " + ", ".join(f"{HOOK_TYPE_KO.get(r['hook_type'], r['hook_type'])} {r['outlier_ratio']:.1f}배" for r in _ref))
        _eng = str((st.session_state.storyboard[0] or {}).get("script_engine") or "")
        _ENGINE_LABEL = {"claude": "Claude", "openai": "GPT(OpenAI)", "gemini": "Gemini"}
        if _eng in _ENGINE_LABEL:
            st.caption(f"✍️ 이 대본은 **{_ENGINE_LABEL[_eng]}**가 작성했어요. (직접 고친 문장은 이 표시와 무관해요)")
        elif _eng in ("template", "mock"):
            st.warning("⚠️ 이 대본은 AI가 아니라 **업종 기본 문구(템플릿)**로 만들어졌어요. Mock 모드가 켜져 있거나 AI 호출이 실패했을 수 있어요. "
                       "사이드바 'Mock 모드'를 끄고 다시 기획해 보세요.")
        if (st.session_state.storyboard[0] or {}).get("photo_reordered"):
            st.caption("🎞️ 선택한 1컨셉을 가장 잘 보여 주는 사진을 앞쪽 컷에, 가게 외관은 마지막(CTA) 컷에 배치했어요. (대본은 사진이 아니라 1컨셉을 따라갑니다)")
        st.markdown("💡 *씬마다 나레이션 대사와 지속시간을 바로 수정하세요. 대사는 화면 자막과 음성에 똑같이 쓰입니다.*")
        if st.session_state.get("storyboard_mode", pipeline_mode) != pipeline_mode:
            st.warning(f"⚠️ 이 콘티는 '{MODE_LABELS.get(st.session_state.get('storyboard_mode'), '다른')}' 모드로 기획되었습니다. "
                       f"현재 모드로 만들려면 스토리보드를 다시 기획해 주세요.")

        # 카드 입력값 → 세션 스토리보드 병합 (화면에 안 보이는 키: hook_title, role, image_path 등은 원본 그대로 보존)
        # 위젯 값은 key로 세션에 묶이고, 기획이 새로 만들어진 경우(스토리보드 텍스트가 마지막 편집값과 다름)에는 새 값으로 다시 채운다.
        synced = st.session_state.setdefault("_sb_synced", {})
        merged_records = []
        for _i, _sc in enumerate(st.session_state.storyboard):
            _n = _scene_num(_sc, _i + 1)
            _text0 = str(_sc.get("voiceover") or _sc.get("subtitle") or _sc.get("caption") or "").strip()
            try:
                _dur0 = float(_sc.get("duration_sec") or 2.5)
            except (TypeError, ValueError):
                _dur0 = 2.5
            _kt, _kd = f"sb_text_{_n}", f"sb_dur_{_n}"
            if synced.get(_n) != (_text0, _dur0) or _kt not in st.session_state:
                st.session_state[_kt], st.session_state[_kd] = _text0, max(0.5, min(15.0, _dur0))
            with st.container(border=True, key=f"sb_card_{_n}"):
                _cth, _c1, _c2 = st.columns([1.4, 4.6, 1])
                _mp = _sc.get("image_path") or _sc.get("source_path") or _sc.get("video_path")
                with _cth:                                   # 이 문장과 짝인 사진 (사진 ↔ 대본 1:1)
                    _cuts = [m for m in [_mp] + [(x.get("image_path") or x.get("video_path")) if isinstance(x, dict) else x for x in (_sc.get("extra_media") or [])] if m and os.path.exists(m)]
                    if _cuts:
                        _ptags = (st.session_state.storyboard[0] or {}).get("photo_tags") or {}
                        for _cm in _cuts:
                            st.image(_media_thumb(_cm)[0], use_container_width=True)
                        _tgs = [t for t in (_ptags.get(os.path.basename(m)) for m in _cuts) if t]
                        if _tgs:
                            st.caption(" / ".join(_tgs))
                        if len(_cuts) > 1:
                            st.caption(f"🎞️ 컷 {len(_cuts)}장 빠르게 전환")
                    else:
                        st.caption("사진 없음")
                _c1.markdown(f"**씬 {_n:02d}**")
                _text = _c1.text_area("나레이션 · 자막 대사", key=_kt, height=90, label_visibility="collapsed")
                _dur = _c2.number_input("지속시간(초)", min_value=0.5, max_value=15.0, step=0.5, key=_kd)
            _text = (_text or "").strip()
            merged = {**_sc, "scene_index": _n, "scene_number": _n, "duration_sec": float(_dur)}
            if _text:
                # 음성 대본(voiceover)과 자막(subtitle/caption) 100% 텍스트 일치화
                merged["voiceover"] = merged["subtitle"] = merged["caption"] = _text
            synced[_n] = (_text or _text0, float(_dur))
            merged_records.append(merged)
        st.session_state.storyboard = merged_records
        current_job().save_plan(merged_records)

        # 씬별 사진·영상 캐러셀 (씬 키 기준, 전 씬 표시) 및 교체/거부/업로드
        if uses_scene_media:
            g_head_col1, g_head_col2 = st.columns([3, 1])
            with g_head_col1:
                st.markdown("#### 🏞️ 씬별 사진·영상")
            with g_head_col2:
                if is_travel_mode and st.session_state.get("image_pool") and st.button(
                    "🔄 자동 매칭 다시 실행",
                    help="직접 업로드한 씬을 제외한 모든 씬을 다시 매칭합니다. 거부한 사진은 다시 나오지 않습니다."
                ):
                    targets = [s for s in st.session_state.storyboard if s.get("asset_status") != "uploaded"]
                    with st.spinner("🎯 씬별 사진을 다시 매칭하고 비전 검증 중..."):
                        rematch_scenes(targets, api_key)
                    st.rerun()

            clean_pool = [
                c for c in st.session_state.get("image_pool", [])
                if is_travel_mode
                and c.get("category") != "negative"
                and not core.scraper.is_negative_image(c.get("title", ""), c.get("url", ""))
                and c.get("url") not in st.session_state.rejected_urls
            ]
            with st.container(horizontal=True, key="scene_carousel", gap="small"):
                for idx, scene in enumerate(st.session_state.storyboard):
                    s_num = _scene_num(scene, idx + 1)
                    with st.container(border=True, key=f"scene_card_{s_num}"):
                        role_label = SCENE_ROLE_LABELS.get(str(scene.get("role", "")), "🎬 씬")
                        st.markdown(f"**{s_num:02d} · {role_label}** · ⏱ {scene.get('duration_sec', 2.5)}초"
                                    + (f" · 🎯 {SHOT_LABELS.get(scene['shot'], scene['shot'])}" if is_commerce_mode and scene.get("shot") else ""))

                        if scene.get("extra_media"):
                            st.caption(f"🎞️ 이 문장 동안 컷 {1 + len(scene['extra_media'])}장이 이어서 나와요 (문장 중간에 사진 전환)")
                        img_p = scene.get("image_path")
                        video_media = scene.get("media") if (scene.get("media") or {}).get("type") == "video" else None
                        if video_media and os.path.exists(video_media.get("path", "")):
                            try:
                                v_info = probe_media(video_media["path"])
                                trim_now = float(video_media.get("trim_start", DEFAULT_TRIM_START))
                                # 아이폰 HEVC .mov는 브라우저에서 재생되지 않을 수 있어 대표 프레임 이미지로 미리보기
                                preview = current_job().scene_asset(s_num, ".jpg", suffix=f"_preview_{int(trim_now * 10):03d}")
                                if not os.path.exists(preview):
                                    extract_thumbnail(video_media["path"], preview, at=trim_now)
                                st.image(preview, use_container_width=True)
                                st.caption(f"🎬 직접 촬영 영상 · {v_info.duration:.1f}초 · {'세로' if v_info.is_portrait else '가로(흐린 배경 채움)'}"
                                           f"{' · HDR→SDR' if v_info.is_hdr else ''}")
                                trim = st.number_input(
                                    "사용 시작 지점(초)", min_value=0.0, max_value=max(0.0, round(v_info.duration - 0.5, 1)),
                                    value=float(min(trim_now, max(0.0, v_info.duration - 0.5))), step=0.5, key=f"trim_{s_num}"
                                )
                                if abs(trim - trim_now) > 1e-6:
                                    video_media["trim_start"] = float(trim)
                                    st.rerun()
                            except Exception as ve:
                                st.error(f"영상 정보를 읽을 수 없습니다: {ve}")
                        elif img_p and os.path.exists(img_p):
                            st.image(img_p, use_container_width=True)
                            verdict = scene.get("vision_verdict") or {}
                            if scene.get("asset_status") == "uploaded":
                                status = "📷 직접 업로드"
                            elif scene.get("asset_status") == "manual":
                                status = "✋ 수동 선택"
                            elif verdict:
                                status = f"✅ 비전 {verdict.get('category')} / 적합도 {verdict.get('fit')}"
                            else:
                                status = "⚠️ 비전 미검증"
                            st.caption(f"{status} · {os.path.getsize(img_p) / 1024:.0f}KB")
                        else:
                            st.warning("사진·영상 없음")

                        # 자막 미리보기: 사용자/LLM 텍스트는 HTML로 해석하지 않고 이스케이프된 텍스트로만 표시
                        sub_text = str(scene.get("voiceover") or scene.get("subtitle") or "").strip()
                        if sub_text:
                            st.caption(f"💬 {html.escape(sub_text)}")

                        with st.popover("📸 교체", use_container_width=True):
                            st.markdown(f"**씬 {s_num} 사진·영상 교체**")

                            # ① 이 사진 거부 → 자동 재선별 (숙소 제휴 모드, 거부 사진은 이후 재등장 금지)
                            if is_travel_mode and st.session_state.get("image_pool") and st.button(
                                "🚫 이 사진 거부하고 자동 재선별", key=f"reject_{s_num}", use_container_width=True
                            ):
                                if scene.get("matched_image_url"):
                                    st.session_state.rejected_urls.add(scene["matched_image_url"])
                                with st.spinner("다른 사진을 찾는 중..."):
                                    rematch_scenes([scene], api_key)
                                st.rerun()

                            # ② 내 사진·영상 업로드
                            up = st.file_uploader("📷 내 사진·영상으로 교체", type=UPLOAD_TYPES, key=f"scene_upload_{s_num}")
                            if up is not None:
                                up_path = save_uploaded_file(up)
                                applied_key = f"applied_upload_{s_num}"
                                if st.session_state.get(applied_key) != up_path:
                                    st.session_state[applied_key] = up_path
                                    try:
                                        assign_media_to_scene(scene, up_path, "uploaded")
                                        st.toast(f"씬 {s_num}을(를) 업로드한 {'영상' if is_video_file(up_path) else '사진'}으로 교체했습니다!", icon="📷")
                                        st.rerun()
                                    except Exception as ue:
                                        st.error(f"업로드 파일을 사용할 수 없습니다: {ue}")

                            # ③ 크롤링 후보에서 직접 선택 (숙소 제휴 모드)
                            if clean_pool:
                                sel_cand_key = f"sel_cand_{s_num}"
                                cur_cand_idx = st.session_state.get(sel_cand_key)
                                if cur_cand_idx is not None and (not isinstance(cur_cand_idx, int) or cur_cand_idx < 0 or cur_cand_idx >= len(clean_pool)):
                                    st.session_state[sel_cand_key] = 0
                                cand_opts = list(range(len(clean_pool)))
                                chosen_i = st.selectbox(
                                    f"후보에서 선택 ({len(clean_pool)}장)",
                                    options=cand_opts,
                                    format_func=lambda i: (
                                        f"#{i + 1} [{str(clean_pool[i].get('category', '')).upper()}] {clean_pool[i].get('title') or '숙소 공간'} (…{str(clean_pool[i].get('url', ''))[-22:]})"
                                        if (i is not None and isinstance(i, int) and 0 <= i < len(clean_pool)) else ""
                                    ),
                                    key=sel_cand_key
                                )
                                target_cand = clean_pool[chosen_i] if (chosen_i is not None and 0 <= chosen_i < len(clean_pool)) else clean_pool[0]
                                st.image(target_cand["url"], caption=f"선택: {target_cand.get('title') or '숙소 사진'}", use_container_width=True)
                                if st.button("✅ 이 사진으로 즉시 교체", key=f"apply_swap_{s_num}", use_container_width=True):
                                    try:
                                        resp = requests.get(
                                            target_cand["url"],
                                            headers=core.scraper.cdn_headers_for(target_cand["url"], target_cand.get("referer")),
                                            timeout=12
                                        )
                                        if resp.status_code == 200 and len(resp.content) >= 30000:
                                            save_p = current_job().scene_asset(s_num, ".jpg")
                                            with open(save_p, "wb") as f:
                                                f.write(resp.content)
                                            old_url = scene.get("matched_image_url")
                                            if old_url and old_url != target_cand["url"]:
                                                st.session_state.rejected_urls.add(old_url)
                                            assign_media_to_scene(scene, save_p, "manual", matched_url=target_cand["url"])
                                            st.toast(f"씬 {s_num} 사진이 교체되었습니다!", icon="🎉")
                                            st.rerun()
                                        else:
                                            st.error(f"다운로드 실패: HTTP {resp.status_code}, {len(resp.content):,} bytes")
                                    except Exception as de:
                                        st.error(f"교체 오류: {de}")

            st.caption("ℹ️ 씬 카드는 좌우로 넘겨 볼 수 있습니다. 마음에 들지 않는 장면은 '📸 교체'에서 바꿔 주세요.")

st.markdown("---")

# ==============================================================================
# 3단계(= ② 영상 만들기): 최종 광고 영상 렌더링 및 업로드
# ==============================================================================
st.markdown("### 🎬 영상 제작 & 업로드" if is_video_overlay_mode else "### [4단계] 🎬 쇼츠 영상 만들기")
if not is_video_overlay_mode:
    with st.container(border=True):
        _w_audio_block()                       # 오디오·성우·BGM + 자막 폰트·위치를 접지 않고 바로 노출

render_btn = st.button("✨ 인스타 릴스 / 유튜브 쇼츠용 영상 만들기", type="primary", use_container_width=True, key="render_cta",
                       help="소리·자막은 바로 위에서 정해요. 대본이 아직 없으면 자동으로 기획부터 해요.")
bgm_on = st.session_state.get("bgm_on", False) and bool(bgm_tracks)
if bgm_on and bgm_choice is None:
    bgm_choice = st.session_state.get("bgm_choice") or bgm_mod.RANDOM
if render_btn and not st.session_state.storyboard and (is_commerce_mode or (st.session_state.get("uploaded_paths") and not is_video_overlay_mode)):
    st.session_state.auto_plan_then_render = True
    st.rerun()
render_btn = render_btn or bool(st.session_state.pop("auto_render_now", False))

if render_btn:
    if not st.session_state.storyboard:
        st.warning("먼저 '스토리보드 자동 기획'을 실행해 주세요.")
    else:
        scenes = st.session_state.storyboard
        # 여행 모드: 실물 사진이 없는 씬이 있으면 렌더링 중단 (순번 대체·AI 가짜 숙소 사진 금지)
        missing_scenes = [
            _scene_num(s, i + 1) for i, s in enumerate(scenes)
            if not scene_has_media(s)
        ] if (uses_scene_media and not mock_mode) else []
        if st.session_state.get("storyboard_mode", pipeline_mode) != pipeline_mode:
            st.error("현재 콘티는 다른 제작 모드로 기획되었습니다. 스토리보드를 다시 기획한 뒤 렌더링해 주세요.")
            st.stop()
        if is_commerce_mode and st.session_state.get("storyboard_layout", "single") != st.session_state.commerce_layout:
            st.error(f"현재 콘티는 '{BUNDLE_LAYOUTS.get(st.session_state.get('storyboard_layout'), '다른 구성')}'으로 기획되었습니다. "
                     "스토리보드를 다시 기획해 주세요.")
            st.stop()
        if is_owner_mode and not product_name.strip():
            st.error("상호명을 입력해 주세요. 워터마크와 네이버 검색 엔딩 카드에 사용됩니다.")
            st.stop()
        if missing_scenes:
            st.error(f"씬 {', '.join(map(str, missing_scenes))}에 사진이 없습니다. 위 갤러리에서 사진을 교체하거나 업로드한 뒤 다시 시도해 주세요.")
            st.stop()
        if is_video_overlay_mode:
            single_v = st.session_state.uploaded_paths[0] if (st.session_state.uploaded_paths and is_video_file(st.session_state.uploaded_paths[0])) else None
            if not single_v:
                st.error("원본 비디오 파일이 없습니다. 동영상을 먼저 업로드해 주세요.")
                st.stop()
            progress_bar = st.progress(0, text="파이프라인 초기화 중...")
            try:
                # 1. 각 씬의 나레이션 TTS 생성
                progress_bar.progress(20, text="🎙️ 나레이션 TTS 음성 생성 중...")
                asset_gen = AssetGenerator(cache_dir=current_job().assets_dir)
                for sc in scenes:
                    sc["voice_actor"] = st.session_state.get("selected_actor", selected_actor)
                    sc["audio_path"] = asset_gen.generate_voice(sc, force=force_render)

                # 2. 직가공 렌더링 (자막/후킹/뱃지 + 오디오 믹싱)
                progress_bar.progress(50, text="🎞️ 촬영 영상 직가공 렌더링 중 (움직임 100% 보존)...")
                out_vid = os.path.join(current_job().final_dir, "final.mp4")
                aud_mode = "keep_ducking" if "20%" in st.session_state.get("video_audio_opt", "") else "mute"
                fit_m = "crop" if "크롭" in st.session_state.get("video_fit_opt", "") else "blur"
                selected_concept = st.session_state.get("selected_core_concept", "").strip()
                badge_text = (selected_concept or product_name or business_type or "").strip()

                # 썸네일(카피 오버레이) + 업로드용 독립 커버(thumbnail.jpg)를 먼저 만들고, 그 이미지를 영상 0초 커버로도 쓴다
                thumb_out = os.path.join(current_job().final_dir, "final_thumb.png")
                copy = make_shorts_copy(
                    "owner", hook=scenes[0].get("hook_title", ""), usp=usp_desc,
                    product_name=product_name or "촬영 영상 쇼츠", api_key=api_key, mock=mock_mode,
                    signature=badge_text, core_concept=selected_concept, business_type=business_type or ""
                )
                thumb_ok = False
                try:
                    import numpy as _np
                    from PIL import Image as _PILImage
                    _raw = os.path.join(current_job().final_dir, "_thumb_frame.png")
                    extract_thumbnail(single_v, _raw, at=0.5)
                    _frame = _np.array(_PILImage.open(_raw).convert("RGB"))
                    AdCompositor(output_dir=current_job().final_dir).render_thumbnail(
                        _frame, copy["thumb_main"], thumb_out, sub_text=copy.get("thumb_sub", ""), accent=copy.get("accent", ""))
                    os.remove(_raw)
                    from core.youtube_upload import export_cover_jpg
                    export_cover_jpg(thumb_out)
                    thumb_ok = True
                except Exception as te:
                    logger.warning(f"썸네일 생성 실패: {te}")

                render_video_overlay(
                    video_path=single_v,
                    storyboard=scenes,
                    output_path=out_vid,
                    audio_mode=aud_mode,
                    fit_mode=fit_m,
                    badge_text=badge_text,
                    duck_volume=0.20,
                )
                if thumb_ok:            # 영상이 깨끗하게 완성된 뒤, 독립 커버(thumbnail.jpg)만 맨 앞에 붙임
                    from core.render.cover_clip import apply_cover_if_enabled
                    apply_cover_if_enabled(out_vid, os.path.join(current_job().final_dir, "thumbnail.jpg"))

                progress_bar.progress(85, text="✨ SEO 메타데이터 생성 중...")
                st.session_state.shorts_copy = copy
                st.session_state.thumb_path = thumb_out
                st.session_state.final_video_path = out_vid
                st.session_state.seo_metadata = generate_seo_metadata(
                    scenes=scenes, mode="owner", product_name=product_name or "촬영 영상 쇼츠",
                    usp=usp_desc, search_keyword=badge_text, api_key=api_key, mock=mock_mode,
                    business_type=business_type or "", core_concept=selected_concept,
                    tag_set=copy.get("tag_set")
                )
                progress_bar.progress(100, text="완료!")
                st.success("🎉 촬영 영상 원본 직가공 쇼츠가 성공적으로 제작되었습니다!")

            except Exception as re:
                st.error(f"렌더링 도중 에러 발생: {re}")
                logger.exception("렌더링 실패")
        else:
            progress_bar = st.progress(0, text="파이프라인 초기화 중...")

            try:
                # 공용 제작 엔진: 음성(동시) → 클립 → 썸네일·업로드 카피 → 조립(자막·제휴 표시·고정 바·라벨·BGM)
                res = pipeline.render(current_spec(bgm_path=bgm_mod.pick_track(bgm_choice) if bgm_on else None), current_job(),
                                      scenes, progress=lambda pct, text: progress_bar.progress(pct, text=text), force=force_render)
                st.session_state.generated_images = res.generated_images
                st.session_state.shorts_copy = res.shorts_copy
                _res_meta = getattr(res, "metadata", None)
                if _res_meta:
                    st.session_state.seo_metadata = _res_meta
                if bgm_on and res.bgm.get("error"):
                    st.warning(f"BGM을 섞지 못해 BGM 없이 저장했어요: {res.bgm['error']}")
                st.session_state.bgm_status = res.bgm
                st.session_state.thumb_path = res.thumb_path
                if is_book_mode:
                    st.session_state.book_comment = res.comment     # 렌더 직후 대기열에도 저장됨 (업로드 연결 전)
                st.session_state.final_video_path = res.final_path
                st.success("🎉 최종 숏폼 영상이 성공적으로 제작되었습니다!")

            except Exception as re:
                st.error(f"렌더링 도중 에러 발생: {re}")
                logger.exception("렌더링 실패")

# 결과물 표출
if st.session_state.final_video_path and os.path.exists(st.session_state.final_video_path):
    st.markdown("### [5단계] 📤 만들어진 영상 확인 및 채널 업로드")
    col_vid, col_meta = st.columns([1, 1])
    with col_vid:
        st.markdown("#### 📱 9:16 쇼츠 완성본 (스마트폰 뷰어)")
        with st.container(key="phone_frame"):
            st.video(st.session_state.final_video_path)

        with open(st.session_state.final_video_path, "rb") as f:
            st.download_button(
                label="⬇️ 인스타 릴스 / 유튜브 쇼츠용 고화질 MP4 저장",
                data=f,
                file_name=f"{product_name}_shortform_video.mp4",
                mime="video/mp4",
                use_container_width=True,
                type="primary",
                key="download_cta"
            )
        _bs = st.session_state.get("bgm_status") or {}
        if _bs.get("info"):
            _bi = _bs["info"]
            st.success(f"🎵 BGM: {bgm_mod.display_name(_bi['track'])} (적용) · 곡 {_bi['start']:.1f}초부터, 나레이션 중 {_bi['duck']:.0%}")
            st.caption("자체 음원이 들어간 영상이에요. 플랫폼 음원을 따로 태그하면 소리가 겹치니 그대로 올려 주세요.")
        elif _bs.get("on") and _bs.get("error"):
            st.warning(f"🔇 BGM 꺼짐 (섞기 실패: {_bs['error']})")
        else:
            st.info("🔇 BGM 꺼짐 · 넣으려면 4단계의 '🎵 자체 BGM 넣기'를 켜고 다시 만들어 주세요. "
                    "(BGM 없이 올릴 땐 플랫폼 추천 트렌딩 음원을 태그해 보세요)")
        if is_book_mode:
            st.markdown("**💬 첫 댓글 (책방 위치 + 알라딘 구매 링크 + 제휴 고지)** · 유튜브 업로드 연결 전이라 대기열에 저장돼요")
            if "book_comment" not in st.session_state:
                st.session_state.book_comment = book_comment_text(st.session_state.get("shorts_copy"))
            st.text_area("첫 댓글 초안", key="book_comment", height=230)
            b_save, b_copy = st.columns([1, 1])
            with b_save:
                if st.button("💾 첫 댓글 대기열에 저장", use_container_width=True, key="book_comment_save"):
                    from core.youtube_upload import queue_comment_draft
                    queue_comment_draft("book", st.session_state.final_video_path, st.session_state.book_comment,
                                        job_id=current_job().job_id)
                    st.success("저장했어요 (outputs/youtube/comment_drafts.json). 업로드를 연결하면 이 댓글을 그대로 달아요.")
            with b_copy:
                copy_button(st.session_state.book_comment, "📋 첫 댓글 복사", key="copy_book_comment")
        elif is_bundle:
            from core.shorts_meta import build_first_comment
            st.markdown("**📝 고정 댓글용 구매 좌표 + 제휴 고지** (상품별 링크 · 쿠팡 파트너스/스마트스토어 고지 자동)")
            post_text = build_first_comment("commerce", "", None, items=bundle_items_now())
            st.code(post_text or "(묶음 상품 칸에 구매 링크를 넣어 주세요)", language=None)
            if post_text:
                copy_button(post_text, "📋 구매 좌표+고지 원클릭 복사", key="copy_bundle")
        elif is_commerce_mode and st.session_state.get("shop_item"):
            _spx = current_spec()
            _tx = pipeline.upload_texts(_spx, st.session_state.get("shorts_copy"), hook=((st.session_state.get("storyboard") or [{}])[0].get("hook_title") or ""))
            st.markdown("**📝 첫 댓글 패키지 — 네이버 쇼핑커넥트 링크 + 공정위 대가성 문구 + 사진 출처 문구**")
            st.code(_tx["comment"], language=None)
            copy_button(_tx["comment"], "📋 첫 댓글 패키지 원클릭 복사", key="copy_shop_comment")
        elif is_commerce_mode:
            st.markdown("**📝 영상 설명·고정 댓글에 함께 올릴 문구** (쿠팡 파트너스 안내 + 공정위 추천·보증 심사지침)")
            link = st.session_state.get("affiliate_link") or "(쿠팡 파트너스 링크를 붙여 넣으세요)"
            post_text = f"{COUPANG_DISCLOSURE_FULL}\n제품 링크: {link}"
            st.code(post_text, language=None)
            copy_button(post_text, "📋 문구+링크 원클릭 복사", key="copy_commerce")
        elif is_travel_mode and st.session_state.get("stay_ctx"):
            import core.stay_match as _sm
            _ctx = st.session_state.stay_ctx
            _lk = _stay_link_now()
            _f = _stay_facts_now()
            _alias = (st.session_state.get("stay_alias_input") or _ctx.get("alias") or "").strip()
            st.markdown("**📝 고정 댓글 / 프로필 링크용 [여기어때 예약 제휴 링크] 패키지** (숙소 특징 + 제휴 링크 + 해시태그, 공정위 고지 포함)")
            if _lk["note"]:
                st.warning(_lk["note"])
            _post = _sm.comment_package(_alias, _f, _lk["link"], _sm.stay_hashtags(_ctx["stay"].get("region", ""), _alias, _f))
            st.code(_post, language=None)
            copy_button(_post, "📋 고정 댓글 원클릭 복사", key="copy_stay_comment")
        elif is_travel_mode and st.session_state.get("affiliate_program"):
            ota = st.session_state.affiliate_program
            st.markdown(f"**🔗 {OTA_LABEL.get(ota, '')} 어필리에이트 추천 링크** (영상 설명·고정 댓글용, 공정위 추천·보증 심사지침 고지 포함)")
            link = st.session_state.get("affiliate_link")
            if link:
                st.code(link, language=None)
                copy_button(link, "📋 링크만 복사", key="copy_aff_link")
                post_text = f"{ota_disclosure_full(ota)}\n숙소 예약 링크: {link}"
                st.code(post_text, language=None)
                copy_button(post_text, "📋 고지 문구+링크 원클릭 복사", key="copy_aff_post")
            else:
                st.warning("제휴 ID(.env의 AGODA_CID 또는 TRIP_AID·TRIP_SID)가 없어 수익 링크를 만들지 못했어요.")
    with col_meta:
        # =============================================================
        # 🚀 SEO 메타데이터 자동 생성 & 유튜브 원클릭 자동 업로드 바인딩
        # =============================================================
        _seo = st.session_state.get("seo_metadata")
        if not _seo:
            _seo = current_job().load_metadata()
            if not _seo and st.session_state.get("storyboard"):
                _sp = current_spec()
                _seo = generate_seo_metadata(
                    scenes=st.session_state.storyboard,
                    mode=_sp.mode,
                    product_name=_sp.name,
                    usp=_sp.usp,
                    search_keyword=_sp.search_keyword,
                    address=_sp.address,
                    api_key=_sp.api_key,
                    mock=_sp.mock,
                    business_type=_sp.business_type or "",
                    core_concept=getattr(_sp, "core_concept", "") or ""
                )
                current_job().save_metadata(_seo)
            st.session_state.seo_metadata = _seo

        _seo = _seo or {}
        _seo_title = _seo.get("title", "")
        _seo_desc = _seo.get("description", "")
        _seo_tags = _seo.get("tags", "")

        _ota = st.session_state.get("affiliate_program")
        from core.naver_shop import CONNECT_DISCLOSURE_FULL as _CDF, CONNECT_DISCLOSURE_SHORT as _CDS
        _is_shop = is_commerce_mode and not is_bundle and bool(st.session_state.get("shop_item"))
        _disc = ("" if is_bundle            # 묶음: 상품 링크 종류별 고지를 첫 댓글에서 자동으로 붙임
                 else _CDF if _is_shop
                 else COUPANG_DISCLOSURE_FULL if is_commerce_mode
                 else ota_disclosure_full(_ota) if is_travel_mode and _ota else "")
        _sb = st.session_state.get("storyboard") or []
        if is_book_mode:
            st.info("📚 시골책방 채널 업로드는 아직 연결 전이에요. 영상은 로컬에 저장됐고, 첫 댓글은 왼쪽에서 대기열로 저장돼요.")
        else:
            if set(st.session_state.get("web_photo_paths", [])) & set(st.session_state.get("uploaded_paths", [])):
                st.warning("⚠️ 이 영상에는 웹에서 찾은 사진이 들어 있어요. 사진은 크롭·색감 보정·비네팅으로 편집했고 사람·워터마크·글씨 사진은 걸러냈지만, "
                           "이것이 권리 확인을 대신하지는 않아요. 공개 업로드 전에 점주(업체) 동의를 받았는지 확인하거나 직접 촬영한 사진으로 바꿔 다시 만들어 주세요.")
            # 유튜브 패널에 SEO 제목, 설명, 태그를 직접 바인딩하여 원클릭 업로드 지원
            _call_supported(render_youtube_panel, st.session_state.final_video_path, st.session_state.get("thumb_path"), pipeline_mode,
                                 hook=(_sb[0].get("hook_title") or "") if _sb else "",
                                 link=st.session_state.get("affiliate_link", "") if not (is_owner_mode or is_bundle) else "",
                                 disclosure=_disc, business_name=product_name,
                                 search_kw=owner_search_kw if is_owner_mode else "",
                                 copy=st.session_state.get("shorts_copy"),
                                 items=bundle_items_now() if is_bundle else None,
                                 disclosure_short=(bundle_disclosure_short(bundle_items_now()) if is_bundle
                                                   else _CDS if _is_shop
                                                   else COUPANG_DISCLOSURE if is_commerce_mode
                                                   else "※ " + OTA_DISCLOSURE if is_travel_mode and _ota else ""),
                                 seo_title=_seo_title, seo_desc=_seo_desc, seo_tags=_seo_tags,
                                 business_type=business_type if is_owner_mode else "",
                                 usp=usp_desc if is_owner_mode else "",
                                 core_concept=st.session_state.get("selected_core_concept", ""),
                                 address=(st.session_state.get("owner_address", "") or "") if is_owner_mode else "")

        # 수동 업로드 사용자를 위한 보조 원클릭 복사 아코디언
        with st.expander("📋 SEO 메타데이터 개별 복사 (직접 수동 업로드 시)", expanded=False):
            st.caption("유튜브 스튜디오나 인스타그램 앱에 직접 붙여넣을 때 사용하세요.")
            st.markdown("**1. 쇼츠 제목**")
            ui_title = st.text_input("쇼츠 제목 입력", value=_seo_title, key="ui_seo_title_exp", label_visibility="collapsed")
            copy_button(ui_title, "📋 제목 복사", key="btn_copy_seo_title")

            st.markdown("**2. 영상 설명**")
            ui_desc = st.text_area("영상 설명 입력", value=_seo_desc, height=120, key="ui_seo_desc_exp", label_visibility="collapsed")
            copy_button(ui_desc, "📋 설명 복사", key="btn_copy_seo_desc")

            st.markdown("**3. 검색 태그**")
            ui_tags = st.text_input("검색 태그 입력", value=_seo_tags, key="ui_seo_tags_exp", label_visibility="collapsed")
            copy_button(ui_tags, "📋 태그 전체 복사", key="btn_copy_seo_tags")
        st.markdown("#### 🖼️ 씬별 키프레임 갤러리")
        if st.session_state.generated_images:
            cols = st.columns(len(st.session_state.generated_images))
            for i, p in enumerate(st.session_state.generated_images):
                if os.path.exists(p):
                    with cols[i]:
                        st.image(p, caption=f"Scene {i+1}", use_container_width=True)

        st.markdown("#### 📊 제작 사양")
        concept_disp = st.session_state.get('selected_core_concept') or '미지정'
        st.markdown(f"""
        - **제작 모드**: {production_mode}
        - **해상도**: 1080 x 1920 (9:16 Vertical Video)
        - **비디오 렌더링**: 로컬 2D Ken Burns 모션 + 직접 촬영 영상 (24 FPS, H.264 / AAC, 비용 0원)
        - **작업 ID**: `{current_job().job_id}`
        - **핵심 1컨셉**: {concept_disp}
        - **화자 시점**: {script_style_options[selected_script_style]}
        - **성우 말투**: {selected_tone_label}
        - **적용 성우**: {selected_actor_label} ({audio_mode_options[selected_audio_choice]})
        - **자막 폰트**: {FONT_PRESETS[caption_font_key]["label"]}
        """)

