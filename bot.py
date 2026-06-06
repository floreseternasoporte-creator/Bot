import os
import shutil
import logging
import tempfile
import subprocess
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message="FP16 is not supported on CPU")

import numpy as np
import librosa
import whisper
import edge_tts
from moviepy import VideoFileClip
from pydub import AudioSegment
from deep_translator import GoogleTranslator
from sklearn.cluster import AgglomerativeClustering
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score

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

# ── Idiomas ───────────────────────────────────────────────────────────────────
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

# ── Voces Edge TTS — múltiples por género para distinguir hablantes ───────────
EDGE_VOICES: dict[str, dict[str, list[str]]] = {
    "es":    {"male": ["es-ES-AlvaroNeural",    "es-MX-JorgeNeural"],
              "female": ["es-ES-ElviraNeural",  "es-MX-DaliaNeural"]},
    "en":    {"male": ["en-US-GuyNeural",        "en-GB-RyanNeural"],
              "female": ["en-US-JennyNeural",   "en-GB-SoniaNeural"]},
    "fr":    {"male": ["fr-FR-HenriNeural",      "fr-CA-AntoineNeural"],
              "female": ["fr-FR-DeniseNeural",  "fr-CA-SylvieNeural"]},
    "de":    {"male": ["de-DE-ConradNeural",     "de-AT-JonasNeural"],
              "female": ["de-DE-KatjaNeural",   "de-AT-IngridNeural"]},
    "it":    {"male": ["it-IT-DiegoNeural",      "it-IT-BenignoNeural"],
              "female": ["it-IT-ElsaNeural",    "it-IT-FiammaNeural"]},
    "pt":    {"male": ["pt-BR-AntonioNeural",    "pt-PT-DuarteNeural"],
              "female": ["pt-BR-FranciscaNeural", "pt-PT-RaquelNeural"]},
    "ru":    {"male": ["ru-RU-DmitryNeural",     "ru-RU-PavelNeural"],
              "female": ["ru-RU-SvetlanaNeural", "ru-RU-DariyaNeural"]},
    "zh-CN": {"male": ["zh-CN-YunxiNeural",      "zh-CN-YunjianNeural"],
              "female": ["zh-CN-XiaoxiaoNeural", "zh-CN-XiaohanNeural"]},
    "ja":    {"male": ["ja-JP-KeitaNeural",      "ja-JP-NaokiNeural"],
              "female": ["ja-JP-NanamiNeural",   "ja-JP-AoiNeural"]},
    "ar":    {"male": ["ar-SA-HamedNeural",      "ar-EG-ShakirNeural"],
              "female": ["ar-SA-ZariyahNeural",  "ar-EG-SalmaNeural"]},
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
        [InlineKeyboardButton("📄 Solo .srt",              callback_data="fmt_srt")],
        [InlineKeyboardButton("🎬 Video con subtítulos",   callback_data="fmt_video")],
        [InlineKeyboardButton("📄🎬 SRT + subtítulos",     callback_data="fmt_both")],
        [InlineKeyboardButton("🗣️ Video doblado",          callback_data="fmt_dub")],
        [InlineKeyboardButton("🗣️🎬 Doblado + subtítulos", callback_data="fmt_dub_sub")],
        [InlineKeyboardButton("⬅️ Volver a idiomas",       callback_data="back_langs")],
    ])


# ── SRT ───────────────────────────────────────────────────────────────────────

def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def build_srt(segments: list, source_lang: str, target_lang: str) -> str:
    entries = []
    idx = 1
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        if target_lang.lower() != source_lang.lower():
            try:
                text = GoogleTranslator(source=source_lang, target=target_lang).translate(text) or text
            except Exception as e:
                logger.warning(f"Traducción fallida: {e}")
        entries.append(
            f"{idx}\n"
            f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n"
            f"{text}"
        )
        idx += 1
    return "\n\n".join(entries) + "\n"


# ── Subtítulos quemados ───────────────────────────────────────────────────────

def burn_subtitles(video_path: Path, srt_path: Path, output_path: Path) -> None:
    style = "FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Shadow=1"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"subtitles={srt_path.name}:force_style='{style}'",
        "-c:v", "libx264", "-c:a", "copy", "-preset", "fast",
        str(output_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(srt_path.parent))
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg subtitles error: {r.stderr[-400:]}")


# ── Diarización de hablantes ──────────────────────────────────────────────────

def diarize_speakers(audio_path: Path, segments: list) -> list[int]:
    """
    Devuelve una lista de IDs de hablante (uno por segmento) usando
    MFCC + StandardScaler + AgglomerativeClustering.
    Detecta automáticamente cuántos hablantes hay (1-4).
    """
    y, sr = librosa.load(str(audio_path), sr=16000, mono=True)

    embeddings: list[np.ndarray | None] = []
    valid_indices: list[int] = []

    for i, seg in enumerate(segments):
        duration = seg["end"] - seg["start"]
        if duration < 0.5:
            embeddings.append(None)
            continue
        start = int(seg["start"] * sr)
        end   = int(seg["end"]   * sr)
        chunk = y[start:end]
        if len(chunk) < int(0.5 * sr):
            embeddings.append(None)
            continue
        mfcc = librosa.feature.mfcc(y=chunk, sr=sr, n_mfcc=20)
        feat = np.concatenate([np.mean(mfcc, axis=1), np.std(mfcc, axis=1)])
        embeddings.append(feat)
        valid_indices.append(i)

    n_valid = len(valid_indices)
    if n_valid < 2:
        return [0] * len(segments)

    X = np.array([embeddings[i] for i in valid_indices])
    X = StandardScaler().fit_transform(X)

    # Selecciona k óptimo (2-4) usando silhouette score
    best_labels = np.zeros(n_valid, dtype=int)
    best_score  = -1.0
    max_k = min(4, n_valid)
    for k in range(2, max_k + 1):
        labels = AgglomerativeClustering(n_clusters=k, linkage="ward").fit_predict(X)
        try:
            score = float(silhouette_score(X, labels))
        except Exception:
            score = -1.0
        if score > best_score:
            best_score  = score
            best_labels = labels

    # Mapear de regreso a todos los segmentos
    speaker_ids = [0] * len(segments)
    for pos, seg_idx in enumerate(valid_indices):
        speaker_ids[seg_idx] = int(best_labels[pos])

    n_speakers = len(set(best_labels))
    logger.info(f"Diarización: {n_speakers} hablante(s) detectados (silhouette={best_score:.3f})")
    return speaker_ids


def detect_speaker_genders(audio_path: Path, segments: list, speaker_ids: list[int]) -> dict[int, str]:
    """Detecta el género de cada hablante a partir de la frecuencia fundamental (F0)."""
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)

    # Agrupa chunks de audio por hablante
    speaker_chunks: dict[int, list[np.ndarray]] = {}
    for seg, spk in zip(segments, speaker_ids):
        start = int(seg["start"] * sr)
        end   = int(seg["end"]   * sr)
        speaker_chunks.setdefault(spk, []).append(y[start:end])

    genders: dict[int, str] = {}
    for spk_id, chunks in speaker_chunks.items():
        audio = np.concatenate(chunks)
        if len(audio) < sr:
            genders[spk_id] = "male"
            continue
        f0, voiced, _ = librosa.pyin(
            audio,
            fmin=librosa.note_to_hz("C2"),
            fmax=librosa.note_to_hz("C7"),
            sr=sr,
        )
        voiced_f0 = f0[voiced & ~np.isnan(f0)]
        if len(voiced_f0) == 0:
            genders[spk_id] = "male"
        else:
            median_f0 = float(np.nanmedian(voiced_f0))
            genders[spk_id] = "female" if median_f0 > 165 else "male"
            logger.info(f"Hablante {spk_id}: F0={median_f0:.1f} Hz → {genders[spk_id]}")

    return genders


def assign_speaker_voices(
    speaker_ids: list[int],
    speaker_genders: dict[int, str],
    lang_code: str,
) -> dict[int, str]:
    """Asigna una voz Edge TTS única a cada hablante."""
    pool = EDGE_VOICES.get(lang_code, EDGE_VOICES["en"])
    voice_map: dict[int, str] = {}
    gender_counter: dict[str, int] = {"male": 0, "female": 0}

    for spk_id in sorted(set(speaker_ids)):
        gender = speaker_genders.get(spk_id, "male")
        voices = pool[gender]
        idx = gender_counter[gender] % len(voices)
        voice_map[spk_id] = voices[idx]
        gender_counter[gender] += 1
        logger.info(f"Hablante {spk_id} ({gender}) → {voice_map[spk_id]}")

    return voice_map


# ── Audio: utilidades ─────────────────────────────────────────────────────────

def get_audio_duration_s(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def adjust_audio_speed(input_path: Path, target_s: float, output_path: Path) -> None:
    actual = get_audio_duration_s(input_path)
    if actual <= 0 or target_s <= 0:
        shutil.copy(str(input_path), str(output_path))
        return
    ratio = max(0.5, min(2.0, actual / target_s))
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_path),
         "-filter:a", f"atempo={ratio:.4f}", str(output_path)],
        capture_output=True,
    )
    if r.returncode != 0 or not output_path.exists():
        shutil.copy(str(input_path), str(output_path))


async def generate_dubbed_audio(
    segments: list,
    lang_code: str,
    speaker_ids: list[int],
    voice_map: dict[int, str],
    total_duration_s: float,
    tmpdir: Path,
) -> Path:
    """Genera el audio completo doblado: TTS por segmento, sincronizado por timestamp."""
    fallback_voices = EDGE_VOICES.get(lang_code, EDGE_VOICES["en"])
    fallback_voice  = fallback_voices["male"][0]

    total_ms = int(total_duration_s * 1000) + 500
    dubbed   = AudioSegment.silent(duration=total_ms)

    for i, (seg, spk_id) in enumerate(zip(segments, speaker_ids)):
        text = seg["text"].strip()
        if not text or (seg["end"] - seg["start"]) < 0.3:
            continue

        voice    = voice_map.get(spk_id, fallback_voice)
        start_ms = int(seg["start"] * 1000)
        target_s = seg["end"] - seg["start"]

        raw_path = tmpdir / f"tts_raw_{i}.mp3"
        adj_path = tmpdir / f"tts_adj_{i}.mp3"

        try:
            await edge_tts.Communicate(text, voice).save(str(raw_path))
            if not raw_path.exists() or raw_path.stat().st_size == 0:
                continue
            adjust_audio_speed(raw_path, target_s, adj_path)
            src = adj_path if adj_path.exists() else raw_path
            dubbed = dubbed.overlay(AudioSegment.from_file(str(src)), position=start_ms)
        except Exception as e:
            logger.warning(f"TTS error segmento {i}: {e}")

    out = tmpdir / "dubbed_audio.mp3"
    dubbed.export(str(out), format="mp3", bitrate="128k")
    return out


def mix_audio_into_video(video_path: Path, audio_path: Path, output_path: Path) -> None:
    r = subprocess.run(
        ["ffmpeg", "-y",
         "-i", str(video_path),
         "-i", str(audio_path),
         "-c:v", "copy",
         "-map", "0:v:0", "-map", "1:a:0",
         "-shortest", str(output_path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg mix error: {r.stderr[-400:]}")


# ── Comandos ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 ¡Hola! Soy un bot de transcripción y doblaje de videos.\n\n"
        "📹 Envíame un video y elige qué hacer:\n"
        "  📄 Subtítulos .srt\n"
        "  🎬 Video con subtítulos quemados\n"
        "  🗣️ Video doblado — detecto cuántas personas hablan, "
        "el género de cada una y asigno una voz distinta\n"
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
        "   🗣️ *Video doblado* — audio reemplazado con voces IA\n"
        "   🗣️🎬 *Doblado + subtítulos* — audio IA + texto\n\n"
        "🤖 *Diarización automática:* el bot detecta cuántas personas "
        "hablan en el video, analiza si es hombre o mujer, y le asigna "
        "una voz Edge TTS diferente a cada una.\n\n"
        "⚠️ Límite de Telegram: 50 MB por archivo.",
        parse_mode="Markdown",
    )


# ── Manejadores ───────────────────────────────────────────────────────────────

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    video = update.message.video
    if not video:
        await update.message.reply_text("Por favor, envía un archivo de video válido.")
        return
    user = update.effective_user
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
    if not video_file_id and data != "back_langs":
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
        selected  = context.user_data.get("selected_languages", [])
        langs_str = ", ".join(SUPPORTED_LANGUAGES[l] for l in selected)
        await query.edit_message_text(
            f"⏳ Procesando…\n"
            f"🌐 Idioma(s): *{langs_str}*\n"
            f"📦 Formato: *{OUTPUT_FORMATS.get(fmt, fmt)}*\n\n"
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

        with tempfile.TemporaryDirectory() as _tmp:
            tmp = Path(_tmp)

            # 1. Descargar video
            video_path = tmp / "video.mp4"
            await new_file.download_to_drive(video_path)

            # 2. Extraer audio
            clip = VideoFileClip(str(video_path))
            if clip.audio is None:
                await message.reply_text("❌ El video no tiene audio.")
                clip.close()
                return
            total_s   = clip.duration
            audio_path = tmp / "audio.mp3"
            clip.audio.write_audiofile(str(audio_path), logger=None)
            clip.close()

            # 3. Transcribir
            await message.reply_text("🎙️ Transcribiendo audio con Whisper…")
            result      = model.transcribe(str(audio_path))
            source_lang = result.get("language", "en")
            segments    = result.get("segments", [])
            if not segments:
                await message.reply_text("❌ No se detectó habla en el video.")
                return

            # 4. Diarización y género (solo para doblaje)
            speaker_ids: list[int] = []
            voice_map:   dict[int, str] = {}

            if output_format in ("dub", "dub_sub"):
                await message.reply_text("🔍 Analizando hablantes…")
                speaker_ids = diarize_speakers(audio_path, segments)

                n_speakers  = len(set(speaker_ids))
                genders     = detect_speaker_genders(audio_path, segments, speaker_ids)

                # Resumen para el usuario
                summary_parts = []
                for spk_id in sorted(genders):
                    label = "mujer 👩" if genders[spk_id] == "female" else "hombre 👨"
                    summary_parts.append(f"  • Hablante {spk_id + 1}: {label}")
                await message.reply_text(
                    f"👥 {n_speakers} hablante(s) detectado(s):\n" + "\n".join(summary_parts)
                )
            else:
                genders    = {}
                speaker_ids = [0] * len(segments)

            # 5. Por cada idioma seleccionado
            for lang_code in selected_languages:
                lang_name = SUPPORTED_LANGUAGES[lang_code]
                await message.reply_text(f"📝 Procesando en {lang_name}…")

                # Construir SRT
                srt_content = build_srt(segments, source_lang, lang_code)
                srt_path    = tmp / f"subtitles_{lang_code}.srt"
                srt_path.write_text(srt_content, encoding="utf-8")

                # ── Solo SRT / SRT+subtítulos ──────────────────────────
                if output_format in ("srt", "both"):
                    await message.reply_document(
                        document=srt_path,
                        caption=f"📄 Subtítulos en {lang_name}",
                    )

                # ── Video con subtítulos quemados ──────────────────────
                if output_format in ("video", "both"):
                    await message.reply_text(f"🎬 Quemando subtítulos en {lang_name}…")
                    burned = tmp / f"burned_{lang_code}.mp4"
                    try:
                        burn_subtitles(video_path, srt_path, burned)
                        await _send_video(message, burned, f"🎬 Video con subtítulos en {lang_name}")
                    except Exception as e:
                        await message.reply_text(f"❌ Error quemando subtítulos: {e}")

                # ── Video doblado ──────────────────────────────────────
                if output_format in ("dub", "dub_sub"):
                    # Asignar voces (por idioma)
                    voice_map = assign_speaker_voices(speaker_ids, genders, lang_code)

                    await message.reply_text(
                        f"🗣️ Generando doblaje en {lang_name}…\n"
                        + "\n".join(
                            f"  • Hablante {spk + 1}: {v.split('-')[2]}"
                            for spk, v in sorted(voice_map.items())
                        )
                    )
                    try:
                        dubbed_audio = await generate_dubbed_audio(
                            segments, lang_code, speaker_ids, voice_map, total_s, tmp
                        )
                        dubbed_video = tmp / f"dubbed_{lang_code}.mp4"
                        mix_audio_into_video(video_path, dubbed_audio, dubbed_video)

                        final = dubbed_video
                        if output_format == "dub_sub":
                            dubbed_sub = tmp / f"dubbed_sub_{lang_code}.mp4"
                            burn_subtitles(dubbed_video, srt_path, dubbed_sub)
                            final = dubbed_sub

                        await _send_video(message, final, f"🗣️ Video doblado en {lang_name}")

                    except Exception as e:
                        logger.error(f"Error doblando: {e}", exc_info=True)
                        await message.reply_text(f"❌ Error generando doblaje: {e}")

            await message.reply_text("✅ ¡Todo listo! Envía otro video cuando quieras.")

    except Exception as e:
        logger.error(f"Error procesando: {e}", exc_info=True)
        await message.reply_text(f"❌ Error inesperado: {e}")


async def _send_video(message, path: Path, caption: str) -> None:
    if not path.exists():
        await message.reply_text(f"❌ No se generó el archivo: {caption}")
        return
    size_mb = path.stat().st_size / 1024 / 1024
    if path.stat().st_size > TELEGRAM_MAX_BYTES:
        await message.reply_text(
            f"⚠️ {caption}: el archivo pesa {size_mb:.1f} MB y supera el límite de 50 MB de Telegram."
        )
    else:
        await message.reply_video(video=path, caption=caption, supports_streaming=True)


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
