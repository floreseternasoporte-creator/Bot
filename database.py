from sqlalchemy import create_engine, Column, String, Integer, DateTime, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kor.db")

# Railway PostgreSQL fix
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class VirtualNumber(Base):
    __tablename__ = "virtual_numbers"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True)
    country_code = Column(String)
    country_name = Column(String)
    user_id = Column(String, index=True)           # Telegram user ID
    telegram_username = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)


class ReceivedCode(Base):
    __tablename__ = "received_codes"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, index=True)
    from_number = Column(String)
    code = Column(String)
    full_message = Column(Text)
    service = Column(String, nullable=True)        # e.g. WhatsApp, Google
    received_at = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, index=True)
    from_number = Column(String)
    call_sid = Column(String, unique=True)
    duration = Column(Integer, default=0)
    status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, index=True)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)
    joined_at = Column(DateTime, default=datetime.utcnow)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    print("✅ Database initialized")
