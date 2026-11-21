// Vercel Serverless Function: POST /api/lead
// 리드 수집: Google Apps Script 웹훅 or kimtyy@gmail.com 전달

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

  console.log('[새로운 리드 접수]', {
    storeName,
    ownerName,
    phoneNumber,
    planOptions,
    createdAt: createdAt || new Date().toISOString(),
  });

  // 구글 시트 웹훅(Google Apps Script) URL이 환경변수에 등록되어 있을 경우 자동 포워딩
  const webhookUrl = process.env.GOOGLE_SHEETS_WEBHOOK_URL;
  if (webhookUrl) {
    try {
      await fetch(webhookUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          storeName,
          ownerName,
          phoneNumber,
          planOptions,
          createdAt: createdAt || new Date().toISOString(),
        }),
      });
    } catch (e) {
      console.error('Webhook forward failed', e);
    }
  }

  return res.status(200).json({
    success: true,
    message: '리드가 정상적으로 기록되었습니다.',
  });
}

