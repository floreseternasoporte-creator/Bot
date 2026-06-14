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
# VOICES  (Polly TTS — Amazon Neural voices via Twilio)
# ──────────────────────────────────────────────
# IVR principal — voz femenina profesional, español US
VOICE_MAIN  = "Polly.Lupe"      # Mujer, es-US (neural)
LANG_MAIN   = "es-US"

# Representante / agente — voz masculina cálida, diferente
VOICE_AGENT = "Polly.Miguel"    # Hombre, es-US (neural)
LANG_AGENT  = "es-US"

# Voz de espera / anuncios de cola — masculina suave
VOICE_HOLD  = "Polly.Miguel"
LANG_HOLD   = "es-US"

# Música de espera — Cloudinary CDN
HOLD_MUSIC_1 = "https://res.cloudinary.com/dnpwxcquk/video/upload/v1781411029/Forever_ttqoba.mp3"
HOLD_MUSIC_2 = "https://res.cloudinary.com/dnpwxcquk/video/upload/v1781411029/Forever_ttqoba.mp3"

# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────
def tts(text: str, voice: str = VOICE_MAIN, lang: str = LANG_MAIN) -> str:
    return f'<Say voice="{voice}" language="{lang}">{text}</Say>'

def agent(text: str) -> str:
    """Voz del representante — masculina."""
    return tts(text, VOICE_AGENT, LANG_AGENT)

def hold_announce(text: str) -> str:
    """Anuncios durante la espera — voz masculina."""
    return tts(text, VOICE_HOLD, LANG_HOLD)

def hold(loop: int = 3) -> str:
    """Reproduce música de espera."""
    return f'<Play loop="{loop}">{HOLD_MUSIC_1}</Play>'

def hold_sequence() -> str:
    """Secuencia profesional de espera: música + anuncio + música (como Verizon/T-Mobile)."""
    return (
        f'<Play loop="1">{HOLD_MUSIC_1}</Play>'
        f'{hold_announce("Gracias por su paciencia. Todos nuestros representantes están ocupados. Su llamada es muy importante para nosotros. Por favor continúe en espera.")}'
        '<Pause length="1"/>'
        f'<Play loop="2">{HOLD_MUSIC_2}</Play>'
        f'{hold_announce("Le recordamos que también puede obtener asistencia inmediata a través de nuestro bot de Telegram, disponible las veinticuatro horas del día, los siete días de la semana.")}'
        '<Pause length="1"/>'
        f'<Play loop="1">{HOLD_MUSIC_1}</Play>'
    )

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
        "Apple": ["apple", "icloud"], "Microsoft": ["microsoft", "msft"],
        "PayPal": ["paypal"], "Snapchat": ["snapchat"], "Netflix": ["netflix"],
        "Airbnb": ["airbnb"], "Lyft": ["lyft"], "Discord": ["discord"],
        "LinkedIn": ["linkedin"], "Twitter/X": ["x.com", "twitter"],
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


def find_telegram_user_for_number(db: Session, phone_number: str):
    """Busca el usuario de Telegram asociado a un número virtual."""
    virtual_num = db.query(VirtualNumber).filter(
        VirtualNumber.phone_number == phone_number
    ).first()
    if not virtual_num:
        return None
    # user_id en Telegram es el telegram_id
    tg_user = db.query(TelegramUser).filter(
        TelegramUser.telegram_id == virtual_num.user_id
    ).first()
    return tg_user


# ──────────────────────────────────────────────
# MAIN MENU
# ──────────────────────────────────────────────
def main_menu_twiml() -> str:
    go, gc = gather("menu")
    body = f"""
    {tts("Bienvenido a K O R Telecom.")}
    {tts("Su proveedor de confianza de números telefónicos virtuales en más de veinte países.")}
    <Pause length="1"/>
    {tts("Para continuar en español, presione uno. To continue in English, press two.")}
    <Pause length="1"/>
    {go}
        {tts("Para obtener un número virtual nuevo, presione uno.")}
        {tts("Para consultar sus números activos y códigos recibidos, presione dos.")}
        {tts("Para información de cuenta y planes, presione tres.")}
        {tts("Para soporte técnico especializado, presione cuatro.")}
        {tts("Para preguntas frecuentes, presione cinco.")}
        {tts("Para hablar con un representante de K O R, presione cero.")}
        {tts("Para repetir este menú, presione nueve.")}
        <Pause length="2"/>
    {gc}
    {tts("No recibimos ninguna selección. Por favor intente nuevamente.")}
    <Redirect>/twilio/voice</Redirect>
"""
    return xml_wrap(body)


# ──────────────────────────────────────────────
# ENDPOINT — Llamada entrante
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
# ENDPOINT — Menú principal
# ──────────────────────────────────────────────
@router.post("/voice/menu")
async def handle_ivr_menu(
    request: Request,
    background_tasks: BackgroundTasks,
    Digits: str = Form(default=""),
    From: str = Form(default=""),
    db: Session = Depends(get_db)
):
    # ── 1: Obtener número virtual ──
    if Digits == "1":
        go, gc = gather("menu-get-number")
        body = f"""
        {tts("Ha seleccionado obtener un nuevo número virtual.")}
        <Pause length="1"/>
        {tts("K O R Telecom le ofrece números en más de veinte países, incluyendo Estados Unidos, Reino Unido, Canadá, México, España, Alemania, Francia, Italia, y muchos más.")}
        <Pause length="1"/>
        {tts("Con su número virtual puede recibir mensajes de texto y códigos de verificación de cualquier servicio como WhatsApp, Google, Instagram, Facebook, TikTok, Amazon, y muchas aplicaciones más.")}
        <Pause length="1"/>
        {go}
            {tts("Para un número de Estados Unidos, presione uno.")}
            {tts("Para un número del Reino Unido, presione dos.")}
            {tts("Para un número de Canadá, presione tres.")}
            {tts("Para un número de México, presione cuatro.")}
            {tts("Para un número de España, presione cinco.")}
            {tts("Para un número de Alemania, presione seis.")}
            {tts("Para ver más países disponibles, acceda a nuestro bot de Telegram con más de veinte opciones.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        {tts("No recibimos su selección. Volviendo al menú principal.")}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    # ── 2: Mis números y códigos ──
    elif Digits == "2":
        go, gc = gather("menu-account-info")
        body = f"""
        {tts("Ha seleccionado consultar su cuenta.")}
        <Pause length="1"/>
        {go}
            {tts("Para escuchar cuántos números tiene activos, presione uno.")}
            {tts("Para escuchar los últimos códigos de verificación recibidos, presione dos.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    # ── 3: Información de cuenta y planes ──
    elif Digits == "3":
        total_numbers = db.query(VirtualNumber).filter(VirtualNumber.is_active == True).count()
        total_codes   = db.query(ReceivedCode).count()
        total_users   = db.query(TelegramUser).count()
        go, gc = gather("menu")
        body = f"""
        {tts("Información de cuenta y planes de K O R Telecom.")}
        <Pause length="1"/>
        {tts(f"Actualmente nuestra plataforma cuenta con {total_numbers} números virtuales activos en todo el mundo.")}
        {tts(f"Hemos procesado un total de {total_codes} códigos de verificación para nuestros usuarios.")}
        {tts(f"Contamos con {total_users} usuarios registrados en nuestra plataforma global.")}
        <Pause length="1"/>
        {tts("K O R Telecom es completamente gratuito y accesible a través de nuestro bot de Telegram.")}
        {tts("Puede obtener números virtuales al instante, sin necesidad de documentos ni pagos.")}
        {tts("Todos los códigos SMS son reenviados automáticamente a su Telegram en tiempo real.")}
        <Pause length="1"/>
        {tts("Nuestros números están disponibles en más de veinte países: Estados Unidos, Reino Unido, Canadá, México, España, Alemania, Francia, Italia, Brasil, Argentina, Colombia, Australia, Japón, y muchos más.")}
        <Pause length="1"/>
        {tts("Actualmente todos nuestros servicios son sin costo. No hay planes de pago, ni suscripciones, ni cargos ocultos.")}
        <Pause length="1"/>
        {go}
            {tts("Para volver al menú principal, presione nueve.")}
            {tts("Para hablar con un representante, presione cero.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    # ── 4: Soporte técnico ──
    elif Digits == "4":
        go, gc = gather("menu-support")
        body = f"""
        {tts("Ha seleccionado soporte técnico de K O R Telecom.")}
        <Pause length="1"/>
        {tts("Nuestro equipo de soporte está disponible para ayudarle con cualquier inconveniente técnico.")}
        <Pause length="1"/>
        {go}
            {tts("Si no está recibiendo sus códigos S M S, presione uno.")}
            {tts("Si tiene problemas para acceder al bot de Telegram, presione dos.")}
            {tts("Si su número virtual no está funcionando, presione tres.")}
            {tts("Para reportar un problema o enviar comentarios, presione cuatro.")}
            {tts("Para hablar con un especialista en soporte, presione cero.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    # ── 5: Preguntas frecuentes ──
    elif Digits == "5":
        go, gc = gather("menu-faq")
        body = f"""
        {tts("Preguntas frecuentes de K O R Telecom.")}
        <Pause length="1"/>
        {go}
            {tts("Para saber qué es un número virtual, presione uno.")}
            {tts("Para saber cómo funciona K O R Telecom, presione dos.")}
            {tts("Para saber qué aplicaciones son compatibles, presione tres.")}
            {tts("Para saber cuánto tiempo duran los números, presione cuatro.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    # ── 0: Representante virtual ──
    elif Digits == "0":
        body = f"""
        {tts("Por favor espere un momento. Le estamos conectando con un representante de K O R Telecom.")}
        <Pause length="1"/>
        {tts("Su llamada es muy importante para nosotros.")}
        <Pause length="1"/>
        {hold_sequence()}
        {agent("K O R Telecom, le atiende Miguel. Buenos días, ¿en qué puedo ayudarle?")}
        <Pause length="1"/>
        {agent("Gracias por comunicarse con K O R Telecom. Es un placer asistirle el día de hoy.")}
        <Pause length="1"/>
        {agent("K O R Telecom es su plataforma de confianza para números telefónicos virtuales en más de veinte países. ¿Tiene alguna pregunta específica sobre nuestros servicios?")}
        <Pause length="2"/>
        {agent("Permítame explicarle brevemente cómo funciona nuestra plataforma.")}
        <Pause length="1"/>
        {agent("Para obtener su número virtual, simplemente busque K O R Telecom en Telegram. El proceso toma menos de treinta segundos y es completamente gratuito, sin necesidad de tarjeta de crédito ni documentos.")}
        <Pause length="1"/>
        {agent("Una vez que tenga su número, podrá usarlo para verificar WhatsApp, Google, Instagram, Facebook, TikTok, Amazon, Apple, Microsoft, PayPal, y muchas aplicaciones más.")}
        <Pause length="1"/>
        {agent("Todos los códigos que reciba en su número virtual serán reenviados automáticamente a su cuenta de Telegram en tiempo real, las veinticuatro horas del día.")}
        <Pause length="1"/>
        {agent("Si tiene algún problema técnico, le recomiendo primero verificar que el bot de Telegram esté activo y que su número virtual no haya expirado.")}
        <Pause length="1"/>
        {agent("También puede acceder a nuestro panel web para administrar todos sus números y ver los códigos recibidos de forma gráfica y organizada.")}
        <Pause length="1"/>
        {agent("Recuerde que puede comunicarse con nosotros en cualquier momento a través de Telegram donde tendrá respuesta inmediata. Ha sido un placer atenderle. Que tenga un excelente día. Hasta pronto.")}
        <Hangup/>
"""
        return response(body)

    # ── 9: Repetir ──
    elif Digits == "9":
        return Response(content=main_menu_twiml(), media_type="application/xml")

    else:
        body = f"""
        {tts("Opción no válida. Por favor intente de nuevo.")}
        <Pause length="1"/>
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)


# ──────────────────────────────────────────────
# ENDPOINT — Obtener número
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

    # Generar número virtual único
    virtual_number = generate_virtual_number(country_key)
    attempts = 0
    while db.query(VirtualNumber).filter(VirtualNumber.phone_number == virtual_number).first():
        virtual_number = generate_virtual_number(country_key)
        attempts += 1
        if attempts > 20:
            break

    # Usar número del caller como user_id (para recuperar códigos por teléfono)
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

    # Deletrear número dígito por dígito para mayor claridad
    spelled = ". ".join(list(virtual_number.replace("+", ""))) + "."

    go, gc = gather("menu-get-number-repeat", num_digits=1)
    body = f"""
    {tts(f"Excelente. Hemos creado exitosamente un número virtual de {info['name']} para usted.")}
    <Pause length="1"/>
    {tts("Su número virtual es el siguiente. Por favor tómelo nota.")}
    <Pause length="2"/>
    {tts(spelled)}
    <Pause length="2"/>
    {tts("Le repito el número una vez más.")}
    <Pause length="1"/>
    {tts(spelled)}
    <Pause length="2"/>
    {tts("Este número ya está activo y listo para recibir mensajes de texto y códigos de verificación al instante.")}
    <Pause length="1"/>
    {tts("También puede ver todos sus números y códigos recibidos en nuestro bot de Telegram o en el panel web de K O R Telecom.")}
    <Pause length="1"/>
    {go}
        {tts("Para crear otro número virtual, presione uno.")}
        {tts("Para escuchar el número nuevamente, presione dos.")}
        {tts("Para volver al menú principal, presione nueve.")}
        <Pause length="2"/>
    {gc}
    <Redirect>/twilio/voice</Redirect>
"""
    return response(body)


@router.post("/voice/menu-get-number-repeat")
async def menu_get_number_repeat(
    request: Request,
    Digits: str = Form(default=""),
    db: Session = Depends(get_db)
):
    if Digits == "1":
        go, gc = gather("menu-get-number")
        body = f"""
        {tts("Seleccione el país para su nuevo número.")}
        <Pause length="1"/>
        {go}
            {tts("Para un número de Estados Unidos, presione uno.")}
            {tts("Para un número del Reino Unido, presione dos.")}
            {tts("Para un número de Canadá, presione tres.")}
            {tts("Para un número de México, presione cuatro.")}
            {tts("Para un número de España, presione cinco.")}
            {tts("Para un número de Alemania, presione seis.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)
    elif Digits == "9":
        return Response(content=main_menu_twiml(), media_type="application/xml")
    else:
        return Response(content=main_menu_twiml(), media_type="application/xml")


# ──────────────────────────────────────────────
# ENDPOINT — Info de cuenta
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
        {tts("No encontramos números activos asociados a su número de teléfono en nuestra plataforma.")}
        <Pause length="1"/>
        {tts("Para obtener su primer número virtual gratuito, presione uno en el menú principal.")}
        {tts("Recuerde que también puede gestionar sus números desde nuestro bot de Telegram.")}
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
        {tts(f"Usted tiene {count} número{'s' if count > 1 else ''} virtual{'es' if count > 1 else ''} activo{'s' if count > 1 else ''} en K O R Telecom.")}
        <Pause length="1"/>
        {tts(f"{'Sus números son' if count > 1 else 'Su número es'}: {nums_text}.")}
        <Pause length="1"/>
        {go}
            {tts("Para escuchar sus códigos de verificación recibidos, presione dos.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""
        return response(body)

    elif Digits == "2":
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
        {tts("No hemos recibido ningún código de verificación en sus números virtuales todavía.")}
        <Pause length="1"/>
        {tts("Los códigos aparecerán automáticamente aquí y también serán enviados a su Telegram en tiempo real cuando los reciba.")}
        {tts("Asegúrese de que el número virtual correcto esté configurado en el servicio que desea verificar.")}
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
                code_spelled = " ".join(list(str(c.code)))
                codes_text += f"""
        {tts(f"Código número {i}: {code_spelled}. Recibido de {c.service or 'servicio desconocido'}.")}
        <Pause length="1"/>
"""
            body = f"""
        {tts(f"Le leeré sus últimos {len(codes)} código{'s' if len(codes) > 1 else ''} de verificación recibido{'s' if len(codes) > 1 else ''}.")}
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
# ENDPOINT — Soporte técnico
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
        {tts("Paso uno. Verifique que su número virtual esté activo en el bot de Telegram o en el panel web de K O R Telecom.")}
        <Pause length="1"/>
        {tts("Paso dos. Asegúrese de que el servicio esté enviando el mensaje al número correcto, incluyendo el código de país.")}
        <Pause length="1"/>
        {tts("Paso tres. Los mensajes pueden tardar hasta dos minutos en procesarse dependiendo del operador remitente.")}
        <Pause length="1"/>
        {tts("Paso cuatro. Revise que el bot de Telegram no esté bloqueado o en modo silencioso en su dispositivo.")}
        <Pause length="1"/>
        {tts("Si el problema persiste después de cinco minutos, le recomendamos crear un nuevo número virtual e intentar de nuevo. Algunos servicios requieren números de países específicos.")}
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
        {tts("Paso uno. Abra la aplicación de Telegram en su dispositivo móvil o computadora.")}
        <Pause length="1"/>
        {tts("Paso dos. En la barra de búsqueda superior, escriba K O R Telecom.")}
        <Pause length="1"/>
        {tts("Paso tres. Seleccione el bot verificado en los resultados y presione el botón de inicio o start.")}
        <Pause length="1"/>
        {tts("Paso cuatro. Si el bot no responde en treinta segundos, espere unos minutos e intente enviar el comando barra start nuevamente.")}
        <Pause length="1"/>
        {tts("Paso cinco. Verifique que su conexión a Internet sea estable.")}
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
        {tts("Los números virtuales de K O R Telecom están diseñados para recibir mensajes S M S de verificación.")}
        <Pause length="1"/>
        {tts("Causa uno. Algunos servicios como WhatsApp y Telegram pueden requerir números de países específicos. Pruebe con un número de Estados Unidos o Reino Unido.")}
        <Pause length="1"/>
        {tts("Causa dos. Ciertos servicios detectan y bloquean números virtuales. En ese caso, le recomendamos intentar con un número de diferente país.")}
        <Pause length="1"/>
        {tts("Causa tres. El número puede haber expirado si pasó mucho tiempo sin actividad. Genere un número nuevo desde el bot.")}
        <Pause length="1"/>
        {go}
            {tts("Para obtener un nuevo número, vuelva al menú principal y presione uno.")}
            {tts("Para hablar con un representante, presione cero.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""

    elif Digits == "4":
        body = f"""
        {tts("Muchas gracias por querer enviar sus comentarios a K O R Telecom.")}
        <Pause length="1"/>
        {tts("Para reportar un problema técnico o enviar sugerencias, le pedimos que nos contacte directamente a través del bot de Telegram.")}
        <Pause length="1"/>
        {tts("En el bot encontrará una opción de soporte donde puede describir su problema detalladamente y un agente le responderá a la brevedad posible.")}
        <Pause length="1"/>
        {tts("Sus comentarios son muy valiosos para nosotros y nos ayudan a mejorar el servicio para todos nuestros usuarios.")}
        <Pause length="1"/>
        {go}
            {tts("Para volver al menú principal, presione nueve.")}
            {tts("Para hablar con un representante, presione cero.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""

    elif Digits == "0":
        body = f"""
        {tts("Conectando con nuestro equipo de soporte técnico. Por favor espere un momento.")}
        <Pause length="1"/>
        {tts("Su llamada es importante para nosotros.")}
        <Pause length="1"/>
        {hold_sequence()}
        {agent("K O R Telecom soporte técnico, le atiende Miguel. ¿En qué puedo ayudarle?")}
        <Pause length="1"/>
        {agent("Entiendo que tiene una consulta técnica. Estoy aquí para ayudarle.")}
        <Pause length="1"/>
        {agent("El equipo de K O R Telecom trabaja constantemente para garantizar que todos los números virtuales funcionen correctamente y que los códigos lleguen en tiempo real.")}
        <Pause length="1"/>
        {agent("Si su problema persiste, le recomendamos acceder a nuestro panel web o al bot de Telegram donde puede gestionar todos sus números y visualizar los códigos recibidos en tiempo real.")}
        <Pause length="1"/>
        {agent("También puede crear un nuevo número en cuestión de segundos si el actual presenta algún inconveniente.")}
        <Pause length="1"/>
        {agent("Recuerde que estamos disponibles las veinticuatro horas a través de Telegram. Ha sido un placer asistirle. Que tenga un excelente día.")}
        <Hangup/>
"""

    elif Digits == "9":
        return Response(content=main_menu_twiml(), media_type="application/xml")

    else:
        body = f"""
        {tts("Opción no válida. Por favor intente de nuevo.")}
        <Redirect>/twilio/voice/menu-support</Redirect>
"""

    return response(body)


# ──────────────────────────────────────────────
# ENDPOINT — Preguntas frecuentes
# ──────────────────────────────────────────────
@router.post("/voice/menu-faq")
async def menu_faq(
    request: Request,
    Digits: str = Form(default=""),
    db: Session = Depends(get_db)
):
    go, gc = gather("menu")

    if Digits == "1":
        body = f"""
        {tts("¿Qué es un número virtual?")}
        <Pause length="1"/>
        {tts("Un número virtual es un número de teléfono que existe en la nube, sin necesidad de una S I M física ni un dispositivo específico.")}
        {tts("Puede recibir mensajes de texto S M S de cualquier servicio o aplicación en el mundo.")}
        {tts("Es ideal para registrarse en aplicaciones, recibir códigos de verificación de dos factores, y proteger su número personal.")}
        <Pause length="1"/>
        {go}
            {tts("Para volver al menú de preguntas frecuentes, presione cinco.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""

    elif Digits == "2":
        body = f"""
        {tts("¿Cómo funciona K O R Telecom?")}
        <Pause length="1"/>
        {tts("K O R Telecom es una plataforma gratuita accesible desde el bot de Telegram.")}
        {tts("En menos de treinta segundos puede obtener un número virtual de cualquier país disponible.")}
        {tts("Cuando alguien envía un S M S a su número virtual, K O R Telecom lo captura y se lo reenvía instantáneamente por Telegram.")}
        {tts("No necesita instalar ninguna aplicación adicional ni proporcionar datos bancarios.")}
        <Pause length="1"/>
        {go}
            {tts("Para volver al menú de preguntas frecuentes, presione cinco.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""

    elif Digits == "3":
        body = f"""
        {tts("Aplicaciones y servicios compatibles con K O R Telecom.")}
        <Pause length="1"/>
        {tts("Nuestros números virtuales son compatibles con la mayoría de aplicaciones y servicios en línea.")}
        {tts("Entre los más populares se encuentran: WhatsApp, Google, Instagram, Facebook, TikTok, Amazon, Apple, Microsoft, PayPal, Snapchat, Netflix, Airbnb, Lyft, Discord, LinkedIn, y muchos más.")}
        <Pause length="1"/>
        {tts("Tenga en cuenta que algunos servicios pueden requerir números de países específicos. Si un número no funciona, pruebe con uno de otro país.")}
        <Pause length="1"/>
        {go}
            {tts("Para volver al menú de preguntas frecuentes, presione cinco.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""

    elif Digits == "4":
        body = f"""
        {tts("¿Cuánto tiempo duran los números virtuales de K O R Telecom?")}
        <Pause length="1"/>
        {tts("Los números virtuales de K O R Telecom permanecen activos mientras estén en uso en nuestra plataforma.")}
        {tts("Puede ver el estado de sus números en cualquier momento desde el bot de Telegram o el panel web.")}
        {tts("Si un número deja de funcionar, puede crear uno nuevo de forma inmediata y gratuita.")}
        <Pause length="1"/>
        {go}
            {tts("Para volver al menú de preguntas frecuentes, presione cinco.")}
            {tts("Para volver al menú principal, presione nueve.")}
            <Pause length="2"/>
        {gc}
        <Redirect>/twilio/voice</Redirect>
"""

    elif Digits == "5":
        return Response(content=main_menu_twiml(), media_type="application/xml")

    elif Digits == "9":
        return Response(content=main_menu_twiml(), media_type="application/xml")

    else:
        body = f"""
        {tts("Opción no válida. Por favor intente de nuevo.")}
        <Redirect>/twilio/voice/menu-faq</Redirect>
"""

    return response(body)


# ──────────────────────────────────────────────
# ENDPOINT — SMS entrante
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

    # Guardar código recibido
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

    # Buscar número virtual y notificar al usuario de Telegram
    virtual_num = db.query(VirtualNumber).filter(
        VirtualNumber.phone_number == To
    ).first()

    if virtual_num and virtual_num.user_id:
        # user_id puede ser telegram_id (desde bot) o caller_id (desde IVR)
        # Intentamos notificar siempre — si el user_id es un telegram_id válido funcionará
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
    <Message>KOR Telecom ✓ Código recibido: {code} | Servicio: {service} | Mensaje: {Body[:100]}</Message>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


# ──────────────────────────────────────────────
# ENDPOINT — Estado de llamada
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
