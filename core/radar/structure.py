"""
core/radar/structure.py - 떡상 쇼츠의 '전체' 구조·호흡 분석 (후킹 3초 분석의 확장)

- 영상을 내려받지 않고 공개 YouTube URL을 Gemini에 전달해 처음부터 끝까지 분석 (hooks.py와 같은 방식)
- 저장하는 것은 구조·통계뿐: 구간(역할·길이), 말 속도(초당 글자), 문장 수·평균 길이·어미 분포, 인용 낭독 여부,
  자막 스타일, 음악, CTA 유형, 재사용 가능한 구조 템플릿. 나레이션 원문은 통계 계산에만 쓰고 저장하지 않음 (남의 대본 복제 방지)
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ad_factory.radar.structure")

SEGMENT_ROLES = ("hook", "situation", "problem", "quote", "story", "explanation", "comfort", "recommendation",
                 "book_reveal", "cta", "other")
SEGMENT_ROLE_KO = {"hook": "훅", "situation": "상황 제시", "problem": "고민·결핍", "quote": "책 속 문장 낭독", "story": "이야기·우화",
                   "explanation": "설명·소개", "comfort": "위로·메시지", "recommendation": "추천 이유", "book_reveal": "책 공개",
                   "cta": "CTA", "other": "기타"}

STRUCTURE_PROMPT = """
You are analyzing the FULL length of a viral Korean YouTube Short about {topic}.
First decide relevance: is this {criteria}?
Return JSON only with these keys:
- "relevant": true or false
- "content_type": one of recommendation, quote_reading, story, review, bookstore_tour, reading_vlog, other
- "duration_sec": observed length in seconds (number)
- "narration": the full spoken narration in Korean exactly as spoken, sentences separated by newlines ("" if none).
   (Used only for counting sentence length and endings; it will not be stored.)
- "voice": one of human, tts, none
- "segments": list of {{"start": seconds, "end": seconds, "role": one of {roles}, "summary": Korean phrase under 25 chars describing what happens (do not copy the narration)}}
- "quote": {{"present": bool, "read_aloud": bool, "shown_as_text_card": bool, "seconds": total seconds the book passage is read or shown}}
- "books_featured": number of different books shown
- "on_screen_text": {{"font": one of serif, sans, handwriting, none; "position": one of top, center, bottom, mixed; "density": one of low, medium, high}}
- "music": one of none, calm_piano, lofi, emotional, upbeat, other
- "visual_style": Korean phrase describing the main visuals (e.g. "책 표지 클로즈업 + 손으로 넘기기")
- "cta": {{"present": bool, "type": one of follow, comment, link, visit_store, save, none, "at_sec": seconds or null}}
- "pacing": one of slow, medium, fast
- "structure_template": a REUSABLE Korean skeleton of the whole flow using placeholders like {{고민}}, {{책 속 문장}}, {{위로}}, {{책 제목}}
   (e.g. "{{고민}}하는 사람에게 → {{책 속 문장}} → {{위로}} → 책 제목 공개"). Never copy distinctive phrases verbatim.
- "why_it_works": one Korean sentence on why viewers watch to the end
"""

CRITERIA = {
    "book": "a real book or bookstore content: book recommendation or review, reading a passage/quote from a book, "
            "bookstore visit or tour, or a reading vlog showing actual books. NOT relevant: celebrity talk clips, "
            "drama/movie clips, animation, memes, or content where books are not the subject",
}
TOPIC = {"book": "books, reading and bookstores"}


def _sentences(narration: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", narration or "")
    return [p.strip() for p in parts if len(p.strip()) >= 2]


def narration_stats(narration: str, duration_sec: float) -> Dict[str, Any]:
    """나레이션 원문 → 통계 (원문은 버림): 글자 수(공백 제외)·초당 글자·문장 수·평균 문장 길이·어미 분포"""
    from core.script_guard import ending_kind
    sents = _sentences(narration)
    chars = len(re.sub(r"\s+", "", narration or ""))
    kinds = [ending_kind(s) for s in sents]
    n = len(sents) or 1
    return {
        "narration_chars": chars,
        "chars_per_sec": round(chars / duration_sec, 2) if duration_sec and chars else 0.0,
        "sentences": len(sents),
        "avg_sentence_chars": round(sum(len(s) for s in sents) / n, 1) if sents else 0.0,
        "question_ratio": round(kinds.count("question") / n, 2) if sents else 0.0,
        "exclaim_ratio": round(kinds.count("exclaim") / n, 2) if sents else 0.0,
        "formal_ratio": round(sum(1 for s in sents if re.search(r"(니다|니까)[.!?]?$", s)) / n, 2) if sents else 0.0,
    }


def normalize(data: Dict[str, Any], duration_hint: Optional[float] = None, source: str = "") -> Dict[str, Any]:
    dur = float(data.get("duration_sec") or duration_hint or 0) or float(duration_hint or 0)
    segs = []
    for s in data.get("segments") or []:
        try:
            st, en = float(s.get("start") or 0), float(s.get("end") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        role = str(s.get("role") or "other").strip().lower()
        segs.append({"start": round(st, 1), "end": round(max(en, st), 1), "role": role if role in SEGMENT_ROLES else "other",
                     "summary": str(s.get("summary") or "")[:40]})
    q = data.get("quote") if isinstance(data.get("quote"), dict) else {}
    ost = data.get("on_screen_text") if isinstance(data.get("on_screen_text"), dict) else {}
    cta = data.get("cta") if isinstance(data.get("cta"), dict) else {}
    out = {
        "relevant": data.get("relevant") is True or str(data.get("relevant")).lower() == "true",
        "content_type": str(data.get("content_type") or "other")[:30],
        "duration_sec": round(dur, 1),
        "voice": str(data.get("voice") or "")[:10],
        "segments": segs,
        "quote": {"present": bool(q.get("present")), "read_aloud": bool(q.get("read_aloud")),
                  "card": bool(q.get("shown_as_text_card")), "seconds": float(q.get("seconds") or 0)},
        "books_featured": int(data.get("books_featured") or 0) if str(data.get("books_featured") or "0").isdigit() else 0,
        "font": str(ost.get("font") or "")[:15], "text_position": str(ost.get("position") or "")[:10],
        "text_density": str(ost.get("density") or "")[:10],
        "music": str(data.get("music") or "")[:20],
        "visual_style": str(data.get("visual_style") or "")[:60],
        "cta": {"present": bool(cta.get("present")), "type": str(cta.get("type") or "none")[:15], "at_sec": cta.get("at_sec")},
        "pacing": str(data.get("pacing") or "")[:10],
        "structure_template": str(data.get("structure_template") or "")[:160],
        "why_it_works": str(data.get("why_it_works") or "")[:200],
        "source": source,
    }
    out.update(narration_stats(str(data.get("narration") or ""), dur))     # 원문은 여기서 통계로만 쓰고 버림
    return out


def extract_structure(video_id: str, api_key: str, vertical: str = "book", duration_hint: Optional[float] = None,
                      model: str = "gemini-3.6-flash") -> Dict[str, Any]:
    from google import genai
    from google.genai import types
    from core.guardrail import validate_model_call
    from core.radar.hooks import to_watch_url

    client = genai.Client(api_key=api_key)
    prompt = STRUCTURE_PROMPT.format(topic=TOPIC.get(vertical, vertical), criteria=CRITERIA.get(vertical, vertical),
                                     roles=", ".join(SEGMENT_ROLES))
    res = client.models.generate_content(
        model=validate_model_call(model, category="llm"),
        contents=types.Content(parts=[types.Part(file_data=types.FileData(file_uri=to_watch_url(video_id))),
                                      types.Part(text=prompt)]),
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
    )
    data = json.loads(res.text)
    if isinstance(data, list):
        data = data[0] if data else {}
    return normalize(data, duration_hint, source=f"gemini:{model}")


def try_extract_structure(video_id: str, api_key: Optional[str], vertical: str, duration_hint: Optional[float] = None,
                          dry_run: bool = False) -> Optional[Dict[str, Any]]:
    if dry_run or not api_key:
        return normalize({"relevant": True, "content_type": "mock", "duration_sec": duration_hint or 30,
                          "narration": "(dry-run)", "segments": [], "structure_template": "(dry-run)"},
                         duration_hint, source="mock")
    try:
        return extract_structure(video_id, api_key, vertical, duration_hint)
    except Exception as e:
        logger.warning(f"전체 구조 분석 실패 ({video_id}): {e}")
        return None
