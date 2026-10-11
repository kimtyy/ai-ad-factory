"""
ui/youtube_panel.py - 렌더 완료 화면: 썸네일 + 유튜브 쇼츠 원클릭 업로드 (제목·설명·첫 댓글·공개 범위)

제목·설명·댓글은 모드별로 미리 채워 두고 고칠 수 있게 함:
- 숙소 제휴(아고다·트립닷컴): 설명·첫 댓글에 제휴 링크 + 공정위 추천·보증 고지
- 쇼핑 제휴(쿠팡): 쿠팡 파트너스 고지 + 링크
- 사장님: 네이버 검색 안내
"""
import hashlib
import os
from typing import Any, Dict, List, Optional

import streamlit as st

from core import youtube_upload as yu
from core.shorts_meta import build_shorts_description, make_shorts_copy

TAGS = {   # 업로드 메타데이터 tags (검색용, 화면에 안 보임)
    "travel": ["숙소추천", "호텔추천", "여행", "국내여행", "여행을떠나자"],
    "commerce": ["추천템", "살림템", "리뷰"],
    "owner": ["가게추천", "가볼만한곳", "동네가게"],
}


def default_texts(mode: str, copy: Optional[Dict[str, Any]], link: str, disclosure_full: str, disclosure_short: str = "",
                  business_name: str = "", search_kw: str = "", hook: str = "") -> Dict[str, str]:
    """
    copy(core.shorts_meta.make_shorts_copy 결과) → 제목·설명·첫 댓글 기본값
    설명: 【제목】 / 제휴 고지(짧게) / 📌 첫 댓글 안내 / 해시태그 5~7개. 링크와 전체 고지는 첫 댓글에
    """
    copy = copy or make_shorts_copy(mode, hook=hook, product_name=business_name, mock=True)
    title = copy["title"]
    desc = build_shorts_description(mode, title, copy["hashtags"], disclosure_short)
    if mode == "owner":
        comment = f"📍 네이버에서 '{search_kw}' 검색해 보세요!" if search_kw else ""
    else:
        comment = (f"🔗 {'숙소 상세 정보·예약' if mode == 'travel' else '제품 상세 정보·구매'} 링크: {link}\n{disclosure_full}".strip()
                   if link else "")
    return {"title": title[:yu.TITLE_MAX], "desc": desc, "comment": comment}


def render_youtube_panel(video_path: str, thumb_path: Optional[str], mode: str, hook: str, link: str,
                         disclosure: str, business_name: str = "", search_kw: str = "",
                         copy: Optional[Dict[str, Any]] = None, disclosure_short: str = "") -> None:
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
    elif copy is not None:
        st.warning("🔥 떡상 레이더 데이터가 없어 기본 카피로 만들었어요. 레이더 자동 수집(scripts/install_radar_schedule.ps1)을 확인해 주세요.")
    st.markdown("#### 🖼️ 썸네일 (영상 첫 0.5초에도 들어감)")
    if thumb_path and os.path.exists(thumb_path):
        c1, c2 = st.columns([1, 1.4])
        with c1:
            st.image(thumb_path, use_container_width=True)
        with c2:
            with open(thumb_path, "rb") as f:
                st.download_button("⬇️ 썸네일 PNG 저장", f, file_name=os.path.basename(thumb_path), mime="image/png",
                                   use_container_width=True, key="dl_thumb")
            st.caption("1080x1920 고화질 PNG. 업로드 시 2MB가 넘으면 같은 크기 JPEG로 자동 변환해 등록합니다.")

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
    d = default_texts(mode, copy, link, disclosure, disclosure_short, business_name, search_kw, hook)
    title = st.text_input("제목 (#Shorts 자동 추가)", value=d["title"], max_chars=yu.TITLE_MAX, key=f"yt_title_{tag}")
    desc = st.text_area("설명란", value=d["desc"], height=170, key=f"yt_desc_{tag}")
    comment = st.text_area("첫 댓글 (업로드 직후 자동 등록)", value=d["comment"], height=90, key=f"yt_comment_{tag}")
    privacy = st.radio("공개 범위", list(yu.PRIVACY_KO), index=2, format_func=yu.PRIVACY_KO.get, horizontal=True,
                       key=f"yt_privacy_{tag}", help="처음에는 비공개로 올려 확인한 뒤 스튜디오에서 공개로 바꾸는 것을 권장해요.")
    if mode in ("travel", "commerce") and link and link not in comment:
        st.warning("첫 댓글에 제휴 링크가 빠져 있어요 (설명란이 '첫 번째 댓글 확인' 안내로 바뀌었어요).")
    if mode in ("travel", "commerce") and link and not any(w in desc for w in ("수수료", "파트너스", "제휴")):
        st.warning("설명란 첫 부분에 제휴 고지 문구(공정위 추천·보증 심사지침)가 빠져 있어요.")

    if st.button("🚀 유튜브 쇼츠 업로드", type="primary", use_container_width=True, key=f"yt_upload_{tag}"):
        bar = st.progress(0.0, text="업로드 중...")
        try:
            res = yu.upload_short(video_path, title, desc, mode=key, thumb_path=thumb_path, comment=comment, privacy=privacy,
                                  tags=TAGS.get(mode, []),
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
        st.markdown(f"- 썸네일: {'등록됨' if res['thumbnail_ok'] else '미등록'} · 첫 댓글: {'등록됨' if res['comment_ok'] else '없음'}\n"
                    f"- 📌 **댓글 고정은 API로 할 수 없어요** → [스튜디오 댓글]({res['comments_url']})에서 내 댓글 ⋮ → '고정' 한 번\n"
                    f"- [스튜디오에서 열기]({res['studio_url']})")
        for w in res["warnings"]:
            st.warning(w)
