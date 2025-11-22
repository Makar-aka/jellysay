import logging
from app.config import JELLYFIN_BASE_URL, JELLYFIN_API_KEY

def log(message):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
    logging.info(message)

def get_poster_url(item_id):
    return f"{JELLYFIN_BASE_URL}/Items/{item_id}/Images/Primary?maxWidth=600&quality=90&X-Emby-Token={JELLYFIN_API_KEY}"