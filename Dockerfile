FROM python:3.11-slim-buster

WORKDIR /app

# Instalar ffmpeg y otras dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

ENV TELEGRAM_TOKEN="your_telegram_token_here"

CMD ["python", "bot.py"]
