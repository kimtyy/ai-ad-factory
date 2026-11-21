import type { FC } from 'react';

interface NavbarProps {
  onReset?: () => void;
}

export const Navbar: FC<NavbarProps> = ({ onReset }) => {
  return (
    <header className="sticky top-0 z-30 bg-white/95 backdrop-blur-md border-b border-slate-100">
      <div className="max-w-md mx-auto px-4 h-14 flex items-center justify-between">
        <button
          onClick={onReset}
          className="flex items-center gap-2 text-left focus:outline-none group"
        >
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-[#C92A2A] to-[#FA5252] flex items-center justify-center text-white font-black text-sm shadow-xs group-hover:scale-105 transition-transform">
            S
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-extrabold text-[16px] tracking-tight text-slate-900">
                샵스토리
              </span>
              <span className="text-[10px] font-bold text-[#C92A2A] bg-red-50 border border-red-100 px-1.5 py-0.5 rounded-full">
                무료 진단
              </span>
            </div>
          </div>
        </button>

        <a
          href="https://shopstory-store.streamlit.app"
          target="_blank"
          rel="noopener noreferrer"
          className="text-[12px] font-semibold text-slate-500 hover:text-slate-800 transition-colors flex items-center gap-1"
        >
          <span>제작 작업실</span>
          <svg className="w-3.5 h-3.5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
        </a>
      </div>
    </header>
  );
};
