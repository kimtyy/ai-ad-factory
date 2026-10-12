"""
core/planners/book_reflect.py - 시골책방 '사유 스토리텔링' 템플릿: 책 속 원문 없이, 수집한 책소개만으로 50~60초

구조:
  씬 1    훅·고민 (경고형·리스트형 헤드라인)                       0%
  씬 2    고민이 드러나는 구체적인 순간                              0%
  씬 3~5  📖 이 책이 건네는 이야기 (큐레이터가 책의 주제·시선을 풀어 말함)  종이 카드 (따옴표·출처 없음 - 책 속 문장처럼 보이지 않게)
  씬 6    위로                                                        0%
  씬 7    책 공개 CTA (우화 템플릿과 같은 지정 문구)
- 근거: 책소개(카카오·국립중앙도서관)에 있는 사실만. 책 속 문장을 지어내거나 인용하지 않음 (따옴표가 나오면 지움)
- 책 제목·저자는 마지막 장면에서 처음 공개 (앞 대사에 나오면 템플릿으로)
"""
import logging
import re
from typing import Any, Dict, List, Optional

from core.script_guard import complete_sentence, ending_kind, find_banned_style, find_promo_tone, shorten_line, to_haeyo

logger = logging.getLogger("ad_factory.planners.book_reflect")

MESSAGE_COUNT = 3
MESSAGE_RANGE = (40, 55)          # 이 책이 건네는 이야기 한 줄 (약 7초) × 3 → 전체 약 50~60초
LINE_RANGE = (30, 38)
HOOK_MAX = 14
MESSAGE_HEADER = "📖 이 책이 건네는 이야기"
MESSAGE_CY = 0.52
DEFAULT_HOOK = "마음이 지칠 때 꺼내 읽는 책"
TEMPLATES = {
    "hook_line": "요즘 괜히 마음이 무겁지 않나요?",
    "situation": "하루를 버티고 돌아온 밤, 아무 말 없이 곁에 있어 줄 무언가가 필요하잖아요.",
    "message": ("이 책은 거창한 정답 대신, 오늘을 버틴 마음을 가만히 들여다보게 해 줘요.",
                "읽다 보면 내 이야기 같아서, 한 장 넘길 때마다 숨이 조금씩 고르게 쉬어지죠.",
                "덮고 나면 내일을 조금 다르게 걸어 보고 싶어지는 책이에요."),
    "comfort": "천천히 가도 괜찮다고, 오늘 하루를 다독여 주는 시간이 될 거예요.",
}


def plan_reflect(store_name: str, book_title: str, author: str, book_info: str, book_photos: List[str],
                 store_photos: List[str], llm: Optional[Dict[str, Any]], scene_factory, claim_check) -> List[Dict[str, Any]]:
    """book.plan_book_storyboard가 부름. scene_factory = book._scene, claim_check = 책소개 근거 검사"""
    from core.planners.book_fable import FABLE_CTA, mentions_book
    cover = book_photos[0] if book_photos else None
    pool = [p for p in (book_photos[1:] + list(store_photos)) if p] or [cover]
    lines = [str((s or {}).get("voiceover") or "") for s in ((llm or {}).get("scenes") or []) if isinstance(s, dict)]
    templated: set = set()

    def clean(i: int, text: str, fallback: str, max_len: int) -> str:
        text = re.sub(r"[\"“”‘’'「」『』]", "", to_haeyo((text or "").strip()))      # 인용처럼 보이는 따옴표 제거
        hit = (claim_check(text) or find_promo_tone(text) or mentions_book(text, book_title, author)) if text else None
        if hit:
            logger.info(f"[사유 대사 차단] 씬 {i + 1}: '{hit}' (대사: {text}) → 템플릿")
            text = ""
        if not text:
            templated.add(i)
        text = text or fallback
        if len(text) > max_len:
            text = shorten_line(text, max_len - 3, max_len)
        done = complete_sentence(text)
        if done is None or find_banned_style(done):
            done = complete_sentence(fallback) or fallback
            templated.add(i)
        return done

    def line(k: int) -> str:
        return lines[k] if k < len(lines) else ""

    hook = str((llm or {}).get("hook_title") or "").strip() or DEFAULT_HOOK
    if len(hook) > HOOK_MAX + 4 or claim_check(hook) or find_promo_tone(hook) or mentions_book(hook, book_title, author):
        hook = DEFAULT_HOOK
    scenes = [scene_factory(0, "hook", clean(0, line(0), TEMPLATES["hook_line"], 30), pool[0], "full",
                            hook_title=hook, media_kind="store")]
    scenes.append(scene_factory(1, "situation", clean(1, line(1), TEMPLATES["situation"], LINE_RANGE[1] + 10),
                                pool[1 % len(pool)], "full", media_kind="store"))
    for j in range(MESSAGE_COUNT):
        i = len(scenes)
        text = clean(i, line(2 + j), TEMPLATES["message"][j], MESSAGE_RANGE[1] + 10)
        sc = scene_factory(i, "message", text, pool[(2 + j) % len(pool)], "full", quote_card=text, quote_source="",
                           quote_cy=MESSAGE_CY, card_header=MESSAGE_HEADER, media_kind="store", video_motion_prompt_en="zoom_in")
        scenes.append(sc)
    scenes[-1]["hold_sec"] = 1.0
    scenes.append(scene_factory(len(scenes), "comfort", clean(len(scenes), line(5), TEMPLATES["comfort"], LINE_RANGE[1] + 10),
                                pool[5 % len(pool)], "full", media_kind="store"))
    from core.planners.book_fable import CTA_CAPTION_CY
    scenes.append(scene_factory(len(scenes), "cta", FABLE_CTA.format(title=book_title, store=store_name), cover or pool[0],
                                "book_quote" if cover else "full", media_kind="cover", hold_sec=1.0,
                                caption_center_y=CTA_CAPTION_CY if cover else None))
    # 템플릿이 끼어 평서가 이어지면 그 템플릿 줄만 감탄형으로
    body = [s for s in scenes if s["role"] in ("hook", "situation", "message", "comfort")]
    kinds = [ending_kind(s["voiceover"]) for s in body]
    for j, s in enumerate(body):
        near_plain = (j > 0 and kinds[j - 1] == "plain") or (j + 1 < len(kinds) and kinds[j + 1] == "plain")
        if s["scene_index"] - 1 in templated and kinds[j] == "plain" and near_plain and s["voiceover"].endswith("요."):
            s["voiceover"] = s["subtitle"] = s["caption"] = s["voiceover"][:-1] + "!"
            if s.get("quote_card"):
                s["quote_card"] = s["voiceover"]
            kinds[j] = "exclaim"
    return scenes


def reflect_prompt(book_title: str, author: str, book_info: str, store_name: str, copy_rules: str) -> str:
    return f"""
[책] 「{book_title}」{(' / ' + author) if author else ''} ← 제목·저자는 영상 마지막에만 공개되므로 대사에 절대 쓰지 마세요
[책소개 - 여기 있는 사실만 근거 (출판사·도서관 소개)] {book_info}
[책방] {store_name}

[구조 - 사유 스토리텔링 (떡상 우화형 벤치마크: 고민 → 책이 건네는 이야기 → 위로 → 마지막에 책 공개)]
- hook_title: 8~{HOOK_MAX}자. 경고형("~하면 손해예요") 또는 리스트형("지칠 때 꺼내 읽는 책"). 해요체·명사형만. 근거 없는 과장 금지.
- 씬 1: 현대인의 고민·결핍을 짚는 한 줄 (공백 포함 14~24자, 질문형 권장)
- 씬 2: 그 고민이 드러나는 구체적인 순간 (공백 포함 {LINE_RANGE[0]}~{LINE_RANGE[1]}자)
- 씬 3·4·5: '이 책이 건네는 이야기' 세 문장 (각 공백 포함 {MESSAGE_RANGE[0]}~{MESSAGE_RANGE[1]}자). 책소개에 있는 주제·시선·이야기 흐름을
  큐레이터가 자기 말로 풀어 주세요. 판본·구성 정보(몇 편 수록, 기념판, 발문·후기 수록 등)보다 책이 다루는 주제·정서·시선을
  먼저 고르고, 그것이 씬 1·2의 고민과 어떻게 닿는지 말하세요 (책소개에 주제가 없으면 사실 범위 안에서 조심스럽게).
  책 속 문장을 지어내거나 따옴표로 인용하지 마세요 ('책에 이런 문장이 있어요' 금지).
  책소개에 없는 내용·수상·판매량·베스트셀러 금지. 줄거리 나열·결말 금지.
- 씬 6: 위로 한 문장 (공백 포함 {LINE_RANGE[0]}~{LINE_RANGE[1]}자) - 씬 1·2의 고민과 책의 이야기를 잇기
- 책 공개 CTA는 따로 붙이므로 쓰지 마세요.
{copy_rules}

JSON만 출력: {{"hook_title": "...", "scenes": [{{"scene_index": 1, "voiceover": "..."}}, ... 씬 6까지]}}
"""
