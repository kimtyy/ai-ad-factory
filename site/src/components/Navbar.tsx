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
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-[#0F2744] to-[#1E3A5F] flex items-center justify-center text-white font-black text-sm shadow-xs group-hover:scale-105 transition-transform">
            S
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-extrabold text-[16px] tracking-tight text-slate-900">
                샵스토리
              </span>
              <span className="text-[10px] font-bold text-[#0F2744] bg-[#EEF3F9] border border-[#D0DCEB] px-1.5 py-0.5 rounded-full">
                무료 진단
              </span>
            </div>
          </div>
        </button>
      </div>
    </header>
  );
};
