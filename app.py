"""
app.py - AI Short-form Ad Video Factory 반응형 Streamlit 웹 대시보드
쇼핑몰 크롤링, 누끼 추출, 톤앤매너/길이/성우 선택, 실시간 콘티 편집 및 9:16 광고 영상 원클릭 렌더링
"""
import os
import re
import sys
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
import core.scraper
import core.compositor
import core.analyzer
import core.asset_generator
import core.tts
import core.audio
importlib.reload(core.scraper)
importlib.reload(core.compositor)
importlib.reload(core.analyzer)
importlib.reload(core.asset_generator)
importlib.reload(core.tts)
importlib.reload(core.audio)

from core.analyzer import plan_storyboard, TONE_PRESETS, DURATION_PRESETS, TRAVEL_TEMPLATES, DEFAULT_TRAVEL_TEMPLATE
from core.asset_generator import AssetGenerator, VOICE_ACTOR_PROFILES
from core.compositor import AdCompositor
from core.scraper import scrape_product_info
from core.job import JobContext, cleanup_old_jobs
from core.media import is_video_file, probe_media, extract_thumbnail
from core.coupang import SEARCH_LIMIT_PER_HOUR, CoupangAPIError, CoupangPartners
from core.media.library import MAX_MEDIA, add_media, move_media, remove_media
from core.tts import owner_readings
from core.naver_local import NaverAPIError, NaverLocalSearch, is_naver_link, place_id_from_link, region_of
from core.affiliate_ota import OTA_DISCLOSURE, OTA_LABEL, ota_disclosure_full
from ui.copy_button import copy_button
from ui.youtube_panel import render_youtube_panel
from core.render import bgm as bgm_mod
from core.shorts_meta import make_shorts_copy
from core.radar.trends import digest_for
from core.planners import (BUSINESS_TYPES, COUPANG_DISCLOSURE, COUPANG_DISCLOSURE_FULL, DEFAULT_BUSINESS_TYPE,
                           analyze_product_photo, plan_commerce_storyboard, plan_owner_storyboard, suggest_store_points)
from core.render.commerce_layout import SHOT_LABELS, assign_shots
from core.planners.book import TEMPLATES as BOOK_TEMPLATES, plan_book_storyboard
from core.planners.commerce_bundle import (BUNDLE_SIZE, DEFAULT_BOTTOM_BAR, bundle_title_ok, plan_bundle_storyboard,
                                           short_name as bundle_short_name)
from core.planners.travel import plan_travel_storyboard
from core.radar.hints import load_hook_hints, vertical_for
from core.radar.hooks import HOOK_TYPE_KO
from ui.radar_page import render_radar_page, RADAR_PERSIST_KEYS, init_radar_state, hints_enabled, allow_dryrun_hints
from core.planners.owner import ROLE_LABELS_KO as OWNER_ROLE_LABELS
from core.media.normalize import DEFAULT_TRIM_START
from core.render.brand import BrandOverlay, EndCard
from core import pipeline

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
.st-key-render_cta button,.st-key-download_cta button{min-height:60px;font-size:1.1rem;font-weight:800;border-radius:9999px;
  background:linear-gradient(135deg,#8B5CF6,#06B6D4);border:none;color:#fff}
.st-key-phone_frame{max-width:340px;margin:0 auto;border:3px solid rgba(139,92,246,.45);border-radius:32px;padding:14px 10px;background:#000}
</style>""", unsafe_allow_html=True)

# 외부 접속(Cloudflare 터널·다른 기기)은 PIN 통과 전까지 아래 앱(작업 폴더 생성·API 호출 포함)을 실행하지 않음
from ui.auth import require_pin
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
    """업로드 사진/영상을 작업 공간 uploads/에 내용 해시 기반 고유 파일명으로 저장"""
    data = up_file.getbuffer()
    ext = os.path.splitext(up_file.name)[-1].lower() or ".jpg"
    path = os.path.join(current_job().uploads_dir, f"upload_{hashlib.md5(data).hexdigest()[:12]}{ext}")
    if not os.path.exists(path):
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
    """목록용 미리보기: 사진은 그대로, 영상은 대표 프레임(+길이)"""
    if not is_video_file(path):
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
        st.markdown(f"**📸 영상 소스 {len(paths)}개** · 위에서부터 씬 순서 (최대 {MAX_MEDIA}개)")
        if st.session_state.pop("media_stale", False):
            st.info("사진·영상 구성이 바뀌었어요. 씬 수까지 맞추려면 [🚀 스토리보드 자동 기획]을 다시 눌러 주세요.")
        for i, p in enumerate(paths):
            tag = hashlib.md5(p.encode()).hexdigest()[:8]
            c_no, c_img, c_info, c_up, c_down, c_del = st.columns([0.35, 1.1, 2.6, 0.55, 0.55, 0.55], vertical_alignment="center")
            c_no.markdown(f"**{i + 1}**")
            thumb, label = _media_thumb(p)
            if thumb:
                c_img.image(thumb, width=88)
            c_info.caption(label or os.path.basename(p)[:28])
            if c_up.button("⬆", key=f"media_up_{tag}", disabled=i == 0, help="앞으로"):
                _media_changed(move_media(paths, i, -1))
                st.rerun()
            if c_down.button("⬇", key=f"media_down_{tag}", disabled=i == len(paths) - 1, help="뒤로"):
                _media_changed(move_media(paths, i, +1))
                st.rerun()
            if c_del.button("🗑", key=f"media_del_{tag}", help="목록에서 빼기 (파일은 작업 폴더에 남음)"):
                _media_changed(remove_media(paths, i))
                st.rerun()
        nonce = st.session_state.get("media_uploader_nonce", 0)
        new_files = st.file_uploader(
            "➕ 내 폰 사진·영상 추가 (JPG/PNG/MP4/MOV)", accept_multiple_files=True, type=UPLOAD_TYPES,
            key=f"media_uploader_{nonce}", disabled=len(paths) >= MAX_MEDIA,
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
MODES = {"🏠 사장님 매장 홍보": "owner", "🧳 숙소 제휴 쇼츠": "travel", "🛒 쇼핑 제휴 쇼츠": "commerce",
         "📚 시골책방 도서 큐레이션": "book"}
MODE_LABELS = {v: k for k, v in MODES.items()}
SCENE_ROLE_LABELS = {**OWNER_ROLE_LABELS, "hook": "🔥 후킹", "feature": "✨ 특장점", "facility": "☕ 부대시설",
                     "room": "🛏 객실", "cta": "📍 CTA", "hero": "🛒 첫인상", "usage": "🙌 사용 장면"}


def _on_mode_change():
    # segmented_control은 선택된 항목을 다시 누르면 선택 해제(None)되므로 직전 모드로 복원
    if st.session_state.get("mode_switch") is None:
        st.session_state.mode_switch = MODE_LABELS[st.session_state.production_mode]
    else:
        st.session_state.production_mode = MODES[st.session_state.mode_switch]


if st.session_state.production_mode not in MODE_LABELS:   # 이전 버전 세션 값("여행 상품 모드" 등) 정리
    st.session_state.production_mode = "travel" if "여행" in str(st.session_state.production_mode) else "owner"
if "mode_switch" not in st.session_state:
    st.session_state.mode_switch = MODE_LABELS[st.session_state.production_mode]
pipeline_mode = st.session_state.production_mode

# Streamlit은 이번 실행에 그려지지 않은 위젯의 상태를 지우므로, 다른 모드를 잠깐 다녀오면 사장님 입력값이 사라짐.
# 매 실행 시작에 키를 재할당해 정리 대상에서 제외 (Streamlit 공식 권장 방식)
for _k, _default in (("owner_type", DEFAULT_BUSINESS_TYPE), ("brand_watermark", True), ("brand_end_card", True),
                     ("page_switch", "make")):
    st.session_state.setdefault(_k, _default)   # 위젯 인자 대신 세션에서 기본값 설정 (세션 재할당과 함께 써도 경고 없음)
init_radar_state()
for _k in ("owner_name", "owner_type", "owner_search_kw", "owner_usp", "owner_address", "owner_reading", "place_query", "bgm_on", "bgm_choice",
           "product_name_input", "product_usp_input", "commerce_layout", "bundle_theme", "bundle_bottom",
           "book_store", "book_search_kw", "book_title", "book_author", "book_info", "book_quote", "book_link",
           "book_photo_count", "book_template",
           *[f"bundle_{f}_{k}" for f in ("name", "usp", "link") for k in range(3)],
           "brand_watermark", "brand_end_card", "brand_position", "brand_sub", "page_switch") + RADAR_PERSIST_KEYS:
    if _k in st.session_state:
        st.session_state[_k] = st.session_state[_k]

is_owner_mode = pipeline_mode == "owner"
is_travel_mode = pipeline_mode == "travel"
is_commerce_mode = pipeline_mode == "commerce"
is_book_mode = pipeline_mode == "book"
uses_scene_media = pipeline_mode in ("owner", "travel", "commerce", "book")   # 씬별 실사 사진/영상 배정 UI를 쓰는 모드 (전 모드 실물만)

# ==============================================================================
# 상단 헤더 + 화면 전환 (영상 만들기 / 실시간 떡상 레이더)
# ==============================================================================
PAGES = {"make": "🎬 영상 만들기", "radar": "🔥 실시간 떡상 레이더"}
st.title("🎬 AI 광고 공장")
st.caption("내 가게 홍보 영상부터 숙소·쇼핑 제휴 쇼츠까지, 9:16 세로 영상을 만듭니다.")
page = st.segmented_control("화면", list(PAGES), key="page_switch", format_func=PAGES.get,
                            label_visibility="collapsed", width="stretch") or "make"
if page == "radar":
    render_radar_page()
    st.stop()

# ==============================================================================
# 사이드바: 기획 및 제작 설정 패널
# ==============================================================================
def _sync_bgm(src: str, dst: str):
    """사이드바 BGM 스위치와 렌더 버튼 옆 스위치를 같은 상태로 (콜백은 다음 실행 전에 돌아 위젯 값 변경 가능)"""
    st.session_state[dst] = st.session_state.get(src, False)
    if src == "bgm_on_main":
        st.session_state.bgm_on = st.session_state[src]


with st.sidebar:
    st.markdown("### ⚙️ 광고 제작 설정")
    
    # 보안: 서버 키를 입력창 기본값으로 넣으면 브라우저로 전송되므로 절대 프리필하지 않음
    env_api_key = os.getenv("GEMINI_API_KEY", "")
    user_api_key = st.text_input(
        "Gemini API Key (선택)",
        value="",
        type="password",
        placeholder="서버 키 사용 중 (입력 시 이 키로 대체)" if env_api_key else "API 키를 입력하세요",
        help="비워두면 서버 .env에 등록된 키를 사용합니다. 서버 키는 화면으로 전송되지 않습니다."
    )
    api_key = user_api_key.strip() or env_api_key

    mock_mode = st.checkbox("🧪 Mock 모드 (API 과금 없이 초고속 테스트)", value=False)
    force_render = st.checkbox("🔄 강제 재생성 (기존 캐시 무시)", value=True)

    st.markdown("---")
    st.markdown("#### ✍️ 대본 기획 스타일")
    script_style_options = {
        "info": "📢 정보/후킹형 (홍보·추천 스타일)",
        "experience": "✈️ 1인칭 경험형 (생생한 투숙 후기 스타일)"
    }
    script_style_keys = list(script_style_options.keys())
    default_style_idx = 0
    if st.session_state.get("script_style") in script_style_keys:
        default_style_idx = script_style_keys.index(st.session_state.script_style)

    selected_script_style = st.radio(
        "대본 스타일",
        script_style_keys,
        index=default_style_idx,
        format_func=lambda x: script_style_options[x],
        help="📢 정보/후킹형: 시청자 결핍과 특장점을 직격으로 추천하는 런비 스타일.\n✈️ 1인칭 경험형: 인스타/쇼츠 브이로그 감성의 생생한 실제 투숙 후기 스타일."
    )
    st.session_state.script_style = selected_script_style

    travel_template = DEFAULT_TRAVEL_TEMPLATE
    if is_travel_mode:
        travel_template = st.selectbox(
            "숙소 제휴 씬 템플릿", list(TRAVEL_TEMPLATES), key="travel_template",
            format_func=lambda k: TRAVEL_TEMPLATES[k]["name"],
            help="7씬 황금 비율: 01~03 수영장·자쿠지 후킹 → 04~05 객실·창밖 뷰 → 06~07 바베큐·카페·불멍 + 댓글 CTA"
        )

    st.markdown("#### 🎭 대본 톤앤매너")
    tone_keys = list(TONE_PRESETS.keys())
    tone_labels = [TONE_PRESETS[k]["name"] for k in tone_keys]
    selected_tone_label = st.selectbox("톤앤매너 선택", tone_labels, index=0)
    selected_tone = tone_keys[tone_labels.index(selected_tone_label)]

    st.markdown("#### ⏱️ 영상 길이 프리셋")
    duration_keys = list(DURATION_PRESETS.keys())
    duration_labels = [DURATION_PRESETS[k]["name"] for k in duration_keys]
    selected_duration_label = st.selectbox("영상 길이", duration_labels, index=1)
    selected_duration = duration_keys[duration_labels.index(selected_duration_label)]

    st.markdown("#### 🔊 오디오 및 성우 제어")
    audio_mode_options = {
        "voice_and_sub": "🎙️ AI 나레이션 + 자막 (기본)",
        "silent": "🔇 무음 + 자막 전용 (유튜브 음원용)",
    }
    selected_audio_mode = st.selectbox(
        "오디오 모드",
        list(audio_mode_options.keys()),
        format_func=lambda x: audio_mode_options[x],
        index=0
    )

    actor_keys = list(VOICE_ACTOR_PROFILES.keys())
    actor_labels = [VOICE_ACTOR_PROFILES[k]["name"] for k in actor_keys]
    default_actor_idx = 0
    if "selected_actor" in st.session_state and st.session_state.selected_actor in actor_keys:
        default_actor_idx = actor_keys.index(st.session_state.selected_actor)
    selected_actor_label = st.selectbox(
        "AI 성우 보이스",
        actor_labels,
        index=default_actor_idx,
        disabled=(selected_audio_mode == "silent"),
        key="voice_actor_select_box"
    )
    selected_actor = actor_keys[actor_labels.index(selected_actor_label)]
    st.session_state.selected_actor = selected_actor

    # 자체 음원 BGM (assets/bgm): 나레이션 구간 20% 덕킹 + 시작 페이드인·끝 페이드아웃
    bgm_tracks = bgm_mod.ordered_tracks()
    st.session_state.setdefault("bgm_on", False)
    # 시골책방: 잔잔한 피아노 BGM 기본 (.env BGM_BOOK_TRACK) - 이 세션에서 처음 시골책방에 들어올 때 한 번만 켜 둠
    _book_track = bgm_mod.book_default_track() if is_book_mode else None
    if _book_track and not st.session_state.get("bgm_book_init"):
        st.session_state.bgm_on, st.session_state.bgm_choice, st.session_state.bgm_book_init = True, _book_track, True
    bgm_on = st.toggle("🎵 자체 BGM 넣기", key="bgm_on", disabled=not bgm_tracks, on_change=_sync_bgm, args=("bgm_on", "bgm_on_main"),
                       help="직접 만들었거나 권리를 가진 음원만 assets/bgm 폴더에 넣어 주세요. 나레이션이 나올 땐 20%로 낮춰 섞습니다.")
    bgm_choice = None
    if bgm_tracks:
        if bgm_on:
            bgm_options = [bgm_mod.RANDOM] + [os.path.basename(t) for t in bgm_tracks]
            if st.session_state.get("bgm_choice") not in bgm_options:
                st.session_state.bgm_choice = bgm_mod.RANDOM
            _sig = set(bgm_mod.signature_tracks())
            bgm_choice = st.selectbox("BGM 음원", bgm_options, key="bgm_choice",
                                      format_func=lambda v: "🎲 랜덤 (렌더마다 무작위)" if v == bgm_mod.RANDOM
                                      else f"⭐ {v}" if v in _sig else v,
                                      help="곡 처음의 인트로는 건너뛰고 비트가 들어오는 지점(드롭)부터 자동으로 붙습니다.")
    else:
        st.caption("🎵 자체 BGM: `assets/bgm/` 폴더에 mp3·wav·m4a 음원을 넣으면 켤 수 있어요.")

    brand_opts = None
    if is_owner_mode:
        st.markdown("---")
        st.markdown("#### 🏷️ 브랜드 오버레이")
        watermark_on = st.checkbox("상호명 워터마크", key="brand_watermark")
        end_card_on = st.checkbox("네이버 검색 유도 엔딩 카드", key="brand_end_card")
        brand_logo_file = st.file_uploader("로고 (PNG 권장, 선택)", type=["png", "jpg", "jpeg"], key="brand_logo")
        brand_position = st.radio("워터마크 위치", ["top_right", "top_left"], horizontal=True, key="brand_position",
                                  format_func=lambda x: "우측 상단" if x == "top_right" else "좌측 상단")
        end_sub = st.text_input("엔딩 카드 보조 문구 (선택)", key="brand_sub", placeholder="예: 경기 가평군 · 예약 문의 환영")
        brand_opts = {
            "logo_path": save_uploaded_file(brand_logo_file) if brand_logo_file else None,
            "position": brand_position, "watermark": watermark_on, "end_card": end_card_on, "sub": end_sub.strip(),
        }

    st.markdown("---")
    _url_file = os.path.join("outputs", "tunnel_url.txt")       # scripts/watchdog.ps1이 터널을 재시작하면 새 주소를 기록
    if os.path.exists(_url_file):
        st.caption(f"📱 모바일 접속 주소: {open(_url_file, encoding='utf-8-sig').read().strip()}")
    st.caption(f"📁 작업 ID: `{current_job().job_id}`")
    if st.button("🆕 새 작업 시작", use_container_width=True, help="새 작업 공간을 만듭니다. 이전 작업 파일은 outputs/jobs/에 보존됩니다."):
        start_new_job()
        st.rerun()
    st.caption("AI Short-form Ad Video Factory v2.0 (Streamlit)")


# ==============================================================================
# 제작 모드 스위처
# ==============================================================================
st.segmented_control(
    "제작 모드", list(MODES), key="mode_switch", on_change=_on_mode_change, width="stretch",
    help="🏠 사장님 매장 홍보: 내 폰 사진·영상으로 상호명과 네이버 검색 유도가 들어간 홍보 영상\n"
         "🧳 숙소 제휴 쇼츠: 숙소 URL 크롤링, 숙소명을 숨긴 호기심 유발 + 댓글 유도\n"
         "🛒 쇼핑 제휴 쇼츠: 판매 페이지 실물 사진(또는 직접 올린 사진) + 써 본 사람 리뷰 썰 + 쿠팡 파트너스 표시\n"
         "📚 시골책방 도서 큐레이션: 책 한 권을 차분하게 (책 속 한 문장 인용 카드) + 책방 방문·알라딘 구매 링크"
)
production_mode = MODE_LABELS[pipeline_mode]

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
    st.radio("쇼핑 영상 구성", list(BUNDLE_LAYOUTS), format_func=BUNDLE_LAYOUTS.get, key="commerce_layout", horizontal=True)
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


# 쿠팡 상품 검색 (파트너스 공식 API): 상품명·고해상도 대표 사진·파트너스 링크를 한 번에
if is_commerce_mode and CoupangPartners().enabled:
    with st.expander("🔎 쿠팡 상품 검색 (파트너스 공식 API)", expanded=not st.session_state.uploaded_paths):
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
        for item in st.session_state.coupang_results:
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
                    same = [it["image_url"] for it in st.session_state.coupang_results if it["product_id"] == item["product_id"]]
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
    pts = suggest_store_points(st.session_state.get("owner_name", ""), st.session_state.get("owner_type", DEFAULT_BUSINESS_TYPE),
                               st.session_state.uploaded_paths, existing, api_key=api_key_, thumb_dir=current_job().assets_dir,
                               mock=mock_)
    if pts:
        st.session_state.owner_usp = " / ".join([x for x in [existing] + pts if x])
        st.session_state.store_points_msg = f"🤖 사진을 보고 매장 특징 {len(pts)}개를 보탰어요. 사실과 다르면 고쳐 주세요."
    else:
        st.session_state.store_points_msg = ("사진이 없거나(먼저 사진·영상을 올려 주세요) 정리할 특징을 찾지 못했어요."
                                             if not mock_ else "Mock 모드에서는 AI 특징 정리를 건너뜁니다.")


def _fill_place(it: dict):
    """네이버 검색 결과 1건 → 상호명·업종·주소 칸 (위젯이 만들어지기 전에 호출: 같은 실행에서 바로 반영)"""
    st.session_state.owner_name = it["name"]
    st.session_state.owner_type = it["business_type"]
    st.session_state.owner_address = it["road_address"] or it["address"]
    st.session_state.place_results = []


# 사장님: 네이버 플레이스 링크·상호명 → 공식 지역 검색 API로 상호명·업종·주소 (사진·리뷰는 가져오지 않음)
if is_owner_mode:
    naver = NaverLocalSearch()
    with st.container(border=True, key="place_box"):
        st.markdown("**🔗 네이버 플레이스·지도 링크 또는 상호명으로 가게 정보 가져오기**")
        q1, q2 = st.columns([4, 1])
        with q1:
            place_q = st.text_input("플레이스 링크 또는 상호명", key="place_query", label_visibility="collapsed",
                                    placeholder="예: https://naver.me/xxxx 또는 성수 불판")
        with q2:
            place_btn = st.button("정보 가져오기", key="place_fetch", use_container_width=True)
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
                        elif len(found) == 1:
                            # 결과가 하나면 고를 필요 없이 바로 채움 (한 번 클릭으로 끝)
                            _fill_place(found[0])
                            st.success(f"'{found[0]['name']}' 정보를 채웠어요 ({found[0]['category']}). 사진·영상은 아래에서 직접 올려 주세요.")
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
                    st.toast("상호명·업종·주소를 채웠어요. 사진·영상은 아래에서 직접 올려 주세요.", icon="🏢")
                    st.rerun()
        st.caption("📷 사진·리뷰는 네이버에서 가져오지 않아요 (이용 약관·방문자 사진 저작권). 아래에서 직접 찍은 사진·영상을 올려 주세요.")

# 상품/가게 정보 입력 폼 (모드별)
business_type, owner_search_kw = None, ""
if is_owner_mode:
    o1, o2 = st.columns([1, 1])
    with o1:
        product_name = st.text_input("🏢 상호명 (영상에 그대로 노출)", key="owner_name", placeholder="예: 성수 불판 성수점")
    with o2:
        business_type = st.selectbox("업종", list(BUSINESS_TYPES), key="owner_type",
                                     format_func=lambda k: BUSINESS_TYPES[k]["label"])
    o3, o4 = st.columns([1, 2])
    with o3:
        owner_search_kw = st.text_input("🔍 네이버 검색어", key="owner_search_kw", placeholder="비우면 상호명 사용")
    with o4:
        usp_desc = st.text_input("✨ 우리 가게 자랑 (사실만 입력, 가격·할인은 여기 적은 것만 영상에 나옵니다)", key="owner_usp",
                                 placeholder="예: 성수역 2분 / 단체석 30명 / 숙성 삼겹살")
    o5, o6 = st.columns([2, 1], vertical_alignment="bottom")
    with o5:
        owner_address = st.text_input("📍 주소·지역 (선택, 엔딩 카드에 표시)", key="owner_address", placeholder="예: 경기 가평군 청평면")
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
elif is_book_mode:
    st.session_state.setdefault("book_photo_count", 1)
    st.session_state.setdefault("book_template", "basic")
    st.radio("시골책방 템플릿", list(BOOK_TEMPLATES), key="book_template", horizontal=True,
             format_func=lambda k: {"basic": "📖 기본 큐레이션 (한 권 · 약 30~35초)",
                                    "fable": "🕯️ 우화 (원문 낭독 · 50~75초)",
                                    "reflect": "🌙 사유 (책소개 기반 · 원문 없이 50~60초)"}[k])
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
    st.radio("올린 사진 중 책 사진", [1, 2], key="book_photo_count", horizontal=True,
             format_func=lambda n: "1번째 = 책 표지" if n == 1 else "1번째 = 표지, 2번째 = 펼친 면")
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

if uses_scene_media:
    img_pool = st.session_state.get("image_pool", [])
    if is_travel_mode and img_pool:
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
    render_media_manager()

st.markdown("---")

# ==============================================================================
# 2단계: AI 대본 기획 및 실시간 편집 (st.data_editor)
# ==============================================================================
st.markdown("### 📝 2. AI 스토리보드 기획 및 대본 실시간 편집")

col_plan_btn, col_plan_desc = st.columns([1, 3])
with col_plan_btn:
    generate_plan_btn = st.button("🚀 스토리보드 자동 기획", use_container_width=True)
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
    if is_owner_mode or is_commerce_mode or is_book_mode:
        st.caption(f"모드: **{production_mode}** | 사진·영상 **{len(st.session_state.uploaded_paths)}개** 기준으로 씬 구성 | 성우: **{selected_actor_label}**")
    else:
        st.caption(f"모드: **{production_mode}** | 스타일: **{script_style_options[selected_script_style]}** | **{selected_tone_label}** | 성우: **{selected_actor_label}**")

def current_spec(bgm_path=None) -> pipeline.JobSpec:
    """지금 화면 입력값 → 공용 제작 엔진 입력 (캘린더 스케줄러는 같은 JobSpec을 일정 설명란에서 채움)"""
    ss = st.session_state
    return pipeline.JobSpec(
        mode=pipeline_mode, name=product_name, usp=usp_desc, media_paths=list(ss.uploaded_paths),
        num_scenes=DURATION_PRESETS[selected_duration]["default_scenes"] + 1,
        business_type=business_type, search_keyword=owner_search_kw, reading=ss.get("owner_reading", ""),
        address=ss.get("owner_address", ""), brand=brand_cfg,
        layout=ss.get("commerce_layout", "single"), link=ss.get("affiliate_link", ""),
        product_boxes=ss.get("product_boxes", {}), bundle_theme=ss.get("bundle_theme", ""),
        bundle_bottom=ss.get("bundle_bottom", ""), bundle_items=bundle_items_now() if is_bundle else [],
        book_title=ss.get("book_title", ""), book_author=ss.get("book_author", ""), book_quote=ss.get("book_quote", ""),
        book_link=ss.get("book_link", ""), book_search_kw=ss.get("book_search_kw", ""),
        book_photo_count=ss.get("book_photo_count", 1), book_template=ss.get("book_template", "basic"),
        affiliate_program=ss.get("affiliate_program", ""),
        voice_actor=ss.get("selected_actor", selected_actor), audio_mode=selected_audio_mode, bgm_path=bgm_path,
        api_key=api_key, mock=mock_mode, hook_hints=hook_hints if (is_owner_mode or is_travel_mode) else None)


# 원클릭(쇼핑): 콘티 없이 '영상 만들기'를 누르면 기획부터 자동으로 하고 이어서 렌더
auto_flow = bool(st.session_state.pop("auto_plan_then_render", False))
if generate_plan_btn or auto_flow:
    plan_spinner_msg = {
        "owner": "🧠 올린 사진·영상을 보고 우리 가게 홍보 대본을 쓰고 있습니다...",
        "travel": f"🧠 숙소 제휴 스토리보드({script_style_options[selected_script_style]})를 기획하고 있습니다...",
        "commerce": "🧠 쇼핑 제휴 스토리보드를 기획하고 있습니다...",
        "book": "🧠 책 한 권을 차분하게 소개하는 대본을 쓰고 있습니다...",
    }[pipeline_mode]
    with st.spinner(plan_spinner_msg):
        try:
            if is_travel_mode and not product_name.strip():
                raise ValueError("숙소명을 입력해 주세요. 링크에서 찾지 못한 정보는 지어내지 않습니다.")
            # 여행 모드: 크롤링 사진 인벤토리를 넘겨 실제 없는 시설을 대본에 쓰지 못하게 함
            photo_inventory = None
            travel_photo_first = is_travel_mode and bool(st.session_state.get("image_pool")) and not st.session_state.uploaded_paths
            if pipeline_mode in pipeline.PIPELINE_MODES:
                # 사장님·쇼핑(단일/묶음)·시골책방: 공용 제작 엔진이 기획 + 씬 사진 배정 (캘린더 스케줄러와 같은 코드)
                scenes = pipeline.plan(current_spec(), current_job())
            elif travel_photo_first:
                # 트랙 A 숙소 제휴: 사진 풀 비전 분류 → 7씬 비율 슬롯에 사진 배정 → 실제 사진 설명 기반 대본
                with st.spinner("👁️ 사진을 비전으로 분류해 씬 비율(수영장3·객실2·부대시설2)에 배정하고 대본을 쓰는 중..."):
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
                    hook_hints=hook_hints if is_travel_mode else None
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
            if auto_flow:
                st.session_state.auto_render_now = True
        except Exception as pe:
            st.error(f"기획 실패: {pe}")

# 스토리보드 실시간 편집기 (st.data_editor)
if st.session_state.storyboard:
    _ref = (st.session_state.storyboard[0] or {}).get("hook_reference") if st.session_state.storyboard else None
    if _ref:
        _used = st.session_state.storyboard[0].get("hook_pattern_used")
        st.caption("🔥 씬 1 후킹: " + (f"레이더 패턴 적용 [{HOOK_TYPE_KO.get(_used['hook_type'], _used['hook_type'])}] {_used['pattern_template']}"
                                     if _used else "레이더 패턴 참고했으나 사실에 맞는 패턴이 없어 기본 규칙 적용")
                   + " · 참고 " + ", ".join(f"{HOOK_TYPE_KO.get(r['hook_type'], r['hook_type'])} {r['outlier_ratio']:.1f}배" for r in _ref))
    st.markdown("💡 *아래 표에서 대사(voiceover), 화면 자막(subtitle), 후킹 타이틀(hook_title), 비주얼 키워드 등을 직접 클릭하여 수정할 수 있습니다:*")
    
    df_scenes = pd.DataFrame(st.session_state.storyboard)
    if st.session_state.get("storyboard_mode", pipeline_mode) != pipeline_mode:
        st.warning(f"⚠️ 이 콘티는 '{MODE_LABELS.get(st.session_state.get('storyboard_mode'), '다른')}' 모드로 기획되었습니다. "
                   f"현재 모드로 만들려면 스토리보드를 다시 기획해 주세요.")
    if is_owner_mode or is_commerce_mode or is_book_mode:
        cols_to_display = ["scene_index", "role", "duration_sec", "hook_title", "voiceover"]
    elif is_travel_mode:
        cols_to_display = ["scene_index", "role", "target_visual_role", "visual_keywords", "duration_sec", "hook_title", "subtitle", "voiceover", "video_motion_prompt_en"]
    else:
        cols_to_display = ["scene_number", "duration_sec", "hook_title", "caption", "voiceover", "visual_description", "video_motion_prompt_en", "image_prompt_en"]
    existing_cols = [c for c in cols_to_display if c in df_scenes.columns]

    edited_df = st.data_editor(
        df_scenes[existing_cols],
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "scene_index": st.column_config.NumberColumn("씬 번호", disabled=True, width="small"),
            "scene_number": st.column_config.NumberColumn("씬 번호", disabled=True, width="small"),
            "role": st.column_config.TextColumn("서사 역할", width="small"),
            "target_visual_role": st.column_config.TextColumn("시각 매칭 역할", width="medium"),
            "visual_keywords": st.column_config.ListColumn("시각 검색 키워드", width="medium"),
            "duration_sec": st.column_config.NumberColumn("지속시간(초)", width="small"),
            "hook_title": st.column_config.TextColumn("상단 후킹 바", width="medium"),
            "subtitle": st.column_config.TextColumn("화면 자막", width="medium"),
            "caption": st.column_config.TextColumn("화면 자막", width="medium"),
            "voiceover": st.column_config.TextColumn("나레이션 대사", width="large"),
            "visual_description": st.column_config.TextColumn("비주얼 연출", width="large"),
            "video_motion_prompt_en": st.column_config.TextColumn("2D Ken Burns 모션", width="medium"),
            "image_prompt_en": st.column_config.TextColumn("이미지 프롬프트 (EN)", width="large"),
        }
    )

    # 편집 데이터 세션 반영: 표에 안 보이는 키(image_path, matched_image_url, focus_bottom 등)를 보존하며 병합
    original_by_num = {_scene_num(s, i + 1): s for i, s in enumerate(st.session_state.storyboard)}
    next_num = max(original_by_num.keys(), default=0) + 1
    merged_records = []
    for r in edited_df.to_dict(orient="records"):
        s_idx = _safe_int(r.get("scene_index") or r.get("scene_number"), 0)
        if s_idx <= 0:  # data_editor에서 새로 추가된 행
            s_idx = next_num
            next_num += 1
        merged = {**original_by_num.get(s_idx, {}), **r, "scene_index": s_idx, "scene_number": s_idx}
        # 음성 대본(voiceover)과 자막(subtitle/caption) 100% 텍스트 일치화
        text_content = str(merged.get("voiceover") or merged.get("subtitle") or merged.get("caption") or "").strip()
        if text_content:
            merged["voiceover"] = text_content
            merged["subtitle"] = text_content
            merged["caption"] = text_content
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
                            chosen_i = st.selectbox(
                                f"후보에서 선택 ({len(clean_pool)}장)",
                                options=list(range(len(clean_pool))),
                                format_func=lambda i: f"#{i + 1} [{str(clean_pool[i].get('category', '')).upper()}] {clean_pool[i].get('title') or '숙소 공간'} (…{clean_pool[i].get('url', '')[-22:]})",
                                key=f"sel_cand_{s_num}"
                            )
                            target_cand = clean_pool[chosen_i]
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
# 3단계: 최종 광고 영상 렌더링 및 플레이어
# ==============================================================================
_rc1, _rc2 = st.columns([4, 1.3], vertical_alignment="center")
with _rc2:
    # 모바일은 사이드바가 접혀 있어 BGM 상태가 안 보임 → 렌더 버튼 옆에도 같은 스위치 (사이드바와 양방향 동기화)
    st.session_state.bgm_on_main = st.session_state.get("bgm_on", False)
    st.toggle("🎵 BGM 사용", key="bgm_on_main", disabled=not bgm_tracks, on_change=_sync_bgm, args=("bgm_on_main", "bgm_on"),
              help="자체 음원(assets/bgm)을 나레이션 아래에 깔아요. 음원 선택은 사이드바에서 (기본 랜덤).")
with _rc1:
    render_btn = st.button("✨ 인스타 릴스 / 유튜브 쇼츠용 영상 만들기", type="primary", use_container_width=True, key="render_cta")
bgm_on = st.session_state.get("bgm_on", False) and bool(bgm_tracks)
if bgm_on and bgm_choice is None:
    bgm_choice = st.session_state.get("bgm_choice") or bgm_mod.RANDOM
if is_commerce_mode:
    st.caption("🛒 쇼핑은 [상품 검색] → [이 상품 사용] → [영상 만들기] 세 번이면 끝나요. 기획은 자동으로 진행됩니다.")
if render_btn and is_commerce_mode and not st.session_state.storyboard:
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
        progress_bar = st.progress(0, text="파이프라인 초기화 중...")

        try:
            # 공용 제작 엔진: 음성(동시) → 클립 → 썸네일·업로드 카피 → 조립(자막·제휴 표시·고정 바·라벨·BGM)
            res = pipeline.render(current_spec(bgm_path=bgm_mod.pick_track(bgm_choice) if bgm_on else None), current_job(),
                                  scenes, progress=lambda pct, text: progress_bar.progress(pct, text=text), force=force_render)
            st.session_state.generated_images = res.generated_images
            st.session_state.shorts_copy = res.shorts_copy
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
            st.info("🔇 BGM 꺼짐 · 넣으려면 렌더 버튼 옆 '🎵 BGM 사용'을 켜고 다시 만들어 주세요. "
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
        elif is_commerce_mode:
            st.markdown("**📝 영상 설명·고정 댓글에 함께 올릴 문구** (쿠팡 파트너스 안내 + 공정위 추천·보증 심사지침)")
            link = st.session_state.get("affiliate_link") or "(쿠팡 파트너스 링크를 붙여 넣으세요)"
            post_text = f"{COUPANG_DISCLOSURE_FULL}\n제품 링크: {link}"
            st.code(post_text, language=None)
            copy_button(post_text, "📋 문구+링크 원클릭 복사", key="copy_commerce")
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
        _ota = st.session_state.get("affiliate_program")
        _disc = ("" if is_bundle            # 묶음: 상품 링크 종류별 고지를 첫 댓글에서 자동으로 붙임
                 else COUPANG_DISCLOSURE_FULL if is_commerce_mode
                 else ota_disclosure_full(_ota) if is_travel_mode and _ota else "")
        _sb = st.session_state.get("storyboard") or []
        if is_book_mode:
            st.info("📚 시골책방 채널 업로드는 아직 연결 전이에요. 영상은 로컬에 저장됐고, 첫 댓글은 왼쪽에서 대기열로 저장돼요.")
        else:
            render_youtube_panel(st.session_state.final_video_path, st.session_state.get("thumb_path"), pipeline_mode,
                                 hook=(_sb[0].get("hook_title") or "") if _sb else "",
                                 link=st.session_state.get("affiliate_link", "") if not (is_owner_mode or is_bundle) else "",
                                 disclosure=_disc, business_name=product_name,
                                 search_kw=owner_search_kw if is_owner_mode else "",
                                 copy=st.session_state.get("shorts_copy"),
                                 items=bundle_items_now() if is_bundle else None,
                                 disclosure_short=(bundle_disclosure_short(bundle_items_now()) if is_bundle
                                                   else COUPANG_DISCLOSURE if is_commerce_mode
                                                   else "※ " + OTA_DISCLOSURE if is_travel_mode and _ota else ""))
        st.markdown("#### 🖼️ 씬별 키프레임 갤러리")
        if st.session_state.generated_images:
            cols = st.columns(len(st.session_state.generated_images))
            for i, p in enumerate(st.session_state.generated_images):
                if os.path.exists(p):
                    with cols[i]:
                        st.image(p, caption=f"Scene {i+1}", use_container_width=True)

        st.markdown("#### 📊 제작 사양")
        st.markdown(f"""
        - **제작 모드**: {production_mode}
        - **해상도**: 1080 x 1920 (9:16 Vertical Video)
        - **비디오 렌더링**: 로컬 2D Ken Burns 모션 + 직접 촬영 영상 (24 FPS, H.264 / AAC, 비용 0원)
        - **작업 ID**: `{current_job().job_id}`
        - **적용 톤앤매너**: {selected_tone_label}
        - **적용 성우**: {selected_actor_label} ({audio_mode_options[selected_audio_mode]})
        - **자막 폰트**: Pretendard-Black (Weight 900)
        """)
