"""
core/store_facts.py - 매장 고유 팩트 4종 추출 (추상적 홍보 형용사는 버리고 '고유 명사 팩트'만)

  ① special_menus   메뉴판에서 흔한 메뉴명이 아니라 독특한 원재료·조리법 수식어가 붙은 메뉴명 (예: 눈꽃버섯 갈비탕, 굴돌솥비빔밥, 한돈짜글이)
  ② review_nouns    리뷰에서 형용사를 지우고 2회 이상 반복된 구체적 명사 (예: 격자 칼집, 통새송이버섯, 유기그릇 물냉면, 반찬 리필)
  ③ photo_tags      업로드 사진별로 눈에 실제 보이는 피사체만 (비전, 상상 금지) → 사진-대사 1:1 일치의 근거
  ④ location_context 주소의 상권 특성에 따른 방문 상황 (관광지 나들이 등. 주차·거리 같은 사실은 지어내지 않음)

원칙: 네이버 플레이스·리뷰 페이지는 robots.txt로 자동 접근이 막혀 있어 가져오지 않는다. ①②는 사장님이 붙여 넣은 메뉴판·리뷰 글에서만 뽑고,
뽑은 말은 모두 그 글 안에 실제로 있는 표현이어야 한다 (지어내지 않음). ③은 사진에서만, ④는 주소에서만.
"""
import hashlib
import json
import logging
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ad_factory.store_facts")

# ---------------------------------------------------------------- ① 특이 메뉴명 (결정적 추출: 입력 글에 있는 표현만)
BASE_MENU_WORDS = ("갈비탕", "갈비찜", "갈비", "비빔밥", "짜글이", "냉면", "국밥", "찌개", "전골", "구이", "삼겹살", "불고기", "칼국수", "수제비",
                   "돈까스", "돈가스", "파스타", "피자", "치킨", "순대", "족발", "보쌈", "해장국", "설렁탕", "곰탕", "막국수", "만두", "라면", "덮밥",
                   "샐러드", "스테이크", "라떼", "커피", "케이크", "초밥", "우동")      # 한 글자 기본어(밥·면·탕·죽·빵)는 문장 조각이 잡혀서 뺌
GENERIC_MENU_NAMES = {"돼지갈비", "소갈비", "갈비탕", "갈비", "된장찌개", "김치찌개", "비빔밥", "냉면", "물냉면", "비빔냉면", "공기밥", "국밥", "삼겹살",
                      "불고기", "라면", "음료", "콜라", "사이다", "소주", "맥주", "커피", "아메리카노", "라떼", "치킨", "피자", "파스타", "돈까스", "돈가스",
                      "순대국", "설렁탕", "곰탕", "칼국수", "만두", "김밥", "떡볶이", "볶음밥", "된장", "김치"}
MENU_NOISE = re.compile(r"(\d[\d,.]*\s*(원|천원|만원|g|kg|ml|인분|인|개|\+)?|[()\[\]{}·•▶★※※~*/|:：,;]+|추천|인기|대표|세트|메뉴|사이드|주류|음료|포함|가격)")


def extract_special_menus(menu_text: str, limit: int = 6) -> List[str]:
    """메뉴판 글에서 '독특한 수식어가 앞에 붙은' 메뉴명만. 흔한 메뉴명(돼지갈비 등) 그 자체는 버림. 글에 있는 표현 그대로"""
    found: List[str] = []
    for raw in re.split(r"[\n\r]+", menu_text or ""):
        line = MENU_NOISE.sub(" ", raw)
        for phrase in re.findall(r"[가-힣A-Za-z]+(?:\s[가-힣A-Za-z]+)?", line):
            p = phrase.strip()
            if len(p) < 4 or p in GENERIC_MENU_NAMES:
                continue
            base = next((b for b in BASE_MENU_WORDS if p.replace(" ", "").endswith(b)), None)
            if not base:
                continue
            modifier = p.replace(" ", "")[: -len(base)]
            if len(modifier) < 2 or p.replace(" ", "") in GENERIC_MENU_NAMES or modifier in ("맛", "특", "매운", "순한", "큰", "작은", "한", "두"):
                continue
            if _VERBISH.search(modifier) or modifier.endswith(("면", "고", "서", "는", "은", "게", "의", "에", "도", "만")):
                continue                     # '하나면 밥'처럼 문장 조각이 수식어로 잡힌 것
            if len(base) == 1 and len(modifier) < 3:
                continue                     # 한 글자 기본어(밥·면·탕)는 수식어가 충분히 구체적일 때만
            if p not in found:
                found.append(p)
    return found[:limit]


# ---------------------------------------------------------------- ② 리뷰 형용사 제거 → 반복 명사 카운팅
_ADJ_PREFIX = ("맛있", "맛없", "친절", "최고", "좋", "훌륭", "깔끔", "괜찮", "만족", "추천", "강추", "대박", "멋", "예쁘", "푸짐", "든든", "신선",
               "부드럽", "쫄깃", "고소", "담백", "시원", "따뜻", "저렴", "비싸", "빠르", "느리", "재방문", "다음", "정말", "진짜", "너무", "완전",
               "아주", "매우", "항상", "같", "없", "많", "적", "넓", "크", "작", "싱싱", "독특", "특별", "존맛", "최상", "굿", "감사", "또", "다시")
_VERBISH = re.compile(r"(했|해요|하다|하게|합니다|있어|있는|있고|없는|없어|스러|럽|롭|이다|였|예요|에요|네요|어요|아요|았|었|겠|지만|는데|니까|으며|해서|드세요|하세요|"
                      r"좋아|좋은|좋고|맛있|맛나|같아|같은|드시|먹었|왔|갔|봤|싶|다$|요$|죠$|네$|음$|함$|한$|운$|은$|는$|던$|인$|ㄴ$)")
_REVIEW_STOP = {"사장님", "사장", "가게", "매장", "음식", "식당", "방문", "재방문", "사람", "가족", "주말", "오늘", "때문", "정도", "느낌", "생각", "가격",
                "분위기", "서비스", "직원", "종업원", "이모님", "리뷰", "후기", "영수증", "다음", "처음", "이번", "저희", "우리", "여기", "거기", "그냥",
                "하나", "이상", "근처", "친구", "부모님", "아이", "식사", "점심", "저녁", "손님", "자리", "시간", "한번", "번째", "계속", "하루", "맛", "양"}
_JOSA_TAIL = ("에서는", "에서", "으로", "에게", "까지", "부터", "이랑", "랑", "도", "만", "은", "는", "이", "가", "을", "를", "의", "에", "로", "과", "와", "고", "며")


def _trim_josa(tok: str) -> str:
    for suf in _JOSA_TAIL:
        if len(tok) - len(suf) >= 2 and tok.endswith(suf):
            return tok[: -len(suf)]
    return tok


def extract_review_nouns(review_text: str, min_count: int = 2, limit: int = 6) -> List[str]:
    """
    리뷰 글에서 형용사·서술어를 모두 지운 뒤 2회 이상 반복된 구체적 명사(짝)만 반환. 글에 실제로 반복된 말만이라 지어낼 수 없다.
    '유기그릇 물냉면'처럼 붙어 다니는 명사는 2어절(bigram)로 묶고, 그 구성어가 같은 횟수면 구성어는 버린다.
    """
    uni: Counter = Counter()
    bi: Counter = Counter()
    for sent in re.split(r"[\n\r.!?~…]+", review_text or ""):
        kept: List[Optional[str]] = []
        for tok in re.findall(r"[가-힣A-Za-z0-9]+", sent):
            if tok.startswith(_ADJ_PREFIX) or _VERBISH.search(tok):
                kept.append(None)              # 형용사·서술어: 삭제 (명사 짝이 끊김)
                continue
            n = _trim_josa(tok)
            if len(n) < 2 or n in _REVIEW_STOP or re.fullmatch(r"[0-9]+", n):
                kept.append(None)
                continue
            kept.append(n)
        for i, n in enumerate(kept):
            if n is None:
                continue
            uni[n] += 1
            if i + 1 < len(kept) and kept[i + 1] is not None:
                bi[f"{n} {kept[i + 1]}"] += 1
    out: List[str] = []
    used: set = set()
    for phrase, c in bi.most_common():
        if c >= min_count:
            out.append(phrase)
            used.update(phrase.split())
    for w, c in uni.most_common():
        if c >= min_count and w not in used:
            if any(w in o for o in out):
                continue
            out.append(w)
    return out[:limit]


# 음식·반찬·조리·시설 어휘: 공식 API(네이버) 요약문에 한 번이라도 나오면 방문자가 쓴 실제 기록으로 보고 확정에 쓴다 (형용사·검색용 문구는 어휘에 없음)
FACT_VOCAB = ("양념게장", "간장게장", "잡채", "계란찜", "된장찌개", "김치찌개", "비빔밥", "육회", "한우", "보쌈", "족발", "수육", "파전", "도토리묵", "샐러드",
              "겉절이", "더덕무침", "명이나물", "동치미", "잔치국수", "칼국수", "막국수", "곰탕", "설렁탕", "순두부", "불고기", "삼겹살", "목살", "항정살",
              "물냉면", "비빔냉면", "냉면", "갈비탕", "떡갈비", "양념갈비", "생갈비", "돼지갈비", "소갈비", "수제갈비", "쌈채소", "상추", "깻잎", "쌈장", "나물",
              "된장", "김치", "두부", "뚝배기", "돌솥밥", "공기밥", "볶음밥", "식혜", "숯불", "참숯", "석쇠", "불판", "칼집", "통버섯", "새송이", "버섯",
              "주차장", "무료주차", "개별룸", "단체석", "좌석", "셀프바", "반찬리필", "리필", "유아의자", "키즈", "포장", "배달", "예약", "웨이팅", "브레이크타임")


def food_vocab_hits(text: str, limit: int = 8) -> List[str]:
    """요약문에 한 번이라도 나온 음식·반찬·조리·시설 어휘 (많이 나온 순). 긴 단어가 이미 잡았으면 그 안의 짧은 단어는 뺌('물냉면'이 있으면 '냉면' 제외)"""
    t = re.sub(r"\s+", "", text or "")
    cnt = {w: t.count(w) for w in FACT_VOCAB if w in t}
    out: List[str] = []
    for w, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], -len(kv[0]))):
        if any(w != o and w in o for o in out):
            continue
        out.append(w)
    return out[:limit]


# ---------------------------------------------------------------- ④ 주소 → 방문 상황 (과장·사실 지어내기 없이 상권 성격만)
_TOURIST_REGIONS = ("가평", "양평", "춘천", "강릉", "속초", "양양", "제주", "경주", "여수", "전주", "통영", "태안", "남해", "거제", "포천", "파주", "남양주", "곡성")


def location_context(address: str) -> str:
    """주소의 상권 성격에 따른 방문 상황 한 줄. 관광지로 잘 알려진 지역만 '나들이·여행 중 가족 외식'. 주차·거리 같은 사실은 쓰지 않는다"""
    a = (address or "").strip()
    if not a:
        return ""
    for r in _TOURIST_REGIONS:
        if r in a:
            return f"{r} 나들이·여행 중에 가족과 들르는 외식"
    if re.search(r"역\s|대학|캠퍼스|대학로", a):
        return "역·대학가 근처에서 가볍게 찾는 한 끼"
    return ""


# ---------------------------------------------------------------- ③ 사진별 피사체 태깅 (비전)
def _photo_key(path: str) -> str:
    try:
        st = os.stat(path)
        return hashlib.md5(f"{os.path.abspath(path)}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:12]
    except OSError:
        return hashlib.md5(path.encode()).hexdigest()[:12]


BUCKET_LABELS = {1: "다 구워진 메인 요리 클로즈업·고기 자르기", 2: "전체 상차림·굽기 전 칼집/양념 원육", 3: "불판·석쇠 위에서 구워지는 조리 장면",
                 4: "냉면·찌개·탕·밑반찬 같은 곁들임", 5: "가게 외관·간판·내부 전경·주차장"}


def analyze_photos(media_paths: List[str], *, business_name: str = "", api_key: Optional[str] = None, thumb_dir: Optional[str] = None,
                   cache_path: Optional[str] = None, mock: bool = False) -> Tuple[Dict[str, str], Dict[str, int]]:
    """
    사진·영상 대표 프레임마다 비전으로 ① 실제로 보이는 피사체 한 줄(subject) ② 맛집 5단계 버킷 번호(bucket 1~5)를 한 번에 받는다
    → ({경로: 피사체}, {경로: 버킷}). 버킷: 1=다 구워진 메인 요리·고기 자르기 / 2=전체 상차림·굽기 전 원육 / 3=구워지는 조리 장면 /
    4=냉면·찌개·반찬 같은 곁들임 / 5=가게 외관·간판·내부·주차장. 상상·추측 금지. 같은 사진은 캐시(cache_path)로 다시 부르지 않는다
    """
    media = [p for p in (media_paths or []) if p][:8]
    if mock or not media:
        return {}, {}
    cache: Dict[str, Any] = {}
    if cache_path and os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path, encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}

    def entry(p: str) -> Dict[str, Any]:
        v = cache.get(_photo_key(p))
        return v if isinstance(v, dict) else ({"s": v} if v else {})          # 옛 캐시(문자열)는 피사체만 있는 것으로

    todo = [p for p in media if not entry(p).get("asked")]               # 이미 물어본 사진은 다시 부르지 않음 (엔진이 버킷을 안 줘도 반복 호출 방지)
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
            rubric = "\n".join(f"  {k}번: {v}" for k, v in BUCKET_LABELS.items())
            contents.append(f"""[역할: 로컬 매장 고유 팩트 발굴 전문 AI PD]
가게: {business_name or '(상호 미입력)'}
위 사진·영상 대표 프레임 {len(order)}장 각각에 대해 두 가지를 정확히 기록하세요.
1) subject: 프레임 안에 실제로 눈으로 보이는 피사체만 한 줄(공백 포함 40자 이내). 보이는 사물·음식·식기·불판·좌석·간판·외관만 (예: '참숯 불판 위 노릇한 갈비', '가게 외관 간판').
   맛·분위기·형용사, 사진에 없는 메뉴·재료·수량은 절대 쓰지 마세요.
2) bucket: 아래 5단계 중 가장 알맞은 번호 하나.
{rubric}
   (가게 외관·간판·주차장·매장 내부 전경은 반드시 5번. 먹음직스럽게 다 구워진 고기 클로즈업이나 고기 자르는 장면은 1번.)
JSON만 출력: {{"photos": [{{"index": 1, "subject": "...", "bucket": 1}}, ...]}}""")
            try:
                data, _ = generate_json(contents, "당신은 사진에 보이는 사실만 기록하는 꼼꼼한 사진 분석가입니다. 응답은 JSON만 출력합니다.",
                                        temperature=0.2, gemini_api_key=api_key, purpose="사진 피사체 태깅")
                for item in (data.get("photos") if isinstance(data, dict) else None) or []:
                    k = int(item.get("index") or 0) - 1
                    subj = re.sub(r"\s+", " ", str(item.get("subject") or "")).strip()
                    try:
                        bk = int(item.get("bucket") or 0)
                    except (TypeError, ValueError):
                        bk = 0
                    if 0 <= k < len(order):
                        e = dict(entry(order[k]))
                        e["asked"] = True
                        if subj:
                            e["s"] = subj[:60]
                        if bk in BUCKET_LABELS:
                            e["b"] = bk
                        cache[_photo_key(order[k])] = e
            except Exception as e:
                logger.warning(f"사진 피사체·버킷 분석 실패 (분석 없이 진행): {e}")
        if cache_path:
            try:
                json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
            except OSError:
                pass
    tags = {p: entry(p)["s"] for p in media if entry(p).get("s")}
    buckets = {p: int(entry(p)["b"]) for p in media if entry(p).get("b")}
    return tags, buckets


def tag_photos(media_paths: List[str], *, business_name: str = "", api_key: Optional[str] = None, thumb_dir: Optional[str] = None,
               cache_path: Optional[str] = None, mock: bool = False) -> Dict[str, str]:
    """사진마다 실제로 보이는 사물 한 줄 → {경로: 피사체} (analyze_photos의 피사체 부분)"""
    return analyze_photos(media_paths, business_name=business_name, api_key=api_key, thumb_dir=thumb_dir, cache_path=cache_path, mock=mock)[0]


# ---------------------------------------------------------------- 묶음 / 가게 자랑(USP) 반영
def build_store_facts(*, menu_text: str = "", review_text: str = "", address: str = "") -> Dict[str, Any]:
    """메뉴판·리뷰 글·주소에서 ①②④ 추출 (③ 사진 태그는 tag_photos가 따로)"""
    return {"special_menus": extract_special_menus(menu_text), "review_nouns": extract_review_nouns(review_text),
            "location_context": location_context(address)}


def merge_facts(*parts: Dict[str, Any], limit: int = 8) -> Dict[str, Any]:
    """여러 출처의 팩트를 합침 (앞쪽 출처가 우선, 중복 제거). location_context는 처음 있는 값"""
    out: Dict[str, Any] = {"special_menus": [], "review_nouns": [], "location_context": ""}
    for part in parts:
        for key in ("special_menus", "review_nouns"):
            for v in (part or {}).get(key) or []:
                if v and v not in out[key] and len(out[key]) < limit:
                    out[key].append(v)
        if not out["location_context"] and (part or {}).get("location_context"):
            out["location_context"] = part["location_context"]
    return out


def facts_to_points(facts: Dict[str, Any]) -> List[str]:
    """가게 자랑(USP) 칸에 보탤 짧은 구절 (이미 있는 말은 호출하는 쪽에서 중복 제거)"""
    pts: List[str] = []
    pts += [m for m in (facts.get("special_menus") or [])[:3]]
    rn = (facts.get("review_nouns") or [])[:4]
    if rn:
        pts.append("손님 리뷰에서 반복된 것: " + ", ".join(rn))
    if facts.get("location_context"):
        pts.append(str(facts["location_context"]))
    return pts


def facts_prompt_block(facts: Dict[str, Any], photo_lines: List[str]) -> str:
    """대본 프롬프트에 넣는 팩트 블록. 이 블록에 없는 고유 명사는 쓰지 못하게 한다"""
    lines = ["[이 매장의 입력 팩트 — 대본의 고유 명사는 오직 여기와 특장점(USP)에서만 가져오세요]"]
    sm, rn = facts.get("special_menus") or [], facts.get("review_nouns") or []
    lines.append(f"- special_menus(특이 메뉴명): {', '.join(sm) if sm else '(없음)'}")
    lines.append(f"- review_nouns(리뷰에서 2회 이상 반복된 명사): {', '.join(rn) if rn else '(없음)'}")
    lines.append(f"- location_context(방문 상황): {facts.get('location_context') or '(없음)'}")
    if photo_lines:
        lines.append("- photo_tags(사진 속에 실제로 보이는 것 — 컷 배치 참고용. 나레이션이 사진을 따라가며 주제를 바꾸면 안 됩니다):")
        lines += [f"    {ln}" for ln in photo_lines]
    return "\n".join(lines)
