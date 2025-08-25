import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timedelta
import os
import json
import requests
from requests.exceptions import HTTPError
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Логирование
log_directory = '/app/log'
log_filename = os.path.join(log_directory, 'jellysay.log')
os.makedirs(log_directory, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
rotating_handler = TimedRotatingFileHandler(log_filename, when="midnight", interval=1, backupCount=7)
rotating_handler.setLevel(logging.INFO)
rotating_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(rotating_handler)

# Константы
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
JELLYFIN_BASE_URL = os.environ["JELLYFIN_BASE_URL"]
JELLYFIN_API_KEY = os.environ["JELLYFIN_API_KEY"]
YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
EPISODE_PREMIERED_WITHIN_X_DAYS = int(os.environ["EPISODE_PREMIERED_WITHIN_X_DAYS"])
SEASON_ADDED_WITHIN_X_DAYS = int(os.environ["SEASON_ADDED_WITHIN_X_DAYS"])

notified_items_file = '/app/data/notified_items.json'
os.makedirs('/app/data', exist_ok=True)

def load_notified_items():
    if os.path.exists(notified_items_file):
        with open(notified_items_file, 'r') as file:
            return json.load(file)
    return {}

def save_notified_items(notified_items_to_save):
    with open(notified_items_file, 'w') as file:
        json.dump(notified_items_to_save, file)

notified_items = load_notified_items()

def item_already_notified(item_type, item_name, release_year):
    key = f"{item_type}:{item_name}:{release_year}"
    return key in notified_items

def mark_item_as_notified(item_type, item_name, release_year, max_entries=100):
    key = f"{item_type}:{item_name}:{release_year}"
    notified_items[key] = True
    if len(notified_items) > max_entries:
        oldest_key = list(notified_items.keys())[0]
        del notified_items[oldest_key]
        logging.info(f"Key '{oldest_key}' has been deleted from notified_items")
    save_notified_items(notified_items)

def get_item_details(item_id):
    headers = {'accept': 'application/json'}
    params = {'api_key': JELLYFIN_API_KEY}
    url = f"{JELLYFIN_BASE_URL}/emby/Items?Recursive=true&Fields=DateCreated,Overview,PremiereDate&Ids={item_id}"
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()

def get_youtube_trailer_url(query):
    if not YOUTUBE_API_KEY:
        return None
    base_search_url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        'part': 'snippet',
        'q': query,
        'type': 'video',
        'key': YOUTUBE_API_KEY,
        'maxResults': 1
    }
    try:
        response = requests.get(base_search_url, params=params)
        response.raise_for_status()
        response_data = response.json()
        video_id = response_data.get("items", [{}])[0].get('id', {}).get('videoId')
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else None
    except Exception as e:
        logging.warning(f"Ошибка поиска трейлера на YouTube: {e}")
        return None

def send_telegram_photo(photo_url, caption):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto'
    try:
        photo_response = requests.get(photo_url)
        if photo_response.status_code != 200:
            logging.warning(f"Ошибка получения изображения: {photo_response.status_code}")
            return send_telegram_message(caption)
        resp = requests.post(url, data={
            'chat_id': TELEGRAM_CHAT_ID,
            'caption': caption,
            'parse_mode': 'Markdown'
        }, files={
            'photo': ('poster.jpg', photo_response.content)
        })
        if resp.status_code == 200:
            logging.info("Сообщение с фото успешно отправлено в Telegram")
        else:
            logging.error(f"Ошибка отправки фото в Telegram: {resp.status_code}. Ответ: {resp.text}")
        return resp
    except Exception as e:
        logging.error(f"Ошибка отправки фото в Telegram: {str(e)}")
        return send_telegram_message(caption)

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    resp = requests.post(url, data=data)
    if resp.status_code == 200:
        logging.info("Сообщение успешно отправлено в Telegram")
    else:
        logging.error(f"Ошибка отправки в Telegram: {resp.status_code} {resp.text}")
    return resp

def build_message(item_type, item, trailer_url=None):
    if item_type == "Movie":
        message = f"*🍿 Новый фильм!*\n\n*{item.get('Name')}* ({item.get('ProductionYear')})\n\n{item.get('Overview', '')}"
        if trailer_url:
            message += f"\n\n[Трейлер]({trailer_url})"
        return message
    elif item_type == "Season":
        message = f"*📺 Новый сезон!*\n\n*{item.get('SeriesName', item.get('Name'))}* ({item.get('ProductionYear', '')})\nСезон: {item.get('Name')}\n\n{item.get('Overview', '')}"
        if trailer_url:
            message += f"\n\n[Трейлер]({trailer_url})"
        return message
    elif item_type == "Episode":
        message = f"*🎬 Новая серия!*\n\nСериал: *{item.get('SeriesName')}*\nСезон: {item.get('SeasonNumber00')}\nСерия: {item.get('EpisodeNumber00')}\nНазвание: {item.get('Name')}\nГод: {item.get('Year')}\n\n{item.get('Overview', '')}"
        if trailer_url:
            message += f"\n\n[Трейлер]({trailer_url})"
        return message
    return "Новинка!"

def get_poster_url(item_id):
    return f"{JELLYFIN_BASE_URL}/Items/{item_id}/Images/Primary?maxWidth=600&quality=90&X-Emby-Token={JELLYFIN_API_KEY}"

@app.route("/webhook", methods=["POST"])
def announce_new_releases_from_jellyfin():
    try:
        payload = request.get_json(force=True)
        item_type = payload.get("ItemType")
        item_name = payload.get("Name")
        release_year = payload.get("Year")
        series_name = payload.get("SeriesName")
        season_epi = payload.get("EpisodeNumber00")
        season_num = payload.get("SeasonNumber00")
        item_id = payload.get("ItemId")

        if not item_type or not item_name or not release_year:
            logging.warning(f"Некорректный payload: {payload}")
            return jsonify({"status": "error", "message": "Некорректный payload"}), 400

        if item_already_notified(item_type, item_name, release_year):
            logging.info(f"Уведомление уже отправлено: {item_type} {item_name} {release_year}")
            return jsonify({"status": "ok", "message": "Уже отправлено"}), 200

        # Поиск трейлера
        search_query = f"{item_name} Trailer {release_year}"
        trailer_url = get_youtube_trailer_url(search_query)

        # Формирование сообщения
        message = build_message(item_type, payload, trailer_url)

        # Отправка постера (если есть) или просто текста
        if item_id:
            poster_url = get_poster_url(item_id)
            send_telegram_photo(poster_url, message)
        else:
            send_telegram_message(message)

        mark_item_as_notified(item_type, item_name, release_year)
        logging.info(f"Уведомление отправлено: {item_type} {item_name} {release_year}")
        return jsonify({"status": "ok", "message": "Уведомление отправлено"}), 200

    except HTTPError as http_err:
        logging.error(f"HTTP error occurred: {http_err}")
        return jsonify({"status": "error", "message": str(http_err)}), 500
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "message": "Jellysay is running"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3535)