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
  const shortsCount = result.scoreCard.shortsCount || 0;
  const currentMenu = planOptions.selectedMenu || result.aiPlan.recommendedMenu;
  const isDefaultMenu = !planOptions.selectedMenu || planOptions.selectedMenu === result.aiPlan.recommendedMenu;

  // 주인공 메뉴 변경 시 3초 훅(Hook) 카피 안의 메뉴명 실시간 동기화 (주류/음료 자연스러움 분기)
  const dynamicHookCopy = (() => {
    if (isDefaultMenu) {
      return result.aiPlan.hookCopy;
    }
    const defaultMenu = result.aiPlan.recommendedMenu;
    if (defaultMenu && result.aiPlan.hookCopy.includes(defaultMenu)) {
      return result.aiPlan.hookCopy.split(defaultMenu).join(currentMenu);
    }
    const text = `${currentMenu} ${result.category}`.toLowerCase();
    if (/(맥주|생맥|눈꽃맥주|얼음맥주|하이볼|소주|막걸리|와인|사케|칵테일|호프|주점|펍|포차|이자카야)/.test(text)) {
      return `${result.storeName} 오시면 첫 잔은 무조건 이거죠! 한 모금에 속이 뻥 뚫리는 ${currentMenu} 실물 영접 🍺`;
    }
    if (/(빙수|눈꽃빙수|팥빙수|아이스크림|파르페|스무디|에이드|주스|커피|라떼|아메리카노|카페|디저트)/.test(text)) {
      return `${result.storeName} 가면 이건 무조건 맛봐야죠! 시원하게 입맛 당기는 ${currentMenu} 실물 영접 🍧`;
    }
    if (/(갈비|삼겹살|목살|소고기|한우|곱창|막창|구이|불고기|스테이크|숯불)/.test(text)) {
      return `${result.storeName} 가면 이건 무조건 시켜야죠! 윤기 좔좔 흐르는 ${currentMenu} 실물 영접 🔥`;
    }
    if (/(찌개|탕|국밥|전골|라면|우동|짬뽕)/.test(text)) {
      return `${result.storeName} 가면 이건 무조건 시켜야죠! 속까지 뜨끈하게 확 풀리는 ${currentMenu} 실물 영접 🍲`;
    }
    return `${result.storeName} 가면 이건 무조건 시켜야죠! 손님들 극찬 쏟아지는 ${currentMenu} 실물 영접 🔥`;
  })();

  return (
    <div className="px-4 py-6 space-y-6">
      {/* 매장 헤더 카드 */}
      <div className="bg-white rounded-3xl p-5 shadow-sm border border-slate-100 relative overflow-hidden">
        <div className="flex items-start justify-between gap-3 mb-2">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[11px] font-bold px-2 py-0.5 rounded-full bg-[#EEF3F9] text-[#0F2744] border border-[#D0DCEB]">
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

      {/* 1. 손님이 기억하는 우리 가게 필살기 TOP 3 (모바일 2단 분기 줄바꿈 방지) */}
      <div className="space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1 px-1">
          <h3 className="text-[17px] font-extrabold text-slate-900 tracking-tight flex items-center gap-1.5 whitespace-nowrap">
            <span>손님이 기억하는 필살기</span>
            <span className="text-[#0F2744]">TOP 3</span>
          </h3>
          <span className="text-xs text-slate-400 font-medium whitespace-nowrap">네이버 리뷰 빅데이터 요약</span>
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
                      ? 'bg-gradient-to-tr from-[#0F2744] to-[#1E3A5F] text-white'
                      : item.rank === 2
                      ? 'bg-slate-700 text-white'
                      : 'bg-slate-500 text-white'
                  }`}
                >
                  {item.rank}
                </div>
                <div className="flex-1">
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <h4 className="text-[15px] font-bold text-slate-900 leading-snug">
                      {item.title}
                    </h4>
                    <span className="text-[11px] font-bold text-[#0F2744] bg-[#EEF3F9] px-2 py-0.5 rounded-full shrink-0">
                      {item.mentionRate}
                    </span>
                  </div>
                  <p className="text-xs text-slate-600 leading-relaxed mb-2">
                    {item.description}
                  </p>
                  <div className="bg-slate-50 rounded-xl p-2.5 text-[11px] text-slate-500 italic border-l-2 border-[#0F2744]">
                    {item.sampleReview}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 와 본 손님 vs 아직 안 와 본 손님 브릿지 카드 */}
      <div className="bg-gradient-to-br from-[#0F2744] to-[#1E3A5F] text-white rounded-3xl p-5 shadow-md">
        <div className="flex items-center gap-1.5 mb-2">
          <span className="text-[11px] font-bold px-2.5 py-0.5 rounded-full bg-white/15 text-white/90 border border-white/20">
            손님 심리 분석
          </span>
        </div>
        <h4 className="text-[18px] font-black leading-snug mb-3.5">
          그런데 이 필살기,<br />
          <span className="text-amber-300">와 본 손님</span>만 알고 있습니다
        </h4>

        <div className="grid grid-cols-2 gap-2.5 mb-3.5 text-xs">
          <div className="bg-white/10 rounded-2xl p-3 border border-white/10">
            <div className="font-bold text-emerald-300 flex items-center gap-1 mb-1">
              <span>✅</span> 와 본 손님
            </div>
            <p className="text-[11px] text-white/80 leading-relaxed">
              리뷰에 칭찬 가득,<br />
              "진작 올 걸" 단골 확정
            </p>
          </div>
          <div className="bg-white/10 rounded-2xl p-3 border border-white/10">
            <div className="font-bold text-amber-300 flex items-center gap-1 mb-1">
              <span>❓</span> 아직 안 와 본 손님
            </div>
            <p className="text-[11px] text-white/80 leading-relaxed">
              간판·별점만 대충 보고<br />
              그냥 스쳐 지나침
            </p>
          </div>
        </div>

        <div className="bg-white rounded-2xl p-3 text-center text-[#0F2744] font-extrabold text-xs shadow-xs leading-relaxed">
          💬 텍스트 리뷰 수십 개를 읽는 것보다<br />
          <strong className="text-[#C92A2A]">3초 쇼츠 한 편</strong>이 훨씬 빠릅니다
        </div>
      </div>

      {/* 2. 우리 가게 홍보 성적표 */}
      <div className="bg-white rounded-3xl p-5 shadow-sm border border-slate-100">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-[17px] font-extrabold text-slate-900 tracking-tight">
            우리 가게 홍보 성적표
          </h3>
          {/* shortsCount 여부에 따른 조건부 배지 논리 수정 */}
          {shortsCount === 0 ? (
            <span className="text-xs font-bold text-amber-700 bg-amber-50 px-2.5 py-0.5 rounded-full border border-amber-200 whitespace-nowrap">
              🚨 긴급 점검 필요
            </span>
          ) : (
            <span className="text-xs font-bold text-[#0F2744] bg-[#EEF3F9] px-2.5 py-0.5 rounded-full border border-[#D0DCEB] whitespace-nowrap">
              💡 릴스·클립 확장 추천
            </span>
          )}
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

          {/* 홍보 쇼츠 영상 - 유튜브 실제 검색 연동 결과 */}
          <div
            className={`p-4 rounded-2xl border transition-all ${
              shortsCount === 0
                ? 'bg-red-50/70 border-red-200'
                : 'bg-emerald-50/60 border-emerald-200'
            }`}
          >
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2.5">
                <span className="text-lg">🎬</span>
                <div>
                  <div className="text-xs font-extrabold text-slate-900">유튜브 홍보 영상</div>
                  <div className="text-[11px] text-slate-500">YouTube 쇼츠 & 롱폼 실시간 검색</div>
                </div>
              </div>
              {shortsCount === 0 ? (
                <span className="text-xs font-black text-[#C92A2A] bg-white px-2.5 py-1 rounded-full border border-red-200 shadow-2xs">
                  🚨 유튜브 쇼츠 0편 (비어 있음!)
                </span>
              ) : (
                <span className="text-xs font-bold text-emerald-700 bg-white px-2.5 py-1 rounded-full border border-emerald-200 shadow-2xs">
                  ✅ 유튜브 영상 {shortsCount}편 확인됨
                </span>
              )}
            </div>

            {shortsCount === 0 ? (
              <p className="text-xs text-red-900 leading-relaxed font-medium mt-1">
                딱 하나, <strong className="font-extrabold underline decoration-red-400">여기가 비어 있어요!</strong><br />
                아직 유튜브에 홍보 쇼츠가 한 편도 없습니다. 경쟁 매장이 선점하기 전에 첫 영상을 확보해야 합니다.
              </p>
            ) : (
              <div className="mt-1 space-y-1.5">
                <p className="text-xs text-emerald-900 leading-relaxed font-medium">
                  기존 영상이 확인되지만, 알고리즘 유입을 유지하려면 신메뉴·계절별 새 쇼츠를 꾸준히 이어가야 합니다.
                </p>
                {result.scoreCard.sampleVideoTitle && (
                  <div className="p-2 rounded-xl bg-white/80 border border-emerald-100 text-[11px] text-emerald-800 font-medium truncate flex items-center gap-1.5">
                    <span className="shrink-0 text-emerald-600 font-bold">▶ 최근 영상:</span>
                    <span className="truncate">{result.scoreCard.sampleVideoTitle}</span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* 인스타 릴스 · 네이버 클립 · 틱톡 정직한 안내 */}
          <div className="p-3.5 rounded-2xl bg-slate-50 border border-slate-200/80 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2.5">
              <span className="text-lg">📱</span>
              <div>
                <div className="text-xs font-bold text-slate-800">인스타 릴스 · 네이버 클립 · 틱톡</div>
                <div className="text-[11px] text-slate-500">유튜브 외 3대 숏폼 채널 동시 진출</div>
              </div>
            </div>
            <span className="text-xs font-bold text-[#0F2744] bg-[#EEF3F9] px-2.5 py-1 rounded-full border border-[#D0DCEB] shrink-0">
              💡 동시 확산 필요
            </span>
          </div>
        </div>
      </div>

      {/* 3. AI 기획 노트 & [숏폼 기획 수정하기 >] */}
      <div className="bg-gradient-to-br from-white to-slate-50 rounded-3xl p-5 shadow-sm border border-slate-200/80">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-[#0F2744]" />
            <h3 className="text-[17px] font-extrabold text-slate-900 tracking-tight">
              AI 쇼츠 기획 노트
            </h3>
          </div>
          <button
            onClick={onOpenBottomSheet}
            className="text-xs font-bold text-[#0F2744] hover:text-[#1E3A5F] flex items-center gap-0.5 group"
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
                {currentMenu}
              </span>
              {isDefaultMenu ? (
                <span className="text-[11px] font-bold text-[#0F2744] bg-[#EEF3F9] border border-[#D0DCEB] px-2 py-0.5 rounded-full">
                  AI 추천
                </span>
              ) : (
                <span className="text-[11px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full">
                  직접 선택
                </span>
              )}
            </div>
          </div>

          <div className="bg-white rounded-2xl p-4 border border-slate-100 shadow-xs">
            <div className="text-[11px] font-bold text-slate-400 mb-1">
              시선을 훔치는 첫 3초 훅(Hook) 카피
            </div>
            <div className="text-[14px] font-bold text-slate-800 leading-relaxed">
              “{dynamicHookCopy}”
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

      {/* 4. 실제 완성 쇼츠 미리보기 (9:16 세로 카드) */}
      <div className="bg-white rounded-3xl p-5 shadow-sm border border-slate-100">
        <div className="mb-3">
          <div className="flex items-center gap-1.5 mb-1">
            <span className="text-[11px] font-bold text-[#0F2744] bg-[#EEF3F9] border border-[#D0DCEB] px-2 py-0.5 rounded-full">
              실제 제작 사례
            </span>
          </div>
          <h3 className="text-[17px] font-extrabold text-slate-900 tracking-tight">
            샵스토리 완성 영상은 이 정도 퀄리티로 나옵니다
          </h3>
          <p className="text-xs text-slate-500 mt-1">
            별도 촬영 방문 없이, 사장님 매장 사진과 리뷰만으로 제작된 실제 숏폼입니다.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3 pt-2">
          <div className="space-y-1.5">
            <div className="w-full aspect-[9/16] rounded-2xl overflow-hidden bg-slate-900 shadow-md border border-slate-200">
              <iframe
                src="https://www.youtube.com/embed/6G1JpEvY2iw?playsinline=1&rel=0&modestbranding=1"
                title="샵스토리 제작 쇼츠 샘플 1"
                loading="lazy"
                className="w-full h-full border-0"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowFullScreen
              />
            </div>
            <p className="text-[11px] font-bold text-slate-700 text-center truncate">
              🎬 효정명인갈비 쇼츠 ①
            </p>
          </div>
          <div className="space-y-1.5">
            <div className="w-full aspect-[9/16] rounded-2xl overflow-hidden bg-slate-900 shadow-md border border-slate-200">
              <iframe
                src="https://www.youtube.com/embed/U8FELXcHHIg?playsinline=1&rel=0&modestbranding=1"
                title="샵스토리 제작 쇼츠 샘플 2"
                loading="lazy"
                className="w-full h-full border-0"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowFullScreen
              />
            </div>
            <p className="text-[11px] font-bold text-slate-700 text-center truncate">
              🎬 효정명인갈비 쇼츠 ②
            </p>
          </div>
        </div>
      </div>

      {/* 5. 제작 사양 & 안심 보장 카드 */}
      <div className="bg-white rounded-3xl p-5 shadow-sm border border-slate-100 space-y-4">
        <div className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-[#0F2744]" />
          <h3 className="text-[17px] font-extrabold text-slate-900 tracking-tight">
            제작 사양 & 안심 보장
          </h3>
        </div>

        {/* 제작 사양 2x2 그리드 */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="p-3 rounded-2xl bg-slate-50 border border-slate-100">
            <div className="text-slate-400 text-[10px] font-semibold mb-0.5">화질 규격</div>
            <div className="font-extrabold text-slate-800 text-[13px]">🎬 풀HD (1080×1920)</div>
          </div>
          <div className="p-3 rounded-2xl bg-slate-50 border border-slate-100">
            <div className="text-slate-400 text-[10px] font-semibold mb-0.5">화면 비율</div>
            <div className="font-extrabold text-slate-800 text-[13px]">📱 9:16 세로형 쇼츠</div>
          </div>
          <div className="p-3 rounded-2xl bg-slate-50 border border-slate-100">
            <div className="text-slate-400 text-[10px] font-semibold mb-0.5">배포 채널</div>
            <div className="font-extrabold text-slate-800 text-[13px]">🚀 4대 채널 패키지</div>
            <div className="text-[10px] text-slate-500">유튜브·릴스·클립·틱톡</div>
          </div>
          <div className="p-3 rounded-2xl bg-slate-50 border border-slate-100">
            <div className="text-slate-400 text-[10px] font-semibold mb-0.5">매장 유입</div>
            <div className="font-extrabold text-slate-800 text-[13px]">📍 네이버 플레이스 연동</div>
            <div className="text-[10px] text-slate-500">엔딩 검색창 그래픽</div>
          </div>
        </div>

        {/* 안심 보장 3줄 */}
        <div className="p-4 rounded-2xl bg-[#EEF3F9] border border-[#D0DCEB] text-xs text-slate-700 space-y-2.5">
          <div className="font-bold text-[#0F2744] flex items-center gap-1.5 text-[13px]">
            <span>🛡️</span> 샵스토리의 3대 안심 보장
          </div>
          <ul className="space-y-2 text-[12px] leading-relaxed">
            <li className="flex items-start gap-2">
              <span className="text-[#0F2744] font-bold shrink-0">✓</span>
              <span><strong>추가 촬영 불필요:</strong> 사장님이 직접 촬영하실 필요 없이 기존 매장·리뷰 자료로 제작합니다.</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-[#0F2744] font-bold shrink-0">✓</span>
              <span><strong>7일 책임 수정 보장:</strong> 영상 전달 후 7일간 자막·목소리·배경음악 수정을 책임지고 지원합니다.</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-[#0F2744] font-bold shrink-0">✓</span>
              <span><strong>원스톱 업로드 패키지:</strong> 매장 계정 자산으로 바로 올릴 수 있는 제목·해시태그·장소 태그까지 함께 제공합니다.</span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
};
