"""
ui/radar_page.py - Streamlit '🔥 실시간 떡상 레이더' 화면

- 레이더 실행(수집) 버튼: YOUTUBE_API_KEY가 없으면 dry-run(가짜 데이터, 별도 DB)으로 실행됨을 안내
- 최근 고배수 쇼츠 카드 + 후킹 유형 요약 (store.recent_outliers / hook_type_summary)
- 영상 기획 시 씬 1 후킹 힌트 사용 여부 설정 + 실제 주입될 힌트 미리보기
"""
import os
from datetime import datetime

import streamlit as st

from core.radar import store
from core.radar.hints import load_hook_hints
from core.radar.hooks import HOOK_TYPE_KO
from core.radar.keywords import DEFAULT_DAYS, PLANNER_VERTICALS, VERTICAL_KEYWORDS, VERTICAL_LABELS
from core.radar.scoring import CATEGORY_NAMES, VERTICAL_FILTER_DEFAULTS, min_ratio_for
from core.radar.youtube import QUOTA_COST
from core.radar.run import execute

# 화면을 옮겨 다녀도 값이 유지되어야 하는 위젯 키 (app.py에서 매 실행 재할당)
RADAR_PERSIST_KEYS = ("radar_hints_on", "radar_allow_dryrun", "radar_vertical", "radar_days", "radar_min_ratio",
                      "radar_hooks", "radar_source", "radar_view_vertical", "radar_show_irrelevant")


RADAR_DEFAULTS = {"radar_hints_on": True, "radar_allow_dryrun": False, "radar_vertical": "stay", "radar_days": DEFAULT_DAYS,
                  "radar_min_ratio": 5.0, "radar_hooks": 5, "radar_view_vertical": "stay",
                  "radar_show_irrelevant": False}


def init_radar_state():
    """위젯 기본값은 세션에서 한 번만 설정 (위젯 인자 기본값 + 세션 재할당을 함께 쓰면 Streamlit 경고가 뜸)"""
    for k, v in RADAR_DEFAULTS.items():
        st.session_state.setdefault(k, v)


def hints_enabled() -> bool:
    return bool(st.session_state.get("radar_hints_on", True))


def allow_dryrun_hints() -> bool:
    return bool(st.session_state.get("radar_allow_dryrun", False))


def _plain(text) -> str:
    """제목 등 외부 텍스트를 마크다운/HTML로 해석되지 않게 이스케이프"""
    s = str(text or "")
    for ch in "\\`*_{}[]()#+-.!|<>~":
        s = s.replace(ch, "\\" + ch)
    return s


MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
LEADERBOARD_SIZE = 10


def _irrelevant_caption(r) -> str:
    if r.get("vertical") == "viral":
        why = "범용 템플릿으로 추상화 불가"
    else:
        why = "실제 숙소·맛집 리뷰가 아님"
    detail = " · ".join(_plain(x) for x in (r.get("hook_content_type"), r.get("hook_note")) if x)
    return f"🚫 힌트 제외: {why} ({detail})"


def _hook_lines(r):
    if r.get("hook_type") and not r.get("hook_relevant"):
        st.caption(_irrelevant_caption(r))
    elif r.get("hook_type"):
        st.markdown(f"🪝 **{HOOK_TYPE_KO.get(r['hook_type'], r['hook_type'])}** · {_plain(r['pattern_template'])}")
        if r.get("why_it_works"):
            st.caption(_plain(r["why_it_works"]))
    else:
        st.caption("후킹 미추출")


def _render_grid(rows):
    per_row = 3
    for i in range(0, len(rows), per_row):
        cols = st.columns(per_row)
        for col, r in zip(cols, rows[i:i + per_row]):
            with col.container(border=True):
                is_real = not str(r["video_id"]).startswith("mock")
                if is_real:
                    st.image(f"https://i.ytimg.com/vi/{r['video_id']}/hqdefault.jpg", use_container_width=True)
                st.markdown(f"**🔥 {r['outlier_ratio']:.1f}배** · 조회 {r['views']:,} · 구독 {r['subscribers']:,}")
                st.markdown(_plain(r["title"]))
                _hook_lines(r)
                published = (r.get("published_at") or "")[:10]
                st.caption(f"{_plain(r.get('channel') or '')} · {published} · 키워드 '{_plain(r['keyword'])}'")
                if is_real:
                    st.link_button("▶ 영상 보기", r["url"], use_container_width=True)


def _render_leaderboard(rows):
    """viral: 배수순 Top 10 순위 카드 (범용 스켈레톤을 가장 크게)"""
    for no, r in enumerate(rows[:LEADERBOARD_SIZE], 1):
        is_real = not str(r["video_id"]).startswith("mock")
        with st.container(border=True, key=f"radar_lb_{no}"):
            c_rank, c_img, c_body = st.columns([0.5, 1.2, 4], vertical_alignment="center")
            c_rank.markdown(f"### {MEDALS.get(no, f'#{no}')}")
            with c_img:
                if is_real:
                    st.image(f"https://i.ytimg.com/vi/{r['video_id']}/hqdefault.jpg", use_container_width=True)
                else:
                    st.caption("(dry-run)")
            with c_body:
                cat = CATEGORY_NAMES.get(str(r.get("category_id")), r.get("category_id") or "-")
                st.markdown(f"**🔥 {r['outlier_ratio']:.1f}배** · 조회 {r['views']:,} · 구독 {r['subscribers']:,} · {_plain(cat)}")
                if r.get("hook_type") and r.get("hook_relevant"):
                    st.markdown(f"🦴 **범용 템플릿** · {HOOK_TYPE_KO.get(r['hook_type'], r['hook_type'])}  \n"
                                f"#### {_plain(r['pattern_template'])}")
                    if r.get("why_it_works"):
                        st.caption(_plain(r["why_it_works"]))
                else:
                    _hook_lines(r)
                published = (r.get("published_at") or "")[:10]
                st.caption(f"원본: {_plain(r['title'])} · {_plain(r.get('channel') or '')} · {published} · 키워드 '{_plain(r['keyword'])}'")
                if is_real:
                    st.link_button("▶ 원본 보기", r["url"])


def render_radar_page():
    init_radar_state()
    st.subheader("🔥 실시간 떡상 레이더")
    st.caption("최근 업로드된 쇼츠 중 구독자 대비 조회수 배수가 높은 영상을 모으고, 첫 3초 후킹 구조를 추출합니다. "
               "추출된 패턴은 영상 기획 시 씬 1 후킹 힌트로 쓰입니다.")
    has_key = bool(os.getenv("YOUTUBE_API_KEY", "").strip())
    if not has_key:
        st.info("YOUTUBE_API_KEY가 .env에 없어 레이더는 **Dry-run(가짜 데이터)** 으로만 실행됩니다. "
                "Dry-run 패턴은 기본적으로 실제 대본 힌트에 쓰이지 않습니다. 실데이터 수집은 키를 설정한 뒤 다시 실행하세요.")

    # ---------------------------------------------------------------- 실행
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([1.2, 1.4, 1, 1])
        with c1:
            vertical = st.selectbox("업종", list(VERTICAL_KEYWORDS) + ["all"], key="radar_vertical",
                                    format_func=lambda v: "전체" if v == "all" else VERTICAL_LABELS[v])
        with c2:
            days = st.slider("최근 업로드 기간 (일)", 3, 30, key="radar_days")
        with c3:
            min_ratio = st.number_input("최소 배수", 2.0, 100.0, step=1.0, key="radar_min_ratio")
        with c4:
            hooks = st.number_input("후킹 추출 수", 0, 20, key="radar_hooks", help="상위 영상 중 몇 개의 첫 3초를 분석할지 (Gemini 호출 수)")
        kws = sum((VERTICAL_KEYWORDS[v] for v in (VERTICAL_KEYWORDS if vertical == "all" else [vertical])), [])
        # 키워드당 search 1회 + videos/channels 배치(키워드당 약 1~2 units)
        floor = VERTICAL_FILTER_DEFAULTS["viral"]["min_ratio"]
        st.caption(f"검색 키워드: {', '.join(kws)} · 예상 YouTube 쿼터 약 {len(kws) * (QUOTA_COST['search'] + 2)} units"
                   + (f" · Viral은 최소 배수 {floor:g}배 하한 적용, 음악 카테고리만 제외" if vertical in ("viral", "all") else ""))
        label = "📡 레이더 실행 (수집)" if has_key else "📡 레이더 실행 (Dry-run)"
        if st.button(label, type="primary", use_container_width=True, key="radar_run"):
            with st.spinner("최근 쇼츠를 수집하고 후킹 구조를 추출하는 중..."):
                try:
                    res = execute(vertical=vertical, days=int(days), min_ratio=float(min_ratio), hooks=int(hooks),
                                  dry_run=not has_key)
                    st.session_state.radar_last = res
                    st.session_state.radar_source = "dry" if res["mode"] == "dry-run" else "live"
                except Exception as e:
                    st.error(f"레이더 실행 실패: {e}")
        last = st.session_state.get("radar_last")
        if last:
            for v in last["verticals"]:
                msg = (f"{VERTICAL_LABELS[v['vertical']]} [{last['mode'].upper()}] 후보 {v['candidates']}개 → 떡상 {v['outliers']}개 · "
                       f"후킹 추출 {v['hooks_extracted']}건"
                       + (f" ({'범용화 불가' if v['vertical'] == 'viral' else '도메인 부적합'} {v['hooks_irrelevant']}건 제외)"
                          if v.get("hooks_irrelevant") else "")
                       + f" · 쿼터 {v['quota_used']} units")
                (st.warning if v["budget_stopped"] else st.success)(msg + (" · 쿼터 예산 도달로 일부 키워드 생략" if v["budget_stopped"] else ""))

    # ---------------------------------------------------------------- 결과 보기
    sources = [(k, p) for k, p in (("live", store.DEFAULT_DB), ("dry", store.DRYRUN_DB)) if os.path.exists(p)]
    if not sources:
        st.caption("아직 수집된 데이터가 없습니다. 위에서 레이더를 실행해 주세요.")
    else:
        src_labels = {"live": "실데이터", "dry": "Dry-run (가짜 데이터)"}
        keys = [k for k, _ in sources]
        if st.session_state.get("radar_source") not in keys:
            st.session_state.radar_source = keys[0]
        v1, v2 = st.columns([1, 1])
        with v1:
            src = st.segmented_control("데이터", keys, key="radar_source", format_func=lambda k: src_labels[k]) or keys[0]
        with v2:
            view_v = st.segmented_control("업종 보기", list(VERTICAL_KEYWORDS), key="radar_view_vertical",
                                          format_func=lambda v: VERTICAL_LABELS[v]) or "stay"
        view_ratio = min_ratio_for(view_v, float(min_ratio))
        conn = store.connect(dict(sources)[src])
        try:
            summary = store.hook_type_summary(conn, view_v)
            rows = store.recent_outliers(conn, view_v, int(days), view_ratio, limit=24)
        finally:
            conn.close()
        # Gemini가 도메인 부적합(예능·애니·밈·반려동물 클립)으로 판정한 영상은 기본 숨김
        hidden = [r for r in rows if r.get("hook_type") and not r.get("hook_relevant")]

        if summary:
            st.markdown("##### 요즘 먹히는 후킹 유형")
            cols = st.columns(min(len(summary), 5))
            for col, s in zip(cols, summary[:5]):
                col.metric(HOOK_TYPE_KO.get(s["hook_type"], s["hook_type"]), f"평균 {s['avg_ratio']}배", f"{s['n']}개 영상",
                           delta_color="off")

        show_hidden = st.checkbox(f"제외된 비적합 영상 보기 ({len(hidden)}개)", key="radar_show_irrelevant",
                                  disabled=not hidden, help="실제 숙소·맛집 리뷰가 아니라고 판정되어 힌트에서 빠진 영상")
        if not show_hidden:
            rows = [r for r in rows if r not in hidden]
        hidden_note = f" · 비적합 {len(hidden)}개 숨김" if hidden and not show_hidden else ""
        if view_v == "viral":
            st.markdown(f"##### 🏆 최근 {int(days)}일 전체 떡상 랭킹 Top {min(len(rows), LEADERBOARD_SIZE)} "
                        f"({view_ratio:g}배 이상){hidden_note}")
        else:
            st.markdown(f"##### 최근 {int(days)}일 떡상 쇼츠 ({len(rows)}개, {view_ratio:g}배 이상){hidden_note}")
        if not rows:
            st.caption("조건에 맞는 영상이 없습니다. 기간·배수 조건을 완화하거나 레이더를 다시 실행해 보세요.")
        elif view_v == "viral":
            _render_leaderboard(rows)
        else:
            _render_grid(rows)

    # ---------------------------------------------------------------- 기획 연동 설정
    st.divider()
    st.markdown("##### 🎬 영상 기획에 연결")
    st.toggle("영상 기획 시 레이더 후킹 패턴을 씬 1 힌트로 사용", key="radar_hints_on",
              help="숙소 제휴(7씬)와 사장님 모드(숙소·음식점)의 씬 1 후킹 대본 프롬프트에 최근 고배수 패턴 구조를 넣습니다.")
    st.toggle("테스트용: Dry-run(가짜) 패턴도 힌트로 사용", key="radar_allow_dryrun",
              help="실데이터가 없을 때 연결 동작만 확인하는 용도입니다. 실제 영상 제작에는 끄세요.")
    for v in PLANNER_VERTICALS:
        hints = load_hook_hints(v, days=int(days), min_ratio=float(min_ratio), allow_dryrun=allow_dryrun_hints())
        title = f"{VERTICAL_LABELS[v]} 씬 1 힌트 미리보기 · " + (
            f"패턴 {len(hints.references)}개{f' (범용 {hints.viral_count}개 보충)' if hints.viral_count else ''}"
            f"{' (Dry-run)' if hints.is_dryrun else ''}" if hints else "사용 가능한 패턴 없음")
        with st.expander(title):
            if hints:
                st.code(hints.prompt_block, language=None)
            else:
                st.caption("수집된 실데이터 패턴이 없어 기본 후킹 규칙으로 기획됩니다.")
