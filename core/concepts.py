"""
core/concepts.py - 모드별 기본 '1컨셉' 후보 (사진·AI 분석 전에 화면이 먼저 보여 주는 안전한 기본안)

예전에는 모든 모드가 매장용 기본안('우리 매장에서 …')을 그대로 써서 쇼핑·숙소 탭에도 매장 문구가 뜨고 대본 프롬프트까지 들어갔다.
매장(owner)은 core.planners.owner.suggest_ad_concepts를 그대로 쓰고, 그 밖의 모드는 이 모듈의 모드 전용 문구를 쓴다.
"""
from typing import Dict, List


def default_concepts(mode: str, name: str = "") -> List[Dict[str, str]]:
    """mode: travel / commerce / book / video_overlay → [{id, angle, badge, headline, core_concept, description}] (3개)"""
    nm = (name or "").strip()
    if mode == "travel":
        return [
            {"id": "price_view", "angle": "가성비 + 공간 로망", "badge": "💸 1인당 가성비 + 로망 공간", "headline": "이 가격에 이런 숙소가 있다고요?",
             "core_concept": "1인당 체감 가성비로 누리는 단 하나의 압도적 공간(자쿠지·온수풀·통창 뷰)", "description": "수집된 요금·시설 팩트로 1인당 체감가를 먼저, 공간 로망 하나로 끝까지"},
            {"id": "view", "angle": "뷰·분위기", "badge": "🌅 뷰·분위기 저격", "headline": "문 열자마자 이 풍경이에요",
             "core_concept": "문 열자마자 펼쳐지는 뷰 하나로 승부하는 숙소", "description": "사진에 보이는 뷰·분위기만으로 감성 한 줄"},
            {"id": "water", "angle": "물·스파", "badge": "♨️ 물·스파 도파민", "headline": "하루 종일 물에서 놀 수 있어요",
             "core_concept": "개별 온수풀·자쿠지 등 물놀이 시설로 하루를 채우는 숙소", "description": "확인된 물 시설만 말하는 도파민형"},
        ]
    if mode == "commerce":
        what = nm or "이 상품"
        return [
            {"id": "pain_solution", "angle": "불편 해결", "badge": "🔧 생활 속 불편 해결", "headline": "아직도 이렇게 하세요?",
             "core_concept": f"생활 속 불편함 하나를 해결하는 {what}", "description": "기존 방식의 한계를 짚고 단 하나의 킬러 기능으로 해결"},
            {"id": "value", "angle": "체감 가성비", "badge": "💸 체감 가성비", "headline": "이 가격이면 부담 없어요",
             "core_concept": f"{what}의 한 달·한 번 단위 체감 가성비", "description": "최저가를 그대로 말하지 않고 쓰는 횟수로 환산"},
            {"id": "review", "angle": "후기 증명", "badge": "🗣️ 써 본 사람들 후기", "headline": "후기가 이유를 말해줘요",
             "core_concept": f"써 본 사람들이 꼽는 {what}의 한 가지 이유", "description": "블로그 후기에 반복된 포인트 하나로 증명"},
        ]
    if mode == "book":
        return [
            {"id": "one_sentence", "angle": "한 문장", "badge": "📖 책 속 한 문장", "headline": "오늘 읽은 한 문장",
             "core_concept": "책 속 한 문장이 건네는 조용한 위로", "description": "차분하게 한 권, 한 문장"},
            {"id": "place", "angle": "책방 분위기", "badge": "🪟 책방의 창가", "headline": "책 한 권 들고 앉고 싶은 자리",
             "core_concept": "책 한 권과 어울리는 책방의 한 자리", "description": "서가·창가 분위기로 방문 유도"},
            {"id": "pick", "angle": "이 책을 고른 이유", "badge": "💡 이 책을 고른 이유", "headline": "왜 이 책이냐면요",
             "core_concept": "이 책을 오늘 소개하는 단 하나의 이유", "description": "한 가지 이유로 천천히"},
        ]
    return [
        {"id": "highlight", "angle": "하이라이트", "badge": "🔥 하이라이트", "headline": "이 장면이 핵심이에요",
         "core_concept": "영상 속 가장 눈에 띄는 한 장면으로 승부", "description": "촬영 영상의 핵심 장면 하나"},
    ]
