import requests
import json
import time
import os
import sqlite3
import asyncio
import logging
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
)

# ... (все ваши переменные и функции до get_new_items)

LIBRARIES_FILE = "scan_libraries.json"

def get_libraries():
    headers = {'X-Emby-Token': JELLYFIN_API_KEY}
    url = f"{JELLYFIN_URL}/Users/{JELLYFIN_USER_ID}/Views"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json().get("Items", [])

def load_selected_libraries():
    if os.path.exists(LIBRARIES_FILE):
        with open(LIBRARIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_selected_libraries(library_ids):
    with open(LIBRARIES_FILE, "w", encoding="utf-8") as f:
        json.dump(library_ids, f)

def get_new_items():
    headers = {'X-Emby-Token': JELLYFIN_API_KEY}
    selected_libraries = load_selected_libraries()
    all_items = []
    if not selected_libraries:
        # Если не выбрано — сканируем все библиотеки
        params = {'Limit': 20, 'userId': JELLYFIN_USER_ID}
        url = f'{JELLYFIN_URL}/Items/Latest'
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        logging.info("Получены новинки со всех библиотек Jellyfin.")
        return response.json()
    for lib_id in selected_libraries:
        params = {'Limit': 20, 'userId': JELLYFIN_USER_ID, 'ParentId': lib_id}
        url = f'{JELLYFIN_URL}/Items/Latest'
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        items = response.json()
        all_items.extend(items)
    logging.info(f"Получены новинки из выбранных библиотек Jellyfin: {selected_libraries}")
    return all_items

# --- Новый обработчик для меню библиотек ---

async def libraries_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_ADMIN_ID or update.effective_chat.type != "private":
        return
    libraries = get_libraries()
    selected = set(load_selected_libraries())
    keyboard = []
    for lib in libraries:
        checked = "✅" if lib["Id"] in selected else "❌"
        keyboard.append([
            InlineKeyboardButton(
                f"{checked} {lib['Name']}", callback_data=f"togglelib_{lib['Id']}"
            )
        ])
    keyboard.append([InlineKeyboardButton("Сохранить", callback_data="save_libs")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите библиотеки для сканирования:", reply_markup=reply_markup)

async def libraries_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_ADMIN_ID:
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    selected = set(load_selected_libraries())
    libraries = get_libraries()
    if data.startswith("togglelib_"):
        lib_id = data.split("_", 1)[1]
        if lib_id in selected:
            selected.remove(lib_id)
        else:
            selected.add(lib_id)
        # Обновить меню
        keyboard = []
        for lib in libraries:
            checked = "✅" if lib["Id"] in selected else "❌"
            keyboard.append([
                InlineKeyboardButton(
                    f"{checked} {lib['Name']}", callback_data=f"togglelib_{lib['Id']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("Сохранить", callback_data="save_libs")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выберите библиотеки для сканирования:", reply_markup=reply_markup)
        # Временно сохраняем выбор в context.user_data
        context.user_data["selected_libraries"] = list(selected)
    elif data == "save_libs":
        # Сохраняем выбор
        libs = context.user_data.get("selected_libraries", list(selected))
        save_selected_libraries(libs)
        await query.edit_message_text("Настройки библиотек сохранены.")

# --- Добавьте команду в help ---

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_ADMIN_ID or update.effective_chat.type != "private":
        return
    help_text = (
        "<b>Доступные команды:</b>\n"
        "/force_check — вручную запустить проверку новинок\n"
        "/clean_db — очистить базу отправленных уведомлений\n"
        "/stats — показать количество записей в базе\n"
        "/libraries — выбрать библиотеки для сканирования\n"
        "/help — показать это сообщение\n\n"
        "Бот реагирует только на команды администратора в личных сообщениях. "
        "Уведомления о новинках отправляются в группу."
    )
    await update.message.reply_text(help_text, parse_mode="HTML")

# --- В main_async добавьте обработчики ---

async def main_async():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("force_check", force_check))
    app.add_handler(CommandHandler("clean_db", clean_db_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("libraries", libraries_cmd))
    app.add_handler(CallbackQueryHandler(libraries_callback))
    app.add_handler(MessageHandler(filters.ALL, lambda update, context: None))
    await app.run_polling()