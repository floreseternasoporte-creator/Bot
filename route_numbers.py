from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db, VirtualNumber, ReceivedCode
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

router = APIRouter()


class NumberCreate(BaseModel):
    country_key: str
    user_id: str


@router.get("/list")
async def list_numbers(user_id: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(VirtualNumber)
    if user_id:
        query = query.filter(VirtualNumber.user_id == user_id)
    numbers = query.filter(VirtualNumber.is_active == True).all()
    return {
        "total": len(numbers),
        "numbers": [
            {
                "id": n.id,
                "phone_number": n.phone_number,
                "country_code": n.country_code,
                "country_name": n.country_name,
                "user_id": n.user_id,
                "created_at": n.created_at.isoformat(),
                "is_active": n.is_active,
            }
            for n in numbers
        ]
    }


@router.get("/{phone_number}/codes")
async def get_codes_for_number(phone_number: str, db: Session = Depends(get_db)):
    codes = db.query(ReceivedCode).filter(
        ReceivedCode.phone_number == phone_number
    ).order_by(ReceivedCode.received_at.desc()).limit(50).all()
    return {
        "phone_number": phone_number,
        "total": len(codes),
        "codes": [
            {
                "id": c.id,
                "from": c.from_number,
                "code": c.code,
                "message": c.full_message,
                "service": c.service,
                "received_at": c.received_at.isoformat(),
            }
            for c in codes
        ]
    }


@router.delete("/{phone_number}")
async def deactivate_number(phone_number: str, db: Session = Depends(get_db)):
    num = db.query(VirtualNumber).filter(VirtualNumber.phone_number == phone_number).first()
    if not num:
        raise HTTPException(status_code=404, detail="Number not found")
    num.is_active = False
    db.commit()
    return {"ok": True, "message": f"Number {phone_number} deactivated"}
