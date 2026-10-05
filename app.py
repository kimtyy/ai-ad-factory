"""
app.py - AI Short-form Ad Video Factory 반응형 Streamlit 웹 대시보드
쇼핑몰 크롤링, 누끼 추출, 톤앤매너/길이/성우 선택, 실시간 콘티 편집 및 9:16 광고 영상 원클릭 렌더링
"""
import os
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
from core.image_processor import extract_product_cutout, composite_cutout_on_background
from core.job import JobContext, cleanup_old_jobs
from core.media import is_video_file, probe_media, extract_thumbnail
from core.planners import BUSINESS_TYPES, DEFAULT_BUSINESS_TYPE, plan_owner_storyboard
from core.planners.travel import plan_travel_storyboard
from core.radar.hints import load_hook_hints, vertical_for
from core.radar.hooks import HOOK_TYPE_KO
from ui.radar_page import render_radar_page, RADAR_PERSIST_KEYS, init_radar_state, hints_enabled, allow_dryrun_hints
from core.planners.owner import ROLE_LABELS_KO as OWNER_ROLE_LABELS
from core.media.normalize import DEFAULT_TRIM_START
from core.render.brand import BrandOverlay, EndCard

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

# 세션 상태 초기화
if "storyboard" not in st.session_state:
    st.session_state.storyboard = []
if "scraped_data" not in st.session_state:
    st.session_state.scraped_data = None
if "cutout_path" not in st.session_state:
    st.session_state.cutout_path = None
if "image_pool" not in st.session_state:
    st.session_state.image_pool = []
if "gallery_images" not in st.session_state:
    st.session_state.gallery_images = []
if "production_mode" not in st.session_state:
    st.session_state.production_mode = "owner"
if "final_video_path" not in st.session_state:
    st.session_state.final_video_path = None
if "generated_images" not in st.session_state:
    st.session_state.generated_images = []
if "script_style" not in st.session_state:
    st.session_state.script_style = "info"
if "rejected_urls" not in st.session_state:
    st.session_state.rejected_urls = set()   # 사용자가 거부한 사진 URL (재선별 시 재등장 금지)
if "vision_cache" not in st.session_state:
    st.session_state.vision_cache = {}       # 비전 판정 캐시 (이미지 해시:역할 -> 판정)
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
    st.session_state.generated_images = []
    st.session_state.cutout_path = None
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
    """씬 사진/영상이 바뀌면 그 씬의 파생 키프레임/클립 캐시만 삭제"""
    for suffix in (".png", ".mp4", ".mp4.json", "_thumb.jpg"):
        p = current_job().asset_path(f"scene_{s_num:02d}{suffix}")
        if os.path.exists(p):
            os.remove(p)


def save_uploaded_file(up_file) -> str:
    """업로드 사진/영상을 작업 공간 uploads/에 내용 해시 기반 고유 파일명으로 저장"""
    data = up_file.getbuffer()
    ext = os.path.splitext(up_file.name)[-1].lower() or ".jpg"
    path = os.path.join(current_job().uploads_dir, f"upload_{hashlib.md5(data).hexdigest()[:12]}{ext}")
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    return path


def scene_has_media(scene: dict) -> bool:
    media = scene.get("media") or {}
    if media.get("type") == "video" and media.get("path") and os.path.exists(media["path"]):
        return True
    return bool(scene.get("image_path") and os.path.exists(scene["image_path"]))


def assign_media_to_scene(scene: dict, path: str, source: str, matched_url: str = None):
    """씬 dict에 사진 또는 스마트폰 영상을 키로 기록 (순번 매핑 금지)"""
    if is_video_file(path):
        probe_media(path)  # 읽을 수 없는 영상이면 여기서 예외 → 배정하지 않음
        scene["media"] = {"type": "video", "path": path, "trim_start": DEFAULT_TRIM_START, "fit": "auto"}
        scene.pop("image_path", None)
    else:
        scene["image_path"] = path
        scene.pop("media", None)
    scene["asset_status"] = source
    scene["vision_verdict"] = None
    if matched_url:
        scene["matched_image_url"] = matched_url
    else:
        scene.pop("matched_image_url", None)
    invalidate_scene_derivatives(_scene_num(scene, 1))


def assign_uploads_to_scenes(scenes: list, paths: list):
    for scene, p in zip(scenes, paths):
        assign_media_to_scene(scene, p, "uploaded")


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
#   commerce (트랙 A) 쇼핑 제휴 쇼츠: 상품 URL + 누끼 합성
# ==============================================================================
MODES = {"🏠 사장님 매장 홍보": "owner", "🧳 숙소 제휴 쇼츠": "travel", "🛒 쇼핑 제휴 쇼츠": "commerce"}
MODE_LABELS = {v: k for k, v in MODES.items()}
SCENE_ROLE_LABELS = {**OWNER_ROLE_LABELS, "hook": "🔥 후킹", "feature": "✨ 특장점", "facility": "☕ 부대시설",
                     "room": "🛏 객실", "cta": "📍 CTA"}


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
for _k in ("owner_name", "owner_type", "owner_search_kw", "owner_usp",
           "brand_watermark", "brand_end_card", "brand_position", "brand_sub", "page_switch") + RADAR_PERSIST_KEYS:
    if _k in st.session_state:
        st.session_state[_k] = st.session_state[_k]

is_owner_mode = pipeline_mode == "owner"
is_travel_mode = pipeline_mode == "travel"
uses_scene_media = pipeline_mode in ("owner", "travel")   # 씬별 실사 사진/영상 배정 UI를 쓰는 모드

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
         "🛒 쇼핑 제휴 쇼츠: 상품 URL 크롤링 + 누끼 합성 광고"
)
production_mode = MODE_LABELS[pipeline_mode]

target_url, crawl_btn = "", False
if not is_owner_mode:
    col_url, col_btn = st.columns([4, 1])
    with col_url:
        url_placeholder = (
            "숙소 상품 페이지 URL (야놀자, 여기어때, 에어비앤비, 네이버 예약 등)"
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
        st.session_state.scraped_data = None

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

            if scraped.get("success"):
                st.session_state.scraped_data = scraped
                st.success(f"✅ 크롤링 성공! '{scraped['product_name']}' 정보를 불러왔습니다.")
                
                if is_travel_mode:
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
                    # 일반 커머스 모드: 단일 이미지 누끼 추출 진행
                    st.session_state.gallery_images = []
                    if scraped.get("image_path") and os.path.exists(scraped["image_path"]):
                        with st.spinner("✂️ rembg AI로 제품 누끼(배경 투명화)를 자동 추출하고 있습니다..."):
                            try:
                                cutout_p = extract_product_cutout(scraped["image_path"], current_job().asset_path("product_cutout.png"))
                                st.session_state.cutout_path = cutout_p
                                st.toast("제품 누끼 추출 완료!", icon="✨")
                            except Exception as e:
                                st.warning(f"누끼 추출 실패: {e}")
            else:
                st.error(f"❌ 크롤링 실패: {scraped.get('error')}. 직접 정보를 입력해 주세요.")

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
    owner_search_kw = owner_search_kw.strip() or product_name.strip()
else:
    default_name = "제주 애월 프라이빗 감성 풀빌라" if is_travel_mode else "히알루론산 수분 앰플"
    default_usp = "전 객실 파노라마 오션뷰, 야외 온수 인피니티 풀, 프라이빗 자쿠지" if is_travel_mode else "속당김 즉각 해결, 48시간 고보습 지속"
    scraped_now = st.session_state.scraped_data or {}
    col_prod, col_usp = st.columns([1, 2])
    with col_prod:
        prod_label = "숙소명 (대본에서는 숨김 처리)" if is_travel_mode else "홍보 제품명"
        product_name = st.text_input(prod_label, value=scraped_now.get("product_name", default_name))
    with col_usp:
        usp_label = "숙소 특장점 (USP)" if is_travel_mode else "핵심 소구점 (USP)"
        usp_desc = st.text_input(usp_label, value=scraped_now.get("usp", default_usp))

# 사장님 모드 브랜드 오버레이: 상호명/검색어는 본문 입력값을 사용
brand_cfg = None
if is_owner_mode and brand_opts and product_name.strip():
    brand_cfg = BrandOverlay(
        business_name=product_name.strip(),
        logo_path=brand_opts["logo_path"],
        position=brand_opts["position"],
        show_label=brand_opts["watermark"],
        end_card=EndCard(search_keyword=owner_search_kw, sub_text=brand_opts["sub"]) if brand_opts["end_card"] else None,
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
    else:
        st.caption("💡 숙소 URL을 크롤링하거나, 아래에서 사진·영상을 직접 올릴 수 있습니다.")

    uploaded_files = st.file_uploader(
        "📱 내 폰 사진·영상 첨부 (JPG/PNG/MP4/MOV)",
        accept_multiple_files=True,
        type=UPLOAD_TYPES,
        help="영상은 자동으로 9:16 세로 규격화·무음 처리되어 그대로 영상 장면으로 들어갑니다."
    )
    if uploaded_files:
        new_uploaded_paths = [save_uploaded_file(up_file) for up_file in uploaded_files[:10]]
        if new_uploaded_paths != st.session_state.uploaded_paths:
            st.session_state.uploaded_paths = new_uploaded_paths
            # 이미 기획된 스토리보드가 있으면 즉시 씬 키로 배정 (없으면 기획 직후 배정)
            if st.session_state.storyboard:
                assign_uploads_to_scenes(st.session_state.storyboard, new_uploaded_paths)
            st.toast(f"📱 사진·영상 {len(new_uploaded_paths)}개 첨부 완료!", icon="✨")
            st.rerun()
else:
    # 쇼핑 제휴 모드: 단일 대표 이미지 및 투명 누끼 PNG 박스 노출
    if st.session_state.scraped_data and st.session_state.scraped_data.get("image_path"):
        col_img1, col_img2 = st.columns(2)
        with col_img1:
            st.markdown("**수집된 원본 상품 이미지**")
            st.image(st.session_state.scraped_data["image_path"], width=260)
        with col_img2:
            st.markdown("**AI 추출 투명 누끼 PNG**")
            if st.session_state.cutout_path and os.path.exists(st.session_state.cutout_path):
                st.image(st.session_state.cutout_path, width=260)
            else:
                st.info("누끼 추출 대기 중")

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
    if is_owner_mode:
        st.caption(f"모드: **{production_mode}** | 올린 미디어 **{len(st.session_state.uploaded_paths)}개** 기준으로 씬 구성 | 성우: **{selected_actor_label}**")
    else:
        st.caption(f"모드: **{production_mode}** | 스타일: **{script_style_options[selected_script_style]}** | **{selected_tone_label}** | 성우: **{selected_actor_label}**")

if generate_plan_btn:
    plan_spinner_msg = {
        "owner": "🧠 올린 사진·영상을 보고 우리 가게 홍보 대본을 쓰고 있습니다...",
        "travel": f"🧠 숙소 제휴 스토리보드({script_style_options[selected_script_style]})를 기획하고 있습니다...",
        "commerce": "🧠 쇼핑 제휴 스토리보드를 기획하고 있습니다...",
    }[pipeline_mode]
    with st.spinner(plan_spinner_msg):
        try:
            if is_owner_mode and not product_name.strip():
                raise ValueError("상호명을 입력해 주세요.")
            # 여행 모드: 크롤링 사진 인벤토리를 넘겨 실제 없는 시설을 대본에 쓰지 못하게 함
            photo_inventory = None
            travel_photo_first = is_travel_mode and bool(st.session_state.get("image_pool")) and not st.session_state.uploaded_paths
            if is_owner_mode:
                # 트랙 B: 업로드 미디어를 보고 씬 구성, 상호명 보존, 네이버 검색 CTA
                scenes = plan_owner_storyboard(
                    business_name=product_name,
                    usp=usp_desc,
                    business_type=business_type,
                    search_keyword=owner_search_kw,
                    media_paths=st.session_state.uploaded_paths,
                    num_scenes=DURATION_PRESETS[selected_duration]["default_scenes"] + 1,
                    api_key=api_key,
                    mock=mock_mode,
                    thumb_dir=current_job().assets_dir,
                    output_plan_path=current_job().plan_path,
                    hook_hints=hook_hints,
                )
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

            # [씬별 에셋 배정] 업로드 미디어 우선 배정 → (숙소 제휴) 나머지 씬만 핀포인트 매칭 (모두 씬 키로 기록)
            if uses_scene_media:
                assign_uploads_to_scenes(scenes, st.session_state.uploaded_paths)
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
    if is_owner_mode:
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
                    st.markdown(f"**{s_num:02d} · {role_label}** · ⏱ {scene.get('duration_sec', 2.5)}초")

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
render_btn = st.button("✨ 인스타 릴스 / 유튜브 쇼츠용 영상 만들기", type="primary", use_container_width=True, key="render_cta")

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
        if is_owner_mode and not product_name.strip():
            st.error("상호명을 입력해 주세요. 워터마크와 네이버 검색 엔딩 카드에 사용됩니다.")
            st.stop()
        if missing_scenes:
            st.error(f"씬 {', '.join(map(str, missing_scenes))}에 사진이 없습니다. 위 갤러리에서 사진을 교체하거나 업로드한 뒤 다시 시도해 주세요.")
            st.stop()
        progress_bar = st.progress(0, text="파이프라인 초기화 중...")

        try:
            # 1. 에셋 생성기 초기화 (UI 선택 성우 값 정상 전달 보장)
            chosen_actor = st.session_state.get("selected_actor", selected_actor)
            print(f"[app.py] AI 성우 전달 확인: label='{selected_actor_label}', key='{chosen_actor}'")
            asset_gen = AssetGenerator(
                gemini_api_key=api_key,
                voice_actor=chosen_actor,
                audio_mode=selected_audio_mode,
                mock=mock_mode,
                mode=pipeline_mode,
                cache_dir=current_job().assets_dir
            )

            manifest = []
            total_scenes = len(scenes)
            generated_imgs = []

            # 2. 씬별 에셋 순차 생성
            for idx, scene in enumerate(scenes):
                s_num = int(scene.get("scene_index") or scene.get("scene_number") or (idx + 1))
                scene["scene_index"] = s_num
                scene["scene_number"] = s_num

                step_pct = int((idx / total_scenes) * 70)
                gen_step_text = (
                    f"[{idx+1}/{total_scenes}] 씬 {s_num} 풍경 갤러리 매핑 및 음성 생성 중..."
                    if is_travel_mode
                    else f"[{idx+1}/{total_scenes}] 씬 {s_num} 이미지 및 음성 생성 중..."
                )
                progress_bar.progress(step_pct, text=gen_step_text)

                # 여행 상품 모드: 핀포인트 다운로드된 씬 에셋 1:1 매핑 (rembg 누끼 연산 100% 바이패스)
                g_cut = scene.get("image_path") if uses_scene_media else None
                if g_cut and not os.path.exists(g_cut):
                    g_cut = None

                img_path = asset_gen.generate_image(scene, force=force_render, gallery_image_path=g_cut)
                generated_imgs.append(img_path)

                # 일반 커머스 모드: 제품 누끼가 존재하고 씬 2인 경우 누끼 합성 (여행 모드는 절대 실행 안 함)
                if pipeline_mode == "commerce" and st.session_state.cutout_path and os.path.exists(st.session_state.cutout_path):
                    if s_num == 2:
                        comp_img_path = current_job().scene_asset(s_num, ".png", suffix="_comp")
                        try:
                            composite_cutout_on_background(
                                background_path=img_path,
                                cutout_path=st.session_state.cutout_path,
                                output_path=comp_img_path,
                                scale_ratio=0.48,
                                y_center_ratio=0.62
                            )
                            img_path = comp_img_path
                            generated_imgs[-1] = img_path
                            logger.info(f"씬 {s_num}에 제품 누끼 합성 완료")
                        except Exception as ce:
                            logger.warning(f"누끼 합성 실패: {ce}")

                # 음성 생성
                audio_path = asset_gen.generate_voice(scene, force=force_render)

                # 비디오 생성 (업로드 영상이면 UGC 정규화 클립, 아니면 로컬 2D Ken Burns 모션 - 외부 비디오 API 차단)
                video_path = asset_gen.generate_video(scene, img_path, force=force_render)

                item = dict(scene)
                item["scene_index"] = s_num
                item["scene_number"] = s_num
                item["image_path"] = img_path
                item["audio_path"] = audio_path
                item["video_path"] = video_path
                # 음성과 자막 100% 일치 보장
                vo_sync = (scene.get("voiceover") or scene.get("subtitle") or scene.get("caption") or "").strip()
                item["voiceover"] = vo_sync
                item["subtitle"] = vo_sync
                item["caption"] = vo_sync
                manifest.append(item)

            st.session_state.generated_images = generated_imgs

            # 3. 타임라인 조립 & 자막 합성 (Compositor)
            progress_bar.progress(75, text="타임라인 조립, 상단 후킹 바 및 볼드 자막 오버레이 렌더링 중...")
            importlib.reload(core.compositor)
            from core.compositor import AdCompositor
            compositor = AdCompositor(output_dir=current_job().final_dir)

            final_mp4 = compositor.assemble_ad(
                asset_manifest=manifest,
                output_filename="final.mp4",
                fps=24,
                audio_mode=selected_audio_mode,
                mode=pipeline_mode,
                brand=brand_cfg
            )
            current_job().save_plan(scenes)

            progress_bar.progress(100, text="✨ 렌더링 완료!")
            st.session_state.final_video_path = final_mp4
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
        st.info("💡 인스타 릴스/쇼츠 업로드 시 플랫폼 내 추천 트렌딩 음원을 함께 태그해 보세요.")
    with col_meta:
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
