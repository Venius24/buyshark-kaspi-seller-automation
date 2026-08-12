"""Скелет провижининга персональных email-адресов на *@buyshark.kz — по
одному на каждого клиента/сотрудника, которого сервис заводит на себя в
Kaspi MC. Реального клиента под это пока нет (см. AUTOCONNECT_PLAN.md), это
задел на будущее: ничего не вызывает этот модуль на проде.

Никакого шага на стороне Cloudflare для "создания" адреса не нужно — Email
Routing уже настроен catch-all-правилом на *@buyshark.kz (проверено вживую
2026-08-12: письма на произвольные несуществующие ранее локальные части
долетали до вебхука без единой правки в Cloudflare). provision_mailbox()
только резервирует уникальную локальную часть в нашей БД, чтобы:
  1. гарантировать отсутствие коллизий (не полагаться на вероятность, как
     сейчас делает generate_employee_credentials() в kaspi_auto_connect.py);
  2. дать вебхуку (webhooks_incoming_email.py) по чему сопоставлять
     входящее письмо с конкретным клиентом (ClientMailbox.email -> id
     -> IncomingEmail.mailbox_id).
"""
import re
import secrets
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ClientMailbox

logger = logging.getLogger(__name__)

MAILBOX_DOMAIN = "buyshark.kz"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(label: str) -> str:
    slug = _SLUG_RE.sub("", label.lower())[:16]
    return slug or "client"


async def provision_mailbox(
    db: AsyncSession,
    owner_user_id: str | None = None,
    store_id: str | None = None,
    label: str | None = None,
) -> ClientMailbox:
    """Резервирует новый уникальный адрес вида
    buyshark.<slug>.<hex>@buyshark.kz и сохраняет запись. Ретраит генерацию
    локальной части при коллизии (маловероятной, но БД гарантирует, а не
    просто отсекает вероятность как secrets.token_hex(3))."""
    slug = _slugify(label or "client")

    for _ in range(5):
        email = f"buyshark.{slug}.{secrets.token_hex(3)}@{MAILBOX_DOMAIN}"
        exists = await db.execute(select(ClientMailbox.id).where(ClientMailbox.email == email))
        if exists.scalar_one_or_none():
            continue

        mailbox = ClientMailbox(
            email=email,
            owner_user_id=owner_user_id,
            store_id=store_id,
            label=label,
        )
        db.add(mailbox)
        await db.flush()
        logger.info(f"📬 Провижининг почты: {email} (owner_user_id={owner_user_id}, label={label!r})")
        return mailbox

    raise RuntimeError(f"Не удалось подобрать уникальный email для slug={slug!r} за 5 попыток")
