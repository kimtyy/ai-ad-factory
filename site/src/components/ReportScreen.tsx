import type { FC } from 'react';
import { StoreDiagnosticResult, PlanOptions } from '../types';

interface ReportScreenProps {
  result: StoreDiagnosticResult;
  planOptions: PlanOptions;
  onOpenBottomSheet: () => void;
  onResetSearch: () => void;
}

export const ReportScreen: FC<ReportScreenProps> = ({
  result,
  planOptions,
  onOpenBottomSheet,
  onResetSearch,
}) => {
  return (
    <div className="px-4 py-6 space-y-6">
      {/* 매장 헤더 카드 */}
      <div className="bg-white rounded-3xl p-5 shadow-sm border border-slate-100 relative overflow-hidden">
        <div className="flex items-start justify-between gap-3 mb-2">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[11px] font-bold px-2 py-0.5 rounded-full bg-red-50 text-[#C92A2A] border border-red-100">
                {result.category}
              </span>
              <span className="text-[11px] text-slate-400 font-medium">진단 완료</span>
            </div>
            <h2 className="text-[22px] font-extrabold text-slate-900 tracking-tight leading-snug">
              {result.storeName}
            </h2>
          </div>
          <button
            onClick={onResetSearch}
            className="text-xs text-slate-400 hover:text-slate-600 underline font-medium shrink-0 pt-1"
          >
            다시 검색
          </button>
        </div>

        <p className="text-xs text-slate-500 mb-3 flex items-center gap-1">
          <svg className="w-3.5 h-3.5 text-slate-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          <span className="truncate">{result.address}</span>
        </p>

        <div className="p-3.5 rounded-2xl bg-slate-50 border border-slate-100 text-xs text-slate-700 leading-relaxed font-medium">
          💡 {result.summary}
        </div>
      </div>

      {/* 1. 손님이 기억하는 우리 가게 필살기 TOP 3 */}
      <div className="space-y-3">
        <div className="flex items-center justify-between px-1">
          <h3 className="text-[17px] font-extrabold text-slate-900 tracking-tight flex items-center gap-1.5">
            <span>손님이 기억하는 필살기</span>
            <span className="text-[#C92A2A]">TOP 3</span>
          </h3>
          <span className="text-xs text-slate-400 font-medium">네이버 리뷰 빅데이터 요약</span>
        </div>

        <div className="space-y-3">
          {result.topStrengths.map((item) => (
            <div
              key={item.rank}
              className="bg-white rounded-2xl p-4 shadow-xs border border-slate-100 hover:border-slate-200 transition-all"
            >
              <div className="flex items-start gap-3">
                <div
                  className={`w-7 h-7 rounded-xl flex items-center justify-center font-black text-sm shrink-0 shadow-2xs ${
                    item.rank === 1
                      ? 'bg-gradient-to-tr from-[#E03131] to-[#FA5252] text-white'
                      : item.rank === 2
                      ? 'bg-amber-500 text-white'
                      : 'bg-slate-700 text-white'
                  }`}
                >
                  {item.rank}
                </div>
                <div className="flex-1">
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <h4 className="text-[15px] font-bold text-slate-900 leading-snug">
                      {item.title}
                    </h4>
                    <span className="text-[11px] font-bold text-[#C92A2A] bg-red-50 px-2 py-0.5 rounded-full shrink-0">
                      {item.mentionRate}
                    </span>
                  </div>
                  <p className="text-xs text-slate-600 leading-relaxed mb-2">
                    {item.description}
                  </p>
                  <div className="bg-slate-50 rounded-xl p-2.5 text-[11px] text-slate-500 italic border-l-2 border-[#E03131]">
                    {item.sampleReview}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 2. 우리 가게 홍보 성적표 (결핍 찌르기) */}
      <div className="bg-white rounded-3xl p-5 shadow-sm border border-slate-100">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-[17px] font-extrabold text-slate-900 tracking-tight">
            우리 가게 홍보 성적표
          </h3>
          <span className="text-xs font-bold text-amber-600 bg-amber-50 px-2.5 py-0.5 rounded-full">
            긴급 점검 필요
          </span>
        </div>

        <div className="space-y-3">
          {/* 공식 메뉴판 */}
          <div className="flex items-center justify-between p-3.5 rounded-2xl bg-slate-50 border border-slate-100">
            <div className="flex items-center gap-2.5">
              <span className="text-lg">📋</span>
              <div>
                <div className="text-xs font-bold text-slate-800">공식 메뉴판 등록</div>
                <div className="text-[11px] text-slate-500">네이버 플레이스 연동</div>
              </div>
            </div>
            <span className="text-xs font-bold text-emerald-600 bg-emerald-50 px-2.5 py-1 rounded-full border border-emerald-100">
              ✅ {result.scoreCard.menuCount}개 메뉴 확인됨
            </span>
          </div>

          {/* 홍보용 사진 */}
          <div className="flex items-center justify-between p-3.5 rounded-2xl bg-slate-50 border border-slate-100">
            <div className="flex items-center gap-2.5">
              <span className="text-lg">📸</span>
              <div>
                <div className="text-xs font-bold text-slate-800">홍보용 매장/메뉴 사진</div>
                <div className="text-[11px] text-slate-500">블로그 & 방문자 등록 사진</div>
              </div>
            </div>
            <span className="text-xs font-bold text-emerald-600 bg-emerald-50 px-2.5 py-1 rounded-full border border-emerald-100">
              ✅ 확보 가능
            </span>
          </div>

          {/* 홍보 쇼츠 영상 - 결핍 찌르기 */}
          <div className="p-4 rounded-2xl bg-red-50/70 border border-red-200">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2.5">
                <span className="text-lg">🎬</span>
                <div>
                  <div className="text-xs font-extrabold text-slate-900">홍보 쇼츠 영상</div>
                  <div className="text-[11px] text-slate-500">유튜브 쇼츠 / 인스타 릴스</div>
                </div>
              </div>
              <span className="text-xs font-black text-[#C92A2A] bg-white px-2.5 py-1 rounded-full border border-red-200 shadow-2xs">
                🚨 0편 (비어 있음!)
              </span>
            </div>
            <p className="text-xs text-red-900 leading-relaxed font-medium mt-1">
              딱 하나, <strong className="font-extrabold underline decoration-red-400">여기가 비어 있어요!</strong><br />
              요즘 손님 82%는 쇼츠·릴스를 보고 첫 방문을 결정합니다. 경쟁 매장이 먼저 선점하기 전에 1편을 확보해 두셔야 합니다.
            </p>
          </div>
        </div>
      </div>

      {/* 3. AI 기획 노트 & [숏폼 기획 수정하기 >] */}
      <div className="bg-gradient-to-br from-white to-slate-50 rounded-3xl p-5 shadow-sm border border-slate-200/80">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-[#E03131]" />
            <h3 className="text-[17px] font-extrabold text-slate-900 tracking-tight">
              AI 쇼츠 기획 노트
            </h3>
          </div>
          <button
            onClick={onOpenBottomSheet}
            className="text-xs font-bold text-[#C92A2A] hover:text-[#B02525] flex items-center gap-0.5 group"
          >
            <span>기획 수정하기</span>
            <span className="group-hover:translate-x-0.5 transition-transform">&gt;</span>
          </button>
        </div>

        {/* 주인공 메뉴 & 카피 카드 */}
        <div className="space-y-3.5 mb-4">
          <div className="bg-white rounded-2xl p-4 border border-slate-100 shadow-xs">
            <div className="text-[11px] font-bold text-slate-400 mb-1">
              선택된 주인공 메뉴
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[16px] font-extrabold text-slate-900">
                {planOptions.selectedMenu || result.aiPlan.recommendedMenu}
              </span>
              <span className="text-[11px] font-bold text-[#C92A2A] bg-red-50 border border-red-100 px-2 py-0.5 rounded-full">
                AI 추천
              </span>
            </div>
          </div>

          <div className="bg-white rounded-2xl p-4 border border-slate-100 shadow-xs">
            <div className="text-[11px] font-bold text-slate-400 mb-1">
              시선을 훔치는 첫 3초 훅(Hook) 카피
            </div>
            <div className="text-[14px] font-bold text-slate-800 leading-relaxed">
              “{result.aiPlan.hookCopy}”
            </div>
          </div>
        </div>

        {/* 현재 세팅된 옵션 알약 요약 */}
        <div className="bg-white/80 rounded-2xl p-3 border border-slate-100 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-400 text-[11px] font-semibold">설정:</span>
          <span className="px-2.5 py-1 rounded-full bg-slate-100 text-slate-700 font-medium">
            🔤 {planOptions.font}
          </span>
          <span className="px-2.5 py-1 rounded-full bg-slate-100 text-slate-700 font-medium">
            🎙️ {planOptions.voice}
          </span>
          <span className="px-2.5 py-1 rounded-full bg-slate-100 text-slate-700 font-medium">
            🎵 {planOptions.bgm}
          </span>
        </div>

        {/* 바텀시트 열기 버튼 */}
        <button
          onClick={onOpenBottomSheet}
          className="w-full mt-4 h-12 rounded-xl bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 font-bold text-xs flex items-center justify-center gap-1.5 shadow-2xs transition-all active:scale-[0.99]"
        >
          <span>⚙️ 숏폼 기획 (메뉴·자막·목소리·BGM) 수정하기</span>
          <span className="text-slate-400">&gt;</span>
        </button>
      </div>
    </div>
  );
};
