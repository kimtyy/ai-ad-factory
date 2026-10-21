"""
core/planners/stay.py - 숙소 제휴 5단계: '1컨셉 1메시지' 18초 5문장 대본 (1인당 체감 가성비 + 단 하나의 압도적 공간 로망)

침실·주방·화장실 나열(부동산식 방 소개) 금지. 5문장이 모두 [1인당 체감가(또는 서울 근교 이동 시간) + 자쿠지/온수풀/통창뷰 중 하나]로 이어진다:
  1(0~3초) 숫자·시각 반전 후킹, 숙소 본명 금지 → 2(~6.5초) 여행객 결핍 공감 → 3(~11초) 공간 도파민 → 4(~15초) 결정적 혜택 → 5(~18초) 제휴 링크 CTA
사진은 3-2-2 정렬(물3·뷰2·바비큐/야경2)된 순서 그대로 문장에 묶는다: [1,2,2,1,1]장.
지어내기 방지: 가격은 파싱한 '1인 체감가' 하나만, 시설은 수집된 팩트·사진 설명에 있는 것만 쓴다. 어긋나면 팩트 기반 템플릿 문장으로 교체.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from core.planners.owner import _scene
from core.stay_match import FACILITY_PATTERNS, extract_facilities

logger = logging.getLogger("ad_factory.planners.stay")

STAY_CTA = "주말 특가 마감되기 전에 지금 저장하고 고정 댓글 최저가 링크 확인해 보세요!"
SCENE_SECONDS = [3.0, 3.5, 4.5, 4.0, 3.0]            # 0~3 / 3~6.5 / 6.5~11 / 11~15 / 15~18초
ROLES = ["hook", "feature", "feature", "feature", "cta"]
GROUP_SIZES = {5: [1, 1, 1, 1, 1], 6: [1, 2, 1, 1, 1], 7: [1, 2, 2, 1, 1]}
MAX_LEN = 46
# 팩트·사진 설명에 없으면 말하면 안 되는 시설 단어 (FACILITY_PATTERNS에 없는 것들)
CLAIM_WORDS = ("수영장", "풀장", "사우나", "헬스장", "찜질", "노래방", "뷔페", "레스토랑", "카페", "키즈", "당구", "탁구", "스키", "해수욕장", "워터파크", "마사지")
STAY_END_CARD_TEXT = "최저가 할인 예약 링크는 [고정 댓글 / 프로필 링크] 확인!"

STAY_SYSTEM = """당신은 숙소 제휴 쇼츠 18초 광고 카피라이터입니다. 규칙:
- 5문장 모두 [1인당 체감 가성비 + 단 하나의 압도적 공간 로망(자쿠지·온수풀·통창뷰 중 하나)] 1컨셉만 관통합니다.
- 침실·주방·화장실·방 개수 나열(부동산식 소개) 절대 금지. 숙소 본명 언급 금지.
- 입력된 팩트·사진 설명에 없는 시설·가격·수치·후기·순위는 만들지 않습니다.
- '피로가 풀려요', '밤이 길게 느껴져요', '힐링돼요'처럼 사진·팩트로 증명할 수 없는 분위기·효과·체감 문구 금지. 보이는 장면과 확인된 시설만 구체적으로 쓰세요.
- 해요체, 문장당 공백 포함 46자 이내, 이모티콘·웃음 표기 금지. JSON만 출력합니다."""


def _josa(word: str, a: str, b: str) -> str:
    """받침 있으면 a, 없으면 b (과/와, 이/가 …)"""
    ch = (word or " ")[-1]
    return a if ("가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28) else b


def region_token(region: str) -> str:
    from core.stay_match import place_token
    return place_token(region)


def kind_word(alias: str, region: str) -> str:
    """별칭에서 지역을 뺀 유형 문구 ('가평 리버뷰 독채풀빌라' → '리버뷰 독채풀빌라')"""
    reg = region_token(region)
    rest = alias[len(reg):].strip() if reg and alias.startswith(reg) else alias
    return rest or "숙소"


def template_lines(facts: Dict[str, Any], alias: str, region: str) -> List[str]:
    """수집된 팩트만으로 만든 5문장 (LLM 실패·검증 탈락 시 대체). 없는 팩트는 문장에서 빼고 일반 표현으로"""
    reg = region_token(region) or "국내"
    kind = kind_word(alias, region)
    pp = facts.get("per_person") or ""
    fac = facts.get("facilities") or []
    water = next((f for f in fac if f in ("개별 온수풀", "인피니티풀", "노천 자쿠지", "히노끼탕")), "")
    view = next((f for f in fac if f in ("오션뷰", "리버뷰", "마운틴뷰", "통창 뷰")), "")
    night = [f for f in fac if f in ("불멍", "바비큐")]
    sig = water or view
    if pp and sig:
        s1 = f"1인 {pp}에 {sig}{_josa(sig, '까지', '까지')} 다 쓰는 {reg} {kind} 실화일까요?"
    elif pp:
        s1 = f"1인 {pp}에 묵는 {reg} {kind}, 실화일까요?"
    elif sig:
        s1 = f"{sig}{_josa(sig, '까지', '까지')} 갖춘 {reg} {kind}, 실화일까요?"
    else:
        s1 = f"{reg} {kind}, 이런 곳이 있었어요?"
    want = "뷰 좋은 풀빌라" if "풀빌라" in kind else "분위기 좋은 숙소"
    s2 = f"주말에 {want} 가려다 1박 가격에 놀라서 포기하셨죠!"
    if water and view:
        s3 = f"문 열자마자 {view}가 펼쳐지고 {water}{_josa(water, '까지', '까지')} 하루 종일 즐겨요."
    elif view:
        s3 = f"문 열자마자 {view}가 펼쳐져서 하루 종일 보고만 있어도 좋아요."
    elif water:
        s3 = f"{water}{_josa(water, '을', '를')} 하루 종일 프라이빗하게 즐길 수 있어요."
    else:
        s3 = "사진 속 그 풍경을 객실에서 그대로 즐길 수 있어요."
    if len(night) == 2:
        s4 = f"저녁엔 {night[0]}{_josa(night[0], '과', '와')} {night[1]}까지 즐길 수 있어요."
    elif night:
        s4 = f"저녁엔 {night[0]}{_josa(night[0], '을', '를')} 즐길 수 있어요."
    else:
        s4 = "저녁까지 방 안에서 여유롭게 쉬다 가기 좋아요."
    return [s1, s2, s3, s4, STAY_CTA]


def _hook(facts: Dict[str, Any], alias: str, region: str) -> str:
    reg = region_token(region) or "국내"
    pp = facts.get("per_person") or ""
    sig = facts.get("signature") or ""
    if pp:
        return f"1인 {pp} {sig or kind_word(alias, region)}"[:14]
    return f"{reg} {sig or '독채'} 실화?"[:14]


def _media_groups(n: int) -> List[List[int]]:
    """사진 번호(0부터)를 5개 문장에 묶음. 5장 미만이면 돌려 씀"""
    if n <= 0:
        return []
    if n < 5:
        return [[k % n] for k in range(5)]
    sizes = GROUP_SIZES.get(min(n, 7), [1] * 5)
    out, k = [], 0
    for sz in sizes:
        out.append(list(range(k, k + sz)))
        k += sz
    return out


def validate_line(text: str, facts: Dict[str, Any], evidence: str, real_name: str = "") -> str:
    """문장이 통과하면 '' , 아니면 탈락 이유. 팩트에 없는 가격·시설, 본명 노출, 너무 긴 문장을 막는다"""
    t = (text or "").strip()
    if not t or len(t) > MAX_LEN:
        return "길이"
    if real_name and real_name.replace(" ", "") in t.replace(" ", ""):
        return "본명 노출"
    pp = (facts.get("per_person") or "").replace(" ", "")
    ev_flat = (evidence or "").replace(" ", "")
    for m in re.finditer(r"\d[\d,]*\s*(?:만\s*원대?|원|%|퍼센트|박|인|명|분|시간)", t):
        frag = m.group(0).replace(" ", "")
        if frag in ev_flat or (pp and frag == pp) or frag in ("1인", "1박"):
            continue
        if pp and frag.rstrip("대") == pp.rstrip("대") or (pp and pp.startswith(frag)):
            continue
        return f"근거 없는 수치 {frag}"
    ev = set(extract_facilities(evidence))
    for f in extract_facilities(t):
        if f not in ev:
            return f"근거 없는 시설 {f}"
    from core.script_guard import find_banned_style
    banned = find_banned_style(t)
    if banned:
        return f"어색한 문구 ({banned})"
    pool_ev = any(f in ev for f in ("개별 온수풀", "인피니티풀"))
    for w in CLAIM_WORDS:
        if w in t and w not in (evidence or "") and not (w in ("수영장", "풀장") and pool_ev):
            return f"근거 없는 시설 {w}"
    return ""


def plan_stay_storyboard(
    real_name: str,
    alias: str,
    region: str,
    facts: Dict[str, Any],
    media_paths: List[str],
    photo_tags: Optional[Dict[str, str]] = None,
    api_key: Optional[str] = None,
    mock: bool = False,
    output_plan_path: Optional[str] = None,
    hook_hints: Any = None,
    usp: str = "",
) -> List[Dict[str, Any]]:
    """
    숙소 5문장 18초 콘티. media_paths는 3-2-2로 이미 정렬된 사진 (최대 7장). 장면별 media_group은 이 순서의 번호.
    """
    photo_tags = photo_tags or {}
    media = list(media_paths or [])[:7]
    groups = _media_groups(len(media))
    evidence = " ".join([usp or "", " ".join(facts.get("facilities") or []), " ".join(photo_tags.values()), alias or ""])
    tpl = template_lines(facts, alias, region)
    lines = list(tpl)
    engine = "template"

    from core.llm import script_llm_ready
    if not mock and script_llm_ready(api_key):
        try:
            from core.llm import generate_json
            photo_lines = []
            for i, g in enumerate(groups):
                tags = " / ".join(photo_tags.get(media[k], "") for k in g if k < len(media) and photo_tags.get(media[k]))
                photo_lines.append(f"문장 {i + 1} 구간 화면: {tags or '(설명 없음)'}")
            prompt = f"""[숙소 팩트 — 이 안에 있는 것만 말하세요]
- 지역: {region_token(region) or '(모름)'} / 유형: {kind_word(alias, region)}
- 1인당 체감가(1박 요금÷{facts.get('people_used')}인): {facts.get('per_person') or '(모름: 가격 숫자 쓰지 마세요)'}
- 확인된 시설: {', '.join(facts.get('facilities') or []) or '(없음)'}
- 이번 영상의 압도적 공간 로망(1컨셉): {facts.get('signature') or '(없음)'}
- 직접 적은 특장점: {usp or '(없음)'}
{chr(10).join(photo_lines)}

[5문장 공식 — 문장 순서 고정]
1(0~3초) 숫자·시각 반전 후킹 (숙소 본명 금지). 예: "1인 4만 원대에 개인 온수풀까지 다 쓰는 가평 독채 숙소 실화일까요?"
2(3~6.5초) 여행객 결핍 공감. 예: "주말에 뷰 좋은 풀빌라 가려다 1박 가격에 놀라서 포기하셨죠!"
3(6.5~11초) 1컨셉 공간 도파민 (통창뷰·자쿠지·온수풀 중 팩트에 있는 것만). 예: "문 열자마자 통창 강 뷰가 펼쳐지고 프라이빗 자쿠지까지 즐겨요."
4(11~15초) 결정적 혜택 팩트 (불멍·바비큐·조식 등 확인된 것만).
5(15~18초) 정확히 이 문장: "{STAY_CTA}"
JSON만 출력: {{"scenes": [{{"scene_index": 1, "hook_title": "8~12자 헤드라인", "voiceover": "..."}}, ... 5개]}}"""
            data, eng = generate_json([prompt], STAY_SYSTEM, temperature=0.6, gemini_api_key=api_key, purpose="숙소 18초 대본")
            sc = (data.get("scenes") if isinstance(data, dict) else data) or []
            got = [str((s or {}).get("voiceover") or "").strip() for s in sc if isinstance(s, dict)]
            for i in range(4):
                if i < len(got):
                    why = validate_line(got[i], facts, evidence, real_name)
                    if why:
                        logger.info(f"[숙소 대본 검증 탈락] 문장 {i + 1}: {why} ('{got[i]}') → 팩트 템플릿 문장")
                    else:
                        lines[i] = got[i]
            hook_llm = str(((sc[0] if sc else {}) or {}).get("hook_title") or "").strip() if sc else ""
            engine = eng
        except Exception as e:
            logger.warning(f"숙소 대본 엔진 실패, 팩트 템플릿으로 대체: {e}")
            hook_llm = ""
    else:
        hook_llm = ""
    reg_ = region_token(region)
    for i in range(4):                                        # 본명(공백 무시)이 문장에 남아 있으면 '지역 숙소'로 (포털 검색 이탈 방지)
        if real_name.strip():
            pat = r"\s*".join(map(re.escape, real_name.replace(" ", "")))
            lines[i] = re.sub(pat, f"{reg_} 숙소" if reg_ else "이 숙소", lines[i])
    hook = hook_llm if (hook_llm and not validate_line(hook_llm, facts, evidence, real_name) and len(hook_llm) <= 14) else _hook(facts, alias, region)

    scenes = []
    for i in range(5):
        sc = _scene(i, ROLES[i], lines[i], hook if i == 0 else "")
        sc["duration_sec"] = SCENE_SECONDS[i]
        if groups:
            sc["media_group"] = list(groups[i])
        scenes.append(sc)
    scenes[0]["script_engine"] = engine
    scenes[0]["core_concept"] = " ".join(x for x in (facts.get("per_person") and f"1인 {facts['per_person']}", facts.get("signature")) if x) or "압도적 공간 로망"
    scenes[0]["stay_auto"] = True
    scenes[0]["stay_alias"] = alias
    if output_plan_path:
        with open(output_plan_path, "w", encoding="utf-8") as f:
            json.dump(scenes, f, ensure_ascii=False, indent=2)
    logger.info(f"숙소 18초 콘티: 사진 {len(media)}장 → 5문장, 엔진={engine}, 1인가={facts.get('per_person') or '미확인'}")
    return scenes
