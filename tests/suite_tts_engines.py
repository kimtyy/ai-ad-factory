import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import json
import os
import subprocess

import imageio_ffmpeg

import core.asset_generator as ag
import core.tts as tts

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


KEYS = ("AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "AZURE_SPEECH_ENDPOINT", "OPENAI_API_KEY", "TTS_ENGINE", "TTS_BACKUP_ENGINE", "OPENAI_TTS_MODEL",
        "OPENAI_TTS_PACE", "OPENAI_TTS_VOICE_FEMALE", "OPENAI_TTS_VOICE_MALE", "TTS_BACKUP_ALL")
_keep = {k: os.environ.get(k) for k in KEYS}


def env(**kw):
    for k in KEYS:
        os.environ.pop(k, None)
    for k, v in kw.items():
        os.environ[k] = v


FF = imageio_ffmpeg.get_ffmpeg_exe()
W = tmp("tts_engines_")


def mp3(path, sec=2):
    subprocess.run([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=300:duration={sec}", "-c:a", "libmp3lame", path], check=True)
    return open(path, "rb").read()


GOOD = mp3(os.path.join(W, "good.mp3"))


class Resp:
    def __init__(self, status=200, content=GOOD, text=""):
        self.status_code, self.content, self.text = status, content, text


try:
    # ---------------------------------------------------------------- 1. 엔진 선택: 키가 있으면 Azure 공식 API, 없으면 기존 Edge, 백업은 OpenAI
    env()
    check(tts.default_engine() == "edge" and not tts.azure_configured(), "Azure 키가 없으면 기본은 기존 Edge-TTS (키 없이도 앱이 동작)")
    env(AZURE_SPEECH_KEY="k", AZURE_SPEECH_REGION="koreacentral")
    check(tts.default_engine() == "azure" and tts.azure_configured(), "AZURE_SPEECH_KEY·REGION이 있으면 기본 엔진은 azure (공식 API)")
    env(AZURE_SPEECH_KEY="k")
    check(tts.default_engine() == "edge", "키만 있고 지역이 없으면 azure로 보지 않음")
    env(AZURE_SPEECH_KEY="k", AZURE_SPEECH_REGION="koreacentral", TTS_ENGINE="openai")
    check(tts.default_engine() == "openai", "TTS_ENGINE으로 엔진을 강제할 수 있음")
    env(AZURE_SPEECH_KEY="k", AZURE_SPEECH_REGION="koreacentral", TTS_ENGINE="없는엔진")
    check(tts.default_engine() == "azure", "알 수 없는 TTS_ENGINE 값은 무시")
    env(OPENAI_API_KEY="sk")
    check(tts.backup_engine("azure") == "openai" and tts.backup_engine("edge") == "openai", "백업 엔진: OpenAI 키가 있으면 azure·edge 실패 → openai")
    check(tts.backup_engine("openai") is None and tts.backup_engine("gtts") is None, "이미 백업(openai)이거나 마지막 수단(gtts)이면 더 넘길 곳 없음")
    env()
    check(tts.backup_engine("azure") == "gtts" and tts.backup_engine("edge") == "gtts", "OpenAI 키가 없으면 마지막 수단 gTTS")
    env(OPENAI_API_KEY="sk", TTS_BACKUP_ENGINE="gtts")
    check(tts.backup_engine("azure") == "gtts", "TTS_BACKUP_ENGINE으로 백업 엔진을 바꿀 수 있음")

    # ---------------------------------------------------------------- 2. Azure 공식 REST: SSML·헤더·주소·오류 처리
    ssml = tts._azure_ssml("밥 & 술 <최고> '오늘'", "ko-KR-SunHiNeural", "+18%", "+0Hz")
    check("&amp;" in ssml and "&lt;최고&gt;" in ssml and "name='ko-KR-SunHiNeural'" in ssml and "rate='+18%'" in ssml and "pitch='+0Hz'" in ssml and "xml:lang='ko-KR'" in ssml,
          f"SSML: 특수문자 이스케이프·음성·속도·피치 {ssml[:90]}")
    env(AZURE_SPEECH_KEY="az-key", AZURE_SPEECH_REGION="koreacentral")
    seen = []

    def post_ok(url, data=None, json=None, headers=None, timeout=None):
        seen.append({"url": url, "data": data, "json": json, "headers": headers, "timeout": timeout})
        return Resp()

    out = os.path.join(W, "az1.mp3")
    tts._azure_synthesize("안녕하세요", "ko-KR-InJoonNeural", "+5%", "+0Hz", out, post=post_ok)
    c = seen[0]
    check(c["url"] == "https://koreacentral.tts.speech.microsoft.com/cognitiveservices/v1" and c["headers"]["Ocp-Apim-Subscription-Key"] == "az-key"
          and c["headers"]["Content-Type"] == "application/ssml+xml" and c["headers"]["X-Microsoft-OutputFormat"].endswith("mono-mp3") and b"InJoonNeural" in c["data"] and os.path.getsize(out) > 1000,
          "Azure 호출: 지역 주소·구독 키 헤더·SSML 본문·mp3 저장")
    os.environ["AZURE_SPEECH_ENDPOINT"] = "https://custom.example/tts"
    tts._azure_synthesize("a", "ko-KR-SunHiNeural", "+5%", "+0Hz", out, post=post_ok)
    check(seen[-1]["url"] == "https://custom.example/tts", "AZURE_SPEECH_ENDPOINT로 주소를 바꿀 수 있음")
    os.environ.pop("AZURE_SPEECH_ENDPOINT")

    import requests as _rq
    _post = _rq.post
    try:
        # 일시 오류(429)는 재시도해서 성공
        n = {"c": 0}

        def post_429_then_ok(url, **kw):
            n["c"] += 1
            return Resp(429, b"", "Too Many Requests") if n["c"] < 3 else Resp()

        _rq.post = post_429_then_ok
        tts_sleep = __import__("time").sleep
        __import__("time").sleep = lambda s: None
        p = tts.generate_speech("재시도 테스트", os.path.join(W, "az2.mp3"), force=True, engine="azure")
        check(n["c"] == 3 and os.path.getsize(p) > 1000, f"Azure 429는 재시도 후 성공 ({n['c']}회째)")

        # 키 오류(401)는 재시도하지 않고 바로 실패
        n["c"] = 0

        def post_401(url, **kw):
            n["c"] += 1
            return Resp(401, b"", "Access denied")

        _rq.post = post_401
        try:
            tts.generate_speech("키 오류", os.path.join(W, "az3.mp3"), force=True, engine="azure")
            raised, msg = False, ""
        except RuntimeError as e:
            raised, msg = True, str(e)
        check(raised and n["c"] == 1 and "401" in msg and "일관성" in msg, f"Azure 401(키·권한 오류)은 재시도 없이 즉시 실패 ({n['c']}회): {msg[:70]}")

        # 5xx 반복은 정해진 횟수까지만 재시도하고 실패 (한 씬만 다른 엔진으로 대체하지 않음)
        n["c"] = 0

        def post_503(url, **kw):
            n["c"] += 1
            return Resp(503, b"", "unavailable")

        _rq.post = post_503
        try:
            tts.generate_speech("서버 오류", os.path.join(W, "az4.mp3"), force=True, engine="azure")
            raised = False
        except RuntimeError as e:
            raised = "일관성" in str(e)
        check(raised and n["c"] == tts.AZURE_ATTEMPTS, f"Azure 5xx는 {tts.AZURE_ATTEMPTS}회까지만 재시도 후 오류 ({n['c']}회)")

        # 네트워크 예외도 재시도
        n["c"] = 0

        def post_net(url, **kw):
            n["c"] += 1
            if n["c"] < 2:
                raise ConnectionError("끊김")
            return Resp()

        _rq.post = post_net
        p = tts.generate_speech("네트워크", os.path.join(W, "az5.mp3"), force=True, engine="azure")
        check(n["c"] == 2 and os.path.getsize(p) > 1000, "네트워크 예외는 재시도 후 성공")

        env()
        try:
            tts.generate_speech("키 없음", os.path.join(W, "az6.mp3"), force=True, engine="azure")
            raised = False
        except RuntimeError as e:
            raised = "AZURE_SPEECH_KEY" in str(e)
        check(raised, "engine='azure'인데 키가 없으면 무엇이 빠졌는지 알려 줌")

        # ---------------------------------------------------------------- 3. OpenAI 백업: 모델별 본문·성우·속도 보정
        env(OPENAI_API_KEY="sk-test")
        sent = []

        def post_oa(url, json=None, headers=None, timeout=None, **kw):
            sent.append({"url": url, "json": json, "headers": headers})
            return Resp()

        o = os.path.join(W, "oa1.mp3")
        tts._openai_synthesize("오늘 밤 오세요!", "ko-KR-SunHiNeural", "+18%", o, post=post_oa)
        b = sent[0]["json"]
        check(sent[0]["url"] == "https://api.openai.com/v1/audio/speech" and sent[0]["headers"]["Authorization"] == "Bearer sk-test" and b["model"] == "gpt-4o-mini-tts"
              and b["voice"] == "nova" and b["response_format"] == "mp3" and b["input"] == "오늘 밤 오세요!", "OpenAI 호출: 주소·Bearer 키·모델·여성 성우(nova)·mp3")
        check(abs(b["speed"] - round(1.18 * 1.45, 2)) < 0.01 and "한국어" in b["instructions"] and "밝고" in b["instructions"], f"gpt-4o-mini-tts: 속도 보정(1.18×1.45={b['speed']})과 말투 instructions")
        tts._openai_synthesize("차분하게", "ko-KR-InJoonNeural", "+5%", o, post=post_oa)
        b2 = sent[1]["json"]
        check(b2["voice"] == "onyx" and "차분하고" in b2["instructions"], "남성 성우는 onyx, 말투는 차분하게")
        os.environ["OPENAI_TTS_MODEL"] = "tts-1"
        tts._openai_synthesize("옛 모델", "ko-KR-SunHiNeural", "+18%", o, post=post_oa)
        b3 = sent[2]["json"]
        check(b3["model"] == "tts-1" and "instructions" not in b3 and abs(b3["speed"] - round(1.18 * 1.1, 2)) < 0.01, "tts-1 계열: instructions 없이 speed만 (보정 1.1)")
        os.environ["OPENAI_TTS_PACE"] = "2.0"
        os.environ["OPENAI_TTS_VOICE_FEMALE"] = "shimmer"
        os.environ["OPENAI_TTS_MODEL"] = "gpt-4o-mini-tts"
        tts._openai_synthesize("조정", "ko-KR-SunHiNeural", "+0%", o, post=post_oa)
        check(sent[3]["json"]["speed"] == 2.0 and sent[3]["json"]["voice"] == "shimmer", "OPENAI_TTS_PACE·OPENAI_TTS_VOICE_FEMALE 환경변수로 조정")
        check(tts._speed_from_rate("+18%") == 1.18 and tts._speed_from_rate("-5%") == 0.95 and tts._speed_from_rate("") == 1.0 and tts._speed_from_rate("이상한값") == 1.0, "속도 문자열 → 배속 변환")

        env(OPENAI_API_KEY="sk-test")
        _rq.post = lambda url, **kw: Resp(401, b"", "bad key")
        try:
            tts.generate_speech("키 오류", os.path.join(W, "oa2.mp3"), force=True, engine="openai")
            raised = False
        except RuntimeError as e:
            raised = "OpenAI TTS" in str(e) and "401" in str(e)
        check(raised, "OpenAI 401은 즉시 실패")
        env()
        try:
            tts.generate_speech("키 없음", os.path.join(W, "oa3.mp3"), force=True, engine="openai")
            raised = False
        except RuntimeError as e:
            raised = "OPENAI_API_KEY" in str(e)
        check(raised, "engine='openai'인데 키가 없으면 안내")

        # ---------------------------------------------------------------- 4. generate_speech가 엔진을 고름 (engine 생략 = 기본 엔진)
        calls = []

        def post_route(url, **kw):
            calls.append(url)
            return Resp()

        _rq.post = post_route
        env(AZURE_SPEECH_KEY="k", AZURE_SPEECH_REGION="koreacentral", OPENAI_API_KEY="sk")
        tts.generate_speech("기본 엔진", os.path.join(W, "r1.mp3"), force=True)
        tts.generate_speech("백업 엔진", os.path.join(W, "r2.mp3"), force=True, engine="openai")
        check(len(calls) == 2 and "microsoft.com" in calls[0] and "openai.com" in calls[1], f"엔진 생략 시 Azure, 지정하면 OpenAI: {[c.split('/')[2] for c in calls]}")
        check(not tts.generate_speech("캐시", os.path.join(W, "r1.mp3")) == "" and len(calls) == 2, "이미 만든 파일은 캐시로 재사용 (API 재호출 없음)")
    finally:
        _rq.post = _post
        __import__("time").sleep = tts_sleep

    # ---------------------------------------------------------------- 5. 렌더: Azure 실패 → 전 씬을 같은 백업(OpenAI)으로 일괄 다시 만듦
    def run_gen(primary_fail_engines):
        calls2 = []
        gs = tts.generate_speech

        def fake_speech(text, output_path, voice_actor="female_20s", rate=None, pitch=None, force=False, keep_commas=False, engine=None):
            engine = engine or tts.default_engine()
            calls2.append(engine)
            if engine in primary_fail_engines:
                raise RuntimeError(f"{engine} 연속 실패")
            mp3(output_path, 3)
            return output_path
        tts.generate_speech = fake_speech
        ag.TTS_WORKERS = 1
        try:
            gen = ag.AssetGenerator(cache_dir=tmp("tts_gen_"), mode="owner", audio_mode="voice_and_sub", mock=False)
            scenes = [{"scene_index": i, "voiceover": f"{i}번째 씬이에요!", "duration_sec": 2.0} for i in range(1, 5)]
            try:
                out2 = gen.generate_voices(scenes, force=True)
                err = ""
            except RuntimeError as e:
                out2, err = [], str(e)
        finally:
            tts.generate_speech = gs
        return gen, out2, calls2, err

    env(AZURE_SPEECH_KEY="k", AZURE_SPEECH_REGION="koreacentral", OPENAI_API_KEY="sk", TTS_BACKUP_ALL="1")
    gen, out2, calls2, err = run_gen({"azure"})
    meta = [json.load(open(p + ".voice.json", encoding="utf-8")) for p in out2]
    check(len(out2) == 4 and gen.tts_engine == "openai" and calls2.count("azure") == 1 and calls2.count("openai") == 4 and all(m["engine"] == "openai" for m in meta),
          f"Azure 실패 → 전 씬 OpenAI로 일괄 전환 (Azure는 첫 실패에서 중단, 백업은 4씬 모두 같은 엔진): {calls2}")
    gen, out2, calls2, err = run_gen(set())
    check(len(out2) == 4 and calls2 == ["azure"] * 4 and getattr(gen, "tts_engine", None) is None, f"Azure 정상이면 4씬 모두 Azure, 백업으로 넘어가지 않음: {calls2}")
    env(AZURE_SPEECH_KEY="k", AZURE_SPEECH_REGION="koreacentral", TTS_BACKUP_ALL="1")
    gen, out2, calls2, err = run_gen({"azure"})
    check(len(out2) == 4 and gen.tts_engine == "gtts" and calls2.count("gtts") == 4, f"OpenAI 키가 없으면 마지막 수단 gTTS로: {calls2}")
    env(AZURE_SPEECH_KEY="k", AZURE_SPEECH_REGION="koreacentral", OPENAI_API_KEY="sk", TTS_BACKUP_ALL="1")
    gen, out2, calls2, err = run_gen({"azure", "openai"})
    check(not out2 and "openai" in err and calls2.count("openai") == 1, f"백업(OpenAI)도 실패하면 더 넘기지 않고 오류로 알림: {err[:60]}")
    env(AZURE_SPEECH_KEY="k", AZURE_SPEECH_REGION="koreacentral", OPENAI_API_KEY="sk")
    gen, out2, calls2, err = run_gen({"azure"})
    check(not out2 and calls2 == ["azure"] and "azure" in err, "TTS_BACKUP_ALL이 꺼져 있으면 백업으로 넘기지 않고 오류")
    env(OPENAI_API_KEY="sk", TTS_BACKUP_ALL="1")
    gen, out2, calls2, err = run_gen({"edge"})
    check(len(out2) == 4 and gen.tts_engine == "openai" and calls2.count("edge") == 1, f"Azure 키가 없는 환경(기본 Edge)도 실패하면 OpenAI 백업: {calls2}")
finally:
    for k, v in _keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

finish(fails)
