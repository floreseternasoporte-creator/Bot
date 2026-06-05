# 📱 TelegramMediaBot

Bot de Telegram para guardar y organizar fotos, videos, audios y documentos **directamente en los servidores de Telegram**. Sin Google Drive, sin nube externa.

## ✨ Características

- 📸 **Galería personal** — Fotos, videos, audios y documentos en un solo lugar
- 🗂 **Álbumes** — Organiza tu contenido como en Google Photos
- 📤 **Reenvío** — Visualiza cualquier archivo directamente en el chat
- 📊 **Estadísticas** — Totales por tipo y tamaño
- 💾 **100% Telegram** — Los archivos viven en los servidores de Telegram

## 🚀 Despliegue en Railway

1. Sube este repositorio a GitHub
2. En [Railway](https://railway.app), crea un nuevo proyecto → **Deploy from GitHub repo**
3. En **Variables**, añade:
   - `TELEGRAM_TOKEN` — Token de tu bot (obtenido con [@BotFather](https://t.me/BotFather))
4. Railway detecta el `Dockerfile` automáticamente y despliega

> **Nota sobre datos:** La base de datos SQLite (`media.db`) se guarda en `/app/data` dentro del contenedor. Para persistencia entre deploys, añade un [Railway Volume](https://docs.railway.app/reference/volumes) montado en `/app/data` y configura la variable `DATA_DIR=/app/data`.

## 📋 Comandos del bot

| Comando | Descripción |
|---------|-------------|
| `/start` | Menú principal |
| `/galeria` | Explorar todos los archivos |
| `/albumes` | Ver y gestionar álbumes |
| `/nuevo_album` | Crear un álbum nuevo |
| `/stats` | Estadísticas de uso |
| `/ayuda` | Ayuda completa |

## 💡 Uso

1. **Envía cualquier archivo** al bot (foto, video, audio, documento)
2. El bot te preguntará **en qué álbum guardarlo** (o crea uno nuevo)
3. Explora tu galería con `/galeria` o `/albumes`
4. Usa los botones de navegación para ver página a página
5. Pulsa **"Ver archivos"** para que el bot reenvíe los archivos al chat

## 🗂 Sistema de álbumes

- Crea álbumes ilimitados
- Asigna archivos a álbumes al subirlos
- Navega por álbumes con paginación (10 por página)
- Elimina álbumes sin perder los archivos

## 📁 Estructura del repositorio

```
.
├── bot.py           # Lógica completa del bot
├── requirements.txt # Dependencias Python
├── Dockerfile       # Imagen Docker para Railway
└── README.md
```

## 📝 Variables de entorno

| Variable | Requerida | Descripción |
|----------|-----------|-------------|
| `TELEGRAM_TOKEN` | ✅ | Token del bot de Telegram |
| `DATA_DIR` | ❌ | Ruta de la base de datos (default: `/app/data`) |
