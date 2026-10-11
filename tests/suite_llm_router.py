"""대본 LLM 라우터: Claude → OpenAI → Gemini 자동 백업, 엔진별 요청 형식, 가드레일, 판에 박힌 패턴 금지·다시 쓰기, 플래너 연결"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import fixture, finish  # noqa: E402
import json
import os
import logging
logging.disable(logging.WARNING)

from PIL import Image

import core.llm.router as rt
from core.script_guard import COPY_TONE_RULES, SAFE_LINES, find_banned_style

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


GOOD = {"story": "...", "scenes": [{"scene_index": 1, "hook_title": "여기 진짜 한국?", "voiceover": "퇴근길에 시원한 한 잔이 당기는 날 있죠?"}]}
calls = []


def ok(name, payload=GOOD):
    def f(contents, system, temperature, max_tokens):
        calls.append(name)
        return json.dumps(payload, ensure_ascii=False)
    return f


def boom(name, exc):
    def f(*a, **k):
        calls.append(name)
        raise exc
    return f


os.environ.update({"ANTHROPIC_API_KEY": "k1", "OPENAI_API_KEY": "k2", "GEMINI_API_KEY": "k3"})

# ---------------------------------------------------------------- 1. 자동 백업 순서
import anthropic
import openai
import httpx2 as _hx

_req = _hx.Request("POST", "https://api.example.com")


def _resp(code):
    return _hx.Response(code, request=_req)


rt._overrides.update({"claude": ok("claude"), "openai": ok("openai"), "gemini": ok("gemini")})
data, eng = rt.generate_json(["x"], "sys")
check(eng == "claude" and calls == ["claude"], "평소: 1순위 Claude로 생성")
cases = {
    "Rate Limit(429)": anthropic.RateLimitError("rate", response=_resp(429), body=None),
    "잔액 부족(400 credit)": anthropic.BadRequestError("Your credit balance is too low", response=_resp(400), body=None),
    "서버 오류(529 overloaded)": anthropic.InternalServerError("overloaded", response=_resp(529), body=None),
    "네트워크": anthropic.APIConnectionError(request=_req),
}
for label, exc in cases.items():
    calls.clear()
    rt._overrides["claude"] = boom("claude", exc)
    data, eng = rt.generate_json(["x"], "sys")
    check(eng == "openai" and calls == ["claude", "openai"] and data["scenes"][0]["voiceover"],
          f"Claude {label} → 즉시 GPT로 전환해 대본 완성")
calls.clear()
rt._overrides["claude"] = ok("claude", None)
rt._overrides["claude"] = lambda *a: (calls.append("claude"), "여기 대본입니다 (JSON 아님)")[1]
data, eng = rt.generate_json(["x"], "sys")
check(eng == "openai" and calls == ["claude", "openai"], "Claude 응답이 JSON이 아니어도 다음 엔진으로")
calls.clear()
rt._overrides["openai"] = boom("openai", openai.RateLimitError("rate", response=_resp(429), body=None))
data, eng = rt.generate_json(["x"], "sys")
check(eng == "gemini" and calls == ["claude", "openai", "gemini"], "Claude·GPT 모두 실패 → 기존 Gemini가 마지막 안전망")
rt._overrides["gemini"] = boom("gemini", RuntimeError("quota"))
try:
    rt.generate_json(["x"], "sys")
    check(False, "전부 실패")
except rt.LLMUnavailable as e:
    check(len(e.errors) == 3 and "Claude" in e.errors[0] and "OpenAI" in e.errors[1] and "Gemini" in e.errors[2],
          "세 엔진 모두 실패 → 엔진별 실패 사유를 담은 LLMUnavailable (플래너는 템플릿 대본으로)")
rt._overrides.update({"claude": ok("claude"), "openai": ok("openai"), "gemini": ok("gemini")})
os.environ["ANTHROPIC_API_KEY"] = ""
calls.clear()
data, eng = rt.generate_json(["x"], "sys")
check(eng == "openai" and calls == ["openai"], "Claude 키가 없으면 GPT부터 (호출 시도 없음)")
os.environ["ANTHROPIC_API_KEY"] = "k1"
gemini_file = type("GeminiFile", (), {"uri": "files/abc"})()
check(rt.available_providers(None, ["프롬프트", gemini_file]) == ["gemini"],
      "레퍼런스 영상(Gemini 업로드 파일)이 섞이면 그걸 읽을 수 있는 Gemini만")

# ---------------------------------------------------------------- 2. 엔진별 실제 요청 형식 (가짜 SDK 클라이언트)
rt._overrides.clear()
img = Image.open(fixture("photo_pool.jpg"))
sent = {}


class FakeAnthropic:
    def __init__(self, **kw):
        sent["anthropic_client"] = kw
        self.messages = self

    def create(self, **kw):
        sent["anthropic"] = kw
        block = type("B", (), {"type": "text", "text": json.dumps(GOOD, ensure_ascii=False)})()
        return type("M", (), {"content": [block], "stop_reason": "end_turn", "stop_details": None})()


_orig_anth = anthropic.Anthropic
anthropic.Anthropic = FakeAnthropic
data, eng = rt.generate_json(["[씬 1 미디어]", img, "JSON만 출력"], "시스템", temperature=0.6)
anthropic.Anthropic = _orig_anth
kw = sent["anthropic"]
blocks = kw["messages"][0]["content"]
check(eng == "claude" and kw["model"] == "claude-haiku-5-5" and kw["system"] == "시스템"
      and [b["type"] for b in blocks] == ["text", "image", "text"] and blocks[1]["source"]["media_type"] == "image/jpeg"
      and kw["output_config"] == {"effort": "medium"} and "temperature" not in kw and "thinking" not in kw,
      "Claude 요청: claude-haiku-5-5, 사진은 base64 이미지 블록(순서 유지), effort medium, temperature 미전송(Haiku 5.5는 기본값만)")
check(sent["anthropic_client"].get("max_retries") == 1, "SDK 재시도 1회 뒤 즉시 다음 엔진")
os.environ["ANTHROPIC_MODEL"] = "claude-opus-5-5"
calls.clear()
rt._overrides["openai"] = ok("openai")
data, eng = rt.generate_json(["x"], "sys")
check(eng == "openai", "허용 목록 밖 모델(예: Opus)로 바꾸면 가드레일이 막고 GPT로 (고가 모델 무단 호출 방지)")
os.environ.pop("ANTHROPIC_MODEL")
rt._overrides.clear()


class FakeOpenAI:
    def __init__(self, **kw):
        self.chat = type("C", (), {"completions": self})()

    def create(self, **kw):
        sent["openai"] = kw
        msg = type("Msg", (), {"content": json.dumps(GOOD, ensure_ascii=False), "refusal": None})()
        return type("R", (), {"choices": [type("Ch", (), {"message": msg, "finish_reason": "stop"})()]})()


_orig_oa = openai.OpenAI
openai.OpenAI = FakeOpenAI
os.environ["ANTHROPIC_API_KEY"] = ""
data, eng = rt.generate_json(["[씬 1 미디어]", img, "JSON만 출력"], "시스템", temperature=0.6)
openai.OpenAI = _orig_oa
os.environ["ANTHROPIC_API_KEY"] = "k1"
ok_ = sent["openai"]
parts = ok_["messages"][1]["content"]
check(eng == "openai" and ok_["model"] == "gpt-4o-mini" and ok_["response_format"] == {"type": "json_object"}
      and ok_["messages"][0] == {"role": "system", "content": "시스템"}
      and parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"),
      "GPT 요청: gpt-4o-mini, JSON 모드, 사진은 data URI 이미지")

# ---------------------------------------------------------------- 3. 판에 박힌 패턴 금지 + 한 번 다시 쓰기
check(all(find_banned_style(t) for t in ("맥주가 시원하거든요.", "분위기도 좋고요.", "솔직히 최고예요.", "근데 여기 진짜예요.", "시원하게 되고요"))
      and not any(find_banned_style(t) for t in ("퇴근길에 한 잔 당길 때, 정답은 여기예요.", "이 조합, 반칙이에요!", "어디 가야 할지 모르겠죠?")),
      "금지 패턴: '~거든요', '~고요/~되고요', '솔직히', '근데' (질문·단언·감탄형은 통과)")
check(all(find_banned_style(t) for t in ("메뉴 고민이 싹 사라진다요.", "여기서 다 먹는다요!", "자리가 있다요."))
      and not any(find_banned_style(t) for t in ("여기가 바로 바다요?", "비벼볼까요?", "완벽하네요.")),
      "평서형에 '요'를 붙인 비문('사라진다요')도 다시 쓰기 대상 (명사+요 '바다요'는 허용)")
check("엄격 금지" in COPY_TONE_RULES and "TPO" in COPY_TONE_RULES and "3자 큐레이터" in COPY_TONE_RULES and "25~30초" in COPY_TONE_RULES
      and "추임새 의무" not in COPY_TONE_RULES and all(not find_banned_style(l) for ls in SAFE_LINES.values() for l in ls),
      "공통 프롬프트: 3자 큐레이터·TPO·25~30초 리듬·금지 패턴 (예전 '솔직히·근데' 의무 삭제), 대체 문구도 금지 패턴 없음")
bad = {"story": "", "scenes": [{"scene_index": 1, "voiceover": "솔직히 여기 맥주가 진짜 시원하거든요."},
                               {"scene_index": 2, "voiceover": "창가 자리에 앉으면 계곡이 먼저 들려요."}]}
rt._overrides["claude"] = lambda c, s, t, m: json.dumps({"fixes": [{"scene_index": 1, "voiceover": "퇴근길 시원한 한 잔이 당길 땐, 여기 맥주가 정답이에요."}]},
                                                        ensure_ascii=False)
fixed, log = rt.repair_scene_lines(json.loads(json.dumps(bad, ensure_ascii=False)), find_banned_style, "sys", "rules")
check(fixed["scenes"][0]["voiceover"].startswith("퇴근길") and fixed["scenes"][1]["voiceover"] == bad["scenes"][1]["voiceover"]
      and len(log) == 1, "규칙 어긴 줄만 모아 한 번 다시 쓰게 함 (지킨 줄은 그대로)")

# ---------------------------------------------------------------- 4. 플래너 연결 (사장님): 엔진 기록 + 남은 위반은 템플릿으로
from core.planners.owner import plan_owner_storyboard
plan_reply = {"story": "...", "scenes": [
    {"scene_index": 1, "role": "exterior", "hook_title": "모르면 손해인 곳", "voiceover": "솔직히 여기 분위기 진짜 좋거든요."},
    {"scene_index": 2, "role": "signature", "voiceover": "퇴근길에 시원한 한 잔이 당기는 날 있죠?"},
    {"scene_index": 3, "role": "interior", "voiceover": "근데 단체 모임도 편하고요."},
    {"scene_index": 4, "role": "cta", "voiceover": "x"}]}
step = {"n": 0}


def claude_seq(contents, system, temperature, max_tokens):
    step["n"] += 1
    if step["n"] == 1:
        assert "3자 큐레이터" in contents[-1] and "엄격 금지" in contents[-1]
        return json.dumps(plan_reply, ensure_ascii=False)
    return json.dumps({"fixes": [{"scene_index": 1, "voiceover": "가평에서 맥주 한 잔 하기 좋은 밤, 여기예요."},
                                 {"scene_index": 3, "voiceover": "근데 단체 모임도 편하거든요."}]}, ensure_ascii=False)


rt._overrides = {"claude": claude_seq}
sb = plan_owner_storyboard("설맥 가평현리점", "맥주얼음 맥주, 넓은 매장", "food", search_keyword="설맥",
                           media_paths=[fixture("photo_pool.jpg"), fixture("photo_room.jpg"), fixture("photo_pool.jpg"), fixture("photo_room.jpg")], api_key="g")
rt._overrides = {}
vo = [s["voiceover"] for s in sb]
check(sb[0]["script_engine"] == "claude" and step["n"] == 2, f"사장님 대본: Claude로 생성 + 규칙 보정 1회 (엔진 기록 {sb[0]['script_engine']})")
check(vo[0].startswith("가평에서 맥주") and vo[1] == "퇴근길에 시원한 한 잔이 당기는 날 있죠?" and len(vo) == 4 and not any(find_banned_style(v) for v in vo),
      f"보정된 줄은 반영, 보정해도 위반이면 템플릿으로 → 최종 대사에 금지 패턴 0개 {vo}")
check(vo[-1] == "네이버에서 설맥 검색해 보세요!" and all(s.get("voiceover") for s in sb),
      "출력 형식은 그대로 (씬별 voiceover → TTS·BGM 덕킹·렌더로 그대로 전달)")

# Gemini 키 없이 Claude 키만 있어도 대본 엔진이 돌아야 함 (예전엔 Gemini 키가 없으면 바로 템플릿)
os.environ["ANTHROPIC_API_KEY"], os.environ["GEMINI_API_KEY"] = "test-claude", ""
check(rt.script_llm_ready(None), "Claude 키만 있어도 대본 엔진 사용 가능")
step["n"] = 0
rt._overrides = {"claude": claude_seq}
sb2 = plan_owner_storyboard("설맥 가평현리점", "맥주얼음 맥주, 넓은 매장", "food", search_keyword="설맥",
                            media_paths=[fixture("photo_pool.jpg"), fixture("photo_room.jpg")], api_key=None)
rt._overrides = {}
os.environ["ANTHROPIC_API_KEY"] = ""
check(sb2[0]["script_engine"] == "claude", f"Gemini 키 없이 Claude 키만 → Claude 대본 (엔진 기록 {sb2[0]['script_engine']})")
os.environ["OPENAI_API_KEY"] = ""
check(not rt.script_llm_ready(None), "키가 하나도 없으면 템플릿 (엔진 호출 안 함)")

# 어미 리듬: 질문형·감탄형 최소 1개씩, 평서 '~요.' 연속 금지 → 고칠 줄과 방향을 짚어 한 번에 보정
from core.script_guard import ending_kind, length_rule, line_char_range, rhythm_issues, script_violation_finder
flat = ["이 날은 구운 반건오징어가 제격이에요.", "매콤한 무침과 면까지 나눠 먹기 좋아요.", "팥빙수로 달콤하게 마무리하는 날이죠.",
        "단체 모임 자리 걱정은 덜어도 돼요.", "바 좌석과 부스가 나란히 있어 혼술도 편해요.", "바삭한 먹태가 안주로 제격이네요."]
iss = rhythm_issues(flat)
check([ending_kind(t) for t in ("어때요?", "딱이죠!", "제격이네요.", "편해요.")] == ["question", "exclaim", "exclaim", "plain"],
      "어미 판별: 질문형 / 감탄·확신형(!·죠·네요) / 평서")
check("질문형" in iss.get(1, "") and 4 in iss and len(iss) == 2, f"질문형 없음 + 평서 연속 → 고칠 줄 지정 {iss}")
good = ["퇴근길 맥주 생각날 때, 구운 오징어가 제격이죠!", "비 오는 날 매콤한 무침에 소면, 어때요?", "넓은 매장이라 단체 모임도 편해요.",
        "혼자 쉬는 밤엔 바삭한 먹태가 딱이죠!"]
check(rhythm_issues(good) == {}, "질문·감탄이 섞이고 평서 연속이 없으면 보정 없음")
find = script_violation_finder(flat)
check(find(flat[1]).startswith("질문형") and find(flat[0]) is None and find("근데 맛있어요.") == "'근데'",
      "보정 탐지기 = 판에 박힌 패턴 + 어미 리듬")
lo, hi, lo_t, hi_t = line_char_range(6)
check((lo, hi) == (26, 29) and 150 <= lo_t < hi_t <= 180 and "26~29자" in length_rule(6),
      f"분량: 6줄이면 줄당 {lo}~{hi}자, 합계 {lo_t}~{hi_t}자 (나레이션 24~26초 실측 환산)")
check("~어때요?" in COPY_TONE_RULES and "~딱이죠!" in COPY_TONE_RULES and "평서 어미는 두 문장 연속 금지" in COPY_TONE_RULES
      and "27~29초" in COPY_TONE_RULES, "공통 프롬프트에 어미 리듬·분량 규칙")
finish(fails)
