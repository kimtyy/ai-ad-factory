"""시각 품질 스코어러(core/media/visual_scorer) + highlight_selector 연동: 선명도·조도·급패닝·흔들림·컷 감점/원천 제외 (합성 프레임·더미 영상)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402
import os
import shutil
import subprocess
import logging
logging.disable(logging.WARNING)

for _k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ[_k] = ""

import cv2
import imageio_ffmpeg
import numpy as np

from core.media import visual_scorer as vsc
from core.media.audio_stt import Sentence, Span, Transcript, Word
from core.planners import highlight_selector as hs

FF = imageio_ffmpeg.get_ffmpeg_exe()
fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


rng = np.random.RandomState(7)


def textured(seed=0, h=180, w=320):
    """질감이 풍부한 합성 프레임 (선명, 이동 추정이 잘 되도록 저주파 + 고주파 혼합)"""
    r = np.random.RandomState(seed)
    base = cv2.resize(r.randint(0, 255, (h // 8, w // 8), dtype=np.uint8), (w, h), interpolation=cv2.INTER_CUBIC)
    fine = r.randint(0, 255, (h, w), dtype=np.uint8)
    return cv2.addWeighted(base, 0.6, fine, 0.4, 0)


N = 20                                    # 5초 분량 (4fps)
BASE = textured(1)


def steady():
    return [BASE.copy() for _ in range(N)]


def shifted(dxs):
    return [np.roll(BASE, int(dx), axis=1) for dx in dxs]


# ---------------------------------------------------------------- 1. 단일 속성별 판정
ok = vsc.analyze_frames(steady())
check(not ok.hard_reject and ok.score >= 0.85 and ok.frames == N,
      f"선명·적정 밝기·고정 화면 → 높은 점수 ({ok.score}, 선명도 {ok.sharpness}, 밝기 {ok.brightness})")

blur = vsc.analyze_frames([cv2.GaussianBlur(BASE, (0, 0), 6) for _ in range(N)])
check(blur.hard_reject and any("흐림" in r for r in blur.reasons) and blur.sharpness < vsc.BLUR_REJECT,
      f"심한 흐림 → 원천 제외 (선명도 {blur.sharpness}) {blur.reasons}")

dark = vsc.analyze_frames([(BASE * 0.05).astype(np.uint8) for _ in range(N)])
check(dark.hard_reject and any("어두" in r for r in dark.reasons), f"너무 어두움 → 원천 제외 (밝기 {dark.brightness}) {dark.reasons}")

dim = vsc.analyze_frames([(BASE * 0.35).astype(np.uint8) for _ in range(N)])
check(not dim.hard_reject and dim.score < ok.score, f"약간 어두움은 감점만 (밝기 {dim.brightness}, 점수 {dim.score} < {ok.score})")

blown = vsc.analyze_frames([np.clip(BASE.astype(np.int16) + 140, 0, 255).astype(np.uint8) for _ in range(N)])
check(blown.hard_reject or blown.score < ok.score - 0.1, f"과노출은 제외/감점 (밝기 {blown.brightness}, 점수 {blown.score}, 제외 {blown.hard_reject})")

fast = vsc.analyze_frames(shifted([80 * i for i in range(N)]))
check(fast.hard_reject and any("패닝" in r for r in fast.reasons) and fast.pan_speed > vsc.PAN_REJECT,
      f"급패닝 → 원천 제외 (초당 {fast.pan_speed:.2f}폭) {fast.reasons}")

wild = vsc.analyze_frames(shifted([14 if i % 2 else -14 for i in range(N)]))
check(wild.hard_reject and any("흔들림" in r for r in wild.reasons) and wild.jitter > vsc.JITTER_REJECT,
      f"심한 좌우 흔들림 → 원천 제외 (떨림 {wild.jitter}) {wild.reasons}")

mild = vsc.analyze_frames(shifted([3 if i % 2 else -3 for i in range(N)]))
check(not mild.hard_reject and ok.score > mild.score > wild.score and any("흔들림" in r for r in mild.reasons),
      f"약한 흔들림은 감점만 (점수 {ok.score} > {mild.score} > {wild.score})")

slow = vsc.analyze_frames(shifted(list(range(N))))                      # 프레임당 1px 느린 팬
check(not slow.hard_reject and slow.pan_speed < vsc.PAN_SOFT and slow.score > 0.8, f"느린 패닝은 정상 (초당 {slow.pan_speed:.3f}폭, {slow.score})")

other = textured(2)
cut2 = vsc.analyze_frames([BASE] * 7 + [other] * 7 + [BASE] * 6)
check(cut2.cuts == 2 and not cut2.hard_reject and cut2.score < ok.score, f"컷 2회는 감점만 (컷 {cut2.cuts}, 점수 {cut2.score})")
cut4 = vsc.analyze_frames([BASE] * 4 + [other] * 4 + [BASE] * 4 + [other] * 4 + [BASE] * 4)
check(cut4.hard_reject and cut4.cuts >= vsc.CUT_REJECT, f"컷 {cut4.cuts}회 → 몽타주로 원천 제외")

few = vsc.analyze_frames(steady()[:3])
check(not few.hard_reject and few.score == 0.5 and few.frames == 3, "프레임이 너무 적으면 판단 보류 (중립 0.5, 불합격 없음)")

# ---------------------------------------------------------------- 2. 점수 규칙 · 주입형 스코어러
check(0.0 <= min(ok.score, dark.score, fast.score) and max(ok.score, blur.score, wild.score) <= 1.0, "점수는 항상 0~1")
scorer = vsc.VisualScorer(frame_loader=lambda s, e: steady())
a = scorer(10.0, 28.0)
b = scorer(10.0, 28.0)
check(a is b and scorer.calls == 1, "같은 창은 캐시 (재분석 없음)")


def boom(s, e):
    raise RuntimeError("디코딩 실패")


bad = vsc.VisualScorer(frame_loader=boom)(0.0, 10.0)
check(not bad.hard_reject and bad.score == 0.5 and "실패" in " ".join(bad.reasons), "프레임을 못 읽어도 후보를 죽이지 않음 (판단 보류)")

# ---------------------------------------------------------------- 3. 실제 영상 경로 (ffmpeg → 낮은 해상도 샘플링 → 판정)
W = tmp("vis_score_")
try:
    def make(name, vf=None, extra=()):
        p = os.path.join(W, name)
        args = [FF, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=15:d=10"]
        if vf:
            args += ["-vf", vf]
        subprocess.run(args + ["-c:v", "libx264", "-pix_fmt", "yuv420p", *extra, p], check=True)
        return p

    v_ok = make("ok.mp4")
    v_blur = make("blur.mp4", "boxblur=14")
    v_dark = make("dark.mp4", "lutyuv=y=val*0.04:u=128:v=128")
    f = vsc.load_gray_frames(v_ok, 1.0, 7.0)
    check(22 <= len(f) <= 26 and f[0].shape[1] == vsc.FRAME_WIDTH, f"6초 구간만 4fps·폭 {vsc.FRAME_WIDTH}px로 추출 ({len(f)}장, {f[0].shape})")
    s_ok = vsc.VisualScorer(v_ok)(1.0, 9.0)
    s_blur = vsc.VisualScorer(v_blur)(1.0, 9.0)
    s_dark = vsc.VisualScorer(v_dark)(1.0, 9.0)
    check(not s_ok.hard_reject, f"정상 영상은 통과 (점수 {s_ok.score}, {s_ok.reasons})")
    check(s_blur.hard_reject and s_dark.hard_reject, f"흐린 영상·어두운 영상은 원천 제외 (흐림 {s_blur.reasons}, 어두움 {s_dark.reasons})")
    port = fixture("phone_portrait.mov")
    fp = vsc.load_gray_frames(port, 0.0, 3.0)
    check(fp and fp[0].shape[0] > fp[0].shape[1], f"회전 메타데이터(세로 촬영)를 반영해 읽음 {fp[0].shape if fp else None}")
    hdr = vsc.VisualScorer(fixture("phone_hdr.mov"))(0.0, 3.0)
    check(hdr.frames > 0 and not hdr.reasons[:1] == ["시각 분석 실패: RuntimeError"], f"HDR 원본도 톤매핑해 분석 ({hdr.frames}프레임, 점수 {hdr.score})")
finally:
    shutil.rmtree(W, ignore_errors=True)


# ---------------------------------------------------------------- 4. highlight_selector 연동
def long_talk():
    chatter = ["오늘 날씨가 정말 좋네요", "어제 뭐 하셨어요", "주말에는 어디 가세요", "요즘 사람이 많더라고요", "커피 한 잔 하고 싶어요"]
    concept = ["주문이 들어오면 바로 무쳐요", "겉절이는 미리 만들어 두지 않아요", "그 자리에서 신선하게 버무려요",
               "무치자마자 상에 올려요", "아삭한 식감이 바로 살아 있어요", "신선한 겉절이만 내요"]
    sents, words, spans, t, n = [], [], [], 0.0, 0
    while t < 175.0:
        txt = concept[n % len(concept)] if (40 <= t < 70 or 110 <= t < 140) else chatter[n % len(chatter)]
        words.append(Word(txt, t, t + 2.9, 0.9))
        sents.append(Sentence(txt, round(t, 2), round(t + 2.9, 2), (len(words) - 1, len(words)), 0.9, 0.02))
        spans.append(Span(t - 0.05, t + 2.95))
        t += 3.5
        n += 1
    return Transcript(3, "k", "ko", "fake", 180.0, words, sents, spans)


CONCEPT = "주문 즉시 무쳐내는 신선한 겉절이"
tr = long_talk()
GOOD = steady()
BLURRY = [cv2.GaussianBlur(BASE, (0, 0), 6) for _ in range(N)]


def region_loader(bad_regions):
    def load(s, e):
        mid = (s + e) / 2
        return BLURRY if any(a <= mid < b for a, b in bad_regions) else GOOD
    return load


no_visual = hs.select_highlights(tr, CONCEPT, use_llm=False)
check(any(40 <= (c.start_sec + c.end_sec) / 2 < 70 for c in no_visual), "기준선: 시각 점수 없이는 컨셉 구간 A(40~70초)도 후보에 오름")
sc = vsc.VisualScorer(frame_loader=region_loader([(40, 70)]))
with_visual = hs.select_highlights(tr, CONCEPT, use_llm=False, visual_scorer=sc)
check(with_visual and not any(40 <= (c.start_sec + c.end_sec) / 2 < 70 for c in with_visual),
      f"흐린 구간 A는 원천 제외, 나머지에서 후보 선정 ({[round(c.start_sec) for c in with_visual]})")
top = with_visual[0]
check(top.visual and top.visual["hard_reject"] is False and top.visual_score is not None and top.visual["frames"] == N,
      f"후보에 시각 분석 상세 기록 (점수 {top.visual_score}, 선명도 {top.visual['sharpness']})")
ordered = sorted(with_visual, key=lambda c: c.start_sec)
check(all(15.0 <= c.window_sec <= 22.0 for c in with_visual) and all(ordered[i].end_sec <= ordered[i + 1].start_sec for i in range(len(ordered) - 1)),
      "시각 필터 후에도 창 15~22초·비겹침 유지")

# 가중 합산: 텍스트가 같으면 화면이 더 좋은 쪽이 앞선다
shaky = shifted([4 if i % 2 else -4 for i in range(N)])


def prefer_b(s, e):
    return shaky if 40 <= (s + e) / 2 < 70 else GOOD


pref = hs.select_highlights(tr, CONCEPT, use_llm=False, visual_scorer=vsc.VisualScorer(frame_loader=prefer_b))
in_a = [c for c in pref if 40 <= (c.start_sec + c.end_sec) / 2 < 70]
in_b = [c for c in pref if 110 <= (c.start_sec + c.end_sec) / 2 < 140]
check(pref and in_b and (not in_a or max(c.visual_score for c in in_a) < min(c.visual_score for c in in_b))
      and 110 <= (pref[0].start_sec + pref[0].end_sec) / 2 < 140,
      f"텍스트 점수가 비슷하면 화면이 안정적인 구간이 상위 ({[round(c.start_sec) for c in pref]}, 시각 {[c.visual_score for c in pref]})")

# 전부 불량이면 후보 없음 (원천 컷)
all_bad = hs.select_highlights(tr, CONCEPT, use_llm=False, visual_scorer=vsc.VisualScorer(frame_loader=lambda s, e: BLURRY))
check(all_bad == [], "모든 창이 시각 불량이면 빈 목록 (텍스트가 좋아도 영상이 무너진 구간은 내보내지 않음)")

# 분석 횟수 상한 (영상 디코딩 비용 제한) + float 스코어러 하위 호환
calls = {"n": 0}


def counting(s, e):
    calls["n"] += 1
    return 0.5


hs.select_highlights(tr, CONCEPT, use_llm=False, visual_scorer=counting)
check(0 < calls["n"] <= hs.VISUAL_MAX_CHECKS, f"시각 분석 횟수 상한 준수 ({calls['n']} ≤ {hs.VISUAL_MAX_CHECKS})")
legacy = hs.select_highlights(tr, CONCEPT, use_llm=False, visual_scorer=lambda s, e: 1.0 if 110 <= (s + e) / 2 < 140 else 0.0)
check(len(legacy) == 3 and legacy[0].visual_score == 1.0 and legacy[0].visual and legacy[0].visual["hard_reject"] is False,
      "0~1 float 스코어러도 그대로 동작 (하위 호환, 원천 제외 없음)")
check(abs(hs.VISUAL_WEIGHT - 0.25) < 1e-9, "가중치: 텍스트 0.75 + 시각 0.25")
finish(fails)
