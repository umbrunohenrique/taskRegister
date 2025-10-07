#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "Starting Telegram bot + FastAPI dashboard..."

# Install dependencies (if not already installed in the environment)
pip install -r requirements.txt

# Run main.py (bot + dashboard)
python main.py
