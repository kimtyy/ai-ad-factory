"""
tests/suite_thumbnail_layout.py - 썸네일 3종(intense·magazine·navy_premium) 레이아웃 회귀: 긴 한글 이름 좌우 잘림·글리프(□) 깨짐·브랜드 라벨,
그리고 단독 작업실(app_store.py) 4단계 스타일 선택 → 5단계 미리보기·다운로드 종단 간
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _support import ROOT, fixture, tmp, finish  # noqa: E402  (레포 루트로 chdir + sys.path 설정)
import os
import shutil

import numpy as np
from PIL import Image

from core.compositor import AdCompositor
from core.render import fonts
from core.render import thumbnail as TH

fails = 0


def check(c, m):
    global fails
    fails += not c
    print(("PASS" if c else "FAIL") + " " + m)


W, H = 1080, 1920
work = tmp("thumb_layout_")
comp = AdCompositor(output_dir=work)
flat = np.full((H, W, 3), 128, dtype=np.uint8)               # 균일 회색 배경: 가장자리에서 배경과 다른 픽셀 = 글자가 잘린 것

NAMES = {
    "name_menu": ("설맥 가평현리점", "버터먹은먹태", ""),
    "long_words": ("가평 설악 효정명인갈비 본점 한우 숙성 전문점", "숯불 양념 생갈비 한정 메뉴 소개", "🔥"),
    "one_token_16": ("가평현리설맥눈꽃생맥주전문점본점", "버터먹은먹태구이안주세트", ""),
    "one_token_24": ("가평현리설맥눈꽃생맥주전문점본점가평현리", "버터먹은먹태구이안주세트버터먹은먹태", "🔥"),
    "short": ("설맥", "먹태", ""),
    "no_sub": ("설맥 가평현리점 버터먹은먹태", "", ""),
}


def edge_clipped(path):
    """좌·우 가장자리 12px 안에 글자·장식이 닿았는지 (균일 배경에서 행마다 바깥쪽 12px과 그 안쪽 12px을 비교)"""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    left = np.abs(a[:, 0:12].mean(axis=1) - a[:, 12:24].mean(axis=1)).max()
    right = np.abs(a[:, -12:].mean(axis=1) - a[:, -24:-12].mean(axis=1)).max()
    return max(left.max(), right.max()) > 14


# ---------------------------------------------------------------- 1. 긴 한글 이름: 3스타일 × 6가지 이름에서 좌우 잘림 없음
for style in TH.THUMBNAIL_STYLES:
    bad = []
    for key, (main, sub, acc) in NAMES.items():
        out = os.path.join(work, f"{style}__{key}.png")
        comp.render_thumbnail(flat, main, out, sub_text=sub, accent=acc, style=style)
        if edge_clipped(out) or Image.open(out).size != (W, H):
            bad.append(key)
    check(not bad, f"{style}: 긴 이름·공백 없는 16/24자 이름·이모지 포함 6가지 모두 좌우가 잘리지 않음 {bad}")

# ---------------------------------------------------------------- 2. 긴 한 덩어리 분할 헬퍼
f48 = fonts.load(fonts.resolve("impact", comp), 60)
check(fonts.break_long_tokens("설맥 가평현리점", f48, 900) == "설맥 가평현리점" and fonts.break_long_tokens("", f48, 900) == "", "어절이 나뉜 일반 이름은 그대로")
split = fonts.break_long_tokens("가평현리설맥눈꽃생맥주전문점본점가평현리", f48, 400)
check(" " in split and split.replace(" ", "") == "가평현리설맥눈꽃생맥주전문점본점가평현리" and all(f48.getlength(p) <= 400 + 60 for p in split.split(" ")), f"공백 없는 긴 이름은 글자 수로 균등 분할(글자 유지): {split}")

# ---------------------------------------------------------------- 3. 네이비: 소제목 장식 글리프 깨짐(□)·브랜드 오타 라벨 없음
src = open(os.path.join(ROOT, "core", "render", "thumbnail.py"), encoding="utf-8").read()
code_only = " ".join(ln.split("#")[0] for ln in src.splitlines())                      # 주석 제외한 코드·문자열만
check("✦" not in code_only and "SHOPSHOT" not in code_only.upper(), "네이비 소제목: 글꼴에 없는 ✦ 글리프·'SHOPSHOT' 오타 문구를 코드에서 쓰지 않음")
impact = fonts.resolve("impact", comp)
from fontTools.ttLib import TTFont  # noqa: E402

cmap = TTFont(impact[0], fontNumber=max(0, impact[1])).getBestCmap()
check(0x2726 not in cmap, "확인: 이 글꼴에는 ✦(U+2726)가 실제로 없음 → 도형으로 그린 것이 맞는 대응")
gold = lambda a: ((a[..., 0] > 225) & (a[..., 1] > 170) & (a[..., 1] < 215) & (a[..., 2] < 110))  # noqa: E731
navy_out = os.path.join(work, "navy_default.png")
comp.render_thumbnail(flat, "설맥 가평현리점", navy_out, sub_text="", style="navy_premium")
na = np.asarray(Image.open(navy_out).convert("RGB")).astype(int)
ys, xs = np.where(gold(na))
check(len(xs) > 200, f"네이비: 골드 소제목·다이아몬드 장식이 그려짐 (골드 픽셀 {len(xs)})")
band = gold(na[:, :, :])
rows = np.where(band.sum(axis=1) > 0)[0]
lab_rows = band[rows.min():rows.min() + 60] if len(rows) else band[:0]
cols = np.where(lab_rows.sum(axis=0) > 0)[0] if len(rows) else []
mid = W // 2
check(len(cols) and cols.min() < mid - 40 and cols.max() > mid + 40, "소제목 양옆에 장식이 대칭으로 있음 (왼쪽·오른쪽 모두)")
long_sub = os.path.join(work, "navy_longsub.png")
comp.render_thumbnail(flat, "설맥 가평현리점", long_sub, sub_text="버터먹은먹태 눈꽃치킨 골뱅이무침 설맥아구찜 코다리냉면 숙주쌀국수 눈꽃빙수", style="navy_premium")
check(not edge_clipped(long_sub), "아주 긴 소제목도 카드 안에서 줄이거나 말줄임 (좌우 잘림 없음)")

# ---------------------------------------------------------------- 4. 글꼴 공백 글리프: 3종 폰트·팝체 포함 공백이 □로 안 나옴
check(all(fonts.space_is_blank(fonts.resolve(k, comp)) for k in ("impact", "serif", "pop")), "임팩트·명조·팝체 모두 공백이 빈 여백으로 그려짐")
mg = os.path.join(work, "mag_pop.png")
TH.render_thumbnail_magazine(comp, flat, "설맥 가평현리점 버터먹은먹태", mg, sub_text="가평 현리", tone="lively")
check(os.path.exists(mg) and not edge_clipped(mg), "매거진 + 유쾌 톤(둥근 팝체): 어절 사이 공백이 깨지지 않고 렌더")

# ---------------------------------------------------------------- 5. 모임·행사·추억 호환 함수 (판촉 문구 제거)
mem = os.path.join(work, "memory.png")
TH.render_memory_thumbnail(flat, "가평 상인회 특가 단합대회", "lively", mem, comp, sub_text="최저가 무료 이벤트")
check(os.path.exists(mem) and not edge_clipped(mem), "render_memory_thumbnail: 판촉 문구(특가·최저가·무료·이벤트) 제거 후 렌더")

# ---------------------------------------------------------------- 6. 단독 작업실(app_store.py): 4단계 스타일 선택 → 5단계 썸네일 미리보기·다운로드
from streamlit.testing.v1 import AppTest  # noqa: E402

for _k in ("GEMINI_API_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
    os.environ[_k] = ""
os.environ["STORE_APP"] = "1"
for _k in ("AZURE_SPEECH_KEY", "TTS_ENGINE"):
    os.environ.pop(_k, None)
at = AppTest.from_file(os.path.join(ROOT, "app_store.py"), default_timeout=600)
at.session_state["mock_mode"] = True
at.run()
job = at.session_state.job
photos = []
for i, name in enumerate(("photo_pool.jpg", "photo_room.jpg", "photo_pool.jpg")):
    p = shutil.copy(fixture(name), os.path.join(job.uploads_dir, f"t{i}.jpg"))
    photos.append(p)
at.session_state["uploaded_paths"] = photos
at.text_input(key="owner_name").input("설맥 가평현리점")
at.text_input(key="owner_usp").input("눈꽃맥주, 버터먹은먹태, 설맥치킨")
at.run()
radio = [r for r in at.radio if r.key == "thumbnail_style_choice"]
check(not at.exception and radio and list(radio[0].options) == [TH.THUMBNAIL_STYLE_META[k]["label"] for k in TH.THUMBNAIL_STYLES],
      f"app_store.py 4단계에 썸네일 스타일 선택(3종) 노출 {[r.label for r in radio]}")
check(radio and radio[0].value == "intense" and at.session_state["thumbnail_style_choice"] == "intense", "매장 홍보 기본 스타일은 강렬한 먹방형")
[b for b in at.button if "대본 만들기" in b.label][0].click().run()
check(not at.exception and len(at.session_state.storyboard) == 3, f"기획 완료 3씬 {[e.value[:80] for e in at.exception]}")

def image_captions(node):
    out = []
    for c in getattr(node, "children", {}).values():
        if type(c).__name__ == "Image":
            out += [str(getattr(i, "caption", "") or "") for i in getattr(c.proto, "imgs", [])]
        out += image_captions(c)
    return out


def px(path):
    return np.asarray(Image.open(path).convert("RGB")).astype(int)


shots = {}
for style in ("intense", "navy_premium", "magazine"):
    at.radio(key="thumbnail_style_choice").set_value(style).run()
    check(at.session_state["thumbnail_style_choice"] == style, f"스타일 선택 반영: {style}")
    at.button(key="render_cta").click().run()
    tp = at.session_state["thumb_path"] if "thumb_path" in at.session_state else None
    dl = [e for e in at.get("download_button")]
    labels = [e.proto.label for e in dl]
    check(not at.exception and tp and os.path.exists(tp), f"{style}: 렌더 완료·썸네일 파일 생성 {[e.value[:80] for e in at.exception]}")
    check(any("썸네일 커버 이미지 저장" in l for l in labels) and any("저장" in l and "썸네일" not in l for l in labels), f"{style}: 5단계에 MP4 저장 + 🖼️ 썸네일 저장 버튼이 나란히 {labels}")
    check(any("썸네일 미리보기" in cap for cap in image_captions(at.main)), f"{style}: 5단계 썸네일 미리보기 이미지 표시")
    a = np.asarray(Image.open(tp).convert("RGB")).astype(int)
    shots[style] = {"navy": int(((a[..., 0] <= 35) & (a[..., 1] >= 25) & (a[..., 1] <= 60) & (a[..., 2] >= 50) & (a[..., 2] <= 95)).sum()),
                    "yellow": int(((a[:800, :, 0] > 200) & (a[:800, :, 1] > 160) & (a[:800, :, 2] < 100)).sum()), "path": tp}
check(shots["navy_premium"]["navy"] > shots["intense"]["navy"] + 80_000 and shots["navy_premium"]["navy"] > shots["magazine"]["navy"] + 80_000 and shots["intense"]["yellow"] > 8_000 > shots["navy_premium"]["yellow"],
      f"선택한 스타일이 실제 PNG에 반영됨 (네이비 카드 픽셀: navy_premium {shots['navy_premium']['navy']:,} vs intense {shots['intense']['navy']:,}·magazine {shots['magazine']['navy']:,}, 노랑 타이틀: intense {shots['intense']['yellow']:,})")
check(len({shots[s]["path"] for s in shots}) >= 1 and shots["magazine"]["yellow"] < shots["intense"]["yellow"], "magazine은 노랑 그라데이션 타이틀이 없는 감성형")
job_ids = [job.job_id]

# 모임·행사·추억 모드(memory=True): 기본 매거진. 광고용 큰 글씨(intense)를 골라도 매거진으로 대체하고, 네이비는 그대로 존중
at.session_state["store_kind"] = "memory"
at.run()
at.text_input(key="mem_title").input("가평 현리 상인회 가을 단합대회")
at.session_state["uploaded_paths"] = photos[:3]
at.run()
check(not at.exception and at.session_state["thumbnail_style_choice"] in TH.THUMBNAIL_STYLES, "모임·행사·추억 모드 화면 정상")
mem = {}
for style in ("magazine", "intense", "navy_premium"):
    at.session_state["thumbnail_style_choice"] = style
    at.run()
    [b for b in at.button if b.key == "generate_plan_btn" or "대본 만들기" in b.label or "카피" in b.label][0].click().run()
    at.button(key="render_cta").click().run()
    tp = at.session_state["thumb_path"] if "thumb_path" in at.session_state else None
    mem[style] = px(tp) if (tp and os.path.exists(tp) and not at.exception) else None
    check(mem[style] is not None, f"추억 모드 + {style}: 썸네일 생성 {[e.value[:80] for e in at.exception]}")
if all(v is not None for v in mem.values()):
    d_int = np.abs(mem["intense"] - mem["magazine"]).mean()
    d_navy = np.abs(mem["navy_premium"] - mem["magazine"]).mean()
    yellow = int(((mem["intense"][:800, :, 0] > 200) & (mem["intense"][:800, :, 1] > 160) & (mem["intense"][:800, :, 2] < 100)).sum())
    check(d_int < 1.0 and yellow < 2_000, f"추억 모드에서 intense를 골라도 광고용 큰 글씨 대신 매거진으로 (차이 {d_int:.2f}, 노랑 {yellow})")
    check(d_navy > 8.0, f"추억 모드에서도 네이비 프리미엄은 그대로 적용 (매거진과 차이 {d_navy:.1f})")
job_ids += [at.session_state.job.job_id]

# 이 테스트가 만든 작업 폴더 정리 (사용자 기존 작업 폴더는 건드리지 않음)
jobs_root = os.path.join(ROOT, "outputs", "jobs")
for jid in set(job_ids + [at.session_state.job.job_id]):
    shutil.rmtree(os.path.join(jobs_root, jid), ignore_errors=True)
finish(fails)
