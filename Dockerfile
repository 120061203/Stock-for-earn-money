FROM python:3.12-slim

# zoneinfo（ZoneInfo("Asia/Taipei")）需要系統時區資料庫，slim 映像預設不含。
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data /app/.cache \
    && chown -R appuser:appuser /app \
    && chmod +x /app/docker-entrypoint.sh
USER appuser

ENV PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    PORT=8765

EXPOSE 8765

ENTRYPOINT ["/app/docker-entrypoint.sh"]
