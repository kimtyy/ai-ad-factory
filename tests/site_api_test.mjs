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
  if (u.includes('/search/v1/local')) return resp({ items: LOCAL_ITEMS });                  // 서버가 5개로 자르고 중복을 걸러야 함
  if (u.includes('/search/v1/blog')) return resp({ items: BLOGS });
  if (u.includes('m.search.naver.com')) return resp(null, searchHtml);
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

console.log(fails ? 'FAILURES: ' + fails : 'FAILURES: 0');
process.exit(fails ? 1 : 0);
