import os
from fastapi import APIRouter, Depends, HTTPException
from backend.kaspi_wrapper import KaspiAPI
from backend.models import AuthRequest, LoginCredentialsRequest
from backend.state_helpers import (
    _get_token, _save_session, SESSION_FILE, _save_mc_credentials, _refresh_mc_session,
    _load_mc_cookies, _save_mc_cookies_for_store, _get_store_status,
)
from backend.services.playwright_worker import worker, Command
from backend.security.store_context import StoreContext, get_store_context

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
            raise HTTPException(status_code=400, detail="Kaspi API отклонил токен. Проверьте правильность.")
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

@router.post("/login")
def login(req: LoginCredentialsRequest, ctx: StoreContext = Depends(get_store_context)):
    """
    Логин в Kaspi MC через Playwright (реальный браузер, единственный
    существующий механизм получения куков mc-session/mc-sid).

    ctx.store_id резолвится дуальной авторизацией (JWT -> стор пользователя,
    X-Store-Id либо самый старый стор, см. security/store_context.py) — без
    какого-либо нового стейта на фронтенде. Креды и свежие куки сохраняются
    ИСКЛЮЧИТЕЛЬНО в БД (Store.encrypted_mc_email/password/cookies) для
    этого стора — закрывает 3.1, часть (а).
    """
    email = req.email.strip()
    password = req.password
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email и пароль обязательны")

    success, msg, cookies = worker.send_cmd(Command.LOGIN, {"email": email, "password": password})
    if success:
        # Сохраняем креды (в зашифрованном виде), чтобы при истечении сессионных
        # куков backend мог автоматически перелогиниться без участия пользователя.
        _save_mc_credentials(email, password, store_id=ctx.store_id)
        _save_mc_cookies_for_store(ctx.store_id, cookies)
        return {"success": True, "message": msg}
    else:
        raise HTTPException(status_code=400, detail=msg)

@router.get("/mc-status")
def get_mc_status(verify: bool = False, ctx: StoreContext = Depends(get_store_context)):
    """
    has_session отражает наличие Store.encrypted_cookies в БД для ИМЕННО
    ЭТОГО стора, а не общий state.json.

    ВАЖНО (2026-07-30): при verify=False valid раньше всегда возвращался
    True, если куки просто расшифровывались — независимо от Store.status.
    Это давало рассинхрон с StoreHub (который честно читает Store.status):
    куки физически лежат в БД и корректно расшифровываются, но давно
    невалидны на стороне Kaspi (health_check_worker/verify уже пометили
    статус COOKIE_EXPIRED), а бейдж в Настройках всё равно светился зелёным.
    Теперь valid при verify=False = (Store.status == 'ACTIVE') — тот же
    источник правды, что и у StoreHub.

    verify=True гоняет Command.VERIFY_SESSION именно с куками ЭТОГО стора
    (де-глобализация Playwright-воркера — каждый вызов стейтлесс, без
    общего браузера/файла на все сторы, см. playwright_worker.py).
    """
    mc_cookies = _load_mc_cookies(store_id=ctx.store_id)
    has_session = bool(mc_cookies)

    if not has_session:
        return {"success": True, "has_session": False, "valid": False}

    if verify:
        success, valid, fresh_cookies = worker.send_cmd(Command.VERIFY_SESSION, mc_cookies)
        if success and not valid:
            # Куки истекли — пробуем автоматически обновить их сохранёнными кредами.
            if _refresh_mc_session(store_id=ctx.store_id):
                mc_cookies = _load_mc_cookies(store_id=ctx.store_id)
                success, valid, fresh_cookies = worker.send_cmd(Command.VERIFY_SESSION, mc_cookies)
        if success:
            if valid and fresh_cookies:
                _save_mc_cookies_for_store(ctx.store_id, fresh_cookies)
            return {"success": True, "has_session": True, "valid": valid}
        else:
            return {"success": False, "detail": valid}

    is_valid = _get_store_status(ctx.store_id) == "ACTIVE"
    return {"success": True, "has_session": True, "valid": is_valid}

@router.post("/close")
def close_session():
    return {"success": True}
