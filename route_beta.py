"""
KOR Beta — Traductor de Llamadas en Tiempo Real
================================================
Flujo:
  1. Usuario llama al número KOR y entra al menú Beta (opción 6).
  2. IVR le pide el número de destino (dígitos).
  3. KOR llama al número de destino (outbound via Twilio).
  4. Ambas partes hablan; KOR graba cada turno con <Record>.
  5. Al terminar la grabación, el audio se transcribe (Twilio Speech Recognition).
  6. El texto se traduce con Google Translate (googletrans).
  7. La traducción se reproduce con Polly TTS al otro participante.
  8. El ciclo se repite hasta que alguno cuelga.

Detección de idioma:
  - Se detecta automáticamente en cada turno con googletrans.detect().
  - El usuario que llama puede configurar su idioma preferido al inicio.
  - El destino recibe la traducción en su idioma detectado.

Voces Polly por idioma:
  es → Polly.Lupe (es-US)
  en → Polly.Joanna (en-US)
  fr → Polly.Celine (fr-FR)
  de → Polly.Marlene (de-DE)
  pt → Polly.Ines (pt-PT)
  it → Polly.Bianca (it-IT)
  ja → Polly.Mizuki (ja-JP)
  zh → Polly.Zhiyu (cmn-CN)
  ar → Polly.Zeina (arb)
  ru → Polly.Tatyana (ru-RU)
  ko → Polly.Seoyeon (ko-KR)
"""

from fastapi import APIRouter, Request, Form, Depends, BackgroundTasks
from fastapi.responses import Response, JSONResponse
from sqlalchemy.orm import Session
from database import get_db, SessionLocal
from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, APP_URL
import httpx
import json
import re
import urllib.parse
from datetime import datetime

router = APIRouter()

# ──────────────────────────────────────────────────────────────────────────────
# VOICE MAP — Polly voices per language code
# ──────────────────────────────────────────────────────────────────────────────
VOICE_MAP = {
    "es": ("Polly.Lupe",    "es-US"),
    "en": ("Polly.Joanna",  "en-US"),
    "fr": ("Polly.Celine",  "fr-FR"),
    "de": ("Polly.Marlene", "de-DE"),
    "pt": ("Polly.Ines",    "pt-PT"),
    "it": ("Polly.Bianca",  "it-IT"),
    "ja": ("Polly.Mizuki",  "ja-JP"),
    "zh": ("Polly.Zhiyu",   "cmn-CN"),
    "ar": ("Polly.Zeina",   "arb"),
    "ru": ("Polly.Tatyana", "ru-RU"),
    "ko": ("Polly.Seoyeon", "ko-KR"),
}

LANG_NAMES = {
    "es": "Español", "en": "English", "fr": "Français",
    "de": "Deutsch", "pt": "Português", "it": "Italiano",
    "ja": "日本語", "zh": "中文", "ar": "العربية",
    "ru": "Русский", "ko": "한국어",
}

DEFAULT_VOICE = ("Polly.Lupe", "es-US")

# In-memory session store  { call_sid: { caller_lang, dest_lang, dest_number, digits_buffer } }
beta_sessions: dict = {}

# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def xml_wrap(body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n{body}\n</Response>'

def tts(text: str, lang: str = "es") -> str:
    voice, language = VOICE_MAP.get(lang, DEFAULT_VOICE)
    escaped = (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;"))
    return f'<Say voice="{voice}" language="{language}">{escaped}</Say>'

def resp(body: str):
    return Response(content=xml_wrap(body), media_type="application/xml")

def gather(action: str, num_digits: int = 1, timeout: int = 10, speech: bool = False) -> tuple:
    speech_attr = ' input="dtmf speech"' if speech else ' input="dtmf"'
    lang_attr   = ' language="es-ES"'    if speech else ''
    return (
        f'<Gather numDigits="{num_digits}" action="/beta/{action}" method="POST" timeout="{timeout}"{speech_attr}{lang_attr}>',
        '</Gather>'
    )


# ──────────────────────────────────────────────────────────────────────────────
# GOOGLE TRANSLATE  (free tier via unofficial API — no key needed)
# ──────────────────────────────────────────────────────────────────────────────
async def google_translate(text: str, target_lang: str, source_lang: str = "auto") -> str:
    """Translate text using Google Translate unofficial endpoint."""
    if not text or not text.strip():
        return text
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": source_lang,
            "tl": target_lang,
            "dt": "t",
            "q": text,
        }
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, params=params)
            data = r.json()
        # Response structure: [ [ ["translated", "original", ...], ... ], ..., detected_lang ]
        translated = ""
        for block in data[0]:
            if block and block[0]:
                translated += block[0]
        return translated.strip() or text
    except Exception as e:
        print(f"❌ Translate error: {e}")
        return text


async def detect_language(text: str) -> str:
    """Detect language of text. Returns ISO 639-1 code."""
    if not text or not text.strip():
        return "es"
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": "es",
            "dt": "t",
            "q": text,
        }
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, params=params)
            data = r.json()
        # data[2] = detected language code
        detected = data[2] if len(data) > 2 and data[2] else "es"
        return detected
    except Exception as e:
        print(f"❌ Detect lang error: {e}")
        return "es"


# ──────────────────────────────────────────────────────────────────────────────
# TWILIO OUTBOUND CALL
# ──────────────────────────────────────────────────────────────────────────────
async def make_outbound_call(to_number: str, caller_call_sid: str, caller_lang: str):
    """Place an outbound call to the destination number via Twilio REST API."""
    connect_url = f"{APP_URL}/beta/outbound-answer"
    params = urllib.parse.urlencode({
        "caller_sid": caller_call_sid,
        "caller_lang": caller_lang,
    })
    twiml_url = f"{connect_url}?{params}"

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls.json",
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={
                "To":   to_number,
                "From": TWILIO_PHONE_NUMBER,
                "Url":  twiml_url,
            }
        )
        data = r.json()
        print(f"📞 Outbound call initiated: {data.get('sid', data)}")
        return data.get("sid")


# ──────────────────────────────────────────────────────────────────────────────
# ENDPOINT — KOR Beta entry point (called from main IVR option 6)
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/menu")
async def beta_menu(
    request: Request,
    CallSid: str = Form(default=""),
):
    """Entry point for KOR Beta from the main IVR."""
    beta_sessions[CallSid] = {
        "caller_lang": "es",
        "dest_lang":   "en",
        "dest_number": "",
        "digits_buffer": "",
    }
    go, gc = gather("select-lang", num_digits=1)
    body = (
        f'{tts("Bienvenido a K O R Beta. El traductor de llamadas en tiempo real.", "es")}'
        '<Pause length="1"/>'
        f'{tts("Esta función le permite llamar a cualquier número del mundo y traducir la conversación automáticamente.", "es")}'
        '<Pause length="1"/>'
        f'{tts("Primero, seleccione su idioma.", "es")}'
        '<Pause length="1"/>'
        f'{go}'
        f'{tts("Para español, presione uno.", "es")}'
        f'{tts("For English, press two.", "en")}'
        f'{tts("Pour français, appuyez sur trois.", "fr")}'
        f'{tts("Für Deutsch, drücken Sie vier.", "de")}'
        f'{tts("Para português, pressione cinco.", "pt")}'
        f'{tts("Para repetir, presione nueve.", "es")}'
        '<Pause length="2"/>'
        f'{gc}'
        f'{tts("No detectamos selección. Por favor intente de nuevo.", "es")}'
        '<Redirect>/beta/menu</Redirect>'
    )
    return resp(body)


@router.post("/select-lang")
async def beta_select_lang(
    request: Request,
    CallSid: str = Form(default=""),
    Digits: str = Form(default=""),
):
    """User selects their language."""
    lang_map = {"1": "es", "2": "en", "3": "fr", "4": "de", "5": "pt"}
    lang = lang_map.get(Digits, "es")

    if CallSid in beta_sessions:
        beta_sessions[CallSid]["caller_lang"] = lang

    lang_name = LANG_NAMES.get(lang, "Español")
    go, gc = gather("enter-number", num_digits=15, timeout=20)
    body = (
        f'{tts(f"Idioma seleccionado: {lang_name}.", lang)}'
        '<Pause length="1"/>'
        f'{tts("Ahora ingrese el número de destino con el código de país, incluyendo el signo más. Por ejemplo, para Estados Unidos: uno, seguido del número. Presione la tecla numeral cuando termine.", lang)}'
        '<Pause length="1"/>'
        f'{go}'
        '<Pause length="25"/>'
        f'{gc}'
        f'{tts("No recibimos el número. Por favor intente de nuevo.", lang)}'
        '<Redirect>/beta/menu</Redirect>'
    )
    return resp(body)


@router.post("/enter-number")
async def beta_enter_number(
    request: Request,
    CallSid: str = Form(default=""),
    Digits: str = Form(default=""),
):
    """Receive destination number digits and confirm."""
    session = beta_sessions.get(CallSid, {"caller_lang": "es"})
    lang    = session.get("caller_lang", "es")

    if not Digits or len(Digits) < 7:
        go, gc = gather("enter-number", num_digits=15, timeout=20)
        body = (
            f'{tts("Número muy corto. Por favor ingrese el número completo con código de país.", lang)}'
            '<Pause length="1"/>'
            f'{go}'
            '<Pause length="25"/>'
            f'{gc}'
            '<Redirect>/beta/menu</Redirect>'
        )
        return resp(body)

    # Format number: ensure starts with +
    number = Digits.strip()
    if not number.startswith("+"):
        number = "+" + number

    if CallSid in beta_sessions:
        beta_sessions[CallSid]["dest_number"] = number

    # Spell out digits for confirmation
    spelled = ". ".join(list(Digits)) + "."

    go, gc = gather("confirm-number", num_digits=1)
    body = (
        f'{tts("El número que ingresó es:", lang)}'
        '<Pause length="1"/>'
        f'{tts(spelled, lang)}'
        '<Pause length="1"/>'
        f'{go}'
        f'{tts("Para confirmar y marcar, presione uno.", lang)}'
        f'{tts("Para ingresar otro número, presione dos.", lang)}'
        f'{tts("Para cancelar y volver al menú, presione nueve.", lang)}'
        '<Pause length="3"/>'
        f'{gc}'
        '<Redirect>/beta/menu</Redirect>'
    )
    return resp(body)


@router.post("/confirm-number")
async def beta_confirm_number(
    request: Request,
    background_tasks: BackgroundTasks,
    CallSid: str = Form(default=""),
    Digits: str = Form(default=""),
):
    """Confirm and place the outbound call."""
    session = beta_sessions.get(CallSid, {})
    lang    = session.get("caller_lang", "es")

    if Digits == "9":
        body = (
            f'{tts("Volviendo al menú principal.", lang)}'
            '<Redirect>/twilio/voice</Redirect>'
        )
        return resp(body)

    if Digits == "2":
        go, gc = gather("enter-number", num_digits=15, timeout=20)
        body = (
            f'{tts("Ingrese el nuevo número de destino con código de país.", lang)}'
            '<Pause length="1"/>'
            f'{go}'
            '<Pause length="25"/>'
            f'{gc}'
            '<Redirect>/beta/menu</Redirect>'
        )
        return resp(body)

    if Digits == "1":
        dest_number = session.get("dest_number", "")
        if not dest_number:
            body = (
                f'{tts("No encontramos el número de destino. Por favor intente de nuevo.", lang)}'
                '<Redirect>/beta/menu</Redirect>'
            )
            return resp(body)

        # Place outbound call in background
        background_tasks.add_task(
            make_outbound_call, dest_number, CallSid, lang
        )

        # Put caller on hold while we connect
        hold_music = "https://res.cloudinary.com/dnpwxcquk/video/upload/v1781411029/Forever_ttqoba.mp3"
        body = (
            f'{tts("Marcando. Por favor espere mientras conectamos su llamada.", lang)}'
            '<Pause length="2"/>'
            f'{tts("K O R Beta está conectando y activando el sistema de traducción en tiempo real.", lang)}'
            f'<Play loop="5">{hold_music}</Play>'
            f'{tts("La conexión está tardando más de lo esperado. Por favor intente de nuevo en unos momentos.", lang)}'
            '<Hangup/>'
        )
        return resp(body)

    body = (
        f'{tts("Opción no válida.", lang)}'
        '<Redirect>/beta/confirm-number</Redirect>'
    )
    return resp(body)


# ──────────────────────────────────────────────────────────────────────────────
# ENDPOINT — Destination picks up (outbound leg)
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/outbound-answer")
async def beta_outbound_answer(
    request: Request,
    caller_sid: str = "",
    caller_lang: str = "es",
    CallSid: str = Form(default=""),
):
    """TwiML for the outbound leg when destination picks up."""
    # Store dest call sid linked to caller
    beta_sessions[CallSid] = {
        "caller_lang": caller_lang,
        "dest_lang":   "en",
        "role":        "dest",
        "caller_sid":  caller_sid,
    }
    if caller_sid in beta_sessions:
        beta_sessions[caller_sid]["dest_sid"] = CallSid

    dest_greeting_lang = "en"  # default — will be detected on first turn
    body = (
        f'{tts("Hello. You have received a translated call from KOR Beta. Please speak after the tone, then press the pound key.", dest_greeting_lang)}'
        '<Pause length="1"/>'
        f'<Record action="/beta/dest-spoke?caller_sid={caller_sid}&amp;caller_lang={caller_lang}" '
        f'method="POST" maxLength="30" finishOnKey="#" playBeep="true" transcribe="true" '
        f'transcribeCallback="/beta/transcribe-dest?caller_sid={caller_sid}&amp;caller_lang={caller_lang}"/>'
        f'{tts("No detectamos audio. Por favor hable después del tono.", "es")}'
    )
    return resp(body)


@router.post("/dest-spoke")
async def beta_dest_spoke(
    request: Request,
    caller_sid: str = "",
    caller_lang: str = "es",
    RecordingUrl: str = Form(default=""),
    TranscriptionText: str = Form(default=""),
    CallSid: str = Form(default=""),
):
    """After destination speaks — continue recording loop."""
    body = (
        '<Pause length="1"/>'
        f'{tts("Por favor espere mientras traducimos.", "es")}'
        f'<Record action="/beta/dest-spoke?caller_sid={caller_sid}&amp;caller_lang={caller_lang}" '
        f'method="POST" maxLength="30" finishOnKey="#" playBeep="true" transcribe="true" '
        f'transcribeCallback="/beta/transcribe-dest?caller_sid={caller_sid}&amp;caller_lang={caller_lang}"/>'
    )
    return resp(body)


# ──────────────────────────────────────────────────────────────────────────────
# TRANSCRIPTION CALLBACKS — Google Translate + push to other leg
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/transcribe-dest")
async def beta_transcribe_dest(
    request: Request,
    background_tasks: BackgroundTasks,
    caller_sid: str = "",
    caller_lang: str = "es",
    TranscriptionText: str = Form(default=""),
    CallSid: str = Form(default=""),
):
    """Destination spoke — translate and inject into caller's leg."""
    text = TranscriptionText.strip()
    if not text:
        return JSONResponse({"ok": True})

    background_tasks.add_task(
        _translate_and_inject,
        text=text,
        target_lang=caller_lang,
        target_call_sid=caller_sid,
        speaker_role="dest",
    )
    return JSONResponse({"ok": True})


@router.post("/transcribe-caller")
async def beta_transcribe_caller(
    request: Request,
    background_tasks: BackgroundTasks,
    dest_sid: str = "",
    dest_lang: str = "en",
    TranscriptionText: str = Form(default=""),
    CallSid: str = Form(default=""),
):
    """Caller spoke — translate and inject into destination's leg."""
    text = TranscriptionText.strip()
    if not text:
        return JSONResponse({"ok": True})

    background_tasks.add_task(
        _translate_and_inject,
        text=text,
        target_lang=dest_lang,
        target_call_sid=dest_sid,
        speaker_role="caller",
    )
    return JSONResponse({"ok": True})


async def _translate_and_inject(
    text: str,
    target_lang: str,
    target_call_sid: str,
    speaker_role: str,
):
    """Detect language, translate, and inject TTS into the target call leg."""
    # 1. Detect source language
    detected = await detect_language(text)
    print(f"🌐 [{speaker_role}] Detected: {detected} → Target: {target_lang} | Text: {text[:60]}")

    # 2. Translate
    if detected == target_lang:
        translated = text  # same language — no translation needed
    else:
        translated = await google_translate(text, target_lang, detected)

    print(f"✅ Translated: {translated[:80]}")

    # 3. Build TTS TwiML to inject
    voice, lang_code = VOICE_MAP.get(target_lang, DEFAULT_VOICE)
    escaped = (translated
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;"))

    inject_twiml = xml_wrap(
        f'<Say voice="{voice}" language="{lang_code}">{escaped}</Say>'
        '<Pause length="1"/>'
    )

    # 4. Push to the other call leg via Twilio Calls REST API (modify call)
    await _inject_twiml_to_call(target_call_sid, inject_twiml)


async def _inject_twiml_to_call(call_sid: str, twiml: str):
    """Update a live call with new TwiML using Twilio REST API."""
    if not call_sid:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls/{call_sid}.json",
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                data={"Twiml": twiml}
            )
            print(f"📡 Inject result [{call_sid}]: {r.status_code}")
    except Exception as e:
        print(f"❌ Inject error: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# ENDPOINT — Caller's recording turn (after outbound connects back)
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/caller-turn")
async def beta_caller_turn(
    request: Request,
    CallSid: str = Form(default=""),
    dest_sid: str = "",
    dest_lang: str = "en",
    caller_lang: str = "es",
):
    """Prompt the caller to speak their turn."""
    body = (
        f'{tts("Hable ahora y presione numeral cuando termine.", caller_lang)}'
        f'<Record action="/beta/caller-spoke?dest_sid={dest_sid}&amp;dest_lang={dest_lang}&amp;caller_lang={caller_lang}" '
        f'method="POST" maxLength="30" finishOnKey="#" playBeep="true" transcribe="true" '
        f'transcribeCallback="/beta/transcribe-caller?dest_sid={dest_sid}&amp;dest_lang={dest_lang}"/>'
        f'{tts("No detectamos audio. La llamada continuará en espera.", caller_lang)}'
    )
    return resp(body)


@router.post("/caller-spoke")
async def beta_caller_spoke(
    request: Request,
    dest_sid: str = "",
    dest_lang: str = "en",
    caller_lang: str = "es",
    CallSid: str = Form(default=""),
):
    """After caller speaks — loop back for next turn."""
    body = (
        '<Pause length="1"/>'
        f'{tts("Traduciendo...", caller_lang)}'
        f'<Record action="/beta/caller-spoke?dest_sid={dest_sid}&amp;dest_lang={dest_lang}&amp;caller_lang={caller_lang}" '
        f'method="POST" maxLength="30" finishOnKey="#" playBeep="true" transcribe="true" '
        f'transcribeCallback="/beta/transcribe-caller?dest_sid={dest_sid}&amp;dest_lang={dest_lang}"/>'
    )
    return resp(body)


# ──────────────────────────────────────────────────────────────────────────────
# ENDPOINT — Beta status / info (GET, for dashboard or debug)
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/status")
async def beta_status():
    return JSONResponse({
        "feature": "KOR Beta — Traductor de Llamadas",
        "active_sessions": len(beta_sessions),
        "supported_languages": list(LANG_NAMES.values()),
        "translator": "Google Translate (free API)",
        "tts_engine": "Amazon Polly via Twilio",
    })
