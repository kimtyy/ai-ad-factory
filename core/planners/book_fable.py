"""
core/planners/book_fable.py - 시골책방 '우화/사유 스토리텔링' 템플릿 (도서 레이더 2026-10-11 벤치마크: 우화·명언 낭독형 96~130배)

구조 (50~75초, 길이는 원문 길이가 정함):
  씬 1     훅·상황: 현대인의 고민·결핍 (경고형·리스트형 헤드라인)        책방·펼친 면 사진 전체 화면   성우 기본 속도(0%)
  씬 2     고민을 한 번 더 구체적으로                                    책방 사진 전체 화면
  씬 3~k   📖 책 속 우화·문장 낭독 (원문 350자까지, 문장 단위 카드 여러 장)  종이 인용 카드 가운데        성우 -5% (듀얼 템포)
  씬 k+1   위로: 고민과 원문을 잇는 한 문장                               책방 사진 전체 화면
  씬 k+2   책 공개 + CTA: "오늘 마음을 다독여준 이 책은 『도서명』입니다. …"  2:3 책 표지 카드
- 책 제목·저자는 마지막 씬에서 처음 공개: 앞 장면 사진에 표지를 쓰지 않고, 인용 카드 출처를 숨기고, AI 대사에 제목·저자가 나오면 템플릿으로
- 원문은 AI가 만들지 않음 (입력 원문을 문장 경계에서 나누기만, 글자 하나 바꾸지 않음)
"""
import logging
import re
from typing import Any, Dict, List, Optional

from core.script_guard import complete_sentence, ending_kind, find_banned_style, find_promo_tone, shorten_line, to_haeyo

from core.script_guard import RHYTHM_ENFORCE  # noqa: E402

logger = logging.getLogger("ad_factory.planners.book_fable")

FABLE_QUOTE_MAX = 350
QUOTE_CHUNK_MAX = 90              # 인용 카드 한 장 (4줄 안)
QUOTE_RATE = "-5%"                # 낭독 구간만 차분하게 (나머지는 기본 속도)
QUOTE_LAST_HOLD_SEC = 1.5         # 마지막 원문 카드 뒤 여운
FABLE_HOOK_MAX = 14
FABLE_LINE_RANGE = (30, 38)
FABLE_CTA = "오늘 마음을 다독여준 이 책은 『{title}』입니다. {store} 서가에서 직접 펼쳐보세요."
FABLE_DEFAULT_HOOK = "마음이 지칠 때 꼭 들어야 할 한 문장"
FABLE_TEMPLATES = {
    "hook_line": "다들 괜찮은 척하느라 많이 지쳤죠?",
    "situation": "잘하고 있다는 말 한마디가 듣고 싶은 밤, 오늘도 혼자 버텼잖아요.",
    "comfort": "천천히 가도 괜찮다는 이 말이 오늘 하루를 조금 가볍게 해 줄 거예요.",
}
QUOTE_CY = 0.52                   # 우화 카드는 화면 가운데 (앞뒤 장면이 전체 화면이라)
CTA_CAPTION_CY = 0.68             # 책 공개 장면: 위로 줄인 표지 카드 아래 (기본 인용 카드 자리)


def split_quote(text: str, max_chunk: int = QUOTE_CHUNK_MAX) -> List[str]:
    """
    원문 → 인용 카드 조각 (문장 끝 . ! ? … 」 뒤, 그래도 길면 쉼표 뒤에서만 나눔). 글자는 바꾸지 않음:
    ' '.join(조각) == 공백 정리한 원문
    """
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return []
    sents = [s for s in re.split(r"(?<=[.!?…」”\"])\s+", t) if s]
    pieces: List[str] = []
    for s in sents:
        if len(s) <= max_chunk:
            pieces.append(s)
            continue
        cur = ""
        for part in re.split(r"(?<=,)\s+", s):               # 긴 문장은 쉼표 뒤에서
            if cur and len(cur) + 1 + len(part) > max_chunk:
                pieces.append(cur)
                cur = part
            else:
                cur = f"{cur} {part}".strip()
        if cur:
            pieces.append(cur)
    chunks: List[str] = []
    for p in pieces:                                         # 짧은 문장끼리는 한 카드에
        if chunks and len(chunks[-1]) + 1 + len(p) <= max_chunk:
            chunks[-1] = f"{chunks[-1]} {p}"
        else:
            chunks.append(p)
    return chunks


def mentions_book(text: str, title: str, author: str) -> Optional[str]:
    """제목·저자가 대사에 나오면 그 말 (마지막 공개 전에는 쓰면 안 됨)"""
    norm = re.sub(r"\s+", "", text or "")
    for w in (title, author):
        w = re.sub(r"\s+", "", w or "")
        if len(w) >= 2 and w in norm:
            return w
    return None


def plan_fable(store_name: str, book_title: str, author: str, book_info: str, quote: str, book_photos: List[str],
               store_photos: List[str], llm: Optional[Dict[str, Any]], scene_factory, claim_check) -> List[Dict[str, Any]]:
    """book.plan_book_storyboard가 부름 (사진 분리·인용 검사·LLM 호출은 그쪽에서). scene_factory = book._scene"""
    cover = book_photos[0] if book_photos else None
    page = book_photos[1] if len(book_photos) > 1 else None
    # 제목이 보이는 표지는 마지막에만. 앞 장면은 펼친 면·책방 사진 (없으면 어쩔 수 없이 표지)
    pool = [p for p in [page] + list(store_photos) if p] or [cover]
    lines = [str((s or {}).get("voiceover") or "") for s in ((llm or {}).get("scenes") or []) if isinstance(s, dict)]
    templated: set = set()

    def clean(i: int, text: str, fallback: str, max_len: int) -> str:
        text = to_haeyo((text or "").strip())
        hit = (claim_check(text) or find_promo_tone(text) or mentions_book(text, book_title, author)) if text else None
        if hit:
            logger.info(f"[우화 대사 차단] 씬 {i + 1}: '{hit}' (대사: {text}) → 템플릿")
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

    hook = str((llm or {}).get("hook_title") or "").strip() or FABLE_DEFAULT_HOOK
    if len(hook) > FABLE_HOOK_MAX + 4 or claim_check(hook) or find_promo_tone(hook) or mentions_book(hook, book_title, author):
        hook = FABLE_DEFAULT_HOOK
    scenes = [scene_factory(0, "hook", clean(0, line(0), FABLE_TEMPLATES["hook_line"], 30), pool[0], "full",
                            hook_title=hook, media_kind="store")]
    scenes.append(scene_factory(1, "situation", clean(1, line(1), FABLE_TEMPLATES["situation"], FABLE_LINE_RANGE[1] + 10),
                                pool[1 % len(pool)], "full", media_kind="store"))
    chunks = split_quote(quote)
    for j, ch in enumerate(chunks):
        sc = scene_factory(len(scenes), "quote", ch, page or pool[(2 + j) % len(pool)], "full", quote_card=ch,
                           quote_source="", quote_cy=QUOTE_CY, tts_rate=QUOTE_RATE, media_kind="page" if page else "store",
                           video_motion_prompt_en="zoom_in")
        if j == len(chunks) - 1:
            sc["hold_sec"] = QUOTE_LAST_HOLD_SEC
        scenes.append(sc)
    scenes.append(scene_factory(len(scenes), "comfort", clean(len(scenes), line(2), FABLE_TEMPLATES["comfort"],
                                                              FABLE_LINE_RANGE[1] + 10),
                                pool[2 % len(pool)], "full", media_kind="store"))
    cta = FABLE_CTA.format(title=book_title, store=store_name)
    # 책 공개: 표지 카드를 위로 줄이고(인용 카드 자리 배치) 긴 CTA 자막(최대 4줄)은 카드 아래에
    scenes.append(scene_factory(len(scenes), "cta", cta, cover or pool[0], "book_quote" if cover else "full",
                                media_kind="cover", hold_sec=1.0, caption_center_y=CTA_CAPTION_CY if cover else None))

    # 템플릿이 끼어 평서가 이어지면 그 템플릿 줄만 감탄형으로
    body = [s for s in scenes if s["role"] in ("hook", "situation", "comfort")]
    kinds = [ending_kind(s["voiceover"]) for s in body]
    for j, s in enumerate(body if RHYTHM_ENFORCE else ()):
        near_plain = (j > 0 and kinds[j - 1] == "plain") or (j + 1 < len(kinds) and kinds[j + 1] == "plain")
        if s["scene_index"] - 1 in templated and kinds[j] == "plain" and near_plain and s["voiceover"].endswith("요."):
            s["voiceover"] = s["subtitle"] = s["caption"] = s["voiceover"][:-1] + "!"
            kinds[j] = "exclaim"
    return scenes


def fable_prompt(book_title: str, author: str, book_info: str, quote: str, store_name: str, copy_rules: str) -> str:
    return f"""
[책] 「{book_title}」{(' / ' + author) if author else ''} ← 제목·저자는 영상 마지막에만 공개되므로 대사에 절대 쓰지 마세요
[책 소개 - 여기 있는 사실만 근거] {book_info or '(없음)'}
[책 속 원문 - 따로 낭독하므로 대사에 옮기지 마세요] {quote}
[책방] {store_name}

[구조 - 우화·사유 스토리텔링 (떡상 우화형: 고민 → 원문 낭독 → 위로 → 마지막에 책 공개)]
- hook_title: 8~{FABLE_HOOK_MAX}자. 경고형("~하면 손해예요") 또는 리스트형("지칠 때 꼭 들어야 할 한 문장"). 해요체·명사형만, 반말 명령형 금지.
  '인생 망한다' 같은 근거 없는 과장 금지.
- 씬 1 voiceover: 현대인의 고민·결핍을 짚는 한 줄 (공백 포함 14~24자, 질문형 권장)
- 씬 2 voiceover: 그 고민이 드러나는 구체적인 순간 (공백 포함 {FABLE_LINE_RANGE[0]}~{FABLE_LINE_RANGE[1]}자)
- 씬 3 voiceover: 원문 낭독 뒤의 위로. 원문의 메시지와 씬 1·2의 고민을 잇는 한 문장 (공백 포함 {FABLE_LINE_RANGE[0]}~{FABLE_LINE_RANGE[1]}자)
- 줄거리 나열·책 소개 금지 (책 공개 CTA는 따로 붙임)
{copy_rules}

JSON만 출력: {{"hook_title": "...", "scenes": [{{"scene_index": 1, "voiceover": "..."}}, {{"scene_index": 2, "voiceover": "..."}}, {{"scene_index": 3, "voiceover": "..."}}]}}
"""
