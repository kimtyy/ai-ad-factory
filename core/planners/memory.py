"""
core/planners/memory.py - 🎉 모임·행사·추억 쇼츠 대본 (사진·영상 N개 = 컷 N개 = 카피 N줄, 1:1)

- 컷 수를 고정하지 않는다 (하한 없음, 상한 23): 올린 파일 개수 그대로 한 파일에 한 줄 (3개 → 3줄, 12개 → 12줄, 23개 → 23줄)
- 컷당 2.5초: 23컷 × 2.5초 = 57.5초로 유튜브 쇼츠·릴스 59초 한도 안에 들어온다 (영상 클립은 실제 길이, 합계가 넘으면 줄임)
- 사진·영상 속 장면(건배·식사·웃음·풍경·단체 사진)을 순서대로 보고, 도입 → 하이라이트 → 마무리 3단 흐름으로 줄을 쓴다
- 사실 가드: 이름·나이·관계·인원수·가격 같은 건 모임 이름에 적힌 것만 허용 (사진만 보고 단정하지 않음)
- 상호명·네이버 검색·메뉴판 대조는 쓰지 않는다 (매장 홍보 엔진과 완전히 분리)
렌더는 매장 '1컷 1카피 랩형' 정책(컷당 약 2초)을 그대로 쓴다 (core/pipeline.py가 spec.memory로 연결).
"""
import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.mode_policy import RAP_POLICY, clamp_line_to_policy

logger = logging.getLogger("ad_factory.memory")

MAX_CUTS = 23                         # 23컷 × 2.5초 = 57.5초 (쇼츠 59초 한도 안)
CUT_SEC = 2.5                         # 컷당 체류 시간
MAX_TOTAL_SEC = MAX_CUTS * CUT_SEC    # 57.5초 (공식 계산: 23컷 × 2.5초)

# 실제 렌더 길이(실측으로 맞춤) = 컷 진행 시간의 합 + 마지막 컷의 길이 − 진행 1컷분 + 맨 앞 커버 0.5초
#   · 컷 진행 시간 = 컷 길이 − 전환 겹침(XFADE_OVERLAP_SEC), 마지막 컷은 낭독 끝 + 1.0초 여운(무음이면 약 1.0초 더 김)
# 쇼츠·릴스 59초 한도 안에 들어오도록 컷이 가장 많을 때(23컷)만 컷당 시간을 2.5초에서 2.47초로 아주 조금 줄인다 (22컷 이하는 2.5초 그대로)
TARGET_MAX_SEC = 58.4
FIXED_OVERHEAD_SEC = 0.5 + 1.05       # 커버 0.5초 + 마지막 컷 여운(최대)
XFADE_OVERLAP_SEC = 0.12              # 컷 전환 디졸브 (모드 정책 MEMORY_POLICY.xfade_sec): 한 컷이 차지하는 진행 시간 = 컷 길이 − 겹침


def advance_budget(count: int) -> float:
    """컷 진행 시간의 합이 넘으면 안 되는 한도 (초)"""
    return TARGET_MAX_SEC - FIXED_OVERHEAD_SEC


def cut_sec(count: int) -> float:
    """사진 컷 1개의 진행 시간: 보통 2.5초, 22~23컷이면 한도에 맞춰 2.45초 안팎"""
    if count <= 0:
        return CUT_SEC
    return min(CUT_SEC, math.floor(advance_budget(count) / count * 100) / 100)
LINE_MIN, LINE_MAX = 6, 20            # 한 줄 길이 (공백 포함). 실측 낭독 약 0.11초/자 → 20자 ≈ 2.2초라 컷 2.5초 안에 끝남

TONES: Dict[str, Dict[str, Any]] = {
    "lively": {
        "label": "🌟 유쾌하고 활기차게", "use": "친구 모임, 동창회, 체육대회, 뒤풀이 파티",
        "font": "pop", "voice_tone": "lively", "bgm_mood": "업템포",
        "prompt": "웃음이 터지는 신나고 에너지 넘치는 톤. 짧고 리듬 있게 끊고, 느낌표를 적당히 써서 분위기를 띄웁니다.",
    },
    "warm": {
        "label": "🌿 따뜻하고 감동적이게", "use": "가족 모임, 부모님 생신, 힐링 여행, 추억",
        "font": "serif", "voice_tone": "calm", "bgm_mood": "어쿠스틱",
        "prompt": "다정하고 뭉클한 톤. 과장 없이 담담하게, 함께한 순간의 온기와 고마움이 느껴지게 씁니다.",
    },
    "formal": {
        "label": "🏆 기록·기념 공식형", "use": "동네 축제, 상인회 총회, 봉사활동, 워크숍",
        "font": "impact", "voice_tone": "calm", "bgm_mood": "미드템포",
        "prompt": "단정하고 믿음직한 기록 영상 톤. '함께했습니다' 같은 격식체 대신 깔끔한 해요체로, 행사의 활기와 의미를 또렷하게 남깁니다.",
    },
}
DEFAULT_TONE = "lively"

ROLE_LABELS_KO = {"intro": "🎬 도입", "moment": "🎉 하이라이트", "ending": "💝 마무리"}


def stage_counts(count: int):
    """3단 감정 흐름의 컷 수 → (도입, 마무리). 23컷이면 도입 1~3컷(약 7초) · 하이라이트 4~19컷 · 마무리 20~23컷(약 10초), 적을수록 비율을 줄임"""
    if count <= 1:
        return 0, 1
    if count <= 3:
        return 1, 1
    intro = 3 if count >= 15 else 2 if count >= 8 else 1
    end = 4 if count >= 20 else 3 if count >= 12 else 2 if count >= 8 else 1
    return intro, end


# 사진을 못 볼 때(Mock·엔진 실패)의 안전 문구: 사실 주장 없이 분위기만. 단계별 풀에서 순서대로 쓰고 같은 줄을 되풀이하지 않는다 (23컷까지 겹치지 않을 만큼 충분히)
FALLBACK_LINES: Dict[str, Dict[str, List[str]]] = {
    "lively": {
        "first": ["드디어 다 모였어요!", "오늘, 제대로 놀아볼까요?", "모이니까 벌써 신나요!"],
        "mid": ["웃음이 끊기질 않아요!", "이 분위기, 최고예요!", "함께라서 더 즐거워요!", "한 컷 한 컷이 다 웃겨요!",
                "시간 가는 줄 몰랐어요!", "이 순간을 놓칠 순 없죠!", "분위기가 점점 올라가요!", "다들 표정이 말해줘요!",
                "건배 소리가 울려 퍼져요!", "먹다 보니 또 웃음이에요!", "사진 찍는 손도 바빠요!", "아무도 지루할 틈이 없어요!",
                "텐션이 하늘을 찔러요!", "웃느라 배가 아플 지경이에요!", "이런 날은 또 안 올지도 몰라요!", "다 같이 외쳐 봐요!",
                "한 잔 더, 한 컷 더!", "수다가 끝나지 않아요!", "신나는 에너지가 번져요!", "역시 우리가 모이면 달라요!"],
        "last": ["다 같이 한 컷, 찰칵!", "오늘도 최고였어요!", "벌써 다음 모임이 기다려져요!", "다음에 또 모여요, 약속!"],
    },
    "warm": {
        "first": ["오랜만에 모두 모였어요.", "마음이 먼저 달려왔어요.", "그리운 얼굴들이 모였어요."],
        "mid": ["함께라서 따뜻한 시간이에요.", "웃음 속에 정이 가득해요.", "평범한 하루가 특별해졌어요.", "이 순간이 오래 남을 거예요.",
                "곁에 있어 고마운 사람들이에요.", "서로의 웃음이 닮아 있어요.", "그저 같이 있는 것만으로 좋아요.", "이런 날이 참 소중해요.",
                "도란도란 이야기가 이어져요.", "눈빛마다 반가움이 가득해요.", "따뜻한 한 끼가 마음을 채워요.", "말없이 웃어도 다 통해요.",
                "시간이 천천히 흘러가요.", "이 온기를 오래 간직하고 싶어요.", "함께한 자리가 선물 같아요.", "웃는 얼굴들이 참 고와요.",
                "마음 한켠이 따뜻해져요.", "늘 이렇게 모였으면 좋겠어요.", "소소한 행복이 모여 있어요.", "추억 한 장이 또 쌓여요."],
        "last": ["다 함께 모인 이 순간을 남겨요.", "오늘 하루, 오래 기억할게요.", "고마운 마음 가득 담아 둡니다.", "다음에도 이렇게 만나요."],
    },
    "formal": {
        "first": ["올해도 이렇게 모였어요.", "뜻깊은 하루가 시작됐어요.", "함께하는 자리가 열렸어요."],
        "mid": ["한자리에 모여 마음을 나눴어요.", "현장의 활기가 가득했어요.", "서로 손을 모은 시간이에요.", "참여한 모두가 주인공이에요.",
                "정성이 모여 행사가 완성돼요.", "웃음과 박수가 이어졌어요.", "이 장면을 기록으로 남겨요.", "함께한 하루가 쌓여 가요.",
                "준비한 마음이 빛을 냈어요.", "서로를 격려하는 자리예요.", "활동 현장이 분주하게 돌아가요.", "동네가 하나로 모인 순간이에요.",
                "수고한 손길이 곳곳에 보여요.", "화기애애한 분위기가 이어져요.", "함께 나눈 식사도 든든해요.", "참석자들의 표정이 밝아요.",
                "하나둘 정성이 더해져요.", "소중한 순간을 기록해요.", "이웃의 정이 느껴져요.", "행사의 열기가 끝까지 이어져요."],
        "last": ["다 함께 모여 기념으로 남깁니다.", "오늘의 기록을 이렇게 남깁니다.", "함께해 주셔서 고맙습니다.", "내년에도 힘차게 만나요."],
    },
}

# 사진만 보고 단정하면 안 되는 관계 호칭: 모임 이름에 적혀 있을 때만 허용
RELATION_WORDS = ("엄마", "아빠", "어머니", "아버지", "부모님", "할머니", "할아버지", "아들", "딸", "손주", "손자", "손녀",
                  "형", "누나", "언니", "오빠", "동생", "이모", "삼촌", "고모", "신랑", "신부", "사위", "며느리", "선생님")
# 추억 영상에 어울리지 않는 광고·판촉·과장 표현
PROMO_WORDS = ("할인", "무료", "쿠폰", "이벤트", "가격", "특가", "구독", "좋아요", "검색", "저장해", "예약", "문의", "역대급", "1등", "최저가")
EMOJI_RE = re.compile("[\U00010000-\U0010ffff☀-➿️]")
LAUGH_RE = re.compile(r"\^\^+|ㅎ{2,}|ㅋ{2,}|ㅠ{2,}|ㅜ{2,}")


def tone_of(key: Optional[str]) -> str:
    return key if key in TONES else DEFAULT_TONE


def role_for(index: int, count: int) -> str:
    intro, end = stage_counts(count)
    if index >= count - end:
        return "ending"
    return "intro" if index < intro else "moment"


def fallback_line(tone: str, index: int, count: int) -> str:
    """사진 없이도 안전한 문구: 단계(도입/하이라이트/마무리)별 풀에서 순서대로"""
    pool = FALLBACK_LINES[tone_of(tone)]
    intro, end = stage_counts(count)
    role = role_for(index, count)
    if role == "ending":
        if count == 1:
            return pool["last"][1]
        return pool["last"][min(index - (count - end), len(pool["last"]) - 1)]
    if role == "intro":
        return pool["first"][min(index, len(pool["first"]) - 1)]
    return pool["mid"][(index - intro) % len(pool["mid"])]


def clean_line(text: str) -> str:
    """이모티콘·웃음 표기·따옴표 제거, 공백 정리"""
    t = EMOJI_RE.sub("", str(text or ""))
    t = LAUGH_RE.sub("", t)
    t = t.replace("“", "").replace("”", "").replace('"', "").replace("'", "")
    return re.sub(r"\s+", " ", t).strip()


def find_violation(text: str, title: str = "") -> Optional[str]:
    """줄을 쓸 수 없는 이유 (없으면 None): 지어낸 사실·판촉 표현·사진으로 알 수 없는 호칭"""
    t = (text or "").strip()
    if not t:
        return "빈 줄"
    for w in PROMO_WORDS:
        if w in t:
            return f"판촉·광고 표현 '{w}'"
    digits = re.findall(r"\d+", t)
    if digits and not all(d in (title or "") for d in digits):
        return f"모임 이름에 없는 숫자 '{digits[0]}'"
    for w in RELATION_WORDS:
        if w in t and w not in (title or ""):
            return f"사진만으로 알 수 없는 호칭 '{w}'"
    return None


def _fit(text: str) -> str:
    return clamp_line_to_policy(text, RAP_POLICY)


def _scene(i: int, count: int, text: str) -> Dict[str, Any]:
    role = role_for(i, count)
    motions = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_down", "pan_up"]
    return {
        "scene_index": i + 1, "scene_number": i + 1, "role": role,
        "target_visual_role": role, "visual_keywords": [ROLE_LABELS_KO[role]],
        "duration_sec": CUT_SEC, "hook_title": "", "voiceover": text, "subtitle": text, "caption": text,
        "visual_description": ROLE_LABELS_KO[role], "image_prompt_en": "",
        "video_motion_prompt_en": motions[i % len(motions)], "focus_bottom": False,
    }


MEMORY_SYSTEM = (
    "당신은 동네 행사·가족 모임·친구 모임의 추억을 담는 세로형 숏폼(쇼츠) 카피라이터입니다. "
    "올라온 사진·영상 한 컷마다 그 장면에 실제로 보이는 모습을 짚는 짧은 한 줄을 쓰고, 전체가 하나의 추억 이야기로 이어지게 합니다. "
    "광고가 아니라 함께한 사람들에게 보내는 영상이므로 판촉·검색 유도·가게 홍보 표현은 쓰지 않습니다. 반드시 JSON만 출력합니다."
)


def _flow_guide(n: int, tone: str) -> str:
    intro, end = stage_counts(n)
    end_mood = {"warm": "훈훈한 여운과 감동", "lively": "유쾌한 여운", "formal": "기록과 감사"}[tone_of(tone)]
    if n == 1:
        return "컷이 1개라 도입이면서 마무리입니다."
    hi_a, hi_b = intro + 1, n - end
    mid = (f"컷 {hi_a}~{hi_b} = 하이라이트 (행사 활동, 맛있는 식사, 건배, 웃는 표정, 노래·게임 같은 즐거운 에피소드를 컷마다 서로 다른 장면으로 리드미컬하게 묘사)"
           if hi_b > hi_a else f"컷 {hi_a} = 하이라이트 (가장 즐거운 장면)" if hi_b == hi_a else "")
    parts = [f"컷 1{'~' + str(intro) if intro > 1 else ''} = 도입 (모임 장소에 도착하고 설레게 만나는 시작)", mid,
             f"컷 {n - end + 1}{'~' + str(n) if end > 1 else ''} = 마무리 (다 함께 모인 단체 사진이 있으면 그 컷, {end_mood}과 다음 모임을 기약하는 끝맺음)"]
    return " → ".join(p for p in parts if p)


def _prompt(title: str, tone: str, n: int) -> str:
    t = TONES[tone_of(tone)]
    name = (title or "").strip()
    many = ("\n- 컷이 많으니(15개 이상) 지루하거나 반복되지 않게, 같은 낱말·같은 어미·같은 구조를 3줄 이상 이어 쓰지 말고 컷마다 보이는 장면을 구체적으로 달리 쓰세요."
            if n >= 15 else "")
    return f"""
[모임·행사 정보]
- 모임·행사 이름: {name or '(적지 않음)'}
- 분위기 톤: {t['label']} ({t['use']}) — {t['prompt']}

[규칙: 컷 {n}개 = 줄 {n}개, 1:1]
- 위 사진·영상은 '컷 1'부터 '컷 {n}'까지 올라온 순서입니다. 정확히 {n}줄을 쓰세요 (더도 덜도 안 됩니다). 줄 i는 컷 i 한 장만 말합니다.
- 줄 i는 그 컷에 실제로 보이는 장면(건배, 식사, 웃는 표정, 풍경, 단체 사진, 노래, 게임, 걷는 모습 등)을 구체적으로 짚으세요. 다른 컷 이야기는 금지입니다.
- [3단 감정 흐름] {_flow_guide(n, tone)}{many}
- 한 줄은 공백 포함 {LINE_MIN + 4}~{LINE_MAX - 4}자(최대 {LINE_MAX}자), 컷당 2.5초 안에 읽히는 짧은 숏카피. 늘어지는 서술은 빼고 핵심 장면 + 짧은 느낌으로.
- 사진에서 확인되지 않는 사실은 쓰지 마세요: 사람 이름, 나이, 가족 관계 호칭(엄마·아빠·할머니 등)은 모임 이름에 적혀 있을 때만 쓸 수 있고, 인원수·날짜·숫자·가격·순위도 쓰지 마세요.
- 얼굴·외모 평가('예쁜', '잘생긴')와 특정인을 가리키는 말은 쓰지 마세요. 장소·음식 이름도 사진이나 모임 이름에 분명히 있을 때만 쓰세요.
- 같은 말이나 같은 어미를 되풀이하지 마세요. 이모티콘, 웃음 표기(^^, ㅎㅎ, ㅋㅋ), 따옴표는 쓰지 마세요.
- 모임 이름을 그대로 읽어 주고 싶으면 첫 줄이나 마지막 줄에서 한 번만, 핵심 단어만 자연스럽게 넣으세요 (필수 아님).

JSON만 출력: {{"story": "전체 추억 이야기 한 줄 요약", "scenes": [{{"scene_index": 1, "voiceover": "..."}}]}}
"""


def _media_images(media: List[str], thumb_dir: Optional[str]) -> List[Any]:
    from core.planners.owner import _media_preview_image
    contents: List[Any] = []
    for i, path in enumerate(media):
        try:
            img = _media_preview_image(path, thumb_dir)
        except Exception as e:
            logger.warning(f"컷 {i + 1} 미리보기를 만들지 못함: {e}")
            img = None
        if img is not None:
            contents += [f"[컷 {i + 1}]", img]
    return contents


def _plan_with_llm(title: str, tone: str, media: List[str], api_key: Optional[str], thumb_dir: Optional[str]):
    from core.llm import generate_json
    n = len(media)
    contents = _media_images(media, thumb_dir)
    if not contents:
        raise ValueError("사진·영상 미리보기를 하나도 만들지 못했어요")
    contents.append(_prompt(title, tone, n))
    # 사진을 직접 보고 쓰는 호출이라 사진 판독용 모델 티어(vision)를 쓴다 (ANTHROPIC_MODEL_VISION, 없으면 기본 모델)
    data, engine = generate_json(contents, MEMORY_SYSTEM, temperature=0.7, gemini_api_key=api_key, purpose="모임 추억 대본", tier="vision")
    items = data.get("scenes", data) if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise ValueError(f"예상치 못한 응답: {str(data)[:200]}")
    lines = [str(x.get("voiceover") or "").strip() if isinstance(x, dict) else str(x or "").strip() for x in items]
    story = str(data.get("story") or "").strip() if isinstance(data, dict) else ""
    return lines, story, engine


def finalize_lines(raw_lines: List[str], count: int, title: str, tone: str) -> Tuple[List[str], List[str]]:
    """엔진이 준 줄 → 정확히 count줄. 규칙 위반·빈 줄·중복·부족분은 안전 문구로 바꾸고 바꾼 이유를 돌려준다"""
    notes: List[str] = []
    if len(raw_lines) != count:
        notes.append(f"줄 수 {len(raw_lines)}개 ≠ 컷 {count}개 → {'앞에서 ' + str(count) + '줄만 사용' if len(raw_lines) > count else '부족한 줄은 안전 문구'}")
    out: List[str] = []
    for i in range(count):
        text = _fit(clean_line(raw_lines[i])) if i < len(raw_lines) else ""
        why = find_violation(text, title)
        if not why and text.replace(" ", "") in {o.replace(" ", "") for o in out}:
            why = "앞 줄과 같은 말"
        if not why and len(text) > LINE_MAX + 2:
            why = f"너무 김({len(text)}자)"
        if why:
            if text:
                notes.append(f"컷 {i + 1}: {why} → 안전 문구")
            text = fallback_line(tone, i, count)
            if text.replace(" ", "") in {o.replace(" ", "") for o in out}:      # 안전 문구도 겹치면 풀의 다음 줄
                pool = FALLBACK_LINES[tone_of(tone)]["mid"]
                text = next((p for p in pool if p.replace(" ", "") not in {o.replace(" ", "") for o in out}), text)
        out.append(text)
    return out, notes


def plan_memory_storyboard(title: str = "", tone: str = DEFAULT_TONE, media_paths: Optional[List[str]] = None,
                           api_key: Optional[str] = None, mock: bool = False, thumb_dir: Optional[str] = None,
                           output_plan_path: Optional[str] = None, max_cuts: int = MAX_CUTS) -> List[Dict[str, Any]]:
    """
    올린 사진·영상 순서 그대로 N컷 N줄 (최대 10). 사진을 볼 수 없으면(Mock·모든 엔진 실패) 위치별 안전 문구로 같은 N줄을 만든다.
    media_paths가 비어 있으면 ValueError (지어낼 근거가 없다).
    """
    tone = tone_of(tone)
    title = (title or "").strip()
    media = [p for p in (media_paths or []) if p][:max_cuts]
    if not media:
        raise ValueError("사진·영상을 1개 이상 올려 주세요. 올린 개수 그대로 컷이 만들어져요.")
    n = len(media)
    raw: List[str] = []
    story, engine, notes = "", "template", []
    from core.llm import script_llm_ready
    if not mock and script_llm_ready(api_key):
        try:
            raw, story, engine = _plan_with_llm(title, tone, media, api_key, thumb_dir)
        except Exception as e:
            logger.warning(f"추억 대본 엔진(Claude·GPT·Gemini) 모두 실패, 안전 문구로 대체: {e}")
            raw, engine = [], "template"
    lines, notes = finalize_lines(raw, n, title, tone)
    for note in notes:
        logger.info(f"[추억 대본 보정] {note}")
    scenes = [_scene(i, n, lines[i]) for i in range(n)]
    scenes[0]["script_engine"] = engine
    scenes[0]["memory"] = {"title": title, "tone": tone, "cuts": n, "story": story, "fixes": notes}
    if output_plan_path:
        with open(output_plan_path, "w", encoding="utf-8") as f:
            json.dump(scenes, f, ensure_ascii=False, indent=2)
    logger.info(f"추억 콘티 완료: '{title or '(제목 없음)'}' {n}컷 {n}줄 (톤={tone}, 엔진={engine})")
    return scenes
