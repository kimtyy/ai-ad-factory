"""
ui/youtube_panel.py - 렌더 완료 화면: 썸네일 + 유튜브 쇼츠 원클릭 업로드 (제목·설명·첫 댓글·공개 범위)

제목·설명·댓글은 모드별로 미리 채워 두고 고칠 수 있게 함:
- 숙소 제휴(아고다·트립닷컴): 설명·첫 댓글에 제휴 링크 + 공정위 추천·보증 고지
- 쇼핑 제휴(쿠팡): 쿠팡 파트너스 고지 + 링크
- 사장님: 네이버 검색 안내 + 네이버 지도 링크
첫 댓글 = AI 참여 유도 질문 + 위 안내/링크 + 핵심 해시태그 (core.shorts_meta.build_first_comment), 업로드 직후 자동 등록
"""
import hashlib
import os
from typing import Any, Dict, List, Optional

import streamlit as st

from core import youtube_upload as yu
from core.shorts_meta import build_first_comment, build_shorts_description, make_shorts_copy

TAGS = {   # 업로드 메타데이터 tags (검색용, 화면에 안 보임)
    "travel": ["숙소추천", "호텔추천", "여행", "국내여행", "여행을떠나자"],
    "commerce": ["추천템", "살림템", "리뷰"],
    "owner": ["가게추천", "가볼만한곳", "동네가게"],
}


def default_texts(mode: str, copy: Optional[Dict[str, Any]], link: str, disclosure_full: str, disclosure_short: str = "",
                  business_name: str = "", search_kw: str = "", hook: str = "",
                  items: Optional[List[Dict[str, str]]] = None,
                  business_type: str = "", usp: str = "", core_concept: str = "", address: str = "") -> Dict[str, str]:
    """
    copy(core.shorts_meta.make_shorts_copy 결과) → 제목·설명·첫 댓글 기본값
    설명: 【제목】 / 제휴 고지(짧게) / 📌 첫 댓글 안내 / 해시태그 5~7개. 링크와 전체 고지는 첫 댓글에
    """
    copy = copy or make_shorts_copy(mode, hook=hook, product_name=business_name, mock=True,
                                   core_concept=core_concept, business_type=business_type)
    title = copy["title"]
    desc = build_shorts_description(mode, title, copy["hashtags"], disclosure_short)
    comment = build_first_comment(mode, copy.get("comment_question", ""), copy.get("hashtags"),
                                  search_keyword=search_kw or (business_name if mode == "owner" else ""),
                                  link=link, disclosure_full=disclosure_full, items=items,
                                  business_type=business_type, usp=usp, core_concept=core_concept, address=address)
    return {"title": title[:yu.TITLE_MAX], "desc": desc, "comment": comment}


def render_youtube_panel(video_path: str, thumb_path: Optional[str], mode: str, hook: str, link: str,
                         disclosure: str, business_name: str = "", search_kw: str = "",
                         copy: Optional[Dict[str, Any]] = None, disclosure_short: str = "",
                         items: Optional[List[Dict[str, str]]] = None,
                         seo_title: Optional[str] = None, seo_desc: Optional[str] = None,
                         seo_tags: Optional[str] = None,
                         business_type: str = "", usp: str = "", core_concept: str = "", address: str = "") -> None:
    """items: 쇼핑 묶음 큐레이션 상품 [{name, short, link}] → 첫 댓글에 상품별 구매 좌표 + 링크 종류별 제휴 고지"""
    radar = (copy or {}).get("radar")
    if radar:
        pt = radar.get("pattern")
        when = str(radar.get("built_at") or "")[:10]
        msg = (f"🔥 떡상 레이더 반영 ({when} 기준 · 최근 떡상 {radar.get('n_outliers', 0)}개 분석, 최고 {radar.get('top_ratio', 0)}배)"
               + (f"\n\n- 제목 구조: \"{pt['template']}\" ({pt.get('ratio', 0)}배 영상 패턴)" if pt else "\n\n- 제목 구조: 사실에 맞는 패턴이 없어 기본 훅 사용")
               + (f"\n- 레이더 해시태그: {' '.join(radar.get('tags') or [])}" if radar.get("tags") else ""))
        if radar.get("fallback_from"):
            msg += "\n- 이 업종 데이터가 아직 없어 업종 무관(전체 떡상) 패턴을 사용했어요"
        (st.warning if radar.get("stale") else st.info)(msg + (f"\n- ⚠️ 데이터가 {radar.get('age_days')}일 전 것이에요 (자동 수집 확인)"
                                                               if radar.get("stale") else ""))
    elif copy is not None and not seo_title:
        st.warning("🔥 떡상 레이더 데이터가 없어 기본 카피로 만들었어요. 레이더 자동 수집(scripts/install_radar_schedule.ps1)을 확인해 주세요.")
    st.markdown("#### 🖼️ 썸네일 (영상 첫 0.8초 커버로도 들어감)")
    if thumb_path and os.path.exists(thumb_path):
        cover = yu.thumbnail_for_upload(thumb_path)          # 업로드되는 바로 그 파일 (독립 커버 이미지 thumbnail.jpg)
        c1, c2 = st.columns([1, 1.4])
        with c1:
            st.image(cover, use_container_width=True)
        with c2:
            with open(cover, "rb") as f:
                st.download_button("⬇️ 커버 이미지 저장 (thumbnail.jpg)", f, file_name=os.path.basename(cover), mime="image/jpeg",
                                   use_container_width=True, key="dl_thumb")
            st.caption(f"유튜브 업로드에 쓰이는 커버 이미지예요 (1080x1920 JPEG, {os.path.getsize(cover) // 1024}KB, 2MB 이하). "
                       "원본 PNG는 같은 폴더에 그대로 있어요.")
            from core.mode_policy import INTRO_COVER_SEC, intro_cover_enabled
            if intro_cover_enabled():
                st.info(f"ℹ️ 쇼츠는 **첫 프레임(0초)이 대표 썸네일로 자동 지정**돼요. 이 영상은 첫 {INTRO_COVER_SEC:.1f}초에 "
                        "위 커버 화면이 들어가 있어 피드에서 그대로 보여요 (나레이션과 자막은 그대로 진행). "
                        "맞춤 썸네일 업로드는 쇼츠 피드에 적용되지 않을 수 있어요.")
            else:
                st.warning("⚠️ 쇼츠는 첫 프레임(0초)이 대표 썸네일이에요. 인트로 커버가 꺼져 있어(INTRO_COVER=0) 영상의 첫 화면이 그대로 "
                           "대표 화면이 됩니다.")

    key = yu.channel_key(mode)
    target = yu.expected_channel(key)
    st.markdown(f"#### 🚀 유튜브 쇼츠 업로드 → **{target or '(채널 이름 미설정)'}** 채널")
    if not target:
        st.info(f"{yu.CHANNEL_LABEL[key]} 채널 이름이 .env에 없어요. `YOUTUBE_CHANNEL_{key.upper()}=채널이름`을 적어야 "
                "업로드할 수 있어요 (엉뚱한 채널에 올라가지 않게 업로드 직전 채널 이름을 확인합니다).")
        return
    if not yu.is_authorized(key):
        st.info(f"'{target}' 채널이 아직 연결되지 않았어요. PC에서 한 번만 `{yu.auth_command(key)}`를 실행해 "
                f"'{target}' 채널을 연결하면, 이 자리에서 원클릭 업로드가 됩니다.")
        return

    # 영상이 바뀌면 입력 칸도 새로 (위젯 키에 영상 경로·수정 시각을 섞음)
    tag = hashlib.md5(f"{video_path}:{os.path.getmtime(video_path)}".encode()).hexdigest()[:8]
    d = default_texts(mode, copy, link, disclosure, disclosure_short, business_name, search_kw, hook, items=items,
                      business_type=business_type, usp=usp, core_concept=core_concept, address=address)
    initial_title = (seo_title or d["title"])[:yu.TITLE_MAX]
    initial_desc = seo_desc or d["desc"]
    # SEO 설명란이 제휴 고지를 빠뜨리면 기본 설명란의 고지(공정위 추천·보증 심사지침)를 맨 앞에 붙임
    if mode in ("travel", "commerce") and link and not any(w in initial_desc for w in ("수수료", "파트너스", "제휴")):
        notice = (disclosure_short or "").strip()
        if notice:
            initial_desc = f"{notice}\n\n{initial_desc}"
    initial_tags_str = seo_tags or ", ".join(TAGS.get(mode, []))
    # 단일 태그 풀: 카피가 만든 tag_set이 있으면 설명란 해시태그 줄·검색 태그가 그것과 같아지도록 맞춘다 (첫 댓글은 같은 core에서 만들어짐)
    from core.hashtags import TagSet, replace_hashtag_line
    _ts = TagSet.from_dict((copy or {}).get("tag_set"))
    if _ts is not None:
        initial_desc = replace_hashtag_line(initial_desc, _ts.core)
        initial_tags_str = ", ".join(_ts.pool)

    st.markdown("**1. 쇼츠 제목** (후킹 문구 + 키워드 + #shorts 자동 반영)")
    title = st.text_input("제목", value=initial_title, max_chars=yu.TITLE_MAX, key=f"yt_title_{tag}", label_visibility="collapsed")
    
    st.markdown("**2. 설명란** (핵심 요약 + 해시태그)")
    desc = st.text_area("설명란", value=initial_desc, height=140, key=f"yt_desc_{tag}", label_visibility="collapsed")

    st.markdown("**3. 검색 태그** (알고리즘 유입용 쉼표 구분)")
    tags_input = st.text_input("검색 태그", value=initial_tags_str, key=f"yt_tags_{tag}", label_visibility="collapsed")

    st.markdown("**4. 첫 댓글** (업로드 직후 채널 계정으로 자동 등록)")
    comment = st.text_area("첫 댓글", value=d["comment"], height=140, key=f"yt_comment_{tag}", label_visibility="collapsed")

    if comment.strip() and not yu.has_comment_scope(key):
        st.warning(f"이 채널 연결에는 댓글 권한이 없어요 → PC에서 `{yu.auth_command(key)}`로 다시 연결해야 첫 댓글이 자동 등록돼요.")
    priv_opts = [str(k) for k in yu.PRIVACY_KO if k]
    cur_priv = st.session_state.get(f"yt_privacy_{tag}")
    priv_idx = priv_opts.index(cur_priv) if cur_priv in priv_opts else (2 if len(priv_opts) > 2 else 0)
    privacy = st.radio(str("공개 범위" or ""), priv_opts, index=priv_idx,
                       format_func=lambda k: str(yu.PRIVACY_KO.get(k, k or "")), horizontal=True,
                       key=f"yt_privacy_{tag}", help="처음에는 비공개로 올려 확인한 뒤 스튜디오에서 공개로 바꾸는 것을 권장해요.")
    if mode in ("travel", "commerce") and link and link not in comment:
        st.warning("첫 댓글에 제휴 링크가 빠져 있어요 (설명란이 '첫 번째 댓글 확인' 안내로 바뀌었어요).")
    missing = [it.get("short") or it.get("name") for it in (items or []) if not (it.get("link") or "").strip()
               or it["link"].strip() not in comment]
    if missing:
        st.warning(f"첫 댓글에 구매 링크가 빠진 상품이 있어요: {', '.join(missing)} (위 묶음 상품 칸에 링크를 넣어 주세요).")
    if mode in ("travel", "commerce") and link and not any(w in desc for w in ("수수료", "파트너스", "제휴")):
        st.warning("설명란 첫 부분에 제휴 고지 문구(공정위 추천·보증 심사지침)가 빠져 있어요.")

    parsed_tags = [t.strip().lstrip("#") for t in tags_input.split(",") if t.strip()] or TAGS.get(mode, [])

    if st.button("🚀 유튜브 쇼츠 원클릭 자동 업로드", type="primary", use_container_width=True, key=f"yt_upload_{tag}"):
        bar = st.progress(0.0, text="유튜브 쇼츠 업로드 중...")
        try:
            res = yu.upload_short(video_path, title, desc, mode=key, thumb_path=thumb_path, comment=comment, privacy=privacy,
                                  tags=parsed_tags,
                                  category_id=yu.CATEGORY_TRAVEL if mode == "travel" else yu.CATEGORY_PEOPLE,
                                  progress=lambda p: bar.progress(min(0.99, p), text=f"업로드 중... {p * 100:.0f}%"))
            bar.progress(1.0, text="업로드 완료")
            st.session_state[f"yt_result_{tag}"] = res
        except yu.YouTubeUploadError as e:
            bar.empty()
            st.error(str(e))
        except Exception as e:      # 네트워크·API 오류: 화면이 죽지 않게
            bar.empty()
            st.error(f"업로드 실패: {e}")

    res: Optional[Dict[str, Any]] = st.session_state.get(f"yt_result_{tag}")
    if res:
        st.success(f"✅ '{res['channel']}' 채널에 업로드됨 ({yu.PRIVACY_KO.get(res['privacy'], res['privacy'])}): {res['url']}")
        kids = res.get("made_for_kids")
        st.markdown(f"- 썸네일: {'등록됨' if res['thumbnail_ok'] else '미등록'} · 첫 댓글: "
                    f"{'[등록됨](' + res['comment_url'] + ')' if res['comment_ok'] and res.get('comment_url') else '등록됨' if res['comment_ok'] else '⏳ 공개 전환 대기' if res.get('comment_pending') else '없음'}"
                    f" · 시청자층: {'아동용 아님 (댓글 열림)' if kids is False else '⚠️ 아동용 (댓글 막힘)' if kids else '확인 못 함'}\n"
                    f"- 📌 **댓글 고정은 API로 할 수 없어요** → [스튜디오 댓글]({res['comments_url']})에서 내 댓글 ⋮ → '고정' 한 번\n"
                    f"- [스튜디오에서 열기]({res['studio_url']})")
        for w in res["warnings"]:
            st.warning(w)
        if res.get("comment_pending") and yu.pending_comment(res["video_id"]):
            st.info("⏳ **첫 댓글 대기 중** - 유튜브는 비공개 영상에 댓글을 받지 않아요. 스튜디오에서 공개(또는 일부 공개)로 바꾼 뒤 "
                    "아래 버튼을 누르면 바로 등록돼요 (매일 아침 레이더 작업에서도 자동 확인).")
            if st.button("💬 공개로 바꿨어요 → 첫 댓글 등록", key=f"yt_pending_{tag}", use_container_width=True):
                r = (yu.post_pending_comments(only_video=res["video_id"]) or [{"state": "gone"}])[0]
                if r["state"] == "posted":
                    st.success(f"첫 댓글 등록 완료: {r['comment_url']}")
                elif r["state"] == "waiting":
                    st.warning("아직 비공개예요. 스튜디오에서 공개 범위를 바꾼 뒤 다시 눌러 주세요.")
                else:
                    st.warning(r.get("warning") or "영상을 찾을 수 없어 대기열에서 뺐어요.")
