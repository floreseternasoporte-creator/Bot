import os
import json
import shutil
import logging
import tempfile
import subprocess
import warnings
import asyncio
from pathlib import Path

warnings.filterwarnings("ignore", message="FP16 is not supported on CPU")

import numpy as np
import librosa
import whisper
import edge_tts
from moviepy import VideoFileClip
from pydub import AudioSegment
from deep_translator import GoogleTranslator

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN no está configurado.")
    exit(1)

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", 8080))
TELEGRAM_MAX_BYTES = 49 * 1024 * 1024

logger.info("Cargando modelo Whisper (tiny)...")
model = whisper.load_model("tiny")
logger.info("Modelo Whisper cargado.")

# ── Idiomas soportados ────────────────────────────────────────────────────────
SUPPORTED_LANGUAGES = {
    "es":    "Español",
    "en":    "Inglés",
    "fr":    "Francés",
    "de":    "Alemán",
    "it":    "Italiano",
    "pt":    "Portugués",
    "ru":    "Ruso",
    "zh-CN": "Chino",
    "ja":    "Japonés",
    "ar":    "Árabe",
}

# ── Voces Edge TTS por idioma y género ────────────────────────────────────────
EDGE_VOICES = {
    "es":    {"male": "es-ES-AlvaroNeural",      "female": "es-ES-ElviraNeural"},
    "en":    {"male": "en-US-GuyNeural",          "female": "en-US-JennyNeural"},
    "fr":    {"male": "fr-FR-HenriNeural",        "female": "fr-FR-DeniseNeural"},
    "de":    {"male": "de-DE-ConradNeural",       "female": "de-DE-KatjaNeural"},
    "it":    {"male": "it-IT-DiegoNeural",        "female": "it-IT-ElsaNeural"},
    "pt":    {"male": "pt-BR-AntonioNeural",      "female": "pt-BR-FranciscaNeural"},
    "ru":    {"male": "ru-RU-DmitryNeural",       "female": "ru-RU-SvetlanaNeural"},
    "zh-CN": {"male": "zh-CN-YunxiNeural",        "female": "zh-CN-XiaoxiaoNeural"},
    "ja":    {"male": "ja-JP-KeitaNeural",        "female": "ja-JP-NanamiNeural"},
    "ar":    {"male": "ar-SA-HamedNeural",        "female": "ar-SA-ZariyahNeural"},
}

OUTPUT_FORMATS = {
    "srt":     "📄 Solo archivo .srt",
    "video":   "🎬 Video con subtítulos",
    "both":    "📄🎬 SRT + subtítulos en video",
    "dub":     "🗣️ Video doblado",
    "dub_sub": "🗣️🎬 Doblado + subtítulos",
}


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
        [InlineKeyboardButton("📄 Solo .srt",               callback_data="fmt_srt")],
        [InlineKeyboardButton("🎬 Video con subtítulos",    callback_data="fmt_video")],
        [InlineKeyboardButton("📄🎬 SRT + subtítulos",      callback_data="fmt_both")],
        [InlineKeyboardButton("🗣️ Video doblado",           callback_data="fmt_dub")],
        [InlineKeyboardButton("🗣️🎬 Doblado + subtítulos",  callback_data="fmt_dub_sub")],
        [InlineKeyboardButton("⬅️ Volver a idiomas",        callback_data="back_langs")],
    ])


# ── Utilidades: SRT ───────────────────────────────────────────────────────────

def format_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def build_srt(segments: list, source_lang: str, target_lang: str) -> str:
    entries = []
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
        entry = (
            f"{idx}\n"
            f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n"
            f"{text}"
        )
        entries.append(entry)
        idx += 1
    return "\n\n".join(entries) + "\n"


# ── Utilidades: subtítulos quemados ───────────────────────────────────────────

def burn_subtitles(video_path: Path, srt_path: Path, output_path: Path) -> None:
    style = "FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Shadow=1"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"subtitles={srt_path.name}:force_style='{style}'",
        "-c:v", "libx264",
        "-c:a", "copy",
        "-preset", "fast",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(srt_path.parent))
    if result.returncode != 0:
        logger.error(f"ffmpeg burn error: {result.stderr[-600:]}")
        raise RuntimeError(f"ffmpeg falló quemando subtítulos: {result.stderr[-400:]}")


# ── Utilidades: doblaje ───────────────────────────────────────────────────────

def detect_gender(audio_path: Path) -> str:
    """Analiza la frecuencia fundamental del audio para determinar el género del hablante."""
    try:
        y, sr = librosa.load(str(audio_path), sr=22050, mono=True, duration=60)
        f0, voiced_flag, _ = librosa.pyin(
            y,
            fmin=librosa.note_to_hz("C2"),
            fmax=librosa.note_to_hz("C7"),
            sr=sr,
        )
        voiced_f0 = f0[voiced_flag & ~np.isnan(f0)]
        if len(voiced_f0) == 0:
            logger.info("No se detectó F0 — asumiendo masculino")
            return "male"
        avg_f0 = float(np.nanmedian(voiced_f0))
        gender = "female" if avg_f0 > 165 else "male"
        logger.info(f"F0 mediana: {avg_f0:.1f} Hz → {gender}")
        return gender
    except Exception as e:
        logger.warning(f"Error detectando género: {e} — asumiendo masculino")
        return "male"


def get_audio_duration_s(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def adjust_audio_speed(input_path: Path, target_duration_s: float, output_path: Path) -> None:
    """Ajusta la velocidad del audio TTS para que encaje exactamente en el segmento."""
    actual = get_audio_duration_s(input_path)
    if actual <= 0 or target_duration_s <= 0:
        shutil.copy(str(input_path), str(output_path))
        return

    ratio = actual / target_duration_s
    ratio = max(0.5, min(2.0, ratio))

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_path),
         "-filter:a", f"atempo={ratio:.4f}", str(output_path)],
        capture_output=True, check=False,
    )
    if not output_path.exists():
        shutil.copy(str(input_path), str(output_path))


async def generate_dubbed_audio(
    segments: list,
    lang_code: str,
    gender: str,
    total_duration_s: float,
    tmpdir: Path,
) -> Path:
    """Genera audio doblado con Edge TTS y lo sincroniza con los timestamps de Whisper."""
    voices = EDGE_VOICES.get(lang_code, EDGE_VOICES["en"])
    voice = voices.get(gender, voices["male"])
    logger.info(f"Voz seleccionada: {voice}")

    total_ms = int(total_duration_s * 1000) + 500
    dubbed = AudioSegment.silent(duration=total_ms)

    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text or (seg["end"] - seg["start"]) < 0.3:
            continue

        start_ms = int(seg["start"] * 1000)
        target_s = seg["end"] - seg["start"]

        raw_path = tmpdir / f"tts_raw_{i}.mp3"
        adj_path = tmpdir / f"tts_adj_{i}.mp3"

        try:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(raw_path))

            if not raw_path.exists() or raw_path.stat().st_size == 0:
                continue

            adjust_audio_speed(raw_path, target_s, adj_path)
            src = adj_path if adj_path.exists() else raw_path
            tts_seg = AudioSegment.from_file(str(src))
            dubbed = dubbed.overlay(tts_seg, position=start_ms)

        except Exception as e:
            logger.warning(f"Error generando TTS segmento {i}: {e}")
            continue

    out_path = tmpdir / "dubbed_audio.mp3"
    dubbed.export(str(out_path), format="mp3", bitrate="128k")
    return out_path


def mix_audio_into_video(video_path: Path, audio_path: Path, output_path: Path) -> None:
    """Reemplaza el audio del video con el audio doblado."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"ffmpeg mix error: {result.stderr[-600:]}")
        raise RuntimeError(f"ffmpeg falló mezclando audio: {result.stderr[-400:]}")


def send_if_fits(file_path: Path) -> bool:
    return file_path.exists() and file_path.stat().st_size <= TELEGRAM_MAX_BYTES


# ── Comandos ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 ¡Hola! Soy un bot de transcripción y doblaje de videos.\n\n"
        "📹 Envíame un video y elige qué hacer:\n"
        "  📄 Subtítulos .srt\n"
        "  🎬 Video con subtítulos quemados\n"
        "  🗣️ Video doblado (detecto si es hombre o mujer y uso la voz más parecida)\n"
        "  🗣️🎬 Doblado + subtítulos\n\n"
        "/help — Instrucciones"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Cómo usar el bot:*\n\n"
        "1. Envíame un video\n"
        "2. Selecciona el idioma de salida\n"
        "3. Elige el formato:\n"
        "   📄 *Solo .srt* — archivo de subtítulos\n"
        "   🎬 *Video con subtítulos* — texto quemado en el video\n"
        "   🗣️ *Video doblado* — audio reemplazado con voz IA\n"
        "   🗣️🎬 *Doblado + subtítulos* — ambos\n\n"
        "🤖 El bot detecta automáticamente si el hablante es hombre o mujer "
        "y elige la voz más parecida de Microsoft Edge TTS.\n\n"
        "⚠️ Límite de Telegram: 50 MB por archivo.",
        parse_mode="Markdown",
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
        "✅ Video recibido.\n\nSelecciona el idioma (o idiomas) de salida:",
        reply_markup=build_language_keyboard([]),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    video_file_id = context.user_data.get("video_file_id")
    if not video_file_id and data not in ("back_langs",):
        await query.edit_message_text("⚠️ No hay ningún video activo. Envía un video primero.")
        return

    if data.startswith("lang_"):
        lang_code = data[len("lang_"):]
        selected = context.user_data.setdefault("selected_languages", [])
        if lang_code in selected:
            selected.remove(lang_code)
        else:
            selected.append(lang_code)
        await query.edit_message_text(
            "Selecciona el idioma (o idiomas) de salida:",
            reply_markup=build_language_keyboard(selected),
        )

    elif data == "next_format":
        selected = context.user_data.get("selected_languages", [])
        if not selected:
            await query.answer("Selecciona al menos un idioma primero.", show_alert=True)
            return
        langs_str = ", ".join(SUPPORTED_LANGUAGES[l] for l in selected)
        await query.edit_message_text(
            f"🌐 Idioma(s): *{langs_str}*\n\n¿Cómo quieres recibir el resultado?",
            parse_mode="Markdown",
            reply_markup=build_format_keyboard(),
        )

    elif data == "back_langs":
        selected = context.user_data.get("selected_languages", [])
        await query.edit_message_text(
            "Selecciona el idioma (o idiomas) de salida:",
            reply_markup=build_language_keyboard(selected),
        )

    elif data.startswith("fmt_"):
        fmt = data[len("fmt_"):]
        selected = context.user_data.get("selected_languages", [])
        langs_str = ", ".join(SUPPORTED_LANGUAGES[l] for l in selected)
        fmt_label = OUTPUT_FORMATS.get(fmt, fmt)

        await query.edit_message_text(
            f"⏳ Procesando…\n"
            f"🌐 Idioma(s): *{langs_str}*\n"
            f"📦 Formato: *{fmt_label}*\n\n"
            "Esto puede tardar varios minutos.",
            parse_mode="Markdown",
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

        with tempfile.TemporaryDirectory() as _tmpdir:
            tmpdir = Path(_tmpdir)

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
            total_duration_s = clip.duration
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

            # 4. Detectar género (solo si se necesita para doblaje)
            gender = None
            if output_format in ("dub", "dub_sub"):
                await message.reply_text("🔍 Detectando género del hablante…")
                gender = detect_gender(audio_path)
                gender_label = "mujer 👩" if gender == "female" else "hombre 👨"
                await message.reply_text(f"Hablante detectado: {gender_label}")

            # 5. Procesar por cada idioma
            for lang_code in selected_languages:
                lang_name = SUPPORTED_LANGUAGES[lang_code]
                await message.reply_text(f"📝 Procesando en {lang_name}…")

                # Generar SRT
                srt_content = build_srt(segments, source_lang, lang_code)
                srt_path = tmpdir / f"subtitles_{lang_code}.srt"
                srt_path.write_text(srt_content, encoding="utf-8")

                # ── Solo SRT ──────────────────────────────────────────────
                if output_format in ("srt", "both"):
                    await message.reply_document(
                        document=srt_path,
                        caption=f"📄 Subtítulos en {lang_name}",
                    )

                # ── Video con subtítulos quemados ─────────────────────────
                if output_format in ("video", "both"):
                    await message.reply_text(f"🎬 Quemando subtítulos en {lang_name}…")
                    burned_path = tmpdir / f"burned_{lang_code}.mp4"
                    try:
                        burn_subtitles(video_path, srt_path, burned_path)
                        if send_if_fits(burned_path):
                            await message.reply_video(
                                video=burned_path,
                                caption=f"🎬 Video con subtítulos en {lang_name}",
                                supports_streaming=True,
                            )
                        else:
                            size_mb = burned_path.stat().st_size / 1024 / 1024
                            await message.reply_text(
                                f"⚠️ El video ({size_mb:.1f} MB) supera el límite de 50 MB de Telegram."
                            )
                    except RuntimeError as e:
                        await message.reply_text(f"❌ Error quemando subtítulos: {e}")

                # ── Video doblado ──────────────────────────────────────────
                if output_format in ("dub", "dub_sub"):
                    await message.reply_text(
                        f"🗣️ Generando doblaje en {lang_name} con voz de "
                        f"{'mujer' if gender == 'female' else 'hombre'}…"
                    )
                    try:
                        dubbed_audio = await generate_dubbed_audio(
                            segments, lang_code, gender, total_duration_s, tmpdir
                        )
                        dubbed_video = tmpdir / f"dubbed_{lang_code}.mp4"
                        mix_audio_into_video(video_path, dubbed_audio, dubbed_video)

                        # Si también quiere subtítulos, quemarlos en el video doblado
                        if output_format == "dub_sub":
                            dubbed_sub_path = tmpdir / f"dubbed_sub_{lang_code}.mp4"
                            burn_subtitles(dubbed_video, srt_path, dubbed_sub_path)
                            final_path = dubbed_sub_path
                        else:
                            final_path = dubbed_video

                        if send_if_fits(final_path):
                            await message.reply_video(
                                video=final_path,
                                caption=f"🗣️ Video doblado en {lang_name}",
                                supports_streaming=True,
                            )
                        else:
                            size_mb = final_path.stat().st_size / 1024 / 1024
                            await message.reply_text(
                                f"⚠️ El video doblado ({size_mb:.1f} MB) supera el límite de 50 MB de Telegram."
                            )
                    except Exception as e:
                        logger.error(f"Error doblando: {e}", exc_info=True)
                        await message.reply_text(f"❌ Error generando doblaje: {e}")

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
