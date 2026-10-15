"""Streamlit '롱폼 하이라이트' 화면: 입력 검증·백그라운드 분석 진행률(단조 증가)·후보 카드·렌더·오류 안내·화면 이동 후 유지
(가짜 전사기·가짜 음성 주입, 합성 영상 — 모델 다운로드/외부 호출 없음)"""
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
os.environ["YOUTUBE_API_KEY"] = ""

import imageio_ffmpeg
from streamlit.testing.v1 import AppTest

from core import longform as lf
from core.media import audio_stt as st_mod

FF = imageio_ffmpeg.get_ffmpeg_exe()
fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


def ff(*a):
    return subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", *a], capture_output=True)


def duration(p):
    r = subprocess.run([FF, "-hide_banner", "-i", p], capture_output=True, text=True, encoding="utf-8", errors="ignore").stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


class FakeTranscriber(st_mod.Transcriber):
    """3.5초 간격 문장 (구간 20~45초는 컨셉 문장, 한 문장에 가격 표현 포함). low_speech=True면 발화 구간이 드물어 트랙 B 후보"""
    cache_id = "fake-ui"

    def __init__(self, low_speech=False):
        self.low = low_speech
        self.calls = 0

    def transcribe(self, wav_path, *, language, initial_prompt, model, progress=None):
        self.calls += 1
        sents, spans, t, n = [], [], 2.0, 0
        while t < 56.0:
            if 20 <= t < 45:
                txt = "겉절이 1인분에 9,900원 할인이에요" if n == 9 else "주문하면 바로 무쳐요 신선한 겉절이"
            else:
                txt = "오늘 날씨가 좋네요"
            words = [{"text": w, "start": round(t + i * 0.5, 2), "end": round(t + i * 0.5 + 0.45, 2), "prob": 0.9}
                     for i, w in enumerate(txt.split())]
            sents.append({"text": txt, "start": round(t, 2), "end": round(t + 2.9, 2), "avg_prob": 0.9, "no_speech_prob": 0.02, "words": words})
            if not self.low or n % 4 == 0:
                spans.append({"start": round(t - 0.05, 2), "end": round(t + 0.6 if self.low else t + 2.95, 2)})
            t += 3.5
            n += 1
            if progress:
                progress(int(t / 56 * 100), "가짜 전사")
        return {"sentences": sents, "speech_spans": spans}


W = tmp("app_longform_")
try:
    src = os.path.join(W, "롱폼 촬영.mp4")
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=15:d=60", "-f", "lavfi", "-i", "sine=f=330:d=60,volume=4",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src)
    silent = os.path.join(W, "무음.mp4")
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=15:d=20", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", silent)
    tone = os.path.join(W, "tone.mp3")
    ff("-f", "lavfi", "-i", "sine=f=900:d=1.5,volume=4", "-c:a", "libmp3lame", tone)

    calls = {"voice": []}

    def voice_factory(work_dir, actor):
        def v(scene):
            calls["voice"].append(scene.get("scene_index"))
            return tone
        return v

    fake = FakeTranscriber()
    lf._TRANSCRIBER_FACTORY = lambda: fake
    lf._VOICE_FACTORY = voice_factory
    os.environ["STT_CACHE_DIR"] = os.path.join(W, "stt_cache")

    def new_app():
        at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=180).run()
        at.session_state.page_switch = "longform"
        return at.run()

    def finish_job(at, timeout=240):
        jid = at.session_state.lf_analysis_id if "lf_analysis_id" in at.session_state else None
        job = lf.get_job(jid) if jid else None
        if job:
            job.join(timeout)
        return job

    # ---- 1. 화면 진입 · 입력 검증
    at = new_app()
    check(not at.exception and any("롱폼 하이라이트" in m.value for m in at.markdown), "'롱폼 하이라이트' 화면 진입 (예외 없음)")
    check(at.text_input(key="lf_concept") is not None and at.button(key="lf_start") is not None, "컨셉·시작 버튼 위젯 존재")
    at.button(key="lf_start").click().run()
    check(any("경로" in e.value for e in at.error), f"경로 없이 시작하면 안내 {[e.value[:30] for e in at.error]}")
    at.text_input(key="lf_path").input(os.path.join(W, "없는파일.mp4")).run()
    at.button(key="lf_start").click().run()
    check(any("찾을 수 없" in e.value for e in at.error), "없는 파일 경로 안내")
    at.text_input(key="lf_path").input(src).run()
    at.button(key="lf_start").click().run()
    check(any("1컨셉" in e.value for e in at.error), "컨셉 없이 시작하면 안내")

    # ---- 2. 분석: 백그라운드 + 진행률 단조 증가 + 후보 카드
    at.text_input(key="lf_concept").input("주문 즉시 무쳐내는 신선한 겉절이").run()
    at.text_input(key="lf_store").input("오늘식탁").run()
    at.text_input(key="lf_terms").input("겉절이, 무침").run()
    at.button(key="lf_start").click().run()
    job = finish_job(at)
    check(job is not None and job.done and job.error is None, f"백그라운드 분석 완료 (오류 {job.error if job else '없음?'})")
    hist = job.history
    check(hist and hist[0] <= 5 and hist[-1] == 100 and all(a <= b for a, b in zip(hist, hist[1:])) and len(set(hist)) >= 6,
          f"진행률 0→100 단조 증가 (단계 {len(set(hist))}개: {sorted(set(hist))[:8]}…)")
    at.run()
    at.run()
    res = at.session_state.lf_result if "lf_result" in at.session_state else None
    check(res is not None and 1 <= len(res.candidates) <= 3 and fake.calls == 1, f"후보 {len(res.candidates) if res else 0}개 (전사 1회)")
    check(all(os.path.exists(p) for p in res.thumbs.values()) and len(res.thumbs) == len(res.candidates), "후보 대표 썸네일(창 중간 프레임) 생성")
    md = " ".join(m.value for m in at.markdown)
    prog_texts = " ".join(str(getattr(x.proto, "text", "")) for x in at.get("progress"))
    check("후보 1" in md and "트랙" in md and "발화" in prog_texts, f"카드: 후보 번호·트랙 뱃지·발화 커버리지 표시 ({prog_texts[:40]})")
    check(any("확인 필요" in w.value and "가격" in w.value for w in at.warning), f"카드: 가격 클레임 주의 뱃지 {[w.value[:40] for w in at.warning]}")
    cand = res.candidates[0]
    check(15.0 <= cand.window_sec <= 22.0 and cand.total_sec <= 25.0 and re.search(r"\d\d:\d\d\.\d", md), f"타임코드·총 길이 표시 ({cand.start_sec:.1f}~{cand.end_sec:.1f}s, 총 {cand.total_sec}s)")
    check(at.radio(key="lf_pick") is not None, "후보 선택 라디오")

    # ---- 3. 렌더 → 완성본
    at.button(key="lf_render_btn").click().run()
    r = at.session_state.lf_render
    check(not r.get("error") and os.path.exists(r["path"]) and abs(r["duration"] - (cand.window_sec + 3.0)) < 0.2,
          f"쇼츠 렌더 완료: {r.get('duration')}초 = 창 {cand.window_sec}s + CTA 3s ({r.get('error', '')})")
    check(99 in calls["voice"], "CTA 음성 생성이 호출됨")
    check(any("완성본" in m.value for m in at.markdown) and not at.exception, "완성본 섹션 표시")

    # ---- 4. 화면을 옮겼다 돌아와도 결과 유지
    at.session_state.page_switch = "make"
    at.run()
    at.session_state.page_switch = "longform"
    at.run()
    check("lf_result" in at.session_state and at.session_state.lf_concept == "주문 즉시 무쳐내는 신선한 겉절이"
          and any("후보 1" in m.value for m in at.markdown), "다른 화면에 다녀와도 입력·후보 결과 유지")

    # ---- 5. 같은 영상 재분석: 전사 캐시 적중 (재전사 없음)
    at.button(key="lf_start").click().run()
    finish_job(at)
    at.run()
    at.run()
    check(fake.calls == 1, f"같은 영상 다시 분석해도 재전사 없음 (전사 호출 {fake.calls}회)")

    # ---- 6. 음성이 없는 영상 → 친절한 오류
    at2 = new_app()
    at2.text_input(key="lf_path").input(silent).run()
    at2.text_input(key="lf_concept").input("아무 컨셉").run()
    at2.button(key="lf_start").click().run()
    finish_job(at2)
    at2.run()
    at2.run()
    check(any("음성이 없" in e.value for e in at2.error), f"음성 없는 영상 안내 {[e.value[:40] for e in at2.error]}")

    # ---- 7. 트랙 B 후보 (발화 드묾): Mock 대본 + 가짜 TTS 로 렌더
    fake_b = FakeTranscriber(low_speech=True)
    lf._TRANSCRIBER_FACTORY = lambda: fake_b
    at3 = new_app()
    at3.text_input(key="lf_path").input(src).run()
    at3.text_input(key="lf_concept").input("주문 즉시 무쳐내는 신선한 겉절이").run()
    at3.text_input(key="lf_store").input("오늘식탁").run()
    at3.checkbox(key="lf_mock").check().run()
    at3.button(key="lf_start").click().run()
    finish_job(at3)
    at3.run()
    at3.run()
    res3 = at3.session_state.lf_result if "lf_result" in at3.session_state else None
    check(res3 is not None and res3.candidates and res3.candidates[0].track == "B", f"발화가 드문 영상 → 트랙 B 후보 ({[c.track for c in res3.candidates] if res3 else None})")
    check(any("새 컨셉 TTS" in i.value for i in at3.info), "트랙 B 선택 시 안내 문구")
    calls["voice"].clear()
    at3.button(key="lf_render_btn").click().run()
    r3 = at3.session_state.lf_render
    check(not r3.get("error") and os.path.exists(r3["path"]) and r3["track"] == "B" and len(calls["voice"]) >= 2,
          f"트랙 B 렌더 완료 (TTS {len(calls['voice'])}회, {r3.get('error', '')})")
finally:
    lf._TRANSCRIBER_FACTORY = None
    lf._VOICE_FACTORY = None
    shutil.rmtree(W, ignore_errors=True)
finish(fails)
