// site/api/search-stores.js
// GET /api/search-stores?q=상호명  →  { items: [{ storeName, category, roadAddress, address, link }] } (최대 5개)
// 사장님이 같은 이름의 여러 지점 중 자기 매장을 직접 고르게 하는 후보 목록 (NCP NAVER API HUB 우선, OpenAPI 폴백)
import { searchLocal, toCandidate } from '../server/naver.js';

export default async function handler(req, res) {
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }
  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  const q = String((req.query && req.query.q) || '').trim().slice(0, 40);
  if (q.length < 2) {
    return res.status(400).json({ error: '상호명을 2글자 이상 입력해 주세요.', items: [] });
  }

  const clientId = process.env.NAVER_CLIENT_ID;
  const clientSecret = process.env.NAVER_CLIENT_SECRET;
  if (!clientId || !clientSecret) {
    return res.status(200).json({ items: [], note: 'NAVER API 키가 설정되지 않았어요.' });
  }

  try {
    const raw = await searchLocal(q, clientId, clientSecret, 5);
    const seen = new Set();
    const items = [];
    for (const it of raw) {
      const c = toCandidate(it);
      const key = `${c.storeName}|${c.roadAddress || c.address}`;
      if (!c.storeName || seen.has(key)) continue;
      seen.add(key);
      items.push(c);
    }
    res.setHeader('Cache-Control', 's-maxage=60, stale-while-revalidate=300');
    return res.status(200).json({ items: items.slice(0, 5) });
  } catch (error) {
    console.error('search-stores error:', error);
    return res.status(200).json({ items: [] });                    // 후보를 못 가져와도 화면은 기존처럼 상호명 입력으로 계속 진행
  }
}
