import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from app.database import get_unsent_webhooks, mark_webhook_as_sent, insert_webhook
from app.telegram import send_telegram_message, send_telegram_photo
from app.config import load_templates, NOTIFICATION_PAUSE
from app.utils import log
from app.utils import get_poster_url, save_poster
    

# Загрузка шаблонов сообщений
templates = load_templates()

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Чтение длины данных из заголовка
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

        try:
            # Парсинг JSON
            payload = json.loads(post_data)
            log(f"Получен вебхук: {payload}")

            # Определение типа элемента
            item_type = payload.get("ItemType")
            if item_type not in templates:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Unknown item type")
                return

            # Формирование сообщения
            message = templates[item_type].format(**payload)

            # Отправка сообщения в Telegram
            send_telegram_message(message)

            # Получение и отправка постера (если требуется)
            item_id = payload.get("ItemId")
            poster_url = get_poster_url(item_id) if item_id else None
            poster_path = save_poster(item_id, poster_url) if poster_url else None

            # Запись вебхука в базу
            insert_webhook({
                "item_id": item_id,
                "item_type": item_type,
                "name": payload.get("Name"),
                "year": payload.get("Year"),
                "overview": payload.get("Overview"),
                "series_name": payload.get("SeriesName"),
                "season_number": payload.get("SeasonNumber00"),
                "episode_number": payload.get("EpisodeNumber00"),
                "poster_path": poster_path,
                "sent": 0
            })

            # Ответ на запрос
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Webhook записан в базу.")
        except Exception as e:
            log(f"Ошибка обработки вебхука: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Internal server error")

def run_server(host="0.0.0.0", port=3535):
    server = HTTPServer((host, port), WebhookHandler)
    log(f"Сервер запущен на {host}:{port}")
    server.serve_forever()

def send_notifications():
    """Отправка уведомлений порциями."""
    while True:
        webhooks = get_unsent_webhooks(limit=10)
        if not webhooks:
            log("Нет новых уведомлений для отправки.")
            time.sleep(NOTIFICATION_PAUSE)
            continue

        for webhook in webhooks:
            webhook_id, item_id, item_type, name, year, overview, series_name, season_number, episode_number, poster_path, sent = webhook
            message = templates[item_type].format(
                Name=name,
                Year=year,
                Overview=overview,
                SeriesName=series_name,
                SeasonNumber00=season_number,
                EpisodeNumber00=episode_number
            )
            if poster_path:
                send_telegram_photo(poster_path, message)
            else:
                send_telegram_message(message)
            mark_webhook_as_sent(webhook_id)
            time.sleep(NOTIFICATION_PAUSE)

if __name__ == "__main__":
    run_server()