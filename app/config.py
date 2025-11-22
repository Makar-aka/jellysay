import json

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
JELLYFIN_BASE_URL = os.getenv("JELLYFIN_BASE_URL")  # Базовый URL сервера Jellyfin
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY")    # API-ключ для Jellyfin
NOTIFICATION_PAUSE = int(os.getenv("NOTIFICATION_PAUSE", 5))  # Пауза между отправками (в секундах)

print(f"JELLYFIN_BASE_URL: {JELLYFIN_BASE_URL}")
print(f"JELLYFIN_API_KEY: {JELLYFIN_API_KEY}")


# Загрузка шаблонов сообщений
def load_templates():
    with open("app/templates.json", "r", encoding="utf-8") as file:
        return json.load(file)