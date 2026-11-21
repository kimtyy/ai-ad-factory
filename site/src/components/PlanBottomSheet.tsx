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

export const PlanBottomSheet: FC<PlanBottomSheetProps> = ({
  isOpen,
  onClose,
  options,
  onChangeOptions,
  menus,
  recommendedMenu,
}) => {
  const [showAllMenus, setShowAllMenus] = useState(false);

  if (!isOpen) return null;

  // AI 추천 2~3개 메뉴
  const topRecommendedMenus = menus.slice(0, 3).map((m) => m.name);
  if (!topRecommendedMenus.includes(recommendedMenu) && recommendedMenu) {
    topRecommendedMenus.unshift(recommendedMenu);
  }

  // 나머지 메뉴 목록
  const otherMenus = menus.filter((m) => !topRecommendedMenus.includes(m.name));

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
          {/* 1. 주인공 메뉴 선택 */}
          <div>
            <label className="block text-xs font-bold text-slate-800 mb-2">
              🍽️ 주인공 메뉴 (영상에 메인으로 등장할 요리)
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
                        ? 'bg-[#E03131] text-white shadow-sm shadow-red-500/30'
                        : 'bg-slate-100 hover:bg-slate-200 text-slate-700'
                    }`}
                  >
                    <span>{menu}</span>
                    <span
                      className={`text-[10px] px-1.5 py-0.2 rounded-full font-semibold ${
                        isSelected ? 'bg-white/20 text-white' : 'bg-red-50 text-[#C92A2A]'
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
                  + 더보기 ({otherMenus.length}개 메뉴)
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
                          ? 'bg-[#E03131] text-white font-bold shadow-xs'
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
                        ? 'border-[#E03131] bg-red-50/50 text-[#C92A2A]'
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
                        ? 'border-[#E03131] bg-red-50/50 text-[#C92A2A]'
                        : 'border-slate-200 bg-white hover:bg-slate-50 text-slate-700'
                    }`}
                  >
                    <span>{voice}</span>
                    {isSelected && <span className="text-[#C92A2A] text-sm font-black">✓</span>}
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
                        ? 'border-[#E03131] bg-red-50/50 text-[#C92A2A]'
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

        {/* 확인 버튼 */}
        <div className="mt-8 pt-4 border-t border-slate-100">
          <button
            onClick={onClose}
            className="w-full h-13 py-3.5 rounded-2xl bg-slate-900 hover:bg-black text-white font-bold text-sm shadow-md transition-all active:scale-[0.98]"
          >
            기획 수정 완료 (적용하기)
          </button>
        </div>
      </div>
    </div>
  );
};
