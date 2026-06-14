from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import Response
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session
from database import get_db, VirtualNumber, ReceivedCode, CallLog, TelegramUser, SessionLocal
from config import COMPANY_NAME, TWILIO_PHONE_NUMBER
from route_telegram import notify_user_of_code, generate_virtual_number, COUNTRIES
import re
from datetime import datetime

router = APIRouter()

# ──────────────────────────────────────────────
# VOICES  (Polly TTS — Twilio supports these)
# ──────────────────────────────────────────────
# Main IVR / professional voice — male, US Spanish
VOICE_MAIN  = "Polly.Miguel"   # Male, es-US
LANG_MAIN   = "es-US"

# Representative "agent" voice — also male but warmer
VOICE_AGENT = "Polly.Miguel"
LANG_AGENT  = "es-US"

# Hold music — royalty-free, hosted on Twilio's CDN
HOLD_MUSIC = "https://com.twilio.sounds.music.s3.amazonaws.com/MARKOVICHP_-_A_Wonderful_Feeling.mp3"

# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────
def tts(text: str, voice: str = VOICE_MAIN, lang: str = LANG_MAIN) -> str:
    return f'<Say voice="{voice}" language="{lang}">{text}</Say>'

def agent(text: str) -> str:
    """Agent / representative voice."""
    return tts(text, VOICE_AGENT, LANG_AGENT)

def hold(loop: int = 1) -> str:
    return f'<Play loop="{loop}">{HOLD_MUSIC}</Play>'

def gather(action: str, num_digits: int = 1, timeout: int = 8, finish_on_key: str = "") -> tuple:
    """Returns (open_tag, close_tag)."""
    fok = f' finishOnKey="{finish_on_key}"' if finish_on_key else ""
    return (
        f'<Gather numDigits="{num_digits}" action="/twilio/voice/{action}" method="POST" timeout="{timeout}"{fok}>',
        '</Gather>'
    )

def xml_wrap(body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n{body}\n</Response>'

def response(body: str):
    return Response(content=xml_wrap(body), media_type="application/xml")


# ──────────────────────────────────────────────
# EXTRACT / DETECT HELPERS
# ──────────────────────────────────────────────
def extract_code(message: str) -> str:
    patterns = [
        r'\b(\d{6})\b', r'\b(\d{5})\b', r'\b(\d{4})\b',
        r'code[:\s]+([A-Z0-9]{4,8})', r'is[:\s]+([A-Z0-9]{4,8})',
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
    try:
        await notify_user_of_code(
            user_id=user_id, phone_number=phone_number,
            from_number=from_number, code=code, full_message=full_message
        )
    except Exception as e:
        print(f"❌ Error notifying user {user_id}: {e}")


# ──────────────────────────────────────────────
# MAIN MENU
# ──────────────────────────────────────────────
def main_menu_twiml() -> str:
    go, gc = gather("menu")
    body = f"""
    {tts("Bienvenido a K O R Telecom.")}
    {tts("Su proveedor líder de números telefónicos virtuales en más de veinte países.")}
    <Pause length="1"/>
    {go}
        {tts("Por favor escuche las siguientes opciones.")}
        <Pause length="1"/>
        {tts("Para obtener un nuevo número virtual, presione uno.")}
        {tts("Para consultar sus números activos y códigos recibidos, presione dos.")}
        {tts("Para información de cuenta y facturación, presione tres.")}
        {tts("Para soporte técnico, presione cuatro.")}
        {tts("Para hablar con un representante de K O R, presione cero.")}
        {tts("Para repetir estas opciones, presione nueve.")}
        <Pause length="2"/>
    {gc}
    {tts("No recibimos ninguna selección.")}
    {tts("Gracias por llamar a K O R Telecom. Hasta pronto.")}
    <Hangup/>
"""
    return xml_wrap(body)


# ──────────────────────────────────────────────
# ENDPOINT — Incoming call
# ──────────────────────────────────────────────
@router.post("/voice")
async def handle_incoming_call(
    request: Request,
    background_tasks: BackgroundTasks,
    To: str = Form(default=""),
    From: str = Form(default=""),
    CallSid: str = Form(default=""),
    db: Session = Depends(get_db)
):
    call_log = CallLog(
        phone_number=To or TWILIO_PHONE_NUMBER,
        from_number=From,
        call_sid=CallSid or f"auto_{datetime.utcnow().timestamp()}",
        status="received",
        created_at=datetime.utcnow(),
    )
    db.add(call_log)
    db.commit()
    return Response(content=main_menu_twiml(), media_type="application/xml")


# ──────────────────────────────────────────────
# ENDPOINT — Main menu handler
# ──────────────────────────────────────────────
@router.post("/voice/menu")
async def handle_ivr_menu(
    request: Request,
    background_tasks: BackgroundTasks,
    Digits: str = Form(default=""),
    db: Session = Depends(get_db)
):
    # ── 1: Get a new virtual number ──
    if Digits == "1":
        go, gc = gather("menu-get-number")
        body = f"""
        {tts("Ha seleccionado obtener un nuevo número virtual.")}
        <Pause length="1"/>
        {tts("K O R Telecom le ofrece números en más de veinte países, incluyendo Estados Unidos, Reino Unido, Canadá, México, España, Alemania, Francia, y muchos más.")}
        <Pause length="1"/>
        {tts("Estos números le permiten recibir mensajes de texto y códigos de verificación de cualquier servicio como WhatsApp, Google, Instagram, Facebook, TikTok, y muchos otros.")}
        <Pause length="1"/>
        {go}
            {tts("Para obtener un número de Estados Unidos, presione uno.")}
            {tts("Para un número del Reino Unido, presione dos.")}
            {tts("Para un número de Canadá, presione tres.")}
            {tts("Para un número de México, presione cuatro.")}
            {tts("Para un número de España, presione cinco.")}
            {tts("Para un número de Alemania, presione seis.")}
            {tts("Para más países, acceda a nuestro bot de Telegram donde tiene más de veinte opciones disponibles.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        {tts("No recibimos su selección. Volviendo al menú principal.")}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    # ── 2: My numbers & codes ──
    elif Digits == "2":
        go, gc = gather("menu-account-info")
        body = f"""
        {tts("Ha seleccionado consultar su cuenta.")}
        <Pause length="1"/>
        {go}
            {tts("Para escuchar cuántos números tiene activos, presione uno.")}
            {tts("Para escuchar los últimos códigos recibidos, presione dos.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    # ── 3: Account & billing info ──
    elif Digits == "3":
        total_numbers = db.query(VirtualNumber).filter(VirtualNumber.is_active == True).count()
        total_codes   = db.query(ReceivedCode).count()
        total_users   = db.query(TelegramUser).count()
        go, gc = gather("menu")
        body = f"""
        {tts("Información general de K O R Telecom.")}
        <Pause length="1"/>
        {tts(f"Actualmente nuestra plataforma cuenta con {total_numbers} números virtuales activos en todo el mundo.")}
        {tts(f"Hemos procesado un total de {total_codes} códigos de verificación para nuestros usuarios.")}
        {tts(f"Contamos con {total_users} usuarios registrados en nuestra plataforma.")}
        <Pause length="1"/>
        {tts("K O R Telecom es completamente gratuito y accesible a través de nuestro bot de Telegram.")}
        {tts("Puede obtener números virtuales al instante, sin necesidad de documentos ni pagos.")}
        {tts("Todos los códigos SMS son reenviados automáticamente a su Telegram en tiempo real.")}
        <Pause length="1"/>
        {go}
            {tts("Para volver al menú principal, presione uno.")}
            {tts("Para hablar con un representante, presione cero.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    # ── 4: Technical support ──
    elif Digits == "4":
        go, gc = gather("menu-support")
        body = f"""
        {tts("Ha seleccionado soporte técnico.")}
        <Pause length="1"/>
        {tts("Permítame darle información sobre los problemas más comunes.")}
        <Pause length="1"/>
        {go}
            {tts("Si no está recibiendo sus códigos SMS, presione uno.")}
            {tts("Si tiene problemas para acceder al bot de Telegram, presione dos.")}
            {tts("Si su número no está funcionando, presione tres.")}
            {tts("Para hablar con un representante en vivo, presione cero.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    # ── 0: Virtual representative ──
    elif Digits == "0":
        body = f"""
        {tts("Por favor espere. Le estamos conectando con un representante de K O R Telecom.")}
        <Pause length="1"/>
        {hold(1)}
        {agent("Gracias por su paciencia. Buenos días, le atiende Miguel, representante de K O R Telecom.")}
        <Pause length="1"/>
        {agent("Es un placer hablar con usted. ¿En qué puedo ayudarle el día de hoy?")}
        <Pause length="1"/>
        {agent("K O R Telecom ofrece números telefónicos virtuales en más de veinte países para recibir mensajes de verificación de cualquier aplicación o servicio en línea.")}
        <Pause length="1"/>
        {agent("Para obtener su número virtual, simplemente busque nuestro bot en Telegram. El proceso toma menos de treinta segundos y es completamente gratuito.")}
        <Pause length="1"/>
        {agent("Una vez que tenga su número, podrá usarlo para verificar WhatsApp, Google, Instagram, Facebook, TikTok, Amazon, y muchas aplicaciones más.")}
        <Pause length="1"/>
        {agent("Todos los códigos que reciba en su número virtual serán reenviados automáticamente a su cuenta de Telegram en tiempo real.")}
        <Pause length="1"/>
        {agent("Si tiene algún problema técnico, le recomiendo verificar que el bot de Telegram esté activo y que su número virtual no haya expirado.")}
        <Pause length="1"/>
        {agent("También puede visitar nuestro panel de administración en línea para ver todos sus números activos y los códigos recibidos.")}
        <Pause length="1"/>
        {agent("Ha sido un placer atenderle. Recuerde que puede comunicarse con nosotros en cualquier momento a través de Telegram. Que tenga un excelente día. Hasta pronto.")}
        <Hangup/>
"""
        return response(body)

    # ── 9: Repeat ──
    elif Digits == "9":
        return Response(content=main_menu_twiml(), media_type="application/xml")

    else:
        body = f"""
        {tts("Opción no válida.")}
        <Pause length="1"/>
        {tts("Por favor intente de nuevo.")}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)


# ──────────────────────────────────────────────
# ENDPOINT — Get number submenu
# ──────────────────────────────────────────────
@router.post("/voice/menu-get-number")
async def menu_get_number(
    request: Request,
    Digits: str = Form(default=""),
    From: str = Form(default=""),
    db: Session = Depends(get_db)
):
    country_map = {
        "1": "us", "2": "uk", "3": "ca",
        "4": "mx", "5": "es", "6": "de",
    }

    if Digits == "9":
        return Response(content=main_menu_twiml(), media_type="application/xml")

    country_key = country_map.get(Digits)
    if not country_key:
        body = f"""
        {tts("Opción no válida. Volviendo al menú principal.")}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    info = COUNTRIES.get(country_key)
    if not info:
        body = f"""
        {tts("Lo sentimos, ese país no está disponible en este momento. Volviendo al menú principal.")}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    # Generate a virtual number and save it linked to the caller's phone
    virtual_number = generate_virtual_number(country_key)
    # Avoid duplicates
    while db.query(VirtualNumber).filter(VirtualNumber.phone_number == virtual_number).first():
        virtual_number = generate_virtual_number(country_key)

    # Use caller's From number as user_id so they can retrieve codes
    caller_id = From.replace("+", "").replace("-", "").strip() if From else "phone_user"

    new_number = VirtualNumber(
        phone_number=virtual_number,
        country_code=info["code"],
        country_name=info["name"],
        user_id=caller_id,
        is_active=True,
        created_at=datetime.utcnow(),
    )
    db.add(new_number)
    db.commit()

    # Spell out number digit by digit for clarity
    spelled = ". ".join(list(virtual_number.replace("+", ""))) + "."

    go, gc = gather("menu")
    body = f"""
    {tts(f"Excelente. Hemos creado un número virtual de {info['name']} para usted.")}
    <Pause length="1"/>
    {tts("Su número es el siguiente. Por favor tómelo nota.")}
    <Pause length="1"/>
    {tts(spelled)}
    <Pause length="1"/>
    {tts("Le repito el número.")}
    <Pause length="1"/>
    {tts(spelled)}
    <Pause length="1"/>
    {tts("Este número ya está activo y listo para recibir mensajes de texto y códigos de verificación.")}
    {tts("También puede ver todos sus números y códigos en nuestro bot de Telegram o en el panel web.")}
    <Pause length="1"/>
    {go}
        {tts("Para crear otro número, presione uno.")}
        {tts("Para volver al menú principal, presione nueve.")}
        <Pause length="2"/>
    {gc}
    <Redirect>/twilio/voice</Redirect>
"""
    return response(body)


# ──────────────────────────────────────────────
# ENDPOINT — Account info submenu
# ──────────────────────────────────────────────
@router.post("/voice/menu-account-info")
async def menu_account_info(
    request: Request,
    Digits: str = Form(default=""),
    From: str = Form(default=""),
    db: Session = Depends(get_db)
):
    caller_id = From.replace("+", "").replace("-", "").strip() if From else ""

    if Digits == "1":
        # Count numbers for this caller
        count = db.query(VirtualNumber).filter(
            VirtualNumber.user_id == caller_id,
            VirtualNumber.is_active == True
        ).count() if caller_id else 0

        numbers = db.query(VirtualNumber).filter(
            VirtualNumber.user_id == caller_id,
            VirtualNumber.is_active == True
        ).all() if caller_id else []

        go, gc = gather("menu")
        if count == 0:
            body = f"""
        {tts("No encontramos números activos asociados a su número de teléfono.")}
        <Pause length="1"/>
        {tts("Para obtener su primer número virtual, presione uno en el menú principal.")}
        <Pause length="1"/>
        {go}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        else:
            nums_text = ". ".join([n.phone_number for n in numbers[:3]])
            body = f"""
        {tts(f"Usted tiene {count} número{'s' if count > 1 else ''} virtual{'es' if count > 1 else ''} activo{'s' if count > 1 else ''}.")}
        <Pause length="1"/>
        {tts(f"Sus número{'s' if count > 1 else ''}: {nums_text}.")}
        <Pause length="1"/>
        {go}
            {tts("Para escuchar sus códigos recibidos, presione dos.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    elif Digits == "2":
        # Recent codes for this caller
        user_numbers = db.query(VirtualNumber).filter(
            VirtualNumber.user_id == caller_id
        ).all() if caller_id else []

        num_list = [n.phone_number for n in user_numbers]
        codes = []
        if num_list:
            codes = db.query(ReceivedCode).filter(
                ReceivedCode.phone_number.in_(num_list)
            ).order_by(ReceivedCode.received_at.desc()).limit(3).all()

        go, gc = gather("menu")
        if not codes:
            body = f"""
        {tts("No hemos recibido ningún código en sus números virtuales todavía.")}
        <Pause length="1"/>
        {tts("Los códigos aparecerán automáticamente aquí y también serán enviados a su Telegram cuando los reciba.")}
        <Pause length="1"/>
        {go}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        else:
            codes_text = ""
            for i, c in enumerate(codes, 1):
                codes_text += f"""
        {tts(f"Código número {i}: {' '.join(list(c.code))} — recibido de {c.service or 'servicio desconocido'}.")}
        <Pause length="1"/>
"""
            body = f"""
        {tts(f"Le leeré sus últimos {len(codes)} código{'s' if len(codes) > 1 else ''} recibido{'s' if len(codes) > 1 else ''}.")}
        <Pause length="1"/>
        {codes_text}
        {go}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    elif Digits == "9":
        return Response(content=main_menu_twiml(), media_type="application/xml")

    else:
        body = f"""
        {tts("Opción no válida.")}
        <Redirect>/twilio/voice/menu-account-info</Redirect>
"""
        return response(body)


# ──────────────────────────────────────────────
# ENDPOINT — Support submenu
# ──────────────────────────────────────────────
@router.post("/voice/menu-support")
async def menu_support(
    request: Request,
    Digits: str = Form(default=""),
    db: Session = Depends(get_db)
):
    go, gc = gather("menu")

    if Digits == "1":
        body = f"""
        {tts("Si no está recibiendo sus códigos S M S, le indicamos los pasos a seguir.")}
        <Pause length="1"/>
        {tts("Primero, verifique que su número virtual esté activo en el bot de Telegram.")}
        {tts("Segundo, asegúrese de que el servicio esté enviando el mensaje al número correcto.")}
        {tts("Tercero, los mensajes pueden tardar hasta dos minutos en procesarse dependiendo del operador.")}
        {tts("Si el problema persiste después de cinco minutos, le recomendamos crear un nuevo número virtual e intentar de nuevo.")}
        <Pause length="1"/>
        {go}
            {tts("Para volver al menú principal, presione nueve.")}
            {tts("Para hablar con un representante, presione cero.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""

    elif Digits == "2":
        body = f"""
        {tts("Si tiene problemas para acceder al bot de Telegram de K O R Telecom, siga estos pasos.")}
        <Pause length="1"/>
        {tts("Abra la aplicación de Telegram en su dispositivo.")}
        {tts("En la barra de búsqueda, escriba K O R Telecom Bot.")}
        {tts("Seleccione el bot verificado y presione el botón de inicio.")}
        {tts("Si el bot no responde, espere unos minutos e intente enviar el comando slash start.")}
        <Pause length="1"/>
        {go}
            {tts("Para volver al menú principal, presione nueve.")}
            {tts("Para hablar con un representante, presione cero.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""

    elif Digits == "3":
        body = f"""
        {tts("Si su número virtual no está funcionando correctamente, le explicamos las causas más comunes.")}
        <Pause length="1"/>
        {tts("Los números virtuales de K O R Telecom son funcionales para recibir mensajes S M S.")}
        {tts("Algunos servicios como WhatsApp y Telegram pueden requerir números de ciertos países específicos.")}
        {tts("Si un número no funciona con un servicio en particular, pruebe con un número de otro país.")}
        {tts("También tenga en cuenta que algunos servicios bloquean números virtuales. En ese caso, pruebe con un número de Estados Unidos o Reino Unido.")}
        <Pause length="1"/>
        {go}
            {tts("Para obtener un nuevo número, vuelva al menú principal y presione uno.")}
            {tts("Para hablar con un representante, presione cero.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""

    elif Digits == "0":
        # Transfer to representative flow
        body = f"""
        {tts("Conectando con un representante. Por favor espere.")}
        <Pause length="1"/>
        {hold(1)}
        {agent("Gracias por su paciencia. Le atiende Miguel del departamento de soporte técnico de K O R Telecom.")}
        <Pause length="1"/>
        {agent("Entiendo que tiene una consulta técnica. Con mucho gusto le ayudo.")}
        <Pause length="1"/>
        {agent("El equipo de K O R Telecom trabaja constantemente para garantizar que todos los números virtuales funcionen correctamente.")}
        {agent("Si su problema persiste, le recomendamos acceder a nuestro panel web o al bot de Telegram donde puede gestionar todos sus números y ver los códigos recibidos en tiempo real.")}
        <Pause length="1"/>
        {agent("¿Hay algo más en que pueda ayudarle? Recuerde que estamos disponibles las veinticuatro horas a través de Telegram. Que tenga un excelente día.")}
        <Hangup/>
"""

    elif Digits == "9":
        return Response(content=main_menu_twiml(), media_type="application/xml")

    else:
        body = f"""
        {tts("Opción no válida.")}
        <Redirect>/twilio/voice/menu-support</Redirect>
"""

    return response(body)


# ──────────────────────────────────────────────
# ENDPOINT — SMS
# ──────────────────────────────────────────────
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
    <Message>KOR Telecom: Código recibido: {code} | De: {service} | {Body[:100]}</Message>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


# ──────────────────────────────────────────────
# ENDPOINT — Call status callback
# ──────────────────────────────────────────────
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
