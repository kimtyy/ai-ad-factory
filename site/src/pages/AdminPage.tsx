import { useState, useEffect } from 'react';
import type { FC, FormEvent } from 'react';
import { LeadRecord } from '../types';
import { buildStudioUrl } from '../utils/studioUrl';

function cleanDigits(s?: string): string {
  return String(s || '').replace(/[^0-9]/g, '');
}

function cleanStore(s?: string): string {
  return String(s || '').replace(/\s+/g, '').toLowerCase();
}

function areLeadsDuplicate(a: LeadRecord, b: LeadRecord): boolean {
  // 1. 직접 ID 일치
  if (a.id && b.id && a.id === b.id) return true;

  // 2. 전화번호 정규화 비교
  const phoneA = cleanDigits(a.phoneNumber);
  const phoneB = cleanDigits(b.phoneNumber);
  if (phoneA.length >= 8 && phoneB.length >= 8 && phoneA === phoneB) {
    // 상호명 비교
    const storeA = cleanStore(a.storeName);
    const storeB = cleanStore(b.storeName);
    const storeMatch = storeA === storeB || storeA.includes(storeB) || storeB.includes(storeA);

    if (storeMatch) {
      // 시간차 확인 (동일일자이거나 60분 이내 신청이면 동일 건으로 판정)
      const tA = new Date(a.createdAt).getTime();
      const tB = new Date(b.createdAt).getTime();
      if (!isNaN(tA) && !isNaN(tB)) {
        if (Math.abs(tA - tB) < 60 * 60 * 1000) return true;
        const dateA = new Date(tA).toISOString().slice(0, 10);
        const dateB = new Date(tB).toISOString().slice(0, 10);
        if (dateA === dateB) return true;
      } else {
        return true;
      }
    }
  }

  return false;
}

export const AdminPage: FC = () => {
  const [token, setToken] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [leads, setLeads] = useState<LeadRecord[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  // 1. 초기 토큰 확인
  useEffect(() => {
    const savedToken = sessionStorage.getItem('shopstory_admin_token');
    if (savedToken) {
      setToken(savedToken);
      fetchLeads(savedToken);
    }
  }, []);

  // 2. 로그인 처리
  const handleLogin = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMsg('');
    setIsLoading(true);

    try {
      const res = await fetch('/api/admin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'login', password }),
      });

      const data = await res.json();
      if (!res.ok || !data.token) {
        throw new Error(data.error || '비밀번호가 올바르지 않습니다.');
      }

      setToken(data.token);
      sessionStorage.setItem('shopstory_admin_token', data.token);
      setPassword('');
      fetchLeads(data.token);
    } catch (err: any) {
      setErrorMsg(err.message || '로그인에 실패했습니다.');
    } finally {
      setIsLoading(false);
    }
  };

  // 3. 로그아웃
  const handleLogout = () => {
    setToken(null);
    sessionStorage.removeItem('shopstory_admin_token');
    setLeads([]);
  };

  // 4. 리드 목록 불러오기 (서버 + 로컬스토리지 병합)
  const fetchLeads = async (authToken: string) => {
    setIsLoading(true);
    setErrorMsg('');

    let serverLeads: LeadRecord[] = [];
    try {
      const res = await fetch('/api/admin', {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.leads)) {
          serverLeads = data.leads;
        }
      }
    } catch (err) {
      console.warn('Server leads fetch error, using local leads:', err);
    }

    // 로컬스토리지 백업 병합
    let localLeads: LeadRecord[] = [];
    try {
      const stored = JSON.parse(localStorage.getItem('shopstory_leads') || '[]');
      if (Array.isArray(stored)) {
        localLeads = stored.map((item: any, idx: number) => ({
          id: item.id || `local_${idx}_${item.createdAt || ''}`,
          storeName: item.storeName || '상호 미확인',
          ownerName: item.ownerName || '사장님',
          phoneNumber: item.phoneNumber || '',
          planOptions: item.planOptions || {
            selectedMenu: '대표 메뉴',
            font: '감성 명조',
            voice: '밝은 여성',
            bgm: 'AI 추천',
          },
          status: item.status || '접수대기',
          createdAt: item.createdAt || new Date().toISOString(),
        }));
      }
    } catch (e) {
      console.error('Local leads parse error', e);
    }

    // 중복 제거 병합: ID 또는 (전화번호 + 상호명 + 근접시간) 기준으로 2줄 중복 표시 완전 제거
    const merged: LeadRecord[] = [];

    // 1) 서버 리드를 우선 등록
    for (const sLead of serverLeads) {
      const idx = merged.findIndex((m) => areLeadsDuplicate(m, sLead));
      if (idx === -1) {
        merged.push(sLead);
      } else {
        merged[idx] = { ...merged[idx], ...sLead };
      }
    }

    // 2) 로컬스토리지 리드 병합 (서버에 없는 신규 건만 추가)
    for (const lLead of localLeads) {
      const idx = merged.findIndex((m) => areLeadsDuplicate(m, lLead));
      if (idx === -1) {
        merged.push(lLead);
      }
    }

    merged.sort((a, b) => {
      const tA = new Date(a.createdAt).getTime() || 0;
      const tB = new Date(b.createdAt).getTime() || 0;
      return tB - tA;
    });

    setLeads(merged);
    setIsLoading(false);
  };

  // 5. 진행 상태 변경
  const handleStatusChange = async (leadId: string, newStatus: '접수대기' | '제작완료' | '상담완료') => {
    setLeads((prev) =>
      prev.map((l) => (l.id === leadId ? { ...l, status: newStatus } : l))
    );

    // 로컬스토리지 업데이트
    try {
      const local = JSON.parse(localStorage.getItem('shopstory_leads') || '[]');
      const updated = local.map((l: any) =>
        l.id === leadId ? { ...l, status: newStatus } : l
      );
      localStorage.setItem('shopstory_leads', JSON.stringify(updated));
    } catch (e) {}

    // 서버 업데이트
    if (token) {
      try {
        await fetch('/api/admin', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            action: 'update_status',
            leadId,
            status: newStatus,
          }),
        });
        setSuccessMsg('상태가 업데이트되었습니다.');
        setTimeout(() => setSuccessMsg(''), 2500);
      } catch (e) {
        console.warn('Status sync error', e);
      }
    }
  };

  // 날짜 포맷팅 함수
  const formatDate = (isoString: string) => {
    try {
      const d = new Date(isoString);
      if (isNaN(d.getTime())) return isoString;
      return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    } catch {
      return isoString;
    }
  };

  // 로그인 화면
  if (!token) {
    return (
      <div className="min-h-screen bg-[#F8F9FA] flex items-center justify-center p-4">
        <div className="w-full max-w-sm bg-white rounded-3xl shadow-xl p-8 border border-slate-100 text-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-tr from-[#0F2744] to-[#1E3A5F] flex items-center justify-center text-white font-black text-xl shadow-md mx-auto mb-4">
            S
          </div>
          <h2 className="text-xl font-extrabold text-slate-900 mb-1">
            샵스토리 관리자
          </h2>
          <p className="text-xs text-slate-500 mb-6">
            점주 신청 리드 관리 대시보드
          </p>

          {errorMsg && (
            <div className="mb-4 p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-600 font-bold">
              {errorMsg}
            </div>
          )}

          <form onSubmit={handleLogin} className="space-y-3">
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="관리자 비밀번호를 입력하세요"
              className="w-full h-12 px-4 rounded-xl bg-slate-50 border border-slate-200 focus:border-[#0F2744] focus:bg-white focus:outline-none text-sm font-medium transition-all"
            />
            <button
              type="submit"
              disabled={isLoading || !password.trim()}
              className="w-full h-12 rounded-xl bg-[#0F2744] hover:bg-[#1E3A5F] text-white font-bold text-sm shadow-md transition-all active:scale-[0.98]"
            >
              {isLoading ? '로그인 확인 중...' : '대시보드 로그인'}
            </button>
          </form>

          <div className="mt-6 pt-4 border-t border-slate-100 text-center">
            <a
              href="/"
              className="text-xs text-slate-400 hover:text-slate-600 font-medium underline"
            >
              ← 메인 진단 페이지로 돌아가기
            </a>
          </div>
        </div>
      </div>
    );
  }

  // 대시보드 메인 화면 (모바일 2줄 반응형 레이아웃 & 상시 작업실 버튼)
  return (
    <div className="min-h-screen bg-[#F8F9FA] text-slate-900">
      {/* 상단 네비게이션 헤더 */}
      <header className="sticky top-0 z-30 bg-white/95 backdrop-blur-md border-b border-slate-200 px-4 py-3 shadow-xs">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          {/* 타이틀 영역 */}
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl bg-[#0F2744] flex items-center justify-center text-white font-black text-sm shadow-xs shrink-0">
              S
            </div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-extrabold text-slate-900 whitespace-nowrap">
                점주 리드 관리 대시보드
              </h1>
              <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-[#EEF3F9] text-[#0F2744] border border-[#D0DCEB] whitespace-nowrap">
                총 {leads.length}건
              </span>
            </div>
          </div>

          {/* 버튼 영역 (모바일 줄바꿈 방지) */}
          <div className="flex items-center gap-2 self-start sm:self-auto flex-wrap sm:flex-nowrap">
            {/* 상시 제작 작업실 열기 버튼 */}
            <a
              href="https://shopstory-store.streamlit.app"
              target="_blank"
              rel="noopener noreferrer"
              className="px-3.5 py-1.5 rounded-xl bg-gradient-to-r from-[#0F2744] to-[#1E3A5F] hover:from-[#1E3A5F] hover:to-[#2A4D7A] text-white text-xs font-bold shadow-xs active:scale-95 transition-all flex items-center gap-1.5 whitespace-nowrap"
            >
              <span>🎬</span>
              <span>제작 작업실 열기</span>
              <span>↗</span>
            </a>

            <button
              onClick={() => fetchLeads(token)}
              disabled={isLoading}
              className="px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-200 text-xs font-bold text-slate-700 transition-colors flex items-center gap-1 whitespace-nowrap"
            >
              <span>🔄</span>
              <span>{isLoading ? '새로고침 중...' : '새로고침'}</span>
            </button>

            <button
              onClick={handleLogout}
              className="px-3 py-1.5 rounded-xl bg-white border border-slate-200 hover:bg-slate-50 text-xs font-medium text-slate-500 transition-colors whitespace-nowrap"
            >
              로그아웃
            </button>
          </div>
        </div>
      </header>

      {/* 안내 알림 */}
      {successMsg && (
        <div className="max-w-6xl mx-auto px-4 mt-4">
          <div className="p-3 rounded-xl bg-emerald-50 border border-emerald-200 text-xs text-emerald-800 font-bold">
            ✓ {successMsg}
          </div>
        </div>
      )}

      {/* 본문 콘텐츠 */}
      <main className="max-w-6xl mx-auto px-4 py-6">
        {leads.length === 0 ? (
          <div className="bg-white rounded-3xl p-12 text-center border border-slate-200 shadow-xs">
            <div className="text-4xl mb-3">📭</div>
            <h3 className="text-base font-bold text-slate-800">아직 접수된 리드가 없습니다</h3>
            <p className="text-xs text-slate-400 mt-1 mb-4">
              점주님이 매장 정밀 진단 후 연락처를 남기면 이곳에 실시간으로 표시됩니다.
            </p>
            <a
              href="https://shopstory-store.streamlit.app"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-[#0F2744] hover:bg-[#1E3A5F] text-white text-xs font-bold shadow-sm"
            >
              <span>🎬 오프라인 의뢰 건 바로 제작하기 (작업실 열기 ↗)</span>
            </a>
          </div>
        ) : (
          <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="bg-slate-50/80 border-b border-slate-200 text-slate-500 font-bold">
                    <th className="py-3 px-4 whitespace-nowrap">접수일시</th>
                    <th className="py-3 px-4 whitespace-nowrap">상호명</th>
                    <th className="py-3 px-4 whitespace-nowrap">사장님 성함</th>
                    <th className="py-3 px-4 whitespace-nowrap">연락처</th>
                    <th className="py-3 px-4">선택 기획 옵션</th>
                    <th className="py-3 px-4 whitespace-nowrap">진행 상태</th>
                    <th className="py-3 px-4 text-right whitespace-nowrap">작업실</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 font-medium">
                  {leads.map((lead) => (
                    <tr key={lead.id} className="hover:bg-slate-50/50 transition-colors">
                      {/* 접수일시 */}
                      <td className="py-3.5 px-4 text-slate-400 whitespace-nowrap">
                        {formatDate(lead.createdAt)}
                      </td>

                      {/* 상호명 */}
                      <td className="py-3.5 px-4 font-bold text-slate-900 whitespace-nowrap">
                        {lead.storeName}
                      </td>

                      {/* 사장님 성함 */}
                      <td className="py-3.5 px-4 font-semibold text-slate-700 whitespace-nowrap">
                        {lead.ownerName}
                      </td>

                      {/* 연락처 (클릭 시 즉시 통화) */}
                      <td className="py-3.5 px-4 whitespace-nowrap">
                        <a
                          href={`tel:${lead.phoneNumber.replace(/[^0-9]/g, '')}`}
                          className="font-bold text-[#0F2744] hover:underline flex items-center gap-1"
                        >
                          <span>📞</span>
                          <span>{lead.phoneNumber}</span>
                        </a>
                      </td>

                      {/* 선택 기획 옵션 */}
                      <td className="py-3.5 px-4">
                        <div className="flex flex-wrap gap-1 max-w-xs">
                          <span className="px-2 py-0.5 rounded-md bg-[#EEF3F9] text-[#0F2744] font-bold text-[11px] truncate max-w-[140px] border border-[#D0DCEB]">
                            🍽️ {lead.planOptions?.selectedMenu || '대표 메뉴'}
                          </span>
                          <span className="px-2 py-0.5 rounded-md bg-slate-100 text-slate-600 text-[11px]">
                            {lead.planOptions?.font?.split(' ')[0] || '폰트'}
                          </span>
                          <span className="px-2 py-0.5 rounded-md bg-slate-100 text-slate-600 text-[11px]">
                            {lead.planOptions?.voice?.split(' ')[0] || '목소리'}
                          </span>
                          <span className="px-2 py-0.5 rounded-md bg-slate-100 text-slate-600 text-[11px]">
                            {lead.planOptions?.bgm || 'BGM'}
                          </span>
                        </div>
                        {lead.planOptions?.ownerMemo && (
                          <div className="mt-1.5 px-2 py-1 rounded-md bg-amber-50 border border-amber-200 text-amber-800 text-[11px] font-semibold max-w-xs">
                            📝 사장님 메모: {lead.planOptions.ownerMemo}
                          </div>
                        )}
                        {lead.planOptions?.address && (
                          <div className="mt-1 text-[10px] text-slate-400 max-w-xs truncate">📍 {lead.planOptions.address}</div>
                        )}
                      </td>

                      {/* 진행 상태 변경 드롭다운 */}
                      <td className="py-3.5 px-4 whitespace-nowrap">
                        <select
                          value={lead.status || '접수대기'}
                          onChange={(e) =>
                            handleStatusChange(
                              lead.id,
                              e.target.value as '접수대기' | '제작완료' | '상담완료'
                            )
                          }
                          className={`text-xs font-bold rounded-lg px-2.5 py-1 border transition-colors cursor-pointer ${
                            lead.status === '제작완료'
                              ? 'bg-blue-50 text-blue-700 border-blue-200'
                              : lead.status === '상담완료'
                              ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                              : 'bg-amber-50 text-amber-700 border-amber-200'
                          }`}
                        >
                          <option value="접수대기">⏳ 접수대기</option>
                          <option value="제작완료">🎬 제작완료</option>
                          <option value="상담완료">✅ 상담완료</option>
                        </select>
                      </td>

                      {/* 작업실 열기 버튼 */}
                      <td className="py-3.5 px-4 text-right whitespace-nowrap">
                        <a
                          href={buildStudioUrl(lead)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 px-3 py-1.5 rounded-xl bg-[#0F2744] hover:bg-[#1E3A5F] text-white text-xs font-bold shadow-xs active:scale-95 transition-all"
                        >
                          <span>🎬</span>
                          <span>작업실 열기</span>
                          <span>↗</span>
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>
    </div>
  );
};
