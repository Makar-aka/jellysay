import sqlite3
import os

DB_PATH = "app/data/webhooks.db"

def init_db():
    """Инициализация базы данных."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            item_type TEXT NOT NULL,
            name TEXT NOT NULL,
            year INTEGER,
            overview TEXT,
            series_name TEXT,
            season_number INTEGER,
            episode_number INTEGER,
            poster_path TEXT,
            sent INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def insert_webhook(data):
    """Добавление вебхука в базу."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO webhooks (item_id, item_type, name, year, overview, series_name, season_number, episode_number, poster_path, sent)
        VALUES (:item_id, :item_type, :name, :year, :overview, :series_name, :season_number, :episode_number, :poster_path, :sent)
    """, data)
    conn.commit()
    conn.close()

def get_unsent_webhooks(limit=10):
    """Получение неотправленных вебхуков."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM webhooks WHERE sent = 0 LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def mark_webhook_as_sent(webhook_id):
    """Пометка вебхука как отправленного."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE webhooks SET sent = 1 WHERE id = ?
    """, (webhook_id,))
    conn.commit()
    conn.close()