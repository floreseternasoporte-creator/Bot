from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
from route_telegram import router as telegram_router
from route_twilio import router as twilio_router
from route_numbers import router as numbers_router
from route_dashboard import router as dashboard_router
from route_setup import router as setup_router
from database import init_db

app = FastAPI(
    title="KOR Telecom API",
    description="KOR — Virtual Phone Number Platform",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(telegram_router, prefix="/telegram", tags=["Telegram"])
app.include_router(twilio_router,   prefix="/twilio",   tags=["Twilio"])
app.include_router(numbers_router,  prefix="/numbers",  tags=["Numbers"])
app.include_router(setup_router,    prefix="",          tags=["Setup"])
app.include_router(dashboard_router,prefix="",          tags=["Dashboard"])

@app.on_event("startup")
async def on_startup():
    init_db()

@app.get("/health")
async def health_check():
    return {"status": "ok", "company": "KOR Telecom", "version": "1.0.0"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
