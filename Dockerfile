FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    TORCH_HOME=/app/models/torch \
    WHISPER_CACHE=/app/models/whisper \
    XDG_CACHE_HOME=/app/models \
    TTS_HOME=/app/models/tts \
    ARGOS_PACKAGES_DIR=/app/models/argos \
    COQUI_TOS_AGREED=1

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

# ── Paso 1: setuptools + wheel ─────────────────────────────────────────────────
RUN pip install --no-cache-dir --upgrade pip "setuptools<75" wheel

# ── Constraints globales ───────────────────────────────────────────────────────
COPY constraints.txt .
ENV PIP_CONSTRAINT=/app/constraints.txt

RUN pip install --no-cache-dir "numpy==1.26.4"

# ── Paso 2: PyTorch CPU ────────────────────────────────────────────────────────
RUN pip install --no-cache-dir \
    torch==2.2.2 torchaudio==2.2.2 \
    --index-url https://download.pytorch.org/whl/cpu

# ── Paso 3: Whisper ────────────────────────────────────────────────────────────
RUN pip install --no-cache-dir --no-build-isolation \
    "openai-whisper @ git+https://github.com/openai/whisper.git@v20231117"

# ── Paso 4: Resto de dependencias ─────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Directorios de modelos ─────────────────────────────────────────────────────
RUN mkdir -p /app/data \
             /app/models/torch \
             /app/models/whisper \
             /app/models/tts \
             /app/models/argos

# ── Pre-descarga modelo Whisper base ──────────────────────────────────────────
RUN python - <<'PY'
import whisper
print("Descargando modelo Whisper base...")
whisper.load_model("base")
print("Whisper base descargado OK")
PY

# ── Pre-descarga modelo XTTS-v2 ───────────────────────────────────────────────
RUN python - <<'PY'
import os
os.environ["COQUI_TOS_AGREED"] = "1"
from TTS.api import TTS
print("Descargando modelo XTTS-v2 (puede tardar varios minutos)...")
TTS("tts_models/multilingual/multi-dataset/xtts_v2")
print("XTTS-v2 descargado OK")
PY

# ── Pre-instalación paquetes Argos (todos los idiomas a/desde inglés) ─────────
RUN python - <<'PY'
import argostranslate.package
print("Actualizando índice de paquetes Argos...")
argostranslate.package.update_package_index()
available = argostranslate.package.get_available_packages()
codes = ["es", "fr", "de", "pt", "it", "ja", "zh", "ru", "ar"]
for code in codes:
    for src, dst in [(code, "en"), ("en", code)]:
        pkg = next((p for p in available if p.from_code == src and p.to_code == dst), None)
        if pkg:
            print(f"Instalando Argos {src}→{dst}...")
            argostranslate.package.install_from_path(pkg.download())
            print(f"  OK {src}→{dst}")
        else:
            print(f"  AVISO: no encontrado {src}→{dst}")
# Pares directos útiles (sin pivote)
direct_pairs = [("es", "fr"), ("es", "de"), ("es", "pt"), ("fr", "es"), ("de", "es"), ("pt", "es")]
for src, dst in direct_pairs:
    pkg = next((p for p in available if p.from_code == src and p.to_code == dst), None)
    if pkg:
        print(f"Instalando Argos directo {src}→{dst}...")
        argostranslate.package.install_from_path(pkg.download())
print("Todos los paquetes Argos instalados")
PY

# ── Verificación final de dependencias ────────────────────────────────────────
RUN python - <<'PY'
import numpy
import torch
import torchaudio
import whisper
import argostranslate.translate
from TTS.api import TTS
print("smoke test OK — numpy", numpy.__version__, "| torch", torch.__version__)
PY

# ── Código fuente ──────────────────────────────────────────────────────────────
COPY bot.py .

CMD ["python", "bot.py"]
