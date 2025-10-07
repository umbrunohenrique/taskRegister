# bot_pg.py
"""
Telegram Bot with PostgreSQL storage for activities, notes, and media.
"""

import os
import uuid
import datetime
import asyncio
from typing import Optional

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# SQLAlchemy imports
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

# ---------- DATABASE SETUP ----------

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("Postgres.DATABASE_URL")
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True)
    activities = relationship("Activity", back_populates="user")

class Activity(Base):
    __tablename__ = "activities"
    id = Column(String, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    pending_photo = Column(Boolean, default=False)
    user = relationship("User", back_populates="activities")
    notes = relationship("Note", back_populates="activity", cascade="all, delete-orphan")
    media = relationship("Media", back_populates="activity", cascade="all, delete-orphan")

class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    activity_id = Column(String, ForeignKey("activities.id"))
    message_id = Column(Integer, nullable=True)
    text = Column(Text)
    note_type = Column(String, default="note")
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    activity = relationship("Activity", back_populates="notes")

class Media(Base):
    __tablename__ = "media"
    id = Column(Integer, primary_key=True)
    activity_id = Column(String, ForeignKey("activities.id"))
    filename = Column(String)
    caption = Column(Text, nullable=True)
    message_id = Column(Integer, nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    activity = relationship("Activity", back_populates="media")

# create tables
Base.metadata.create_all(bind=engine)

# ---------- CONFIG ----------
TOKEN = os.environ.get("BOT_TOKEN")  # Put your token as ENV variable in Railway
WAIT_SECONDS = 60
FIXED_KEYBOARD = ReplyKeyboardMarkup([["🆕 Novo registro", "📋 Ver registros"]], resize_keyboard=True)

# ---------- DATABASE HELPERS ----------

def get_or_create_user(session, telegram_id: int):
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if not user:
        user = User(telegram_id=telegram_id)
        session.add(user)
        session.commit()
    return user

def create_activity(session, telegram_user_id: int, initial_text: Optional[str]=None, pending_photo: bool=False) -> str:
    user = get_or_create_user(session, telegram_user_id)
    aid = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
    activity = Activity(id=aid, user=user, pending_photo=pending_photo)
    session.add(activity)
    session.commit()
    if initial_text:
        note = Note(activity=activity, text=initial_text)
        session.add(note)
        session.commit()
    return aid

def add_note_to_activity(session, activity_id: str, text: str, message_id: Optional[int]=None, note_type: str="note"):
    note = Note(activity_id=activity_id, text=text, message_id=message_id, note_type=note_type)
    session.add(note)
    session.commit()

def add_media_to_activity(session, activity_id: str, filename: str, caption: Optional[str]=None, message_id: Optional[int]=None):
    media = Media(activity_id=activity_id, filename=filename, caption=caption, message_id=message_id)
    session.add(media)
    session.commit()

# ---------- BOT HANDLERS ----------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Olá! Eu vou ajudar a registrar suas atividades (texto + fotos).\n"
        "Use os botões rápidos ou envie texto/foto.\n\n"
        "Botões:\n - 🆕 Novo registro\n - 📋 Ver registros",
        reply_markup=FIXED_KEYBOARD
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    session = SessionLocal()

    # Novo registro
    if text == "🆕 Novo registro":
        await update.message.reply_text("Envie agora a descrição da nova tarefa.")
        context.user_data["await_mode"] = "registro_text"
        session.close()
        return

    # Ver registros
    if text == "📋 Ver registros":
        await update.message.reply_text("Painel web ainda não implementado.")
        session.close()
        return

    # Se está aguardando texto
    if context.user_data.get("await_mode") == "registro_text":
        context.user_data.pop("await_mode", None)
        activity_id = create_activity(session, user_id, initial_text=text, pending_photo=False)
        await update.message.reply_text(f"✅ Atividade criada: {activity_id}")
        session.close()
        return

    # Caso geral
    await update.message.reply_text("Texto não processado. Use os botões rápidos ou envie uma foto.")
    session.close()

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = SessionLocal()
    message = update.message
    filename = f"{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
    path = os.path.join("uploads", str(user_id))
    os.makedirs(path, exist_ok=True)
    file_path = os.path.join(path, filename)
    photo = await message.photo[-1].get_file()
    await photo.download_to_drive(file_path)

    # Criar nova atividade com foto
    activity_id = create_activity(session, user_id, pending_photo=False)
    add_media_to_activity(session, activity_id, filename=file_path, caption=message.caption, message_id=message.message_id)
    await update.message.reply_text(f"✅ Foto registrada na atividade: {activity_id}")
    session.close()

async def list_activities_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = SessionLocal()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user or not user.activities:
        await update.message.reply_text("Nenhuma atividade encontrada.")
        session.close()
        return

    text_lines = ["Atividades mais recentes:"]
    for act in sorted(user.activities, key=lambda a: a.created_at, reverse=True)[:20]:
        n_notes = len(act.notes)
        n_media = len(act.media)
        text_lines.append(f"- {act.id} | notas: {n_notes} fotos: {n_media} | pending_photo:{act.pending_photo}")

    await update.message.reply_text("\n".join(text_lines))
    session.close()

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Envie textos ou fotos. Responda mensagens para anexar a atividades. Use /list para ver resumo.")

# ---------- BOOT ----------

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("list", list_activities_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    print("Bot iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()
