"""
core/planners/book.py - 시골책방(도서 큐레이션) 플래너: 책 한 권을 깊게, 약 35초 (성우 -5%, 디졸브 0.6초)

구조 (화면):
  씬 1  [0~4초]   훅 (헤드라인 + 짧은 질문)              2:3 책 표지 카드
  씬 2~3 [4~14초] 이런 날 이 책 (TPO 2문장)               표지 → 펼친 면 카드
  씬 4  [14~24초] 📖 책 속 한 문장 (입력한 원문 낭독)      위로 줄인 표지 카드 + 종이 인용 카드 (자막 대신)
  씬 5  [24~31초] 책방 분위기                            책방 사진 전체 화면
  씬 6  [31~35초] CTA (책방 방문 + 구매 링크는 댓글)       책방 사진 전체 화면
- 책 속 문장은 AI가 만들지 않음: 사용자가 입력한 원문만 (없으면 인용 씬을 빼서 약 28초)
- 사실 근거: 사용자가 적은 책 소개(출판사 소개·장르 등)만. 베스트셀러·수상·판매량 같은 말은 소개에 있을 때만
- 대본 엔진: Claude → GPT → Gemini 자동 백업, 어미 리듬(질문형·감탄형) 규칙·판에 박힌 패턴 금지 동일 적용
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from core.llm import script_llm_ready
from core.planners.commerce import _claim
from core.script_guard import (COPY_TONE_RULES, complete_sentence, diversify_exclamations, ending_kind, find_banned_style,
                               find_promo_tone, shorten_line, strip_brochure_words, to_haeyo)

from core.script_guard import RHYTHM_ENFORCE  # noqa: E402

logger = logging.getLogger("ad_factory.planners.book")

BOOK_CAPTION_CY = 0.250          # 표지 카드 씬 자막 위치 (compositor.BOOK_CAPTION_CY와 같음)
QUOTE_MAX = 90                   # 인용 카드·낭독에 맞는 길이 (넘으면 잘라 쓰지 않고 줄여 달라고 안내)
HOOK_TITLE_MAX = 12
HOOK_LINE_RANGE = (10, 18)
TPO_LINE_RANGE = (30, 38)        # -5% 속도 실측(약 0.15초/자)로 문장당 약 4.5~5.5초 → 이런 날 이 책 약 10초
STORE_LINE_RANGE = (30, 38)
QUOTE_HOLD_SEC = 2.0             # 인용문 낭독 뒤 인용 카드를 2초 더 보여 줌 (여운) → 전체 약 33~35초
DEFAULT_HOOK = "오늘 밤, 이 한 권"
DEFAULT_HOOK_LINE = "조용히 읽고 싶은 날 있죠?"
TPO_TEMPLATES = ("생각이 많은 밤, 천천히 한 장씩 넘기기 좋은 책이에요.",
                 "한 장씩 읽다 보면 마음이 조금 가라앉는 책이죠!")
STORE_TEMPLATE = "창가 자리에 앉아 몇 장 넘겨 보고 싶은 책방이에요."
CTA_TEMPLATE = "{store}에서 직접 펼쳐 보세요, 구매 링크는 댓글에 있어요!"
LABEL_TEMPLATE = "📚 {store}의 한 권"
# 책 소개에 없으면 쓰면 안 되는 말 (허위·과장)
BOOK_HYPE = ("베스트셀러", "스테디셀러", "수상", "만 부", "만부", "판매 1위", "화제작", "필독서", "역주행", "품절", "완판", "최저가")


def book_hype(text: str, info: str = "") -> Optional[str]:
    """책 소개(info)에 근거 없는 판매·수상·화제 주장 → 걸린 표현 (없으면 None)"""
    info_n = re.sub(r"\s+", "", info or "")
    for w in BOOK_HYPE:
        if w in (text or "") and re.sub(r"\s+", "", w) not in info_n:
            return w
    return None


def check_quote(quote: str, max_len: int = QUOTE_MAX) -> str:
    """인용문 정리 (따옴표 제거·공백 정리). 너무 길면 ValueError (원문을 임의로 자르지 않음)"""
    q = re.sub(r"\s+", " ", (quote or "").strip().strip('"“”「」『』\''))
    if len(q) > max_len:
        raise ValueError(f"책 속 문장이 {len(q)}자예요. 인용 카드·낭독에 맞게 {max_len}자 이내로 골라 주세요 "
                         "(원문을 임의로 자르지 않아요).")
    return q


TEMPLATES = {"basic": "기본 큐레이션", "fable": "우화·사유 스토리텔링", "reflect": "사유 스토리텔링 (책소개 기반)"}
REFLECT_MIN_INFO = 60            # 사유 템플릿에 필요한 책소개 최소 길이 (이보다 짧으면 근거가 부족)


def split_book_media(media_paths: List[str], book_photo_count: int = 1):
    """올린 순서: 앞 book_photo_count장 = 책(표지, 펼친 면), 나머지 = 책방 사진"""
    paths = [p for p in (media_paths or []) if p]
    n = max(1, min(2, int(book_photo_count or 1)))
    return paths[:n], paths[n:]


def _scene(i: int, role: str, text: str, path: Optional[str], layout: str, **extra) -> Dict[str, Any]:
    motions = ["zoom_in", "zoom_out", "pan_down", "zoom_in", "pan_right", "zoom_out"]
    sc = {"scene_index": i + 1, "scene_number": i + 1, "role": role, "target_visual_role": role,
          "visual_keywords": [role], "duration_sec": 3.5, "hook_title": "", "voiceover": text, "subtitle": text,
          "caption": text, "visual_description": role, "image_prompt_en": "",
          "video_motion_prompt_en": motions[i % len(motions)], "focus_bottom": False, "layout": layout, "shot": "full"}
    if layout in ("book_card", "book_quote"):
        sc["caption_center_y"] = BOOK_CAPTION_CY
    if path:
        sc["source_path"] = path
    sc.update(extra)
    return sc


def plan_book_storyboard(
    store_name: str,
    book_title: str,
    author: str = "",
    book_info: str = "",
    quote: str = "",
    media_paths: Optional[List[str]] = None,
    book_photo_count: int = 1,
    api_key: Optional[str] = None,
    mock: bool = False,
    thumb_dir: Optional[str] = None,
    output_plan_path: Optional[str] = None,
    template: str = "basic",
    core_concept: str = "",
) -> List[Dict[str, Any]]:
    """
    template: basic(한 권 큐레이션, 약 30~35초) / fable(우화·사유: 고민 → 원문 낭독 카드 여러 장 → 위로 → 마지막에 책 공개, 50~75초)
    → 씬 5~6개(fable은 원문 길이만큼 더). scenes[0]에 book(제목·저자·책방·라벨 문구)·script_engine 기록.
    씬마다 layout(book_card / book_quote / full), source_path, caption_center_y, 인용 씬은 quote_card·quote_source
    """
    store_name, book_title, author = (store_name or "").strip(), (book_title or "").strip(), (author or "").strip()
    if not store_name or not book_title:
        raise ValueError("책방 이름과 책 제목을 입력해 주세요.")
    template = template if template in TEMPLATES else "basic"
    from core.planners import book_fable
    q = check_quote(quote, book_fable.FABLE_QUOTE_MAX if template == "fable" else QUOTE_MAX)
    if template == "fable" and not q:
        raise ValueError("우화·사유 템플릿은 책 속 원문이 꼭 필요해요 (낭독이 영상의 중심이에요).")
    if template == "reflect" and len((book_info or "").strip()) < REFLECT_MIN_INFO:
        raise ValueError("사유 스토리텔링은 책소개(사실)가 필요해요. [책 정보 자동 채우기]로 책소개를 가져오거나 직접 적어 주세요.")
    book_photos, store_photos = split_book_media(media_paths or [], book_photo_count)
    if not mock and not book_photos:
        raise ValueError("책 사진이 없어요. 책 표지 사진을 첫 번째로 올려 주세요 (AI로 표지를 만들지 않아요).")
    cover = book_photos[0] if book_photos else None
    page = book_photos[1] if len(book_photos) > 1 else cover
    store1 = store_photos[0] if store_photos else None
    store2 = store_photos[1] if len(store_photos) > 1 else store1

    llm = None
    if not mock and script_llm_ready(api_key):
        try:
            llm = _plan_with_llm(store_name, book_title, author, book_info, book_photos, store_photos, api_key, thumb_dir,
                                 template=template, quote=q)
        except Exception as e:
            logger.warning(f"시골책방 대본 엔진 모두 실패, 템플릿 대사로 대체: {e}")
    if template == "reflect":
        from core.planners.book_reflect import plan_reflect
        scenes = plan_reflect(store_name, book_title, author, book_info, book_photos, store_photos, llm, _scene,
                              lambda t: book_hype(t, book_info) or _claim(t, book_info))
        return _finish(scenes, llm, book_title, author, store_name, q, template, output_plan_path, core_concept=core_concept)
    if template == "fable":
        scenes = book_fable.plan_fable(store_name, book_title, author, book_info, q, book_photos, store_photos, llm, _scene,
                                       lambda t: book_hype(t, book_info) or _claim(t, book_info))
        return _finish(scenes, llm, book_title, author, store_name, q, template, output_plan_path, core_concept=core_concept)
    lines = [str((s or {}).get("voiceover") or "") for s in ((llm or {}).get("scenes") or []) if isinstance(s, dict)]

    templated: set = set()

    def clean(i: int, text: str, fallback: str, max_len: int) -> str:
        text = to_haeyo(text.strip())
        for check, label in ((lambda t: book_hype(t, book_info) or _claim(t, book_info), "근거 없는 주장"),
                             (find_promo_tone, "홍보 어투")):
            hit = check(text) if text else None
            if hit:
                logger.info(f"[{label} 차단] 씬 {i + 1}: '{hit}' (대사: {text}) → 템플릿 대사")
                text = ""
        if not text:
            templated.add(i)
        text = strip_brochure_words(text or fallback)
        if len(text) > max_len:
            text = shorten_line(text, max_len - 3, max_len)
        done = complete_sentence(text)
        if done is None or find_banned_style(done):
            logger.info(f"[대사 규칙 위반] 씬 {i + 1}: '{text}' → 템플릿 대사")
            done = complete_sentence(fallback) or fallback
            templated.add(i)
        return done

    def line(k: int) -> str:
        return lines[k] if k < len(lines) else ""

    hook = str((llm or {}).get("hook_title") or "").strip() or DEFAULT_HOOK
    if len(hook) > HOOK_TITLE_MAX + 4 or book_hype(hook, book_info) or find_promo_tone(hook):
        hook = DEFAULT_HOOK
    scenes = [_scene(0, "hook", clean(0, line(0), DEFAULT_HOOK_LINE, HOOK_LINE_RANGE[1] + 8), cover, "book_card",
                     hook_title=hook, media_kind="cover")]
    scenes.append(_scene(1, "book", clean(1, line(1), TPO_TEMPLATES[0], TPO_LINE_RANGE[1] + 10), cover, "book_card",
                         media_kind="cover"))
    scenes.append(_scene(2, "book", clean(2, line(2), TPO_TEMPLATES[1], TPO_LINE_RANGE[1] + 10), page, "book_card",
                         media_kind="page", shot="center" if page == cover else "full"))
    source = f"「{book_title}」" + (f", {author}" if author else "")
    if q:
        scenes.append(_scene(len(scenes), "quote", q, page, "book_quote", quote_card=q, quote_source=source,
                             media_kind="page", hold_sec=QUOTE_HOLD_SEC, tts_rate=book_fable.QUOTE_RATE))
    store_line = clean(len(scenes), line(3), STORE_TEMPLATE, STORE_LINE_RANGE[1] + 10)
    scenes.append(_scene(len(scenes), "store", store_line, store1 or cover, "full" if store1 else "book_card",
                         media_kind="store"))
    cta = CTA_TEMPLATE.format(store=store_name)
    scenes.append(_scene(len(scenes), "cta", cta, store2 or cover, "full" if store2 else "book_card", media_kind="store"))

    body = [s for s in scenes if s["role"] not in ("quote", "cta")]
    varied, changes = diversify_exclamations([s["voiceover"] for s in body], reserved=hook)
    for s, vo in zip(body, varied):
        s["voiceover"] = s["subtitle"] = s["caption"] = vo
    # 템플릿이 끼어 평서 '~요.'가 이어지면 그 템플릿 줄을 감탄형으로 (질문형 강제 변환은 비문 위험)
    kinds = [ending_kind(s["voiceover"]) for s in body]
    for j, s in enumerate(body if RHYTHM_ENFORCE else ()):
        near_plain = (j > 0 and kinds[j - 1] == "plain") or (j + 1 < len(kinds) and kinds[j + 1] == "plain")
        if s["scene_index"] - 1 in templated and kinds[j] == "plain" and near_plain and s["voiceover"].endswith("요."):
            s["voiceover"] = s["subtitle"] = s["caption"] = s["voiceover"][:-1] + "!"
            kinds[j] = "exclaim"
    for c in changes:
        logger.info(f"[감탄사 중복 분산] {c}")

    return _finish(scenes, llm, book_title, author, store_name, q, template, output_plan_path, core_concept=core_concept)


def _finish(scenes, llm, book_title, author, store_name, q, template, output_plan_path, core_concept: str = "") -> List[Dict[str, Any]]:
    scenes[0].update({"script_engine": (llm or {}).get("_engine") or "template",
                      "book": {"title": book_title, "author": author, "store": store_name, "template": template,
                               "label": LABEL_TEMPLATE.format(store=store_name), "quote": q}})
    if core_concept:
        scenes[0]["core_concept"] = core_concept.strip()
    if output_plan_path:
        with open(output_plan_path, "w", encoding="utf-8") as f:
            json.dump(scenes, f, ensure_ascii=False, indent=2)
    logger.info(f"시골책방 콘티 완료 [{TEMPLATES[template]}]: 「{book_title}」 {len(scenes)}개 씬 "
                f"(인용 {'있음' if q else '없음'}, LLM={scenes[0]['script_engine']})")
    return scenes


BOOK_SYSTEM = ("당신은 동네 책방의 도서 큐레이션 숏폼(쇼츠) 카피라이터입니다. 3자 큐레이터로서 '이런 날엔 이 책'을 차분하게 짚어 줍니다. "
               "책 소개에 있는 사실만 쓰고, 줄거리를 늘어놓거나 결말을 말하지 않으며, 책 속 문장을 지어내지 않습니다. "
               "반드시 JSON 하나만 출력합니다.")


def _plan_with_llm(store_name, book_title, author, book_info, book_photos, store_photos, api_key, thumb_dir,
                   template: str = "basic", quote: str = "") -> Dict[str, Any]:
    from core.llm import generate_json, repair_scene_lines
    from core.planners.owner import _media_preview_image
    from core.script_guard import STYLE_REPAIR_RULES, script_violation_finder

    contents: List[Any] = []
    if template in ("fable", "reflect"):     # 제목 마지막 공개: 책 사진은 안 보여 줌 (대사에 제목이 새지 않게), 책방 사진만
        book_photos = []
    for label, paths in (("책 사진", book_photos[:2]), ("책방 사진", store_photos[:2])):
        for p in paths:
            img = _media_preview_image(p, thumb_dir)
            if img is not None:
                contents += [f"[{label}]", img]
    if template == "fable":
        from core.planners.book_fable import fable_prompt
        contents.append(fable_prompt(book_title, author, book_info, quote, store_name, COPY_TONE_RULES))
    elif template == "reflect":
        from core.planners.book_reflect import reflect_prompt
        contents.append(reflect_prompt(book_title, author, book_info, store_name, COPY_TONE_RULES))
    else:
        contents.append(f"""
[책] 「{book_title}」{(' / ' + author) if author else ''}
[책 소개 - 여기 있는 사실만 근거] {book_info or '(없음 - 제목과 사진으로 알 수 있는 것만)'}
[책방] {store_name}

[구조 - 차분한 호흡, 성우가 천천히 읽음]
- 씬 1: hook_title(8~{HOOK_TITLE_MAX}자, 읽고 싶은 순간을 건드리는 헤드라인) + voiceover 한 줄(공백 포함 {HOOK_LINE_RANGE[0]}~{HOOK_LINE_RANGE[1]}자, 질문형 권장)
  예: hook_title "잠 안 오는 밤의 책" → voiceover "이런 밤엔 어떤 책 펼쳐요?"
- 씬 2·3: '이런 날 이 책' 두 문장 (각 공백 포함 {TPO_LINE_RANGE[0]}~{TPO_LINE_RANGE[1]}자). 언제·누구에게·어떤 기분일 때 이 책인지.
  줄거리 나열·결말 언급 금지. 책 소개에 없는 내용(수상·판매량·베스트셀러 등) 금지.
- 씬 4: 책방 분위기 한 문장 (공백 포함 {STORE_LINE_RANGE[0]}~{STORE_LINE_RANGE[1]}자). 책방 사진에 보이는 것만 (사진이 없으면 책을 펼치기 좋은 자리 이야기).
- 책 속 문장 인용과 CTA는 따로 붙이므로 쓰지 마세요.
{COPY_TONE_RULES}

JSON만 출력: {{"hook_title": "...", "scenes": [{{"scene_index": 1, "voiceover": "..."}}, {{"scene_index": 2, "voiceover": "..."}}, {{"scene_index": 3, "voiceover": "..."}}, {{"scene_index": 4, "voiceover": "..."}}]}}
""")
    data, engine = generate_json(contents, BOOK_SYSTEM, temperature=0.6, gemini_api_key=api_key, purpose="시골책방 대본")
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
        raise ValueError(f"예상치 못한 응답: {str(data)[:200]}")
    lines = [str(s.get("voiceover") or "") for s in data["scenes"] if isinstance(s, dict)]
    style = script_violation_finder(lines)
    # 분량: 훅 줄을 뺀 문장이 짧으면 영상이 35초에 못 미침 → 같은 보정 호출에서 길이도 맞춤
    short = {t for t in lines[1:] if t and len(t) < TPO_LINE_RANGE[0]}
    length_msg = f"너무 짧음 → 공백 포함 {TPO_LINE_RANGE[0]}~{TPO_LINE_RANGE[1]}자로 방문 상황·감각을 더 구체적으로"
    rules = STYLE_REPAIR_RULES + f" (시골책방: 훅 줄을 뺀 문장은 공백 포함 {TPO_LINE_RANGE[0]}~{TPO_LINE_RANGE[1]}자)"
    data, fixes = repair_scene_lines(data, lambda t: style(t) or (length_msg if t in short else None), BOOK_SYSTEM, rules,
                                     gemini_api_key=api_key)
    for f in fixes:
        logger.info(f"[대본 규칙 보정] {f}")
    data["_engine"] = engine
    return data
