from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import Response
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session
from database import get_db, VirtualNumber, ReceivedCode, CallLog
from config import COMPANY_NAME, TWILIO_PHONE_NUMBER
from route_telegram import notify_user_of_code
import re
import httpx
from datetime import datetime

router = APIRouter()

# Voz española de Amazon Polly — Lupe (es-US, la más natural)
VOICE = "Polly.Lupe"
LANG  = "es-US"

# Música de espera corporativa gratuita (Twilio CDN)
HOLD_MUSIC = "https://com.twilio.sounds.music.s3.amazonaws.com/MARKOVICHP_-_A_Wonderful_Feeling.mp3"


def tts(text: str) -> str:
    """Wrap text in Say tag with Spanish Lupe voice."""
    return f'<Say voice="{VOICE}" language="{LANG}">{text}</Say>'


def extract_code(message: str) -> str:
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
    message_lower = message.lower()
    services = {
        "WhatsApp": ["whatsapp"], "Google": ["google"],
        "Facebook": ["facebook", "fb"], "Instagram": ["instagram"],
        "Twitter": ["twitter", "x.com"], "Telegram": ["telegram"],
        "TikTok": ["tiktok"], "Uber": ["uber"], "Amazon": ["amazon"],
        "Apple": ["apple"], "Microsoft": ["microsoft"],
        "PayPal": ["paypal"], "Snapchat": ["snapchat"], "Netflix": ["netflix"],
    }
    for service, keywords in services.items():
        for kw in keywords:
            if kw in message_lower:
                return service
    return "Unknown"


async def _notify_background(user_id: str, phone_number: str, from_number: str,
                              code: str, full_message: str):
    """Helper that runs notify_user_of_code safely in a background task."""
    try:
        await notify_user_of_code(
            user_id=user_id,
            phone_number=phone_number,
            from_number=from_number,
            code=code,
            full_message=full_message
        )
    except Exception as e:
        print(f"❌ Error notifying user {user_id}: {e}")


@router.post("/voice")
async def handle_incoming_call(
    request: Request,
    background_tasks: BackgroundTasks,
    To: str = Form(default=""),
    From: str = Form(default=""),
    CallSid: str = Form(default=""),
    db: Session = Depends(get_db)
):
    # Log the call
    call_log = CallLog(
        phone_number=To or TWILIO_PHONE_NUMBER,
        from_number=From,
        call_sid=CallSid or f"manual_{datetime.utcnow().timestamp()}",
        status="received",
        created_at=datetime.utcnow(),
    )
    db.add(call_log)
    db.commit()

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{HOLD_MUSIC}</Play>
    <Pause length="1"/>
    {tts("Gracias por llamar a K O R Telecom.")}
    {tts("Su proveedor de confianza para números telefónicos virtuales en todo el mundo.")}
    <Pause length="1"/>
    {tts("Por favor escuche las siguientes opciones.")}
    <Pause length="1"/>
    <Gather numDigits="1" action="/twilio/voice/menu" method="POST" timeout="10">
        {tts("Para información de su cuenta, presione uno.")}
        {tts("Para soporte técnico, presione dos.")}
        {tts("Para hablar con un representante, presione tres.")}
        {tts("Para repetir este menú, presione nueve.")}
        {tts("Por favor haga su selección ahora.")}
    </Gather>
    {tts("No recibimos su selección. Gracias por llamar a K O R Telecom. Hasta luego.")}
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.post("/voice/menu")
async def handle_ivr_menu(
    request: Request,
    background_tasks: BackgroundTasks,
    Digits: str = Form(default=""),
    db: Session = Depends(get_db)
):
    if Digits == "1":
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{HOLD_MUSIC}</Play>
    {tts("Ha seleccionado información de cuenta.")}
    <Pause length="1"/>
    {tts("Para consultar su cuenta, visítenos en nuestro sitio web o contáctenos a través de nuestro bot de Telegram disponible las veinticuatro horas del día.")}
    <Pause length="1"/>
    {tts("Gracias por elegir K O R Telecom. Que tenga un excelente día.")}
    <Hangup/>
</Response>"""

    elif Digits == "2":
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{HOLD_MUSIC}</Play>
    {tts("Ha seleccionado soporte técnico.")}
    <Pause length="1"/>
    {tts("Nuestro equipo de soporte está disponible las veinticuatro horas, los siete días de la semana a través de nuestro bot de Telegram.")}
    {tts("Un agente le atenderá a la brevedad posible.")}
    <Pause length="1"/>
    {tts("Gracias por llamar a K O R Telecom.")}
    <Hangup/>
</Response>"""

    elif Digits == "3":
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    {tts("Ha seleccionado hablar con un representante.")}
    <Pause length="1"/>
    {tts("Por favor permanezca en línea. Su llamada es muy importante para nosotros.")}
    <Play loop="2">{HOLD_MUSIC}</Play>
    {tts("Todos nuestros agentes están ocupados en este momento. Por favor contáctenos a través de Telegram para una atención más rápida.")}
    <Pause length="1"/>
    {tts("Gracias por su paciencia. Hasta luego.")}
    <Hangup/>
</Response>"""

    elif Digits == "9":
        return await handle_incoming_call(
            request=request,
            background_tasks=background_tasks,
            To="", From="", CallSid="", db=db
        )

    else:
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    {tts("Opción no válida.")}
    <Pause length="1"/>
    {tts("Gracias por llamar a K O R Telecom. Hasta luego.")}
    <Hangup/>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/sms")
async def handle_incoming_sms(
    request: Request,
    background_tasks: BackgroundTasks,
    To: str = Form(default=""),
    From: str = Form(default=""),
    Body: str = Form(default=""),
    db: Session = Depends(get_db)
):
    code = extract_code(Body)
    service = detect_service(From, Body)

    virtual_num = db.query(VirtualNumber).filter(
        VirtualNumber.phone_number == To
    ).first()

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

    # Use BackgroundTasks instead of asyncio.create_task to avoid event loop errors
    if virtual_num and virtual_num.user_id:
        background_tasks.add_task(
            _notify_background,
            user_id=virtual_num.user_id,
            phone_number=To,
            from_number=From,
            code=code,
            full_message=Body
        )

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>KOR Telecom: Tu mensaje fue recibido y reenviado. Código: {code}</Message>
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
    call = db.query(CallLog).filter(CallLog.call_sid == CallSid).first()
    if call:
        call.status = CallStatus
        call.duration = int(CallDuration) if CallDuration.isdigit() else 0
        db.commit()
    return {"ok": True}
