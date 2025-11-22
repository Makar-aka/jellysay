import os
import json

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Загрузка шаблонов сообщений
def load_templates():
    with open("app/templates.json", "r", encoding="utf-8") as file:
        return json.load(file)