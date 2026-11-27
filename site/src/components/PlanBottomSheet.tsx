import { useState } from 'react';
import type { FC } from 'react';
import { PlanOptions, MenuItem } from '../types';

interface PlanBottomSheetProps {
  isOpen: boolean;
  onClose: () => void;
  options: PlanOptions;
  onChangeOptions: (newOptions: PlanOptions) => void;
  menus: MenuItem[];
  recommendedMenu: string;
  businessType?: 'food' | 'service';
}

const FONT_OPTIONS = [
  '임팩트 고딕',
  '감성 명조 (AI 추천)',
  '둥근 예능 팝체',
  '단단한 붓글씨',
];

const VOICE_OPTIONS = [
  '밝은 여성 (AI 추천)',
  '차분한 남성',
  '나레이션 없음 (BGM만)',
];

const BGM_OPTIONS = [
  'AI 추천',
  '신나는',
  '따뜻한',
  '세련된',
];

// 단순 음료/공기밥/사리/부속 항목 필터링 (주인공 메뉴에서 제외)
const EXCLUDED_SIDE_ITEMS_REGEX = /^(생수|생수\s*\d+[a-zA-Z가-힣]*|콜라|코카콜라|사이다|칠성사이다|환타|웰치스|토닉워터|토닉워터\s*\([^\)]*\)|진로토닉|공기밥|공깃밥|햇반|레몬\s*슬라이스|음료수|캔음료|탄산음료|생과일쥬스|라면사리|우동사리|떡사리|당면사리|소면사리|치즈사리|수제비사리|사리추가|공기밥추가|소주|맥주|참이슬|처음처럼|진로|새로|카스|테라|켈리|막걸리)$/i;

const isExcludedSideItem = (name: string): boolean => {
  const clean = name.replace(/\s+/g, '').replace(/[(\[（].*?[)\]）]/g, '');
  return EXCLUDED_SIDE_ITEMS_REGEX.test(clean) || EXCLUDED_SIDE_ITEMS_REGEX.test(name.trim());
};

export const PlanBottomSheet: FC<PlanBottomSheetProps> = ({
  isOpen,
  onClose,
  options,
  onChangeOptions,
  menus,
  recommendedMenu,
  businessType = 'food',
}) => {
  const [showAllMenus, setShowAllMenus] = useState(false);
  const isFood = businessType !== 'service';

  if (!isOpen) return null;

  // 단순 음료/사리 제외한 메인 요리 중심 필터링 (후보가 없으면 전체 메뉴 허용)
  const validMenus = menus.filter((m) => !isExcludedSideItem(m.name));
  const menuPool = validMenus.length > 0 ? validMenus : menus;

  // AI 추천 2~3개 메뉴
  const topRecommendedMenus = menuPool.slice(0, 3).map((m) => m.name);
  if (!topRecommendedMenus.includes(recommendedMenu) && recommendedMenu && !isExcludedSideItem(recommendedMenu)) {
    topRecommendedMenus.unshift(recommendedMenu);
  }

  // 나머지 메뉴 목록
  const otherMenus = menuPool.filter((m) => !topRecommendedMenus.includes(m.name));

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center">
      {/* 백드롭 */}
      <div
        className="fixed inset-0 bg-slate-900/60 backdrop-blur-xs transition-opacity"
        onClick={onClose}
      />

      {/* 바텀시트 컨테이너 */}
      <div className="relative w-full max-w-md bg-white rounded-t-3xl shadow-2xl p-6 max-h-[85vh] overflow-y-auto animate-in slide-in-from-bottom duration-300">
        {/* 드래그 핸들 바 */}
        <div className="w-12 h-1.5 bg-slate-200 rounded-full mx-auto mb-4" />

        <div className="flex items-center justify-between mb-5">
          <div>
            <h3 className="text-[18px] font-extrabold text-slate-900">
              숏폼 영상 기획 수정
            </h3>
            <p className="text-xs text-slate-500">
              터치하여 사장님이 원하시는 영상 스타일을 선택하세요.
            </p>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-full bg-slate-100 hover:bg-slate-200 flex items-center justify-center text-slate-500 text-sm font-bold transition-colors"
          >
            ✕
          </button>
        </div>

        <div className="space-y-6">
          {/* 1. 주인공 메뉴 / 핵심 서비스 선택 */}
          <div>
            <label className="block text-xs font-bold text-slate-800 mb-2">
              {isFood ? '🍽️ 주인공 메뉴 (영상에 메인으로 등장할 요리)' : '📌 핵심 강조 서비스 (영상에 메인으로 소개될 서비스)'}
            </label>
            <div className="flex flex-wrap gap-2">
              {topRecommendedMenus.map((menu) => {
                const isSelected = options.selectedMenu === menu;
                return (
                  <button
                    key={menu}
                    onClick={() => onChangeOptions({ ...options, selectedMenu: menu })}
                    className={`px-3.5 py-2 rounded-2xl text-xs font-bold flex items-center gap-1.5 transition-all ${
                      isSelected
                        ? 'bg-[#0F2744] text-white shadow-sm shadow-[#0F2744]/20'
                        : 'bg-slate-100 hover:bg-slate-200 text-slate-700'
                    }`}
                  >
                    <span>{menu}</span>
                    <span
                      className={`text-[10px] px-1.5 py-0.2 rounded-full font-semibold ${
                        isSelected ? 'bg-white/20 text-white' : 'bg-[#EEF3F9] text-[#0F2744]'
                      }`}
                    >
                      AI 추천
                    </span>
                  </button>
                );
              })}

              {/* 더보기 버튼 */}
              {otherMenus.length > 0 && !showAllMenus && (
                <button
                  onClick={() => setShowAllMenus(true)}
                  className="px-3.5 py-2 rounded-2xl text-xs font-bold bg-white border border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors"
                >
                  + 더보기 ({otherMenus.length}개 {isFood ? '메뉴' : '항목'})
                </button>
              )}
            </div>

            {/* 펼쳐진 메뉴 목록 */}
            {showAllMenus && (
              <div className="mt-2.5 p-3 rounded-2xl bg-slate-50 border border-slate-100 flex flex-wrap gap-2 animate-in fade-in duration-200">
                {otherMenus.map((menu) => {
                  const isSelected = options.selectedMenu === menu.name;
                  return (
                    <button
                      key={menu.name}
                      onClick={() => onChangeOptions({ ...options, selectedMenu: menu.name })}
                      className={`px-3 py-1.5 rounded-xl text-xs font-medium transition-all ${
                        isSelected
                          ? 'bg-[#0F2744] text-white font-bold shadow-xs'
                          : 'bg-white border border-slate-200 text-slate-700 hover:bg-slate-100'
                      }`}
                    >
                      {menu.name}
                      {menu.price && <span className="opacity-75 text-[10px] ml-1">({menu.price})</span>}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* 2. 자막 폰트 */}
          <div>
            <label className="block text-xs font-bold text-slate-800 mb-2">
              🔤 자막 폰트 스타일
            </label>
            <div className="grid grid-cols-2 gap-2">
              {FONT_OPTIONS.map((font) => {
                const isSelected = options.font === font;
                return (
                  <button
                    key={font}
                    onClick={() => onChangeOptions({ ...options, font })}
                    className={`p-3 rounded-2xl text-xs text-left font-bold transition-all border ${
                      isSelected
                        ? 'border-[#0F2744] bg-[#EEF3F9]/70 text-[#0F2744]'
                        : 'border-slate-200 bg-white hover:bg-slate-50 text-slate-700'
                    }`}
                  >
                    {font}
                  </button>
                );
              })}
            </div>
          </div>

          {/* 3. 나레이션 목소리 */}
          <div>
            <label className="block text-xs font-bold text-slate-800 mb-2">
              🎙️ 나레이션 목소리
            </label>
            <div className="flex flex-col gap-2">
              {VOICE_OPTIONS.map((voice) => {
                const isSelected = options.voice === voice;
                return (
                  <button
                    key={voice}
                    onClick={() => onChangeOptions({ ...options, voice })}
                    className={`p-3 rounded-2xl text-xs text-left font-bold transition-all border flex items-center justify-between ${
                      isSelected
                        ? 'border-[#0F2744] bg-[#EEF3F9]/70 text-[#0F2744]'
                        : 'border-slate-200 bg-white hover:bg-slate-50 text-slate-700'
                    }`}
                  >
                    <span>{voice}</span>
                    {isSelected && <span className="text-[#0F2744] text-sm font-black">✓</span>}
                  </button>
                );
              })}
            </div>
          </div>

          {/* 4. 배경음악 */}
          <div>
            <label className="block text-xs font-bold text-slate-800 mb-2">
              🎵 배경음악 (BGM)
            </label>
            <div className="grid grid-cols-2 gap-2">
              {BGM_OPTIONS.map((bgm) => {
                const isSelected = options.bgm === bgm;
                return (
                  <button
                    key={bgm}
                    onClick={() => onChangeOptions({ ...options, bgm })}
                    className={`p-3 rounded-2xl text-xs text-center font-bold transition-all border ${
                      isSelected
                        ? 'border-[#0F2744] bg-[#EEF3F9]/70 text-[#0F2744]'
                        : 'border-slate-200 bg-white hover:bg-slate-50 text-slate-700'
                    }`}
                  >
                    {bgm}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* 5. 꼭 강조할 점 · 수정 요청 (선택, 비워 둬도 신청 가능) */}
        <div className="mt-6">
          <label className="block text-xs font-bold text-slate-800 mb-2">
            ✍️ 꼭 강조할 점 · 수정 요청 <span className="font-medium text-slate-400">(선택)</span>
          </label>
          <input
            type="text"
            value={options.ownerMemo || ''}
            maxLength={80}
            onChange={(e) => onChangeOptions({ ...options, ownerMemo: e.target.value })}
            placeholder="예: 단체석 30석 완비, 한우육회 메인 강조"
            className="w-full h-11 px-3.5 rounded-xl bg-white border border-slate-200 focus:border-[#0F2744] focus:outline-none text-sm font-medium text-slate-900 placeholder-slate-400 transition-all"
          />
        </div>

        {/* 확인 버튼 */}
        <div className="mt-6 pt-4 border-t border-slate-100">
          <button
            onClick={onClose}
            className="w-full h-13 py-3.5 rounded-2xl bg-[#0F2744] hover:bg-[#1E3A5F] text-white font-bold text-sm shadow-md transition-all active:scale-[0.98]"
          >
            기획 수정 완료 (적용하기)
          </button>
        </div>
      </div>
    </div>
  );
};
