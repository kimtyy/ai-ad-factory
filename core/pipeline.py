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
import shutil
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
    book_publisher: str = ""
    book_genre: str = ""
    book_photo_count: int = 1
    book_template: str = "basic"
    # 1컨셉 1메시지 광고용 핵심 컨셉
    core_concept: str = ""
    tone_instruction: str = ""
    # 쇼핑: 네이버 쇼핑(쇼핑커넥트) 상품 팩 {product, pack, value, cut_roles, keyword} — 있으면 18초 5문장 문제해결형 + 쇼핑커넥트 고지
    shop_item: Dict[str, Any] = field(default_factory=dict)
    # 숙소 (렌더만)
    affiliate_program: str = ""
    # 공통
    voice_actor: str = "female_20s"
    audio_mode: str = "voice_and_sub"
    tempo: str = "rap"                           # 매장 대본 호흡: 'rap' 1컷 1카피(기본, 사진 N장 = 2초 숏카피 N줄) / 'beat' 비트 리듬형(5문장, 안주 컷 고속 전환 — 화면에서는 안 씀)
    caption_font: str = ""                       # 자막 폰트 프리셋 'impact'/'serif'/'pop' (비우면 모드 기본)
    bgm_path: Optional[str] = None
    api_key: Optional[str] = None
    mock: bool = False
    hook_hints: Any = None
    # 사장님: 매장 고유 팩트 (메뉴판·리뷰 글에서 추출한 special_menus·review_nouns, 주소 상권) + 사진별 피사체 태그 {사진 경로: '보이는 것'}
    store_facts: Dict[str, Any] = field(default_factory=dict)
    photo_tags: Dict[str, str] = field(default_factory=dict)
    photo_buckets: Dict[str, int] = field(default_factory=dict)          # 사진 5단계 시각 정렬 버킷 {경로: 1~5}
    keep_photo_order: bool = False                                       # True면 올린 순서 그대로 (AI 자동 정렬·비전 분류 호출 생략)

    @property
    def is_bundle(self) -> bool:
        return self.mode == "commerce" and self.layout == "bundle"

    @property
    def is_shop(self) -> bool:
        """네이버 쇼핑(쇼핑커넥트) 단일 상품 영상 (묶음 큐레이션은 제외)"""
        return self.mode == "commerce" and not self.is_bundle and bool(self.shop_item)


@dataclass
class RenderResult:
    final_path: str
    thumb_path: Optional[str]
    shorts_copy: Optional[Dict[str, Any]]
    bgm: Dict[str, Any]
    generated_images: List[str]
    comment: str = ""
    metadata: Optional[Dict[str, Any]] = None
    cover_path: Optional[str] = None        # 업로드·미리보기용 독립 커버 이미지 (thumbnail.jpg)


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


_EXTRA_MOTIONS = ("pan left", "zoom out", "pan right", "pan down", "zoom in")


def _extra_cut_videos(gen, scene: dict, s_num: int, real_media: bool, force: bool) -> List[str]:
    """한 문장 안에서 이어지는 추가 컷(extra_media) 클립: 대표 컷과 다른 모션으로 각각 만들어 경로 목록 반환 (없으면 빈 목록)"""
    out: List[str] = []
    for k, em in enumerate(scene.get("extra_media") or [], 1):
        ex = {key: val for key, val in scene.items() if key not in ("image_path", "media", "extra_media")}
        ex["scene_index"] = ex["scene_number"] = s_num
        ex["video_motion_prompt_en"] = _EXTRA_MOTIONS[(s_num + k) % len(_EXTRA_MOTIONS)]
        if em.get("media"):
            ex["media"] = em["media"]
        else:
            ex["image_path"] = em.get("image_path")
        suffix = f"_c{k + 1}"
        g_cut = em.get("image_path") if real_media else None
        if g_cut and not os.path.exists(g_cut):
            g_cut = None
        img = gen.generate_image(ex, force=force, gallery_image_path=g_cut, suffix=suffix)
        out.append(gen.generate_video(ex, img, force=force, suffix=suffix))
    return out


def apply_style_env(spec: "JobSpec") -> None:
    """모드 정책이 읽는 스타일 스위치를 이 작업에 맞게 맞춤 (화면·캘린더 스케줄러 공통): 네이버 쇼핑 18초 5문장 스타일"""
    if spec.mode == "commerce" and spec.is_shop:
        os.environ["COMMERCE_SHOP_STYLE"] = "1"
    else:
        os.environ.pop("COMMERCE_SHOP_STYLE", None)
    if spec.mode == "owner" and getattr(spec, "tempo", "beat") == "rap":
        os.environ["OWNER_TEMPO"] = "rap"
    else:
        os.environ.pop("OWNER_TEMPO", None)


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


def _extra_media_entry(path: str) -> Dict[str, Any]:
    """한 문장 안의 추가 컷 1개: 사진이면 image_path, 스마트폰 영상이면 media(트림 시작 기본값)"""
    from core.media import is_video_file, probe_media
    from core.media.normalize import DEFAULT_TRIM_START
    if is_video_file(path):
        probe_media(path)
        return {"source_path": path, "media": {"type": "video", "path": path, "trim_start": DEFAULT_TRIM_START, "fit": "auto"}}
    return {"source_path": path, "image_path": path}


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
    if any(s.get("media_group") for s in scenes):
        # 5문장 : 6~8컷: 씬의 첫 미디어가 대표 컷, 나머지는 같은 문장 안에서 이어지는 추가 컷 (extra_media)
        for i, scene in enumerate(scenes):
            group = [k for k in (scene.get("media_group") or [i]) if k < len(paths)]
            if not group:
                continue
            assign_media_to_scene(job, scene, paths[group[0]], "uploaded")
            scene["extra_media"] = [_extra_media_entry(paths[k]) for k in group[1:]]
        return
    for scene, p in zip(scenes, paths):
        assign_media_to_scene(job, scene, p, "uploaded")


# ---------------------------------------------------------------- 기획
def plan(spec: JobSpec, job) -> List[Dict[str, Any]]:
    """모드별 플래너 → 씬 사진·영상 배정. 입력이 모자라면 ValueError (지어내지 않음)"""
    from core.planners import plan_commerce_storyboard, plan_owner_storyboard
    from core.naver_local import region_of
    from core.planners.book import plan_book_storyboard
    from core.planners.commerce_bundle import plan_bundle_storyboard
    if spec.mode not in PIPELINE_MODES:
        raise ValueError(f"'{spec.mode}' 모드 기획은 화면에서만 할 수 있어요 (지원: {', '.join(PIPELINE_MODES)}).")
    apply_style_env(spec)
    common = dict(api_key=spec.api_key, mock=spec.mock, thumb_dir=job.assets_dir, output_plan_path=job.plan_path)
    if spec.mode == "owner":
        if not spec.name.strip():
            raise ValueError("상호명을 입력해 주세요.")
        from core.store_facts import analyze_photos, location_context
        facts = dict(spec.store_facts or {})
        if not facts.get("location_context"):
            facts["location_context"] = location_context(spec.address)
        photo_tags = dict(spec.photo_tags or {})
        photo_buckets = dict(spec.photo_buckets or {})
        if spec.keep_photo_order:
            photo_buckets = {}                                          # 순서 고정: 정렬용 비전 분류는 건너뜀 (속도) — 이미 가진 태그만 사용
        elif (not photo_tags or not photo_buckets) and spec.media_paths and not spec.mock:
            # 사진을 직접 보고 피사체·5단계 버킷을 적어 둠 (캐시: 같은 사진은 다시 부르지 않음) → 올린 순서와 상관없이 맛집 시선 순서로 컷 정렬
            new_tags, new_buckets = analyze_photos(spec.media_paths, business_name=spec.name, api_key=spec.api_key, thumb_dir=job.assets_dir,
                                                   cache_path=job.asset_path("photo_tags.json"),
                                                   official_menus=(spec.store_facts or {}).get("official_menus"))
            photo_tags = photo_tags or new_tags
            photo_buckets = photo_buckets or new_buckets
        scenes = plan_owner_storyboard(business_name=spec.name, usp=spec.usp, business_type=spec.business_type,
                                       search_keyword=spec.search_keyword or spec.name, media_paths=spec.media_paths,
                                       num_scenes=spec.num_scenes or DEFAULT_SCENES, hook_hints=spec.hook_hints,
                                       core_concept=spec.core_concept, tone_instruction=spec.tone_instruction,
                                       region=region_of(spec.address) if spec.address else "",
                                       store_facts=facts, photo_tags=photo_tags, photo_buckets=photo_buckets,
                                       keep_order=spec.keep_photo_order, tempo=spec.tempo, **common)
        if photo_tags and scenes:
            scenes[0]["photo_tags"] = {os.path.basename(k): v for k, v in photo_tags.items()}      # 화면 확인용
    elif spec.mode == "book":
        scenes = plan_book_storyboard(store_name=spec.name, book_title=spec.book_title, author=spec.book_author,
                                      book_info=spec.usp, quote=spec.book_quote, media_paths=spec.media_paths,
                                      book_photo_count=spec.book_photo_count, template=spec.book_template,
                                      core_concept=spec.core_concept, **common)
        apply_book_visuals(spec, job, scenes)
    elif spec.is_bundle:
        scenes = plan_bundle_storyboard(theme=spec.bundle_theme, items=spec.bundle_items,
                                        media_paths=spec.media_paths,
                                        core_concept=spec.core_concept, **common)
    elif spec.is_shop:
        from core.planners.shop import plan_shop_storyboard
        si = spec.shop_item
        if not spec.media_paths and not spec.mock:
            raise ValueError("상품 사진이 없습니다. 네이버 쇼핑 사진을 가져오거나 직접 올려 주세요.")
        scenes = plan_shop_storyboard(product=si.get("product") or {"name": spec.name}, pack=si.get("pack"), media_paths=spec.media_paths,
                                      media_roles=si.get("cut_roles"), usp=spec.usp, value=si.get("value"), keyword=si.get("keyword", ""),
                                      core_concept=spec.core_concept, **common)
    else:
        if not spec.name.strip():
            raise ValueError("상품명을 입력해 주세요. 링크에서 찾지 못한 정보는 지어내지 않습니다.")
        if not spec.media_paths and not spec.mock:
            raise ValueError("상품 사진이 없습니다. 판매 페이지 링크를 크롤링하거나 상품 사진을 직접 올려 주세요.")
        scenes = plan_commerce_storyboard(product_name=spec.name, usp=spec.usp, media_paths=spec.media_paths,
                                          num_scenes=spec.num_scenes or DEFAULT_SCENES, product_boxes=spec.product_boxes,
                                          core_concept=spec.core_concept, **common)
    assign_scene_media(spec, job, scenes)
    return scenes


def apply_book_visuals(spec: JobSpec, job, scenes: List[Dict[str, Any]],
                       progress: Optional[Callable[[int, int], None]] = None) -> None:
    """
    시골책방 화면 구성: 마지막(책 공개 CTA) 씬만 진짜 표지 카드 + 책 정보, 나머지 씬은 대본에 맞춰 생성한 AI 일러스트 (전체 화면)
    표지(책 사진 1번째)는 마지막 씬에만. 이미지 생성이 일부 실패하면 바로 앞 씬 그림을 다시 쓰고, 전부 실패하면 오류
    """
    from core.scene_images import generate_scene_images
    cover = spec.media_paths[0] if spec.media_paths else None
    res = generate_scene_images(scenes, os.path.join(job.assets_dir, "ai_images"),
                                book={"info": spec.usp, "genre": spec.book_genre}, api_key=spec.api_key, mock=spec.mock,
                                on_done=progress)
    if not res["images"]:
        raise RuntimeError("씬 이미지를 하나도 만들지 못했어요: " + " / ".join(res["errors"][:2]))
    last = None
    for s in scenes:
        if s.get("role") == "cta":
            continue
        k = int(s["scene_index"])
        path = res["images"].get(k) or last or next(iter(res["images"].values()))
        last = path
        s.update({"source_path": path, "layout": "full", "media_kind": "ai_image", "ai_prompt": res["prompts"].get(k, "")})
        s.pop("caption_center_y", None)
        if s.get("quote_card"):
            s["quote_cy"] = 0.52                     # 전체 화면 그림 위 가운데 종이 카드
    cta = next((s for s in scenes if s.get("role") == "cta"), None)
    if cta is not None and cover:
        from core.planners.book_fable import CTA_CAPTION_CY
        info = " · ".join(x for x in (spec.book_author, spec.book_publisher) if x)
        cta.update({"source_path": cover, "layout": "book_quote", "caption_center_y": CTA_CAPTION_CY, "media_kind": "cover",
                    "info_line": info})
    if scenes:
        scenes[0].setdefault("book", {})["visuals"] = [
            f"AI 일러스트 {len(res['images'])}장 생성 ({res['model']}, 프롬프트 {res['engine']})"
            + (f" · 실패 {len(res['errors'])}장은 앞 장면 그림 재사용" if res["errors"] else ""),
            f"화풍: {res['style'][:80]}"]


def assign_scene_media(spec: JobSpec, job, scenes: List[Dict[str, Any]]) -> None:
    """플래너가 고른 컷(source_path)이 전 씬에 있으면 그대로, 아니면 올린 순서대로 (쇼핑은 돌려 쓰며 앵글 변경)"""
    if spec.mode in ("commerce", "book") and scenes and all(s.get("source_path") for s in scenes):
        for s in scenes:
            assign_media_to_scene(job, s, s["source_path"], "matched")
    else:
        shop_auto = bool(scenes and scenes[0].get("shop_auto"))        # 네이버 쇼핑: 사진이 문장별로 이미 묶여 있어 돌려 쓰지 않음
        assign_uploads_to_scenes(job, scenes, spec.media_paths, cycle=spec.mode == "commerce" and not shop_auto, product_boxes=spec.product_boxes)


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
        kind = shop_link_kind(link)
        return ("알라딘 제휴 링크 포함 · 도서 구매 시 수수료를 받을 수 있어요" if kind == "aladin"
                else COUPANG_DISCLOSURE if kind == "coupang" else "제휴 링크 포함 · 구매 시 수수료를 받을 수 있어요")
    if spec.is_bundle:
        kinds = {shop_link_kind(it["link"]) for it in spec.bundle_items if it.get("link")}
        if kinds == {"coupang"} or not kinds:
            return COUPANG_DISCLOSURE
        return ("쿠팡 파트너스·제휴 링크 포함 · 구매 시 수수료를 받을 수 있어요" if "coupang" in kinds
                else "제휴 링크 포함 · 구매 시 수수료를 받을 수 있어요")
    if spec.mode == "commerce":
        if spec.is_shop:
            from core.naver_shop import CONNECT_DISCLOSURE_SHORT
            return CONNECT_DISCLOSURE_SHORT
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
                               address=spec.address or "",
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
    # 시골책방은 씬 1~N-1이 AI 일러스트 → 설명란에 'AI 생성 일러스트 사용' 자동 (실물 사진만 쓰는 다른 모드는 안 붙임)
    desc = build_shorts_description(spec.mode, copy["title"], copy["hashtags"], short, ai_illustration=spec.mode == "book")
    if spec.mode == "book":
        comment = book_comment(spec, copy)
    elif spec.is_shop:
        # 네이버 쇼핑커넥트: 구매 안내 + 쇼핑커넥트 링크 + 공정위 대가성 문구 + 사진 출처 문구 + 해시태그 (core/naver_shop.comment_package)
        from core.naver_shop import comment_package
        from core.photo_safety import SOURCE_NOTICE
        short = (spec.shop_item.get("pack") or {}).get("keyword") or spec.name
        comment = comment_package(short, spec.link, copy.get("hashtags"))
        comment = comment + "\n\n" + SOURCE_NOTICE
    else:
        full = ("" if spec.is_bundle else COUPANG_DISCLOSURE_FULL if spec.mode == "commerce"
                else ota_disclosure_full(spec.affiliate_program) if spec.mode == "travel" and spec.affiliate_program else "")
        comment = build_first_comment(spec.mode, copy.get("comment_question", ""), copy.get("hashtags"),
                                      search_keyword=spec.search_keyword or (spec.name if spec.mode == "owner" else ""),
                                      link="" if spec.mode == "owner" or spec.is_bundle else spec.link,
                                      disclosure_full=full, items=bundle_comment_items(spec) if spec.is_bundle else None,
                                      business_type=spec.business_type or "", usp=spec.usp or "",
                                      core_concept=getattr(spec, "core_concept", "") or "",
                                      address=spec.address if spec.mode == "owner" else "")
    ts = (copy or {}).get("tag_set") or {}
    return {"title": copy["title"][:TITLE_MAX], "desc": desc, "comment": comment,
            "tags": list(ts.get("pool") or [])}


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

    # 렌더링 전 이전 작업의 잔여 오디오 및 임시 폴더 클린업
    if hasattr(job, "clean_audio_assets"):
        job.clean_audio_assets()
    if hasattr(job, "final_dir"):
        tmp_dir = os.path.join(job.final_dir, "_ffmpeg_tmp")
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

    real_media = spec.mode in ("owner", "travel", "commerce", "book")
    apply_style_env(spec)
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
        extra_videos = _extra_cut_videos(gen, scene, s_num, real_media, force)
        import gc
        gc.collect()                                          # 씬마다 쓴 이미지·프레임 버퍼를 바로 반환 (클라우드 1GB 한도)
        vo = (scene.get("voiceover") or scene.get("subtitle") or scene.get("caption") or "").strip()
        manifest.append({**scene, "scene_index": s_num, "scene_number": s_num, "image_path": img, "audio_path": audio,
                         "video_path": video, "extra_videos": extra_videos, "voiceover": vo, "subtitle": vo, "caption": vo})

    step(75, "타임라인 조립, 자막·오버레이 렌더링 중...")
    importlib.reload(core.compositor)
    comp = core.compositor.AdCompositor(output_dir=job.final_dir)
    book = (scenes[0].get("book") or {}) if scenes else {}
    fable = spec.mode == "book" and book.get("template") in ("fable", "reflect")     # 제목 마지막 공개 → 업로드 카피에서도 숨김
    thumb, copy = None, None
    try:
        from moviepy.editor import VideoFileClip
        with VideoFileClip(manifest[0]["video_path"]) as v1:
            first_frame = v1.get_frame(0)
        hook = (scenes[0].get("hook_title") or "").strip() if scenes else ""
        usp_items = [s.strip() for s in re.split(r"[,/·\n]", spec.usp or "") if s.strip()]
        bundle_title = (scenes[0].get("bundle_title") or "") if spec.is_bundle and scenes else ""
        
        # [씬 1 중앙 뱃지 텍스트 우선순위]
        # 1순위: 선택된 1컨셉 핵심 키워드 (spec.core_concept)
        # 2순위: 등록된 정식 매장명 (spec.name, owner 모드)
        # 3순위: USP 첫 구절 (usp_items[0])
        # 4순위: 업종명 (spec.business_type)
        concept_clean = getattr(spec, "core_concept", "") or ""
        badge_sig = ""
        if concept_clean.strip():
            badge_sig = concept_clean.strip()
        elif spec.mode == "owner" and spec.name and spec.name.strip():
            badge_sig = spec.name.strip()
        elif spec.mode == "book":
            badge_sig = "마음을 다독이는 책" if fable else spec.book_title.strip()
        elif usp_items:
            badge_sig = usp_items[0]
        elif spec.business_type and spec.business_type.strip() and spec.business_type.strip() not in ("기타", "가게", "매장"):
            badge_sig = spec.business_type.strip()

        copy = make_shorts_copy(
            spec.mode, hook=hook, usp=spec.usp, product_name=bundle_title or spec.name, api_key=spec.api_key, mock=spec.mock,
            trend=digest_for(spec.mode, spec.business_type),
            signature=badge_sig,
            hidden_terms=[spec.book_title, spec.book_author] if fable else None,
            region=region_of(spec.address) if spec.mode == "owner" else "",
            core_concept=concept_clean,
            business_type=spec.business_type or "")
        thumb = comp.render_thumbnail(first_frame, copy["thumb_main"], os.path.join(job.final_dir, "final_thumb.png"),
                                      sub_text=copy["thumb_sub"], accent=copy["accent"])
    except Exception as e:
        logger.warning(f"썸네일 생성 실패 (영상은 계속 렌더): {e}")

    if spec.caption_font:
        comp.caption_font = spec.caption_font
    final = comp.assemble_ad(
        asset_manifest=manifest, output_filename="final.mp4", fps=24, audio_mode=spec.audio_mode, mode=spec.mode,
        brand=spec.brand if spec.mode == "owner" else None,
        label=book.get("label") if spec.mode == "book" else None,
        disclosure=disclosure_short(spec) or None,
        bars=((scenes[0].get("bundle_title") or "", spec.bundle_bottom or DEFAULT_BOTTOM_BAR) if spec.is_bundle and scenes else None),
        bgm_path=spec.bgm_path)   # 렌더는 커버와 무관 (0초 커버는 완성 후 후처리)
    bgm = {"on": bool(spec.bgm_path), "info": comp.last_bgm_info, "error": comp.last_bgm_error}
    logger.info(f"BGM 상태: {'켜짐' if spec.bgm_path else '꺼짐'} → {comp.last_bgm_info or comp.last_bgm_error or '적용 안 함'}")
    job.save_plan(scenes)

    # 쇼츠 최적화 SEO 메타데이터 자동 생성 및 metadata.json 저장
    seo_meta = None
    try:
        from core.seo_generator import generate_seo_metadata
        seo_meta = generate_seo_metadata(
            scenes=scenes,
            mode=spec.mode,
            product_name=(scenes[0].get("bundle_title") or spec.name) if spec.is_bundle and scenes else spec.name,
            usp=spec.usp,
            search_keyword=spec.search_keyword,
            address=spec.address,
            api_key=spec.api_key,
            mock=spec.mock,
            business_type=spec.business_type or "",
            core_concept=getattr(spec, "core_concept", "") or "",
            tag_set=(copy or {}).get("tag_set"),          # 설명란·첫 댓글·검색 태그가 같은 태그 풀을 쓴다
        )
        job.save_metadata(seo_meta)
        logger.info(f"[SEO 메타데이터 저장 완료] {job.metadata_path}")
    except Exception as me:
        logger.warning(f"SEO 메타데이터 생성 중 오류 (렌더링은 완료): {me}")

    comment = ""
    if spec.mode == "book":
        # 시골책방 채널 업로드 연결 전: 첫 댓글 텍스트를 대기열(outputs/youtube/comment_drafts.json)에 바로 저장
        from core.youtube_upload import queue_comment_draft
        comment = book_comment(spec, copy)
        queue_comment_draft("book", final, comment, job_id=job.job_id)
    step(100, "✨ 렌더링 완료!")
    cover = None
    if thumb and os.path.exists(thumb):
        try:
            from core.youtube_upload import export_cover_jpg
            cover = export_cover_jpg(thumb)
            logger.info(f"[커버 이미지] {cover} ({os.path.getsize(cover) // 1024}KB)")
            from core.render.cover_clip import apply_cover_if_enabled   # 영상 완성 후 독립 후처리: 커버 1장만 앞에 붙임
            apply_cover_if_enabled(final, cover)
        except Exception as ce:
            logger.warning(f"커버 이미지(thumbnail.jpg) 저장 실패 - 업로드 시 다시 시도: {ce}")
    return RenderResult(final_path=final, thumb_path=thumb, shorts_copy=copy, bgm=bgm, generated_images=images,
                        comment=comment, metadata=seo_meta, cover_path=cover)
