# AI 광고 공장 - Railway 배포용 (대시보드 + 캘린더·레이더 스케줄러를 한 컨테이너에서)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul \
    DATA_DIR=/data \
    RENDER_ENCODER=libx264

# ffmpeg(영상 조립·음성 처리), 한글 글꼴(대체용)·컬러 이모지(썸네일·바 이모지), opencv·onnxruntime 런타임 라이브러리, 시간대
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-noto-cjk fonts-noto-color-emoji libglib2.0-0 libgomp1 libsm6 libxext6 tzdata ca-certificates \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
RUN chmod +x scripts/railway_start.sh

# Railway가 PORT를 넣어 줌 (로컬 docker run 기본 8501)
EXPOSE 8501
CMD ["scripts/railway_start.sh"]
