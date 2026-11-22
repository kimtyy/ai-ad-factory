import { useState } from 'react';
import type { FC, FormEvent, ChangeEvent } from 'react';
import { PlanOptions, LeadSubmission } from '../types';

interface LeadModalProps {
  isOpen: boolean;
  onClose: () => void;
  storeName: string;
  planOptions: PlanOptions;
  onChangeOptions: (newOptions: PlanOptions) => void;
  onSubmit: (lead: LeadSubmission) => Promise<void>;
  isCompleted: boolean;
  onResetAll: () => void;
}

export const LeadModal: FC<LeadModalProps> = ({
  isOpen,
  onClose,
  storeName,
  planOptions,
  onChangeOptions,
  onSubmit,
  isCompleted,
  onResetAll,
}) => {
  const [ownerName, setOwnerName] = useState('');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [agreed, setAgreed] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  if (!isOpen) return null;

  const handlePhoneChange = (e: ChangeEvent<HTMLInputElement>) => {
    // 자동 하이픈 포맷팅
    const raw = e.target.value.replace(/[^0-9]/g, '');
    let formatted = raw;
    if (raw.length > 3 && raw.length <= 7) {
      formatted = `${raw.slice(0, 3)}-${raw.slice(3)}`;
    } else if (raw.length > 7) {
      formatted = `${raw.slice(0, 3)}-${raw.slice(3, 7)}-${raw.slice(7, 11)}`;
    }
    setPhoneNumber(formatted);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMessage('');

    if (!ownerName.trim()) {
      setErrorMessage('사장님 성함을 입력해 주세요.');
      return;
    }

    const cleanPhone = phoneNumber.replace(/[^0-9]/g, '');
    if (cleanPhone.length < 10) {
      setErrorMessage('올바른 휴대폰 번호를 입력해 주세요.');
      return;
    }

    if (!agreed) {
      setErrorMessage('개인정보 수집 및 안내 동의에 체크해 주세요.');
      return;
    }

    setIsSubmitting(true);
    try {
      await onSubmit({
        storeName,
        ownerName: ownerName.trim(),
        phoneNumber,
        // selectedMenu·font·voice·bgm 과 함께 선택한 지점의 주소·placeId·사장님 메모를 저장 (작업실이 그대로 이어받는다)
        planOptions: {
          ...planOptions,
          address: planOptions.address || '',
          placeId: planOptions.placeId || '',
          ownerMemo: (planOptions.ownerMemo || '').trim(),
        },
        createdAt: new Date().toISOString(),
      });
    } catch (err: any) {
      setErrorMessage(err?.message || '신청 접수 중 오류가 발생했습니다. 다시 시도해 주세요.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* 백드롭 */}
      <div
        className="fixed inset-0 bg-slate-900/65 backdrop-blur-xs transition-opacity"
        onClick={() => !isSubmitting && onClose()}
      />

      {/* 모달 카드 */}
      <div className="relative w-full max-w-sm bg-white rounded-3xl shadow-2xl p-6 overflow-hidden animate-in zoom-in-95 duration-200">
        {!isCompleted ? (
          <div>
            {/* 닫기 버튼 */}
            <button
              onClick={onClose}
              className="absolute top-4 right-4 w-8 h-8 rounded-full bg-slate-100 hover:bg-slate-200 flex items-center justify-center text-slate-500 text-xs font-bold transition-colors"
            >
              ✕
            </button>

            {/* 헤더 */}
            <div className="mb-4">
              <span className="text-[11px] font-bold text-[#0F2744] bg-[#EEF3F9] border border-[#D0DCEB] px-2.5 py-0.5 rounded-full inline-block mb-1.5">
                쇼츠 제작 신청
              </span>
              <h3 className="text-[20px] font-extrabold text-slate-900 leading-snug">
                사장님 매장 맞춤 영상,<br />
                쇼츠 제작을 신청하세요
              </h3>
              <p className="text-xs text-slate-500 mt-1">
                기획하신 내용 그대로 전문 스튜디오에서 영상을 제작해 카카오톡으로 안내드립니다.
              </p>
            </div>

            {/* 선택된 기획 요약 카드 */}
            <div className="p-3.5 rounded-2xl bg-slate-50 border border-slate-100 mb-4 text-xs space-y-1">
              <div className="flex justify-between">
                <span className="text-slate-400">매장 상호:</span>
                <span className="font-bold text-slate-800">{storeName}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">주인공 메뉴:</span>
                <span className="font-bold text-[#0F2744]">{planOptions.selectedMenu}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">스타일 세팅:</span>
                <span className="font-medium text-slate-700">
                  {planOptions.font.split(' ')[0]} · {planOptions.voice.split(' ')[0]} · {planOptions.bgm}
                </span>
              </div>
            </div>

            {/* 에러 메시지 */}
            {errorMessage && (
              <div className="mb-3 p-2.5 rounded-xl bg-red-50 border border-red-200 text-xs text-red-600 font-semibold">
                ⚠️ {errorMessage}
              </div>
            )}

            {/* 입력 폼 */}
            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <label className="block text-xs font-bold text-slate-700 mb-1">
                  상호명 (자동 입력)
                </label>
                <input
                  type="text"
                  value={storeName}
                  disabled
                  className="w-full h-11 px-3.5 rounded-xl bg-slate-100 border border-slate-200 text-slate-600 text-sm font-semibold cursor-not-allowed"
                />
              </div>

              <div>
                <label className="block text-xs font-bold text-slate-700 mb-1">
                  사장님 성함 <span className="text-[#0F2744]">*</span>
                </label>
                <input
                  type="text"
                  required
                  value={ownerName}
                  onChange={(e) => setOwnerName(e.target.value)}
                  placeholder="예: 홍길동"
                  className="w-full h-11 px-3.5 rounded-xl bg-white border border-slate-200 focus:border-[#0F2744] focus:outline-none text-sm font-medium text-slate-900 transition-all"
                />
              </div>

              <div>
                <label className="block text-xs font-bold text-slate-700 mb-1">
                  휴대폰 번호 <span className="text-[#0F2744]">*</span>
                </label>
                <input
                  type="tel"
                  required
                  value={phoneNumber}
                  onChange={handlePhoneChange}
                  placeholder="010-0000-0000"
                  maxLength={13}
                  className="w-full h-11 px-3.5 rounded-xl bg-white border border-slate-200 focus:border-[#0F2744] focus:outline-none text-sm font-medium text-slate-900 transition-all"
                />
                <p className="text-[10px] text-slate-400 mt-1">
                  * 영상 제작 완료 알림 및 영상 파일을 카카오톡으로 전송해 드립니다.
                </p>
              </div>

              <div>
                <label className="block text-xs font-bold text-slate-700 mb-1">
                  꼭 강조할 점 · 수정 요청 <span className="font-medium text-slate-400">(선택)</span>
                </label>
                <input
                  type="text"
                  value={planOptions.ownerMemo || ''}
                  maxLength={80}
                  onChange={(e) => onChangeOptions({ ...planOptions, ownerMemo: e.target.value })}
                  placeholder="예: 단체석 30석 완비, 한우육회 메인 강조"
                  className="w-full h-11 px-3.5 rounded-xl bg-white border border-slate-200 focus:border-[#0F2744] focus:outline-none text-sm font-medium text-slate-900 placeholder-slate-400 transition-all"
                />
              </div>

              {/* 개인정보 수집 동의 */}
              <label className="flex items-start gap-2 pt-1 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={agreed}
                  onChange={(e) => setAgreed(e.target.checked)}
                  className="mt-0.5 w-4 h-4 text-[#0F2744] rounded border-slate-300 focus:ring-0"
                />
                <span className="text-[11px] text-slate-500 leading-tight">
                  [필수] 매장 진단 리포트 발송 및 쇼츠 제작 안내를 위한 개인정보 수집·이용에 동의합니다.
                </span>
              </label>

              <button
                type="submit"
                disabled={isSubmitting}
                className="w-full h-13 mt-4 rounded-2xl bg-gradient-to-r from-[#0F2744] to-[#1E3A5F] hover:from-[#1E3A5F] hover:to-[#2A4D7A] text-white font-extrabold text-[15px] shadow-lg shadow-[#0F2744]/20 active:scale-[0.98] transition-all flex items-center justify-center gap-2"
              >
                {isSubmitting ? (
                  <div className="flex items-center gap-2">
                    <svg className="animate-spin h-5 w-5 text-white" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
                    </svg>
                    <span>접수 처리 중...</span>
                  </div>
                ) : (
                  <span>🎬 쇼츠 제작 신청하기</span>
                )}
              </button>
            </form>
          </div>
        ) : (
          /* 신청 완료 화면 */
          <div className="py-4 text-center space-y-4">
            <div className="w-16 h-16 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-100 flex items-center justify-center text-3xl mx-auto shadow-sm">
              ✓
            </div>

            <div>
              <span className="text-xs font-bold text-emerald-700 bg-emerald-50 px-3 py-1 rounded-full border border-emerald-100">
                신청 접수 완료
              </span>
              <h3 className="text-[21px] font-extrabold text-slate-900 mt-2">
                신청이 완료되었습니다!
              </h3>
            </div>

            <div className="bg-slate-50 rounded-2xl p-4 text-xs text-slate-600 leading-relaxed text-left border border-slate-100 space-y-2">
              <p>
                <strong>{ownerName || '사장님'}</strong> 님 매장{' '}
                <strong className="text-[#0F2744]">{storeName}</strong> 맞춤 기획안으로 영상을 제작해,
                입력하신 번호(<strong className="text-slate-800">{phoneNumber}</strong>, 카카오톡)로 빠르게 안내드릴게요.
              </p>
              <div className="pt-2 border-t border-slate-200/60 text-[11px] text-slate-500">
                • 제작 안내: 확인 후 순차 발송 (영업시간 기준 1~2시간 이내)<br />
                • 담당자 직접 문의: <a href="mailto:kimtyy@gmail.com" className="underline text-slate-700">kimtyy@gmail.com</a>
              </div>
            </div>

            <div className="pt-2 space-y-2">
              <button
                onClick={onResetAll}
                className="w-full h-12 rounded-2xl bg-[#0F2744] hover:bg-[#1E3A5F] text-white font-bold text-sm shadow-md transition-all active:scale-[0.98]"
              >
                다른 매장 정밀 진단해보기
              </button>
              <button
                onClick={onClose}
                className="w-full h-10 rounded-xl bg-white text-slate-500 font-semibold text-xs hover:bg-slate-50 transition-colors"
              >
                닫기
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
