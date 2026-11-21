export interface MenuItem {
  name: string;
  price?: string;
  desc?: string;
  isPopular?: boolean;
}

export interface StoreDiagnosticResult {
  storeName: string;
  category: string;
  address: string;
  summary: string;
  // 손님이 기억하는 필살기 TOP 3
  topStrengths: {
    rank: number;
    title: string;
    description: string;
    mentionRate: string;
    sampleReview: string;
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

