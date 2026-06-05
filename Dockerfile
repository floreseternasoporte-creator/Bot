FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Evita prompts interactivos en apt
    DEBIAN_FRONTEND=noninteractive \
    # Caché de modelos dentro del contenedor
    TORCH_HOME=/app/models/torch \
    WHISPER_CACHE=/app/models/whisper \
    # XTTS descarga modelos aquí
    TTS_HOME=/app/models/tts \
    # Argos paquetes de idiomas
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

# ── Dependencias Python ────────────────────────────────────────────────────────
COPY requirements.txt .

# Instalar PyTorch CPU primero (más liviano, suficiente para Railway free tier)
RUN pip install --no-cache-dir \
    torch==2.2.2 torchaudio==2.2.2 --index-url https://download.pytorch.org/whl/cpu

# Resto de dependencias
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
