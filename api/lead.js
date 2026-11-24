// site/api/lead.js
// 리드 수집: Google Apps Script 웹훅(doPost) + 서버 캐시 보관

// Vercel Serverless Function 런타임 간 공유를 위한 전역 메모리 캐시
if (!global.__SHOPSTORY_LEADS__) {
  global.__SHOPSTORY_LEADS__ = [];
}

export default async function handler(req, res) {
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  let body = req.body;
  if (typeof body === 'string') {
    try {
      body = JSON.parse(body);
    } catch (e) {
      body = {};
    }
  }

  const { storeName, ownerName, phoneNumber, planOptions, createdAt } = body || {};

  if (!storeName || !phoneNumber) {
    return res.status(400).json({ error: '상호명과 휴대폰 번호는 필수입니다.' });
  }

  const newLead = {
    id: `lead_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
    storeName: String(storeName).trim(),
    ownerName: String(ownerName || '사장님').trim(),
    phoneNumber: String(phoneNumber).trim(),
    planOptions: planOptions || {
      selectedMenu: '대표 메뉴',
      font: '감성 명조',
      voice: '밝은 여성',
      bgm: 'AI 추천',
    },
    status: '접수대기',
    createdAt: createdAt || new Date().toISOString(),
  };

  const cleanPhone = String(phoneNumber).replace(/[^0-9]/g, '');
  const cleanStore = String(storeName).replace(/\s+/g, '').toLowerCase();
  const cleanOwner = String(ownerName || '사장님').trim().toLowerCase();

  // 중복 제출(더블클릭) 방지: 동일 신청 건이 30초 이내 중복 전송된 경우 기존 건 반환
  const existingDup = global.__SHOPSTORY_LEADS__.find((l) => {
    const lPhone = String(l.phoneNumber || '').replace(/[^0-9]/g, '');
    const lStore = String(l.storeName || '').replace(/\s+/g, '').toLowerCase();
    const lOwner = String(l.ownerName || '').trim().toLowerCase();
    const lMemo = String(l.planOptions?.ownerMemo || '');
    const lMenu = String(l.planOptions?.selectedMenu || '');
    const timeDiff = Math.abs(Date.now() - new Date(l.createdAt).getTime());
    return (
      lPhone === cleanPhone &&
      lStore === cleanStore &&
      lOwner === cleanOwner &&
      lMemo === (cleanOptions?.ownerMemo || '') &&
      lMenu === (cleanOptions?.selectedMenu || '') &&
      timeDiff < 30 * 1000
    );
  });

  if (existingDup) {
    console.log('[중복 리드 접수 방지 - 기존 건 반환]', storeName, phoneNumber);
    return res.status(200).json({
      success: true,
      message: '이미 접수된 신청입니다.',
      lead: existingDup,
    });
  }

  // 메모리 보관
  global.__SHOPSTORY_LEADS__.unshift(newLead);

  console.log('[새로운 리드 접수]', newLead);

  // 구글 시트 웹훅(Google Apps Script) 전송
  const webhookUrl = process.env.GOOGLE_SHEETS_WEBHOOK_URL;
  if (webhookUrl) {
    try {
      await fetch(webhookUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'create_lead',
          ...newLead,
        }),
      });
    } catch (e) {
      console.error('Google Sheets webhook forward failed:', e.message);
    }
  }

  return res.status(200).json({
    success: true,
    message: '리드가 정상적으로 기록되었습니다.',
    lead: newLead,
  });
}
