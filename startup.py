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
    """Print Twilio webhook URLs to configure."""
    if not APP_URL:
        print("⚠️  Set these in your Twilio console once Railway URL is known:")
        print("   Voice URL: https://YOUR-RAILWAY-URL/twilio/voice")
        print("   SMS URL:   https://YOUR-RAILWAY-URL/twilio/sms")
        return

    print("\n📞 Configure these URLs in your Twilio console:")
    print(f"   Voice URL (HTTP POST): {APP_URL}/twilio/voice")
    print(f"   SMS URL   (HTTP POST): {APP_URL}/twilio/sms")
    print(f"   Status CB (HTTP POST): {APP_URL}/twilio/status")
    print("\n   Go to: https://console.twilio.com/us1/develop/phone-numbers/manage/active")


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
