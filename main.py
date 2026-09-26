import os
import re
import pytz
import threading
import time 
from datetime import datetime, timedelta
from telebot import TeleBot
from telebot.types import ChatPermissions
from tinydb import TinyDB, Query
from dotenv import load_dotenv

# --- Config ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMINS", "").split(','))) if os.getenv("ADMINS") else []

bot = TeleBot(TOKEN)
db = TinyDB("punishments.json")
warns_table = db.table("warns")
mutes_table = db.table("mutes")
kicks_table = db.table("kicks")
bans_table = db.table("bans")

# --- Функции для создания объектов разрешений ---
def create_restricted_permissions():
    return ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False
    )

def create_full_permissions():
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False
    )

ALLOWED_LINKS = ["https://t.me/TikTokModDownload", "https://t.me/ChatTTMD"]
chat_ids = set()
chat_locked = {}

def now_utc():
    return datetime.utcnow()

def msk_time():
    return datetime.now(pytz.timezone("Europe/Moscow"))

def is_restricted_time():
    msk_hour = msk_time().hour
    return msk_hour >= 17 or msk_hour < 6

def contains_bad_link(text):
    links = re.findall(r'https?://\S+', text)
    return any(link not in ALLOWED_LINKS for link in links)

def warn_user(user_id):
    record = warns_table.get(Query().user_id == user_id)
    count = (record["warns"] if record else 0) + 1
    if record:
        warns_table.update({"warns": count}, Query().user_id == user_id)
    else:
        warns_table.insert({"user_id": user_id, "warns": count})
    return count

def unwarn_user(user_id):
    record = warns_table.get(Query().user_id == user_id)
    if record and record["warns"] > 0:
        new_warns = record["warns"] - 1
        warns_table.update({"warns": new_warns}, Query().user_id == user_id)
        return new_warns
    return 0

def restrict_all(chat_id, user_id, until=None):
    permissions = create_restricted_permissions()
    until_timestamp = int(until.timestamp()) if until else None
    bot.restrict_chat_member(
        chat_id, 
        user_id, 
        permissions=permissions, 
        until_date=until_timestamp
    )

def unrestrict_all(chat_id, user_id):
    permissions = create_full_permissions()
    bot.restrict_chat_member(
        chat_id, 
        user_id, 
        permissions=permissions
    )

def mute_user_db(chat_id, user_id, until, mute_type="manual"):
    until_timestamp = int(until.timestamp()) if until else None
    mutes_table.upsert({
        "chat_id": chat_id,
        "user_id": user_id,
        "until": until_timestamp,
        "type": mute_type
    }, (Query().chat_id == chat_id) & (Query().user_id == user_id))

def unmute_user_db(chat_id, user_id):
    mutes_table.remove((Query().chat_id == chat_id) & (Query().user_id == user_id))

def log_action(table, chat_id, user_id, reason=""):
    table.insert({"chat_id": chat_id, "user_id": user_id, "timestamp": now_utc().isoformat(), "reason": reason})

def update_chat_lock(chat_id):
    """Мгновенно обновляет состояние блокировки для конкретного чата"""
    try:
        should_lock = is_restricted_time()
        
        if should_lock and not chat_locked.get(chat_id, False):
            bot.set_chat_permissions(chat_id, create_restricted_permissions())
            chat_locked[chat_id] = True
            bot.send_message(chat_id, "🔒 Чат закрыт с 17:00 до 6:00 по МСК.")
        
        elif not should_lock and chat_locked.get(chat_id, False):
            bot.set_chat_permissions(chat_id, create_full_permissions())
            chat_locked[chat_id] = False
            bot.send_message(chat_id, "🔓 Чат открыт. Доброе утро!")
    
    except Exception as e:
        print(f"[chat_lock error] Ошибка в чате {chat_id}: {e}")

def check_expired_mutes():
    """Проверяет и снимает истекшие муты"""
    now_timestamp = int(now_utc().timestamp())
    manual_expired = mutes_table.search(
        (Query().type == "manual") &
        (Query().until.test(lambda u: u is not None and now_timestamp >= u))
    )

    for mute in manual_expired:
        try:
            unrestrict_all(mute["chat_id"], mute["user_id"])
            unmute_user_db(mute["chat_id"], mute["user_id"])
            bot.send_message(mute["chat_id"], f"🔊 Пользователь {mute['user_id']} автоматически размучен.")
        except Exception as e:
            print(f"Ошибка при размуте: {e}")

def background_scheduler():
    """Фоновый планировщик для мгновенной реакции на время"""
    while True:
        try:
            for chat_id in list(chat_ids):
                update_chat_lock(chat_id)
            check_expired_mutes()
            
            now = datetime.utcnow()
            seconds_until_next_minute = 60 - now.second
            time.sleep(seconds_until_next_minute)
            
        except Exception as e:
            print(f"Ошибка в планировщике: {e}")
            time.sleep(60)

def get_target_user(message):
    if message.reply_to_message:
        return message.reply_to_message.from_user.id
    else:
        bot.reply_to(message, "Ответь на сообщение пользователя.")
        return None

@bot.message_handler(commands=['start'])
def start_command(message):
    bot.reply_to(message, "✅ Бот запущен и готов к работе!")
    if message.chat.type in ['group', 'supergroup']:
        chat_ids.add(message.chat.id)
        update_chat_lock(message.chat.id)

@bot.message_handler(commands=['ban'])
def cmd_ban(message):
    if message.from_user.id not in ADMIN_IDS: return
    uid = get_target_user(message)
    if uid:
        try:
            bot.ban_chat_member(message.chat.id, uid)
            log_action(bans_table, message.chat.id, uid)
            bot.send_message(message.chat.id, "🚫 Пользователь забанен.")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка при бане: {e}")

@bot.message_handler(commands=['unban'])
def cmd_unban(message):
    if message.from_user.id not in ADMIN_IDS: return
    uid = get_target_user(message)
    if uid:
        try:
            bot.unban_chat_member(message.chat.id, uid)
            bot.send_message(message.chat.id, "✅ Пользователь разбанен.")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка при разбане: {e}")

@bot.message_handler(commands=['kick'])
def cmd_kick(message):
    if message.from_user.id not in ADMIN_IDS: return
    uid = get_target_user(message)
    if uid:
        try:
            bot.ban_chat_member(message.chat.id, uid)
            bot.unban_chat_member(message.chat.id, uid)
            log_action(kicks_table, message.chat.id, uid)
            bot.send_message(message.chat.id, "👢 Пользователь кикнут.")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка при кике: {e}")

@bot.message_handler(commands=['mute'])
def cmd_mute(message):
    if message.from_user.id not in ADMIN_IDS: return
    uid = get_target_user(message)
    if uid:
        parts = message.text.split()
        try:
            mins = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 60
        except (ValueError, IndexError):
            mins = 60
        
        until = now_utc() + timedelta(minutes=mins) if mins > 0 else None
        
        try:
            restrict_all(message.chat.id, uid, until)
            mute_user_db(message.chat.id, uid, until, mute_type="manual")
            bot.send_message(message.chat.id, f"🔇 Пользователь замучен на {mins} мин.")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка при муте: {e}")

@bot.message_handler(commands=['unmute'])
def cmd_unmute(message):
    if message.from_user.id not in ADMIN_IDS: return
    uid = get_target_user(message)
    if uid:
        try:
            unrestrict_all(message.chat.id, uid)
            unmute_user_db(message.chat.id, uid)
            bot.send_message(message.chat.id, "🔊 Пользователь размучен.")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка при размуте: {e}")

@bot.message_handler(commands=['warn'])
def cmd_warn(message):
    if message.from_user.id not in ADMIN_IDS: return
    uid = get_target_user(message)
    if uid:
        warns = warn_user(uid)
        bot.send_message(message.chat.id, f"⚠️ Выдано предупреждение. Текущее: {warns}/3.")

@bot.message_handler(commands=['unwarn'])
def cmd_unwarn(message):
    if message.from_user.id not in ADMIN_IDS: return
    uid = get_target_user(message)
    if uid:
        left = unwarn_user(uid)
        bot.send_message(message.chat.id, f"✅ Предупреждение снято. Осталось: {left}/3.")

@bot.message_handler(commands=['warnlist'])
def cmd_warnlist(message):
    items = warns_table.all()
    if not items:
        bot.reply_to(message, "Нет предупреждений.")
    else:
        msg = "\n".join([f"ID: {w['user_id']} — {w['warns']} предупреждений" for w in items])
        bot.reply_to(message, f"📋 Список предупреждений:\n{msg}")

@bot.message_handler(commands=['mutelist'])
def cmd_mutelist(message):
    items = mutes_table.all()
    if not items:
        bot.reply_to(message, "Нет замученных.")
    else:
        msg = "\n".join([
            f"ID: {m['user_id']} — до {datetime.fromtimestamp(m['until']).strftime('%Y-%m-%d %H:%M:%S')} ({m['type']})" 
            if m['until'] else f"ID: {m['user_id']} — Навсегда ({m['type']})"
            for m in items
        ])
        bot.reply_to(message, f"🔇 Список мутов:\n{msg}")

@bot.message_handler(commands=['banlist'])
def cmd_banlist(message):
    items = bans_table.all()
    if not items:
        bot.reply_to(message, "Нет банов.")
    else:
        msg = "\n".join([f"ID: {b['user_id']} — {b['timestamp']}" for b in items])
        bot.reply_to(message, f"🚫 Список банов:\n{msg}")

@bot.message_handler(commands=['kicklist'])
def cmd_kicklist(message):
    items = kicks_table.all()
    if not items:
        bot.reply_to(message, "Нет киков.")
    else:
        msg = "\n".join([f"ID: {k['user_id']} — {k['timestamp']}" for k in items])
        bot.reply_to(message, f"👢 Список киков:\n{msg}")

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation'])
def handle_message(message):
    if message.chat.type not in ['group', 'supergroup']:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    chat_ids.add(chat_id)
    is_admin = user_id in ADMIN_IDS

    if chat_id not in chat_locked:
        chat_locked[chat_id] = is_restricted_time()

    if message.sender_chat and not is_admin:
        try:
            bot.delete_message(chat_id, message.message_id)
            bot.send_message(chat_id, "🚫 Запрещено писать от имени канала.")
        except Exception as e:
            print(f"Ошибка при удалении сообщения от канала: {e}")
        return

    if message.text and contains_bad_link(message.text) and not is_admin:
        try:
            bot.delete_message(chat_id, message.message_id)
            warns = warn_user(user_id)
            bot.send_message(chat_id, f"🔗 Разрешены только ссылки на:\n" + "\n".join(ALLOWED_LINKS))
            
            if warns >= 3:
                until = now_utc() + timedelta(hours=1)
                restrict_all(chat_id, user_id, until)
                mute_user_db(chat_id, user_id, until, mute_type="auto")
                bot.send_message(chat_id, f"🔇 Пользователь замучен на 1 час за 3 предупреждения.")
        except Exception as e:
            print(f"Ошибка при обработке сообщения с ссылкой: {e}")
        return

# --- Запуск бота ---
if __name__ == "__main__":
    print("✅ Бот запущен")
    
    scheduler_thread = threading.Thread(target=background_scheduler, daemon=True)
    scheduler_thread.start()
    
    for chat_id in list(chat_ids):
        try:
            chat_locked[chat_id] = is_restricted_time()
        except:
            pass
    
    bot.infinity_polling()
