FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Berlin \
    NEWTECH_ROOT_FOLDER=/data/payments \
    NEWTECH_OUTPUT_FILE=/data/payments/result.xlsx

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        tesseract-ocr \
        tesseract-ocr-rus \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data/payments /app/reports /app/secrets

CMD ["python", "scripts/run_daily_update.py", "--payment-source", "max"]
