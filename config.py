import os
from dotenv import load_dotenv

load_dotenv()

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "AC5a52668250f7500b7ccb8f7588a1cd76")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN",  "0fa5cb8db246b4c3e64c79da8c6bf73d")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "+18126339116")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8195405662:AAE6z92H7iz8H6BJB02uhyoHfjXYtXQvj38")
TELEGRAM_API_URL   = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# App
APP_URL = os.getenv("RAILWAY_PUBLIC_DOMAIN", os.getenv("APP_URL", ""))
if APP_URL and not APP_URL.startswith("http"):
    APP_URL = f"https://{APP_URL}"
SECRET_KEY   = os.getenv("SECRET_KEY", "kor-telecom-secret-2024")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kor.db")

# Company
COMPANY_NAME         = "KOR"
COMPANY_GREETING     = "Thank you for calling KOR Telecom. Your trusted virtual number provider."
COMPANY_HOLD_MESSAGE = "Please hold while we connect your call. KOR Telecom, connecting the world."
