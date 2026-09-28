# ==========================================================================
#  Polixor – תצורת Docker אופציונלית
#
#  בנייה:   docker build -t polixor .
#  הרצה:    docker run -p 8756:8756 -v polixor-data:/data polixor
#
#  הערה: תמיכת GPU דורשת nvidia-container-toolkit ותמונת בסיס עם CUDA.
#        התצורה הזו רצה על CPU בלבד.
# ==========================================================================

# ---------- שלב 1: בניית הממשק ----------
FROM node:20-slim AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# ---------- שלב 2: שרת ----------
FROM python:3.11-slim

# FFmpeg + גופנים עם תמיכה בעברית (חיוני לכתוביות RTL)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        fonts-noto-core \
        fonts-noto-hinted \
        fontconfig \
        libgl1 \
        libglib2.0-0 \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY backend/polixor ./polixor
COPY backend/tests ./tests
COPY --from=frontend /build/dist /app/frontend_dist

ENV POLIXOR_DATA_DIR=/data \
    POLIXOR_FRONTEND_DIST=/app/frontend_dist \
    POLIXOR_HOST=0.0.0.0 \
    POLIXOR_PORT=8756 \
    PYTHONUNBUFFERED=1

VOLUME ["/data"]
EXPOSE 8756

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8756/api/health',timeout=4).status==200 else 1)"

CMD ["python", "-m", "polixor.main"]
