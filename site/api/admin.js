// site/api/admin.js
// 관리자 로그인 인증 + 권한별(super/manager/agent) 리드 목록 조회 및 상태 관리

import {
  authenticateAgent,
  createAdminToken,
  verifyAdminToken,
  canViewLead,
  canUpdateLead,
} from './agents-auth.js';

// Vercel Serverless Function 런타임 간 공유를 위한 전역 메모리 캐시
if (!global.__SHOPSTORY_LEADS__) {
  global.__SHOPSTORY_LEADS__ = [];
}

function areLeadsDuplicate(a, b) {
  if (a.id && b.id && a.id === b.id) return true;
  const phoneA = String(a.phoneNumber || '').replace(/[^0-9]/g, '');
  const phoneB = String(b.phoneNumber || '').replace(/[^0-9]/g, '');
  if (phoneA.length >= 8 && phoneB.length >= 8 && phoneA === phoneB) {
    const storeA = String(a.storeName || '').replace(/\s+/g, '').toLowerCase();
    const storeB = String(b.storeName || '').replace(/\s+/g, '').toLowerCase();
    if (storeA && storeB && (storeA === storeB || storeA.includes(storeB) || storeB.includes(storeA))) {
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
    const cleanItem = {
      ...item,
      refCode: item.refCode || 'hq',
      managerCode: item.managerCode || 'hq',
    };
    const idx = merged.findIndex((m) => areLeadsDuplicate(m, cleanItem));
    if (idx === -1) {
      merged.push(cleanItem);
    } else {
      merged[idx] = { ...cleanItem, ...merged[idx] };
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

  // 1. POST: 로그인 및 상태 변경
  if (req.method === 'POST') {
    let body = req.body;
    if (typeof body === 'string') {
      try {
        body = JSON.parse(body);
      } catch (e) {
        body = {};
      }
    }

    const { action, code, password, leadId, status } = body || {};

    // 1-1. 관리자 로그인 (POST action='login')
    if (action === 'login') {
      const user = await authenticateAgent(code, password);
      if (!user) {
        return res.status(401).json({ error: '담당자 코드 또는 비밀번호가 올바르지 않습니다.' });
      }

      const token = createAdminToken(user);
      return res.status(200).json({
        success: true,
        token,
        user: {
          code: user.code,
          name: user.name,
          role: user.role,
          managerCode: user.managerCode,
        },
        message: '관리자 로그인 성공',
      });
    }

    // 1-2. 상태 변경 (POST action='update_status')
    if (action === 'update_status') {
      const authHeader = req.headers.authorization || '';
      const token = authHeader.replace(/^Bearer\s+/, '') || body.token;
      const user = verifyAdminToken(token);
      if (!user) {
        return res.status(401).json({ error: '인증이 필요합니다.' });
      }

      // 대상 리드 검색
      let targetLead = global.__SHOPSTORY_LEADS__.find((l) => l.id === leadId);

      // 메모리에 없으면 시트 데이터 조회 시도
      if (!targetLead) {
        const webhookUrl = process.env.GOOGLE_SHEETS_WEBHOOK_URL;
        if (webhookUrl) {
          try {
            const gasRes = await fetch(`${webhookUrl}?action=get_leads`, {
              method: 'GET',
              headers: { Accept: 'application/json' },
            });
            if (gasRes.ok) {
              const gasData = await gasRes.json();
              if (Array.isArray(gasData.leads)) {
                targetLead = gasData.leads.find((l) => l.id === leadId);
              }
            }
          } catch (e) {}
        }
      }

      if (targetLead) {
        // 권한 검증: super는 전체, manager는 본인/팀, agent는 본인 리드만
        if (!canUpdateLead(user, targetLead)) {
          return res.status(403).json({
            error: user.role === 'agent'
              ? '본인 담당 리드만 상태를 변경할 수 있습니다.'
              : '해당 리드의 상태를 변경할 권한이 없습니다.',
          });
        }
        targetLead.status = status || targetLead.status;
      } else if (user.role !== 'super') {
        // 리드를 찾을 수 없고 수퍼관리자가 아니면 거부
        return res.status(404).json({ error: '리드를 찾을 수 없거나 변경 권한이 없습니다.' });
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

  // 2. GET: 권한별 리드 목록 조회
  if (req.method === 'GET') {
    const authHeader = req.headers.authorization || '';
    const token = authHeader.replace(/^Bearer\s+/, '') || req.query.token;
    const user = verifyAdminToken(token);
    if (!user) {
      return res.status(401).json({ error: '관리자 로그인이 필요합니다.' });
    }

    let allLeads = [...global.__SHOPSTORY_LEADS__];

    // Google Apps Script doGet 연동이 있으면 최신 데이터 풀
    const webhookUrl = process.env.GOOGLE_SHEETS_WEBHOOK_URL;
    if (webhookUrl) {
      try {
        const gasRes = await fetch(`${webhookUrl}?action=get_leads`, {
          method: 'GET',
          redirect: 'follow',
          headers: { Accept: 'application/json' },
        });
        if (gasRes.ok) {
          const gasData = await gasRes.json();
          if (Array.isArray(gasData.leads)) {
            allLeads = dedupeLeads(gasData.leads, global.__SHOPSTORY_LEADS__);
          }
        }
      } catch (e) {
        console.warn('Google Sheets read error:', e.message);
      }
    } else {
      allLeads = dedupeLeads([], global.__SHOPSTORY_LEADS__);
    }

    // 모든 리드에 기본 refCode / managerCode 보장
    allLeads = allLeads.map((l) => ({
      ...l,
      refCode: l.refCode || 'hq',
      managerCode: l.managerCode || 'hq',
    }));

    // 권한별 원천 필터링
    const visibleLeads = allLeads.filter((l) => canViewLead(user, l));

    // 등급별 집계 통계
    const stats = {
      totalCount: visibleLeads.length,
      byStatus: {
        접수대기: visibleLeads.filter((l) => (l.status || '접수대기') === '접수대기').length,
        제작완료: visibleLeads.filter((l) => l.status === '제작완료').length,
        상담완료: visibleLeads.filter((l) => l.status === '상담완료').length,
      },
    };

    if (user.role === 'super') {
      const byAgent = {};
      const byManager = {};
      for (const l of visibleLeads) {
        const ag = l.refCode || 'hq';
        const mg = l.managerCode || 'hq';
        byAgent[ag] = (byAgent[ag] || 0) + 1;
        byManager[mg] = (byManager[mg] || 0) + 1;
      }
      stats.byAgent = byAgent;
      stats.byManager = byManager;
    } else if (user.role === 'manager') {
      const userCode = user.code.toLowerCase();
      stats.directCount = visibleLeads.filter((l) => (l.refCode || 'hq').toLowerCase() === userCode).length;
      stats.teamCount = visibleLeads.filter((l) => (l.refCode || 'hq').toLowerCase() !== userCode).length;
      const byAgent = {};
      for (const l of visibleLeads) {
        const ag = l.refCode || 'hq';
        byAgent[ag] = (byAgent[ag] || 0) + 1;
      }
      stats.byAgent = byAgent;
    }

    return res.status(200).json({
      success: true,
      user: {
        code: user.code,
        name: user.name,
        role: user.role,
        managerCode: user.managerCode,
      },
      leads: visibleLeads,
      stats,
    });
  }

  return res.status(405).json({ error: 'Method Not Allowed' });
}
