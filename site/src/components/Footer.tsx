import type { FC } from 'react';

export const Footer: FC = () => {
  return (
    <footer className="w-full px-5 py-8 text-center text-[11px] text-slate-400 space-y-2.5 border-t border-slate-200/70 mt-auto leading-relaxed">
      <div className="font-bold text-slate-600 text-xs tracking-tight">
        샵스토리 (ShopStory)
      </div>
      <p className="text-slate-500 max-w-xs mx-auto">
        AI 기반 소상공인 매장 정밀 진단 및 숏폼 홍보 영상 자동 제작 솔루션
      </p>

      <div className="text-slate-400 pt-1 space-y-0.5 border-t border-slate-100">
        <div>상호: 허브스마트 | 대표: 김태영</div>
        <div>사업자등록번호: 526-04-00927 | 통신판매업신고: 제2026-경기가평-274호</div>
        <div>주소: 경기도 가평군 조종면 조종희망로 16-19, 1층</div>
        <div>
          이메일: <a href="mailto:kimtyy@naver.com" className="hover:text-slate-600">kimtyy@naver.com</a> | 고객센터: <a href="tel:031-5175-8887" className="hover:text-slate-600">031-5175-8887</a>
        </div>
      </div>

      <div className="pt-2 text-slate-400">
        <div className="flex items-center justify-center gap-2 font-medium text-slate-500 mb-1.5">
          <a href="/terms.html" className="hover:text-slate-800 underline underline-offset-2">
            이용약관
          </a>
          <span className="text-slate-300">|</span>
          <a href="/privacy.html" className="hover:text-slate-800 underline underline-offset-2">
            개인정보처리방침
          </a>
          <span className="text-slate-300">|</span>
          <a href="/admin" className="hover:text-slate-800 underline underline-offset-2">
            관리자
          </a>
        </div>
        <div>© 2026 허브스마트. All rights reserved.</div>
      </div>
    </footer>
  );
};
