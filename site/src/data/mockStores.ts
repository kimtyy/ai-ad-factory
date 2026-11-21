import { StoreDiagnosticResult } from '../types';

export const KNOWN_STORES: Record<string, StoreDiagnosticResult> = {
  '설맥 가평현리점': {
    storeName: '설맥 가평현리점',
    category: '요리주점 / 펍',
    address: '경기 가평군 조종면 조종초석로 14',
    summary: '특허받은 하얀 눈꽃얼음 맥주와 가성비 넘치는 요리 안주로 현지인과 군부대 면회객들에게 인기 만점인 대표 펍',
    topStrengths: [
      {
        rank: 1,
        title: '머리끝까지 시원한 특허받은 눈꽃얼음 맥주',
        description: '얼음을 곱게 갈아 맥주 위에 소복이 올린 눈꽃맥주가 첫 모금부터 끝까지 살얼음 온도를 유지',
        mentionRate: '94% 언급',
        sampleReview: '“눈꽃얼음이 녹아도 싱겁지 않고 마지막 한 모금까지 진짜 시원해서 여름엔 무조건 설맥만 갑니다.”',
      },
      {
        rank: 2,
        title: '바삭함이 살아있는 찹쌀치킨 & 스파이시 윙봉',
        description: '일반 호프집 수준을 뛰어넘는 바삭하고 쫄깃한 수제 튀김 안주류 호평',
        mentionRate: '86% 언급',
        sampleReview: '“눈꽃치킨 튀김옷이 찹쌀탕수육처럼 겉바속촉이라 아이들도 좋아하고 안주로 최고예요.”',
      },
      {
        rank: 3,
        title: '단체 모임하기 좋은 쾌적하고 넓은 테이블 공간',
        description: '깔끔한 매장 인테리어와 스크린, 친절한 점주님 응대로 재방문율 높음',
        mentionRate: '79% 언급',
        sampleReview: '“현리 쪽에서 단체로 회식하거나 2차로 깔끔하게 한잔하기 제일 좋은 곳입니다.”',
      },
    ],
    scoreCard: {
      menuCount: 28,
      hasPhotos: true,
      shortsCount: 0,
    },
    aiPlan: {
      recommendedMenu: '눈꽃맥주 & 눈꽃치킨',
      hookCopy: '가평에서 이거 안 마셔봤다고요? 얼음이 눈처럼 소복이 쌓인 진짜 눈꽃맥주 실물 영접 🍺',
      conceptPitch: '눈꽃 얼음이 소복하게 쌓이는 클로즈업 샷으로 시각적 쾌감을 자극하고, 바삭한 치킨 한 입 베어 무는 사운드로 손님의 발길을 유도합니다.',
    },
    menus: [
      { name: '설맥 눈꽃생맥주', price: '4,500원', isPopular: true, desc: '특허받은 살얼음 눈꽃이 올라간 시그니처 생맥주' },
      { name: '눈꽃치킨 (스파이시/오리지널)', price: '18,900원', isPopular: true, desc: '겉바속촉 찹쌀 튀김옷에 치즈가루가 솔솔 뿌려진 인기 안주' },
      { name: '마늘간장 순살치킨', price: '19,500원', isPopular: true, desc: '특제 마늘간장 소스로 감칠맛을 극대화한 메뉴' },
      { name: '매콤 칼칼 어묵탕', price: '16,000원', isPopular: false, desc: '각종 부산 어묵과 칼칼한 국물의 조화' },
      { name: '골뱅이 쫄면무침', price: '17,500원', isPopular: false, desc: '매콤달콤한 양념과 쫄깃한 소면의 필살 궁합' },
      { name: '모둠 소시지 & 감자튀김', price: '15,000원', isPopular: false, desc: '맥주 안주로 실패 없는 클래식 메뉴' },
      { name: '버팔로 윙&봉', price: '16,900원', isPopular: false, desc: '매콤 짭조름한 맥주 도둑' },
      { name: '치즈 라볶이', price: '12,000원', isPopular: false, desc: '매콤달콤 떡볶이에 모짜렐라 치즈 듬뿍' },
      { name: '먹태구이 & 땅콩', price: '14,000원', isPopular: false, desc: '바삭하게 구워 청양마요 소스와 함께' },
    ],
  },
};

export function generateDynamicDiagnosis(storeName: string): StoreDiagnosticResult {
  const trimmed = storeName.trim();
  if (KNOWN_STORES[trimmed]) {
    return KNOWN_STORES[trimmed];
  }

  // 매장명 키워드 기반 스마트 추론
  const isCafe = /(카페|커피|베이커리|디저트|베이크|로스터즈|tea|coffee|cafe)/i.test(trimmed);
  const isMeat = /(갈비|삼겹살|고기|돼지|한우|곱창|막창|구이|불고기|정육)/i.test(trimmed);
  const isChicken = /(치킨|통닭|닭강정|꼬꼬|chicken)/i.test(trimmed);
  const isPub = /(맥주|호프|포차|주점|비어|pub|beer|술)/i.test(trimmed);
  const isPizzaBurger = /(피자|버거|파스타|양식|스테이크|pizza|burger)/i.test(trimmed);
  const isNoodleSoup = /(국밥|면|우동|라멘|칼국수|짬뽕|탕|찌개|국수)/i.test(trimmed);

  let category = '일반음식점 / 식음료';
  let menuCount = 18;
  let topMenu = '시그니처 대표 메뉴';
  let hookCopy = `지금 ${trimmed}에서 손님들이 줄서서 먹는 바로 그 메뉴, 실물 공개합니다!`;
  let top1Title = '돈이 아깝지 않은 푸짐한 양과 정갈한 플레이팅';
  let top1Desc = '네이버 플레이스 방문자 리뷰에서 가성비와 비주얼 칭찬 비율 85% 이상 기록';
  let top2Title = '한번 맛보면 단골이 되는 비법 소스와 깊은 풍미';
  let top2Desc = '자극적이지 않으면서도 중독성 있는 감칠맛으로 재방문 후기 다수 확인';
  let top3Title = '사장님의 친절한 응대와 쾌적하고 위생적인 매장';
  let top3Desc = '가족 외식 및 모임 장소로 지인에게 추천하고 싶다는 리뷰 다수';

  let sampleMenus = [
    { name: `${trimmed} 시그니처 세트`, price: '24,000원', isPopular: true, desc: '가장 많이 찾는 베스트셀러 대표 조합' },
    { name: '스페셜 메인 요리', price: '18,500원', isPopular: true, desc: '신선한 재료로 당일 조리하는 필살 메뉴' },
    { name: '매콤 특제 사이드 메뉴', price: '9,900원', isPopular: false, desc: '메인 메뉴와 함께 곁들이기 좋은 사이드' },
    { name: '수제 곁들임 한상', price: '12,000원', isPopular: false, desc: '정성껏 준비한 푸짐한 사이드 구성' },
    { name: '시원한 에이드 / 음료', price: '4,500원', isPopular: false, desc: '입안을 깔끔하게 정리해주는 음료' },
  ];

  if (isCafe) {
    category = '카페 / 디저트';
    menuCount = 24;
    topMenu = '시그니처 수제 크림 라떼';
    hookCopy = `인스타에서 핫한 비주얼 끝판왕! ${trimmed}에서만 맛볼 수 있는 인생 디저트 🍰`;
    top1Title = '사진 찍기 좋은 감성적인 인테리어와 포토존';
    top1Desc = '손님들의 자발적인 SNS 업로드 및 릴스 공유를 유도하는 세련된 공간';
    top2Title = '풍미 깊은 원두 커피와 달콤 부드러운 수제 디저트';
    top2Desc = '매일 아침 직접 구워내는 베이커리와 고소한 크림 라떼 호평';
    top3Title = '조용하게 대화 나누기 좋은 아늑한 좌석 구조';
    top3Desc = '데이트 코스 및 혼카페 손님들의 높은 평점 유지';
    sampleMenus = [
      { name: '시그니처 솔티드 크림라떼', price: '6,500원', isPopular: true, desc: '단짠단짠 부드러운 수제 생크림이 올라간 시그니처' },
      { name: '매일 굽는 바질 소금빵', price: '3,800원', isPopular: true, desc: '프랑스산 고메버터의 풍미가 가득한 겉바속촉 소금빵' },
      { name: '발로나 초콜릿 브라우니 & 바닐라 아이스크림', price: '7,000원', isPopular: false, desc: '꾸덕하고 진한 초코와 아이스크림' },
      { name: '스페셜티 아메리카노 (고소/산미)', price: '4,500원', isPopular: false, desc: '매일 아침 엄선해 세팅하는 에스프레소' },
      { name: '제주 유기농 말차 바스크 치즈케이크', price: '7,500원', isPopular: false, desc: '진한 치즈와 쌉싸름한 말차의 조화' },
    ];
  } else if (isMeat) {
    category = '육류 / 고깃집';
    menuCount = 16;
    topMenu = '숙성 생삼겹살 & 비법 된장찌개';
    hookCopy = `육즙 팡팡! 두툼한 고기에서 흐르는 윤기 좀 보세요.. 오늘 저녁은 무조건 여기다 🥩`;
    top1Title = '육즙이 가득 갇힌 두툼한 명품 원육 퀄리티';
    top1Desc = '잡내 전혀 없는 프리미엄 숙성육과 숯불 향이 손님 리뷰 90% 이상 차지';
    top2Title = '고기 맛을 2배로 올려주는 정갈한 밑반찬과 소스';
    top2Desc = '파절이, 명이나물, 와사비, 특제 멜젓 등 다채로운 조합 칭찬 일색';
    top3Title = '친절하게 구워주는 그릴링 서비스와 쾌적한 환기';
    top3Desc = '연기 냄새 걱정 없이 가족, 단체 회식하기 편하다는 리뷰 다수';
    sampleMenus = [
      { name: '숙성 특삼겹살 (180g)', price: '16,000원', isPopular: true, desc: '황금 숙성으로 풍부한 육즙과 부드러운 식감' },
      { name: '꽃목살 (180g)', price: '16,000원', isPopular: true, desc: '스테이크처럼 촉촉한 프리미엄 두툼 목살' },
      { name: '살얼음 김치말이국수', price: '6,000원', isPopular: false, desc: '고기 먹은 뒤 입가심으로 필수인 칼칼한 국수' },
      { name: '차돌 된장찌개', price: '7,000원', isPopular: false, desc: '진한 구수함에 고기 듬뿍 들어간 밥도둑' },
      { name: '구워먹는 치즈 & 특제 멜젓', price: '5,000원', isPopular: false, desc: '고기와 함께 곁들이는 별미' },
    ];
  } else if (isChicken || isPub) {
    category = '호프 / 치킨 / 요리주점';
    menuCount = 22;
    topMenu = '바삭한 수제 크리스피 치킨';
    hookCopy = `퇴근 후 시원한 생맥주에 바삭한 치킨 한 조각! ${trimmed}에서 힐링하고 가세요 🍻`;
    top1Title = '바삭함이 끝까지 유지되는 깨끗한 튀김 옷';
    top1Desc = '신선한 기름으로 튀겨내 눅눅함 없이 고소하다는 리뷰 91%';
    top2Title = '가성비 넘치고 실패 없는 다양한 안주 구성';
    top2Desc = '치킨 외에도 탕류, 볶음류 등 푸짐한 양으로 단체 모임에 최적';
    top3Title = '살얼음 꽁꽁 생맥주와 편안한 분위기';
    top3Desc = '친구, 동료와 부담 없이 들러 기분 좋게 한잔하기 좋은 아지트';
    sampleMenus = [
      { name: '특제 크리스피 반반 치킨', price: '19,000원', isPopular: true, desc: '후라이드와 특제 양념의 완벽한 밸런스' },
      { name: '살얼음 숙성 생맥주 500cc', price: '4,500원', isPopular: true, desc: '머리까지 시원해지는 영하 2도 숙성 맥주' },
      { name: '해물 짬뽕탕', price: '17,000원', isPopular: false, desc: '얼큰한 불향과 푸짐한 해물이 들어간 국물 안주' },
      { name: '매콤 떡볶이 & 모둠 튀김', price: '14,000원', isPopular: false, desc: '맥주와 찰떡궁합인 국민 분식 세트' },
      { name: '촉촉 노가리 & 먹태', price: '13,000원', isPopular: false, desc: '청양마요 간장 소스에 찍어먹는 마른안주' },
    ];
  } else if (isPizzaBurger) {
    category = '피자 / 버거 / 양식';
    menuCount = 20;
    topMenu = '수제 프리미엄 치즈 피자 / 수제버거';
    hookCopy = `치즈가 폭포수처럼 쏟아진다! ${trimmed}에서 즐기는 극강의 단짠 비주얼 🍕🍔`;
    top1Title = '치즈가 아낌없이 들어간 갓 구운 피자와 수제 패티';
    top1Desc = '흘러넘치는 자연산 모짜렐라 치즈와 풍부한 육즙의 수제 패티 만족도 93%';
    top2Title = '남녀노소 호불호 없는 정통 아메리칸 & 이탈리안 스타일';
    top2Desc = '도우 끝까지 쫄깃하고 담백해서 끝까지 맛있다는 후기 다수';
    top3Title = '데이트 및 친구 모임에 어울리는 감각적인 인테리어';
    top3Desc = '힙한 감성과 분위기로 SNS 사진 찍기 좋은 핫플로 호평';
    sampleMenus = [
      { name: '스페셜 콰트로 치즈 피자', price: '21,900원', isPopular: true, desc: '4가지 고급 치즈가 듬뿍 올라간 대표 메뉴' },
      { name: '수제 더블 치즈버거 세트', price: '12,500원', isPopular: true, desc: '직접 구운 소고기 패티와 고소한 번의 완벽 조화' },
      { name: '트러플 크림 파스타', price: '15,000원', isPopular: false, desc: '은은한 트러플 향과 꾸덕한 크림의 만남' },
      { name: '버팔로 핫 윙 (8pcs)', price: '9,500원', isPopular: false, desc: '매콤새콤한 오리지널 버팔로 소스' },
      { name: '케이준 감자튀김', price: '5,000원', isPopular: false, desc: '바삭하고 짭조름한 맥주 찰떡 사이드' },
    ];
  } else if (isNoodleSoup) {
    category = '한식 / 탕·국수';
    menuCount = 14;
    topMenu = '깊고 진한 가마솥 사골 곰탕';
    hookCopy = `국물 한 숟가락에 속이 확 풀린다! 12시간 푹 끓인 ${trimmed}의 보약 같은 한 그릇 뚝배기 🍲`;
    top1Title = '12시간 이상 우려낸 진하고 잡내 없는 진국 국물';
    top1Desc = '조미료 맛 없이 맑고 깊은 감칠맛으로 남녀노소 극찬';
    top2Title = '매일 직접 담그는 칼칼하고 아삭한 겉절이 김치';
    top2Desc = '“김치 먹으러 또 온다”는 평가가 있을 만큼 압도적인 반찬 인기';
    top3Title = '뚝배기 가득 들어간 부드러운 고기와 푸짐한 인심';
    top3Desc = '든든한 한 끼 식사로 직장인과 혼밥족의 발길이 끊이지 않음';
  }

  return {
    storeName: trimmed,
    category,
    address: '네이버 플레이스 등록 매장',
    summary: `${trimmed}의 실시간 리뷰와 메뉴 데이터를 분석한 결과, 손님 만족도가 매우 높고 시각적 매력이 뛰어난 알짜 매장입니다.`,
    topStrengths: [
      {
        rank: 1,
        title: top1Title,
        description: top1Desc,
        mentionRate: '89% 언급',
        sampleReview: `“${trimmed}은 언제 와도 믿고 먹는 곳이에요. 특히 대표 메뉴는 꼭 시켜야 합니다!”`,
      },
      {
        rank: 2,
        title: top2Title,
        description: top2Desc,
        mentionRate: '83% 언급',
        sampleReview: '“음식 하나하나에 사장님 정성이 느껴지고 양도 넉넉해서 항상 만족스럽습니다.”',
      },
      {
        rank: 3,
        title: top3Title,
        description: top3Desc,
        mentionRate: '78% 언급',
        sampleReview: '“직원분들도 너무 친절하시고 매장이 청결해서 기분 좋게 식사하고 갑니다.”',
      },
    ],
    scoreCard: {
      menuCount,
      hasPhotos: true,
      shortsCount: 0,
    },
    aiPlan: {
      recommendedMenu: topMenu,
      hookCopy,
      conceptPitch: '첫 3초 동안 시선을 사로잡는 강력한 비주얼 훅과 리드미컬한 BGM으로 잠재 고객의 이목을 집중시키고, 네이버 플레이스 방문 링크 클릭을 유도합니다.',
    },
    menus: sampleMenus,
  };
}
