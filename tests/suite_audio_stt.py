"""롱폼 전사 엔진(core/media/audio_stt): 가짜 전사기 주입·공용 캐시(적중/무효화/깨짐)·16kHz mono 추출·지연 임포트·의존성 금지 목록"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish, live_skip  # noqa: E402
import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave
import logging
logging.disable(logging.WARNING)

import imageio_ffmpeg

from core.media import audio_stt as st

FF = imageio_ffmpeg.get_ffmpeg_exe()
fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


def ff(*a):
    return subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", *a], capture_output=True)


class FakeTranscriber(st.Transcriber):
    """결정적 가짜 전사기: 호출 횟수·받은 프롬프트 기록, 모델 다운로드 없음"""
    cache_id = "fake"

    def __init__(self):
        self.calls = 0
        self.prompts = []
        self.wav_seen = []

    def transcribe(self, wav_path, *, language, initial_prompt, model, progress=None):
        self.calls += 1
        self.prompts.append(initial_prompt)
        self.wav_seen.append(wav_path)
        for p in (10, 40, 70, 100):
            if progress:
                progress(p, "가짜 전사")
        return {
            "sentences": [
                {"text": "안녕하세요 오늘식탁입니다", "start": 0.4, "end": 2.6, "avg_prob": 0.93, "no_speech_prob": 0.02,
                 "words": [{"text": "안녕하세요", "start": 0.4, "end": 1.2, "prob": 0.95},
                           {"text": "오늘식탁입니다", "start": 1.3, "end": 2.6, "prob": 0.91}]},
                {"text": "주문하면 바로 무쳐요", "start": 4.0, "end": 6.0, "avg_prob": 0.88, "no_speech_prob": 0.05,
                 "words": [{"text": "주문하면", "start": 4.0, "end": 4.8, "prob": 0.9},
                           {"text": "바로", "start": 4.9, "end": 5.3, "prob": 0.85},
                           {"text": "무쳐요", "start": 5.4, "end": 6.0, "prob": 0.89}]},
            ],
            "speech_spans": [{"start": 0.3, "end": 2.7}, {"start": 3.9, "end": 6.1}],
        }


W = tmp("stt_suite_")
CACHE = os.path.join(W, "cache")
try:
    # 합성 소스: 8초, 44.1kHz 스테레오 사인파 (오디오 있음) / 오디오 없는 영상
    src_dir = os.path.join(W, "한글 'src'")                      # 경로에 한글과 ' 를 포함 (셸 해석 금지 검증)
    os.makedirs(src_dir)
    vid = os.path.join(src_dir, "롱폼 영상.mp4")
    ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=15:d=8", "-f", "lavfi", "-i", "sine=f=330:d=8:sample_rate=44100",
       "-ac", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", vid)
    silent = os.path.join(src_dir, "무음.mp4")
    ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=15:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", silent)

    # 1. 오디오 추출: 16kHz mono, 길이 일치
    out_wav = os.path.join(W, "x.wav")
    dur = st.extract_audio_16k(vid, out_wav)
    with wave.open(out_wav, "rb") as w:
        fmt = (w.getframerate(), w.getnchannels(), w.getsampwidth())
    check(fmt == (16000, 1, 2) and abs(dur - 8.0) < 0.2, f"16kHz mono PCM 추출 (한글·' 경로) {fmt}, {dur:.2f}s")
    try:
        st.extract_audio_16k(silent, os.path.join(W, "n.wav"))
        ok = False
    except st.NoAudioStreamError:
        ok = True
    check(ok, "오디오 스트림 없는 영상 → NoAudioStreamError")

    # 2. 가짜 전사기 주입 + 진행률 단조 증가 + 결과 규격
    fake = FakeTranscriber()
    prog = []
    tr = st.transcribe_video(vid, store_name="오늘식탁", terms=["겉절이", "무침"], transcriber=fake, cache_dir=CACHE,
                             progress=lambda p, m: prog.append(p))
    check(fake.calls == 1 and tr.model == (os.getenv("WHISPER_MODEL") or st.DEFAULT_MODEL) and tr.language == "ko",
          "가짜 전사기 주입 동작 (모델 다운로드 없음)")
    check(prog and prog[0] == 0 and prog[-1] == 100 and all(a <= b for a, b in zip(prog, prog[1:])),
          f"진행률 콜백 0→100 단조 증가 {prog}")
    check(len(tr.sentences) == 2 and len(tr.words) == 5 and tr.sentences[1].word_range == (2, 5)
          and tr.words[tr.sentences[1].word_range[0]].text == "주문하면", "문장·단어 타임스탬프 규격 (word_range 연결)")
    check(abs(tr.speech_coverage(0, 8) - (2.4 + 2.2) / 8) < 0.01 and tr.speech_coverage(2.8, 3.8) == 0.0,
          f"발화 커버리지 계산 {tr.speech_coverage(0, 8):.2f}")
    check("오늘식탁" in fake.prompts[0] and "겉절이" in fake.prompts[0], f"initial_prompt에 상호명·메뉴명 주입: {fake.prompts[0]}")
    check(not any(os.path.exists(p) for p in fake.wav_seen), "임시 WAV 정리됨")

    # 3. 캐시 적중: 재전사 없음 / 결과 동일 / 진행률 100
    prog2 = []
    tr2 = st.transcribe_video(vid, store_name="오늘식탁", terms=["겉절이", "무침"], transcriber=fake, cache_dir=CACHE,
                              progress=lambda p, m: prog2.append(p))
    check(fake.calls == 1 and tr2.to_dict() == tr.to_dict() and prog2[-1] == 100, "캐시 적중 시 재전사 스킵")
    st.transcribe_video(vid, store_name="오늘식탁", terms=["겉절이", "무침"], transcriber=fake, cache_dir=CACHE, force=True)
    check(fake.calls == 2, "force=True는 캐시 무시")

    # 4. 무효화: 프롬프트·모델·언어·mtime·크기, 전사기 종류
    base = fake.calls
    st.transcribe_video(vid, store_name="다른가게", terms=["겉절이", "무침"], transcriber=fake, cache_dir=CACHE)
    st.transcribe_video(vid, store_name="오늘식탁", terms=["겉절이", "무침"], transcriber=fake, cache_dir=CACHE, model="medium")
    st.transcribe_video(vid, store_name="오늘식탁", terms=["겉절이", "무침"], transcriber=fake, cache_dir=CACHE, language="en")
    check(fake.calls == base + 3, "프롬프트·모델·언어가 바뀌면 자동 무효화")
    copy = os.path.join(src_dir, "복사본.mp4")
    shutil.copy(vid, copy)

    def key(p, tid="fake"):
        return st.make_cache_key(p, model="m", language="ko", initial_prompt="", transcriber_id=tid)

    k0 = key(copy)
    os.utime(copy, (time.time() + 100, time.time() + 100))
    k1 = key(copy)
    check(k0 != k1, "mtime이 바뀌면 키가 바뀜")
    check(key(copy, "faster-whisper") != k1, "전사기 종류가 다르면 키가 다름 (가짜 결과가 실제 캐시를 오염시키지 않음)")
    with open(copy, "ab") as f:
        f.write(b"x")
    check(key(copy) != k1, "파일이 바뀌면(크기) 키가 바뀜")

    # 5. 경량 키: 큰 파일은 앞/뒤 샘플만 읽음 (가운데 변경은 구분 못 하는 설계, 꼬리 변경은 구분)
    big = os.path.join(W, "big.bin")
    size = 3 * st.SAMPLE_BYTES
    with open(big, "wb") as f:
        f.write(b"\0" * size)
    t0 = os.stat(big).st_mtime_ns
    kb = key(big)
    with open(big, "r+b") as f:
        f.seek(size // 2)
        f.write(b"\1")
    os.utime(big, ns=(t0, t0))
    check(key(big) == kb, "큰 파일은 가운데를 읽지 않음 (전체 해시 없음)")
    with open(big, "r+b") as f:
        f.seek(size - 10)
        f.write(b"\1")
    os.utime(big, ns=(t0, t0))
    check(key(big) != kb, "꼬리 샘플이 바뀌면 키가 바뀜")

    # 6. 캐시 안전: 깨진 JSON → 재전사, 스키마 불일치 → 무효, 임시 파일 잔재 없음, 용량 정리
    cdir2 = os.path.join(W, "cache2")
    f2 = FakeTranscriber()
    t_a = st.transcribe_video(vid, transcriber=f2, cache_dir=cdir2)
    path = st._cache_path(t_a.source_key, cdir2)
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"schema_version": 1, "source_key": "x", "sentences": [')
    t_b = st.transcribe_video(vid, transcriber=f2, cache_dir=cdir2)
    check(f2.calls == 2 and len(t_b.sentences) == 2, "깨진 캐시는 무시하고 재전사")
    d = json.load(open(path, encoding="utf-8"))
    d["schema_version"] = 999
    json.dump(d, open(path, "w", encoding="utf-8"))
    st.transcribe_video(vid, transcriber=f2, cache_dir=cdir2)
    check(f2.calls == 3, "스키마 버전이 다르면 무효화")
    check(not [n for n in os.listdir(cdir2) if n.endswith(".tmp")], "원자적 쓰기: 임시 파일 잔재 없음")
    for i in range(3):
        p = os.path.join(cdir2, f"old{i}.json")
        with open(p, "w") as f:
            f.write("x" * 400_000)
        os.utime(p, (1000 + i, 1000 + i))
    removed = st.prune_cache(cdir2, max_mb=0.5)
    left = sorted(n for n in os.listdir(cdir2) if n.endswith(".json"))
    check(removed >= 2 and "old0.json" not in left and "old1.json" not in left, f"캐시 용량 정리 (오래된 것부터 삭제 {removed}개)")

    # 7. 프롬프트 한도
    pr = st.build_initial_prompt("오늘식탁", [f"메뉴이름{i:02d}번" for i in range(60)])
    check(len(pr) <= st.PROMPT_MAX_CHARS and pr.startswith("상호명: 오늘식탁."), f"initial_prompt 길이 한도 + 상호명 유지 ({len(pr)}자)")
    check(st.build_initial_prompt("", []) == "", "상호·용어가 없으면 빈 프롬프트")

    # 7b. 긴 문장 분할: 단어 사이 가장 큰 틈에서 나눔 (하이라이트 경계 후보 확보)
    ws = [st.Word(f"w{i}", i * 1.0, i * 1.0 + 0.6) for i in range(20)]            # 20초, 틈 0.4s
    ws[9] = st.Word("w9", 9.0, 9.6)
    ws[10] = st.Word("w10", 10.9, 11.5)                                           # 9→10 사이 틈 1.3s (가장 큼)
    big_s = st.Sentence("긴문장", 0.0, 19.6, (0, 20), 0.9, 0.0)
    parts = st.split_long_sentences(ws, [big_s])
    check(len(parts) >= 3 and all(p.end - p.start <= st.MAX_SENTENCE_SEC + 1e-6 for p in parts)
          and any(p.word_range[1] == 10 and p.end == 9.6 for p in parts), f"8초 초과 문장을 가장 큰 틈에서 분할 {[(p.word_range) for p in parts]}")
    check(sum(p.word_range[1] - p.word_range[0] for p in parts) == 20 and parts[0].word_range[0] == 0, "분할 후 단어가 빠지거나 겹치지 않음")
    short = st.split_long_sentences(ws[:5], [st.Sentence("짧음", 0.0, 4.6, (0, 5))])
    check(len(short) == 1, "8초 이하 문장은 그대로")

    # 8. 지연 임포트: faster-whisper 없이도 모듈 임포트 OK, 호출 시에만 안내 예외 (서브프로세스로 격리)
    code = (
        "import sys; sys.modules['faster_whisper'] = None\n"
        "import core.media.audio_stt as m\n"
        "try:\n"
        "    m.FasterWhisperTranscriber().ensure_ready()\n"
        "    print('NOERR')\n"
        "except m.STTDependencyError as e:\n"
        "    print('DEPERR', 'pip install faster-whisper' in str(e))\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    check(r.returncode == 0 and "DEPERR True" in r.stdout, f"미설치 시뮬레이션: 임포트 OK, 호출 시 안내 예외 ({r.stdout.strip() or r.stderr.strip()[-120:]})")
    code2 = (
        "import sys, tempfile; sys.modules['faster_whisper'] = None\n"
        "import core.media.audio_stt as m\n"
        "calls = []\n"
        "orig = m.extract_audio_16k\n"
        "m.extract_audio_16k = lambda *a, **k: calls.append(1) or orig(*a, **k)\n"
        "try:\n"
        "    m.transcribe_video(sys.argv[1], cache_dir=tempfile.mkdtemp())\n"
        "except m.STTDependencyError:\n"
        "    print('EARLY', len(calls) == 0)\n")
    r2 = subprocess.run([sys.executable, "-c", code2, vid], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    check("EARLY True" in r2.stdout, f"의존성 누락은 오디오 추출 전에 실패 ({r2.stdout.strip() or r2.stderr.strip()[-120:]})")

    from streamlit.testing.v1 import AppTest
    sys.modules["faster_whisper"] = None               # 앱 구동 중에도 미설치 상태 유지
    try:
        at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120).run()
        check(not at.exception, "faster-whisper 미설치 상태에서 앱 정상 구동")
    finally:
        sys.modules.pop("faster_whisper", None)

    # 9. 의존성 정책: requirements에 전사 스택은 있고 AGPL·무거운 대안은 없음
    req = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read().lower()
    pkgs = {re.split(r"[<>=!~\[\s#]", ln.strip(), maxsplit=1)[0] for ln in req.splitlines()
            if ln.strip() and not ln.strip().startswith("#")}
    banned = {"ultralytics", "whisper-timestamped", "whisperx", "yolov5", "yolov8", "silero-vad", "pyannote.audio", "pyannote-audio"}
    check("faster-whisper" in pkgs and not (pkgs & banned), f"requirements: faster-whisper 포함, AGPL·무거운 대안 없음 {sorted(pkgs & banned)}")

    # 10. 실모델 1건 (--live 전용, 설치돼 있을 때만)
    if not live_skip("실제 faster-whisper 전사 (모델 다운로드 필요)"):
        try:
            import faster_whisper  # noqa: F401
            real = st.transcribe_video(vid, cache_dir=os.path.join(W, "live"), model=os.getenv("WHISPER_MODEL", "tiny"))
            check(real.duration_sec > 7 and real.schema_version == st.SCHEMA_VERSION, f"실모델 전사 완료 ({len(real.sentences)}문장)")
        except ImportError:
            print("SKIP faster-whisper 미설치")
finally:
    shutil.rmtree(W, ignore_errors=True)
finish(fails)
