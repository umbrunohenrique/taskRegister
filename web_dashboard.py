# web_dashboard.py
import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db_models import SessionLocal, User, Activity, Note, Media  # use the same db models

app = FastAPI()

# Templates folder
templates = Jinja2Templates(directory="templates")

# Optional: serve uploaded media files
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    session = SessionLocal()
    users = session.query(User).all()
    data = []

    for user in users:
        activities_data = []
        for act in user.activities:
            notes = [{"text": n.text, "type": n.note_type, "timestamp": n.timestamp} for n in act.notes]
            media = [{"filename": m.filename, "caption": m.caption, "timestamp": m.timestamp} for m in act.media]
            activities_data.append({
                "id": act.id,
                "created_at": act.created_at,
                "pending_photo": act.pending_photo,
                "notes": notes,
                "media": media
            })
        data.append({
            "telegram_id": user.telegram_id,
            "activities": activities_data
        })
    session.close()
    return templates.TemplateResponse("dashboard.html", {"request": request, "users": data})
