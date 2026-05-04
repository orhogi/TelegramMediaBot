FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg procps \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --home-dir /app appuser

WORKDIR /app

ENV TZ=Asia/Kolkata \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .

RUN pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python3 -m compileall -b -o 2 TelegramMediaBot

RUN mkdir -p /app/sessions /app/downloads /app/assets \
    && chown -R appuser:appuser /app

VOLUME ["/app/sessions"]

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD pgrep -f "python3 -m TelegramMediaBot" || exit 1

USER appuser

CMD ["python3", "-m", "TelegramMediaBot"]
