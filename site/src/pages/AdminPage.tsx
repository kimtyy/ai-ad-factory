import { useState, useEffect } from 'react';
import type { FC, FormEvent } from 'react';
import { LeadRecord } from '../types';
import { buildStudioUrl } from '../utils/studioUrl';

interface AdminUser {
  code: string;
  name: string;
  role: 'super' | 'manager' | 'agent';
  managerCode?: string;
}

interface AdminStats {
  totalCount: number;
  byStatus: {
    접수대기: number;
    제작완료: number;
    상담완료: number;
  };
  byAgent?: Record<string, number>;
  byManager?: Record<string, number>;
  directCount?: number;
  teamCount?: number;
}

function cleanDigits(s?: string): string {
  return String(s || '').replace(/[^0-9]/g, '');
}

function cleanStore(s?: string): string {
  return String(s || '').replace(/\s+/g, '').toLowerCase();
}

function areLeadsDuplicate(a: LeadRecord, b: LeadRecord): boolean {
  if (a.id && b.id && a.id === b.id) return true;

  const phoneA = cleanDigits(a.phoneNumber);
  const phoneB = cleanDigits(b.phoneNumber);
  if (phoneA.length >= 8 && phoneB.length >= 8 && phoneA === phoneB) {
    const storeA = cleanStore(a.storeName);
    const storeB = cleanStore(b.storeName);
    const storeMatch = !!storeA && !!storeB && (storeA === storeB || storeA.includes(storeB) || storeB.includes(storeA));

    const mA = String(a.planOptions?.selectedMenu || '').trim();
    const mB = String(b.planOptions?.selectedMenu || '').trim();
    if (mA && mB && mA !== mB) return false;

    if (storeMatch) {
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
  const [user, setUser] = useState<AdminUser | null>(null);
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [leads, setLeads] = useState<LeadRecord[]>([]);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [successMsg, setSuccessMsg] = useState('');
  const [copyToast, setCopyToast] = useState(false);

  // 필터 상태
  const [agentFilter, setAgentFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');

  // 1. 초기 토큰 및 사용자 정보 복원
  useEffect(() => {
    const savedToken = sessionStorage.getItem('shopstory_admin_token');
    const savedUser = sessionStorage.getItem('shopstory_admin_user');
    if (savedToken) {
      setToken(savedToken);
      if (savedUser) {
        try {
          setUser(JSON.parse(savedUser));
        } catch (e) {}
      }
      fetchLeads(savedToken);
    }
  }, []);

  // 2. 로그인 처리 (code + password)
  const handleLogin = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMsg('');
    setIsLoading(true);

    try {
      const res = await fetch('/api/admin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'login',
          code: code.trim(),
          password: password.trim(),
        }),
      });

      const data = await res.json();
      if (!res.ok || !data.token) {
        throw new Error(data.error || '담당자 코드 또는 비밀번호가 올바르지 않습니다.');
      }

      setToken(data.token);
      setUser(data.user);
      sessionStorage.setItem('shopstory_admin_token', data.token);
      sessionStorage.setItem('shopstory_admin_user', JSON.stringify(data.user));
      setPassword('');
      setCode('');
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
    setUser(null);
    sessionStorage.removeItem('shopstory_admin_token');
    sessionStorage.removeItem('shopstory_admin_user');
    setLeads([]);
    setStats(null);
  };

  // 4. 리드 목록 불러오기 (서버 필터링 데이터 수신)
  const fetchLeads = async (authToken: string) => {
    setIsLoading(true);
    setErrorMsg('');

    try {
      const res = await fetch('/api/admin', {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (!res.ok) {
        if (res.status === 401) {
          handleLogout();
          throw new Error('세션이 만료되었습니다. 다시 로그인해 주세요.');
        }
        throw new Error('리드 목록을 불러오지 못했습니다.');
      }

      const data = await res.json();
      if (data.user) {
        setUser(data.user);
        sessionStorage.setItem('shopstory_admin_user', JSON.stringify(data.user));
      }

      let serverLeads: LeadRecord[] = Array.isArray(data.leads) ? data.leads : [];
      if (data.stats) {
        setStats(data.stats);
      }

      // 로컬스토리지 백업 병합 (서버에 아직 없는 동일 권한 리드 보완)
      let localLeads: LeadRecord[] = [];
      try {
        const stored = JSON.parse(localStorage.getItem('shopstory_leads') || '[]');
        if (Array.isArray(stored)) {
          localLeads = stored
            .map((item: any, idx: number) => ({
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
              refCode: item.refCode || 'hq',
              managerCode: item.managerCode || 'hq',
              createdAt: item.createdAt || new Date().toISOString(),
            }))
            .filter((item) => {
              // 본인 권한 범위 내 리드만 로컬 병합 허용
              if (!data.user || data.user.role === 'super') return true;
              const uCode = data.user.code.toLowerCase();
              if (data.user.role === 'manager') {
                return (
                  (item.refCode || '').toLowerCase() === uCode ||
                  (item.managerCode || '').toLowerCase() === uCode
                );
              }
              if (data.user.role === 'agent') {
                return (item.refCode || '').toLowerCase() === uCode;
              }
              return false;
            });
        }
      } catch (e) {
        console.error('Local leads parse error', e);
      }

      const merged: LeadRecord[] = [];
      for (const sLead of serverLeads) {
        const idx = merged.findIndex((m) => areLeadsDuplicate(m, sLead));
        if (idx === -1) {
          merged.push(sLead);
        } else {
          merged[idx] = { ...merged[idx], ...sLead };
        }
      }
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
    } catch (err: any) {
      setErrorMsg(err.message || '목록을 불러오지 못했습니다.');
    } finally {
      setIsLoading(false);
    }
  };

  // 5. 진행 상태 변경
  const handleStatusChange = async (leadId: string, newStatus: '접수대기' | '제작완료' | '상담완료') => {
    // 낙관적 UI 업데이트
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

    // 서버 업데이트 전송
    if (token) {
      try {
        const res = await fetch('/api/admin', {
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

        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.error || '상태 변경 권한이 없습니다.');
        }

        setSuccessMsg('상태가 업데이트되었습니다.');
        setTimeout(() => setSuccessMsg(''), 2500);
      } catch (e: any) {
        alert(e.message || '상태 변경에 실패했습니다.');
        if (token) fetchLeads(token); // 원상복구
      }
    }
  };

  // 6. 내 영업 링크 복사
  const handleCopyRefLink = async () => {
    const myCode = user?.code && user.code !== 'hq' ? user.code : 'hq';
    const origin = typeof window !== 'undefined' && window.location.origin ? window.location.origin : 'https://www.shopstory.kr';
    const link = `${origin}/?ref=${encodeURIComponent(myCode)}`;

    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(link);
      } else {
        const ta = document.createElement('textarea');
        ta.value = link;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setCopyToast(true);
      setTimeout(() => setCopyToast(false), 2500);
    } catch (e) {
      alert(`내 영업 링크:\n${link}`);
    }
  };

  // 날짜 포맷팅
  const formatDate = (isoString: string) => {
    try {
      const d = new Date(isoString);
      if (isNaN(d.getTime())) return isoString;
      return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    } catch {
      return isoString;
    }
  };

  // 필터링 적용된 리드 목록
  const displayedLeads = leads.filter((lead) => {
    if (statusFilter !== 'all' && (lead.status || '접수대기') !== statusFilter) {
      return false;
    }
    if (agentFilter !== 'all') {
      if (agentFilter === '__direct__') {
        // 중간관리자 본인 직속 건
        return (lead.refCode || 'hq').toLowerCase() === (user?.code || '').toLowerCase();
      }
      return (lead.refCode || 'hq').toLowerCase() === agentFilter.toLowerCase();
    }
    return true;
  });

  // 영업자 필터 옵션 목록 추출
  const availableAgents = Array.from(
    new Set(leads.map((l) => l.refCode || 'hq').filter(Boolean))
  );

  // ----------------------------------------------------
  // 로그인 화면
  // ----------------------------------------------------
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
            수퍼관리자 · 팀장 · 현장 영업자 전용 대시보드
          </p>

          {errorMsg && (
            <div className="mb-4 p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-600 font-bold">
              {errorMsg}
            </div>
          )}

          <form onSubmit={handleLogin} className="space-y-3">
            <div className="text-left">
              <label className="text-[11px] font-bold text-slate-500 ml-1 mb-1 block">
                담당자 코드 <span className="text-slate-400 font-normal">(수퍼관리자는 비워두세요)</span>
              </label>
              <input
                type="text"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="영업자 또는 팀장 코드"
                className="w-full h-12 px-4 rounded-xl bg-slate-50 border border-slate-200 focus:border-[#0F2744] focus:bg-white focus:outline-none text-sm font-medium transition-all"
              />
            </div>

            <div className="text-left">
              <label className="text-[11px] font-bold text-slate-500 ml-1 mb-1 block">
                비밀번호
              </label>
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="비밀번호를 입력하세요"
                className="w-full h-12 px-4 rounded-xl bg-slate-50 border border-slate-200 focus:border-[#0F2744] focus:bg-white focus:outline-none text-sm font-medium transition-all"
              />
            </div>

            <button
              type="submit"
              disabled={isLoading || !password.trim()}
              className="w-full h-12 rounded-xl bg-[#0F2744] hover:bg-[#1E3A5F] text-white font-bold text-sm shadow-md transition-all active:scale-[0.98] mt-2 cursor-pointer disabled:opacity-50"
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

  // ----------------------------------------------------
  // 등급별 배지 컴포넌트
  // ----------------------------------------------------
  const renderRoleBadge = () => {
    if (user?.role === 'super') {
      return (
        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-extrabold bg-purple-50 text-purple-700 border border-purple-200 shadow-2xs whitespace-nowrap">
          <span>👑</span>
          <span>수퍼관리자</span>
        </span>
      );
    }
    if (user?.role === 'manager') {
      return (
        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-extrabold bg-blue-50 text-blue-700 border border-blue-200 shadow-2xs whitespace-nowrap">
          <span>🧭</span>
          <span>중간관리자</span>
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-extrabold bg-emerald-50 text-emerald-700 border border-emerald-200 shadow-2xs whitespace-nowrap">
        <span>🏃</span>
        <span>영업자</span>
      </span>
    );
  };

  // ----------------------------------------------------
  // 메인 대시보드 화면
  // ----------------------------------------------------
  return (
    <div className="min-h-screen bg-[#F8F9FA] text-slate-900">
      {/* 상단 네비게이션 헤더 */}
      <header className="sticky top-0 z-30 bg-white/95 backdrop-blur-md border-b border-slate-200 px-4 py-3 shadow-xs">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          {/* 타이틀 및 사용자 계정 정보 */}
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl bg-[#0F2744] flex items-center justify-center text-white font-black text-sm shadow-xs shrink-0">
              S
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-base font-extrabold text-slate-900 whitespace-nowrap">
                점주 리드 관리
              </h1>
              {renderRoleBadge()}
              <span className="text-xs font-bold text-slate-600 bg-slate-100 px-2 py-0.5 rounded-md whitespace-nowrap">
                {user?.name} ({user?.code})
              </span>
              <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-[#EEF3F9] text-[#0F2744] border border-[#D0DCEB] whitespace-nowrap">
                {displayedLeads.length}건
              </span>
            </div>
          </div>

          {/* 상단 버튼 영역 (내 영업링크 복사 + 상시 작업실 열기 + 새로고침 + 로그아웃) */}
          <div className="flex items-center gap-2 self-start sm:self-auto flex-wrap sm:flex-nowrap">
            {/* 🔗 내 영업 링크 복사 버튼 */}
            <button
              onClick={handleCopyRefLink}
              className={`px-3 py-1.5 rounded-xl text-xs font-bold shadow-xs active:scale-95 transition-all flex items-center gap-1.5 whitespace-nowrap cursor-pointer ${
                copyToast
                  ? 'bg-emerald-600 text-white'
                  : 'bg-white border border-[#0F2744] text-[#0F2744] hover:bg-[#EEF3F9]'
              }`}
            >
              <span>{copyToast ? '✓' : '🔗'}</span>
              <span>{copyToast ? '링크 복사됨!' : '내 영업 링크 복사'}</span>
            </button>

            {/* [🎬 제작 작업실 열기 ↗] 상시 버튼 (전 등급 허용) */}
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
              onClick={() => token && fetchLeads(token)}
              disabled={isLoading}
              className="px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-200 text-xs font-bold text-slate-700 transition-colors flex items-center gap-1 whitespace-nowrap cursor-pointer"
            >
              <span>🔄</span>
              <span>{isLoading ? '새로고침 중...' : '새로고침'}</span>
            </button>

            <button
              onClick={handleLogout}
              className="px-3 py-1.5 rounded-xl bg-white border border-slate-200 hover:bg-slate-50 text-xs font-medium text-slate-500 transition-colors whitespace-nowrap cursor-pointer"
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
        {/* 요약 통계 및 필터 바 */}
        <div className="mb-4 bg-white rounded-2xl p-4 border border-slate-200 shadow-2xs flex flex-col md:flex-row md:items-center justify-between gap-4">
          {/* 통계 지표 */}
          <div className="flex items-center gap-3 flex-wrap text-xs">
            <span className="font-bold text-slate-500">현황:</span>
            <span className="px-2.5 py-1 rounded-lg bg-amber-50 text-amber-800 border border-amber-200 font-bold">
              ⏳ 접수대기 {stats?.byStatus?.접수대기 ?? leads.filter((l) => (l.status || '접수대기') === '접수대기').length}
            </span>
            <span className="px-2.5 py-1 rounded-lg bg-blue-50 text-blue-800 border border-blue-200 font-bold">
              🎬 제작완료 {stats?.byStatus?.제작완료 ?? leads.filter((l) => l.status === '제작완료').length}
            </span>
            <span className="px-2.5 py-1 rounded-lg bg-emerald-50 text-emerald-800 border border-emerald-200 font-bold">
              ✅ 상담완료 {stats?.byStatus?.상담완료 ?? leads.filter((l) => l.status === '상담완료').length}
            </span>

            {/* 중간관리자 직속/팀 통계 */}
            {user?.role === 'manager' && stats?.directCount != null && (
              <span className="ml-2 text-slate-500 font-medium">
                (직속: <b className="text-slate-800">{stats.directCount}건</b> / 소속팀원: <b className="text-slate-800">{stats.teamCount}건</b>)
              </span>
            )}
          </div>

          {/* 필터 컨트롤 드롭다운 */}
          <div className="flex items-center gap-2 flex-wrap">
            {/* 상태 필터 */}
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="text-xs font-semibold rounded-xl px-3 py-1.5 bg-slate-50 border border-slate-200 text-slate-700 cursor-pointer"
            >
              <option value="all">전체 상태</option>
              <option value="접수대기">⏳ 접수대기만</option>
              <option value="제작완료">🎬 제작완료만</option>
              <option value="상담완료">✅ 상담완료만</option>
            </select>

            {/* 수퍼관리자: 전체 영업자별 필터 */}
            {user?.role === 'super' && (
              <select
                value={agentFilter}
                onChange={(e) => setAgentFilter(e.target.value)}
                className="text-xs font-semibold rounded-xl px-3 py-1.5 bg-slate-50 border border-slate-200 text-slate-700 cursor-pointer"
              >
                <option value="all">전체 담당자 ({leads.length})</option>
                {availableAgents.map((ag) => (
                  <option key={ag} value={ag}>
                    {ag === 'hq' ? '🏢 본사 직속 (hq)' : `🏃 ${ag}`} ({leads.filter((l) => (l.refCode || 'hq') === ag).length})
                  </option>
                ))}
              </select>
            )}

            {/* 중간관리자: 직속 및 팀원별 필터 */}
            {user?.role === 'manager' && (
              <select
                value={agentFilter}
                onChange={(e) => setAgentFilter(e.target.value)}
                className="text-xs font-semibold rounded-xl px-3 py-1.5 bg-slate-50 border border-slate-200 text-slate-700 cursor-pointer"
              >
                <option value="all">팀 전체 ({leads.length})</option>
                <option value="__direct__">📌 내 직속 신청만 ({stats?.directCount ?? 0})</option>
                {availableAgents
                  .filter((ag) => ag.toLowerCase() !== user.code.toLowerCase())
                  .map((ag) => (
                    <option key={ag} value={ag}>
                      🏃 팀원 {ag} ({leads.filter((l) => (l.refCode || 'hq') === ag).length})
                    </option>
                  ))}
              </select>
            )}
          </div>
        </div>

        {/* 리드 테이블 */}
        {displayedLeads.length === 0 ? (
          <div className="bg-white rounded-3xl p-12 text-center border border-slate-200 shadow-xs">
            <div className="text-4xl mb-3">📭</div>
            <h3 className="text-base font-bold text-slate-800">
              {leads.length === 0 ? '아직 접수된 리드가 없습니다' : '조건에 맞는 리드가 없습니다'}
            </h3>
            <p className="text-xs text-slate-400 mt-1 mb-4">
              점주님이 내 전용 영업 링크로 접속해 신청하면 실시간으로 이곳에 표시됩니다.
            </p>
            <div className="flex items-center justify-center gap-2">
              <button
                onClick={handleCopyRefLink}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-white border border-[#0F2744] text-[#0F2744] hover:bg-[#EEF3F9] text-xs font-bold shadow-xs cursor-pointer"
              >
                <span>🔗 내 영업 링크 복사하기</span>
              </button>
              <a
                href="https://shopstory-store.streamlit.app"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-[#0F2744] hover:bg-[#1E3A5F] text-white text-xs font-bold shadow-sm"
              >
                <span>🎬 오프라인 의뢰 건 바로 제작하기 ↗</span>
              </a>
            </div>
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
                    {/* 수퍼관리자 또는 중간관리자일 때 영업자 배지 컬럼 표시 */}
                    {(user?.role === 'super' || user?.role === 'manager') && (
                      <th className="py-3 px-4 whitespace-nowrap">
                        {user.role === 'super' ? '담당자 / 팀' : '구분'}
                      </th>
                    )}
                    <th className="py-3 px-4 whitespace-nowrap">진행 상태</th>
                    <th className="py-3 px-4 text-right whitespace-nowrap">작업실</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 font-medium">
                  {displayedLeads.map((lead) => (
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
                          <div className="mt-1 text-[10px] text-slate-400 max-w-xs truncate">
                            📍 {lead.planOptions.address}
                          </div>
                        )}
                      </td>

                      {/* 담당자 / 팀 컬럼 */}
                      {(user?.role === 'super' || user?.role === 'manager') && (
                        <td className="py-3.5 px-4 whitespace-nowrap">
                          {user.role === 'super' ? (
                            <div className="flex flex-col gap-0.5">
                              <span className="inline-flex items-center gap-1 font-bold text-slate-800 text-[11px]">
                                <span>🏃</span>
                                <span>{lead.refCode || 'hq'}</span>
                              </span>
                              {lead.managerCode && lead.managerCode !== 'hq' && (
                                <span className="text-[10px] text-slate-400">
                                  팀: {lead.managerCode}
                                </span>
                              )}
                            </div>
                          ) : (
                            /* 중간관리자: 본인 직속 vs 소속 팀원 */
                            <div>
                              {(lead.refCode || 'hq').toLowerCase() === user.code.toLowerCase() ? (
                                <span className="px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200 text-[11px] font-bold">
                                  📌 본인직속
                                </span>
                              ) : (
                                <span className="px-2 py-0.5 rounded-md bg-slate-100 text-slate-700 text-[11px] font-bold">
                                  🏃 {lead.refCode || '소속원'}
                                </span>
                              )}
                            </div>
                          )}
                        </td>
                      )}

                      {/* 진행 상태 변경 드롭다운 (전 등급 허용) */}
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

                      {/* 작업실 열기 버튼 (전 등급 허용) */}
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
