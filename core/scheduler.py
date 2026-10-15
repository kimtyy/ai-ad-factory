"""
core/scheduler.py - 구글 캘린더 일정 → 자동 제작 (scripts/calendar_runner.py가 작업 스케줄러로 10분마다 호출)

흐름 (한 번 실행에 일정 1건씩, 오래된 것부터):
  ① 전용 캘린더에서 시작 시간이 지난 일정 중 아직 처리 안 한 것 (제목 '[모드명] 키워드', 최근 LOOKBACK_HOURS 안)
  ② 설명란 양식 해석 → core.pipeline.JobSpec  (사진은 inbox/<사진폴더>에서)
  ③ 공용 제작 엔진: 기획 → 렌더 → 쇼핑·사장님은 비공개 업로드 + 첫 댓글 대기열 / 시골책방은 첫 댓글 초안 ('채널 미연결')
  ④ 일정 설명란 맨 아래에 결과 블록 기록 (✅ 링크·첫 댓글 전문 / ❌ 원인과 고칠 것) + 일정 숨은 속성에 처리 상태
- 같은 일정은 한 번만 (일정 숨은 속성 adf_status + 로컬 기록). 다시 돌리려면 설명란에 '재실행: 예'
- 안전: 업로드는 비공개만, 하루 최대 CALENDAR_MAX_PER_DAY건(기본 3), 대시보드에서 렌더 중(ffmpeg 실행 중)이면 다음 주기로,
  입력이 모자라면 대충 만들지 않고 빠진 항목을 설명란에 적음
"""
import datetime as dt
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import calendar_sync as cs

logger = logging.getLogger("ad_factory.scheduler")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "outputs", "scheduler")
LOOKBACK_HOURS = 24                 # 이보다 오래 지난 일정은 처리하지 않음 (PC가 오래 꺼져 있었던 경우 폭주 방지)
RESULT_MARK = "──── 🤖 AI 광고 공장 자동 제작 결과"
MEDIA_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".m4v")
MODE_KO = {"commerce": "쇼핑", "book": "시골책방", "owner": "사장님", "travel": "숙소"}
OWNER_TYPES_KO = {"음식점": "food", "카페": "food", "숙소": "stay", "펜션": "stay", "매장": "shop", "기타": "shop"}


def inbox_dir() -> str:
    return os.getenv("CALENDAR_INBOX", "").strip() or os.path.join(ROOT, "inbox")


def max_per_day() -> int:
    try:
        return max(0, int(os.getenv("CALENDAR_MAX_PER_DAY", "3")))
    except ValueError:
        return 3


# ---------------------------------------------------------------- 설명란 양식
FIELD_RE = re.compile(r"^\s*([가-힣A-Za-z0-9 ]{1,12}?)\s*[:：]\s*(.*)$")


def strip_result(description: str) -> str:
    """이전 실행 결과 블록(RESULT_MARK 아래)을 뺀 사용자 입력 부분"""
    d = description or ""
    i = d.find(RESULT_MARK)
    return (d[:i] if i >= 0 else d).rstrip()


def _plain(text: str) -> str:
    """구글 캘린더 설명란은 HTML(<br>, <b> 등)일 수 있음 → 줄바꿈 텍스트로"""
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", text or "")
    t = re.sub(r"<[^>]+>", "", t)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        t = t.replace(a, b)
    return t


def parse_fields(description: str) -> Dict[str, str]:
    """'키: 값' 줄 → dict. 키 없이 이어지는 줄은 앞 키의 값에 붙임 (여러 줄 원문용). 구분선(───)·빈 줄은 무시"""
    out: Dict[str, str] = {}
    last = None
    for raw in _plain(strip_result(description)).splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"[─━\-=_·.]{3,}", line):
            continue
        m = FIELD_RE.match(line)
        if m and not line.startswith("http"):
            last = m.group(1).replace(" ", "")
            out[last] = m.group(2).strip()
        elif last:
            out[last] = (out[last] + " " + line).strip()
    return out


def _photos(folder: str) -> List[str]:
    """inbox/<사진폴더> 안 사진·영상 (파일 이름 순 = 씬 순서)"""
    base = os.path.realpath(inbox_dir())
    path = os.path.realpath(os.path.join(base, folder)) if folder else ""
    if not folder or not path.startswith(base) or not os.path.isdir(path):
        return []
    return [os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(MEDIA_EXTS)]


class SpecError(ValueError):
    """설명란 입력이 모자람 → 결과 블록에 '고칠 것'으로 적음"""


def spec_from_event(title: Dict[str, str], fields: Dict[str, str], api_key: Optional[str], mock: bool = False):
    """일정 제목·설명란 → JobSpec (+ 업로드 여부). 빠진 필수 항목은 SpecError로 한꺼번에"""
    from core import pipeline
    mode, keyword = title["mode"], title["keyword"]
    f = {k: v for k, v in fields.items() if v}
    folder = f.get("사진폴더") or keyword
    media = _photos(folder)
    need: List[str] = []
    common = dict(api_key=api_key, mock=mock, voice_actor=f.get("성우", "female_20s"), media_paths=media)
    bgm = (f.get("BGM") or "").strip()
    if bgm and bgm not in ("없음", "끔"):
        from core.render import bgm as bgm_mod
        common["bgm_path"] = bgm_mod.pick_track(bgm_mod.RANDOM if bgm == "랜덤" else bgm)
        if not common["bgm_path"]:
            need.append(f"BGM: '{bgm}' 파일이 assets/bgm에 없어요")
    elif mode == "book" and not bgm:
        from core.render import bgm as bgm_mod
        track = bgm_mod.book_default_track()
        common["bgm_path"] = bgm_mod.pick_track(track) if track else None
    if mode == "travel":
        raise SpecError("숙소 모드는 크롤링·사진 매칭이 필요해 캘린더 자동 제작을 지원하지 않아요 (대시보드에서 만들어 주세요).")
    notes: List[str] = []
    if mode == "owner":
        name = f.get("상호") or keyword
        spec = pipeline.JobSpec(mode="owner", name=name, usp=f.get("특징", ""), search_keyword=f.get("검색어", name),
                                business_type=OWNER_TYPES_KO.get(f.get("업종", ""), None), address=f.get("주소", ""),
                                reading=f.get("읽는법", ""), **common)
        from core.render.brand import BrandOverlay, EndCard
        from core.naver_local import region_of
        spec.brand = BrandOverlay(business_name=name, end_card=EndCard(search_keyword=spec.search_keyword,
                                                                      sub_text=region_of(spec.address)))
    elif mode == "book":
        query = f.get("ISBN") or f.get("책") or ""
        if not query:
            need.append("책: 책 제목 (또는 ISBN: 13자리)")
        # 템플릿: 원문이 있으면 우화, 없으면 책소개로 사유 스토리텔링(책소개가 충분할 때), 그것도 없으면 기본 큐레이션
        tpl_req = {"우화": "fable", "사유": "reflect", "기본": "basic"}.get(f.get("템플릿", ""), "")
        title, author, intro, link = f.get("책", ""), f.get("저자", ""), f.get("소개", ""), f.get("링크", "")
        publisher = f.get("출판사", "")
        # 시골책방 화면: 씬 1~N-1은 AI 일러스트, 마지막 씬만 표지 → 사진은 표지 한 장만 (자동 수집 표지, '표지: 직접'이면 inbox 첫 사진)
        common["media_paths"] = list(media[:1]) if f.get("표지") == "직접" else []
        if query and f.get("자동수집", "예") not in ("아니오", "끔"):
            # 책 정보 자동 수집: 카카오·국립중앙도서관(책소개)·쿠팡 파트너스(고화질 표지·제휴 링크). 직접 적은 값이 우선
            from core.book_info import BookInfoError, collect_book
            try:
                info = collect_book(query, author=author, aladin_link=link, out_dir=os.path.join(STATE_DIR, "books"))
                title, author = title or info.title, author or info.author
                publisher = publisher or info.publisher
                intro, link = intro or info.description, link or info.purchase_link
                notes.append("📚 책 정보 자동 수집: " + info.summary() + " · 출처 " + ", ".join(info.sources))
                notes += [f"⚠️ {w}" for w in info.warnings if "키 없음" not in w or not info.sources]
                if info.cover_path and f.get("표지", "자동") != "직접":
                    common["media_paths"] = [info.cover_path]
            except BookInfoError as e:
                need.append(f"책 정보 자동 수집 실패: {e}")
        from core.planners.book import REFLECT_MIN_INFO
        auto = "fable" if f.get("문장") else ("reflect" if len(intro) >= REFLECT_MIN_INFO else "basic")
        tpl = tpl_req or auto
        if tpl == "fable" and not f.get("문장"):
            tpl = "reflect" if len(intro) >= REFLECT_MIN_INFO else "basic"
            notes.append(f"우화 템플릿은 책 속 원문('문장:')이 필요해 {'사유 스토리텔링' if tpl == 'reflect' else '기본 큐레이션'}으로 만들었어요")
        if tpl == "reflect" and len(intro) < REFLECT_MIN_INFO:
            tpl = "basic"
            notes.append("책소개가 짧아 사유 스토리텔링 대신 기본 큐레이션으로 만들었어요")
        if not intro:
            notes.append("책소개가 없어 제목만으로 대본을 써요 (설명란 '소개:'로 사실을 적어 주면 더 정확해요)")
        if not common["media_paths"] and media:
            common["media_paths"] = list(media[:1])          # 자동 수집 표지가 없으면 inbox 첫 사진을 표지로
        spec = pipeline.JobSpec(mode="book", name=f.get("책방") or "시골책방", usp=intro, book_title=title,
                                book_author=author, book_quote=f.get("문장", ""), book_link=link,
                                book_search_kw=f.get("검색어", ""), book_template=tpl, book_publisher=publisher,
                                book_photo_count=2 if f.get("책사진") == "2" else 1, **common)
    else:
        items = []
        for k in ("상품1", "상품2", "상품3"):
            if f.get(k):
                parts = [p.strip() for p in f[k].split("|")]
                items.append({"name": parts[0], "usp": parts[1] if len(parts) > 1 else "",
                              "link": parts[2] if len(parts) > 2 else ""})
        if items or f.get("구성") == "묶음":
            if len(items) < 3:
                need.append("상품1·상품2·상품3: '이름 | 특징 | 링크' 세 줄")
            missing_links = [i + 1 for i, it in enumerate(items) if not it["link"]]
            if missing_links:
                need.append(f"상품{', 상품'.join(map(str, missing_links))}: 구매 링크 (쿠팡 파트너스/스마트스토어)")
            spec = pipeline.JobSpec(mode="commerce", layout="bundle", name=f.get("주제") or keyword,
                                    bundle_theme=f.get("주제") or keyword, bundle_bottom=f.get("하단바", ""),
                                    bundle_items=items, usp=", ".join(it["usp"] for it in items if it["usp"]), **common)
        else:
            if not f.get("상품"):
                need.append("상품: 판매 페이지 상품명 (지어내지 않아요)")
            if not f.get("링크"):
                need.append("링크: 쿠팡 파트너스 링크")
            spec = pipeline.JobSpec(mode="commerce", name=f.get("상품", ""), usp=f.get("특징", ""), link=f.get("링크", ""),
                                    **common)
    if not spec.media_paths and not mock:
        need.append("표지: 책 표지를 찾지 못했어요 (책 제목·ISBN 확인, 또는 inbox 폴더에 표지 사진)" if mode == "book" else
                    f"사진폴더: inbox/{folder} 에 사진이 없어요 (설명란 '사진폴더: 폴더이름', 파일 이름 순서 = 씬 순서)")
    if need:
        raise SpecError("설명란에 빠진 항목: " + " / ".join(need))
    spec.notes = notes            # 결과 블록에 함께 적을 안내 (자동 수집 출처·템플릿 전환 등)
    return spec


# ---------------------------------------------------------------- 상태·안전 장치
def _state_path() -> str:
    return os.path.join(STATE_DIR, "state.json")


def load_state() -> Dict[str, Any]:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"events": {}, "runs": []}


def save_state(state: Dict[str, Any]) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _state_path())


def render_busy() -> bool:
    """대시보드 등에서 ffmpeg가 돌고 있으면 True (렌더 겹치면 둘 다 느려지고 메모리 부족 위험). Windows·리눅스(Railway) 공용"""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=15,
                                 encoding="utf-8", errors="ignore").stdout
            return any(line.lower().startswith('"ffmpeg') for line in out.splitlines())
        out = subprocess.run(["ps", "-eo", "comm"], capture_output=True, text=True, timeout=15).stdout
        return any(line.strip().lower().startswith("ffmpeg") for line in out.splitlines())
    except Exception:
        return False


def _event_key(ev: Dict[str, Any]) -> str:
    return ev.get("id", "")


def _status(ev: Dict[str, Any]) -> str:
    return ((ev.get("extendedProperties") or {}).get("private") or {}).get("adf_status", "")


def _start(ev: Dict[str, Any]) -> Optional[dt.datetime]:
    s = ev.get("start") or {}
    raw = s.get("dateTime") or s.get("date")
    if not raw:
        return None
    try:
        if "T" in raw:
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.datetime.fromisoformat(raw).replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    except ValueError:
        return None


def _older_than(iso: str, now: dt.datetime, hours: float) -> bool:
    try:
        return now - dt.datetime.fromisoformat(iso) > dt.timedelta(hours=hours)
    except (TypeError, ValueError):
        return True


def due_events(events: List[Dict[str, Any]], now: dt.datetime, state: Dict[str, Any]) -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    """시작 시간이 지났고 아직 처리 안 한 [모드명] 일정 (재실행: 예 이면 다시)"""
    out = []
    for ev in events:
        title = cs.parse_title(ev.get("summary", ""))
        start = _start(ev)
        if not title or not start or start > now or start < now - dt.timedelta(hours=LOOKBACK_HOURS):
            continue
        fields = parse_fields(ev.get("description", ""))
        rerun = fields.get("재실행", "").strip() in ("예", "네", "yes", "y", "Y")
        priv = (ev.get("extendedProperties") or {}).get("private") or {}
        stale = _status(ev) == "running" and _older_than(priv.get("adf_started", ""), now, hours=2)   # 실행 중 PC가 꺼진 경우
        done = (_status(ev) in ("done", "failed") or (_status(ev) == "running" and not stale)
                or state["events"].get(_event_key(ev), {}).get("status") in ("done", "failed"))
        if done and not rerun:
            continue
        out.append((ev, title))
    return out


# ---------------------------------------------------------------- 결과 기록
def result_block(now: dt.datetime, ok: bool, lines: List[str]) -> str:
    head = f"{RESULT_MARK} ({now.astimezone().strftime('%Y-%m-%d %H:%M')}) ────"
    return head + "\n" + "\n".join(lines)


def write_back(service, cal_id: str, ev: Dict[str, Any], block: str, status: str, extra: Optional[Dict[str, str]] = None) -> None:
    """설명란 = 사용자 입력(이전 결과·'재실행: 예' 줄 제거) + 새 결과 블록, 숨은 속성에 상태"""
    user_part = _plain(strip_result(ev.get("description", "")))
    user_part = re.sub(r"(?m)^\s*재실행\s*[:：].*$\n?", "", user_part).rstrip()
    body = {"description": (user_part + "\n\n" + block).strip()[:8000],
            "extendedProperties": {"private": {"adf_status": status, **(extra or {})}}}
    service.events().patch(calendarId=cal_id, eventId=ev["id"], body=body).execute()


def mark_running(service, cal_id: str, ev: Dict[str, Any]) -> None:
    service.events().patch(calendarId=cal_id, eventId=ev["id"],
                           body={"extendedProperties": {"private": {"adf_status": "running",
                                                                    "adf_started": dt.datetime.now(dt.timezone.utc).isoformat()}}}).execute()


# ---------------------------------------------------------------- 한 건 처리
@dataclass
class RunReport:
    processed: List[Dict[str, Any]] = field(default_factory=list)
    skipped: str = ""
    pending_comments: List[Dict[str, Any]] = field(default_factory=list)


def process_event(service, cal_id: str, ev: Dict[str, Any], title: Dict[str, str], now: dt.datetime,
                  api_key: Optional[str], mock: bool = False, upload: bool = True) -> Dict[str, Any]:
    from core import pipeline
    from core.job import JobContext
    mode_ko = MODE_KO.get(title["mode"], title["mode"])
    rec: Dict[str, Any] = {"event_id": ev["id"], "title": ev.get("summary", ""), "mode": title["mode"], "at": now.isoformat()}
    try:
        spec = spec_from_event(title, parse_fields(ev.get("description", "")), api_key, mock)
    except SpecError as e:
        block = result_block(now, False, [f"❌ 시작 못 함 ({mode_ko}): {e}",
                                          "고친 뒤 설명란에 '재실행: 예' 한 줄을 넣으면 다음 주기(10분 안)에 다시 만들어요."])
        write_back(service, cal_id, ev, block, "failed")
        return {**rec, "status": "failed", "error": str(e)}

    mark_running(service, cal_id, ev)
    job = JobContext.create(app="calendar", event_id=ev["id"], title=ev.get("summary", ""))
    for i, p in enumerate(spec.media_paths):        # 원본 inbox는 그대로 두고 작업 폴더로 복사
        dst = os.path.join(job.uploads_dir, f"{i + 1:02d}_{os.path.basename(p)}")
        os.makedirs(job.uploads_dir, exist_ok=True)
        shutil.copy(p, dst)
        spec.media_paths[i] = dst
    rec["job_id"] = job.job_id
    try:
        scenes = pipeline.plan(spec, job)
        missing = pipeline.missing_media(scenes)
        if missing and not mock:
            raise SpecError(f"씬 {', '.join(map(str, missing))}에 사진이 없어요 (사진폴더에 사진을 더 넣어 주세요)")
        res = pipeline.render(spec, job, scenes)
        from core.render.ffmpeg_assemble import probe
        dur = probe(res.final_path).get("duration") or 0
        engine = (scenes[0].get("script_engine") if scenes else "") or "-"
        lines = []
        extra = {"adf_job": job.job_id}
        if spec.mode == "book":
            lines += getattr(spec, "notes", [])
            lines += [f"🖼️ {v}" for v in ((scenes[0].get("book") or {}).get("visuals") or [])] if scenes else []
            texts = pipeline.upload_texts(spec, res.shorts_copy, hook=(scenes[0].get("hook_title") or ""))
            lines += ["📚 시골책방 채널 미연결 → 로컬 렌더 + 첫 댓글 초안 저장 (업로드 안 함)",
                      f"영상 {dur:.1f}초 · 대본 엔진 {engine} · 파일 {os.path.relpath(res.final_path, ROOT)}",
                      "", f"제목 초안: {texts['title']}", "설명란 초안:", texts["desc"],
                      "", "💬 첫 댓글 초안 (outputs/youtube/comment_drafts.json):", res.comment]
        else:
            texts = pipeline.upload_texts(spec, res.shorts_copy, hook=(scenes[0].get("hook_title") or ""))
            if upload:
                from core import youtube_upload as yu
                up = yu.upload_short(res.final_path, texts["title"], texts["desc"], mode=spec.mode, thumb_path=res.thumb_path,
                                     comment=texts["comment"], privacy="private",
                                     tags=texts.get("tags") or pipeline.UPLOAD_TAGS.get(spec.mode, []),
                                     category_id=yu.CATEGORY_PEOPLE)
                extra["adf_video"] = up["video_id"]
                rec["video_id"] = up["video_id"]
                lines += [f"✅ '{up['channel']}' 채널에 비공개 업로드: {up['url']}", f"스튜디오: {up['studio_url']}",
                          f"영상 {dur:.1f}초 · 대본 엔진 {engine} · 썸네일 {'등록' if up['thumbnail_ok'] else '미등록'}",
                          ("💬 첫 댓글: 비공개라 대기 중 → 스튜디오에서 공개(또는 일부 공개)로 바꾸면 자동 등록"
                           if up.get("comment_pending") else "💬 첫 댓글 등록됨" if up.get("comment_ok") else "💬 첫 댓글 없음")]
                lines += [f"⚠️ {w}" for w in up.get("warnings") or []]
            else:
                lines += [f"✅ 렌더 완료 (업로드 안 함) · 영상 {dur:.1f}초 · 파일 {os.path.relpath(res.final_path, ROOT)}"]
            lines += ["", f"제목: {texts['title']}", "", "💬 첫 댓글:", texts["comment"]]
        write_back(service, cal_id, ev, result_block(now, True, lines), "done", extra)
        return {**rec, "status": "done", "final": res.final_path, "duration": round(dur, 1)}
    except Exception as e:
        logger.exception(f"자동 제작 실패: {ev.get('summary')}")
        hint = str(e)[:400]
        write_back(service, cal_id, ev, result_block(now, False, [f"❌ 제작 실패 ({mode_ko}): {hint}",
                                                                  f"작업 폴더: outputs/jobs/{job.job_id}",
                                                                  "고친 뒤 설명란에 '재실행: 예'를 넣으면 다시 만들어요."]),
                   "failed", {"adf_job": job.job_id})
        return {**rec, "status": "failed", "error": hint}


def run_once(now: Optional[dt.datetime] = None, service=None, api_key: Optional[str] = None, mock: bool = False,
             upload: bool = True, busy_check: Callable[[], bool] = render_busy) -> RunReport:
    """한 번 실행: 대기 첫 댓글 확인 → 처리할 일정 1건 (하루 한도·렌더 중이면 건너뜀)"""
    now = now or dt.datetime.now(dt.timezone.utc)
    report = RunReport()
    if upload:
        try:
            from core import youtube_upload as yu
            report.pending_comments = yu.post_pending_comments()      # 공개로 바뀐 영상의 첫 댓글 등록
        except Exception as e:
            logger.warning(f"대기 첫 댓글 확인 실패: {e}")
    service = service or cs.build_service()
    cal = cs.saved_calendar()
    state = load_state()
    events = cs.list_events(service, cal["id"], now - dt.timedelta(hours=LOOKBACK_HOURS), now + dt.timedelta(minutes=1))
    due = due_events(events, now, state)
    if not due:
        report.skipped = "처리할 일정 없음"
        return report
    today = now.astimezone().strftime("%Y-%m-%d")
    done_today = sum(1 for r in state["runs"] if r.get("at", "").startswith(today) and r.get("status") == "done")
    if done_today >= max_per_day():
        report.skipped = f"하루 한도 {max_per_day()}건 도달 (CALENDAR_MAX_PER_DAY)"
        return report
    if busy_check():
        report.skipped = "다른 렌더가 진행 중 (다음 주기에 처리)"
        return report
    ev, title = due[0]
    rec = process_event(service, cal["id"], ev, title, now, api_key, mock=mock, upload=upload)
    rec["at"] = now.astimezone().isoformat()
    state["events"][ev["id"]] = {"status": rec["status"], "job_id": rec.get("job_id"), "at": rec["at"]}
    state["runs"] = (state["runs"] + [rec])[-200:]
    save_state(state)
    report.processed.append(rec)
    return report
