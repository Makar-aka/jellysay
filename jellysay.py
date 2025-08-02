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
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
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

def get_new_items():
    headers = {'X-Emby-Token': JELLYFIN_API_KEY}
    
    # Сначала получаем список последних элементов
    params = {
        'Limit': 20, 
        'userId': JELLYFIN_USER_ID,
        'Fields': 'DateCreated,DateLastMediaAdded,PremiereDate'  # Запрашиваем дополнительные поля
    }
    url = f'{JELLYFIN_URL}/Items/Latest'
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        items = response.json()
        
        # Теперь получаем полную информацию для каждого элемента
        full_items = []
        for item in items:
            item_id = item['Id']
            item_url = f'{JELLYFIN_URL}/Items/{item_id}'
            item_response = requests.get(item_url, headers=headers, params={'userId': JELLYFIN_USER_ID})
            if item_response.status_code == 200:
                full_item = item_response.json()
                full_items.append(full_item)
                logging.info(f"Получена полная информация для {full_item.get('Name')}")
            
        logging.info(f"Получены новинки с Jellyfin (всего: {len(full_items)})")
        return full_items
    except Exception as e:
        logging.error(f"Jellyfin API error: {e}")
        return []

def get_poster_url(item_id):
    return f"{JELLYFIN_URL}/Items/{item_id}/Images/Primary?maxWidth=600&tag=&quality=90&X-Emby-Token={JELLYFIN_API_KEY}"

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

def is_recent(item, interval_hours):
    date_str = item.get('DateCreated')
    logging.info(f"Проверка даты для {item.get('Name')}: {json.dumps(item, ensure_ascii=False, indent=2)}")
    if not date_str:
        logging.info(f"Нет DateCreated для {item.get('Name', 'Без названия')}")
        return False
    try:
        # Обрезаем до формата 2025-07-21T11:21:02
        if '.' in date_str:
            date_str = date_str.split('.')[0]  # Убираем микросекунды
        date_str = date_str.replace('Z', '+00:00')  # Заменяем Z на таймзону
        
        dt = datetime.fromisoformat(date_str)
        now = datetime.now(timezone.utc)
        delta = now - dt
        logging.info(f"{item.get('Name', 'Без названия')}: DateCreated={dt}, now={now}, delta={delta}, threshold={timedelta(hours=interval_hours)}")
        return delta <= timedelta(hours=interval_hours)
    except Exception as e:
        logging.error(f"Ошибка разбора даты для {item.get('Name', 'Без названия')}: {e}, исходная дата: {item.get('DateCreated')}")
        return False

def send_telegram_photo(photo_url, caption, chat_id=None):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto'
    payload = {
        'chat_id': chat_id or TELEGRAM_CHAT_ID,
        'photo': photo_url,
        'caption': caption,
        'parse_mode': 'HTML'
    }
    try:
        resp = requests.post(url, data=payload)
        if resp.status_code == 200:
            logging.info(f"Отправлено сообщение в Telegram: {caption[:40]}...")
        else:
            logging.error(f"Ошибка отправки в Telegram: {resp.status_code} {resp.text}")
    except Exception as e:
        logging.error(f"Ошибка отправки в Telegram: {e}")

def check_and_notify():
    items = get_new_items()
    logging.info(f"Получено элементов из Jellyfin: {len(items)}")
    for item in items:
        item_id = item['Id']
        name = item.get('Name', 'Без названия')
        already_sent = is_sent(item_id)
        recent = is_recent(item, NEW_ITEMS_INTERVAL_HOURS)
        logging.info(f"Проверка: {name} (ID: {item_id}) | Уже отправляли: {already_sent} | Свежий: {recent}")
        if not already_sent and recent:
            logging.info(f"Попытка отправки: {name} (ID: {item_id})")
            poster_url = get_poster_url(item_id)
            message = build_message(item)
            send_telegram_photo(poster_url, message)
            mark_as_sent(item_id)
            logging.info(f"Новинка отправлена: {name}")

async def force_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_ADMIN_ID or update.effective_chat.type != "private":
        return
    check_and_notify()
    await update.message.reply_text("Проверка завершена.")

async def clean_db_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_ADMIN_ID or update.effective_chat.type != "private":
        return
    clean_db()
    await update.message.reply_text("База очищена.")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_ADMIN_ID or update.effective_chat.type != "private":
        return
    count = count_db()
    await update.message.reply_text(f"В базе {count} записей.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_ADMIN_ID or update.effective_chat.type != "private":
        return
    help_text = (
        "<b>Доступные команды:</b>\n"
        "/force_check — вручную запустить проверку новинок\n"
        "/clean_db — очистить базу отправленных уведомлений\n"
        "/stats — показать количество записей в базе\n"
        "/help — показать это сообщение\n\n"
        "Бот реагирует только на команды администратора в личных сообщениях. "
        "Уведомления о новинках отправляются в группу."
    )
    await update.message.reply_text(help_text, parse_mode="HTML")

def start_check_loop():
    while True:
        try:
            check_and_notify()
        except Exception as e:
            logging.error(f'Ошибка: {e}')
        time.sleep(CHECK_INTERVAL)

async def main_async():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).job_queue(None).build()
    app.add_handler(CommandHandler("force_check", force_check))
    app.add_handler(CommandHandler("clean_db", clean_db_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.ALL, lambda update, context: None))
    await app.run_polling()

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