"""
core/planners/owner.py - 트랙 B: 사장님(펜션/음식점/매장) 직영 홍보 숏폼 플래너

제휴 쇼츠(트랙 A)와 반대 원칙으로 동작합니다.
- 상호명 보존: 대본·후킹에 상호명을 그대로 노출 (트랙 A는 숙소명 은닉)
- 미디어 우선(Asset-First): 사장님이 올린 사진/영상 N개를 순서대로 보고 씬 N개의 대사를 작성
  (트랙 A는 대본을 먼저 쓰고 크롤링 사진을 매칭하는 Script-First)
- 엔딩 CTA: '댓글에 여행' 대신 네이버 플레이스 검색 유도
- 허위 방지: 가격/할인/순위 등 사장님이 입력한 USP에 없는 수치·혜택 표현은 자동 차단
"""
import io
import json
import re
import logging
import os
from typing import Any, Dict, List, Optional

from core.script_guard import (fix_voice_agreement, photo_subject_issues, VO_HARD_MAX, valid_cta, find_example_copy, find_invented_menu, find_name_leak, complete_sentence, diversify_exclamations, find_promo_tone,
                                find_banned_style, find_concept_drift,
                                find_unsupported_claim, is_hook_echo, shorten_line, strip_brochure_words, to_haeyo,
                                concept_fallback_line, repeated_line_issues)
from core.llm import script_llm_ready
from core.radar.hints import HookHints, copies_reference
from dataclasses import replace as _dc_replace
from core.mode_policy import (RAP_POLICY, OWNER_POLICY, clamp_line_to_policy, complete_owner_sentence,
                               create_length_violation_checker, build_ad_system_prompt, build_one_concept_guidelines)

logger = logging.getLogger("ad_factory.planners.owner")

BUSINESS_TYPES = {
    "stay": {"label": "🏡 펜션·숙소", "roles": ["exterior", "room", "facility", "view", "detail"]},
    "food": {"label": "🍽 음식점·카페", "roles": ["exterior", "signature", "cooking", "interior", "detail"]},
    "shop": {"label": "🛍 매장·기타", "roles": ["exterior", "product", "interior", "detail", "service"]},
}
DEFAULT_BUSINESS_TYPE = "food"

ROLE_LABELS_KO = {
    "exterior": "외관·입구", "room": "객실", "facility": "부대시설", "view": "주변 풍경", "detail": "디테일",
    "signature": "대표 메뉴", "cooking": "조리 장면", "interior": "실내 분위기", "product": "대표 상품",
    "service": "서비스", "cta": "검색 유도",
}

MOCK_LINES = {
    "stay": {"exterior": "골목 안으로 들어오시면, 아늑한 휴식이 기다려요!",
             "room": "포근하고 아늑한 침실에서, 편안하게 쉬어가세요!",
             "facility": "다양한 부대시설이 있어서, 하루 종일 머물기 좋아요!",
             "view": "창밖으로 탁 트인 풍경 보며, 여유롭게 힐링해 보세요!",
             "detail": "소품 하나까지 직접 고른, 편안한 공간이에요!"},
    "food": {"exterior": "찾아오기 정말 편한 위치라, 언제든 가볍게 들러보세요!",
             "signature": "고민 없이 바로 고르는, 매장의 인기 대표 메뉴예요!",
             "cooking": "주문이 들어오면 그때부터 조리를 시작해요!",
             "interior": "쾌적하고 널찍한 실내 공간에서, 편안하게 식사해 보세요!",
             "detail": "신선한 재료만 듬뿍 담아서, 든든하고 만족스러워요!"},
    "shop": {"exterior": "찾아오기 정말 쉬운 위치에서, 항상 활짝 열려 있어요!",
             "product": "많은 분들이 꾸준히 찾아주시는, 인기 만점 대표 상품이에요!",
             "interior": "쾌적하고 넓은 매장이라서, 편하게 둘러보기 좋아요!",
             "detail": "하나하나 꼼꼼하게 엄선한, 알찬 구성으로 준비했어요!",
             "service": "언제 오셔도 친절하게, 기분 좋은 안내를 약속드려요!"},
}


def sanitize_signboard_words(text: str) -> str:
    """
    사진 속 안내판 글자를 인식할 때 튀어나올 수 있는 딱딱한 메타 단어('문구', '표시', '안내판') 완화 및 오타 교정
    """
    s = (text or "").strip()
    if not s:
        return ""
    # 1. 흔한 오타 및 띄어쓰기 교정
    s = s.replace("안정상비의약품", "안전상비의약품")
    s = s.replace("안정 상비의약품", "안전상비의약품")

    # 2. 메타 단어 자연스러운 구어체 정제
    # '...카드 문구, ...' -> '...카드 사용 매장이니, ...'
    s = re.sub(r"([가-힣]+(?:카드|페이|상품권))\s*문구(?:\s*[,.]?|\s+)", r"\1 사용 매장이니, ", s)
    # '...판매점 표시, ...' -> '... 판매점이니, ...'
    s = re.sub(r"([가-힣]*의약품)\s*판매점\s*표시(?:\s*[,.]?|\s+)", r"\1 판매점이니, ", s)
    # 일반적인 '안내판 속/에' 제거
    s = re.sub(r"(?:입구\s*)?(?:안내판|안내문|스티커)\s*(?:속|에)?\s*", "", s)
    # 서술어가 문구/표시 뒤에 붙은 경우: '양주사랑카드 문구 확인하세요' -> '양주사랑카드 챙겨가세요'
    s = re.sub(r"\s+(?:문구|표시)(?:가|를|도|는)?\s+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def ensure_natural_pauses(text: str) -> str:
    """
    기계적으로 쉼표를 억지로 끼워 넣지 않고,
    사람이 말하듯 자연스러운 문맥의 호흡(주어/조건절 연결)일 때만 쉼표(,) 배치
    """
    s = (text or "").strip()
    if not s:
        return ""
    # 쉼표 앞뒤 불필요한 공백 정돈
    s = re.sub(r"\s*,\s*", ", ", s)

    # 이미 쉼표가 있거나 16자 미만으로 짧은 문장은 인위적 쉼표 없이 온전하게 한 호흡으로 발화
    if "," in s or len(s) < 16:
        return s

    # 자연스러운 절 연결 포인트 (문맥상 사람이 숨을 쉬어가는 확실한 연결 어미 및 조건절)
    patterns = [
        r"([가-힣]{2,}(?:아서|어서|여서))\s+",
        r"([가-힣]{2,}(?:고|고요))\s+(?=[가-힣]{2,})",
        r"([가-힣]{2,}(?:는데|은데|인데))\s+",
        r"([가-힣]{2,}(?:으면|면))\s+",
        r"([가-힣]{2,}(?:으니까|니까|으니|으니깐))\s+",
        r"([가-힣]{2,}(?:이니|니))\s+(?=[가-힣]{2,})",
        r"((?:[가-힣]+\s+)?(?:때도|때는|때|시에도))\s+",
        r"([가-힣]{2,}(?:라면|다면))\s+",
    ]
    for p in patterns:
        m = re.search(p, s)
        if m:
            matched_word = m.group(1)
            end_pos = m.end()
            # 앞뒤로 최소 4자 이상의 내용이 있을 때만 자연스럽게 호흡 분할
            if 4 <= len(s[:m.start()] + matched_word) <= len(s) - 5:
                return s[:m.start()] + matched_word + ", " + s[end_pos:]

    # 패턴에 해당하지 않으면 억지로 쉼표를 넣지 않고 그대로 반환 (자연스러운 한 호흡 발화)
    return s

# 사장님이 입력하지 않은 수치/혜택/순위 주장 차단 규칙은 core.script_guard.PRICE_CLAIM_PATTERNS에서 관리


CTA_SAVE_REASON = {      # 단순 '검색해 보세요'가 아니라 지금 저장해야 하는 이유 (업종별 이용 장면)
    "food": "주말 외식 장소로 나중에 찾으면 늦어요, 지금 저장하고",
    "stay": "다음 휴가 숙소로 나중에 찾으면 늦어요, 지금 저장하고",
    "shop": "필요할 때 나중에 찾으면 늦어요, 지금 저장하고",
}


def short_region(region: str) -> str:
    """'경기 가평군' → '가평' (대사에서 자연스러운 짧은 지역명. '성동구'처럼 구는 그대로)"""
    last = (region or "").strip().split()[-1] if (region or "").strip() else ""
    return last[:-1] if len(last) > 2 and last.endswith(("시", "군")) else last


def owner_cta_tail(search_keyword: str, region: str = "") -> str:
    """CTA 끝 문구: '지금 저장하고 가평 효정명인갈비를 검색해 보세요!' (LLM이 쓰는 마지막 문장도 이 꼬리로 끝나야 함)"""
    from core.script_guard import with_josa
    kw = (search_keyword or "").strip()
    if not kw:
        return "지금 저장하고 네이버에서 검색해 보세요!"
    reg = short_region(region)
    target = f"{reg} {kw}" if reg and reg not in kw else kw
    return f"지금 저장하고 {with_josa(target, '을/를')} 검색해 보세요!"


CTA_SAVE_REASON_BY_CONTEXT = {      # 업종의 실제 방문 맥락에 맞는 저장 명분 (core.venue_context): 맥주집에 '주말 외식 장소'라고 하지 않게
    "bar": "저녁 먹고 한 잔 더 생각날 때 나중에 찾으면 늦어요, 지금 저장하고",
    "cafe": "식후 커피 한 잔 생각날 때 나중에 찾으면 늦어요, 지금 저장하고",
}


def owner_cta(search_keyword: str, region: str = "", business_type: str = "", context_kind: str = "") -> str:
    """마지막 씬 CTA: 저장 명분 + [지역명+상호명] 검색 ('주말 외식 장소로 나중에 찾으면 늦어요, 지금 저장하고 가평 효정명인갈비를 검색해 보세요!')"""
    from core.script_guard import with_josa
    kw = (search_keyword or "").strip()
    if not kw:
        return "지금 저장하고 네이버에서 검색해 보세요!"
    reg = short_region(region)
    target = f"{reg} {kw}" if reg and reg not in kw else kw
    reason = CTA_SAVE_REASON_BY_CONTEXT.get(context_kind) or CTA_SAVE_REASON.get(business_type, CTA_SAVE_REASON[DEFAULT_BUSINESS_TYPE])
    return f"{reason} {with_josa(target, '을/를')} 검색해 보세요!"


def _roles_for(business_type: str, count: int) -> List[str]:
    base = BUSINESS_TYPES.get(business_type, BUSINESS_TYPES[DEFAULT_BUSINESS_TYPE])["roles"]
    roles = [base[i % len(base)] for i in range(max(count - 1, 0))]
    return roles + ["cta"]


_CONTAINER_WORDS = {"잔", "접시", "그릇", "컵", "병", "모둠", "세트", "한잔", "한접시", "플레이트", "메뉴판"}
_PHOTO_TEMPLATES = ("{n} 한 잔이 생각나는 날 있죠?", "{n}, 이 집에서 꼭 맛봐야 하는 메뉴예요!", "{n}까지 곁들이면 더 든든해요!", "{n}도 놓치면 아쉬워요!")


def scrub_menus(text: str, banned: List[str]) -> str:
    """특장점(USP) 글에서 사진에 없는 메뉴 이름이 들어간 구절(쉼표·슬래시·줄 단위)을 뺀다"""
    if not text or not banned:
        return text
    from core.script_guard import find_unphotographed_menu
    parts = re.split(r"(?<=[,/·\n])", text)
    keep = [p for p in parts if not find_unphotographed_menu(p, banned, "x")]
    return re.sub(r"\s+", " ", "".join(keep)).strip(" ,/·")


_ADJ_END = ("게", "한", "은", "는", "운", "던", "친", "진", "른", "긴", "린", "낸", "온", "인", "된", "난")


_GENERIC_FOOD = {"요리", "메뉴", "모음", "음식", "양념", "여러", "한상", "한판"}


def _tag_items(tag: str) -> List[str]:
    """사진 설명 → 항목들. '구운 통오징어 한 접시와 땅콩' → ['구운 통오징어 한 접시', '땅콩'], '여러 메뉴 모음: A, B' → ['A', 'B']"""
    t = tag.split(":", 1)[1] if ":" in (tag or "") else (tag or "")
    t = re.sub(r"(접시|잔|그릇|컵|병|판)(와|과)\s+", lambda m: m.group(1) + " | ", t)
    return [x.strip() for x in re.split(r"[,，|]|\s+및\s", t) if x.strip()]


def _subject_phrase(part: str) -> str:
    """사진 설명 한 토막 → 문장에 넣을 피사체 말 (첫 항목의 중심 명사). '매콤하게 무친 골뱅이' → '골뱅이', '구운 통오징어 한 접시와 땅콩' → '통오징어', '치즈 요리' → '치즈 요리'"""
    from core.script_guard import _PHOTO_STOP
    items = _tag_items(part)
    for it in items:
        ws = [w for w in re.findall(r"[가-힣]{2,}", it) if w not in _PHOTO_STOP and w not in _CONTAINER_WORDS and not w.endswith(("접시", "그릇"))
              and not (len(w) >= 3 and w.endswith("잔"))]
        if not ws or all(w in _GENERIC_FOOD for w in ws):
            continue
        head = ws[-1]
        if head in _GENERIC_FOOD and len(ws) >= 2:
            return f"{ws[-2]} {head}"
        if len(ws) >= 2 and not ws[-2].endswith(_ADJ_END) and ws[-2] not in _GENERIC_FOOD and len(ws[-2]) + len(head) <= 8:
            return f"{ws[-2]} {head}"
        return head
    return ""


def _collage_items(tag: str) -> List[str]:
    """여러 메뉴가 한 장에 담긴 사진 → 구체적인 메뉴 이름들 ('양념 요리'처럼 일반어뿐인 항목은 제외)"""
    out: List[str] = []
    for it in _tag_items(tag):
        ph = _subject_phrase(it)
        if ph and not any(ph in x or x in ph for x in out):
            out.append(ph)
    return out


_PHOTO_TEMPLATES_SHORT = ("{n}, 한 입이면 끝!", "{n}, 놓치면 아쉬워요!", "{n}까지 완벽해요!", "{n} 이 맛이에요!")


def photo_fallback_line(tag: str, index: int = 0, core_concept: str = "", short: bool = False) -> str:
    """그 문장이 나오는 사진 속 피사체(tag)를 직접 말하는 안전 문구. LLM 대사가 사진과 어긋나 버려질 때 쓴다 (피사체를 모르면 빈 문자열)"""
    from core.script_guard import _PHOTO_STOP
    if " / " in (tag or ""):                                  # 컷 여러 장이 빠르게 전환되는 문장: 피사체를 순서대로 호명
        subs = []
        for part in tag.split(" / "):
            s_ = _subject_phrase(part)
            if s_ and not any(s_ in x or x in s_ for x in subs):        # 같은 안주를 두 번 부르지 않음 ('오징어와 오징어')
                subs.append(s_)
        if len(subs) >= 2:
            return f"{', '.join(subs[:-1])}에 {subs[-1]}까지 안주 라인업이 완벽해요!"
    if ":" in (tag or "") or (tag or "").count(",") >= 2:             # 여러 메뉴가 한 장에 담긴 사진: 구체적인 메뉴 이름 2~3개를 호명
        items = _collage_items(tag)
        if len(items) >= 2:
            return (f"{', '.join(items[:2])}까지 한 상에!" if short else f"{', '.join(items[:-1][:2])}에 {items[:3][-1]}까지 한 상에 다 있어요!")
    subject = _subject_phrase(tag)                                  # '과일 화채', '반건조 오징어'처럼 명사 끝, 꾸미는 말은 한 낱말까지
    if not subject:
        return ""
    if any(w in subject for w in ("매장", "홀", "내부", "실내", "좌석", "입구", "외관", "간판")):
        return f"{subject}에서 편하게 즐기기 좋아요!"                      # 공간 사진은 메뉴 문구 대신
    if short:
        return _PHOTO_TEMPLATES_SHORT[index % len(_PHOTO_TEMPLATES_SHORT)].format(n=subject)
    k = 0 if index == 0 else 1 + (index - 1) % (len(_PHOTO_TEMPLATES) - 1)
    return _PHOTO_TEMPLATES[k].format(n=subject)


def _unsupported_claim(text: str, usp: str) -> Optional[str]:
    # 사장님 모드는 Gemini가 사진을 직접 보고 쓰므로 시설 묘사는 허용, 가격·할인·순위는 USP에 적힌 것만 허용
    return find_unsupported_claim(text, usp=usp, check_amenities=False)


def _media_preview_image(path: str, thumb_dir: Optional[str]):
    """사진은 그대로, 영상은 대표 프레임을 추출해 512px 미리보기 PIL 이미지로 반환"""
    from PIL import Image, ImageOps
    from core.media import is_video_file, extract_thumbnail
    src = path
    if is_video_file(path):
        if not thumb_dir:
            return None
        src = os.path.join(thumb_dir, f"plan_{os.path.splitext(os.path.basename(path))[0]}.jpg")
        if not os.path.exists(src):
            extract_thumbnail(path, src, at=0.8)
    from core.media.imgutil import PREVIEW_SIDE, open_rgb_downscaled
    img = open_rgb_downscaled(src, PREVIEW_SIDE)             # 비전·미리보기용: 큰 원본을 풀 해상도로 열지 않음
    img.thumbnail((512, 512))
    return img


# 6~8컷은 5문장에 비대칭으로 묶는다 (문장 호흡과 컷 전환이 엇갈려 지루하지 않게): 후킹·핵심 증거·후기 검증 문장에 컷 2장씩, 전개·CTA는 1장
MEDIA_GROUP_SIZES = {6: [2, 1, 1, 1, 1], 7: [2, 1, 2, 1, 1], 8: [2, 1, 2, 2, 1]}


EXTERIOR_WORDS = ("외관", "간판", "건물", "입구", "출입구", "가게 전경", "매장 전경", "주차장", "내부 전경", "매장 내부")


def order_media_for_concept(media: List[str], photo_tags: Dict[str, str], concept: str, photo_buckets: Optional[Dict[str, int]] = None) -> List[int]:
    """
    올린 순서와 상관없이 맛집 5단계 시선 순서로 컷을 정렬한다 (비전이 매긴 버킷):
      1 다 구워진 메인 요리 클로즈업·고기 자르기(첫 3초 후킹) → 2 전체 상차림·굽기 전 원육 → 3 구워지는 조리 장면 → 4 곁들임(냉면·반찬) → 5 가게 외관·간판(마지막 컷에만)
    같은 버킷 안에서는 컨셉 단어가 사진 태그에 많은 순, 그다음 올린 순서. 외관(5)은 한 장만 마지막에 쓰고 나머지 외관은 쓰지 않는다
    (다른 컷이 충분할 때). 버킷이 없으면 태그의 외관 단어로 외관만 마지막으로 보내고 컨셉 점수로 정렬, 태그도 컨셉도 없으면 올린 순서 그대로.
    반환: media 인덱스 순서 (일부 사진은 빠질 수 있음)
    """
    n = len(media)
    ident = list(range(n))
    if not n:
        return ident
    photo_tags = photo_tags or {}
    photo_buckets = photo_buckets or {}
    from core.script_guard import content_stems
    keys = content_stems(concept or "")
    keys_full = {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", concept or "")}
    rows = []
    for i, p in enumerate(media):
        tag = photo_tags.get(p, "")
        bk = photo_buckets.get(p)
        is_ext = (bk == 5) or (bk is None and any(w in tag for w in EXTERIOR_WORDS))
        sc = sum(1 for k in keys if k in tag) + sum(1 for w in keys_full if w in tag)
        rows.append({"i": i, "b": bk if bk in (1, 2, 3, 4, 5) else (5 if is_ext else 0), "ext": is_ext, "sc": sc})
    if not photo_buckets and not any(r["sc"] > 0 for r in rows if not r["ext"]) and not any(r["ext"] for r in rows):
        return ident                                                # 버킷도, 외관 태그도, 컨셉에 맞는 태그도 없음: 올린 순서 유지
    main = [r for r in rows if not r["ext"]]
    exts = [r for r in rows if r["ext"]]
    # 버킷이 있는 사진은 1→4 순서, 버킷이 없는 사진(0)은 컨셉 점수 순으로 그 뒤… 버킷 정보가 없을 때는 컨셉 점수만으로 정렬
    if photo_buckets:
        main.sort(key=lambda r: (r["b"] if r["b"] else 9, -r["sc"], r["i"]))
    else:
        main.sort(key=lambda r: (-r["sc"], r["i"]))
    out = [r["i"] for r in main]
    if exts:
        out.append(exts[0]["i"])                                    # 외관은 마지막 컷에만 한 장
        if len(main) < 2:                                           # 다른 컷이 너무 적으면 남는 외관도 뒤에 붙임
            out += [r["i"] for r in exts[1:]]
    return out


def media_groups(n_media: int) -> List[List[int]]:
    """미디어 번호(0부터)를 씬별 묶음으로: 5컷 이하는 1컷 = 1문장, 6~8컷은 5문장(예: 8컷 = [0,1] [2] [3,4] [5,6] [7])"""
    n = max(0, min(int(n_media), 8))
    sizes = MEDIA_GROUP_SIZES.get(n) or [1] * n
    out, k = [], 0
    for size in sizes:
        out.append(list(range(k, k + size)))
        k += size
    return out


# ---------------------------------------------------------------- 비트 리듬형: 사진 6~10장을 5문장에 역할별로 묶음 (안주 컷은 한 문장 안에서 고속 전환)
_EXTERIOR_TAG = ("간판", "외관", "입구", "출입구", "건물", "전경", "가게 앞")
_VENUE_TAG = ("홀", "내부", "실내", "테이블", "좌석", "인테리어", "매장", "조명")
_SPREAD_TAG = ("모둠", "한 상", "한상", "세트", "상차림", "푸짐", "플래터", "한판")
_DRINK_TAG = ("맥주", "생맥", "하이볼", "소주", "막걸리", "와인", "사케", "칵테일", "음료", "커피", "라떼", "잔")


def photo_role(tag: str) -> str:
    """사진 피사체 → 역할: exterior(간판·외관) / spread(안주 모둠·한 상) / drink(술·음료) / venue(홀·내부) / snack(그 밖의 메뉴)"""
    t = tag or ""
    if any(w in t for w in _EXTERIOR_TAG):
        return "exterior"
    if any(w in t for w in _SPREAD_TAG):
        return "spread"
    if any(w in t for w in _DRINK_TAG):
        return "drink"
    if any(w in t for w in _VENUE_TAG):
        return "venue"
    return "snack"


def beat_groups(media_sorted: List[str], tags: Dict[str, str]) -> List[List[int]]:
    """
    정렬된 사진 N장(6~10) → 5문장 묶음 [S1 후킹, S2 공감, S3 메뉴 라인업(컷 여러 장), S4 분위기, S5 CTA]. 번호는 media_sorted 인덱스.
    S1 첫 사진 / S5 간판·외관 / S4 홀·내부 / S2 모둠·한 상(없으면 남는 술 사진, 그래도 없으면 S1 사진 재사용) / S3 나머지 메뉴 전부(순서 유지).
    5장 이하는 1장 = 1문장. 피사체 정보가 없으면 가운데 문장(S3)에 중간 사진을 몰아서 [1,1,N-4,1,1]
    """
    n = len(media_sorted)
    if n <= 5:
        return [[k] for k in range(n)]
    if not any((tags or {}).get(p) for p in media_sorted):
        out, k = [], 0
        for size in (1, 1, n - 4, 1, 1):
            out.append(list(range(k, k + size)))
            k += size
        return out
    roles = [photo_role((tags or {}).get(p, "")) for p in media_sorted]
    used = {0}
    s1 = [0]
    ext = [i for i in range(1, n) if roles[i] == "exterior"]
    s5 = [ext[-1]] if ext else [n - 1]
    used.add(s5[0])
    s4 = [i for i in range(1, n) if roles[i] == "venue" and i not in used][:2]
    used.update(s4)
    s2 = [i for i in range(1, n) if roles[i] == "spread" and i not in used][:2]
    if not s2:
        s2 = [i for i in range(1, n) if roles[i] == "drink" and i not in used][:1]
    used.update(s2)
    s3 = [i for i in range(n) if i not in used]
    if not s4:                                              # 분위기 컷이 없으면 메뉴 컷의 마지막 1장(3장 이상일 때) 또는 간판 사진을 다시 씀
        s4 = [s3.pop()] if len(s3) >= 3 else [s5[0]]
    if not s2:                                              # 공감 컷이 없으면 첫 사진을 이어서 사용
        s2 = [0]
    if not s3:
        s3 = [s2[0]]
    return [s1, sorted(s2), s3, sorted(s4), s5]


def rap_cta(search_keyword: str, context_kind: str = "") -> str:
    """랩형 마지막 줄: 저장 유도 없이 상호로 바로 오세요 (예: '오늘 밤 시원한 맥주 생각나면 설맥 가평현리점으로 바로 오세요!')"""
    from core.script_guard import with_josa
    kw = (search_keyword or "").strip()
    if not kw:
        return "오늘 바로 들러 보세요!"
    lead = {"bar": "오늘 밤 시원한 맥주 생각나면 ", "cafe": "커피 한 잔 생각나면 "}.get(context_kind, "오늘은 ")
    return f"{lead}{with_josa(kw, '으로/로')} 바로 오세요!"


def valid_rap_cta(text: str, search_keyword: str = "") -> bool:
    t = (text or "").strip()
    kw = re.sub(r"\s+", "", search_keyword or "")
    return bool(t) and len(t) <= 44 and t.endswith("!") and "저장" not in t and "검색" not in t and (not kw or kw in re.sub(r"\s+", "", t)) and not find_banned_style(t)


def _scene(i: int, role: str, text: str, hook: str = "") -> Dict[str, Any]:
    motions = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_down", "pan_up"]
    return {
        "scene_index": i + 1, "scene_number": i + 1, "role": role,
        "target_visual_role": role, "visual_keywords": [ROLE_LABELS_KO.get(role, role)],
        "duration_sec": OWNER_POLICY.default_scene_sec, "hook_title": hook, "voiceover": text, "subtitle": text, "caption": text,
        "visual_description": ROLE_LABELS_KO.get(role, role), "image_prompt_en": "",
        "video_motion_prompt_en": motions[i % len(motions)], "focus_bottom": False,
    }


def plan_owner_storyboard(
    business_name: str,
    usp: str = "",
    business_type: str = DEFAULT_BUSINESS_TYPE,
    search_keyword: str = "",
    media_paths: Optional[List[str]] = None,
    num_scenes: Optional[int] = None,
    tone_instruction: str = "",
    api_key: Optional[str] = None,
    mock: bool = False,
    thumb_dir: Optional[str] = None,
    output_plan_path: Optional[str] = None,
    hook_hints: Optional[HookHints] = None,
    core_concept: str = "",
    region: str = "",
    store_facts: Optional[Dict[str, Any]] = None,
    photo_tags: Optional[Dict[str, str]] = None,
    photo_buckets: Optional[Dict[str, int]] = None,
    keep_order: bool = False,
    tempo: str = "beat",
) -> List[Dict[str, Any]]:
    """
    사장님 홍보 콘티 생성.
    - media_paths가 있으면 씬 수 = 미디어 수 (2~8), i번째 씬 = i번째 미디어. 없으면 업종 템플릿으로 num_scenes개.
    - 마지막 씬은 항상 네이버 검색 유도 CTA (마지막 미디어 위에 재생).
    - core_concept: 점주가 선택한 단 하나의 1컨셉 (지정 시 대본 전체가 해당 컨셉으로 100% 관통 수렴)
    """
    business_name = (business_name or "").strip()
    search_keyword = (search_keyword or "").strip() or business_name
    media_orig = list(media_paths or [])[:10]
    # keep_order: 사장님이 직접 정한 순서를 100% 그대로 (AI 버킷 정렬·외관 제외 없음, 첫 번째로 올린 사진이 첫 컷·썸네일)
    order = list(range(len(media_orig))) if keep_order else order_media_for_concept(media_orig, photo_tags or {}, core_concept, photo_buckets or {})
    media = [media_orig[k] for k in order]                       # 컨셉 순서로 정렬한 미디어 (대본 프롬프트·컷 묶음은 이 순서)
    reordered = order != list(range(len(media_orig)))           # 순서가 바뀌었거나 일부 사진(여분의 외관)을 뺀 경우
    rap = tempo == "rap"
    pol = RAP_POLICY if rap else OWNER_POLICY
    if len(media) < 2:
        groups = []
    elif rap or len(media) <= 5:
        groups = [[k] for k in range(len(media))]                              # 랩형: 사진 N장 = 2초 숏카피 N줄 (1:1). 5장 이하는 비트형도 1:1
    else:
        groups = beat_groups(media, photo_tags or {})                          # 비트형: 사진 6~10장도 5문장 18초 호흡 (안주 컷은 한 문장 안에서 고속 전환)
    if reordered and not groups:
        groups = [[k] for k in range(len(media))]
    count = len(groups) if groups else max(int(num_scenes or 4), 2)
    roles = _roles_for(business_type, count)
    cta_line = owner_cta(search_keyword, region, business_type)
    lines = MOCK_LINES.get(business_type, MOCK_LINES[DEFAULT_BUSINESS_TYPE])
    default_hook = f"{business_name} 가봤어요?" if business_name else "여기 진짜 가볼 만해요!"

    from core import venue_context as vc
    from core.script_guard import find_unphotographed_menu, photographed_menus
    official = [m for m in ((store_facts or {}).get("official_menus") or []) if m]
    photo_text_all = " ".join((photo_tags or {}).get(p, "") for p in media)             # 이번 영상에 쓰는 사진들의 피사체
    if official and photo_text_all.strip():
        shown_menus = photographed_menus(official, photo_text_all)
        banned_menus = [m for m in official if m not in shown_menus]
        store_facts = dict(store_facts or {})
        store_facts["photo_menus"] = shown_menus
        store_facts["banned_menus"] = banned_menus
        store_facts["special_menus"] = [m for m in (store_facts.get("special_menus") or []) if not find_unphotographed_menu(m, banned_menus, "x")]
        usp = scrub_menus(usp, banned_menus)
    else:
        banned_menus = []
    tag_by_scene = [" / ".join((photo_tags or {}).get(media[k], "") for k in g if k < len(media) and (photo_tags or {}).get(media[k])) for g in groups] if groups else []
    ctx_kind = vc.detect(business_name, usp, (photo_tags or {}).values(), extra=" ".join((store_facts or {}).get("special_menus") or []))
    cta_line = rap_cta(search_keyword, ctx_kind) if rap else owner_cta(search_keyword, region, business_type, ctx_kind)
    fact_evidence = " ".join([usp or "", core_concept or "", " ".join((store_facts or {}).get("special_menus") or []),
                              " ".join((store_facts or {}).get("review_nouns") or []), " ".join((photo_tags or {}).values())])
    llm = None
    llm_story = ""
    if not mock and script_llm_ready(api_key):   # Gemini 키 없이 Claude·GPT 키만 있어도 대본 엔진 사용
        try:
            llm = _plan_with_llm(business_name, usp, business_type, roles, media, cta_line,
                                 tone_instruction, api_key, thumb_dir, hook_hints, core_concept=core_concept, region=region,
                                 groups=groups, store_facts=store_facts, photo_tags=photo_tags,
                                 search_keyword=search_keyword, context_kind=ctx_kind, tempo=tempo, policy=pol)
            llm_story = str(getattr(llm, "story", "") or "")
        except Exception as e:
            logger.warning(f"사장님 대본 엔진(Claude·GPT·Gemini) 모두 실패, 업종 템플릿으로 대체: {e}")

    scenes = []
    for i, role in enumerate(roles):
        is_last = i == len(roles) - 1
        item = (llm or [])[i] if llm and i < len(llm) else {}
        tag_i = tag_by_scene[i] if i < len(tag_by_scene) else ""
        llm_cta = str(item.get("voiceover") or "").strip() if is_last else ""
        cta_ok = valid_rap_cta(llm_cta, search_keyword) if rap else valid_cta(llm_cta, search_keyword)
        text = (llm_cta if (llm_cta and cta_ok) else cta_line) if is_last else str(item.get("voiceover") or "").strip() or (
            photo_fallback_line(tag_i, i, core_concept, short=rap) or concept_fallback_line(core_concept, i) or lines.get(role, lines["exterior"]))   # 엔진 실패 시에도 사진·컨셉을 유지
        if not is_last:
            text = to_haeyo(text)   # 말투 통일: 합쇼체 → 해요체
            claim = _unsupported_claim(text, usp)
            if claim:
                logger.info(f"[허위 차단] 씬 {i + 1}: USP에 없는 표현 '{claim}' → 템플릿 대사로 교체")
                text = lines.get(role, lines["exterior"])
            safe = lambda: (photo_fallback_line(tag_i, i, core_concept, short=rap) or concept_fallback_line(core_concept, i) or lines.get(role, lines["exterior"]))   # noqa: E731
            drift = find_concept_drift(text, core_concept, usp)
            if drift:
                logger.info(f"[1컨셉 이탈 차단] 씬 {i + 1}: {drift} → 컨셉 안전 문구로 교체")
                text = safe()
            banned = find_banned_style(text)
            if banned:
                logger.info(f"[판에 박힌 패턴 차단] 씬 {i + 1}: {banned} (대사: {text}) → 컨셉 안전 문구로 교체")
                text = safe()
            ctx_bad = vc.find_violation(text, ctx_kind)
            if ctx_bad:
                logger.info(f"[업종 방문 맥락 위반 차단] 씬 {i + 1}: '{ctx_bad}' ({ctx_kind}) (대사: {text}) → 사진 기반 안전 문구로 교체")
                text = safe()
            ghost = find_unphotographed_menu(text, banned_menus, photo_text_all) if banned_menus else None
            if ghost:
                logger.info(f"[사진에 없는 메뉴 차단] 씬 {i + 1}: '{ghost}' (대사: {text}) → 그 사진을 말하는 문구로 교체")
                text = safe()
            if tag_i and photo_subject_issues([text], [tag_i]):
                logger.info(f"[사진-대사 불일치 차단] 씬 {i + 1}: 화면 '{tag_i}' (대사: {text}) → 그 사진을 말하는 문구로 교체")
                text = safe()
            copied = find_example_copy(text, fact_evidence) or find_invented_menu(text, fact_evidence)
            if copied:
                logger.info(f"[예시 복붙·가짜 메뉴 차단] 씬 {i + 1}: '{copied}' (대사: {text}) → 컨셉 안전 문구로 교체")
                text = safe()
            if find_name_leak(text, business_name):
                # 상호명은 마지막 CTA에서만 공개: 1~4문장에 남은 상호는 '여기'로 바꿔 문장은 살림
                leak = find_name_leak(text, business_name)
                logger.info(f"[상호명 노출 차단] 씬 {i + 1}: '{leak}' (대사: {text}) → 상호 제거")
                text = re.sub(re.escape(business_name.replace(" ", "")), "여기", text.replace(business_name, "여기"))
            text = strip_brochure_words(text)
            text = sanitize_signboard_words(text)
            text = fix_voice_agreement(text)
            n_cut = len(groups[i]) if i < len(groups) else 1
            hard_max = 26 if rap else max(VO_HARD_MAX, 12 * n_cut + 8)
            if len(text) > hard_max:
                text = shorten_line(text, hard_max - 3, hard_max)
            if rap:
                text = text.strip() if re.search(r"[!?,.…]$", text.strip()) else text.strip() + "!"        # 랩형 숏카피: 어미 완성 대신 짧은 끊김 허용
                text = clamp_line_to_policy(text, pol)
            else:
                # 온전한 친절한 구어체 어미 검증 및 완성 (~있어요, ~만나보세요, ~챙겨가세요)
                text = complete_owner_sentence(text)
                text = clamp_line_to_policy(text, _dc_replace(pol, max_chars=max(pol.max_chars, 12 * n_cut + 8)) if n_cut > 1 else pol)
                text = ensure_natural_pauses(text)
        if llm and i < len(llm) and llm[i].get("role") in ROLE_LABELS_KO and not is_last:
            role = llm[i]["role"]
        hook = ""
        if i == 0:
            hook = to_haeyo(str((llm[0].get("hook_title") if llm else "") or "").strip()) or default_hook   # 훅도 합쇼체 금지
            if _unsupported_claim(hook, usp) or find_banned_style(hook):
                hook = default_hook
            if hook_hints:
                if copies_reference(hook, hook_hints):
                    logger.info(f"[후킹 원문 복제 차단] '{hook}' → 기본 후킹")
                    hook = default_hook
                if copies_reference(text, hook_hints):
                    text = lines.get(role, lines["exterior"])
            if is_hook_echo(hook, text, core_concept):
                # 컨셉을 담은 서브 대사는 살리고 헤드라인을 기본 후킹으로 바꾼다 (서브 대사를 기계 문구로 바꾸면 컨셉이 사라짐)
                logger.info(f"[후킹 반복 차단] 헤드라인 '{hook}' ≈ 서브 '{text}' → 기본 후킹으로 교체")
                hook = default_hook
        scenes.append(_scene(i, "cta" if is_last else role, text, hook))
        scenes[-1]["duration_sec"] = pol.default_scene_sec if rap else max(pol.default_scene_sec, 1.35 * (len(groups[i]) if i < len(groups) else 1))
        if groups and (reordered or any(len(g) > 1 for g in groups)):
            scenes[-1]["media_group"] = [order[k] for k in groups[i]]   # 이 문장 동안 보여 줄 '올린 순서' 기준 미디어 번호들 (첫 번째가 대표, 나머지는 문장 중간 전환 컷)
        if i == 0 and hook_hints:
            scenes[0]["hook_reference"] = hook_hints.summary()
            used = hook_hints.pattern_used((llm[0] if llm else {}).get("hook_pattern_used"))
            scenes[0]["hook_pattern_used"] = {"video_id": used["video_id"], "hook_type": used["hook_type"],
                                              "pattern_template": used["pattern_template"]} if used else None

    # 씬 간 앵무새 반복(같은 말 되풀이) 차단: 앞 씬과 겹치는 대사는 컨셉 안전 문구로 (CTA 제외)
    if len(scenes) > 2:
        body = [sc["voiceover"] for sc in scenes[:-1]]
        for j, why in repeated_line_issues(body, scenes[0].get("hook_title", "")).items():
            if j == 0 or not core_concept:
                continue     # 씬 1은 훅 비교에서 이미 처리, 컨셉 없으면 교체할 근거 문구가 없음
            logger.info(f"[씬 간 반복 차단] 씬 {j + 1}: {why} (대사: {body[j]})")
            scenes[j]["voiceover"] = scenes[j]["subtitle"] = scenes[j]["caption"] = concept_fallback_line(core_concept, j)

    # 감탄사 다양성: 같은 감탄 표현은 영상 전체(헤드라인 포함) 1회 (CTA 제외)
    if len(scenes) > 1:
        if scenes:
            scenes[0]["script_engine"] = getattr(llm, "engine", "") if llm else "template"
        varied, changes = diversify_exclamations([sc["voiceover"] for sc in scenes[:-1]], reserved=scenes[0].get("hook_title", ""))
        for k_, (sc, vo) in enumerate(zip(scenes[:-1], varied)):
            if rap:
                vo_clean = clamp_line_to_policy(vo, pol)
            else:
                n_cut_ = len(groups[k_]) if k_ < len(groups) else 1
                vo_clean = ensure_natural_pauses(clamp_line_to_policy(complete_owner_sentence(vo), _dc_replace(pol, max_chars=max(pol.max_chars, 12 * n_cut_ + 8)) if n_cut_ > 1 else pol))
            sc["voiceover"] = sc["subtitle"] = sc["caption"] = vo_clean
        for c in changes:
            logger.info(f"[감탄사 중복 분산] {c}")
    final_concept = (core_concept or "").strip() or str(getattr(llm, "core_concept", "") or "").strip()
    if scenes:
        scenes[0]["core_concept"] = final_concept
    if llm_story and scenes:
        scenes[0]["story"] = llm_story
    if scenes and reordered:
        scenes[0]["photo_reordered"] = True
    if scenes:
        scenes[0]["tempo"] = tempo
    if output_plan_path:
        with open(output_plan_path, "w", encoding="utf-8") as f:
            json.dump(scenes, f, ensure_ascii=False, indent=2)
    concept_str = f", 코어컨셉='{final_concept}'" if final_concept else ""
    logger.info(f"사장님 콘티 완료: '{business_name}' {len(scenes)}개 씬 (미디어 {len(media)}개{concept_str}, LLM={getattr(llm, 'engine', '') or 'N'})")
    return scenes


STORE_POINT_MAX_LEN = 22


def suggest_store_points(business_name: str, business_type: str, media_paths: Optional[List[str]] = None,
                         existing_usp: str = "", api_key: Optional[str] = None, thumb_dir: Optional[str] = None,
                         mock: bool = False) -> List[str]:
    """
    사장님이 올린 사진·영상(대표 프레임)과 업종으로 매장 특징·후킹 포인트를 최대 4개 정리 (가게 자랑 칸 보완용).
    근거는 사진에 보이는 것뿐. 가격·할인·순위·수치는 사장님이 이미 적은 자랑(existing_usp)에 있을 때만 남김
    (AI가 지어낸 '30명 단체석', '1위 맛집'이 대본의 근거가 되면 허위 광고)
    Claude → OpenAI → Gemini 자동 라우터(generate_json) 연동.
    """
    from core.script_guard import find_unsupported_spec
    from core.llm import generate_json
    media = [p for p in (media_paths or []) if p][:4]
    if mock or not media:
        return []
    type_label = BUSINESS_TYPES.get(business_type, BUSINESS_TYPES[DEFAULT_BUSINESS_TYPE])["label"]
    try:
        contents: List[Any] = []
        for i, path in enumerate(media):
            img = _media_preview_image(path, thumb_dir)
            if img is not None:
                contents += [f"[사진 {i + 1}]", img]
        if not contents:
            return []
        contents.append(f"""
가게: {business_name or '(상호명 미입력)'} / 업종: {type_label}
사장님이 이미 적은 자랑: {existing_usp or '(없음)'}

위 사진들을 보고 손님이 '가 보고 싶다'고 느낄 매장 특징·후킹 포인트를 최대 4개 정리하세요.
- 근거는 사진에 눈으로 보이는 것뿐 (공간 분위기, 대표 메뉴·상품 모습, 좌석·인테리어, 외관, 주변 풍경)
- 가격·할인·이벤트·순위·수상·인원수·거리(분) 같은 숫자나 혜택은 쓰지 마세요 (사장님 자랑에 있으면 생략해도 됨)
- 이미 적힌 자랑과 겹치지 않게, 각 포인트는 공백 포함 {STORE_POINT_MAX_LEN}자 이내 명사형 짧은 구절
JSON만 출력: {{"points": ["...", "..."]}}
""")
        data, engine = generate_json(contents, OWNER_SYSTEM, temperature=0.4, gemini_api_key=api_key, purpose="매장 특징 요약")
        raw = [str(x or "").strip() for x in (data.get("points") or [])] if isinstance(data, dict) else []
    except Exception as e:
        logger.warning(f"매장 특징 정리 실패: {e}")
        return []
    evidence = f"{business_name} {existing_usp}"
    evidence_numbers = set(re.findall(r"\d+", evidence))
    ok = [t for t in dict.fromkeys(raw)
          if 2 <= len(t) <= STORE_POINT_MAX_LEN and not _unsupported_claim(t, evidence) and not find_unsupported_spec(t, evidence)
          and set(re.findall(r"\d+", t)) <= evidence_numbers           # 사진만으로는 숫자(인원·거리·가격)를 알 수 없음
          and t not in (existing_usp or "")]
    dropped = [t for t in raw if t and t not in ok]
    if dropped:
        logger.info(f"[매장 특징 검증] 근거 없는 수치·혜택 또는 중복 제외: {dropped}")
    return ok[:4]


def suggest_ad_concepts(
    business_name: str,
    business_type: str = DEFAULT_BUSINESS_TYPE,
    media_paths: Optional[List[str]] = None,
    usp: str = "",
    api_key: Optional[str] = None,
    thumb_dir: Optional[str] = None,
    mock: bool = False,
    photo_tags: Optional[Dict[str, str]] = None,
    cache_path: Optional[str] = None,
    official_menus: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """
    업종의 '실제 손님 방문 맥락'(core.venue_context)과 1단계에서 확정된 사진 전체(최대 10장)를 함께 보고 컨셉 3개를 제안한다.
    전 업종 범용: 사진 속 시각적 요소(Vision)와 매장 팩트를 교차 검증(Vision-Fact Guard)하여
    소비자 결핍(Pain Point)을 정밀 타격하는 숏폼 3대 결핍 앵글 1컨셉 후보 자동 생성
    1안 [손실 회피 / 실속형]
    2안 [비상 구원 / 해결형]
    3안 [취향 저격 / 도파민형]
    """
    from core.llm import generate_json
    media = [p for p in (media_paths or []) if p][:10]            # 첫 사진만이 아니라 확정된 사진 전체를 봄
    tags = dict(photo_tags or {})
    if not tags and media and not mock and api_key:
        try:
            from core.store_facts import analyze_photos
            tags, _b = analyze_photos(media, business_name=business_name, api_key=api_key, thumb_dir=thumb_dir, cache_path=cache_path, official_menus=official_menus)
        except Exception as e:
            logger.info(f"컨셉 제안용 사진 분석 건너뜀: {e}")
    from core import venue_context as vc
    from core.script_guard import find_unphotographed_menu, photographed_menus
    kind = vc.detect(business_name, usp, tags.values())
    _ph_text = " ".join(tags.values())
    _shown = photographed_menus(official_menus or [], _ph_text)
    _banned = [m for m in (official_menus or []) if m not in _shown] if tags else []
    type_info = BUSINESS_TYPES.get(business_type, BUSINESS_TYPES[DEFAULT_BUSINESS_TYPE])
    type_label = type_info["label"]

    # 기본 폴백 3대 앵글 (Mock 모드이거나 API 호출 실패 시 업종별 고품질 기본값 제공)
    name_display = business_name or "우리 매장"
    fallback_concepts = [
        {
            "id": "loss_prevention",
            "angle": "손실 회피 / 실속형",
            "badge": "💸 손실 회피 / 실속형",
            "headline": "이거 모르면 손해일까요?",
            "core_concept": f"{name_display}에서 알뜰하게 챙기는 실속 구성",
            "description": "가격 대비 구성 등 손님의 지갑을 지켜주는 이유에 집중",
        },
        {
            "id": "emergency_solution",
            "angle": "비상 구원 / 해결형",
            "badge": "🚨 비상 구원 / 해결형",
            "headline": "급하거나 고민될 때 헤매지 마세요!",
            "core_concept": f"언제든 편하게 들러 필요한 것을 바로 해결하는 {name_display}",
            "description": "빠른 해결, 쾌적한 시설, 접근성 등 손님의 불편을 바로 풀어 주는 이유에 집중",
        },
        {
            "id": "taste_dopamine",
            "angle": "취향 저격 / 도파민형",
            "badge": "✨ 취향 저격 / 도파민형",
            "headline": "오늘 하루 스트레스 1초 컷!",
            "core_concept": f"{name_display}의 인기 대표 메뉴와 매력적인 분위기 만끽하기",
            "description": "시그니처 메뉴, 독보적 비주얼, 디저트/주력 상품으로 감각적 만족 집중",
        },
    ]

    tailored = vc.fallback_concepts(kind, name_display, tags.values(), usp)
    if tailored:
        fallback_concepts = tailored                      # 맥주집 등은 실제 방문 맥락(저녁 2차·퇴근 후 1차)에 맞춘 기본안
    if mock or not api_key:
        return fallback_concepts

    try:
        contents: List[Any] = []
        for i, path in enumerate(media):
            img = _media_preview_image(path, thumb_dir)
            if img is not None:
                contents += [f"[사진 {i + 1}]", img]
        photo_summary = "\n".join(f"- 사진 {i + 1}: {tags.get(p, '(첨부 이미지 참고)')}" for i, p in enumerate(media)) or "(사진 없음)"
        venue_block = vc.prompt_block(kind)

        prompt = f"""
당신은 골목 상권 전 업종 숏폼 광고 전문 크리에이티브 디렉터입니다.
가게: {name_display} / 업종: {type_label}
사장님 특장점(USP/팩트): {usp or '(없음)'}

{venue_block}
{("[사진 속 메뉴만 사용] 컨셉에 쓸 수 있는 메뉴는 이번 사진에 찍힌 것뿐입니다: " + (", ".join(_shown) or "사진 설명에 보이는 것") + chr(10) + "⛔ 사진에 없는 메뉴(매장에 있어도 컨셉·헤드라인에 절대 쓰지 말 것): " + ", ".join(_banned[:40])) if _banned else ""}

[확정된 사진 전체 구성 — 첫 사진만 보지 말고 전부 함께 보고, 컨셉은 이 사진들로 100% 뒷받침되어야 합니다]
{photo_summary}
예) 사진에 맥주 + 다양한 안주가 함께 있다면 '살얼음 맥주와 찰떡궁합 안주 라인업'처럼 올린 사진 전체로 입증되는 컨셉.

위 첨부된 사진 속 실제 시각 요소와 매장 팩트를 교차 검증하여,
소비자 결핍(Pain Point)을 정밀 타격하는 '전 업종 3대 결핍 앵글 1컨셉 카드' 3개를 생성하세요.

[시각 입증 팩트 가드 (Vision-Fact Guard) - 절대 규칙]
1. 반드시 첨부된 사진 속에 눈으로 실제로 확인되는 사물/시설/메뉴/인테리어만 근거로 삼으세요.
2. 사진으로 시각적 입증이 불가능한 주제는 제안 후보군에서 자동 배제하세요!
   - 사진에 술/맥주가 전혀 안 보이면 '살얼음 생맥주'나 '주류' 컨셉 제외
   - 사진에 보이지 않는 품목·시설(특정 메뉴, 주차장, 좌석 규모 등)을 내세운 컨셉 제외
3. 억지 유행어·밈은 배제하고, 소비자에게 실제 체감되는 확실한 행동 이유(1컨셉 1메시지)를 제시하세요.

[전 업종 범용 3대 결핍 앵글 구성]
1안 [손실 회피 / 실속형]: 손님이 모르면 손해 보는 지점을 짚는 질문형 (가성비 구성, 알뜰한 이용법 등 지갑을 아껴주는 이유)
2안 [비상 구원 / 해결형]: 손님이 막막한 순간을 짚는 질문형 (운영 시간, 빠른 해결, 접근성 등 고객의 불편 해결)
3안 [취향 저격 / 도파민형]: 감각을 건드리는 한 줄 (시그니처 메뉴, 비주얼, 공간 분위기 등 오감 만족)

JSON만 출력:
{{
  "concepts": [
    {{
      "id": "loss_prevention",
      "angle": "손실 회피 / 실속형",
      "badge": "💸 손실 회피 / 실속형",
      "headline": "8~12자 강력한 질문/후킹 한 줄",
      "core_concept": "이번 30초 광고 전체를 관통할 15~25자 핵심 컨셉 한 줄",
      "description": "이 컨셉이 손님에게 주는 핵심 가치 1문장"
    }},
    {{
      "id": "emergency_solution",
      "angle": "비상 구원 / 해결형",
      "badge": "🚨 비상 구원 / 해결형",
      "headline": "8~12자 강력한 질문/후킹 한 줄",
      "core_concept": "이번 30초 광고 전체를 관통할 15~25자 핵심 컨셉 한 줄",
      "description": "이 컨셉이 손님에게 주는 핵심 가치 1문장"
    }},
    {{
      "id": "taste_dopamine",
      "angle": "취향 저격 / 도파민형",
      "badge": "✨ 취향 저격 / 도파민형",
      "headline": "8~12자 강력한 질문/후킹 한 줄",
      "core_concept": "이번 30초 광고 전체를 관통할 15~25자 핵심 컨셉 한 줄",
      "description": "이 컨셉이 손님에게 주는 핵심 가치 1문장"
    }}
  ]
}}
"""
        contents.append(prompt)
        data, engine = generate_json(contents, OWNER_SYSTEM, temperature=0.5, gemini_api_key=api_key, purpose="1컨셉 제안 생성")
        raw_concepts = data.get("concepts", []) if isinstance(data, dict) else []
        if isinstance(raw_concepts, list) and len(raw_concepts) >= 3:
            results = []
            for k, item in enumerate(raw_concepts[:3]):
                blob = f"{item.get('headline', '')} {item.get('core_concept', '')} {item.get('description', '')}"
                bad = vc.find_violation(blob, kind) or (find_unphotographed_menu(blob, official_menus or [], _ph_text) if tags else None)
                if bad and k < len(fallback_concepts):
                    logger.info(f"[업종 맥락 위반 컨셉 교체] '{bad}' ({kind}) → 방문 맥락에 맞는 기본 컨셉")
                    results.append(fallback_concepts[k])
                    continue
                results.append({
                    "id": str(item.get("id") or item.get("angle") or "concept"),
                    "angle": str(item.get("angle") or "추천 컨셉"),
                    "badge": str(item.get("badge") or "🎯 추천 컨셉"),
                    "headline": str(item.get("headline") or "").strip(),
                    "core_concept": str(item.get("core_concept") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                })
            return results
    except Exception as e:
        logger.warning(f"1컨셉 AI 제안 실패, 기본 템플릿 사용: {e}")

    return fallback_concepts


OWNER_SYSTEM = build_ad_system_prompt("동네 매장·플레이스", mode="owner")
# 대본 생성 전용: 공용 시스템 프롬프트 + 본론 락인·상투어 금지·고유 명사 규칙 (컨셉 제안·특징 요약 호출에는 붙이지 않음)
LOCKIN_BLOCK = """[절대 원칙: "쇼츠는 광고다. 광고는 1컨셉 1메시지다." + "대본과 화면은 1:1이다."]
1. 컨셉은 하나로 관통, 문장은 화면과 1:1: 선택된 단 하나의 코어 컨셉(1메시지)을 끝까지 관통하되, 각 문장은 반드시 '그 문장이 나오는 순간 화면에 보이는 사진'(프롬프트의 문장별 사진·피사체 목록)을 직접 말하라. 사진 속 메뉴·장면은 컨셉을 뒷받침하는 증거로 소개하는 것이지, 컨셉과 무관한 메뉴 나열이 아니다. 화면에는 화채·먹태가 나오는데 맥주 거품 이야기만 하는 것처럼 다른 사진 이야기를 하거나, 사진에 없는 메뉴·장면을 말하는 것을 엄격히 금지한다.
2. 문장 구조 (사진 N장 = 문장 N개):
   - 1문장(후킹): 상호명을 절대 쓰지 말고, 선택된 1메시지와 직결된 질문·결핍·상황으로 시작하되 1번 사진의 피사체를 말하라.
   - 중간 문장들: 각각 자기 사진 속 메뉴·장면으로 같은 컨셉을 다른 각도(공감·대비 → 핵심 증거 → 체감 → 곁들임·분위기)로 증명하라. 문장마다 그 사진의 피사체 + 컨셉에 맞는 구체적 이유 한 가지(입력 팩트의 손질·양념·조리 방식 또는 보이는 식감·온도·비주얼). "이것만 기억해요" 같은 추상 어구는 쓰지 말라.
   - 마지막 문장(CTA, 보통 외관·간판 사진): 컨셉을 한 구절로 요약하며 지금 저장하고 [지역명 + 상호명]을 검색하게 유도하라.
3. 문장 길이 및 중복 제한: 인접 문장에 같은 단어를 반복하지 말고(예: 2문장에 쓴 "만족하는"을 3문장에서 또 쓰지 말고 다른 표현으로), 각 문장은 공백 포함 20~28자 내외로 작성하여 하단(72%)에 1~2줄씩 2분할로 표시되게 하라.
4. 입력된 USP·메뉴·사진 태그에 없는 가짜 메뉴·반찬 이름은 지어내지 마라. 입력 팩트에 있는 것은 칼집·양념·불향처럼 침샘을 자극하는 미각·시각 표현으로 생생하게 쓰고, "이름부터 믿음직하죠", "맛을 내요", "이어져요" 같은 건조한 캡션 말투와 "팡 터지는 육즙", "감칠맛 폭발", "겉바속촉", "정갈한 밑반찬", "극락 그 자체" 같은 클리셰는 쓰지 마라. 프롬프트 예시의 문구·단어를 그대로 복사하지 마라."""
OWNER_SCRIPT_SYSTEM = OWNER_SYSTEM + "\n\n" + LOCKIN_BLOCK
FEWSHOT_BLOCK = """[Few-Shot: 1컨셉 + 문장마다 화면 1:1 구조 예시 — <꺾쇠> 안은 이 매장의 입력 팩트·사진 피사체로 채우세요]
※ 실제 메뉴·반찬·손질법·리뷰 문구는 일부러 넣지 않았습니다. 구조만 배우고 <꺾쇠>는 이 매장의 special_menus·review_nouns·특장점·컨셉·사진 피사체에 있는 단어로만 채우세요.

[Bad 1: 컨셉 없이 사진 따라 메뉴 이름만 나열]
사진2 <메뉴 A> → "<메뉴 A>가 있어요."  사진3 <메뉴 B> → "<메뉴 B>도 있어요."   (컨셉이 사라진 백화점식 나열)
[Bad 2: 화면과 어긋난 대사]
화면: <곁들임 안주> 사진 / 대사: "봉긋 솟은 크림 거품이 먼저 보이죠?"   (다른 사진 이야기)
[Good: 컨셉 하나 + 문장마다 그 순간 화면의 피사체]
컨셉 <C>
사진1 <대표 메뉴> → 1문장(후킹, 상호 없음): <C> 상황을 짚으며 <대표 메뉴>를 말함
사진2 <곁들임 A> → <C>를 완성하는 <곁들임 A>의 구체적 이유 (맛·식감·온도)
사진3 <곁들임 B> → <C> 안에서 <곁들임 B>가 하는 역할
사진N-1 <공간·메뉴 모둠> → <C>를 편하게 즐기는 상황 한 가지
사진N <외관·간판> → <C 한 구절 요약> 찾으신다면 지금 저장하고 <지역> <상호>를 검색해 보세요!"""


def _plan_with_llm(business_name, usp, business_type, roles, media, cta_line, tone_instruction, api_key, thumb_dir,
                   hook_hints=None, core_concept: str = "", region: str = "", groups=None,
                   store_facts: Optional[Dict[str, Any]] = None, photo_tags: Optional[Dict[str, str]] = None,
                   search_keyword: str = "", context_kind: str = "", tempo: str = "beat", policy=None):
    from core.llm import generate_json, repair_scene_lines
    from core.script_guard import script_violation_finder
    from core.store_facts import facts_prompt_block

    type_label = BUSINESS_TYPES.get(business_type, BUSINESS_TYPES[DEFAULT_BUSINESS_TYPE])["label"]
    contents: List[Any] = []
    if groups:
        for i, g in enumerate(groups):
            for j, mi in enumerate(g):
                img = _media_preview_image(media[mi], thumb_dir)
                if img is not None:
                    contents += [f"[씬 {i + 1} 미디어{f' {j + 1}/{len(g)} (같은 문장 동안 연달아 나옴)' if len(g) > 1 else ''}]", img]
    else:
        for i, path in enumerate(media[:len(roles)]):
            img = _media_preview_image(path, thumb_dir)
            if img is not None:
                contents += [f"[씬 {i + 1} 미디어]", img]
    n = len(roles)
    # 문장별 화면 사진의 피사체 태그 (사진-대사 1:1 일치의 근거): 문장 i = 그 문장 동안 나오는 사진들
    sentence_groups = groups or [[i] for i in range(min(n, len(media)))]
    tags_by_path = photo_tags or {}
    line_tags: List[str] = []
    photo_lines: List[str] = []
    for i, g in enumerate(sentence_groups):
        parts = [(k, tags_by_path.get(media[k], "")) for k in g if k < len(media)]
        line_tags.append(" / ".join(t for _, t in parts if t))
        for k, t in parts:
            photo_lines.append(f"사진 {k + 1}번 (문장 {i + 1} 구간 화면): {t or '(태그 없음)'}")
    facts_block = facts_prompt_block(store_facts or {}, photo_lines)
    evidence = " ".join([usp or "", " ".join((store_facts or {}).get("special_menus") or []),
                         " ".join((store_facts or {}).get("review_nouns") or []), (store_facts or {}).get("location_context") or "",
                         " ".join(tags_by_path.values())])
    role_hint = ", ".join(f"씬{i + 1}={ROLE_LABELS_KO.get(r, r)}" for i, r in enumerate(roles))
    cta_tail = owner_cta_tail(search_keyword, region)
    rap = tempo == "rap"
    pol = policy or (RAP_POLICY if rap else OWNER_POLICY)
    concept_guidelines = build_one_concept_guidelines("owner", n, type_label, (rap_cta(search_keyword, context_kind) if rap else f"<선택한 1메시지를 한 구절로 요약> 찾으신다면 {cta_tail}"), pol)
    multi = any(len(g) > 1 for g in sentence_groups)
    max_cuts = max((len(g) for g in sentence_groups), default=1)
    chk_pol = pol if rap or not multi else _dc_replace(pol, max_chars=max(pol.max_chars, 12 * max_cuts + 8))
    if rap:
        scene_rule = (f"- 총 {n}줄 = 사진 {n}장 (줄 i = 사진 i, 위 '문장별 화면 사진·피사체' 목록과 1:1). 한 줄은 2초 안팎의 짧고 강렬한 숏카피입니다. "
                      f"줄 i는 그 순간 화면에 나오는 사진 속 피사체를 직접 말해야 해요. 다른 사진 이야기나 사진에 없는 메뉴는 금지입니다.")
        length_rule = ("- 각 voiceover는 공백 포함 10~20자(최대 24자)의 랩처럼 리듬 있는 숏카피. '~해요' 완결형일 필요 없이 '~!', '~,'로 끊어도 되지만 "
                       "비문·어색한 낱말 결합은 금지입니다 (음식에 '한 잔'을 붙이는 '건어물 한 잔', 깨진 낱말 '개와' 같은 표현 금지). 문법이 맞는 말만 쓰세요.")
        readable_rule = ("- [길이] 줄마다 2초 안에 읽히게 20자 안팎. 늘어지는 서술어는 빼고 핵심 명사 + 짧은 동사로.\n"
                         "- [조사·어미 호응] '~이/가'로 시작한 주어 뒤에는 타동사를 쓰지 마세요. '생버섯이 곁들여요'(X) → '생버섯이 곁들여져요'(O) 또는 '생버섯까지 곁들여요'(O).")
        structure_rule = (f"- [랩형 구조 (사진 {n}장 = 숏카피 {n}줄)]: 1줄=후킹(상호 없이, 방문 맥락 + 1번 사진 피사체) → 중간 줄=각 사진의 피사체 한 줄씩(맛·식감·온도 한 가지) → "
                          f"마지막 줄=상호명으로 바로 오세요 CTA ('{rap_cta(search_keyword, context_kind)}' 형태, 저장 유도·'검색해 보세요'는 쓰지 않음).")
        last_rule = f"* 마지막 줄은 정확히 '{rap_cta(search_keyword, context_kind)}' 형태로 상호명을 말하며 끝내세요 (상호명·지역은 이 줄에서만 공개)."
    else:
        scene_rule = (f"- 총 {n}문장({'5문장 호흡' if n == 5 else '사진 1장 = 문장 1개'}). "
                      + ("사진 " + str(len(media)) + "장이 문장에 묶여 있어요: 아래 '문장별 화면 사진·피사체' 목록대로, 한 문장 동안 컷 여러 장이 1.2~1.5초 간격으로 빠르게 전환되는 문장은 "
                         "그 피사체들을 화면에 나오는 순서대로 모두 호명하세요. " if multi else "문장 i = 사진 i (위 목록과 1:1). ")
                      + "컨셉은 하나로 관통하되 각 문장은 그 순간 화면에 나오는 사진 속 피사체를 직접 말해야 합니다. 다른 사진 이야기나 사진에 없는 메뉴는 금지입니다.")
        length_rule = (f"- 각 voiceover는 해요체로 끝나는 한 문장, 공백 포함 {pol.min_chars + 6}~{pol.max_chars - 2}자 (최대 {pol.max_chars}자)."
                       + (f" 단, 컷이 여러 장 전환되는 문장은 컷당 약 11~14자를 더해 길게 쓰세요 (컷 4장 = 44~56자, 그 구간이 5~6초라 말이 모자라면 화면이 끊겨 보여요)." if multi else "")
                       + " 한 문장에 한 가지 이야기만 하세요. 비문·어색한 결합('건어물 한 잔'처럼 음식에 '한 잔')과 깨진 낱말('개와')은 금지입니다.")
        readable_rule = ("- [길이·자막 가독성] 마지막 CTA를 뺀 각 문장은 공백 포함 20~28자로 간결하게 (컷 여러 장 문장은 예외). 자막이 최대 2줄씩 나뉘어 읽혀야 합니다.\n"
                         "  늘어지는 서술어('~하는 분들은 여기서 확인해 보세요')는 빼고, 핵심 명사와 짧은 동사 위주로 끊어 쓰세요.")
        structure_rule = ("- [비트 리듬형 5문장 구조]: 1문장=후킹(상호 없이, 1번 사진 피사체 + 방문 맥락) → 2문장=공감·대비(방문 상황) → 3문장=메뉴 컷 라인업을 순서대로 호명 "
                          "→ 4문장=매장 분위기·편하게 즐기는 상황 → 5문장=컨셉 한 구절 요약 + 저장 유도 + 상호명 검색 CTA." if n == 5 else
                          f"- [1컨셉 문장 구조 (사진 {n}장 = 문장 {n}개, CTA 포함)]: 1문장=후킹(상호 없이, 1번 사진 피사체) → 중간 문장=각 사진의 메뉴·장면으로 같은 컨셉을 다른 각도로 증명 → 마지막=컨셉 한 구절 요약 + 저장 유도 + 상호명 검색 CTA.")
        last_rule = f"* 마지막 씬은 선택한 1메시지를 한 구절로 요약한 뒤 반드시 '{cta_tail}'로 끝내세요 (상호명·지역은 이 문장에서만 공개)."

    from core import venue_context as _vc
    vc_block = _vc.prompt_block(context_kind)
    concept_override_prompt = ""
    if core_concept and core_concept.strip():
        concept_override_prompt = f"""
[★ 점주 선택 확정 1컨셉 (Core Concept) - 100% 필수 관통]
이번 영상은 점주가 직접 선정한 아래 단 하나의 핵심 컨셉만을 30초 내내 입증하고 증명해야 합니다.
다른 주제로 화제를 돌리지 말고, 오직 이 하나의 컨셉으로 모든 씬과 사진을 기승전결 엮으세요!
★ 확정된 코어 컨셉: "{core_concept.strip()}"
JSON의 "core_concept" 필드에도 정확히 이 값을 그대로 넣으세요.
"""

    contents.append(f"""
[광고주 및 매장 정보]
- 상호명(1~4문장에서는 절대 말하지 마세요. 마지막 CTA 문구에서만 공개): {business_name}
- 업종: {type_label}
- 사장님이 직접 적은 특장점(USP): {usp or '(없음)'}
- 지역: {short_region(region) or '(알 수 없음)'}

{facts_block}
- 톤 참고: {tone_instruction or '친근하고 경쾌한 사장님 직영 홍보 톤'}

{concept_override_prompt}

{vc_block}

{concept_guidelines}

[추가 세부 지침]
{scene_rule}
- 가격, 할인, 무료, 순위(1위 등), 수상, 이벤트처럼 USP에 없는 수치·혜택은 절대 쓰지 마세요.
- 씬 1의 hook_title은 스크롤을 멈추게 하는 8~12자 헤드라인 (상호명 포함 가능, 읽는 데 2초 이내).
{length_rule}
- 씬마다 같은 메시지를 다른 각도(대비·증거·체감)로 증명하세요. 헤드라인이나 앞 씬의 말을 바꿔 되풀이하면 안 됩니다.
- 상투구 금지: '한 땀 한 땀', '정성껏', '맛보실 수 있어요', '따라서', '그러므로', '또한', '그리고'.
{readable_rule}
{structure_rule}
- [카피 규칙: 설명이 아니라 세일즈 카피]
  * 조리 과정·행동 묘사 금지: '~구워요', '~잘라요', '그릇에 담아요', '집게로 뒤집을수록' 같은 일기장식 설명문은 쓰지 마세요.
  * 대신 손님이 얻는 감각과 이유를 쓰세요: 육즙, 숯불 향, 식감, 윤기, 살얼음처럼 미각·후각·시각을 건드리는 단어를 씬마다 하나 이상 넣으세요.
    단, 감각어는 USP·사진에 있는 메뉴와 조리 방식에 붙는 것만 쓰고, 없는 재료·'비법'·가격·수치 같은 새 사실은 만들지 마세요.
  * 씬 1 첫 문장은 후킹: 지역명({short_region(region) or '지역명'}) 또는 누구를 위한 곳인지(타깃)를 짚으세요.
    (구조 예: '{short_region(region) or '동네'} 사람들이 ○○ 생각날 때 찾는 곳이에요!' — 문구는 이 매장 정보로 새로 쓰기)
  {last_rule}
{FEWSHOT_BLOCK}
- 사장님이 적은 특장점(USP)과 첨부 사진에 없는 메뉴·반찬·조리 방식·시설·서비스는 지어내지 마세요 (예: 적히지 않은 계란찜·냉면·미리 손질).
  매장 운영 방식(손님이 직접 굽는지, 구워서 내는지 등)은 USP에 있는 한 가지로만 일관되게 쓰고, 모르면 방식을 단정하지 말고 맛·분위기·상황으로 말하세요.
- '~할 수 있어요' 어미는 영상 전체에서 2번까지만, 나머지는 '~해요/~예요/~죠'로 단정하세요. 웃음 표기(^^, ㅎㅎ)와 이모티콘은 쓰지 마세요.
{hook_hints.prompt_block if hook_hints else ""}
- role은 다음 중 하나: {", ".join(k for k in ROLE_LABELS_KO if k != "cta")} (마지막 씬은 "cta")

JSON만 출력: {{"core_concept": "이번 영상의 단 하나의 핵심 메시지", "story": "기승전결 전체 스토리 요약", "scenes": [{{"scene_index": 1, "role": "...", "hook_title": "...", "voiceover": "..."}}]}}
""")
    # 대본 엔진: Claude → OpenAI → Gemini 자동 백업 (core/llm/router.py)
    data, engine = generate_json(contents, OWNER_SCRIPT_SYSTEM, temperature=0.6, gemini_api_key=api_key, purpose="사장님 광고 대본")
    # 모드 전용 Hard Limit 글자 수 초과 및 판에 박힌 패턴/어미 리듬 위반 줄 재작성 요청
    vo_lines = [str(s.get("voiceover") or "") for s in (data.get("scenes") or [])[:-1] if isinstance(s, dict)] if isinstance(data, dict) else []
    hook_line = str(((data.get("scenes") or [{}])[0] or {}).get("hook_title") or "") if isinstance(data, dict) else ""
    violation_checker = create_length_violation_checker(
        chk_pol, script_violation_finder(vo_lines, core_concept=core_concept, hook=hook_line, usp=usp, lock_topic=True,
                                              evidence=evidence + " " + (core_concept or ""),
                                              business_name=business_name, region=short_region(region),
                                              line_tags=line_tags[:len(vo_lines)], context_kind=context_kind))
    data, fixes = repair_scene_lines(data, violation_checker, OWNER_SCRIPT_SYSTEM, pol.repair_instruction, gemini_api_key=api_key)
    for f in fixes:
        logger.info(f"[owner 대본 규칙 보정] {f}")
    scenes = data.get("scenes", data) if isinstance(data, dict) else data
    if not isinstance(scenes, list) or not scenes:
        raise ValueError(f"예상치 못한 응답: {str(data)[:200]}")
    # 가드레일: 온전한 친절한 구어체 서술어 및 메타 단어 정제
    for sc in scenes[:-1]:
        if isinstance(sc, dict) and sc.get("voiceover"):
            cleaned = sanitize_signboard_words(str(sc["voiceover"]))
            if rap:
                sc["voiceover"] = clamp_line_to_policy(cleaned, chk_pol)
                continue
            completed = complete_owner_sentence(cleaned)
            clamped = clamp_line_to_policy(completed, chk_pol)
            sc["voiceover"] = ensure_natural_pauses(clamped)
    out = _ScenesWithStory(scenes)
    out.core_concept = str(data.get("core_concept") or "").strip() if isinstance(data, dict) else ""
    out.story = str(data.get("story") or "").strip() if isinstance(data, dict) else ""
    out.engine = engine
    return out


class _ScenesWithStory(list):
    """씬 목록 + 단 하나의 핵심 컨셉(core_concept) + LLM 전체 독백(story) + 엔진(engine)"""
    core_concept = ""
    story = ""
    engine = ""


# 하위 호환 별칭
_plan_with_gemini = _plan_with_llm
