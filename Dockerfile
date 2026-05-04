FROM python:3.12-alpine

RUN apk add --no-cache ffmpeg

RUN adduser -D -h /app appuser

WORKDIR /app

ENV TZ=Asia/Kolkata \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .

RUN apk add --no-cache --virtual .build-deps build-base \
    && pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt \
    && apk --purge del .build-deps \
    && rm -rf /var/cache/apk/*

COPY . .

RUN python3 -m compileall -b -o 2 TelegramMediaBot

RUN mkdir -p /app/sessions /app/downloads /app/assets \
    && chown -R appuser:appuser /app

VOLUME ["/app/sessions"]

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD pgrep -f "python3 -m TelegramMediaBot" || exit 1

USER appuser

CMD ["python3", "-m", "TelegramMediaBot"]
