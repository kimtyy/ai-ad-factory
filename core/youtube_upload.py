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
- 업로드 순서: videos.insert(재개 가능 업로드) → thumbnails.set(썸네일) → commentThreads.insert(첫 댓글)
  썸네일·댓글이 실패해도 영상 업로드는 성공으로 두고 경고만 돌려줌

API 제약 (2026-10 확인):
- 댓글 '고정(핀)'은 API에 없음 → 댓글은 자동으로 달고, 고정은 YouTube 앱·스튜디오에서 한 번 눌러야 함
- 2020-07-28 이후 만든 Google Cloud 프로젝트는 '감사(audit)' 전까지 API로 올린 영상이 비공개로 잠김
  (privacyStatus를 public으로 보내도 비공개) → 스튜디오에서 공개로 바꾸거나 프로젝트 감사 신청
- 맞춤 썸네일은 채널 인증(전화번호)·쇼츠 썸네일 권한이 있어야 적용됨. 거절되면 경고로 알림 (영상 첫 0.3초가 썸네일 화면)
"""
import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ad_factory.youtube_upload")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]
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
    return os.path.join(SECRETS_DIR, f"token_{channel_key(mode)}.json")


def expected_channel(mode: str) -> str:
    """해당 모드의 기대 채널명 (.env YOUTUBE_CHANNEL_TRAVEL / _STORE / _SHOPPING)"""
    return os.getenv(f"YOUTUBE_CHANNEL_{channel_key(mode).upper()}", "").strip()


def auth_command(mode: str) -> str:
    return f"python scripts/youtube_auth.py --mode {channel_key(mode)}"


def client_secrets_path() -> str:
    return os.getenv("YOUTUBE_CLIENT_SECRETS", "").strip() or os.path.join(SECRETS_DIR, "client_secret.json")


def is_authorized(mode: str) -> bool:
    return os.path.exists(token_path(mode))


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
    return {**ch, "expected": expected, "saved_to": path}


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


def thumbnail_for_upload(thumb_png: str) -> str:
    """thumbnails.set 2MB 한도: PNG가 크면 같은 크기의 고화질 JPEG 사본(_upload.jpg)을 만들어 사용 (원본 PNG는 그대로)"""
    if os.path.getsize(thumb_png) <= THUMB_MAX_BYTES:
        return thumb_png
    from PIL import Image
    img = Image.open(thumb_png).convert("RGB")
    out = os.path.splitext(thumb_png)[0] + "_upload.jpg"
    for q in (92, 88, 84, 80, 75, 70):
        img.save(out, "JPEG", quality=q, optimize=True, progressive=True)
        if os.path.getsize(out) <= THUMB_MAX_BYTES:
            break
    return out


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
                 progress: Optional[Callable[[float], None]] = None) -> Dict[str, Any]:
    """
    mode(travel/owner/commerce 또는 travel/store/shopping)의 토큰으로 업로드
    → {video_id, url, studio_url, channel, channel_key, privacy, thumbnail_ok, comment_ok, comment_id, warnings}
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
           "comment_ok": False, "comment_id": "", "warnings": []}
    logger.info(f"유튜브 업로드 완료: {out['url']} ({channel['title']}, {out['privacy']})")
    if privacy != "private" and out["privacy"] == "private":
        out["warnings"].append("API 프로젝트가 아직 Google 감사(audit)를 받지 않아 비공개로 올라갔어요. 스튜디오에서 공개로 바꿔 주세요.")

    if thumb_path and os.path.exists(thumb_path):
        try:
            service.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumbnail_for_upload(thumb_path))).execute()
            out["thumbnail_ok"] = True
        except HttpError as e:
            out["warnings"].append("썸네일 등록이 거절됐어요 (채널 인증·쇼츠 맞춤 썸네일 권한 필요). "
                                   f"영상 첫 화면이 썸네일이라 피드에는 그대로 보여요. (HTTP {getattr(e.resp, 'status', '?')})")

    if comment and comment.strip():
        try:
            c = service.commentThreads().insert(part="snippet", body={"snippet": {
                "videoId": vid, "topLevelComment": {"snippet": {"textOriginal": comment.strip()[:9000]}}}}).execute()
            out["comment_ok"], out["comment_id"] = True, c.get("id", "")
        except HttpError as e:
            out["warnings"].append(f"첫 댓글 등록 실패 (HTTP {getattr(e.resp, 'status', '?')}). 스튜디오에서 직접 달아 주세요.")
    return out
