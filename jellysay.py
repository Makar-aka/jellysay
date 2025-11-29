#!/usr/bin/env python3
import logging
from logging.handlers import TimedRotatingFileHandler
import os
import json
import requests
from requests.exceptions import HTTPError, RequestException
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from pathlib import Path
import threading
import tempfile
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import sys

load_dotenv()

app = Flask(__name__)

# Конфигурация путей
BASE_DIR = Path(os.getenv("JELLYSAY_BASE_DIR", "/app"))
LOG_DIRECTORY = BASE_DIR / "log"
DATA_DIRECTORY = BASE_DIR / "data"
LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
NOTIFIED_ITEMS_FILE = DATA_DIRECTORY / "notified_items.json"

# Логирование — централизованно
log_filename = LOG_DIRECTORY / "jellysay.log"
logger = logging.getLogger("jellysay")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

rotating_handler = TimedRotatingFileHandler(str(log_filename), when="midnight", interval=1, backupCount=7, encoding="utf-8")
rotating_handler.setFormatter(formatter)
logger.addHandler(rotating_handler)

# Константы / безопасный доступ к env
def require_env(name):
    val = os.getenv(name)
    if not val:
        logger.error("Не задана обязательная переменная окружения: %s", name)
        sys.exit(1)
    return val

TELEGRAM_BOT_TOKEN = require_env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = require_env("TELEGRAM_CHAT_ID")
JELLYFIN_BASE_URL = require_env("JELLYFIN_BASE_URL")
JELLYFIN_API_KEY = require_env("JELLYFIN_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
try:
    EPISODE_PREMIERED_WITHIN_X_DAYS = int(os.getenv("EPISODE_PREMIERED_WITHIN_X_DAYS", "7"))
    SEASON_ADDED_WITHIN_X_DAYS = int(os.getenv("SEASON_ADDED_WITHIN_X_DAYS", "14"))
except ValueError:
    logger.warning("Некорректные значения для дней, выставлены по умолчанию")
    EPISODE_PREMIERED_WITHIN_X_DAYS = 7
    SEASON_ADDED_WITHIN_X_DAYS = 14

# Сессия requests с Retry и таймаутами
DEFAULT_TIMEOUT = 10  # сек
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)

# Защита при работе с файлом уведомлений
_notified_lock = threading.Lock()

def load_notified_items():
    with _notified_lock:
        if NOTIFIED_ITEMS_FILE.exists():
            try:
                with NOTIFIED_ITEMS_FILE.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error("Ошибка чтения файла notified_items: %s", e)
                return {}
        return {}

def save_notified_items(data):
    # атомарная запись: tmp -> replace
    with _notified_lock:
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, dir=str(DATA_DIRECTORY), encoding="utf-8") as tmp:
                json.dump(data, tmp, ensure_ascii=False, separators=(",", ":" ))
                tmp_path = Path(tmp.name)
            tmp_path.replace(NOTIFIED_ITEMS_FILE)
        except Exception as e:
            logger.exception("Ошибка при сохранении notified_items: %s", e)

notified_items = load_notified_items()

def item_key(item_type, item_name, release_year):
    return f"{item_type}:{item_name}:{release_year}"

def item_already_notified(item_type, item_name, release_year):
    return item_key(item_type, item_name, release_year) in notified_items

def mark_item_as_notified(item_type, item_name, release_year, max_entries=100):
    key = item_key(item_type, item_name, release_year)
    with _notified_lock:
        notified_items[key] = True
        # контроль размера — удаляем старейший ключ
        if len(notified_items) > max_entries:
            oldest_key = next(iter(notified_items))
            notified_items.pop(oldest_key, None)
            logger.info("Key '%s' has been deleted from notified_items", oldest_key)
        save_notified_items(notified_items)

# Утилиты
def safe_get_json(url, params=None, headers=None, timeout=DEFAULT_TIMEOUT):
    try:
        resp = session.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except RequestException as e:
        logger.warning("Ошибка запроса %s: %s", url, e)
        raise

def escape_markdown(text: str) -> str:
    if not isinstance(text, str):
        return ""
    # Простая экранировка для Markdown (поддержка основных спецсимволов)
    for ch in ("\\", "_", "*", "[", "]", "(", ")", "`"):
        text = text.replace(ch, "\\" + ch)
    return text

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
        data = safe_get_json(base_search_url, params=params)
        video_id = data.get("items", [{}])[0].get('id', {}).get('videoId')
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else None
    except Exception:
        return None

def get_poster_url(item_id):
    return f"{JELLYFIN_BASE_URL}/Items/{item_id}/Images/Primary?maxWidth=600&quality=90&X-Emby-Token={JELLYFIN_API_KEY}"

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": escape_markdown(text),
        "parse_mode": "Markdown"
    }
    try:
        resp = session.post(url, data=payload, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        logger.info("Сообщение успешно отправлено в Telegram")
        return resp
    except RequestException as e:
        logger.error("Ошибка отправки в Telegram: %s", e)
        return None

def send_telegram_photo(photo_url, caption):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto'
    try:
        photo_resp = session.get(photo_url, timeout=DEFAULT_TIMEOUT)
        if photo_resp.status_code != 200 or not photo_resp.content:
            logger.warning("Ошибка получения изображения: %s", photo_resp.status_code)
            return send_telegram_message(caption)
        files = {'photo': ('poster.jpg', photo_resp.content)}
        data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': escape_markdown(caption), 'parse_mode': 'Markdown'}
        resp = session.post(url, data=data, files=files, timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 200:
            logger.info("Сообщение с фото успешно отправлено в Telegram")
        else:
            logger.error("Ошибка отправки фото в Telegram: %s %s", resp.status_code, resp.text)
        return resp
    except RequestException as e:
        logger.exception("Ошибка отправки фото в Telegram: %s", e)
        return send_telegram_message(caption)

def build_message(item_type, item, trailer_url=None):
    # Используем безопасную вставку и экранирование
    if item_type == "Movie":
        name = escape_markdown(item.get('Name', ''))
        year = item.get('ProductionYear', '')
        overview = escape_markdown(item.get('Overview', ''))
        msg = f"*🍿 Новый фильм!*\n\n*{name}* ({year})\n\n{overview}"
        if trailer_url:
            msg += f"\n\n[Трейлер]({trailer_url})"
        return msg
    if item_type == "Season":
        series = escape_markdown(item.get('SeriesName', item.get('Name', '')))
        season_name = escape_markdown(item.get('Name', ''))
        year = item.get('ProductionYear', '')
        overview = escape_markdown(item.get('Overview', ''))
        msg = f"*📺 Новый сезон!*\n\n*{series}* ({year})\nСезон: {season_name}\n\n{overview}"
        if trailer_url:
            msg += f"\n\n[Трейлер]({trailer_url})"
        return msg
    if item_type == "Episode":
        series = escape_markdown(item.get('SeriesName', ''))
        season = escape_markdown(str(item.get('SeasonNumber00', '')))
        episode = escape_markdown(str(item.get('EpisodeNumber00', '')))
        title = escape_markdown(item.get('Name', ''))
        year = item.get('Year', '')
        overview = escape_markdown(item.get('Overview', ''))
        msg = f"*🎬 Новая серия!*\n\nСериал: *{series}*\nСезон: {season}\nСерия: {episode}\nНазвание: {title}\nГод: {year}\n\n{overview}"
        if trailer_url:
            msg += f"\n\n[Трейлер]({trailer_url})"
        return msg
    return escape_markdown("Новинка!")

@app.route("/webhook", methods=["POST"])
def announce_new_releases_from_jellyfin():
    try:
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            logger.warning("Некорректный payload (не JSON объект): %s", payload)
            return jsonify({"status": "error", "message": "Некорректный payload"}), 400

        item_type = payload.get("ItemType")
        item_name = payload.get("Name")
        release_year = payload.get("Year")
        item_id = payload.get("ItemId")

        if not item_type or not item_name or not release_year:
            logger.warning("Некорректный payload: %s", payload)
            return jsonify({"status": "error", "message": "Некорректный payload"}), 400

        if item_already_notified(item_type, item_name, release_year):
            logger.info("Уведомление уже отправлено: %s %s %s", item_type, item_name, release_year)
            return jsonify({"status": "ok", "message": "Уже отправлено"}), 200

        search_query = f"{item_name} Trailer {release_year}"
        trailer_url = get_youtube_trailer_url(search_query)

        message = build_message(item_type, payload, trailer_url)

        if item_id:
            poster_url = get_poster_url(item_id)
            send_telegram_photo(poster_url, message)
        else:
            send_telegram_message(message)

        mark_item_as_notified(item_type, item_name, release_year)
        logger.info("Уведомление отправлено: %s %s %s", item_type, item_name, release_year)
        return jsonify({"status": "ok", "message": "Уведомление отправлено"}), 200

    except HTTPError as http_err:
        logger.error("HTTP error occurred: %s", http_err)
        return jsonify({"status": "error", "message": str(http_err)}), 500
    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "message": "Jellysay is running"}), 200

if __name__ == "__main__":
    # В продакшене запускать через WSGI (gunicorn/uvicorn), но для локального запуска:
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 3535)))