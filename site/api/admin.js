// site/api/admin.js
// 관리자 로그인 인증 + 리드 목록 조회 및 상태 관리

// Vercel Serverless Function 런타임 간 공유를 위한 전역 메모리 캐시
if (!global.__SHOPSTORY_LEADS__) {
  global.__SHOPSTORY_LEADS__ = [];
}

const DEFAULT_ADMIN_PASSWORD = 'shopstory2026!';

function verifyToken(token) {
  if (!token) return false;
  return token.startsWith('adm_');
}

function areLeadsDuplicate(a, b) {
  if (a.id && b.id && a.id === b.id) return true;
  const phoneA = String(a.phoneNumber || '').replace(/[^0-9]/g, '');
  const phoneB = String(b.phoneNumber || '').replace(/[^0-9]/g, '');
  if (phoneA.length >= 8 && phoneB.length >= 8 && phoneA === phoneB) {
    const storeA = String(a.storeName || '').replace(/\s+/g, '').toLowerCase();
    const storeB = String(b.storeName || '').replace(/\s+/g, '').toLowerCase();
    if (storeA && storeB && (storeA === storeB || storeA.includes(storeB) || storeB.includes(storeA))) {
      // 양쪽 모두 선택 메뉴가 있고 서로 다르면 별개 신청 (같은 날 다른 메뉴로 다시 신청한 건을 합치지 않음)
      const mA = String(a.planOptions?.selectedMenu || '').trim();
      const mB = String(b.planOptions?.selectedMenu || '').trim();
      if (mA && mB && mA !== mB) return false;
      const tA = new Date(a.createdAt).getTime();
      const tB = new Date(b.createdAt).getTime();
      if (!isNaN(tA) && !isNaN(tB)) {
        if (Math.abs(tA - tB) < 60 * 60 * 1000) return true;
        const dateA = new Date(tA).toISOString().slice(0, 10);
        const dateB = new Date(tB).toISOString().slice(0, 10);
        if (dateA === dateB) return true;
      } else {
        return true;
      }
    }
  }
  return false;
}

function dedupeLeads(listA, listB) {
  const merged = [];
  for (const item of [...(listA || []), ...(listB || [])]) {
    if (!item) continue;
    const idx = merged.findIndex((m) => areLeadsDuplicate(m, item));
    if (idx === -1) {
      merged.push(item);
    } else {
      merged[idx] = { ...item, ...merged[idx] };      // 먼저 들어온(시트) 값이 우선 — 메모리의 옛 상태가 변경된 상태를 덮지 않게
    }
  }
  return merged.sort((a, b) => {
    const tA = new Date(a.createdAt).getTime() || 0;
    const tB = new Date(b.createdAt).getTime() || 0;
    return tB - tA;
  });
}

export default async function handler(req, res) {
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const adminPassword = process.env.ADMIN_PASSWORD || DEFAULT_ADMIN_PASSWORD;

  // 1. 관리자 로그인 (POST action='login')
  if (req.method === 'POST') {
    let body = req.body;
    if (typeof body === 'string') {
      try {
        body = JSON.parse(body);
      } catch (e) {
        body = {};
      }
    }

    const { action, password, leadId, status } = body || {};

    if (action === 'login') {
      if (!password || password.trim() !== adminPassword.trim()) {
        return res.status(401).json({ error: '비밀번호가 올바르지 않습니다.' });
      }

      // 간단한 세션 토큰 발급
      const token = `adm_${Buffer.from(String(Date.now())).toString('base64')}`;
      return res.status(200).json({
        success: true,
        token,
        message: '관리자 로그인 성공',
      });
    }

    // 2. 상태 변경 (POST action='update_status')
    if (action === 'update_status') {
      const authHeader = req.headers.authorization || '';
      const token = authHeader.replace(/^Bearer\s+/, '') || body.token;
      if (!verifyToken(token)) {
        return res.status(401).json({ error: '인증이 필요합니다.' });
      }

      const item = global.__SHOPSTORY_LEADS__.find((l) => l.id === leadId);
      if (item) {
        item.status = status || item.status;
      }

      // 구글 시트 웹훅 업데이트 전송
      const webhookUrl = process.env.GOOGLE_SHEETS_WEBHOOK_URL;
      if (webhookUrl) {
        try {
          await fetch(webhookUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'update_status', leadId, status }),
          });
        } catch (e) {
          console.warn('Google Sheets status sync error:', e.message);
        }
      }

      return res.status(200).json({ success: true, message: '상태가 업데이트되었습니다.' });
    }
  }

  // 3. 리드 목록 조회 (GET)
  if (req.method === 'GET') {
    const authHeader = req.headers.authorization || '';
    const token = authHeader.replace(/^Bearer\s+/, '') || req.query.token;
    if (!verifyToken(token)) {
      return res.status(401).json({ error: '관리자 로그인이 필요합니다.' });
    }

    let leads = [...global.__SHOPSTORY_LEADS__];

    // Google Apps Script doGet 연동이 있으면 최신 데이터 풀
    const webhookUrl = process.env.GOOGLE_SHEETS_WEBHOOK_URL;
    if (webhookUrl) {
      try {
        const gasRes = await fetch(`${webhookUrl}?action=get_leads`, {
          method: 'GET',
          redirect: 'follow',
          headers: { 'Accept': 'application/json' },
        });
        if (gasRes.ok) {
          const gasData = await gasRes.json();
          if (Array.isArray(gasData.leads)) {
            leads = dedupeLeads(gasData.leads, global.__SHOPSTORY_LEADS__);
          }
        }
      } catch (e) {
        console.warn('Google Sheets read error:', e.message);
      }
    } else {
      leads = dedupeLeads([], global.__SHOPSTORY_LEADS__);
    }

    return res.status(200).json({
      success: true,
      leads,
    });
  }

  return res.status(405).json({ error: 'Method Not Allowed' });
}

