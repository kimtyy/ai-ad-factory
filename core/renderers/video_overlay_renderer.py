"""
core/renderers/video_overlay_renderer.py - 촬영 영상 원본 직가공 쇼츠 렌더러 (Video Overlay Pipeline)

사용자가 직접 촬영한 단일 영상(MP4/MOV)의 움직임을 100% 보존하면서
'9:16 리사이징/크롭 + 자막/후킹/뱃지 오버레이 + TTS 나레이션 및 원본 음성 더킹 믹싱'을
FFmpeg filter_complex 단 한 번으로 고속 렌더링합니다.
"""
import os
import re
import shutil
import logging
import subprocess
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

from core.media.normalize import probe_media, _hdr_to_sdr_filter, _ffmpeg
from core.render.ffmpeg_assemble import pick_encoder, ENCODER_ARGS, ffmpeg_exe, FFmpegAssembleError
from core.compositor import AdCompositor

logger = logging.getLogger("ad_factory.render.video_overlay")


def render_top_badge(badge_text: str, width: int = 1080, height: int = 1920, font_path: Optional[str] = None) -> Image.Image:
    """상단 1컨셉 / 매장명 뱃지 PNG 생성 (반투명 라운드 박스 + 텍스트)"""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if not badge_text or not badge_text.strip():
        return img

    text = badge_text.strip()   # 이모지는 한글 폰트에 글리프가 없어 깨지므로 쓰지 않음
    draw = ImageDraw.Draw(img)

    font_size = int(width * 0.038)  # 약 40px
    font = None
    if font_path and os.path.exists(font_path):
        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            pass
    if font is None:
        try:
            font = ImageFont.truetype("malgun.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    t_w = bbox[2] - bbox[0]
    t_h = bbox[3] - bbox[1]

    pad_x = 24
    pad_y = 12
    box_w = t_w + pad_x * 2
    box_h = t_h + pad_y * 2

    # 상단 9% (세이프존)
    center_y = int(height * 0.09)
    x0 = (width - box_w) // 2
    y0 = center_y - box_h // 2
    x1 = x0 + box_w
    y1 = y0 + box_h

    # 둥근 모서리 배경 박스 (다크 반투명)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=(18, 18, 24, 210), outline=(255, 215, 0, 180), width=2)
    # 텍스트
    tx = x0 + pad_x
    ty = y0 + pad_y - bbox[1]
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))

    return img


def _audio_duration(path: Optional[str]) -> float:
    """TTS 파일 길이(초). ffmpeg -i 의 Duration 줄을 읽음 (영상 스트림이 없는 mp3도 가능). 실패 시 0"""
    if not path or not os.path.exists(path):
        return 0.0
    try:
        r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr or "")
        return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    except Exception:
        return 0.0


def retime_scenes(storyboard: List[Dict[str, Any]], total_sec: float, gap: float = 0.15) -> List[Dict[str, Any]]:
    """
    계획된 씬 구간보다 TTS가 길면 다음 씬 나레이션과 겹쳐 두 목소리가 동시에 나온다.
    → 씬을 순서대로 훑으며 시작 시각을 '이전 나레이션 끝 + gap' 이후로 미루고, 표시 구간도 나레이션 길이만큼 늘린다.
    영상 길이를 넘는 씬은 버린다. 반환: [{idx, start, end, tts_sec}, ...]
    """
    out: List[Dict[str, Any]] = []
    cursor = 0.0
    for idx, sc in enumerate(storyboard):
        s = float(sc.get("start_sec", 0.0))
        e = float(sc.get("end_sec", s + float(sc.get("duration", 3.0))))
        if e <= s:
            e = s + 3.0
        tts = _audio_duration(sc.get("audio_path"))
        start = max(s, cursor)
        end = max(e, start + tts + gap) if tts else max(e, start + 1.0)
        if start >= total_sec - 0.3:
            logger.warning(f"[Video Overlay] 씬 {idx + 1}은 영상 길이({total_sec:.1f}s)를 넘어 제외됩니다")
            continue
        if start + tts > total_sec + 0.05:
            logger.warning(f"[Video Overlay] 씬 {idx + 1} 나레이션({tts:.1f}s)이 영상 끝에서 잘립니다 - 대본을 줄이세요")
        end = min(end, total_sec)
        out.append({"idx": idx, "start": start, "end": end, "tts_sec": tts})
        cursor = start + tts + gap if tts else end
    return out


def render_video_overlay(
    video_path: str,
    storyboard: List[Dict[str, Any]],
    output_path: str,
    audio_mode: str = "keep_ducking",  # "keep_ducking" (배경음 20% 유지) | "mute" (음소거)
    fit_mode: str = "crop",            # "crop" (9:16 중앙 포커스 크롭) | "blur" (상하 블러 패딩)
    badge_text: str = "",
    width: int = 1080,
    height: int = 1920,
    fps: int = 24,
    duck_volume: float = 0.20,
    window: Optional[Tuple[float, float]] = None,
    cta_sec: float = 3.0,
    original_fade: float = 0.1,
    music_suspected: bool = False,
    caption_center_y: Optional[float] = None,
    video_hold: float = 0.0,
    intro_image: Optional[str] = None,
    intro_sec: float = 0.5,
) -> str:
    """
    단일 비디오 파일 위에 대본 타임라인에 맞춰 자막/후킹/뱃지 및 TTS 오디오를 직가공 렌더링합니다.

    Args:
        video_path: 사용자 촬영 원본 비디오 경로
        storyboard: 타임라인 씬 목록 ([{start_sec, end_sec, caption, hook_title, audio_path}, ...])
        output_path: 최종 결과물 비디오 저장 경로
        audio_mode: 'keep_ducking' (원본을 duck_volume 비율로 낮춰 유지) | 'mute' (음소거) |
                    'original' (원본 100% 유지, 롱폼 트랙 A) | 'denoise' (afftdn 노이즈 제거 후 100%, 롱폼 트랙 C)
        fit_mode: 'crop' (중앙 크롭) 또는 'blur' (블러 패딩)
        badge_text: 상단 1컨셉 또는 매장명 뱃지 문구
        width, height: 해상도 (기본 1080x1920)
        fps: 프레임 레이트 (기본 24)
        duck_volume: 원본 음성 배경음 비율 (기본 0.20 = 20%)
        window: (start_sec, end_sec) 롱폼 원본의 이 구간만 사용 (None이면 영상 전체, 기존 동작과 동일).
                입력 측 -ss/-t 로 바로 잘라 필터 그래프에서 단 1회 인코딩한다 (중간 컷 파일·-c copy 없음, 정규화도 이 구간에만 적용).
                이때 storyboard의 start_sec/end_sec는 '창 시작점 기준 상대 시간'이고, 출력 길이 = 창 길이 + cta_sec.
        cta_sec: window 모드에서 창 뒤에 붙는 CTA 구간 길이. 원본이 이어지면 계속 재생, 끝나면 마지막 프레임 정지.
                 원본 오디오는 창 끝에서 페이드아웃되고 CTA 구간에는 storyboard의 CTA 씬 TTS만 흐른다.
        original_fade: window 모드 원본 오디오 페이드 인/아웃 길이(초)
        music_suspected: True면 keep_ducking에서도 원본을 0으로 (배경음악 저작권·소음 방지, 롱폼 트랙 B)
        intro_image, intro_sec: 영상 맨 앞 intro_sec초를 이 이미지(썸네일)로 덮는다 (화면만: 나레이션·자막 타임라인은 그대로).
                    쇼츠 피드는 영상 내부 0초 프레임을 대표 화면으로 쓰기 때문
        video_hold: window 모드에서 영상만 이 시간(초) 뒤부터 읽고, 그 앞 구간은 첫 프레임을 붙잡아 둔다. 오디오는 window 시작부터 그대로.
                    (발화가 이미 시작됐는데 장면 컷이 조금 뒤에 오는 경우, 이전 장면 잔상 없이 나레이션을 유지하는 인점 가드)
        caption_center_y: 자막 중심의 화면 높이 비율. None이면 가로 영상의 blur(원본 비율 유지) 레이아웃에서는 자동으로
                          아래쪽 블러 띠(원본 화면을 가리지 않는 위치), 그 외에는 정중앙
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"원본 영상 파일을 찾을 수 없습니다: {video_path}")

    info = probe_media(video_path)
    src_start = 0.0
    win_len = info.duration
    avail_cta = 0.0
    if window is not None:
        w0, w1 = float(window[0]), float(window[1])
        if w1 <= w0 or w0 < 0:
            raise ValueError(f"window가 올바르지 않습니다: {window}")
        w0 = max(0.0, min(w0, max(0.0, info.duration - 0.5)))
        w1 = min(w1, info.duration)
        if w1 - w0 < 0.5:
            raise ValueError(f"window가 원본({info.duration:.1f}s) 범위를 벗어났습니다: {window}")
        src_start, win_len = w0, w1 - w0
        avail_cta = max(0.0, min(float(cta_sec), info.duration - w1))   # 원본이 이어지는 CTA 구간 길이 (모자라면 마지막 프레임 정지)
    total_sec = (win_len + float(cta_sec)) if window is not None else info.duration
    logger.info(
        f"[Video Overlay] 원본: {os.path.basename(video_path)} ({info.width}x{info.height}, "
        f"{info.duration:.2f}s, rot={info.rotation}, hdr={info.is_hdr}, audio={info.has_audio})"
    )

    work_dir = os.path.join(os.path.dirname(os.path.abspath(output_path)), "_overlay_tmp")
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(work_dir, exist_ok=True)

    is_landscape_blur = (fit_mode == "blur") and not (info.is_portrait or (info.width <= info.height))
    if caption_center_y is None and is_landscape_blur:
        fg_h = width * info.height / max(1, info.width)         # 원본이 폭에 맞춰 들어갔을 때의 높이
        band_top = (height + fg_h) / 2.0
        caption_center_y = min(0.80, (band_top + 0.42 * (height - band_top)) / height)   # 아래 띠 안, 쇼츠 하단 UI 위
    center_kw = {"center_y": float(caption_center_y)} if caption_center_y else {}

    comp = AdCompositor(output_dir=work_dir)
    badge_img = render_top_badge(badge_text, width=width, height=height, font_path=comp.font_path)

    # 1. 씬별 자막/오버레이 PNG 사전 렌더링
    overlay_entries: List[Tuple[str, float, float]] = []  # (png_path, start_sec, end_sec)

    timeline = retime_scenes(storyboard, total_sec)
    start_by_idx = {t["idx"]: t["start"] for t in timeline}

    for t in timeline:
        idx, s, e = t["idx"], t["start"], t["end"]
        sc = storyboard[idx]

        caption = (sc.get("caption") or sc.get("subtitle") or sc.get("voiceover") or "").strip()
        hook = (sc.get("hook_title") or "").strip()

        # Scene 1에서 hook과 caption의 표시 시간차(후킹 단독 1.5초 등) 처리
        # 단, hook이 caption의 불완전한 앞부분이거나(말토막) 중복되는 경우,
        # 훅 단독으로 띄우면 00:00 오프닝에서 자막이 덜 완성된 채 잘려 보이므로 전 구간 온전한 자막으로 노출
        from core.mode_policy import hook_split_enabled
        is_partial_echo = bool(hook and caption and (caption.startswith(hook) or hook in caption)) or not hook_split_enabled()
        if idx == 0 and hook and not is_partial_echo:
            hook_sec = min(2.0, max(1.0, (e - s) * 0.45))
            # 1단계: 후킹 타이틀 단독
            layer1 = Image.fromarray(
                comp.create_overlay_image(caption_text=None, hook_title=hook, width=width, height=height, mode="owner", **center_kw)
            ).convert("RGBA")
            if badge_text:
                layer1.alpha_composite(badge_img)
            png1 = os.path.join(work_dir, f"overlay_{idx}_hook.png")
            layer1.save(png1)
            overlay_entries.append((png1, s, s + hook_sec))

            # 2단계: 본문 자막 (후킹 바는 상단 유지)
            layer2 = Image.fromarray(
                comp.create_overlay_image(caption_text=caption, hook_title=hook, width=width, height=height, mode="owner", **center_kw)
            ).convert("RGBA")
            if badge_text:
                layer2.alpha_composite(badge_img)
            png2 = os.path.join(work_dir, f"overlay_{idx}_body.png")
            layer2.save(png2)
            overlay_entries.append((png2, s + hook_sec, e))
        else:
            layer = Image.fromarray(
                comp.create_overlay_image(caption_text=caption, hook_title=None if is_partial_echo else (hook if idx == 0 else None),
                                          width=width, height=height, mode="owner", **center_kw)
            ).convert("RGBA")
            if badge_text:
                layer.alpha_composite(badge_img)
            png_path = os.path.join(work_dir, f"overlay_{idx}.png")
            layer.save(png_path)
            overlay_entries.append((png_path, s, e))

    # 첫 자막이 늦게 시작하면(발화가 조금 뒤에 시작) 그 앞 구간에도 상단 뱃지는 계속 보이게
    if badge_text and overlay_entries:
        first_start = min(e[1] for e in overlay_entries)
        if first_start > 0.02:
            lead_png = os.path.join(work_dir, "overlay_lead_badge.png")
            badge_img.save(lead_png)
            overlay_entries.insert(0, (lead_png, 0.0, first_start))

    # 0초 커버: 썸네일 이미지를 전체 화면으로 맨 위에 (본문 자막 레이어는 그 아래에서 계속 진행)
    if intro_image and os.path.exists(intro_image) and intro_sec > 0:
        with Image.open(intro_image) as _src:
            cover_png = os.path.join(work_dir, "overlay_intro_cover.png")
            _src.convert("RGB").resize((width, height), Image.LANCZOS).convert("RGBA").save(cover_png)
        overlay_entries.append((cover_png, 0.0, min(float(intro_sec), total_sec)))

    # 2. 비디오 기본 변환 필터 그래프 (해상도, 크롭/블러, HDR to SDR)
    hdr_filter = (_hdr_to_sdr_filter() + ",") if info.is_hdr else ""
    
    # 9:16 세로형 여부 판단
    is_portrait = info.is_portrait or (info.width <= info.height)
    
    if is_portrait:
        # 이미 세로 영상인 경우: 1080x1920에 맞춰 꽉 채우기
        v_base_filter = (
            f"[0:v]{hdr_filter}scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p[vbase]"
        )
    else:
        # 가로 영상인 경우
        if fit_mode == "blur":
            v_base_filter = (
                f"[0:v]{hdr_filter}split[v_bg][v_fg];"
                f"[v_bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                f"gblur=sigma=40,eq=brightness=-0.08[bg_blur];"
                f"[v_fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg_scaled];"
                f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps},format=yuv420p[vbase]"
            )
        else:
            # 기본값: 9:16 중앙 포커스 크롭
            v_base_filter = (
                f"[0:v]{hdr_filter}scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p[vbase]"
            )

    filter_complex_parts = [v_base_filter]
    hold = 0.0
    audio_in = 0
    if window is not None:
        hold = max(0.0, min(float(video_hold or 0.0), win_len - 0.5))
        input_args = ["-ss", f"{src_start + hold:.3f}", "-t", f"{win_len - hold + avail_cta:.3f}", "-i", video_path]
        if hold > 0:      # 오디오는 원래 시작점부터 읽는 별도 입력
            input_args += ["-ss", f"{src_start:.3f}", "-t", f"{win_len + avail_cta:.3f}", "-i", video_path]
            audio_in = 1
    else:
        input_args = ["-i", video_path]
    num_inputs = 2 if audio_in else 1

    def add_input(args: List[str]) -> int:
        nonlocal num_inputs
        idx = num_inputs
        input_args.extend(args)
        num_inputs += 1
        return idx

    current_v_label = "vbase"
    if window is not None and cta_sec > 0:
        # 원본이 CTA 구간까지 이어지지 않으면 마지막 프레임을 복제해 정지 (이어지면 아무것도 덧붙지 않음)
        lead = f"start_mode=clone:start_duration={hold:.3f}:" if hold > 0 else ""
        filter_complex_parts.append(f"[vbase]tpad={lead}stop_mode=clone:stop_duration={float(cta_sec):.3f}[vpad]")
        current_v_label = "vpad"

    # 자막 PNG 레이어 오버레이
    for i, (png_path, start_t, end_t) in enumerate(overlay_entries):
        in_idx = add_input(["-loop", "1", "-t", f"{total_sec:.3f}", "-i", png_path])
        next_v_label = f"v_ov_{i}"
        filter_complex_parts.append(
            f"[{current_v_label}][{in_idx}:v]overlay=0:0:enable='between(t,{start_t:.3f},{end_t:.3f})'[{next_v_label}]"
        )
        current_v_label = next_v_label

    # 3. 오디오 트랙 조합 (TTS 나레이션 + 원본 사운드 더킹/음소거)
    tts_audio_inputs = []
    tts_audio_filters = []
    active_tts_count = 0

    for idx, sc in enumerate(storyboard):
        ap = sc.get("audio_path")
        if idx in start_by_idx and ap and os.path.exists(ap):
            in_idx = add_input(["-i", ap])
            start_ms = max(0, int(start_by_idx[idx] * 1000))
            tts_audio_filters.append(f"[{in_idx}:a]adelay={start_ms}|{start_ms}[adly_{idx}]")
            tts_audio_inputs.append(f"[adly_{idx}]")
            active_tts_count += 1

    audio_map_label = None

    # 원본 사운드 처리: 모드 → 볼륨/노이즈 제거. 음악 의심이면 ducking에서도 원본 0
    orig_volume: Optional[float]
    if not info.has_audio or audio_mode == "mute" or (music_suspected and audio_mode == "keep_ducking"):
        orig_volume = None
    elif audio_mode in ("original", "denoise"):
        orig_volume = 1.0
    else:
        orig_volume = duck_volume

    def orig_chain() -> str:
        steps: List[str] = []
        if window is not None:
            steps += [f"atrim=0:{win_len:.3f}", "asetpts=PTS-STARTPTS"]
        if audio_mode == "denoise":
            steps.append("afftdn")
        if orig_volume is not None and abs(orig_volume - 1.0) > 1e-6:
            steps.append(f"volume={orig_volume:.3f}")
        if window is not None:
            f = max(0.0, min(float(original_fade), win_len / 4.0))
            if f > 0:
                steps += [f"afade=t=in:st=0:d={f:.3f}", f"afade=t=out:st={max(0.0, win_len - f):.3f}:d={f:.3f}"]
            steps.append(f"apad=whole_dur={total_sec:.3f}")
        return ",".join(steps) if steps else "anull"

    if active_tts_count > 0:
        filter_complex_parts.extend(tts_audio_filters)
        if active_tts_count == 1:
            filter_complex_parts.append(f"{tts_audio_inputs[0]}anull[tts_mix]")
        else:
            filter_complex_parts.append(
                f"{''.join(tts_audio_inputs)}amix=inputs={active_tts_count}:normalize=0:dropout_transition=0[tts_mix]"
            )

        if orig_volume is not None:
            filter_complex_parts.append(f"[{audio_in}:a]{orig_chain()}[orig_x]")
            filter_complex_parts.append(
                f"[orig_x][tts_mix]amix=inputs=2:normalize=0:dropout_transition=0,alimiter=limit=0.95[aout]")
            audio_map_label = "[aout]"
        else:
            audio_map_label = "[tts_mix]"
    else:
        # TTS가 없을 때 (기존 직가공 호출은 원본 그대로 통과 — window 모드만 모드별 볼륨 적용)
        if window is None and audio_mode == "keep_ducking" and orig_volume is not None:
            orig_volume = 1.0
        if orig_volume is not None:
            filter_complex_parts.append(f"[{audio_in}:a]{orig_chain()}[aout]")
            audio_map_label = "[aout]"
        else:
            audio_map_label = None

    # 4. FFmpeg 명령어 조합 및 인코딩 실행
    encoder = pick_encoder()
    enc_opts = ENCODER_ARGS.get(encoder, ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])

    filter_script = ";\n".join(filter_complex_parts)
    script_file = os.path.join(work_dir, "filters.txt")
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(filter_script)

    cmd = [
        ffmpeg_exe(),
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        *input_args,
        "-filter_complex_script", script_file,
        "-map", f"[{current_v_label}]",
    ]

    if audio_map_label:
        cmd.extend(["-map", audio_map_label, "-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.append("-an")

    cmd.extend([
        *enc_opts,
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        "-t", f"{total_sec:.3f}",
        "-movflags", "+faststart",
        output_path,
    ])

    logger.info(f"[Video Overlay 렌더 시작] 인코더: {encoder}, 씬 수: {len(storyboard)}, 길이: {total_sec:.2f}초"
                + (f", 창 {src_start:.1f}~{src_start + win_len:.1f}s + CTA {cta_sec:.1f}s, 오디오 {audio_mode}" if window is not None else ""))
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")

    if res.returncode != 0:
        err_msg = (res.stderr or "")[-800:]
        logger.error(f"[Video Overlay 렌더 실패] {err_msg}")
        # 하드웨어 인코더 실패 시 libx264로 fallback 재시도
        if encoder != "libx264":
            logger.warning("[Video Overlay] libx264 veryfast로 재시도합니다.")
            cmd_fb = [
                ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
                *input_args,
                "-filter_complex_script", script_file,
                "-map", f"[{current_v_label}]",
            ]
            if audio_map_label:
                cmd_fb.extend(["-map", audio_map_label, "-c:a", "aac", "-b:a", "192k"])
            else:
                cmd_fb.append("-an")
            cmd_fb.extend([
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-r", str(fps), "-pix_fmt", "yuv420p", "-t", f"{total_sec:.3f}",
                "-movflags", "+faststart", output_path,
            ])
            res_fb = subprocess.run(cmd_fb, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            if res_fb.returncode != 0:
                raise FFmpegAssembleError(f"Video Overlay 렌더 실패 (fallback 포함): {(res_fb.stderr or '')[-500:]}")
        else:
            raise FFmpegAssembleError(f"Video Overlay 렌더 실패: {err_msg}")

    # 임시 작업 디렉토리 정리
    shutil.rmtree(work_dir, ignore_errors=True)
    logger.info(f"✅ [Video Overlay 렌더 완료] -> {output_path}")
    return output_path
