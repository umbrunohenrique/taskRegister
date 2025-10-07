# bot_rewrite.py
"""
Bot Telegram para registrar atividades (texto + fotos), com:
- Agrupamento de múltiplos textos/fotos por atividade
- Espera por foto por até 60 segundos
- Respostas a mensagens vinculam conteúdo à atividade da mensagem respondida
- Imagem com legenda -> legenda vira caption (diferente de nota)
Armazenamento local em: registros/<user_id>/activities/<activity_id>/
"""

import os
import json
import uuid
import datetime
import asyncio
from typing import Optional, Dict, Any

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# ---------- CONFIG ----------
BASE_DIR = os.path.join(os.path.dirname(__file__), "registros")
os.makedirs(BASE_DIR, exist_ok=True)

TOKEN = "8161964822:AAHdeSDv5YL5pL09-jRok6SMy5hOEOK4-Jo"  # <--- substituir
WAIT_SECONDS = 60  # tempo máximo de espera por foto (segundos)

# Teclado fixo (opcional)
FIXED_KEYBOARD = ReplyKeyboardMarkup([["🆕 Novo registro", "📋 Ver registros"]], resize_keyboard=True)

# ---------- UTILITÁRIOS DE ARMAZENAMENTO ----------

def user_dir(user_id: int) -> str:
    p = os.path.join(BASE_DIR, str(user_id))
    os.makedirs(p, exist_ok=True)
    os.makedirs(os.path.join(p, "activities"), exist_ok=True)
    return p

def mappings_path(user_id: int) -> str:
    return os.path.join(user_dir(user_id), "mappings.json")

def load_mappings(user_id: int) -> dict:
    path = mappings_path(user_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_mappings(user_id: int, mappings: dict):
    with open(mappings_path(user_id), "w", encoding="utf-8") as f:
        json.dump(mappings, f, ensure_ascii=False, indent=2)

def activity_path(user_id: int, activity_id: str) -> str:
    return os.path.join(user_dir(user_id), "activities", activity_id)

def create_activity(user_id: int, initial_text: Optional[str]=None, pending_photo: bool=False) -> str:
    aid = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
    p = activity_path(user_id, aid)
    os.makedirs(p, exist_ok=True)
    meta = {
        "id": aid,
        "created_at": datetime.datetime.utcnow().isoformat(),
        "pending_photo": bool(pending_photo),
        "notes": [],   # each: {message_id, text, timestamp, type: "note"|"caption"}
        "media": []    # each: {filename, caption, timestamp, message_id}
    }
    if initial_text:
        meta["notes"].append({
            "message_id": None,
            "text": initial_text,
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "type": "note"
        })
    save_activity_meta(user_id, aid, meta)
    return aid

def save_activity_meta(user_id: int, activity_id: str, meta: dict):
    p = activity_path(user_id, activity_id)
    with open(os.path.join(p, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

def load_activity_meta(user_id: int, activity_id: str) -> dict:
    p = activity_path(user_id, activity_id)
    meta_file = os.path.join(p, "metadata.json")
    if os.path.exists(meta_file):
        with open(meta_file, "r", encoding="utf-8") as f:
            return json.load(f)
    raise FileNotFoundError("Activity meta not found")

def add_note_to_activity(user_id: int, activity_id: str, text: str, message_id: Optional[int], note_type: str = "note"):
    meta = load_activity_meta(user_id, activity_id)
    meta["notes"].append({
        "message_id": message_id,
        "text": text,
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "type": note_type
    })
    save_activity_meta(user_id, activity_id, meta)

def add_media_to_activity(user_id: int, activity_id: str, source_path: str, filename: str, caption: Optional[str], message_id: Optional[int]):
    p = activity_path(user_id, activity_id)
    # salvar arquivo físico (copiar/mover)
    dest_name = f"{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}_{filename}"
    dest_path = os.path.join(p, dest_name)
    # mover/renomear (se source_path já no user dir, pode dar rename; mas para segurança, faremos copy)
    try:
        # se source_path estiver em mesmo FS, use os.replace/move para manter, senão leia bytes
        os.replace(source_path, dest_path)
    except Exception:
        # fallback copy
        with open(source_path, "rb") as rf:
            with open(dest_path, "wb") as wf:
                wf.write(rf.read())
    # registrar no metadata
    meta = load_activity_meta(user_id, activity_id)
    meta["media"].append({
        "filename": dest_name,
        "caption": caption or "",
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "message_id": message_id
    })
    # se estava pendente de foto, desativa
    meta["pending_photo"] = False
    save_activity_meta(user_id, activity_id, meta)

def link_message_to_activity(user_id: int, message_id: int, activity_id: str):
    mappings = load_mappings(user_id)
    mappings[str(message_id)] = activity_id
    save_mappings(user_id, mappings)

def find_activity_by_message(user_id: int, message_id: int) -> Optional[str]:
    mappings = load_mappings(user_id)
    return mappings.get(str(message_id))

# ---------- BOT HANDLERS ----------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Olá! Eu vou ajudar a registrar suas atividades (texto + fotos).\n"
        "Use os botões rápidos ou envie texto/foto.\n\n"
        "Botões:\n - 🆕 Novo registro: inicia um novo registro\n - 📋 Ver registros: (integração com painel web)\n",
        reply_markup=FIXED_KEYBOARD
    )

# Quando receber texto
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()
    reply_to = update.message.reply_to_message

    # 1) Se é resposta a uma mensagem que está vinculada a uma atividade -> anexar a essa atividade
    if reply_to:
        target_activity = find_activity_by_message(user_id, reply_to.message_id)
        if target_activity:
            # anexa como note
            add_note_to_activity(user_id, target_activity, text, update.message.message_id, note_type="note")
            # vincula a mensagem atual ao mesmo activity (para futuras respostas)
            link_message_to_activity(user_id, update.message.message_id, target_activity)
            await update.message.reply_text("✔️ Texto adicionado à atividade existente.")
            return

    # 2) Se texto equivale ao botão fixo "Novo registro" ou "Ver registros"
    if text == "🆕 Novo registro":
        # pede descrição (ou pode aceitar direto como nova descrição)
        await update.message.reply_text("Ok — envie agora a descrição da nova tarefa (ou digite e escolha).")
        context.user_data["await_mode"] = "registro_text"  # sinaliza que próximo texto é para iniciar registro
        return
    if text == "📋 Ver registros":
        # apenas envio de instrução — integração com painel web é separada
        await update.message.reply_text("Abra a página de consulta (seu painel) para ver registros.\n(ou implementaremos listagem aqui)")
        return

    # 3) Se user está no modo "registro_text" (digitou o comando Novo registro antes)
    if context.user_data.get("await_mode") == "registro_text":
        # tratar como novo registro: perguntar se quer registrar como texto simples ou enviar foto
        context.user_data.pop("await_mode", None)
        # salvamos temporariamente o texto em pending_texts por message_id
        pending_texts = context.user_data.setdefault("pending_texts", {})
        pending_texts[str(update.message.message_id)] = text

        kb = [
            [
                InlineKeyboardButton("📝 Registrar como texto", callback_data=f"register_text|{update.message.message_id}"),
                InlineKeyboardButton("📷 Vou enviar foto", callback_data=f"await_photo|{update.message.message_id}")
            ]
        ]
        await update.message.reply_text("Como deseja registrar esse texto?", reply_markup=InlineKeyboardMarkup(kb))
        return

    # 4) Caso geral: texto avulso (não resposta). Perguntar se quer registrar como texto simples ou irá enviar foto depois.
    # Salvamos o texto temporariamente e apresentamos opções (igual fluxo acima)
    pending_texts = context.user_data.setdefault("pending_texts", {})
    pending_texts[str(update.message.message_id)] = text
    kb = [
        [
            InlineKeyboardButton("📝 Registrar como texto", callback_data=f"register_text|{update.message.message_id}"),
            InlineKeyboardButton("📷 Vou enviar foto", callback_data=f"await_photo|{update.message.message_id}")
        ]
    ]
    await update.message.reply_text("Deseja registrar como texto simples ou vai enviar uma foto para anexar?", reply_markup=InlineKeyboardMarkup(kb))


# Callback das opções (register_text / await_photo / choose etc.)
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data  # ex: "register_text|123456"
    user = query.from_user
    user_id = user.id

    if data.startswith("register_text|"):
        _, msgid_s = data.split("|", 1)
        text = context.user_data.get("pending_texts", {}).pop(msgid_s, None)
        if text is None:
            await query.edit_message_text("Texto não encontrado (talvez tenha expirado). Envie novamente.")
            return
        # criar atividade com texto
        aid = create_activity(user_id, initial_text=text, pending_photo=False)
        # vincular a mensagem original e também a mensagem do bot? vincular o message_id do usuário (msgid_s)
        link_message_to_activity(user_id, int(msgid_s), aid)
        # também vincula a callback message? Não necessário
        await query.edit_message_text("✅ Atividade criada como texto simples.")
        return

    if data.startswith("await_photo|"):
        _, msgid_s = data.split("|", 1)
        text = context.user_data.get("pending_texts", {}).pop(msgid_s, None)
        if text is None:
            await query.edit_message_text("Texto não encontrado (talvez tenha expirado). Envie novamente.")
            return
        # criar atividade pendente
        aid = create_activity(user_id, initial_text=text, pending_photo=True)
        # vincula a mensagem original ao activity
        link_message_to_activity(user_id, int(msgid_s), aid)
        # armazenar no contexto do usuário (para aceitar próxima foto)
        context.user_data["awaiting_photo_activity"] = aid

        # agenda timeout de WAIT_SECONDS
        async def timeout_job(ctx: ContextTypes.DEFAULT_TYPE):
            job = ctx.job  # job context
            data = job.data or {}
            aid_local = data.get("activity_id")
            uid_local = data.get("user_id")
            # checar se activity ainda pendente
            try:
                meta = load_activity_meta(uid_local, aid_local)
            except Exception:
                # activity não existe -> já finalizada
                return
            if meta.get("pending_photo"):
                # atualizar meta: tirar pending
                meta["pending_photo"] = False
                save_activity_meta(uid_local, aid_local, meta)
                # remover awaiting flag do contexto do usuário (se existir)
                # job callback não tem acesso direto a user_data, então apenas enviar mensagem
                try:
                    await context.bot.send_message(chat_id=uid_local, text="⏰ Tempo de espera por foto expirou. A atividade ficou registrada sem foto.")
                except Exception:
                    pass

        job = context.job_queue.run_once(timeout_job, when=WAIT_SECONDS, data={"user_id": user_id, "activity_id": aid})
        # guardar job para possível cancelamento
        context.user_data["awaiting_photo_job_id"] = job.id if hasattr(job, "id") else None
        # também guardar job object so we can cancel easier (PTB returns Job object)
        context.user_data["awaiting_photo_job"] = job

        await query.edit_message_text("✅ Criei a atividade e estou aguardando a foto por até 60 segundos. Envie a foto respondendo a esta conversa.")
        return

    if data.startswith("choosephoto|"):
        # future expansion: escolher entre últimas fotos se quisermos implementar
        await query.edit_message_text("Função de escolher foto não implementada neste fluxo.")
        return

# Ao receber foto
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    message = update.message
    caption = message.caption or ""
    reply_to = message.reply_to_message

    # Primeiro: gravar imagem temporária em pasta do usuário (diretório temporário)
    user_p = user_dir(user_id)
    # salvar arquivo recebido em temp path
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    file = await message.photo[-1].get_file()
    temp_filename = f"upload_{ts}_{uuid.uuid4().hex[:6]}.jpg"
    temp_path = os.path.join(user_p, temp_filename)
    await file.download_to_drive(custom_path=temp_path)

    # 1) Se é resposta a mensagem que pertence a uma activity -> anexar à activity
    if reply_to:
        target_aid = find_activity_by_message(user_id, reply_to.message_id)
        if target_aid:
            add_media_to_activity(user_id, target_aid, temp_path, message.photo[-1].file_unique_id + ".jpg", caption, message.message_id)
            # vincular essa nova mensagem (foto) ao mesmo activity
            link_message_to_activity(user_id, message.message_id, target_aid)
            # se havia job pendente, cancelar
            job = context.user_data.get("awaiting_photo_job")
            if job:
                try:
                    job.schedule_removal()
                except Exception:
                    pass
                context.user_data.pop("awaiting_photo_job", None)
                context.user_data.pop("awaiting_photo_job_id", None)
                context.user_data.pop("awaiting_photo_activity", None)
            await update.message.reply_text("🖼️ Foto anexada à atividade vinculada.")
            return

    # 2) Se existe uma atividade aguardando foto no contexto do usuário -> anexar nela
    pending_aid = context.user_data.get("awaiting_photo_activity")
    if pending_aid:
        try:
            add_media_to_activity(user_id, pending_aid, temp_path, message.photo[-1].file_unique_id + ".jpg", caption, message.message_id)
            link_message_to_activity(user_id, message.message_id, pending_aid)
            # cancelar job se existir
            job = context.user_data.get("awaiting_photo_job")
            if job:
                try:
                    job.schedule_removal()
                except Exception:
                    pass
            # limpar flags
            context.user_data.pop("awaiting_photo_activity", None)
            context.user_data.pop("awaiting_photo_job", None)
            context.user_data.pop("awaiting_photo_job_id", None)
            await update.message.reply_text("✅ Foto recebida e anexada à atividade que estava aguardando.")
            return
        except Exception as e:
            # se algo falhar, garantir que arquivo temporário seja removido
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
            await update.message.reply_text("Erro ao anexar foto: " + str(e))
            return

    # 3) Caso contrário: criar nova atividade com a foto (caption vira caption)
    new_aid = create_activity(user_id, initial_text=None, pending_photo=False)
    add_media_to_activity(user_id, new_aid, temp_path, message.photo[-1].file_unique_id + ".jpg", caption, message.message_id)
    link_message_to_activity(user_id, message.message_id, new_aid)
    await update.message.reply_text("🖼️ Nova atividade criada com esta foto.")


# Comando para listar atividades (simples)
async def list_activities_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    p = os.path.join(user_dir(user_id), "activities")
    if not os.path.isdir(p):
        await update.message.reply_text("Nenhuma atividade encontrada.")
        return
    lst = sorted(os.listdir(p), reverse=True)
    if not lst:
        await update.message.reply_text("Nenhuma atividade encontrada.")
        return
    text_lines = ["Atividades (mais recentes primeiro):"]
    for aid in lst[:20]:
        try:
            meta = load_activity_meta(user_id, aid)
            created = meta.get("created_at", "")[:19]
            n_notes = len(meta.get("notes", []))
            n_media = len(meta.get("media", []))
            text_lines.append(f"- {aid} | {created} | notas:{n_notes} fotos:{n_media} | pending_photo:{meta.get('pending_photo')}")
        except Exception:
            text_lines.append(f"- {aid} | (erro ao ler metadata)")
    await update.message.reply_text("\n".join(text_lines))

# Handler fallback / help
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Envie textos ou fotos. Responda mensagens para anexar ao registro daquela mensagem. Use /list para ver resumo.")

# ---------- BOOT ----------

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("list", list_activities_cmd))

    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()
