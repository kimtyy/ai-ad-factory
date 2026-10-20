"""
core/mode_policy.py - 모드별 렌더링/대본 정책 독립화 (Mode Policy)

모드 간 간섭을 완전히 방지하고 각 모드 고유의 템포, 대본 길이, 자막 스타일, 씬 듀레이션을 격리 관리합니다:
- owner (매장/플레이스 직영):
  * 씬당 20~30자 구어체 한 문장 (Hard Limit: 최대 32자, 훅 헤드라인만 8~12자)
  * 씬 듀레이션: 2.0초 ~ 2.3초 (경쾌하고 빠른 쇼츠 템포)
  * 자막 스타일: 폰트 비율 0.082, 스트로크 0.0085 (볼드/임팩트 중심)
  * 단일 성우 강제
- travel (숙소/여행 제휴):
  * 씬당 18~28자 감성 썰 문장 허용 (Hard Limit: 최대 30자)
  * 씬 듀레이션: 3.0초 ~ 3.5초 (영상미와 공간감 유지)
  * 자막 스타일: 폰트 비율 0.066, 스트로크 0.0065
  * 단일 성우 강제 (씬 교차 듀엣 로직 완전 비활성화 유지)
- commerce (쇼핑 제휴/단일·묶음):
  * 씬당 18~28자 상품 소개/리뷰 (Hard Limit: 최대 32자)
  * 씬 듀레이션: 2.4초 ~ 2.8초
  * 자막 스타일: 상단 후킹 바 / 하단 볼드 자막
- book (시골책방/도서 큐레이션):
  * 씬당 16~28자 차분한 독백/인용 (Hard Limit: 최대 35자)
  * 씬 듀레이션: 3.0초 ~ 4.2초 (긴 디졸브 0.6초)
  * 자막 스타일: 명조체(Noto Serif KR), 폰트 비율 0.060
"""
from dataclasses import dataclass, replace
import os
import re
from typing import Callable, Dict, List, Optional


@dataclass(frozen=True)
class ModePolicy:
    mode: str
    name_ko: str

    # 1. 대본/글자수 제약
    min_chars: int             # 최소 글자수 권장
    target_chars: int          # 목표 글자수
    max_chars: int             # Hard Limit (초과 시 재생성 또는 단문 강제 절단)

    # 2. 씬 듀레이션 & 템포 제약 (초)
    min_scene_sec: float       # 씬 최소 재생시간
    max_scene_sec: float       # 씬 최대 재생시간
    default_scene_sec: float   # 기본 목표 재생시간
    tts_rate: str              # 기본 TTS 속도 (Edge-TTS rate)
    xfade_sec: float           # 씬 디졸브 전환 시간
    tail_sec: float            # 음성 뒤 여유 꼬리 시간

    # 3. 자막 스타일
    caption_font_ratio: float  # 화면 너비 대비 폰트 크기 비율
    caption_stroke_ratio: float# 자막 외곽선 두께 비율
    center_y: float            # 자막 세로 중심 비율
    bold_impact: bool          # 볼드/임팩트 스타일 여부

    # 4. 성우 정책
    single_voice_only: bool    # 단일 성우 강제 여부 (씬 교차 듀엣 차단)

    # 5. 프롬프트 지침
    tone_instruction: str      # LLM 프롬프트에 주입할 모드별 고유 대본 규칙
    repair_instruction: str    # 초과 시 LLM 재작성 규칙

    # 6. 자막 배치 보조 (기본값은 기존 동작 그대로)
    caption_max_width: float = 0.72      # 자막 줄 폭 상한 (화면 너비 대비)
    caption_chunk_chars: int = 0         # 0이 아니면: 이 글자 수(공백 포함)를 넘는 문장은 자막을 앞/뒤 덩어리로 나눠 순차 표시 (TTS는 그대로)
    landscape_fg_center_y: float = 0.50  # 가로 영상을 흐린 배경 위에 얹을 때 원본의 세로 중심 (0.50 = 정중앙)


# =================================================================
# 공통 표준: "쇼츠는 광고다. 광고는 1컨셉 1메시지다"
# =================================================================
def build_ad_system_prompt(category_name: str, mode: str = "owner") -> str:
    """
    모든 모드(owner, travel, commerce, book) 공통 단일 광고 엔진 시스템 프롬프트:
    "쇼츠는 광고다. 광고는 1컨셉 1메시지다."
    """
    return (
        f"당신은 {category_name} 숏폼(릴스/쇼츠) 전문 광고 카피라이터이자 크리에이티브 디렉터입니다.\n"
        "당신의 숏폼은 영구히 남는 '디지털 광고 전단지'로서, 시청자의 뇌리에 '단 하나의 강력한 행동 이유(1컨셉 1메시지)'만 꽂아야 합니다.\n"
        "화면에 나오는 사물/시설을 백화점식으로 이것저것 나열하는 '종합 안내판 대본'은 엄격히 금지됩니다.\n"
        "영상 전체를 관통하는 단 하나의 코어 컨셉(Core USP)을 설정하고, 모든 씬과 사진을 그 하나의 혜택을 증명하는 근거로 기승전결 엮으세요.\n"
        "응답은 반드시 JSON만 출력합니다."
    )


def build_one_concept_guidelines(mode: str, n_scenes: int, category_name: str, cta_line: str,
                                 policy: ModePolicy) -> str:
    """
    1컨셉 1메시지 기승전결 구조 프롬프트 표준 뼈대 (모드 확장 표준)
    """
    return f"""[핵심 제작 철학: "쇼츠는 광고다. 광고는 1컨셉 1메시지다"]
1. [단 하나의 코어 컨셉(Core Concept) 선정]:
   - 이번 숏폼 전체를 관통할 '단 하나의 강력한 핵심 가치(메인 혜택/차별점)'를 단 하나만 확정하세요!
   - (해당 매장/상품의 단 하나의 핵심 가치나 킬러 혜택에만 온전히 집중)
   - JSON 응답의 "core_concept" 필드에 이번 영상의 단 하나의 핵심 컨셉을 반드시 명시하세요.

2. [사물 단순 나열 금지 ➔ 코어 컨셉 입증 근거로 활용]:
   - [절대 금지: 사물 백화점식 나열]: 이것저것 품목이 바뀐다고 화제를 분산시키지 마세요!
   - [올바른 기승전결 전개]: 사진 속 사물이 달라지더라도 모든 멘트는 선정된 '단 하나의 코어 컨셉'으로 일관되게 수렴해야 합니다.
     * 씬 1 (훅 / 핵심 제시): 코어 컨셉에 직결되는 강력한 질문이나 핵심 혜택으로 시선 장악. (공백 포함 8~12자 헤드라인)
     * 씬 2 ~ {n_scenes - 1} (증명 및 체감 혜택): 각 사진의 사물/공간을 코어 컨셉을 증명하는 구체적 배경과 근거로 활용.
       (각 사진의 공간/사물이 바뀌더라도 그 공간을 코어 컨셉의 혜택을 경험하는 구체적 배경과 근거로 연결하여 표현)
     * 씬 {n_scenes} (행동 유도 / CTA): 그 핵심 혜택을 이용하도록 명확한 방문/검색 유도. 정확히: "{cta_line}"

3. [의미 단위 자연스러운 구어체 변환 (메타 단어 금지)]:
   - 사진 속 안내판/스티커의 텍스트를 보더라도 '문구', '표시', '안내판', '스티커' 같은 단어를 절대 읊지 마세요.
   - 손님에게 친절하게 혜택을 전하듯 완전한 의미 단위로 풀어 쓰세요.
   - 올바른 표준 명칭을 사용하세요.

4. [온전한 구어체 서술어 & 낭독 호흡 안정화]:
   - 문장 끝은 말이 토막 나지 않게 자연스러운 친절한 구어체(~있어요, ~만나보세요, ~챙겨가세요)로 온전하게 마무리하세요.
   - 쉼표(,)는 오직 주어/조건절 뒤처럼 '소리 내어 한 번 숨을 고르는 호흡 지점'(예: ~매장이니, ~때도, ~들어오시면,)에만 배치하세요.
"""


# =================================================================
# 1. owner 정책 (매장/플레이스 직영 홍보)
# =================================================================
OWNER_POLICY = ModePolicy(
    mode="owner",
    name_ko="매장/플레이스 직영",
    min_chars=14,
    target_chars=24,
    max_chars=30,               # 본론 문장 20~28자 (자막 2줄 덩어리로 나눠 읽기 쉽게). CTA는 정해진 문구라 예외
    min_scene_sec=2.8,
    max_scene_sec=3.3,
    default_scene_sec=3.0,
    tts_rate="+10%",            # 자연스럽고 여유 있는 템포
    xfade_sec=0.25,
    tail_sec=0.20,
    caption_font_ratio=0.064,   # 예전 0.074에서 약 13.5% 축소: 한 줄 11~12자, 최대 2줄 덩어리
    caption_stroke_ratio=0.0075,
    center_y=0.55,              # 실제 쇼츠 앱은 하단 60~90%에 채널명·제목·AI 라벨·홍보 버튼이 올라옴 → 그 위(55%)가 안전지대 (예전 0.72는 채널명과 겹침)
    bold_impact=True,
    single_voice_only=True,
    caption_max_width=0.76,
    caption_chunk_chars=18,
    landscape_fg_center_y=0.43,
    tone_instruction="""[매장/플레이스 1컨셉 1메시지 광고 규칙 (필수 준수)]
- [1컨셉 1메시지 집중]: 매장의 여러 품목을 이것저것 단순 나열하지 말고, 30초 전체를 관통하는 단 하나의 핵심 메시지(Core USP)를 관통시키세요.
- [사진을 입증 근거로 활용]: 서로 다른 사진이 나오더라도, 각 씬의 대사는 선정된 단 하나의 핵심 컨셉(Core USP)을 입증하는 배경과 혜택으로만 수렴하세요.
- [의미 단위 자연스러운 구어체 변환]: 사진 속 안내판 글자를 읽을 때 '문구', '표시', '안내판' 같은 딱딱한 단어를 절대 쓰지 말고 손님 혜택 중심의 친절한 구어체로 풀어 쓰세요.
- [온전한 종결 어미]: 문장 끝이 토막 나지 않게 자연스러운 친절한 구어체(~있어요, ~만나보세요, ~챙겨가세요)로 온전하게 마무리하세요!
- [낭독 호흡 안정화 쉼표]: 쉼표(,)는 한 번 숨을 고르는 조건절/연결절 바로 뒤에만 자연스럽게 배치하세요.""",
    repair_instruction="""[owner 1컨셉 1메시지 대본 재작성 규칙]
1. 사물을 이것저것 백화점식으로 단순 나열하지 말고, 단 하나의 핵심 컨셉(Core USP)을 입증하는 일관된 스토리로 다시 작성하세요.
2. 사진 속 안내판 글자를 읽을 때 '문구', '표시', '안내판' 같은 딱딱한 단어를 절대 쓰지 말고 혜택형 구어체로 풀어 쓰세요.
3. 쉼표(,)는 숨을 고르는 조건절 뒤에만 배치하고, 문장 끝은 친절한 구어체(~있어요, ~만나보세요, ~챙겨가세요)로 온전하게 마무리하세요."""
)


# =================================================================
# 2. travel 정책 (숙소/여행 제휴)
# =================================================================
TRAVEL_POLICY = ModePolicy(
    mode="travel",
    name_ko="숙소/여행 제휴",
    min_chars=15,
    target_chars=24,
    max_chars=30,               # 씬당 18~28자 감성 문장 (톤 규칙 26~30자와 일치)
    min_scene_sec=3.0,
    max_scene_sec=3.5,
    default_scene_sec=3.2,
    tts_rate="+5%",             # 감성적이고 안정적인 속도
    xfade_sec=0.35,
    tail_sec=0.25,
    caption_font_ratio=0.066,
    caption_stroke_ratio=0.0065,
    center_y=0.50,
    bold_impact=False,
    single_voice_only=True,     # 단일 성우 강제 (씬 교차 듀엣 로직 완전 비활성화)
    tone_instruction="""[숙소/여행 1컨셉 1메시지 감성 썰 풀기 규칙]
- [1컨셉 1메시지]: 시설들을 백화점식으로 나열하지 말고, 숙소의 '단 하나의 감성 테마'(예: 노을 속 완벽한 힐링 or 숲속 비밀 아지트)에 모든 씬을 일관되게 수렴하세요.
- 각 씬의 voiceover는 공백 포함 18~28자(최대 30자)의 감성 구어체 완결 문장으로 작성하세요.
- 유튜버가 시청자 1명에게 나만 아는 비밀 공간 썰을 풀듯 호기심과 여운을 남기세요.
- 숙소명 은닉(Curiosity Gap): 크롤링된 숙소명을 직접 부르지 말고 공간감/경험으로 치환하세요.""",
    repair_instruction="""[travel 1컨셉 감성 대본 재작성 규칙]
시설 나열을 멈추고, 단 하나의 핵심 감성 테마를 증명하는 18~28자 이내의 해요체 문장으로 다시 작성하세요."""
)


# =================================================================
# 3. commerce 정책 (쇼핑 제휴/단일·묶음)
# =================================================================
COMMERCE_POLICY = ModePolicy(
    mode="commerce",
    name_ko="쇼핑 제휴",
    min_chars=12,
    target_chars=24,
    max_chars=32,               # 톤 규칙(26~30자)과 일치: 24자에서 자르면 정상 문장이 잘려 비문이 됨
    min_scene_sec=2.4,
    max_scene_sec=2.8,
    default_scene_sec=2.5,
    tts_rate="+12%",
    xfade_sec=0.30,
    tail_sec=0.20,
    caption_font_ratio=0.064,
    caption_stroke_ratio=0.0060,
    center_y=0.235,             # 상품 카드 위쪽 안전 배치
    bold_impact=False,
    single_voice_only=True,
    tone_instruction="""[쇼핑 제휴 1컨셉 1메시지 추천 규칙]
- [1컨셉 1메시지]: 제품의 수많은 스펙을 나열하지 말고, 고객이 지금 사야 할 '단 하나의 킬러 혜택'을 중심으로 모든 씬을 증명하세요.
- 각 씬의 voiceover는 공백 포함 18~28자(최대 32자 이내)의 명확하고 신뢰감 있는 리뷰 문장으로 작성하세요.
- 판매 페이지(USP)에 없는 허위 기능/수치/인증은 절대 쓰지 마세요.""",
    repair_instruction="""[commerce 1컨셉 대본 재작성 규칙]
스펙 나열을 배제하고, 단 하나의 킬러 혜택을 입증하는 18~28자 이내의 간결한 문장으로 다시 작성하세요."""
)


# =================================================================
# 4. book 정책 (시골책방/도서 큐레이션)
# =================================================================
BOOK_POLICY = ModePolicy(
    mode="book",
    name_ko="시골책방/도서",
    min_chars=16,
    target_chars=26,
    max_chars=35,
    min_scene_sec=3.0,
    max_scene_sec=4.2,
    default_scene_sec=3.5,
    tts_rate="+0%",             # 차분하고 낭독적인 템포
    xfade_sec=0.60,             # 긴 디졸브로 여운 형성
    tail_sec=0.35,
    caption_font_ratio=0.060,   # 명조체 전용 비율
    caption_stroke_ratio=0.0040,
    center_y=0.250,
    bold_impact=False,
    single_voice_only=True,
    tone_instruction="""[시골책방 1컨셉 1메시지 문학 큐레이션 규칙]
- [1컨셉 1메시지]: 공간이나 책을 단순 소개하지 말고, 독자의 마음에 남길 '단 하나의 문학적 메시지'를 관통시키세요.
- 각 씬의 voiceover는 공백 포함 18~28자(최대 35자 이내)의 차분하고 깊이 있는 문장으로 작성하세요.
- 책의 문장과 책방의 고요한 분위기를 전달하는 서정적인 낭독 톤을 유지하세요.""",
    repair_instruction="""[book 1컨셉 대본 재작성 규칙]
단 하나의 문학적 여운에 집중하여 공백 포함 20~28자 이내로 정돈된 명조체 낭독 문장으로 다시 작성하세요."""
)


# =================================================================
# 5. video_overlay 정책 (촬영 영상 직가공)
# =================================================================
VIDEO_OVERLAY_POLICY = ModePolicy(
    mode="video_overlay",
    name_ko="촬영 영상 직가공",
    min_chars=8,
    target_chars=14,
    max_chars=24,
    min_scene_sec=2.0,
    max_scene_sec=5.0,
    default_scene_sec=3.0,
    tts_rate="+5%",
    xfade_sec=0.0,
    tail_sec=0.1,
    caption_font_ratio=0.082,
    caption_stroke_ratio=0.0085,
    center_y=0.50,
    bold_impact=True,
    single_voice_only=True,
    tone_instruction="""[촬영 영상 직가공 1컨셉 1메시지 규칙]
- [1컨셉 1메시지]: 영상 전체를 관통할 단 하나의 핵심 매력/가치를 정하고 모든 씬이 이를 증명하게 하세요.
- 영상 속 실제 움직임과 장면에 어울리는 10~18자 이내의 생생하고 자연스러운 멘트를 작성하세요.""",
    repair_instruction="""[video_overlay 대본 재작성 규칙]
영상의 타임라인 속도에 맞춰 12~16자 이내의 임팩트 있는 문장으로 다시 작성하세요."""
)


POLICIES: Dict[str, ModePolicy] = {
    "owner": OWNER_POLICY,
    "travel": TRAVEL_POLICY,
    "commerce": COMMERCE_POLICY,
    "book": BOOK_POLICY,
    "video_overlay": VIDEO_OVERLAY_POLICY,
}


def hook_split_enabled() -> bool:
    """
    씬 1을 '후킹 헤드라인 단독 → 서브 자막'으로 시간차 분리해 노출하고, 나레이션도 후킹을 먼저 읽는 2단 음성으로 만들지.
    기본 OFF: 후킹 헤드라인(8~12자 조각)이 불완전한 말토막 자막으로 먼저 튀어나오던 문제 때문에, 씬 1도 0.0초부터
    실제 본문 대사(caption) 하나만 단일 자막으로 낸다. 비교·롤백용으로만 HOOK_SPLIT=1 로 켠다 (모듈 변수 HOOK_SPLIT_FORCE 로도 가능).
    """
    import os
    forced = globals().get("HOOK_SPLIT_FORCE")
    if forced is not None:
        return bool(forced)
    return os.getenv("HOOK_SPLIT", "0").strip() == "1"


HOOK_SPLIT_FORCE = None
INTRO_COVER_FORCE = None


def intro_cover_enabled() -> bool:
    """
    영상 맨 앞 INTRO_COVER_SEC(0.5초)를 썸네일 화면(큰 후킹 카피 + 서브 카피)으로 덮을지. 기본 ON.
    유튜브 쇼츠는 thumbnails.set으로 올린 맞춤 썸네일이 쇼츠 피드에 적용되지 않고 영상 안의 프레임(특히 0초)을 대표 화면으로 쓰므로,
    '0초 커버'가 클릭 유도력을 만든다. 영상 렌더와는 분리된 후처리다: 완성된 영상 앞에 thumbnail.jpg 1장을 무음 정지 클립으로 붙일 뿐이라 본문 자막·나레이션은 건드리지 않는다.
    끄려면 INTRO_COVER=0 (또는 모듈 변수 INTRO_COVER_FORCE=False).
    """
    import os
    if INTRO_COVER_FORCE is not None:
        return bool(INTRO_COVER_FORCE)
    return os.getenv("INTRO_COVER", "1").strip() != "0"


INTRO_COVER_SEC = 0.5


# 사장님 모드 '상단 여백' 자막: 음식 사진을 가리지 않게 위쪽 24%, 가로 사진은 중심을 52%로 내려 상단 블러 여백에 자막이 들어가게
OWNER_TOP_POLICY = replace(OWNER_POLICY, center_y=0.24, landscape_fg_center_y=0.52)
CAPTION_POS_OPTIONS = {
    "mid": "🎯 중단 안전영역 (55% - 유튜브 하단 UI 위, 기본값)",
    "top": "⬆️ 상단 여백영역 (24% - 음식 사진 100% 노출)",
}


def get_mode_policy(mode: Optional[str]) -> ModePolicy:
    """모드별 격리 정책 객체 반환 (미지정 시 owner 기본)"""
    m = (mode or "owner").strip().lower()
    if m == "owner" and os.getenv("OWNER_CAPTION_POS", "").strip().lower() == "top":
        return OWNER_TOP_POLICY
    return POLICIES.get(m, OWNER_POLICY)


# =================================================================
# 5. 검증 및 Hard Limit 절단 가드레일 (공통 함수)
# =================================================================
def complete_owner_sentence(text: str) -> str:
    """사장님 모드 친절한 구어체 종결 어미 검증 및 완성 (~있어요, ~만나보세요, ~챙겨가세요, ~준비돼 있어요)"""
    t = (text or "").strip()
    if not t:
        return ""
    end = "?" if t.endswith("?") else "!"
    body = t.rstrip(" ,.!?~…")

    # 1. 이미 온전한 구어체 종결 어미인 경우
    if body.endswith(("요", "죠", "세요", "네요", "답니다", "습니다", "니다", "어요", "아요", "해요", "돼요", "에요", "이요")):
        return body + end

    # 2. 자주 끊기는 불완전 서술어 보정
    if body.endswith("준비돼"):
        return body + " 있어요" + end
    if body.endswith("열려"):
        return body + " 있어요" + end
    if body.endswith(("가득", "가득한")):
        return body.rstrip("한") + "해요" + end
    if body.endswith(("기다려", "보여")):
        return body + "요" + end
    if body.endswith(("들러", "살펴", "찾아")):
        return body + "보세요" + end
    if body.endswith(("으로", "로")):
        return body + " 준비했어요" + end
    if body.endswith(("를", "을")):
        return body + " 만나보세요" + end
    if body.endswith(("과", "와")):
        return body[:-1] + "도 만나보세요" + end
    if body.endswith(("에", "서")):
        return body + " 만나보세요" + end

    # 부사형 '~히'로 끝난 경우(확실히·분명히): 명사처럼 '예요'를 붙이면 비문이 되므로 형용사 해요체로
    if body.endswith("히") and len(body) >= 3:
        return body[:-1] + "해요" + end

    # 3. 명사형으로 끝난 경우 자연스러운 구어체 종결 어미 연결 (기계적 패딩 폐기)
    last_char = body[-1]
    if '가' <= last_char <= '힣':
        has_batchim = (ord(last_char) - 0xAC00) % 28 > 0
        return body + ("이에요" if has_batchim else "예요") + end
    return body + "예요" + end


_FINAL_ENDINGS = ("요", "죠", "다", "까", "네", "군", "걸", "래", "자", "야")


def _is_finished(sentence: str) -> bool:
    """문장이 서술어로 끝났는지 (구두점 제외 마지막 글자가 종결 어미). 관형형·연결어미·조사로 끝나면 False"""
    b = (sentence or "").rstrip(" ,.!?~…")
    if not b:
        return False
    if b.endswith(("는데", "은데", "인데", "지만", "니까", "면서", "해서", "하고", "라서", "라고", "다고", "이라", "에서", "으로", "에게")):
        return False
    return b.endswith(_FINAL_ENDINGS)


def clamp_line_to_policy(text: str, policy: ModePolicy) -> str:
    """
    모드의 Hard Limit(max_chars)을 넘는 대사를 '완결된 문장'으로만 줄인다. 어미를 지어 붙이거나(\"~ 있어요\") 어절을 뚝 자르지 않는다.
    줄이는 순서 (앞에서부터 성공하면 종료):
      ① 한도 이내(+2자 여유)면 그대로
      ② 문장 부호(.!?)로 나뉜 완결 문장 중 한도에 들어오는 가장 긴 앞부분
      ③ 그래도 안 되면 원문을 그대로 둔다 (TTS 길이는 나레이션에 맞춰지고, 길이 초과는 LLM 재작성 단계에서 처리).
         잘린 비문이 나가는 것보다 긴 완결 문장이 낫다.
    """
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return ""
    if len(t) <= policy.max_chars + 2:
        return complete_owner_sentence(t) if policy.mode == "owner" else t

    # ② 완결 문장 단위 (문장 부호 뒤 공백으로 분리)
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", t) if x.strip()]
    if len(sentences) >= 2:
        acc = ""
        for sent in sentences:
            cand = (acc + " " + sent).strip()
            if len(cand) > policy.max_chars + 2:
                break
            acc = cand
        if acc and _is_finished(acc):
            return acc

    # ③ 완결되게 줄일 수 없으면 원문 보존 (쉼표 앞 절을 떼면 컨셉어가 사라지거나 의미가 깨지므로 자르지 않음)
    return complete_owner_sentence(t) if policy.mode == "owner" else t


def create_length_violation_checker(policy: ModePolicy, base_checker: Optional[Callable[[str], Optional[str]]] = None):
    """
    LLM 대본 규칙 검사용 콜백: 기본 어투 위반 + 모드별 최대 글자 수 초과 탐지
    """
    def check_violation(text: str) -> Optional[str]:
        t = (text or "").strip()
        if not t:
            return None
        if len(t) > policy.max_chars:
            return f"글자 수 초과 ({len(t)}자 > 최대 {policy.max_chars}자 제한)"
        if base_checker:
            return base_checker(t)
        return None

    return check_violation
