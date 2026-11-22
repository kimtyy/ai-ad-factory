// site/api/diagnose.js
// 매장 진단: 네이버 플레이스 공식정보(placeId 기준) + 지역 검색 + 블로그 검색 + 유튜브 실제 영상 검색
// POST {storeName, address?, placeId?}  또는  GET ?store=&address=&place_id=   (작업실 app_store.py 와 같은 결과를 받는다)
import {
  cleanHtml, searchLocal, addressScore, nameScore, regionTokens,
  findPlaceId, fetchPlaceHome, fetchPlaceMenus, buildOfficialSummary,
} from '../server/naver.js';

// 2. 네이버 블로그 검색 (NCP 우선 -> OpenAPI 폴백). 같은 이름의 다른 지점 글이 섞이지 않도록 상호·지역 키워드로 걸러낸다
function brandToken(storeName) {
  const w = String(storeName || '').split(/\s+/).find((x) => x.replace(/[^가-힣A-Za-z0-9]/g, '').length >= 2) || '';
  return w.replace(/[^가-힣A-Za-z0-9]/g, '');
}

function filterBlogsForStore(items, storeName, address) {
  const brand = brandToken(storeName);
  const tokens = regionTokens(address);
  return (items || []).filter((it) => {
    const text = `${cleanHtml(it.title)} ${cleanHtml(it.description)}`.replace(/\s/g, '');
    if (brand && !text.includes(brand)) return false;
    if (tokens.length > 0 && !tokens.some((t) => text.includes(t))) return false;      // 다른 지역 지점의 글 제외
    return true;
  });
}

async function fetchNaverBlog(storeName, address, clientId, clientSecret) {
  if (!clientId || !clientSecret) return [];

  const region = regionTokens(address)[0] || '';
  const query = `${storeName} ${region} 솔직후기`.replace(/\s+/g, ' ').trim();
  const q = encodeURIComponent(query);
  const tries = [
    [`https://naverapihub.apigw.ntruss.com/search/v1/blog?query=${q}&display=15`,
      { 'X-NCP-APIGW-API-KEY-ID': clientId, 'X-NCP-APIGW-API-KEY': clientSecret }],
    [`https://openapi.naver.com/v1/search/blog.json?query=${q}&display=15`,
      { 'X-Naver-Client-Id': clientId, 'X-Naver-Client-Secret': clientSecret }],
  ];
  for (const [url, headers] of tries) {
    try {
      const res = await fetch(url, { headers, signal: AbortSignal.timeout(6000) });
      if (!res.ok) continue;
      const data = await res.json();
      if (data.items && data.items.length > 0) return filterBlogsForStore(data.items, storeName, address);
    } catch (e) {
      console.warn('Blog fetch error:', e.message);
    }
  }
  return [];
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

function snippet(text, n) {
  const t = cleanHtml(text);
  return t.length > n ? `${t.slice(0, n)}...` : t;
}

// 실제 데이터(플레이스 방문자 선택 키워드·이 매장에 연결된 후기·지점 필터를 통과한 블로그 후기)로 필살기 TOP 3를 만든다.
// 근거가 하나도 없을 때만 일반 문구를 쓰고 isFallback 으로 표시한다 (작업실 앱은 그 항목을 쓰지 않는다).
function buildStrengths({ resolvedName, home, blogItems }) {
  const reviews = [];
  for (const r of (home && home.reviews) || []) reviews.push({ title: r.title, text: r.contents });
  for (const b of blogItems || []) reviews.push({ title: cleanHtml(b.title), text: cleanHtml(b.description) });
  const voted = (home && home.voted) || [];

  const out = [];
  for (let i = 0; i < 3; i += 1) {
    const v = voted[i];
    const r = reviews[i];
    if (v) {
      out.push({
        rank: i + 1,
        title: `방문자들이 꼽은 "${v.keyword}"`,
        description: r ? snippet(r.title, 60) : '네이버 플레이스 방문자 리뷰 키워드',
        mentionRate: `${v.count}명 선택`,
        sampleReview: r ? `“${snippet(r.text, 110)}”` : '',
        isFallback: false,
      });
    } else if (r) {
      out.push({
        rank: i + 1,
        title: `손님 후기: ${snippet(r.title, 36)}`,
        description: snippet(r.title, 60),
        mentionRate: '손님 솔직 후기',
        sampleReview: `“${snippet(r.text, 110)}”`,
        isFallback: false,
      });
    }
  }
  const real = out.length;
  const generic = [
    { title: `${resolvedName}의 시그니처 대표 메뉴`, description: '네이버 플레이스에 등록된 핵심 메뉴', mentionRate: '대표 메뉴', sampleReview: '' },
    { title: '정갈하고 푸짐한 상차림 구성', description: '손님들이 부담 없이 즐길 수 있는 구성', mentionRate: '확인 필요', sampleReview: '' },
    { title: '친절한 응대와 쾌적한 매장 환경', description: '가족 외식 및 모임 장소로 추천', mentionRate: '확인 필요', sampleReview: '' },
  ];
  for (let i = real; i < 3; i += 1) out.push({ rank: i + 1, ...generic[i], isFallback: true });

  const source = real === 0 ? 'fallback' : (voted.length > 0 ? 'official' : 'blog');
  return { strengths: out, source };
}

export default async function handler(req, res) {
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }
  if (req.method !== 'POST' && req.method !== 'GET') {
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  let input = {};
  if (req.method === 'GET') {
    const q = req.query || {};
    input = { storeName: q.store || q.storeName, address: q.address, placeId: q.place_id || q.placeId };
  } else {
    let body = req.body;
    if (typeof body === 'string') {
      try {
        body = JSON.parse(body);
      } catch (e) {
        body = {};
      }
    }
    input = body || {};
  }

  const trimmedName = String(input.storeName || '').trim().slice(0, 60);
  if (!trimmedName) {
    return res.status(400).json({ error: '상호명을 입력해 주세요.' });
  }
  const givenAddress = String(input.address || '').trim().slice(0, 120);
  let placeId = /^\d{6,}$/.test(String(input.placeId || '')) ? String(input.placeId) : '';

  const clientId = process.env.NAVER_CLIENT_ID;
  const clientSecret = process.env.NAVER_CLIENT_SECRET;
  const youtubeApiKey = process.env.YOUTUBE_API_KEY;

  try {
    // 1. 정확한 placeId (선택한 지점의 상호+주소로 찾고, 주소가 안 맞는 동명 지점은 버린다)
    if (!placeId) placeId = await findPlaceId(trimmedName, givenAddress);

    // 2. 홈 정보 우선 조회 (홈 정보의 keywords 를 메뉴판 파싱 가중치에 연동)
    const home = placeId ? await fetchPlaceHome(placeId) : null;
    const keywords = (home && home.keywords) || [];

    // 3. 병렬 수집: 플레이스 공식 메뉴판 / 지역 검색 / 블로그 후기(지점 필터) / 유튜브
    const regionHint = regionTokens(givenAddress)[0] || '';
    const [officialMenusRaw, localItems, blogItems, youtubeResult] = await Promise.all([
      placeId ? fetchPlaceMenus(placeId, keywords) : Promise.resolve([]),
      searchLocal(`${trimmedName} ${regionHint}`.trim(), clientId, clientSecret, 5),
      fetchNaverBlog(trimmedName, givenAddress, clientId, clientSecret),
      fetchYouTubeVideos(trimmedName, youtubeApiKey),
    ]);

    // 4. 매장명·주소·업종: 플레이스 공식 > 주소가 맞는 지역 검색 결과 > 입력값
    let localItem = null;
    if (localItems.length > 0) {
      localItem = [...localItems].sort((a, b) => {
        const sa = addressScore(`${a.roadAddress} ${a.address}`, givenAddress) * 2 + nameScore(cleanHtml(a.title), trimmedName);
        const sb = addressScore(`${b.roadAddress} ${b.address}`, givenAddress) * 2 + nameScore(cleanHtml(b.title), trimmedName);
        return sb - sa;
      })[0];
      if (givenAddress && addressScore(`${localItem.roadAddress} ${localItem.address}`, givenAddress) === 0) localItem = null;
    }
    const resolvedName = (home && home.name) || (localItem ? cleanHtml(localItem.title) : trimmedName);
    const address = (home && (home.roadAddress || home.address))
      || (localItem && (localItem.roadAddress || localItem.address))
      || givenAddress
      || '네이버 플레이스 등록 매장';
    const category = (home && home.category) || (localItem ? cleanHtml(localItem.category) : '일반음식점');

    // 5. 메뉴판: 공식 메뉴가 없을 때만 안내용 임시 항목 (menusSource 로 구분)
    let menus = officialMenusRaw || [];

    const menusSource = menus.length > 0 ? 'official' : 'fallback';
    if (menus.length === 0) {
      menus = [
        { name: `${resolvedName} 대표 시그니처 메뉴`, price: '대표', isPopular: true, desc: '손님들이 가장 많이 찾는 대표 인기 메뉴' },
        { name: '스페셜 단품 요리', price: '인기', isPopular: true, desc: '정성껏 조리하는 메인 메뉴' },
      ];
    } else {
      menus = menus.map((m, i) => ({ ...m, isPopular: Boolean(m.isPopular || i < 2) }));
    }

    // 주인공 추천 메뉴 (AI 쇼츠 기획용) 선정
    // 단순 음료·공기밥 등은 주인공 메뉴에서 제외
    const DRINK_RICE_REGEX = /^(콜라|사이다|환타|웰치스|음료수|탄산음료|공기밥|공깃밥|생수|물|토닉워터|레몬슬라이스|주류|소주|맥주|막걸리)(\s*\(.*\))?$/;
    const candidates = menus.filter((m) => !DRINK_RICE_REGEX.test(m.name.trim()));
    const menuPool = candidates.length > 0 ? candidates : menus;

    let recommendedMenu = '';
    // 1순위: 사장님 등록 대표 키워드와 일치/포함되는 공식 메뉴
    const matchedKwMenu = menuPool.find((m) => keywords.some((k) => m.name.includes(k) || k.includes(m.name)));
    if (matchedKwMenu) {
      recommendedMenu = matchedKwMenu.name;
    } else if (keywords.length > 0 && keywords[0]) {
      // 2순위: 배달 메뉴(쌀국수 등)만 있어 키워드 일치 메뉴가 없을 때, 사장님이 스마트플레이스에 직접 등록한 대표 키워드
      recommendedMenu = keywords[0];
    } else {
      // 3순위: 인기/대표 메뉴 또는 첫 번째 메뉴
      const popMenu = menuPool.find((m) => m.isPopular);
      recommendedMenu = popMenu ? popMenu.name : (menuPool[0]?.name || `${resolvedName} 대표 메뉴`);
    }

    const keywordMenu = home && home.keywords.find((k) => menus.some((m) => m.name.includes(k)));
    const topKeyword = keywordMenu || (home && home.keywords[0]) || '';

    // 5. 필살기 TOP 3 + 후기 요약
    const { strengths: topStrengths, source: strengthsSource } = buildStrengths({ resolvedName, home, blogItems });
    const reviewSummary = [
      ...((home && home.reviews) || []).map((r) => `${r.title}: ${snippet(r.contents, 120)}`),
      ...(blogItems || []).map((b) => `${cleanHtml(b.title)}: ${snippet(b.description, 120)}`),
    ].slice(0, 5).join('\n');

    const hookCopy = `${resolvedName} 가면 이건 무조건 시켜야죠! 윤기 좔좔 흐르는 ${recommendedMenu} 실물 영접 🔥`;
    const officialSummary = buildOfficialSummary(home);

    return res.status(200).json({
      storeName: resolvedName,
      category,
      address,
      placeId: placeId || '',
      officialSummary,
      officialKeywords: (home && home.keywords) || [],
      reviewSummary,
      menusSource,
      strengthsSource,
      summary: placeId
        ? `${resolvedName}의 네이버 플레이스 공식정보와 최신 손님 후기를 분석한 결과입니다.`
        : `${resolvedName}의 네이버 지역 정보와 손님 후기를 분석한 결과입니다.`,
      topStrengths,
      scoreCard: {
        menuCount: menusSource === 'official' ? menus.length : 0,
        hasPhotos: true,
        shortsCount: youtubeResult.count,
        sampleVideoTitle: youtubeResult.sampleTitle,
      },
      aiPlan: {
        recommendedMenu,
        hookCopy,
        conceptPitch: `첫 3초 동안 ${recommendedMenu}의 클로즈업 비주얼과 사운드를 강조해 손님의 침샘을 자극하고 방문을 유도합니다.${topKeyword ? ` 사장님이 대표 키워드로 등록한 '${topKeyword}'도 함께 보여드려요.` : ''}`,
      },
      menus,
    });
  } catch (error) {
    console.error('Diagnosis handler error:', error);
    return res.status(500).json({ error: error.message });
  }
}
