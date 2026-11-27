// site/api 서버 로직 테스트 (가짜 fetch). 실행: node tests/site_api_test.mjs  →  "PASS …" / "FAIL …" 한 줄씩 (Python 스위트가 읽는다)
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const ROOT = process.cwd();
const load = async (rel) => (await import(pathToFileURL(path.join(ROOT, 'site', rel)).href));
let fails = 0;
const check = (c, m) => { if (!c) fails++; console.log((c ? 'PASS ' : 'FAIL ') + m); };

process.env.NAVER_CLIENT_ID = 'test-id';
process.env.NAVER_CLIENT_SECRET = 'test-secret';
delete process.env.YOUTUBE_API_KEY;
delete process.env.GOOGLE_SHEETS_WEBHOOK_URL;

// ---------------------------------------------------------------- 가짜 네이버 플레이스 페이지
function apolloHtml(pid, name, road, addr, { keywords = ['눈꽃맥주', '설맥치킨'], menus = [['눈꽃빙수', '14900'], ['골뱅이 무침(기본)', '18900']] } = {}) {
  const state = {
    [`PlaceDetailBase:${pid}`]: { __typename: 'PlaceDetailBase', id: pid, name, category: '맥주,호프', roadAddress: road, address: addr, conveniences: ['단체 이용 가능', '예약'] },
    'RestaurantSeatItems:1': { __typename: 'RestaurantSeatItems', name: '단체석', description: '최소 10명 ~ 최대 30명' },
    'FsasReview:a': { __typename: 'FsasReview', type: 'blog', title: '가평 설맥 후기', contents: '눈꽃맥주가 시원해요', url: 'https://m.blog.naver.com/x/1' },
    [`VisitorReviewStatsResult:${pid}`]: { __typename: 'VisitorReviewStatsResult', analysis: { votedKeyword: { details: [
      { displayName: '친절해요', count: 11 }, { displayName: '음식이 맛있어요', count: 24 }, { displayName: '단체모임 하기 좋아요', count: 12 }] } } },
  };
  menus.forEach(([n, p], i) => { state[`PlaceDetail_BaeminMenu:${i}`] = { __typename: 'PlaceDetail_BaeminMenu', name: n, price: p }; });
  return `<html><script>window.__APOLLO_STATE__ = ${JSON.stringify(state)};\n</script><script>{"keywordList":${JSON.stringify(keywords)}}</script></html>`;
}
const RIGHT = '1430341417';
const WRONG = '1999999999';
const PAGES = {
  [RIGHT]: apolloHtml(RIGHT, '설맥 가평현리점', '경기 가평군 조종면 조종희망로 16-19 1층', '경기 가평군 조종면 현리 259-10'),
  [WRONG]: apolloHtml(WRONG, '설맥 강남점', '서울 강남구 테헤란로 152', '서울 강남구 역삼동 737', { keywords: ['강남키워드'], menus: [['엉뚱한 메뉴', '1000']] }),
};
let searchHtml = `<a href="https://m.place.naver.com/restaurant/${WRONG}/home">a</a><a href="https://m.place.naver.com/restaurant/${RIGHT}/home">b</a>`;

const LOCAL_ITEMS = [
  { title: '<b>설맥</b> 가평현리점', category: '음식점>호프', roadAddress: '경기 가평군 조종면 조종희망로 16-19 1층', address: '경기 가평군 조종면 현리 259-10', link: '' },
  { title: '설맥 강남점', category: '음식점>호프', roadAddress: '서울 강남구 테헤란로 152', address: '서울 강남구 역삼동 737', link: '' },
  { title: '<b>설맥</b> 가평현리점', category: '음식점>호프', roadAddress: '경기 가평군 조종면 조종희망로 16-19 1층', address: '경기 가평군 조종면 현리 259-10', link: '' },   // 중복
  { title: '설맥 수원점', category: '음식점>호프', roadAddress: '경기 수원시 팔달구', address: '', link: '' },
  { title: '설맥 부산점', category: '음식점>호프', roadAddress: '부산 해운대구', address: '', link: '' },
  { title: '설맥 제주점', category: '음식점>호프', roadAddress: '제주 제주시', address: '', link: '' },
];
const BLOGS = [
  { title: '가평 설맥 눈꽃맥주 후기', description: '가평 현리 <b>설맥</b>에서 눈꽃맥주 마셨어요' },
  { title: '강남 설맥 후기', description: '강남역 설맥 다녀왔어요' },
  { title: '전혀 다른 가게 맛집', description: '다른 가게 이야기' },
];

let calls = [];
const resp = (obj, text) => ({ ok: true, status: 200, json: async () => obj, text: async () => text ?? '' });
globalThis.fetch = async (url) => {
  const u = String(url);
  calls.push(u);
  if (u.includes('/search/v1/local')) {
    if (u.includes('%EC%82%BC%EC%A7%84') || u.includes('삼진설비')) {
      return resp({ items: [{ title: '<b>삼진설비</b>', category: '수도설비공사', roadAddress: '서울 중랑구 망우로 123', address: '서울 중랑구 망우동 1', link: '' }] });
    }
    return resp({ items: LOCAL_ITEMS });                  // 서버가 5개로 자르고 중복을 걸러야 함
  }
  if (u.includes('/search/v1/blog')) {
    if (u.includes('%EC%82%BC%EC%A7%84') || u.includes('삼진설비')) {
      return resp({ items: [] }); // 리뷰가 적은 비식당 폴백 테스트
    }
    return resp({ items: BLOGS });
  }
  if (u.includes('m.search.naver.com')) {
    if (u.includes('%EC%82%BC%EC%A7%84') || u.includes('삼진설비')) {
      return resp(null, '<html><body>검색결과 없음</body></html>');
    }
    return resp(null, searchHtml);
  }
  const m = u.match(/m\.place\.naver\.com\/place\/(\d+)\//);
  if (m && PAGES[m[1]]) return resp(null, PAGES[m[1]]);
  if (u.includes('youtube.com')) return resp(null, '');
  return { ok: false, status: 404, json: async () => ({}), text: async () => '' };
};

function call(handler, { method = 'GET', query = {}, body } = {}) {
  return new Promise((resolve) => {
    const res = { _s: 200, _h: {}, status(c) { this._s = c; return this; }, json(o) { resolve([this._s, o]); }, end() { resolve([this._s, null]); }, setHeader(k, v) { this._h[k] = v; } };
    handler({ method, query, body, headers: {} }, res);
  });
}

// ---------------------------------------------------------------- 1. /api/search-stores
const search = (await load('api/search-stores.js')).default;
let [s, r] = await call(search, { query: { q: '설맥' } });
check(s === 200 && r.items.length === 5, `후보 최대 5개 (${r.items.length}개)`);
check(r.items[0].storeName === '설맥 가평현리점' && r.items[0].roadAddress.startsWith('경기 가평군') && r.items[0].category === '음식점>호프' && !JSON.stringify(r).includes('<b>'), 'storeName·category·roadAddress·address·link, HTML 태그 제거');
check(r.items.filter((x) => x.storeName === '설맥 가평현리점').length === 1, '같은 상호+주소 중복 제거');
check(Object.keys(r.items[0]).sort().join(',') === 'address,category,link,roadAddress,storeName', '응답 항목: storeName, category, roadAddress, address, link');
check(calls.some((c) => c.includes('naverapihub.apigw.ntruss.com/search/v1/local') && c.includes('display=5')), 'NCP NAVER API HUB local(display=5) 호출');
[s, r] = await call(search, { query: { q: '설' } });
check(s === 400 && r.items.length === 0, '1글자는 400');
[s] = await call(search, { method: 'POST', query: { q: '설맥' } });
check(s === 405, 'GET 외 메서드는 405');
const keep = [process.env.NAVER_CLIENT_ID, process.env.NAVER_CLIENT_SECRET];
delete process.env.NAVER_CLIENT_ID;
[s, r] = await call(search, { query: { q: '설맥' } });
check(s === 200 && Array.isArray(r.items) && r.items.length === 0, 'API 키가 없어도 빈 목록으로 응답 (화면은 입력한 이름으로 계속)');
[process.env.NAVER_CLIENT_ID, process.env.NAVER_CLIENT_SECRET] = keep;
{
  const real = globalThis.fetch;
  globalThis.fetch = async (url) => (String(url).includes('ntruss') ? { ok: false, status: 500, json: async () => ({}) } : real(url));
  const legacy = [];
  const real2 = globalThis.fetch;
  globalThis.fetch = async (url, o) => { if (String(url).includes('openapi.naver.com')) legacy.push(url); return String(url).includes('openapi.naver.com') ? resp({ items: LOCAL_ITEMS.slice(0, 2) }) : real2(url, o); };
  [s, r] = await call(search, { query: { q: '설맥 폴백' } });
  check(s === 200 && r.items.length === 2 && legacy.length === 1, 'NCP 실패 시 OpenAPI로 폴백');
  globalThis.fetch = real;
}

// ---------------------------------------------------------------- 2. /api/diagnose
const diagnose = (await load('api/diagnose.js')).default;
calls = [];
[s, r] = await call(diagnose, { method: 'POST', body: { storeName: '설맥 가평현리점', address: '경기 가평군 조종면 조종희망로 16-19 1층' } });
check(s === 200 && r.placeId === RIGHT && r.address.startsWith('경기 가평군') && r.storeName === '설맥 가평현리점', `주소로 정확한 지점 선택 — 검색 1순위가 다른 지점이어도 placeId ${r.placeId}`);
check(r.menusSource === 'official' && r.menus.map((m) => m.name).join('/') === '눈꽃빙수/골뱅이 무침' && !JSON.stringify(r.menus).includes('엉뚱'), '공식 메뉴판: 그 매장 것만 (괄호 옵션 정리)');
check(r.officialSummary.includes('대표 키워드: 눈꽃맥주, 설맥치킨') && r.officialSummary.includes('좌석: 단체석') && r.officialSummary.includes('음식이 맛있어요(24)'), `officialSummary(공식 매장 특징): ${r.officialSummary.slice(0, 60)}...`);
check(r.strengthsSource === 'official' && r.topStrengths[0].title.includes('음식이 맛있어요') && r.topStrengths[0].mentionRate === '24명 선택' && r.topStrengths.every((x) => x.isFallback === false), '필살기 TOP 3: 방문자 선택 키워드 기반 (근거 있는 항목)');
check(r.reviewSummary.includes('눈꽃맥주가 시원해요') && r.reviewSummary.includes('가평') && !r.reviewSummary.includes('강남역') && !r.reviewSummary.includes('다른 가게'), '후기 요약: 다른 지점·다른 가게 글은 걸러짐');
check(r.officialKeywords.length === 2 && r.scoreCard.menuCount === 2, '대표 키워드·메뉴 수');
[s, r] = await call(diagnose, { method: 'POST', body: { storeName: '설맥 가평현리점', address: '서울 강남구 역삼로 1' } });
check(r.placeId === WRONG, '주소가 강남이면 강남 지점이 맞는 지점으로 선택됨 (주소 기준)');
searchHtml = `<a href="https://m.place.naver.com/restaurant/${WRONG}/home">a</a>`;
[s, r] = await call(diagnose, { method: 'POST', body: { storeName: '설맥 가평현리점', address: '경기 가평군 조종면' } });
check(s === 200 && r.placeId === '' && r.menusSource === 'fallback' && r.strengthsSource !== 'official' && r.topStrengths.some((x) => x.isFallback === true), '주소가 맞는 지점이 없으면 엉뚱한 지점 정보를 쓰지 않고 fallback 표시(isFallback)');
check(!r.menus.some((m) => m.name === '엉뚱한 메뉴') && r.scoreCard.menuCount === 0, 'fallback 메뉴는 메뉴 수에 세지 않음');
searchHtml = `<a href="https://m.place.naver.com/restaurant/${WRONG}/home">a</a><a href="https://m.place.naver.com/restaurant/${RIGHT}/home">b</a>`;
[s, r] = await call(diagnose, { method: 'GET', query: { store: '설맥 가평현리점', place_id: RIGHT } });
check(s === 200 && r.placeId === RIGHT && r.menusSource === 'official', 'GET ?store=&place_id= 도 동작 (작업실 연동용)');
calls = [];
[s, r] = await call(diagnose, { method: 'POST', body: { storeName: '설맥 가평현리점', placeId: RIGHT } });
check(!calls.some((c) => c.includes('m.search.naver.com')) && r.placeId === RIGHT, 'placeId를 주면 검색 없이 그 매장 공식정보만 읽음');
[s, r] = await call(diagnose, { method: 'POST', body: { storeName: '설맥 가평현리점', placeId: 'abc; drop' } });
check(s === 200 && /^\d*$/.test(r.placeId), '형식이 틀린 placeId는 무시 (숫자만)');
[s] = await call(diagnose, { method: 'POST', body: { storeName: '  ' } });
check(s === 400, '상호명이 비면 400');
[s] = await call(diagnose, { method: 'PUT', body: {} });
check(s === 405, 'POST·GET 외 메서드는 405');
[s, r] = await call(diagnose, { method: 'POST', body: JSON.stringify({ storeName: '설맥 가평현리점', placeId: RIGHT }) });
check(s === 200 && r.placeId === RIGHT, '문자열 JSON 본문도 허용 (기존 호환)');

// 2-1. 비식당 업종 (삼진설비 / 수도설비공사) 진단 테스트
[s, r] = await call(diagnose, { method: 'POST', body: { storeName: '삼진설비' } });
check(s === 200 && r.businessType === 'service', `비식당 업종 자동 판별: businessType=${r.businessType}`);
check(r.scoreCard.menuCount === 0, `비식당 공식 메뉴 미등록 시 menuCount=0 (결과: ${r.scoreCard.menuCount})`);
const topTitles = r.topStrengths.map((x) => x.title).join(' / ');
check(!topTitles.includes('상차림') && !topTitles.includes('외식') && !topTitles.includes('시그니처 메뉴'), '비식당 폴백: 상차림/가족외식/시그니처 메뉴 단어 미포함');
check(r.topStrengths[0].title.includes('핵심 전문 서비스') && r.topStrengths[1].title.includes('꼼꼼한 진행과 친절한 응대') && r.topStrengths[2].title.includes('네이버 플레이스 검색으로 바로 연결'), '비식당 TOP 3 신뢰 폴백 문구 정상 생성');
check(r.topStrengths.every((x) => !x.sampleReview), '비식당 폴백 시 빈 sampleReview ("" 보장)');
check(!r.aiPlan.hookCopy.includes('실물 영접') && !r.aiPlan.hookCopy.includes('시켜야죠') && !r.aiPlan.hookCopy.includes('입맛 당기는'), '비식당 훅 카피: 음식 관련 표현(실물 영접/시켜야죠/입맛 당기는) 미포함');
check(r.aiPlan.hookCopy.includes('🔧') || r.aiPlan.hookCopy.includes('현장 해결'), `비식당 설비 훅 카피 생성 (${r.aiPlan.hookCopy})`);

// ---------------------------------------------------------------- 3. /api/lead : 주소·placeId·메모 저장 + 입력 정리
const lead = (await load('api/lead.js')).default;
[s, r] = await call(lead, { method: 'POST', body: { storeName: '설맥 가평현리점', ownerName: '홍길동', phoneNumber: '010-1234-5678',
  planOptions: { selectedMenu: '눈꽃빙수', font: '둥근 예능 팝체', voice: '차분한 남성', bgm: '따뜻한', address: '경기 가평군 조종면', placeId: RIGHT, ownerMemo: '단체석 30석 완비\n한우육회 메인 강조', evil: '<script>' } } });
const po = r.lead.planOptions;
check(s === 200 && po.selectedMenu === '눈꽃빙수' && po.font === '둥근 예능 팝체' && po.voice === '차분한 남성' && po.bgm === '따뜻한' && po.address === '경기 가평군 조종면' && po.placeId === RIGHT, 'selectedMenu·font·voice·bgm·address·placeId 저장');
check(po.ownerMemo === '단체석 30석 완비 한우육회 메인 강조' && !('evil' in po), 'ownerMemo 저장(줄바꿈 정리), 허용하지 않은 항목은 버림');
[s, r] = await call(lead, { method: 'POST', body: { storeName: '가게', phoneNumber: '010-1111-2222', planOptions: { placeId: 'x1', ownerMemo: 'ㅁ'.repeat(300), address: 'a'.repeat(500) } } });
check(r.lead.planOptions.placeId === '' && r.lead.planOptions.ownerMemo.length === 100 && r.lead.planOptions.address.length === 120, 'placeId는 숫자만, 메모 100자·주소 120자 제한');
[s, r] = await call(lead, { method: 'POST', body: { storeName: '가게', phoneNumber: '010-1111-2222' } });
check(s === 200 && r.lead.planOptions.ownerMemo === '' && r.lead.planOptions.selectedMenu === '대표 메뉴', '메모를 비워도 신청 가능 (기본값)');
[s] = await call(lead, { method: 'POST', body: { storeName: '가게' } });
check(s === 400, '상호·연락처 없으면 400');

// ---------------------------------------------------------------- 4. 작업실 열기 URL (src/utils/studioUrl.ts)
try {
  const { buildStudioUrl } = await load('src/utils/studioUrl.ts');
  const url = buildStudioUrl({ storeName: '설맥 가평현리점', planOptions: { selectedMenu: '눈꽃빙수', font: '둥근 예능 팝체 (AI 추천)', voice: '차분한 남성', bgm: 'AI 추천', address: '경기 가평군 조종면 조종희망로 16-19', placeId: RIGHT, ownerMemo: '단체석 30석 완비, 한우육회 메인 강조' } });
  const u = new URL(url);
  check(u.origin === 'https://shopstory-store.streamlit.app' && u.searchParams.get('store') === '설맥 가평현리점' && u.searchParams.get('place_id') === RIGHT && u.searchParams.get('menu') === '눈꽃빙수'
    && u.searchParams.get('font') === '둥근 예능 팝체 (AI 추천)' && u.searchParams.get('voice') === '차분한 남성' && u.searchParams.get('bgm') === 'AI 추천'
    && u.searchParams.get('memo') === '단체석 30석 완비, 한우육회 메인 강조' && u.searchParams.get('address') === '경기 가평군 조종면 조종희망로 16-19', '작업실 URL: store·address·place_id·menu·font·voice·bgm·memo');
  check(url.includes('store=%EC%84%A4') && !url.includes(' ') && !url.includes('+') && url.includes('%2C'), 'encodeURIComponent 적용 (공백·쉼표 인코딩)');
  const bare = buildStudioUrl({ storeName: '가게', planOptions: { selectedMenu: '', font: '', voice: '', bgm: '' } });
  check(bare === 'https://shopstory-store.streamlit.app/?store=%EA%B0%80%EA%B2%8C', '빈 값은 붙이지 않음');
  check(buildStudioUrl({ storeName: '가게', planOptions: { ownerMemo: 'ㅁ'.repeat(300) } }).split('memo=')[1].length === decodeURIComponent('ㅁ'.repeat(100)).length * 9, '메모는 100자까지');
} catch (e) {
  check(false, 'studioUrl.ts 불러오기 실패: ' + e.message);
}

// ---------------------------------------------------------------- 5. /api/recent-stores
const recentStores = (await load('api/recent-stores.js')).default;
[s, r] = await call(recentStores, { method: 'GET' });
check(s === 200 && Array.isArray(r.stores) && r.stores.length >= 2, `최근 진단 매장 조회 성공 (${r.stores.length}개)`);
check(r.stores.some((x) => x.storeName === '설맥 가평현리점') && r.stores.some((x) => x.storeName === '효정명인갈비'), '초기 시드 매장(설맥 가평현리점, 효정명인갈비) 포함');
[s] = await call(recentStores, { method: 'POST' });
check(s === 405, 'GET 외 메서드는 405');

// ---------------------------------------------------------------- 6. 리드 중복 제출 방지 & /api/admin 중복 제거
// 6-1. 동일 신청 더블클릭 중복 제출 방지
[s, r] = await call(lead, { method: 'POST', body: { storeName: '중복테스트매장', ownerName: '김사장', phoneNumber: '010-9999-8888', planOptions: { selectedMenu: '메인메뉴' } } });
const firstLeadId = r.lead.id;
[s, r] = await call(lead, { method: 'POST', body: { storeName: '중복테스트매장', ownerName: '김사장', phoneNumber: '010-9999-8888', planOptions: { selectedMenu: '메인메뉴' } } });
check(s === 200 && r.lead.id === firstLeadId && r.message.includes('이미 접수'), '동일 신청 더블클릭 시 중복 생성 없이 기존 건 반환');

// 6-2. /api/admin 관리자 목록 중복 제거 확인
const admin = (await load('api/admin.js')).default;
[s, r] = await call(admin, { method: 'POST', body: { action: 'login', password: 'shopstory2026!' } });
const adminToken = r.token;
check(s === 200 && !!adminToken, '관리자 로그인 성공');
[s, r] = await call(admin, { method: 'GET', query: { token: adminToken } });
check(s === 200 && Array.isArray(r.leads), '관리자 리드 목록 조회 성공');
const dupCount = r.leads.filter((l) => l.phoneNumber === '010-9999-8888').length;
check(dupCount === 1, `동일 리드 중복 제거 확인 (결과: ${dupCount}개)`);

// 6-3. 클라이언트 시계가 어긋나도(createdAt 1시간 전) 서버 수신 시각 기준으로 더블클릭 중복 차단
const skew = { storeName: '시계오차매장', ownerName: '이사장', phoneNumber: '010-7777-6666', createdAt: new Date(Date.now() - 3600e3).toISOString() };
[s, r] = await call(lead, { method: 'POST', body: skew });
const skewId = r.lead.id;
[s, r] = await call(lead, { method: 'POST', body: skew });
check(r.lead.id === skewId, '클라이언트 시계 오차가 있어도 중복 접수 차단');
// 6-4. 다른 메뉴 신청은 별개 건으로 접수
[s, r] = await call(lead, { method: 'POST', body: { ...skew, planOptions: { selectedMenu: '다른메뉴' } } });
check(r.lead.id !== skewId, '메뉴가 다른 신청은 별개 건');
// 6-5. 서버 최초 접수 값(상태 등)이 메모리의 옛 값에 덮이지 않음 — 시트 없는 환경에서는 병합 순서만 확인
const dupAdmin = (await call(admin, { method: 'GET', query: { token: adminToken } }))[1].leads.filter((l) => l.phoneNumber === '010-7777-6666');
check(dupAdmin.length === 2, `관리자 목록: 별개 신청 2건 유지 (${dupAdmin.length})`);

// ---------------------------------------------------------------- 7. ?ref= 영업자 추적 & 3단계(super/manager/agent) 권한 분리 테스트
global.__SHOPSTORY_MOCK_AGENTS__ = [
  { code: 'team01', name: '김팀장', password: 'pass_team01', role: 'manager', manager_code: 'hq', active: 'Y' },
  { code: 'agent01', name: '이영업', password: 'pass_agent01', role: 'agent', manager_code: 'team01', active: 'Y' },
  { code: 'team02', name: '박팀장', password: 'pass_team02', role: 'manager', manager_code: 'hq', active: 'Y' },
  { code: 'agent02', name: '최영업', password: 'pass_agent02', role: 'agent', manager_code: 'team02', active: 'Y' },
  { code: 'agent_dormant', name: '휴면영업', password: 'pass_dormant', role: 'agent', manager_code: 'team01', active: 'N' },
];

// 7-1. ?ref= 파라미터 기반 리드 저장 & managerCode 자동 매핑
[s, r] = await call(lead, {
  method: 'POST',
  body: {
    storeName: '영업자01매장',
    ownerName: '오너1',
    phoneNumber: '010-8888-1111',
    refCode: 'agent01',
    planOptions: { selectedMenu: '시그니처' },
  },
});
const leadAgent01 = r.lead;
check(s === 200 && leadAgent01.refCode === 'agent01' && leadAgent01.managerCode === 'team01', 'ref=agent01 접수 시 refCode: agent01, managerCode: team01 자동 매핑');

[s, r] = await call(lead, {
  method: 'POST',
  body: {
    storeName: '영업자02매장',
    ownerName: '오너2',
    phoneNumber: '010-8888-2222',
    refCode: 'agent02',
    planOptions: { selectedMenu: '메인요리' },
  },
});
const leadAgent02 = r.lead;
check(s === 200 && leadAgent02.refCode === 'agent02' && leadAgent02.managerCode === 'team02', 'ref=agent02 접수 시 refCode: agent02, managerCode: team02 자동 매핑');

[s, r] = await call(lead, {
  method: 'POST',
  body: {
    storeName: '본사직속매장',
    ownerName: '오너3',
    phoneNumber: '010-8888-3333',
    // refCode 미지정
    planOptions: { selectedMenu: '기본메뉴' },
  },
});
const leadHq = r.lead;
check(s === 200 && leadHq.refCode === 'hq' && leadHq.managerCode === 'hq', 'refCode 없을 시 기본값 hq / hq 매핑');

// 7-2. 3단계 로그인 인증 검증 (super, manager, agent)
// (1) 수퍼관리자 (code 공란 or hq + master password)
[s, r] = await call(admin, { method: 'POST', body: { action: 'login', code: '', password: 'shopstory2026!' } });
const superToken = r.token;
check(s === 200 && !!superToken && r.user.role === 'super' && r.user.code === 'hq', '수퍼관리자 로그인 성공 (role: super)');

// (2) 중간관리자 (team01 + password)
[s, r] = await call(admin, { method: 'POST', body: { action: 'login', code: 'team01', password: 'pass_team01' } });
const managerToken = r.token;
check(s === 200 && !!managerToken && r.user.role === 'manager' && r.user.code === 'team01', '중간관리자 로그인 성공 (role: manager)');

// (3) 영업자 (agent01 + password)
[s, r] = await call(admin, { method: 'POST', body: { action: 'login', code: 'agent01', password: 'pass_agent01' } });
const agent01Token = r.token;
check(s === 200 && !!agent01Token && r.user.role === 'agent' && r.user.code === 'agent01', '영업자 로그인 성공 (role: agent)');

// (4) 틀린 비밀번호 or 비활성 영업자 로그인 차단
[s, r] = await call(admin, { method: 'POST', body: { action: 'login', code: 'agent01', password: 'wrong_password' } });
check(s === 401, '틀린 비밀번호 로그인 시 401 거부');

[s, r] = await call(admin, { method: 'POST', body: { action: 'login', code: 'agent_dormant', password: 'pass_dormant' } });
check(s === 401, '비활성(active: N) 영업자 로그인 시 401 거부');

// 7-3. 서버측 원천 필터링 (GET /api/admin) 검증
// (1) Super: 전체 리드 및 통계 집계 반환
[s, r] = await call(admin, { method: 'GET', query: { token: superToken } });
check(s === 200 && r.leads.some((l) => l.id === leadAgent01.id) && r.leads.some((l) => l.id === leadAgent02.id) && r.leads.some((l) => l.id === leadHq.id), 'Super는 전체 팀/영업자 리드 모두 조회');
check(r.stats && r.stats.byAgent && r.stats.byAgent.agent01 >= 1 && r.stats.byAgent.agent02 >= 1, 'Super에게 영업자별 건수 집계 stats 제공');

// (2) Manager(team01): 본인 직속 및 소속 팀원(agent01) 리드만 반환 (타 팀 agent02 리드 제외)
[s, r] = await call(admin, { method: 'GET', query: { token: managerToken } });
const mgrLeads = r.leads;
check(s === 200 && mgrLeads.some((l) => l.id === leadAgent01.id), 'Manager(team01): 소속 영업자(agent01) 리드 포함');
check(!mgrLeads.some((l) => l.id === leadAgent02.id), 'Manager(team01): 타 팀 소속(agent02) 리드 원천 차단');
check(!mgrLeads.some((l) => l.id === leadHq.id), 'Manager(team01): 타 부서(hq) 직속 리드 원천 차단');

// (3) Agent(agent01): 오직 본인(agent01) 리드만 반환
[s, r] = await call(admin, { method: 'GET', query: { token: agent01Token } });
const ag01Leads = r.leads;
check(s === 200 && ag01Leads.every((l) => l.refCode === 'agent01'), 'Agent(agent01): 오직 본인 리드만 반환');
check(!ag01Leads.some((l) => l.id === leadAgent02.id) && !ag01Leads.some((l) => l.id === leadHq.id), 'Agent(agent01): 타 영업자 및 타 팀 리드 원천 차단');

// 7-4. 상태 변경 권한 (POST action='update_status') 검증
// (1) Agent01이 타 영업자(agent02) 리드 상태 변경 시도 -> 403 Forbidden
[s, r] = await call(admin, {
  method: 'POST',
  body: { action: 'update_status', token: agent01Token, leadId: leadAgent02.id, status: '제작완료' },
});
check(s === 403, 'Agent가 타인 리드 상태 변경 시 403 차단');

// (2) Agent01이 본인(agent01) 리드 상태 변경 -> 200 OK
[s, r] = await call(admin, {
  method: 'POST',
  body: { action: 'update_status', token: agent01Token, leadId: leadAgent01.id, status: '제작완료' },
});
check(s === 200 && r.success === true, 'Agent가 본인 리드 상태 변경 성공 (200 OK)');

// (3) Manager(team01)가 타 팀(agent02) 리드 상태 변경 시도 -> 403 Forbidden
[s, r] = await call(admin, {
  method: 'POST',
  body: { action: 'update_status', token: managerToken, leadId: leadAgent02.id, status: '제작완료' },
});
check(s === 403, 'Manager가 타 팀 리드 상태 변경 시 403 차단');

// (4) Manager(team01)가 소속 팀(agent01) 리드 상태 변경 -> 200 OK
[s, r] = await call(admin, {
  method: 'POST',
  body: { action: 'update_status', token: managerToken, leadId: leadAgent01.id, status: '상담완료' },
});
check(s === 200 && r.success === true, 'Manager가 소속 팀 리드 상태 변경 성공 (200 OK)');

// (5) Super는 어떤 리드든 상태 변경 가능 -> 200 OK
[s, r] = await call(admin, {
  method: 'POST',
  body: { action: 'update_status', token: superToken, leadId: leadAgent02.id, status: '제작완료' },
});
check(s === 200 && r.success === true, 'Super는 전체 리드 상태 변경 허용 (200 OK)');

// ---------------------------------------------------------------- 8. 토큰 위조·상태값·잘못된 ref·시트 해시 로그인
{
  const crypto = await import('node:crypto');
  // 8-1. 서명 없는 구형 토큰(adm_아무거나)은 더 이상 super 권한을 주지 않는다
  [s] = await call(admin, { method: 'GET', query: { token: 'adm_forged' } });
  check(s === 401, '서명 없는 adm_ 토큰 위조 → 401');
  // 8-2. 페이로드만 super 로 바꾼 토큰은 서명 불일치로 거부
  const real = (await call(admin, { method: 'POST', body: { action: 'login', code: 'agent01', password: 'pass_agent01' } }))[1].token;
  const [pl, sig] = real.slice(4).split('.');
  const forgedPayload = Buffer.from(JSON.stringify({ ...JSON.parse(Buffer.from(pl, 'base64url').toString()), role: 'super' })).toString('base64url');
  [s] = await call(admin, { method: 'GET', query: { token: `adm_${forgedPayload}.${sig}` } });
  check(s === 401, '페이로드 변조(super 승격) 토큰 → 401');
  // 8-3. 만료 없는 토큰·허용되지 않는 상태 값
  [s] = await call(admin, { method: 'POST', body: { action: 'update_status', leadId: 'x', status: '임의값', token: real } });
  check(s === 400, '허용되지 않는 상태 값 → 400');
  // 8-4. 이상한 ref 로 접수해도 오류 없이 처리 (특수문자 제거 / 미등록 코드 보존 / 객체 입력)
  for (const bad of ['<script>alert(1)</script>', '../../etc', { a: 1 }, '', 'x'.repeat(200)]) {
    [s, r] = await call(lead, { method: 'POST', body: { storeName: `ref테스트${Math.random()}`, phoneNumber: '010-1212-3434', refCode: bad } });
    check(s === 200 && /^[A-Za-z0-9_-]{0,32}$/.test(r.lead.refCode) && r.lead.managerCode === 'hq', `비정상 ref(${typeof bad === 'string' ? bad.slice(0, 12) : 'object'}) → 안전 처리 (${r.lead.refCode})`);
  }
  // 8-5. 시트가 해시만 내려줘도 로그인 가능, 틀린 비밀번호는 거부
  global.__SHOPSTORY_MOCK_AGENTS__.push({ code: 'hashed01', name: '해시', passwordSha256: crypto.createHash('sha256').update('s3cret-pw').digest('hex'), role: 'agent', manager_code: 'hq', active: 'Y' });
  [s] = await call(admin, { method: 'POST', body: { action: 'login', code: 'hashed01', password: 's3cret-pw' } });
  check(s === 200, '해시 저장 계정 로그인 성공');
  [s] = await call(admin, { method: 'POST', body: { action: 'login', code: 'hashed01', password: 'wrong' } });
  check(s === 401, '해시 계정 틀린 비밀번호 거부');
}

console.log(fails ? 'FAILURES: ' + fails : 'FAILURES: 0');
process.exit(fails ? 1 : 0);
