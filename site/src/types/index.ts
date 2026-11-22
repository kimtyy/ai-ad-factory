export interface MenuItem {
  name: string;
  price?: string;
  desc?: string;
  isPopular?: boolean;
}

// 상호명 입력 중 보여주는 실제 매장 후보 (네이버 지역 검색)
export interface StoreCandidate {
  storeName: string;
  category: string;
  roadAddress: string;
  address: string;
  link?: string;
}

export interface StoreDiagnosticResult {
  storeName: string;
  category: string;
  address: string;
  placeId?: string; // 네이버 플레이스 ID (선택한 지점의 정확한 공식정보 기준)
  officialSummary?: string; // 플레이스 공식 매장 특징 (대표 키워드·좌석·편의시설·방문자 선택 키워드)
  officialKeywords?: string[];
  reviewSummary?: string;
  menusSource?: 'official' | 'fallback';
  strengthsSource?: 'official' | 'blog' | 'fallback';
  summary: string;
  // 손님이 기억하는 필살기 TOP 3
  topStrengths: {
    rank: number;
    title: string;
    description: string;
    mentionRate: string;
    sampleReview: string;
    isFallback?: boolean;
  }[];
  // 홍보 성적표
  scoreCard: {
    menuCount: number;
    hasPhotos: boolean;
    shortsCount: number; // 실제 유튜브 검색 개수 (0편이면 결핍, 1편 이상이면 지속 제작 권유)
    sampleVideoTitle?: string; // 실제 발견된 영상 제목 (예: "가평 설악에서 여기 모르면 진짜 손해! 효정명인갈비 #shorts")
  };
  // AI 기획 노트
  aiPlan: {
    recommendedMenu: string;
    hookCopy: string;
    conceptPitch: string;
  };
  // 메뉴 전체 목록
  menus: MenuItem[];
}

export interface PlanOptions {
  selectedMenu: string;
  font: string;
  voice: string;
  bgm: string;
  address?: string;
  placeId?: string;
  ownerMemo?: string; // 사장님이 꼭 강조하고 싶은 점·수정 요청 (선택 1줄)
}

export interface LeadSubmission {
  id?: string;
  storeName: string;
  ownerName: string;
  phoneNumber: string;
  planOptions: PlanOptions;
  createdAt: string;
  status?: '접수대기' | '제작완료' | '상담완료';
}

export interface LeadRecord extends LeadSubmission {
  id: string;
  status: '접수대기' | '제작완료' | '상담완료';
}

