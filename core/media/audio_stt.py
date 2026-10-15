"""
core/media/audio_stt.py - 롱폼 영상 오디오 추출 + 전사(STT) + 발화 구간(VAD) + 캐시

롱폼 하이라이트 파이프라인의 1단계. 영상에서 16kHz mono 오디오를 뽑아 단어/문장 타임스탬프와
발화 구간을 만들고, 같은 영상을 다시 고르면 재전사하지 않도록 공용 캐시에 저장한다.

설계 원칙
- 지연 임포트: faster-whisper는 실제로 전사할 때만 임포트한다. 미설치여도 이 모듈과 앱·기존 스위트는 그대로 동작하고,
  전사 호출 시에만 STTDependencyError(설치 안내)가 난다.
- VAD: faster-whisper가 번들한 Silero VAD(ONNX)를 쓴다. 별도 silero-vad/torch 패키지는 쓰지 않는다.
- 전사기(Transcriber)는 주입 가능: 테스트는 모델 다운로드 없이 결정적 가짜 전사기를 넣는다.
- 캐시: 공용 폴더(outputs/cache/stt)에 파일 크기·mtime·앞/뒤 4MB 샘플 해시 + 모델·언어·프롬프트·전사기 식별자로 키를 만든다.
  (job 폴더는 작업마다 새로 생기고 정리되므로 같은 영상을 다시 골라도 적중하지 못함)
"""
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("ad_factory.media.audio_stt")

SCHEMA_VERSION = 3                            # 2: 긴 문장 분할(split_long_sentences) / 3: 음량 포락선(rms_db) 추가
RMS_HOP_SEC = 0.5                             # 음량 포락선 간격 (음악 지배 휴리스틱·트랙 판정용)
SAMPLE_RATE = 16000
DEFAULT_LANGUAGE = "ko"
DEFAULT_MODEL = "small"                       # WHISPER_MODEL 환경변수로 교체 (예: medium, large-v3-turbo)
SAMPLE_BYTES = 4 * 1024 * 1024                # 캐시 키: 파일 앞/뒤 샘플 크기
PROMPT_MAX_CHARS = 200                        # Whisper initial_prompt 토큰 한도(≈224)를 넘지 않게
DEFAULT_CACHE_MAX_MB = 500
MAX_SENTENCE_SEC = 8.0                        # 이보다 긴 문장은 가장 큰 단어 간 틈에서 나눈다 (하이라이트 경계 후보 확보)
MIN_SPLIT_GAP_SEC = 0.25

ProgressCallback = Callable[[int, str], None]


# ---------------------------------------------------------------- 예외
class STTError(RuntimeError):
    """전사 파이프라인 오류의 공통 부모"""


class STTDependencyError(STTError):
    """faster-whisper 등 전사 의존성이 설치되지 않음 (설치 안내 포함)"""


class NoAudioStreamError(STTError):
    """영상에 오디오 스트림이 없음 (전사 불가)"""


# ---------------------------------------------------------------- 결과 데이터
@dataclass
class Word:
    text: str
    start: float
    end: float
    prob: float = 1.0


@dataclass
class Sentence:
    text: str
    start: float
    end: float
    word_range: Tuple[int, int] = (0, 0)      # words[word_range[0]:word_range[1]]
    avg_prob: float = 1.0
    no_speech_prob: float = 0.0


@dataclass
class Span:
    start: float
    end: float


@dataclass
class Transcript:
    schema_version: int
    source_key: str
    language: str
    model: str
    duration_sec: float
    words: List[Word] = field(default_factory=list)
    sentences: List[Sentence] = field(default_factory=list)
    speech_spans: List[Span] = field(default_factory=list)
    rms_hop_sec: float = RMS_HOP_SEC
    rms_db: List[float] = field(default_factory=list)       # 구간별 RMS(dBFS), rms_db[i]는 [i*hop, (i+1)*hop)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Transcript":
        return cls(
            schema_version=int(d["schema_version"]), source_key=str(d["source_key"]), language=str(d["language"]),
            model=str(d["model"]), duration_sec=float(d["duration_sec"]),
            words=[Word(**w) for w in d.get("words", [])],
            sentences=[Sentence(**{**s, "word_range": tuple(s.get("word_range", (0, 0)))}) for s in d.get("sentences", [])],
            speech_spans=[Span(**sp) for sp in d.get("speech_spans", [])],
            rms_hop_sec=float(d.get("rms_hop_sec", RMS_HOP_SEC)), rms_db=[float(x) for x in d.get("rms_db", [])],
        )

    def speech_coverage(self, start: float, end: float) -> float:
        """[start, end) 구간에서 발화 구간이 차지하는 비율 (0~1)"""
        total = max(0.0, end - start)
        if total <= 0:
            return 0.0
        covered = sum(max(0.0, min(end, sp.end) - max(start, sp.start)) for sp in self.speech_spans)
        return min(1.0, covered / total)


# ---------------------------------------------------------------- 전사기 인터페이스
class Transcriber:
    """
    전사기 인터페이스. transcribe()는 아래 형태의 dict를 반환한다 (시간은 모두 원본 영상 기준 초):
      {"sentences": [{"text", "start", "end", "avg_prob", "no_speech_prob",
                      "words": [{"text", "start", "end", "prob"}]}],
       "speech_spans": [{"start", "end"}]}
    cache_id는 캐시 키에 섞여, 가짜 전사기 결과가 실제 전사 캐시를 오염시키지 않는다.
    """
    cache_id = "base"

    def ensure_ready(self) -> None:
        """실행 전 사전 점검 (의존성 누락을 오디오 추출 전에 알림). 문제가 있으면 STTDependencyError"""

    def transcribe(self, wav_path: str, *, language: str, initial_prompt: str, model: str,
                   progress: Optional[ProgressCallback] = None) -> Dict[str, Any]:
        raise NotImplementedError


class FasterWhisperTranscriber(Transcriber):
    """faster-whisper(int8, CPU) + 번들 Silero VAD. 임포트는 transcribe() 안에서만 한다."""
    cache_id = "faster-whisper"

    def __init__(self, device: str = "cpu", compute_type: str = "int8", model_dir: Optional[str] = None):
        self.device = device
        self.compute_type = compute_type
        self.model_dir = model_dir or os.getenv("WHISPER_MODEL_DIR") or None
        self._model = None
        self._model_name = None

    def ensure_ready(self) -> None:
        self._import()

    @staticmethod
    def _import():
        try:
            from faster_whisper import WhisperModel   # noqa: WPS433 (지연 임포트)
            return WhisperModel
        except ImportError as e:
            raise STTDependencyError(
                "faster-whisper가 설치되어 있지 않습니다. `pip install faster-whisper` 후 다시 시도해 주세요 "
                "(모델은 첫 실행 시 자동 다운로드되며, WHISPER_MODEL_DIR로 저장 위치를 지정할 수 있습니다).") from e

    def _load(self, name: str):
        WhisperModel = self._import()
        if self._model is None or self._model_name != name:
            self._model = WhisperModel(name, device=self.device, compute_type=self.compute_type,
                                       download_root=self.model_dir)
            self._model_name = name
        return self._model

    def transcribe(self, wav_path, *, language, initial_prompt, model, progress=None):
        model_obj = self._load(model)
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        # 우리가 이미 16kHz mono PCM WAV로 뽑았으므로 직접 읽는다 (faster_whisper.audio.decode_audio는 PyAV 버전에 민감:
        # faster-whisper 1.2.1 + av 19에서 av.open(metadata_errors=...) TypeError 확인)
        audio = read_wav_float32(wav_path)
        duration = len(audio) / SAMPLE_RATE
        # 발화 커버리지 계산용 구간: 짧은 침묵도 끊어서 실제 말한 시간에 가깝게 (기본 2초 침묵 병합은 너무 느슨함)
        vad = VadOptions(min_silence_duration_ms=400, speech_pad_ms=100)
        spans = [{"start": round(t["start"] / SAMPLE_RATE, 3), "end": round(t["end"] / SAMPLE_RATE, 3)}
                 for t in get_speech_timestamps(audio, vad, sampling_rate=SAMPLE_RATE)]

        segments, _info = model_obj.transcribe(
            audio, language=language, initial_prompt=initial_prompt or None, word_timestamps=True,
            vad_filter=True, vad_parameters=vad, beam_size=5, condition_on_previous_text=False)
        sentences = []
        for seg in segments:                       # 제너레이터: 여기서 실제 전사가 진행됨
            words = [{"text": (w.word or "").strip(), "start": round(float(w.start), 3), "end": round(float(w.end), 3),
                      "prob": round(float(getattr(w, "probability", 1.0)), 4)} for w in (seg.words or [])]
            probs = [w["prob"] for w in words]
            sentences.append({
                "text": (seg.text or "").strip(), "start": round(float(seg.start), 3), "end": round(float(seg.end), 3),
                "avg_prob": round(sum(probs) / len(probs), 4) if probs else 0.0,
                "no_speech_prob": round(float(getattr(seg, "no_speech_prob", 0.0)), 4), "words": words})
            if progress and duration > 0:
                _safe_progress(progress, int(min(99, float(seg.end) / duration * 100)), f"전사 중 {seg.end:.0f}/{duration:.0f}초")
        return {"sentences": sentences, "speech_spans": spans}


# ---------------------------------------------------------------- 도우미
def read_wav_float32(path: str):
    """16kHz mono 16bit PCM WAV → float32 numpy 배열(-1~1). (표준 라이브러리 wave + numpy만 사용)"""
    import numpy as np
    with wave.open(path, "rb") as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise STTError(f"16kHz mono 16bit WAV가 아닙니다: {w.getframerate()}Hz/{w.getnchannels()}ch/{w.getsampwidth() * 8}bit")
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def _safe_progress(cb: Optional[ProgressCallback], pct: int, msg: str) -> None:
    if cb is None:
        return
    try:
        cb(max(0, min(100, int(pct))), msg)
    except Exception as e:                         # 진행률 표시 오류가 전사를 죽이면 안 됨
        logger.debug(f"진행률 콜백 오류 무시: {e}")


def build_initial_prompt(store_name: str = "", terms: Sequence[str] = (), max_chars: int = PROMPT_MAX_CHARS) -> str:
    """상호명·메뉴명을 Whisper initial_prompt로. 고유명사 오인식을 줄이고 길이는 한도 안으로 자른다."""
    parts: List[str] = []
    if (store_name or "").strip():
        parts.append(f"상호명: {store_name.strip()}.")
    seen = set()
    cleaned = []
    for t in terms or ():
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            cleaned.append(t)
    if cleaned:
        parts.append("자주 나오는 말: " + ", ".join(cleaned) + ".")
    text = " ".join(parts)
    if len(text) <= max_chars:
        return text
    # 한도 초과: 용어를 하나씩 줄여 가며 다시 만든다 (상호명은 유지)
    while cleaned and len(text) > max_chars:
        cleaned.pop()
        parts = ([f"상호명: {store_name.strip()}."] if (store_name or "").strip() else [])
        if cleaned:
            parts.append("자주 나오는 말: " + ", ".join(cleaned) + ".")
        text = " ".join(parts)
    return text[:max_chars]


def cache_root() -> str:
    return os.getenv("STT_CACHE_DIR") or os.path.join("outputs", "cache", "stt")


def make_cache_key(video_path: str, *, model: str, language: str, initial_prompt: str, transcriber_id: str) -> str:
    """
    경량 캐시 키: 파일 크기 + mtime + 앞/뒤 4MB 샘플 해시 + 모델 + 언어 + 프롬프트 + 전사기 + 스키마 버전.
    (4GB 파일 전체를 읽지 않는다. 같은 크기·mtime에서 가운데만 바뀐 파일은 구분하지 못하는 것을 감수하는 설계)
    """
    st = os.stat(video_path)
    h = hashlib.sha256()
    h.update(f"v{SCHEMA_VERSION}|{st.st_size}|{int(st.st_mtime_ns)}|{model}|{language}|{initial_prompt}|{transcriber_id}".encode("utf-8"))
    with open(video_path, "rb") as f:
        if st.st_size <= SAMPLE_BYTES * 2:
            h.update(f.read())
        else:
            h.update(f.read(SAMPLE_BYTES))
            f.seek(max(0, st.st_size - SAMPLE_BYTES))
            h.update(f.read(SAMPLE_BYTES))
    return h.hexdigest()[:32]


def _cache_path(key: str, cache_dir: Optional[str] = None) -> str:
    return os.path.join(cache_dir or cache_root(), f"{key}.json")


def load_cached(key: str, cache_dir: Optional[str] = None) -> Optional[Transcript]:
    """캐시 적중 시 Transcript. 없거나 깨졌거나 스키마가 다르면 None (호출자가 재전사)"""
    path = _cache_path(key, cache_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if int(data.get("schema_version", -1)) != SCHEMA_VERSION or data.get("source_key") != key:
            return None
        os.utime(path, None)                       # 최근 사용 갱신 (정리 시 오래된 것부터 삭제)
        return Transcript.from_dict(data)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_cache(tr: Transcript, cache_dir: Optional[str] = None) -> str:
    """임시 파일에 쓴 뒤 교체(원자적). 쓰기 도중 중단돼도 깨진 캐시가 남지 않는다."""
    d = cache_dir or cache_root()
    os.makedirs(d, exist_ok=True)
    path = _cache_path(tr.source_key, d)
    fd, tmp_path = tempfile.mkstemp(prefix=".stt_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(tr.to_dict(), f, ensure_ascii=False)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return path


def prune_cache(cache_dir: Optional[str] = None, max_mb: Optional[float] = None) -> int:
    """캐시 총량이 상한(STT_CACHE_MAX_MB, 기본 500MB)을 넘으면 오래된 것부터 삭제. 삭제한 파일 수 반환"""
    d = cache_dir or cache_root()
    limit = (max_mb if max_mb is not None else float(os.getenv("STT_CACHE_MAX_MB", DEFAULT_CACHE_MAX_MB))) * 1024 * 1024
    try:
        files = [os.path.join(d, n) for n in os.listdir(d) if n.endswith(".json")]
    except OSError:
        return 0
    entries = []
    for p in files:
        try:
            st = os.stat(p)
            entries.append((st.st_mtime, st.st_size, p))
        except OSError:
            continue
    total = sum(e[1] for e in entries)
    removed = 0
    for _mtime, size, p in sorted(entries):
        if total <= limit:
            break
        try:
            os.remove(p)
            total -= size
            removed += 1
        except OSError:
            continue
    return removed


# ---------------------------------------------------------------- 오디오 추출
def extract_audio_16k(video_path: str, out_wav: str) -> float:
    """영상 → 16kHz mono PCM WAV. 길이(초)를 반환. 오디오 스트림이 없으면 NoAudioStreamError."""
    from core.media.normalize import probe_media
    from core.render.ffmpeg_assemble import ffmpeg_exe

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {video_path}")
    info = probe_media(video_path)
    if not info.has_audio:
        raise NoAudioStreamError(f"오디오 스트림이 없는 영상입니다 (전사 불가): {os.path.basename(video_path)}")
    cmd = [ffmpeg_exe(), "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-i", video_path,
           "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", out_wav]
    res = subprocess.run(cmd, capture_output=True)   # 인자 리스트 실행: 경로의 ' 와 한글을 셸이 해석하지 않음
    if res.returncode != 0 or not os.path.exists(out_wav):
        raise STTError("오디오 추출 실패: " + res.stderr.decode("utf-8", "ignore")[-300:])
    with wave.open(out_wav, "rb") as w:
        return w.getnframes() / float(w.getframerate())


# ---------------------------------------------------------------- 메인 진입점
def split_long_sentences(words: List[Word], sentences: List[Sentence], max_sec: float = MAX_SENTENCE_SEC,
                        min_gap: float = MIN_SPLIT_GAP_SEC) -> List[Sentence]:
    """
    Whisper 세그먼트가 구두점 없이 10~20초씩 이어지면 구간 경계 후보가 없다. max_sec를 넘는 문장은
    단어 사이 가장 큰 틈(min_gap 이상)에서 재귀적으로 나눈다. 틈이 없으면 그대로 둔다.
    """
    def one(i0: int, i1: int, orig: Sentence) -> List[Sentence]:
        ws = words[i0:i1]
        if len(ws) < 2 or (ws[-1].end - ws[0].start) <= max_sec:
            return [Sentence(text=orig.text if (i0, i1) == orig.word_range else " ".join(w.text for w in ws),
                             start=ws[0].start if ws else orig.start, end=ws[-1].end if ws else orig.end,
                             word_range=(i0, i1), avg_prob=orig.avg_prob, no_speech_prob=orig.no_speech_prob)]
        gaps = [(ws[k + 1].start - ws[k].end, k) for k in range(len(ws) - 1)]
        top = max(g for g, _ in gaps)
        mid = (len(ws) - 2) / 2.0
        # 가장 큰 틈(0.05초 이내 동률 포함) 중 문장 가운데에 가까운 곳 → 한쪽으로 쏠린 토막을 피함
        gap, k = min(((g, kk) for g, kk in gaps if g >= top - 0.05), key=lambda x: abs(x[1] - mid))
        if gap < min_gap:
            return [Sentence(text=orig.text if (i0, i1) == orig.word_range else " ".join(w.text for w in ws),
                             start=ws[0].start, end=ws[-1].end, word_range=(i0, i1),
                             avg_prob=orig.avg_prob, no_speech_prob=orig.no_speech_prob)]
        return one(i0, i0 + k + 1, orig) + one(i0 + k + 1, i1, orig)

    out: List[Sentence] = []
    for sent in sentences:
        i0, i1 = sent.word_range
        out.extend(one(i0, i1, sent) if i1 > i0 else [sent])
    return out


def compute_rms_envelope(wav_path: str, hop_sec: float = RMS_HOP_SEC) -> List[float]:
    """WAV 전체의 hop_sec 간격 RMS(dBFS) 포락선. 1시간이어도 7200개 값이라 캐시에 함께 저장한다."""
    import numpy as np
    audio = read_wav_float32(wav_path)
    hop = max(1, int(SAMPLE_RATE * hop_sec))
    n = len(audio) // hop
    if n == 0:
        return []
    frames = audio[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    return [round(float(20.0 * np.log10(max(v, 1e-6))), 1) for v in rms]


def _build_transcript(raw: Dict[str, Any], key: str, language: str, model: str, duration: float,
                      rms_db: Optional[List[float]] = None) -> Transcript:
    words: List[Word] = []
    sentences: List[Sentence] = []
    for s in raw.get("sentences", []):
        i0 = len(words)
        for w in s.get("words", []):
            words.append(Word(text=str(w["text"]), start=float(w["start"]), end=float(w["end"]), prob=float(w.get("prob", 1.0))))
        sentences.append(Sentence(text=str(s["text"]), start=float(s["start"]), end=float(s["end"]), word_range=(i0, len(words)),
                                  avg_prob=float(s.get("avg_prob", 1.0)), no_speech_prob=float(s.get("no_speech_prob", 0.0))))
    sentences = split_long_sentences(words, sentences)
    spans = [Span(start=float(sp["start"]), end=float(sp["end"])) for sp in raw.get("speech_spans", [])]
    return Transcript(schema_version=SCHEMA_VERSION, source_key=key, language=language, model=model,
                      duration_sec=round(float(duration), 3), words=words, sentences=sentences, speech_spans=spans,
                      rms_hop_sec=RMS_HOP_SEC, rms_db=list(rms_db or []))


def transcribe_video(video_path: str, *, store_name: str = "", terms: Sequence[str] = (), language: str = DEFAULT_LANGUAGE,
                     model: Optional[str] = None, transcriber: Optional[Transcriber] = None,
                     progress: Optional[ProgressCallback] = None, cache_dir: Optional[str] = None,
                     force: bool = False) -> Transcript:
    """
    롱폼 영상 전사. 캐시가 있으면 재전사하지 않는다 (force=True면 무시).
    progress(pct, msg)는 0→100 단조 증가. 전사기를 안 넘기면 FasterWhisperTranscriber(int8, CPU).
    """
    model_name = model or os.getenv("WHISPER_MODEL") or DEFAULT_MODEL
    tr_impl = transcriber or FasterWhisperTranscriber()
    prompt = build_initial_prompt(store_name, terms)
    _safe_progress(progress, 0, "캐시 확인 중")
    key = make_cache_key(video_path, model=model_name, language=language, initial_prompt=prompt,
                         transcriber_id=getattr(tr_impl, "cache_id", type(tr_impl).__name__))
    if not force:
        hit = load_cached(key, cache_dir)
        if hit is not None:
            logger.info(f"[STT 캐시 적중] {os.path.basename(video_path)} ({len(hit.sentences)}문장) - 재전사 생략")
            _safe_progress(progress, 100, "캐시된 전사 결과 사용")
            return hit

    tr_impl.ensure_ready()                         # 오디오를 뽑기 전에 의존성 누락부터 알림
    wav_dir = tempfile.mkdtemp(prefix="adf_stt_")
    wav_path = os.path.join(wav_dir, "audio_16k.wav")
    try:
        _safe_progress(progress, 2, "오디오 추출 중 (16kHz mono)")
        t0 = time.time()
        duration = extract_audio_16k(video_path, wav_path)
        logger.info(f"[STT] 오디오 추출 {duration:.0f}초 분량 ({time.time() - t0:.1f}s)")
        _safe_progress(progress, 8, "전사 시작")

        last = {"pct": 8}

        def scaled(pct: int, msg: str) -> None:    # 전사기 진행률(0~100)을 8~96 구간으로 매핑, 역행 방지
            p = 8 + int(max(0, min(100, pct)) * 0.88)
            if p >= last["pct"]:
                last["pct"] = p
                _safe_progress(progress, p, msg)

        raw = tr_impl.transcribe(wav_path, language=language, initial_prompt=prompt, model=model_name, progress=scaled)
        try:
            envelope = compute_rms_envelope(wav_path)
        except Exception as e:                      # 포락선 실패는 전사 결과를 버릴 이유가 아님 (음악 휴리스틱만 비활성)
            logger.warning(f"[STT] 음량 포락선 계산 실패: {e}")
            envelope = []
        result = _build_transcript(raw, key, language, model_name, duration, envelope)
        _safe_progress(progress, 97, "전사 결과 저장 중")
        try:
            save_cache(result, cache_dir)
            prune_cache(cache_dir)
        except OSError as e:                       # 캐시 저장 실패는 전사 결과를 버릴 이유가 아님
            logger.warning(f"[STT 캐시 저장 실패] {e}")
        _safe_progress(progress, 100, f"전사 완료 ({len(result.sentences)}문장)")
        return result
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass
        try:
            os.rmdir(wav_dir)
        except OSError:
            pass
