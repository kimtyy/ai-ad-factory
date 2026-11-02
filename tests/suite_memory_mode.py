"""🎉 모임·행사·추억 쇼츠 (단독 앱 1단계 토글): N컷 N카피 1:1(컷수 고정 금지)·사실 가드·톤 매칭·렌더·카카오 멘트·화면 흐름"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import json
import os
import shutil
import subprocess

import imageio_ffmpeg

from core import pipeline
from core.job import JobContext
from core.llm import router
from core.memory_pack import build_memory_packages, kakao_message, memory_copy, seo_metadata
from core.planners import memory as mem

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


work = tmp("memory_mode_")
P1, P2 = fixture("photo_room.jpg"), fixture("photo_pool.jpg")
photos = [shutil.copy(P1 if i % 2 else P2, os.path.join(work, f"p{i}.jpg")) for i in range(10)]
for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(k, None)

# ---------------------------------------------------------------- 1. 컷수 고정 금지: 올린 개수 그대로 N컷 N줄 (1~10)
for n in (1, 2, 4, 7, 10):
    sc = mem.plan_memory_storyboard("가평 상인회 가을 단합대회", "lively", photos[:n], mock=True)
    lines = [s["voiceover"] for s in sc]
    check(len(sc) == n and len(set(l.replace(" ", "") for l in lines)) == n and all(s["subtitle"] == s["voiceover"] == s["caption"] for s in sc),
          f"{n}개 → {n}컷 {n}줄 (중복 없음, 자막=음성)")
sc = mem.plan_memory_storyboard("", "warm", photos * 2, mock=True)
check(len(sc) == 10, "11개 이상 올려도 최대 10컷")
check([s["role"] for s in mem.plan_memory_storyboard("", "warm", photos[:4], mock=True)] == ["intro", "moment", "moment", "ending"],
      "역할: 도입 → 즐거운 순간들 → 마무리")
try:
    mem.plan_memory_storyboard("행사", "warm", [], mock=True)
    check(False, "사진 없으면 ValueError")
except ValueError as e:
    check("1개 이상" in str(e), f"사진 없으면 지어내지 않고 안내: {e}")
check(all(not s["hook_title"] and s["duration_sec"] == 2.0 for s in mem.plan_memory_storyboard("행사", "formal", photos[:4], mock=True)),
      "후킹 헤드라인 없음(줄 수=컷 수 유지)·컷당 2초")

# ---------------------------------------------------------------- 2. 사실 가드: 이름·관계·숫자·판촉은 안전 문구로
check(mem.find_violation("엄마가 환하게 웃어요!", "가을 단합대회") and not mem.find_violation("엄마 생신 축하해요!", "엄마 생신 파티"),
      "관계 호칭은 모임 이름에 적힌 것만 (엄마: 이름에 없으면 차단, 있으면 허용)")
check(mem.find_violation("부모님이 웃어요!", "가을 단합대회") and not mem.find_violation("부모님이 웃어요!", "부모님 생신"),
      "'부모님'은 이름에 적혀 있을 때만 허용")
check(mem.find_violation("50명이 모였어요!", "상인회 모임") and not mem.find_violation("30주년 기념이에요!", "개교 30주년 동창회"),
      "숫자는 모임 이름에 있는 것만")
check(mem.find_violation("지금 예약하면 무료!", "") and mem.find_violation("최저가로 즐겨요", ""), "판촉·광고 표현 차단")
bad, notes = mem.finalize_lines(["엄마가 웃어요", "건배하는 순간!", "건배하는 순간!", "50명 모였네", "길게길게길게길게길게길게길게길게길게길게길게 이어지는 아주 긴 문장입니다 정말로", "마무리 한마디 ^^ 😀"],
                                6, "친구 동창회", "lively")
check(len(bad) == 6 and len(set(b.replace(" ", "") for b in bad)) == 6 and not any(mem.find_violation(b, "친구 동창회") for b in bad)
      and "😀" not in bad[5] and "^^" not in bad[5] and bad[1] == "건배하는 순간!" and len(notes) >= 4,
      f"위반·중복·과장 줄은 안전 문구로, 이모티콘 제거: {bad}")
short, n2 = mem.finalize_lines(["하나뿐인 줄"], 4, "", "warm")
check(len(short) == 4 and any("줄 수" in x for x in n2), "엔진이 줄을 덜 주면 부족분은 안전 문구로 채워 정확히 N줄")
long_, n3 = mem.finalize_lines(["가", "나", "다", "라", "마"], 3, "", "warm")
check(len(long_) == 3, "엔진이 더 주면 앞에서 N줄만")

# ---------------------------------------------------------------- 3. LLM 경로: 컷 이미지·톤·모임 이름이 프롬프트에 들어가고 vision 티어 호출
seen = {}


def fake_claude(contents, system, temperature, max_tokens):
    seen["texts"] = [c for c in contents if isinstance(c, str)]
    seen["images"] = sum(1 for c in contents if not isinstance(c, str))
    seen["system"] = system
    n = int([t for t in seen["texts"] if "정확히" in t][0].split("정확히 ")[1].split("줄")[0])
    pool = ["상 위에 음식이 가득해요!", "다 같이 잔을 들었어요!", "환하게 웃는 얼굴들이에요.", "단체 사진 한 장 남겨요!", "창밖 풍경도 한몫해요.",
            "노래 소리가 번져요!", "손뼉 치는 소리가 커져요.", "이 순간이 오래 남아요.", "작별 인사도 웃으며 해요.", "다음에 또 모여요!"]
    return json.dumps({"story": "웃음 가득한 하루", "scenes": [{"scene_index": i + 1, "voiceover": pool[i]} for i in range(n)]}, ensure_ascii=False)


os.environ["ANTHROPIC_API_KEY"] = "test-key"
router._overrides["claude"] = fake_claude
try:
    sc = mem.plan_memory_storyboard("우리 가족 1박2일 힐링 여행", "warm", photos[:4], mock=False, thumb_dir=work)
    prompt = "\n".join(seen["texts"])
    check(seen["images"] == 4 and "[컷 1]" in prompt and "[컷 4]" in prompt and "정확히 4줄" in prompt and "우리 가족 1박2일 힐링 여행" in prompt
          and "따뜻하고 감동적이게" in prompt and "네이버" not in prompt and "상호" not in prompt, "프롬프트: 컷 4장 이미지·정확히 4줄·모임 이름·톤 포함, 매장 용어 없음")
    check(len(sc) == 4 and sc[0]["script_engine"] == "claude" and sc[0]["memory"]["cuts"] == 4 and "판촉" in seen["system"],
          f"LLM 대본 4줄 + 엔진 표시: {[s['voiceover'] for s in sc]}")
    check(router.LAST_ENGINE.get("모임 추억 대본", {}).get("engine") == "claude", "목적 '모임 추억 대본'으로 엔진 기록")

    def broken_claude(contents, system, temperature, max_tokens):
        raise RuntimeError("boom")
    router._overrides["claude"] = broken_claude
    sc2 = mem.plan_memory_storyboard("", "lively", photos[:5], mock=False, thumb_dir=work)
    check(len(sc2) == 5 and sc2[0]["script_engine"] == "template", "엔진이 모두 실패해도 같은 N줄을 안전 문구로 (script_engine=template)")
finally:
    router._overrides.pop("claude", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)

# ---------------------------------------------------------------- 4. 공유 문구
check(kakao_message("가평 상인회 가을 단합대회") == "우리 가평 상인회 가을 단합대회 영상 하나 만들어봤어! 다들 너무 반가웠고 다음 모임 때 또 보자~ 🎬✨"
      and kakao_message("").startswith("우리 모임 영상 하나 만들어봤어!"), "카카오톡 단톡방 멘트: 모임 이름 치환 (이름 없으면 '우리 모임')")
pk = build_memory_packages("친구들과 7080 동창회", "lively")
check(list(pk)[0] == "kakao" and len(pk) == 5 and pk["kakao"]["body"].startswith("우리 친구들과 7080 동창회 영상"), "탭: 카카오톡 단톡방이 첫 번째 + 4대 플랫폼")
alltext = " ".join(v["body"] + v["title"] for v in pk.values())
check(not any(w in alltext for w in ("가게추천", "검색", "네이버 지도", "메뉴", "위치")) and "#Shorts" in pk["youtube"]["title"], "매장용 문구(가게추천·검색·위치) 없음")
c = memory_copy("가평 조종면 상인회 가을 단합대회", "formal")
check(len(c["thumb_main"]) <= 14 and c["title"].endswith("#Shorts") and c["tag_set"]["pool"] and not any("가게" in t for t in c["hashtags"]),
      f"썸네일 카피 14자 이내·해시태그 추억용: {c['thumb_main']} {c['hashtags']}")
check(memory_copy("", "warm")["thumb_main"] == "함께한 하루", "모임 이름이 없으면 톤별 기본 썸네일")
check(seo_metadata("동창회", "lively")["title"] and "동창회" in seo_metadata("동창회", "lively")["description"], "SEO 메타데이터(제목·설명·태그)")

# ---------------------------------------------------------------- 5. 공용 엔진: 기획(1:1 배정) → 렌더 (Mock, 브랜드·엔딩 카드·매장 SEO 없음)
vid = os.path.join(work, "clip.mp4")
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=4",
                "-pix_fmt", "yuv420p", vid], check=True)
media = [photos[0], vid, photos[1], photos[2]]
spec = pipeline.JobSpec(mode="owner", name="우리 동창회", media_paths=media, memory=True, memory_tone="lively", mock=True, audio_mode="silent")
job = JobContext.create(app="test")
scenes = pipeline.plan(spec, job)
check(len(scenes) == 4 and not pipeline.missing_media(scenes) and scenes[0]["image_path"] == photos[0] and (scenes[1].get("media") or {}).get("path") == vid
      and scenes[2]["image_path"] == photos[1], "엔진 기획: 파일 4개 → 4씬, i번째 파일 = i번째 씬 (영상 포함)")
check(scenes[1]["duration_sec"] >= 3.5 and scenes[0]["duration_sec"] == 2.0, f"영상 컷은 실제 길이 재생({scenes[1]['duration_sec']}초), 사진 컷은 2초")
tx = pipeline.memory_upload_texts(spec, scenes)
check(tx["comment"].startswith("우리 우리 동창회 영상") and tx["title"].endswith("#Shorts") and "#추억" in tx["desc"], "업로드 문구: 카카오 멘트 + 추억 해시태그")
res = pipeline.render(spec, job, scenes)
from core.media import probe_media  # noqa: E402
dur = probe_media(res.final_path).duration
check(os.path.exists(res.final_path) and res.shorts_copy["source"] == "memory" and res.metadata and "동창회" in res.metadata["description"],
      "엔진 렌더: 영상·추억 전용 카피·추억 메타데이터")
check(8.0 <= dur <= 14.0, f"렌더 길이 ≈ 컷 합계 ({dur:.1f}초; 사진 3×2초 + 영상 4초)")
shutil.rmtree(job.workdir, ignore_errors=True)

# ---------------------------------------------------------------- 6. 화면: 1단계 토글 → 모임·행사·추억 폼 → 기획 → 편집 카드 1:1
from streamlit.testing.v1 import AppTest  # noqa: E402

at = AppTest.from_file(os.path.join(ROOT, "app_store.py"), default_timeout=300).run()
check(not at.exception, f"app_store.py 기본 실행 오류 없음 {[e.value[:80] for e in at.exception]}")
kind = [r for r in at.radio if r.key == "store_kind"]
check(kind and kind[0].value == "shop" and list(kind[0].options) == ["🏪 매장 홍보", "🎉 모임·행사·추억"], f"1단계 상단 토글 2개, 기본값 매장 홍보: {kind[0].options if kind else None}")
check(any(b.key == "place_fetch" for b in at.button) and not [t for t in at.text_input if t.key == "mem_title"], "기본(매장 홍보): 네이버 검색·메뉴판 그대로, 추억 입력칸 없음")
at.radio(key="store_kind").set_value("memory").run()
check(not at.exception, f"토글 전환 오류 없음 {[e.value[:100] for e in at.exception]}")
check(not any(b.key == "place_fetch" for b in at.button) and not [t for t in at.text_area if t.key == "official_menu_text"]
      and not [t for t in at.text_input if t.key in ("owner_name", "place_query", "owner_search_kw")], "추억: 네이버 검색창·메뉴판·상호명 입력 숨김")
ti = [t for t in at.text_input if t.key == "mem_title"]
tone = [r for r in at.radio if r.key == "mem_tone"]
check(ti and ti[0].label.startswith("🎈 모임·행사 이름") and "가평 조종면 상인회 가을 단합대회" in ti[0].placeholder, "입력칸: 🎈 모임·행사 이름 (선택) + 예시 플레이스홀더")
check(tone and len(tone[0].options) == 3 and tone[0].options[0].startswith("🌟 유쾌하고 활기차게") and tone[0].options[1].startswith("🌿 따뜻하고 감동적이게")
      and tone[0].options[2].startswith("🏆 기록·기념 공식형"), f"분위기 톤 3종: {tone[0].options if tone else None}")
heads = [m.value for m in at.markdown if m.value.startswith("###")]
check(all(any(f"[{n}단계]" in h for h in heads) for n in (1, 2, 3, 4)) and not any("컨셉 잡기" in h for h in heads), f"1~4단계 일직선 유지, 컨셉 고르기 없음: {[h[:20] for h in heads]}")
labels = [b.label for b in at.button]
check("🚀 추억 대본 만들기" in labels and "🚀 선택된 컨셉으로 대본 만들기" not in labels and not any(l.startswith("🔍 사진 팩트") for l in labels), "3단계: 추억 대본 버튼만")
# 톤 → 폰트 자동 매칭
at.radio(key="mem_tone").set_value("warm").run()
check(at.session_state["caption_font_choice"] == "serif" and at.session_state["audio_choice"] == "voice_and_sub", "따뜻한 톤 → 감성 명조 + AI 나레이션")
at.radio(key="mem_tone").set_value("lively").run()
check(at.session_state["caption_font_choice"] == "pop", "유쾌한 톤 → 둥근 예능 팝체")
# 사진 올림(세션 주입) → 안내 문구 → 기획 → 카드
at.session_state["uploaded_paths"] = photos[:4]
at.text_input(key="mem_title").set_value("가평 상인회 가을 단합대회")
at.session_state["mock_mode"] = True
at.run()
check(any("4컷" in i.value and "4줄" in i.value for i in at.info), f"안내: 올린 4개 = 4컷 4줄 {[i.value[:50] for i in at.info if '컷' in i.value]}")
at.button(key="generate_plan_btn").click().run()
check(not at.exception, f"기획 오류 없음 {[e.value[:120] for e in at.exception]}")
sb = at.session_state["storyboard"]
check(len(sb) == 4 and [s["image_path"] for s in sb] == photos[:4], "기획: 업로드 4개 → 씬 4개, 사진 1:1")
cards = [t for t in at.text_area if t.key and t.key.startswith("sb_text_")]
check(len(cards) == 4, f"3단계: 씬 1~4 카드에 카피 입력칸 4개 (사진 썸네일 ↔ 카피)")
at.session_state["bgm_on"] = False
at.button(key="render_cta").click().run()
check(not at.exception and at.session_state["final_video_path"] and os.path.exists(at.session_state["final_video_path"]), f"화면 렌더 완료 {[e.value[:100] for e in at.exception]}")
dl = [e for e in at.get("download_button")]
check(dl and "스마트폰 갤러리에 저장하기 (카카오톡 단톡방 공유용)" in dl[0].proto.label, "5단계: 상단 강조 저장 버튼 라벨")
tabs = [t.label for t in at.tabs]
check(tabs[:2] == ["💬 카카오톡 단톡방", "▶ 유튜브 쇼츠"], f"5단계 복사 탭: 카카오톡 단톡방이 첫 탭 {tabs}")
check(any("자동 업로드는 켜 두지 않았어요" in i.value for i in at.info), "추억 영상은 유튜브 자동 업로드 패널 대신 안내")
# 매장 홍보로 돌아가면 입력·결과가 새로 시작
at.radio(key="store_kind").set_value("shop").run()
check(not at.exception and not at.session_state["storyboard"] and any(b.key == "place_fetch" for b in at.button), "매장 홍보로 전환: 새 작업 + 네이버 검색창 복귀")

finish(fails)
