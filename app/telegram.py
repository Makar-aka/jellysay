import logging
from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

def send_telegram_message(text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    response = requests.post(url, data=data)
    if response.status_code != 200:
        logging.error(f"Ошибка отправки сообщения: {response.text}")
    return response

def send_telegram_photo(photo_url, caption):
    url = f"{TELEGRAM_API_URL}/sendPhoto"
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