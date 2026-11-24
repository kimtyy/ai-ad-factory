/**
 * 샵스토리(ShopStory) 구글 스프레드시트 연동 Google Apps Script (Code.gs)
 * 
 * [스프레드시트 구성 안내]
 * 1번 탭 (이름: "리드목록"): 접수된 가맹점 리드 데이터 및 진행 상태 저장
 * 2번 탭 (이름: "영업자목록"): 3단계 권한(super/manager/agent) 영업자 계정 및 비밀번호 관리
 * 
 * [배포 방법]
 * 1. 스프레드시트 상단 메뉴 [확장 프로그램] > [Apps Script] 클릭
 * 2. 기존 코드를 모두 지우고 본 파일(Code.gs)의 전체 내용을 붙여넣기
 * 3. 우측 상단 [배포] > [새 배포] 클릭
 * 4. 유형 선택: "웹 앱 (Web App)"
 *    - 설명: "샵스토리 리드 및 영업자 연동 API v1"
 *    - 다음 사용자 권한으로 실행: "나 (내 계정)"
 *    - 액세스 권한이 있는 사용자: "모든 사용자 (Anyone)" ※ 중요: 익명 호출 허용
 * 5. [배포] 클릭 후 발급된 "웹 앱 URL"을 복사하여 Vercel 환경 변수 `GOOGLE_SHEETS_WEBHOOK_URL` 에 설정
 * 6. ★ 보안: 웹앱이 익명 호출을 허용하므로 공유 키가 필수입니다.
 *    - Apps Script [프로젝트 설정] > [스크립트 속성] 에 API_KEY = (길고 무작위한 문자열) 추가
 *    - Vercel 환경 변수 `GAS_API_KEY` 에 같은 값 설정
 *    - API_KEY 가 없거나 일치하지 않는 요청은 모두 거부됩니다 (리드 전화번호·영업자 정보 보호)
 */

var NOTIFY_EMAIL = "kimtyy@gmail.com";

/** 공유 키 검사 (스크립트 속성 API_KEY 미설정 시 전부 거부) */
function isAuthorized(key) {
  var expected = PropertiesService.getScriptProperties().getProperty("API_KEY");
  return !!expected && String(key || "") === expected;
}

function sha256Hex(text) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(text), Utilities.Charset.UTF_8);
  return bytes.map(function (b) { return ("0" + ((b < 0 ? b + 256 : b)).toString(16)).slice(-2); }).join("");
}

var SHEET_LEADS = "리드목록";
var SHEET_AGENTS = "영업자목록";

// 1번 탭: 리드목록 헤더 정의
var LEADS_HEADERS = [
  "접수일시",       // A
  "상호명",         // B
  "사장님 성함",     // C
  "연락처",         // D
  "추천인 코드",     // E (refCode)
  "관리자 코드",     // F (managerCode)
  "진행상태",       // G (status: 접수대기 / 제작완료 / 상담완료)
  "선택메뉴",       // H (selectedMenu)
  "자막폰트",       // I (font)
  "나레이션 성우",   // J (voice)
  "BGM 분위기",     // K (bgm)
  "매장 주소",       // L (address)
  "플레이스 ID",     // M (placeId)
  "사장님 메모",     // N (ownerMemo)
  "고유 ID"         // O (id)
];

// 2번 탭: 영업자목록 헤더 정의
var AGENTS_HEADERS = [
  "code",          // A: 영업자 식별 코드 (예: team01, agent01, hq)
  "name",          // B: 담당자 성명 (예: 김팀장, 이영업)
  "password",      // C: 로그인 비밀번호
  "role",          // D: 권한 등급 (super, manager, agent)
  "manager_code",  // E: 상위 관리자/팀장 코드 (예: hq, team01)
  "active",        // F: 활성 여부 (Y 또는 N)
  "memo"           // G: 비고
];

/**
 * 초기 시트 탭 및 헤더 자동 준비 함수
 */
function getOrCreateSheet(sheetName, headers) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(sheetName);
  
  if (!sheet) {
    sheet = ss.insertSheet(sheetName);
  }
  
  // 헤더가 없으면 첫 행에 삽입
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(headers);
    sheet.getRange(1, 1, 1, headers.length).setFontWeight("bold").setBackground("#F1F5F9");
    sheet.setFrozenRows(1);
  }
  
  return sheet;
}

/**
 * GET 요청 핸들러 (리드 조회, 영업자 목록 조회)
 * ?action=get_leads  -> 1번 탭 리드 목록 반환
 * ?action=get_agents -> 2번 탭 활성 영업자 목록 반환
 */
function doGet(e) {
  try {
    var action = (e && e.parameter && e.parameter.action) ? e.parameter.action : "get_leads";
    
    if (!isAuthorized(e && e.parameter && e.parameter.key)) {
      return jsonResponse({ error: "unauthorized" }, 401);
    }
    
    if (action === "get_agents") {
      return jsonResponse(handleGetAgents());
    }
    
    if (action === "get_leads") {
      return jsonResponse(handleGetLeads());
    }
    
    return jsonResponse({
      status: "ok",
      message: "ShopStory Sheets API running",
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    return jsonResponse({ error: err.toString() }, 500);
  }
}

/**
 * POST 요청 핸들러 (신규 리드 접수, 진행상태 변경)
 * body.action: "create_lead" (또는 기본) -> 리드 추가
 * body.action: "update_status" -> 리드 상태 변경
 */
function doPost(e) {
  try {
    var body = {};
    if (e && e.postData && e.postData.contents) {
      body = JSON.parse(e.postData.contents);
    }
    
    if (!isAuthorized(body.key)) {
      return jsonResponse({ error: "unauthorized" }, 401);
    }
    
    var action = body.action || "create_lead";
    
    if (action === "update_status") {
      return jsonResponse(handleUpdateStatus(body));
    }
    
    if (action === "create_lead") {
      return jsonResponse(handleCreateLead(body));
    }
    
    return jsonResponse({ error: "Unknown action: " + action }, 400);
  } catch (err) {
    return jsonResponse({ error: err.toString() }, 500);
  }
}

/**
 * 1. 영업자 목록 조회 (GET ?action=get_agents)
 */
function handleGetAgents() {
  var sheet = getOrCreateSheet(SHEET_AGENTS, AGENTS_HEADERS);
  var lastRow = sheet.getLastRow();
  
  // 데이터가 없으면 작성 예시 행만 추가 — 비활성(N)이고 비밀번호가 비어 있어 로그인에 쓸 수 없다.
  // (알려진 기본 비밀번호의 샘플 계정을 활성 상태로 만들지 않는다. 총괄 관리자는 Vercel ADMIN_PASSWORD 로 로그인)
  if (lastRow <= 1) {
    sheet.appendRow(["team01", "(예시) 팀장", "", "manager", "hq", "N", "예시 — 비밀번호 입력 후 active=Y 로 바꾸면 활성화"]);
    sheet.appendRow(["agent01", "(예시) 영업자", "", "agent", "team01", "N", "예시 — 비밀번호 입력 후 active=Y 로 바꾸면 활성화"]);
    lastRow = sheet.getLastRow();
  }
  
  var data = sheet.getRange(2, 1, lastRow - 1, AGENTS_HEADERS.length).getValues();
  var agents = [];
  
  for (var i = 0; i < data.length; i++) {
    var row = data[i];
    var code = String(row[0] || "").trim();
    if (!code) continue;
    
    agents.push({
      code: code,
      name: String(row[1] || "").trim(),
      password_sha256: row[2] ? sha256Hex(String(row[2]).trim()) : "",   // 평문 비밀번호는 시트 밖으로 내보내지 않는다
      role: String(row[3] || "agent").trim().toLowerCase(),
      manager_code: String(row[4] || "hq").trim(),
      active: String(row[5] || "Y").trim().toUpperCase()
    });
  }
  
  return { success: true, agents: agents };
}

/**
 * 2. 리드 목록 조회 (GET ?action=get_leads)
 */
function handleGetLeads() {
  var sheet = getOrCreateSheet(SHEET_LEADS, LEADS_HEADERS);
  var lastRow = sheet.getLastRow();
  
  if (lastRow <= 1) {
    return { success: true, leads: [] };
  }
  
  var data = sheet.getRange(2, 1, lastRow - 1, LEADS_HEADERS.length).getValues();
  var leads = [];
  
  for (var i = 0; i < data.length; i++) {
    var row = data[i];
    var storeName = String(row[1] || "").trim();
    var phone = String(row[3] || "").trim();
    if (!storeName && !phone) continue;
    
    var createdAt = row[0];
    if (createdAt instanceof Date) {
      createdAt = createdAt.toISOString();
    } else {
      createdAt = String(createdAt || "");
    }
    
    leads.push({
      id: String(row[14] || ("lead_row_" + (i + 2))),
      createdAt: createdAt,
      storeName: storeName,
      ownerName: String(row[2] || "사장님").trim(),
      phoneNumber: phone,
      refCode: String(row[4] || "hq").trim(),
      managerCode: String(row[5] || "hq").trim(),
      status: String(row[6] || "접수대기").trim(),
      planOptions: {
        selectedMenu: String(row[7] || "대표 메뉴").trim(),
        font: String(row[8] || "감성 명조").trim(),
        voice: String(row[9] || "밝은 여성").trim(),
        bgm: String(row[10] || "AI 추천").trim(),
        address: String(row[11] || "").trim(),
        placeId: String(row[12] || "").trim(),
        ownerMemo: String(row[13] || "").trim()
      }
    });
  }
  
  return { success: true, leads: leads };
}

/**
 * 3. 신규 리드 저장 (POST action=create_lead)
 */
function handleCreateLead(lead) {
  var sheet = getOrCreateSheet(SHEET_LEADS, LEADS_HEADERS);
  var lock = LockService.getScriptLock();
  
  try {
    lock.waitLock(10000); // 동시성 방지 락 10초 대기
    
    var po = lead.planOptions || {};
    var storeName = String(lead.storeName || "").trim();
    var phoneNumber = String(lead.phoneNumber || "").trim();
    var selectedMenu = String(po.selectedMenu || "대표 메뉴").trim();
    var createdAtStr = lead.createdAt || new Date().toISOString();
    
    // 중복 접수 방지 검사 (최근 1시간 내 동일 전화번호 + 동일 상호 + 동일 메뉴)
    var lastRow = sheet.getLastRow();
    if (lastRow > 1) {
      var recentRange = sheet.getRange(Math.max(2, lastRow - 50), 1, Math.min(50, lastRow - 1), LEADS_HEADERS.length).getValues();
      var cleanPhone = phoneNumber.replace(/[^0-9]/g, "");
      var cleanStore = storeName.replace(/\s+/g, "").toLowerCase();
      
      for (var j = recentRange.length - 1; j >= 0; j--) {
        var rRow = recentRange[j];
        var rPhone = String(rRow[3] || "").replace(/[^0-9]/g, "");
        var rStore = String(rRow[1] || "").replace(/\s+/g, "").toLowerCase();
        var rMenu = String(rRow[7] || "").trim();
        
        if (cleanPhone === rPhone && (cleanStore === rStore || cleanStore.indexOf(rStore) !== -1 || rStore.indexOf(cleanStore) !== -1)) {
          if (!selectedMenu || !rMenu || selectedMenu === rMenu) {
            // 중복 발견: 기존 행 정보 반환
            return {
              success: true,
              duplicated: true,
              message: "이미 접수된 리드입니다.",
              id: String(rRow[14] || "")
            };
          }
        }
      }
    }
    
    var rowData = [
      createdAtStr,
      storeName,
      String(lead.ownerName || "사장님").trim(),
      phoneNumber,
      String(lead.refCode || "hq").trim(),
      String(lead.managerCode || "hq").trim(),
      String(lead.status || "접수대기").trim(),
      selectedMenu,
      String(po.font || "감성 명조").trim(),
      String(po.voice || "밝은 여성").trim(),
      String(po.bgm || "AI 추천").trim(),
      String(po.address || "").trim(),
      String(po.placeId || "").trim(),
      String(po.ownerMemo || "").trim(),
      String(lead.id || ("lead_" + new Date().getTime()))
    ];
    
    sheet.appendRow(rowData);
    
    // 이메일 알림 — 실패해도 저장은 유지
    try {
      notifyNewLead(rowData);
    } catch (mailErr) {
      console.warn("Email notify failed: " + mailErr);
    }
    
    return {
      success: true,
      id: rowData[14],
      message: "리드가 성공적으로 저장되었습니다."
    };
  } finally {
    lock.releaseLock();
  }
}

/**
 * 신규 리드 이메일 알림 (상호·사장님·연락처·담당영업자·선택메뉴)
 */
function notifyNewLead(row) {
  var refCode = String(row[4] || "hq");
  var agentLabel = refCode;
  try {
    var agentSheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_AGENTS);
    if (agentSheet && agentSheet.getLastRow() > 1) {
      var agents = agentSheet.getRange(2, 1, agentSheet.getLastRow() - 1, 2).getValues();
      for (var i = 0; i < agents.length; i++) {
        if (String(agents[i][0]).trim().toLowerCase() === refCode.toLowerCase()) {
          agentLabel = refCode + " (" + String(agents[i][1] || "").trim() + ")";
          break;
        }
      }
    }
  } catch (ignore) {}
  
  var lines = [
    "상호명: " + row[1],
    "사장님: " + row[2],
    "연락처: " + row[3],
    "담당 영업자: " + agentLabel,
    "선택 메뉴: " + row[7],
    "매장 주소: " + row[11],
    "사장님 메모: " + row[13],
    "접수 시각: " + row[0]
  ];
  MailApp.sendEmail({
    to: NOTIFY_EMAIL,
    subject: "[샵스토리] 새 신청 접수 - " + row[1],
    body: lines.join("\n")
  });
}

/**
 * 4. 진행 상태 변경 (POST action=update_status)
 */
function handleUpdateStatus(body) {
  var sheet = getOrCreateSheet(SHEET_LEADS, LEADS_HEADERS);
  var lock = LockService.getScriptLock();
  
  try {
    lock.waitLock(10000);
    
    var leadId = String(body.leadId || "").trim();
    var newStatus = String(body.status || "").trim();
    
    if (!leadId || !newStatus) {
      return { error: "leadId and status are required." };
    }
    
    var lastRow = sheet.getLastRow();
    if (lastRow <= 1) {
      return { error: "No leads found." };
    }
    
    // 고유 ID 열(15번째 컬럼 = O열)에서 leadId 탐색
    var idValues = sheet.getRange(2, 15, lastRow - 1, 1).getValues();
    var targetRowIndex = -1;
    
    for (var i = 0; i < idValues.length; i++) {
      if (String(idValues[i][0]).trim() === leadId) {
        targetRowIndex = i + 2; // 시트 행 인덱스 (2행부터 시작)
        break;
      }
    }
    
    if (targetRowIndex === -1) {
      return { error: "Lead not found for id: " + leadId };
    }
    
    // 7번째 열 (G열: 진행상태) 업데이트
    sheet.getRange(targetRowIndex, 7).setValue(newStatus);
    
    return {
      success: true,
      leadId: leadId,
      status: newStatus,
      message: "Status updated successfully"
    };
  } finally {
    lock.releaseLock();
  }
}

/**
 * JSON 응답 생성 헬퍼
 */
function jsonResponse(data, statusCode) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}
