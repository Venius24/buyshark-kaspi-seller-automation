import asyncio
import logging
import io
import os
from typing import Dict, Any, List, Optional
from backend.chatbot import config, bot_instance, telegram_identity as ti, notification_prefs, notify_dispatch, audit, i18n
from backend.chatbot.notifications import _esc_md
from backend.chatbot.kaspi import load_chat_cookies
from backend.chatbot.chat_client import send_message as kaspi_send_chat_message
from backend.services.print_batcher_service import (
    process_zip_or_pdfs,
    get_batch_file_path,
    reset_printed_history,
    load_printed_history,
    peek_already_printed,
    count_already_printed_orders,
    fetch_order_skus
)
from backend.services.auto_assembly_worker import (
    get_unprinted_spool_rows,
    build_spool_zip_bytes,
    mark_spool_printed,
    run_auto_assembly_cycle,
)
from backend.routers.auto_assembly import DELIVERY_TYPES, _default_rule_dict, _row_to_dict
from backend.security.store_context import get_store_by_id
from backend.state_helpers import _get_sync_db_session, _save_chat_cookies_for_store

logger = logging.getLogger(__name__)

# Хранилище загруженных файлов перед выбором формата: chat_id -> file_meta
PENDING_FILES: Dict[int, Dict[str, Any]] = {}

# Ф5 (💬 Чаты): tg_user_id -> {"group_id", "store_id"} — следующее текстовое
# сообщение этого человека уходит клиенту в Kaspi-чат, а не обрабатывается
# как команда/кнопка. In-memory, теряется при рестарте — тот же компромисс,
# что и PENDING_FILES (см. HANDOFF).
PENDING_REPLY: Dict[int, Dict[str, str]] = {}

# Обновление куки чата Kaspi через ТГ (кнопка на /status): tg_user_id ->
# {"store_id", "step": "phone"|"code"}. Тот же in-memory компромисс, что и
# PENDING_REPLY — теряется при рестарте бота, пользователь просто начинает
# заново нажатием кнопки.
PENDING_CHAT_LOGIN: Dict[int, Dict[str, str]] = {}

NOT_LINKED_TEXT = (
    "⛔ Этот Telegram не привязан ни к одному магазину.\n\n"
    "Попросите владельца магазина прислать вам ссылку-приглашение (кабинет → "
    "Настройки → Telegram), либо, если это ваш магазин — откройте кабинет и "
    "нажмите «Привязать Telegram»."
)

DELIVERY_ICONS = {
    "Express": "⚡", "Postomat": "📮", "Самовывоз": "🏬",
    "Today": "📅", "Tomorrow": "🌙", "Kaspi Доставка": "🚚",
}

ALLOWED_RULE_FIELDS = {"stage_accept", "stage_assemble", "stage_waybill", "dry_run"}


def _load_delivery_rules(store_id: str) -> List[dict]:
    """Полная сетка правил Автосборки по всем видам доставки (сотнный
    список из routers/auto_assembly.py::DELIVERY_TYPES) — незаполненные
    виды возвращаются с дефолтами, как и в вебе."""
    session = _get_sync_db_session()
    by_type: Dict[str, dict] = {}
    if session:
        try:
            from sqlalchemy import select
            from backend.db.models import AutoAssemblyRuleDB
            rows = session.scalars(
                select(AutoAssemblyRuleDB).where(AutoAssemblyRuleDB.store_id == store_id)
            ).all()
            by_type = {r.delivery_type: _row_to_dict(r) for r in rows}
        finally:
            session.close()
    return [by_type.get(dt, _default_rule_dict(dt)) for dt in DELIVERY_TYPES]


def _toggle_rule_enabled(store_id: str, delivery_type: str) -> bool:
    """Переключает enabled правила, заводит запись с дефолтами, если её ещё
    не было (например, магазин ни разу не настраивался через веб)."""
    session = _get_sync_db_session()
    if not session:
        return False
    try:
        from backend.db.models import AutoAssemblyRuleDB
        row = session.get(AutoAssemblyRuleDB, (store_id, delivery_type))
        if row is None:
            row = AutoAssemblyRuleDB(store_id=store_id, delivery_type=delivery_type, enabled=True)
            session.add(row)
        else:
            row.enabled = not row.enabled
        session.commit()
        return row.enabled
    finally:
        session.close()


def _toggle_rule_field(store_id: str, delivery_type: str, field: str) -> Optional[bool]:
    if field not in ALLOWED_RULE_FIELDS:
        return None
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import AutoAssemblyRuleDB
        row = session.get(AutoAssemblyRuleDB, (store_id, delivery_type))
        if row is None:
            row = AutoAssemblyRuleDB(store_id=store_id, delivery_type=delivery_type)
            session.add(row)
        setattr(row, field, not getattr(row, field))
        session.commit()
        return getattr(row, field)
    finally:
        session.close()


def _store_merchant_id(store_id: str) -> Optional[str]:
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import Store
        store = session.get(Store, store_id)
        return store.merchant_id if store else None
    finally:
        session.close()


def _recent_auto_assembly_failures(store_id: str, limit: int = 3) -> List[str]:
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from sqlalchemy import select
        from backend.db.models import AutoAssemblyLogDB
        rows = session.scalars(
            select(AutoAssemblyLogDB)
            .where(AutoAssemblyLogDB.store_id == store_id, AutoAssemblyLogDB.decision == "FAILED")
            .order_by(AutoAssemblyLogDB.id.desc())
            .limit(limit)
        ).all()
        return [f"{r.stage} #{r.order_code}: {r.reason}" for r in rows]
    finally:
        session.close()


async def register_bot_commands():
    """Публикует список команд в нативное Telegram-меню (кнопка "/" рядом с
    полем ввода) — без этого /reset, /stats, /unlink, /switch были видны
    только тем, кто уже знал их названия из текста /start."""
    bot = bot_instance.bot
    if not bot or not bot_instance.HAS_AIOGRAM:
        return
    from aiogram.types import BotCommand
    commands = [
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="status", description="📊 Статус автоответчика и куков"),
        BotCommand(command="settings", description="⚙️ Настройки печати и уведомлений"),
        BotCommand(command="notif", description="🔔 Настройки уведомлений (быстрый доступ)"),
        BotCommand(command="switch", description="🏪 Сменить активный магазин"),
        BotCommand(command="stats", description="📈 Статистика напечатанного за сегодня"),
        BotCommand(command="reset", description="♻️ Сбросить историю печати за сегодня"),
        BotCommand(command="unlink", description="🔓 Отвязать этот Telegram от кабинета"),
    ]
    try:
        await bot.set_my_commands(commands)
    except Exception as e:
        logger.error(f"❌ Не удалось зарегистрировать команды бота: {e}")


def register_handlers():
    """Регистрирует хэндлеры команд, документов, инлайн-кнопок и настроек для Telegram бота."""
    dp = bot_instance.dp
    if not dp or not bot_instance.HAS_AIOGRAM:
        return

    from aiogram import types, F
    from aiogram.types import (
        InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile,
        ReplyKeyboardMarkup, KeyboardButton,
    )

    def get_layout_keyboard(prefix: str = "pb_fmt") -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="4 на A4", callback_data=f"{prefix}:a4_4"),
                InlineKeyboardButton(text="8 на A4", callback_data=f"{prefix}:a4_8"),
                InlineKeyboardButton(text="9 на A4", callback_data=f"{prefix}:a4_9"),
                InlineKeyboardButton(text="1 на A4", callback_data=f"{prefix}:a4_1")
            ],
            [
                InlineKeyboardButton(text="🏷️ Термо 75x120", callback_data=f"{prefix}:thermal_75_120"),
                InlineKeyboardButton(text="🏷️ Термо 100x150", callback_data=f"{prefix}:thermal_100_150")
            ],
            [
                InlineKeyboardButton(text="📑 Просто объединить", callback_data=f"{prefix}:just_merge"),
                InlineKeyboardButton(text="⚡ Авто-категории", callback_data=f"{prefix}:auto")
            ]
        ])

    def get_main_reply_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
        L = lambda key: i18n.label(key, lang)
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text=L("morning"))],
                [KeyboardButton(text=L("print")), KeyboardButton(text=L("assembly"))],
                [KeyboardButton(text=L("chats")), KeyboardButton(text=L("store"))],
                [KeyboardButton(text=L("status")), KeyboardButton(text=L("settings"))],
            ],
            resize_keyboard=True,
        )

    def get_print_menu_view(store_id: Optional[str], count: int) -> tuple[str, Optional[InlineKeyboardMarkup]]:
        store_line = f"🏪 **{_esc_md(config.get_store_name(store_id))}**\n\n" if store_id else ""
        text = (
            f"🖨 **Печать**\n\n"
            f"{store_line}"
            f"📥 **Накоплено в автосборке:** `{count}` накладных, ещё не напечатанных.\n\n"
            f"Можно также прислать мне ZIP-архив или PDF вручную — обработаю тем же движком."
        )
        if not count:
            return text, None
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚡ Скачать пачку (авто-формат)", callback_data="spool_fmt:auto")],
            [InlineKeyboardButton(text="🖼️ Выбрать формат", callback_data="spool_layout_menu")],
            [InlineKeyboardButton(text="✅ Пометить все напечатанными", callback_data="spool_mark_all")],
        ])
        return text, kb

    def get_assembly_view(store_id: str) -> tuple[str, InlineKeyboardMarkup]:
        rules = _load_delivery_rules(store_id)
        store_line = f"🏪 **{_esc_md(config.get_store_name(store_id))}**\n\n" if store_id else ""
        lines = [f"⚙️ **Автосборка**\n\n{store_line}"]
        kb_rows = []
        for r in rules:
            dt = r["delivery_type"]
            icon = DELIVERY_ICONS.get(dt, "📦")
            status = "🟢 ВКЛ" if r["enabled"] else "⚪ ВЫКЛ"
            stage_bits = []
            if r["stage_accept"]:
                stage_bits.append("принять")
            if r["stage_assemble"]:
                stage_bits.append("собрать")
            if r["stage_waybill"]:
                stage_bits.append("накладная")
            stages_txt = ", ".join(stage_bits) if stage_bits else "этапы не настроены"
            dry_txt = " · тест-режим" if r["dry_run"] else ""
            lines.append(f"{icon} **{dt}** — {status}{dry_txt}\n   {stages_txt}")
            kb_rows.append([
                InlineKeyboardButton(
                    text=f"{'🔴 Выкл' if r['enabled'] else '🟢 Вкл'} {dt}",
                    callback_data=f"aa_toggle:{dt}",
                ),
                InlineKeyboardButton(text="⚙️ Настроить", callback_data=f"aa_cfg:{dt}"),
            ])
        lines.append("\nРасписание, лимит цены и чёрный список SKU — настраиваются в кабинете (веб).")
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows)

    def get_assembly_rule_config_view(store_id: str, delivery_type: str, can_edit: bool) -> tuple[str, InlineKeyboardMarkup]:
        rules = {r["delivery_type"]: r for r in _load_delivery_rules(store_id)}
        r = rules.get(delivery_type, _default_rule_dict(delivery_type))
        icon = DELIVERY_ICONS.get(delivery_type, "📦")
        text = (
            f"⚙️ **{icon} {delivery_type}**\n\n"
            f"Статус: {'🟢 ВКЛ' if r['enabled'] else '⚪ ВЫКЛ'}\n"
            f"Тест-режим: {'✅ включён (ничего реально не делает)' if r['dry_run'] else '❌ выключен'}\n\n"
            f"✅ Принять заказ: {'ВКЛ' if r['stage_accept'] else 'выкл'}\n"
            f"📦 Собрать: {'ВКЛ' if r['stage_assemble'] else 'выкл'}\n"
            f"🧾 Скачать накладную: {'ВКЛ' if r['stage_waybill'] else 'выкл'}\n\n"
            f"Расписание, лимит цены, чёрный список SKU — настраиваются в кабинете."
        )
        if not can_edit:
            text += "\n\n_Изменять может только владелец магазина._"
            return text, InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="aa_back")]
            ])
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{'✅' if r['stage_accept'] else '⬜'} Принять", callback_data=f"aa_field:stage_accept:{delivery_type}")],
            [InlineKeyboardButton(text=f"{'✅' if r['stage_assemble'] else '⬜'} Собрать", callback_data=f"aa_field:stage_assemble:{delivery_type}")],
            [InlineKeyboardButton(text=f"{'✅' if r['stage_waybill'] else '⬜'} Накладная", callback_data=f"aa_field:stage_waybill:{delivery_type}")],
            [InlineKeyboardButton(text=("🧪 Тест-режим: ВКЛ" if r['dry_run'] else "🧪 Тест-режим: ВЫКЛ"), callback_data=f"aa_field:dry_run:{delivery_type}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="aa_back")],
        ])
        return text, kb

    async def _send_spool_batch(chat_id: int, manifest: dict) -> None:
        """Отправляет все готовые пачки манифеста в чат — общая логика
        между Ф2 (кнопка «Печать») и Ф3 (Утренний сбор)."""
        bot = bot_instance.bot
        for b_key, b_info in manifest["batches"].items():
            batch_file_path = get_batch_file_path(manifest["batch_id"], b_info["category"])
            if batch_file_path and batch_file_path.exists():
                input_file = FSInputFile(path=str(batch_file_path), filename=b_info["file_name"])
                audit_msg = b_info.get("geometry_audit", {}).get("audit_message", "")
                caption = (
                    f"{b_info['icon']} **{b_info['title']}**\n"
                    f"🖨️ `{b_info['printer_type']}`\n"
                    f"📄 Страниц: `{b_info['total_pages']}` шт | Размеры: `{b_info['sample_dimensions']}`\n"
                    f"{audit_msg}"
                )
                await bot.send_document(chat_id=chat_id, document=input_file, caption=caption)

        mark_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Пометить эти накладные напечатанными", callback_data="spool_mark_all")]
        ])
        await bot.send_message(chat_id=chat_id, text="Готово! Пометить накладные как напечатанные?", reply_markup=mark_kb)

    def get_settings_categories_view(store_id: Optional[str]) -> tuple[str, InlineKeyboardMarkup]:
        """Главный экран /settings — категории конфигов, подробности по
        клику на каждую (печать/автосборка/уведомления живут раздельно)."""
        store_line = f"🏪 **{_esc_md(config.get_store_name(store_id))}**\n\n" if store_id else ""
        text = f"⚙️ **Настройки**\n\n{store_line}Выберите категорию:"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🖨️ Печать", callback_data="stg_cat:print")],
            [InlineKeyboardButton(text="⚙️ Автосборка", callback_data="stg_cat:assembly")],
            [InlineKeyboardButton(text="🔔 Уведомления", callback_data="stg_notif_menu")],
        ])
        return text, kb

    def get_print_settings_view(store_id: Optional[str]) -> tuple[str, InlineKeyboardMarkup]:
        stg = config.load_print_settings(store_id)

        exp_mode = stg.get("express_mode", "separate")
        sort_p = stg.get("sort_preset", "filename")
        dedup = stg.get("deduplicate_history", True)
        rot = stg.get("auto_rotate", True)
        def_fmt = stg.get("output_layout", "auto")
        dup_mode = stg.get("dup_handling_mode", "always_new")

        exp_title = {
            "separate": "⚡ Выделять в VIP-батчи",
            "exclude": "🚫 Исключать из печати",
            "ignore": "📦 Печатать всё вместе"
        }.get(exp_mode, exp_mode)

        sort_title = {
            "filename": "🔤 По имени файла",
            "order_id": "🔢 По Order ID",
            "original": "📄 В порядке архива",
            "product_name": "📦 По названию товара",
            "master_group": "🏷️ По Мастер-Группе (со штампом)"
        }.get(sort_p, sort_p)

        fmt_title = {
            "auto": "⚡ Авто-категории",
            "thermal_75_120": "🏷️ 75x120 мм",
            "thermal_100_150": "🏷️ 100x150 мм",
            "a4_4": "🖼️ 4 на A4",
            "a4_8": "🖼️ 8 на A4",
            "a4_9": "🖼️ 9 на A4",
            "a4_1": "📄 1 на A4",
            "just_merge": "📑 Просто объединить"
        }.get(def_fmt, def_fmt)

        dup_title = {
            "always_all": "📦 Всегда печатать всё",
            "always_new": "🆕 Всегда только новые",
            "always_ask": "❓ Всегда спрашивать"
        }.get(dup_mode, dup_mode)

        store_line = f"🏪 **{_esc_md(config.get_store_name(store_id))}**\n\n" if store_id else ""
        text = (
            f"🖨️ **Настройки печати:**\n\n"
            f"{store_line}"
            f"⚡ **Каспи Экспресс:** `{exp_title}`\n"
            f"📋 **Сортировка:** `{sort_title}`\n"
            f"🧾 **Учёт уже распечатанных накладных:** `{dup_title}`\n"
            f"🛡️ **Суточная дедупликация:** `{'Включена ✅' if dedup else 'Выключена ❌'}`\n"
            f"🔄 **Авто-поворот под ленту:** `{'Включен ✅' if rot else 'Выключен ❌'}`\n"
            f"🖼️ **Формат по умолчанию:** `{fmt_title}`\n\n"
            f"Нажимайте на кнопки ниже для переключения параметров:"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"⚡ VIP {'✅' if exp_mode == 'separate' else ''}", callback_data="stg_exp:separate"),
                InlineKeyboardButton(text=f"🚫 Исключать {'✅' if exp_mode == 'exclude' else ''}", callback_data="stg_exp:exclude"),
                InlineKeyboardButton(text=f"📦 Печатать всё {'✅' if exp_mode == 'ignore' else ''}", callback_data="stg_exp:ignore")
            ],
            [
                InlineKeyboardButton(text=f"🔤 Имя {'✅' if sort_p == 'filename' else ''}", callback_data="stg_srt:filename"),
                InlineKeyboardButton(text=f"🔢 Order ID {'✅' if sort_p == 'order_id' else ''}", callback_data="stg_srt:order_id"),
                InlineKeyboardButton(text=f"📄 Архив {'✅' if sort_p == 'original' else ''}", callback_data="stg_srt:original")
            ],
            [
                InlineKeyboardButton(text=f"📦 Товар {'✅' if sort_p == 'product_name' else ''}", callback_data="stg_srt:product_name"),
                InlineKeyboardButton(text=f"🏷️ Мастер-Группа {'✅' if sort_p == 'master_group' else ''}", callback_data="stg_srt:master_group")
            ],
            [
                InlineKeyboardButton(text=f"📦 Всё {'✅' if dup_mode == 'always_all' else ''}", callback_data="stg_dup:always_all"),
                InlineKeyboardButton(text=f"🆕 Только новые {'✅' if dup_mode == 'always_new' else ''}", callback_data="stg_dup:always_new"),
                InlineKeyboardButton(text=f"❓ Спрашивать {'✅' if dup_mode == 'always_ask' else ''}", callback_data="stg_dup:always_ask")
            ],
            [
                InlineKeyboardButton(text=f"🛡️ Дедупликация: {'ВКЛ ✅' if dedup else 'ВЫКЛ ❌'}", callback_data="stg_toggle:deduplicate_history"),
                InlineKeyboardButton(text=f"🔄 Поворот: {'ВКЛ ✅' if rot else 'ВЫКЛ ❌'}", callback_data="stg_toggle:auto_rotate")
            ],
            [
                InlineKeyboardButton(text=f"🏷️ 75x120 {'✅' if def_fmt == 'thermal_75_120' else ''}", callback_data="stg_fmt:thermal_75_120"),
                InlineKeyboardButton(text=f"🏷️ 100x150 {'✅' if def_fmt == 'thermal_100_150' else ''}", callback_data="stg_fmt:thermal_100_150"),
                InlineKeyboardButton(text=f"🖼️ 4 на A4 {'✅' if def_fmt == 'a4_4' else ''}", callback_data="stg_fmt:a4_4"),
                InlineKeyboardButton(text=f"⚡ Авто {'✅' if def_fmt == 'auto' else ''}", callback_data="stg_fmt:auto")
            ],
            [
                InlineKeyboardButton(text="♻️ Сбросить историю печати за сегодня", callback_data="stg_reset_hist")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад к категориям", callback_data="stg_back")
            ]
        ])
        return text, kb

    def get_notification_settings_view(store_id: str, tg_user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        prefs = notification_prefs.get_prefs(tg_user_id, store_id)
        quiet_line = f"{prefs['quiet_from']}–{prefs['quiet_to']}" if prefs["quiet_from"] else "выключены"
        text = (
            f"🔔 **Уведомления** (лично для вас в этом магазине)\n\n"
            f"💬 Чаты: {'ВКЛ ✅' if prefs['chats'] else 'ВЫКЛ ❌'}\n"
            f"🚨 Проблемы: {'ВКЛ ✅' if prefs['problems'] else 'ВЫКЛ ❌'}\n"
            f"💰 Деньги и заказы: {'ВКЛ ✅' if prefs['money'] else 'ВЫКЛ ❌'}\n"
            f"🌙 Тихие часы: `{quiet_line}`\n\n"
            f"Express-заказы и «сессия истекла» приходят всегда, даже в тихие часы."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💬 Чаты {'✅' if prefs['chats'] else '❌'}", callback_data="stg_notif_toggle:chats")],
            [InlineKeyboardButton(text=f"🚨 Проблемы {'✅' if prefs['problems'] else '❌'}", callback_data="stg_notif_toggle:problems")],
            [InlineKeyboardButton(text=f"💰 Деньги {'✅' if prefs['money'] else '❌'}", callback_data="stg_notif_toggle:money")],
            [
                InlineKeyboardButton(text="🌙 23:00–08:00", callback_data="stg_notif_quiet:on"),
                InlineKeyboardButton(text="☀️ Без тихих часов", callback_data="stg_notif_quiet:off"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="stg_notif_back")],
        ])
        return text, kb

    def get_chats_view(store_id: str) -> tuple[str, Optional[InlineKeyboardMarkup]]:
        blocked = config.list_blocked_chats_detailed(store_id)
        store_line = f"🏪 **{_esc_md(config.get_store_name(store_id))}**\n\n" if store_id else ""
        if not blocked:
            return f"💬 **Чаты**\n\n{store_line}Нет диалогов, ожидающих ответа человека — ИИ справляется сам.", None
        lines = [f"💬 **Чаты**\n\n{store_line}Ожидают ответа: `{len(blocked)}` (блокировка сама снимается через 2 дня)"]
        kb_rows = []
        for c in blocked[:10]:
            label = c["client_name"] if c["client_name"] else c["group_id"][:10]
            kb_rows.append([
                InlineKeyboardButton(text=f"✍️ Ответить: {label}", callback_data=f"chat_open:{c['group_id']}"),
                InlineKeyboardButton(text="🤖 Вернуть ИИ", callback_data=f"chat_ai:{c['group_id']}"),
            ])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows)

    async def _require_access(message: types.Message) -> Optional[dict]:
        """Проверяет доступ вызывающего, отвечает отказом/подсказкой если
        доступа нет или нужно сначала выбрать магазин. Возвращает access-dict
        (см. telegram_identity.resolve_bot_access) или None, если отвечать
        дальше не нужно (сообщение уже отправлено)."""
        access = await asyncio.to_thread(ti.resolve_bot_access, message.from_user.id)
        if not access["authorized"]:
            await message.reply(NOT_LINKED_TEXT)
            return None
        if access["needs_store_selection"]:
            lines = ["🏪 У вас несколько магазинов, выберите активный командой /switch:\n"]
            for s in access["store_choices"]:
                lines.append(f"• `{_esc_md(s['name'])}`")
            await message.reply("\n".join(lines))
            return None
        return access

    @dp.message(lambda m: bool(m.text) and not m.text.startswith("/") and m.from_user and m.from_user.id in PENDING_REPLY)
    async def handle_chat_reply(message: types.Message):
        """Ф5 (💬 Чаты): следующее обычное сообщение после «✍️ Ответить»
        уходит клиенту в Kaspi-чат напрямую, минуя ИИ. Зарегистрирован
        раньше остальных текстовых хэндлеров, чтобы перехватывать ответ
        даже если он текстуально совпал бы с подписью кнопки меню."""
        pending = PENDING_REPLY.get(message.from_user.id)
        if not pending:
            return

        # Защита от бага: если человек в режиме "✍️ Ответить" нажал кнопку
        # главного меню, этот хэндлер (зарегистрирован раньше остальных,
        # текстовые команды сюда уже не долетают — см. фильтр выше) ловил и
        # её, и текст кнопки уходил клиенту в Kaspi-чат. Теперь такой текст
        # режим ответа просто отменяет, ничего клиенту не отправляя.
        if message.text in i18n.all_menu_labels():
            PENDING_REPLY.pop(message.from_user.id, None)
            await message.reply("❌ Это похоже на команду/кнопку меню, а не ответ клиенту — ничего не отправлено, режим ответа отменён.")
            return

        pending = PENDING_REPLY.pop(message.from_user.id)
        store_id = pending["store_id"]
        group_id = pending["group_id"]

        merchant_id = await asyncio.to_thread(_store_merchant_id, store_id)
        cookies = await asyncio.to_thread(load_chat_cookies, store_id)
        if not merchant_id or not cookies:
            await message.reply("⚠️ Не удалось отправить — сессия Kaspi-чата недоступна для этого магазина.")
            return

        result = await kaspi_send_chat_message(group_id, message.text, cookies, merchant_id)
        if result.get("success"):
            await asyncio.to_thread(config.mark_chat_replied, store_id, group_id, "telegram_manual")
            await asyncio.to_thread(audit.log_action, store_id, message.from_user.id, "chat_reply", group_id)
            await message.reply("✅ Отправлено клиенту.")
        else:
            await message.reply(f"❌ Ошибка отправки: `{result.get('error')}`")

    @dp.message(F.text.startswith("/start"))
    async def cmd_start(message: types.Message):
        parts = message.text.split(maxsplit=1)
        token = parts[1].strip() if len(parts) > 1 else None

        lang = i18n.resolve_lang(message.from_user.language_code or "ru")

        if token:
            result = await asyncio.to_thread(
                ti.consume_link_token,
                token,
                message.from_user.id,
                message.from_user.username,
                message.from_user.first_name,
                lang,
            )
            if not result["success"]:
                await message.reply(f"⚠️ {result['error']}")
                return
            role_title = {"OWNER": "владелец", "MANAGER": "менеджер", "VIEWER": "наблюдатель"}.get(result["role"], result["role"])
            greeting = "🎉 Добро пожаловать! " if result["is_new_employee"] else "✅ "
            welcome_text = f"{greeting}Telegram привязан к магазину *{_esc_md(result['store_name'])}* как _{role_title}_."
            if result["is_new_employee"]:
                await notify_dispatch.send_with_card(
                    message.from_user.id, welcome_text, card="welcome", reply_markup=get_main_reply_keyboard(lang),
                )
            else:
                await message.reply(welcome_text, reply_markup=get_main_reply_keyboard(lang))
            return

        access = await asyncio.to_thread(ti.resolve_bot_access, message.from_user.id)
        if not access["authorized"]:
            await message.reply(NOT_LINKED_TEXT)
            return
        if access["needs_store_selection"]:
            lines = ["👋 Привет! У вас несколько магазинов, выберите активный командой /switch:\n"]
            for s in access["store_choices"]:
                lines.append(f"• `{_esc_md(s['name'])}`")
            await message.reply("\n".join(lines))
            return

        await asyncio.to_thread(ti.touch_last_seen, message.from_user.id, message.from_user.username, message.from_user.first_name, lang)

        store_id = access["store_id"]
        store_line = f"🏪 **Магазин:** {_esc_md(config.get_store_name(store_id))}\n\n" if store_id else ""
        history_count = len(await asyncio.to_thread(load_printed_history, store_id))
        await message.reply(
            f"👋 Привет! Я бот-помощник магазина Kaspi.\n"
            f"{store_line}"
            f"👉 **С чего начать:** нажмите ☀️ **Утренний сбор** внизу — включит автосборку и пришлёт накладные на печать.\n\n"
            f"🖨 **Печать** — накопленные накладные из автосборки, либо пришлите ZIP/PDF вручную.\n"
            f"💬 **Чаты** — диалоги, где нужен человек; 📊 **Магазин** — сводка за сегодня.\n"
            f"• Уже напечатано сегодня: `{history_count}` шт\n\n"
            f"Ещё команды — по кнопке ⌨️ рядом с полем ввода.",
            reply_markup=get_main_reply_keyboard(lang),
        )

    @dp.message(F.text == "/switch")
    async def cmd_switch(message: types.Message):
        link = await asyncio.to_thread(ti.get_link, message.from_user.id)
        if not link:
            await message.reply(NOT_LINKED_TEXT)
            return
        stores = await asyncio.to_thread(ti.list_user_stores, link["user_id"])
        if not stores:
            await message.reply(NOT_LINKED_TEXT)
            return
        if len(stores) == 1:
            await message.reply(f"У вас только один магазин: **{_esc_md(stores[0]['name'])}**.")
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=s["name"], callback_data=f"switch_store:{s['store_id']}")]
            for s in stores
        ])
        await message.reply("🏪 Выберите активный магазин:", reply_markup=kb)

    @dp.callback_query(F.data.startswith("switch_store:"))
    async def process_switch_store(callback_query: types.CallbackQuery):
        store_id = callback_query.data.split(":", 1)[1]
        ok = await asyncio.to_thread(ti.set_active_store, callback_query.from_user.id, store_id)
        if ok:
            store_name = await asyncio.to_thread(config.get_store_name, store_id)
            await callback_query.answer(f"Активный магазин: {store_name}", show_alert=True)
            try:
                await callback_query.message.edit_text(f"🏪 Активный магазин: **{_esc_md(store_name)}**")
            except Exception:
                pass
        else:
            await callback_query.answer("Не удалось переключить магазин.", show_alert=True)

    @dp.message(F.text == "/unlink")
    async def cmd_unlink(message: types.Message):
        access_before = await asyncio.to_thread(ti.resolve_bot_access, message.from_user.id)
        ok = await asyncio.to_thread(ti.unlink_self, message.from_user.id)
        if ok and access_before.get("store_id"):
            await asyncio.to_thread(audit.log_action, access_before["store_id"], message.from_user.id, "unlink_self", "")
        if ok:
            await message.reply("🔌 Telegram отвязан от кабинета. Чтобы привязать заново — запросите новую ссылку в кабинете.")
        else:
            await message.reply("Этот Telegram и так не был привязан через кабинет (либо вы всё ещё на легаси-списке администраторов из .env).")

    @dp.message(F.text.in_({"/status"} | i18n.all_variants("status")))
    async def cmd_status(message: types.Message):
        access = await _require_access(message)
        if not access:
            return
        store_id = access["store_id"]
        # /status — это и способ отменить незавершённое обновление куки
        # чата (см. подсказку в process_chatcookie_start).
        PENDING_CHAT_LOGIN.pop(message.from_user.id, None)

        store_cfg = await asyncio.to_thread(config.load_chatbot_config, store_id)
        blocked = len(await asyncio.to_thread(config.list_blocked_chat_ids, store_id))
        cookie_bad = config.is_cookie_alert_sent(store_id) if store_id else config.any_cookie_alert_sent()
        store_line = f"🏪 **Магазин:** {_esc_md(config.get_store_name(store_id))}\n" if store_id else ""
        role_line = f"👤 **Ваша роль:** {access['role']}\n" if access["role"] else ""

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Обновить куки чата (SMS)", callback_data="chatcookie_start")
        ]])
        await message.reply(
            f"🤖 **Статус автоответчика:** {'🟢 Работает' if store_cfg.get('enabled', True) else '⏸️ Приостановлен'}\n"
            f"{store_line}"
            f"{role_line}"
            f"🔒 **Заблокировано чатов (Human Override):** {blocked}\n"
            f"🔑 **Cookie статус:** {'🚨 Требуется обновление' if cookie_bad else '✅ Валидны'}",
            reply_markup=kb,
        )

    @dp.callback_query(F.data == "chatcookie_start")
    async def process_chatcookie_start(callback_query: types.CallbackQuery):
        """Запускает шаг 1 из 2 повторного входа в Kaspi-чат (см.
        /api/chatbot/mobile-login/*) прямо из Telegram — раньше это было
        только в вебе, и протухшую SMS-сессию чата приходилось идти
        обновлять через кабинет, даже если владелец уже в Telegram."""
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /start заново.", show_alert=True)
            return
        if access["role"] == "VIEWER":
            await callback_query.answer("Обновлять сессию чата может только владелец или менеджер магазина.", show_alert=True)
            return
        PENDING_CHAT_LOGIN[callback_query.from_user.id] = {"store_id": access["store_id"], "step": "phone"}
        await callback_query.answer()
        await callback_query.message.reply(
            "📱 Пришлите номер телефона для входа в Kaspi-чат (формат: +7XXXXXXXXXX).\n"
            "Kaspi пришлёт код по SMS на этот номер.\n\n"
            "Отмена — команда /status."
        )

    @dp.callback_query(F.data.startswith("gdec:"))
    async def process_guard_decision(callback_query: types.CallbackQuery):
        """Кнопка под вопросом защиты демпинга «продавец без рейтинга рядом
        с нами» (backend/dumper/guard_notify.py): I — не учитывать его на
        этом товаре, A — не учитывать нигде (чёрный список магазина),
        C — учитывать. Магазин берётся из самой строки решения."""
        from backend.state_helpers import _get_guard_decision, _set_guard_decision, _guard_blacklist_merchant
        try:
            _, raw_id, choice = callback_query.data.split(":", 2)
            decision_id = int(raw_id)
        except ValueError:
            await callback_query.answer("Кнопка устарела.", show_alert=True)
            return
        status = {"I": "IGNORE", "A": "IGNORE", "C": "CONSIDER"}.get(choice)
        row = await asyncio.to_thread(_get_guard_decision, decision_id)
        if not row or not status:
            await callback_query.answer("Вопрос уже не актуален.", show_alert=True)
            return
        link = await asyncio.to_thread(ti.get_link, callback_query.from_user.id)
        role = await asyncio.to_thread(ti.get_role, link["user_id"], row["store_id"]) if link else None
        if not role:
            await callback_query.answer("Нет доступа к этому магазину. Откройте /start заново.", show_alert=True)
            return
        if role == "VIEWER":
            await callback_query.answer("Решать может только владелец или менеджер магазина.", show_alert=True)
            return
        who = f"tg:{callback_query.from_user.id}"
        if choice == "A":
            await asyncio.to_thread(_guard_blacklist_merchant, row["store_id"], row["merchant_id"])
        await asyncio.to_thread(_set_guard_decision, decision_id, status, who)
        await asyncio.to_thread(
            audit.log_action, row["store_id"], callback_query.from_user.id,
            "guard_decision", f"{row['sku']} {row['merchant_id']} -> {'IGNORE_ALL' if choice == 'A' else status}",
        )
        verdict = {"I": "🚫 Не учитываем на этом товаре.",
                   "A": "⛔ Не учитываем на всех товарах (чёрный список).",
                   "C": "✅ Учитываем, больше не спрашиваем."}[choice]
        try:
            await callback_query.message.edit_text(
                (callback_query.message.text or "") + "\n\n" + verdict, reply_markup=None)
        except Exception:
            pass
        await callback_query.answer(verdict)

    @dp.callback_query(F.data.startswith("chatcookie_quick:"))
    async def process_chatcookie_quick(callback_query: types.CallbackQuery):
        """Кнопка под алертом «сессия чата истекла»: сразу запрашивает SMS-код
        на последний номер, с которым проходил вход (chat_login_phone), —
        владельцу остаётся только прислать код. Нет сохранённого номера —
        тот же флоу, что chatcookie_start (спросить телефон). Магазин берётся
        из callback, а не из активного стора: алерт мог прийти по магазину,
        который у этого пользователя сейчас не выбран в /switch."""
        store_id = callback_query.data.split(":", 1)[1]
        link = await asyncio.to_thread(ti.get_link, callback_query.from_user.id)
        role = await asyncio.to_thread(ti.get_role, link["user_id"], store_id) if link else None
        if not role:
            await callback_query.answer("Нет доступа к этому магазину. Откройте /start заново.", show_alert=True)
            return
        if role == "VIEWER":
            await callback_query.answer("Обновлять сессию чата может только владелец или менеджер магазина.", show_alert=True)
            return

        phone = await asyncio.to_thread(config.get_chat_login_phone, store_id)
        if not phone:
            PENDING_CHAT_LOGIN[callback_query.from_user.id] = {"store_id": store_id, "step": "phone"}
            await callback_query.answer()
            await callback_query.message.reply(
                "📱 Пришлите номер телефона для входа в Kaspi-чат (формат: +7XXXXXXXXXX).\n"
                "Kaspi пришлёт код по SMS на этот номер.\n\n"
                "Отмена — команда /status."
            )
            return

        from backend.services.playwright_worker import worker, Command
        from backend.chatbot.notifications import mask_phone

        await callback_query.answer("Запрашиваю SMS-код…")
        await callback_query.message.reply(f"⏳ Запрашиваю у Kaspi SMS-код на {mask_phone(phone)}...")
        success, msg, _cookies = await asyncio.to_thread(
            worker.send_cmd, Command.START_SMS_LOGIN, phone, store_id=store_id
        )
        if not success:
            PENDING_CHAT_LOGIN.pop(callback_query.from_user.id, None)
            await callback_query.message.reply(
                f"❌ Не удалось отправить код: {msg}\n"
                "Попробуйте ещё раз кнопкой или через /status → «Обновить куки чата» с другим номером."
            )
            return
        PENDING_CHAT_LOGIN[callback_query.from_user.id] = {"store_id": store_id, "step": "code"}
        await callback_query.message.reply(
            f"✅ Код отправлен на {mask_phone(phone)}. Пришлите его следующим сообщением.\n\n"
            "Отмена — команда /status."
        )

    @dp.message(lambda m: bool(m.text) and not m.text.startswith("/") and m.from_user and m.from_user.id in PENDING_CHAT_LOGIN)
    async def handle_chat_cookie_login(message: types.Message):
        pending = PENDING_CHAT_LOGIN.get(message.from_user.id)
        if not pending:
            return
        if message.text in i18n.all_menu_labels():
            PENDING_CHAT_LOGIN.pop(message.from_user.id, None)
            await message.reply("❌ Это похоже на кнопку меню — обновление куки чата отменено.")
            return

        from backend.services.playwright_worker import worker, Command

        store_id = pending["store_id"]
        text = message.text.strip()

        if pending["step"] == "phone":
            await message.reply("⏳ Отправляю телефон, жду код из SMS от Kaspi...")
            success, msg, _cookies = await asyncio.to_thread(
                worker.send_cmd, Command.START_SMS_LOGIN, text, store_id=store_id
            )
            if not success:
                PENDING_CHAT_LOGIN.pop(message.from_user.id, None)
                await message.reply(f"❌ Не удалось отправить код: {msg}")
                return
            await asyncio.to_thread(config.set_chat_login_phone, store_id, text)
            PENDING_CHAT_LOGIN[message.from_user.id] = {"store_id": store_id, "step": "code"}
            await message.reply("✅ Код отправлен. Пришлите его следующим сообщением.")
            return

        # step == "code"
        PENDING_CHAT_LOGIN.pop(message.from_user.id, None)
        success, msg, fresh_cookies = await asyncio.to_thread(
            worker.send_cmd, Command.SUBMIT_SMS_LOGIN, text, store_id=store_id
        )
        if not success:
            await message.reply(f"❌ Не удалось войти: {msg}")
            return
        if not await asyncio.to_thread(_save_chat_cookies_for_store, store_id, fresh_cookies):
            await message.reply("⚠️ Вход выполнен, но не удалось сохранить куки чата.")
            return
        await asyncio.to_thread(config.set_cookie_alert_sent, store_id, False)
        await asyncio.to_thread(audit.log_action, store_id, message.from_user.id, "chat_cookie_refresh", "telegram")
        await message.reply("✅ Вход в чат Kaspi выполнен, сессия обновлена.")

    @dp.message(F.text.in_({"/settings"} | i18n.all_variants("settings")))
    async def cmd_settings(message: types.Message):
        access = await _require_access(message)
        if not access:
            return
        text, kb = get_settings_categories_view(access["store_id"])
        await message.reply(text, reply_markup=kb)

    @dp.callback_query(F.data == "stg_back")
    async def process_settings_back(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /settings заново.", show_alert=True)
            return
        text, kb = get_settings_categories_view(access["store_id"])
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        await callback_query.answer()

    @dp.callback_query(F.data.startswith("stg_cat:"))
    async def process_settings_category(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /settings заново.", show_alert=True)
            return
        cat = callback_query.data.split(":", 1)[1]
        if cat == "assembly":
            text, kb = await asyncio.to_thread(get_assembly_view, access["store_id"])
        else:
            text, kb = get_print_settings_view(access["store_id"])
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        await callback_query.answer()

    @dp.message(F.text == "/notif")
    async def cmd_notif(message: types.Message):
        """Прямой доступ к уведомлениям, минуя /settings — раньше это было
        закопано на 2 уровня (Настройки → Уведомления), хотя используется не
        реже основных переключателей печати."""
        access = await _require_access(message)
        if not access:
            return
        text, kb = await asyncio.to_thread(get_notification_settings_view, access["store_id"], message.from_user.id)
        await message.reply(text, reply_markup=kb)

    @dp.message(F.text.in_({"/print"} | i18n.all_variants("print")))
    async def cmd_print_menu(message: types.Message):
        access = await _require_access(message)
        if not access:
            return
        rows = await asyncio.to_thread(get_unprinted_spool_rows, access["store_id"])
        text, kb = get_print_menu_view(access["store_id"], len(rows))
        await message.reply(text, reply_markup=kb)

    @dp.callback_query(F.data == "spool_layout_menu")
    async def process_spool_layout_menu(callback_query: types.CallbackQuery):
        await callback_query.answer()
        try:
            await callback_query.message.edit_reply_markup(reply_markup=get_layout_keyboard(prefix="spool_fmt"))
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("spool_fmt:") | F.data.startswith("spool_force:") | F.data.startswith("spool_dup:"))
    async def process_spool_print(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте меню печати заново: /start", show_alert=True)
            return
        store_id = access["store_id"]

        data_parts = callback_query.data.split(":")
        action_type = data_parts[0]  # 'spool_fmt', 'spool_force' или 'spool_dup'

        if action_type == "spool_force":
            layout_mode = data_parts[1]
            filter_duplicates = False
            force_reprint = True
        elif action_type == "spool_dup":
            # data: spool_dup:<all|new|reset>:<layout_mode>
            choice = data_parts[1]
            layout_mode = data_parts[2] if len(data_parts) > 2 else "auto"
            if choice == "reset":
                await asyncio.to_thread(reset_printed_history, store_id)
            filter_duplicates = (choice == "new")
            force_reprint = False
        else:
            layout_mode = data_parts[1]
            stg = await asyncio.to_thread(config.load_print_settings, store_id)
            dup_mode = stg.get("dup_handling_mode", "always_new")
            if dup_mode == "always_ask":
                # Как и при ручной загрузке: спрашиваем только если в спуле
                # действительно есть уже напечатанное сегодня.
                spool_rows = await asyncio.to_thread(get_unprinted_spool_rows, store_id)
                codes = [r.get("order_code") for r in spool_rows if r.get("order_code")]
                already = await asyncio.to_thread(count_already_printed_orders, codes, store_id)
                if codes and already == 0:
                    filter_duplicates = True
                    force_reprint = False
                else:
                    await callback_query.answer()
                    ask_kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🆕 Только новые (пропустить повторы)", callback_data=f"spool_dup:new:{layout_mode}")],
                        [InlineKeyboardButton(text="🖨 Печатать все", callback_data=f"spool_dup:all:{layout_mode}")],
                        [InlineKeyboardButton(text="♻️ Сбросить историю за сегодня и печатать всё", callback_data=f"spool_dup:reset:{layout_mode}")],
                    ])
                    try:
                        await callback_query.message.edit_text(
                            f"🛑 Уже печатались сегодня: **{already}** из **{len(codes)}** заказов.\n\n"
                            f"Какие накладные печатать?",
                            reply_markup=ask_kb
                        )
                    except Exception:
                        pass
                    return
            else:
                filter_duplicates = (dup_mode != "always_all")
                force_reprint = False

        await callback_query.answer()
        try:
            await callback_query.message.edit_text("⏳ **Собираю накопленные накладные в пачку...**")
        except Exception:
            pass

        rows = await asyncio.to_thread(get_unprinted_spool_rows, store_id)
        if not rows:
            try:
                await callback_query.message.edit_text("📭 Нет накопленных накладных для печати.")
            except Exception:
                pass
            return

        try:
            zip_bytes = await asyncio.to_thread(build_spool_zip_bytes, store_id)
            # sort_preset/express_mode/auto_rotate фиксированы (не читаем
            # per-store print_settings) — та же раскладка, что и в вебе
            # (routers/auto_assembly.py::download_spool), спул это отдельный
            # от ручной загрузки в PrintBatcher поток. dup_handling_mode —
            # исключение, читается из настроек (см. выше). Крупные категории
            # магазина здесь тоже неприменимы: сортировка по номеру заказа о
            # товаре ничего не знает, а значит и категорию определить не из чего.
            manifest = await asyncio.to_thread(
                process_zip_or_pdfs,
                file_bytes=zip_bytes,
                original_filename="auto_assembly_spool.zip",
                sort_preset="order_id",
                auto_rotate=True,
                express_mode="separate",
                deduplicate_history=True,
                filter_duplicates=filter_duplicates,
                force_reprint=force_reprint,
                output_layout=layout_mode,
                store_id=store_id,
            )

            if manifest["all_duplicates"]:
                force_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🖨️ Напечатать заново принудительно", callback_data=f"spool_force:{layout_mode}")]
                ])
                await callback_query.message.edit_text(
                    "⚠️ **Все накопленные накладные уже были напечатаны сегодня.**\n\n"
                    "Нажмите кнопку ниже, если хотите принудительно перепечатать:",
                    reply_markup=force_kb
                )
                return

            await callback_query.message.edit_text(
                f"✅ **Пачка готова, отправляю файлы...**\n"
                f"🧾 Накладных в пачке: `{len(rows)}` шт"
            )
            await _send_spool_batch(callback_query.message.chat.id, manifest)

        except Exception as e:
            logger.error(f"Ошибка при сборке пачки спула в Telegram: {e}", exc_info=True)
            await callback_query.message.answer(f"❌ **Ошибка при сборке пачки:** `{str(e)}`")

    @dp.callback_query(F.data == "spool_mark_all")
    async def process_spool_mark_all(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /start заново.", show_alert=True)
            return
        count = await asyncio.to_thread(mark_spool_printed, access["store_id"])
        if count:
            await asyncio.to_thread(audit.log_action, access["store_id"], callback_query.from_user.id, "spool_mark_printed", str(count))
        await callback_query.answer(f"Помечено напечатанными: {count}", show_alert=True)
        try:
            await callback_query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    @dp.message(F.text.in_({"/assembly"} | i18n.all_variants("assembly")))
    async def cmd_assembly_menu(message: types.Message):
        access = await _require_access(message)
        if not access:
            return
        text, kb = await asyncio.to_thread(get_assembly_view, access["store_id"])
        await message.reply(text, reply_markup=kb)

    @dp.callback_query(F.data == "aa_back")
    async def process_aa_back(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /start заново.", show_alert=True)
            return
        text, kb = await asyncio.to_thread(get_assembly_view, access["store_id"])
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        await callback_query.answer()

    @dp.callback_query(F.data.startswith("aa_toggle:"))
    async def process_aa_toggle(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /start заново.", show_alert=True)
            return
        if access["role"] != "OWNER":
            await callback_query.answer("Включать/выключать автосборку может только владелец магазина.", show_alert=True)
            return
        delivery_type = callback_query.data.split(":", 1)[1]
        new_state = await asyncio.to_thread(_toggle_rule_enabled, access["store_id"], delivery_type)
        await asyncio.to_thread(
            audit.log_action, access["store_id"], callback_query.from_user.id,
            "aa_toggle", f"{delivery_type} -> {'ON' if new_state else 'OFF'}",
        )
        await callback_query.answer("Готово")
        text, kb = await asyncio.to_thread(get_assembly_view, access["store_id"])
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("aa_cfg:"))
    async def process_aa_cfg(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /start заново.", show_alert=True)
            return
        delivery_type = callback_query.data.split(":", 1)[1]
        text, kb = await asyncio.to_thread(
            get_assembly_rule_config_view, access["store_id"], delivery_type, access["role"] == "OWNER"
        )
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        await callback_query.answer()

    @dp.callback_query(F.data.startswith("aa_field:"))
    async def process_aa_field(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /start заново.", show_alert=True)
            return
        if access["role"] != "OWNER":
            await callback_query.answer("Изменять правила автосборки может только владелец магазина.", show_alert=True)
            return
        _, field, delivery_type = callback_query.data.split(":", 2)
        if field not in ALLOWED_RULE_FIELDS:
            await callback_query.answer("Неизвестное поле настройки.", show_alert=True)
            return
        new_state = await asyncio.to_thread(_toggle_rule_field, access["store_id"], delivery_type, field)
        await asyncio.to_thread(
            audit.log_action, access["store_id"], callback_query.from_user.id,
            "aa_field", f"{delivery_type}.{field} -> {'ON' if new_state else 'OFF'}",
        )
        await callback_query.answer("Сохранено")
        text, kb = await asyncio.to_thread(get_assembly_rule_config_view, access["store_id"], delivery_type, True)
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass

    @dp.message(F.text.in_({"/morning"} | i18n.all_variants("morning")))
    async def cmd_morning_routine(message: types.Message):
        access = await _require_access(message)
        if not access:
            return
        store_id = access["store_id"]
        is_owner_role = access["role"] == "OWNER"

        status_msg = await message.reply("☀️ **Утренний сбор запущен...**")
        enabled_now: List[str] = []

        if is_owner_role:
            # Шаги 1-2 плана (§5.2): включить простаивающие правила и сразу
            # прогнать цикл автосборки, не дожидаясь фонового тика. Только
            # владелец — сотрудник (шаги 3-5) просто забирает уже готовое.
            rules = await asyncio.to_thread(_load_delivery_rules, store_id)
            disabled_with_stages = [
                r for r in rules
                if not r["enabled"] and (r["stage_accept"] or r["stage_assemble"] or r["stage_waybill"])
            ]
            for r in disabled_with_stages:
                await asyncio.to_thread(_toggle_rule_enabled, store_id, r["delivery_type"])
                enabled_now.append(r["delivery_type"])

            if enabled_now:
                try:
                    await status_msg.edit_text(f"🟢 Включил автосборку: {', '.join(enabled_now)}\n⏳ Запускаю цикл...")
                except Exception:
                    pass
            else:
                try:
                    await status_msg.edit_text("⏳ Запускаю цикл автосборки...")
                except Exception:
                    pass

            store_ctx = await get_store_by_id(store_id)
            if store_ctx:
                try:
                    await run_auto_assembly_cycle(store_ctx.token, store_id)
                except Exception as e:
                    logger.error(f"Ошибка цикла автосборки из утреннего сбора: {e}", exc_info=True)

        try:
            await status_msg.edit_text(
                ("🟢 Включил: " + ", ".join(enabled_now) + "\n" if enabled_now else "")
                + "📦 Собираю накопленные накладные в пачку..."
            )
        except Exception:
            pass

        rows = await asyncio.to_thread(get_unprinted_spool_rows, store_id)
        if not rows:
            try:
                await status_msg.edit_text(
                    "☀️ **Утренний сбор завершён.**\n\n"
                    + ("🟢 Включил: " + ", ".join(enabled_now) + "\n" if enabled_now else "")
                    + "📭 Накладных к печати пока нет."
                )
            except Exception:
                pass
            return

        try:
            zip_bytes = await asyncio.to_thread(build_spool_zip_bytes, store_id)
            stg = await asyncio.to_thread(config.load_print_settings, store_id)
            dup_mode = stg.get("dup_handling_mode", "always_new")
            # Утренний сбор автоматический, без диалога — "Всегда спрашивать"
            # здесь не спрашивает (некому отвечать), падаем на тихий режим
            # "только новые", как и раньше по умолчанию.
            filter_duplicates = (dup_mode != "always_all")
            manifest = await asyncio.to_thread(
                process_zip_or_pdfs,
                file_bytes=zip_bytes,
                original_filename="morning_routine_spool.zip",
                sort_preset="order_id",
                auto_rotate=True,
                express_mode="separate",
                deduplicate_history=True,
                filter_duplicates=filter_duplicates,
                store_categories=stg.get("store_categories"),
                force_reprint=False,
                output_layout="auto",
                store_id=store_id,
            )

            if manifest["all_duplicates"]:
                try:
                    await status_msg.edit_text("⚠️ Все накопленные накладные уже были напечатаны сегодня.")
                except Exception:
                    pass
                return

            await status_msg.edit_text(
                "☀️ **Утренний сбор завершён.**\n\n"
                + ("🟢 Включил: " + ", ".join(enabled_now) + "\n" if enabled_now else "")
                + f"🧾 Накладных в пачке: `{len(rows)}` шт\n📤 Отправляю файлы..."
            )
            await _send_spool_batch(message.chat.id, manifest)

        except Exception as e:
            logger.error(f"Ошибка сборки пачки в утреннем сборе: {e}", exc_info=True)
            await message.answer(f"❌ **Ошибка при сборке пачки:** `{str(e)}`")

    @dp.message(F.text == "/reset")
    async def cmd_reset(message: types.Message):
        access = await _require_access(message)
        if not access:
            return
        await asyncio.to_thread(reset_printed_history, access["store_id"])
        await message.reply("♻️ **История напечатанных заказов за сегодня успешно сброшена!**")

    @dp.message(F.text == "/stats")
    async def cmd_stats(message: types.Message):
        access = await _require_access(message)
        if not access:
            return
        count = len(await asyncio.to_thread(load_printed_history, access["store_id"]))
        await message.reply(f"🏷️ **Напечатано заказов за сегодня:** `{count}` шт")

    @dp.callback_query(
        F.data.startswith("stg_")
        & ~F.data.startswith("stg_notif_")
        & ~F.data.startswith("stg_cat:")
        & (F.data != "stg_back")
    )
    async def process_settings_toggle(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Доступ недоступен в этом контексте, откройте /settings заново.", show_alert=True)
            return
        store_id = access["store_id"]

        data = callback_query.data
        stg = await asyncio.to_thread(config.load_print_settings, store_id)

        if data.startswith("stg_exp:"):
            stg["express_mode"] = data.split(":")[1]
        elif data.startswith("stg_srt:"):
            stg["sort_preset"] = data.split(":")[1]
        elif data.startswith("stg_fmt:"):
            stg["output_layout"] = data.split(":")[1]
        elif data.startswith("stg_dup:"):
            stg["dup_handling_mode"] = data.split(":")[1]
        elif data.startswith("stg_toggle:"):
            key = data.split(":")[1]
            stg[key] = not stg.get(key, True)
        elif data == "stg_reset_hist":
            await asyncio.to_thread(reset_printed_history, store_id)
            await callback_query.answer("♻️ История печати за сегодня сброшена!", show_alert=True)
            text, kb = get_print_settings_view(store_id)
            try:
                await callback_query.message.edit_text(text, reply_markup=kb)
            except Exception:
                pass
            return

        await asyncio.to_thread(config.save_print_settings, stg, store_id)
        await callback_query.answer("⚙️ Настройка сохранена!")

        text, kb = get_print_settings_view(store_id)
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass

    @dp.callback_query(F.data == "stg_notif_menu")
    async def process_notif_menu(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /settings заново.", show_alert=True)
            return
        text, kb = await asyncio.to_thread(get_notification_settings_view, access["store_id"], callback_query.from_user.id)
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        await callback_query.answer()

    @dp.callback_query(F.data == "stg_notif_back")
    async def process_notif_back(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer()
            return
        text, kb = get_settings_categories_view(access["store_id"])
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        await callback_query.answer()

    @dp.callback_query(F.data.startswith("stg_notif_toggle:"))
    async def process_notif_toggle(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /settings заново.", show_alert=True)
            return
        group = callback_query.data.split(":", 1)[1]
        await asyncio.to_thread(notification_prefs.toggle_group, callback_query.from_user.id, access["store_id"], group)
        await asyncio.to_thread(audit.log_action, access["store_id"], callback_query.from_user.id, "notif_toggle", group)
        await callback_query.answer("Сохранено")
        text, kb = await asyncio.to_thread(get_notification_settings_view, access["store_id"], callback_query.from_user.id)
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("stg_notif_quiet:"))
    async def process_notif_quiet(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /settings заново.", show_alert=True)
            return
        mode = callback_query.data.split(":", 1)[1]
        if mode == "on":
            await asyncio.to_thread(notification_prefs.set_quiet_hours, callback_query.from_user.id, access["store_id"], "23:00", "08:00")
        else:
            await asyncio.to_thread(notification_prefs.set_quiet_hours, callback_query.from_user.id, access["store_id"], None, None)
        await asyncio.to_thread(audit.log_action, access["store_id"], callback_query.from_user.id, "notif_quiet_hours", mode)
        await callback_query.answer("Сохранено")
        text, kb = await asyncio.to_thread(get_notification_settings_view, access["store_id"], callback_query.from_user.id)
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass

    @dp.message(F.text.in_({"/chats"} | i18n.all_variants("chats")))
    async def cmd_chats_menu(message: types.Message):
        access = await _require_access(message)
        if not access:
            return
        text, kb = await asyncio.to_thread(get_chats_view, access["store_id"])
        await message.reply(text, reply_markup=kb)

    @dp.callback_query(F.data.startswith("chat_open:"))
    async def process_chat_open(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /start заново.", show_alert=True)
            return
        group_id = callback_query.data.split(":", 1)[1]
        PENDING_REPLY[callback_query.from_user.id] = {"group_id": group_id, "store_id": access["store_id"]}
        await callback_query.answer()
        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"chat_cancel:{group_id}")]
        ])
        await callback_query.message.answer(
            f"✍️ Напишите ответ клиенту следующим сообщением (диалог `{group_id}`).",
            reply_markup=cancel_kb,
        )

    @dp.callback_query(F.data.startswith("chat_cancel:"))
    async def process_chat_cancel(callback_query: types.CallbackQuery):
        pending = PENDING_REPLY.get(callback_query.from_user.id)
        group_id = callback_query.data.split(":", 1)[1]
        if pending and pending.get("group_id") == group_id:
            PENDING_REPLY.pop(callback_query.from_user.id, None)
            await callback_query.answer("Ответ отменён.")
        else:
            await callback_query.answer("Уже отменено.")
        try:
            await callback_query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("chat_ai:"))
    async def process_chat_return_ai(callback_query: types.CallbackQuery):
        access = await asyncio.to_thread(ti.resolve_bot_access, callback_query.from_user.id)
        if not access["authorized"] or access["needs_store_selection"]:
            await callback_query.answer("Откройте /start заново.", show_alert=True)
            return
        group_id = callback_query.data.split(":", 1)[1]
        ok = await asyncio.to_thread(config.clear_chat_human_override, access["store_id"], group_id)
        if ok:
            await asyncio.to_thread(audit.log_action, access["store_id"], callback_query.from_user.id, "chat_return_ai", group_id)
        await callback_query.answer("🤖 Вернул ИИ" if ok else "Уже не заблокирован", show_alert=True)
        text, kb = await asyncio.to_thread(get_chats_view, access["store_id"])
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass

    @dp.message(F.text.in_({"/store"} | i18n.all_variants("store")))
    async def cmd_store_status(message: types.Message):
        access = await _require_access(message)
        if not access:
            return
        store_id = access["store_id"]

        from backend.state_helpers import _load_sales_summary, _load_dumper_logs

        cookie_bad = config.is_cookie_alert_sent(store_id)
        summary = await asyncio.to_thread(_load_sales_summary, store_id, 1)
        spool_rows = await asyncio.to_thread(get_unprinted_spool_rows, store_id)
        failures = await asyncio.to_thread(_recent_auto_assembly_failures, store_id)
        dumper_logs = await asyncio.to_thread(_load_dumper_logs, store_id, 1)

        lines = [
            f"📊 **{_esc_md(config.get_store_name(store_id))}**\n",
            f"🔑 Сессия Kaspi: {'🚨 истекла' if cookie_bad else '🟢 ОК'}",
            f"📦 Заказов сегодня: `{summary.get('total_orders', 0)}` · выручка `{summary.get('total_revenue', 0):,.0f} ₸`".replace(",", " "),
            f"🖨 В очереди на печать: `{len(spool_rows)}`",
        ]
        if dumper_logs:
            lines.append(f"📉 Демпинг: последнее событие — _{_esc_md(dumper_logs[0].get('message', 'н/д'))}_")
        if failures:
            lines.append("\n🚨 **Последние ошибки автосборки:**")
            lines.extend(f"• {_esc_md(f)}" for f in failures)

        if len(spool_rows) > 0:
            lines.append(f"\n👉 Есть что печатать — нажмите 🖨 **Печать** внизу.")
        else:
            lines.append(f"\n👉 Всё чисто — нажмите ☀️ **Утренний сбор**, когда появятся новые заказы.")

        await message.reply("\n".join(lines))

    async def _run_manual_print_job(
        status_message: types.Message,
        chat_id: int,
        pending: dict,
        layout_mode: str,
        filter_duplicates: bool,
        force_reprint: bool,
    ) -> None:
        """Общая обработка ручной загрузки .zip/.pdf — общий хвост между
        прямой обработкой (dup_handling_mode != always_ask), выбором в
        опросе «какие накладные печатать» и принудительной перепечаткой.

        filter_duplicates — только про ЭТОТ прогон. Журнал напечатанного
        ведётся по настройке магазина независимо, иначе пачка, напечатанная
        в режиме «все», выпадала из истории и следующая пачка считала её
        заказы новыми."""
        try:
            await status_message.edit_text("⏳ **Загрузка архива, обрезка белых полей и подгонка MediaBox...**")
        except Exception:
            pass

        try:
            bot = bot_instance.bot
            # Предпроверка повторов уже могла скачать файл — не тянем второй раз.
            file_bytes = pending.get("file_bytes")
            if file_bytes is None:
                file_info = await bot.get_file(pending["file_id"])
                file_stream = io.BytesIO()
                await bot.download_file(file_info.file_path, destination=file_stream)
                file_bytes = file_stream.getvalue()

            store_id = pending.get("store_id")
            stg = await asyncio.to_thread(config.load_print_settings, store_id)

            # PyMuPDF-обработка — синхронная и CPU-тяжёлая (обрезка полей,
            # рендер N-up сеток на десятках страниц). Вызов напрямую в
            # async-хэндлере блокировал бы весь event loop процесса — а это
            # тот же процесс, что обслуживает веб-кабинет и API всех
            # магазинов (см. docs/TELEGRAM_BOT_PLAN.md §7.1).
            # Тот же состав заказов, что и при загрузке из кабинета: иначе
            # одна и та же партия раскладывалась бы по-разному в зависимости
            # от того, откуда её напечатали.
            sort_preset = stg.get("sort_preset", "filename")
            order_skus = None
            if sort_preset in ("master_group", "product_name"):
                store_ctx = await get_store_by_id(store_id)
                if store_ctx:
                    order_skus = await asyncio.to_thread(fetch_order_skus, store_ctx.token)

            manifest = await asyncio.to_thread(
                process_zip_or_pdfs,
                file_bytes=file_bytes,
                original_filename=pending["file_name"],
                sort_preset=sort_preset,
                order_skus=order_skus,
                auto_rotate=stg.get("auto_rotate", True),
                express_mode=stg.get("express_mode", "separate"),
                deduplicate_history=stg.get("deduplicate_history", True),
                filter_duplicates=filter_duplicates,
                store_categories=stg.get("store_categories"),
                force_reprint=force_reprint,
                output_layout=layout_mode,
                store_id=store_id,
            )

            total_files = manifest["total_files"]
            total_pages = manifest["total_pages"]
            dedup_pages = manifest["deduplicated_pages"]
            new_pages = manifest["new_pages"]
            all_dups = manifest["all_duplicates"]
            batches = manifest["batches"]
            express_skipped = manifest.get("excluded_express_pages", 0)
            unreadable = manifest.get("skipped_files", 0)
            ungrouped = manifest.get("ungrouped_pages", 0)
            grouped = manifest.get("grouped_pages", 0)

            if all_dups:
                force_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🖨️ Напечатать заново принудительно", callback_data="pb_force_all")]
                ])
                await status_message.edit_text(
                    f"⚠️ **Все {total_pages} заказов из этого архива уже были напечатаны ранее!**\n\n"
                    f"Система автоматически отфильтровала старые дубликаты. Нажмите кнопку ниже, если хотите принудительно перепечатать весь этот архив:",
                    reply_markup=force_kb
                )
                return

            report_lines = [
                "✅ **Печатный пакет успешно скомпонован!**",
                f"📁 **Файлов в архиве:** `{total_files}` шт | **Всего листов:** `{total_pages}`",
            ]
            if filter_duplicates:
                report_lines.append(f"🛑 **Исключено дубликатов:** `{dedup_pages}` страниц")
            else:
                report_lines.append("🖨 **Режим «печатать все»** — повторы не отсеивались")
            # Экспресс и нечитаемые файлы раньше молча исчезали в разнице между
            # «Файлов в архиве» и «Всего листов» — теперь названы поимённо.
            if express_skipped:
                report_lines.append(f"⚡ **Экспресс отложен:** `{express_skipped}` страниц (режим «Исключать»)")
            if unreadable:
                report_lines.append(f"⚠️ **Не удалось прочитать файлов:** `{unreadable}`")
            if grouped or ungrouped:
                report_lines.append(
                    f"🏷 **Связка остатков:** `{grouped}` со штампом группы, `{ungrouped}` без"
                )
            report_lines.append(f"✨ **Новых листов к печати:** `{new_pages}` страниц")
            report_lines.append("📤 Отправка файлов в чат...")
            await status_message.edit_text("\n".join(report_lines))

            for b_key, b_info in batches.items():
                batch_file_path = get_batch_file_path(manifest["batch_id"], b_info["category"])
                if batch_file_path and batch_file_path.exists():
                    input_file = FSInputFile(path=str(batch_file_path), filename=b_info["file_name"])

                    audit_msg = b_info.get("geometry_audit", {}).get("audit_message", "")
                    caption = (
                        f"{b_info['icon']} **{b_info['title']}**\n"
                        f"🖨️ `{b_info['printer_type']}`\n"
                        f"📄 Страниц: `{b_info['total_pages']}` шт | Размеры: `{b_info['sample_dimensions']}`\n"
                        f"{audit_msg}"
                    )
                    await bot.send_document(
                        chat_id=chat_id,
                        document=input_file,
                        caption=caption
                    )

        except Exception as e:
            logger.error(f"Ошибка при обработке печатного пакета в Telegram: {e}", exc_info=True)
            await status_message.answer(f"❌ **Ошибка при обработке файла:** `{str(e)}`")

    @dp.message(F.document)
    async def handle_document(message: types.Message):
        access = await _require_access(message)
        if not access:
            return

        doc = message.document
        if not doc or not doc.file_name:
            return

        filename = doc.file_name.lower()
        if not (filename.endswith(".zip") or filename.endswith(".pdf")):
            await message.reply("⚠️ Поддерживаются только `.zip` архивы и `.pdf` файлы.")
            return

        chat_id = message.chat.id
        store_id = access["store_id"]
        pending = {
            "file_id": doc.file_id,
            "file_name": doc.file_name,
            "file_size": doc.file_size,
            "store_id": store_id,
        }
        PENDING_FILES[chat_id] = pending

        size_mb = (doc.file_size or 0) / (1024 * 1024)
        stg = await asyncio.to_thread(config.load_print_settings, store_id)
        dup_mode = stg.get("dup_handling_mode", "always_new")
        layout_mode = stg.get("output_layout", "auto")

        # Раньше здесь при каждой загрузке спрашивали формат/размер наклейки —
        # теперь формат берётся из настроек по умолчанию (Настройки → Печать),
        # а опрос (если включён режим "Всегда спрашивать") — про то, какие
        # накладные печатать (все / только новые).
        if dup_mode == "always_ask":
            # Но спрашивать есть смысл, только если в архиве реально есть уже
            # напечатанное. Проверяем это по оглавлению zip (номера заказов в
            # именах файлов) — миллисекунды, без разбора PDF. Архив без единого
            # повтора уходит в печать сразу: лишний вопрос там раздражает и
            # провоцирует ответ «печатать все» по привычке.
            status_message = await message.reply(
                f"📦 **Получен файл:** `{doc.file_name}` ({size_mb:.2f} МБ)\n⏳ Проверяю, есть ли повторы..."
            )
            try:
                file_info = await bot_instance.bot.get_file(doc.file_id)
                stream = io.BytesIO()
                await bot_instance.bot.download_file(file_info.file_path, destination=stream)
                pending["file_bytes"] = stream.getvalue()
                already, total_orders = await asyncio.to_thread(
                    peek_already_printed, pending["file_bytes"], doc.file_name, store_id
                )
            except Exception as e:
                logger.warning(f"Печать: предпроверка повторов не удалась, спрашиваем как обычно: {e}")
                already, total_orders = 0, 0

            if total_orders and already == 0:
                try:
                    await status_message.edit_text(
                        f"📦 **Получен файл:** `{doc.file_name}` ({size_mb:.2f} МБ)\n"
                        f"✅ Повторов нет — все `{total_orders}` заказов новые, печатаю."
                    )
                except Exception:
                    pass
                await _run_manual_print_job(
                    status_message, chat_id, pending, layout_mode,
                    filter_duplicates=True, force_reprint=False
                )
                return

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🆕 Только новые (пропустить повторы)", callback_data="pb_dup:new")],
                [InlineKeyboardButton(text="🖨 Печатать все", callback_data="pb_dup:all")],
                [InlineKeyboardButton(text="♻️ Сбросить историю за сегодня и печатать всё", callback_data="pb_dup:reset")],
            ])
            hint = (
                f"🛑 Уже печатались сегодня: **{already}** из **{total_orders}** заказов."
                if total_orders else
                "Не удалось заранее определить повторы по этому файлу."
            )
            try:
                await status_message.edit_text(
                    f"📦 **Получен файл:** `{doc.file_name}` ({size_mb:.2f} МБ)\n\n"
                    f"{hint}\n\nКакие накладные печатать?",
                    reply_markup=kb
                )
            except Exception:
                pass
            return

        filter_duplicates = (dup_mode != "always_all")
        status_message = await message.reply(f"📦 **Получен файл:** `{doc.file_name}` ({size_mb:.2f} МБ)")
        await _run_manual_print_job(status_message, chat_id, pending, layout_mode, filter_duplicates, force_reprint=False)

    @dp.callback_query(F.data.startswith("pb_dup:"))
    async def process_pb_dup_choice(callback_query: types.CallbackQuery):
        chat_id = callback_query.message.chat.id
        pending = PENDING_FILES.get(chat_id)
        if not pending:
            await callback_query.answer("⚠️ Файл не найден или истёк срок действия. Отправьте архив заново.", show_alert=True)
            return
        choice = callback_query.data.split(":", 1)[1]  # 'all' | 'new' | 'reset'
        if choice == "reset":
            # Сброс прямо в потоке печати: кнопка нужна ровно в тот момент,
            # когда стало ясно, что история мешает, — а не в дебрях настроек.
            await asyncio.to_thread(reset_printed_history, pending.get("store_id"))
        filter_duplicates = (choice == "new")
        await callback_query.answer()
        stg = await asyncio.to_thread(config.load_print_settings, pending.get("store_id"))
        layout_mode = stg.get("output_layout", "auto")
        await _run_manual_print_job(callback_query.message, chat_id, pending, layout_mode, filter_duplicates, force_reprint=False)

    @dp.callback_query(F.data == "pb_force_all")
    async def process_pb_force_all(callback_query: types.CallbackQuery):
        chat_id = callback_query.message.chat.id
        pending = PENDING_FILES.get(chat_id)
        if not pending:
            await callback_query.answer("⚠️ Файл не найден или истёк срок действия. Отправьте архив заново.", show_alert=True)
            return
        await callback_query.answer()
        stg = await asyncio.to_thread(config.load_print_settings, pending.get("store_id"))
        layout_mode = stg.get("output_layout", "auto")
        await _run_manual_print_job(callback_query.message, chat_id, pending, layout_mode, filter_duplicates=False, force_reprint=True)

    @dp.callback_query(F.data.startswith("unblock_"))
    async def process_unblock_callback(callback_query: types.CallbackQuery):
        # split("_", 1): group_id может само содержать "_", берём весь хвост
        group_id = callback_query.data.split("_", 1)[1]

        # Слайс 4: чат теперь per-store (chat_states) — Telegram-колбэк не
        # несёт store_id (единый бот на все магазины), поэтому ищем и
        # разблокируем чат с этим group_id независимо от того, какому
        # магазину он принадлежит.
        unblocked_store_id = await asyncio.to_thread(config.clear_human_override_any_store, group_id)

        if unblocked_store_id is not None:
            original_text = callback_query.message.text or ""
            updated_text = original_text + "\n\n✅ **Чат разблокирован менеджером для ИИ.**"

            try:
                await callback_query.message.edit_text(
                    text=updated_text,
                    reply_markup=None
                )
            except Exception as e:
                logger.error(f"⚠️ Не удалось обновить сообщение в Telegram: {e}")

            await callback_query.answer("Чат разблокирован для ИИ.")
        else:
            await callback_query.answer("Этот чат уже активен или не заблокирован.", show_alert=True)
