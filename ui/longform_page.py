"""
ui/longform_page.py - Streamlit '🎞️ 롱폼 하이라이트' 화면

롱폼 영상(경로 입력 또는 업로드) + 1컨셉 → 전사·시각 품질 분석(백그라운드, 진행률 표시) → 후보 최대 3개 카드
→ 하나 선택 → 쇼츠 렌더(15~22초 + CTA 3초) → 미리보기·다운로드.
실제 처리는 core/longform.py가 맡고, 이 화면은 입력·진행률·카드만 그린다.
"""
import os
import time

import streamlit as st

from core import longform as lf
from core.asset_generator import VOICE_ACTOR_PROFILES
from core.job import JobContext
from core.planners import BUSINESS_TYPES

# 화면을 옮겨 다녀도 값이 유지되어야 하는 위젯 키 (app.py에서 매 실행 재할당)
LONGFORM_PERSIST_KEYS = ("lf_source", "lf_path", "lf_store", "lf_reading", "lf_concept", "lf_usp", "lf_terms", "lf_btype",
                         "lf_model", "lf_visual", "lf_mock", "lf_actor", "lf_fit", "lf_pick")

FIT_CHOICES = {"blur": "원본 비율 유지 (블러 배경) — 가로 화면의 좌우 정보 보존", "crop": "중앙 9:16 크롭 — 화면을 꽉 채움 (좌우가 잘림)"}

MODEL_CHOICES = ["tiny", "small", "medium", "large-v3-turbo"]
TRACK_BADGE = {
    "A": ("🟢 트랙 A", "원본 음성 100% + STT 자막"),
    "C": ("🟡 트랙 C", "잡음 제거 원본 음성 + STT 자막"),
    "B": ("🔵 트랙 B", "새 컨셉 TTS + 원본 8% 이하 더킹"),
}
FLAG_KO = {"price_claim": "가격·혜택 표현", "spec_claim": "효능·인증 표현", "privacy_phone": "전화번호",
           "privacy_rrn": "주민번호 형태", "privacy_account": "계좌번호", "privacy_email": "이메일"}


def _mmss(sec: float) -> str:
    sec = max(0.0, float(sec))
    return f"{int(sec // 60):02d}:{sec % 60:04.1f}"


def _init_defaults() -> None:
    d = {"lf_source": "경로 입력", "lf_path": "", "lf_store": "", "lf_concept": "", "lf_usp": "", "lf_terms": "",
         "lf_model": (os.getenv("WHISPER_MODEL") or "small"), "lf_visual": True, "lf_mock": False,
         "lf_reading": "", "lf_fit": "blur"}
    for k, v in d.items():
        st.session_state.setdefault(k, v)
    if st.session_state.lf_model not in MODEL_CHOICES:
        MODEL_CHOICES.append(st.session_state.lf_model)
    biz = [str(k) for k in BUSINESS_TYPES if k]
    st.session_state.setdefault("lf_btype", biz[0] if biz else "")
    actors = [str(k) for k in VOICE_ACTOR_PROFILES if k]
    st.session_state.setdefault("lf_actor", actors[0] if actors else "female_20s")


def _job() -> JobContext:
    if st.session_state.get("lf_job") is None:
        st.session_state.lf_job = JobContext.create(app="longform")
    return st.session_state.lf_job


def _save_upload(up) -> str:
    """업로드 파일을 작업 폴더 uploads/에 저장 (같은 이름이면 덮어씀). 경로 반환"""
    job = _job()
    name = os.path.basename(up.name or "upload.mp4")
    dst = os.path.join(job.uploads_dir, name)
    with open(dst, "wb") as f:
        f.write(up.getbuffer())
    return dst


def _reset_results() -> None:
    for k in ("lf_result", "lf_render", "lf_analysis_id", "lf_video", "lf_error"):
        st.session_state.pop(k, None)
    st.session_state.pop("lf_pick", None)


def _input_section() -> None:
    st.markdown("### ① 영상과 1컨셉")
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        st.warning("전사 엔진(faster-whisper)이 이 PC에 설치돼 있지 않아요. `pip install faster-whisper` 후 다시 시도해 주세요. "
                   "(모델은 처음 한 번 자동으로 내려받아요.)")
    st.radio("영상 가져오기", ["경로 입력", "파일 업로드"], horizontal=True, key="lf_source",
             help="수 GB짜리 롱폼은 브라우저 업로드(최대 500MB)보다 PC 안의 경로를 직접 입력하는 쪽이 안전해요.")
    if st.session_state.lf_source == "경로 입력":
        st.text_input("영상 파일 경로", key="lf_path", placeholder=r"예: D:\촬영\2026-10-14\가게촬영.mp4")
    else:
        st.file_uploader("롱폼 영상 올리기 (mp4/mov, 최대 500MB)", type=["mp4", "mov", "m4v", "mkv", "webm"], key="lf_upload")
    c1, c2 = st.columns([1.2, 1])
    with c1:
        st.text_input("🎯 1컨셉 (영상이 증명해야 할 단 하나의 메시지)", key="lf_concept",
                      placeholder="예: 주문 즉시 무쳐내는 신선한 겉절이")
    with c2:
        st.text_input("🏢 상호명 (CTA·전사 정확도에 사용)", key="lf_store", placeholder="예: 오늘식탁 / HKG")
        st.text_input("🗣️ 상호명 읽는 법 (선택)", key="lf_reading", placeholder="예: 에이치케이지 (영문 약어는 자동으로도 풀어 읽어요)")
    c3, c4 = st.columns([1.2, 1])
    with c3:
        st.text_input("💡 특장점 (선택, 사실만)", key="lf_usp")
    with c4:
        st.text_input("🍽️ 자주 나오는 말·메뉴 (쉼표 구분, 선택)", key="lf_terms", placeholder="겉절이, 무침, 김치찌개")
    biz_keys = [str(k) for k in BUSINESS_TYPES if k]
    actor_keys = [str(k) for k in VOICE_ACTOR_PROFILES if k]
    st.radio("화면 구성 (가로 영상 → 세로 쇼츠)", list(FIT_CHOICES), key="lf_fit", horizontal=True,
             format_func=lambda k: FIT_CHOICES[k],
             help="좌우 비교·작업 장면처럼 가로 전체가 핵심이면 '원본 비율 유지'를 쓰세요. 자막은 아래쪽 블러 띠에 들어가 화면을 가리지 않아요.")
    with st.expander("고급 설정"):
        a1, a2, a3 = st.columns(3)
        with a1:
            st.selectbox("전사 모델 (클수록 정확·느림)", MODEL_CHOICES, key="lf_model")
        with a2:
            st.selectbox("업종 (트랙 B 대본용)", biz_keys, key="lf_btype",
                         format_func=lambda k: str(BUSINESS_TYPES.get(k, {}).get("label", k or "")))
        with a3:
            st.selectbox("AI 성우 (CTA·트랙 B)", actor_keys, key="lf_actor",
                         format_func=lambda k: str(VOICE_ACTOR_PROFILES.get(k, {}).get("name", k)))
        st.checkbox("화면 품질 필터 사용 (흐림·어둠·급패닝·흔들림 구간 제외)", key="lf_visual")
        st.checkbox("Mock 모드 (대본·음성을 가짜로 — 과금 없음, 테스트용)", key="lf_mock")


def _start_analysis() -> bool:
    ss = st.session_state
    path = ""
    if ss.lf_source == "파일 업로드":
        up = ss.get("lf_upload")
        if up is None:
            st.error("영상을 올려 주세요.")
            return False
        path = _save_upload(up)
    else:
        path = (ss.lf_path or "").strip().strip('"')
        err = lf.validate_video_path(path)
        if err:
            st.error(err)
            return False
    if not (ss.lf_concept or "").strip():
        st.error("1컨셉을 입력해 주세요. 컨셉이 구간을 고르는 기준이에요.")
        return False
    _reset_results()
    terms = [t.strip() for t in (ss.lf_terms or "").replace("\n", ",").split(",") if t.strip()]
    req = lf.LongformRequest(video_path=path, concept=ss.lf_concept.strip(), store_name=(ss.lf_store or "").strip(),
                             usp=(ss.lf_usp or "").strip(), terms=terms, business_type=ss.lf_btype or "",
                             model=ss.lf_model or None, use_visual=bool(ss.lf_visual),
                             use_llm=False if ss.lf_mock else None)
    job = lf.start_analysis(req, work_dir=os.path.join(_job().assets_dir, "longform"))
    ss.lf_analysis_id = job.id
    ss.lf_video = path
    return True


@st.fragment(run_every=1.0)
def _progress_fragment() -> None:
    """분석 진행률 폴링 (1초마다 이 구간만 다시 그림). 끝나면 결과를 세션에 올리고 화면 전체를 다시 그린다."""
    ss = st.session_state
    job = lf.get_job(ss.get("lf_analysis_id", ""))
    if job is None:
        return
    if not job.done:
        st.progress(job.pct / 100.0, text=f"{job.pct}% · {job.msg} · {int(job.elapsed)}초 경과")
        st.caption("긴 영상은 전사에 수십 분이 걸릴 수 있어요. 창을 닫아도 전사 결과는 저장돼 다시 시작하면 이어서 처리돼요.")
        return
    if job.error is not None:
        ss.lf_error = job.error
    else:
        ss.lf_result = job.result
    ss.pop("lf_analysis_id", None)
    st.rerun()


def _card(c, thumb: str, col) -> None:
    badge, desc = TRACK_BADGE.get(c.track, (f"트랙 {c.track}", ""))
    with col:
        with st.container(border=True):
            st.markdown(f"**후보 {c.rank}**  ·  {badge}")
            if thumb and os.path.exists(thumb):
                st.image(thumb, use_container_width=True)
            st.markdown(f"⏱ **{_mmss(c.start_sec)} ~ {_mmss(c.end_sec)}**  \n창 {c.window_sec:.1f}초 + CTA {c.cta_sec:.0f}초 = **{c.total_sec:.1f}초**")
            st.caption(desc)
            st.progress(min(1.0, c.coverage), text=f"발화 {c.coverage * 100:.0f}%")
            vis = f"{c.visual_score * 100:.0f}점" if c.visual_score is not None else "미측정"
            st.markdown(f"컨셉 적합 **{c.concept_score:.2f}** · 화면 품질 **{vis}** · 음악 의심 **{'예 ⚠️' if c.music_suspected else '아니오'}**")
            text = " ".join(s["text"] for s in c.caption_scenes)
            st.markdown(f"> {(text[:110] + '…') if len(text) > 110 else (text or '(자막 없음 - 새 TTS가 대신함)')}")
            if c.flags:
                kinds = sorted({FLAG_KO.get(f["type"], f["type"]) for f in c.flags})
                st.warning("⚠️ 확인 필요: " + ", ".join(kinds), icon="⚠️")
            if c.guard and (c.guard.get("start_delta") or c.guard.get("end_delta") or c.guard.get("video_hold")):
                parts = []
                if c.guard.get("video_hold"):
                    parts.append(f"이전 장면 {c.guard['video_hold']:.2f}초 제거 (나레이션은 그대로)")
                if c.guard.get("start_delta"):
                    parts.append(f"시작을 {c.guard['start_delta']:.2f}초 뒤로 (이전 장면 제거)")
                if c.guard.get("end_delta"):
                    parts.append(f"끝을 {abs(c.guard['end_delta']):.2f}초 앞으로 (다음 장면 제거)")
                st.caption("✂️ 장면 전환 보정: " + ", ".join(parts))
            if c.visual and c.visual.get("reasons"):
                with st.expander("화면 품질 메모"):
                    for r in c.visual["reasons"]:
                        st.write("• " + r)


def _results_section() -> None:
    ss = st.session_state
    res = ss.get("lf_result")
    if ss.get("lf_error") is not None:
        e = ss.lf_error
        name = type(e).__name__
        if name == "STTDependencyError":
            st.error(f"{e}")
        elif name == "NoAudioStreamError":
            st.error("이 영상에는 음성이 없어요. 롱폼 하이라이트는 말소리가 있는 영상이 필요해요.")
        else:
            st.error(f"분석 중 오류가 났어요: {name}: {str(e)[:300]}")
        return
    if res is None:
        return
    st.markdown("### ② 하이라이트 후보")
    tr = res.transcript
    st.caption(f"전체 {tr.duration_sec / 60:.1f}분 · 문장 {len(tr.sentences)}개 전사 · 후보 {len(res.candidates)}개")
    if not res.candidates:
        st.warning(res.message or "후보를 찾지 못했어요.")
        return
    cols = st.columns(len(res.candidates))
    for c, col in zip(res.candidates, cols):
        _card(c, res.thumbs.get(c.rank, ""), col)
    ranks = [c.rank for c in res.candidates]
    if ss.get("lf_pick") not in ranks:
        ss.lf_pick = ranks[0]
    st.radio("사용할 후보", ranks, horizontal=True, key="lf_pick", format_func=lambda r: f"후보 {r}")
    pick = next(c for c in res.candidates if c.rank == ss.lf_pick)
    if pick.track == "B":
        st.info("이 후보는 말소리가 적어 **새 컨셉 TTS 대본**으로 만들어요. 나레이션 음성 생성에 시간이 조금 더 걸려요.")
    if st.button("🎬 이 하이라이트로 쇼츠 제작", type="primary", use_container_width=True, key="lf_render_btn"):
        _render(pick)


def _render(pick) -> None:
    ss = st.session_state
    bar = st.progress(0.0, text="렌더 준비 중")

    def on_progress(pct: int, msg: str) -> None:
        bar.progress(min(1.0, pct / 100.0), text=f"{pct}% · {msg}")

    out = os.path.join(_job().final_dir, f"highlight_{pick.rank}.mp4")
    try:
        r = lf.render_candidate(ss.lf_video, pick, out, store_name=(ss.lf_store or "").strip(), concept=ss.lf_concept.strip(),
                                business_type=ss.lf_btype or "", usp=(ss.lf_usp or "").strip(), actor=ss.lf_actor,
                                fit_mode=ss.get("lf_fit", "blur"), reading=(ss.get("lf_reading") or "").strip(),
                                api_key=os.getenv("GEMINI_API_KEY", ""), mock=bool(ss.lf_mock), progress=on_progress,
                                work_dir=os.path.join(_job().assets_dir, "longform"))
        ss.lf_render = {**r, "rank": pick.rank}
    except Exception as e:
        bar.empty()
        ss.lf_render = {"error": f"{type(e).__name__}: {str(e)[:300]}"}


def _render_result_section() -> None:
    r = st.session_state.get("lf_render")
    if not r:
        return
    st.markdown("### ③ 완성본")
    if r.get("error"):
        st.error(f"렌더링에 실패했어요: {r['error']}")
        return
    path = r["path"]
    if not os.path.exists(path):
        st.warning("완성 파일을 찾을 수 없어요. 다시 제작해 주세요.")
        return
    st.success(f"후보 {r['rank']} · 트랙 {r['track']} · 길이 {r['duration']}초")
    left, right = st.columns([1, 1])
    with left:
        st.video(path)
    with right:
        cover = r.get("cover_path")
        if cover and os.path.exists(cover):
            st.image(cover, caption="커버 이미지 (thumbnail.jpg · 유튜브 업로드용)", width=180)
            with open(cover, "rb") as cf:
                st.download_button("⬇️ 커버 이미지 저장", cf.read(), file_name=os.path.basename(cover), mime="image/jpeg",
                                   use_container_width=True, key="lf_download_cover")
        for w in r.get("warnings", []):
            st.warning(w)
        with open(path, "rb") as f:
            st.download_button("⬇️ mp4 다운로드", f.read(), file_name=os.path.basename(path), mime="video/mp4",
                               use_container_width=True, key="lf_download")
        st.caption(f"저장 위치: {path}")


def render_longform_page() -> None:
    _init_defaults()
    st.markdown("## 🎞️ 롱폼 하이라이트 → 1컨셉 쇼츠")
    st.caption("긴 영상에서 '이 한 가지 메시지'를 가장 잘 보여 주는 15~22초 구간을 찾아 CTA 3초를 붙여 쇼츠로 만듭니다. "
               "말이 살아 있으면 원본 음성 그대로, 말이 적으면 새 TTS로 덮어요.")
    running = lf.get_job(st.session_state.get("lf_analysis_id", "")) is not None
    _input_section()
    if st.button("🔍 하이라이트 후보 분석 시작", type="primary", use_container_width=True, key="lf_start", disabled=running):
        if _start_analysis():
            st.rerun()
    if st.session_state.get("lf_analysis_id"):
        _progress_fragment()
    _results_section()
    _render_result_section()
