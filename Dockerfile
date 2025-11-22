# Используем базовый образ Python
FROM python:3.9-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы приложения
COPY . .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Создаём директории для логов и данных
RUN mkdir -p /app/log /app/data

# Указываем порт, который будет прослушивать приложение
EXPOSE 3535

# Запускаем приложение
CMD ["python", "app/server.py"]