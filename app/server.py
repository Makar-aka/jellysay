import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from app.telegram import send_telegram_message, send_telegram_photo
from app.config import load_templates
from app.utils import log, get_poster_url, save_poster

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

            # Получение и отправка постера (если требуется)
            item_id = payload.get("ItemId")
            poster_url = get_poster_url(item_id) if item_id else None
            poster_path = save_poster(item_id, poster_url) if poster_url else None

            if poster_path:
                send_telegram_photo(poster_path, message)
            else:
                send_telegram_message(message)

            # Ответ на запрос
            self.send_response(200)
            self.end_headers()
            self.wfile.write("Webhook обработан.".encode("utf-8"))
        except Exception as e:
            log(f"Ошибка обработки вебхука: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Internal server error")

def run_server(host="0.0.0.0", port=3535):
    server = HTTPServer((host, port), WebhookHandler)
    log(f"Сервер запущен на {host}:{port}")
    server.serve_forever()

if __name__ == "__main__":
    # Запуск сервера
    run_server()