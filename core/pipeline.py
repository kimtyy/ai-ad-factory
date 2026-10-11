"""
core/pipeline.py - 공용 제작 엔진: 기획 → 씬 사진 배정 → 렌더(음성·클립·썸네일·조립·BGM) → 업로드 문구·첫 댓글

Streamlit 화면(app.py)과 구글 캘린더 스케줄러가 같은 엔진을 씀 (화면 상태 st.session_state에 의존하지 않음).
- JobSpec: 모드별 입력값 (화면 입력칸 또는 캘린더 일정 설명란에서 채움)
- plan(spec, job) → 씬 목록 (사진·영상 배정까지)
- render(spec, job, scenes) → RenderResult (최종 영상·썸네일·업로드 카피·BGM 상태·첫 댓글)
- upload_texts(spec, copy) → 유튜브 제목·설명·첫 댓글
숙소(travel) '기획'은 크롤링 사진 풀·비전 매칭이 화면 흐름에 묶여 있어 app.py에 남김 (렌더는 이 엔진 공용)
"""
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ad_factory.pipeline")

PIPELINE_MODES = ("owner", "commerce", "book")      # 기획까지 엔진이 맡는 모드 (travel은 렌더만)
DEFAULT_SCENES = 7                                  # 사장님·쇼핑 단일: 기본 씬 수 (화면의 길이 프리셋과 같은 값)
UPLOAD_TAGS = {
    "travel": ["숙소추천", "호텔추천", "여행", "국내여행", "여행을떠나자"],
    "commerce": ["추천템", "살림템", "리뷰"],
    "owner": ["가게추천", "가볼만한곳", "동네가게"],
    "book": ["책추천", "동네책방", "독서"],
}


@dataclass
class JobSpec:
    mode: str                                    # owner / commerce / book / travel
    name: str = ""                               # 사장님 상호명 · 쇼핑 상품명 · 시골책방 책방 이름 · 숙소명
    usp: str = ""                                # 특장점·책 소개 (사실만)
    media_paths: List[str] = field(default_factory=list)
    num_scenes: Optional[int] = None
    # 사장님
    business_type: Optional[str] = None
    search_keyword: str = ""
    reading: str = ""                            # 상호명 읽는 법 (나레이션)
    address: str = ""
    brand: Any = None                            # core.render.brand.BrandOverlay
    # 쇼핑
    layout: str = "single"                       # single / bundle
    link: str = ""                               # 단일 상품 제휴 링크
    product_boxes: Dict[str, Any] = field(default_factory=dict)
    bundle_theme: str = ""
    bundle_bottom: str = ""
    bundle_items: List[Dict[str, Any]] = field(default_factory=list)
    # 시골책방
    book_title: str = ""
    book_author: str = ""
    book_quote: str = ""
    book_link: str = ""
    book_search_kw: str = ""
    book_photo_count: int = 1
    book_template: str = "basic"
    # 숙소 (렌더만)
    affiliate_program: str = ""
    # 공통
    voice_actor: str = "female_20s"
    audio_mode: str = "voice_and_sub"
    bgm_path: Optional[str] = None
    api_key: Optional[str] = None
    mock: bool = False
    hook_hints: Any = None

    @property
    def is_bundle(self) -> bool:
        return self.mode == "commerce" and self.layout == "bundle"


@dataclass
class RenderResult:
    final_path: str
    thumb_path: Optional[str]
    shorts_copy: Optional[Dict[str, Any]]
    bgm: Dict[str, Any]
    generated_images: List[str]
    comment: str = ""


# ---------------------------------------------------------------- 씬 사진·영상 배정 (화면·스케줄러 공용)
def scene_has_media(scene: dict) -> bool:
    media = scene.get("media") or {}
    if media.get("type") == "video" and media.get("path") and os.path.exists(media["path"]):
        return True
    return bool(scene.get("image_path") and os.path.exists(scene["image_path"]))


def invalidate_scene_derivatives(job, s_num: int) -> None:
    """씬 사진/영상이 바뀌면 그 씬의 파생 키프레임/클립 캐시만 삭제"""
    for suffix in (".png", ".mp4", ".mp4.json", "_thumb.jpg"):
        p = job.asset_path(f"scene_{s_num:02d}{suffix}")
        if os.path.exists(p):
            os.remove(p)


def _scene_num(scene: dict, default: int) -> int:
    try:
        return int(scene.get("scene_index") or scene.get("scene_number") or default)
    except (TypeError, ValueError):
        return default


def assign_media_to_scene(job, scene: dict, path: str, source: str, matched_url: Optional[str] = None) -> None:
    """씬 dict에 사진 또는 스마트폰 영상을 키로 기록 (순번 매핑 금지)"""
    from core.media import is_video_file, probe_media
    from core.media.normalize import DEFAULT_TRIM_START
    if is_video_file(path):
        probe_media(path)          # 읽을 수 없는 영상이면 여기서 예외 → 배정하지 않음
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
    invalidate_scene_derivatives(job, _scene_num(scene, 1))


def assign_uploads_to_scenes(job, scenes: list, paths: list, cycle: bool = False,
                             product_boxes: Optional[Dict[str, Any]] = None) -> None:
    """i번째 미디어 → i번째 씬. cycle=True(쇼핑)면 사진이 씬보다 적을 때 처음부터 돌려 씀 (앵글을 바꿔 잘라냄)"""
    from core.render.commerce_layout import assign_shots
    if cycle and paths:
        paths = [paths[i % len(paths)] for i in range(len(scenes))]
        for scene, shot, p in zip(scenes, assign_shots(paths), paths):
            scene["shot"] = shot
            if (product_boxes or {}).get(p):
                scene["product_box"] = product_boxes[p]      # Gemini가 찾은 상품 본체 위치
    for scene, p in zip(scenes, paths):
        assign_media_to_scene(job, scene, p, "uploaded")


# ---------------------------------------------------------------- 기획
def plan(spec: JobSpec, job) -> List[Dict[str, Any]]:
    """모드별 플래너 → 씬 사진·영상 배정. 입력이 모자라면 ValueError (지어내지 않음)"""
    from core.planners import plan_commerce_storyboard, plan_owner_storyboard
    from core.planners.book import plan_book_storyboard
    from core.planners.commerce_bundle import plan_bundle_storyboard
    if spec.mode not in PIPELINE_MODES:
        raise ValueError(f"'{spec.mode}' 모드 기획은 화면에서만 할 수 있어요 (지원: {', '.join(PIPELINE_MODES)}).")
    common = dict(api_key=spec.api_key, mock=spec.mock, thumb_dir=job.assets_dir, output_plan_path=job.plan_path)
    if spec.mode == "owner":
        if not spec.name.strip():
            raise ValueError("상호명을 입력해 주세요.")
        scenes = plan_owner_storyboard(business_name=spec.name, usp=spec.usp, business_type=spec.business_type,
                                       search_keyword=spec.search_keyword or spec.name, media_paths=spec.media_paths,
                                       num_scenes=spec.num_scenes or DEFAULT_SCENES, hook_hints=spec.hook_hints, **common)
    elif spec.mode == "book":
        scenes = plan_book_storyboard(store_name=spec.name, book_title=spec.book_title, author=spec.book_author,
                                      book_info=spec.usp, quote=spec.book_quote, media_paths=spec.media_paths,
                                      book_photo_count=spec.book_photo_count, template=spec.book_template, **common)
    elif spec.is_bundle:
        scenes = plan_bundle_storyboard(theme=spec.bundle_theme, items=spec.bundle_items,
                                        media_paths=spec.media_paths, **common)
    else:
        if not spec.name.strip():
            raise ValueError("상품명을 입력해 주세요. 링크에서 찾지 못한 정보는 지어내지 않습니다.")
        if not spec.media_paths and not spec.mock:
            raise ValueError("상품 사진이 없습니다. 판매 페이지 링크를 크롤링하거나 상품 사진을 직접 올려 주세요.")
        scenes = plan_commerce_storyboard(product_name=spec.name, usp=spec.usp, media_paths=spec.media_paths,
                                          num_scenes=spec.num_scenes or DEFAULT_SCENES, product_boxes=spec.product_boxes,
                                          **common)
    assign_scene_media(spec, job, scenes)
    return scenes


def assign_scene_media(spec: JobSpec, job, scenes: List[Dict[str, Any]]) -> None:
    """플래너가 고른 컷(source_path)이 전 씬에 있으면 그대로, 아니면 올린 순서대로 (쇼핑은 돌려 쓰며 앵글 변경)"""
    if spec.mode in ("commerce", "book") and scenes and all(s.get("source_path") for s in scenes):
        for s in scenes:
            assign_media_to_scene(job, s, s["source_path"], "matched")
    else:
        assign_uploads_to_scenes(job, scenes, spec.media_paths, cycle=spec.mode == "commerce", product_boxes=spec.product_boxes)


def missing_media(scenes: List[Dict[str, Any]]) -> List[int]:
    return [_scene_num(s, i + 1) for i, s in enumerate(scenes) if not scene_has_media(s)]


# ---------------------------------------------------------------- 제휴 표시·첫 댓글
def disclosure_short(spec: JobSpec) -> Optional[str]:
    """화면 위쪽 작은 제휴 표시"""
    from core.affiliate_ota import OTA_DISCLOSURE
    from core.planners import COUPANG_DISCLOSURE
    from core.shorts_meta import shop_link_kind
    if spec.mode == "book":
        link = (spec.book_link or "").strip()
        if not link:
            return ""
        return ("알라딘 제휴 링크 포함 · 도서 구매 시 수수료를 받을 수 있어요" if shop_link_kind(link) == "aladin"
                else "제휴 링크 포함 · 구매 시 수수료를 받을 수 있어요")
    if spec.is_bundle:
        kinds = {shop_link_kind(it["link"]) for it in spec.bundle_items if it.get("link")}
        if kinds == {"coupang"} or not kinds:
            return COUPANG_DISCLOSURE
        return ("쿠팡 파트너스·제휴 링크 포함 · 구매 시 수수료를 받을 수 있어요" if "coupang" in kinds
                else "제휴 링크 포함 · 구매 시 수수료를 받을 수 있어요")
    if spec.mode == "commerce":
        return COUPANG_DISCLOSURE
    if spec.mode == "travel" and spec.affiliate_program:
        return OTA_DISCLOSURE
    return None


def bundle_comment_items(spec: JobSpec) -> List[Dict[str, str]]:
    from core.planners.commerce_bundle import short_name
    return [{"name": it.get("name", ""), "short": it.get("short") or short_name(it.get("name", "")), "link": it.get("link", "")}
            for it in spec.bundle_items]


def book_comment(spec: JobSpec, copy: Optional[Dict[str, Any]]) -> str:
    """시골책방 첫 댓글: 참여 질문 → 책방 네이버 검색·지도 → 「책」 알라딘 구매 링크 → 알라딘 제휴 고지 → 해시태그"""
    from core.shorts_meta import build_first_comment
    copy = copy or {}
    link = (spec.book_link or "").strip()
    return build_first_comment("book", copy.get("comment_question", ""), copy.get("hashtags"),
                               search_keyword=(spec.book_search_kw or "").strip() or spec.name.strip(),
                               items=[{"short": f"「{spec.book_title.strip()}」", "link": link}] if link else None)


def upload_texts(spec: JobSpec, copy: Optional[Dict[str, Any]], hook: str = "") -> Dict[str, str]:
    """유튜브 제목·설명·첫 댓글 (화면 업로드 패널과 같은 규칙)"""
    from core.affiliate_ota import ota_disclosure_full
    from core.planners import COUPANG_DISCLOSURE_FULL
    from core.shorts_meta import build_first_comment, build_shorts_description, make_shorts_copy
    from core.youtube_upload import TITLE_MAX
    copy = copy or make_shorts_copy(spec.mode, hook=hook, product_name=spec.name, mock=True)
    short = disclosure_short(spec) or ""
    if spec.mode == "travel" and short:
        short = "※ " + short
    desc = build_shorts_description(spec.mode, copy["title"], copy["hashtags"], short)
    if spec.mode == "book":
        comment = book_comment(spec, copy)
    else:
        full = ("" if spec.is_bundle else COUPANG_DISCLOSURE_FULL if spec.mode == "commerce"
                else ota_disclosure_full(spec.affiliate_program) if spec.mode == "travel" and spec.affiliate_program else "")
        comment = build_first_comment(spec.mode, copy.get("comment_question", ""), copy.get("hashtags"),
                                      search_keyword=spec.search_keyword or (spec.name if spec.mode == "owner" else ""),
                                      link="" if spec.mode == "owner" or spec.is_bundle else spec.link,
                                      disclosure_full=full, items=bundle_comment_items(spec) if spec.is_bundle else None)
    return {"title": copy["title"][:TITLE_MAX], "desc": desc, "comment": comment}


# ---------------------------------------------------------------- 렌더
def render(spec: JobSpec, job, scenes: List[Dict[str, Any]], progress: Optional[Callable[[int, str], None]] = None,
           force: bool = False) -> RenderResult:
    """씬 → 음성(동시) → 클립 → 썸네일·업로드 카피 → 조립(자막·제휴 표시·고정 바·라벨·BGM) → (시골책방) 첫 댓글 대기열"""
    import importlib
    import core.compositor
    from core.asset_generator import AssetGenerator
    from core.naver_local import region_of
    from core.planners.commerce_bundle import DEFAULT_BOTTOM_BAR
    from core.radar.trends import digest_for
    from core.shorts_meta import make_shorts_copy
    from core.tts import owner_readings

    def step(pct: int, text: str):
        if progress:
            progress(pct, text)

    real_media = spec.mode in ("owner", "travel", "commerce", "book")
    gen = AssetGenerator(gemini_api_key=spec.api_key, voice_actor=spec.voice_actor, audio_mode=spec.audio_mode,
                         mock=spec.mock, mode=spec.mode, cache_dir=job.assets_dir,
                         readings=owner_readings(spec.name, spec.reading) if spec.mode == "owner" else None)
    pending, images = [], []
    for idx, scene in enumerate(scenes):
        s_num = int(scene.get("scene_index") or scene.get("scene_number") or (idx + 1))
        scene["scene_index"] = scene["scene_number"] = s_num
        g_cut = scene.get("image_path") if real_media else None
        if g_cut and not os.path.exists(g_cut):
            g_cut = None
        img = gen.generate_image(scene, force=force, gallery_image_path=g_cut)
        images.append(img)
        pending.append((scene, s_num, img))

    total = len(scenes)
    step(5, f"씬 {total}개 나레이션 동시 생성 중...")
    audios = gen.generate_voices([sc for sc, _, _ in pending], force=force,
                                 on_done=lambda d, n: step(5 + int(d / n * 25), f"나레이션 {d}/{n} 완료..."))
    manifest = []
    for idx, ((scene, s_num, img), audio) in enumerate(zip(pending, audios)):
        step(30 + int(idx / total * 40), f"[{idx + 1}/{total}] 씬 {s_num} 영상 생성 중...")
        video = gen.generate_video(scene, img, force=force)
        vo = (scene.get("voiceover") or scene.get("subtitle") or scene.get("caption") or "").strip()
        manifest.append({**scene, "scene_index": s_num, "scene_number": s_num, "image_path": img, "audio_path": audio,
                         "video_path": video, "voiceover": vo, "subtitle": vo, "caption": vo})

    step(75, "타임라인 조립, 자막·오버레이 렌더링 중...")
    importlib.reload(core.compositor)
    comp = core.compositor.AdCompositor(output_dir=job.final_dir)
    book = (scenes[0].get("book") or {}) if scenes else {}
    fable = spec.mode == "book" and book.get("template") == "fable"
    thumb, copy = None, None
    try:
        from moviepy.editor import VideoFileClip
        with VideoFileClip(manifest[0]["video_path"]) as v1:
            first_frame = v1.get_frame(0)
        hook = (scenes[0].get("hook_title") or "").strip() if scenes else ""
        usp_items = [s.strip() for s in re.split(r"[,/·\n]", spec.usp or "") if s.strip()]
        bundle_title = (scenes[0].get("bundle_title") or "") if spec.is_bundle and scenes else ""
        copy = make_shorts_copy(
            spec.mode, hook=hook, usp=spec.usp, product_name=bundle_title or spec.name, api_key=spec.api_key, mock=spec.mock,
            trend=digest_for(spec.mode, spec.business_type),
            signature=(("마음을 다독이는 책" if fable else spec.book_title.strip()) if spec.mode == "book"
                       else usp_items[0] if usp_items else ""),
            hidden_terms=[spec.book_title, spec.book_author] if fable else None,
            region=region_of(spec.address) if spec.mode == "owner" else "")
        thumb = comp.render_thumbnail(first_frame, copy["thumb_main"], os.path.join(job.final_dir, "final_thumb.png"),
                                      sub_text=copy["thumb_sub"], accent=copy["accent"])
    except Exception as e:
        logger.warning(f"썸네일 생성 실패 (영상은 계속 렌더): {e}")

    final = comp.assemble_ad(
        asset_manifest=manifest, output_filename="final.mp4", fps=24, audio_mode=spec.audio_mode, mode=spec.mode,
        brand=spec.brand if spec.mode == "owner" else None,
        label=book.get("label") if spec.mode == "book" else None,
        disclosure=disclosure_short(spec) or None,
        bars=((scenes[0].get("bundle_title") or "", spec.bundle_bottom or DEFAULT_BOTTOM_BAR) if spec.is_bundle and scenes else None),
        intro_image=thumb, bgm_path=spec.bgm_path)
    bgm = {"on": bool(spec.bgm_path), "info": comp.last_bgm_info, "error": comp.last_bgm_error}
    logger.info(f"BGM 상태: {'켜짐' if spec.bgm_path else '꺼짐'} → {comp.last_bgm_info or comp.last_bgm_error or '적용 안 함'}")
    job.save_plan(scenes)
    comment = ""
    if spec.mode == "book":
        # 시골책방 채널 업로드 연결 전: 첫 댓글 텍스트를 대기열(outputs/youtube/comment_drafts.json)에 바로 저장
        from core.youtube_upload import queue_comment_draft
        comment = book_comment(spec, copy)
        queue_comment_draft("book", final, comment, job_id=job.job_id)
    step(100, "✨ 렌더링 완료!")
    return RenderResult(final_path=final, thumb_path=thumb, shorts_copy=copy, bgm=bgm, generated_images=images,
                        comment=comment)
