"""트랙 A 7씬 황금 비율 + 비전 슬롯 배정 + BGM 제외 단위 테스트 (오프라인, API 호출 없음)"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, live_skip, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import hashlib
import inspect
import io
import os
import random
import sys
import tempfile

os.chdir(ROOT)
sys.path.insert(0, ROOT)
import logging
logging.disable(logging.WARNING)

from PIL import Image
import core.scraper as sc
from core.analyzer import build_travel_slots, generate_mock_travel_storyboard, plan_storyboard, GOLDEN7_SLOTS
from core.compositor import AdCompositor

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(f"{'PASS' if c else 'FAIL'} {m}")


roles = lambda slots: [s["role"] for s in slots]

# ---------------------------------------------------------------- 1. 7씬 슬롯 계획
full = {"pool": 5, "bedroom": 4, "facility": 3, "view": 2}
r = roles(build_travel_slots(None, full))
check(r == ["pool", "pool", "pool", "bedroom", "bedroom_or_view", "facility", "facility_or_night_view"], f"사진 충분: 수영장3/객실2/부대시설2(엔딩은 야경뷰 허용) → {r}")
s1 = build_travel_slots(None, {"pool": 1, "bedroom": 3, "facility": 2, "view": 3})
check(roles(s1)[0] == "pool" and roles(s1)[1:3] == ["view", "view"] and s1[1]["fallback_from"] == "pool",
      f"수영장 사진 1장: 남은 수영장 슬롯 → 뷰 대체 표시 → {roles(s1)}")
s2 = roles(build_travel_slots(None, {"pool": 0, "bedroom": 3, "facility": 0, "view": 4}))
check("pool" not in s2 and "facility" not in s2 and len(s2) == 7, f"수영장·부대시설 없는 펜션: 해당 역할 미배정 → {s2}")
s3 = roles(build_travel_slots(None, None, "개별 수영장, 바베큐 가능"))
check(s3.count("pool") == 3 and s3[5:] == ["facility", "facility_or_night_view"], f"인벤토리 없음 + USP에 수영장/바베큐 명시 → {s3}")
s4 = roles(build_travel_slots(None, None, "조용한 독채"))
check("pool" not in s4 and "facility" not in s4, f"인벤토리 없음 + USP 근거 없음: 수영장·부대시설 가정 안 함 → {s4}")
check(len(build_travel_slots(5, full, template="classic")) == 5, "classic 템플릿은 기존처럼 지정 씬 수 유지")

# ---------------------------------------------------------------- 2. Mock 콘티 / plan_storyboard
m = generate_mock_travel_storyboard("힐튼 경주", "", 6, photo_inventory=full)
check(len(m) == 7 and m[-1]["voiceover"] == "숙소 정보는 댓글에 '여행' 남겨주세요!" and m[-1]["target_visual_role"] == "facility_or_night_view",
      "Mock 7씬: 마지막 씬 = 부대시설 + 댓글 '여행' CTA")
check(len({x["voiceover"] for x in m}) == 7 and all(x.get("visual_tags") for x in m), "Mock 7씬: 대사 중복 없음 + 씬별 선호 태그 포함")
check(all("힐튼" not in x["voiceover"] for x in m), "Mock 7씬: 숙소명 은닉")
p7 = plan_storyboard(product_name="X", usp="", mode="travel", mock=True, num_scenes=3, photo_inventory=full)
p6 = plan_storyboard(product_name="X", usp="", mode="travel", mock=True, photo_inventory=full, travel_template="classic")
check(len(p7) == 7 and len(p6) == 6, f"plan_storyboard: golden7 기본 7씬 고정(num_scenes 무시), classic 6씬 → {len(p7)}/{len(p6)}")

# ---------------------------------------------------------------- 3. 비전 슬롯 배정 (가짜 CDN + 가짜 Gemini 판정)
def jpeg(seed, size=(900, 700)):
    rnd = random.Random(seed)
    img = Image.frombytes("RGB", size, bytes(rnd.getrandbits(8) for _ in range(size[0] * size[1] * 3)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


spec = [  # (url key, 제목, 텍스트 카테고리, 비전 판정)
    ("infinity", "", "unknown", ("pool", 9, ["infinity_pool", "outdoor_pool"])),
    ("outdoor", "야외수영장", "pool", ("pool", 8, ["outdoor_pool"])),
    ("jacuzzi", "", "unknown", ("pool", 8, ["jacuzzi"])),
    ("indoor", "실내수영장", "pool", ("pool", 7, ["indoor_pool"])),
    ("pool_low", "수영장", "pool", ("pool", 4, ["outdoor_pool"])),
    ("bed1", "디럭스 객실", "bedroom", ("bedroom", 8, ["bed", "room_interior"])),
    ("bed2", "", "unknown", ("bedroom", 7, ["bed"])),
    ("roomview", "", "unknown", ("bedroom", 8, ["mountain_view", "bed"])),
    ("bbq", "", "unknown", ("facility", 8, ["bbq"])),
    ("firepit", "", "unknown", ("facility", 8, ["firepit", "night"])),
    ("cafe", "카페", "facility", ("facility", 7, ["cafe"])),
    ("lake", "전경", "view", ("view", 9, ["water_view"])),
    ("vending", "로비", "facility", ("reject", 1, [])),
    ("apt", "외관", "view", ("reject", 0, [])),
]
content, verdict_by_hash, pool = {}, {}, []
for i, (key, title, cat, (vcat, fit, tags)) in enumerate(spec):
    url = f"https://cdn.test/{key}.jpg"
    content[url] = jpeg(i)
    verdict_by_hash[hashlib.md5(content[url]).hexdigest()] = {"category": vcat, "fit": fit, "tags": tags, "reason": key}
    pool.append({"url": url, "title": title, "category": cat, "referer": "https://www.test/"})
content["https://cdn.test/infinity_dup.jpg"] = content["https://cdn.test/infinity.jpg"]   # 같은 사진 다른 URL
pool.append({"url": "https://cdn.test/infinity_dup.jpg", "title": "인피니티풀", "category": "pool"})
small = io.BytesIO(); Image.new("RGB", (200, 200), "blue").save(small, "JPEG"); content["https://cdn.test/tiny.jpg"] = small.getvalue()
pool.append({"url": "https://cdn.test/tiny.jpg", "title": "수영장 썸네일", "category": "pool"})


class R:
    def __init__(self, b): self.status_code, self.content = 200, b


calls = {"vision": 0, "download": 0}
sc.requests.get = lambda url, headers=None, timeout=None: (calls.__setitem__("download", calls["download"] + 1), R(content[url]))[1]
sc._create_client = lambda key: object() if key else None


def fake_classify(img_bytes, client, cache=None):
    key = f"{hashlib.md5(img_bytes).hexdigest()}:classify"
    if cache is not None and key in cache:
        return cache[key]
    calls["vision"] += 1
    v = verdict_by_hash[hashlib.md5(img_bytes).hexdigest()]
    if cache is not None:
        cache[key] = v
    return v


sc.classify_photo_with_vision = fake_classify

out = tempfile.mkdtemp(prefix="golden7_")
scenes = generate_mock_travel_storyboard("X", "", 7, photo_inventory=full)
cache = {}
inv = sc.classify_image_pool(pool, out, api_key="k", vision_cache=cache)
check(inv["pool"] == 4 and inv["bedroom"] == 3 and inv["facility"] == 3 and inv["view"] == 1,
      f"비전 인벤토리: 적합도 6 미만·reject·중복·100KB 미만 제외 → {inv}")
check(any(c.get("duplicate_of") for c in pool) and next(c for c in pool if "tiny" in c["url"])["vision"]["category"] == "reject",
      "중복 사진(해시 동일) / 100KB 미만 사진 표시")
first_calls = calls["vision"]
sc.download_targeted_scene_assets(scenes, pool, out, api_key="k", vision_cache=cache)
got = [s["matched_image_url"].split("/")[-1].replace(".jpg", "") if s.get("matched_image_url") else None for s in scenes]
print("     배정 결과:", got)
by_url = {p["url"]: p for p in pool}
cats = [by_url[s["matched_image_url"]]["vision"]["category"] for s in scenes if s.get("matched_image_url")]
check(cats.count("pool") == 3 and cats.count("bedroom") == 2 and cats.count("facility") == 2, f"비율 배정: 수영장3/객실2/부대시설2 → {cats}")
check(got[0] in ("infinity", "infinity_dup"), f"씬 1 = 인피니티 풀 사진 (후킹 태그 + 최고 적합도, 동일 사진 URL 2개 중 1개) → {got[0]}")
check(got[2] == "jacuzzi", "씬 3 = 자쿠지 (자쿠지/물멍 태그)")
check(got[4] == "roomview", "씬 5 = 창밖 마운틴 뷰 객실 (mountain_view 태그)")
check(got[6] == "firepit", "씬 7(엔딩) = 불멍·야간 (firepit/night 태그)")
check(not {"vending", "apt", "pool_low", "tiny"} & set(got) and len({"infinity", "infinity_dup"} & set(got)) == 1,
      "자판기/외벽(reject)·저적합·저용량 미사용 + 동일 사진(해시) 중복 사용 없음")
check(len(set(got)) == 7 and all(os.path.exists(s["image_path"]) for s in scenes), "7씬 모두 서로 다른 사진 + 파일 생성")
check(calls["vision"] == first_calls, f"배정 단계 추가 비전 호출 없음 (캐시 재사용, 총 {calls['vision']}회)")

# 재선별: 씬 2 거부 → 남은 수영장 중 차선 (인피니티/자쿠지 등 다른 씬 사용 사진 제외)
rejected = scenes[1]["matched_image_url"]
in_use = {s["matched_image_url"] for s in scenes if s is not scenes[1]}
sc.download_targeted_scene_assets([scenes[1]], pool, out, api_key="k", exclude_urls={rejected} | in_use, vision_cache=cache)
check(scenes[1]["matched_image_url"].endswith("indoor.jpg"), f"씬 2 거부 → 남은 수영장 차선(indoor)으로 교체: {scenes[1]['matched_image_url']}")

# 수영장 부족: 수영장 슬롯 4개 요청 시 1개는 뷰로 대체 표시
extra = [{"scene_index": i + 1, "target_visual_role": "pool", "visual_tags": []} for i in range(5)]
sc.download_targeted_scene_assets(extra, pool, out, api_key="k", vision_cache=cache)
fb = [s.get("asset_fallback") for s in extra]
check(fb.count(None) == 4 and fb.count("view") == 1, f"수영장 슬롯 5개 > 사용 가능 수영장 4장: 1개는 뷰로 대체 표시 → {fb}")

# 비전 미사용(API 키 없음): 텍스트 카테고리로 배정
pool_text = [{k: v for k, v in c.items() if k not in ("vision", "vision_checked", "local_path", "image_hash", "duplicate_of")} for c in pool]
scenes_t = generate_mock_travel_storyboard("X", "", 7, photo_inventory=full)
sc.download_targeted_scene_assets(scenes_t, pool_text, tempfile.mkdtemp(), api_key=None)
check(sum(1 for s in scenes_t if s.get("image_path")) >= 5 and "vending" not in str([s.get("matched_image_url") for s in scenes_t]),
      f"API 키 없음: 텍스트 메타 기준 배정 ({sum(1 for s in scenes_t if s.get('image_path'))}/7씬), 네거티브 제외")

# 엔딩 씬 7: 고득점 노을/야경/테라스 뷰가 로비보다 우선, 평범한 낮 뷰는 엔딩 후보 아님
def ending_case(name, items):
    pool_e, verdicts = [], {}
    for i, (key, vcat, fit, tags) in enumerate(items):
        url = f"https://cdn.test/end_{name}_{key}.jpg"
        content[url] = jpeg(1000 + hash((name, key)) % 100000)
        verdicts[hashlib.md5(content[url]).hexdigest()] = {"category": vcat, "fit": fit, "tags": tags, "reason": key}
        verdict_by_hash.update(verdicts)
        pool_e.append({"url": url, "title": "", "category": "unknown"})
    scene7 = {"scene_index": 7, "target_visual_role": "facility_or_night_view", "visual_tags": ["firepit", "night", "sunset", "terrace", "bbq"]}
    sc.download_targeted_scene_assets([scene7], pool_e, tempfile.mkdtemp(), api_key="k", vision_cache={})
    return (scene7.get("matched_image_url") or "").split("_")[-1].replace(".jpg", "")

e1 = ending_case("a", [("lobby", "facility", 8, ["lounge"]), ("terrace", "view", 9, ["terrace", "night", "mountain_view"]), ("daytime", "view", 10, ["exterior"])])
check(e1 == "terrace", f"엔딩: 노을·야경 테라스 뷰(9) > 로비(8), 낮 외관(10)은 제외 → {e1}")
e2 = ending_case("b", [("lobby", "facility", 8, ["lounge"]), ("daytime", "view", 10, ["exterior", "mountain_view"])])
check(e2 == "lobby", f"엔딩: 야경/노을 뷰가 없으면 부대시설 유지 (낮 풍경으로 대체하지 않음) → {e2}")
e3 = ending_case("c", [("firepit", "facility", 8, ["firepit", "night"]), ("sunset", "view", 7, ["sunset", "water_view"])])
check(e3 == "firepit", f"엔딩: 불멍(태그 2개 일치) 부대시설이 있으면 그대로 우선 → {e3}")

# ---------------------------------------------------------------- 4. BGM: 기본 꺼짐, 자체 음원(assets/bgm)만 (남의 곡·트렌딩 음원은 엔진에서 합성하지 않음)
sig = inspect.signature(AdCompositor.assemble_ad)
src = inspect.getsource(AdCompositor.assemble_ad)
import core.render.bgm as _bgm
check(sig.parameters["bgm_path"].default is None and "CompositeAudioClip" not in src and "audio_loop" not in src
      and _bgm.BGM_DIR.replace("\\", "/").endswith("assets/bgm"),
      "BGM은 기본 꺼짐, 켜면 자체 음원 폴더(assets/bgm) 음원만 후처리 믹싱 (MoviePy 오디오 합성 코드 없음)")
app_src = open("app.py", encoding="utf-8").read()
main_src = open("main.py", encoding="utf-8").read()
check("voice_and_bgm" not in app_src and "voice_and_bgm" not in main_src, "앱/CLI 오디오 모드에서 BGM 옵션 제거")
check("🔇 BGM 꺼짐" in app_src and "BGM 없이 올릴 땐 플랫폼 추천 트렌딩 음원을 태그해 보세요" in app_src,
      "렌더 완료: BGM 없이 만든 영상엔 'BGM 꺼짐' + 트렌딩 음원 태그 안내")
finish(fails)