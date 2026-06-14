from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database import get_db, VirtualNumber, ReceivedCode, CallLog
from config import COMPANY_NAME, COMPANY_GREETING, COMPANY_HOLD_MESSAGE, TWILIO_PHONE_NUMBER
from route_telegram import notify_user_of_code
import re
from datetime import datetime

router = APIRouter()


def extract_code(message: str) -> str:
    """Extract verification code from SMS message."""
    patterns = [
        r'\b(\d{6})\b',
        r'\b(\d{5})\b',
        r'\b(\d{4})\b',
        r'code[:\s]+([A-Z0-9]{4,8})',
        r'is[:\s]+([A-Z0-9]{4,8})',
        r'([A-Z0-9]{6,8})',
    ]
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return match.group(1)
    return "N/A"


def detect_service(from_number: str, message: str) -> str:
    """Try to detect which service sent the code."""
    message_lower = message.lower()
    services = {
        "WhatsApp": ["whatsapp"],
        "Google": ["google"],
        "Facebook": ["facebook", "fb"],
        "Instagram": ["instagram"],
        "Twitter": ["twitter", "x.com"],
        "Telegram": ["telegram"],
        "TikTok": ["tiktok"],
        "Uber": ["uber"],
        "Amazon": ["amazon"],
        "Apple": ["apple"],
        "Microsoft": ["microsoft"],
        "PayPal": ["paypal"],
        "Snapchat": ["snapchat"],
        "Netflix": ["netflix"],
    }
    for service, keywords in services.items():
        for kw in keywords:
            if kw in message_lower:
                return service
    return "Unknown"


@router.post("/voice")
async def handle_incoming_call(
    request: Request,
    To: str = Form(default=""),
    From: str = Form(default=""),
    CallSid: str = Form(default=""),
    db: Session = Depends(get_db)
):
    """Handle incoming voice calls with KOR IVR greeting."""
    # Log the call
    call_log = CallLog(
        phone_number=To or TWILIO_PHONE_NUMBER,
        from_number=From,
        call_sid=CallSid,
        status="received",
        created_at=datetime.utcnow(),
    )
    db.add(call_log)
    db.commit()

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna" language="en-US">
        {COMPANY_GREETING}
    </Say>
    <Pause length="1"/>
    <Say voice="Polly.Joanna" language="en-US">
        For account information, press 1.
        For technical support, press 2.
        To speak with a representative, press 3.
        To hear this menu again, press 9.
    </Say>
    <Gather numDigits="1" action="/twilio/voice/menu" method="POST" timeout="10">
        <Say voice="Polly.Joanna" language="en-US">Please make your selection now.</Say>
    </Gather>
    <Say voice="Polly.Joanna" language="en-US">
        We did not receive your selection. Thank you for calling {COMPANY_NAME} Telecom. Goodbye.
    </Say>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.post("/voice/menu")
async def handle_ivr_menu(
    request: Request,
    Digits: str = Form(default=""),
    db: Session = Depends(get_db)
):
    """Handle IVR menu selections."""
    if Digits == "1":
        message = (
            "For account information, please visit our website at K O R telecom dot com, "
            "or contact us via our Telegram bot. "
            "Thank you for choosing KOR Telecom."
        )
    elif Digits == "2":
        message = (
            "For technical support, please send a message to our Telegram bot. "
            "Our team is available 24 hours a day, 7 days a week. "
            "Thank you for calling KOR Telecom."
        )
    elif Digits == "3":
        message = (
            "All our representatives are currently assisting other customers. "
            "Please leave a message after the tone, or contact us via Telegram for faster service. "
            "Thank you for your patience."
        )
    elif Digits == "9":
        return await handle_incoming_call(request=request, db=db)
    else:
        message = "Invalid selection. Thank you for calling KOR Telecom. Goodbye."

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna" language="en-US">{message}</Say>
    <Pause length="1"/>
    <Say voice="Polly.Joanna" language="en-US">
        {COMPANY_HOLD_MESSAGE}
    </Say>
    <Hangup/>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.post("/sms")
async def handle_incoming_sms(
    request: Request,
    To: str = Form(default=""),
    From: str = Form(default=""),
    Body: str = Form(default=""),
    db: Session = Depends(get_db)
):
    """Handle incoming SMS — extract code and notify user via Telegram."""
    code = extract_code(Body)
    service = detect_service(From, Body)

    # Find which virtual number received this
    virtual_num = db.query(VirtualNumber).filter(
        VirtualNumber.phone_number == To
    ).first()

    # Save code to DB
    new_code = ReceivedCode(
        phone_number=To,
        from_number=From,
        code=code,
        full_message=Body,
        service=service,
        received_at=datetime.utcnow(),
    )
    db.add(new_code)
    db.commit()

    # Notify the user via Telegram
    if virtual_num and virtual_num.user_id:
        import asyncio
        asyncio.create_task(
            notify_user_of_code(
                user_id=virtual_num.user_id,
                phone_number=To,
                from_number=From,
                code=code,
                full_message=Body
            )
        )

    # Auto-reply SMS
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>KOR Telecom: Your message has been received and forwarded. Code: {code}</Message>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.post("/status")
async def call_status_callback(
    request: Request,
    CallSid: str = Form(default=""),
    CallStatus: str = Form(default=""),
    CallDuration: str = Form(default="0"),
    db: Session = Depends(get_db)
):
    """Update call log with final status."""
    call = db.query(CallLog).filter(CallLog.call_sid == CallSid).first()
    if call:
        call.status = CallStatus
        call.duration = int(CallDuration) if CallDuration.isdigit() else 0
        db.commit()
    return {"ok": True}
