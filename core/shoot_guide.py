"""
촬영 가이드: 선택한 1컨셉·업종에 맞춰 '추천 8컷 촬영 순서'를 안내한다 (화면 안내 전용, 렌더·대본 로직은 건드리지 않음).

세 가지 유형 (앞에서부터 먼저 걸리는 것):
  해결형(설비·수리)  컨셉/업종 글에 누수·고장·수리·시공 등 → 비포 → 원인 탐지 → 수리 과정 → 애프터
  편의점·신상형      편의점·마트·신상·행사 등 (또는 core.hashtags 업종이 retail) → 패키지 → 매대·가격표 → 개봉 → 외관
  F&B·고집형         음식점·카페·술집 등 → 음식 클로즈업 → 설비·케그 → 조리·상차림 → 매장·간판
이 밖의 업종은 None (안내를 띄우지 않음).
"""
from typing import Dict, List, Optional

SOLVE_WORDS = ("누수", "고장", "수리", "설비", "시공", "보수", "교체", "복구", "배관", "막힘", "철거", "정비", "에어컨", "보일러", "해결")
RETAIL_WORDS = ("편의점", "마트", "신상", "신제품", "신상품", "1+1", "2+1", "행사", "이마트24", "gs25", "cu", "세븐일레븐", "매대", "진열")
FNB_WORDS = ("맥주", "치킨", "고기", "삼겹", "국밥", "찌개", "맛집", "식당", "카페", "커피", "술집", "호프", "주점", "음식", "메뉴", "케그",
             "고집", "장인", "숙성", "수제")

GUIDES: Dict[str, Dict[str, object]] = {
    "solve": {"label": "설비·해결형",
              "steps": ["비포(누수·고장 상태)", "원인 탐지 화면", "수리 과정", "애프터(복구·마감)"]},
    "retail": {"label": "편의점·신상형",
               "steps": ["신상품 패키지", "매대 진열·가격표", "개봉 디테일", "매장 외관"]},
    "fnb": {"label": "F&B·고집형",
            "steps": ["맥주·음식 클로즈업", "설비·케그 디테일", "조리·상차림", "매장·간판"]},
}


def _has(text: str, words) -> bool:
    t = (text or "").lower()
    return any(w.lower() in t for w in words)


def guide_kind(concept: str = "", business_type: str = "", name: str = "", usp: str = "") -> Optional[str]:
    """컨셉을 가장 먼저 보고(영상이 말하려는 것), 그다음 업종·상호·장점 글로 유형 판별"""
    for text in (concept, f"{business_type} {name} {usp}"):
        if _has(text, SOLVE_WORDS):
            return "solve"
        if _has(text, RETAIL_WORDS):
            return "retail"
        if _has(text, FNB_WORDS):
            return "fnb"
    bt = (business_type or "").strip().lower()
    if bt in ("food", "restaurant", "cafe", "음식점", "카페"):
        return "fnb"
    return None


def shoot_guide(concept: str = "", business_type: str = "", name: str = "", usp: str = "") -> Optional[Dict[str, object]]:
    """{kind, label, steps: [...], text} 또는 None. steps는 4가지 큰 순서, 8컷은 이를 2컷씩 나눠 찍는 안내"""
    kind = guide_kind(concept, business_type, name, usp)
    if not kind:
        return None
    g = GUIDES[kind]
    steps: List[str] = list(g["steps"])  # type: ignore[arg-type]
    chain = " ➔ ".join(f"**{i}. {s}**" for i, s in enumerate(steps, 1))
    text = (f"📸 **추천 8컷 촬영 순서 · {g['label']}**\n\n{chain}\n\n"
            f"단계마다 2컷씩(가까이 1컷, 넓게 1컷) 찍으면 8컷이 돼요. 올린 순서대로 씬이 만들어집니다.")
    return {"kind": kind, "label": g["label"], "steps": steps, "text": text}
