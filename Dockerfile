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
RUN pip install --no-cache-dir --upgrade pip "setuptools<75" wheel

# ── Constraints globales para evitar mezclas incompatibles de NumPy 1.x/2.x ───
COPY constraints.txt .
ENV PIP_CONSTRAINT=/app/constraints.txt

# Instalar NumPy antes de librerías con extensiones nativas evita que Whisper/TTS
# descarguen NumPy 2.x temporalmente y dejen extensiones compiladas incompatibles.
RUN pip install --no-cache-dir "numpy==1.26.4"

# ── Paso 2: PyTorch CPU (índice separado) ─────────────────────────────────────
RUN pip install --no-cache-dir \
    torch==2.2.2 torchaudio==2.2.2 \
    --index-url https://download.pytorch.org/whl/cpu

# ── Paso 3: Whisper (needs --no-build-isolation for pkg_resources) ────────────
RUN pip install --no-cache-dir --no-build-isolation \
    "openai-whisper @ git+https://github.com/openai/whisper.git@v20231117"

# ── Paso 4: Resto de dependencias ─────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Verificación temprana: si NumPy queda roto, el deploy falla durante build y no
# en mitad de un doblaje de Telegram.
RUN python - <<'PY'
import numpy
import numpy.core.multiarray
import torch
import torchaudio
import whisper
import argostranslate.translate
from TTS.api import TTS
print("dependency smoke test ok", numpy.__version__)
PY

# ── Código fuente ──────────────────────────────────────────────────────────────
COPY bot.py .

# ── Directorios de datos y modelos ────────────────────────────────────────────
RUN mkdir -p /app/data \
             /app/models/torch \
             /app/models/whisper \
             /app/models/tts \
             /app/models/argos

CMD ["python", "bot.py"]
