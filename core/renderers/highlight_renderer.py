"""
core/renderers/highlight_renderer.py - 롱폼 하이라이트 후보(HighlightCandidate) → 직가공 렌더러 연결 (Step 3)

selector가 고른 창(start_sec~end_sec)과 트랙(A/B/C)을 render_video_overlay(window=...)에 그대로 넘긴다.
- 트랙 A (발화 ≥60%): 원본 음성 100% + STT 자막. TTS는 CTA 한 문장뿐
- 트랙 C (30~60%):   원본 음성 afftdn 노이즈 제거 후 100% + STT 자막
- 트랙 B (<30%):     새 컨셉 TTS(씬별) + 원본 ≤8% 더킹 (음악 의심이면 원본 0%)
중간 컷 파일은 만들지 않는다 (렌더러가 입력 측 -ss/-t 로 한 번에 인코딩).
"""
import dataclasses
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.planners.highlight_selector import HighlightCandidate
from core.renderers.video_overlay_renderer import render_video_overlay

logger = logging.getLogger("ad_factory.render.highlight")

TRACK_B_DUCK = 0.08            # 트랙 B 원본 음량 상한 (8%)
TRACK_AUDIO_MODE = {"A": "original", "C": "denoise", "B": "keep_ducking"}


def audio_mode_for_track(track: str) -> str:
    """트랙 → 렌더러 audio_mode (A: 원본 100% / C: 노이즈 제거 / B: 더킹)"""
    try:
        return TRACK_AUDIO_MODE[track]
    except KeyError:
        raise ValueError(f"알 수 없는 트랙: {track!r} (A/B/C 중 하나여야 합니다)")


def build_highlight_storyboard(cand: HighlightCandidate, *, cta_text: str, cta_audio_path: Optional[str] = None,
                               tts_scenes: Optional[Sequence[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    후보 → 렌더러 storyboard (시간은 모두 창 시작점 기준 상대 초).
    A/C: STT caption_scenes를 그대로 자막으로 (TTS 없음). B: tts_scenes([{text,start,end,audio_path}])가 필요.
    마지막에 CTA 씬(창 끝 ~ 창 끝 + cta_sec)이 붙는다.
    """
    win = cand.window_sec
    scenes: List[Dict[str, Any]] = []
    if cand.track == "B":
        if not tts_scenes:
            raise ValueError("트랙 B(발화 부족)는 새 컨셉 TTS 대사(tts_scenes)가 필요합니다")
        for sc in tts_scenes:
            s = max(0.0, float(sc.get("start", sc.get("start_sec", 0.0))))
            e = min(win, float(sc.get("end", sc.get("end_sec", s + 3.0))))
            if e - s < 0.3:
                continue
            text = str(sc.get("text") or sc.get("caption") or sc.get("voiceover") or "").strip()
            scenes.append({"start_sec": s, "end_sec": e, "caption": text, "voiceover": text,
                           "audio_path": sc.get("audio_path"), "hook_title": str(sc.get("hook_title") or "")})
    else:
        for sc in cand.caption_scenes:
            s, e = max(0.0, float(sc["start"])), min(win, float(sc["end"]))
            if e - s < 0.2:
                continue
            scenes.append({"start_sec": s, "end_sec": e, "caption": sc["text"], "voiceover": sc["text"], "audio_path": None})
    scenes.append({"start_sec": win, "end_sec": win + cand.cta_sec, "caption": cta_text, "voiceover": cta_text,
                   "audio_path": cta_audio_path, "role": "cta"})
    return scenes


CUT_GUARD_FRAME = 0.04          # 컷 직후 첫 프레임을 확실히 넘기는 여유 (30fps 1프레임 ≈ 0.033초)
PRE_SEARCH = 0.15               # 시작점 앞쪽도 확인 (탐색 오차·VAD 스냅으로 컷 바로 앞에서 시작한 경우)
MIN_WINDOW_AFTER_GUARD = 14.0   # 보정 후 창이 이보다 짧아지면 보정을 포기 (잔상보다 분량이 우선인 경우)
POST_ONSET_CUT = 0.35           # 첫 단어 시작 후 이 시간 안의 컷은 '인트로 컷'으로 보고 영상만 컷 직후부터 (나레이션은 유지)
MAX_VIDEO_HOLD = 0.6            # 첫 프레임을 붙잡아 두는 최대 길이 (이보다 길면 정지 화면이 눈에 띄므로 포기)
PRE_ONSET_LEAD = 0.12           # 인트로 컷 보정 시 첫 단어 앞 여유 (오디오 페이드 100ms)


def guard_cuts(video_path: str, cand: HighlightCandidate,
               detector: Optional[Callable[[str, float, float], List[float]]] = None) -> Tuple[HighlightCandidate, Dict[str, Any]]:
    """
    시작·끝 지점 가드: 창 안 '첫 발화 이전'에 장면 컷이 있으면 시작점을 컷 직후로, '마지막 발화 이후'에 컷이 있으면
    끝점을 컷 직전으로 옮겨 이전/다음 장면의 잔상이 1프레임도 섞이지 않게 한다. 컷이 발화 도중이면 건드리지 않는다.
    자막 장면 시간(창 기준 상대)과 오디오 페이드 길이도 함께 맞춘다. 반환: (보정된 후보, 기록 dict)
    """
    if detector is None:
        from core.media.visual_scorer import find_cuts
        detector = find_cuts
    info: Dict[str, Any] = {"in_cut": None, "out_cut": None, "start_delta": 0.0, "end_delta": 0.0, "skipped": ""}
    if not cand.caption_scenes:
        info["skipped"] = "자막 장면 없음"
        return cand, info
    first_speech = cand.start_sec + float(cand.caption_scenes[0]["start"])
    if cand.first_word_sec is not None:                # 문장(세그먼트) 시작보다 단어 시작이 실제 목소리에 가깝다
        first_speech = max(first_speech, min(float(cand.first_word_sec), first_speech + 0.6))
    last_speech = cand.start_sec + float(cand.caption_scenes[-1]["end"])
    new_start, new_end = cand.start_sec, cand.end_sec
    hold = 0.0
    try:
        pre = detector(video_path, max(0.0, cand.start_sec - PRE_SEARCH), first_speech + POST_ONSET_CUT + 0.1)
        post = detector(video_path, max(cand.start_sec, last_speech - 0.1), cand.end_sec + 0.1)
    except Exception as e:                       # 컷 검출 실패는 렌더를 막지 않음 (보정만 건너뜀)
        logger.warning(f"[컷 가드 건너뜀] {type(e).__name__}: {e}")
        info["skipped"] = f"컷 검출 실패: {type(e).__name__}"
        return cand, info
    before = [c for c in pre if c <= first_speech - 0.02]
    intro = [c for c in pre if first_speech - 0.02 < c <= first_speech + POST_ONSET_CUT]
    if intro:
        # 목소리가 먼저 시작되고 컷이 곧바로 뒤따르는 편집(J-cut): 오디오는 두고 영상만 컷 직후 프레임부터 쓴다
        c = max(intro)
        video_in = c + CUT_GUARD_FRAME
        s = max(cand.start_sec, first_speech - PRE_ONSET_LEAD)
        if video_in - s <= MAX_VIDEO_HOLD:
            new_start, hold, info["in_cut"] = s, max(0.0, video_in - s), c
    elif before:
        c = max(before)
        cand_start = c + CUT_GUARD_FRAME
        if cand_start > new_start and cand_start < first_speech:
            new_start, info["in_cut"] = cand_start, c
    outs = [c for c in post if c >= last_speech]
    if outs:
        c = min(outs)
        cand_end = c - CUT_GUARD_FRAME
        if cand_end < new_end and cand_end > last_speech:
            new_end, info["out_cut"] = cand_end, c
    if new_end - new_start < MIN_WINDOW_AFTER_GUARD:
        info["skipped"] = f"보정 시 창이 {new_end - new_start:.1f}초로 너무 짧아 유지"
        info["in_cut"] = info["out_cut"] = None
        return cand, info
    if new_start == cand.start_sec and new_end == cand.end_sec:
        return cand, info
    d = new_start - cand.start_sec
    length = new_end - new_start
    scenes = []
    for sc in cand.caption_scenes:
        a, b = max(0.0, float(sc["start"]) - d), min(length, float(sc["end"]) - d)
        if b - a >= 0.2:
            scenes.append({**sc, "start": round(a, 3), "end": round(b, 3)})
    lead = first_speech - new_start
    fixed = dataclasses.replace(
        cand, start_sec=round(new_start, 3), end_sec=round(new_end, 3), caption_scenes=scenes,
        fade_in_sec=round(max(0.02, min(cand.fade_in_sec, lead)), 3), video_hold_sec=round(hold, 3),
        guard={"in_cut": info["in_cut"], "out_cut": info["out_cut"], "start_delta": round(d, 3),
               "end_delta": round(new_end - cand.end_sec, 3), "video_hold": round(hold, 3)})
    info.update(start_delta=round(d, 3), end_delta=round(new_end - cand.end_sec, 3), video_hold=round(hold, 3))
    logger.info(f"[컷 가드] 시작 {cand.start_sec:.2f}->{fixed.start_sec:.2f}s, 끝 {cand.end_sec:.2f}->{fixed.end_sec:.2f}s "
                f"(in_cut={info['in_cut']}, out_cut={info['out_cut']})")
    return fixed, info


def render_highlight(video_path: str, cand: HighlightCandidate, output_path: str, *, cta_text: str,
                     cta_audio_path: Optional[str] = None, tts_scenes: Optional[Sequence[Dict[str, Any]]] = None,
                     badge_text: str = "", fit_mode: str = "blur", guard: bool = True, **render_kw: Any) -> str:
    """
    HighlightCandidate 하나를 9:16 숏폼으로 렌더링 (트랙별 오디오 믹싱·더킹 포함).
    fit_mode: 'blur'(기본) = 원본 비율 유지 + 블러 배경 (가로 영상의 좌우 핵심 정보 보존), 'crop' = 중앙 9:16 크롭
    guard: 시작/끝 지점의 이전·다음 장면 잔상 제거 (컷 검출 → 인점/아웃점 보정)
    """
    if guard:
        cand, _info = guard_cuts(video_path, cand)
    storyboard = build_highlight_storyboard(cand, cta_text=cta_text, cta_audio_path=cta_audio_path, tts_scenes=tts_scenes)
    kw: Dict[str, Any] = dict(render_kw)
    kw.setdefault("duck_volume", TRACK_B_DUCK)
    logger.info(f"[하이라이트 렌더] 트랙 {cand.track}, 창 {cand.start_sec:.1f}~{cand.end_sec:.1f}s + CTA {cand.cta_sec:.1f}s, "
                f"커버리지 {cand.coverage:.0%}, music_suspected={cand.music_suspected}")
    return render_video_overlay(
        video_path, storyboard, output_path, audio_mode=audio_mode_for_track(cand.track), fit_mode=fit_mode,
        badge_text=badge_text, window=(cand.start_sec, cand.end_sec), cta_sec=cand.cta_sec,
        original_fade=cand.fade_in_sec, music_suspected=cand.music_suspected,
        video_hold=float(getattr(cand, "video_hold_sec", 0.0) or 0.0), **kw)


def make_track_b_scenes(cand: HighlightCandidate, core_concept: str, *, store_name: str = "", business_type: str = "",
                        usp: str = "", api_key: str = "", mock: bool = False,
                        voice_fn: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
                        source_video: str = "") -> List[Dict[str, Any]]:
    """
    트랙 B용 새 컨셉 대본(+TTS). 대본은 창 길이 기준으로 직가공 대본 엔진(plan_video_overlay_storyboard)이 쓰고,
    voice_fn(scene) → 오디오 파일 경로 가 씬별 TTS를 만든다 (테스트에서는 가짜 주입).
    반환: [{text,start,end,audio_path,hook_title}] — CTA 씬은 제외 (build_highlight_storyboard가 따로 붙임)
    """
    from core.analyzer import plan_video_overlay_storyboard
    win = cand.window_sec
    plan = plan_video_overlay_storyboard(source_video or "", core_concept=core_concept, store_name=store_name,
                                         business_type=business_type, usp=usp, api_key=api_key, mock=mock,
                                         total_sec=win + cand.cta_sec)
    body = [s for s in plan if s.get("role") != "cta"]
    out: List[Dict[str, Any]] = []
    for sc in body:
        s = float(sc.get("start_sec", 0.0))
        e = min(float(sc.get("end_sec", s + 3.0)), win)       # CTA 씬이 창 끝에서 시작하도록 본문은 창 안으로
        if e - s < 0.3:
            continue
        text = str(sc.get("voiceover") or sc.get("caption") or "").strip()
        audio = voice_fn(sc) if voice_fn else None
        out.append({"text": text, "start": s, "end": e, "audio_path": audio, "hook_title": sc.get("hook_title", "")})
    return out
