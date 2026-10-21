"""
core/stay_photos.py - 숙소 제휴 4단계(사진 쪽): 숙소 전용 비전 분류(블랙리스트 제외) + 3-2-2 정렬 + 후보 사진 수집

사진 수집은 core.store_photos(네이버 공식 이미지 검색)를 그대로 쓰고, 여기어때 상세에서 읽은 사진 주소가 있으면 앞에 합친다.
비전 버킷: 1 = 물·스파(인피니티풀·온수풀·노천 자쿠지·히노끼탕) / 2 = 통창 뷰·객실 개방감(거실·침실 광각) / 3 = 바비큐·불멍·조식·야경·숙소 전경 /
          0 = 제외(변기·세면대 클로즈업, 빈 싱크대·냉장고, 복도·비상구, 주차장 바닥, 사람·워터마크·영수증)
정렬: 앞 3컷 = 버킷 1, 중반 2컷 = 버킷 2, 후반 2컷 = 버킷 3 (7컷). 부족한 버킷은 이웃 버킷의 남는 사진으로 채움.
"""
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ad_factory.stay_photos")

BUCKET_LABELS = {
    1: "물·스파 (인피니티풀·온수풀·노천 자쿠지·히노끼탕 등 가장 비싸 보이는 물 컷)",
    2: "통창 뷰·객실 개방감 (창밖으로 강·바다·숲이 보이는 거실·침실 광각 컷)",
    3: "바비큐·불멍·조식·야경·숙소 전경 (프라이빗 바비큐, 불멍, 조식, 숙소 야경·외관 전경)",
    0: "제외 (변기·세면대 클로즈업, 빈 싱크대·냉장고, 복도·비상구, 주차장 아스팔트, 사람의 얼굴·신체(손·뒷모습 포함)가 보이는 컷, 블로거 서명·개인 워터마크·타사 로고, 영수증·글씨 위주 안내문)",
}
QUOTA = {1: 3, 2: 2, 3: 2}                  # 3-2-2
CACHE_VERSION = 1


def _key(path: str) -> str:
    try:
        st = os.stat(path)
        return hashlib.md5(f"{os.path.basename(path)}|{st.st_size}".encode()).hexdigest()[:16]
    except OSError:
        return hashlib.md5(path.encode()).hexdigest()[:16]


def classify_stay_photos(paths: List[str], *, stay_name: str = "", api_key: Optional[str] = None, thumb_dir: Optional[str] = None,
                         cache_path: Optional[str] = None, mock: bool = False) -> Tuple[Dict[str, str], Dict[str, int]]:
    """
    사진마다 비전으로 ① 실제 보이는 피사체 한 줄 ② 숙소 버킷(0~3)을 받는다 → ({경로: 피사체}, {경로: 버킷}).
    같은 사진은 캐시로 다시 부르지 않음. 비전을 못 쓰면(키 없음·Mock·실패) 빈 결과 → 호출자는 올린 순서를 그대로 쓴다
    """
    media = [p for p in (paths or []) if p][:16]
    if mock or not media:
        return {}, {}
    cache: Dict[str, Any] = {}
    if cache_path and os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path, encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    todo = [p for p in media if (cache.get(_key(p)) or {}).get("v") != CACHE_VERSION]
    if todo:
        from core.llm import generate_json
        from core.planners.owner import _media_preview_image
        contents: List[Any] = []
        order: List[str] = []
        for p in todo:
            img = _media_preview_image(p, thumb_dir)
            if img is not None:
                contents += [f"[사진 {len(order) + 1}]", img]
                order.append(p)
        if order:
            rubric = "\n".join(f"- Bucket {k}: {v}" for k, v in BUCKET_LABELS.items())
            contents.append(f"""[역할: 숙소 쇼츠 3-2-2 시각 정렬기]
숙소: {stay_name or '(이름 미입력)'}
사진 {len(order)}장을 분석해 각 사진마다 두 가지를 기록하세요.
1) subject: 프레임에 실제로 보이는 피사체만 한 줄(공백 포함 40자 이내). 사진에 없는 시설·뷰는 쓰지 마세요.
2) bucket: 아래 중 하나.
{rubric}
   * 수영장·자쿠지·욕조가 시원하게 보이면 1. 침실·거실이라도 창밖 풍경이 크게 보이면 2. 화장실은 변기·세면대가 중심이면 0, 욕조·스파가 중심이면 1.
JSON만 출력: {{"photos": [{{"index": 1, "subject": "...", "bucket": 1}}, ...]}}""")
            try:
                data, _ = generate_json(contents, "당신은 사진에 보이는 사실만 기록하는 꼼꼼한 사진 분석가입니다. 응답은 JSON만 출력합니다.",
                                        temperature=0.2, gemini_api_key=api_key, purpose="숙소 사진 분류")
                for item in (data.get("photos") if isinstance(data, dict) else None) or []:
                    k = int(item.get("index") or 0) - 1
                    try:
                        bk = int(item.get("bucket"))
                    except (TypeError, ValueError):
                        continue
                    if 0 <= k < len(order) and bk in BUCKET_LABELS:
                        cache[_key(order[k])] = {"v": CACHE_VERSION, "b": bk,
                                                 "s": re.sub(r"\s+", " ", str(item.get("subject") or "")).strip()[:60]}
            except Exception as e:
                logger.warning(f"숙소 사진 분류 실패 (분류 없이 진행): {e}")
        if cache_path:
            try:
                json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
            except OSError:
                pass
    tags = {p: cache[_key(p)]["s"] for p in media if cache.get(_key(p), {}).get("s")}
    buckets = {p: int(cache[_key(p)]["b"]) for p in media if "b" in (cache.get(_key(p)) or {})}
    return tags, buckets


def order_stay_photos(paths: List[str], buckets: Dict[str, int]) -> Dict[str, List[str]]:
    """
    3-2-2 정렬 → {"ordered": 최대 7장(물3·뷰2·바비큐/야경2 순), "excluded": 버킷 0, "unused": 남는 사진}.
    부족한 버킷은 이웃 버킷의 남는 사진으로 채움 (1은 2→3, 2는 3→1, 3은 2→1 순). 분류가 전혀 없으면 올린 순서 그대로 앞 7장
    """
    paths = list(dict.fromkeys(paths or []))
    if not buckets:
        return {"ordered": paths[:7], "excluded": [], "unused": paths[7:]}
    excluded = [p for p in paths if buckets.get(p) == 0]
    pool = {k: [p for p in paths if buckets.get(p) == k] for k in (1, 2, 3)}
    unknown = [p for p in paths if buckets.get(p) not in (0, 1, 2, 3)]
    chosen: Dict[int, List[str]] = {1: [], 2: [], 3: []}
    for k in (1, 2, 3):
        chosen[k] = pool[k][:QUOTA[k]]
        pool[k] = pool[k][QUOTA[k]:]
    near = {1: (2, 3), 2: (3, 1), 3: (2, 1)}
    for k in (1, 2, 3):
        for nb in near[k]:
            while len(chosen[k]) < QUOTA[k] and pool[nb]:
                chosen[k].append(pool[nb].pop(0))
        while len(chosen[k]) < QUOTA[k] and unknown:
            chosen[k].append(unknown.pop(0))
    ordered = chosen[1] + chosen[2] + chosen[3]
    unused = pool[1] + pool[2] + pool[3] + unknown
    return {"ordered": ordered, "excluded": excluded, "unused": unused}


def search_stay_candidates(name: str, region: str, cid: str, secret: str, yeogi_images: Optional[List[Dict[str, Any]]] = None,
                           official_only: bool = True) -> Dict[str, Any]:
    """
    갤러리 후보: 여기어때에서 읽은 공식 사진(있으면) + 네이버 공식 이미지 검색(filter=large, 400px 이상) → {"items": [...최대 12], "error"}.
    여기어때 사진은 제목 필터 없이 앞에 (이미 그 숙소의 사진)
    """
    from core.store_photos import MAX_CANDIDATES, search_store_images
    res = search_store_images(name, region, cid, secret, suffix="객실 수영장", official_only=official_only)
    from core.photo_safety import rank_sources
    items = rank_sources(list(yeogi_images or []), official_only=False) + list(res.get("items") or [])
    seen, out = set(), []
    for it in items:
        if it.get("link") and it["link"] not in seen:
            seen.add(it["link"])
            out.append(it)
    return {"items": out[:MAX_CANDIDATES], "error": res.get("error", "") if not out else "", "blog_dropped": res.get("blog_dropped", 0)}
