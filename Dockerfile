FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    BROWSER_HEADLESS=true

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium && \
    apt-get install -y --no-install-recommends xvfb cron && \
    rm -rf /var/lib/apt/lists/*

COPY . .

RUN mkdir -p data && \
    chmod +x docker/entrypoint.sh && \
    cp docker/crontab /etc/cron.d/pickleball && \
    chmod 0644 /etc/cron.d/pickleball

ENTRYPOINT ["/app/docker/entrypoint.sh"]
