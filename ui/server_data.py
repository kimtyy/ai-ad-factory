"""
ui/server_data.py - 서버(Railway)에서만 보이는 '🛠 서버 데이터 가져오기' (사이드바)

PC에서 만든 scripts/railway_seed.py 묶음(zip)을 올리면 정해진 폴더(secrets/·assets/bgm/·outputs/의 지정 파일)에만 풀어 넣음.
- 대시보드 전체가 PIN 잠금 뒤에 있음 (ui/auth.py). zip 안의 경로가 허용 목록 밖이거나 ../ 로 빠져나가면 건너뜀
- 서버 표시: 환경 변수 DATA_DIR 또는 RAILWAY_ENVIRONMENT가 있을 때만
"""
import io
import os
import zipfile
from typing import Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_PREFIXES = ("secrets/", "assets/bgm/", "outputs/radar/", "outputs/youtube/", "outputs/scheduler/")
MAX_MEMBER_MB = 200


def on_server() -> bool:
    return bool(os.getenv("DATA_DIR") or os.getenv("RAILWAY_ENVIRONMENT"))


def extract_seed(data: bytes, root: str = ROOT) -> Dict[str, List[str]]:
    """zip → 허용된 경로에만 풀기. {'saved': [...], 'skipped': [...]}"""
    saved, skipped = [], []
    base = os.path.realpath(root)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            dest = os.path.realpath(os.path.join(base, name))
            if (not name.startswith(ALLOWED_PREFIXES) or ".." in name.split("/") or not dest.startswith(base + os.sep)
                    or info.file_size > MAX_MEMBER_MB * 1e6):
                skipped.append(name)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())
            saved.append(name)
    return {"saved": saved, "skipped": skipped}


def inventory(root: str = ROOT) -> Dict[str, int]:
    def count(rel, exts=None):
        d = os.path.join(root, rel)
        if not os.path.isdir(d):
            return 0
        return sum(1 for f in os.listdir(d) if not exts or f.lower().endswith(exts))
    return {"토큰·설정": count("secrets", (".json",)), "BGM": count("assets/bgm", (".m4a", ".mp3", ".wav")),
            "레이더 DB": int(os.path.exists(os.path.join(root, "outputs", "radar", "radar.db")))}


def render_server_data_panel() -> None:
    import streamlit as st
    if not on_server():
        return
    with st.expander("🛠 서버 데이터 가져오기 (Railway)", expanded=False):
        inv = inventory()
        st.caption(" · ".join(f"{k} {v}" for k, v in inv.items()) + " — PC에서 `python scripts/railway_seed.py`로 만든 zip을 올리세요.")
        up = st.file_uploader("railway_seed.zip", type=["zip"], key="seed_zip")
        if up is not None and st.button("서버에 풀기", key="seed_apply"):
            try:
                res = extract_seed(up.getvalue())
                st.success(f"{len(res['saved'])}개 파일을 넣었어요." + (f" (허용 밖 {len(res['skipped'])}개는 건너뜀)" if res["skipped"] else ""))
            except zipfile.BadZipFile:
                st.error("zip 파일이 아니에요.")
        log = os.path.join(ROOT, "outputs", "scheduler_loop.log")
        if os.path.exists(log):
            with open(log, encoding="utf-8", errors="ignore") as f:
                tail = f.readlines()[-6:]
            st.code("".join(tail) or "(아직 기록 없음)", language=None)
