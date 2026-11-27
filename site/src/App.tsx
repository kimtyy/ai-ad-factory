import { useState, useEffect } from 'react';
import { Navbar } from './components/Navbar';
import { SearchScreen } from './components/SearchScreen';
import { ReportScreen } from './components/ReportScreen';
import { PlanBottomSheet } from './components/PlanBottomSheet';
import { LeadModal } from './components/LeadModal';
import { StickyCTA } from './components/StickyCTA';
import { Footer } from './components/Footer';
import { AdminPage } from './pages/AdminPage';
import { StoreDiagnosticResult, PlanOptions, LeadSubmission, StoreCandidate } from './types';
import { diagnoseStore, submitLead } from './services/api';
import { initRefTracking } from './utils/refTracker';

export default function App() {
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    initRefTracking();
    if (typeof window !== 'undefined') {
      setIsAdmin(window.location.pathname.startsWith('/admin'));
    }
  }, []);

  const [storeName, setStoreName] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [diagnosticResult, setDiagnosticResult] = useState<StoreDiagnosticResult | null>(null);

  // 기획 옵션 상태
  const [planOptions, setPlanOptions] = useState<PlanOptions>({
    selectedMenu: '',
    font: '감성 명조 (AI 추천)',
    voice: '밝은 여성 (AI 추천)',
    bgm: 'AI 추천',
    address: '',
    placeId: '',
    ownerMemo: '',
  });

  // UI 모달 / 바텀시트 상태
  const [isBottomSheetOpen, setIsBottomSheetOpen] = useState(false);
  const [isLeadModalOpen, setIsLeadModalOpen] = useState(false);
  const [isLeadCompleted, setIsLeadCompleted] = useState(false);

  // 1. 매장 진단 실행
  const handleSearch = async (query: string, candidate?: StoreCandidate) => {
    setIsLoading(true);
    setStoreName(query);

    try {
      // 선택한 지점의 주소를 함께 보내 같은 이름의 다른 지점 정보가 섞이지 않게 한다
      const chosenAddress = candidate ? candidate.roadAddress || candidate.address : '';
      const result = await diagnoseStore(query, { address: chosenAddress });
      setDiagnosticResult(result);
      setPlanOptions((prev) => ({
        ...prev,
        selectedMenu: result.aiPlan.recommendedMenu,
        address: result.address && result.address !== '네이버 플레이스 등록 매장' ? result.address : chosenAddress,
        placeId: result.placeId || '',
      }));
      // 화면 상단으로 부드럽게 스크롤
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      console.error('Diagnosis error:', err);
      alert('매장 정보를 가져오는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.');
    } finally {
      setIsLoading(false);
    }
  };

  // 2. 전체 초기화 (새로운 매장 검색)
  const handleResetAll = () => {
    setDiagnosticResult(null);
    setStoreName('');
    setIsLeadModalOpen(false);
    setIsLeadCompleted(false);
    setIsBottomSheetOpen(false);
    setPlanOptions({
      selectedMenu: '',
      font: '감성 명조 (AI 추천)',
      voice: '밝은 여성 (AI 추천)',
      bgm: 'AI 추천',
      address: '',
      placeId: '',
      ownerMemo: '',
    });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  // 3. 리드 신청 전송
  const handleLeadSubmit = async (lead: LeadSubmission) => {
    await submitLead(lead);
    setIsLeadCompleted(true);
  };

  if (isAdmin) {
    return <AdminPage />;
  }

  return (
    <div className="min-h-screen bg-[#F8F9FA] flex flex-col items-center">
      {/* 모바일 뷰 카드 레이아웃: max-w-md로 스마트폰 프레임 최적화 */}
      <div className="w-full max-w-md min-h-screen bg-[#F8F9FA] flex flex-col relative pb-28">
        {/* 상단 네비게이션 */}
        <Navbar onReset={handleResetAll} />

        {/* 메인 콘텐츠 영역 */}
        <main className="flex-1">
          {!diagnosticResult ? (
            /* [화면 1] 가게 검색 시작 */
            <SearchScreen onSearch={handleSearch} isLoading={isLoading} />
          ) : (
            /* [화면 2] 매장 분석 리포트 */
            <ReportScreen
              result={diagnosticResult}
              planOptions={planOptions}
              onOpenBottomSheet={() => setIsBottomSheetOpen(true)}
              onResetSearch={handleResetAll}
            />
          )}
        </main>

        {/* 푸터 정보 */}
        <Footer />

        {/* 하단 고정 메인 CTA 버튼 (리포트 화면에서만 표시) */}
        {diagnosticResult && (
          <StickyCTA
            onOpenModal={() => {
              setIsLeadCompleted(false);
              setIsLeadModalOpen(true);
            }}
            selectedMenu={planOptions.selectedMenu || diagnosticResult.aiPlan.recommendedMenu}
          />
        )}

        {/* 숏폼 기획 수정 바텀시트 */}
        {diagnosticResult && (
          <PlanBottomSheet
            isOpen={isBottomSheetOpen}
            onClose={() => setIsBottomSheetOpen(false)}
            options={planOptions}
            onChangeOptions={setPlanOptions}
            menus={diagnosticResult.menus}
            recommendedMenu={diagnosticResult.aiPlan.recommendedMenu}
            businessType={diagnosticResult.businessType}
          />
        )}

        {/* [화면 3] 리드 수집 모달 및 완료 화면 */}
        <LeadModal
          isOpen={isLeadModalOpen}
          onClose={() => setIsLeadModalOpen(false)}
          storeName={diagnosticResult?.storeName || storeName}
          planOptions={planOptions}
          onChangeOptions={setPlanOptions}
          onSubmit={handleLeadSubmit}
          isCompleted={isLeadCompleted}
          onResetAll={handleResetAll}
          businessType={diagnosticResult?.businessType}
        />
      </div>
    </div>
  );
}
