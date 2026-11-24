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
 */

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
  
  // 데이터가 없으면 기본 관리자 및 샘플 템플릿 행 추가
  if (lastRow <= 1) {
    sheet.appendRow(["hq", "총괄 관리자", "shopstory2026!", "super", "hq", "Y", "본사 수퍼관리자"]);
    sheet.appendRow(["team01", "김팀장", "pass1234", "manager", "hq", "Y", "영업1팀 매니저"]);
    sheet.appendRow(["agent01", "이영업", "pass1234", "agent", "team01", "Y", "영업1팀 현장영업자"]);
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
      password: String(row[2] || "").trim(),
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
