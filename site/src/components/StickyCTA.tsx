import type { FC } from 'react';

interface StickyCTAProps {
  onOpenModal: () => void;
  selectedMenu: string;
}

export const StickyCTA: FC<StickyCTAProps> = ({ onOpenModal, selectedMenu }) => {
  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 p-4 bg-white/95 backdrop-blur-md border-t border-slate-100 shadow-lg">
      <div className="max-w-md mx-auto">
        <button
          onClick={onOpenModal}
          className="w-full h-14 rounded-2xl bg-gradient-to-r from-[#0F2744] to-[#1E3A5F] hover:from-[#1E3A5F] hover:to-[#2A4D7A] text-white font-extrabold text-[15px] sm:text-[16px] flex items-center justify-center gap-2 shadow-lg shadow-[#0F2744]/25 active:scale-[0.98] transition-all"
        >
          <span>🎬 이 기획으로 쇼츠 제작 신청하기</span>
        </button>
        <p className="text-[11px] text-center text-slate-400 mt-1.5 font-medium">
          선택된 주인공: <strong className="text-slate-600">{selectedMenu}</strong> · 시안 확인 후 진행 · 추가 촬영 불필요
        </p>
      </div>
    </div>
  );
};
