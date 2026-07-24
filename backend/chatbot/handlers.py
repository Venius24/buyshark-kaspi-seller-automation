import logging
import io
import os
from typing import Dict, Any
from backend.chatbot import config, bot_instance
from backend.services.print_batcher_service import (
    process_zip_or_pdfs,
    get_batch_file_path,
    reset_printed_history,
    load_printed_history
)

logger = logging.getLogger(__name__)

# Хранилище загруженных файлов перед выбором формата: chat_id -> file_meta
PENDING_FILES: Dict[int, Dict[str, Any]] = {}


def register_handlers():
    """Регистрирует хэндлеры команд, документов, инлайн-кнопок и настроек для Telegram бота."""
    dp = bot_instance.dp
    if not dp or not bot_instance.HAS_AIOGRAM:
        return

    from aiogram import types, F
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

    def get_layout_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="4 на A4", callback_data="pb_fmt:a4_4"),
                InlineKeyboardButton(text="8 на A4", callback_data="pb_fmt:a4_8"),
                InlineKeyboardButton(text="9 на A4", callback_data="pb_fmt:a4_9"),
                InlineKeyboardButton(text="1 на A4", callback_data="pb_fmt:a4_1")
            ],
            [
                InlineKeyboardButton(text="🏷️ Термо 75x120", callback_data="pb_fmt:thermal_75_120"),
                InlineKeyboardButton(text="🏷️ Термо 100x150", callback_data="pb_fmt:thermal_100_150")
            ],
            [
                InlineKeyboardButton(text="📑 Просто объединить", callback_data="pb_fmt:just_merge"),
                InlineKeyboardButton(text="⚡ Авто-категории", callback_data="pb_fmt:auto")
            ]
        ])

    def get_settings_view() -> tuple[str, InlineKeyboardMarkup]:
        stg = config.load_print_settings()

        exp_mode = stg.get("express_mode", "separate")
        sort_p = stg.get("sort_preset", "filename")
        dedup = stg.get("deduplicate_history", True)
        rot = stg.get("auto_rotate", True)
        def_fmt = stg.get("output_layout", "auto")

        exp_title = {
            "separate": "⚡ Выделять в VIP-батчи",
            "exclude": "🚫 Исключать из печати",
            "ignore": "📦 Печатать всё вместе"
        }.get(exp_mode, exp_mode)

        sort_title = {
            "filename": "🔤 По имени файла",
            "order_id": "🔢 По Order ID",
            "original": "📄 В порядке архива"
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

        text = (
            f"⚙️ **Глобальные настройки предприятия (Print-Batcher Engine):**\n\n"
            f"⚡ **Каспи Экспресс:** `{exp_title}`\n"
            f"📋 **Сортировка:** `{sort_title}`\n"
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
            ]
        ])
        return text, kb

    @dp.message(F.text == "/start")
    async def cmd_start(message: types.Message):
        history_count = len(load_printed_history())
        stg = config.load_print_settings()
        await message.reply(
            f"🖨️ **Print-Batcher Engine — Автосборщик печати**\n\n"
            f"Отправьте мне любой **ZIP-архив** или **PDF-файл** с накладными Kaspi / 1С.\n"
            f"Я автоматически:\n"
            f"• Отфильтрую ранее напечатанные дубликаты за сегодня (уже напечатано: `{history_count}` шт)\n"
            f"• Обрежу неиспользуемые белые поля A4\n"
            f"• Подгоню накладные во весь рост стикера (75x120, 100x150, 4 на A4 и т.д.)\n\n"
            f"⚙️ **Текущий формат по умолчанию:** `{stg.get('output_layout', 'auto')}`\n\n"
            f"📋 **Команды бота:**\n"
            f"• `/settings` — Глобальные настройки печати предприятия\n"
            f"• `/reset` — Сбросить историю печати за сегодня\n"
            f"• `/stats` — Статистика напечатанных заказов за сегодня"
        )

    @dp.message(F.text == "/settings")
    async def cmd_settings(message: types.Message):
        text, kb = get_settings_view()
        await message.reply(text, reply_markup=kb)

    @dp.message(F.text == "/reset")
    async def cmd_reset(message: types.Message):
        reset_printed_history()
        await message.reply("♻️ **История напечатанных заказов за сегодня успешно сброшена!**")

    @dp.message(F.text == "/stats")
    async def cmd_stats(message: types.Message):
        count = len(load_printed_history())
        await message.reply(f"🏷️ **Напечатано заказов за сегодня:** `{count}` шт")

    @dp.callback_query(F.data.startswith("stg_"))
    async def process_settings_toggle(callback_query: types.CallbackQuery):
        data = callback_query.data
        stg = config.load_print_settings()

        if data.startswith("stg_exp:"):
            stg["express_mode"] = data.split(":")[1]
        elif data.startswith("stg_srt:"):
            stg["sort_preset"] = data.split(":")[1]
        elif data.startswith("stg_fmt:"):
            stg["output_layout"] = data.split(":")[1]
        elif data.startswith("stg_toggle:"):
            key = data.split(":")[1]
            stg[key] = not stg.get(key, True)
        elif data == "stg_reset_hist":
            reset_printed_history()
            await callback_query.answer("♻️ История печати за сегодня сброшена!", show_alert=True)
            text, kb = get_settings_view()
            try:
                await callback_query.message.edit_text(text, reply_markup=kb)
            except Exception:
                pass
            return

        config.save_print_settings(stg)
        await callback_query.answer("⚙️ Настройка сохранена!")

        text, kb = get_settings_view()
        try:
            await callback_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass

    @dp.message(F.document)
    async def handle_document(message: types.Message):
        doc = message.document
        if not doc or not doc.file_name:
            return

        filename = doc.file_name.lower()
        if not (filename.endswith(".zip") or filename.endswith(".pdf")):
            await message.reply("⚠️ Поддерживаются только `.zip` архивы и `.pdf` файлы.")
            return

        chat_id = message.chat.id
        PENDING_FILES[chat_id] = {
            "file_id": doc.file_id,
            "file_name": doc.file_name,
            "file_size": doc.file_size
        }

        size_mb = (doc.file_size or 0) / (1024 * 1024)
        stg = config.load_print_settings()
        def_fmt = stg.get("output_layout", "auto")

        await message.reply(
            f"📦 **Получен файл:** `{doc.file_name}` ({size_mb:.2f} МБ)\n"
            f"⚙️ **Текущий формат по умолчанию:** `{def_fmt}`\n\n"
            f"Выберите формат обработки или нажмите кнопку ниже:",
            reply_markup=get_layout_keyboard()
        )

    @dp.callback_query(F.data.startswith("pb_fmt:") | F.data.startswith("pb_force:"))
    async def process_print_layout(callback_query: types.CallbackQuery):
        chat_id = callback_query.message.chat.id
        pending = PENDING_FILES.get(chat_id)

        if not pending:
            await callback_query.answer("⚠️ Файл не найден или истек срок действия. Отправьте архив заново.", show_alert=True)
            return

        data_parts = callback_query.data.split(":")
        action_type = data_parts[0]  # 'pb_fmt' or 'pb_force'
        layout_mode = data_parts[1]
        force_reprint = (action_type == "pb_force")

        await callback_query.answer()
        try:
            await callback_query.message.edit_text("⏳ **Загрузка архива, обрезка белых полей и подгонка MediaBox...**")
        except Exception:
            pass

        try:
            bot = bot_instance.bot
            file_info = await bot.get_file(pending["file_id"])
            file_stream = io.BytesIO()
            await bot.download_file(file_info.file_path, destination=file_stream)
            file_bytes = file_stream.getvalue()

            stg = config.load_print_settings()

            manifest = process_zip_or_pdfs(
                file_bytes=file_bytes,
                original_filename=pending["file_name"],
                sort_preset=stg.get("sort_preset", "filename"),
                auto_rotate=stg.get("auto_rotate", True),
                express_mode=stg.get("express_mode", "separate"),
                deduplicate_history=stg.get("deduplicate_history", True),
                force_reprint=force_reprint,
                output_layout=layout_mode
            )

            total_files = manifest["total_files"]
            total_pages = manifest["total_pages"]
            dedup_pages = manifest["deduplicated_pages"]
            new_pages = manifest["new_pages"]
            all_dups = manifest["all_duplicates"]
            batches = manifest["batches"]

            if all_dups:
                force_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🖨️ Напечатать заново принудительно", callback_data=f"pb_force:{layout_mode}")]
                ])
                await callback_query.message.edit_text(
                    f"⚠️ **Все {total_pages} заказов из этого архива уже были напечатаны ранее!**\n\n"
                    f"Система автоматически отфильтровала старые дубликаты. Нажмите кнопку ниже, если хотите принудительно перепечатать весь этот архив:",
                    reply_markup=force_kb
                )
                return

            await callback_query.message.edit_text(
                f"✅ **Печатный пакет успешно скомпонован!**\n"
                f"📁 **Файлов в архиве:** `{total_files}` шт | **Всего листов:** `{total_pages}`\n"
                f"🛑 **Исключено дубликатов:** `{dedup_pages}` страниц\n"
                f"✨ **Новых листов к печати:** `{new_pages}` страниц\n"
                f"📤 Отправка файлов в чат..."
            )

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
            await callback_query.message.answer(f"❌ **Ошибка при обработке файла:** `{str(e)}`")

    @dp.callback_query(F.data.startswith("unblock_"))
    async def process_unblock_callback(callback_query: types.CallbackQuery):
        group_id = callback_query.data.split("_")[1]
        if config.PROCESSED_CHATS.get(group_id) == "HUMAN_OVERRIDE":
            config.PROCESSED_CHATS.pop(group_id, None)
            try:
                await callback_query.message.edit_text(
                    text=(callback_query.message.text or "") + "\n\n✅ **Чат разблокирован.**",
                    reply_markup=None
                )
            except Exception:
                pass
            await callback_query.answer("Чат разблокирован.")
