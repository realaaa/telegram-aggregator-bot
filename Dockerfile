FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot ./bot

# Run as an unprivileged user; /data holds the SQLite DB and Telethon session
RUN useradd --uid 1000 --create-home bot \
    && mkdir -p /data \
    && chown bot:bot /data
USER bot
VOLUME ["/data"]

CMD ["python", "-m", "bot"]
