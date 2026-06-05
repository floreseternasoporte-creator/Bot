
import os
import logging
import json
import tempfile
from pathlib import Path
from moviepy.editor import VideoFileClip
from googletrans import Translator
import whisper

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN no está configurado en las variables de entorno.")
    exit(1)

# Load Whisper model
logger.info("Cargando modelo Whisper (esto puede tardar un poco)...")
model = whisper.load_model("base") # Puedes cambiar a "small", "medium", etc. para mejor precisión/velocidad
logger.info("Modelo Whisper cargado.")

# Supported languages for translation
SUPPORTED_LANGUAGES = {
    "es": "Español",
    "en": "Inglés",
    "fr": "Francés",
    "de": "Alemán",
    "it": "Italiano",
    "pt": "Portugués",
    "ru": "Ruso",
    "zh": "Chino",
    "ja": "Japonés",
    "ar": "Árabe",
}

# States for conversation handler
SELECT_LANGUAGE = 0

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the command /start is issued."""
    await update.message.reply_text(
        "¡Hola! Soy un bot que genera subtítulos para tus videos.\n\n"
        "Simplemente envíame un video y te preguntaré en qué idioma(s) quieres los subtítulos."
    )

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles video messages: downloads the video and asks for subtitle languages."""
    user = update.effective_user
    logger.info(f"Video recibido de {user.full_name} ({user.id})")

    video_file = update.message.video
    if not video_file:
        await update.message.reply_text("Por favor, envía un video válido.")
        return

    # Store file_id in user_data for later use
    context.user_data["video_file_id"] = video_file.file_id
    context.user_data["video_mime_type"] = video_file.mime_type

    keyboard = []
    for lang_code, lang_name in SUPPORTED_LANGUAGES.items():
        keyboard.append([InlineKeyboardButton(lang_name, callback_data=f"lang_{lang_code}")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "He recibido tu video. Ahora, por favor, selecciona el idioma o los idiomas en los que deseas los subtítulos.\n\n" 
        "Puedes seleccionar varios idiomas uno por uno.",
        reply_markup=reply_markup
    )
    return SELECT_LANGUAGE

async def select_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles language selection from inline keyboard."""
    query = update.callback_query
    await query.answer()

    lang_code = query.data.split("_")[1]
    lang_name = SUPPORTED_LANGUAGES.get(lang_code)

    if "selected_languages" not in context.user_data:
        context.user_data["selected_languages"] = []
    
    if lang_code not in context.user_data["selected_languages"]:
        context.user_data["selected_languages"].append(lang_code)
        await query.edit_message_text(text=f"Has seleccionado: {lang_name}. Puedes seleccionar más o enviar /done cuando hayas terminado.")
    else:
        await query.edit_message_text(text=f"Ya habías seleccionado: {lang_name}. Puedes seleccionar más o enviar /done cuando hayas terminado.")
    
    return SELECT_LANGUAGE

async def done_selecting_languages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes the selected languages and starts transcription/translation."""
    user = update.effective_user
    selected_languages = context.user_data.get("selected_languages", [])
    video_file_id = context.user_data.get("video_file_id")
    video_mime_type = context.user_data.get("video_mime_type")

    if not video_file_id or not selected_languages:
        await update.message.reply_text("Parece que no se ha subido ningún video o no se han seleccionado idiomas. Por favor, envía un video y selecciona los idiomas.")
        return

    await update.message.reply_text(f"¡Entendido! Generando subtítulos en {", ".join([SUPPORTED_LANGUAGES[lang] for lang in selected_languages])}. Esto puede tardar un poco...")

    try:
        # Download the video
        new_file = await context.bot.get_file(video_file_id)
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / f"{video_file_id}.mp4"
            await new_file.download_to_drive(video_path)
            logger.info(f"Video descargado a {video_path}")

            # Extract audio
            audio_path = Path(tmpdir) / f"{video_file_id}.mp3"
            clip = VideoFileClip(str(video_path))
            clip.audio.write_audiofile(str(audio_path))
            clip.close()
            logger.info(f"Audio extraído a {audio_path}")

            # Transcribe audio using Whisper
            await update.message.reply_text("Transcribiendo audio con Whisper (local)...")
            result = model.transcribe(str(audio_path))
            logger.info("Transcripción completada.")
            
            # Generate SRT for each selected language
            translator = Translator()
            for lang_code in selected_languages:
                srt_content = ""
                for i, segment in enumerate(result["segments"]):
                    start_time = segment["start"]
                    end_time = segment["end"]
                    text = segment["text"].strip()

                    # Translate if not the original language (Whisper detects language, but we'll assume English for simplicity or translate if different)
                    # For a more robust solution, detect source language of transcript first if needed
                    translated_text = text
                    if lang_code != result["language"]:
                        await update.message.reply_text(f"Traduciendo a {SUPPORTED_LANGUAGES[lang_code]}...")
                        translated_obj = translator.translate(text, dest=lang_code, src=result["language"])
                        translated_text = translated_obj.text

                    srt_content += f"{i + 1}\n"
                    srt_content += f"{format_timestamp(start_time)} --> {format_timestamp(end_time)}\n"
                    srt_content += f"{translated_text}\n\n"
                
                srt_filename = f"subtitles_{lang_code}.srt"
                srt_path = Path(tmpdir) / srt_filename
                with open(srt_path, "w", encoding="utf-8") as f:
                    f.write(srt_content)
                
                await update.message.reply_document(document=srt_path, caption=f"Subtítulos en {SUPPORTED_LANGUAGES[lang_code]}")
                logger.info(f"Subtítulos en {SUPPORTED_LANGUAGES[lang_code]} enviados.")

    except Exception as e:
        logger.error(f"Error al procesar el video: {e}", exc_info=True)
        await update.message.reply_text(f"Ocurrió un error al procesar tu video: {e}")
    finally:
        # Clear user data for the next video
        if "video_file_id" in context.user_data:
            del context.user_data["video_file_id"]
        if "selected_languages" in context.user_data:
            del context.user_data["selected_languages"]
        if "video_mime_type" in context.user_data:
            del context.user_data["video_mime_type"]


def format_timestamp(seconds: float) -> str:
    """Formats seconds into SRT time format (HH:MM:SS,ms)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the user."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if update.effective_message:
        await update.effective_message.reply_text(
            "¡Ups! Ha ocurrido un error. Por favor, inténtalo de nuevo más tarde."
        )

def main() -> None:
    """Start the bot."""
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, handle_video))
    application.add_handler(CallbackQueryHandler(select_language_callback, pattern="^lang_"))
    application.add_handler(CommandHandler("done", done_selecting_languages))

    # Error handler
    application.add_error_handler(error_handler)

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
