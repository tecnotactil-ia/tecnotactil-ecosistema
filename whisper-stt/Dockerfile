# =============================================================================
# Tecno Táctil — Whisper STT v2.0
# Imagen base compartida por:
#   - whisper_api    → Flask HTTP server (CMD python app.py)
#   - whisper_worker → RQ Worker          (CMD rq worker ...)
# =============================================================================

FROM python:3.11-slim

# Dependencias del sistema: ffmpeg obligatorio para Whisper
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    build-essential \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias Python (cacheado si requirements.txt no cambia)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip==24.0 && \
    pip install --no-cache-dir setuptools==69.5.1 wheel==0.43.0 && \
    pip install --no-cache-dir --no-build-isolation -r requirements.txt

# Copiar código fuente
COPY app.py .

EXPOSE 9000

# Variables por defecto (sobreescritas desde docker-compose)
ENV WHISPER_MODEL=large-v3
ENV WHISPER_LANGUAGE=es
ENV WHISPER_API_KEY=changeme
ENV REDIS_HOST=redis
ENV REDIS_PORT=6379
ENV REDIS_DB_QUEUE=2

# El CMD se sobreescribe por servicio en docker-compose:
#   whisper_api    → python app.py
#   whisper_worker → rq worker --url redis://:PASS@redis:6379/2 whisper
CMD ["python", "app.py"]
