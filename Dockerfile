FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot ./bot

# Run as an unprivileged user; /data holds the SQLite DB, Telethon session and
# Telegraph token. UID must match the owner of ./data on the host.
ARG UID=1001
RUN useradd --uid ${UID} --create-home bot \
    && mkdir -p /data \
    && chown bot:bot /data
USER bot
VOLUME ["/data"]

CMD ["python", "-m", "bot"]
