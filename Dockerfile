FROM python:3.11-slim-buster

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

ENV TELEGRAM_TOKEN="your_telegram_token_here"
ENV OPENAI_API_KEY="your_openai_api_key_here"

CMD ["python", "bot.py"]
