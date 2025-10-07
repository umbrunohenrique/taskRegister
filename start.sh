#!/bin/bash
set -e

pip install -r requirements.txt

# Start FastAPI web dashboard in background
uvicorn web_dashboard:app --host 0.0.0.0 --port $PORT &

# Start Telegram bot
python main.py
