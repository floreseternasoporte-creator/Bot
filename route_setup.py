from fastapi import APIRouter
from fastapi.responses import JSONResponse
import httpx
import os
from config import (
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
    TELEGRAM_API_URL, COMPANY_NAME
)

router = APIRouter()


def get_app_url():
    for var in ["RAILWAY_PUBLIC_DOMAIN", "RAILWAY_STATIC_URL", "APP_URL"]:
        val = os.getenv(var, "")
        if val:
            return f"https://{val}" if not val.startswith("http") else val
    return ""


@router.get("/setup")
async def run_setup():
    """Force reconfigure all webhooks. Hit this URL after deploy."""
    app_url = get_app_url()
    results = {}

    if not app_url:
        return JSONResponse({"error": "APP_URL not detected. Set RAILWAY_PUBLIC_DOMAIN in Railway variables."}, status_code=500)

    results["app_url"] = app_url

    # --- Telegram ---
    webhook_url = f"{app_url}/telegram/webhook"
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{TELEGRAM_API_URL}/deleteWebhook")
        resp = await client.post(
            f"{TELEGRAM_API_URL}/setWebhook",
            json={"url": webhook_url, "allowed_updates": ["message", "callback_query"], "drop_pending_updates": True}
        )
        tg = resp.json()
    results["telegram"] = {"url": webhook_url, "ok": tg.get("ok"), "detail": tg}

    # --- Twilio ---
    voice_url  = f"{app_url}/twilio/voice"
    sms_url    = f"{app_url}/twilio/sms"
    status_url = f"{app_url}/twilio/status"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/IncomingPhoneNumbers.json",
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        )
        data = resp.json()

    numbers = data.get("incoming_phone_numbers", [])
    twilio_results = []

    for num in numbers:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/IncomingPhoneNumbers/{num['sid']}.json",
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                data={
                    "VoiceUrl": voice_url, "VoiceMethod": "POST",
                    "SmsUrl": sms_url,    "SmsMethod": "POST",
                    "StatusCallback": status_url,
                }
            )
            r = resp.json()
        twilio_results.append({
            "number": num["phone_number"],
            "ok": "sid" in r,
            "voice_url": voice_url,
            "sms_url": sms_url,
        })

    results["twilio"] = twilio_results
    results["status"] = "✅ All webhooks configured!" if all(t["ok"] for t in twilio_results) else "⚠️ Some failed"
    return results
