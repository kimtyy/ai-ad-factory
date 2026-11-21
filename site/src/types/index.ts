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
    shortsCount: number; // 결핍 포인트: 0편
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
  storeName: string;
  ownerName: string;
  phoneNumber: string;
  planOptions: PlanOptions;
  createdAt: string;
}

