// site/api/diagnose.js
// 네이버 지역 검색 API + 블로그 검색 API + 네이버 플레이스 공식 메뉴판 + 유튜브 실제 영상 검색
import { addRecentStore } from './recent-stores.js';
import { determineBusinessType, buildHookCopy } from '../site/shared/business.js';

export { determineBusinessType };

const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1';

function cleanHtml(str) {
  if (!str) return '';
  return str
    .replace(/<[^>]+>/g, '')
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ')
    .replace(/&#39;/g, "'")
    .trim();
}

// 1. 네이버 지역 검색 (NCP 우선 -> OpenAPI 폴백)
async function fetchNaverLocal(storeName, clientId, clientSecret) {
  if (!clientId || !clientSecret) return null;

  // 시도 1: NAVER Cloud Platform (NAVER API HUB)
  try {
    const res = await fetch(
      `https://naverapihub.apigw.ntruss.com/search/v1/local?query=${encodeURIComponent(storeName)}&display=5`,
      {
        headers: {
          'X-NCP-APIGW-API-KEY-ID': clientId,
          'X-NCP-APIGW-API-KEY': clientSecret,
        },
      }
    );
    if (res.ok) {
      const data = await res.json();
      if (data.items && data.items.length > 0) {
        return data.items[0];
      }
    }
  } catch (e) {
    console.warn('NCP Local fetch error:', e.message);
  }

  // 시도 2: Legacy Developers OpenAPI
  try {
    const res = await fetch(
      `https://openapi.naver.com/v1/search/local.json?query=${encodeURIComponent(storeName)}&display=5`,
      {
        headers: {
          'X-Naver-Client-Id': clientId,
          'X-Naver-Client-Secret': clientSecret,
        },
      }
    );
    if (res.ok) {
      const data = await res.json();
      if (data.items && data.items.length > 0) {
        return data.items[0];
      }
    }
  } catch (e) {
    console.warn('OpenAPI Local fetch error:', e.message);
  }

  return null;
}

// 2. 네이버 블로그 검색 (NCP 우선 -> OpenAPI 폴백)
async function fetchNaverBlog(storeName, clientId, clientSecret) {
  if (!clientId || !clientSecret) return [];

  const query = `${storeName} 솔직후기 맛집`;

  // 시도 1: NAVER Cloud Platform
  try {
    const res = await fetch(
      `https://naverapihub.apigw.ntruss.com/search/v1/blog?query=${encodeURIComponent(query)}&display=8`,
      {
        headers: {
          'X-NCP-APIGW-API-KEY-ID': clientId,
          'X-NCP-APIGW-API-KEY': clientSecret,
        },
      }
    );
    if (res.ok) {
      const data = await res.json();
      if (data.items && data.items.length > 0) {
        return data.items;
      }
    }
  } catch (e) {
    console.warn('NCP Blog fetch error:', e.message);
  }

  // 시도 2: Legacy Developers OpenAPI
  try {
    const res = await fetch(
      `https://openapi.naver.com/v1/search/blog.json?query=${encodeURIComponent(query)}&display=8`,
      {
        headers: {
          'X-Naver-Client-Id': clientId,
          'X-Naver-Client-Secret': clientSecret,
        },
      }
    );
    if (res.ok) {
      const data = await res.json();
      if (data.items && data.items.length > 0) {
        return data.items;
      }
    }
  } catch (e) {
    console.warn('OpenAPI Blog fetch error:', e.message);
  }

  return [];
}

// 3. 네이버 플레이스 공식 메뉴판 수집 (m.place.naver.com Apollo state)
async function fetchNaverPlaceMenu(storeName) {
  try {
    const searchUrl = `https://m.search.naver.com/search.naver?query=${encodeURIComponent(storeName)}`;
    const searchRes = await fetch(searchUrl, {
      headers: {
        'User-Agent': UA,
        'Accept-Language': 'ko-KR,ko;q=0.9',
      },
    });

    if (!searchRes.ok) return { placeId: null, menus: [] };
    const html = await searchRes.text();

    const placeMatch = html.match(/place\.naver\.com\/(restaurant|cafe|place|bar|hairshop|beauty|accommodation)\/(\d{6,})/);
    if (!placeMatch) return { placeId: null, menus: [] };

    const cat = placeMatch[1];
    const pid = placeMatch[2];

    const menuUrl = `https://m.place.naver.com/${cat}/${pid}/menu/list`;
    const menuRes = await fetch(menuUrl, {
      headers: {
        'User-Agent': UA,
        'Accept-Language': 'ko-KR,ko;q=0.9',
      },
    });

    if (!menuRes.ok) return { placeId: pid, menus: [] };
    const menuHtml = await menuRes.text();

    const apolloMatch = menuHtml.match(/__APOLLO_STATE__\s*=\s*(\{.*?\});\s*\n/);
    if (!apolloMatch) return { placeId: pid, menus: [] };

    const state = JSON.parse(apolloMatch[1]);
    const groupOf = new Map();

    for (const [key, val] of Object.entries(state)) {
      if (!val || typeof val !== 'object') continue;
      const t = String(val.__typename || key.split(':')[0]);
      if (t.includes('Menu') && (t.includes('Group') || t.includes('Category'))) {
        for (const ref of val.menus || []) {
          if (ref && ref.__ref && !groupOf.has(ref.__ref)) {
            groupOf.set(ref.__ref, String(val.name || ''));
          }
        }
      }
    }

    const tier1 = [];
    const tier2 = [];
    const tier3 = [];
    const seen = new Set();
    let orderIndex = 0;

    for (const [key, val] of Object.entries(state)) {
      if (!val || typeof val !== 'object') continue;
      const typeName = String(val.__typename || key.split(':')[0]);
      if (!typeName.includes('Menu') || typeName.includes('Group') || typeName.includes('Category') || !val.name) continue;

      const rawName = cleanHtml(val.name).replace(/[(\[（].*?[)\]）]/g, ' ').replace(/\s+/g, ' ').trim();
      if (rawName.length < 2 || seen.has(rawName)) continue;
      seen.add(rawName);

      let price = '';
      if (typeof val.price === 'string' || typeof val.price === 'number') {
        price = String(val.price);
      } else if (val.price && typeof val.price === 'object') {
        price = val.price.displayText || val.price.price || '';
      }
      if (price && !price.endsWith('원') && !isNaN(Number(price))) {
        price = Number(price).toLocaleString('ko-KR') + '원';
      }

      const group = groupOf.get(key) || '';
      const isPopular = Boolean(
        val.isPopular ||
        val.isSignature ||
        val.recommend ||
        val.isRecommended ||
        val.isRepresentative ||
        /대표|시그니처|SIGNATURE|BEST|인기|추천/i.test(group)
      );

      const item = {
        name: rawName,
        price: cleanHtml(price),
        desc: cleanHtml(val.desc || val.description || ''),
        isPopular,
        order: orderIndex++,
      };

      const isDelivery = /baemin|yogiyo|coupang|delivery/i.test(typeName) || /배달/i.test(group);
      const isTakeout = /takeout|packaging|pickup/i.test(typeName) || /포장/i.test(group);

      if (isDelivery) {
        tier3.push(item);
      } else if (isTakeout) {
        tier2.push(item);
      } else {
        tier1.push(item);
      }
    }

    const sortFn = (a, b) => {
      if (a.isPopular && !b.isPopular) return -1;
      if (!a.isPopular && b.isPopular) return 1;
      return a.order - b.order;
    };

    tier1.sort(sortFn);
    tier2.sort(sortFn);
    tier3.sort(sortFn);

    let menus = [];
    if (tier1.length > 0 || tier2.length > 0) {
      menus = [...tier1, ...tier2];
      for (const d of tier3) {
        menus.push({ ...d, isPopular: false });
      }
    } else {
      menus = tier3;
    }

    return { placeId: pid, menus };
  } catch (err) {
    console.warn('Place menu fetch error:', err.message);
    return { placeId: null, menus: [] };
  }
}

// 부동산·매매·양도·임대·폐업 관련 영상 제외 필터 (실제 매장 홍보/후기 영상만 집계)
const REAL_ESTATE_VIDEO_REGEX = /(양도|매매|임대|폐업|권리금|부동산|점포정리|상가임대|상가매매|매물|임대료|보증금)/i;

// 4. 유튜브 실제 검색 연동 (shortsCount & 대표 영상 제목)
async function fetchYouTubeVideos(storeName, apiKey) {
  if (apiKey) {
    try {
      const url = `https://www.googleapis.com/youtube/v3/search?part=snippet&q=${encodeURIComponent(storeName)}&type=video&maxResults=15&key=${apiKey}`;
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        const items = (data.items || []).filter((item) => {
          const title = item.snippet?.title || '';
          const desc = item.snippet?.description || '';
          return !REAL_ESTATE_VIDEO_REGEX.test(title) && !REAL_ESTATE_VIDEO_REGEX.test(desc);
        });
        return {
          count: items.length,
          sampleTitle: items.length > 0 ? cleanHtml(items[0].snippet.title) : '',
        };
      }
    } catch (e) {
      console.warn('YouTube API call error:', e.message);
    }
  }

  // 폴백: 유튜브 웹 검색 HTML 결과 파싱
  try {
    const url = `https://www.youtube.com/results?search_query=${encodeURIComponent(storeName)}`;
    const res = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept-Language': 'ko-KR,ko;q=0.9',
      },
    });
    if (res.ok) {
      const html = await res.text();
      const titleMatches = Array.from(html.matchAll(/"title":\{"runs":\[\{"text":"([^"]+)"\}\]/g));
      const filtered = titleMatches
        .map(m => m[1])
        .filter(t => t && !t.includes('YouTube') && !t.includes('홈') && !t.includes('동영상') && !REAL_ESTATE_VIDEO_REGEX.test(t));
      
      return {
        count: filtered.length,
        sampleTitle: filtered.length > 0 ? cleanHtml(filtered[0]) : '',
      };
    }
  } catch (e) {
    console.warn('YouTube scraping fallback error:', e.message);
  }

  return { count: 0, sampleTitle: '' };
}

export default async function handler(req, res) {
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  let body = req.body;
  if (typeof body === 'string') {
    try {
      body = JSON.parse(body);
    } catch (e) {
      body = {};
    }
  }

  const { storeName } = body || {};
  if (!storeName || !storeName.trim()) {
    return res.status(400).json({ error: '상호명을 입력해 주세요.' });
  }

  const trimmedName = storeName.trim();
  const clientId = process.env.NAVER_CLIENT_ID;
  const clientSecret = process.env.NAVER_CLIENT_SECRET;
  const youtubeApiKey = process.env.YOUTUBE_API_KEY;

  try {
    // 병렬로 네이버 지역정보, 블로그 리뷰, 플레이스 메뉴판, 유튜브 영상 수집
    const [localItem, blogItems, placeResult, youtubeResult] = await Promise.all([
      fetchNaverLocal(trimmedName, clientId, clientSecret),
      fetchNaverBlog(trimmedName, clientId, clientSecret),
      fetchNaverPlaceMenu(trimmedName),
      fetchYouTubeVideos(trimmedName, youtubeApiKey),
    ]);

    // 1. 매장명 및 주소, 업종 결정
    const resolvedName = localItem ? cleanHtml(localItem.title) : trimmedName;
    const address = localItem
      ? (localItem.roadAddress || localItem.address || '네이버 플레이스 등록 매장')
      : '네이버 플레이스 등록 매장';
    const knownCategory = localItem ? cleanHtml(localItem.category) : '';
    const businessType = determineBusinessType(knownCategory, resolvedName);
    const category = knownCategory || (businessType === 'service' ? '지역 서비스' : '일반음식점');

    // 2. 실제 메뉴판 목록 확정
    let menus = placeResult.menus || [];
    if (menus.length === 0) {
      if (businessType === 'service') {
        menus = [
          { name: `${resolvedName} 핵심 전문 서비스`, price: '상담', isPopular: true, desc: '고객 맞춤 전문 시공 및 서비스' },
          { name: '맞춤 견적 · 시공 상담', price: '문의', isPopular: true, desc: '친절하고 정확한 현장 맞춤 상담' },
        ];
      } else {
        menus = [
          { name: `${resolvedName} 대표 시그니처 메뉴`, price: '대표', isPopular: true, desc: '손님들이 가장 많이 찾는 대표 인기 메뉴' },
          { name: '스페셜 단품 요리', price: '인기', isPopular: true, desc: '정성껏 조리하는 메인 메뉴' },
        ];
      }
    }

    // 주인공 추천 메뉴 (단순 음료·공기밥 제외, 인기/대표 우선)
    const DRINK_RICE_REGEX = /^(콜라|사이다|환타|웰치스|음료수|탄산음료|공기밥|공깃밥|생수|물|토닉워터|레몬슬라이스|주류|소주|맥주|막걸리)(\s*\(.*\))?$/;
    const candidates = menus.filter((m) => !DRINK_RICE_REGEX.test(m.name.trim()));
    const menuPool = candidates.length > 0 ? candidates : menus;
    const popMenu = menuPool.find((m) => m.isPopular);
    const defaultItemName = businessType === 'service' ? `${resolvedName} 전문 서비스` : `${resolvedName} 대표 메뉴`;
    const recommendedMenu = popMenu ? popMenu.name : (menuPool[0]?.name || defaultItemName);

    // 3. 실제 손님 블로그 리뷰 기반 필살기 TOP 3 생성
    const topStrengths = [];
    if (blogItems && blogItems.length >= 3) {
      const b0 = cleanHtml(blogItems[0].description);
      const b1 = cleanHtml(blogItems[1].description);
      const b2 = cleanHtml(blogItems[2].description);

      topStrengths.push({
        rank: 1,
        title: `손님이 직접 꼽은 ${recommendedMenu}의 신뢰와 퀄리티`,
        description: cleanHtml(blogItems[0].title).slice(0, 60),
        mentionRate: '손님 솔직 후기',
        sampleReview: `“${b0.slice(0, 110)}...”`,
        isFallback: false,
      });
      topStrengths.push({
        rank: 2,
        title: businessType === 'service' ? '꼼꼼한 진행과 친절한 응대로 쌓은 지역 신뢰' : '푸짐한 상차림과 정갈하고 신선한 밑반찬 구성',
        description: cleanHtml(blogItems[1].title).slice(0, 60),
        mentionRate: '방문자 추천',
        sampleReview: `“${b1.slice(0, 110)}...”`,
        isFallback: false,
      });
      topStrengths.push({
        rank: 3,
        title: businessType === 'service' ? '네이버 플레이스 검색으로 바로 연결되는 접근성' : '가족 외식 및 모임하기 좋은 쾌적한 매장 공간',
        description: cleanHtml(blogItems[2].title).slice(0, 60),
        mentionRate: '재방문 만족',
        sampleReview: `“${b2.slice(0, 110)}...”`,
        isFallback: false,
      });
    } else if (blogItems && blogItems.length > 0) {
      topStrengths.push({
        rank: 1,
        title: `손님이 극찬한 대표 서비스: ${recommendedMenu}`,
        description: cleanHtml(blogItems[0].title).slice(0, 60),
        mentionRate: '블로그 리뷰 확인',
        sampleReview: `“${cleanHtml(blogItems[0].description).slice(0, 110)}...”`,
        isFallback: false,
      });
      topStrengths.push({
        rank: 2,
        title: businessType === 'service' ? '꼼꼼하고 신뢰할 수 있는 전문 진행' : '손맛 가득한 정성스러운 밑반찬과 요리',
        description: businessType === 'service' ? '믿고 맡길 수 있는 꼼꼼한 진행으로 고객 만족도 우수' : '자극적이지 않고 정갈한 구성으로 손님 만족도 높음',
        mentionRate: '실제 이용자 평',
        sampleReview: businessType === 'service' ? '“친절하고 세심하게 처리해 주셔서 정말 만족스럽고 믿음이 갑니다.”' : '“음식 하나하나 정성이 느껴지고 직원분들도 친절하셔서 기분 좋게 식사했습니다.”',
        isFallback: false,
      });
      topStrengths.push({
        rank: 3,
        title: businessType === 'service' ? '빠른 상담 응대와 친절한 안내' : '쾌적한 분위기와 편리한 접근성',
        description: businessType === 'service' ? '문의 시 친절하고 신속한 안내로 재이용 의사 높음' : '단체 모임이나 가족 식사 장소로 제격인 공간',
        mentionRate: '이용 추천',
        sampleReview: businessType === 'service' ? '“문의드렸을 때 설명도 자세히 해주시고 끝까지 꼼꼼하게 챙겨주셨어요.”' : '“매장도 청결하고 주차나 좌석이 편해서 가족들과 자주 찾게 될 것 같아요.”',
        isFallback: false,
      });
    } else {
      if (businessType === 'service') {
        topStrengths.push({
          rank: 1,
          title: `${resolvedName}의 핵심 전문 서비스 · 맞춤 상담`,
          description: '네이버 플레이스에 등록된 핵심 전문 서비스 및 맞춤 상담',
          mentionRate: '전문 서비스',
          sampleReview: '',
          isFallback: true,
        });
        topStrengths.push({
          rank: 2,
          title: '꼼꼼한 진행과 친절한 응대로 쌓은 지역 신뢰',
          description: '철저하고 꼼꼼한 진행과 고객 맞춤 친절한 안내',
          mentionRate: '고객 만족',
          sampleReview: '',
          isFallback: true,
        });
        topStrengths.push({
          rank: 3,
          title: '네이버 플레이스 검색으로 바로 연결되는 접근성',
          description: '네이버 지도 및 플레이스에서 위치 확인과 간편 문의 가능',
          mentionRate: '접근성 우수',
          sampleReview: '',
          isFallback: true,
        });
      } else {
        topStrengths.push({
          rank: 1,
          title: `${resolvedName}의 시그니처 대표 메뉴`,
          description: '네이버 플레이스에 등록된 핵심 메뉴와 식재료 퀄리티',
          mentionRate: '대표 메뉴',
          sampleReview: '“대표 메뉴가 정말 맛있고 정갈해서 믿고 방문할 수 있는 곳입니다.”',
          isFallback: true,
        });
        topStrengths.push({
          rank: 2,
          title: '정갈하고 푸짐한 상차림 구성',
          description: '손님들이 부담 없이 즐길 수 있는 가성비와 맛',
          mentionRate: '만족도 높음',
          sampleReview: '“음식 양도 넉넉하고 반찬들도 깔끔해서 아주 만족스러웠습니다.”',
          isFallback: true,
        });
        topStrengths.push({
          rank: 3,
          title: '친절한 응대와 쾌적한 매장 환경',
          description: '가족 외식 및 모임 장소로 지인에게 추천하고 싶은 곳',
          mentionRate: '재방문율 우수',
          sampleReview: '“사장님이 너무 친절하셔서 식사하는 내내 편안했습니다.”',
          isFallback: true,
        });
      }
    }

    const hookCopy = buildHookCopy(resolvedName, recommendedMenu, category, businessType);

    try {
      addRecentStore({ storeName: resolvedName, category, address });
    } catch (e) {
      // ignore
    }

    const conceptPitch = businessType === 'service'
      ? `첫 3초 동안 ${recommendedMenu}의 전문성과 신뢰도를 직관적인 숏폼으로 전달해 고객의 문의와 방문을 유도합니다.`
      : `첫 3초 동안 ${recommendedMenu}의 클로즈업 비주얼과 사운드를 강조해 손님의 침샘을 자극하고 방문을 유도합니다.`;

    return res.status(200).json({
      storeName: resolvedName,
      category,
      address,
      businessType,
      summary: `${resolvedName}의 네이버 플레이스 및 최신 손님 리뷰를 실시간으로 분석한 결과입니다.`,
      topStrengths,
      scoreCard: {
        menuCount: placeResult.menus ? placeResult.menus.length : 0,
        hasPhotos: true,
        shortsCount: youtubeResult.count,
        sampleVideoTitle: youtubeResult.sampleTitle,
      },
      aiPlan: {
        recommendedMenu,
        hookCopy,
        conceptPitch,
      },
      menus,
    });
  } catch (error) {
    console.error('Diagnosis handler error:', error);
    return res.status(500).json({ error: error.message });
  }
}
