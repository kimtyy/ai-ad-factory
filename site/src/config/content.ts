// 사이트에 표시되는 모든 텍스트 — 여기서 수정합니다.
export const SITE_CONFIG = {
  brandName: '샵스토리 (ShopStory)',
  tagline: 'AI 사장님 매장 홍보 영상 제작소',
  copyright: '© 2026 허브스마트. All rights reserved.',
  company: {
    lines: [
      '상호: 허브스마트 | 대표: 김태영',
      '사업자등록번호: 526-04-00927 | 통신판매업신고: 신청 중',
      '주소: 경기도 가평군 조종면 조종희망로 16-19, 1층',
      '고객센터: 031-5175-8887',
    ],
  },
  supportEmail: 'kimtyy@gmail.com',
  appUrl: 'https://shopstory-store.streamlit.app',

  hero: {
    badge: '🔥 전국 동네 가게를 위한 AI 숏폼 솔루션',
    titleLeft: ['가게 이름만', '입력하면'],
    titleRight: ['1분 만에', '18초 쇼츠 완성'],
    watermark: 'SHOPSTORY',
    description:
      '네이버 플레이스 공식 메뉴판과 매장 사진·영상으로 1컷 1카피 자막과 음성이 들어간 세로형(9:16) 홍보 쇼츠를 자동으로 만들어 드립니다. 복잡한 영상 편집 없이 사장님 손안에서 끝납니다.',
    ctaButton: '🚀 지금 샵스토리 시작하기',
    secondaryButton: '🎬 샘플 영상 보러가기',
  },

  sample: {
    badge: 'SAMPLE',
    title: '이렇게 만들어집니다',
    description: '샵스토리로 만든 18초 홍보 쇼츠 예시입니다. 컷마다 한 줄 카피와 자막, 음성이 들어갑니다.',
  },

  metrics: {
    title: '샵스토리 핵심 포인트',
    items: [
      { value: '1분', label: '영상 완성 소요 시간' },
      { value: '18초', label: '집중해서 보는 숏폼 길이' },
      { value: '4개', label: '유튜브·인스타·네이버클립·틱톡 지원' },
      { value: '9:16', label: '세로형 쇼츠 규격' },
    ],
  },

  process: {
    badge: 'HOW IT WORKS',
    title: '누구나 3단계면 전문 홍보 쇼츠가 완성됩니다',
    steps: [
      {
        step: '01',
        title: '가게 이름 검색 & 메뉴판 자동 수집',
        description:
          '상호명만 넣으면 네이버 플레이스의 공식 메뉴판과 매장 정보를 실제 데이터 그대로 불러옵니다.',
      },
      {
        step: '02',
        title: '사진·영상 선택 & 1컷 1카피 대본',
        description:
          '사진이나 폰으로 찍은 짧은 동영상을 올리면, AI가 사진 속 메뉴를 판별해 컷마다 딱 들어맞는 2초 숏카피를 씁니다.',
      },
      {
        step: '03',
        title: '4대 숏폼 플랫폼 업로드 문구 원클릭 복사',
        description:
          '완성한 영상을 스마트폰 갤러리에 저장하고, 유튜브 쇼츠·인스타 릴스·네이버 클립·틱톡에 맞춘 제목·본문·해시태그를 눌러서 복사해 올립니다.',
      },
    ],
  },

  platforms: {
    title: '영상 하나로 4대 플랫폼을 한 번에',
    items: [
      { name: '유튜브 쇼츠', description: '검색 노출을 고려한 제목 + 상세 설명 + 네이버 지도 첫 댓글 패키지' },
      { name: '인스타그램 릴스', description: '저장을 부르는 감성 캡션 + 지역·업종 인기 해시태그' },
      { name: '네이버 클립', description: '네이버 플레이스 [장소] 스티커 가이드로 매장 방문 유도' },
      { name: '틱톡', description: '추천 탭(FYP)을 겨냥한 짧고 강한 훅 카피와 트렌드 해시태그' },
    ],
  },

  compliance: {
    title: '안전하고 투명한 서비스',
    youtubeApiNotice: {
      title: 'YouTube API 연동 안내',
      content:
        '샵스토리는 사장님 본인이 소유한 유튜브 채널에 영상을 업로드하고 첫 댓글을 등록하는 용도로만 Google YouTube API를 사용합니다. 사장님이 직접 허용한 경우에 한해 권한을 사용하며, 다른 사람의 채널이나 개인 데이터에는 접근하지 않습니다.',
    },
    pinNotice: '점주 전용 서비스로, 이용 코드(PIN)가 필요합니다. 코드 문의는 이메일로 보내 주세요.',
    privacyPolicyLink: '/privacy.html',
  },
};
