
"""

import os
import asyncio
import logging
import sqlite3
from datetime import datetime

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
DB_PATH        = os.environ.get("DB_PATH", "media.db")
PAGE_SIZE      = 10

# ── Estado del ConversationHandler ────────────────────────────────────────────
(ASK_ALBUM_NAME,) = range(1)

# ── Base de datos ──────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS albums (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id   INTEGER NOT NULL,
            name          TEXT    NOT NULL,
            created_at    TEXT    NOT NULL,
            cover_file_id TEXT
        );
        CREATE TABLE IF NOT EXISTS media (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id    INTEGER NOT NULL,
            album_id       INTEGER,
            file_id        TEXT NOT NULL,
            file_unique_id TEXT NOT NULL,
            media_type     TEXT NOT NULL,
            file_name      TEXT,
            mime_type      TEXT,
            size_bytes     INTEGER DEFAULT 0,
            width          INTEGER,
            height         INTEGER,
            duration       INTEGER,
            uploaded_at    TEXT NOT NULL,
            FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE SET NULL
        );
    """)
    con.commit()
    con.close()
    log.info("DB lista en %s", DB_PATH)

# ── Helpers DB ────────────────────────────────────────────────────────────────
def _con():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def get_albums(tid):
    with _con() as c:
        rows = c.execute(
            "SELECT a.*, COUNT(m.id) as total FROM albums a "
            "LEFT JOIN media m ON m.album_id=a.id "
            "WHERE a.telegram_id=? GROUP BY a.id ORDER BY a.created_at DESC", (tid,)
        ).fetchall()
    return [dict(r) for r in rows]

def get_album(album_id, tid):
    with _con() as c:
        row = c.execute(
            "SELECT * FROM albums WHERE id=? AND telegram_id=?", (album_id, tid)
        ).fetchone()
    return dict(row) if row else None

def create_album(tid, name):
    with _con() as c:
        cur = c.execute(
            "INSERT INTO albums (telegram_id,name,created_at) VALUES (?,?,?)",
            (tid, name, datetime.utcnow().isoformat())
        )
        return cur.lastrowid

def delete_album(album_id, tid):
    with _con() as c:
        c.execute("UPDATE media SET album_id=NULL WHERE album_id=? AND telegram_id=?", (album_id, tid))
        c.execute("DELETE FROM albums WHERE id=? AND telegram_id=?", (album_id, tid))

def add_media(tid, album_id, file_id, file_unique_id, media_type,
              file_name, mime_type, size_bytes, width=None, height=None, duration=None):
    with _con() as c:
        c.execute(
            "INSERT INTO media (telegram_id,album_id,file_id,file_unique_id,media_type,"
            "file_name,mime_type,size_bytes,width,height,duration,uploaded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, album_id, file_id, file_unique_id, media_type,
             file_name, mime_type, size_bytes, width, height, duration,
             datetime.utcnow().isoformat())
        )
        if album_id:
            c.execute(
                "UPDATE albums SET cover_file_id=? WHERE id=? AND cover_file_id IS NULL",
                (file_id, album_id)
            )

def get_media(tid, album_id=None, media_type=None, limit=PAGE_SIZE, offset=0):
    conds, params = ["telegram_id=?"], [tid]
    if album_id == "none":
        conds.append("album_id IS NULL")
    elif album_id is not None:
        conds.append("album_id=?"); params.append(album_id)
    if media_type:
        conds.append("media_type=?"); params.append(media_type)
    with _con() as c:
        rows = c.execute(
            f"SELECT * FROM media WHERE {' AND '.join(conds)} "
            f"ORDER BY uploaded_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset)
        ).fetchall()
    return [dict(r) for r in rows]

def count_media(tid, album_id=None, media_type=None):
    conds, params = ["telegram_id=?"], [tid]
    if album_id == "none":
        conds.append("album_id IS NULL")
    elif album_id is not None:
        conds.append("album_id=?"); params.append(album_id)
    if media_type:
        conds.append("media_type=?"); params.append(media_type)
    with _con() as c:
        return c.execute(
            f"SELECT COUNT(*) FROM media WHERE {' AND '.join(conds)}", params
        ).fetchone()[0]

def get_stats(tid):
    with _con() as c:
        r = c.execute(
            "SELECT COUNT(*) total,"
            "SUM(media_type='photo') photos,"
            "SUM(media_type='video') videos,"
            "SUM(media_type='document') docs,"
            "SUM(media_type='audio') audios,"
            "SUM(size_bytes) total_size "
            "FROM media WHERE telegram_id=?", (tid,)
        ).fetchone()
        albums = c.execute(
            "SELECT COUNT(*) FROM albums WHERE telegram_id=?", (tid,)
        ).fetchone()[0]
    return {
        "total": r[0] or 0, "photos": r[1] or 0, "videos": r[2] or 0,
        "docs": r[3] or 0, "audios": r[4] or 0,
        "total_size": r[5] or 0, "albums": albums
    }

def move_to_album(file_unique_id, tid, album_id):
    with _con() as c:
        c.execute(
            "UPDATE media SET album_id=? WHERE file_unique_id=? AND telegram_id=?",
            (album_id, file_unique_id, tid)
        )

# ── Formato ────────────────────────────────────────────────────────────────────
def fmt_size(b):
    if not b: return "0 B"
    if b < 1024: return f"{b} B"
    if b < 1024**2: return f"{b//1024} KB"
    return f"{b//1024**2} MB"

def fmt_date(iso):
    try: return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except Exception: return iso[:16]

# ── Handlers ───────────────────────────────────────────────────────────────────
async def cmd_start(update, ctx):
    from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Mkp
    kb = Mkp([
        [Btn("📸 Fotos",        callback_data="browse:photo:0"),
         Btn("🎬 Videos",       callback_data="browse:video:0")],
        [Btn("🗂 Álbumes",      callback_data="albums_menu"),
         Btn("📊 Estadísticas", callback_data="stats")],
        [Btn("❓ Ayuda",         callback_data="help")],
    ])
    await update.message.reply_text(
        "📱 *Tu galería personal de Telegram*\n\n"
        "Envíame fotos, videos, audios o documentos y los organizo.\n"
        "Crea álbumes para agrupar tu contenido.\n\n"
        "👇 Elige una opción:",
        parse_mode="Markdown", reply_markup=kb
    )

async def cmd_ayuda(update, ctx):
    text = (
        "📚 *Cómo usar el bot:*\n\n"
        "1️⃣ Envía fotos, videos o archivos.\n"
        "2️⃣ Elige en qué álbum guardarlos.\n"
        "3️⃣ Explora tu galería con /galeria.\n\n"
        "📌 *Comandos:*\n"
        "/start — Menú principal\n"
        "/galeria — Ver galería\n"
        "/albumes — Gestionar álbumes\n"
        "/nuevo\\_album — Crear álbum\n"
        "/stats — Estadísticas\n"
        "/ayuda — Esta ayuda\n\n"
        "💡 Los archivos se guardan en Telegram, no en ningún servidor externo."
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(text, parse_mode="Markdown")

async def cb_help(update, ctx):
    await cmd_ayuda(update, ctx)

# ── Recibir archivos ───────────────────────────────────────────────────────────
async def _save_media(update, ctx, media_type):
    from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Mkp
    tid = update.effective_user.id
    msg = update.message

    if media_type == "photo":
        obj = msg.photo[-1]
        file_id, fuid = obj.file_id, obj.file_unique_id
        w, h, dur = obj.width, obj.height, None
        size = obj.file_size or 0
        fname, mime = "photo.jpg", "image/jpeg"

    elif media_type == "video":
        obj = msg.video
        file_id, fuid = obj.file_id, obj.file_unique_id
        w, h, dur = obj.width, obj.height, obj.duration
        size = obj.file_size or 0
        fname = obj.file_name or "video.mp4"
        mime  = obj.mime_type or "video/mp4"

    elif media_type == "audio":
        obj = msg.audio or msg.voice
        file_id, fuid = obj.file_id, obj.file_unique_id
        w, h, dur = None, None, obj.duration
        size  = obj.file_size or 0
        fname = getattr(obj, "file_name", None) or "audio.ogg"
        mime  = getattr(obj, "mime_type", None) or "audio/ogg"

    else:  # document
        obj = msg.document
        file_id, fuid = obj.file_id, obj.file_unique_id
        w, h, dur = None, None, None
        size  = obj.file_size or 0
        fname = obj.file_name or "archivo"
        mime  = obj.mime_type or "application/octet-stream"

    add_media(tid, None, file_id, fuid, media_type, fname, mime, size, w, h, dur)
    ctx.user_data["last_fuid"] = fuid

    albums = get_albums(tid)
    rows, row = [], []
    for a in albums[:6]:
        row.append(Btn(f"🗂 {a['name']} ({a['total']})", callback_data=f"assign:{fuid}:{a['id']}"))
        if len(row) == 2: rows.append(row); row = []
    if row: rows.append(row)
    rows.append([
        Btn("📁 Sin álbum",   callback_data=f"assign:{fuid}:none"),
        Btn("➕ Nuevo álbum", callback_data=f"newalbum:{fuid}"),
    ])

    ICONS = {"photo": "🖼", "video": "🎬", "audio": "🎵", "document": "📄"}
    await msg.reply_text(
        f"{ICONS.get(media_type,'📁')} *{fname}* guardado\n"
        f"📏 {fmt_size(size)}\n\n¿En qué álbum lo ponemos?",
        parse_mode="Markdown",
        reply_markup=Mkp(rows)
    )

async def handle_photo(u, c):    await _save_media(u, c, "photo")
async def handle_video(u, c):    await _save_media(u, c, "video")
async def handle_audio(u, c):    await _save_media(u, c, "audio")
async def handle_document(u, c): await _save_media(u, c, "document")

# ── Asignar álbum ──────────────────────────────────────────────────────────────
async def cb_assign_album(update, ctx):
    await update.callback_query.answer()
    tid = update.effective_user.id
    _, fuid, album_raw = update.callback_query.data.split(":", 2)
    album_id = None if album_raw == "none" else int(album_raw)
    move_to_album(fuid, tid, album_id)
    if album_id:
        a = get_album(album_id, tid)
        text = f"✅ Añadido al álbum *{a['name'] if a else 'álbum'}*"
    else:
        text = "✅ Guardado sin álbum"
    await update.callback_query.message.edit_text(text, parse_mode="Markdown")

# ── Crear álbum (conversación) ─────────────────────────────────────────────────
async def cb_newalbum_for_file(update, ctx):
    await update.callback_query.answer()
    _, fuid = update.callback_query.data.split(":", 1)
    ctx.user_data["pending_fuid"] = fuid
    await update.callback_query.message.reply_text("📝 ¿Cómo se llamará el álbum?")
    return ASK_ALBUM_NAME

async def cmd_nuevo_album(update, ctx):
    ctx.user_data["pending_fuid"] = None
    await update.message.reply_text("📝 ¿Cómo se llamará el nuevo álbum?")
    return ASK_ALBUM_NAME

async def cb_create_album_menu(update, ctx):
    await update.callback_query.answer()
    ctx.user_data["pending_fuid"] = None
    await update.callback_query.message.reply_text("📝 ¿Cómo se llamará el nuevo álbum?")
    return ASK_ALBUM_NAME

async def recv_album_name(update, ctx):
    from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Mkp
    from telegram.ext import ConversationHandler
    tid  = update.effective_user.id
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("El nombre no puede estar vacío, intenta de nuevo:")
        return ASK_ALBUM_NAME
    album_id = create_album(tid, name)
    fuid = ctx.user_data.pop("pending_fuid", None)
    if fuid:
        move_to_album(fuid, tid, album_id)
    ctx.user_data.clear()
    kb = Mkp([[Btn(f"📂 Ver {name}", callback_data=f"album:{album_id}:0")]])
    await update.message.reply_text(
        f"✅ Álbum *{name}* creado" + (" y archivo añadido." if fuid else "."),
        parse_mode="Markdown", reply_markup=kb
    )
    return ConversationHandler.END

async def cancel_conv(update, ctx):
    from telegram.ext import ConversationHandler
    ctx.user_data.clear()
    await update.message.reply_text("Cancelado.")
    return ConversationHandler.END

# ── Álbumes ────────────────────────────────────────────────────────────────────
async def cmd_albumes(update, ctx):
    from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Mkp
    tid    = update.effective_user.id
    albums = get_albums(tid)
    rows   = [[Btn(f"🗂 {a['name']} · {a['total']} arch.", callback_data=f"album:{a['id']}:0")]
               for a in albums]
    rows.append([Btn("➕ Nuevo álbum", callback_data="create_album_menu"),
                 Btn("🏠 Inicio",      callback_data="main_menu")])
    text = f"🗂 *Tus álbumes* ({len(albums)})\n\nElige uno:" if albums else "No tienes álbumes aún."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=Mkp(rows))

async def cb_albums_menu(update, ctx):
    from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Mkp
    await update.callback_query.answer()
    tid    = update.effective_user.id
    albums = get_albums(tid)
    rows   = [[Btn(f"🗂 {a['name']} · {a['total']} arch.", callback_data=f"album:{a['id']}:0")]
               for a in albums]
    rows.append([Btn("➕ Nuevo álbum", callback_data="create_album_menu"),
                 Btn("🏠 Inicio",      callback_data="main_menu")])
    text = f"🗂 *Tus álbumes* ({len(albums)})\n\nElige uno:" if albums else "No tienes álbumes aún."
    await update.callback_query.message.edit_text(text, parse_mode="Markdown", reply_markup=Mkp(rows))

async def cb_album_view(update, ctx):
    from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Mkp
    await update.callback_query.answer()
    tid = update.effective_user.id
    _, aid, pg = update.callback_query.data.split(":")
    aid, page  = int(aid), int(pg)
    album  = get_album(aid, tid)
    if not album:
        await update.callback_query.message.edit_text("Álbum no encontrado."); return

    offset = page * PAGE_SIZE
    items  = get_media(tid, album_id=aid, limit=PAGE_SIZE, offset=offset)
    total  = count_media(tid, album_id=aid)
    pages  = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    if not items:
        rows = [[Btn("🗂 Álbumes", callback_data="albums_menu"),
                 Btn("🗑 Eliminar", callback_data=f"del_album:{aid}")]]
        await update.callback_query.message.edit_text(
            f"📂 *{album['name']}*\n\nÁlbum vacío.",
            parse_mode="Markdown", reply_markup=Mkp(rows)); return

    ICONS = {"photo": "🖼", "video": "🎬", "audio": "🎵", "document": "📄"}
    lines = [f"📂 *{album['name']}* · {total} arch. · Pág {page+1}/{pages}\n"]
    for it in items:
        lines.append(f"{ICONS.get(it['media_type'],'📁')} `{it['file_name'] or it['media_type']}` · {fmt_date(it['uploaded_at'])}")

    nav = []
    if page > 0:       nav.append(Btn("◀ Anterior", callback_data=f"album:{aid}:{page-1}"))
    if page < pages-1: nav.append(Btn("Siguiente ▶", callback_data=f"album:{aid}:{page+1}"))
    rows = []
    if nav: rows.append(nav)
    rows.append([Btn("📤 Ver archivos",  callback_data=f"send_album:{aid}:{page}"),
                 Btn("🗑 Eliminar álbum", callback_data=f"del_album:{aid}")])
    rows.append([Btn("🗂 Álbumes", callback_data="albums_menu"),
                 Btn("🏠 Inicio",  callback_data="main_menu")])
    await update.callback_query.message.edit_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=Mkp(rows))

async def cb_send_album(update, ctx):
    await update.callback_query.answer("Enviando…")
    tid = update.effective_user.id
    _, aid, pg = update.callback_query.data.split(":")
    items = get_media(tid, album_id=int(aid), limit=PAGE_SIZE, offset=int(pg)*PAGE_SIZE)
    album = get_album(int(aid), tid)
    chat  = update.effective_chat.id
    for it in items:
        cap = f"📂 {album['name'] if album else ''} · {fmt_date(it['uploaded_at'])}"
        try:
            if   it["media_type"] == "photo":    await ctx.bot.send_photo(chat, it["file_id"], caption=cap)
            elif it["media_type"] == "video":    await ctx.bot.send_video(chat, it["file_id"], caption=cap)
            elif it["media_type"] == "audio":    await ctx.bot.send_audio(chat, it["file_id"], caption=cap)
            else:                                await ctx.bot.send_document(chat, it["file_id"], caption=cap)
        except Exception as e:
            log.warning("No se pudo reenviar %s: %s", it["file_id"], e)

async def cb_delete_album(update, ctx):
    from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Mkp
    await update.callback_query.answer()
    tid = update.effective_user.id
    aid = int(update.callback_query.data.split(":")[1])
    a   = get_album(aid, tid)
    if not a:
        await update.callback_query.message.edit_text("Álbum no encontrado."); return
    rows = [[Btn("✅ Sí, eliminar", callback_data=f"confirm_del_album:{aid}"),
             Btn("❌ Cancelar",     callback_data=f"album:{aid}:0")]]
    await update.callback_query.message.edit_text(
        f"⚠️ ¿Eliminar *{a['name']}*?\nLos archivos no se borran, solo se desvinculan.",
        parse_mode="Markdown", reply_markup=Mkp(rows))

async def cb_confirm_delete_album(update, ctx):
    from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Mkp
    await update.callback_query.answer()
    tid = update.effective_user.id
    aid = int(update.callback_query.data.split(":")[1])
    a   = get_album(aid, tid)
    name = a["name"] if a else "álbum"
    delete_album(aid, tid)
    rows = [[Btn("🗂 Mis álbumes", callback_data="albums_menu")]]
    await update.callback_query.message.edit_text(
        f"🗑 Álbum *{name}* eliminado.", parse_mode="Markdown", reply_markup=Mkp(rows))

# ── Galería ────────────────────────────────────────────────────────────────────
async def cmd_galeria(update, ctx):
    from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Mkp
    kb = Mkp([
        [Btn("🖼 Fotos",      callback_data="browse:photo:0"),
         Btn("🎬 Videos",     callback_data="browse:video:0")],
        [Btn("🎵 Audios",     callback_data="browse:audio:0"),
         Btn("📄 Documentos", callback_data="browse:document:0")],
        [Btn("📦 Todo",       callback_data="browse:all:0"),
         Btn("🗂 Álbumes",    callback_data="albums_menu")],
    ])
    await update.message.reply_text("🖼 *Tu galería* — ¿qué quieres explorar?",
                                    parse_mode="Markdown", reply_markup=kb)

async def cb_browse(update, ctx):
    from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Mkp
    await update.callback_query.answer()
    tid = update.effective_user.id
    _, mtype, pg = update.callback_query.data.split(":")
    page = int(pg)
    mt   = None if mtype == "all" else mtype
    items = get_media(tid, media_type=mt, limit=PAGE_SIZE, offset=page*PAGE_SIZE)
    total = count_media(tid, media_type=mt)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    LABELS = {"photo": "Fotos 🖼", "video": "Videos 🎬",
              "audio": "Audios 🎵", "document": "Documentos 📄", "all": "Todo 📦"}
    ICONS  = {"photo": "🖼", "video": "🎬", "audio": "🎵", "document": "📄"}
    label  = LABELS.get(mtype, "Archivos")

    if not items:
        await update.callback_query.message.edit_text(
            f"No tienes {label.lower()} aún.\nEnvíame archivos y aparecerán aquí."); return

    lines = [f"*{label}* — {total} arch. · Pág {page+1}/{pages}\n"]
    for it in items:
        lines.append(
            f"{ICONS.get(it['media_type'],'📁')} `{it['file_name'] or it['media_type']}`\n"
            f"   📅 {fmt_date(it['uploaded_at'])} · {fmt_size(it['size_bytes'])}"
        )

    nav = []
    if page > 0:       nav.append(Btn("◀ Anterior", callback_data=f"browse:{mtype}:{page-1}"))
    if page < pages-1: nav.append(Btn("Siguiente ▶", callback_data=f"browse:{mtype}:{page+1}"))
    rows = []
    if nav: rows.append(nav)
    rows.append([Btn("📤 Enviar página", callback_data=f"send_browse:{mtype}:{page}"),
                 Btn("🏠 Inicio",        callback_data="main_menu")])
    await update.callback_query.message.edit_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=Mkp(rows))

async def cb_send_browse(update, ctx):
    await update.callback_query.answer("Enviando…")
    tid = update.effective_user.id
    _, mtype, pg = update.callback_query.data.split(":")
    mt    = None if mtype == "all" else mtype
    items = get_media(tid, media_type=mt, limit=PAGE_SIZE, offset=int(pg)*PAGE_SIZE)
    chat  = update.effective_chat.id
    for it in items:
        cap = f"{fmt_date(it['uploaded_at'])} · {fmt_size(it['size_bytes'])}"
        try:
            if   it["media_type"] == "photo":    await ctx.bot.send_photo(chat, it["file_id"], caption=cap)
            elif it["media_type"] == "video":    await ctx.bot.send_video(chat, it["file_id"], caption=cap)
            elif it["media_type"] == "audio":    await ctx.bot.send_audio(chat, it["file_id"], caption=cap)
            else:                                await ctx.bot.send_document(chat, it["file_id"], caption=cap)
        except Exception as e:
            log.warning("No se pudo reenviar %s: %s", it["file_id"], e)

# ── Estadísticas ───────────────────────────────────────────────────────────────
async def cmd_stats(update, ctx):
    from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Mkp
    tid = update.effective_user.id
    s   = get_stats(tid)
    kb  = Mkp([[Btn("🏠 Inicio", callback_data="main_menu")]])
    text = (
        "📊 *Estadísticas*\n\n"
        f"🖼 Fotos:        {s['photos']}\n"
        f"🎬 Videos:       {s['videos']}\n"
        f"🎵 Audios:       {s['audios']}\n"
        f"📄 Documentos:   {s['docs']}\n"
        f"📦 Total:        {s['total']}\n"
        f"🗂 Álbumes:      {s['albums']}\n"
        f"💾 Tamaño total: {fmt_size(s['total_size'])}\n\n"
        "_Almacenado en servidores de Telegram_"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

async def cb_stats(update, ctx):
    await cmd_stats(update, ctx)

# ── Menú principal ─────────────────────────────────────────────────────────────
async def cb_main_menu(update, ctx):
    from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Mkp
    await update.callback_query.answer()
    kb = Mkp([
        [Btn("📸 Fotos",        callback_data="browse:photo:0"),
         Btn("🎬 Videos",       callback_data="browse:video:0")],
        [Btn("🗂 Álbumes",      callback_data="albums_menu"),
         Btn("📊 Estadísticas", callback_data="stats")],
        [Btn("❓ Ayuda",         callback_data="help")],
    ])
    await update.callback_query.message.edit_text(
        "📱 *Menú principal*\n\nEnvíame archivos o elige una opción:",
        parse_mode="Markdown", reply_markup=kb)

# ── Arranque ───────────────────────────────────────────────────────────────────
def main():
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        CallbackQueryHandler, ConversationHandler, filters
    )

    if not TELEGRAM_TOKEN:
        raise SystemExit("❌ Falta TELEGRAM_TOKEN — agrégala como variable de entorno")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # ConversationHandler para crear álbumes
    album_conv = ConversationHandler(
        entry_points=[
            CommandHandler("nuevo_album",     cmd_nuevo_album),
            CallbackQueryHandler(cb_newalbum_for_file, pattern=r"^newalbum:"),
            CallbackQueryHandler(cb_create_album_menu, pattern="^create_album_menu$"),
        ],
        states={
            ASK_ALBUM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_album_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_chat=True,
        per_message=False,
    )
    app.add_handler(album_conv)

    # Comandos
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("ayuda",       cmd_ayuda))
    app.add_handler(CommandHandler("galeria",     cmd_galeria))
    app.add_handler(CommandHandler("albumes",     cmd_albumes))
    app.add_handler(CommandHandler("stats",       cmd_stats))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_main_menu,            pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(cb_help,                 pattern="^help$"))
    app.add_handler(CallbackQueryHandler(cb_stats,                pattern="^stats$"))
    app.add_handler(CallbackQueryHandler(cb_albums_menu,          pattern="^albums_menu$"))
    app.add_handler(CallbackQueryHandler(cb_album_view,           pattern=r"^album:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_send_album,           pattern=r"^send_album:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_delete_album,         pattern=r"^del_album:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_confirm_delete_album, pattern=r"^confirm_del_album:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_assign_album,         pattern=r"^assign:"))
    app.add_handler(CallbackQueryHandler(cb_browse,               pattern=r"^browse:"))
    app.add_handler(CallbackQueryHandler(cb_send_browse,          pattern=r"^send_browse:"))

    # Media entrante
    app.add_handler(MessageHandler(filters.PHOTO,                 handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO,                 handle_video))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE, handle_audio))
    app.add_handler(MessageHandler(filters.Document.ALL,          handle_document))

    log.info("Bot arrancado con polling ✅")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    init_db()
    main()
PYEOF
echo "bot.py listo"
