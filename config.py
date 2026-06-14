import os
from dotenv import load_dotenv

load_dotenv()

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "AC7212c66b518d626848e23485591af681")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "33368fd2ce95ad7ccb232833a98dac9e")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "+17638786908")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8195405662:AAE6z92H7iz8H6BJB02uhyoHfjXYtXQvj38")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# App
APP_URL = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
if APP_URL and not APP_URL.startswith("http"):
    APP_URL = f"https://{APP_URL}"
SECRET_KEY = os.getenv("SECRET_KEY", "kor-telecom-secret-2024")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kor.db")

# Company
COMPANY_NAME = "KOR"
COMPANY_GREETING = "Thank you for calling KOR Telecom. Your trusted virtual number provider."
COMPANY_HOLD_MESSAGE = "Please hold while we connect your call. KOR Telecom, connecting the world."
