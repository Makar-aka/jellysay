#!/usr/bin/env python3
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timedelta
import os
import json
from pathlib import Path
from dotenv import load_dotenv
import sys
import threading
import tempfile

# Попытка импортировать requests с понятным логом при ошибке
try:
    import requests
    from requests.exceptions import HTTPError, RequestException
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except Exception as e:
    print(f"Critical: cannot import requests: {e}", file=sys.stderr)
    raise

from flask import Flask, request, jsonify

load_dotenv()

app = Flask(__name__)

# Пути и директории
BASE_DIR = Path(os.getenv("JELLYSAY_BASE_DIR", "/app"))
LOG_DIRECTORY = BASE_DIR / "log"
DATA_DIRECTORY = BASE_DIR / "data"
LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
NOTIFIED_ITEMS_FILE = DATA_DIRECTORY / "notified_items.json"

# Логирование
log_filename = LOG_DIRECTORY / "jellyfin_telegram-notifier.log"
logger = logging.getLogger("jellysay")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
rotating_handler = TimedRotatingFileHandler(str(log_filename), when="midnight", interval=1, backupCount=7, encoding="utf-8")
rotating_handler.setFormatter(formatter)
logger.addHandler(rotating_handler)

# Проверка обязательных переменных окружения
def require_env(name):
    value = os.getenv(name)
    if not value:
        logger.error("Missing required environment variable: %s", name)
        raise SystemExit(1)
    return value

TELEGRAM_BOT_TOKEN = require_env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = require_env("TELEGRAM_CHAT_ID")
JELLYFIN_BASE_URL = require_env("JELLYFIN_BASE_URL")
JELLYFIN_API_KEY = require_env("JELLYFIN_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
try:
    EPISODE_PREMIERED_WITHIN_X_DAYS = int(os.getenv("EPISODE_PREMIERED_WITHIN_X_DAYS", "7"))
    SEASON_ADDED_WITHIN_X_DAYS = int(os.getenv("SEASON_ADDED_WITHIN_X_DAYS", "3"))
except ValueError:
    EPISODE_PREMIERED_WITHIN_X_DAYS = 7
    SEASON_ADDED_WITHIN_X_DAYS = 3

# Requests: сессия + ретраи + таймаут
DEFAULT_TIMEOUT = 10
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.3, status_forcelist=(429, 500, 502, 503, 504))
session.mount("https://", HTTPAdapter(max_retries=retries))
session.mount("http://", HTTPAdapter(max_retries=retries))

# Работа с notified_items — потокобезопасно
_notified_lock = threading.Lock()

def load_notified_items():
    with _notified_lock:
        if NOTIFIED_ITEMS_FILE.exists():
            try:
                return json.loads(NOTIFIED_ITEMS_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error("Cannot read notified items file: %s", e)
                return {}
        return {}

def save_notified_items(data):
    with _notified_lock:
        try:
            # атомарная запись
            with tempfile.NamedTemporaryFile("w", delete=False, dir=str(DATA_DIRECTORY), encoding="utf-8") as tmp:
                json.dump(data, tmp, ensure_ascii=False, indent=None)
                tmp_path = Path(tmp.name)
            tmp_path.replace(NOTIFIED_ITEMS_FILE)
        except Exception as e:
            logger.exception("Failed to save notified items: %s", e)

notified_items = load_notified_items()

def item_key(item_type, item_name, release_year):
    return f"{item_type}:{item_name}:{release_year}"

def item_already_notified(item_type, item_name, release_year):
    return item_key(item_type, item_name, release_year) in notified_items

def mark_item_as_notified(item_type, item_name, release_year, max_entries=100):
    key = item_key(item_type, item_name, release_year)
    with _notified_lock:
        notified_items[key] = True
        if len(notified_items) > max_entries:
            # удаляем самый старый ключ (порядок вставки сохраняется в Python 3.7+)
            oldest_key = next(iter(notified_items))
            notified_items.pop(oldest_key, None)
            logger.info("Key '%s' removed from notified_items", oldest_key)
        save_notified_items(notified_items)

# Утилиты
def parse_date_only(date_str):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.split("T")[0])
    except Exception:
        try:
            return datetime.strptime(date_str.split("T")[0], "%Y-%m-%d")
        except Exception:
            return None

def is_within_last_x_days(date_str, x):
    dt = parse_date_only(date_str)
    if not dt:
        return False
    return dt >= (datetime.now() - timedelta(days=x))

def is_not_within_last_x_days(date_str, x):
    dt = parse_date_only(date_str)
    if not dt:
        # если нет даты — считаем как "не в пределах"
        return True
    return dt < (datetime.now() - timedelta(days=x))

def get_item_details(item_id):
    url = f"{JELLYFIN_BASE_URL}/emby/Items?Recursive=true&Fields=DateCreated,Overview,PremiereDate&Ids={item_id}"
    try:
        resp = session.get(url, headers={"accept": "application/json"}, params={"api_key": JELLYFIN_API_KEY}, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except RequestException as e:
        logger.exception("Error fetching item details %s: %s", item_id, e)
        raise

def get_youtube_trailer_url(query):
    if not YOUTUBE_API_KEY:
        return None
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {"part": "snippet", "q": query, "type": "video", "key": YOUTUBE_API_KEY, "maxResults": 1}
    try:
        resp = session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        video_id = data.get("items", [{}])[0].get("id", {}).get("videoId")
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else None
    except RequestException as e:
        logger.warning("YouTube search failed: %s", e)
        return None

def get_poster_url(item_id):
    return f"{JELLYFIN_BASE_URL}/Items/{item_id}/Images/Primary?maxWidth=600&quality=90&X-Emby-Token={JELLYFIN_API_KEY}"

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = session.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        logger.info("Telegram message sent")
        return resp
    except RequestException as e:
        logger.error("Telegram send message failed: %s", e)
        return None

def send_telegram_photo(photo_url_or_id, caption):
    # Если аргумент похож на URL — скачиваем, иначе пробуем как item id на Jellyfin
    if not photo_url_or_id:
        return send_telegram_message(caption)
    # Try to download poster using a full URL or constructed Jellyfin Primary URL
    if not str(photo_url_or_id).startswith("http"):
        photo_url = get_poster_url(photo_url_or_id)
    else:
        photo_url = photo_url_or_id
    try:
        img_resp = session.get(photo_url, timeout=DEFAULT_TIMEOUT)
        if img_resp.status_code != 200 or not img_resp.content:
            logger.warning("Image not available %s -> fallback to text", photo_url)
            return send_telegram_message(caption)
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        files = {"photo": ("poster.jpg", img_resp.content)}
        data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "Markdown"}
        resp = session.post(url, data=data, files=files, timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 200:
            logger.info("Telegram photo sent")
        else:
            logger.error("Telegram send photo failed: %s %s", resp.status_code, resp.text)
        return resp
    except RequestException as e:
        logger.exception("Error sending photo: %s", e)
        return send_telegram_message(caption)

# Основной webhook
@app.route("/webhook", methods=["POST"])
def announce_new_releases_from_jellyfin():
    try:
        # Логируем заголовки и сырое тело для диагностики
        try:
            raw_body = request.get_data(as_text=True)
        except Exception:
            raw_body = "<cannot read body>"
        logger.info("Webhook headers: %s", dict(request.headers))
        logger.info("Webhook content-type: %s", request.content_type)
        logger.info("Webhook raw body (first 2000 chars): %s", raw_body[:2000])

        # Попытки парсинга: 1) JSON автоматически, 2) form-data, 3) явный json.loads(raw)
        payload = None
        payload = request.get_json(force=False, silent=True)
        if payload is None and request.form:
            payload = request.form.to_dict()
            logger.info("Parsed payload from form-data")
        if payload is None and raw_body:
            try:
                payload = json.loads(raw_body)
                logger.info("Parsed payload from raw JSON")
            except Exception:
                payload = None

        if not isinstance(payload, dict):
            logger.warning("Invalid payload format, cannot parse to dict")
            return jsonify({"status": "error", "message": "Invalid payload format"}), 400

        # проверяем обязательные поля
        item_type = payload.get("ItemType")
        item_name = payload.get("Name")
        release_year = payload.get("Year")
        if not item_type or not item_name or not release_year:
            logger.warning("Missing required fields: ItemType/Name/Year. Payload keys: %s", list(payload.keys()))
            return jsonify({"status": "error", "message": "Missing required fields: ItemType/Name/Year"}), 400

        # ...далее оставьте вашу существующую логику обработки (Movie/Season/Episode)...
        # для компактности — вызываем существующую логику через вспомогательную функцию process_payload
        return process_payload(payload)

            # Проверяем, что эпизод премьеровался недавно (если есть)
            if premiere and not is_within_last_x_days(premiere, EPISODE_PREMIERED_WITHIN_X_DAYS):
                logger.info("Эпизод премьеровался раньше порога, пропуск: %s", name)
                return {"status": "ok", "message": "Episode too old, skipped"}, 200     

if __name__ == "__main__":
    # Для продакшена используйте gunicorn; этот запуск — для локальной отладки
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))