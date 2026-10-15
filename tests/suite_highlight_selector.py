"""롱폼 하이라이트 선정(core/planners/highlight_selector): 창 15~22초·총 ≤25초·비겹침 3개, 트랙 A/C/B 경계값, 무음 스냅·마진·틈 클램프,
LLM은 세그먼트 ID만(시간은 코드), 결정적 폴백, 경고 플래그(원문 불변), 음악 지배 휴리스틱 (가짜 Transcript, 외부 호출 없음)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, finish  # noqa: E402
import os
import logging
logging.disable(logging.WARNING)

# 외부 LLM 호출이 절대 일어나지 않도록 키 제거 (결정적 폴백 경로를 검증)
for _k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ[_k] = ""

from core.media.audio_stt import Sentence, Span, Transcript, Word
from core.planners import highlight_selector as hs

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


CONCEPT = "주문 즉시 무쳐내는 신선한 겉절이"


def make_tr(items, duration=None, spans=None, rms=None, hop=0.5):
    """items: [(start, end, text)] → Transcript (단어는 문장 안에서 균등 분할, 발화 구간은 문장±0.05초)"""
    sents, words = [], []
    for s, e, t in items:
        toks = t.split() or [t]
        i0 = len(words)
        step = (e - s) / len(toks)
        for k, tok in enumerate(toks):
            words.append(Word(tok, round(s + k * step, 3), round(s + (k + 1) * step, 3), 0.9))
        sents.append(Sentence(t, s, e, (i0, len(words)), 0.9, 0.02))
    sp = spans if spans is not None else [Span(max(0.0, s - 0.05), e + 0.05) for s, e, _ in items]
    dur = duration or (items[-1][1] + 5.0)
    return Transcript(schema_version=3, source_key="k", language="ko", model="fake", duration_sec=dur,
                      words=words, sentences=sents, speech_spans=sp, rms_hop_sec=hop, rms_db=list(rms or []))


def long_talk():
    """180초: 잡담 / 컨셉 구간 A(40~70) / 잡담 / 컨셉 구간 B(110~140) / 잡담. 문장 약 3초, 사이 0.6초"""
    chatter = ["오늘 날씨가 정말 좋네요", "어제 뭐 하셨어요", "주말에는 어디 가세요", "요즘 사람이 많더라고요", "커피 한 잔 하고 싶어요"]
    concept = ["주문이 들어오면 바로 무쳐요", "겉절이는 미리 만들어 두지 않아요", "그 자리에서 신선하게 버무려요",
               "무치자마자 상에 올려요", "아삭한 식감이 바로 살아 있어요", "신선한 겉절이만 내요"]
    items, t, n = [], 0.0, 0
    while t < 175.0:
        in_a, in_b = 40 <= t < 70, 110 <= t < 140
        text = (concept[n % len(concept)] if (in_a or in_b) else chatter[n % len(chatter)])
        items.append((round(t, 2), round(t + 2.9, 2), text))
        t += 3.5
        n += 1
    return make_tr(items, duration=180.0)


# ---------------------------------------------------------------- 1. 창 길이·총 길이·비겹침·컨셉 적합
tr = long_talk()
cands = hs.select_highlights(tr, CONCEPT, use_llm=False)
check(len(cands) == 3, f"후보 3개 산출 ({len(cands)}개)")
check(all(15.0 <= c.window_sec <= 22.0 for c in cands), f"창 길이 15~22초 {[c.window_sec for c in cands]}")
check(all(c.cta_sec == 3.0 and c.total_sec <= 25.0 and c.total_sec >= 18.0 for c in cands), f"CTA 3초 포함 총 18~25초 {[c.total_sec for c in cands]}")
ordered = sorted(cands, key=lambda c: c.start_sec)
check(all(ordered[i].end_sec <= ordered[i + 1].start_sec for i in range(len(ordered) - 1)), "후보끼리 시간이 겹치지 않음")
check([c.rank for c in cands] == [1, 2, 3], "순위(rank) 부여")


def in_concept_region(c):
    mid = (c.start_sec + c.end_sec) / 2
    return 40 <= mid < 70 or 110 <= mid < 140


check(in_concept_region(cands[0]) and in_concept_region(cands[1]), f"상위 2개는 컨셉 구간에 위치 ({[round(c.start_sec) for c in cands]})")
check(cands[0].concept_score > cands[2].concept_score, "컨셉 적합도 점수 내림차순")
check(all(c.first_sentence <= c.last_sentence for c in cands), "문장 ID 범위 제공")
check(hs.select_highlights(tr, CONCEPT, use_llm=False)[0].to_dict() == cands[0].to_dict(), "결정적 폴백: 같은 입력 → 같은 결과")

# ---------------------------------------------------------------- 2. 경계: 스냅·마진·틈 클램프
spans = [(0.0, 9.0), (10.0, 20.0), (20.4, 30.0), (35.0, 40.0)]
a, b = hs.adjust_boundaries(10.0, 40.0, [(35.0, 40.0), (0.0, 5.0)], 100.0)
check(a == 9.7 and b == 40.4, f"넓은 무음: 앞 0.3초·뒤 0.4초 마진 ({a}, {b})")
a, b = hs.adjust_boundaries(10.0, 30.0, [(0.0, 9.8), (10.0, 30.0), (30.2, 35.0)], 100.0)
check(a == 9.9 and b == 30.1, f"인접 발화 침범 시 틈의 절반으로 클램프 ({a}, {b})")
a, b = hs.adjust_boundaries(10.3, 29.8, [(0.0, 9.0), (9.9, 30.0), (31.0, 35.0)], 100.0)
check(a == 9.6 and b == 30.4, f"무음 스냅: 발화 시작/끝점으로 이동 후 마진 ({a}, {b})")
a, b = hs.adjust_boundaries(14.0, 36.0, [(10.0, 20.0), (20.0, 40.0)], 100.0)
check(a == 14.0 and b == 36.0, f"연속 발화 한가운데는 마진 없음 ({a}, {b})")
a, b = hs.adjust_boundaries(0.1, 99.9, [(0.0, 100.0)], 100.0)
check(a >= 0.0 and b <= 100.0, "영상 처음/끝을 벗어나지 않음")
a, b = hs.adjust_boundaries(0.2, 20.0, [(5.0, 6.0)], 100.0)
check(a == 0.0, f"영상 시작 근처 경계는 0초로 클램프 ({a})")
check(hs.FADE_SEC == 0.1 and cands[0].fade_in_sec == 0.1 and cands[0].fade_out_sec == 0.1, "오디오 페이드 100ms")

# ---------------------------------------------------------------- 3. 트랙 A/C/B 경계값
check([hs.classify_track(x) for x in (1.0, 0.60, 0.599, 0.45, 0.30, 0.299, 0.0)] == ["A", "A", "C", "C", "C", "B", "B"],
      "커버리지 임계값: ≥60% A / 30~60% C / <30% B")


def tr_cov(cov):
    items = [(round(i * 3.5, 2), round(i * 3.5 + 2.9, 2), "주문하면 바로 무쳐요 겉절이예요") for i in range(6)]
    c = hs.select_highlights(make_tr(items, duration=40.0), CONCEPT, use_llm=False)[0]
    # 창 길이에 맞춰 원하는 커버리지가 되도록 발화 구간을 하나로 만든다
    want = cov * c.window_sec
    spans2 = [Span(c.start_sec, c.start_sec + want)] if want > 0 else []
    tr2 = make_tr(items, duration=40.0, spans=spans2)
    return hs._build_candidate(tr2, hs._Window(c.first_sentence, c.last_sentence, c.start_sec, c.end_sec, 0.5), "fallback")


for cov, expect in ((0.80, "A"), (0.61, "A"), (0.59, "C"), (0.31, "C"), (0.29, "B"), (0.05, "B")):
    got = tr_cov(cov)
    check(got.track == expect and abs(got.coverage - cov) < 0.01, f"창 커버리지 {cov:.2f} → 트랙 {expect} (실제 {got.coverage:.2f}/{got.track})")

# ---------------------------------------------------------------- 4. LLM: 세그먼트 ID만, 시간은 코드
sent = {}


def good_llm(system, prompt):
    sent["prompt"] = prompt
    pool_ids = []
    import re
    for m in re.finditer(r"start_id=(\d+) end_id=(\d+)", prompt):
        pool_ids.append((int(m.group(1)), int(m.group(2))))
    pick = pool_ids[-1]                                   # 일부러 마지막(점수 낮은) 후보를 고름
    return {"picks": [{"start_id": pick[0], "end_id": pick[1], "reason": "테스트", "start_sec": 1.0, "end_sec": 2.0}]}


llm_c = hs.select_highlights(tr, CONCEPT, llm_fn=good_llm, use_llm=True)
check(llm_c and llm_c[0].source == "llm" and "start_id=" in sent["prompt"] and "초 단위 시간 금지" in sent["prompt"],
      "LLM 호출: 후보 메뉴(문장 번호)만 제시")
check(llm_c[0].start_sec > 2.0 or llm_c[0].end_sec - llm_c[0].start_sec >= 15.0, "LLM이 준 초(start_sec 1.0)는 무시, 시간은 코드가 계산")
check(len(llm_c) == 3 and len({(c.first_sentence, c.last_sentence) for c in llm_c}) == 3, "LLM이 덜 고르면 점수순으로 채워 3개")
fake_menu = hs.select_highlights(tr, CONCEPT, use_llm=True,
                                 llm_fn=lambda s, p: {"picks": [{"start_id": 9999, "end_id": 10001}, {"start_id": 0, "end_id": 1}]})
check(all(c.source == "fallback" for c in fake_menu) and len(fake_menu) == 3, "메뉴에 없는 ID는 버리고 결정적 폴백")


def boom(system, prompt):
    raise RuntimeError("LLM down")


check(all(c.source == "fallback" for c in hs.select_highlights(tr, CONCEPT, use_llm=True, llm_fn=boom)), "LLM 예외 시 폴백")
check(hs.select_highlights(tr, CONCEPT)[0].source == "fallback", "키가 없으면 LLM 호출 없이 결정적 폴백 (외부 호출 없음)")

# ---------------------------------------------------------------- 5. 자막 규격 · 경고 플래그(원문 불변)
c0 = cands[0]
cs = c0.caption_scenes
check(cs and cs[0]["start"] >= 0.0 and cs[0]["start"] < 1.5 and all(0 <= s["start"] < s["end"] <= c0.window_sec + 1e-6 for s in cs),
      f"자막 장면은 창 시작 기준 상대시간 (첫 장면 {cs[0]['start']}s, 마지막 끝 {cs[-1]['end']}s ≤ {c0.window_sec}s)")
orig = {s.text for s in tr.sentences}
check(all(s["text"] in orig for s in cs), "STT 원문 그대로 (의미 변경 없음, 공백 정리만)")

risky = [(0.0, 3.0, "겉절이 1인분에 9,900원 할인이에요"), (3.5, 6.5, "문의는 010-1234-5678 로 주세요"),
         (7.0, 10.0, "국민은행 123-456-7890 으로 입금해 주세요"), (10.5, 13.5, "식약처 인증 받은 신선한 겉절이예요"),
         (14.0, 17.0, "이메일은 shop@example.com 입니다"), (17.5, 20.5, "평범한 문장이에요 겉절이는 신선해요")]
rt = make_tr(risky, duration=30.0)
rc = hs.select_highlights(rt, CONCEPT, use_llm=False)[0]
kinds = {f["type"] for f in rc.flags}
check({"price_claim", "privacy_phone", "privacy_account", "spec_claim", "privacy_email"} <= kinds, f"가격·효능·개인정보 경고 플래그 {sorted(kinds)}")
check(all(f["text"] and "at" in f for f in rc.flags) and any(s["text"] == risky[1][2] for s in rc.caption_scenes),
      "플래그는 경고만 추가하고 자막 원문은 수정하지 않음")
check(not hs.scan_flags("3초면 펴져요 아주 가벼워요", 0.0), "일상 숫자 표현('3초')은 효능 주장으로 보지 않음")

# ---------------------------------------------------------------- 6. 음악 지배 휴리스틱
items = [(round(i * 3.5, 2), round(i * 3.5 + 2.9, 2), "주문하면 바로 무쳐요 겉절이예요") for i in range(6)]
hop = 0.5
n = int(40 / hop)


def rms_env(speech_db, bg_db, spans_):
    out = []
    for k in range(n):
        c = (k + 0.5) * hop
        out.append(speech_db if any(sp.start <= c <= sp.end for sp in spans_) else bg_db)
    return out


low_spans = [Span(0.0, 6.0)]                                   # 창의 일부만 발화 → 커버리지 낮음
loud = make_tr(items, duration=40.0, spans=low_spans, rms=rms_env(-20.0, -22.0, low_spans))
quiet = make_tr(items, duration=40.0, spans=low_spans, rms=rms_env(-20.0, -50.0, low_spans))
full_spans = [Span(0.0, 22.0)]
fullc = make_tr(items, duration=40.0, spans=full_spans, rms=rms_env(-20.0, -22.0, full_spans))
no_rms = make_tr(items, duration=40.0, spans=low_spans)
w = hs._Window(0, 5, 0.0, 22.0, 0.5)
check(hs._build_candidate(loud, w, "fallback").music_suspected is True, "비발화 구간 RMS ≥ -30dBFS & 커버리지 낮음 → music_suspected")
check(hs._build_candidate(quiet, w, "fallback").music_suspected is False, "비발화 구간이 조용하면 음악 의심 아님")
check(hs._build_candidate(fullc, w, "fallback").music_suspected is False, "발화 커버리지가 높으면 음악 의심 아님")
check(hs._build_candidate(no_rms, w, "fallback").music_suspected is False, "음량 포락선이 없으면 플래그 OFF (안전)")
check(hs._build_candidate(loud, w, "fallback").track == "B", "음악이 깔린 낮은 커버리지 창은 트랙 B")

# ---------------------------------------------------------------- 7. 시각 점수 훅 · 경계 케이스
fav = lambda s, e: 1.0 if 110 <= (s + e) / 2 < 140 else 0.0
vc = hs.select_highlights(tr, CONCEPT, use_llm=False, visual_scorer=fav)
check(vc[0].visual_score is not None and 110 <= (vc[0].start_sec + vc[0].end_sec) / 2 < 140, "시각 점수가 있으면 반영(선택 입력)")
check(hs.select_highlights(make_tr([(0.0, 3.0, "짧은 영상이에요")], duration=5.0), CONCEPT, use_llm=False) == [], "15초 창을 만들 수 없으면 빈 목록")
check(hs.select_highlights(Transcript(3, "k", "ko", "fake", 10.0), CONCEPT) == [], "문장이 없으면 빈 목록")
dead = make_tr([(0.0, 3.0, "겉절이 시작"), (15.0, 18.0, "겉절이 끝"), (18.5, 21.5, "무쳐요 바로"), (22.0, 25.0, "신선해요 겉절이"),
                (25.5, 28.5, "주문하면 바로 무쳐요")], duration=40.0)
dc = hs.select_highlights(dead, CONCEPT, use_llm=False)
check(all(c.start_sec >= 14.0 for c in dc), f"창 안에 6초 넘는 무음이 있는 구간은 제외 ({[round(c.start_sec, 1) for c in dc]})")

# ---------------------------------------------------------------- 8. Step 1 전사 결과와 연동 (캐시 왕복 후 선정)
from core.media import audio_stt as st
back = st.Transcript.from_dict(tr.to_dict())
check(hs.select_highlights(back, CONCEPT, use_llm=False)[0].to_dict() == cands[0].to_dict(), "Transcript 직렬화 왕복 후에도 같은 후보")
finish(fails)
