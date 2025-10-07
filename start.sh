#!/bin/bash
set -e  # Exit on error

# Optional: activate virtual environment if you have one
# source venv/bin/activate

# Install dependencies
if [ -f "requirements.txt" ]; then
    echo "Installing dependencies..."
    pip install --no-cache-dir -r requirements.txt
fi

# Start the Python server
# Replace main:app with your app object if using FastAPI, or main.py if plain Python
if [ -n "$PORT" ]; then
    # For web frameworks like FastAPI/Flask on Railway
    uvicorn main:app --host 0.0.0.0 --port $PORT
else
    # Fallback for plain Python scripts
    python main.py
fi
