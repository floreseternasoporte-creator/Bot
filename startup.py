#!/usr/bin/env python3
"""
KOR Telecom - Startup & Setup Script
Initializes database and configures Telegram webhook
"""
import asyncio
import httpx
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import init_db
from config import TELEGRAM_API_URL, APP_URL, TELEGRAM_BOT_TOKEN


async def setup_telegram_webhook():
    """Register the Telegram webhook with the deployed Railway URL."""
    if not APP_URL:
        print("⚠️  APP_URL not set — skipping webhook registration.")
        print("   Set RAILWAY_PUBLIC_DOMAIN or APP_URL env var and restart.")
        return

    webhook_url = f"{APP_URL}/telegram/webhook"
    print(f"📡 Setting Telegram webhook to: {webhook_url}")

    async with httpx.AsyncClient() as client:
        # Delete old webhook first
        await client.post(f"{TELEGRAM_API_URL}/deleteWebhook")

        # Set new webhook
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
            print("✅ Telegram webhook registered successfully!")
        else:
            print(f"❌ Webhook registration failed: {data}")


async def configure_twilio():
    """Auto-configure Twilio webhooks via API so calls and SMS work immediately."""
    from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER

    if not APP_URL:
        print("⚠️  APP_URL not set — skipping Twilio auto-config.")
        return

    voice_url  = f"{APP_URL}/twilio/voice"
    sms_url    = f"{APP_URL}/twilio/sms"
    status_url = f"{APP_URL}/twilio/status"

    # Find the phone number SID first
    list_url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}"
        f"/IncomingPhoneNumbers.json"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            list_url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        )
        data = resp.json()

    numbers = data.get("incoming_phone_numbers", [])
    if not numbers:
        print("❌ No Twilio numbers found. Check credentials.")
        return

    phone_sid = numbers[0]["sid"]
    update_url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}"
        f"/IncomingPhoneNumbers/{phone_sid}.json"
    )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            update_url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={
                "VoiceUrl":        voice_url,
                "VoiceMethod":     "POST",
                "SmsUrl":          sms_url,
                "SmsMethod":       "POST",
                "StatusCallback":  status_url,
            }
        )
        result = resp.json()

    if "sid" in result:
        print(f"✅ Twilio webhooks configured automatically!")
        print(f"   Voice: {voice_url}")
        print(f"   SMS:   {sms_url}")
    else:
        print(f"❌ Twilio config failed: {result}")


async def main():
    print("🚀 KOR Telecom — Starting up...")
    print("=" * 50)

    # Init database
    print("🗄️  Initializing database...")
    init_db()

    # Setup Telegram
    await setup_telegram_webhook()

    # Print Twilio config
    await configure_twilio()

    print("=" * 50)
    print("✅ KOR Telecom is ready!")
    print(f"   Dashboard: {APP_URL or 'http://localhost:8000'}/")
    print(f"   API Docs:  {APP_URL or 'http://localhost:8000'}/docs")


if __name__ == "__main__":
    asyncio.run(main())
