// api/recent-stores.js
// 최근 진단된 매장 목록 API (실제 진단된 매장 최신순 최대 6개)

export const SEED_STORES = [
  {
    storeName: '설맥 가평현리점',
    category: '생맥주, 호프',
    address: '경기 가평군 조종면 현리',
  },
  {
    storeName: '효정명인갈비',
    category: '육류, 고기요리',
    address: '경기 가평군 설악면',
  },
];

if (!global.__SHOPSTORY_RECENT_STORES__) {
  global.__SHOPSTORY_RECENT_STORES__ = [...SEED_STORES];
}

export function addRecentStore(store) {
  if (!store || !store.storeName) return;
  if (!global.__SHOPSTORY_RECENT_STORES__) {
    global.__SHOPSTORY_RECENT_STORES__ = [...SEED_STORES];
  }

  const cleanName = String(store.storeName).trim();
  const filtered = global.__SHOPSTORY_RECENT_STORES__.filter(
    (s) => s.storeName.replace(/\s+/g, '') !== cleanName.replace(/\s+/g, '')
  );

  filtered.unshift({
    storeName: cleanName,
    category: String(store.category || '음식점').trim(),
    address: String(store.address || '').trim(),
  });

  global.__SHOPSTORY_RECENT_STORES__ = filtered.slice(0, 6);
}

export default async function handler(req, res) {
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }
  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  if (!global.__SHOPSTORY_RECENT_STORES__ || global.__SHOPSTORY_RECENT_STORES__.length === 0) {
    global.__SHOPSTORY_RECENT_STORES__ = [...SEED_STORES];
  }

  res.setHeader('Cache-Control', 's-maxage=10, stale-while-revalidate=60');
  return res.status(200).json({
    success: true,
    stores: global.__SHOPSTORY_RECENT_STORES__.slice(0, 6),
  });
}
