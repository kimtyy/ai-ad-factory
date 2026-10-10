"""
core/youtube_upload.py - 완성 쇼츠를 내 유튜브 채널에 업로드 (YouTube Data API v3, OAuth)

- 인증: 레이더용 API 키로는 업로드 불가 → OAuth. 처음 한 번 PC에서 `python scripts/youtube_auth.py` 실행
  (브라우저에서 '여행을 떠나자' 채널을 고르고 허용) → 토큰을 secrets/youtube_token.json에 저장 (git 제외)
  필요한 파일: Google Cloud 콘솔 > API 및 서비스 > 사용자 인증 정보 > OAuth 클라이언트 ID(데스크톱 앱) JSON
  → secrets/client_secret.json (또는 .env의 YOUTUBE_CLIENT_SECRETS 경로)
- 채널 확인: .env의 YOUTUBE_EXPECTED_CHANNEL(예: 여행을 떠나자)과 로그인 채널 이름이 다르면 업로드하지 않음
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
TOKEN_PATH = os.path.join(SECRETS_DIR, "youtube_token.json")
THUMB_MAX_BYTES = 2 * 1024 * 1024        # thumbnails.set 한도 2MB
TITLE_MAX = 100
DESC_MAX = 5000
PRIVACY_KO = {"public": "공개", "unlisted": "일부 공개", "private": "비공개"}
CATEGORY_TRAVEL = "19"                   # Travel & Events
CATEGORY_PEOPLE = "22"                   # People & Blogs


class YouTubeUploadError(RuntimeError):
    pass


def client_secrets_path() -> str:
    return os.getenv("YOUTUBE_CLIENT_SECRETS", "").strip() or os.path.join(SECRETS_DIR, "client_secret.json")


def is_authorized() -> bool:
    return os.path.exists(TOKEN_PATH)


def load_credentials():
    """저장된 토큰 → 자격 증명 (만료되면 갱신해 다시 저장). 없으면 YouTubeUploadError"""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    if not os.path.exists(TOKEN_PATH):
        raise YouTubeUploadError("유튜브 계정 연결이 안 되어 있어요. PC에서 `python scripts/youtube_auth.py`를 한 번 실행해 채널을 연결해 주세요.")
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        else:
            raise YouTubeUploadError("유튜브 연결이 만료됐어요. PC에서 `python scripts/youtube_auth.py`를 다시 실행해 주세요.")
    return creds


def authorize_interactive() -> Dict[str, str]:
    """PC 브라우저로 OAuth 동의 → 토큰 저장 → 연결된 채널 정보 (scripts/youtube_auth.py에서 호출)"""
    from google_auth_oauthlib.flow import InstalledAppFlow
    secrets = client_secrets_path()
    if not os.path.exists(secrets):
        raise YouTubeUploadError(f"OAuth 클라이언트 파일이 없어요: {secrets}")
    flow = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", authorization_prompt_message="")
    os.makedirs(SECRETS_DIR, exist_ok=True)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    return my_channel(build_service(creds))


# 테스트에서 가짜 서비스로 바꿔 끼우는 지점
_service_factory: Optional[Callable[[], Any]] = None


def build_service(creds=None):
    if _service_factory is not None:
        return _service_factory()
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds or load_credentials(), cache_discovery=False)


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


def upload_short(video_path: str, title: str, description: str, *, thumb_path: Optional[str] = None,
                 comment: str = "", privacy: str = "private", tags: Optional[List[str]] = None,
                 category_id: str = CATEGORY_TRAVEL, expected_channel: Optional[str] = None,
                 progress: Optional[Callable[[float], None]] = None) -> Dict[str, Any]:
    """
    → {video_id, url, studio_url, channel, privacy, thumbnail_ok, comment_ok, comment_id, warnings}
    expected_channel(기본 .env YOUTUBE_EXPECTED_CHANNEL)과 다른 채널이면 올리지 않음
    """
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

    service = build_service()
    channel = my_channel(service)
    expected = (expected_channel if expected_channel is not None else os.getenv("YOUTUBE_EXPECTED_CHANNEL", "")).strip()
    if expected and channel["title"].strip() != expected:
        raise YouTubeUploadError(f"연결된 채널이 '{channel['title']}'이에요 ('{expected}' 아님). "
                                 f"`python scripts/youtube_auth.py`로 '{expected}' 채널을 다시 연결해 주세요.")

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
           "comments_url": f"https://studio.youtube.com/video/{vid}/comments", "channel": channel["title"],
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
