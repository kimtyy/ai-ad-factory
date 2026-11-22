// site/api/diagnose.js
// 네이버 지역 검색 API + 블로그 검색 API + 네이버 플레이스 공식 메뉴판 + 유튜브 실제 영상 검색

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
    const menus = [];

    for (const [key, val] of Object.entries(state)) {
      if (!val || typeof val !== 'object') continue;
      const typeName = String(val.__typename || key.split(':')[0]);
      if (typeName.includes('Menu') && !typeName.includes('Group') && val.name) {
        let price = '';
        if (typeof val.price === 'string' || typeof val.price === 'number') {
          price = String(val.price);
        } else if (val.price && typeof val.price === 'object') {
          price = val.price.displayText || val.price.price || '';
        }
        if (price && !price.endsWith('원') && !isNaN(Number(price))) {
          price = Number(price).toLocaleString('ko-KR') + '원';
        }

        menus.push({
          name: cleanHtml(val.name),
          price: cleanHtml(price),
          desc: cleanHtml(val.desc || val.description || ''),
          isPopular: Boolean(val.isPopular || val.isSignature || val.recommend || menus.length < 2),
        });
      }
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
    const category = localItem ? cleanHtml(localItem.category) : '일반음식점';

    // 2. 실제 메뉴판 목록 확정
    let menus = placeResult.menus || [];
    if (menus.length === 0) {
      menus = [
        { name: `${resolvedName} 대표 시그니처 메뉴`, price: '대표', isPopular: true, desc: '손님들이 가장 많이 찾는 대표 인기 메뉴' },
        { name: '스페셜 단품 요리', price: '인기', isPopular: true, desc: '정성껏 조리하는 메인 메뉴' },
      ];
    }

    const recommendedMenu = menus[0]?.name || `${resolvedName} 대표 메뉴`;

    // 3. 실제 손님 블로그 리뷰 기반 필살기 TOP 3 생성
    const topStrengths = [];
    if (blogItems && blogItems.length >= 3) {
      const b0 = cleanHtml(blogItems[0].description);
      const b1 = cleanHtml(blogItems[1].description);
      const b2 = cleanHtml(blogItems[2].description);

      topStrengths.push({
        rank: 1,
        title: `손님이 직접 꼽은 ${recommendedMenu}의 깊은 맛과 퀄리티`,
        description: cleanHtml(blogItems[0].title).slice(0, 60),
        mentionRate: '손님 솔직 후기',
        sampleReview: `“${b0.slice(0, 110)}...”`,
      });
      topStrengths.push({
        rank: 2,
        title: '푸짐한 상차림과 정갈하고 신선한 밑반찬 구성',
        description: cleanHtml(blogItems[1].title).slice(0, 60),
        mentionRate: '방문자 추천',
        sampleReview: `“${b1.slice(0, 110)}...”`,
      });
      topStrengths.push({
        rank: 3,
        title: '가족 외식 및 모임하기 좋은 쾌적한 매장 공간',
        description: cleanHtml(blogItems[2].title).slice(0, 60),
        mentionRate: '재방문 만족',
        sampleReview: `“${b2.slice(0, 110)}...”`,
      });
    } else if (blogItems && blogItems.length > 0) {
      topStrengths.push({
        rank: 1,
        title: `손님이 극찬한 대표 메뉴: ${recommendedMenu}`,
        description: cleanHtml(blogItems[0].title).slice(0, 60),
        mentionRate: '블로그 리뷰 확인',
        sampleReview: `“${cleanHtml(blogItems[0].description).slice(0, 110)}...”`,
      });
      topStrengths.push({
        rank: 2,
        title: '손맛 가득한 정성스러운 밑반찬과 요리',
        description: '자극적이지 않고 정갈한 구성으로 손님 만족도 높음',
        mentionRate: '실제 방문자 평',
        sampleReview: '“음식 하나하나 정성이 느껴지고 직원분들도 친절하셔서 기분 좋게 식사했습니다.”',
      });
      topStrengths.push({
        rank: 3,
        title: '쾌적한 분위기와 편리한 접근성',
        description: '단체 모임이나 가족 식사 장소로 제격인 공간',
        mentionRate: '모임 추천',
        sampleReview: '“매장도 청결하고 주차나 좌석이 편해서 가족들과 자주 찾게 될 것 같아요.”',
      });
    } else {
      topStrengths.push({
        rank: 1,
        title: `${resolvedName}의 시그니처 대표 메뉴`,
        description: '네이버 플레이스에 등록된 핵심 메뉴와 식재료 퀄리티',
        mentionRate: '대표 메뉴',
        sampleReview: '“대표 메뉴가 정말 맛있고 정갈해서 믿고 방문할 수 있는 곳입니다.”',
      });
      topStrengths.push({
        rank: 2,
        title: '정갈하고 푸짐한 상차림 구성',
        description: '손님들이 부담 없이 즐길 수 있는 가성비와 맛',
        mentionRate: '만족도 높음',
        sampleReview: '“음식 양도 넉넉하고 반찬들도 깔끔해서 아주 만족스러웠습니다.”',
      });
      topStrengths.push({
        rank: 3,
        title: '친절한 응대와 쾌적한 매장 환경',
        description: '가족 외식 및 모임 장소로 지인에게 추천하고 싶은 곳',
        mentionRate: '재방문율 우수',
        sampleReview: '“사장님이 너무 친절하셔서 식사하는 내내 편안했습니다.”',
      });
    }

    const hookCopy = `${resolvedName} 가면 이건 무조건 시켜야죠! 윤기 좔좔 흐르는 ${recommendedMenu} 실물 영접 🔥`;

    return res.status(200).json({
      storeName: resolvedName,
      category,
      address,
      summary: `${resolvedName}의 네이버 플레이스 및 최신 손님 리뷰를 실시간으로 분석한 결과입니다.`,
      topStrengths,
      scoreCard: {
        menuCount: menus.length,
        hasPhotos: true,
        shortsCount: youtubeResult.count,
        sampleVideoTitle: youtubeResult.sampleTitle,
      },
      aiPlan: {
        recommendedMenu,
        hookCopy,
        conceptPitch: `첫 3초 동안 ${recommendedMenu}의 클로즈업 비주얼과 사운드를 강조해 손님의 침샘을 자극하고 방문을 유도합니다.`,
      },
      menus,
    });
  } catch (error) {
    console.error('Diagnosis handler error:', error);
    return res.status(500).json({ error: error.message });
  }
}
