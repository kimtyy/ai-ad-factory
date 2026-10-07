"""
core/tts.py - MS Edge-TTS 기반 고품질 신경망 한국어 음성 합성 엔진
- 여성 보이스: 'ko-KR-SunHiNeural' (기본값)
- 남성 보이스: 'ko-KR-InJoonNeural'
- 쇼츠 최적화 파라미터: rate="+12%", pitch="+3Hz"
"""
import os
import re
import asyncio
import logging
import concurrent.futures
from typing import Optional, Dict, Any

logger = logging.getLogger("ad_factory.tts")

# -------------------------------------------------------------
# MS Edge-TTS 한국어 신경망 보이스 매핑
# -------------------------------------------------------------
VOICE_MAPPING = {
    # 여성 보이스 -> ko-KR-SunHiNeural 강제
    "female": "ko-KR-SunHiNeural",
    "female_20s": "ko-KR-SunHiNeural",
    "female_30s": "ko-KR-SunHiNeural",
    "sunhi": "ko-KR-SunHiNeural",
    "ko-kr-sunhineural": "ko-KR-SunHiNeural",
    "여성": "ko-KR-SunHiNeural",
    "20대 여성": "ko-KR-SunHiNeural",
    "30대 여성": "ko-KR-SunHiNeural",

    # 남성 보이스 -> ko-KR-InJoonNeural 매핑
    "male": "ko-KR-InJoonNeural",
    "male_20s": "ko-KR-InJoonNeural",
    "male_30s": "ko-KR-InJoonNeural",
    "injoon": "ko-KR-InJoonNeural",
    "ko-kr-injoonneural": "ko-KR-InJoonNeural",
    "남성": "ko-KR-InJoonNeural",
    "20대 남성": "ko-KR-InJoonNeural",
    "30대 남성": "ko-KR-InJoonNeural",
}

DEFAULT_FEMALE_VOICE = "ko-KR-SunHiNeural"
DEFAULT_MALE_VOICE = "ko-KR-InJoonNeural"

# 쇼츠 호흡 및 연음 최적화 파라미터 (과도한 톤 변조 방지 및 자연스러운 음절 연음)
DEFAULT_RATE = "+5%"   # 과도한 배속으로 인한 음절 뭉개짐 방지 (+5%)
DEFAULT_PITCH = "+0Hz" # 과도한 톤 변조 방지 (+0Hz 자연 신경망 음색)


def resolve_edge_voice(actor_key: Optional[str]) -> str:
    """
    선택된 성우 키 또는 레이블을 기반으로 Edge-TTS 보이스 식별자를 반환합니다.
    - 여성 선택 시: 'ko-KR-SunHiNeural' 강제
    - 남성 선택 시: 'ko-KR-InJoonNeural' 매핑
    - 미지정/기본값: 'ko-KR-SunHiNeural' (여성 기본값)
    """
    if not actor_key:
        return DEFAULT_FEMALE_VOICE

    key_lower = str(actor_key).strip().lower()

    # 직접 매핑 확인
    for pattern, voice_name in VOICE_MAPPING.items():
        if pattern in key_lower:
            return voice_name

    # 남성 키워드가 명시적으로 들어간 경우만 남성 매핑, 그 외는 무조건 여성 기본값
    if any(m in key_lower for m in ["male", "남성", "injoon", "인준"]):
        return DEFAULT_MALE_VOICE

    return DEFAULT_FEMALE_VOICE


_DIGITS = "영일이삼사오육칠팔구"


def sino_korean_number(num: int) -> str:
    """정수를 한자어 수사로 (0 → 영, 10 → 십, 48 → 사십팔, 1200 → 천이백, 35000 → 삼만 오천)"""
    if num == 0:
        return "영"
    out = []
    for unit_value, unit_name in ((10 ** 8, "억"), (10 ** 4, "만")):
        if num >= unit_value:
            head, num = divmod(num, unit_value)
            out.append(("" if head == 1 and unit_name == "만" else sino_korean_number(head)) + unit_name)
    if num:
        part = ""
        for value, name in ((1000, "천"), (100, "백"), (10, "십"), (1, "")):
            d, num = divmod(num, value)
            if d:
                part += ("" if d == 1 and name else _DIGITS[d]) + name
        out.append(part)
    return " ".join(out)


_NATIVE_ONES = ["", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉"]
_NATIVE_TENS = ["", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔"]
# 고유어로 세는 단위 (1~99만): '3개' → '세 개', '2명' → '두 명', '3시간' → '세 시간'. '개월'·'번'(출구 번호와 횟수가 섞임)은 제외
NATIVE_COUNTERS = r"(?:개(?!월)|명|시간|시(?![가-힣])|잔|마리|살|그릇|가지|곳|번째|병|켤레|송이|시간째)"


def native_korean_number(num: int) -> str:
    """단위 앞 고유어 수사 (1 → 한, 20 → 스무, 21 → 스물한, 99 → 아흔아홉). 1~99만"""
    tens, ones = divmod(num, 10)
    if tens == 2 and ones == 0:
        return "스무"
    return _NATIVE_TENS[tens] + _NATIVE_ONES[ones]


def apply_readings(text: str, readings: Optional[Dict[str, str]] = None) -> str:
    """
    읽는 법 사전 적용 (TTS 입력에만, 자막은 원문): {'St. John's': '세인트존스'} 처럼 상호명·지명을 소리 나는 대로.
    긴 표기부터 바꿔 'St. John's 호텔'과 'St. John's'가 겹쳐도 안전
    """
    if not text or not readings:
        return text or ""
    for src in sorted((k for k in readings if k and k.strip() and (readings.get(k) or "").strip()), key=len, reverse=True):
        text = text.replace(src, readings[src].strip())
    return text


def owner_readings(business_name: str, reading: str) -> Dict[str, str]:
    """
    사장님 상호명 읽는 법 → TTS 사전. 'St. John's 강릉점' + '세인트존스 강릉점'이면 전체 표기와,
    대본에 지점명을 뺀 이름만 나와도 바뀌도록 앞부분('St. John's' → '세인트존스')도 함께 등록
    """
    name, say = (business_name or "").strip(), (reading or "").strip()
    if not name or not say or name == say:
        return {}
    out = {name: say}
    nw, sw = name.split(), say.split()
    if len(nw) == len(sw) and len(nw) > 1:
        for a, b in zip(nw, sw):
            if a != b and len(a) >= 2:
                out.setdefault(a, b)
    # 끝 어절(지점명 등)이 같으면 앞부분끼리도 등록: 'St. John's 강릉점'/'세인트존스 강릉점' → 'St. John's' → '세인트존스'
    while len(nw) > 1 and len(sw) > 1 and nw[-1] == sw[-1]:
        nw, sw = nw[:-1], sw[:-1]
        if " ".join(nw) != " ".join(sw):
            out.setdefault(" ".join(nw), " ".join(sw))
    return out


# 붙어 쓴 행정구역 ('경기도가평군청평면') → 끊어 읽기. 광역 단위는 이름 목록으로만 (일반 단어 '…도'를 건드리지 않게)
_PROVINCES = ("서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|서울시|부산시|대구시|인천시|광주시|대전시|울산시|세종시|경기도|강원특별자치도|강원도|"
              "충청북도|충청남도|전북특별자치도|전라북도|전라남도|경상북도|경상남도|제주특별자치도|제주도")
_PROVINCE_GLUE = re.compile(rf"({_PROVINCES})(?=[가-힣]{{1,4}}[시군구])")   # 뒤에 시·군·구가 올 때만 ('광주시청', '경기도민'은 그대로)
_LOCAL_GLUE = re.compile(r"(?<![가-힣])([가-힣]{1,4}[시군구])([가-힣]{1,4}[시군구읍면동리])"
                         r"(?=(?:에서|까지|부터|으로|로|에|은|는|이|가|도|의)?(?![가-힣]))")


def space_region_names(text: str) -> str:
    text = _PROVINCE_GLUE.sub(r"\1 ", text)
    for _ in range(2):   # 시+구+동처럼 세 단계로 붙은 경우
        text = _LOCAL_GLUE.sub(r"\1 \2", text)
    return text


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]")


def clean_tts_text(text: str, keep_commas: bool = False) -> str:
    """
    [TTS 음성 합성용 텍스트 정제기]
    - 문장 부호(!, .)와 쉼표 제거 -> 음절 간 어색한 끊김 방지 및 자연스러운 연음 유도
      (keep_commas=True: 의문문 억양용 '숨 쉼표'를 살림)
    - 물음표(?)는 유지: 지우면 신경망 TTS가 평서문으로 읽어 끝음이 떨어짐 (실측: '맞아요' 끝 F0 -26% → '맞아요?' +5~21%)
    - 특수문자 및 따옴표 제거
    - 숫자의 한자어 변환
    """
    if not text:
        return ""

    # 0-1. 이모지·장식 기호 제거, 붙어 쓴 지역명 끊기, 영문 상호 정리 ("John's" → "Johns", '카페ONE' → '카페 ONE')
    text = _EMOJI.sub(" ", text)
    text = re.sub(r"[·•|｜ㅣ★☆♥♡※▶►◆◇■□●○『』「」【】〈〉《》–—]", " ", text)
    text = re.sub(r"(?<=[A-Za-z])['’](?=[A-Za-z])", "", text)
    text = re.sub(r"(?<=[가-힣])(?=[A-Za-z])", " ", text)
    text = re.sub(r"\s*-\s*", " ", text)
    text = space_region_names(text)
    # 0-2. 고유어로 세는 단위는 고유어 수사 ('3개' → '세 개', '2명' → '두 명', '20잔' → '스무 잔'). 예전엔 '삼개'로 읽음
    text = re.sub(rf"(?<![\d,])([1-9]\d?)\s*(?={NATIVE_COUNTERS})", lambda m: native_korean_number(int(m.group(1))) + " ", text)
    # 0-3. 한자어 숫자 변환 - 쉼표 제거 전에 ('1,200' 보존) (자릿값 포함: 48 → 사십팔, 1200 → 천이백). 예전엔 한 자리씩 읽어 '48시간'이 '사팔 시간'이 됐음
    text = re.sub(r'\d{1,3}(?:,\d{3})+|\d+', lambda m: sino_korean_number(int(m.group(0).replace(",", ""))), text)

    # 1. 물결표, 특수문자, 따옴표, 괄호류 제거
    text = text.replace("~", " ").replace("～", " ").replace("^", " ")
    text = re.sub(r'[\'\"`#*@&%=+;:\(\)\[\]\{\}<>\/\\_]', ' ', text)

    # 2. 문장 부호(!, .) 및 쉼표 제거 (자연스러운 연음 유도). 물음표는 하나로 정리해 유지 ('?!', '??' → '?')
    text = re.sub(r"\?[!?]*|!+\?", "?", text)
    text = text.replace("!", " ").replace(".", " ")
    if not keep_commas:
        text = text.replace(",", " ")


    # 4. 공백 및 조사 정리
    text = re.sub(r'\s+', ' ', text)
    # 떨어져 있는 조사 한 글자만 앞말에 붙임 ('숙소 를' → '숙소를'). '이/가'는 관형사·동사('이 뷰', '여기 가 보면')와
    # 구별이 안 돼 제외하고, 다음 어절의 첫 글자('이 가격', '가성비')도 건드리지 않음
    text = re.sub(r'(\S)\s+([을를은는])(?=\s|$|[?,])', r'\1\2', text)
    text = re.sub(r'\s+([?,])', r'\1', text)
    return text.strip()


def question_variants(text: str) -> list:
    """
    의문문 억양 후보 (TTS 입력용, 자막은 그대로). 신경망 TTS는 문장마다 끝 억양이 달라 한 규칙으로 안 올라가므로
    후보를 합성해 끝음 상승폭(end_rise_percent)이 가장 큰 것을 고름:
      그대로 / 마지막 어절 앞 숨 쉼표 / 마지막 두 어절 앞 숨 쉼표
    실측(SunHi): '여기 진짜, 한국 맞아요?' +20% (그대로 +5%), '아직도 여기, 모르세요?' +50% (그대로 +21%)
    """
    base = clean_tts_text(text, keep_commas=True)
    if not base.endswith("?"):
        return [base]
    body = base[:-1].rstrip()
    head, _, last_sentence = body.rpartition("? ")
    prefix = (head + "? ") if head else ""
    words = last_sentence.split(" ")
    out = [base]
    if len(words) >= 2 and "," not in words[-2]:
        out.append(prefix + " ".join(words[:-1]) + ", " + words[-1] + "?")
    if len(words) >= 3 and "," not in words[-3]:
        out.append(prefix + " ".join(words[:-2]) + ", " + " ".join(words[-2:]) + "?")
    return list(dict.fromkeys(out))


def end_rise_percent(path: str) -> Optional[float]:
    """
    말끝 억양 상승폭(%): 앞 60% 구간 대비 마지막 구간의 기본 주파수(F0) 중앙값 변화 (자기상관 피치 추정, 16kHz 모노).
    측정 불가하면 None
    """
    import subprocess
    import numpy as np
    import imageio_ffmpeg
    try:
        raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", path, "-f", "s16le", "-ac", "1",
                              "-ar", "16000", "-"], capture_output=True, timeout=30).stdout
    except Exception:
        return None
    x = np.frombuffer(raw, np.int16).astype(np.float32)
    sr, win, hop = 16000, 640, 160
    lo, hi = sr // 400, sr // 70
    track = []
    for i in range(0, max(0, len(x) - win), hop):
        fr = x[i:i + win] - x[i:i + win].mean()
        if np.sqrt((fr ** 2).mean()) < 300:
            continue
        ac = np.correlate(fr, fr, "full")[win - 1:]
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] > 0.3 * ac[0]:
            track.append(sr / lag)
    if len(track) < 10:
        return None
    n = len(track)
    head = float(np.median(track[: int(n * 0.6)]))
    tail = float(np.median(track[-max(4, n // 10):]))
    return (tail / head - 1.0) * 100.0


async def _generate_edge_tts_async(
    text: str,
    voice: str,
    rate: str,
    pitch: str,
    output_path: str
):
    import edge_tts
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        pitch=pitch
    )
    await communicate.save(output_path)


def _run_async_safe(coro):
    """Streamlit 또는 실행 중인 이벤트 루프 환경에서도 안전하게 async 코루틴 실행"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


def generate_speech(
    text: str,
    output_path: str,
    voice_actor: str = "female_20s",
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
    force: bool = False,
    keep_commas: bool = False
) -> str:
    """
    Edge-TTS를 이용해 고품질 한국어 나레이션 오디오 파일(MP3)을 생성합니다.
    
    Args:
        text: 나레이션 대사
        output_path: 저장될 MP3 파일 경로
        voice_actor: 성우 키 ('female_20s', 'male_20s', 'ko-KR-SunHiNeural', 등)
        rate: 속도 (기본값 '+12%')
        pitch: 피치 (기본값 '+3Hz')
        force: 기존 캐시 무시 강제 재생성 여부
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if not force and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
        logger.info(f"[TTS Cache Hit] {output_path}")
        return output_path

    # 성우 보이스 자동 매핑
    voice = resolve_edge_voice(voice_actor)
    cleaned = clean_tts_text(text, keep_commas=keep_commas)
    if not cleaned:
        cleaned = "힐링 여행을 즐겨보세요."

    logger.info(f"[Edge-TTS] 음성 합성 시작: Voice={voice}, Rate={rate}, Pitch={pitch} -> '{cleaned}'")
    print(f"[TTS] Edge-TTS 생성: Voice={voice} (원래 요청={voice_actor}), Rate={rate}, Pitch={pitch} -> {output_path}")

    try:
        _run_async_safe(_generate_edge_tts_async(
            text=cleaned,
            voice=voice,
            rate=rate,
            pitch=pitch,
            output_path=output_path
        ))

        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            sz = os.path.getsize(output_path)
            logger.info(f"[Edge-TTS 성공] {output_path} ({sz:,} bytes)")
            print(f"[TTS] [성공] {output_path} ({sz:,} bytes) [Voice: {voice}]")
            return output_path
        else:
            raise RuntimeError("Edge-TTS 출력 파일이 비어있거나 생성되지 않았습니다.")

    except Exception as e:
        logger.warning(f"Edge-TTS 생성 실패 ({e}). gTTS Fallback 시도 중...")
        print(f"[TTS] [경고] Edge-TTS 생성 예외 ({e}), gTTS Fallback 진행...")
        try:
            from gtts import gTTS
            tts = gTTS(text=cleaned, lang="ko", slow=False)
            tts.save(output_path)
            print(f"[TTS] [gTTS Fallback 성공] {output_path}")
            return output_path
        except Exception as ge:
            logger.error(f"gTTS Fallback도 실패: {ge}")
            raise RuntimeError(f"모든 TTS 엔진 호출 실패: {ge}")
