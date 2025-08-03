import requests
import json
import time
import os
import sqlite3
import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger()

logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)

# Константы для защиты от спама
MESSAGE_DELAY = 3  # Задержка между сообщениями в секундах
MAX_MESSAGES_PER_MINUTE = 20  # Максимум сообщений в минуту
message_count = 0
last_message_time = datetime.now()

load_dotenv()

JELLYFIN_URL = os.getenv('JELLYFIN_URL')
JELLYFIN_API_KEY = os.getenv('JELLYFIN_API_KEY')
JELLYFIN_USER_ID = os.getenv('JELLYFIN_USER_ID')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
TELEGRAM_ADMIN_ID = int(os.getenv('TELEGRAM_ADMIN_ID'))
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', 600))
NEW_ITEMS_INTERVAL_HOURS = int(os.getenv('NEW_ITEMS_INTERVAL_HOURS', 24))

DB_FILE = os.getenv('DB_FILE', 'sent_items.db')

def init_db():
    # Создаем директорию для базы данных, если её нет
    db_dir = os.path.dirname(DB_FILE)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir)
            logger.info(f"Создана директория для базы данных: {db_dir}")
        except Exception as e:
            logger.error(f"Ошибка создания директории для базы данных: {e}", exc_info=True)
            raise

    # Проверяем, существует ли файл базы данных
    is_new_db = not os.path.exists(DB_FILE)

    # Подключаемся к базе данных (создаст файл, если его нет)
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        if is_new_db:
            logger.info(f"Создан новый файл базы данных: {DB_FILE}")

        # Проверяем существование таблицы
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sent_items'")
        table_exists = c.fetchone() is not None

        if not table_exists:
            # Создаем новую таблицу
            c.execute('''
                CREATE TABLE sent_items (
                    item_id TEXT PRIMARY KEY,
                    sent_at TIMESTAMP,
                    item_name TEXT,
                    item_type TEXT
                )
            ''')
            logger.info("Создана новая таблица sent_items")
        else:
            # Проверяем и добавляем недостающие колонки
            c.execute("PRAGMA table_info(sent_items)")
            columns = {col[1] for col in c.fetchall()}

            if 'sent_at' not in columns:
                c.execute('ALTER TABLE sent_items ADD COLUMN sent_at TIMESTAMP')
                c.execute("UPDATE sent_items SET sent_at = CURRENT_TIMESTAMP WHERE sent_at IS NULL")
                logger.info("Добавлена колонка sent_at")

            if 'item_name' not in columns:
                c.execute('ALTER TABLE sent_items ADD COLUMN item_name TEXT')
                logger.info("Добавлена колонка item_name")

            if 'item_type' not in columns:
                c.execute('ALTER TABLE sent_items ADD COLUMN item_type TEXT')
                logger.info("Добавлена колонка item_type")

        conn.commit()
        conn.close()
        logger.info("База данных успешно инициализирована")

    except Exception as e:
        logger.error(f"Ошибка инициализации базы данных: {e}", exc_info=True)
        raise

def is_sent(item_id):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT 1 FROM sent_items WHERE item_id = ?', (item_id,))
        result = c.fetchone() is not None
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Ошибка при проверке элемента в базе данных: {e}", exc_info=True)
        return False

def mark_as_sent(item_id, item_name="", item_type=""):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        c.execute('SELECT 1 FROM sent_items WHERE item_id = ?', (item_id,))
        exists = c.fetchone() is not None

        if not exists:
            try:
                c.execute(
                    'INSERT INTO sent_items (item_id, sent_at, item_name, item_type) VALUES (?, ?, ?, ?)',
                    (item_id, current_time, item_name, item_type)
                )
                conn.commit()
                logger.info(f"Добавлен элемент: {item_name} ({item_type})")
            except sqlite3.OperationalError as e:
                logger.warning(f"Ошибка при добавлении записи: {e}")
                c.execute('INSERT INTO sent_items (item_id) VALUES (?)', (item_id,))
                conn.commit()
                logger.warning("Использован старый формат записи в базу данных")

        conn.close()
    except Exception as e:
        logger.error(f"Ошибка при работе с базой данных: {e}", exc_info=True)
        raise

def get_db_records(limit=100, offset=0):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        SELECT item_id, sent_at, item_name, item_type 
        FROM sent_items 
        ORDER BY sent_at DESC 
        LIMIT ? OFFSET ?
    ''', (limit, offset))
    records = c.fetchall()
    conn.close()
    return records

def clean_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('DELETE FROM sent_items')
    conn.commit()
    conn.close()
    logger.info("База очищена")

def count_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM sent_items')
    count = c.fetchone()[0]
    conn.close()
    return count

def get_new_items():
    logger.info("Запрос новых элементов из Jellyfin")
    headers = {'X-Emby-Token': JELLYFIN_API_KEY}
    params = {
        'Limit': 20, 
        'userId': JELLYFIN_USER_ID,
        'Fields': 'DateCreated,DateLastMediaAdded,PremiereDate'
    }
    url = f'{JELLYFIN_URL}/Items/Latest'

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        items = response.json()
        logger.info(f"Получено {len(items)} элементов из Jellyfin")

        full_items = []
        for item in items:
            item_id = item['Id']
            item_url = f'{JELLYFIN_URL}/Items/{item_id}'
            item_response = requests.get(item_url, headers=headers, params={'userId': JELLYFIN_USER_ID})
            if item_response.status_code == 200:
                full_item = item_response.json()
                full_items.append(full_item)

        return full_items
    except Exception as e:
        logger.error(f"Ошибка Jellyfin API: {e}", exc_info=True)
        return []

def get_poster_url(item_id):
    return f"{JELLYFIN_URL}/Items/{item_id}/Images/Primary?maxWidth=600&tag=&quality=90&X-Emby-Token={JELLYFIN_API_KEY}"

def group_episodes(items):
    """
    Группирует новые эпизоды по сериалу и сезону.
    Возвращает список: [(series_name, season_number, [episode_numbers], episode_sample), ...]
    """
    episodes = {}
    for item in items:
        if item.get('Type') == 'Episode':
            series_name = item.get('SeriesName', item.get('Name', 'Без названия'))
            season = item.get('ParentIndexNumber', 0)
            episode = item.get('IndexNumber', 0)
            key = (series_name, season)
            if key not in episodes:
                episodes[key] = {'numbers': [], 'sample': item}
            episodes[key]['numbers'].append(episode)
    # Сортировка номеров серий
    for v in episodes.values():
        v['numbers'].sort()
    return [(k[0], k[1], v['numbers'], v['sample']) for k, v in episodes.items()]

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
    return message, name, content_type

def build_series_message(series_name, season, episode_numbers, sample_item):
    content_type = 'Сериал'
    year = sample_item.get('ProductionYear', '—')
    genres = ', '.join(sample_item.get('Genres', [])) if sample_item.get('Genres') else '—'
    date_added = sample_item.get('DateCreated', '')[:10] if sample_item.get('DateCreated') else '—'
    overview = sample_item.get('Overview', 'Нет описания')
    episodes_str = ','.join(str(num) for num in episode_numbers)
    message = (
        f"<b>{series_name}</b>\n"
        f"<b>Тип:</b> {content_type}\n"
        f"<b>Год:</b> {year}\n"
        f"<b>Жанр:</b> {genres}\n"
        f"<b>Добавлено:</b> {date_added}\n"
        f"<b>Сезон:</b> {season} <b>серия:</b> {episodes_str}\n\n"
        f"{overview}"
    )
    return message

def is_recent(item, interval_hours):
    date_str = item.get('DateCreated')
    if not date_str:
        return False
    try:
        if '.' in date_str:
            date_str = date_str.split('.')[0]
        date_str = date_str.replace('Z', '')

        dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        return delta <= timedelta(hours=interval_hours)
    except Exception as e:
        logger.error(f"Ошибка разбора даты: {e}", exc_info=True)
        return False

async def send_telegram_photo(photo_url, caption, chat_id=None):
    global message_count, last_message_time

    target_chat_id = chat_id or TELEGRAM_CHAT_ID

    now = datetime.now()
    if (now - last_message_time).total_seconds() >= 60:
        message_count = 0
        last_message_time = now

    if message_count >= MAX_MESSAGES_PER_MINUTE:
        wait_time = 60 - (now - last_message_time).total_seconds()
        if wait_time > 0:
            logger.warning(f"Достигнут лимит сообщений ({message_count}/{MAX_MESSAGES_PER_MINUTE}), ожидание {wait_time:.1f} сек")
            await asyncio.sleep(wait_time)
            message_count = 0
            last_message_time = datetime.now()

    await asyncio.sleep(MESSAGE_DELAY)

    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto'
    try:
        photo_response = requests.get(photo_url)
        if photo_response.status_code != 200:
            logger.error(f"Ошибка получения изображения: {photo_response.status_code}")
            return False

        resp = requests.post(url, data={
            'chat_id': target_chat_id,
            'caption': caption,
            'parse_mode': 'HTML'
        }, files={
            'photo': ('poster.jpg', photo_response.content)
        })

        if resp.status_code == 200:
            message_count += 1
            logger.info(f"Отправлено сообщение (#{message_count})")
            return True
        else:
            logger.error(f"Ошибка отправки в Telegram: {resp.status_code}. Ответ: {resp.text}")
            return False

    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {str(e)}", exc_info=True)
        return False

async def check_and_notify():
    items = get_new_items()
    if not items:
        return 0, 0

    processed = 0
    sent = 0

    # 1. Группируем только НЕотправленные эпизоды
    episodes_to_group = []
    for item in items:
        if item.get('Type') == 'Episode':
            season = item.get('ParentIndexNumber', 0)
            episode = item.get('IndexNumber', 0)
            series_id = item.get('SeriesId')
            # Формируем уникальный id для серии
            episode_id = f"{series_id}_S{season}E{episode}"
            if not is_sent(episode_id) and is_recent(item, NEW_ITEMS_INTERVAL_HOURS):
                episodes_to_group.append(item)

    # 2. Группируем по сериалу и сезону
    episode_groups = group_episodes(episodes_to_group)
    processed += sum(len(g[2]) for g in episode_groups)

    # 3. Отправляем уведомления по группам эпизодов
    for series_name, season, episode_numbers, sample_item in episode_groups:
        poster_url = get_poster_url(sample_item['Id'])
        message = build_series_message(series_name, season, episode_numbers, sample_item)
        if await send_telegram_photo(poster_url, message):
            for ep_num in episode_numbers:
                mark_as_sent(f"{sample_item['SeriesId']}_S{season}E{ep_num}", series_name, 'Episode')
            sent += 1

    # 4. Обрабатываем остальные типы (фильмы, новые сериалы)
    for item in items:
        if item.get('Type') != 'Episode' and not is_sent(item['Id']) and is_recent(item, NEW_ITEMS_INTERVAL_HOURS):
            poster_url = get_poster_url(item['Id'])
            message, name, item_type = build_message(item)
            if await send_telegram_photo(poster_url, message):
                mark_as_sent(item['Id'], name, item_type)
                sent += 1
            processed += 1

    if processed > 0:
        logger.info(f"Проверка завершена. Обработано: {processed}, Отправлено: {sent}")
    return processed, sent

async def db_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_ADMIN_ID or update.effective_chat.type != "private":
        return

    page = 1
    if context.args and context.args[0].isdigit():
        page = int(context.args[0])

    per_page = 10
    offset = (page - 1) * per_page

    records = get_db_records(per_page, offset)
    if not records:
        await update.message.reply_text("База данных пуста или достигнут конец списка")
        return

    total = count_db()
    total_pages = (total + per_page - 1) // per_page

    message = f"<b>Записи в базе данных (страница {page}/{total_pages}):</b>\n\n"
    for i, (item_id, sent_at, name, type_) in enumerate(records, offset + 1):
        sent_date = datetime.fromisoformat(sent_at).strftime("%Y-%m-%d %H:%M:%S")
        message += f"{i}. {name} ({type_})\n⌚️ {sent_date}\n🆔 {item_id}\n\n"

    if page < total_pages:
        message += f"\nСледующая страница: /db_list {page + 1}"

    await update.message.reply_text(message, parse_mode="HTML")

async def force_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_ADMIN_ID or update.effective_chat.type != "private":
        return
    processed, sent = await check_and_notify()
    await update.message.reply_text(
        f"Проверка завершена\n"
        f"Обработано элементов: {processed}\n"
        f"Отправлено уведомлений: {sent}"
    )

async def clean_db_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_ADMIN_ID or update.effective_chat.type != "private":
        return
    clean_db()
    await update.message.reply_text("База очищена")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_ADMIN_ID or update.effective_chat.type != "private":
        return
    count = count_db()
    await update.message.reply_text(f"В базе {count} записей")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_ADMIN_ID or update.effective_chat.type != "private":
        return
    help_text = (
        "<b>Доступные команды:</b>\n"
        "/force_check — вручную запустить проверку новинок\n"
        "/clean_db — очистить базу отправленных уведомлений\n"
        "/stats — показать количество записей в базе\n"
        "/db_list [страница] — показать список всех записей в базе\n"
        "/help — показать это сообщение\n\n"
        "Бот реагирует только на команды администратора в личных сообщениях. "
        "Уведомления о новинках отправляются в группу."
    )
    await update.message.reply_text(help_text, parse_mode="HTML")

async def main_async():
    application = (ApplicationBuilder()
                  .token(TELEGRAM_BOT_TOKEN)
                  .job_queue(None)
                  .write_timeout(30)
                  .read_timeout(30)
                  .build())

    application.add_handler(CommandHandler("force_check", force_check))
    application.add_handler(CommandHandler("clean_db", clean_db_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("db_list", db_list_cmd))
    application.add_handler(MessageHandler(filters.ALL, lambda update, context: None))

    # Фоновая задача проверки новинок
    async def check_loop():
        while True:
            try:
                await check_and_notify()
            except Exception as e:
                logger.error(f'Ошибка в цикле проверки: {e}', exc_info=True)
            await asyncio.sleep(CHECK_INTERVAL)

    asyncio.create_task(check_loop())

    logger.info("Бот запущен и готов к работе")
    await application.run_polling(allowed_updates=[])

def main():
    init_db()
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main_async())

if __name__ == '__main__':
    main()