# 🎙️ DubBot — Bot de doblaje con clonación de voz

Bot de Telegram que dobla videos y audios a múltiples idiomas **clonando la voz del original**, al estilo ElevenLabs pero 100% open source y gratuito.

## ✨ Características

- 🗣 **Clonación de voz** — El doblaje suena con la misma voz del original (XTTS-v2)
- 🌐 **10 idiomas** — Español, Inglés, Francés, Alemán, Portugués, Italiano, Japonés, Chino, Ruso, Árabe
- 📝 **Transcripción automática** — Whisper detecta el idioma automáticamente
- 🔒 **100% local** — Ningún dato se envía a servicios externos
- 🎬 **Video y audio** — mp4, mov, avi, mkv, mp3, wav, ogg…

## 🧠 Stack tecnológico

| Función | Modelo |
|---------|--------|
| Transcripción | [Whisper](https://github.com/openai/whisper) (base) |
| Traducción | [ArgosTranslate](https://github.com/argosopentech/argos-translate) |
| Síntesis de voz clonada | [XTTS-v2](https://github.com/coqui-ai/TTS) (Coqui TTS) |
| Procesamiento multimedia | FFmpeg |

## 🚀 Despliegue en Railway

1. Sube este repositorio a GitHub
2. En [Railway](https://railway.app) → **New Project → Deploy from GitHub repo**
3. En **Variables**, agrega:
   - `TELEGRAM_TOKEN` — Token de tu bot (obtenido con [@BotFather](https://t.me/BotFather))
4. Railway detecta el `Dockerfile` automáticamente

> ⚠️ **Railway Hobby plan recomendado** — Los modelos de ML requieren al menos 2GB de RAM y ~5GB de disco. El free tier puede quedarse sin memoria.

### Variables opcionales

| Variable | Default | Descripción |
|----------|---------|-------------|
| `TELEGRAM_TOKEN` | ✅ Requerida | Token del bot |
| `DATA_DIR` | `/app/data` | Ruta de la base de datos y archivos temporales |

### Volumen persistente (recomendado)

Para que los modelos descargados persistan entre deploys (evita descargarlos cada vez):

- Railway → tu servicio → **Volumes** → Mount en `/app/models`

## 📋 Comandos

| Comando | Descripción |
|---------|-------------|
| `/start` | Menú principal |
| `/ayuda` | Instrucciones de uso |

## 💡 Uso

1. Envía un **video o audio** al bot
2. Selecciona el **idioma de destino**
3. El bot procesa automáticamente (1-5 min según duración)
4. Recibes el **video doblado** con la voz clonada

## 📁 Estructura

```
.
├── bot.py           # Lógica completa del bot + pipeline de doblaje
├── requirements.txt # Dependencias Python
├── Dockerfile       # Imagen Docker para Railway
└── README.md
```

## ⏱ Tiempos estimados de procesamiento

| Duración del video | Tiempo aprox |
|--------------------|-------------|
| < 1 minuto | 1-2 min |
| 1-5 minutos | 3-7 min |
| 5-15 minutos | 8-20 min |

> La primera ejecución es más lenta porque descarga los modelos (~3GB total).
