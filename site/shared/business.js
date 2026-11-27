// site/shared/business.js
// 업종 판별과 업종별 문구 — 서버(api/diagnose.js)와 화면(ReportScreen, 오프라인 폴백)이 같은 규칙을 쓴다

// 비식당 업종어. 네이버 업종(category)에는 이 목록을 먼저 적용한다 ('스터디카페'·'요리학원'처럼 음식어가 섞인 서비스업 때문)
const SERVICE_CATEGORY = /(설비|수도|보일러|누수|배관|인테리어|시공|수리|정비|카센터|자동차|세차|타이어|공업사|미용|헤어|바버|네일|피부|에스테틱|왁싱|속눈썹|뷰티|마사지|병원|의원|치과|안과|외과|내과|소아과|산부인과|이비인후과|약국|학원|교습|교육|독서실|스터디|부동산|중개|펜션|숙박|모텔|호텔|게스트하우스|민박|꽃집|꽃배달|화원|플라워|헬스|피트니스|필라테스|요가|체육|세탁|청소|방역|사진관|스튜디오|철물|유리|샷시|새시|도배|장판|에어컨|철거|이사|용달|렌트|렌터카|법률|변호사|법무|세무|회계|노무|웨딩|장례|목욕|사우나|찜질|교회|전기|조명|인쇄|간판|안경|휴대폰)/;

// 외식·식음료 업종어
const FOOD_CATEGORY = /(음식|식음료|식당|한식|중식|중국|일식|일본|양식|분식|카페|커피|디저트|베이커리|제과|빵|떡|한과|맥주|호프|주점|술집|펍|포차|이자카야|바\(bar\)|와인|막걸리|고기|육류|정육|갈비|삼겹살|곱창|막창|족발|보쌈|구이|치킨|닭|오리|국밥|국수|냉면|우동|라면|라멘|만두|죽|순대|해장국|설렁탕|곰탕|추어|감자탕|찌개|전골|탕|해물|생선|횟집|회집|수산|조개|낙지|주꾸미|아귀|장어|게요리|복어|레스토랑|뷔페|패스트푸드|피자|버거|샌드위치|토스트|핫도그|샐러드|브런치|도시락|반찬|아이스크림|빙수|와플|도넛|찻집|과일|청과|식품|푸드|마라|아시아|돈가스|돈까스|초밥|스시|파스타|스테이크|샤브|백반|가정식|한정식|쌈밥|보리밥|비빔밥|김밥|떡볶이|두부|빈대떡|요리)/;

// 상호명만 있을 때 쓰는 목록. 상호에는 '유리네·이사랑·화원·독수리'처럼 우연히 겹치는 말이 많아 음식어를 먼저 본다
const FOOD_NAME = /(식당|분식|국밥|갈비|삼겹|고기|한우|정육|곱창|막창|족발|보쌈|치킨|통닭|닭갈비|국수|칼국수|냉면|막국수|우동|라멘|짬뽕|반점|횟집|회관|수산|해장국|감자탕|찌개|곱도리|떡집|떡볶이|김밥|반찬|도시락|빵|베이커리|제과|카페|커피|디저트|빙수|맥주|호프|주점|포차|이자카야|피자|버거|파스타|돈까스|돈가스|초밥|스시|가든|푸드|cafe|coffee|pub|beer|chicken|pizza|burger)/i;
const SERVICE_NAME_STRONG = /(스터디카페|독서실|교습소|학원)/;

export function determineBusinessType(category = '', storeName = '') {
  const cat = String(category || '').toLowerCase().trim();
  const name = String(storeName || '').toLowerCase().trim();
  if (cat) {
    if (SERVICE_CATEGORY.test(cat)) return 'service';
    if (FOOD_CATEGORY.test(cat)) return 'food';
  }
  if (SERVICE_NAME_STRONG.test(name)) return 'service';
  if (FOOD_NAME.test(name)) return 'food';
  if (SERVICE_CATEGORY.test(name)) return 'service';
  return cat ? 'service' : 'food';        // 업종도 상호 단서도 없으면 기존 기본값(food)
}

const REPAIR = /(설비|수도|보일러|누수|배관|인테리어|시공|수리|정비|카센터|세차|타이어|공업사|철물|유리|샷시|도배|장판|에어컨|철거|청소|방역|이사|용달|전기|조명|도어)/;
const MEDICAL = /(병원|의원|치과|안과|피부과|성형외과|정형외과|내과|이비인후과|클리닉|약국)/;
const BEAUTY = /(미용|헤어|바버|네일|피부|에스테틱|왁싱|속눈썹|뷰티|메이크업)/;
const EDUCATION = /(학원|교습|교육|독서실|스터디|필라테스|요가|헬스|피트니스|체육)/;

export function buildHookCopy(storeName, menuName, category = '', businessType = 'food') {
  const sName = storeName || '우리 매장';
  const menu = menuName || (businessType === 'service' ? '대표 서비스' : '대표 메뉴');

  if (businessType === 'service') {
    const group = serviceGroup(category, sName);
    const g = SERVICE_GROUPS[group];
    // 기본 추천 칩은 문장에 끼워 넣으면 어색하므로('꼼꼼한 현장 점검 · 수리 현장 해결') 그룹 전용 문장을 쓴다
    if (Object.values(SERVICE_GROUPS).some((x) => x.chips.includes(menu))) return g.hook(sName);
    if (group === 'medical') return `${sName} 알아보고 계셨나요? ${menu} 안내를 지금 확인해 보세요 🏥`;
    if (group === 'repair') return `${sName} 찾으셨나요? 꼼꼼하고 확실한 ${menu} 현장 해결, 지금 확인하세요 🔧`;
    if (group === 'beauty') return `${sName}에서 경험하는 섬세한 변화! ${menu} 실제 시술 느낌을 확인해 보세요 ✨`;
    return `우리 동네에서 믿고 찾는 ${sName}! ${menu} 핵심 강점을 숏폼으로 만나보세요 📌`;
  }

  const text = `${menu} ${category}`.toLowerCase();
  if (/(맥주|생맥|눈꽃맥주|얼음맥주|하이볼|소주|막걸리|와인|사케|칵테일|호프|주점|펍|포차|이자카야)/.test(text)) {
    return `${sName} 오시면 첫 잔은 무조건 이거죠! 한 모금에 속이 뻥 뚫리는 ${menu} 실물 영접 🍺`;
  }
  if (/(빙수|눈꽃빙수|팥빙수|아이스크림|파르페|스무디|에이드|주스|커피|라떼|아메리카노|카페|디저트)/.test(text)) {
    return `${sName} 가면 이건 무조건 맛봐야죠! 시원하게 입맛 당기는 ${menu} 실물 영접 🍧`;
  }
  if (/(갈비|삼겹살|목살|소고기|한우|곱창|막창|구이|불고기|스테이크|숯불)/.test(text)) {
    return `${sName} 가면 이건 무조건 시켜야죠! 윤기 좔좔 흐르는 ${menu} 실물 영접 🔥`;
  }
  if (/(찌개|탕|국밥|전골|라면|우동|짬뽕)/.test(text)) {
    return `${sName} 가면 이건 무조건 시켜야죠! 속까지 뜨끈하게 확 풀리는 ${menu} 실물 영접 🍲`;
  }
  return `${sName} 가면 이건 무조건 시켜야죠! 손님들 극찬 쏟아지는 ${menu} 실물 영접 🔥`;
}

// 강조 항목을 바꿨을 때의 훅: 기본 훅 안의 항목명만 바꾸되 상호명 안에 같은 글자가 있어도 상호는 건드리지 않는다
export function swapHookMenu({ hookCopy, storeName, fromMenu, toMenu, category, businessType }) {
  const parts = storeName ? hookCopy.split(storeName) : [hookCopy];
  if (fromMenu && parts.some((p) => p.includes(fromMenu))) {
    return parts.map((p) => p.split(fromMenu).join(toMenu)).join(storeName || '');
  }
  return buildHookCopy(storeName, toMenu, category, businessType);
}

// 글자나 숫자가 하나도 없으면(공백·따옴표·괄호·말줄임표만) 후기 상자를 보여주지 않는다
export function hasReviewText(s) {
  return /[\p{L}\p{N}]/u.test(String(s || ''));
}

export function serviceStrengths(storeName) {
  return [
    { title: `${storeName}의 핵심 전문 서비스 · 맞춤 상담`, description: '네이버 플레이스에 등록된 핵심 전문 서비스 및 맞춤 상담', mentionRate: '전문 서비스', sampleReview: '' },
    { title: '꼼꼼한 진행과 친절한 응대로 쌓은 지역 신뢰', description: '철저하고 꼼꼼한 진행과 고객 맞춤 친절한 안내', mentionRate: '고객 만족', sampleReview: '' },
    { title: '네이버 플레이스 검색으로 바로 연결되는 접근성', description: '네이버 지도 및 플레이스에서 위치 확인과 간편 문의 가능', mentionRate: '접근성 우수', sampleReview: '' },
  ];
}

// 비식당 업종 그룹별 기본 추천 서비스 칩과 훅 (메뉴·키워드가 없을 때)
const SERVICE_GROUPS = {
  medical: {
    chips: ['친절한 진료 · 건강 상담', '꼼꼼한 검진 · 맞춤 치료'],
    desc: '증상과 상황에 맞춘 친절한 안내',
    hook: (s) => `${s} 알아보고 계셨나요? 진료·상담 안내를 지금 확인해 보세요 🏥`,
  },
  repair: {
    chips: ['맞춤 견적 · 시공 상담', '꼼꼼한 현장 점검 · 수리'],
    desc: '친절하고 정확한 현장 맞춤 상담',
    hook: (s) => `${s} 찾으셨나요? 꼼꼼한 점검부터 맞춤 상담까지, 지금 확인하세요 🔧`,
  },
  beauty: {
    chips: ['1:1 맞춤 스타일링', '꼼꼼한 시술 · 예약 안내'],
    desc: '원하는 스타일에 맞춘 1:1 상담',
    hook: (s) => `${s}에서 경험하는 섬세한 변화! 1:1 맞춤 상담과 예약 안내를 확인해 보세요 ✨`,
  },
  other: {
    chips: ['맞춤 상담 · 커리큘럼 안내', '친절한 예약 · 이용 안내'],
    desc: '상황에 맞춘 친절한 상담과 안내',
    hook: (s) => `우리 동네에서 믿고 찾는 ${s}! 상담·이용 안내를 숏폼으로 만나보세요 📌`,
  },
};

// 업종(category)을 먼저 보고, 단서가 없으면 상호로 판단한다
export function serviceGroup(category = '', storeName = '') {
  for (const raw of [category, storeName]) {
    const text = String(raw || '').toLowerCase();
    if (MEDICAL.test(text)) return 'medical';
    if (EDUCATION.test(text)) return 'other';       // '전기학원'·'미용학원'은 교육
    if (REPAIR.test(text)) return 'repair';
    if (BEAUTY.test(text)) return 'beauty';
  }
  return 'other';
}

export function serviceMenus(storeName, category = '') {
  const g = SERVICE_GROUPS[serviceGroup(category, storeName)];
  return g.chips.map((name, i) => ({ name, price: i === 0 ? '상담' : '문의', isPopular: true, desc: g.desc }));
}
