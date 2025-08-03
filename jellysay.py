import os
import sqlite3
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import requests

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger()

# Загрузка переменных окружения
load_dotenv()

JELLYFIN_URL = os.getenv('JELLYFIN_URL')
JELLYFIN_API_KEY = os.getenv('JELLYFIN_API_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
DB_FILE = os.getenv('DB_FILE', 'sent_items.db')

# Flask приложение
app = Flask(__name__)

# Инициализация базы данных
def init_db():
    db_dir = os.path.dirname(DB_FILE)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS sent_items (
            item_id TEXT PRIMARY KEY,
            sent_at TIMESTAMP,
            item_name TEXT,
            item_type TEXT
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("База данных успешно инициализирована")

# Проверка, был ли элемент уже отправлен
def is_sent(item_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT 1 FROM sent_items WHERE item_id = ?', (item_id,))
    result = c.fetchone() is not None
    conn.close()
    return result

# Пометка элемента как отправленного
def mark_as_sent(item_id, item_name="", item_type=""):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    c.execute('INSERT OR IGNORE INTO sent_items (item_id, sent_at, item_name, item_type) VALUES (?, ?, ?, ?)',
              (item_id, current_time, item_name, item_type))
    conn.commit()
    conn.close()
    logger.info(f"Элемент добавлен в базу: {item_name} ({item_type})")

# Получение URL постера
def get_poster_url(item_id):
    return f"{JELLYFIN_URL}/Items/{item_id}/Images/Primary?maxWidth=600&quality=90&X-Emby-Token={JELLYFIN_API_KEY}"

# Отправка сообщения в Telegram
def send_telegram_photo(photo_url, caption):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto'
    try:
        photo_response = requests.get(photo_url)
        if photo_response.status_code != 200:
            logger.error(f"Ошибка получения изображения: {photo_response.status_code}")
            return False

        resp = requests.post(url, data={
            'chat_id': TELEGRAM_CHAT_ID,
            'caption': caption,
            'parse_mode': 'HTML'
        }, files={
            'photo': ('poster.jpg', photo_response.content)
        })

        if resp.status_code == 200:
            logger.info("Сообщение успешно отправлено в Telegram")
            return True
        else:
            logger.error(f"Ошибка отправки в Telegram: {resp.status_code}. Ответ: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {str(e)}", exc_info=True)
        return False

# Формирование сообщения для Telegram
def build_message(item):
    series_name = item.get('SeriesName', 'Без названия')
    season = item.get('ParentIndexNumber', '—')
    episode = item.get('IndexNumber', '—')
    name = item.get('Name', 'Без названия')
    overview = item.get('Overview', 'Нет описания')
    year = item.get('ProductionYear', '—')
    genres = ', '.join(item.get('Genres', [])) if item.get('Genres') else '—'
    date_added = item.get('DateCreated', '')[:10] if item.get('DateCreated') else '—'

    message = (
        f"<b>{series_name}</b>\n"
        f"<b>Сезон:</b> {season} <b>Серия:</b> {episode}\n"
        f"<b>Название:</b> {name}\n"
        f"<b>Год:</b> {year}\n"
        f"<b>Жанр:</b> {genres}\n"
        f"<b>Добавлено:</b> {date_added}\n\n"
        f"{overview}"
    )
    return message

# Webhook для обработки событий Jellyfin
@app.route('/webhook', methods=['POST'])
def jellyfin_webhook():
    try:
        data = request.json
        logger.info(f"Получены данные от webhook: {json.dumps(data, indent=2, ensure_ascii=False)}")

        event_type = data.get('Event')
        if event_type == 'ItemAdded':
            item = data.get('Item', {})
            item_type = item.get('Type', '')
            if item_type == 'Episode':
                series_name = item.get('SeriesName', 'Без названия')
                season = item.get('ParentIndexNumber', 0)
                episode = item.get('IndexNumber', 0)
                episode_id = f"{item.get('SeriesId')}_S{season}E{episode}"

                if not is_sent(episode_id):
                    poster_url = get_poster_url(item['Id'])
                    message = build_message(item)
                    if send_telegram_photo(poster_url, message):
                        mark_as_sent(episode_id, item.get('Name', ''), 'Episode')
                        logger.info(f"Уведомление отправлено: {series_name} S{season}E{episode}")
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Ошибка обработки webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=3535)