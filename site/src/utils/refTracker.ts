// site/src/utils/refTracker.ts
// 추천 링크(?ref=영업자코드) 추적 및 7일 유효기간 관리

const REF_STORAGE_KEY = 'shopstory_ref';
const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000;

interface StoredRef {
  refCode: string;
  savedAt: number;
}

/**
 * 영문, 숫자, 하이픈, 언더스코어만 허용 (최대 32자)
 */
export function sanitizeRefCode(code?: string | null): string {
  if (!code) return '';
  return String(code).trim().replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 32);
}

/**
 * 현재 URL 파라미터에서 ?ref= 확인 후 유효하면 localStorage에 저장
 */
export function initRefTracking(): string {
  if (typeof window === 'undefined') return 'hq';

  try {
    const params = new URLSearchParams(window.location.search);
    const urlRef = sanitizeRefCode(params.get('ref'));

    if (urlRef) {
      const data: StoredRef = {
        refCode: urlRef,
        savedAt: Date.now(),
      };
      localStorage.setItem(REF_STORAGE_KEY, JSON.stringify(data));
      return urlRef;
    }

    return getStoredRefCode();
  } catch (e) {
    console.warn('Ref tracking init error:', e);
    return 'hq';
  }
}

/**
 * 저장된 refCode 조회 (7일 만료 검사, 없거나 만료 시 'hq')
 */
export function getStoredRefCode(): string {
  if (typeof window === 'undefined') return 'hq';

  try {
    // 1. 현재 URL 파라미터 우선 확인
    const params = new URLSearchParams(window.location.search);
    const urlRef = sanitizeRefCode(params.get('ref'));
    if (urlRef) return urlRef;

    // 2. localStorage 조회
    const raw = localStorage.getItem(REF_STORAGE_KEY);
    if (!raw) return 'hq';

    const parsed: StoredRef = JSON.parse(raw);
    if (!parsed || !parsed.refCode || !parsed.savedAt) return 'hq';

    const age = Date.now() - parsed.savedAt;
    if (age > SEVEN_DAYS_MS) {
      // 7일 만료
      localStorage.removeItem(REF_STORAGE_KEY);
      return 'hq';
    }

    return sanitizeRefCode(parsed.refCode) || 'hq';
  } catch (e) {
    return 'hq';
  }
}
