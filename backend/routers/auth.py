import os
from fastapi import APIRouter, HTTPException
from backend.kaspi_wrapper import KaspiAPI
from backend.models import AuthRequest, PhoneRequest, SmsRequest
from backend.state_helpers import _get_token, _save_session, SESSION_FILE
from backend.services.playwright_worker import worker, Command

router = APIRouter(prefix="/api", tags=["auth"])

@router.post("/auth")
def authenticate(req: AuthRequest):
    """Принимает API токен, верифицирует через GET /cities и сохраняет."""
    token = req.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Токен не может быть пустым.")

    try:
        valid = KaspiAPI.verify_token(token)
        if not valid:
            raise HTTPException(status_code=401, detail="Kaspi API отклонил токен. Проверьте правильность.")
    except HTTPException:
        raise
    except Exception as e:
        err = str(e)
        if "timeout" in err.lower() or "Connection" in err or "NameResolution" in err:
            _save_session({"token": token})
            return {
                "success": True,
                "verified": False,
                "message": f"Токен сохранён (оффлайн-режим: {err})",
            }
        raise HTTPException(status_code=400, detail=f"Ошибка верификации: {err}")

    try:
        merchant_id = KaspiAPI.get_merchant_id(token)
    except Exception:
        merchant_id = "20186986"
    _save_session({"token": token, "merchant_id": merchant_id})
    return {"success": True, "verified": True, "message": "Токен верифицирован и сохранён."}

@router.post("/verify-session")
def verify_session():
    """Проверить, есть ли сохранённый токен и валиден ли он."""
    token = _get_token()
    if not token:
        return {"valid": False}

    try:
        valid = KaspiAPI.verify_token(token)
        return {"valid": valid}
    except Exception:
        return {"valid": True}

@router.post("/logout")
def logout():
    """Удалить сохранённый токен."""
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
    return {"success": True, "message": "Сессия удалена."}

@router.post("/start-login")
def start_login(req: PhoneRequest):
    phone = req.phone.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Не передан номер телефона")
    
    success, msg = worker.send_cmd(Command.START_LOGIN, phone)
    if success:
        return {"success": True, "message": msg}
    else:
        raise HTTPException(status_code=400, detail=msg)

@router.post("/submit-sms")
def submit_sms(req: SmsRequest):
    sms = req.sms.strip()
    if not sms:
        raise HTTPException(status_code=400, detail="Не передан SMS-код")
        
    success, msg = worker.send_cmd(Command.SUBMIT_SMS, sms)
    if success:
        return {"success": True, "message": msg}
    else:
        raise HTTPException(status_code=400, detail=msg)

@router.get("/mc-status")
def get_mc_status(verify: bool = False):
    has_session = os.path.exists("state.json")
    if not has_session:
        return {"success": True, "has_session": False, "valid": False}
        
    if verify:
        success, valid = worker.send_cmd(Command.VERIFY_SESSION)
        if success:
            return {"success": True, "has_session": True, "valid": valid}
        else:
            return {"success": False, "detail": valid}
            
    return {"success": True, "has_session": True, "valid": True}

@router.post("/close")
def close_session():
    return {"success": True}
