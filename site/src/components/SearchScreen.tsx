import { useEffect, useRef, useState } from 'react';
import type { FC, FormEvent } from 'react';
import type { StoreCandidate } from '../types';
import { searchStores, getRecentStores, RecentStoreItem } from '../services/api';

interface SearchScreenProps {
  onSearch: (storeName: string, candidate?: StoreCandidate) => void;
  isLoading: boolean;
}

export const SearchScreen: FC<SearchScreenProps> = ({ onSearch, isLoading }) => {
  const [storeName, setStoreName] = useState('');
  const [loadingStep, setLoadingStep] = useState(0);
  const [candidates, setCandidates] = useState<StoreCandidate[]>([]);
  const [selected, setSelected] = useState<StoreCandidate | null>(null);
  const [isSuggesting, setIsSuggesting] = useState(false);
  const [hint, setHint] = useState('');
  const [recentStores, setRecentStores] = useState<RecentStoreItem[]>([
    { storeName: '설맥 가평현리점', category: '생맥주, 호프', address: '경기 가평군 조종면 현리' },
    { storeName: '효정명인갈비', category: '육류, 고기요리', address: '경기 가평군 설악면' },
  ]);
  const justPicked = useRef(false);

  useEffect(() => {
    getRecentStores().then((items) => {
      if (items && items.length > 0) {
        setRecentStores(items);
      }
    });
  }, []);

  // 상호명 2글자 이상 → 300ms 뒤 실제 매장 후보(같은 이름 여러 지점)를 보여줘서 사장님이 자기 지점을 직접 고르게 한다
  useEffect(() => {
    if (justPicked.current) {
      justPicked.current = false;
      return;
    }
    setSelected(null);
    setHint('');
    const q = storeName.trim();
    if (q.length < 2 || isLoading) {
      setCandidates([]);
      setIsSuggesting(false);
      return;
    }
    const controller = new AbortController();
    setIsSuggesting(true);
    const timer = setTimeout(async () => {
      const items = await searchStores(q, controller.signal);
      if (!controller.signal.aborted) {
        setCandidates(items);
        setIsSuggesting(false);
      }
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeName]);

  const pickCandidate = (c: StoreCandidate) => {
    justPicked.current = true;
    setStoreName(c.storeName);
    setSelected(c);
    setCandidates([]);
    setHint('');
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!storeName.trim() || isLoading) return;
    // 후보가 떠 있는데 고르지 않았으면 엉뚱한 지점이 진단되지 않도록 먼저 고르게 한다 (목록에 없으면 '입력한 이름으로 진단')
    if (!selected && candidates.length > 0) {
      setHint('아래 목록에서 사장님 매장(지점)을 선택해 주세요.');
      return;
    }
    startLoadingAnimation();
    onSearch(storeName.trim(), selected || undefined);
  };

  const diagnoseTypedName = () => {
    if (!storeName.trim() || isLoading) return;
    setCandidates([]);
    startLoadingAnimation();
    onSearch(storeName.trim());
  };

  const handleRecentStoreClick = (item: RecentStoreItem) => {
    justPicked.current = true;
    setStoreName(item.storeName);
    setCandidates([]);
    startLoadingAnimation();
    onSearch(item.storeName, {
      storeName: item.storeName,
      address: item.address,
      roadAddress: item.address,
      category: item.category,
    });
  };

  const startLoadingAnimation = () => {
    setLoadingStep(1);
    setTimeout(() => setLoadingStep(2), 500);
    setTimeout(() => setLoadingStep(3), 1100);
  };

  return (
    <div className="px-5 pt-8 pb-12 flex flex-col items-center text-center">
      {/* 배지 */}
      <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#EEF3F9] border border-[#D0DCEB] text-[#0F2744] text-xs font-semibold mb-5 shadow-xs">
        <span className="w-2 h-2 rounded-full bg-[#0F2744] animate-pulse" />
        <span>1분 매장 정밀 진단 & AI 쇼츠 기획</span>
      </div>

      {/* 헤드라인 */}
      <h1 className="text-[25px] sm:text-[28px] font-extrabold text-slate-900 leading-[1.35] tracking-tight mb-4">
        가게 이름만 넣으면,<br />
        <span className="text-[#0F2744] underline decoration-[#A4C2E8] decoration-wavy underline-offset-4">
          손님이 꼽은 필살기
        </span>와<br />
        쇼츠 기획을 1분 만에 뽑아드려요
      </h1>

      <p className="text-[14px] text-slate-600 leading-relaxed max-w-sm mb-7">
        네이버 플레이스 리뷰와 메뉴판을 실시간으로 분석해<br />
        손님을 끌어당길 9:16 숏폼 영상 기획을 바로 확인하세요.
      </p>

      {/* 검색 카드 */}
      <div className="w-full bg-white rounded-3xl p-5 shadow-lg shadow-slate-200/60 border border-slate-100 mb-6 text-left">
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <label className="text-xs font-bold text-slate-700 block">
            매장 상호명 입력
          </label>
          <div className="relative">
            <input
              type="text"
              value={storeName}
              onChange={(e) => setStoreName(e.target.value)}
              placeholder="예: 설맥 가평현리점"
              disabled={isLoading}
              className="w-full h-14 pl-4 pr-12 rounded-2xl bg-slate-50 border border-slate-200 focus:border-[#0F2744] focus:bg-white focus:outline-none text-[16px] text-slate-900 placeholder-slate-400 font-medium transition-all"
            />
            {storeName && !isLoading && (
              <button
                type="button"
                onClick={() => setStoreName('')}
                className="absolute right-3.5 top-1/2 -translate-y-1/2 w-6 h-6 rounded-full bg-slate-200 hover:bg-slate-300 flex items-center justify-center text-slate-600 text-xs transition-colors"
              >
                ✕
              </button>
            )}
          </div>

          {/* 선택한 지점 */}
          {selected && !isLoading && (
            <div className="flex items-start gap-2 p-3 rounded-2xl bg-[#EEF3F9] border border-[#D0DCEB] text-left">
              <span className="text-emerald-600 text-sm mt-0.5">✓</span>
              <div className="min-w-0">
                <div className="text-[13px] font-bold text-[#0F2744] truncate">{selected.storeName}</div>
                <div className="text-[11px] text-slate-500 truncate">{selected.roadAddress || selected.address}</div>
              </div>
            </div>
          )}

          {/* 실시간 매장 후보 (같은 이름의 다른 지점이 섞이지 않게 직접 선택) */}
          {!selected && !isLoading && (candidates.length > 0 || isSuggesting) && (
            <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden text-left">
              {isSuggesting && candidates.length === 0 && (
                <div className="px-4 py-3 text-xs text-slate-400">매장을 찾는 중...</div>
              )}
              {candidates.map((c) => (
                <button
                  key={`${c.storeName}|${c.roadAddress || c.address}`}
                  type="button"
                  onClick={() => pickCandidate(c)}
                  className="w-full px-4 py-3 text-left border-b last:border-b-0 border-slate-100 hover:bg-slate-50 active:bg-slate-100 transition-colors"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-[14px] font-bold text-slate-900 truncate">{c.storeName}</span>
                    {c.category && (
                      <span className="shrink-0 text-[10px] font-semibold text-[#0F2744] bg-[#EEF3F9] px-1.5 py-0.5 rounded-full">
                        {c.category.split('>').pop()?.trim()}
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-slate-500 mt-0.5 truncate">{c.roadAddress || c.address}</div>
                </button>
              ))}
              {candidates.length > 0 && (
                <button
                  type="button"
                  onClick={diagnoseTypedName}
                  className="w-full px-4 py-2.5 text-left text-[12px] font-semibold text-slate-500 hover:bg-slate-50 bg-slate-50/60"
                >
                  찾는 매장이 없어요 · 입력한 이름으로 진단
                </button>
              )}
            </div>
          )}

          {hint && <p className="text-xs font-semibold text-red-500">⚠️ {hint}</p>}

          <button
            type="submit"
            disabled={!storeName.trim() || isLoading}
            className={`w-full h-14 rounded-2xl font-bold text-[16px] flex items-center justify-center gap-2 shadow-md transition-all active:scale-[0.98] ${
              !storeName.trim() || isLoading
                ? 'bg-slate-200 text-slate-400 cursor-not-allowed shadow-none'
                : 'bg-gradient-to-r from-[#0F2744] to-[#1E3A5F] hover:from-[#1E3A5F] hover:to-[#2A4D7A] text-white shadow-[#0F2744]/20'
            }`}
          >
            {isLoading ? (
              <div className="flex items-center gap-2">
                <svg className="animate-spin h-5 w-5 text-white" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
                </svg>
                <span>매장 리뷰 분석 중...</span>
              </div>
            ) : (
              <>
                <span>🔍 우리 매장 정밀 진단하기</span>
              </>
            )}
          </button>
        </form>

        {/* 로딩 진행 단계 표시 */}
        {isLoading && (
          <div className="mt-5 pt-4 border-t border-slate-100">
            <div className="flex items-center justify-between text-xs font-semibold text-slate-500 mb-2">
              <span>실시간 데이터 수집 & AI 진단</span>
              <span className="text-[#0F2744] font-bold">진행 중...</span>
            </div>
            <div className="w-full bg-slate-100 h-2 rounded-full overflow-hidden mb-3">
              <div
                className="h-full bg-gradient-to-r from-[#1E3A5F] to-[#0F2744] transition-all duration-700 ease-out"
                style={{ width: loadingStep === 1 ? '35%' : loadingStep === 2 ? '75%' : '95%' }}
              />
            </div>
            <ul className="text-xs text-slate-600 space-y-1.5 font-medium">
              <li className="flex items-center gap-2">
                <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] ${loadingStep >= 1 ? 'bg-green-100 text-green-700' : 'bg-slate-200 text-slate-500'}`}>
                  ✓
                </span>
                <span>네이버 플레이스 공식 메뉴판 수집</span>
              </li>
              <li className="flex items-center gap-2">
                <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] ${loadingStep >= 2 ? 'bg-green-100 text-green-700' : 'bg-slate-200 text-slate-500'}`}>
                  ✓
                </span>
                <span>방문자·블로그 리뷰 칭찬 키워드 자연어 분석</span>
              </li>
              <li className="flex items-center gap-2">
                <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] ${loadingStep >= 3 ? 'bg-[#EEF3F9] text-[#0F2744]' : 'bg-slate-200 text-slate-500'}`}>
                  {loadingStep >= 3 ? '⚡' : '•'}
                </span>
                <span>매장 맞춤 3초 훅(Hook) 쇼츠 기획서 생성</span>
              </li>
            </ul>
          </div>
        )}
      </div>

      {/* 최근 진단된 매장 세로 리스트 카드 */}
      <div className="w-full text-left">
        <div className="flex items-center justify-between mb-2.5 px-0.5">
          <h3 className="text-xs font-bold text-slate-500 tracking-tight flex items-center gap-1.5">
            <span>⏱️</span>
            <span>최근 진단된 매장</span>
          </h3>
          <span className="text-[11px] text-slate-400 font-medium">클릭 시 바로 진단</span>
        </div>

        <div className="bg-white rounded-2xl border border-slate-200/80 shadow-2xs divide-y divide-slate-100 overflow-hidden">
          {recentStores.slice(0, 6).map((item, idx) => (
            <button
              key={`${item.storeName}_${idx}`}
              type="button"
              onClick={() => handleRecentStoreClick(item)}
              disabled={isLoading}
              className="w-full px-4 py-3 flex items-center justify-between gap-3 text-left hover:bg-slate-50 active:bg-slate-100/80 transition-colors group cursor-pointer disabled:opacity-50"
            >
              <div className="flex items-center gap-3 min-w-0">
                <span className="w-5 h-5 rounded-full bg-slate-100 group-hover:bg-[#EEF3F9] group-hover:text-[#0F2744] text-slate-500 font-bold text-[11px] flex items-center justify-center shrink-0 transition-colors">
                  {idx + 1}
                </span>
                <div className="min-w-0">
                  <div className="text-[13px] font-bold text-slate-800 group-hover:text-[#0F2744] truncate transition-colors">
                    {item.storeName}
                  </div>
                  <div className="text-[11px] text-slate-400 truncate mt-0.5">
                    {item.category || '일반음식점'} {item.address ? `· ${item.address}` : ''}
                  </div>
                </div>
              </div>
              <span className="text-slate-300 group-hover:text-[#0F2744] group-hover:translate-x-0.5 text-xs font-bold transition-all shrink-0">
                ›
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* 하단 안심 안내 문구 */}
      <div className="mt-10 p-4 rounded-2xl bg-slate-100/70 text-slate-500 text-xs leading-relaxed max-w-sm">
        🔒 별도의 회원가입 없이 누구나 즉시 매장 정밀 진단 리포트를 확인하실 수 있습니다.
      </div>
    </div>
  );
};
