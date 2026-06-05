"""
DubBot — Bot de doblaje con clonación de voz
=============================================
Pipeline:
  1. Usuario envía video (o audio)
  2. Bot pregunta idioma destino
  3. Whisper  → transcribe audio original
  4. ArgosTranslate → traduce el texto
  5. XTTS-v2  → sintetiza voz clonada en el idioma destino
  6. FFmpeg   → reemplaza el audio en el video y devuelve el resultado

Dependencias: ver requirements.txt
"""

import os
import sys
import asyncio
import logging
import sqlite3
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

os.environ.setdefault("COQUI_TOS_AGREED", "1")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "dubbot.db"
BOT_MODE = os.environ.get("BOT_MODE", "auto").strip().lower()
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "telegram-webhook").strip("/") or "telegram-webhook"
WEBHOOK_SECRET_TOKEN = os.environ.get("WEBHOOK_SECRET_TOKEN", "").strip() or None
WEBHOOK_LISTEN = os.environ.get("WEBHOOK_LISTEN", "0.0.0.0")
WEBHOOK_PORT = int(os.environ.get("PORT", os.environ.get("WEBHOOK_PORT", "8080")))

# Timeout máximo para todo el pipeline (segundos)
PIPELINE_TIMEOUT = int(os.environ.get("PIPELINE_TIMEOUT", "900"))

# Tamaño máximo de archivo que Telegram permite descargar con bots (bytes)
TG_MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB

# ── Idiomas soportados ─────────────────────────────────────────────────────────
LANGUAGES = {
    "es": "🇪🇸 Español",
    "en": "🇺🇸 Inglés",
    "fr": "🇫🇷 Francés",
    "de": "🇩🇪 Alemán",
    "pt": "🇧🇷 Portugués",
    "it": "🇮🇹 Italiano",
    "ja": "🇯🇵 Japonés",
    "zh": "🇨🇳 Chino",
    "ru": "🇷🇺 Ruso",
    "ar": "🇸🇦 Árabe",
}

XTTS_LANG_MAP = {
    "es": "es", "en": "en", "fr": "fr", "de": "de",
    "pt": "pt", "it": "it", "ja": "ja", "zh": "zh",
    "ru": "ru", "ar": "ar",
}

ARGOS_LANG_MAP = {
    "es": "es", "en": "en", "fr": "fr", "de": "de",
    "pt": "pt", "it": "it", "ja": "ja", "zh": "zh",
    "ru": "ru", "ar": "ar",
}

# Estados
WAITING_LANG = "waiting_lang"
PENDING_MEDIA_KEY = "pending_media"
MARKDOWN_V2_SPECIALS = r"_*[]()~`>#+-=|{}.!"


def escape_markdown_v2(text: str) -> str:
    return "".join(
        f"\\{char}" if char in MARKDOWN_V2_SPECIALS else char
        for char in str(text)
    )


def store_pending_media(ctx, file_id, file_type, file_size=None):
    media_key = uuid4().hex[:16]
    ctx.user_data.setdefault(PENDING_MEDIA_KEY, {})[media_key] = {
        "file_id": file_id,
        "file_type": file_type,
        "file_size": file_size,
    }
    return media_key


def get_public_webhook_url() -> str | None:
    if WEBHOOK_URL:
        return WEBHOOK_URL.rstrip("/")
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway_domain:
        return f"https://{railway_domain.rstrip('/')}/{WEBHOOK_PATH}"
    return None


def webhook_path_from_url(webhook_url: str) -> str:
    parsed = urlparse(webhook_url)
    return parsed.path.strip("/") or WEBHOOK_PATH


def should_use_webhook() -> tuple[bool, str | None]:
    webhook_url = get_public_webhook_url()
    if BOT_MODE == "webhook":
        if not webhook_url:
            raise RuntimeError(
                "BOT_MODE=webhook requiere WEBHOOK_URL o RAILWAY_PUBLIC_DOMAIN"
            )
        return True, webhook_url
    if BOT_MODE == "polling":
        return False, None
    if BOT_MODE != "auto":
        raise RuntimeError("BOT_MODE debe ser auto, webhook o polling")
    return bool(webhook_url), webhook_url


# ── Base de datos ──────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id   INTEGER NOT NULL,
            file_id       TEXT,
            file_type     TEXT,
            src_lang      TEXT,
            dst_lang      TEXT,
            status        TEXT DEFAULT 'pending',
            created_at    TEXT NOT NULL,
            finished_at   TEXT
        );
    """)
    con.commit()
    con.close()
    log.info("DB inicializada")


def create_job(telegram_id, file_id, file_type):
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "INSERT INTO jobs (telegram_id, file_id, file_type, created_at) VALUES (?,?,?,?)",
        (telegram_id, file_id, file_type, datetime.utcnow().isoformat())
    )
    job_id = cur.lastrowid
    con.commit(); con.close()
    return job_id


def update_job(job_id, **kwargs):
    con = sqlite3.connect(DB_PATH)
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [job_id]
    con.execute(f"UPDATE jobs SET {sets} WHERE id=?", vals)
    con.commit(); con.close()


# ── Modelos (cargados una sola vez al iniciar) ─────────────────────────────────
_whisper_model = None
_xtts_model = None
_argos_initialized = False


def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        log.info("Cargando Whisper base desde caché…")
        import whisper
        _whisper_model = whisper.load_model("base")
        log.info("Whisper listo")
    return _whisper_model


def get_xtts():
    global _xtts_model
    if _xtts_model is None:
        log.info("Cargando XTTS-v2 desde caché…")
        from TTS.api import TTS
        _xtts_model = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        log.info("XTTS-v2 listo")
    return _xtts_model


def ensure_argos(src: str, dst: str):
    """Verifica que el par de idiomas esté disponible (ya pre-instalado en imagen)."""
    import argostranslate.package
    installed = {(p.from_code, p.to_code) for p in argostranslate.package.get_installed_packages()}
    pairs_needed = []
    if src != dst:
        if (src, dst) not in installed:
            # Intentar par directo
            if src != "en" and dst != "en":
                # Necesitamos pivote: src→en y en→dst
                if (src, "en") not in installed:
                    pairs_needed.append((src, "en"))
                if ("en", dst) not in installed:
                    pairs_needed.append(("en", dst))
            else:
                pairs_needed.append((src, dst))
    if pairs_needed:
        log.info(f"Faltan paquetes Argos: {pairs_needed} — descargando…")
        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()
        for s, d in pairs_needed:
            pkg = next((p for p in available if p.from_code == s and p.to_code == d), None)
            if pkg:
                argostranslate.package.install_from_path(pkg.download())
                log.info(f"Argos {s}→{d} instalado")


def translate_text(text: str, src: str, dst: str) -> str:
    import argostranslate.translate
    ensure_argos(src, dst)
    result = argostranslate.translate.translate(text, src, dst)
    if result and result.strip():
        return result
    # Pivote en inglés
    if src != "en" and dst != "en":
        ensure_argos(src, "en")
        ensure_argos("en", dst)
        mid = argostranslate.translate.translate(text, src, "en")
        return argostranslate.translate.translate(mid, "en", dst)
    return text


def init_models():
    """Pre-carga todos los modelos al arrancar para evitar delays en el primer uso."""
    log.info("=== Inicializando modelos al arrancar ===")
    try:
        get_whisper()
    except Exception as e:
        log.error(f"Error cargando Whisper: {e}")
    try:
        get_xtts()
    except Exception as e:
        log.error(f"Error cargando XTTS-v2: {e}")
    log.info("=== Modelos listos ===")


# ── Pipeline de doblaje ────────────────────────────────────────────────────────
async def run_dubbing_pipeline(
    video_path: str,
    dst_lang: str,
    workdir: str,
    progress_cb=None
) -> str:
    loop = asyncio.get_running_loop()

    def _run():
        return _sync_pipeline(video_path, dst_lang, workdir, progress_cb, loop)

    return await asyncio.wait_for(
        loop.run_in_executor(None, _run),
        timeout=PIPELINE_TIMEOUT
    )


def _notify_progress(progress_cb, loop, message: str):
    """Envía actualización de progreso de forma segura desde un hilo."""
    if progress_cb is None or loop is None:
        return
    try:
        future = asyncio.run_coroutine_threadsafe(progress_cb(message), loop)
        future.result(timeout=10)
    except Exception as e:
        log.warning(f"Error en progress_cb: {e}")


def _sync_pipeline(video_path, dst_lang, workdir, progress_cb, loop):
    import subprocess

    wp = Path(workdir)

    # ── 1. Extraer audio original ──────────────────────────────────────────
    audio_orig = str(wp / "audio_orig.wav")
    _notify_progress(progress_cb, loop, "🎵 Extrayendo audio del video…")
    log.info("Extrayendo audio…")
    result = subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        audio_orig
    ], capture_output=True)
    if result.returncode != 0:
        err = result.stderr.decode(errors="replace")[-500:]
        raise RuntimeError(f"FFmpeg error al extraer audio: {err}")

    # ── 2. Transcripción con Whisper ───────────────────────────────────────
    _notify_progress(progress_cb, loop, "📝 Transcribiendo audio con Whisper…")
    log.info("Transcribiendo con Whisper…")
    whisper_model = get_whisper()
    wresult = whisper_model.transcribe(audio_orig)
    original_text = wresult["text"].strip()
    detected_lang = wresult.get("language", "en")
    log.info(f"Idioma detectado: {detected_lang} | Texto: {original_text[:80]}…")

    if not original_text:
        raise ValueError("No se detectó voz en el archivo. ¿El video tiene audio?")

    # ── 3. Traducción con ArgosTranslate ──────────────────────────────────
    _notify_progress(progress_cb, loop, f"🌐 Traduciendo {detected_lang} → {dst_lang}…")
    log.info(f"Traduciendo {detected_lang} → {dst_lang}…")
    if detected_lang == dst_lang:
        translated_text = original_text
        log.info("Idioma origen igual al destino, sin traducción")
    else:
        translated_text = translate_text(original_text, detected_lang, dst_lang)
    log.info(f"Traducción: {translated_text[:80]}…")

    # ── 4. Síntesis con XTTS-v2 ───────────────────────────────────────────
    _notify_progress(progress_cb, loop, "🗣 Sintetizando voz clonada con XTTS-v2…\n_Esto puede tardar 1-3 minutos…_")
    log.info("Sintetizando voz clonada con XTTS-v2…")
    audio_dubbed = str(wp / "audio_dubbed.wav")
    tts = get_xtts()
    tts.tts_to_file(
        text=translated_text,
        speaker_wav=audio_orig,
        language=XTTS_LANG_MAP.get(dst_lang, "en"),
        file_path=audio_dubbed
    )

    # ── 5. Mezclar audio doblado en el video ──────────────────────────────
    _notify_progress(progress_cb, loop, "🎬 Mezclando audio doblado en el video…")
    log.info("Mezclando audio doblado en el video…")
    output_path = str(wp / "dubbed_output.mp4")

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path],
        capture_output=True, text=True
    )
    has_video = "video" in probe.stdout

    if has_video:
        res = subprocess.run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_dubbed,
            "-c:v", "copy",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            output_path
        ], capture_output=True)
        if res.returncode != 0:
            err = res.stderr.decode(errors="replace")[-500:]
            raise RuntimeError(f"FFmpeg error al mezclar video: {err}")
    else:
        output_path = str(wp / "dubbed_output.mp3")
        res = subprocess.run([
            "ffmpeg", "-y", "-i", audio_dubbed,
            "-codec:a", "libmp3lame", "-qscale:a", "2",
            output_path
        ], capture_output=True)
        if res.returncode != 0:
            err = res.stderr.decode(errors="replace")[-500:]
            raise RuntimeError(f"FFmpeg error al convertir audio: {err}")

    log.info("Pipeline completado")
    return output_path, original_text, translated_text, detected_lang


# ── Handlers de Telegram ───────────────────────────────────────────────────────
async def cmd_start(update, ctx):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = [
        [InlineKeyboardButton("🎬 ¿Cómo funciona?", callback_data="how_it_works")],
        [InlineKeyboardButton("📊 Mis trabajos",     callback_data="my_jobs")],
    ]
    await update.message.reply_text(
        "🎙️ *DubBot — Doblaje con clonación de voz*\n\n"
        "Envíame un *video* o *audio* y lo doblo a cualquier idioma.\n"
        "La voz del original se clona automáticamente.\n\n"
        "⚠️ *Límite:* archivos hasta 20 MB\n\n"
        "👇 *Simplemente envíame un archivo para empezar.*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def cmd_ayuda(update, ctx):
    await update.message.reply_text(
        "📚 *Cómo usar DubBot:*\n\n"
        "1️⃣ Envía un video o audio (mp4, mov, avi, mp3, wav, ogg…)\n"
        "2️⃣ Elige el idioma de destino\n"
        "3️⃣ Espera mientras proceso el doblaje\n"
        "4️⃣ Recibe el video/audio doblado con la voz clonada\n\n"
        "🧠 *Tecnología usada:*\n"
        "• Whisper (OpenAI) — transcripción\n"
        "• ArgosTranslate — traducción offline\n"
        "• XTTS-v2 (Coqui) — síntesis de voz clonada\n"
        "• FFmpeg — procesamiento de video\n\n"
        "⏱ El proceso tarda entre 2 y 5 minutos según la duración.\n"
        "⚠️ Límite de archivo: 20 MB (restricción de Telegram).",
        parse_mode="Markdown"
    )


async def cb_how_it_works(update, ctx):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        "🔬 *Pipeline de doblaje:*\n\n"
        "1. 🎵 Extraigo el audio de tu video con FFmpeg\n"
        "2. 📝 Whisper transcribe lo que se dice\n"
        "3. 🌐 ArgosTranslate traduce al idioma elegido\n"
        "4. 🗣 XTTS-v2 sintetiza el texto en la *voz clonada* del original\n"
        "5. 🎬 FFmpeg reemplaza el audio en el video\n"
        "6. 📤 Te envío el resultado\n\n"
        "Todo procesado localmente, sin enviar datos a terceros.",
        parse_mode="Markdown"
    )


async def cb_my_jobs(update, ctx):
    await update.callback_query.answer()
    tid = update.effective_user.id
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT dst_lang, status, created_at FROM jobs WHERE telegram_id=? ORDER BY id DESC LIMIT 10",
        (tid,)
    ).fetchall()
    con.close()
    if not rows:
        await update.callback_query.message.edit_text("Aún no has procesado ningún video.")
        return
    lines = ["📋 *Tus últimos trabajos:*\n"]
    STATUS_ICON = {"pending": "⏳", "processing": "⚙️", "done": "✅", "error": "❌"}
    for lang, status, created in rows:
        icon = STATUS_ICON.get(status, "❓")
        lang_name = LANGUAGES.get(lang, lang or "?")
        dt = created[:16].replace("T", " ")
        lines.append(f"{icon} {lang_name} · {dt}")
    await update.callback_query.message.edit_text("\n".join(lines), parse_mode="Markdown")


def _lang_keyboard(media_key: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = []
    row = []
    for code, label in LANGUAGES.items():
        row.append(InlineKeyboardButton(label, callback_data=f"dub:{media_key}:{code}"))
        if len(row) == 2:
            kb.append(row); row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancel_dub")])
    return InlineKeyboardMarkup(kb)


async def _handle_media(update, ctx, file_id, file_unique_id, file_type, display_name, file_size=None):
    if file_size and file_size > TG_MAX_FILE_BYTES:
        await update.message.reply_text(
            f"⚠️ El archivo pesa *{file_size // (1024*1024)} MB*, pero el límite de Telegram "
            f"para bots es de *20 MB*.\n\n"
            "Por favor envía un archivo más pequeño o comprime el video antes.",
            parse_mode="Markdown"
        )
        return
    media_key = store_pending_media(ctx, file_id, file_type, file_size)
    kb = _lang_keyboard(media_key)
    safe_display_name = escape_markdown_v2(display_name)
    await update.message.reply_text(
        f"🎬 *{safe_display_name}* recibido\\!\n\n"
        "¿A qué idioma quieres doblarlo?\n"
        "_Selecciona el idioma de destino:_",
        parse_mode="MarkdownV2",
        reply_markup=kb
    )


async def handle_video(update, ctx):
    v = update.message.video
    await _handle_media(update, ctx, v.file_id, v.file_unique_id, "video",
                        v.file_name or "video.mp4", v.file_size)


async def handle_video_note(update, ctx):
    v = update.message.video_note
    await _handle_media(update, ctx, v.file_id, v.file_unique_id, "video_note",
                        "video_nota.mp4", v.file_size)


async def handle_audio(update, ctx):
    a = update.message.audio or update.message.voice
    name = getattr(a, "file_name", None) or "audio.ogg"
    await _handle_media(update, ctx, a.file_id, a.file_unique_id, "audio", name, a.file_size)


async def handle_document(update, ctx):
    d = update.message.document
    mime = d.mime_type or ""
    if not any(x in mime for x in ["video", "audio"]):
        await update.message.reply_text(
            "⚠️ Solo acepto archivos de *video* o *audio*.\n"
            "Formatos: mp4, mov, avi, mkv, mp3, wav, ogg…",
            parse_mode="Markdown"
        )
        return
    ftype = "video" if "video" in mime else "audio"
    await _handle_media(update, ctx, d.file_id, d.file_unique_id, ftype,
                        d.file_name or "archivo", d.file_size)


async def cb_cancel_dub(update, ctx):
    ctx.user_data.pop(PENDING_MEDIA_KEY, None)
    await update.callback_query.answer("Cancelado")
    await update.callback_query.message.edit_text("❌ Doblaje cancelado.")


async def error_handler(update, ctx):
    from telegram.error import Conflict
    if isinstance(ctx.error, Conflict):
        log.critical(
            "Telegram rechazó el polling porque hay otra instancia usando "
            "getUpdates con el mismo TELEGRAM_TOKEN. Detén la otra copia del "
            "bot o usa BOT_MODE=webhook/WEBHOOK_URL. Cerrando esta instancia."
        )
        ctx.application.stop_running()
        return
    log.exception("Error no controlado en Telegram", exc_info=ctx.error)
    if update and getattr(update, "effective_message", None):
        try:
            await update.effective_message.reply_text(
                "❌ Ocurrió un error inesperado. Intenta de nuevo o usa /ayuda."
            )
        except Exception:
            log.exception("No se pudo notificar el error al usuario")


async def cb_dub(update, ctx):
    """El usuario eligió el idioma — arranca el pipeline."""
    await update.callback_query.answer()
    query = update.callback_query
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        await query.message.edit_text("⚠️ Datos inválidos. Por favor envía el archivo de nuevo.")
        return
    _, media_key, dst_lang = parts

    tid = update.effective_user.id
    chat_id = update.effective_chat.id
    lang_name = LANGUAGES.get(dst_lang, dst_lang)

    media_info = ctx.user_data.get(PENDING_MEDIA_KEY, {}).get(media_key)
    if not media_info:
        await query.message.edit_text("⚠️ No encontré el archivo. Por favor envíalo de nuevo.")
        return

    file_id   = media_info["file_id"]
    file_type = media_info["file_type"]

    job_id = create_job(tid, file_id, file_type)
    update_job(job_id, dst_lang=dst_lang, status="processing")

    status_msg = await query.message.edit_text(
        f"⚙️ *Procesando doblaje a {lang_name}…*\n\n"
        "🔄 Descargando archivo…",
        parse_mode="Markdown"
    )

    async def progress_cb(step_text: str):
        try:
            await status_msg.edit_text(
                f"⚙️ *Procesando doblaje a {lang_name}…*\n\n"
                f"{step_text}",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    workdir = tempfile.mkdtemp(prefix="dubbot_", dir=DATA_DIR)
    try:
        # Descargar archivo de Telegram
        tg_file = await ctx.bot.get_file(file_id)
        ext = Path(tg_file.file_path).suffix or (".mp4" if "video" in file_type else ".ogg")
        input_path = str(Path(workdir) / f"input{ext}")
        await tg_file.download_to_drive(input_path)

        # Verificar que el archivo se descargó correctamente
        input_size = Path(input_path).stat().st_size
        if input_size == 0:
            raise ValueError("El archivo descargado está vacío.")
        log.info(f"Archivo descargado: {input_path} ({input_size} bytes)")

        # Ejecutar pipeline con progress callback
        result = await run_dubbing_pipeline(input_path, dst_lang, workdir, progress_cb)
        output_path, original_text, translated_text, detected_lang = result

        await progress_cb("📤 Enviando resultado…")

        # Enviar resultado
        caption = (
            f"🎙️ *Doblaje completado*\n"
            f"🌐 {LANGUAGES.get(detected_lang, detected_lang)} → {lang_name}\n\n"
            f"📝 *Original:* _{original_text[:200]}{'…' if len(original_text)>200 else ''}_\n\n"
            f"🗣 *Traducción:* _{translated_text[:200]}{'…' if len(translated_text)>200 else ''}_"
        )

        out = Path(output_path)
        with open(output_path, "rb") as f:
            if out.suffix == ".mp4":
                await ctx.bot.send_video(chat_id, f, caption=caption,
                                          parse_mode="Markdown",
                                          supports_streaming=True)
            else:
                await ctx.bot.send_audio(chat_id, f, caption=caption,
                                          parse_mode="Markdown")

        update_job(job_id, status="done", finished_at=datetime.utcnow().isoformat())
        await status_msg.delete()

    except asyncio.TimeoutError:
        log.error(f"Timeout en pipeline job {job_id} después de {PIPELINE_TIMEOUT}s")
        update_job(job_id, status="error")
        await status_msg.edit_text(
            f"⏱ *Tiempo de procesamiento agotado*\n\n"
            f"El doblaje tardó más de {PIPELINE_TIMEOUT // 60} minutos.\n"
            "Intenta con un video más corto (menos de 60 segundos).",
            parse_mode="Markdown"
        )
    except Exception as e:
        log.exception(f"Error en pipeline job {job_id}: {e}")
        update_job(job_id, status="error")
        error_str = str(e)[:300]
        await status_msg.edit_text(
            f"❌ *Error al procesar el doblaje*\n\n"
            f"`{error_str}`\n\n"
            "Por favor intenta de nuevo con otro archivo.",
            parse_mode="Markdown"
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        ctx.user_data.get(PENDING_MEDIA_KEY, {}).pop(media_key, None)


# ── Build app ──────────────────────────────────────────────────────────────────
def _build_app():
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        CallbackQueryHandler, filters
    )

    if not TELEGRAM_TOKEN:
        log.error("TELEGRAM_TOKEN no configurado. Agrega la variable de entorno.")
        sys.exit(1)

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("ayuda",  cmd_ayuda))
    app.add_handler(CommandHandler("help",   cmd_ayuda))

    app.add_handler(CallbackQueryHandler(cb_how_it_works, pattern="^how_it_works$"))
    app.add_handler(CallbackQueryHandler(cb_my_jobs,      pattern="^my_jobs$"))
    app.add_handler(CallbackQueryHandler(cb_cancel_dub,   pattern="^cancel_dub$"))
    app.add_handler(CallbackQueryHandler(cb_dub,          pattern=r"^dub:"))

    app.add_handler(MessageHandler(filters.VIDEO,                       handle_video))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE,                  handle_video_note))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE,       handle_audio))
    app.add_handler(MessageHandler(filters.Document.VIDEO |
                                   filters.Document.AUDIO,              handle_document))
    app.add_handler(MessageHandler(filters.Document.ALL,                handle_document))

    app.add_error_handler(error_handler)

    return app


def run_bot():
    app = _build_app()
    use_webhook, webhook_url = should_use_webhook()

    if use_webhook:
        url_path = webhook_path_from_url(webhook_url)
        log.info(
            "DubBot iniciado con webhook en %s:%s/%s → %s",
            WEBHOOK_LISTEN, WEBHOOK_PORT, url_path, webhook_url
        )
        app.run_webhook(
            listen=WEBHOOK_LISTEN,
            port=WEBHOOK_PORT,
            url_path=url_path,
            webhook_url=webhook_url,
            secret_token=WEBHOOK_SECRET_TOKEN,
            drop_pending_updates=True,
        )
        return

    log.info(
        "DubBot iniciado con polling. Si aparece un 409 Conflict, hay otra "
        "instancia activa con el mismo TELEGRAM_TOKEN."
    )
    app.run_polling(drop_pending_updates=True, bootstrap_retries=0)


if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        log.error(
            "\n\n"
            "  ╔══════════════════════════════════════════════════════╗\n"
            "  ║  TELEGRAM_TOKEN no está configurado                  ║\n"
            "  ║  Agrega la variable en Railway:                      ║\n"
            "  ║  Settings → Variables → TELEGRAM_TOKEN = tu_token   ║\n"
            "  ╚══════════════════════════════════════════════════════╝\n"
        )
        sys.exit(1)

    init_db()
    init_models()
    run_bot()
