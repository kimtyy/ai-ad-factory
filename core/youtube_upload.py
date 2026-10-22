"""
core/youtube_upload.py - 완성 쇼츠를 제작 모드별 유튜브 채널에 업로드 (YouTube Data API v3, OAuth)

제작 모드 → 채널 (토큰 파일 / .env 기대 채널명):
  숙소 제휴(travel)   → secrets/token_travel.json   / YOUTUBE_CHANNEL_TRAVEL   (예: 여행을 떠나자)
  사장님(owner)       → secrets/token_store.json    / YOUTUBE_CHANNEL_STORE    (예: 샵스토리)
  쇼핑 제휴(commerce) → secrets/token_shopping.json / YOUTUBE_CHANNEL_SHOPPING

- 인증: 채널마다 PC에서 한 번 `python scripts/youtube_auth.py --mode travel|store|shopping`
  (브라우저에서 해당 채널을 고르고 허용). 기대 채널명과 다르면 토큰을 저장하지 않음
  필요한 파일: Google Cloud 콘솔 > OAuth 클라이언트 ID(데스크톱 앱) JSON → secrets/client_secret.json
  (또는 .env의 YOUTUBE_CLIENT_SECRETS 경로). secrets/는 git 제외
- 안전 장치: 업로드 직전 API로 실제 채널명을 확인해 그 모드의 기대 채널명과 다르면 중단.
  기대 채널명이 비어 있으면 비교할 기준이 없으므로 업로드하지 않음
- 업로드 순서: videos.insert(재개 가능 업로드, selfDeclaredMadeForKids=False) → videos.list(status)로 아동용 아님·댓글 가능 확인
  → thumbnails.set(썸네일) → commentThreads.insert(첫 댓글, 채널 본인 계정). 첫 댓글은 youtube.force-ssl 권한 필요
  썸네일·댓글이 실패해도 영상 업로드는 성공으로 두고 원인별 경고만 돌려줌
- 비공개 영상에는 댓글을 달 수 없음 (2026-10-11 실측: 처리 완료·아동용 아님·force-ssl 권한이어도 403 forbidden)
  → 비공개 업로드의 첫 댓글은 대기열(outputs/youtube/pending_comments.json)에 두고, 공개·일부 공개로 바뀌면
    post_pending_comments()가 등록 (완료 화면 버튼 · `python scripts/post_pending_comments.py` · 레이더 일일 작업)

API 제약 (2026-10 확인):
- 댓글 '고정(핀)'은 API에 없음 → 댓글은 자동으로 달고, 고정은 YouTube 앱·스튜디오에서 한 번 눌러야 함
- 2020-07-28 이후 만든 Google Cloud 프로젝트는 '감사(audit)' 전까지 API로 올린 영상이 비공개로 잠김
  (privacyStatus를 public으로 보내도 비공개) → 스튜디오에서 공개로 바꾸거나 프로젝트 감사 신청
- 맞춤 썸네일은 채널 인증(전화번호)·쇼츠 썸네일 권한이 있어야 적용됨. 거절되면 경고로 알림 (영상 첫 0.3초가 썸네일 화면)
"""
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ad_factory.youtube_upload")

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
COMMENT_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"     # commentThreads.insert (첫 댓글)
SCOPES = [UPLOAD_SCOPE, COMMENT_SCOPE]
COMMENT_MAX = 9000
COMMENT_RETRY_WAIT = 3.0                  # 업로드 직후 영상이 아직 안 잡혀 404/5xx면 한 번 더
PENDING_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs", "youtube",
                            "pending_comments.json")    # 비공개라 못 단 첫 댓글 (공개 전환 후 등록)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS_DIR = os.path.join(ROOT, "secrets")
THUMB_MAX_BYTES = 2 * 1024 * 1024        # thumbnails.set 한도 2MB
TITLE_MAX = 100
DESC_MAX = 5000
PRIVACY_KO = {"public": "공개", "unlisted": "일부 공개", "private": "비공개"}
CATEGORY_TRAVEL = "19"                   # Travel & Events
CATEGORY_PEOPLE = "22"                   # People & Blogs

# 제작 모드(app의 pipeline_mode 또는 채널 키) → 채널 키
CHANNEL_KEYS = ("travel", "store", "shopping")
MODE_TO_CHANNEL = {"travel": "travel", "owner": "store", "commerce": "shopping",
                   "store": "store", "shopping": "shopping"}
CHANNEL_LABEL = {"travel": "숙소 제휴", "store": "사장님 매장 홍보", "shopping": "쇼핑 제휴"}


class YouTubeUploadError(RuntimeError):
    pass


def channel_key(mode: str) -> str:
    key = MODE_TO_CHANNEL.get((mode or "").strip().lower())
    if not key:
        raise YouTubeUploadError(f"알 수 없는 제작 모드예요: {mode!r} (travel / store / shopping)")
    return key


def token_path(mode: str) -> str:
    path = os.path.join(SECRETS_DIR, f"token_{channel_key(mode)}.json")
    _materialize_from_env(path, f"YOUTUBE_TOKEN_{channel_key(mode).upper()}")
    return path


def _materialize_from_env(path: str, env_name: str) -> None:
    """클라우드처럼 secrets/ 파일을 둘 수 없는 환경: 환경변수(Streamlit Secrets)에 JSON 문자열로 넣어 둔 값을 파일로 풀어 쓴다.
    파일이 이미 있으면 건드리지 않되, Secrets의 갱신 토큰(refresh_token)이 파일과 '다르면' 새로 로그인해 Secrets를 교체한 것이므로
    Secrets가 이긴다 (옛 만료 토큰 파일이 남아 새 토큰을 무시하던 문제). 같은 로그인에서 액세스 토큰만 갱신된 파일은 그대로 둔다.
    값은 로그에 남기지 않는다."""
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return
    try:
        import json
        env_data = json.loads(raw)                       # JSON 형식 확인 (아니면 무시)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    file_data = json.load(f)
            except (OSError, ValueError):
                file_data = {}
            same_login = bool(env_data.get("refresh_token")) and env_data.get("refresh_token") == file_data.get("refresh_token")
            if same_login or not env_data.get("refresh_token"):
                return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw)
    except (ValueError, OSError):
        pass


def expected_channel(mode: str) -> str:
    """해당 모드의 기대 채널명 (.env YOUTUBE_CHANNEL_TRAVEL / _STORE / _SHOPPING)"""
    return os.getenv(f"YOUTUBE_CHANNEL_{channel_key(mode).upper()}", "").strip()


def auth_command(mode: str) -> str:
    return f"python scripts/youtube_auth.py --mode {channel_key(mode)}"


def client_secrets_path() -> str:
    path = os.getenv("YOUTUBE_CLIENT_SECRETS", "").strip() or os.path.join(SECRETS_DIR, "client_secret.json")
    _materialize_from_env(path, "YOUTUBE_CLIENT_SECRET_JSON")      # 클라우드: JSON 문자열을 Secrets에 직접
    return path


def is_authorized(mode: str) -> bool:
    return os.path.exists(token_path(mode))


def granted_scopes(mode: str) -> Optional[List[str]]:
    """그 모드 토큰에 저장된 권한 목록 (토큰이 없거나 기록이 없으면 None = 알 수 없음)"""
    import json
    try:
        with open(token_path(mode), encoding="utf-8") as f:
            scopes = json.load(f).get("scopes")
    except (OSError, ValueError):
        return None
    return scopes.split() if isinstance(scopes, str) else scopes


def has_comment_scope(mode: str) -> bool:
    """첫 댓글 권한(youtube.force-ssl)이 토큰에 있는지. 기록이 없으면 일단 시도 (API가 거절하면 원인 안내)"""
    scopes = granted_scopes(mode)
    return scopes is None or COMMENT_SCOPE in scopes


def load_credentials(mode: str):
    """그 모드의 토큰 → 자격 증명 (만료되면 갱신해 다시 저장). 없으면 YouTubeUploadError"""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    path = token_path(mode)
    if not os.path.exists(path):
        raise YouTubeUploadError(f"{CHANNEL_LABEL[channel_key(mode)]} 채널이 연결되지 않았어요. "
                                 f"PC에서 `{auth_command(mode)}`를 한 번 실행해 주세요.")
    creds = Credentials.from_authorized_user_file(path, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        else:
            raise YouTubeUploadError(f"유튜브 연결이 만료됐어요. PC에서 `{auth_command(mode)}`를 다시 실행해 주세요.")
    return creds


def authorize_interactive(mode: str) -> Dict[str, str]:
    """
    PC 브라우저로 OAuth 동의 → 연결된 채널 확인 → 기대 채널명과 같을 때만 그 모드의 토큰 파일로 저장.
    기대 채널명이 비어 있으면 저장하고 채널명을 돌려줌 (.env에 적도록 안내, 그 전까지 업로드는 막힘)
    → {id, title, expected, saved_to}
    """
    from google_auth_oauthlib.flow import InstalledAppFlow
    key = channel_key(mode)
    secrets = client_secrets_path()
    if not os.path.exists(secrets):
        raise YouTubeUploadError(f"OAuth 클라이언트 파일이 없어요: {secrets}")
    flow = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent select_account", authorization_prompt_message="")
    ch = my_channel(build_service(creds, mode=key))
    expected = expected_channel(key)
    if expected and ch["title"].strip() != expected:
        raise YouTubeUploadError(f"고른 채널이 '{ch['title']}'이에요. {CHANNEL_LABEL[key]} 채널은 '{expected}'여야 해서 "
                                 "토큰을 저장하지 않았어요. 다시 실행해 올바른 채널을 골라 주세요.")
    os.makedirs(SECRETS_DIR, exist_ok=True)
    path = token_path(key)
    with open(path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    # 동의 화면에서 일부 권한을 끄고 허용할 수 있음 → 댓글 권한이 빠졌으면 알려 줌 (업로드는 가능)
    granted = getattr(creds, "granted_scopes", None) or getattr(creds, "scopes", None)
    comment_ok = granted is None or COMMENT_SCOPE in granted
    return {**ch, "expected": expected, "saved_to": path, "comment_scope": comment_ok}


# 테스트에서 가짜 서비스로 바꿔 끼우는 지점: _service_factory(channel_key) → service
_service_factory: Optional[Callable[[str], Any]] = None


def build_service(creds=None, mode: Optional[str] = None):
    if _service_factory is not None:
        return _service_factory(channel_key(mode) if mode else "")
    from googleapiclient.discovery import build
    if creds is None:
        if not mode:
            raise YouTubeUploadError("업로드할 제작 모드가 필요해요.")
        creds = load_credentials(mode)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def my_channel(service) -> Dict[str, str]:
    res = service.channels().list(part="snippet", mine=True).execute()
    items = res.get("items") or []
    if not items:
        raise YouTubeUploadError("연결된 계정에 유튜브 채널이 없어요.")
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}


COVER_NAME = "thumbnail.jpg"


def export_cover_jpg(src: str, dst: Optional[str] = None) -> str:
    """
    유튜브 업로드·미리보기용 커버 이미지를 독립 파일(기본: 원본과 같은 폴더의 thumbnail.jpg)로 저장.
    sRGB JPEG, thumbnails.set 한도(2MB) 이하가 되도록 품질을 낮춰 가며 저장한다 (원본 PNG는 그대로 둠).
    """
    from PIL import Image
    out = dst or os.path.join(os.path.dirname(os.path.abspath(src)), COVER_NAME)
    img = Image.open(src).convert("RGB")
    for q in (95, 92, 88, 84, 80, 75, 70, 60):
        img.save(out, "JPEG", quality=q, optimize=True, progressive=True)
        if os.path.getsize(out) <= THUMB_MAX_BYTES:
            break
    return out


def thumbnail_for_upload(thumb: str) -> str:
    """thumbnails.set에 올릴 파일: 항상 독립 커버 이미지(thumbnail.jpg). 이미 만들어 둔 것이 최신이면 그대로 사용"""
    cover = os.path.join(os.path.dirname(os.path.abspath(thumb)), COVER_NAME)
    if os.path.basename(thumb) == COVER_NAME and os.path.getsize(thumb) <= THUMB_MAX_BYTES:
        return thumb
    if os.path.exists(cover) and os.path.getmtime(cover) >= os.path.getmtime(thumb) and os.path.getsize(cover) <= THUMB_MAX_BYTES:
        return cover
    return export_cover_jpg(thumb, cover)


def _http_reason(e) -> str:
    """HttpError 본문의 errors[0].reason (commentsDisabled·insufficientPermissions·videoNotFound 등)"""
    import json
    try:
        err = json.loads((getattr(e, "content", b"") or b"{}").decode("utf-8", "replace")).get("error") or {}
        return str(((err.get("errors") or [{}])[0]).get("reason") or "")
    except (ValueError, AttributeError):
        return ""


COMMENT_FAIL_KO = {
    "commentsDisabled": "이 영상은 댓글이 꺼져 있어요 (아동용 지정 또는 채널 기본 댓글 설정). "
                        "스튜디오 > 설정 > 업로드 기본설정 > 고급 설정을 확인해 주세요.",
    "insufficientPermissions": "댓글 권한(youtube.force-ssl)이 없는 연결이에요. PC에서 `{cmd}`로 다시 연결해 주세요.",
    "forbidden": "유튜브가 댓글 작성을 거절했어요 (채널 댓글 권한·커뮤니티 제한 확인).",
    "videoNotFound": "업로드 직후라 영상이 아직 처리 중이에요. 잠시 뒤 스튜디오에서 댓글을 달아 주세요.",
}


def made_for_kids_status(service, vid: str) -> Dict[str, Any]:
    """업로드된 영상의 아동용 여부 확인 (madeForKids=True면 댓글이 막힘) → {made_for_kids, self_declared}"""
    items = service.videos().list(part="status", id=vid).execute().get("items") or []
    st = (items[0].get("status") if items else None) or {}
    return {"made_for_kids": st.get("madeForKids"), "self_declared": st.get("selfDeclaredMadeForKids")}


def build_description(hook: str, link: str = "", disclosure: str = "", tags: Optional[List[str]] = None) -> str:
    parts = [hook.strip()] if hook and hook.strip() else []
    if link:
        parts.append(f"🔗 예약·구매 링크: {link}")
    if disclosure:
        parts.append(disclosure.strip())
    hashtags = " ".join("#" + t.strip().lstrip("#").replace(" ", "") for t in (tags or []) if t.strip())
    parts.append((hashtags + " #Shorts").strip())
    return "\n\n".join(parts)[:DESC_MAX]


def upload_short(video_path: str, title: str, description: str, *, mode: str, thumb_path: Optional[str] = None,
                 comment: str = "", privacy: str = "private", tags: Optional[List[str]] = None,
                 category_id: str = CATEGORY_TRAVEL,
                 progress: Optional[Callable[[float], None]] = None,
                 sleep: Optional[Callable[[float], None]] = None) -> Dict[str, Any]:
    """
    mode(travel/owner/commerce 또는 travel/store/shopping)의 토큰으로 업로드
    → {video_id, url, studio_url, channel, channel_key, privacy, made_for_kids, thumbnail_ok, comment_ok, comment_id,
       comment_url, warnings}
    업로드 직전 API로 확인한 채널명이 그 모드의 기대 채널명과 다르면(또는 기대 채널명이 비어 있으면) 중단
    """
    key = channel_key(mode)
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    if not os.path.exists(video_path):
        raise YouTubeUploadError(f"영상 파일이 없어요: {video_path}")
    if privacy not in PRIVACY_KO:
        raise YouTubeUploadError(f"공개 범위 값이 올바르지 않아요: {privacy}")
    title = (title or "").strip()
    if not title:
        raise YouTubeUploadError("제목을 입력해 주세요.")
    if "#shorts" not in title.lower() and len(title) + 8 <= TITLE_MAX:
        title += " #Shorts"
    title = title[:TITLE_MAX]

    expected = expected_channel(key)
    if not expected:
        raise YouTubeUploadError(f"{CHANNEL_LABEL[key]} 채널 이름이 .env에 없어요 (YOUTUBE_CHANNEL_{key.upper()}). "
                                 "엉뚱한 채널에 올라가지 않도록 채널 이름을 적어야 업로드할 수 있어요.")
    service = build_service(mode=key)
    channel = my_channel(service)
    if channel["title"].strip() != expected:
        logger.warning(f"업로드 중단: {key} 토큰의 채널 '{channel['title']}' ≠ 기대 '{expected}'")
        raise YouTubeUploadError(f"업로드 중단: 연결된 채널이 '{channel['title']}'이에요 ({CHANNEL_LABEL[key]} 채널은 '{expected}'). "
                                 f"`{auth_command(key)}`로 '{expected}' 채널을 다시 연결해 주세요.")

    body = {
        "snippet": {"title": title, "description": (description or "")[:DESC_MAX], "categoryId": category_id,
                    "tags": [t for t in (tags or []) if t][:30], "defaultLanguage": "ko", "defaultAudioLanguage": "ko"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(video_path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = service.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status and progress:
            progress(status.progress())
    vid = resp["id"]
    out = {"video_id": vid, "url": f"https://youtube.com/shorts/{vid}", "studio_url": f"https://studio.youtube.com/video/{vid}/edit",
           "comments_url": f"https://studio.youtube.com/video/{vid}/comments", "channel": channel["title"], "channel_key": key,
           "privacy": (resp.get("status") or {}).get("privacyStatus", privacy), "thumbnail_ok": False,
           "comment_ok": False, "comment_id": "", "comment_url": "", "made_for_kids": None, "warnings": []}
    logger.info(f"유튜브 업로드 완료: {out['url']} ({channel['title']}, {out['privacy']})")
    if privacy != "private" and out["privacy"] == "private":
        out["warnings"].append("API 프로젝트가 아직 Google 감사(audit)를 받지 않아 비공개로 올라갔어요. 스튜디오에서 공개로 바꿔 주세요.")

    if thumb_path and os.path.exists(thumb_path):
        try:
            service.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumbnail_for_upload(thumb_path))).execute()
            out["thumbnail_ok"] = True
        except HttpError as e:
            out["warnings"].append("맞춤 썸네일 등록이 거절됐어요 (채널 인증·권한 필요). 쇼츠는 영상의 첫 프레임(0초)이 대표 썸네일이라 "
                                   f"피드에는 영상 안의 0초 화면이 그대로 보여요. (HTTP {getattr(e.resp, 'status', '?')})")
        except Exception as e:      # 네트워크·파일·인코딩 오류도 업로드 결과는 유지 (썸네일은 부가 기능)
            logger.warning(f"[썸네일 등록 실패] {type(e).__name__}: {e}")
            out["warnings"].append("맞춤 썸네일을 등록하지 못했어요. 쇼츠는 영상의 첫 프레임(0초)이 대표 썸네일로 자동 지정돼서 "
                                   f"업로드 자체에는 영향이 없어요. ({type(e).__name__})")

    # 아동용 아님(댓글 열림) 확인: 요청에 selfDeclaredMadeForKids=False를 보냈어도 채널 기본값·유튜브 판정으로 바뀔 수 있음
    comments_open = True
    try:
        kids = made_for_kids_status(service, vid)
        out["made_for_kids"] = kids["made_for_kids"]
        if kids["made_for_kids"]:
            comments_open = False
            out["warnings"].append("유튜브가 이 영상을 '아동용'으로 지정해 댓글이 막혔어요. 스튜디오 > 영상 세부정보 > 시청자층에서 "
                                   "'아동용이 아님'으로 바꾼 뒤 댓글을 달아 주세요.")
    except Exception as e:      # 확인 실패가 이미 끝난 업로드를 실패로 만들지 않게
        logger.warning(f"아동용 여부 확인 실패 ({e}) → 댓글은 그대로 진행")

    out["comment_pending"] = False
    if comment and comment.strip() and comments_open:
        text = comment.strip()[:COMMENT_MAX]
        if not has_comment_scope(key):
            out["warnings"].append(COMMENT_FAIL_KO["insufficientPermissions"].format(cmd=auth_command(key)))
        elif out["privacy"] == "private":
            # 비공개 영상엔 댓글 불가 → 대기열에 두고 공개·일부 공개로 바뀌면 등록 (API 호출로 쿼터 낭비 안 함)
            queue_pending_comment(key, vid, text)
            out["comment_pending"] = True
            logger.info(f"첫 댓글 대기: {vid} (비공개 → 공개·일부 공개 전환 후 등록)")
        else:
            _post_first_comment(service, vid, text, key, out, sleep or time.sleep)
    return out


DRAFTS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs", "youtube",
                           "comment_drafts.json")    # 업로드 연결 전 모드(시골책방)의 첫 댓글 초안 (업로드 연결 시 그대로 사용)


def queue_comment_draft(mode: str, video_path: str, text: str, job_id: str = "") -> Dict[str, Any]:
    """
    업로드 없이 첫 댓글 텍스트만 대기열에 저장 (같은 영상이면 덮어씀). 업로드 연결 후 video_id가 생기면
    pending_comments와 같은 흐름으로 등록하면 됨 → 저장된 항목 반환
    """
    import datetime
    import json
    try:
        with open(DRAFTS_PATH, encoding="utf-8") as f:
            items = json.load(f)
        items = items if isinstance(items, list) else []
    except (OSError, ValueError):
        items = []
    vp = os.path.abspath(video_path) if video_path else ""
    entry = {"mode": mode, "video_path": vp, "job_id": job_id, "text": (text or "").strip()[:COMMENT_MAX],
             "status": "draft", "saved_at": datetime.datetime.now().isoformat(timespec="seconds")}
    items = [d for d in items if d.get("video_path") != vp] + [entry]
    os.makedirs(os.path.dirname(DRAFTS_PATH), exist_ok=True)
    tmp = DRAFTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DRAFTS_PATH)
    return entry


def _load_pending() -> List[Dict[str, Any]]:
    import json
    try:
        with open(PENDING_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_pending(items: List[Dict[str, Any]]) -> None:
    import json
    os.makedirs(os.path.dirname(PENDING_PATH), exist_ok=True)
    tmp = PENDING_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PENDING_PATH)


def queue_pending_comment(mode: str, vid: str, text: str) -> None:
    import datetime
    items = [p for p in _load_pending() if p.get("video_id") != vid]
    items.append({"video_id": vid, "channel_key": channel_key(mode), "text": text,
                  "queued_at": datetime.datetime.now().isoformat(timespec="seconds")})
    _save_pending(items)


def pending_comment(vid: str) -> Optional[Dict[str, Any]]:
    return next((p for p in _load_pending() if p.get("video_id") == vid), None)


def post_pending_comments(only_video: Optional[str] = None, sleep: Optional[Callable[[float], None]] = None) -> List[Dict[str, Any]]:
    """
    대기 중인 첫 댓글 → 영상이 공개·일부 공개면 등록하고 대기열에서 뺌. 아직 비공개면 그대로, 삭제된 영상이면 버림
    → [{video_id, state: posted|waiting|kids|gone|failed, comment_url, warning}]
    """
    from googleapiclient.errors import HttpError
    results, keep, services = [], [], {}
    for p in _load_pending():
        vid, key = p.get("video_id", ""), p.get("channel_key", "")
        if only_video and vid != only_video:
            keep.append(p)
            continue
        r = {"video_id": vid, "state": "waiting", "comment_url": "", "warning": ""}
        try:
            svc = services.get(key) or services.setdefault(key, build_service(mode=key))
            items = svc.videos().list(part="status", id=vid).execute().get("items") or []
            status = (items[0].get("status") if items else None) or {}
            if not items:
                r["state"] = "gone"
            elif status.get("privacyStatus") == "private":
                keep.append(p)
            elif status.get("madeForKids"):
                r["state"], r["warning"] = "kids", "아동용으로 지정돼 댓글이 막혀 있어요 (스튜디오 > 시청자층 변경)"
                keep.append(p)
            else:
                out = {"warnings": []}
                _post_first_comment(svc, vid, p.get("text", ""), key, out, sleep or time.sleep)
                if out.get("comment_ok"):
                    r["state"], r["comment_url"] = "posted", out.get("comment_url", "")
                else:
                    r["state"], r["warning"] = "failed", " ".join(out["warnings"])
                    keep.append(p)
        except (HttpError, YouTubeUploadError) as e:
            r["state"], r["warning"] = "failed", str(e)[:200]
            keep.append(p)
        results.append(r)
    _save_pending(keep)
    return results


def _post_first_comment(service, vid: str, text: str, key: str, out: Dict[str, Any], sleep: Callable[[float], None]) -> None:
    """채널 본인 계정으로 첫 댓글. 업로드 직후 404/5xx면 한 번 더 시도, 실패하면 원인별 안내"""
    from googleapiclient.errors import HttpError
    body = {"snippet": {"videoId": vid, "topLevelComment": {"snippet": {"textOriginal": text}}}}
    for attempt in (1, 2):
        try:
            c = service.commentThreads().insert(part="snippet", body=body).execute()
            out["comment_ok"], out["comment_id"] = True, c.get("id", "")
            out["comment_url"] = f"https://www.youtube.com/watch?v={vid}&lc={out['comment_id']}" if out["comment_id"] else ""
            logger.info(f"첫 댓글 등록: {out['comment_id']} ({vid})")
            return
        except HttpError as e:
            code, reason = int(getattr(e.resp, "status", 0) or 0), _http_reason(e)
            if attempt == 1 and (code == 404 or code >= 500) and reason != "commentsDisabled":
                sleep(COMMENT_RETRY_WAIT)
                continue
            msg = COMMENT_FAIL_KO.get(reason, "").format(cmd=auth_command(key)) or "스튜디오에서 직접 달아 주세요."
            out["warnings"].append(f"첫 댓글 등록 실패 (HTTP {code or '?'}{', ' + reason if reason else ''}). {msg}")
            return
