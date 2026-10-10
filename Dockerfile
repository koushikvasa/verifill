# Verifill: API + UI in one container (Railway / any Docker host).
FROM python:3.12-slim

# Tesseract (with eng + osd data for orientation detection) and the OpenCV runtime libs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY samples ./samples
COPY scripts ./scripts

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 VERIFILL_OFFLINE=0 GROUND_OCR=rapidocr
# Railway injects PORT; default for local docker run.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT}"]
