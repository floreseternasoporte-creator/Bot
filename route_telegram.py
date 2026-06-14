from fastapi import APIRouter, Request, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from database import get_db, VirtualNumber, ReceivedCode, TelegramUser, SessionLocal
from config import TELEGRAM_API_URL, TELEGRAM_BOT_TOKEN, COMPANY_NAME
import httpx
import re
from datetime import datetime

router = APIRouter()

COUNTRIES = {
    "us": {"name": "United States", "code": "+1", "flag": "🇺🇸"},
    "uk": {"name": "United Kingdom", "code": "+44", "flag": "🇬🇧"},
    "ca": {"name": "Canada", "code": "+1", "flag": "🇨🇦"},
    "de": {"name": "Germany", "code": "+49", "flag": "🇩🇪"},
    "fr": {"name": "France", "code": "+33", "flag": "🇫🇷"},
    "es": {"name": "Spain", "code": "+34", "flag": "🇪🇸"},
    "mx": {"name": "Mexico", "code": "+52", "flag": "🇲🇽"},
    "br": {"name": "Brazil", "code": "+55", "flag": "🇧🇷"},
    "ar": {"name": "Argentina", "code": "+54", "flag": "🇦🇷"},
    "co": {"name": "Colombia", "code": "+57", "flag": "🇨🇴"},
    "au": {"name": "Australia", "code": "+61", "flag": "🇦🇺"},
    "jp": {"name": "Japan", "code": "+81", "flag": "🇯🇵"},
    "cn": {"name": "China", "code": "+86", "flag": "🇨🇳"},
    "in": {"name": "India", "code": "+91", "flag": "🇮🇳"},
    "ru": {"name": "Russia", "code": "+7", "flag": "🇷🇺"},
    "it": {"name": "Italy", "code": "+39", "flag": "🇮🇹"},
    "nl": {"name": "Netherlands", "code": "+31", "flag": "🇳🇱"},
    "se": {"name": "Sweden", "code": "+46", "flag": "🇸🇪"},
    "ng": {"name": "Nigeria", "code": "+234", "flag": "🇳🇬"},
    "za": {"name": "South Africa", "code": "+27", "flag": "🇿🇦"},
}


async def send_telegram_message(chat_id: str, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)
    except Exception as e:
        print(f"❌ Error sending Telegram message to {chat_id}: {e}")


async def send_main_menu(chat_id: str, name: str = ""):
    greeting = f"Hey {name}! " if name else ""
    text = (
        f"📡 <b>{COMPANY_NAME} Telecom</b>\n\n"
        f"{greeting}Welcome to KOR — your virtual phone number platform.\n\n"
        "What would you like to do?"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "🌍 Get a Virtual Number", "callback_data": "get_number"}],
            [{"text": "📋 My Numbers", "callback_data": "my_numbers"}],
            [{"text": "📨 My Codes", "callback_data": "my_codes"}],
            [{"text": "ℹ️ Help", "callback_data": "help"}],
        ]
    }
    await send_telegram_message(chat_id, text, reply_markup=keyboard)


async def send_country_selection(chat_id: str):
    text = "🌍 <b>Select a country for your virtual number:</b>"
    buttons = []
    country_list = list(COUNTRIES.items())
    for i in range(0, len(country_list), 2):
        row = []
        for key, val in country_list[i:i+2]:
            row.append({
                "text": f"{val['flag']} {val['name']} ({val['code']})",
                "callback_data": f"country_{key}"
            })
        buttons.append(row)
    buttons.append([{"text": "⬅️ Back", "callback_data": "menu"}])
    keyboard = {"inline_keyboard": buttons}
    await send_telegram_message(chat_id, text, reply_markup=keyboard)


def generate_virtual_number(country_code: str) -> str:
    """Generate a realistic virtual number for the selected country."""
    import random
    info = COUNTRIES.get(country_code, {})
    prefix = info.get("code", "+1")

    if prefix == "+1":
        area = random.randint(200, 999)
        num = random.randint(1000000, 9999999)
        return f"{prefix}{area}{num}"
    elif prefix == "+44":
        num = random.randint(100000000, 999999999)
        return f"{prefix}7{num}"
    elif prefix in ["+49", "+33", "+34", "+39", "+31", "+46"]:
        num = random.randint(100000000, 999999999)
        return f"{prefix}{num}"
    else:
        num = random.randint(1000000000, 9999999999)
        return f"{prefix}{num}"


@router.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """Webhook — opens its own DB session so the background task isn't bound
    to a request-scoped session that closes before the task runs."""
    try:
        data = await request.json()
    except Exception:
        return {"ok": True}
    background_tasks.add_task(process_update_safe, data)
    return {"ok": True}


async def process_update_safe(data: dict):
    """Opens a fresh DB session for the background task."""
    db = SessionLocal()
    try:
        await process_update(data, db)
    except Exception as e:
        print(f"❌ Error processing Telegram update: {e}")
    finally:
        db.close()


async def process_update(data: dict, db: Session):
    # Handle callback queries (button presses)
    if "callback_query" in data:
        cb = data["callback_query"]
        chat_id = str(cb["message"]["chat"]["id"])
        user_data = cb["from"]
        action = cb["data"]

        ensure_user(db, user_data)

        if action == "menu":
            await send_main_menu(chat_id, user_data.get("first_name", ""))
        elif action == "get_number":
            await send_country_selection(chat_id)
        elif action.startswith("country_"):
            country_key = action.replace("country_", "")
            await handle_create_number(chat_id, str(user_data["id"]), country_key, db)
        elif action == "my_numbers":
            await handle_my_numbers(chat_id, str(user_data["id"]), db)
        elif action == "my_codes":
            await handle_my_codes(chat_id, str(user_data["id"]), db)
        elif action == "help":
            await handle_help(chat_id)
        elif action.startswith("codes_"):
            number = action.replace("codes_", "")
            await handle_codes_for_number(chat_id, number, db)
        elif action.startswith("delete_"):
            number = action.replace("delete_", "")
            await handle_delete_number(chat_id, str(user_data["id"]), number, db)

        # Answer callback to remove loading state
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{TELEGRAM_API_URL}/answerCallbackQuery",
                                  json={"callback_query_id": cb["id"]})
        except Exception as e:
            print(f"❌ Error answering callback: {e}")

    # Handle text messages
    elif "message" in data:
        msg = data["message"]
        chat_id = str(msg["chat"]["id"])
        user_data = msg.get("from", {})
        text = msg.get("text", "")

        ensure_user(db, user_data)

        if text.startswith("/start"):
            await send_main_menu(chat_id, user_data.get("first_name", ""))
        elif text.startswith("/menu"):
            await send_main_menu(chat_id, user_data.get("first_name", ""))
        elif text.startswith("/numbers"):
            await handle_my_numbers(chat_id, str(user_data["id"]), db)
        elif text.startswith("/codes"):
            await handle_my_codes(chat_id, str(user_data["id"]), db)
        elif text.startswith("/help"):
            await handle_help(chat_id)
        else:
            await send_main_menu(chat_id, user_data.get("first_name", ""))


def ensure_user(db: Session, user_data: dict):
    user_id = str(user_data.get("id", ""))
    if not user_id:
        return
    existing = db.query(TelegramUser).filter(TelegramUser.telegram_id == user_id).first()
    if not existing:
        new_user = TelegramUser(
            telegram_id=user_id,
            username=user_data.get("username"),
            first_name=user_data.get("first_name"),
        )
        db.add(new_user)
        db.commit()


async def handle_create_number(chat_id: str, user_id: str, country_key: str, db: Session):
    info = COUNTRIES.get(country_key)
    if not info:
        await send_telegram_message(chat_id, "❌ Country not found.")
        return

    virtual_number = generate_virtual_number(country_key)

    # Check if already exists (unlikely but safe)
    while db.query(VirtualNumber).filter(VirtualNumber.phone_number == virtual_number).first():
        virtual_number = generate_virtual_number(country_key)

    new_number = VirtualNumber(
        phone_number=virtual_number,
        country_code=info["code"],
        country_name=info["name"],
        user_id=user_id,
        is_active=True,
        created_at=datetime.utcnow(),
    )
    db.add(new_number)
    db.commit()

    text = (
        f"✅ <b>Virtual Number Created!</b>\n\n"
        f"📱 <b>Number:</b> <code>{virtual_number}</code>\n"
        f"🌍 <b>Country:</b> {info['flag']} {info['name']}\n"
        f"📡 <b>Status:</b> Active\n\n"
        f"Use this number to receive SMS codes. "
        f"Codes will be sent here automatically when received.\n\n"
        f"<i>KOR Telecom — Connecting the World 🌐</i>"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "📋 My Numbers", "callback_data": "my_numbers"}],
            [{"text": "🌍 Get Another Number", "callback_data": "get_number"}],
            [{"text": "⬅️ Main Menu", "callback_data": "menu"}],
        ]
    }
    await send_telegram_message(chat_id, text, reply_markup=keyboard)


async def handle_my_numbers(chat_id: str, user_id: str, db: Session):
    numbers = db.query(VirtualNumber).filter(
        VirtualNumber.user_id == user_id,
        VirtualNumber.is_active == True
    ).all()

    if not numbers:
        text = "📋 <b>Your Numbers</b>\n\nYou don't have any active numbers yet."
        keyboard = {
            "inline_keyboard": [
                [{"text": "🌍 Get a Number", "callback_data": "get_number"}],
                [{"text": "⬅️ Main Menu", "callback_data": "menu"}],
            ]
        }
        await send_telegram_message(chat_id, text, reply_markup=keyboard)
        return

    text = f"📋 <b>Your Active Numbers ({len(numbers)})</b>\n\n"
    buttons = []
    for num in numbers:
        flag = "🌍"
        for k, v in COUNTRIES.items():
            if v["code"] == num.country_code:
                flag = v["flag"]
                break
        text += f"{flag} <code>{num.phone_number}</code> — {num.country_name}\n"
        buttons.append([
            {"text": f"📨 Codes for {num.phone_number}", "callback_data": f"codes_{num.phone_number}"},
            {"text": "🗑️ Delete", "callback_data": f"delete_{num.phone_number}"},
        ])

    buttons.append([{"text": "🌍 Get Another Number", "callback_data": "get_number"}])
    buttons.append([{"text": "⬅️ Main Menu", "callback_data": "menu"}])
    keyboard = {"inline_keyboard": buttons}
    await send_telegram_message(chat_id, text, reply_markup=keyboard)


async def handle_my_codes(chat_id: str, user_id: str, db: Session):
    user_numbers = db.query(VirtualNumber).filter(VirtualNumber.user_id == user_id).all()
    number_list = [n.phone_number for n in user_numbers]

    if not number_list:
        await send_telegram_message(chat_id,
            "📨 <b>Received Codes</b>\n\nYou have no numbers yet. Get one first!")
        return

    codes = db.query(ReceivedCode).filter(
        ReceivedCode.phone_number.in_(number_list)
    ).order_by(ReceivedCode.received_at.desc()).limit(20).all()

    if not codes:
        text = "📨 <b>Received Codes</b>\n\nNo codes received yet. Codes will appear here automatically."
    else:
        text = f"📨 <b>Received Codes (last {len(codes)})</b>\n\n"
        for code in codes:
            msg_preview = code.full_message[:80] if code.full_message else ""
            suffix = "..." if len(code.full_message or "") > 80 else ""
            text += (
                f"📱 <code>{code.phone_number}</code>\n"
                f"🔑 Code: <b>{code.code}</b>\n"
                f"💬 Message: {msg_preview}{suffix}\n"
                f"🕐 {code.received_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            )

    keyboard = {"inline_keyboard": [[{"text": "⬅️ Main Menu", "callback_data": "menu"}]]}
    await send_telegram_message(chat_id, text, reply_markup=keyboard)


async def handle_codes_for_number(chat_id: str, number: str, db: Session):
    codes = db.query(ReceivedCode).filter(
        ReceivedCode.phone_number == number
    ).order_by(ReceivedCode.received_at.desc()).limit(10).all()

    if not codes:
        text = f"📨 <b>Codes for {number}</b>\n\nNo codes received yet."
    else:
        text = f"📨 <b>Codes for <code>{number}</code></b>\n\n"
        for code in codes:
            text += (
                f"🔑 <b>{code.code}</b>\n"
                f"From: {code.from_number}\n"
                f"📝 {code.full_message}\n"
                f"🕐 {code.received_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            )

    keyboard = {"inline_keyboard": [
        [{"text": "⬅️ My Numbers", "callback_data": "my_numbers"}],
        [{"text": "🏠 Main Menu", "callback_data": "menu"}],
    ]}
    await send_telegram_message(chat_id, text, reply_markup=keyboard)


async def handle_delete_number(chat_id: str, user_id: str, number: str, db: Session):
    num = db.query(VirtualNumber).filter(
        VirtualNumber.phone_number == number,
        VirtualNumber.user_id == user_id
    ).first()
    if num:
        num.is_active = False
        db.commit()
        await send_telegram_message(chat_id, f"🗑️ Number <code>{number}</code> has been deleted.")
    else:
        await send_telegram_message(chat_id, "❌ Number not found.")
    await handle_my_numbers(chat_id, user_id, db)


async def handle_help(chat_id: str):
    text = (
        f"ℹ️ <b>{COMPANY_NAME} Telecom — Help</b>\n\n"
        "📱 <b>Get a Virtual Number</b>\n"
        "Choose from 20+ countries. Use the number to verify apps and receive SMS codes.\n\n"
        "📋 <b>My Numbers</b>\n"
        "View all your active virtual numbers.\n\n"
        "📨 <b>My Codes</b>\n"
        "See all verification codes received on your numbers.\n\n"
        "📞 <b>Calls</b>\n"
        "Incoming calls are answered automatically with a professional KOR greeting.\n\n"
        "<b>Commands:</b>\n"
        "/start — Main menu\n"
        "/numbers — Your numbers\n"
        "/codes — Received codes\n"
        "/help — This message\n\n"
        "<i>KOR Telecom — Virtual Numbers Worldwide 🌐</i>"
    )
    keyboard = {"inline_keyboard": [[{"text": "⬅️ Main Menu", "callback_data": "menu"}]]}
    await send_telegram_message(chat_id, text, reply_markup=keyboard)


async def notify_user_of_code(user_id: str, phone_number: str, from_number: str,
                               code: str, full_message: str):
    """Called when a new SMS code arrives — push notification to Telegram user."""
    text = (
        f"🔔 <b>New Verification Code!</b>\n\n"
        f"📱 Number: <code>{phone_number}</code>\n"
        f"📤 From: {from_number}\n"
        f"🔑 Code: <b><code>{code}</code></b>\n"
        f"💬 Message: {full_message}\n\n"
        f"<i>KOR Telecom</i>"
    )
    await send_telegram_message(user_id, text)
