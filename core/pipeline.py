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
    mode: str                                    # owner / commerce / book / travel  (모임·행사·추억 쇼츠는 mode="owner" + memory=True: 렌더 엔진은 매장 1컷 1카피 랩형을 그대로 씀)
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
    # 🎉 모임·행사·추억 쇼츠: name = 모임·행사 이름(선택), 사진·영상 N개(최대 10) = 컷 N개 = 카피 N줄 (core/planners/memory.py)
    memory: bool = False
    memory_tone: str = "lively"                                          # lively(유쾌) / warm(따뜻) / formal(기록·기념)

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


def apply_style_env(spec: "JobSpec", count: Optional[int] = None) -> None:
    """모드 정책이 읽는 스타일 스위치를 이 작업에 맞게 맞춤 (화면·캘린더 스케줄러 공통): 네이버 쇼핑 18초 5문장 스타일"""
    if spec.mode == "commerce" and spec.is_shop:
        os.environ["COMMERCE_SHOP_STYLE"] = "1"
    else:
        os.environ.pop("COMMERCE_SHOP_STYLE", None)
    if spec.mode == "owner" and getattr(spec, "tempo", "beat") == "rap":
        os.environ["OWNER_TEMPO"] = "rap"
    else:
        os.environ.pop("OWNER_TEMPO", None)
    if spec.mode == "owner" and getattr(spec, "memory", False):
        from core.planners.memory import MAX_CUTS, cut_sec
        os.environ["MEMORY_TEMPO"] = "1"             # 🎉 모임·행사·추억: 컷당 2.5초 정책 (core/mode_policy.MEMORY_POLICY)
        n_cuts = count if count is not None else len(spec.media_paths)
        os.environ["MEMORY_CUT_SEC"] = str(cut_sec(min(n_cuts, MAX_PLAN_ITEMS)))        # 컷이 가장 많을 때만 쇼츠 59초 한도에 맞춰 조금 줄임
    else:
        os.environ.pop("MEMORY_TEMPO", None)
        os.environ.pop("MEMORY_CUT_SEC", None)


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
    if spec.mode == "owner":
        # 매장 홍보·모임·행사·추억 공통: 영상을 10초에서 자르지 않는다. 단독 영상·8초 이상 긴 영상은 3~4초 구간으로 나눠 구간마다 카피를 붙이고,
        # 전체가 59초를 넘을 때만 영상 길이를 비율대로 줄인다
        items = media_items(spec.media_paths, memory=spec.memory)
        if items["expanded"]:
            return plan_segmented(spec, job, items)
    if spec.memory:
        return plan_memory(spec, job)
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
    if spec.mode == "owner":
        fit_video_cuts(scenes)
        fit_total_budget(scenes, budget=None, cut=OWNER_PHOTO_SEC, owner=True)
    return scenes


def plan_memory(spec: JobSpec, job) -> List[Dict[str, Any]]:
    """🎉 모임·행사·추억 (짧은 영상·사진만): 올린 파일 개수 그대로 컷 N개 = 카피 N줄. 영상은 원본 길이 그대로 재생 (긴 영상은 plan_segmented)"""
    from core.planners.memory import cut_sec, plan_memory_storyboard
    media = list(spec.media_paths)[:MAX_PLAN_ITEMS]
    scenes = plan_memory_storyboard(title=spec.name, tone=spec.memory_tone, media_paths=media, api_key=spec.api_key, mock=spec.mock,
                                    thumb_dir=job.assets_dir, output_plan_path=job.plan_path, max_cuts=MAX_PLAN_ITEMS)
    for sc in scenes:
        sc["duration_sec"] = cut_sec(len(scenes))
    assign_uploads_to_scenes(job, scenes, media)
    fit_video_cuts(scenes)
    fit_total_budget(scenes)
    return scenes


# ---------------------------------------------------------------- 영상 길이 보존: 10초 상한 없음, 59초를 넘을 때만 비율대로 줄임
SEG_TARGET_SEC = 3.5              # 긴 영상을 나누는 구간 길이 (약 3~4초마다 대표 프레임 + 카피 한 줄)
LONG_CLIP_SEC = 8.0               # 여러 파일을 올렸을 때 이 길이 이상인 영상 클립은 구간으로 나눔 (그보다 짧으면 1컷 1카피)
SINGLE_MIN_SEGMENTS = 4           # 영상 1개만 올리면 짧아도 최소 4구간 (4문장)
MAX_PLAN_ITEMS = 26               # 사진 + 영상 구간을 합친 컷 수 상한 (대본 엔진 한 번 호출·59초 한도)
SEG_XFADE_PAD = 0.12              # 구간 사이 디졸브(0.12초)로 겹치는 만큼 보태 영상이 끊기지 않게 (컷 진행 시간 = 구간 길이)
OWNER_PHOTO_SEC = 2.3             # 매장 홍보 사진 컷의 진행 시간(낭독 길이에 따라 2~2.7초) — 59초 예산 계산용
TARGET_VIDEO_LAST_SEC = 58.7      # 마지막이 영상 구간이면 끝 여운이 따로 안 붙어(구간 길이로 끝남) 렌더 길이 ≈ 컷 합 + 커버 0.5초라 한도를 조금 더 씀
OWNER_MARGIN_SEC = 0.35           # 매장 홍보는 사진 컷 낭독 길이가 2~2.7초로 들쭉날쭉 → 그만큼 더 보수적으로


LAST_SCENE_TAIL_SEC = 3.45        # 마지막 컷은 낭독(최대 약 2.45초, 무음이면 컷 길이) + 여운 1.0초. 마지막 영상 구간이 이보다 짧으면 그만큼 렌더가 길어짐


def duration_budget(last_is_video: bool, owner: bool = False, count: int = 0, has_photo: bool = True, last_len: Optional[float] = None) -> float:
    """컷 진행 시간 합이 넘으면 안 되는 한도(초): 쇼츠·릴스 59초 − 맨 앞 커버 0.5초 − 마지막 컷 여운 (마지막이 충분히 긴 영상 구간이면 여운이 구간 안에 들어가 더 쓸 수 있음)"""
    from core.planners.memory import advance_budget
    if last_is_video:
        extra = 0.45 if last_len is None else max(0.0, LAST_SCENE_TAIL_SEC - last_len)
        base = TARGET_VIDEO_LAST_SEC - 0.5 - extra
    else:
        base = advance_budget(count)
    return base - (OWNER_MARGIN_SEC if (owner and has_photo) else 0.0)           # 사진 컷이 없으면 낭독 길이 변동이 렌더 길이에 영향을 주지 않음
MIN_CLIP_SEC = 2.0                # 줄이더라도 영상 하나가 이보다 짧아지지 않게


def single_video_sentences(length_sec: float) -> int:
    """영상 1개의 실제 길이 → 구간(문장) 수: 약 3.5초마다 한 구간, 짧아도 최소 4 (예: 15초 4 · 25초 7 · 35초 10)"""
    return max(SINGLE_MIN_SEGMENTS, round(length_sec / SEG_TARGET_SEC))


_LEN_CACHE: Dict[Any, float] = {}


def _video_len(path: str) -> float:
    """영상 길이(초). 화면이 다시 그려질 때마다 ffprobe를 부르지 않게 (경로·수정시각·크기) 기준으로 기억"""
    from core.media import probe_media
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        key = (path, 0, 0)
    if key not in _LEN_CACHE:
        try:
            _LEN_CACHE[key] = float(probe_media(path).duration)
        except (ValueError, OSError):
            return 3.0
    return _LEN_CACHE[key]


def media_items(paths: List[str], memory: bool = False, lengths: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """
    올린 파일 → 컷 목록 (화면 안내·기획 공통). 영상은 10초에서 자르지 않는다.
    ① 전체 길이(사진 컷 + 영상 원본 길이)가 59초 한도 안이면 어떤 영상도 줄이지 않는다
    ② 한도를 넘으면 영상들의 길이를 같은 비율로 줄인다 (사진·한도 밖은 그대로, 영상 하나는 MIN_CLIP_SEC 이상)
    ③ 영상 1개만 올렸거나 8초 이상인 긴 영상은 약 3.5초 구간으로 나눈다 (구간마다 대표 프레임·카피 한 줄). 컷이 MAX_PLAN_ITEMS를 넘으면 구간을 더 길게
    반환: {"items": [{src, kind, trim, len, file_index}], "cuts", "seconds", "expanded", "trimmed", "videos"}
    """
    from core.media import is_video_file
    paths = list(paths or [])
    last_video = bool(paths) and is_video_file(paths[-1])
    if memory:
        from core.planners.memory import cut_sec
        photo_sec = cut_sec(len(paths))
    else:
        photo_sec = OWNER_PHOTO_SEC
    budget = duration_budget(last_video, owner=not memory, count=len(paths), has_photo=any(not is_video_file(p) for p in paths), last_len=None if not last_video else LAST_SCENE_TAIL_SEC)
    lens0 = {p: float((lengths or {}).get(p) or _video_len(p)) for p in paths if is_video_file(p)}
    n_photo = sum(1 for p in paths if p not in lens0)
    has_photo = n_photo > 0

    def build(budget: float) -> Dict[str, Any]:
        lens, trimmed = dict(lens0), False
        vid_total = sum(lens.values())
        if vid_total > 0 and n_photo * photo_sec + vid_total > budget:
            f = max(0.0, budget - n_photo * photo_sec) / vid_total
            lens = {p: max(MIN_CLIP_SEC, L * f) for p, L in lens.items()}
            trimmed = True
        single = len(paths) == 1 and bool(lens)

        def split(seg_sec: float):
            out = []
            for i, p in enumerate(paths):
                if p not in lens:
                    out.append({"src": p, "kind": "photo", "trim": 0.0, "len": None, "file_index": i})
                    continue
                L = lens[p]
                if single:
                    n = single_video_sentences(L) if seg_sec == SEG_TARGET_SEC else max(1, round(L / seg_sec))
                elif L >= LONG_CLIP_SEC:
                    n = max(2, round(L / seg_sec))
                else:
                    n = 1
                n = max(1, min(n, int(L // 1.0) or 1))           # 구간이 1초보다 짧아지지 않게
                for k in range(n):
                    out.append({"src": p, "kind": "video", "trim": round(L / n * k, 3), "len": round(L / n, 3), "file_index": i})
            return out
        seg = SEG_TARGET_SEC
        items = split(seg)
        while len(items) > MAX_PLAN_ITEMS and seg < 12:
            seg += 0.5
            items = split(seg)
        seconds = sum(photo_sec if it["kind"] == "photo" else it["len"] for it in items)
        expanded = single or any(it["kind"] == "video" and sum(1 for x in items if x["file_index"] == it["file_index"]) > 1 for it in items)
        return {"items": items, "cuts": len(items), "seconds": round(seconds, 2), "expanded": bool(expanded), "trimmed": trimmed,
                "videos": len(lens), "photo_sec": photo_sec, "lengths": lens}

    out = build(budget)
    if last_video and out["items"]:
        # 마지막 컷이 영상 구간이면, 구간이 짧을수록 낭독 여운이 구간 밖으로 나와 렌더가 길어짐 → 실제 구간 길이로 한도를 다시 계산해 한 번 더 맞춤
        tighter = duration_budget(True, owner=not memory, count=len(paths), has_photo=has_photo, last_len=out["items"][-1]["len"])
        if tighter < budget - 0.01:
            out = build(tighter)
    return out


def plan_segmented(spec: JobSpec, job, plan_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    영상을 구간으로 나눠 쓰는 기획 (매장 홍보·모임·행사·추억 공통): 구간마다 대표 프레임을 뽑아 사진처럼 판독·1컷 1카피 대본을 만들고,
    각 문장은 영상의 해당 구간을 이어서 재생한다. 사진은 올린 순서 그대로 컷 1개 (구간 순서가 뒤섞이지 않게 자동 정렬 끔)
    """
    import dataclasses
    from core.media import extract_thumbnail
    items = plan_info["items"]
    apply_style_env(spec, len(items))                       # 모임·행사·추억 컷 시간은 파일 수가 아니라 구간까지 센 컷 수 기준
    frames = []
    for i, it in enumerate(items):
        if it["kind"] == "photo":
            frames.append(it["src"])
            continue
        fp = job.asset_path(f"vframe_{i + 1:02d}.jpg")
        total = plan_info["lengths"].get(it["src"]) or it["len"]
        extract_thumbnail(it["src"], fp, at=min(it["trim"] + it["len"] / 2, max(total - 0.2, 0.0)))
        frames.append(fp)
    vspec = dataclasses.replace(spec, media_paths=frames, keep_photo_order=True, tempo="rap")
    scenes = plan_memory(vspec, job) if spec.memory else plan(vspec, job)
    last = len(scenes) - 1
    for i, (sc, it) in enumerate(zip(scenes, items)):
        if it["kind"] != "video":
            continue
        assign_media_to_scene(job, sc, it["src"], "uploaded")
        sc["media"]["trim_start"] = it["trim"]
        sc["source_path"] = frames[i]                       # 카드 미리보기·사진 설명은 그 구간의 대표 프레임
        sc["duration_sec"] = round(it["len"], 2)
        sc["min_sec"] = round(it["len"] + (0.0 if i == last else SEG_XFADE_PAD), 2)
    if scenes:
        vids = [p for p in dict.fromkeys(it["src"] for it in items if it["kind"] == "video")]
        if len(items) and len(vids) == 1 and all(it["kind"] == "video" for it in items):
            scenes[0]["single_video"] = {"path": vids[0], "length_sec": round(plan_info["lengths"][vids[0]], 2), "segments": len(items)}
        scenes[0]["media_segments"] = {"cuts": len(items), "seconds": plan_info["seconds"], "trimmed": plan_info["trimmed"]}
    return scenes


def fit_video_cuts(scenes: List[Dict[str, Any]]) -> None:
    """사진 + 영상 혼합(구간으로 나누지 않는 짧은 영상): 영상 컷은 그 영상의 원본 길이 전체(10초 상한 없음)를 처음부터 재생, 사진 컷은 기존대로 약 2초 줌인"""
    from core.media import probe_media
    for sc in scenes:
        m = sc.get("media")
        if not (isinstance(m, dict) and m.get("type") == "video" and m.get("path")):
            continue
        try:
            length = float(probe_media(m["path"]).duration)
        except (ValueError, OSError):
            continue
        use = max(1.0, length)
        m["trim_start"] = 0.0
        sc["min_sec"] = round(use, 2)
        sc["duration_sec"] = round(use, 2)
    return None


FINAL_TIMELINE_MAX_SEC = 58.0     # 타임라인 길이 한도 = 쇼츠·릴스 59초 − 맨 앞 커버 0.5초 − 인코딩 오차 여유


def fit_after_voice(spec: JobSpec, scenes: List[Dict[str, Any]], audio_paths: List[str], limit: float = FINAL_TIMELINE_MAX_SEC) -> float:
    """
    음성이 만들어진 뒤 실제 낭독 길이로 렌더 타임라인(조립과 같은 규칙)을 계산해, 한도를 넘으면 '영상 구간'의 길이(min_sec)만 같은 비율로 줄인다.
    기획 단계의 추정(사진 컷 2.3초 등)은 낭독이 길면 어긋나므로 마지막에 한 번 더 맞춘다. 사진 컷·낭독 길이가 정한 하한 밑으로는 줄이지 않는다.
    최종 타임라인 길이(초)를 돌려준다
    """
    from core.compositor import _xfade_for
    from core.mode_policy import get_mode_policy
    from core.render.ffmpeg_assemble import probe
    pol = get_mode_policy(spec.mode)
    xf = _xfade_for(spec.mode)
    last = len(scenes) - 1
    floors, cur = [], []
    for i, (sc, ap) in enumerate(zip(scenes, audio_paths)):
        a = float(probe(ap)["duration"] or 0.0)
        floor = max(pol.min_scene_sec, a + (1.0 if i == last else pol.tail_sec + xf) + float(sc.get("hold_sec") or 0))
        floors.append(floor)
        cur.append(max(floor, float(sc.get("min_sec") or 0)))

    def timeline(durs):
        return sum(d - (xf if i < last else 0.0) for i, d in enumerate(durs))
    total = timeline(cur)
    if total <= limit:
        return round(total, 2)
    spare = [(i, cur[i] - floors[i]) for i, sc in enumerate(scenes) if (sc.get("media") or {}).get("type") == "video" and cur[i] - floors[i] > 0.01]
    room = sum(e for _, e in spare)
    if room <= 0:
        return round(total, 2)
    keep = max(0.0, 1.0 - (total - limit) / room)
    for i, e in spare:
        new = round(floors[i] + e * keep, 2)
        scenes[i]["min_sec"] = new
        scenes[i]["duration_sec"] = min(float(scenes[i].get("duration_sec") or new), new)
        cur[i] = new
    logger.info(f"[59초 맞춤] 낭독이 길어 타임라인 {total:.1f}초 → 영상 구간 {room:.1f}초분을 {1 - keep:.0%} 줄임")
    return round(timeline(cur), 2)


def fit_total_budget(scenes: List[Dict[str, Any]], budget: Optional[float] = None, cut: Optional[float] = None, owner: bool = False) -> float:
    """
    러닝타임 한도(쇼츠·릴스 59초): 사진 컷은 컷당 일정한 시간(추억 2.5초 · 매장 2.3초), 영상 컷은 원본 길이. 컷 진행 시간(영상은 길이 − 전환 겹침)의
    합이 한도를 넘으면 영상 컷의 '사진 컷 초과분'만 같은 비율로 줄인다 (사진·짧은 영상은 그대로). 한도 안이면 아무것도 줄이지 않는다.
    budget/cut을 생략하면 모임·행사·추억(core.planners.memory) 값. 줄인 뒤 컷 진행 시간의 합계를 돌려준다
    """
    from core.planners.memory import XFADE_OVERLAP_SEC as XF, cut_sec
    n = len(scenes)
    cut = cut_sec(n) if cut is None else cut
    if budget is None:
        last = scenes[-1] if scenes else {}
        budget = duration_budget(bool(scenes) and (last.get("media") or {}).get("type") == "video", owner=owner, count=n,
                                 has_photo=any((sc.get("media") or {}).get("type") != "video" for sc in scenes),
                                 last_len=float(last.get("min_sec") or last.get("duration_sec") or 0) or None)

    def adv(sc):
        if (sc.get("media") or {}).get("type") == "video":
            return max(cut, float(sc.get("min_sec") or 0) - XF, float(sc.get("duration_sec") or 0) - XF)
        return cut
    total = sum(adv(sc) for sc in scenes)
    extras = [(sc, adv(sc) - cut) for sc in scenes if adv(sc) > cut]
    spare = sum(e for _, e in extras)
    if total > budget and spare > 0:
        keep = max(0.0, 1.0 - (total - budget) / spare)
        for sc, e in extras:
            sc["min_sec"] = sc["duration_sec"] = round(cut + e * keep + XF, 2)
        total = sum(adv(sc) for sc in scenes)
    return round(total, 2)


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


def memory_upload_texts(spec: JobSpec, scenes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
    """🎉 모임·행사·추억 유튜브 제목·설명·카카오톡 단톡방 멘트 (위치·검색 유도·판촉 문구 없음)"""
    from core.memory_pack import kakao_message, memory_copy, seo_metadata
    from core.youtube_upload import TITLE_MAX
    c = memory_copy(spec.name, spec.memory_tone, scenes)
    meta = seo_metadata(spec.name, spec.memory_tone, scenes)
    return {"title": c["title"][:TITLE_MAX], "desc": meta["description"], "comment": kakao_message(spec.name), "tags": list(c["hashtags"])}


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
    apply_style_env(spec, len(scenes))
    gen = AssetGenerator(gemini_api_key=spec.api_key, voice_actor=spec.voice_actor, audio_mode=spec.audio_mode,
                         mock=spec.mock, mode=spec.mode, cache_dir=job.assets_dir,
                         readings=owner_readings(spec.name, spec.reading) if spec.mode == "owner" and not spec.memory else None)
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
    if spec.mode == "owner":
        fit_after_voice(spec, [sc for sc, _, _ in pending], audios)         # 실제 낭독 길이로 합계가 59초를 넘으면 영상 구간만 줄임
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

        if spec.memory:
            from core.memory_pack import memory_copy
            copy = memory_copy(spec.name, spec.memory_tone, scenes)          # 매장용 가게추천 카피 대신 추억 영상 전용 카피
        else:
            copy = make_shorts_copy(
                spec.mode, hook=hook, usp=spec.usp, product_name=bundle_title or spec.name, api_key=spec.api_key, mock=spec.mock,
                trend=digest_for(spec.mode, spec.business_type),
                signature=badge_sig,
                hidden_terms=[spec.book_title, spec.book_author] if fable else None,
                region=region_of(spec.address) if spec.mode == "owner" else "",
                core_concept=concept_clean,
                business_type=spec.business_type or "")
        if spec.memory:
            # 🎉 추억: 광고용 큰 글씨 대신 기념 사진 느낌의 감성 썸네일 (감성 명조/둥근 팝체, 판촉 문구 없음)
            from core.render.thumbnail import render_memory_thumbnail
            thumb = render_memory_thumbnail(first_frame, copy["thumb_main"], spec.memory_tone, os.path.join(job.final_dir, "final_thumb.png"),
                                            comp, sub_text=copy["thumb_sub"])
        else:
            thumb = comp.render_thumbnail(first_frame, copy["thumb_main"], os.path.join(job.final_dir, "final_thumb.png"),
                                          sub_text=copy["thumb_sub"], accent=copy["accent"])
    except Exception as e:
        logger.warning(f"썸네일 생성 실패 (영상은 계속 렌더): {e}")

    if spec.caption_font:
        comp.caption_font = spec.caption_font
    final = comp.assemble_ad(
        asset_manifest=manifest, output_filename="final.mp4", fps=24, audio_mode=spec.audio_mode, mode=spec.mode,
        brand=spec.brand if spec.mode == "owner" and not spec.memory else None,
        label=book.get("label") if spec.mode == "book" else None,
        disclosure=disclosure_short(spec) or None,
        bars=((scenes[0].get("bundle_title") or "", spec.bundle_bottom or DEFAULT_BOTTOM_BAR) if spec.is_bundle and scenes else None),
        bgm_path=spec.bgm_path)   # 렌더는 커버와 무관 (0초 커버는 완성 후 후처리)
    bgm = {"on": bool(spec.bgm_path), "info": comp.last_bgm_info, "error": comp.last_bgm_error}
    logger.info(f"BGM 상태: {'켜짐' if spec.bgm_path else '꺼짐'} → {comp.last_bgm_info or comp.last_bgm_error or '적용 안 함'}")
    job.save_plan(scenes)

    # 쇼츠 최적화 SEO 메타데이터 자동 생성 및 metadata.json 저장 (추억 쇼츠는 매장용 생성기 대신 전용 메타데이터)
    seo_meta = None
    try:
        if spec.memory:
            from core.memory_pack import seo_metadata as _memory_seo
            seo_meta = _memory_seo(spec.name, spec.memory_tone, scenes)
        else:
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
