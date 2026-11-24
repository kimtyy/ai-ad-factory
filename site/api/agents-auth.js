// site/api/agents-auth.js
// 영업자/관리자 3단계 권한 관리 및 HMAC 서명 토큰 발급·검증

import crypto from 'node:crypto';

export const DEFAULT_ADMIN_PASSWORD = 'shopstory2026!';

function getSecret() {
  return process.env.ADMIN_JWT_SECRET || process.env.ADMIN_PASSWORD || DEFAULT_ADMIN_PASSWORD;
}

/** 구글 시트 웹앱 호출용 공유 키 (Code.gs 스크립트 속성 API_KEY 와 같은 값). 없으면 시트 쪽이 모든 요청을 거부한다 */
export function gasKey() {
  return process.env.GAS_API_KEY || '';
}

export function gasUrl(base, action) {
  return `${base}${base.includes('?') ? '&' : '?'}action=${action}&key=${encodeURIComponent(gasKey())}`;
}

function sha256Hex(s) {
  return crypto.createHash('sha256').update(String(s), 'utf8').digest('hex');
}

function getAdminPassword() {
  return process.env.ADMIN_PASSWORD || DEFAULT_ADMIN_PASSWORD;
}

/**
 * 구글 시트(2번 탭: 영업자목록) 또는 캐시/메모리에서 활성 영업자 목록 조회
 */
export async function getAgents(forceRefresh = false) {
  if (!global.__SHOPSTORY_AGENTS_CACHE__) {
    global.__SHOPSTORY_AGENTS_CACHE__ = { timestamp: 0, agents: [] };
  }

  const cache = global.__SHOPSTORY_AGENTS_CACHE__;
  const now = Date.now();

  // 테스트용 모의 영업자 데이터가 주입되어 있으면 우선 병합
  const mockAgents = Array.isArray(global.__SHOPSTORY_MOCK_AGENTS__) ? global.__SHOPSTORY_MOCK_AGENTS__ : [];

  // 캐시가 유효하고 강제 새로고침이 아니면 캐시 반환 (TTL 60초)
  if (!forceRefresh && cache.agents.length > 0 && now - cache.timestamp < 60 * 1000) {
    return mergeAgents(cache.agents, mockAgents);
  }

  const webhookUrl = process.env.GOOGLE_SHEETS_WEBHOOK_URL;
  if (webhookUrl) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 4000);

      const res = await fetch(gasUrl(webhookUrl, 'get_agents'), {
        method: 'GET',
        redirect: 'follow',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      });
      clearTimeout(timer);

      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.agents)) {
          cache.agents = data.agents.map((a) => ({
            code: String(a.code || '').trim(),
            name: String(a.name || '').trim(),
            password: String(a.password || '').trim(),                       // (테스트용 모의 계정 전용) 시트는 해시만 내려준다
            passwordSha256: String(a.password_sha256 || '').trim().toLowerCase(),
            role: String(a.role || 'agent').trim().toLowerCase(), // super, manager, agent
            manager_code: String(a.manager_code || 'hq').trim(),
            active: String(a.active || 'Y').trim().toUpperCase(),
          }));
          cache.timestamp = now;
        }
      }
    } catch (e) {
      console.warn('[Agents] Google Sheets agent fetch failed, using cache/mock:', e.message);
    }
  }

  return mergeAgents(cache.agents, mockAgents);
}

function mergeAgents(listA, listB) {
  const map = new Map();
  for (const a of [...listA, ...listB]) {
    if (!a || !a.code) continue;
    map.set(String(a.code).toLowerCase(), a);
  }
  return Array.from(map.values());
}

/**
 * 로그인 인증: code (비어있거나 hq이면 수퍼관리자) + password
 */
export async function authenticateAgent(code, password) {
  const cleanCode = String(code || '').trim();
  const cleanPass = String(password || '').trim();
  const adminPassword = getAdminPassword().trim();

  // 1. 마스터 안전장치: ADMIN_PASSWORD 로 로그인 (아이디 비워두거나 hq)하면 시트 실패 시에도 항상 super
  if ((!cleanCode || cleanCode.toLowerCase() === 'hq') && cleanPass === adminPassword) {
    return {
      code: 'hq',
      name: '총괄 관리자',
      role: 'super',
      managerCode: 'hq',
    };
  }

  if (!cleanPass) return null;

  // 2. 영업자 목록에서 코드 일치 및 활성화 여부 확인
  const agents = await getAgents();
  const matched = agents.find(
    (a) => String(a.code).toLowerCase() === cleanCode.toLowerCase() && String(a.active).toUpperCase() === 'Y'
  );

  if (matched) {
    // 본인 비밀번호 또는 마스터 비밀번호로 로그인 허용
    const ownOk = (matched.passwordSha256 && matched.passwordSha256 === sha256Hex(cleanPass))
      || (matched.password && matched.password === cleanPass);
    if (ownOk || cleanPass === adminPassword) {
      return {
        code: matched.code,
        name: matched.name || matched.code,
        role: ['super', 'manager', 'agent'].includes(matched.role) ? matched.role : 'agent',
        managerCode: matched.manager_code || 'hq',
      };
    }
  }

  return null;
}

/**
 * refCode에 해당하는 상위 managerCode 매핑
 */
export async function resolveLeadCodes(refCode) {
  const cleanRef = String(refCode || '').trim().replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 32);

  if (!cleanRef || cleanRef.toLowerCase() === 'hq') {
    return { refCode: 'hq', managerCode: 'hq' };
  }

  const agents = await getAgents();
  const agent = agents.find((a) => String(a.code).toLowerCase() === cleanRef.toLowerCase());

  if (agent) {
    if (agent.role === 'manager') {
      // 본인이 매니저인 경우
      return { refCode: agent.code, managerCode: agent.code };
    }
    return {
      refCode: agent.code,
      managerCode: agent.manager_code || 'hq',
    };
  }

  // 시트에 아직 등록되지 않은 영업자 코드라도 refCode는 보존
  return { refCode: cleanRef, managerCode: 'hq' };
}

/**
 * HMAC 서명 토큰 발급
 */
export function createAdminToken(user) {
  const secret = getSecret();
  const payload = {
    code: user.code,
    name: user.name,
    role: user.role,
    managerCode: user.managerCode || 'hq',
    exp: Date.now() + 7 * 24 * 60 * 60 * 1000, // 7일 유효
  };

  const payloadB64 = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const hmac = crypto.createHmac('sha256', secret).update(payloadB64).digest('base64url');

  return `adm_${payloadB64}.${hmac}`;
}

/**
 * HMAC 서명 토큰 검증
 */
export function verifyAdminToken(token) {
  if (!token || typeof token !== 'string') return null;
  if (!token.startsWith('adm_')) return null;

  // 서명이 없는 구형 토큰('adm_' + 아무 문자열)은 위조가 가능하므로 받지 않는다
  if (!token.includes('.')) return null;

  try {
    const secret = getSecret();
    const parts = token.slice(4).split('.');
    if (parts.length !== 2) return null;

    const [payloadB64, hmac] = parts;
    const expectedHmac = crypto.createHmac('sha256', secret).update(payloadB64).digest('base64url');

    // 시간 타이밍 공격 방지 비교
    const hmacBuf = Buffer.from(hmac);
    const expectedBuf = Buffer.from(expectedHmac);
    if (hmacBuf.length !== expectedBuf.length || !crypto.timingSafeEqual(hmacBuf, expectedBuf)) {
      return null;
    }

    const payload = JSON.parse(Buffer.from(payloadB64, 'base64url').toString('utf8'));

    if (!payload.exp || Date.now() > payload.exp) {
      return null; // 만료됨
    }

    return {
      code: payload.code || 'hq',
      name: payload.name || '관리자',
      role: ['super', 'manager', 'agent'].includes(payload.role) ? payload.role : 'agent',
      managerCode: payload.managerCode || 'hq',
    };
  } catch (e) {
    return null;
  }
}

/**
 * 리드 조회 권한 검사
 */
export function canViewLead(user, lead) {
  if (!user) return false;
  if (user.role === 'super') return true;

  const userCode = String(user.code || '').toLowerCase();
  const leadRef = String(lead.refCode || 'hq').toLowerCase();
  const leadMgr = String(lead.managerCode || 'hq').toLowerCase();

  if (user.role === 'manager') {
    return leadRef === userCode || leadMgr === userCode;
  }

  if (user.role === 'agent') {
    return leadRef === userCode;
  }

  return false;
}

/**
 * 리드 상태 변경 권한 검사
 */
export function canUpdateLead(user, lead) {
  if (!user) return false;
  if (user.role === 'super') return true;

  const userCode = String(user.code || '').toLowerCase();
  const leadRef = String(lead.refCode || 'hq').toLowerCase();
  const leadMgr = String(lead.managerCode || 'hq').toLowerCase();

  if (user.role === 'manager') {
    return leadRef === userCode || leadMgr === userCode;
  }

  if (user.role === 'agent') {
    return leadRef === userCode;
  }

  return false;
}
