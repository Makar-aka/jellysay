from app.config import JELLYFIN_BASE_URL, JELLYFIN_API_KEY
import requests
import os

def log(message):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
    logging.info(message)

def get_poster_url(item_id):
    return f"{JELLYFIN_BASE_URL}/Items/{item_id}/Images/Primary?maxWidth=600&quality=90&X-Emby-Token={JELLYFIN_API_KEY}"

POSTERS_DIR = "app/posters"

def save_poster(item_id, poster_url):
    """Сохранение постера в локальную папку."""
    os.makedirs(POSTERS_DIR, exist_ok=True)
    poster_path = os.path.join(POSTERS_DIR, f"{item_id}.jpg")
    try:
        response = requests.get(poster_url)
        if response.status_code == 200:
            with open(poster_path, "wb") as file:
                file.write(response.content)
            return poster_path
        else:
            log(f"Ошибка загрузки постера: {response.status_code}")
            return None
    except Exception as e:
        log(f"Ошибка сохранения постера: {e}")
        return None