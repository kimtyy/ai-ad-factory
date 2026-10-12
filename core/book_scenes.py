"""
core/book_scenes.py - 시골책방 씬별 이미지 매칭

- 사진 후보 = 이번 영상에 올린 책방 사진(일정 inbox 폴더·화면 업로드) + 재사용 책방 사진 라이브러리(.env BOOK_LIBRARY_DIR,
  기본 inbox/_책방사진). 실제 사진만 (AI 이미지 생성 없음)
- 사진마다 한 번 AI 비전으로 설명(무엇이 보이는지·분위기)을 붙여 캐시(outputs/books/library_tags.json) → 장면 대사와 가장 어울리는
  사진을 AI가 배정 (같은 사진 연속 금지). 키가 없거나 실패하면 순서대로 돌려 씀
- 사진이 하나도 없으면 표지를 아주 흐리게(제목이 안 읽히게) 깐 분위기 배경 → 책 제목은 마지막 장면에서만 보임
- 바꾸는 장면: 책방·분위기 장면(전체 화면, 표지·펼친 면이 아닌 것). 표지 카드·책 공개 CTA·펼친 면 사진은 그대로
"""
import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional

from PIL import Image, ImageEnhance, ImageFilter

logger = logging.getLogger("ad_factory.book_scenes")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA_EXTS = (".jpg", ".jpeg", ".png", ".webp")
TAG_BATCH = 6
MOOD_BLUR = 48                 # 1080px 기준 - 표지 글자가 읽히지 않을 만큼


def library_dir() -> str:
    return os.getenv("BOOK_LIBRARY_DIR", "").strip() or os.path.join(ROOT, "inbox", "_책방사진")


def cache_path() -> str:
    return os.path.join(ROOT, "outputs", "books", "library_tags.json")


def library_photos() -> List[str]:
    d = library_dir()
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(MEDIA_EXTS)]


def _key(path: str) -> str:
    st = os.stat(path)
    return hashlib.md5(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]


def _load_cache() -> Dict[str, Any]:
    try:
        with open(cache_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(c: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(cache_path()), exist_ok=True)
    tmp = cache_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cache_path())


DESCRIBE_SYSTEM = "당신은 숏폼 영상 편집자입니다. 사진을 보고 영상 장면 배정에 쓸 짧은 설명을 씁니다. 반드시 JSON 하나만 출력합니다."


def describe_photos(paths: List[str], api_key: Optional[str] = None, mock: bool = False) -> Dict[str, Dict[str, Any]]:
    """사진별 {desc, mood} (캐시 우선). AI를 못 쓰면 파일 이름을 설명으로"""
    from core.llm import script_llm_ready
    cache = _load_cache()
    out, todo = {}, []
    for p in paths:
        k = _key(p)
        if k in cache:
            out[p] = cache[k]
        else:
            todo.append(p)
    if todo and not mock and script_llm_ready(api_key):
        from core.llm import generate_json
        for i in range(0, len(todo), TAG_BATCH):
            batch = todo[i:i + TAG_BATCH]
            contents: List[Any] = []
            for n, p in enumerate(batch, 1):
                try:
                    im = Image.open(p).convert("RGB")
                    im.thumbnail((768, 768))
                    contents += [f"[사진 {n}]", im]
                except Exception:
                    continue
            contents.append("각 사진에 보이는 것(공간·사물·빛)과 분위기를 한국어로 짧게 적으세요. 보이지 않는 것은 쓰지 마세요.\n"
                            'JSON만: {"photos": [{"index": 1, "desc": "창가 자리에 오후 햇살, 나무 책상", "mood": "따뜻함"}]}')
            try:
                data, _ = generate_json(contents, DESCRIBE_SYSTEM, temperature=0.2, max_tokens=1500, gemini_api_key=api_key,
                                        purpose="책방 사진 설명")
                for item in (data.get("photos") if isinstance(data, dict) else []) or []:
                    k = int(item.get("index") or 0) - 1
                    if 0 <= k < len(batch):
                        out[batch[k]] = {"desc": str(item.get("desc") or "")[:80], "mood": str(item.get("mood") or "")[:20]}
                        cache[_key(batch[k])] = out[batch[k]]
            except Exception as e:
                logger.warning(f"책방 사진 설명 실패 → 파일 이름 사용: {e}")
        _save_cache(cache)
    for p in paths:
        out.setdefault(p, {"desc": os.path.splitext(os.path.basename(p))[0].replace("_", " "), "mood": ""})
    return out


def mood_cover(cover: str, out_dir: str) -> str:
    """표지를 아주 흐리고 어둡게 → 분위기 배경 (제목이 안 읽힘)"""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "book_mood_bg.jpg")
    im = Image.open(cover).convert("RGB")
    w, h = im.size
    scale = 1080 / max(1, w)
    im = im.resize((1080, max(1, int(h * scale))), Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(MOOD_BLUR))
    im = ImageEnhance.Brightness(im).enhance(0.62)
    im.save(path, "JPEG", quality=92)
    return path


MATCH_SYSTEM = "당신은 숏폼 영상 편집자입니다. 장면 대사마다 가장 어울리는 실제 사진을 고릅니다. 반드시 JSON 하나만 출력합니다."


def _llm_assign(lines: List[str], descs: List[Dict[str, Any]], api_key: Optional[str]) -> Optional[List[int]]:
    from core.llm import generate_json
    photos = "\n".join(f"{i + 1}. {d['desc']}" + (f" (분위기: {d['mood']})" if d.get("mood") else "") for i, d in enumerate(descs))
    scenes = "\n".join(f"장면 {i + 1}: {t}" for i, t in enumerate(lines))
    prompt = (f"[사진]\n{photos}\n\n[장면 대사]\n{scenes}\n\n"
              "장면마다 대사의 분위기·상황과 가장 어울리는 사진 번호를 고르세요. 사진이 충분하면 같은 사진은 한 번만, "
              "부족하면 다시 써도 되지만 같은 사진이 연달아 나오면 안 됩니다.\n"
              'JSON만: {"assign": [{"scene": 1, "photo": 3}]}')
    data, _ = generate_json([prompt], MATCH_SYSTEM, temperature=0.2, max_tokens=800, gemini_api_key=api_key, purpose="장면 사진 배정")
    pick = {int(a.get("scene") or 0): int(a.get("photo") or 0) for a in (data.get("assign") if isinstance(data, dict) else []) or []
            if isinstance(a, dict)}
    out = [pick.get(i + 1, 0) - 1 for i in range(len(lines))]
    if any(not (0 <= k < len(descs)) for k in out):
        return None
    return out


def _no_repeats(idx: List[int], n: int) -> List[int]:
    """같은 사진이 연달아 나오지 않게 (사진이 2장 이상일 때)"""
    if n < 2:
        return idx
    for i in range(1, len(idx)):
        if idx[i] == idx[i - 1]:
            idx[i] = (idx[i] + 1) % n
    return idx


def assign_visuals(scenes: List[Dict[str, Any]], cover: Optional[str], page: Optional[str], event_photos: List[str],
                   api_key: Optional[str] = None, mock: bool = False, out_dir: str = ".") -> List[str]:
    """책방·분위기 장면의 사진을 대사에 맞춰 다시 고름 → 기록(log) 목록. 씬 dict의 source_path·layout을 바꿈"""
    # 펼친 면 사진은 원문 인용 장면의 배경으로만 고정 (다른 장면은 매칭 대상)
    targets = [s for s in scenes if s.get("role") != "cta" and not (page and s.get("source_path") == page and s.get("role") == "quote")
               and (s.get("layout") == "full" or s.get("role") == "store")]
    if not targets:
        return []
    pool = list(dict.fromkeys([p for p in event_photos if p and p not in (cover, page)] + library_photos()))
    if not pool:
        if not cover:
            return []
        bg = mood_cover(cover, out_dir)
        for s in targets:
            s["source_path"], s["layout"] = bg, "full"
            s.pop("caption_center_y", None)
        return [f"책방 사진이 없어 표지를 흐리게 깐 분위기 배경 사용 (제목은 마지막 장면에서 공개) - {len(targets)}개 장면"]
    descs = describe_photos(pool, api_key, mock)
    lines = [s.get("voiceover", "") for s in targets]
    idx = None
    from core.llm import script_llm_ready
    if not mock and script_llm_ready(api_key) and len(pool) > 1:
        try:
            idx = _llm_assign(lines, [descs[p] for p in pool], api_key)
        except Exception as e:
            logger.warning(f"장면 사진 배정 실패 → 순서대로: {e}")
    how = "AI가 대사에 맞춰 배정" if idx else "순서대로 배정"
    idx = _no_repeats(idx or [i % len(pool) for i in range(len(targets))], len(pool))
    for s, k in zip(targets, idx):
        s["source_path"], s["layout"] = pool[k], "full"
        s.pop("caption_center_y", None)
    used = len(set(idx))
    return [f"책방 사진 {len(pool)}장 중 {used}장을 {len(targets)}개 장면에 {how} "
            f"(라이브러리 {len([p for p in pool if p in library_photos()])}장)"]
