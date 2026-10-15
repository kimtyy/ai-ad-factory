"""
core/longform.py - 롱폼 하이라이트 서비스 계층 (UI 비종속)

  분석: 롱폼 영상 → 전사(캐시) → 컨셉 입증 후보 3개(시각 품질 필터 포함) → 후보별 대표 썸네일
  렌더: 선택한 후보 → CTA 음성(+트랙 B 대본/TTS) → 직가공 렌더러(window) → 최종 쇼츠

Streamlit 화면(ui/longform_page.py)은 이 모듈만 호출한다. 분석은 수십 분 걸릴 수 있어 백그라운드 스레드(AnalysisJob)로 돌리고
화면은 진행률만 폴링한다 (세션이 끊겨도 전사 캐시가 남아 이어서 처리된다).
"""
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.media import audio_stt
from core.media.audio_stt import Transcriber, Transcript
from core.planners.highlight_selector import HighlightCandidate, select_highlights

logger = logging.getLogger("ad_factory.longform")

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")
ProgressCallback = Callable[[int, str], None]

# 테스트/배포에서 전사기·음성 생성기를 바꿔 끼우는 지점 (기본값: faster-whisper, Edge-TTS)
_TRANSCRIBER_FACTORY: Optional[Callable[[], Transcriber]] = None
_VOICE_FACTORY: Optional[Callable[[str, str], Callable[[Dict[str, Any]], Optional[str]]]] = None


@dataclass
class LongformRequest:
    video_path: str
    concept: str
    store_name: str = ""
    usp: str = ""
    terms: List[str] = field(default_factory=list)       # 메뉴·고유명사 (전사 정확도용 initial_prompt)
    business_type: str = ""
    model: Optional[str] = None                           # WHISPER_MODEL 기본
    language: str = "ko"
    use_visual: bool = True                               # 시각 품질 필터 (끄면 텍스트만으로 선정)
    use_llm: Optional[bool] = None                        # None = 키가 있으면 자동


@dataclass
class LongformResult:
    transcript: Transcript
    candidates: List[HighlightCandidate]
    thumbs: Dict[int, str] = field(default_factory=dict)  # rank → 썸네일 경로
    message: str = ""                                     # 후보가 없을 때 사유


def validate_video_path(path: str) -> Optional[str]:
    """입력 경로 검사. 문제가 있으면 사용자에게 보여 줄 한국어 사유, 정상이면 None"""
    p = (path or "").strip().strip('"')
    if not p:
        return "영상 경로를 입력해 주세요."
    if not os.path.isfile(p):
        return f"파일을 찾을 수 없어요: {p}"
    if not p.lower().endswith(VIDEO_EXTS):
        return f"지원하는 영상 형식이 아니에요 ({', '.join(VIDEO_EXTS)})."
    return None


def _monotonic(cb: Optional[ProgressCallback]) -> ProgressCallback:
    state = {"pct": -1}

    def wrapped(pct: int, msg: str) -> None:
        p = max(state["pct"], max(0, min(100, int(pct))))
        state["pct"] = p
        if cb:
            try:
                cb(p, msg)
            except Exception as e:      # 화면 오류가 분석을 죽이지 않게
                logger.debug(f"진행률 콜백 오류 무시: {e}")
    return wrapped


# ---------------------------------------------------------------- 분석
def analyze_longform(req: LongformRequest, work_dir: str, progress: Optional[ProgressCallback] = None, *,
                     transcriber: Optional[Transcriber] = None, cache_dir: Optional[str] = None,
                     scorer: Optional[Callable[[float, float], Any]] = None, guard_cuts: bool = True) -> LongformResult:
    """
    전사(0~55%) → 후보 선정+시각 품질(55~95%) → 썸네일(95~100%). progress는 0→100 단조 증가.
    전사기·시각 점수기는 주입 가능 (테스트는 가짜).
    """
    from core.media.normalize import extract_thumbnail
    from core.media.visual_scorer import VisualScorer
    from core.planners.highlight_selector import VISUAL_MAX_CHECKS

    prog = _monotonic(progress)
    err = validate_video_path(req.video_path)
    if err:
        raise FileNotFoundError(err)
    if not (req.concept or "").strip():
        raise ValueError("1컨셉(영상이 증명해야 할 단 하나의 메시지)을 입력해 주세요.")
    if transcriber is None and _TRANSCRIBER_FACTORY is not None:
        transcriber = _TRANSCRIBER_FACTORY()

    prog(0, "전사 준비 중")

    def stt_progress(pct: int, msg: str) -> None:
        prog(int(pct * 0.55), msg)

    tr = audio_stt.transcribe_video(req.video_path, store_name=req.store_name, terms=req.terms, language=req.language,
                                    model=req.model, transcriber=transcriber, progress=stt_progress, cache_dir=cache_dir)
    prog(56, f"전사 완료 ({len(tr.sentences)}문장) - 후보 구간 탐색 중")

    visual = None
    if req.use_visual:
        base = scorer or VisualScorer(req.video_path)
        counter = {"n": 0}
        expected = max(4, VISUAL_MAX_CHECKS // 2)

        def visual(start: float, end: float):          # 선택기가 후보 창마다 호출 → 호출 수로 진행률 표시
            counter["n"] += 1
            prog(58 + min(36, counter["n"] * 36 // expected), f"화면 품질 분석 중 ({counter['n']}번째 구간 {start:.0f}~{end:.0f}초)")
            return base(start, end)

    cands = select_highlights(tr, req.concept, store_name=req.store_name, usp=req.usp, use_llm=req.use_llm, visual_scorer=visual)
    if guard_cuts and cands:
        from core.renderers.highlight_renderer import guard_cuts as _guard
        prog(94, "시작·끝 지점 장면 전환 확인 중")
        cands = [_guard(req.video_path, c)[0] for c in cands]
    prog(95, "대표 썸네일 추출 중")
    thumbs: Dict[int, str] = {}
    os.makedirs(work_dir, exist_ok=True)
    for c in cands:
        out = os.path.join(work_dir, f"thumb_{c.rank}.jpg")
        try:
            extract_thumbnail(req.video_path, out, at=(c.start_sec + c.end_sec) / 2.0, max_width=540)
            thumbs[c.rank] = out
        except Exception as e:
            logger.warning(f"[썸네일 실패] 후보 {c.rank}: {e}")
    msg = ""
    if not cands:
        msg = ("조건에 맞는 15~22초 구간을 찾지 못했어요. 컨셉 문구가 영상 속 말과 맞는지, 영상 길이가 충분한지, "
               "혹은 화면이 너무 어둡거나 흔들리지 않는지 확인해 주세요.")
    prog(100, f"후보 {len(cands)}개 준비 완료" if cands else "후보를 찾지 못했어요")
    return LongformResult(transcript=tr, candidates=cands, thumbs=thumbs, message=msg)


# ---------------------------------------------------------------- 렌더
def _default_voice(work_dir: str, actor: str, mock: bool,
                   readings: Optional[Dict[str, str]] = None) -> Callable[[Dict[str, Any]], Optional[str]]:
    """씬 하나의 TTS 파일을 만드는 함수 (Edge-TTS). 실패하면 None → 호출자가 경고만 띄우고 자막으로 진행"""
    def voice(scene: Dict[str, Any]) -> Optional[str]:
        from core.asset_generator import AssetGenerator
        gen = AssetGenerator(cache_dir=os.path.join(work_dir, "tts"), voice_actor=actor, mock=mock, readings=readings)
        sc = dict(scene)
        sc.setdefault("voice_actor", actor)
        try:
            os.makedirs(gen.cache_dir, exist_ok=True)
            return gen.generate_voice(sc, force=True)
        except Exception as e:
            logger.warning(f"[TTS 실패] 씬 {sc.get('scene_index')}: {type(e).__name__}: {str(e)[:120]}")
            return None
    return voice


def _cover_main_text(concept: str, limit: int = 14) -> str:
    """컨셉 문장 → 썸네일 메인 카피(공백 포함 limit자 이내, 어절 경계에서 자름)"""
    c = (concept or "").strip()
    if len(c) <= limit:
        return c
    words, out = c.split(), ""
    for w in words:
        cand = (out + " " + w).strip()
        if len(cand) > limit:
            break
        out = cand
    return out or c[:limit]


def make_cover(video_path: str, cand: HighlightCandidate, out_dir: str, *, concept: str = "", store_name: str = "") -> Optional[str]:
    """
    후보 창 중간 프레임 + 컨셉 카피로 썸네일(final_thumb.png)을 만들고, 업로드용 독립 커버 이미지(thumbnail.jpg)로 저장해 경로를 반환.
    실패해도 쇼츠 렌더 결과는 유지한다 (None).
    """
    try:
        import numpy as np
        from PIL import Image
        from core.compositor import AdCompositor
        from core.media.normalize import extract_thumbnail
        from core.shorts_meta import THUMB_MAIN_MAX, THUMB_SUB_MAX
        from core.youtube_upload import export_cover_jpg
        os.makedirs(out_dir, exist_ok=True)
        raw = os.path.join(out_dir, "_cover_frame.png")
        extract_thumbnail(video_path, raw, at=(cand.start_sec + cand.end_sec) / 2.0, max_width=1080)
        frame = np.array(Image.open(raw).convert("RGB"))
        png = os.path.join(out_dir, "final_thumb.png")
        AdCompositor(output_dir=out_dir).render_thumbnail(
            frame, _cover_main_text(concept or store_name, THUMB_MAIN_MAX), png,
            sub_text=(store_name or "")[:THUMB_SUB_MAX], accent="🔥")
        try:
            os.remove(raw)
        except OSError:
            pass
        return export_cover_jpg(png)
    except Exception as e:
        logger.warning(f"[커버 이미지 생성 실패] {type(e).__name__}: {e}")
        return None


def cta_text_for(store_name: str) -> str:
    from core.planners.owner import owner_cta
    return owner_cta((store_name or "").strip())


def render_candidate(video_path: str, cand: HighlightCandidate, out_path: str, *, store_name: str = "", concept: str = "",
                     business_type: str = "", usp: str = "", actor: str = "female_20s", api_key: str = "", mock: bool = False,
                     progress: Optional[ProgressCallback] = None, work_dir: Optional[str] = None,
                     voice_fn: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
                     fit_mode: str = "blur", reading: str = "") -> Dict[str, Any]:
    """
    선택한 후보 하나를 9:16 숏폼으로 렌더링. 반환 {path, duration, track, warnings, cta_audio}.
    fit_mode: 'blur'(기본, 원본 비율 유지 + 블러 배경) | 'crop'(중앙 9:16 크롭). reading: 상호명 읽는 법(TTS에만 적용)
    트랙 B(발화 부족)는 새 컨셉 대본+TTS가 필요하고, 음성 생성이 모두 실패하면 ValueError.
    CTA 음성이 실패하면 자막만으로 완성하고 경고를 남긴다.
    """
    from core.renderers.highlight_renderer import make_track_b_scenes, render_highlight
    prog = _monotonic(progress)
    work_dir = work_dir or os.path.dirname(os.path.abspath(out_path))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    if voice_fn is None:
        from core.tts import owner_readings
        readings = owner_readings(store_name, reading) if (store_name and reading) else None
        voice_fn = (_VOICE_FACTORY(work_dir, actor) if _VOICE_FACTORY else _default_voice(work_dir, actor, mock, readings))
    warnings: List[str] = []
    cta = cta_text_for(store_name)

    tts_scenes = None
    if cand.track == "B":
        prog(5, "트랙 B: 새 컨셉 대본 작성 중")
        counter = {"n": 0}

        def numbered(sc: Dict[str, Any]) -> Optional[str]:
            counter["n"] += 1
            scene = dict(sc)
            scene["scene_index"] = counter["n"]
            prog(10 + min(35, counter["n"] * 6), f"나레이션 음성 생성 중 ({counter['n']}번째)")
            return voice_fn(scene)

        tts_scenes = make_track_b_scenes(cand, concept, store_name=store_name, business_type=business_type, usp=usp,
                                         api_key=api_key, mock=mock, voice_fn=numbered, source_video=video_path)
        if not tts_scenes or not any(s.get("audio_path") for s in tts_scenes):
            raise ValueError("트랙 B에 필요한 나레이션 음성을 만들지 못했어요. 음성 생성(Edge-TTS) 상태를 확인하거나 다른 후보를 골라 주세요.")
    prog(50, "CTA 음성 생성 중")
    cta_audio = voice_fn({"scene_index": 99, "voiceover": cta, "caption": cta, "hook_title": ""})
    if not cta_audio:
        warnings.append("CTA 음성을 만들지 못해 자막으로만 안내해요.")
    prog(55, "커버 이미지 만드는 중")
    out_dir = os.path.dirname(os.path.abspath(out_path))
    cover = make_cover(video_path, cand, out_dir, concept=concept, store_name=store_name)
    prog(60, "영상 렌더링 중 (창 구간 단일 인코딩)")
    render_highlight(video_path, cand, out_path, cta_text=cta, cta_audio_path=cta_audio, tts_scenes=tts_scenes,
                     badge_text=(concept or store_name or "")[:24], fit_mode=fit_mode)
    from core.render.cover_clip import apply_cover_if_enabled      # 영상 완성 후 독립 후처리 (렌더·자막 로직과 분리)
    apply_cover_if_enabled(out_path, cover)
    if cand.flags:
        warnings.append(f"자막에 확인이 필요한 표현이 {len(cand.flags)}개 있어요 (가격·효능·개인정보). 올리기 전에 검토해 주세요.")
    if cand.music_suspected:
        warnings.append("배경음악이 깔린 구간으로 보여요. 원본 음악을 쓰지 않도록 원본 소리는 껐어요." if cand.track == "B"
                        else "배경음악이 있을 수 있어요. 저작권을 확인해 주세요.")
    from core.media.normalize import probe_media
    if not cover:
        warnings.append("커버 이미지(thumbnail.jpg)를 만들지 못했어요. 영상 첫 화면이 대신 쓰여요.")
    prog(100, "완성")
    return {"path": out_path, "duration": round(probe_media(out_path).duration, 2), "track": cand.track,
            "warnings": warnings, "cta_audio": bool(cta_audio), "cover_path": cover}


# ---------------------------------------------------------------- 백그라운드 분석 작업
class AnalysisJob:
    """분석을 스레드로 실행하고 진행률·결과·오류를 보관 (화면은 폴링). 진행률은 0→100 단조 증가."""

    def __init__(self, fn: Callable[[ProgressCallback], Any]):
        self.id = uuid.uuid4().hex[:12]
        self.pct = 0
        self.msg = "대기 중"
        self.done = False
        self.error: Optional[BaseException] = None
        self.result: Any = None
        self.started = time.time()
        self.history: List[int] = []
        self._lock = threading.Lock()
        self._fn = fn
        self._thread = threading.Thread(target=self._run, name=f"longform-{self.id}", daemon=True)

    def _progress(self, pct: int, msg: str) -> None:
        with self._lock:
            if pct >= self.pct:
                self.pct = int(pct)
                self.msg = msg
            self.history.append(self.pct)

    def _run(self) -> None:
        try:
            self.result = self._fn(self._progress)
        except BaseException as e:      # noqa: BLE001 - 화면에 사유를 보여 주기 위해 보관
            if isinstance(e, (audio_stt.STTError, FileNotFoundError, ValueError)):
                logger.warning(f"롱폼 분석 중단(사용자 안내 대상): {type(e).__name__}: {e}")
            else:
                logger.exception("롱폼 분석 실패")
            self.error = e
        finally:
            self.done = True

    def start(self) -> "AnalysisJob":
        self._thread.start()
        return self

    def join(self, timeout: Optional[float] = None) -> bool:
        self._thread.join(timeout)
        return self.done

    @property
    def elapsed(self) -> float:
        return time.time() - self.started


_JOBS: Dict[str, AnalysisJob] = {}
_JOBS_LOCK = threading.Lock()


def start_analysis(req: LongformRequest, work_dir: str, **kw: Any) -> AnalysisJob:
    job = AnalysisJob(lambda cb: analyze_longform(req, work_dir, cb, **kw))
    with _JOBS_LOCK:
        for old_id in [k for k, j in _JOBS.items() if j.done and time.time() - j.started > 3600][:20]:
            _JOBS.pop(old_id, None)          # 1시간 지난 끝난 작업 정리
        _JOBS[job.id] = job
    return job.start()


def get_job(job_id: str) -> Optional[AnalysisJob]:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)
