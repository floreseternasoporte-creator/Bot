"""
Telegram Bot — Media Manager con almacenamiento nativo de Telegram
===================================================================
• No requiere Google Drive ni ninguna nube externa
• Los archivos se almacenan en los servidores de Telegram (file_id)
• Sistema de álbumes estilo Google Photos
• Corre permanentemente en Hugging Face Spaces o cualquier servidor
"""

import os
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
_data_dir = os.environ.get("DATA_DIR", "/app/data")
DB_PATH = os.path.join(_data_dir, "media.db")

# ── Estados del ConversationHandler ───────────────────────────────────────────
(ASK_ALBUM_NAME,) = range(1)

# ── Base de datos SQLite ───────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS albums (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id  INTEGER NOT NULL,
            name         TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            cover_file_id TEXT
        );

        CREATE TABLE IF NOT EXISTS media (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id     INTEGER NOT NULL,
            album_id        INTEGER,
            file_id         TEXT NOT NULL,
            file_unique_id  TEXT NOT NULL,
            media_type      TEXT NOT NULL,
            file_name       TEXT,
            mime_type       TEXT,
            size_bytes      INTEGER DEFAULT 0,
            width           INTEGER,
            height          INTEGER,
            duration        INTEGER,
            uploaded_at     TEXT NOT NULL,
            FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE SET NULL
        );
    """)
    con.commit()
    con.close()
    log.info("Base de datos inicializada")

# ── Queries ────────────────────────────────────────────────────────────────────
def get_albums(telegram_id: int):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT a.*, COUNT(m.id) as total FROM albums a "
        "LEFT JOIN media m ON m.album_id = a.id "
        "WHERE a.telegram_id=? GROUP BY a.id ORDER BY a.created_at DESC",
        (telegram_id,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

def get_album(album_id: int, telegram_id: int):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM albums WHERE id=? AND telegram_id=?",
        (album_id, telegram_id)
    ).fetchone()
    con.close()
    return dict(row) if row else None

def create_album(telegram_id: int, name: str) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "INSERT INTO albums (telegram_id, name, created_at) VALUES (?,?,?)",
        (telegram_id, name, datetime.utcnow().isoformat())
    )
    album_id = cur.lastrowid
    con.commit(); con.close()
    return album_id

def delete_album(album_id: int, telegram_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE media SET album_id=NULL WHERE album_id=? AND telegram_id=?",
                (album_id, telegram_id))
    con.execute("DELETE FROM albums WHERE id=? AND telegram_id=?",
                (album_id, telegram_id))
    con.commit(); con.close()

def add_media(telegram_id, album_id, file_id, file_unique_id, media_type,
              file_name, mime_type, size_bytes, width=None, height=None, duration=None):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO media (telegram_id,album_id,file_id,file_unique_id,media_type,"
        "file_name,mime_type,size_bytes,width,height,duration,uploaded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (telegram_id, album_id, file_id, file_unique_id, media_type,
         file_name, mime_type, size_bytes, width, height, duration,
         datetime.utcnow().isoformat())
    )
    # Si el álbum no tiene portada, asignarla
    if album_id:
        con.execute(
            "UPDATE albums SET cover_file_id=? WHERE id=? AND cover_file_id IS NULL",
            (file_id, album_id)
        )
    con.commit(); con.close()

def get_media(telegram_id: int, album_id=None, media_type=None, limit=20, offset=0):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    conds = ["telegram_id=?"]
    params = [telegram_id]
    if album_id == "none":
        conds.append("album_id IS NULL")
    elif album_id is not None:
        conds.append("album_id=?"); params.append(album_id)
    if media_type:
        conds.append("media_type=?"); params.append(media_type)
    where = " AND ".join(conds)
    rows = con.execute(
        f"SELECT * FROM media WHERE {where} ORDER BY uploaded_at DESC LIMIT ? OFFSET ?",
        (*params, limit, offset)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

def count_media(telegram_id: int, album_id=None, media_type=None):
    con = sqlite3.connect(DB_PATH)
    conds = ["telegram_id=?"]
    params = [telegram_id]
    if album_id == "none":
        conds.append("album_id IS NULL")
    elif album_id is not None:
        conds.append("album_id=?"); params.append(album_id)
    if media_type:
        conds.append("media_type=?"); params.append(media_type)
    where = " AND ".join(conds)
    count = con.execute(f"SELECT COUNT(*) FROM media WHERE {where}", params).fetchone()[0]
    con.close()
    return count

def get_stats(telegram_id: int):
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT "
        "COUNT(*) as total, "
        "SUM(CASE WHEN media_type='photo' THEN 1 ELSE 0 END) as photos, "
        "SUM(CASE WHEN media_type='video' THEN 1 ELSE 0 END) as videos, "
        "SUM(CASE WHEN media_type='document' THEN 1 ELSE 0 END) as docs, "
        "SUM(CASE WHEN media_type='audio' THEN 1 ELSE 0 END) as audios, "
        "SUM(size_bytes) as total_size "
        "FROM media WHERE telegram_id=?",
        (telegram_id,)
    ).fetchone()
    albums = con.execute(
        "SELECT COUNT(*) FROM albums WHERE telegram_id=?", (telegram_id,)
    ).fetchone()[0]
    con.close()
    return {
        "total": row[0] or 0, "photos": row[1] or 0, "videos": row[2] or 0,
        "docs": row[3] or 0, "audios": row[4] or 0,
        "total_size": row[5] or 0, "albums": albums
    }

def delete_media(media_id: int, telegram_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM media WHERE id=? AND telegram_id=?", (media_id, telegram_id))
    con.commit(); con.close()

def move_to_album(file_unique_id: str, telegram_id: int, album_id):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "UPDATE media SET album_id=? WHERE file_unique_id=? AND telegram_id=?",
        (album_id, file_unique_id, telegram_id)
    )
    con.commit(); con.close()

def get_pending_album(telegram_id: int):
    """Obtiene file_unique_id pendiente de asignar a álbum."""
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT file_unique_id FROM media WHERE telegram_id=? ORDER BY id DESC LIMIT 1",
        (telegram_id,)
    ).fetchone()
    con.close()
    return row[0] if row else None

# ── Helpers ────────────────────────────────────────────────────────────────────
def fmt_size(b: int) -> str:
    if b < 1024: return f"{b} B"
    if b < 1024**2: return f"{b//1024} KB"
    return f"{b//1024**2} MB"

def fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return iso[:16]

PAGE_SIZE = 10

# ── /start ─────────────────────────────────────────────────────────────────────
async def cmd_start(update, ctx):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = [
        [InlineKeyboardButton("📸 Mis fotos",    callback_data="browse:photo:0"),
         InlineKeyboardButton("🎬 Mis videos",   callback_data="browse:video:0")],
        [InlineKeyboardButton("🗂 Álbumes",       callback_data="albums_menu"),
         InlineKeyboardButton("📊 Estadísticas", callback_data="stats")],
        [InlineKeyboardButton("❓ Ayuda",         callback_data="help")],
    ]
    await update.message.reply_text(
        "📱 *Bienvenido a tu galería de Telegram*\n\n"
        "Envíame fotos, videos, audios o documentos y los organizo por ti.\n"
        "Crea álbumes para agrupar tu contenido. ¡Todo queda guardado en Telegram!\n\n"
        "👇 Elige una opción:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ── /ayuda ─────────────────────────────────────────────────────────────────────
async def cmd_ayuda(update, ctx):
    await update.message.reply_text(
        "📚 *Cómo usar el bot:*\n\n"
        "1️⃣ Envía fotos, videos o archivos directamente.\n"
        "2️⃣ Tras recibir el archivo, elige en qué álbum guardarlo.\n"
        "3️⃣ Explora tu galería con /galeria o los botones.\n\n"
        "📌 *Comandos:*\n"
        "/start — Menú principal\n"
        "/galeria — Ver toda la galería\n"
        "/albumes — Gestionar álbumes\n"
        "/nuevo\\_album — Crear un álbum\n"
        "/stats — Estadísticas\n"
        "/ayuda — Esta ayuda\n\n"
        "💡 *Tip:* Puedes enviar varias fotos a la vez y elegir el álbum.",
        parse_mode="Markdown"
    )

async def cb_help(update, ctx):
    await update.callback_query.answer()
    await cmd_ayuda(update, ctx)

# ── Recibir media ──────────────────────────────────────────────────────────────
async def _save_media(update, ctx, media_type: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    tid = update.effective_user.id
    msg = update.message

    # Extraer datos según tipo
    if media_type == "photo":
        obj = msg.photo[-1]
        file_id, file_unique_id = obj.file_id, obj.file_unique_id
        width, height = obj.width, obj.height
        size_bytes = obj.file_size or 0
        file_name, mime_type, duration = "photo.jpg", "image/jpeg", None

    elif media_type == "video":
        obj = msg.video
        file_id, file_unique_id = obj.file_id, obj.file_unique_id
        width, height, duration = obj.width, obj.height, obj.duration
        size_bytes = obj.file_size or 0
        file_name = obj.file_name or "video.mp4"
        mime_type = obj.mime_type or "video/mp4"

    elif media_type == "audio":
        obj = msg.audio or msg.voice
        file_id, file_unique_id = obj.file_id, obj.file_unique_id
        width, height, duration = None, None, obj.duration
        size_bytes = obj.file_size or 0
        file_name = getattr(obj, "file_name", None) or "audio.ogg"
        mime_type = getattr(obj, "mime_type", None) or "audio/ogg"

    else:  # document
        obj = msg.document
        file_id, file_unique_id = obj.file_id, obj.file_unique_id
        width, height, duration = None, None, None
        size_bytes = obj.file_size or 0
        file_name = obj.file_name or "archivo"
        mime_type = obj.mime_type or "application/octet-stream"

    # Guardar sin álbum primero
    add_media(tid, None, file_id, file_unique_id, media_type,
              file_name, mime_type, size_bytes, width, height, duration)

    # Guardar en ctx para asignar álbum
    ctx.user_data["last_file_unique_id"] = file_unique_id
    ctx.user_data["last_media_type"] = media_type

    # Botones: álbumes existentes + sin álbum + nuevo álbum
    albums = get_albums(tid)
    kb = []
    row = []
    for a in albums[:6]:
        row.append(InlineKeyboardButton(
            f"🗂 {a['name']} ({a['total']})",
            callback_data=f"assign:{file_unique_id}:{a['id']}"
        ))
        if len(row) == 2:
            kb.append(row); row = []
    if row:
        kb.append(row)
    kb.append([
        InlineKeyboardButton("📁 Sin álbum",    callback_data=f"assign:{file_unique_id}:none"),
        InlineKeyboardButton("➕ Nuevo álbum",  callback_data=f"newalbum:{file_unique_id}")
    ])

    ICONS = {"photo": "🖼", "video": "🎬", "audio": "🎵", "document": "📄"}
    icon = ICONS.get(media_type, "📁")

    await msg.reply_text(
        f"{icon} *{file_name}* guardado\\!\n"
        f"📏 {fmt_size(size_bytes)}\n\n"
        "¿En qué álbum lo guardamos?",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def handle_photo(update, ctx):    await _save_media(update, ctx, "photo")
async def handle_video(update, ctx):    await _save_media(update, ctx, "video")
async def handle_audio(update, ctx):    await _save_media(update, ctx, "audio")
async def handle_document(update, ctx): await _save_media(update, ctx, "document")

# ── Asignar a álbum ────────────────────────────────────────────────────────────
async def cb_assign_album(update, ctx):
    await update.callback_query.answer()
    tid = update.effective_user.id
    _, file_unique_id, album_raw = update.callback_query.data.split(":", 2)
    album_id = None if album_raw == "none" else int(album_raw)
    move_to_album(file_unique_id, tid, album_id)

    if album_id:
        album = get_album(album_id, tid)
        name = album["name"] if album else "álbum"
        text = f"✅ Añadido al álbum *{name}*"
    else:
        text = "✅ Guardado sin álbum"

    await update.callback_query.message.edit_text(text, parse_mode="Markdown")

# ── Crear álbum desde media ────────────────────────────────────────────────────
async def cb_newalbum_for_file(update, ctx):
    await update.callback_query.answer()
    _, file_unique_id = update.callback_query.data.split(":", 1)
    ctx.user_data["pending_file_for_album"] = file_unique_id
    await update.callback_query.message.reply_text(
        "📝 ¿Cómo se llamará el nuevo álbum?"
    )
    return ASK_ALBUM_NAME

async def recv_album_name(update, ctx):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import ConversationHandler
    tid = update.effective_user.id
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("El nombre no puede estar vacío. Intenta de nuevo:")
        return ASK_ALBUM_NAME
    album_id = create_album(tid, name)
    file_unique_id = ctx.user_data.pop("pending_file_for_album", None)
    if file_unique_id:
        move_to_album(file_unique_id, tid, album_id)
    ctx.user_data.clear()
    kb = [[InlineKeyboardButton(f"📂 Ver {name}", callback_data=f"album:{album_id}:0")]]
    await update.message.reply_text(
        f"✅ Álbum *{name}* creado y archivo añadido\\!",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return ConversationHandler.END

async def cancel_conv(update, ctx):
    from telegram.ext import ConversationHandler
    ctx.user_data.clear()
    await update.message.reply_text("Cancelado.")
    return ConversationHandler.END

# ── /nuevo_album ───────────────────────────────────────────────────────────────
async def cmd_nuevo_album(update, ctx):
    ctx.user_data["pending_file_for_album"] = None
    await update.message.reply_text("📝 ¿Cómo se llamará el nuevo álbum?")
    return ASK_ALBUM_NAME

# ── /albumes ──────────────────────────────────────────────────────────────────
async def cmd_albumes(update, ctx):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    tid = update.effective_user.id
    albums = get_albums(tid)
    if not albums:
        kb = [[InlineKeyboardButton("➕ Crear álbum", callback_data="create_album_menu")]]
        await update.message.reply_text(
            "No tienes álbumes aún. ¡Crea el primero!",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    kb = []
    for a in albums:
        kb.append([InlineKeyboardButton(
            f"🗂 {a['name']} · {a['total']} archivos",
            callback_data=f"album:{a['id']}:0"
        )])
    kb.append([
        InlineKeyboardButton("➕ Nuevo álbum", callback_data="create_album_menu"),
        InlineKeyboardButton("🏠 Inicio",       callback_data="main_menu")
    ])
    await update.message.reply_text(
        f"🗂 *Tus álbumes* ({len(albums)})\n\nElige uno para ver su contenido:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cb_albums_menu(update, ctx):
    await update.callback_query.answer()
    tid = update.effective_user.id
    albums = get_albums(tid)
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    if not albums:
        kb = [[InlineKeyboardButton("➕ Crear álbum", callback_data="create_album_menu"),
               InlineKeyboardButton("🏠 Inicio",       callback_data="main_menu")]]
        await update.callback_query.message.edit_text(
            "No tienes álbumes aún. ¡Crea el primero!",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    kb = []
    for a in albums:
        kb.append([InlineKeyboardButton(
            f"🗂 {a['name']} · {a['total']} archivos",
            callback_data=f"album:{a['id']}:0"
        )])
    kb.append([
        InlineKeyboardButton("➕ Nuevo álbum", callback_data="create_album_menu"),
        InlineKeyboardButton("🏠 Inicio",       callback_data="main_menu")
    ])
    await update.callback_query.message.edit_text(
        f"🗂 *Tus álbumes* ({len(albums)})\n\nElige uno para ver su contenido:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cb_create_album_menu(update, ctx):
    await update.callback_query.answer()
    ctx.user_data["pending_file_for_album"] = None
    await update.callback_query.message.reply_text(
        "📝 ¿Cómo se llamará el nuevo álbum?\n\nEscribe el nombre:"
    )
    return ASK_ALBUM_NAME

# ── Ver álbum ──────────────────────────────────────────────────────────────────
async def cb_album_view(update, ctx):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    await update.callback_query.answer()
    tid = update.effective_user.id
    parts = update.callback_query.data.split(":")  # album:ID:PAGE
    album_id, page = int(parts[1]), int(parts[2])
    album = get_album(album_id, tid)
    if not album:
        await update.callback_query.message.edit_text("Álbum no encontrado.")
        return

    offset = page * PAGE_SIZE
    items = get_media(tid, album_id=album_id, limit=PAGE_SIZE, offset=offset)
    total = count_media(tid, album_id=album_id)

    if not items:
        kb = [[InlineKeyboardButton("🗂 Mis álbumes", callback_data="albums_menu"),
               InlineKeyboardButton("🗑 Eliminar álbum", callback_data=f"del_album:{album_id}")]]
        await update.callback_query.message.edit_text(
            f"📂 *{album['name']}*\n\nEste álbum está vacío.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    # Cabecera
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    header = (
        f"📂 *{album['name']}*\n"
        f"📅 Creado: {fmt_date(album['created_at'])}\n"
        f"📦 {total} archivos · Página {page+1}/{pages}\n\n"
    )
    ICONS = {"photo": "🖼", "video": "🎬", "audio": "🎵", "document": "📄"}
    lines = []
    for it in items:
        icon = ICONS.get(it["media_type"], "📁")
        lines.append(f"{icon} `{it['file_name'] or it['media_type']}` · {fmt_date(it['uploaded_at'])}")
    text = header + "\n".join(lines)

    # Navegación
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Anterior", callback_data=f"album:{album_id}:{page-1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Siguiente ▶", callback_data=f"album:{album_id}:{page+1}"))

    kb = []
    if nav: kb.append(nav)
    kb.append([
        InlineKeyboardButton("📤 Ver archivos", callback_data=f"send_album:{album_id}:{page}"),
        InlineKeyboardButton("🗑 Eliminar álbum", callback_data=f"del_album:{album_id}")
    ])
    kb.append([
        InlineKeyboardButton("🗂 Mis álbumes", callback_data="albums_menu"),
        InlineKeyboardButton("🏠 Inicio",       callback_data="main_menu")
    ])
    await update.callback_query.message.edit_text(
        text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
    )

async def cb_send_album(update, ctx):
    """Envía los archivos del álbum como mensajes de Telegram."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    await update.callback_query.answer("Enviando archivos…")
    tid = update.effective_user.id
    parts = update.callback_query.data.split(":")
    album_id, page = int(parts[1]), int(parts[2])
    album = get_album(album_id, tid)
    if not album:
        return
    offset = page * PAGE_SIZE
    items = get_media(tid, album_id=album_id, limit=PAGE_SIZE, offset=offset)
    chat_id = update.effective_chat.id
    for it in items:
        caption = f"📂 {album['name']} · {fmt_date(it['uploaded_at'])}"
        try:
            if it["media_type"] == "photo":
                await ctx.bot.send_photo(chat_id, it["file_id"], caption=caption)
            elif it["media_type"] == "video":
                await ctx.bot.send_video(chat_id, it["file_id"], caption=caption)
            elif it["media_type"] == "audio":
                await ctx.bot.send_audio(chat_id, it["file_id"], caption=caption)
            else:
                await ctx.bot.send_document(chat_id, it["file_id"], caption=caption)
        except Exception as e:
            log.warning(f"No se pudo reenviar {it['file_id']}: {e}")

# ── Eliminar álbum ─────────────────────────────────────────────────────────────
async def cb_delete_album(update, ctx):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    await update.callback_query.answer()
    album_id = int(update.callback_query.data.split(":")[1])
    tid = update.effective_user.id
    album = get_album(album_id, tid)
    if not album:
        await update.callback_query.message.edit_text("Álbum no encontrado.")
        return
    kb = [[
        InlineKeyboardButton("✅ Sí, eliminar", callback_data=f"confirm_del_album:{album_id}"),
        InlineKeyboardButton("❌ Cancelar",     callback_data=f"album:{album_id}:0")
    ]]
    await update.callback_query.message.edit_text(
        f"⚠️ ¿Eliminar el álbum *{album['name']}*?\n\n"
        "Los archivos no se borran, solo se quitan del álbum.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cb_confirm_delete_album(update, ctx):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    await update.callback_query.answer()
    album_id = int(update.callback_query.data.split(":")[1])
    tid = update.effective_user.id
    album = get_album(album_id, tid)
    name = album["name"] if album else "álbum"
    delete_album(album_id, tid)
    kb = [[InlineKeyboardButton("🗂 Mis álbumes", callback_data="albums_menu")]]
    await update.callback_query.message.edit_text(
        f"🗑 Álbum *{name}* eliminado.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ── /galeria ──────────────────────────────────────────────────────────────────
async def cmd_galeria(update, ctx):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = [
        [InlineKeyboardButton("🖼 Fotos",       callback_data="browse:photo:0"),
         InlineKeyboardButton("🎬 Videos",      callback_data="browse:video:0")],
        [InlineKeyboardButton("🎵 Audios",      callback_data="browse:audio:0"),
         InlineKeyboardButton("📄 Documentos",  callback_data="browse:document:0")],
        [InlineKeyboardButton("📦 Todo",        callback_data="browse:all:0"),
         InlineKeyboardButton("🗂 Álbumes",     callback_data="albums_menu")],
    ]
    await update.message.reply_text(
        "🖼 *Tu galería*\n\nElige qué quieres explorar:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cb_browse(update, ctx):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    await update.callback_query.answer()
    tid = update.effective_user.id
    _, mtype, pg = update.callback_query.data.split(":")
    page = int(pg)
    mt = None if mtype == "all" else mtype
    offset = page * PAGE_SIZE
    items = get_media(tid, media_type=mt, limit=PAGE_SIZE, offset=offset)
    total = count_media(tid, media_type=mt)

    LABELS = {"photo": "Fotos 🖼", "video": "Videos 🎬",
              "audio": "Audios 🎵", "document": "Documentos 📄", "all": "Todo 📦"}
    label = LABELS.get(mtype, "Archivos")
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    if not items:
        await update.callback_query.message.edit_text(
            f"No tienes {label.lower()} aún.\nEnvíame archivos y los organizaré aquí."
        )
        return

    ICONS = {"photo": "🖼", "video": "🎬", "audio": "🎵", "document": "📄"}
    lines = [f"*{label}* — {total} archivos · Página {page+1}/{pages}\n"]
    for it in items:
        icon = ICONS.get(it["media_type"], "📁")
        album_label = ""
        lines.append(
            f"{icon} `{it['file_name'] or it['media_type']}`\n"
            f"   📅 {fmt_date(it['uploaded_at'])} · {fmt_size(it['size_bytes'])}"
        )
    text = "\n".join(lines)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Anterior", callback_data=f"browse:{mtype}:{page-1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Siguiente ▶", callback_data=f"browse:{mtype}:{page+1}"))

    kb = []
    if nav: kb.append(nav)
    kb.append([
        InlineKeyboardButton("📤 Enviar esta página", callback_data=f"send_browse:{mtype}:{page}"),
        InlineKeyboardButton("🏠 Inicio",              callback_data="main_menu")
    ])
    await update.callback_query.message.edit_text(
        text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
    )

async def cb_send_browse(update, ctx):
    """Envía los archivos de la página actual de galería."""
    await update.callback_query.answer("Enviando…")
    tid = update.effective_user.id
    _, mtype, pg = update.callback_query.data.split(":")
    page = int(pg)
    mt = None if mtype == "all" else mtype
    offset = page * PAGE_SIZE
    items = get_media(tid, media_type=mt, limit=PAGE_SIZE, offset=offset)
    chat_id = update.effective_chat.id
    for it in items:
        caption = f"{fmt_date(it['uploaded_at'])} · {fmt_size(it['size_bytes'])}"
        try:
            if it["media_type"] == "photo":
                await ctx.bot.send_photo(chat_id, it["file_id"], caption=caption)
            elif it["media_type"] == "video":
                await ctx.bot.send_video(chat_id, it["file_id"], caption=caption)
            elif it["media_type"] == "audio":
                await ctx.bot.send_audio(chat_id, it["file_id"], caption=caption)
            else:
                await ctx.bot.send_document(chat_id, it["file_id"], caption=caption)
        except Exception as e:
            log.warning(f"No se pudo reenviar {it['file_id']}: {e}")

# ── Estadísticas ───────────────────────────────────────────────────────────────
async def cmd_stats(update, ctx):
    tid = update.effective_user.id
    s = get_stats(tid)
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = [[InlineKeyboardButton("🏠 Inicio", callback_data="main_menu")]]
    text = (
        "📊 *Tus estadísticas*\n\n"
        f"🖼 Fotos:       {s['photos']}\n"
        f"🎬 Videos:      {s['videos']}\n"
        f"🎵 Audios:      {s['audios']}\n"
        f"📄 Documentos:  {s['docs']}\n"
        f"📦 Total:       {s['total']}\n"
        f"🗂 Álbumes:     {s['albums']}\n"
        f"💾 Tamaño total: {fmt_size(s['total_size'])}\n\n"
        "_Todo almacenado en los servidores de Telegram_"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown",
                                         reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.callback_query.message.edit_text(text, parse_mode="Markdown",
                                                       reply_markup=InlineKeyboardMarkup(kb))

async def cb_stats(update, ctx):
    await update.callback_query.answer()
    await cmd_stats(update, ctx)

# ── Menú principal ─────────────────────────────────────────────────────────────
async def cb_main_menu(update, ctx):
    await update.callback_query.answer()
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = [
        [InlineKeyboardButton("📸 Fotos",       callback_data="browse:photo:0"),
         InlineKeyboardButton("🎬 Videos",      callback_data="browse:video:0")],
        [InlineKeyboardButton("🗂 Álbumes",     callback_data="albums_menu"),
         InlineKeyboardButton("📊 Estadísticas", callback_data="stats")],
        [InlineKeyboardButton("❓ Ayuda",        callback_data="help")],
    ]
    await update.callback_query.message.edit_text(
        "📱 *Menú principal*\n\nEnvíame archivos o elige una opción:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ── Main ───────────────────────────────────────────────────────────────────────
def _build_app():
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        CallbackQueryHandler, ConversationHandler, filters
    )

    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN no configurado")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # ConversationHandler para crear álbum
    # per_message=True evita el warning y funciona correctamente con callbacks
    album_conv = ConversationHandler(
        entry_points=[
            CommandHandler("nuevo_album", cmd_nuevo_album),
            CallbackQueryHandler(cb_newalbum_for_file, pattern=r"^newalbum:"),
            CallbackQueryHandler(cb_create_album_menu, pattern="^create_album_menu$"),
        ],
        states={
            ASK_ALBUM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_album_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_message=False,
        per_chat=True,
    )

    application.add_handler(album_conv)

    # Comandos
    application.add_handler(CommandHandler("start",        cmd_start))
    application.add_handler(CommandHandler("ayuda",        cmd_ayuda))
    application.add_handler(CommandHandler("galeria",      cmd_galeria))
    application.add_handler(CommandHandler("albumes",      cmd_albumes))
    application.add_handler(CommandHandler("stats",        cmd_stats))

    # Callbacks
    application.add_handler(CallbackQueryHandler(cb_main_menu,            pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(cb_help,                 pattern="^help$"))
    application.add_handler(CallbackQueryHandler(cb_stats,                pattern="^stats$"))
    application.add_handler(CallbackQueryHandler(cb_albums_menu,          pattern="^albums_menu$"))
    application.add_handler(CallbackQueryHandler(cb_album_view,           pattern=r"^album:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(cb_send_album,           pattern=r"^send_album:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(cb_delete_album,         pattern=r"^del_album:\d+$"))
    application.add_handler(CallbackQueryHandler(cb_confirm_delete_album, pattern=r"^confirm_del_album:\d+$"))
    application.add_handler(CallbackQueryHandler(cb_assign_album,         pattern=r"^assign:"))
    application.add_handler(CallbackQueryHandler(cb_browse,               pattern=r"^browse:"))
    application.add_handler(CallbackQueryHandler(cb_send_browse,          pattern=r"^send_browse:"))

    # Media
    application.add_handler(MessageHandler(filters.PHOTO,                    handle_photo))
    application.add_handler(MessageHandler(filters.VIDEO,                    handle_video))
    application.add_handler(MessageHandler(filters.AUDIO | filters.VOICE,    handle_audio))
    application.add_handler(MessageHandler(filters.Document.ALL,             handle_document))

    return application


async def main_async():
    """Corrutina principal — llamada desde app.py con su propio event loop."""
    application = _build_app()
    log.info("Bot iniciado con polling")
    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        # Espera indefinida hasta señal de cierre
        import asyncio as _asyncio
        stop_event = _asyncio.Event()
        await stop_event.wait()


if __name__ == "__main__":
    import asyncio as _asyncio
    logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
    init_db()
    _asyncio.run(main_async())
