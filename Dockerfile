FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# All live state (SQLite DB, Telegram .session files) lives on ONE persistent
# volume mounted at /data — the image itself holds no secrets or sessions.
# Railway: add a Volume to the service with mount path /data.
RUN mkdir -p /data/db /data/sessions /data/media
ENV DATA_DIR=/data/db
ENV SESSION_DIR=/data/sessions
ENV MEDIA_DIR=/data/media

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8888

EXPOSE 8888

CMD ["python", "main.py"]
