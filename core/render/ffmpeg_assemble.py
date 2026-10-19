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
from PIL import Image, ImageOps

logger = logging.getLogger("ad_factory.render.ffmpeg")

HW_ENCODERS = ("h264_qsv", "h264_nvenc", "h264_amf")
ENCODER_ARGS = {
    "h264_qsv": ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "22", "-forced_idr", "1"],
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "22"],
    "h264_amf": ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "22", "-qp_p", "24"],
    "libx264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
}
_encoder_cache: Optional[str] = None
LOW_MEM_X264 = ["-c:v", "libx264", "-preset", "superfast", "-crf", "21",
                "-x264-params", "rc-lookahead=0:bframes=0:ref=1:sync-lookahead=0:threads=2"]


def low_memory() -> bool:
    """메모리가 작은 환경(Streamlit Cloud 약 1GB 등)인지: RENDER_LOW_MEMORY=1/0 으로 강제, 기본은 컨테이너 메모리 한도 2.5GB 이하면 켬"""
    forced = os.getenv("RENDER_LOW_MEMORY", "").strip()
    if forced in ("0", "1"):
        return forced == "1"
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as f:
                raw = f.read().strip()
            if raw.isdigit() and int(raw) < 2.5 * 1024 ** 3:
                return True
        except OSError:
            continue
    return False


def encoder_args(enc: str) -> List[str]:
    """인코더별 인자. 저메모리 환경의 libx264는 lookahead·B프레임을 꺼 1080x1920에서도 수백 MB 안에 머문다 (용량은 다소 커짐)"""
    if enc == "libx264" and low_memory():
        return list(LOW_MEM_X264)
    return list(ENCODER_ARGS[enc])


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
             brand=None, disclosure: Optional[str] = None, intro_image: Optional[str] = None, bars=None, label=None,
             xfade: float = 0.35, intro_sec: float = 0.5, hook_solo_sec: float = 1.5,
             center_modes=("travel", "owner", "commerce")) -> str:
    """
    comp: AdCompositor (create_overlay_image·render_disclosure·font_path 재사용)
    manifest 항목: video_path, audio_path, caption/subtitle/voiceover, hook_title, hook_sec
    bars: (상단 타이틀, 하단 구매 안내) 쇼핑 묶음 고정 바 → 모든 자막 PNG에 함께 합성 (영상 전체 고정)
    label: 시골책방 상단 라벨 바 (영상 전체). manifest 항목의 quote_card(+quote_source)가 있으면 그 씬은 자막 대신 인용 카드,
           caption_center_y가 있으면 그 씬 자막 위치
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
        from core.mode_policy import get_mode_policy
        policy = get_mode_policy(mode)
        is_last = idx == len(manifest) - 1
        dur = a_dur + (1.0 if is_last else policy.tail_sec + xfade) + float(item.get("hold_sec") or 0)
        dur = max(policy.min_scene_sec, dur)
        caption = item.get("subtitle") or item.get("caption") or item.get("voiceover", "")
        quote = item.get("quote_card") or ""
        if quote:
            caption = ""                     # 인용 씬: 자막 대신 종이 인용 카드
        hook = item.get("hook_title", "") or ""
        from core.mode_policy import hook_split_enabled
        hook_on = hook_split_enabled()
        is_partial_echo = bool(hook and caption and (caption.startswith(hook) or hook in caption)) \
            or (not hook_on and mode in center_modes)        # 훅 분리 중단: 중앙 자막 모드는 본문 대사만 단일 자막
        if hook_on and mode in center_modes and hook.strip() and not is_partial_echo:
            hs = max(0.5, min(float(item.get("hook_sec") or hook_solo_sec), dur - 0.5))
            segs = [(0.0, hs, None, hook), (hs, dur, caption, None)]
        else:
            segs = [(0.0, dur, caption, None if is_partial_echo else hook)]
            chunks = comp.caption_chunks(caption, mode, W) if (mode in center_modes and caption) else []
            if len(chunks) > 1:
                # 긴 문장은 최대 2줄씩 앞/뒤 덩어리로 순차 표시 (음성은 그대로, 시간은 글자 수 비율, 마지막 덩어리는 씬 끝까지)
                from core.render.caption_chunks import chunk_windows
                segs = [(s0, e0, c, None) for c, (s0, e0) in zip(chunks, chunk_windows(chunks, a_dur, dur))]
        pngs = []
        for j, (s, e, cap, hk) in enumerate(segs):
            if not (cap or hk or brand or disclosure or bars or label or quote or item.get("info_line")):
                continue
            layer = Image.fromarray(comp.create_overlay_image(caption_text=cap, hook_title=hk, width=W, height=H, mode=mode,
                                                              center_y=item.get("caption_center_y")
                                                              or (BUNDLE_CAPTION_CY if bars else None))).convert("RGBA")
            if brand:
                if "wm" not in static_layers:
                    static_layers["wm"] = brand.render_watermark(W, H, font_path=comp.font_path)
                layer.alpha_composite(static_layers["wm"])
            if label:
                if "label" not in static_layers:
                    static_layers["label"] = comp.render_book_label(label, W, H)
                layer.alpha_composite(static_layers["label"])
            if item.get("info_line"):
                layer.alpha_composite(comp.render_info_line(item["info_line"], W, H))
            if quote:
                if ("quote", idx) not in static_layers:
                    static_layers[("quote", idx)] = comp.render_quote_card(quote, item.get("quote_source", ""), W, H,
                                                                           cy=item.get("quote_cy"),
                                                                           header=item.get("card_header", ""))
                layer.alpha_composite(static_layers[("quote", idx)])
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
            with Image.open(intro_image) as source:
                ImageOps.exif_transpose(source).convert("RGB").resize((W, H), Image.LANCZOS).save(ip)
            pngs.append((ip, 0.0, min(intro_sec, dur)))
        if is_last and has_card:
            # 엔딩 카드가 페이드인되기 시작하는 시점(씬 끝 xfade 전)에 마지막 씬 자막·워터마크를 끝냄: 카드는 검색 안내만 단독으로
            pngs = [(p_, s_, max(s_ + 0.1, min(e_, dur - xfade))) for p_, s_, e_ in pngs]
        extras = [p for p in (item.get("extra_videos") or []) if p and os.path.exists(p)]
        scenes.append({"video": vp, "audio": ap, "dur": dur, "pngs": pngs, "extras": extras})

    card_png, card_sec = None, 0.0
    if has_card:
        lf = last_frame(scenes[-1]["video"], scenes[-1]["dur"])
        if lf is not None:
            card_png = os.path.join(work, "endcard.png")
            Image.fromarray(brand.render_end_card(lf, font_path=comp.font_path)).convert("RGB").resize((W, H)).save(card_png)
            card_sec = float(brand.end_card.duration_sec) + xfade
    starts, total = build_timeline([s["dur"] for s in scenes], xfade, card_sec)

    # ---------------------------------------------------------------- 2. 씬 단위 스트리밍 조립 (메모리 일정)
    # 모든 씬을 한 filter_complex에 넣으면 ffmpeg가 뒤쪽 씬까지 미리 디코딩해 프레임이 쌓인다 (8씬 1080x1920에서 3GB 이상,
    # Streamlit Cloud 1GB 한도 초과). 씬을 하나씩 raw 프레임으로 풀어 파이썬에서 크로스페이드만 섞고 인코더로 흘려 보낸다:
    # 동시에 메모리에 있는 프레임은 씬 1개 + 겹침 구간(약 8프레임)뿐이다. 화면(오버레이·페이드·전환)은 이전과 같다.
    import numpy as np

    units: List[Dict[str, Any]] = []
    for i, sc in enumerate(scenes):
        is_final_clip = i == len(scenes) - 1 and not card_png
        units.append({"kind": "scene", "sc": sc, "dur": sc["dur"], "fade_out": is_final_clip})
    if card_png:
        units.append({"kind": "card", "png": card_png, "dur": card_sec, "fade_out": True})
    X = max(1, int(round(xfade * fps)))

    def unit_cmd(u: Dict[str, Any]) -> List[str]:
        d = u["dur"]
        if u["kind"] == "card":
            cmd = ["-loop", "1", "-framerate", str(fps), "-t", f"{d:.3f}", "-i", u["png"]]
            chain = f"[0:v]scale={W}:{H},setsar=1,format=yuv420p,settb=AVTB,fps={fps},fade=t=out:st={d - 0.4:.3f}:d=0.4[vo]"
            return cmd + ["-filter_complex", chain, "-map", "[vo]"]
        sc = u["sc"]
        sources = [sc["video"]] + list(sc.get("extras") or [])
        cmd = []
        for src in sources:
            cmd += ["-i", src]
        n_src = len(sources)
        # 한 문장 안의 컷 전환: 문장 길이를 컷 수로 균등 분할해 이어 붙임 (예: 후킹 3.0초 = 1.5초 + 1.5초). 컷이 하나면 그대로
        part = d / n_src
        fl = []
        for j in range(n_src):
            d_j = part if j < n_src - 1 else d - part * (n_src - 1)
            fl.append(f"[{j}:v]fps={fps},scale={W}:{H}:flags=bicubic:out_range=tv,setsar=1,format=yuv420p,"   # yuvj(풀레인지)도 tv로 정규화
                      f"tpad=stop_mode=clone:stop_duration={d_j + 1:.3f},trim=duration={d_j:.3f},setpts=PTS-STARTPTS[s{j}]")
        if n_src == 1:
            fl.append("[s0]null[b0]")
        else:
            fl.append("".join(f"[s{j}]" for j in range(n_src)) + f"concat=n={n_src}:v=1:a=0[b0]")
        cur = "b0"
        for k, (png, s_, e_) in enumerate(sc["pngs"]):
            cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{d:.3f}", "-i", png]
            nxt = f"o{k}"
            fl.append(f"[{cur}][{n_src + k}:v]overlay=0:0:enable='between(t,{s_:.3f},{e_ - 0.0005:.4f})':format=auto:eof_action=pass[{nxt}]")
            cur = nxt
        tail = f",fade=t=out:st={d - 0.4:.3f}:d=0.4" if u["fade_out"] else ""
        fl.append(f"[{cur}]format=yuv420p,settb=AVTB,fps={fps}{tail}[vo]")
        return cmd + ["-filter_complex", ";".join(fl), "-map", "[vo]"]

    frame_bytes = W * H * 3 // 2

    def scene_frames(u: Dict[str, Any], n_frames: int, tag: str):
        """씬 하나를 raw yuv420p 프레임으로 (n_frames개 고정: 모자라면 마지막 프레임 반복)"""
        err_path = os.path.join(work, f"unit_{tag}.err")
        with open(err_path, "w", encoding="utf-8") as errf:
            proc = subprocess.Popen([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-threads", "2", "-filter_threads", "1",
                                     "-filter_complex_threads", "1", *unit_cmd(u),
                                     "-frames:v", str(n_frames), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                                    stdout=subprocess.PIPE, stderr=errf)
            last = None
            try:
                for _ in range(n_frames):
                    buf = proc.stdout.read(frame_bytes)
                    if len(buf) < frame_bytes:
                        if last is None:
                            raise FFmpegAssembleError(f"씬 디코딩 실패({tag}): "
                                                      + open(err_path, encoding="utf-8", errors="ignore").read()[-300:])
                        buf = last
                    last = buf
                    yield buf
            finally:
                proc.stdout.close()
                try:
                    proc.wait(timeout=30)
                except Exception:
                    proc.kill()

    def blend(a: bytes, b: bytes, k: int) -> bytes:
        w = int(256 * (k + 1) / (X + 1))
        x = np.frombuffer(a, np.uint8).astype(np.uint16)
        y = np.frombuffer(b, np.uint8).astype(np.uint16)
        return ((x * (256 - w) + y * w) >> 8).astype(np.uint8).tobytes()

    def stream_video(sink) -> None:
        prev_tail: List[bytes] = []
        for i, u in enumerate(units):
            n = max(2, int(round(u["dur"] * fps)))
            x_in = min(X, n // 2, len(prev_tail)) if i > 0 else 0
            x_out = min(X, n // 2) if i < len(units) - 1 else 0
            gen = scene_frames(u, n, str(i))
            for k in range(x_in):
                sink.write(blend(prev_tail[k], next(gen), k))
            for _ in range(n - x_in - x_out):
                sink.write(next(gen))
            prev_tail = [next(gen) for _ in range(x_out)]
            for _ in gen:                    # 남은 프레임 소진 (프로세스 정리)
                pass

    # 오디오: 각 씬의 타임라인 슬롯 길이에 맞춰 패딩 및 트림 후 순차(concat) 합성 (씬 간 음성이 겹치지 않음)
    slot_durs = [(sc["dur"] - xfade) if (i < len(scenes) - 1 or card_png) else sc["dur"] for i, sc in enumerate(scenes)]
    a_inputs: List[str] = []
    a_filters: List[str] = []
    alabels = []
    for i, sc in enumerate(scenes):
        a_inputs += ["-i", sc["audio"]]
        slot_d = max(0.1, slot_durs[i])
        a_filters.append(f"[{i}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                         f"apad=whole_dur={slot_d:.3f},atrim=duration={slot_d:.3f}[aslot{i}]")
        alabels.append(f"[aslot{i}]")
    if card_png:
        a_filters.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={card_sec:.3f}[acard]")
        alabels.append("[acard]")
    a_filters.append(f"{''.join(alabels)}concat=n={len(alabels)}:v=0:a=1[aout]")
    audio_wav = os.path.join(work, "audio.wav")
    ares = subprocess.run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *a_inputs, "-filter_complex", ";".join(a_filters),
                           "-map", "[aout]", "-c:a", "pcm_s16le", audio_wav], capture_output=True, text=True,
                          encoding="utf-8", errors="ignore")
    if ares.returncode != 0 or not os.path.exists(audio_wav):
        raise FFmpegAssembleError("오디오 조립 실패: " + (ares.stderr or "")[-400:])

    # ---------------------------------------------------------------- 3. 인코딩 (하드웨어 → 실패 시 libx264)
    encoder = pick_encoder()
    tried = []
    for enc in dict.fromkeys([encoder, "libx264"]):
        cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-threads", "2",
               "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-i", audio_wav,
               "-map", "0:v", "-map", "1:a", *encoder_args(enc), "-force_key_frames", "0",   # 시각 0 = IDR (커버 프레임)
               "-pix_fmt", "yuv420p", "-r", str(fps),
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", out_path]
        err_path = os.path.join(work, f"enc_{enc}.err")
        with open(err_path, "w", encoding="utf-8") as errf:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errf)
            try:
                stream_video(proc.stdin)
            except (BrokenPipeError, OSError):
                pass                          # 인코더가 먼저 종료(오류): 아래에서 반환 코드로 판단
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            rc = proc.wait()
        err_txt = open(err_path, encoding="utf-8", errors="ignore").read()
        if rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            logger.info(f"ffmpeg 조립 완료 ({enc}, {len(scenes)}씬{' + 엔딩 카드' if card_png else ''}, {total:.2f}초 영상): "
                        f"{time.time() - t0:.1f}초 → {out_path}")
            shutil.rmtree(work, ignore_errors=True)
            return out_path
        tried.append(f"{enc}: {err_txt[-400:]}")
        logger.warning(f"ffmpeg 조립 실패 ({enc}) → 다음 인코더 시도: {err_txt[-300:]}")
    raise FFmpegAssembleError("ffmpeg 조립 실패: " + " | ".join(tried))
