"""롱폼 하이라이트 실영상 피드백 3건: ① 시작/끝 지점 장면 전환 잔상 제거(컷 가드) ② 가로→세로 원본 비율 유지(블러 배경) 레이아웃과 자막 위치
③ TTS 영문 약어 발음 정규화 (HKG → 에이치케이지)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402
import os
import re
import shutil
import subprocess
import logging
logging.disable(logging.WARNING)

for _k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ[_k] = ""

import imageio_ffmpeg
from PIL import Image

from core import longform as lf
from core.media import audio_stt as st_mod
from core.media import visual_scorer as vsc
from core.planners.highlight_selector import HighlightCandidate
from core.renderers import highlight_renderer as hr
from core.tts import clean_tts_text, normalize_tts_pronunciation as norm, spell_acronyms

FF = imageio_ffmpeg.get_ffmpeg_exe()
fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


def ff(*a):
    return subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", *a], capture_output=True)


# ---------------------------------------------------------------- ③ TTS 영문 약어 정규화
check(norm("네이버에서 HKG를 검색해 보세요!") == "네이버에서 에이치케이지를 검색해 보세요!", f"HKG → 에이치케이지: {norm('네이버에서 HKG를 검색해 보세요!')}")
check("에이치케이지" in clean_tts_text("HKG의 그라인더, 네이버에서 HKG를 검색해 보세요!"), "TTS 합성 직전 정제(clean_tts_text)에도 적용")
check(norm("LED 조명과 USB 포트, CCTV") == "엘이디 조명과 유에스비 포트, 씨씨티브이", f"일반 약어 풀어 읽기: {norm('LED 조명과 USB 포트, CCTV')}")
check(norm("AI와 PC, TV, NASA") == "에이아이와 피씨, 티비, 나사", "단어로 읽는 약어는 사전 음가 (TV 티비, NASA 나사)")
check(norm("카페 ONE BAKERY BAR") == "카페 ONE BAKERY BAR", "상호의 대문자 영어 단어는 철자로 풀지 않고 그대로 (엔진이 단어로 읽음)")
check(norm("N+1 행사 10% 할인") == "엔 플러스 일 행사 10 퍼센트 할인", "기존 N+1·% 정규화는 그대로")
check(norm("Hkg grinder st.john") == "Hkg grinder st.john" and spell_acronyms("") == "", "소문자·혼합 표기는 건드리지 않음")
check("HKG" in "네이버에서 HKG를 검색해 보세요!", "자막 원문은 그대로 (정규화는 TTS 입력에만)")

# ---------------------------------------------------------------- 합성 원본 (질감이 있는 색 구간 + 컷 위치를 아는 영상)
W = tmp("lf_fix_")


def noisy_color(color, d, s="640x360", r=15):
    return ["-f", "lavfi", "-i", f"color=c={color}:s={s}:r={r}:d={d},noise=alls=22:allf=t"]


try:
    # 0-10 빨강 / 10-28 노랑 / 28-60 파랑 (컷: 10.0, 28.0). 회색조 밝기가 달라야 컷이 검출됨(순빨강·순초록은 밝기가 같음)
    src = os.path.join(W, "cuts.mp4")
    ff(*noisy_color("red", 10), *noisy_color("yellow", 18), *noisy_color("blue", 32), "-f", "lavfi", "-i", "sine=f=330:d=60,volume=4",
       "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
       "-c:v", "libx264", "-g", "15", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src)

    def px(path, t, xy=(20, 960)):
        out = os.path.join(W, "px.png")
        ff("-ss", str(t), "-i", path, "-frames:v", "1", out)
        return Image.open(out).convert("RGB").getpixel(xy)

    def name(rgb):
        r, g, b = rgb
        if r > 140 and g < 110 and b < 110:
            return "red"
        if r > 150 and g > 150 and b < 110:
            return "yellow"
        if b > 140 and r < 110 and g < 110:
            return "blue"
        return f"?{rgb}"

    def cand(start, first_off, end, last_end_off=None, track="A"):
        length = end - start
        last = last_end_off if last_end_off is not None else length - 0.4
        return HighlightCandidate(
            start_sec=start, end_sec=end, cta_sec=3.0, track=track, coverage=0.8,
            caption_scenes=[{"start": first_off, "end": first_off + 5.0, "text": "주문하면 바로 무쳐요"},
                            {"start": 7.0, "end": last, "text": "그 자리에서 신선하게 버무려요"}],
            flags=[], music_suspected=False)

    # ---- ① 컷 검출 + 시작점 보정
    cuts = vsc.find_cuts(src, 9.0, 11.0)
    check(len(cuts) == 1 and abs(cuts[0] - 10.0) < 0.07, f"컷 검출: 10.0초 부근 1개 ({cuts})")
    c1 = cand(9.6, 0.5, 26.0)                         # 창 시작 9.6s → 컷(10.0) 앞 0.4초가 이전 장면, 첫 발화는 10.1s
    fixed, info = hr.guard_cuts(src, c1)
    check(info["in_cut"] is not None and abs(info["in_cut"] - 10.0) < 0.07 and 10.0 < fixed.start_sec <= 10.1 + 1e-6,
          f"시작점이 컷 직후로 이동: 9.60 → {fixed.start_sec:.2f}s (컷 {info['in_cut']})")
    check(abs(fixed.caption_scenes[0]["start"] - (c1.caption_scenes[0]["start"] - (fixed.start_sec - c1.start_sec))) < 0.01
          and fixed.caption_scenes[0]["start"] >= 0.0, f"자막 상대시간도 같이 이동 (첫 자막 {c1.caption_scenes[0]['start']} → {fixed.caption_scenes[0]['start']})")
    check(fixed.fade_in_sec <= 0.1 and fixed.fade_in_sec <= (c1.start_sec + 0.5) - fixed.start_sec + 1e-6, "오디오 페이드가 첫 발화를 먹지 않도록 조정")

    out_off = os.path.join(W, "ghost.mp4")
    out_on = os.path.join(W, "clean.mp4")
    hr.render_highlight(src, c1, out_off, cta_text="네이버에서 오늘식탁 검색해 보세요!", guard=False, fit_mode="crop")
    hr.render_highlight(src, c1, out_on, cta_text="네이버에서 오늘식탁 검색해 보세요!", guard=True, fit_mode="crop")
    ghost = [name(px(out_off, t)) for t in (0.0, 0.2, 0.35)]
    clean = [name(px(out_on, t)) for t in (0.0, 0.04, 0.2, 0.35)]
    check(ghost[0] == "red" and ghost[1] == "red", f"(대조) 가드를 끄면 이전 장면 잔상이 나옴 {ghost}")
    check(all(c == "yellow" for c in clean), f"가드를 켜면 첫 프레임부터 본문 장면 {clean}")
    exact = cand(10.0, 0.1, 26.0)
    out_ex = os.path.join(W, "exact.mp4")
    hr.render_highlight(src, exact, out_ex, cta_text="네이버에서 오늘식탁 검색해 보세요!", guard=False, fit_mode="crop")
    check(name(px(out_ex, 0.0)) == "yellow", f"컷 정확히 위에서 시작해도 -ss 정밀 탐색이 이전 프레임을 섞지 않음 ({name(px(out_ex, 0.0))})")

    # ---- 목소리가 먼저 시작되고 컷이 곧바로 뒤따르는 경우(J-cut): 나레이션은 그대로, 영상만 컷 직후 프레임부터
    jc = cand(9.6, 0.3, 26.0)                           # 첫 자막 9.9s, 컷 10.0s (발화 시작 0.1초 뒤)
    jc.first_word_sec = 9.9
    fixed_j, info_j = hr.guard_cuts(src, jc)
    check(info_j["in_cut"] is not None and abs(info_j["in_cut"] - 10.0) < 0.07 and 0.0 < info_j["video_hold"] <= hr.MAX_VIDEO_HOLD
          and abs((fixed_j.start_sec + 0.0) - (9.9 - hr.PRE_ONSET_LEAD)) < 0.02,
          f"인트로 컷: 영상만 컷 직후부터(홀드 {info_j['video_hold']}s), 시작은 첫 단어 {hr.PRE_ONSET_LEAD}초 앞 ({fixed_j.start_sec:.2f}s)")
    out_j = os.path.join(W, "jcut.mp4")
    hr.render_highlight(src, jc, out_j, cta_text="네이버에서 오늘식탁 검색해 보세요!", guard=True, fit_mode="crop")
    fr = [name(px(out_j, t)) for t in (0.0, 0.1, 0.2)]
    check(all(c == "yellow" for c in fr), f"J-cut 가드: 첫 프레임부터 이전 장면 없음 {fr}")
    import re as _re
    r_ = subprocess.run([FF, "-hide_banner", "-ss", "0.15", "-t", "0.6", "-i", out_j, "-af", "volumedetect", "-vn", "-f", "null", "-"],
                        capture_output=True, text=True, encoding="utf-8", errors="ignore").stderr
    m_ = _re.search(r"mean_volume:\s*(-?[\d.]+)", r_)
    check(m_ and float(m_[1]) > -25, f"나레이션(원본 오디오)은 끊기지 않고 유지 ({m_[1] if m_ else None} dB)")
    dur_j = subprocess.run([FF, "-hide_banner", "-i", out_j], capture_output=True, text=True, encoding="utf-8", errors="ignore").stderr
    mj = _re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", dur_j)
    total_j = int(mj[1]) * 3600 + int(mj[2]) * 60 + float(mj[3])
    check(abs(total_j - (fixed_j.end_sec - fixed_j.start_sec + 3.0)) < 0.2, f"홀드를 써도 길이는 창+CTA 그대로 ({total_j:.2f}s)")
    far_j = cand(9.0, 0.2, 26.0)                        # 첫 발화 9.2s, 컷 10.0s: 발화가 0.8초나 먼저 → 건드리지 않음
    f_far, i_far = hr.guard_cuts(src, far_j)
    check(f_far.start_sec == 9.0 and i_far["in_cut"] is None and not i_far.get("video_hold"), "발화 시작 0.35초 이후의 컷(말 도중)은 건드리지 않음")

    # ---- 끝 지점: 마지막 발화 뒤에 컷이 있으면 컷 직전에서 끝냄
    c2 = cand(10.05, 0.1, 28.4, last_end_off=17.5)    # 마지막 발화 끝 27.55s, 컷 28.0s, 창 끝 28.4s
    fixed2, info2 = hr.guard_cuts(src, c2)
    check(info2["out_cut"] is not None and abs(info2["out_cut"] - 28.0) < 0.07 and 27.55 < fixed2.end_sec < 28.0,
          f"끝점이 다음 장면 컷 직전으로: 28.40 → {fixed2.end_sec:.2f}s (컷 {info2['out_cut']})")

    # ---- 발화 도중 컷이면 건드리지 않음 / 보정 시 창이 너무 짧아지면 포기 / 검출 실패는 렌더를 막지 않음
    mid = cand(9.0, 0.5, 26.0)                        # 첫 발화 9.5s, 컷 10.0s는 발화 도중
    f3, i3 = hr.guard_cuts(src, mid)
    check(f3.start_sec == 9.0 and i3["in_cut"] is None, "컷이 발화 도중이면 시작점을 건드리지 않음")
    far = cand(10.0, 6.0, 25.0)                       # 첫 발화 16.0s, 컷 후보를 15.0s로 주입 → 보정하면 창 10초 미만
    f4, i4 = hr.guard_cuts(src, far, detector=lambda v, a, b: [15.0] if a <= 15.0 <= b else [])
    check(f4.start_sec == 10.0 and "짧아" in i4["skipped"], f"보정 시 창이 14초 미만이면 보정 포기 ({i4['skipped']})")

    def broken(v, a, b):
        raise RuntimeError("디코딩 실패")

    f5, i5 = hr.guard_cuts(src, c1, detector=broken)
    check(f5 is c1 and "실패" in i5["skipped"], "컷 검출이 실패해도 원본 후보로 계속 진행")

    # ---- ② 레이아웃: 가로 → 세로 (4색 세로 줄무늬 + 흰 자막 위치)
    stripes = os.path.join(W, "stripes.mp4")
    boxes = ",".join(f"drawbox=x={i * 320}:y=0:w=320:h=720:color={c}:t=fill"
                     for i, c in enumerate(("red", "green", "blue", "yellow")))
    ff("-f", "lavfi", "-i", f"color=c=black:s=1280x720:r=15:d=40,{boxes}", "-f", "lavfi", "-i", "sine=f=330:d=40,volume=4",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", stripes)
    cs = HighlightCandidate(start_sec=5.0, end_sec=23.0, cta_sec=3.0, track="A", coverage=0.8,
                            caption_scenes=[{"start": 0.3, "end": 8.0, "text": "좌우 비교가 핵심인 화면이에요"},
                                            {"start": 9.0, "end": 17.0, "text": "손동작이 잘리지 않아야 해요"}],
                            flags=[], music_suspected=False)
    out_blur = os.path.join(W, "blur.mp4")
    out_crop = os.path.join(W, "crop.mp4")
    hr.render_highlight(stripes, cs, out_blur, cta_text="네이버에서 HKG 검색해 보세요!", guard=False)             # 기본값 = blur
    hr.render_highlight(stripes, cs, out_crop, cta_text="네이버에서 HKG 검색해 보세요!", guard=False, fit_mode="crop")

    def colors_at(path, t):
        out = os.path.join(W, "c.png")
        ff("-ss", str(t), "-i", path, "-frames:v", "1", out)
        img = Image.open(out).convert("RGB")
        return img, [img.getpixel((x, 960)) for x in (135, 405, 675, 945)]

    def is_c(rgb, c):
        r, g, b = rgb
        return {"red": r > 180 and g < 90 and b < 90, "green": g > 100 and r < 90 and b < 90,
                "blue": b > 180 and r < 90 and g < 90, "yellow": r > 180 and g > 180 and b < 90}[c]

    img_b, cols_b = colors_at(out_blur, 1.0)
    img_c, cols_c = colors_at(out_crop, 1.0)
    check(all(is_c(c, n) for c, n in zip(cols_b, ("red", "green", "blue", "yellow"))),
          "원본 비율 유지(기본): 가로 4구역이 모두 보임 (좌우 핵심 화면 보존)")
    check(not is_c(cols_c[0], "red") and not is_c(cols_c[3], "yellow"), "대조) 중앙 크롭은 좌우 끝 구역이 잘림")
    arr_b = img_b.load()

    def white_count(img, y0, y1):
        px_ = img.load()
        return sum(1 for y in range(y0, y1, 2) for x in range(0, 1080, 2)
                   if min(px_[x, y]) > 235)

    mid_white = white_count(img_b, 700, 1220)       # 원본 화면 영역 (656~1264)
    low_white = white_count(img_b, 1300, 1800)      # 아래 블러 띠
    check(low_white > 300 and mid_white < 40, f"자막은 아래 블러 띠에 들어가고 원본 화면을 가리지 않음 (화면 {mid_white}px, 띠 {low_white}px)")

    # ---- UI/서비스 계층: fit·상호명 읽는 법이 렌더 단계까지 전달
    seen = {}
    real_render = hr.render_highlight

    def spy_render(video, cand_, out, **kw):
        seen.update(kw)
        return real_render(video, cand_, out, **kw)

    import core.renderers.highlight_renderer as hr_mod
    hr_mod.render_highlight = spy_render
    spoken = []

    def voice(scene):
        spoken.append(scene.get("voiceover"))
        return None

    try:
        r = lf.render_candidate(stripes, cs, os.path.join(W, "svc.mp4"), store_name="HKG", concept="좌우 비교", voice_fn=voice,
                                fit_mode="crop", reading="에이치케이지", work_dir=W)
    finally:
        hr_mod.render_highlight = real_render
    check(seen.get("fit_mode") == "crop" and os.path.exists(r["path"]), "서비스 계층이 화면 구성 옵션을 렌더러로 전달")
    check(spoken and "HKG" in spoken[-1], f"CTA 문구는 원문 상호 그대로(발음 변환은 TTS 단계): {spoken[-1] if spoken else None}")
    from core.tts import owner_readings, apply_readings
    check(apply_readings("네이버에서 HKG 검색해 보세요", owner_readings("HKG", "에이치케이지")) == "네이버에서 에이치케이지 검색해 보세요",
          "상호명 읽는 법을 입력하면 그 읽기가 우선")

    # ---- 분석 단계에서 이미 보정된 시작점이 카드에 반영 (가짜 전사기 + 컷 위치를 아는 영상)
    class FakeTr(st_mod.Transcriber):
        cache_id = "fake-fix"

        def transcribe(self, wav_path, *, language, initial_prompt, model, progress=None):
            sents, spans, t = [], [], 2.0
            while t < 56.0:
                txt = "주문하면 바로 무쳐요 신선한 겉절이" if t >= 19.4 else "오늘 날씨가 좋네요"
                words = [{"text": w, "start": round(t + i * 0.5, 2), "end": round(t + i * 0.5 + 0.45, 2), "prob": 0.9} for i, w in enumerate(txt.split())]
                sents.append({"text": txt, "start": round(t, 2), "end": round(t + 2.9, 2), "avg_prob": 0.9, "no_speech_prob": 0.02, "words": words})
                spans.append({"start": round(t - 0.05, 2), "end": round(t + 2.95, 2)})
                t += 3.5
            return {"sentences": sents, "speech_spans": spans}

    cut2 = os.path.join(W, "cut2.mp4")        # 컷 19.2s: 첫 후보 창(첫 문장 19.5 - 마진 0.3 = 19.2 이전)에 이전 장면 포함
    ff(*noisy_color("red", 19.3), *noisy_color("yellow", 40.7), "-f", "lavfi", "-i", "sine=f=330:d=60,volume=4",
       "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-shortest", cut2)
    os.environ["STT_CACHE_DIR"] = os.path.join(W, "cache")
    req = lf.LongformRequest(video_path=cut2, concept="주문 즉시 무쳐내는 신선한 겉절이", store_name="오늘식탁", use_visual=False, use_llm=False)
    res = lf.analyze_longform(req, os.path.join(W, "an"), transcriber=FakeTr(), cache_dir=os.path.join(W, "cache"))
    guarded = [c for c in res.candidates if c.guard and c.guard.get("start_delta")]
    check(guarded and abs(guarded[0].guard["in_cut"] - 19.3) < 0.08 and guarded[0].start_sec >= 19.3,
          f"분석 단계에서 시작점 보정이 후보에 반영 ({[(c.start_sec, c.guard) for c in res.candidates]})")
finally:
    shutil.rmtree(W, ignore_errors=True)
finish(fails)
