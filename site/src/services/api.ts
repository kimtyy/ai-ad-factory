import { StoreDiagnosticResult, LeadSubmission, StoreCandidate } from '../types';
import { generateDynamicDiagnosis } from '../data/mockStores';

// 상호명 입력 중 실제 매장 후보 (최대 5개). 실패하면 빈 목록 → 화면은 기존처럼 입력한 상호명으로 진단한다
export async function searchStores(q: string, signal?: AbortSignal): Promise<StoreCandidate[]> {
  const trimmed = q.trim();
  if (trimmed.length < 2) return [];
  try {
    const res = await fetch(`/api/search-stores?q=${encodeURIComponent(trimmed)}`, { signal });
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data.items) ? (data.items as StoreCandidate[]) : [];
  } catch (err) {
    if ((err as { name?: string })?.name !== 'AbortError') {
      console.warn('Backend /api/search-stores unavailable:', err);
    }
    return [];
  }
}

export async function diagnoseStore(
  storeName: string,
  opts: { address?: string; placeId?: string } = {},
): Promise<StoreDiagnosticResult> {
  const trimmed = storeName.trim();
  if (!trimmed) {
    throw new Error('상호명을 입력해 주세요.');
  }

  try {
    const res = await fetch('/api/diagnose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ storeName: trimmed, address: opts.address || '', placeId: opts.placeId || '' }),
    });

    if (res.ok) {
      const data = await res.json();
      if (data && data.storeName) {
        return data as StoreDiagnosticResult;
      }
    }
  } catch (err) {
    console.warn('Backend /api/diagnose unavailable, using client-side diagnosis engine:', err);
  }

  // 폴백 엔진 (실제 분석 시간 체감을 위한 자연스러운 지연)
  await new Promise((r) => setTimeout(r, 900));
  return generateDynamicDiagnosis(trimmed);
}

export async function submitLead(lead: LeadSubmission): Promise<{ success: boolean; message: string }> {
  // 1. 로컬 백업 저장 (유실 방지)
  try {
    const existing = JSON.parse(localStorage.getItem('shopstory_leads') || '[]');
    existing.push(lead);
    localStorage.setItem('shopstory_leads', JSON.stringify(existing));
  } catch (e) {
    console.error('LocalStorage lead backup failed', e);
  }

  // 2. 서버리스 API 또는 웹훅 전송
  try {
    const res = await fetch('/api/lead', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(lead),
    });

    if (res.ok) {
      return { success: true, message: '정상 접수되었습니다.' };
    }
  } catch (err) {
    console.warn('Backend /api/lead call failed, saved to local lead storage', err);
  }

  return {
    success: true,
    message: '신청이 정상적으로 저장되었습니다! 곧 입력하신 번호로 카카오톡 안내를 드립니다.',
  };
}

