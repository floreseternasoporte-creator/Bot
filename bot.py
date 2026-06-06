import os
import logging
import tempfile
from pathlib import Path
from moviepy import VideoFileClip
from deep_translator import GoogleTranslator
import whisper

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN no está configurado en las variables de entorno.")
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
        keyboard.append([InlineKeyboardButton("🚀 Generar subtítulos", callback_data="generate")])
    return InlineKeyboardMarkup(keyboard)


def format_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 ¡Hola! Soy un bot de transcripción de videos.\n\n"
        "Envíame un video y selecciona el idioma (o idiomas) en los que quieres los subtítulos. "
        "Te devolveré un archivo .srt con la transcripción.\n\n"
        "Comandos:\n"
        "/start — Mostrar este mensaje\n"
        "/help — Ayuda"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Cómo usar el bot:*\n\n"
        "1. Envíame un video\n"
        "2. Selecciona uno o más idiomas para los subtítulos\n"
        "3. Pulsa *Generar subtítulos*\n"
        "4. Recibirás un archivo .srt por cada idioma seleccionado\n\n"
        "El bot usa Whisper (IA local) para transcribir el audio del video.",
        parse_mode="Markdown"
    )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    video = update.message.video

    if not video:
        await update.message.reply_text("Por favor, envía un archivo de video válido.")
        return

    logger.info(f"Video recibido de {user.full_name} ({user.id}), file_id={video.file_id}")

    context.user_data["video_file_id"] = video.file_id
    context.user_data["selected_languages"] = []

    await update.message.reply_text(
        "✅ Video recibido. Selecciona el idioma o idiomas para los subtítulos y pulsa *Generar subtítulos*:",
        parse_mode="Markdown",
        reply_markup=build_language_keyboard([])
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    video_file_id = context.user_data.get("video_file_id")

    if not video_file_id:
        await query.edit_message_text("⚠️ No hay ningún video activo. Por favor, envía un video primero.")
        return

    if data.startswith("lang_"):
        lang_code = data[len("lang_"):]
        selected = context.user_data.setdefault("selected_languages", [])

        if lang_code in selected:
            selected.remove(lang_code)
        else:
            selected.append(lang_code)

        await query.edit_message_text(
            "Selecciona el idioma o idiomas para los subtítulos y pulsa *Generar subtítulos*:",
            parse_mode="Markdown",
            reply_markup=build_language_keyboard(selected)
        )

    elif data == "generate":
        selected = context.user_data.get("selected_languages", [])

        if not selected:
            await query.answer("Selecciona al menos un idioma primero.", show_alert=True)
            return

        await query.edit_message_text(
            f"⏳ Procesando video en {', '.join(SUPPORTED_LANGUAGES[l] for l in selected)}...\n"
            "Esto puede tardar varios minutos dependiendo del tamaño del video."
        )

        await process_video(query.message, context, video_file_id, selected)

        context.user_data.pop("video_file_id", None)
        context.user_data.pop("selected_languages", None)


async def process_video(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    video_file_id: str,
    selected_languages: list
) -> None:
    try:
        new_file = await context.bot.get_file(video_file_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "video.mp4"
            await new_file.download_to_drive(video_path)
            logger.info(f"Video descargado: {video_path}")

            audio_path = Path(tmpdir) / "audio.mp3"
            clip = VideoFileClip(str(video_path))
            if clip.audio is None:
                await message.reply_text("❌ El video no tiene audio.")
                clip.close()
                return
            clip.audio.write_audiofile(str(audio_path), logger=None)
            clip.close()
            logger.info("Audio extraído.")

            await message.reply_text("🎙️ Transcribiendo audio con Whisper...")
            result = model.transcribe(str(audio_path))
            source_lang = result.get("language", "en")
            segments = result.get("segments", [])
            logger.info(f"Transcripción completada. Idioma detectado: {source_lang}")

            if not segments:
                await message.reply_text("❌ No se detectó habla en el video.")
                return

            for lang_code in selected_languages:
                lang_name = SUPPORTED_LANGUAGES[lang_code]
                await message.reply_text(f"📝 Generando subtítulos en {lang_name}...")

                srt_lines = []
                for i, seg in enumerate(segments, start=1):
                    text = seg["text"].strip()
                    if not text:
                        continue

                    if lang_code.lower() != source_lang.lower():
                        try:
                            translated = GoogleTranslator(
                                source=source_lang,
                                target=lang_code
                            ).translate(text)
                            text = translated or text
                        except Exception as e:
                            logger.warning(f"Error traduciendo segmento: {e}")

                    srt_lines.append(str(i))
                    srt_lines.append(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}")
                    srt_lines.append(text)
                    srt_lines.append("")

                srt_content = "\n".join(srt_lines)
                srt_path = Path(tmpdir) / f"subtitles_{lang_code}.srt"
                srt_path.write_text(srt_content, encoding="utf-8")

                await message.reply_document(
                    document=srt_path,
                    caption=f"✅ Subtítulos en {lang_name}"
                )
                logger.info(f"Enviados subtítulos en {lang_name}.")

            await message.reply_text("✅ ¡Listo! Envía otro video cuando quieras.")

    except Exception as e:
        logger.error(f"Error procesando video: {e}", exc_info=True)
        await message.reply_text(f"❌ Error al procesar el video: {e}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Error no controlado:", exc_info=context.error)
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ Ocurrió un error inesperado. Por favor, inténtalo de nuevo."
        )


def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, handle_video))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_error_handler(error_handler)

    logger.info("Bot iniciado.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
