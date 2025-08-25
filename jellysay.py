import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timedelta
import os
import json
import requests
from requests.exceptions import HTTPError
from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Set up logging
log_directory = '/app/log'
log_filename = os.path.join(log_directory, 'jellysay.log')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Ensure the log directory exists
os.makedirs(log_directory, exist_ok=True)

# Create a handler for rotating log files daily
rotating_handler = TimedRotatingFileHandler(log_filename, when="midnight", interval=1, backupCount=7)
rotating_handler.setLevel(logging.INFO)
rotating_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Add the rotating handler to the logger
logging.getLogger().addHandler(rotating_handler)

# Constants
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
JELLYFIN_BASE_URL = os.environ["JELLYFIN_BASE_URL"]
JELLYFIN_API_KEY = os.environ["JELLYFIN_API_KEY"]
YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
EPISODE_PREMIERED_WITHIN_X_DAYS = int(os.environ["EPISODE_PREMIERED_WITHIN_X_DAYS"])
SEASON_ADDED_WITHIN_X_DAYS = int(os.environ["SEASON_ADDED_WITHIN_X_DAYS"])

# Path for the JSON file to store notified items
notified_items_file = '/app/data/notified_items.json'

# Function to load notified items from the JSON file
def load_notified_items():
    if os.path.exists(notified_items_file):
        with open(notified_items_file, 'r') as file:
            return json.load(file)
    return {}

# Function to save notified items to the JSON file
def save_notified_items(notified_items_to_save):
    with open(notified_items_file, 'w') as file:
        json.dump(notified_items_to_save, file)

notified_items = load_notified_items()

def get_item_details(item_id):
    headers = {'accept': 'application/json'}
    params = {'api_key': JELLYFIN_API_KEY}
    url = f"{JELLYFIN_BASE_URL}/emby/Items?Recursive=true&Fields=DateCreated,Overview&Ids={item_id}"
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()  # Check if request was successful
    return response.json()

@app.route("/webhook", methods=["POST"])
def announce_new_releases_from_jellyfin():
    try:
        payload = json.loads(request.data)
        item_type = payload.get("ItemType")
        item_name = payload.get("Name")
        release_year = payload.get("Year")
        series_name = payload.get("SeriesName")
        season_epi = payload.get("EpisodeNumber00")
        season_num = payload.get("SeasonNumber00")

        if item_type == "Movie":
            logging.info(f"Processing movie: {item_name}")
            # Add movie processing logic here
        elif item_type == "Season":
            logging.info(f"Processing season: {item_name}")
            # Add season processing logic here
        elif item_type == "Episode":
            logging.info(f"Processing episode: {item_name}")
            # Add episode processing logic here
        else:
            logging.warning(f"Item type not supported: {item_type}")
            return "Item type not supported."

        return "Processed successfully."
    except HTTPError as http_err:
        logging.error(f"HTTP error occurred: {http_err}")
        return str(http_err)
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return f"Error: {str(e)}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3535)