FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    TORCH_HOME=/app/models/torch \
    WHISPER_CACHE=/app/models/whisper \
    TTS_HOME=/app/models/tts \
    ARGOS_PACKAGES_DIR=/app/models/argos

WORKDIR /app

# ── Dependencias del sistema ───────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    gcc \
    g++ \
    git \
    libsndfile1 \
    espeak-ng \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Paso 1: setuptools + wheel (evita el error pkg_resources) ─────────────────
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# ── Paso 2: PyTorch CPU (índice separado) ─────────────────────────────────────
RUN pip install --no-cache-dir \
    torch==2.2.2 torchaudio==2.2.2 \
    --index-url https://download.pytorch.org/whl/cpu

# ── Paso 3: Resto de dependencias ─────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Código fuente ──────────────────────────────────────────────────────────────
COPY bot.py .

# ── Directorios de datos y modelos ────────────────────────────────────────────
RUN mkdir -p /app/data \
             /app/models/torch \
             /app/models/whisper \
             /app/models/tts \
             /app/models/argos

CMD ["python", "bot.py"]
