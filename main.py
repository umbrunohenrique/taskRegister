import os
import uuid
import datetime
import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ----------------- Environment -----------------
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set!")

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable not set!")

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

WAIT_SECONDS = 60
FIXED_KEYBOARD = ReplyKeyboardMarkup([["🆕 Novo registro", "📋 Ver registros"]], resize_keyboard=True)

# ----------------- Database -----------------
Base = declarative_base()
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True)
    activities = relationship("Activity", back_populates="user")

class Activity(Base):
    __tablename__ = "activities"
    id = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    pending_photo = Column(Boolean, default=False)
    notes = relationship("Note", back_populates="activity")
    media = relationship("Media", back_populates="activity")
    user = relationship("User", back_populates="activities")

class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    activity_id = Column(String, ForeignKey("activities.id"))
    text = Column(String)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    note_type = Column(String, default="note")
    activity = relationship("Activity", back_populates="notes")

class Media(Base):
    __tablename__ = "media"
    id = Column(Integer, primary_key=True)
    activity_id = Column(String, ForeignKey("activities.id"))
    filename = Column(String)
    caption = Column(String)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    activity = relationship("Activity", back_populates="media")

Base.metadata.create_all(engine)

# ----------------- Helper Functions -----------------
def get_or_create_user(session, telegram_id: int):
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if not user:
        user = User(telegram_id=telegram_id)
        session.add(user)
        session.commit()
    return user

def create_activity(session, user_id, pending_photo=False):
    aid = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
    activity = Activity(id=aid, user_id=user_id, pending_photo=pending_photo)
    session.add(activity)
    session.commit()
    return activity

# ----------------- Telegram Bot -----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Olá! Eu vou ajudar a registrar suas atividades (texto + fotos).",
        reply_markup=FIXED_KEYBOARD
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    user = get_or_create_user(session, update.effective_user.id)
    text = update.message.text.strip()

    if text == "🆕 Novo registro":
        await update.message.reply_text("Ok, envie a descrição da nova atividade.")
        context.user_data["await_mode"] = "registro_text"
        session.close()
        return

    if text == "📋 Ver registros":
        await update.message.reply_text("Abra o painel web para ver registros.")
        session.close()
        return

    if context.user_data.get("await_mode") == "registro_text":
        context.user_data.pop("await_mode", None)
        activity = create_activity(session, user.id, pending_photo=True)
        note = Note(activity_id=activity.id, text=text)
        session.add(note)
        session.commit()
        await update.message.reply_text(f"✅ Atividade criada com ID {activity.id}. Aguarde foto ou envie texto adicional.")
    session.close()

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    user = get_or_create_user(session, update.effective_user.id)
    message = update.message
    file = await message.photo[-1].get_file()
    filename = f"{uuid.uuid4().hex}.jpg"
    filepath = os.path.join(UPLOAD_DIR, filename)
    await file.download_to_drive(custom_path=filepath)

    # Use last pending activity or create new
    pending = session.query(Activity).filter_by(user_id=user.id, pending_photo=True).order_by(Activity.created_at.desc()).first()
    if not pending:
        pending = create_activity(session, user.id)
    media = Media(activity_id=pending.id, filename=filename, caption=message.caption or "")
    pending.pending_photo = False
    session.add(media)
    session.commit()
    await update.message.reply_text(f"🖼️ Foto anexada à atividade {pending.id}.")
    session.close()

def main_bot():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    print("Bot iniciado...")
    app.run_polling()

# ----------------- Web Dashboard -----------------
web_app = FastAPI()
templates = Jinja2Templates(directory="templates")
web_app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

@web_app.get("/", response_class=HTMLResponse)
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
        data.append({"telegram_id": user.telegram_id, "activities": activities_data})
    session.close()
    return templates.TemplateResponse("dashboard.html", {"request": request, "users": data})

# ----------------- Start Both -----------------
if __name__ == "__main__":
    import threading
    # Run FastAPI in a thread
    threading.Thread(target=lambda: uvicorn.run(web_app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))).start()
    # Run bot in main thread
    main_bot()
