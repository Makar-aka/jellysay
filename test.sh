#!/bin/bash

# URL вашего вебхука
WEBHOOK_URL="http://127.0.0.1:3535/webhook"

# Тело запроса
JSON_PAYLOAD='{
    "ItemType": "Episode",
    "Name": "Episode",
    "Year": 2025,
    "SeriesName": "1",
    "EpisodeNumber00": 1,
    "SeasonNumber00": 1
}'

# Выполнение запроса
curl -X POST "$WEBHOOK_URL" \
-H "Content-Type: application/json" \
-d "$JSON_PAYLOAD"


