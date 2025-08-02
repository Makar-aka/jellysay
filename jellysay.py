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

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

load_dotenv()

JELLYFIN_URL = os.getenv('JELLYFIN_URL')
JELLYFIN_API_KEY = os.getenv('JELLYFIN_API_KEY')
JELLYFIN_USER_ID = os.getenv('JELLYFIN_USER_ID')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
TELEGRAM_ADMIN_ID = int(os.getenv('TELEGRAM_ADMIN_ID'))
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', 600))
NEW_ITEMS_INTERVAL_HOURS = int(os.getenv('NEW_ITEMS_INTERVAL_HOURS', 24))

DB_FILE = 'sent_items.db'
LIBRARIES_FILE = "scan_libraries.json"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS sent_items (
            item_id TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()
    logging.info("Инициализирована база данных.")

def is_sent(item_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT 1 FROM sent_items WHERE item_id = ?', (item_id,))
    result = c.fetchone()
    conn.close()
    return result is not None

def mark_as_sent(item_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO sent_items (item_id) VALUES (?)', (item_id,))
    conn.commit()
    conn.close()
    logging.info(f"Добавлен ID в базу: {item_id}")

def clean_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('DELETE FROM sent_items')
    conn.commit()
    conn.close()
    logging.info("База очищена.")

def count_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM sent_items')
    count = c.fetchone()[0]
    conn.close()
    logging.info(f"Количество записей в базе: {count}")
    return count

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
        context.user_data["selected_libraries"] = list(selected)
    elif data == "save_libs":
        libs = context.user_data.get("selected_libraries", list(selected))
        save_selected_libraries(libs)
        await query.edit_message_text("Настройки библиотек сохранены.")

def build_message(item):
    item_type = item.get('Type', 'Unknown')
    if item_type == 'Episode':
        content_type = 'Сериал (серия)'
    elif item_type == 'Movie':
        content_type = 'Фильм'
    elif item_type == 'Series':
        content_type = 'Сериал'
    else:
        content_type = item_type

    name = item.get('Name', 'Без названия')
    overview = item.get('Overview', 'Нет описания')
    year = item.get('ProductionYear', '—')
    genres = ', '.join(item.get('Genres', [])) if item.get('Genres') else '—'
    date_added = item.get('DateCreated', '')[:10] if item.get('DateCreated') else '—'

    if item_type == 'Episode':
        series_name = item.get('SeriesName', '')
        season = item.get('ParentIndexNumber', '')
        episode = item.get('IndexNumber', '')
        name = f"{series_name} — S{season:02}E{episode:02} {name}"

    message = (
        f"<b>{name}</b>\n"
        f"<b>Тип:</b> {content_type}\n"
        f"<b>Год:</b> {year}\n"
        f"<b>Жанр:</b> {genres}\n"
        f"<b>Добавлено:</b> {date_added}\n\n"
        f"{overview}"
    )
    return message

# Остальные функции (is_recent, send_telegram_photo, check_and_notify, force_check, clean_db_cmd, stats_cmd, help_cmd, start_check_loop) без изменений

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

def start_check_loop():
    while True:
        try:
            check_and_notify()
        except Exception as e:
            logging.error(f'Ошибка: {e}')
        time.sleep(CHECK_INTERVAL)

def main():
    init_db()
    Thread(target=start_check_loop, daemon=True).start()
    import nest_asyncio
    nest_asyncio.apply()
    loop = asyncio.get_event_loop()
    loop.create_task(main_async())
    loop.run_forever()

if __name__ == '__main__':
    main()