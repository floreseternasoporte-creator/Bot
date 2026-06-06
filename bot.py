import os
import logging
import tempfile
import subprocess
from pathlib import Path
from moviepy import VideoFileClip
from deep_translator import GoogleTranslator
import whisper

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN no está configurado.")
    exit(1)

logger.info("Cargando modelo Whisper...")
model = whisper.load_model("base")
logger.info("Modelo Whisper cargado.")

SUPPORTED_LANGUAGES = {
    "es": "Español",
    "en": "Inglés",
    "fr": "Francés",
    "de": "Alemán",
    "it": "Italiano",
    "pt": "Portugués",
    "ru": "Ruso",
    "zh-CN": "Chino",
    "ja": "Japonés",
    "ar": "Árabe",
}

OUTPUT_FORMATS = {
    "srt":   "📄 Solo archivo .srt",
    "video": "🎬 Video con subtítulos",
    "both":  "📄🎬 Ambos",
}

TELEGRAM_MAX_BYTES = 49 * 1024 * 1024  # 49 MB (límite Bot API)


# ── Teclados ──────────────────────────────────────────────────────────────────

def build_language_keyboard(selected: list) -> InlineKeyboardMarkup:
    keyboard = []
    row = []
    for code, name in SUPPORTED_LANGUAGES.items():
        mark = "✅ " if code in selected else ""
        row.append(InlineKeyboardButton(f"{mark}{name}", callback_data=f"lang_{code}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    if selected:
        keyboard.append([InlineKeyboardButton("➡️ Siguiente", callback_data="next_format")])
    return InlineKeyboardMarkup(keyboard)


def build_format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Solo archivo .srt",       callback_data="fmt_srt")],
        [InlineKeyboardButton("🎬 Video con subtítulos",    callback_data="fmt_video")],
        [InlineKeyboardButton("📄🎬 Ambos",                 callback_data="fmt_both")],
        [InlineKeyboardButton("⬅️ Volver a idiomas",        callback_data="back_langs")],
    ])


# ── Utilidades ────────────────────────────────────────────────────────────────

def format_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def build_srt(segments: list, source_lang: str, target_lang: str) -> str:
    lines = []
    idx = 1
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        if target_lang.lower() != source_lang.lower():
            try:
                translated = GoogleTranslator(source=source_lang, target=target_lang).translate(text)
                text = translated or text
            except Exception as e:
                logger.warning(f"Error traduciendo: {e}")
        lines.append(str(idx))
        lines.append(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}")
        lines.append(text)
        lines.append("")
        idx += 1
    return "\n".join(lines)


def burn_subtitles(video_path: Path, srt_path: Path, output_path: Path) -> None:
    """Quema el archivo SRT en el video usando ffmpeg."""
    srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"subtitles='{srt_escaped}':force_style='FontSize=22,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2'",
        "-c:a", "copy",
        "-preset", "fast",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(srt_path.parent))
    if result.returncode != 0:
        logger.error(f"ffmpeg stderr: {result.stderr}")
        raise RuntimeError(f"ffmpeg falló: {result.stderr[-500:]}")


# ── Comandos ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 ¡Hola! Soy un bot de transcripción y doblaje de videos.\n\n"
        "📹 Envíame un video y podrás:\n"
        "  • Obtener el archivo de subtítulos (.srt)\n"
        "  • Recibir el video con los subtítulos quemados adentro\n"
        "  • O las dos cosas a la vez\n\n"
        "/help — Ver instrucciones"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Cómo usar el bot:*\n\n"
        "1. Envíame un video\n"
        "2. Selecciona uno o más idiomas\n"
        "3. Pulsa *Siguiente*\n"
        "4. Elige el formato de salida:\n"
        "   📄 *Solo .srt* — archivo de subtítulos\n"
        "   🎬 *Video con subtítulos* — video con texto incrustado\n"
        "   📄🎬 *Ambos* — el archivo y el video\n\n"
        "⚠️ Los videos grandes pueden tardar varios minutos.\n"
        "⚠️ Telegram tiene un límite de 50 MB por archivo.",
        parse_mode="Markdown"
    )


# ── Manejadores ───────────────────────────────────────────────────────────────

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    video = update.message.video

    if not video:
        await update.message.reply_text("Por favor, envía un archivo de video válido.")
        return

    logger.info(f"Video de {user.full_name} ({user.id}), file_id={video.file_id}")

    context.user_data.clear()
    context.user_data["video_file_id"] = video.file_id
    context.user_data["selected_languages"] = []

    await update.message.reply_text(
        "✅ Video recibido.\n\nSelecciona el idioma (o idiomas) para los subtítulos:",
        reply_markup=build_language_keyboard([])
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    video_file_id = context.user_data.get("video_file_id")
    if not video_file_id and not data.startswith("back_"):
        await query.edit_message_text("⚠️ No hay ningún video activo. Envía un video primero.")
        return

    # ── Selección de idioma ────────────────────────────────────────────────
    if data.startswith("lang_"):
        lang_code = data[len("lang_"):]
        selected = context.user_data.setdefault("selected_languages", [])
        if lang_code in selected:
            selected.remove(lang_code)
        else:
            selected.append(lang_code)
        await query.edit_message_text(
            "Selecciona el idioma (o idiomas) para los subtítulos:",
            reply_markup=build_language_keyboard(selected)
        )

    # ── Avanzar a selección de formato ────────────────────────────────────
    elif data == "next_format":
        selected = context.user_data.get("selected_languages", [])
        if not selected:
            await query.answer("Selecciona al menos un idioma primero.", show_alert=True)
            return
        langs_str = ", ".join(SUPPORTED_LANGUAGES[l] for l in selected)
        await query.edit_message_text(
            f"🌐 Idioma(s): *{langs_str}*\n\n¿Cómo quieres recibir los subtítulos?",
            parse_mode="Markdown",
            reply_markup=build_format_keyboard()
        )

    # ── Volver a selección de idioma ──────────────────────────────────────
    elif data == "back_langs":
        selected = context.user_data.get("selected_languages", [])
        await query.edit_message_text(
            "Selecciona el idioma (o idiomas) para los subtítulos:",
            reply_markup=build_language_keyboard(selected)
        )

    # ── Selección de formato → procesar ───────────────────────────────────
    elif data.startswith("fmt_"):
        fmt = data[len("fmt_"):]
        selected = context.user_data.get("selected_languages", [])
        langs_str = ", ".join(SUPPORTED_LANGUAGES[l] for l in selected)
        fmt_label = OUTPUT_FORMATS.get(f"{fmt}", fmt)

        await query.edit_message_text(
            f"⏳ Procesando…\n"
            f"🌐 Idioma(s): *{langs_str}*\n"
            f"📦 Formato: *{fmt_label}*\n\n"
            "Esto puede tardar varios minutos.",
            parse_mode="Markdown"
        )

        await process_video(query.message, context, video_file_id, selected, fmt)

        context.user_data.clear()


# ── Procesamiento principal ───────────────────────────────────────────────────

async def process_video(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    video_file_id: str,
    selected_languages: list,
    output_format: str,
) -> None:
    try:
        new_file = await context.bot.get_file(video_file_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # 1. Descargar video
            video_path = tmpdir / "video.mp4"
            await new_file.download_to_drive(video_path)
            logger.info(f"Video descargado: {video_path}")

            # 2. Extraer audio
            clip = VideoFileClip(str(video_path))
            if clip.audio is None:
                await message.reply_text("❌ El video no tiene audio.")
                clip.close()
                return
            audio_path = tmpdir / "audio.mp3"
            clip.audio.write_audiofile(str(audio_path), logger=None)
            clip.close()
            logger.info("Audio extraído.")

            # 3. Transcribir con Whisper
            await message.reply_text("🎙️ Transcribiendo audio con Whisper…")
            result = model.transcribe(str(audio_path))
            source_lang = result.get("language", "en")
            segments = result.get("segments", [])
            logger.info(f"Transcripción lista. Idioma detectado: {source_lang}")

            if not segments:
                await message.reply_text("❌ No se detectó habla en el video.")
                return

            # 4. Por cada idioma seleccionado
            for lang_code in selected_languages:
                lang_name = SUPPORTED_LANGUAGES[lang_code]
                await message.reply_text(f"📝 Generando subtítulos en {lang_name}…")

                srt_content = build_srt(segments, source_lang, lang_code)
                srt_path = tmpdir / f"subtitles_{lang_code}.srt"
                srt_path.write_text(srt_content, encoding="utf-8")

                # Enviar SRT
                if output_format in ("srt", "both"):
                    await message.reply_document(
                        document=srt_path,
                        caption=f"📄 Subtítulos en {lang_name}"
                    )
                    logger.info(f"SRT enviado: {lang_name}")

                # Quemar subtítulos en el video
                if output_format in ("video", "both"):
                    await message.reply_text(f"🎬 Quemando subtítulos en {lang_name} en el video…")
                    burned_path = tmpdir / f"video_{lang_code}.mp4"
                    try:
                        burn_subtitles(video_path, srt_path, burned_path)
                    except RuntimeError as e:
                        await message.reply_text(f"❌ Error al quemar subtítulos: {e}")
                        continue

                    file_size = burned_path.stat().st_size
                    if file_size > TELEGRAM_MAX_BYTES:
                        await message.reply_text(
                            f"⚠️ El video con subtítulos en {lang_name} pesa "
                            f"{file_size / 1024 / 1024:.1f} MB, que supera el límite de 50 MB de Telegram. "
                            "Prueba con un video más corto o usa solo el archivo .srt."
                        )
                    else:
                        await message.reply_video(
                            video=burned_path,
                            caption=f"🎬 Video con subtítulos en {lang_name}",
                            supports_streaming=True,
                        )
                        logger.info(f"Video quemado enviado: {lang_name}")

            await message.reply_text("✅ ¡Todo listo! Envía otro video cuando quieras.")

    except Exception as e:
        logger.error(f"Error procesando video: {e}", exc_info=True)
        await message.reply_text(f"❌ Error al procesar el video: {e}")


# ── Error global ──────────────────────────────────────────────────────────────

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Error no controlado:", exc_info=context.error)
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ Ocurrió un error inesperado. Por favor, inténtalo de nuevo."
        )


# ── Arranque ──────────────────────────────────────────────────────────────────

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", 8080))


def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, handle_video))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        logger.info(f"Modo webhook: {WEBHOOK_URL} — puerto {PORT}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Modo polling (desarrollo local).")
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
