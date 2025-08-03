import os
import logging
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import requests

# Загрузка переменных окружения
load_dotenv()

JELLYFIN_URL = os.getenv('JELLYFIN_URL')
JELLYFIN_API_KEY = os.getenv('JELLYFIN_API_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Настройки логирования
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
LOG_FILE = os.getenv('LOG_FILE', '')

if LOG_FILE:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler()
        ]
    )
else:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

logger = logging.getLogger()

# Flask приложение
app = Flask(__name__)

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

    message = (
        f"<b>{series_name}</b>\n"
        f"<b>Сезон:</b> {season} <b>Серия:</b> {episode}\n"
        f"<b>Название:</b> {name}\n"
        f"<b>Год:</b> {year}\n\n"
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

                poster_url = get_poster_url(item['Id'])
                message = build_message(item)
                if send_telegram_photo(poster_url, message):
                    logger.info(f"Уведомление отправлено: {series_name} S{season}E{episode}")
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Ошибка обработки webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3535)