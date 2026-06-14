#!/usr/bin/env python3
import asyncio
import httpx
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import init_db
from config import (
    TELEGRAM_API_URL, TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN, COMPANY_NAME
)


def get_app_url():
    """Get the public URL — tries multiple Railway env vars."""
    for var in ["RAILWAY_PUBLIC_DOMAIN", "RAILWAY_STATIC_URL", "APP_URL"]:
        val = os.getenv(var, "")
        if val:
            return f"https://{val}" if not val.startswith("http") else val
    return ""


async def setup_telegram_webhook(app_url: str):
    webhook_url = f"{app_url}/telegram/webhook"
    print(f"📡 Setting Telegram webhook: {webhook_url}")
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{TELEGRAM_API_URL}/deleteWebhook")
        resp = await client.post(
            f"{TELEGRAM_API_URL}/setWebhook",
            json={
                "url": webhook_url,
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": True
            }
        )
        data = resp.json()
        if data.get("ok"):
            print("✅ Telegram webhook OK")
        else:
            print(f"❌ Telegram webhook failed: {data}")


async def configure_twilio(app_url: str):
    """Configure Twilio webhooks via REST API."""
    voice_url  = f"{app_url}/twilio/voice"
    sms_url    = f"{app_url}/twilio/sms"
    status_url = f"{app_url}/twilio/status"

    print(f"📞 Configuring Twilio webhooks...")
    print(f"   Voice → {voice_url}")
    print(f"   SMS   → {sms_url}")

    # Get list of phone numbers
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/IncomingPhoneNumbers.json",
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        )
        data = resp.json()

    numbers = data.get("incoming_phone_numbers", [])
    if not numbers:
        print(f"❌ No numbers found. Raw response: {data}")
        return

    print(f"   Found {len(numbers)} number(s): {[n['phone_number'] for n in numbers]}")

    # Update every number in the account
    for num in numbers:
        phone_sid = num["sid"]
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/IncomingPhoneNumbers/{phone_sid}.json",
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                data={
                    "VoiceUrl":       voice_url,
                    "VoiceMethod":    "POST",
                    "SmsUrl":         sms_url,
                    "SmsMethod":      "POST",
                    "StatusCallback": status_url,
                }
            )
            result = resp.json()

        if "sid" in result:
            print(f"✅ {num['phone_number']} → webhooks configured!")
        else:
            print(f"❌ Failed for {num['phone_number']}: {result}")


async def main():
    print("=" * 50)
    print(f"🚀 {COMPANY_NAME} Telecom — Starting up")
    print("=" * 50)

    # Init DB
    init_db()
    print("✅ Database ready")

    # Get app URL — wait a moment for Railway env to settle
    time.sleep(2)
    app_url = get_app_url()

    if not app_url:
        print("⚠️  Could not detect public URL.")
        print("   Make sure RAILWAY_PUBLIC_DOMAIN is set in Railway Variables.")
        print("   Or add APP_URL=https://your-app.railway.app manually.")
        print("   Skipping webhook setup — app will still start.")
    else:
        print(f"🌐 Public URL: {app_url}")
        await setup_telegram_webhook(app_url)
        await configure_twilio(app_url)

    print("=" * 50)
    print(f"✅ {COMPANY_NAME} Telecom is ready!")
    print(f"   Dashboard : {app_url or 'http://localhost:8000'}/")
    print(f"   API Docs  : {app_url or 'http://localhost:8000'}/docs")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
