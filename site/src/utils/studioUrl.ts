import type { LeadRecord } from '../types';

export const STUDIO_BASE = 'https://shopstory-store.streamlit.app';

// 리드 한 건을 작업실(app_store.py)에 그대로 넘기는 주소. 값은 모두 encodeURIComponent, 비어 있는 값은 붙이지 않는다.
export function buildStudioUrl(lead: Pick<LeadRecord, 'storeName' | 'planOptions'>): string {
  const o = lead.planOptions || ({} as LeadRecord['planOptions']);
  const pairs: [string, string | undefined][] = [
    ['store', lead.storeName],
    ['address', o.address],
    ['place_id', o.placeId],
    ['menu', o.selectedMenu],
    ['font', o.font],
    ['voice', o.voice],
    ['bgm', o.bgm],
    ['memo', o.ownerMemo ? o.ownerMemo.slice(0, 100) : ''],
  ];
  const qs = pairs
    .filter(([, v]) => typeof v === 'string' && v.trim() !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent((v as string).trim())}`)
    .join('&');
  return qs ? `${STUDIO_BASE}/?${qs}` : STUDIO_BASE;
}
