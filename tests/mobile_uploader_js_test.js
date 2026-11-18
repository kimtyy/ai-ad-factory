// core/ui/mobile_uploader/index.html 의 스크립트를 가짜 브라우저 환경(vm)에서 돌려 보는 테스트. 실행: node tests/mobile_uploader_js_test.js
// 출력: 한 줄에 하나씩 "PASS 설명" / "FAIL 설명" (Python 스위트가 읽는다)
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const html = fs.readFileSync(path.join(__dirname, "..", "core", "ui", "mobile_uploader", "index.html"), "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];

let fails = 0;
function check(c, m) { if (!c) fails++; console.log((c ? "PASS " : "FAIL ") + m); }

function makeEnv(opts) {
  opts = opts || {};
  const sent = [];
  const els = {};
  function el(id) {
    return els[id] || (els[id] = {
      id, style: { setProperty() {}, display: "" }, textContent: "", className: "", value: "", disabled: false, _l: {},
      firstChild: { style: {} },
      addEventListener(t, f) { this._l[t] = f; }, click() { this._clicked = (this._clicked || 0) + 1; },
    });
  }
  const win = {
    parent: { postMessage(m) { sent.push(m); } },
    _l: {}, addEventListener(t, f) { this._l[t] = f; },
    createImageBitmap: opts.noBitmap ? undefined : async (f) => {
      if (f._undecodable) throw new Error("decode");
      return { width: f._w, height: f._h, close() {} };
    },
  };
  class FileReader {
    readAsDataURL(b) { setTimeout(() => { this.result = "data:" + (b.type || "application/octet-stream") + ";base64," + Buffer.from(b._bytes).toString("base64"); this.onload(); }, 0); }
  }
  const doc = {
    getElementById: el,
    documentElement: { style: { setProperty() {} }, scrollHeight: 120 },
    createElement(tag) {
      if (tag !== "canvas") return {};
      const c = { width: 0, height: 0, getContext() { return { fillRect() {}, drawImage() {}, set fillStyle(v) {} }; },
        toBlob(cb, type, q) { c._q = q; c._type = type; cb({ type, size: 300 * 1024, _bytes: Buffer.alloc(8, 7), _w: c.width, _h: c.height }); } };
      lastCanvas = c; return c;
    },
  };
  let lastCanvas = null;
  const ctx = vm.createContext({ window: win, document: doc, FileReader, Date, Math, Promise, setTimeout, Buffer, URL: { createObjectURL() { return "blob:x"; }, revokeObjectURL() {} }, Image: function () { const im = this; im.width = 100; im.height = 100; Object.defineProperty(im, "src", { set() { setTimeout(() => im.onload && im.onload(), 0); } }); }, console });
  vm.runInContext(script, ctx);
  return { sent, els, el, win, canvas: () => lastCanvas, ctx };
}
function render(env, args, extra) { env.win._l.message({ data: Object.assign({ type: "streamlit:render", args, theme: { primaryColor: "#123456" } }, extra || {}) }); }
function fakeFile(name, type, w, h, size, extra) { return Object.assign({ name, type, size, _w: w, _h: h, _bytes: Buffer.from("abc") }, extra || {}); }
const tick = () => new Promise((r) => setTimeout(r, 30));
const values = (env) => env.sent.filter((m) => m.type === "streamlit:setComponentValue");

(async () => {
  // 1. 프로토콜: ready 신호와 높이 보고, render 메시지로 라벨 적용
  let env = makeEnv();
  check(env.sent.some((m) => m.type === "streamlit:componentReady" && m.apiVersion === 1 && m.isStreamlitMessage === true), "componentReady(apiVersion 1) 전송");
  check(env.sent.some((m) => m.type === "streamlit:setFrameHeight" && m.height > 0), "프레임 높이 보고");
  render(env, { label: "📸 테스트 라벨", max_files: 5, max_dim: 1440, quality: 0.88, max_video_mb: 40, disabled: false });
  check(env.el("pick").textContent === "📸 테스트 라벨" && env.el("pick").disabled === false, "render 인자(라벨·활성 상태) 반영");
  render(env, { max_files: 5 }, { disabled: true });
  check(env.el("pick").disabled === true, "disabled 렌더 시 버튼 비활성");
  render(env, { max_files: 5 });

  // 2. 버튼 → 파일 선택창
  env.el("pick")._l.click();
  check(env.el("file")._clicked === 1, "버튼 클릭이 파일 선택창(input)을 연다");

  // 3. 사진 3장 + 영상 1개 → 한 묶음, 사진은 JPEG 1440px·0.88, 영상은 그대로
  env = makeEnv(); render(env, { max_files: 10, max_dim: 1440, quality: 0.88, max_video_mb: 40 });
  env.el("file")._l.change({ target: { files: [fakeFile("IMG_1.HEIC.PNG", "image/png", 4032, 3024, 12e6), fakeFile("세로.JPG", "image/jpeg", 3024, 4032, 9e6), fakeFile("small.webp", "image/webp", 800, 600, 50e3), fakeFile("v.mp4", "video/mp4", 0, 0, 5e6)] } });
  await tick(); await tick(); await tick();
  const v1 = values(env);
  check(v1.length === 1 && v1[0].dataType === "json", "묶음은 setComponentValue 한 번으로 전송");
  const b = v1[0] && v1[0].value;
  check(b && typeof b.batch_id === "string" && b.batch_id.length >= 8 && b.files.length === 4 && b.skipped.length === 0, `batch_id 부여·파일 4개·제외 0개 ${b && b.files.length}`);
  check(b && b.files[0].name === "IMG_1.HEIC.jpg" && b.files[0].type === "image/jpeg" && b.files[1].name === "세로.jpg", "사진은 JPEG로 바뀌고 이름 확장자도 .jpg");
  check(b && b.files[0].size === 300 * 1024 && b.files[0].data_b64 === Buffer.alloc(8, 7).toString("base64"), "사진 크기(약 300KB)·base64 본문(data: 머리말 제거)");
  check(b && b.files[3].name === "v.mp4" && b.files[3].type === "video/mp4" && b.files[3].data_b64 === Buffer.from("abc").toString("base64"), "영상은 원본 그대로 base64");
  check(env.canvas()._q === 0.88 && env.canvas()._type === "image/jpeg", "JPEG 품질 0.88");

  // 4. 긴 변 1440 제한 (순수 함수)
  const ui = env.ctx.window.__mobileUploader;
  check(JSON.stringify(ui.fitSize(4032, 3024, 1440)) === "[1440,1080]" && JSON.stringify(ui.fitSize(3024, 4032, 1440)) === "[1080,1440]" && JSON.stringify(ui.fitSize(800, 600, 1440)) === "[800,600]",
        "긴 변 1440px 축소(가로·세로), 작은 사진은 그대로");
  check(ui.jpgName("a.b.HEIC") === "a.b.jpg" && ui.jpgName("") === "photo.jpg" && ui.jpgName("noext") === "noext.jpg", "파일명 확장자 .jpg 치환");
  check(ui.isImage({ name: "x.HEIC", type: "" }) && ui.isImage({ name: "1000012345", type: "image/jpeg" }) && !ui.isImage({ name: "a.pdf", type: "application/pdf" })
        && ui.isVideo({ name: "A.MOV", type: "" }) && !ui.isVideo({ name: "a.jpg", type: "image/jpeg" }), "이미지/영상 판별(대문자·확장자 없음·MIME)");
  check(ui.stripDataUrl("data:image/jpeg;base64,QUJD") === "QUJD" && ui.stripDataUrl("QUJD") === "QUJD", "data: URL 머리말 제거");
  check(ui.newBatchId() !== ui.newBatchId(), "batch_id는 매번 다름");

  // 5. 브라우저가 못 여는 HEIC → 원본 그대로 보내 서버가 변환
  env = makeEnv(); render(env, { max_files: 10, max_dim: 1440, quality: 0.88, max_video_mb: 40 });
  env.el("file")._l.change({ target: { files: [fakeFile("a.heic", "image/heic", 0, 0, 3e6, { _undecodable: true })] } });
  await tick(); await tick();
  const h = values(env)[0] && values(env)[0].value;
  check(h && h.files.length === 1 && h.files[0].name === "a.heic" && h.files[0].data_b64 === Buffer.from("abc").toString("base64"), "HEIC(디코딩 불가)는 원본 그대로 전송");

  // 6. 개수·용량·형식 제한은 조용히 버리지 않고 skipped에 이유와 함께
  env = makeEnv(); render(env, { max_files: 2, max_dim: 1440, quality: 0.88, max_video_mb: 40 });
  env.el("file")._l.change({ target: { files: [fakeFile("a.jpg", "image/jpeg", 100, 100, 1e5), fakeFile("big.mp4", "video/mp4", 0, 0, 50 * 1024 * 1024), fakeFile("c.jpg", "image/jpeg", 100, 100, 1e5), fakeFile("메뉴.pdf", "application/pdf", 0, 0, 1e5)] } });
  await tick(); await tick(); await tick();
  const l = values(env)[0] && values(env)[0].value;
  check(l && l.files.length === 1 && l.files[0].name === "a.jpg", `남은 칸(2) 안에서만 받음 — 파일 ${l && l.files.map((f) => f.name)}`);
  const why = (n) => ((l && l.skipped.find((s) => s.name === n)) || {}).reason || "";
  check(/40MB/.test(why("big.mp4")) && /최대 개수/.test(why("c.jpg")) && /최대 개수/.test(why("메뉴.pdf")), `제외 이유 표시: ${JSON.stringify(l && l.skipped)}`);

  // 7. 아무것도 못 보낼 때도 사용자에게 알림, 선택 취소는 아무 일 없음
  env = makeEnv(); render(env, { max_files: 5, max_dim: 1440, quality: 0.88, max_video_mb: 40 });
  env.el("file")._l.change({ target: { files: [] } });
  await tick();
  check(values(env).length === 0, "선택 취소(빈 목록)는 전송하지 않음");
  env.el("file")._l.change({ target: { files: [fakeFile("a.pdf", "application/pdf", 0, 0, 1e4)] } });
  await tick(); await tick();
  check(values(env).length === 1 && values(env)[0].value.files.length === 0 && /보낼 수 있는 파일이 없어요/.test(env.el("status").textContent) && env.el("status").className === "err", "보낼 파일이 없으면 경고 문구");

  // 8. createImageBitmap이 없는 브라우저에서도 크래시 없이 처리 (원본 전송으로 대체)
  env = makeEnv({ noBitmap: true }); render(env, { max_files: 3, max_dim: 1440, quality: 0.88, max_video_mb: 40 });
  env.el("file")._l.change({ target: { files: [fakeFile("a.jpg", "image/jpeg", 100, 100, 1e5)] } });
  await tick(); await tick(); await tick();
  check(values(env).length === 1 && values(env)[0].value.files.length === 1, "createImageBitmap 없는 브라우저도 전송 성공(폴백)");

  console.log(fails ? "FAILURES: " + fails : "FAILURES: 0");
  process.exit(fails ? 1 : 0);
})();
