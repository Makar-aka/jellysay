FROM python:3.12-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/data

# Копирование файлов проекта
COPY jellysay.py .

# Создание volume для базы данных
VOLUME /app/data

# Установка переменных окружения напрямую
ENV DB_FILE=/app/data/sent_items.db

CMD ["python", "jellysay.py"]