"""
core/render/ffmpeg_assemble.py - 최종 타임라인 조립을 ffmpeg filter_complex 한 번으로 (MoviePy 프레임 합성 대체)

왜: MoviePy는 자막·디졸브·페이드를 파이썬(numpy)으로 1080x1920 프레임마다 알파 합성함.
    프로파일(2씬 7초 영상): 182초 중 96%가 MoviePy blit/dstack/fadeout, ffmpeg 인코딩 자체는 몇 초.
어떻게:
- 자막·후킹·워터마크·제휴 고지·썸네일·엔딩 카드 그림은 기존 Pillow 코드 그대로 PNG로 한 번만 그림 (디자인 동일)
- 씬 영상: fps·크기 맞춤 → tpad(마지막 프레임 유지)로 목표 길이까지 늘리고 trim
- PNG 오버레이: overlay=enable='between(t,시작,끝)' (씬 1 후킹 → 서브 자막 시간차, 첫 0.3초 썸네일 포함)
- 씬 전환: xfade=fade (XFADE_SEC), 마지막 장면(또는 엔딩 카드) 끝 fade out 0.4초
- 나레이션: 씬 시작 시각만큼 adelay → amix(normalize=0, 음량 그대로). 겹침 구간은 앞 씬의 무음 꼬리라 섞이지 않음
- 인코딩: Intel Quick Sync(h264_qsv)·NVIDIA(h264_nvenc)·AMD(h264_amf) 하드웨어 인코더를 한 번 시험해 되는 것 사용,
  없으면 libx264 veryfast. RENDER_ENCODER=libx264|h264_qsv|h264_nvenc|h264_amf|auto 로 강제 가능
타임라인 규칙(씬 길이 = 음성 + 꼬리 여유, 디졸브 겹침, 전체 길이)은 MoviePy 경로와 같음
"""
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

import imageio_ffmpeg
from PIL import Image

logger = logging.getLogger("ad_factory.render.ffmpeg")

HW_ENCODERS = ("h264_qsv", "h264_nvenc", "h264_amf")
ENCODER_ARGS = {
    "h264_qsv": ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "22", "-forced_idr", "1"],
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "22"],
    "h264_amf": ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "22", "-qp_p", "24"],
    "libx264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
}
_encoder_cache: Optional[str] = None


class FFmpegAssembleError(RuntimeError):
    pass


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe(path: str) -> Dict[str, Any]:
    """{duration, width, height} (ffmpeg -i 출력에서)"""
    res = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, text=True,
                         encoding="utf-8", errors="ignore", timeout=30)
    err = res.stderr or ""
    out: Dict[str, Any] = {"duration": None, "width": None, "height": None}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        out["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    if m:
        out["width"], out["height"] = int(m.group(1)), int(m.group(2))
    return out


def pick_encoder() -> str:
    """하드웨어 인코더를 한 번만 시험해 보고 결과를 기억 (드라이버가 없으면 목록에 있어도 실패함)"""
    global _encoder_cache
    forced = os.getenv("RENDER_ENCODER", "auto").strip().lower()
    if forced in ENCODER_ARGS:
        return forced
    if _encoder_cache:
        return _encoder_cache
    for enc in HW_ENCODERS:
        try:
            res = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                  "testsrc2=size=1080x1920:rate=24", "-t", "0.5", *ENCODER_ARGS[enc], "-f", "null", "-"],
                                 capture_output=True, timeout=60)
            if res.returncode == 0:
                _encoder_cache = enc
                logger.info(f"하드웨어 인코더 사용: {enc}")
                return enc
        except Exception:
            continue
    _encoder_cache = "libx264"
    logger.info("하드웨어 인코더 없음 → libx264 veryfast")
    return _encoder_cache


def last_frame(video_path: str, at: float) -> Optional["Any"]:
    """엔딩 카드 배경용: at초 지점(영상보다 길면 마지막) 프레임 → numpy RGB"""
    import numpy as np
    tmp = video_path + ".lastframe.png"
    dur = probe(video_path).get("duration") or at
    t = max(0.0, min(at, dur - 0.05))
    subprocess.run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", video_path,
                    "-frames:v", "1", tmp], capture_output=True, timeout=60)
    if not os.path.exists(tmp):
        return None
    try:
        return np.array(Image.open(tmp).convert("RGB"))
    finally:
        os.remove(tmp)


def build_timeline(scene_durations: List[float], xfade: float, card_sec: float = 0.0) -> Tuple[List[float], float]:
    """씬(과 엔딩 카드) 시작 시각과 전체 길이: 다음 클립은 앞 클립 끝 xfade초 전에 겹쳐 시작"""
    durs = list(scene_durations) + ([card_sec] if card_sec > 0 else [])
    starts, t = [], 0.0
    for i, d in enumerate(durs):
        starts.append(t)
        t += d - (xfade if i < len(durs) - 1 else 0.0)
    return starts, t


def assemble(comp, manifest: List[Dict[str, Any]], out_path: str, fps: int = 24, mode: str = "commerce",
             brand=None, disclosure: Optional[str] = None, intro_image: Optional[str] = None, bars=None,
             xfade: float = 0.35, intro_sec: float = 0.3, hook_solo_sec: float = 1.5,
             center_modes=("travel", "owner", "commerce")) -> str:
    """
    comp: AdCompositor (create_overlay_image·render_disclosure·font_path 재사용)
    manifest 항목: video_path, audio_path, caption/subtitle/voiceover, hook_title, hook_sec
    bars: (상단 타이틀, 하단 구매 안내) 쇼핑 묶음 고정 바 → 모든 자막 PNG에 함께 합성 (영상 전체 고정)
    """
    from core.compositor import BUNDLE_CAPTION_CY     # 순환 import 방지 (compositor가 이 모듈을 부름)
    if not manifest:
        raise FFmpegAssembleError("합성할 씬이 없습니다.")
    t0 = time.time()
    work = os.path.join(os.path.dirname(os.path.abspath(out_path)), "_ffmpeg_tmp")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)

    first = probe(manifest[0]["video_path"])
    W, H = (first["width"] or 1080), (first["height"] or 1920)
    W, H = W - W % 2, H - H % 2
    has_card = bool(brand and getattr(brand, "end_card", None))

    # ---------------------------------------------------------------- 1. 씬 길이·오버레이 PNG (Pillow, 기존 디자인 그대로)
    scenes = []
    static_layers = {}
    for idx, item in enumerate(manifest):
        vp, ap = item.get("video_path"), item.get("audio_path")
        if not vp or not os.path.exists(vp):
            raise FileNotFoundError(f"씬 {idx + 1} 비디오 파일 누락: {vp}")
        if not ap or not os.path.exists(ap):
            raise FileNotFoundError(f"씬 {idx + 1} 오디오 파일 누락: {ap}")
        a_dur = probe(ap)["duration"]
        if not a_dur:
            raise FFmpegAssembleError(f"씬 {idx + 1} 오디오 길이를 읽지 못함: {ap}")
        is_last = idx == len(manifest) - 1
        dur = a_dur + (1.0 if is_last else 0.2 + xfade)
        caption = item.get("subtitle") or item.get("caption") or item.get("voiceover", "")
        hook = item.get("hook_title", "") or ""
        if mode in center_modes and hook.strip():
            hs = max(0.5, min(float(item.get("hook_sec") or hook_solo_sec), dur - 0.5))
            segs = [(0.0, hs, None, hook), (hs, dur, caption, None)]
        else:
            segs = [(0.0, dur, caption, hook)]
        pngs = []
        for j, (s, e, cap, hk) in enumerate(segs):
            if not (cap or hk or brand or disclosure or bars):
                continue
            layer = Image.fromarray(comp.create_overlay_image(caption_text=cap, hook_title=hk, width=W, height=H, mode=mode,
                                                              center_y=BUNDLE_CAPTION_CY if bars else None)).convert("RGBA")
            if brand:
                if "wm" not in static_layers:
                    static_layers["wm"] = brand.render_watermark(W, H, font_path=comp.font_path)
                layer.alpha_composite(static_layers["wm"])
            if bars:
                if "bars" not in static_layers:
                    static_layers["bars"] = comp.render_bundle_bars(bars[0], bars[1], W, H)
                layer.alpha_composite(static_layers["bars"])
            if disclosure:
                if "disc" not in static_layers:
                    static_layers["disc"] = comp.render_disclosure(disclosure, W, H)
                layer.alpha_composite(static_layers["disc"])
            png = os.path.join(work, f"s{idx}_{j}.png")
            layer.save(png)
            pngs.append((png, s, e))
        if idx == 0 and intro_image and os.path.exists(intro_image):
            ip = os.path.join(work, "intro.png")
            Image.open(intro_image).convert("RGB").resize((W, H), Image.LANCZOS).save(ip)
            pngs.append((ip, 0.0, min(intro_sec, dur)))
        scenes.append({"video": vp, "audio": ap, "dur": dur, "pngs": pngs})

    card_png, card_sec = None, 0.0
    if has_card:
        lf = last_frame(scenes[-1]["video"], scenes[-1]["dur"])
        if lf is not None:
            card_png = os.path.join(work, "endcard.png")
            Image.fromarray(brand.render_end_card(lf, font_path=comp.font_path)).convert("RGB").resize((W, H)).save(card_png)
            card_sec = float(brand.end_card.duration_sec) + xfade
    starts, total = build_timeline([s["dur"] for s in scenes], xfade, card_sec)

    # ---------------------------------------------------------------- 2. filter_complex
    inputs: List[str] = []
    filters: List[str] = []
    n_in = 0

    def add_input(args: List[str]) -> int:
        nonlocal n_in
        inputs.extend(args)
        n_in += 1
        return n_in - 1

    vlabels = []
    for i, sc in enumerate(scenes):
        vi = add_input(["-i", sc["video"]])
        d = sc["dur"]
        filters.append(f"[{vi}:v]fps={fps},scale={W}:{H}:flags=bicubic,setsar=1,format=yuv420p,"
                       f"tpad=stop_mode=clone:stop_duration={d + 1:.3f},trim=duration={d:.3f},setpts=PTS-STARTPTS[v{i}b]")
        cur = f"v{i}b"
        for k, (png, s, e) in enumerate(sc["pngs"]):
            pi = add_input(["-loop", "1", "-framerate", str(fps), "-t", f"{d:.3f}", "-i", png])
            nxt = f"v{i}o{k}"
            filters.append(f"[{cur}][{pi}:v]overlay=0:0:enable='between(t,{s:.3f},{e - 0.0005:.4f})':format=auto:eof_action=pass[{nxt}]")
            cur = nxt
        is_final_clip = i == len(scenes) - 1 and not card_png
        tail = f",fade=t=out:st={d - 0.4:.3f}:d=0.4" if is_final_clip else ""
        filters.append(f"[{cur}]format=yuv420p,settb=AVTB,fps={fps}{tail}[v{i}]")   # xfade는 고정 프레임레이트 필요 (settb가 지움)
        vlabels.append((f"v{i}", d))
    if card_png:
        ci = add_input(["-loop", "1", "-framerate", str(fps), "-t", f"{card_sec:.3f}", "-i", card_png])
        filters.append(f"[{ci}:v]scale={W}:{H},setsar=1,format=yuv420p,settb=AVTB,fps={fps},fade=t=out:st={card_sec - 0.4:.3f}:d=0.4[vcard]")
        vlabels.append(("vcard", card_sec))

    acc, acc_dur = vlabels[0]
    for j, (lab, d) in enumerate(vlabels[1:], 1):
        out = f"x{j}"
        filters.append(f"[{acc}][{lab}]xfade=transition=fade:duration={xfade:.3f}:offset={acc_dur - xfade:.3f}[{out}]")
        acc, acc_dur = out, acc_dur + d - xfade
    filters.append(f"[{acc}]trim=duration={total:.3f},setpts=PTS-STARTPTS[vout]")

    alabels = []
    for i, sc in enumerate(scenes):
        ai = add_input(["-i", sc["audio"]])
        ms = int(round(starts[i] * 1000))
        filters.append(f"[{ai}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,adelay={ms}|{ms}[a{i}]")
        alabels.append(f"[a{i}]")
    filters.append(f"{''.join(alabels)}amix=inputs={len(alabels)}:normalize=0:dropout_transition=0,"
                   f"apad=whole_dur={total:.3f},atrim=duration={total:.3f}[aout]")

    script = os.path.join(work, "graph.txt")
    with open(script, "w", encoding="utf-8") as f:
        f.write(";\n".join(filters))

    # ---------------------------------------------------------------- 3. 인코딩 (하드웨어 → 실패 시 libx264)
    encoder = pick_encoder()
    tried = []
    for enc in dict.fromkeys([encoder, "libx264"]):
        cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *inputs, "-/filter_complex", script,
               "-map", "[vout]", "-map", "[aout]", *ENCODER_ARGS[enc], "-force_key_frames", "0",   # 시각 0 = IDR (커버 프레임)
               "-pix_fmt", "yuv420p", "-r", str(fps),
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", out_path]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        if res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            logger.info(f"ffmpeg 조립 완료 ({enc}, {len(scenes)}씬{' + 엔딩 카드' if card_png else ''}, {total:.2f}초 영상): "
                        f"{time.time() - t0:.1f}초 → {out_path}")
            shutil.rmtree(work, ignore_errors=True)
            return out_path
        with open(os.path.join(work, f"fail_{enc}.log"), "w", encoding="utf-8") as f:     # 실패 시 원인 확인용 (작업 폴더에 남김)
            f.write(subprocess.list2cmdline(cmd) + "\n\n" + (res.stderr or ""))
        tried.append(f"{enc}: {(res.stderr or '')[-400:]}")
        logger.warning(f"ffmpeg 조립 실패 ({enc}) → 다음 인코더 시도: {(res.stderr or '')[-300:]}")
    raise FFmpegAssembleError("ffmpeg 조립 실패: " + " | ".join(tried))
