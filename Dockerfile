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

# Ensure data directory exists for persistent volume mounting
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8888

EXPOSE 8888

CMD ["python", "main.py"]
