// site/server/naver.js
// 매장 검색·네이버 플레이스 공식정보 공통 모듈 (api/search-stores.js, api/diagnose.js 가 함께 쓴다).
// Vercel 서버리스 안에서만 실행된다. 모든 외부 호출은 실패해도 예외 없이 빈 값으로 돌아온다.

export const UA =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1';

const PLACE_LINK = /place\.naver\.com\/(?:restaurant|cafe|place|bar|hairshop|beauty|accommodation)\/(\d{6,})/g;

export function cleanHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/<[^>]+>/g, '')
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

async function getText(url, headers, ms = 7000) {
  const res = await fetch(url, { headers, signal: AbortSignal.timeout(ms), redirect: 'follow' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.text();
}

// ---------------------------------------------------------------- 1. 네이버 지역 검색 (NCP NAVER API HUB 우선 → OpenAPI 폴백)
export async function searchLocal(query, clientId, clientSecret, display = 5) {
  if (!clientId || !clientSecret || !query) return [];
  const q = encodeURIComponent(query);
  const tries = [
    [`https://naverapihub.apigw.ntruss.com/search/v1/local?query=${q}&display=${display}`,
      { 'X-NCP-APIGW-API-KEY-ID': clientId, 'X-NCP-APIGW-API-KEY': clientSecret }],
    [`https://openapi.naver.com/v1/search/local.json?query=${q}&display=${display}`,
      { 'X-Naver-Client-Id': clientId, 'X-Naver-Client-Secret': clientSecret }],
  ];
  for (const [url, headers] of tries) {
    try {
      const res = await fetch(url, { headers, signal: AbortSignal.timeout(6000) });
      if (!res.ok) continue;
      const data = await res.json();
      if (Array.isArray(data.items) && data.items.length > 0) return data.items;
    } catch (e) {
      console.warn('Naver local search error:', e.message);
    }
  }
  return [];
}

export function toCandidate(item) {
  return {
    storeName: cleanHtml(item.title),
    category: cleanHtml(item.category),
    roadAddress: cleanHtml(item.roadAddress),
    address: cleanHtml(item.address),
    link: cleanHtml(item.link),
  };
}

// ---------------------------------------------------------------- 2. 주소·지역 매칭 (동명 다른 지점이 섞이지 않게)
const REGION_SUFFIX = /(특별시|광역시|특별자치시|특별자치도|도|시|군|구|면|읍|동|리)$/;

export function regionTokens(address) {
  const words = String(address || '').match(/[가-힣]{2,}/g) || [];
  const out = [];
  for (const w of words) {
    const t = w.replace(REGION_SUFFIX, '');
    if (t.length >= 2 && !out.includes(t) && !/^(경기|서울|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)$/.test(t)) out.push(t);
  }
  return out.slice(0, 6);
}

// 후보의 주소가 원하는 주소와 얼마나 겹치는지 (0~1)
export function addressScore(candidateAddr, wantAddr) {
  const want = regionTokens(wantAddr);
  if (want.length === 0) return 0;
  const cand = String(candidateAddr || '');
  const hit = want.filter((t) => cand.includes(t)).length;
  return hit / want.length;
}

function nameKey(s) {
  return String(s || '').replace(/[^가-힣A-Za-z0-9]/g, '').toLowerCase();
}

export function nameScore(a, b) {
  const x = nameKey(a);
  const y = nameKey(b);
  if (!x || !y) return 0;
  if (x === y) return 1;
  if (x.includes(y) || y.includes(x)) return 0.8;
  const first = x.slice(0, 2);
  return first && y.includes(first) ? 0.4 : 0;
}

// ---------------------------------------------------------------- 3. 네이버 플레이스 페이지 파싱 (m.place.naver.com Apollo state)
export function parseApolloState(html) {
  const m = String(html || '').match(/__APOLLO_STATE__\s*=\s*(\{.*?\});\s*\n/s);
  if (!m) return null;
  try {
    return JSON.parse(m[1]);
  } catch (e) {
    return null;
  }
}

function typeOf(key, val) {
  return String((val && val.__typename) || String(key).split(':')[0]);
}

export function parseMenus(state, keywords = []) {
  const groupOf = new Map();
  for (const [key, val] of Object.entries(state || {})) {
    if (!val || typeof val !== 'object') continue;
    const t = typeOf(key, val);
    if (t.includes('Menu') && (t.includes('Group') || t.includes('Category'))) {
      for (const ref of val.menus || []) {
        if (ref && ref.__ref && !groupOf.has(ref.__ref)) groupOf.set(ref.__ref, String(val.name || ''));
      }
    }
  }

  const tier1 = []; // 매장(홀) 공식 메뉴
  const tier2 = []; // 포장 메뉴
  const tier3 = []; // 배달 메뉴
  const seen = new Set();

  const kwList = Array.isArray(keywords) ? keywords.filter((k) => typeof k === 'string' && k.trim().length >= 2) : [];
  const kwTokens = [];
  for (const k of kwList) {
    const cleanK = String(k).replace(/[^가-힣A-Za-z0-9]/g, '');
    for (let i = 0; i <= cleanK.length - 2; i++) {
      const sub = cleanK.slice(i, i + 2);
      if (!kwTokens.includes(sub)) kwTokens.push(sub);
    }
  }

  let orderIndex = 0;
  for (const [key, val] of Object.entries(state || {})) {
    if (!val || typeof val !== 'object') continue;
    const t = typeOf(key, val);
    if (!t.includes('Menu') || t.includes('Group') || t.includes('Category') || !val.name) continue;

    const rawName = cleanHtml(val.name).replace(/[(\[（].*?[)\]）]/g, ' ').replace(/\s+/g, ' ').trim();
    if (rawName.length < 2 || seen.has(rawName)) continue;
    seen.add(rawName);

    let price = '';
    if (typeof val.price === 'string' || typeof val.price === 'number') price = String(val.price);
    else if (val.price && typeof val.price === 'object') price = val.price.displayText || val.price.price || '';
    if (price && !price.endsWith('원') && !isNaN(Number(price))) price = Number(price).toLocaleString('ko-KR') + '원';

    const group = groupOf.get(key) || '';
    const isKeywordExact = kwList.some((k) => rawName.includes(k) || k.includes(rawName));
    const isTokenMatch = kwTokens.some((tok) => rawName.includes(tok));
    const isKeywordMatch = isKeywordExact || isTokenMatch;

    const isPopular = Boolean(
      val.recommend ||
      val.isRecommended ||
      val.isRepresentative ||
      val.isSignature ||
      /대표|시그니처|SIGNATURE|BEST|인기|추천/i.test(group) ||
      isKeywordMatch
    );

    const item = {
      name: rawName,
      price: cleanHtml(price),
      desc: cleanHtml(val.desc || val.description || ''),
      isPopular,
      order: orderIndex++,
      isKeywordMatch,
      isKeywordExact,
    };

    // Tier 분류
    const isDelivery = /baemin|yogiyo|coupang|delivery/i.test(t) || /배달/i.test(group);
    const isTakeout = /takeout|packaging|pickup/i.test(t) || /포장/i.test(group);

    if (isDelivery) {
      tier3.push(item);
    } else if (isTakeout) {
      tier2.push(item);
    } else {
      tier1.push(item);
    }
  }

  // Tier 내부 정렬: 대표/인기(isPopular) 우선, 그 안에서 원래 순서 보존
  const sortFn = (a, b) => {
    if (a.isPopular && !b.isPopular) return -1;
    if (!a.isPopular && b.isPopular) return 1;
    return a.order - b.order;
  };

  tier1.sort(sortFn);
  tier2.sort(sortFn);
  tier3.sort(sortFn);

  // 병합 전략: 1순위 홀 -> 2순위 포장 -> 3순위 배달
  let merged = [];
  if (tier1.length > 0 || tier2.length > 0) {
    merged = [...tier1, ...tier2];
    // 홀/포장이 있는 경우 배달 메뉴는 뒤에 붙이되 isPopular 강등 (홀 대표를 가리지 않도록)
    for (const d of tier3) {
      merged.push({ ...d, isPopular: false });
    }
  } else {
    // 홀/포장 메뉴가 아예 없는 매장은 배달 메뉴를 온전히 사용하되:
    // 키워드가 주어진 경우 배달 메뉴 자체의 '대표메뉴' 그룹명보다 사장님 등록 키워드 연관 메뉴를 최상단으로 우선!
    const hasAnyKwMatch = tier3.some((d) => d.isKeywordMatch);
    if (hasAnyKwMatch) {
      tier3.forEach((d) => {
        if (!d.isKeywordMatch) d.isPopular = false;
      });
    }
    tier3.sort((a, b) => {
      if (a.isKeywordExact && !b.isKeywordExact) return -1;
      if (!a.isKeywordExact && b.isKeywordExact) return 1;
      if (a.isKeywordMatch && !b.isKeywordMatch) return -1;
      if (!a.isKeywordMatch && b.isKeywordMatch) return 1;
      return sortFn(a, b);
    });
    merged = tier3;
  }

  return merged.map(({ isKeywordMatch, isKeywordExact, order, ...rest }) => rest);
}

// 홈 페이지: 매장 기본정보·대표 키워드·좌석·편의시설·방문자가 고른 키워드·이 매장에 연결된 블로그 후기
export function parsePlaceHome(html, expectId) {
  const state = parseApolloState(html);
  if (!state) return null;
  const baseKey = Object.keys(state).find((k) => k.startsWith('PlaceDetailBase:') && (!expectId || k === `PlaceDetailBase:${expectId}`));
  const base = baseKey ? state[baseKey] : null;
  if (!base) return null;

  const km = String(html).match(/"keywordList":\[(.*?)\]/);
  let keywords = [];
  if (km) {
    try {
      keywords = JSON.parse(`[${km[1]}]`).filter((x) => typeof x === 'string' && x.trim().length >= 2).slice(0, 8);
    } catch (e) {
      keywords = [];
    }
  }

  const seats = [];
  const reviews = [];
  let voted = [];
  for (const [key, val] of Object.entries(state)) {
    if (!val || typeof val !== 'object') continue;
    const t = typeOf(key, val);
    if (t === 'RestaurantSeatItems' && val.name) {
      seats.push(`${cleanHtml(val.name)}${val.description ? ` ${cleanHtml(val.description)}` : ''}`);
    } else if (t === 'FsasReview' && val.type === 'blog' && val.title) {
      reviews.push({ title: cleanHtml(val.title), contents: cleanHtml(val.contents || ''), url: val.url || '' });
    } else if (t === 'VisitorReviewStatsResult') {
      const details = val.analysis && val.analysis.votedKeyword && val.analysis.votedKeyword.details;
      if (Array.isArray(details)) {
        voted = details
          .filter((d) => d && d.displayName && d.count)
          .map((d) => ({ keyword: cleanHtml(d.displayName), count: Number(d.count) }))
          .sort((a, b) => b.count - a.count)
          .slice(0, 5);
      }
    }
  }
  const conveniences = Array.isArray(base.conveniences) ? base.conveniences.map(cleanHtml).filter(Boolean) : [];
  return {
    placeId: String(base.id || expectId || ''),
    name: cleanHtml(base.name),
    category: cleanHtml(base.category),
    roadAddress: cleanHtml(base.roadAddress),
    address: cleanHtml(base.address),
    conveniences,
    keywords,
    seats: seats.slice(0, 4),
    voted,
    reviews: reviews.slice(0, 5),
    visitorReviewsTotal: Number(base.visitorReviewsTotal) || 0,
  };
}

// '공식 매장 특징·소개' 한 덩어리 글 (플레이스에 실제로 적힌 항목만 이어 붙인다. 없는 항목은 건너뜀)
export function buildOfficialSummary(home) {
  if (!home) return '';
  const parts = [];
  if (home.category) parts.push(home.category.replace(/,/g, '·'));
  if (home.keywords.length) parts.push(`대표 키워드: ${home.keywords.slice(0, 5).join(', ')}`);
  if (home.seats.length) parts.push(`좌석: ${home.seats.join(', ')}`);
  if (home.conveniences.length) parts.push(`편의시설: ${home.conveniences.slice(0, 6).join(', ')}`);
  if (home.voted.length) parts.push(`방문자가 꼽은 점: ${home.voted.slice(0, 3).map((v) => `${v.keyword}(${v.count})`).join(', ')}`);
  return parts.join(' / ');
}

// ---------------------------------------------------------------- 4. 정확한 placeId 찾기 (상호명 + 주소로 동명이인 지점 걸러내기)
export async function findPlaceId(storeName, address) {
  const tokens = regionTokens(address);
  const query = [storeName, ...tokens.filter((t) => !String(storeName).includes(t)).slice(0, 2)].join(' ').replace(/\s+/g, ' ').trim();
  let html = '';
  try {
    html = await getText(`https://m.search.naver.com/search.naver?query=${encodeURIComponent(query)}`, { 'User-Agent': UA, 'Accept-Language': 'ko-KR,ko;q=0.9' });
  } catch (e) {
    console.warn('place search error:', e.message);
    return '';
  }
  const ids = [];
  for (const m of html.matchAll(PLACE_LINK)) {
    if (!ids.includes(m[1])) ids.push(m[1]);
    if (ids.length >= 4) break;
  }
  if (ids.length === 0) return '';
  if (!address) return ids[0];                                    // 주소를 모르면 검색 1순위

  const homes = await Promise.all(ids.map((id) => fetchPlaceHome(id)));
  let best = { id: '', score: 0 };
  homes.forEach((h, i) => {
    if (!h) return;
    const a = Math.max(addressScore(h.roadAddress, address), addressScore(h.address, address));
    const score = a * 2 + nameScore(h.name, storeName);
    if (score > best.score) best = { id: ids[i], score };
  });
  return best.score >= 1.2 ? best.id : '';                        // 주소가 맞는 곳이 없으면 엉뚱한 지점보다 '못 찾음'이 낫다
}

export async function fetchPlaceHome(placeId) {
  if (!/^\d{6,}$/.test(String(placeId || ''))) return null;
  try {
    const html = await getText(`https://m.place.naver.com/place/${placeId}/home`, { 'User-Agent': UA, 'Accept-Language': 'ko-KR,ko;q=0.9' }, 8000);
    return parsePlaceHome(html, String(placeId));
  } catch (e) {
    console.warn('place home error:', e.message);
    return null;
  }
}

export async function fetchPlaceMenus(placeId, keywords = []) {
  if (!/^\d{6,}$/.test(String(placeId || ''))) return [];
  try {
    const html = await getText(`https://m.place.naver.com/place/${placeId}/menu/list`, { 'User-Agent': UA, 'Accept-Language': 'ko-KR,ko;q=0.9' }, 8000);
    return parseMenus(parseApolloState(html), keywords);
  } catch (e) {
    console.warn('place menu error:', e.message);
    return [];
  }
}
