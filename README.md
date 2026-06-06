# Bot de Transcripción de Videos

Bot de Telegram que transcribe el audio de videos y genera archivos de subtítulos (.srt) en múltiples idiomas.

## Cómo funciona

1. El usuario envía un video al bot
2. El bot muestra un teclado para seleccionar uno o más idiomas
3. El usuario selecciona los idiomas y pulsa **Generar subtítulos**
4. El bot devuelve un archivo `.srt` por cada idioma seleccionado

## Tecnologías

- **Whisper (OpenAI)** — transcripción de audio local
- **deep-translator** — traducción a múltiples idiomas
- **moviepy + ffmpeg** — extracción de audio del video
- **python-telegram-bot** — integración con Telegram

## Idiomas soportados

Español, Inglés, Francés, Alemán, Italiano, Portugués, Ruso, Chino, Japonés, Árabe

## Configuración

### Variables de entorno

| Variable | Descripción |
|---|---|
| `TELEGRAM_TOKEN` | Token del bot (obtenido desde @BotFather) |

### Ejecución local

```bash
pip install -r requirements.txt
TELEGRAM_TOKEN=tu_token python bot.py
```

### Docker

```bash
docker build -t transcription-bot .
docker run -e TELEGRAM_TOKEN=tu_token transcription-bot
```

## Comandos del bot

| Comando | Descripción |
|---|---|
| `/start` | Mensaje de bienvenida |
| `/help` | Instrucciones de uso |

## Estructura

```
.
├── bot.py           # Lógica del bot
├── requirements.txt # Dependencias Python
├── Dockerfile       # Imagen Docker
└── README.md
```
