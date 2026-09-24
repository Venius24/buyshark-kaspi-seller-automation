"""Исходящая транзакционная почта (сброс пароля и т.п.).

Шлём через HTTP-API Resend, а не SMTP: с прод-VPS исходящий 587 закрыт
(проверено 2026-09-25), 443 открыт. Домен buyshark.kz на Cloudflare —
приём писем там же (Email Routing → webhooks_incoming_email.py), DKIM/SPF
для отправки добавляются в DNS отдельными записями Resend и приёму не мешают.

Без RESEND_API_KEY письмо не уходит, а пишется в лог — чтобы локальный dev
и тесты работали без внешнего сервиса. send_email никогда не бросает:
вызывающий код (например, /forgot-password) не должен раскрывать наружу,
удалась ли отправка.
"""
import os
import logging

import httpx

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "BuyShark <noreply@buyshark.kz>"


def is_configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY"))


async def send_email(to: str, subject: str, html: str, text: str) -> bool:
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        # Тело (в нём может быть ссылка сброса) — только на DEBUG: warning-логи
        # уходят в Sentry хлебными крошками.
        logger.warning("RESEND_API_KEY не задан — письмо «%s» для %s не отправлено", subject, to)
        logger.debug("Тело неотправленного письма:\n%s", text)
        return False

    payload = {
        "from": os.getenv("MAIL_FROM", DEFAULT_FROM),
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    reply_to = os.getenv("MAIL_REPLY_TO")
    if reply_to:
        payload["reply_to"] = reply_to

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                RESEND_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code >= 400:
            logger.error("Resend отказал (%s) для %s: %s", resp.status_code, to, resp.text[:500])
            return False
        logger.info("Письмо «%s» отправлено на %s", subject, to)
        return True
    except Exception:
        logger.exception("Не удалось отправить письмо «%s» на %s", subject, to)
        return False


def render_password_reset(reset_url: str) -> tuple[str, str, str]:
    """(subject, html, text) письма со ссылкой сброса пароля."""
    subject = "Восстановление пароля BuyShark"
    text = (
        "Здравствуйте!\n\n"
        "Кто-то (надеемся, вы) запросил восстановление пароля от кабинета BuyShark.\n"
        f"Чтобы задать новый пароль, откройте ссылку (действует 1 час):\n{reset_url}\n\n"
        "Если вы не запрашивали восстановление — просто проигнорируйте это письмо, "
        "пароль останется прежним.\n\n— BuyShark"
    )
    html = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#0f172a;font-family:Arial,sans-serif;color:#e2e8f0">
  <div style="max-width:480px;margin:0 auto;background:#1e293b;border-radius:12px;padding:28px">
    <h2 style="margin:0 0 16px;color:#fff;font-size:20px">Восстановление пароля</h2>
    <p style="margin:0 0 16px;line-height:1.5">Кто-то (надеемся, вы) запросил восстановление пароля от кабинета BuyShark.</p>
    <p style="margin:0 0 24px">
      <a href="{reset_url}" style="display:inline-block;background:#3b82f6;color:#fff;text-decoration:none;padding:12px 20px;border-radius:8px;font-weight:bold">Задать новый пароль</a>
    </p>
    <p style="margin:0 0 8px;font-size:13px;color:#94a3b8">Ссылка действует 1 час. Если кнопка не открывается, скопируйте адрес:</p>
    <p style="margin:0 0 24px;font-size:12px;word-break:break-all;color:#94a3b8">{reset_url}</p>
    <p style="margin:0;font-size:13px;color:#94a3b8">Если вы не запрашивали восстановление — просто проигнорируйте письмо, пароль останется прежним.</p>
  </div>
</body></html>"""
    return subject, html, text
