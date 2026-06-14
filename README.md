# 📡 KOR Telecom Platform

> Virtual Phone Number Platform — Receive SMS codes worldwide via Telegram Bot

---

## 🚀 Deploy on Railway (3 steps)

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "KOR Telecom v1.0"
git remote add origin https://github.com/YOUR_USERNAME/kor-telecom.git
git push -u origin main
```

### 2. Deploy on Railway
1. Go to [railway.app](https://railway.app)
2. Click **New Project → Deploy from GitHub**
3. Select your `kor-telecom` repo
4. Add a **PostgreSQL** database plugin
5. Set environment variables (copy from `.env.example`)

### 3. Configure Twilio Webhooks
Once Railway gives you a URL (e.g. `https://kor-telecom.railway.app`):

1. Go to [Twilio Console → Phone Numbers](https://console.twilio.com/us1/develop/phone-numbers/manage/active)
2. Click your number **(763) 878-6908**
3. Set:
   - **Voice URL:** `https://kor-telecom.railway.app/twilio/voice` (HTTP POST)
   - **SMS URL:** `https://kor-telecom.railway.app/twilio/sms` (HTTP POST)
4. Save

---

## 🤖 Telegram Bot Commands

| Command | Action |
|---------|--------|
| `/start` | Main menu |
| `/numbers` | Your virtual numbers |
| `/codes` | Received verification codes |
| `/help` | Help & instructions |

## 📱 Features

- 🌍 **20+ Countries** — US, UK, Germany, France, Spain, Mexico, Brazil, and more
- 🔑 **Auto Code Extraction** — Instantly detects verification codes from SMS
- 📞 **Professional IVR** — KOR branded call answering (like Verizon/AT&T)
- 📲 **Instant Notifications** — Codes pushed to Telegram immediately
- 🗄️ **Dashboard** — Web admin panel at `/`
- 📡 **REST API** — Full API at `/docs`

## 🔗 URLs

| Path | Description |
|------|-------------|
| `/` | Admin Dashboard |
| `/docs` | API Documentation |
| `/health` | Health Check |
| `/twilio/voice` | Twilio Voice Webhook |
| `/twilio/sms` | Twilio SMS Webhook |
| `/telegram/webhook` | Telegram Bot Webhook |

---

*KOR Telecom — Connecting the World 🌐*
