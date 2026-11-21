import { useState } from 'react';
import type { FC, FormEvent } from 'react';

interface SearchScreenProps {
  onSearch: (storeName: string) => void;
  isLoading: boolean;
}

const POPULAR_EXAMPLES = [
  '효정명인갈비',
  '설맥 가평현리점',
  '제주 돈사돈 본점',
  '어니언 안국',
];

export const SearchScreen: FC<SearchScreenProps> = ({ onSearch, isLoading }) => {
  const [storeName, setStoreName] = useState('');
  const [loadingStep, setLoadingStep] = useState(0);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!storeName.trim() || isLoading) return;
    startLoadingAnimation();
    onSearch(storeName.trim());
  };

  const handleChipClick = (example: string) => {
    setStoreName(example);
    startLoadingAnimation();
    onSearch(example);
  };

  const startLoadingAnimation = () => {
    setLoadingStep(1);
    setTimeout(() => setLoadingStep(2), 500);
    setTimeout(() => setLoadingStep(3), 1100);
  };

  return (
    <div className="px-5 pt-8 pb-12 flex flex-col items-center text-center">
      {/* 배지 */}
      <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-red-50 border border-red-100 text-[#C92A2A] text-xs font-semibold mb-5 shadow-xs">
        <span className="w-2 h-2 rounded-full bg-[#E03131] animate-pulse" />
        <span>1분 매장 정밀 진단 & AI 쇼츠 기획</span>
      </div>

      {/* 헤드라인 */}
      <h1 className="text-[25px] sm:text-[28px] font-extrabold text-slate-900 leading-[1.35] tracking-tight mb-4">
        가게 이름만 넣으면,<br />
        <span className="text-[#C92A2A] underline decoration-red-200 decoration-wavy underline-offset-4">
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
              className="w-full h-14 pl-4 pr-12 rounded-2xl bg-slate-50 border border-slate-200 focus:border-[#E03131] focus:bg-white focus:outline-none text-[16px] text-slate-900 placeholder-slate-400 font-medium transition-all"
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

          <button
            type="submit"
            disabled={!storeName.trim() || isLoading}
            className={`w-full h-14 rounded-2xl font-bold text-[16px] flex items-center justify-center gap-2 shadow-md transition-all active:scale-[0.98] ${
              !storeName.trim() || isLoading
                ? 'bg-slate-200 text-slate-400 cursor-not-allowed shadow-none'
                : 'bg-gradient-to-r from-[#E03131] to-[#C92A2A] hover:from-[#C92A2A] hover:to-[#B02525] text-white shadow-red-500/25'
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
                <span>🔍 무료 매장 진단하기</span>
              </>
            )}
          </button>
        </form>

        {/* 로딩 진행 단계 표시 */}
        {isLoading && (
          <div className="mt-5 pt-4 border-t border-slate-100">
            <div className="flex items-center justify-between text-xs font-semibold text-slate-500 mb-2">
              <span>실시간 데이터 수집 & AI 진단</span>
              <span className="text-[#C92A2A]">진행 중...</span>
            </div>
            <div className="w-full bg-slate-100 h-2 rounded-full overflow-hidden mb-3">
              <div
                className="h-full bg-gradient-to-r from-[#FA5252] to-[#E03131] transition-all duration-700 ease-out"
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
                <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] ${loadingStep >= 3 ? 'bg-red-100 text-[#C92A2A]' : 'bg-slate-200 text-slate-500'}`}>
                  {loadingStep >= 3 ? '⚡' : '•'}
                </span>
                <span>매장 맞춤 3초 훅(Hook) 쇼츠 기획서 생성</span>
              </li>
            </ul>
          </div>
        )}
      </div>

      {/* 추천 빠른 검색 칩 */}
      <div className="w-full text-left">
        <p className="text-xs font-semibold text-slate-400 mb-2.5">
          지금 사장님들이 많이 진단해 본 매장
        </p>
        <div className="flex flex-wrap gap-2">
          {POPULAR_EXAMPLES.map((example) => (
            <button
              key={example}
              onClick={() => handleChipClick(example)}
              disabled={isLoading}
              className="px-3 py-1.5 rounded-full bg-white border border-slate-200 hover:border-[#E03131] hover:text-[#C92A2A] text-xs font-medium text-slate-600 shadow-2xs transition-all active:scale-95"
            >
              #{example}
            </button>
          ))}
        </div>
      </div>

      {/* 하단 안심 안내 문구 */}
      <div className="mt-10 p-4 rounded-2xl bg-slate-100/70 text-slate-500 text-xs leading-relaxed max-w-sm">
        🔒 별도의 회원가입 없이 누구나 무료로 매장 진단 리포트를 받아보실 수 있습니다.
      </div>
    </div>
  );
};
