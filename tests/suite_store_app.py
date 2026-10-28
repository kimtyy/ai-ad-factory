import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import subprocess

import imageio_ffmpeg
from streamlit.testing.v1 import AppTest

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


# ---------------------------------------------------------------- 1. 기존 통합 앱(app.py)은 그대로
_os.environ.pop("STORE_APP", None)
base = AppTest.from_file(_os.path.join(ROOT, "app.py"), default_timeout=300).run()
check(not base.exception and len(base.sidebar.button) == 9 and not any("샵스토리" in t.value for t in base.title),
      "기존 app.py는 그대로: 좌측 바 9개 메뉴, 샵스토리 제목 없음")

# ---------------------------------------------------------------- 2. 단독 매장 앱(app_store.py)
at = AppTest.from_file(_os.path.join(ROOT, "app_store.py"), default_timeout=300).run()
check(not at.exception, f"app_store.py 오류 없이 실행 {[e.value[:80] for e in at.exception]}")
check(any("샵스토리" in t.value and "AI 사장님 매장 홍보 영상 제작소" in t.value for t in at.title) and len([c for c in at.columns]) >= 3, "헤더: 🏪 샵스토리 - AI 사장님 매장 홍보 영상 제작소")
check(len(at.sidebar.button) == 0 and not at.sidebar.markdown, f"왼쪽 사이드바에는 아무것도 렌더하지 않음 (버튼 {len(at.sidebar.button)}개)")
hdr = {b.key: b.label for b in at.button if b.key in ("nav_home", "nav_settings")}
check(hdr == {"nav_home": "🏠 처음으로", "nav_settings": "⚙️ 설정"} and not [b for b in at.button if b.key and b.key.startswith("navb_")],
      f"[처음으로]·[설정]은 헤더 우측 (하단 바 없음): {hdr}")
check(not [t for t in at.toggle if t.key == "demo_mode"] and not [e for e in at.main if getattr(e, "key", None) in ("mode_switch", "page_switch")],
      "모드 선택·시연 모드 토글 없음")
heads = [m.value for m in at.markdown if m.value.startswith("###")]
check(all(any(f"[{n}단계]" in h for h in heads) for n in (1, 2, 3, 4)), f"1~4단계 일직선: {[h[:22] for h in heads[:5]]}")
check(any(b.key == "place_fetch" for b in at.button) and any(t.key == "official_menu_text" for t in at.text_area), "1단계: [가게 정보 가져오기] + 공식 메뉴판 칸")
labels = [b.label for b in at.button]
check("🚀 선택된 컨셉으로 대본 만들기" in labels and not any(l.startswith(("🎵", "⚡")) for l in labels), "3단계: 1컷 1카피 단일 버튼")
check(any(r.key == "script_style_radio" for r in at.radio), "2단계: 화자 시점 선택")
# 처음으로: 입력을 새 작업으로 초기화하고 모드 홈으로 가지 않음
at.text_input(key="owner_name").set_value("테스트 매장").run()
at.button(key="nav_home").click().run()
check(not at.exception and at.text_input(key="owner_name").value == "" and any("샵스토리" in t.value for t in at.title), "[처음으로]: 입력 초기화, 같은 매장 화면 유지")
check(len([m for m in at.markdown if m.value.strip() == "---"]) == 1, "구분선(---) 연속 출력 없음 (4단계 위 구분선은 1개)")
check(any("⏱️" in (i.value if hasattr(i, "value") else "") for i in list(at.caption) + list(at.info)) and not any(getattr(sb, "label", "") == "⏱️ 영상 길이" for sb in at.selectbox),
      "3단계: 길이 드롭다운 대신 자동 길이 안내 (영상 길이 모순 제거)")

# ---------------------------------------------------------------- 길이 안내문: 올린 에셋 기준 (영상 길이 모순 제거)
VW = tmp("store_notice_")
vid = _os.path.join(VW, "v.mp4")
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=15", "-pix_fmt", "yuv420p", vid], check=True)
at2 = AppTest.from_file(_os.path.join(ROOT, "app_store.py"), default_timeout=300).run()
at2.session_state["uploaded_paths"] = [vid]
at2.run()
infos = [i.value for i in at2.info]
check(any("영상 맞춤: 업로드된 영상(15.0초) 길이에 맞춰 대본이 자동 배분됩니다." in v for v in infos), f"단일 영상: {[v for v in infos if '⏱️' in v]}")
import shutil
photos = []
for _i in range(8):
    _p = _os.path.join(VW, f"p{_i}.jpg")
    shutil.copy(fixture("photo_pool.jpg"), _p)
    photos.append(_p)
at2.session_state["uploaded_paths"] = [photos[0], vid, photos[1]]
at2.run()
infos = [i.value for i in at2.info]
check(any("자동 계산: 선택된 3개 에셋에 맞춰 약 14초 쇼츠로 제작됩니다." in v for v in infos), f"사진+영상 혼합(2+15→10초 상한): {[v for v in infos if '⏱️' in v]}")
at2.session_state["uploaded_paths"] = photos
at2.run()
check(any("선택된 8개 에셋에 맞춰 약 16초" in i.value for i in at2.info), "사진 8장: 약 16초")

# ---------------------------------------------------------------- 3. 음성: 순차 호출 + 실패 시 전 씬 백업 성우(gTTS) 일괄 전환
import core.asset_generator as ag
import core.tts as tts
check(_os.environ.get("TTS_WORKERS") == "1" and _os.environ.get("TTS_BACKUP_ALL") == "1", "단독 앱 환경: TTS_WORKERS=1, TTS_BACKUP_ALL=1")

W = tmp("store_tts_")
FF = imageio_ffmpeg.get_ffmpeg_exe()
calls = []
_gs = tts.generate_speech


def fake_speech(text, output_path, voice_actor="female_20s", rate=None, pitch=None, force=False, keep_commas=False, engine="edge"):
    calls.append((engine, text))
    if engine == "edge":
        raise RuntimeError("Edge-TTS 5회 연속 실패 (403)")
    subprocess.run([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-c:a", "libmp3lame", output_path], check=True)
    return output_path


tts.generate_speech = fake_speech
ag.TTS_WORKERS = 1
try:
    gen = ag.AssetGenerator(cache_dir=W, mode="owner", audio_mode="voice_and_sub", mock=False)
    scenes = [{"scene_index": i, "voiceover": f"{i}번째 씬 문장이에요!", "duration_sec": 2.0} for i in range(1, 5)]
    out = gen.generate_voices(scenes, force=True)
finally:
    tts.generate_speech = _gs
engines = [c[0] for c in calls]
check(len(out) == 4 and all(_os.path.exists(p) for p in out) and gen.tts_engine == "gtts", f"Edge 실패 → 전 씬 백업 성우로 완성 ({len(out)}씬, 엔진={gen.tts_engine})")
check(engines.count("gtts") == 4 and engines.count("edge") == 1, f"Edge는 순차(한 씬 실패 시 즉시 전환), 백업은 4씬 모두 같은 엔진: {engines}")
import json
meta = [json.load(open(p + ".voice.json", encoding="utf-8")) for p in out]
check(all(m.get("engine") == "gtts" for m in meta), "씬 음성 캐시에 엔진이 기록돼 다음 렌더에서 목소리가 섞이지 않음")

finish(fails)
