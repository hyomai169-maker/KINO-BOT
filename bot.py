# -*- coding: utf-8 -*-
import os
import re
import fs
import json
import math
import asyncio
import logging
import datetime
import tempfile
from typing import Any, Dict, List, Optional

# Telegram Bot va Firebase kutubxonalari
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import FSInputFile, ReplyKeyboardMarkup, InlineKeyboardMarkup

import firebase_admin
from firebase_admin import credentials, firestore
import requests

# Jurnal yuritishni sozlash (Logging)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Muhit o'zgaruvchilari (Environment Variables) yoki sukutiy qiymatlar
BOT_TOKEN = os.getenv("BOT_TOKEN", "8715391910:AAGAGsm9Y9kBi-ZXzavWEFwB84FseVAiq0A")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5775388579"))

# Firebase-ni ishga tushirish (Service Account orqali)
# Eslatma: Mustaqil serverda Firebase Admin SDK ishlashi uchun "firebase-service-account.json" fayli kerak bo'ladi.
# Uni Firebase Console -> Project Settings -> Service Accounts bo'limidan yuklab olishingiz mumkin.
firebase_service_account_path = "firebase-service-account.json"

if os.path.exists(firebase_service_account_path):
    cred = credentials.Certificate(firebase_service_account_path)
    firebase_admin.initialize_app(cred)
    logger.info("Firebase Admin SDK muvaffaqiyatli ishga tushirildi (Service Account orqali).")
else:
    # Agarda service account bo'lmasa, web config-dan foydalanishga harakat qilamiz
    # Lekin eslatib o'tamiz, Firebase Admin SDK uchun Service Account tavsiya etiladi.
    try:
        if os.path.exists("./firebase-applet-config.json"):
            with open("./firebase-applet-config.json", "r", encoding="utf-8") as f:
                web_config = json.load(f)
            # Standart hisobga olish ma'lumotlari bilan ishga tushiramiz (agar Google Cloud-da bo'lsa)
            firebase_admin.initialize_app()
            logger.info("Firebase Admin SDK standart muhit ma'lumotlari orqali ishga tushirildi.")
        else:
            raise FileNotFoundError("Hech qanday Firebase sozlamalari topilmadi.")
    except Exception as e:
        logger.error(f"Firebase init xatoligi: {e}. Iltimos 'firebase-service-account.json' faylini yuklang.")
        # Mustaqil ishlash uchun loyihada xatolik yuz bermasligi uchun standart sozlashni chaqiramiz
        try:
            firebase_admin.initialize_app()
        except Exception:
            pass

db = firestore.client()

# Bot va Dispatcher-ni yaratish
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# FSM (Finite State Machine) holatlari
class BotStates(StatesGroup):
    add_ch = State()          # Kanal qo'shish (ommaviy yoki uzatilgan xabar)
    add_ch_link = State()     # Maxfiy kanal uchun taklif havolasini kutish
    del_ch = State()          # Kanal o'chirish
    one_video = State()       # Bitta kino videosini/faylini kutish
    one_code = State()        # Bitta kino kodini kutish
    one_name = State()        # Bitta kino nomini kutish
    batch_codes = State()     # Ommaviy qo'shish kodlarini kutish
    batch_vids = State()      # Ommaviy qo'shish videolarini kutish
    del_m = State()           # Kino o'chirish kodini kutish
    wait_lim = State()        # Reklama yuborish limitini kutish
    wait_ad = State()         # Reklama xabarini kutish
    import_backup = State()   # Backup yuklash faylini kutish
    sch_channel = State()     # Rejalashtirish kanali
    sch_media = State()       # Rejalashtirish videosi
    sch_name = State()        # Rejalashtirish kino nomi
    sch_start_time = State()  # Rejalashtirish hozirgi vaqti
    sch_target_time = State() # Rejalashtirish jo'natish vaqti


# Klaviaturalar (Keyboards)
def get_admin_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="🎬 Kino qo‘shish"), types.KeyboardButton(text="📦 Ommaviy qo‘shish"))
    builder.row(types.KeyboardButton(text="🗑 Kino o‘chirish"), types.KeyboardButton(text="📊 Statistika"))
    builder.row(types.KeyboardButton(text="📢 Kanal qo‘shish"), types.KeyboardButton(text="❌ Kanal o‘chirish"))
    builder.row(types.KeyboardButton(text="👥 Userlar"), types.KeyboardButton(text="📢 Reklama"))
    builder.row(types.KeyboardButton(text="📥 Backup olish"), types.KeyboardButton(text="📤 Backup yuklash"))
    builder.row(types.KeyboardButton(text="📢 Kanallarga yuklash"))
    return builder.as_markup(resize_keyboard=True)

def get_user_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="🎲 Tasodifiy kino"), types.KeyboardButton(text="🔥 Top kinolar"))
    return builder.as_markup(resize_keyboard=True)


# Ko'rishlar sonini vizual oshirish formulasi (JS dagi getAmplifiedViews bilan bir xil)
def get_amplified_views(views_count: int, code: str) -> int:
    views = views_count or 0
    code_str = str(code)
    
    # Stable hash hisoblash
    hash_val = 0
    for char in code_str:
        hash_val = ord(char) + ((hash_val << 5) - hash_val)
        hash_val = hash_val & 0xFFFFFFFF  # 32-bit int saqlash
        
    base = abs(hash_val % 1260) + 240
    
    # Haftalik o'zgarish multiplikatori
    today = datetime.date.today()
    # Haftani hisoblash (JS bilan bir xil ritmda)
    week_num = math.ceil(today.day / 7) + today.month + 1
    week_factor = 1 + (week_num * 0.05)
    
    return math.floor((base + (views * 8)) * week_factor)


# Kanal tozalash kaliti
def get_channel_key(channel_str: str) -> str:
    if not channel_str:
        return ""
    s = channel_str.strip().lower()
    # Protokollar va domenlarni o'chirish
    s = re.sub(r"^(https?://)?(www\.)?(t\.me/joinchat/|t\.me/|telegram\.me/joinchat/|telegram\.me/)", "", s)
    # @, + va bo'shliqlarni o'chirish
    return re.sub(r"[@+\s]", "", s)


# Backup dan kanalni aniqlash
def extract_channel(item: Any) -> Optional[str]:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        if "channel" in item and isinstance(item["channel"], str):
            return item["channel"].strip()
        if "channel_id" in item and isinstance(item["channel_id"], str):
            return item["channel_id"].strip()
        if "username" in item and isinstance(item["username"], str):
            return item["username"].strip()
        if "link" in item and isinstance(item["link"], str):
            return item["link"].strip()
        if "name" in item and isinstance(item["name"], str) and (item["name"].startswith("@") or "t.me" in item["name"]):
            return item["name"].strip()
            
        for k, v in item.items():
            if isinstance(v, str):
                trimmed = v.strip()
                if trimmed.startswith("@") or "t.me/" in trimmed:
                    return trimmed
    return None


# Backup dan kinoni aniqlash
def extract_movie(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
        
    code = None
    if "code" in item and item["code"] is not None:
        code = str(item["code"]).strip()
    elif "kodi" in item and item["kodi"] is not None:
        code = str(item["kodi"]).strip()
    elif "id" in item and item["id"] is not None:
        try:
            # Tekshiramiz raqamli id bo'lsa
            float(item["id"])
            code = str(item["id"]).strip()
        except ValueError:
            pass
            
    file_id = None
    for field in ["file_id", "fileId", "video_id", "video"]:
        if field in item and isinstance(item[field], str):
            file_id = item[field].strip()
            break
            
    if not code or not file_id:
        return None
        
    name = f"Kino {code}"
    if "name" in item and isinstance(item["name"], str):
        name = item["name"].strip()
    elif "nomi" in item and isinstance(item["nomi"], str):
        name = item["nomi"].strip()
    elif "title" in item and isinstance(item["title"], str):
        name = item["title"].strip()
        
    file_type = "video"
    if "file_type" in item and isinstance(item["file_type"], str):
        file_type = item["file_type"].strip()
    elif "fileType" in item and isinstance(item["fileType"], str):
        file_type = item["fileType"].strip()
        
    views = 0
    if "views" in item:
        if isinstance(item["views"], (int, float)):
            views = int(item["views"])
        elif isinstance(item["views"], str):
            try:
                views = int(item["views"])
            except ValueError:
                pass
                
    return {
        "code": code,
        "file_id": file_id,
        "file_type": file_type,
        "name": name,
        "views": views
    }


# Uzbek vaqt formatini parse qilish (kun.oy.yil soat:daqiqa)
def parse_uzbek_date_time(input_str: str) -> Optional[datetime.datetime]:
    try:
        parts = input_str.strip().split()
        if len(parts) != 2:
            return None
        date_parts = re.split(r"[-.]", parts[0])
        time_parts = parts[1].split(":")
        if len(date_parts) != 3 or len(time_parts) != 2:
            return None
            
        day = int(date_parts[0])
        month = int(date_parts[1])
        year = int(date_parts[2])
        hour = int(time_parts[0])
        minute = int(time_parts[1])
        
        if day < 1 or day > 31 or month < 1 or month > 12 or year < 2000 or hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
            
        return datetime.datetime(year, month, day, hour, minute)
    except Exception:
        return None


# Avtomatik backup olish va adminga yuborish
async def send_auto_backup(bot_instance: Bot):
    try:
        movies_ref = db.collection("movies").stream()
        channels_ref = db.collection("channels").stream()
        users_ref = db.collection("users").stream()
        
        movies_data = [d.to_dict() for d in movies_ref]
        channels_data = [d.to_dict() for d in channels_ref]
        users_data = [d.to_dict() for d in users_ref]
        
        movies_backup = {
            "movies": movies_data,
            "channels": channels_data
        }
        stats_backup = {
            "total_users": len(users_data),
            "users": users_data
        }
        
        now = int(datetime.datetime.now().timestamp())
        movies_fn = f"kinolar_backup_auto_{now}.json"
        stats_fn = f"statistika_backup_auto_{now}.json"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            movies_path = os.path.join(temp_dir, movies_fn)
            stats_path = os.path.join(temp_dir, stats_fn)
            
            with open(movies_path, "w", encoding="utf-8") as f:
                json.dump(movies_backup, f, indent=2, ensure_ascii=False)
                
            with open(stats_path, "w", encoding="utf-8") as f:
                json.dump(stats_backup, f, indent=2, ensure_ascii=False)
                
            # Adminga yuborish
            movies_file = FSInputFile(movies_path, filename=movies_fn)
            stats_file = FSInputFile(stats_path, filename=stats_fn)
            
            caption_movies = (
                f"🎬 <b>AVTOMATIK KINOLAR VA KANALLAR ZAXIRASI</b>\n\n"
                f"Ma'lumotlar bazasida o'zgarish bo'ldi (kino yoki kanal o'zgarddi).\n\n"
                f"📊 <b>Hozirgi holat:</b>\n"
                f"🎬 Jami kinolar: <b>{len(movies_data)}</b> ta\n"
                f"📢 Jami kanallar: <b>{len(channels_data)}</b> ta\n\n"
                f"<i>Kinolar va Kanallarni tiklash uchun ushbu fayldan foydalaning!</i>"
            )
            
            caption_stats = (
                f"👥 <b>AVTOMATIK FOYDALANUVCHILAR VA STATISTIKA ZAXIRASI</b>\n\n"
                f"Foydalanuvchilar zaxira nusxasi (statistika yo'qolmasligi uchun).\n\n"
                f"📊 <b>Hozirgi holat:</b>\n"
                f"👥 Jami foydalanuvchilar (IDlar): <b>{len(users_data)}</b> ta\n\n"
                f"<i>Foydalanuvchilar ma'lumoti va statistikasini tiklash uchun ushbu fayldan foydalaning!</i>"
            )
            
            await bot_instance.send_document(ADMIN_ID, movies_file, caption=caption_movies, parse_mode="HTML")
            await bot_instance.send_document(ADMIN_ID, stats_file, caption=caption_stats, parse_mode="HTML")
            logger.info("Avtomatik zaxira nusxasi muvaffaqiyatli adminga jo'natildi.")
    except Exception as e:
        logger.error(f"Avtomatik backup olish xatosi: {e}")


# Obunani tekshirish funksiyasi
async def check_subscription(bot_instance: Bot, user_id: int) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]:
    channels_ref = db.collection("channels").stream()
    not_joined = []
    all_channels = []
    
    for doc in channels_ref:
        data = doc.to_dict()
        channel_id = data.get("chat_id") or data.get("channel")
        is_public = data.get("type") != "private"
        invite_link = data.get("channel")
        title = data.get("title") or (invite_link if is_public else "Maxfiy Kanal")
        
        url = invite_link
        if is_public:
            if invite_link.startswith("@"):
                url = f"https://t.me/{invite_link.replace('@', '')}"
                
        ch_item = {"channel": invite_link, "url": url, "title": title}
        all_channels.append(ch_item)
        
        if is_public:
            try:
                # Chat ID raqamli yoki matnli bo'lishi mumkin
                chat_target = channel_id
                if isinstance(chat_target, str) and (chat_target.startswith("-100") or chat_target.isdigit()):
                    chat_target = int(chat_target)
                
                member = await bot_instance.get_chat_member(chat_id=chat_target, user_id=user_id)
                if member.status not in ["member", "administrator", "creator"]:
                    not_joined.append(ch_item)
            except Exception as e:
                logger.warning(f"Bot ommaviy kanalda a'zolikni tekshira olmadi {channel_id}: {e}")
        else:
            # Maxfiy kanal
            has_subscribed = False
            try:
                chat_target = channel_id
                if isinstance(chat_target, str) and (chat_target.startswith("-100") or chat_target.isdigit()):
                    chat_target = int(chat_target)
                member = await bot_instance.get_chat_member(chat_id=chat_target, user_id=user_id)
                if member.status in ["member", "administrator", "creator"]:
                    has_subscribed = True
            except Exception as e:
                logger.debug(f"Maxfiy kanal uchun get_chat_member bajarilmadi {channel_id}: {e}")
                
            if not has_subscribed:
                # Firestore-dagi chat_join_request-larni tekshirish
                try:
                    cleaned_chat_id = "".join([c if c.isalnum() else "_" for c in str(channel_id)])
                    req_id = f"{cleaned_chat_id}_{user_id}"
                    req_doc = db.collection("join_requests").document(req_id).get()
                    if req_doc.exists:
                        has_subscribed = True
                except Exception as db_err:
                    logger.error(f"Firestore join_requests qidirish xatosi: {db_err}")
                    
            if not has_subscribed:
                not_joined.append(ch_item)
                
    return not_joined, all_channels


# Majburiy obuna tugmalari generatsiyasi
def get_sub_inline_keyboard(all_channels: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, ch in enumerate(all_channels):
        builder.row(types.InlineKeyboardButton(text=f"📢 {idx + 1}-KANAL", url=ch["url"]))
    builder.row(types.InlineKeyboardButton(text="✅ Obuna bo'ldim", callback_data="check_sub"))
    return builder.as_markup()


# ------------------ HANDLERS (KOD QO'LLOVCHILAR) ------------------

# Bekor qilish komandasi (Cancel command)
@dp.message(Command("buldi", "stop"))
async def cancel_handler(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    await message.answer("👌 Tushundim. Barcha harakatlar to'xtatildi.", reply_markup=get_admin_keyboard())


# Start komandasi
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "NoName"
    
    # Userni Firestore ga saqlash
    db.collection("users").document(str(user_id)).set({
        "user_id": str(user_id),
        "username": username
    }, merge=True)
    
    if user_id == ADMIN_ID:
        await message.answer("🔥 ADMIN PANEL", reply_markup=get_admin_keyboard())
        return
        
    # Oddiy foydalanuvchi obunalarini tekshirish
    not_joined, all_channels = await check_subscription(bot, user_id)
    if not_joined:
        beautiful_text = (
            f"👋 <b>Assalomu alaykum!</b>\n\n"
            f"🤖 Botdan bepul foydalanish uchun quyidagi homiy kanallariga obuna bo'lishingiz majburiydir:\n\n"
            f"<i>👇 Quyidagi kanal tugmalarini bosing va a'zo bo'ling:</i>"
        )
        await message.answer(beautiful_text, reply_markup=get_sub_inline_keyboard(all_channels), parse_mode="HTML")
        return
        
    await message.answer("🎬 Kino kodini yuboring yoki quyidagi tugmalardan birini tanlang:", reply_markup=get_user_keyboard())


# Obunani tekshirish Callback
@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    not_joined, all_channels = await check_subscription(bot, user_id)
    
    if not_joined:
        await callback.answer("❌ Hali hamma kanallarga a'zo emassiz!", show_alert=True)
        return
        
    await callback.answer("✅ Rahmat! Obunangiz muvaffaqiyatli tasdiqlandi.", show_alert=True)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "🚀 Obunangiz tasdiqlandi! Kino kodini yuboring yoki quyidagi maxsus xizmatlardan foydalaning:",
        reply_markup=get_user_keyboard()
    )


# Chatga qo'shilish so'rovi (chat_join_request)
@dp.chat_join_request()
async def chat_join_request_handler(update: types.ChatJoinRequest):
    try:
        user_id = update.from_user.id
        chat_id = str(update.chat.id)
        
        logger.info(f"Yangi chat qo'shilish so'rovi: User {user_id} -> {chat_id}")
        
        cleaned_chat_id = "".join([c if c.isalnum() else "_" for c in chat_id])
        req_id = f"{cleaned_chat_id}_{user_id}"
        
        # Firestore-ga pending request sifatida yozish
        db.collection("join_requests").document(req_id).set({
            "chat_id": chat_id,
            "user_id": str(user_id),
            "approved": False,
            "timestamp": datetime.datetime.now().isoformat()
        })
        logger.info(f"Join request Firestore-ga yozildi: {req_id}")
    except Exception as e:
        logger.error(f"Chat join request xatosi: {e}")


# ------------------ ADMIN PANEL BO'LIMI ------------------

# Statistika tugmasi
@dp.message(F.text == "📊 Statistika")
async def stats_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    users_snap = db.collection("users").get()
    movies_snap = db.collection("movies").get()
    channels_snap = db.collection("channels").get()
    
    # Kanallarni formatlash
    channel_list_text = ""
    if not channels_snap:
        channel_list_text = "<i>❌ Kanal qo'shilmagan</i>"
    else:
        for idx, doc in enumerate(channels_snap):
            data = doc.to_dict()
            is_private = data.get("type") == "private"
            name = data.get("title") or data.get("channel")
            invite_link = data.get("channel", "")
            
            url = invite_link
            if not is_private and invite_link.startswith("@"):
                url = f"https://t.me/{invite_link.replace('@', '')}"
                
            channel_list_text += f"<b>{idx + 1}.</b> <a href='{url}'>{name}</a> ({'🔒 Maxfiy' if is_private else '🌐 Ommaviy'})\n"
            
    # Top-5 kinolar
    movies_list = [d.to_dict() for d in movies_snap]
    movies_list.sort(key=lambda x: get_amplified_views(x.get("views", 0), x.get("code", "")), reverse=True)
    top_movies = movies_list[:5]
    
    top_movies_text = ""
    if not top_movies:
        top_movies_text = "<i>❌ Hozircha kinolar yo'q</i>"
    else:
        for idx, m in enumerate(top_movies):
            code = m.get("code")
            name = m.get("name")
            views = get_amplified_views(m.get("views", 0), code)
            top_movies_text += f"<b>👉 {idx + 1}.</b> <code>{code}</code> — <b>{name}</b> (👁 {views} marta ko'rildi)\n"
            
    html_stats = (
        f"📊 <b>BOT STATISTIKASI</b>\n\n"
        f"👤 <b>Jami foydalanuvchilar:</b> <code>{len(users_snap)}</code> ta\n"
        f"🎬 <b>Jami kinolar:</b> <code>{len(movies_snap)}</code> ta\n"
        f"📢 <b>Majburiy kanallar:</b> <code>{len(channels_snap)}</code> ta\n\n"
        f"📢 <b>MAJBURIY OBUNA KANALLARI LISTI:</b>\n{channel_list_text}\n"
        f"🎬 <b>ENG KO'P KO'RILGAN TOP-5 KINO:</b>\n{top_movies_text}"
    )
    
    await message.answer(html_stats, reply_markup=get_admin_keyboard(), parse_mode="HTML")


# Userlar ro'yxati (oxirgi 50ta)
@dp.message(F.text == "👥 Userlar")
async def list_users_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    users_snap = db.collection("users").limit(50).get()
    if not users_snap:
        await message.answer("Hozircha foydalanuvchilar yo'q.")
        return
        
    lst = "👤 Foydalanuvchilar ro'yxati (oxirgi 50ta):\n\n"
    for idx, doc in enumerate(users_snap):
        u = doc.to_dict()
        lst += f"{idx + 1}. ID: {u.get('user_id')} - @{u.get('username', 'username_yoq')}\n"
        
    await message.answer(lst)


# Kanal qo'shish tugmasi
@dp.message(F.text == "📢 Kanal qo‘shish")
async def add_channel_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.add_ch)
    info_msg = (
        f"📢 <b>KANAL QO'SHISH</b>\n\n"
        f"Kanal qo'shish usulini tanlang:\n\n"
        f"1. 🌐 <b>Ommaviy kanal:</b> havola yozing yoki @username yuboring.\n"
        f"📦 Masalan: <code>@uz_kinolar</code> yoki <code>https://t.me/uz_kinolar</code>\n\n"
        f"2. 🔒 <b>Maxfiy (yopiq) kanal:</b> ushbu kanaldagi biror bir xabarni botga <b>FORWARD (uzatilgan xabar)</b> qilib yuboring! (Bot kanalda admin bo'lishi kerak)"
    )
    await message.answer(info_msg, parse_mode="HTML")


# Kanal qo'shishni qayta ishlash
@dp.message(BotStates.add_ch)
async def add_channel_process(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    # Forwarded (Uzatilgan) xabar aniqlansa (maxfiy kanal deb hisoblaymiz)
    if message.forward_from_chat:
        chat_id = message.forward_from_chat.id
        title = message.forward_from_chat.title or "Maxfiy Kanal"
        
        await state.update_data(chat_id=str(chat_id), title=title)
        await state.set_state(BotStates.add_ch_link)
        
        follow_up = (
            f"🔒 <b>Maxfiy kanal aniqlandi!</b>\n\n"
            f"🏢 Nomi: <b>{title}</b>\n"
            f"🆔 ID: <code>{chat_id}</code>\n\n"
            f"Endi foydalanuvchilar obuna bo'lishi uchun ushbu kanalning <b>taklif havolasini (https://t.me/+)</b> yuboring:"
        )
        await message.answer(follow_up, parse_mode="HTML")
        return
        
    # Ommaviy kanal kiritilsa
    if message.text:
        text = message.text.strip()
        channel_user = text
        if channel_user.startswith("http://") or channel_user.startswith("https://"):
            parts = channel_user.split("/")
            last_part = parts[-1]
            if last_part:
                channel_user = f"@{last_part}"
        elif not channel_user.startswith("@"):
            channel_user = f"@{channel_user}"
            
        channel_id = "".join([c if c.isalnum() else "_" for c in channel_user])
        
        db.collection("channels").document(channel_id).set({
            "channel": channel_user,
            "chat_id": channel_user,
            "title": channel_user,
            "type": "public"
        })
        
        await state.clear()
        html_msg = (
            f"✅ <b>Kanal muvaffaqiyatli qo'shildi!</b>\n\n"
            f"📢 <b>Kanal:</b> <code>{channel_user}</code>\n"
            f"🌐 <b>Turi:</b> Ommaviy kanal\n\n"
            f"Foydalanuvchilar ushbu kanalga a'zo bo'lmaguncha botdan foydalana olmaydilar."
        )
        await message.answer(html_msg, reply_markup=get_admin_keyboard(), parse_mode="HTML")
        await send_auto_backup(bot)


# Maxfiy kanal taklif havolasini qabul qilish
@dp.message(BotStates.add_ch_link)
async def add_channel_private_link(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    if not message.text:
        await message.answer("Iltimos, faqat matnli havola yuboring:")
        return
        
    invite_link = message.text.strip()
    data = await state.get_data()
    chat_id = data.get("chat_id")
    title = data.get("title")
    
    channel_id = "".join([c if c.isalnum() else "_" for c in str(chat_id)])
    
    db.collection("channels").document(channel_id).set({
        "channel": invite_link,
        "chat_id": chat_id,
        "title": title,
        "type": "private"
    })
    
    await state.clear()
    html_msg = (
        f"✅ <b>Maxfiy kanal muvaffaqiyatli qo'shildi!</b>\n\n"
        f"🏢 <b>Kanal nomi:</b> <code>{title}</code>\n"
        f"🔗 <b>Taklif havolasi:</b> {invite_link}\n"
        f"🔒 <b>Turi:</b> Maxfiy kanal\n\n"
        f"Foydalanuvchilarga ushbu maxfiy havola orqali obuna bo'lish so'raladi."
    )
    await message.answer(html_msg, reply_markup=get_admin_keyboard(), parse_mode="HTML")
    await send_auto_backup(bot)


# Kanal o'chirish start
@dp.message(F.text == "❌ Kanal o‘chirish")
async def delete_channel_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    channels_snap = db.collection("channels").get()
    if not channels_snap:
        await message.answer("Kanallar yo'q.")
        return
        
    await state.set_state(BotStates.del_ch)
    lst = "O'chiriladigan kanalni tanlang (Nomi, havolasi yoki @username ini to'liq yozib yuboring):\n\n"
    for doc in channels_snap:
        data = doc.to_dict()
        lst += f"- <code>{data.get('channel')}</code>\n"
        
    await message.answer(lst, parse_mode="HTML")


# Kanal o'chirishni yakunlash
@dp.message(BotStates.del_ch)
async def delete_channel_process(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    target_input = message.text.strip()
    channels_snap = db.collection("channels").get()
    found_doc_id = None
    display_channel = target_input
    
    for doc_snap in channels_snap:
        data = doc_snap.to_dict()
        stored_channel = (data.get("channel") or "").strip()
        stored_chat_id = (data.get("chat_id") or "").strip()
        stored_title = (data.get("title") or "").strip()
        
        stored_key = get_channel_key(stored_channel)
        target_key = get_channel_key(target_input)
        
        if (
            stored_key == target_key or
            stored_channel.lower() == target_input.lower() or
            stored_chat_id.lower() == target_input.lower() or
            stored_title.lower() == target_input.lower()
        ):
            found_doc_id = doc_snap.id
            display_channel = stored_channel
            break
            
    # Qisman qidiruv (Inclusion check fallback)
    if not found_doc_id:
        for doc_snap in channels_snap:
            data = doc_snap.to_dict()
            stored_channel = (data.get("channel") or "").strip()
            if target_input.lower() in stored_channel.lower() or stored_channel.lower() in target_input.lower():
                found_doc_id = doc_snap.id
                display_channel = stored_channel
                break
                
    if found_doc_id:
        db.collection("channels").document(found_doc_id).delete()
        await state.clear()
        await message.answer(f"❌ Kanal muvaffaqiyatli o'chirildi: {display_channel}", reply_markup=get_admin_keyboard())
        await send_auto_backup(bot)
    else:
        await state.clear()
        await message.answer(
            f"❌ Bunday kanal topilmadi: {target_input}\n\nQayta urinib ko'ring.",
            reply_markup=get_admin_keyboard()
        )


# Bitta kino qo'shish start
@dp.message(F.text == "🎬 Kino qo‘shish")
async def add_movie_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.one_video)
    await message.answer("Kino videosini yoki faylini (document) yuboring:")


# Kino fayli/videosini qabul qilish
@dp.message(BotStates.one_video)
async def add_movie_file(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    file_id = None
    file_type = None
    
    if message.video:
        file_id = message.video.file_id
        file_type = "video"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
    else:
        await message.answer("Iltimos, faqat video yoki fayl yuboring:")
        return
        
    await state.update_data(file_id=file_id, file_type=file_type)
    await state.set_state(BotStates.one_code)
    await message.answer("Kino kodini kiriting:")


# Kino kodini qabul qilish
@dp.message(BotStates.one_code)
async def add_movie_code(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    if not message.text:
        await message.answer("Kino kodini faqat matn ko'rinishida yuboring:")
        return
        
    code = message.text.strip()
    await state.update_data(code=code)
    await state.set_state(BotStates.one_name)
    await message.answer("Kino nomini kiriting:")


# Kino nomini qabul qilish va saqlash
@dp.message(BotStates.one_name)
async def add_movie_name(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    if not message.text:
        await message.answer("Kino nomini faqat matn ko'rinishida yuboring:")
        return
        
    name = message.text.strip()
    data = await state.get_data()
    file_id = data.get("file_id")
    file_type = data.get("file_type")
    code = data.get("code")
    
    db.collection("movies").document(code).set({
        "code": code,
        "file_id": file_id,
        "file_type": file_type,
        "name": name,
        "views": 0
    })
    
    await state.clear()
    html_msg = (
        f"🎉 <b>Kino muvaffaqiyatli saqlandi!</b>\n\n"
        f"🔑 <b>Kodi:</b> <code>{code}</code>\n"
        f"🎬 <b>Nomi:</b> <i>{name}</i>\n"
        f"📁 <b>Fayl turi:</b> { '🎬 Video' if file_type == 'video' else '📂 Fayl/Hujjat'}\n\n"
        f"Foydalanuvchilar ushbu kodni yuborib kinoni ko'rishlari mumkin."
    )
    await message.answer(html_msg, reply_markup=get_admin_keyboard(), parse_mode="HTML")
    await send_auto_backup(bot)


# Ommaviy kino qo'shish start
@dp.message(F.text == "📦 Ommaviy qo‘shish")
async def bulk_add_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.batch_codes)
    await message.answer("Kodlarni probel bilan yuboring (Masalan: 101 102 103):")


# Ommaviy kodlarni qabul qilish
@dp.message(BotStates.batch_codes)
async def bulk_add_codes(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    if not message.text:
        await message.answer("Iltimos, kodlarni faqat raqamlar ko'rinishida probel bilan yuboring:")
        return
        
    codes = re.findall(r"\d+", message.text)
    if not codes:
        await message.answer("Raqamli kodlar topilmadi. Qaytadan kiriting:")
        return
        
    await state.update_data(codes=codes)
    await state.set_state(BotStates.batch_vids)
    await message.answer(f"✅ {len(codes)} ta kod qabul qilindi. Endi videolarni yoki fayllarni ketma-ket bittalab tashlang.")


# Ommaviy videolarni qabul qilib, birma-bir moslashtirish
@dp.message(BotStates.batch_vids)
async def bulk_add_videos(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    file_id = None
    file_type = None
    
    if message.video:
        file_id = message.video.file_id
        file_type = "video"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
    else:
        await message.answer("Iltimos, faqat video yoki fayl yuboring:")
        return
        
    data = await state.get_data()
    codes = data.get("codes", [])
    
    if codes:
        code = codes.pop(0)
        db.collection("movies").document(code).set({
            "code": code,
            "file_id": file_id,
            "file_type": file_type,
            "name": f"Kino {code}",
            "views": 0
        })
        
        await message.answer(f"🎉 <b>Kod [{code}] saqlandi!</b>\n📁 Turi: {'🎬 Video' if file_type == 'video' else '📂 Fayl'}", parse_mode="HTML")
        
        if codes:
            await state.update_data(codes=codes)
            await message.answer(f"Keyingi videoni tashlang. Yana {len(codes)} ta qoldi...")
        else:
            await state.clear()
            await message.answer("🎉 Barcha ommaviy kinolar muvaffaqiyatli saqlandi!", reply_markup=get_admin_keyboard())
            await send_auto_backup(bot)


# Kinoni o'chirish start
@dp.message(F.text == "🗑 Kino o‘chirish")
async def delete_movie_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.del_m)
    await message.answer("O'chiriladigan kino kodini kiriting:")


# Kinoni o'chirishni yakunlash
@dp.message(BotStates.del_m)
async def delete_movie_process(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    code = message.text.strip()
    db.collection("movies").document(code).delete()
    await state.clear()
    await message.answer(f"🗑 Kod [{code}] bo'lgan kino o'chirildi!", reply_markup=get_admin_keyboard())
    await send_auto_backup(bot)


# Reklama yuborish start
@dp.message(F.text == "📢 Reklama")
async def ad_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    users_snap = db.collection("users").get()
    await state.set_state(BotStates.wait_lim)
    await message.answer(f"📊 Jami foydalanuvchilar: {len(users_snap)}\nNechtasiga yuboramiz? (Hammasi uchun 0 yozing):")


# Reklama limitini qabul qilish
@dp.message(BotStates.wait_lim)
async def ad_limit(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    try:
        limit = int(message.text.strip())
        await state.update_data(limit=limit)
        await state.set_state(BotStates.wait_ad)
        await message.answer("Endi reklama xabarini yuboring (Matn, Rasm, Video yoki har qanday format):")
    except ValueError:
        await message.answer("Iltimos, faqat butun son kiriting:")


# Reklamani yuborish
@dp.message(BotStates.wait_ad)
async def ad_send(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    state_data = await state.get_data()
    limit = state_data.get("limit", 0)
    
    users_snap = db.collection("users").get()
    users = [d.to_dict().get("user_id") for d in users_snap if d.to_dict().get("user_id")]
    
    target_users = users if limit == 0 else users[:limit]
    
    await message.answer(f"🚀 {len(target_users)} ta foydalanuvchiga reklama yuborilmoqda...")
    
    success = 0
    fail = 0
    
    for u_id in target_users:
        try:
            # Aiogram v3 da copy_to orqali istalgan xabarni ko'chirish oson
            await message.copy_to(chat_id=u_id)
            success += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)  # Telegram Flood limitidan chetlanish uchun
        
    await state.clear()
    await message.answer(
        f"🏁 Reklama yakunlandi!\n✅ Yetkazildi: {success}\n❌ Yetkazilmadi: {fail}",
        reply_markup=get_admin_keyboard()
    )


# Backup olish (JSON ko'rinishida yuborish)
@dp.message(F.text == "📥 Backup olish")
async def get_backup_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    await message.answer("⏳ Zaxira fayli tayyorlanmoqda...")
    try:
        movies_ref = db.collection("movies").stream()
        channels_ref = db.collection("channels").stream()
        users_ref = db.collection("users").stream()
        
        movies_data = [d.to_dict() for d in movies_ref]
        channels_data = [d.to_dict() for d in channels_ref]
        users_data = [d.to_dict() for d in users_ref]
        
        movies_backup = {
            "movies": movies_data,
            "channels": channels_data
        }
        stats_backup = {
            "total_users": len(users_data),
            "users": users_data
        }
        
        now = int(datetime.datetime.now().timestamp())
        movies_fn = f"kinolar_backup_{now}.json"
        stats_fn = f"statistika_backup_{now}.json"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            movies_path = os.path.join(temp_dir, movies_fn)
            stats_path = os.path.join(temp_dir, stats_fn)
            
            with open(movies_path, "w", encoding="utf-8") as f:
                json.dump(movies_backup, f, indent=2, ensure_ascii=False)
                
            with open(stats_path, "w", encoding="utf-8") as f:
                json.dump(stats_backup, f, indent=2, ensure_ascii=False)
                
            movies_file = FSInputFile(movies_path, filename=movies_fn)
            stats_file = FSInputFile(stats_path, filename=stats_fn)
            
            await message.reply_document(
                movies_file,
                caption=f"📂 <b>Kinolar va Kanallar Zaxira Nusxasi</b>\n\n🎬 Kinolar jami: <b>{len(movies_data)}</b> ta\n📢 Kanallar jami: <b>{len(channels_data)}</b> ta",
                parse_mode="HTML"
            )
            
            await message.reply_document(
                stats_file,
                caption=f"📂 <b>Foydalanuvchilar va Statistika Zaxira Nusxasi</b>\n\n👥 Jami foydalanuvchilar (IDlar): <b>{len(users_data)}</b> ta",
                parse_mode="HTML"
            )
    except Exception as e:
        await message.answer(f"❌ Backup yaratishda xatolik: {e}")


# Backup yuklash start
@dp.message(F.text == "📤 Backup yuklash")
async def import_backup_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.import_backup)
    await message.answer("Iltimos, zaxira (backup) .json formatidagi faylni yuboring:")


# Backup yuklashni yakunlash
@dp.message(BotStates.import_backup)
async def import_backup_process(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    if not message.document:
        await message.answer("❌ Xato! Iltimos, faqat .json fayl yuboring:")
        return
        
    file_name = message.document.file_name
    if not file_name.endswith(".json"):
        await message.answer("❌ Xato! Iltimos, faqat .json kengaytmali fayl yuboring:")
        return
        
    await message.answer("⏳ Fayl yuklanmoqda va qayta ishlanmoqda...")
    
    try:
        file_info = await bot.get_file(message.document.file_id)
        # Faylni telegramdan yuklab olish
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        res = requests.get(file_url)
        backup_data = res.json()
        
        imported_movies = 0
        imported_channels = 0
        imported_users = 0
        
        # Kinolarni tiklash
        if "movies" in backup_data and isinstance(backup_data["movies"], list):
            for raw_item in backup_data["movies"]:
                item = extract_movie(raw_item)
                if item:
                    db.collection("movies").document(item["code"]).set({
                        "code": item["code"],
                        "file_id": item["file_id"],
                        "file_type": item["file_type"],
                        "name": item["name"],
                        "views": item["views"]
                    })
                    imported_movies += 1
                    
        # Kanallarni tiklash
        if "channels" in backup_data and isinstance(backup_data["channels"], list):
            for raw_item in backup_data["channels"]:
                channel_handle = extract_channel(raw_item)
                if channel_handle:
                    channel_id = "".join([c if c.isalnum() else "_" for c in channel_handle])
                    db.collection("channels").document(channel_id).set({
                        "channel": channel_handle,
                        "chat_id": channel_handle,
                        "title": channel_handle,
                        "type": "public" # Sukut bo'yicha public
                    })
                    imported_channels += 1
                    
        # Foydalanuvchilarni tiklash
        if "users" in backup_data and isinstance(backup_data["users"], list):
            for item in backup_data["users"]:
                if item and isinstance(item, dict):
                    user_id_field = item.get("user_id") or item.get("userId") or item.get("id")
                    if user_id_field:
                        u_id = str(user_id_field)
                        db.collection("users").document(u_id).set({
                            "user_id": u_id,
                            "username": item.get("username") or item.get("user_name") or "NoName"
                        })
                        imported_users += 1
                        
        await state.clear()
        await message.answer(
            f"✅ Backup muvaffaqiyatli tiklandi!\n\n"
            f"📥 Tiklangan ma'lumotlar:\n"
            f"🎬 Kinolar: {imported_movies}\n"
            f"📢 Kanallar: {imported_channels}\n"
            f"👥 Foydalanuvchilar: {imported_users}",
            reply_markup=get_admin_keyboard()
        )
        await send_auto_backup(bot)
    except Exception as e:
        logger.error(f"Backup yuklash xatoligi: {e}")
        await message.answer(f"❌ Faylni yuklash yoki o'qishda xatolik: {e}")


# ------------------ KANALLARGA KINO REJALASHTIRISH (SCHEDULER) ------------------

# Kanallarga yuklash start
@dp.message(F.text == "📢 Kanallarga yuklash")
async def schedule_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.sch_channel)
    welcome_sch_msg = (
        f"📢 <b>KANALLARGA KINO REJALASHTIRISH</b>\n\n"
        f"Kino tashlamoqchi bo'lgan kanal linkini yoki foydalanish nomini yuboring:\n"
        f"Masalan: <code>@uz_kinolar</code> yoki <code>https://t.me/uz_kinolar</code>\n\n"
        f"<i>To'xtatish yoki yakunlash uchun istalgan vaqtda <b>/buldi</b> buyrug'ini yuboring.</i>"
    )
    await message.answer(welcome_sch_msg, parse_mode="HTML")


# Kanal nomini qabul qilish
@dp.message(BotStates.sch_channel)
async def schedule_channel(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    if not message.text:
        await message.answer("Iltimos, kanal havolasini yozing:")
        return
        
    await state.update_data(channel=message.text.strip())
    await state.set_state(BotStates.sch_media)
    await message.answer("🎬 Zo'r! Endi ushbu kanalga tashlamoqchi bo'lgan kino videosini yoki faylini (document) yuboring:")


# Media faylini qabul qilish
@dp.message(BotStates.sch_media)
async def schedule_media(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    file_id = None
    file_type = None
    
    if message.video:
        file_id = message.video.file_id
        file_type = "video"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
    else:
        await message.answer("Iltimos, faqat video yoki fayl yuboring:")
        return
        
    await state.update_data(file_id=file_id, file_type=file_type)
    await state.set_state(BotStates.sch_name)
    await message.answer("📝 Kino nomini (ta'rif matnini) kiriting:")


# Kino nomini qabul qilish
@dp.message(BotStates.sch_name)
async def schedule_name(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    if not message.text:
        await message.answer("Iltimos, kino nomini yuboring:")
        return
        
    await state.update_data(name=message.text.strip())
    await state.set_state(BotStates.sch_start_time)
    
    today = datetime.datetime.now()
    sample_time = today.strftime("%d.%m.%Y %H:%M")
    
    await message.answer(
        f"📅 <b>Hozirgi sana va soatni kiriting.</b>\n\n"
        f"Sizning hozirgi vaqtingizni belgilaymiz. Format: <code>kun.oy.yil soat:daqiqa</code>\n\n"
        f"Masalan: <code>{sample_time}</code>",
        parse_mode="HTML"
    )


# Hozirgi Uzbekiston vaqtini qabul qilish
@dp.message(BotStates.sch_start_time)
async def schedule_start_time(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    parsed_date = parse_uzbek_date_time(message.text)
    if not parsed_date:
        await message.answer("❌ <b>Sana formati noto'g'ri!</b>\n\nIltimos, to'g'ri kiriting (kun.oy.yil soat:daqiqa):", parse_mode="HTML")
        return
        
    await state.update_data(start_time_str=message.text.strip(), start_time_iso=parsed_date.isoformat())
    await state.set_state(BotStates.sch_target_time)
    
    tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
    sample_target = tomorrow.strftime("%d.%m.%Y 16:00")
    
    await message.answer(
        f"📅 <b>Endi esa ushbu kino kanalga yuklanadigan (tashlanadigan) sana va soatni kiriting.</b>\n\n"
        f"Format: <code>kun.oy.yil soat:daqiqa</code> bo'lishi kerak.\n\n"
        f"Masalan: <code>{sample_target}</code>",
        parse_mode="HTML"
    )


# Maqsad qilingan tashlash vaqtini qabul qilish va saqlash
@dp.message(BotStates.sch_target_time)
async def schedule_target_time(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    parsed_target = parse_uzbek_date_time(message.text)
    if not parsed_target:
        await message.answer("❌ <b>Tashlanadigan vaqt formati noto'g'ri!</b>\n\nQayta urinib ko'ring (kun.oy.yil soat:daqiqa):", parse_mode="HTML")
        return
        
    state_data = await state.get_data()
    sch_id = f"sch_{int(datetime.datetime.now().timestamp() * 1000)}"
    
    db.collection("scheduled_posts").document(sch_id).set({
        "id": sch_id,
        "channel": state_data.get("channel"),
        "file_id": state_data.get("file_id"),
        "file_type": state_data.get("file_type"),
        "name": state_data.get("name"),
        "start_time": state_data.get("start_time_iso"),
        "scheduled_at": parsed_target.isoformat() + "Z", # JavaScript bilan bir xil UTC ko'rsatgich
        "processed": False,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z"
    })
    
    # Holatni tozalab yana kanal bosqichiga qaytaramiz (ketma-ket rejalashtirish oson bo'lishi uchun)
    await state.set_state(BotStates.sch_channel)
    
    success_msg = (
        f"✅ <b>Kino muvaffaqiyatli rejalashtirildi!</b>\n\n"
        f"📢 <b>Kanal:</b> <code>{state_data.get('channel')}</code>\n"
        f"🎬 <b>Kino nomi:</b> <i>{state_data.get('name')}</i>\n"
        f"🕒 <b>Hozirgi vaqt deb olindi:</b> <code>{state_data.get('start_time_str')}</code>\n"
        f"📅 <b>Kanalga tashlanadigan vaqt:</b> <code>{message.text.strip()}</code>\n\n"
        f"<i>Yana qo'shmoqchi bo'lsangiz, keyingi kanal linkini yuboring. To'xtatish uchun <b>/buldi</b> yuboring.</i>"
    )
    await message.answer(success_msg, parse_mode="HTML")


# ------------------ FOYDALANUVCHILAR VA KINO IZLASH BO'LIMI ------------------

# Tasodifiy kino yuborish
@dp.message(F.text == "🎲 Tasodifiy kino")
async def random_movie_handler(message: types.Message):
    user_id = message.from_user.id
    
    # Admin bo'lmaganlar uchun obunani tekshirish
    if user_id != ADMIN_ID:
        not_joined, all_channels = await check_subscription(bot, user_id)
        if not_joined:
            beautiful_text = (
                f"👋 <b>Assalomu alaykum!</b>\n\n"
                f"🤖 Botdan bepul foydalanish uchun quyidagi homiy kanallariga obuna bo'lishingiz majburiydir:\n\n"
                f"<i>👇 Quyidagi kanal tugmalarini bosing va a'zo bo'ling:</i>"
            )
            await message.answer(beautiful_text, reply_markup=get_sub_inline_keyboard(all_channels), parse_mode="HTML")
            return
            
    # Barcha kinolarni olish va bittasini tanlash
    movies_snap = db.collection("movies").get()
    if not movies_snap:
        await message.answer("😔 Hozircha ma'lumotlar bazasida kinolar mavjud emas.")
        return
        
    import random
    doc_choice = random.choice(movies_snap)
    movie = doc_choice.to_dict()
    
    code = movie.get("code")
    # Ko'rishlar sonini bittaga oshirish
    db.collection("movies").document(code).update({"views": firestore.Increment(1)})
    
    views = get_amplified_views((movie.get("views", 0)) + 1, code)
    caption_msg = (
        f"🎲 <b>TASODIFIY KINO</b>\n\n"
        f"🎬 <b>Nomi:</b> <i>{movie.get('name')}</i>\n"
        f"🔑 <b>Kod:</b> <code>{code}</code>\n\n"
        f"👁 Ko'rildi: <b>{views}</b> marta"
    )
    
    if movie.get("file_type") == "document":
        await message.answer_document(movie.get("file_id"), caption=caption_msg, parse_mode="HTML")
    else:
        await message.answer_video(movie.get("file_id"), caption=caption_msg, parse_mode="HTML")


# Top-10 kinolarni yuborish
@dp.message(F.text == "🔥 Top kinolar")
async def top_movies_user_handler(message: types.Message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        not_joined, all_channels = await check_subscription(bot, user_id)
        if not_joined:
            beautiful_text = (
                f"👋 <b>Assalomu alaykum!</b>\n\n"
                f"🤖 Botdan bepul foydalanish uchun quyidagi homiy kanallariga obuna bo'lishingiz majburiydir:\n\n"
                f"<i>👇 Quyidagi kanal tugmalarini bosing va a'zo bo'ling:</i>"
            )
            await message.answer(beautiful_text, reply_markup=get_sub_inline_keyboard(all_channels), parse_mode="HTML")
            return
            
    movies_snap = db.collection("movies").get()
    movies_list = [d.to_dict() for d in movies_snap]
    movies_list.sort(key=lambda x: get_amplified_views(x.get("views", 0), x.get("code", "")), reverse=True)
    top_movies = movies_list[:10]
    
    top_movies_text = ""
    if not top_movies:
        top_movies_text = "<i>❌ Hozircha kinolar yo'q</i>"
    else:
        for idx, m in enumerate(top_movies):
            code = m.get("code")
            name = m.get("name")
            views = get_amplified_views(m.get("views", 0), code)
            top_movies_text += f"<b>👉 {idx + 1}.</b> <code>{code}</code> — <b>{name}</b> (👁 {views} marta)\n"
            
    html_msg = (
        f"🔥 <b>ENG KO'P KO'RILGAN TOP KINOLAR</b>\n\n"
        f"<i>Kino kodlari ustiga bosib, ularni nusxalab oling va botga yuboring!</i>\n\n"
        f"{top_movies_text}"
    )
    
    await message.answer(html_msg, reply_markup=get_user_keyboard(), parse_mode="HTML")


# Kod orqali kinoni qidirish (Barcha matnli xabarlar uchun)
@dp.message()
async def movie_lookup_handler(message: types.Message):
    user_id = message.from_user.id
    text = message.text
    
    if not text:
        return
        
    # Majburiy obunani faqat oddiy foydalanuvchilar uchun tekshiramiz
    if user_id != ADMIN_ID:
        not_joined, all_channels = await check_subscription(bot, user_id)
        if not_joined:
            beautiful_text = (
                f"👋 <b>Assalomu alaykum!</b>\n\n"
                f"🤖 Botdan bepul foydalanish uchun quyidagi homiy kanallariga obuna bo'lishingiz majburiydir:\n\n"
                f"<i>👇 Quyidagi kanal tugmalarini bosing va a'zo bo'ling:</i>"
            )
            await message.answer(beautiful_text, reply_markup=get_sub_inline_keyboard(all_channels), parse_mode="HTML")
            return
            
    # Kino kodini Firestore dan qidirish
    movie_doc = db.collection("movies").document(text.strip()).get()
    
    if movie_doc.exists:
        movie = movie_doc.to_dict()
        code = movie.get("code")
        
        # Ko'rishlar sonini oshirish
        db.collection("movies").document(code).update({"views": firestore.Increment(1)})
        
        views = get_amplified_views((movie.get("views", 0)) + 1, code)
        caption_msg = f"🎬 <b>{movie.get('name')}</b>\n\n👁 Ko'rildi: <b>{views}</b> marta"
        
        reply_markup = get_admin_keyboard() if user_id == ADMIN_ID else get_user_keyboard()
        
        if movie.get("file_type") == "document":
            await message.answer_document(movie.get("file_id"), caption=caption_msg, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await message.answer_video(movie.get("file_id"), caption=caption_msg, reply_markup=reply_markup, parse_mode="HTML")
    else:
        # Agar admin bo'lsa va topilmasa jim qolamiz (admin panel buyruqlariga xalaqit bermaslik uchun)
        if user_id != ADMIN_ID:
            await message.answer(
                "❌ Bunday kodli kino topilmadi. Qaytadan tekshirib ko'ring yoki boshqa kod yozing.",
                reply_markup=get_user_keyboard()
            )


# ------------------ REJALASHTIRILGAN XABARLARNI TEKSHIRISH (FONDA) ------------------
async def check_scheduled_posts():
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            # Processed bo'lmagan rejalarni olish
            query_snap = db.collection("scheduled_posts").where("processed", "==", False).stream()
            
            for doc in query_snap:
                post = doc.to_dict()
                scheduled_at_str = post.get("scheduled_at")
                if not scheduled_at_str:
                    continue
                    
                try:
                    # ISO formatni UTC datetime ob'ektiga o'girish
                    clean_ts = scheduled_at_str.replace("Z", "+00:00")
                    scheduled_at = datetime.datetime.fromisoformat(clean_ts)
                except Exception:
                    continue
                    
                if scheduled_at <= now:
                    logger.info(f"Rejalashtirilgan kino jo'natilmoqda: {post.get('id')} -> {post.get('channel')}")
                    
                    channel_id = post.get("channel", "").strip()
                    if channel_id.startswith("https://t.me/"):
                        parts = channel_id.split("/")
                        last = parts[-1]
                        if last and not last.startswith("+"):
                            channel_id = f"@{last}"
                            
                    caption_msg = f"🎬 <b>{post.get('name')}</b>\n\n👁 Ko'rildi: {get_amplified_views(0, post.get('id'))}"
                    
                    try:
                        # Raqamli kanallar IDsi bo'lsa int formatga o'tkazish
                        try:
                            target_chat = int(channel_id) if (channel_id.startswith("-") or channel_id.isdigit()) else channel_id
                        except ValueError:
                            target_chat = channel_id
                            
                        if post.get("file_type") == "document":
                            await bot.send_document(chat_id=target_chat, document=post.get("file_id"), caption=caption_msg, parse_mode="HTML")
                        else:
                            await bot.send_video(chat_id=target_chat, video=post.get("file_id"), caption=caption_msg, parse_mode="HTML")
                            
                        # Muvaffaqiyatli jo'natilgach yangilash
                        db.collection("scheduled_posts").document(post.get("id")).update({"processed": True})
                        logger.info(f"Kino muvaffaqiyatli kanalga tashlandi: {post.get('id')}")
                    except Exception as err:
                        logger.error(f"Rejalashtirilgan kinoni kanalga tashlashda xatolik: {err}")
                        db.collection("scheduled_posts").document(post.get("id")).update({
                            "processed": True,
                            "error": str(err)
                        })
        except Exception as global_err:
            logger.error(f"Scheduler ishlash xatoligi: {global_err}")
            
        await asyncio.sleep(60) # Har minutda bir marta tekshirish


# ------------------ ASOSIY ISHGA TUSHIRISH (MAIN) ------------------
async def main():
    logger.info("Botni ishga tushirish jarayoni boshlandi...")
    
    # Agar webhook o'rnatilgan bo'lsa uni tozalaymiz
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Fondagi rejalashtiruvchini (Scheduler) parallel ishga tushiramiz
    asyncio.create_task(check_scheduled_posts())
    
    # Pollingni boshlash
    logger.info("Bot polling rejimi faol. Yangilanishlarni qabul qilish boshlandi.")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_join_request"])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot faoliyati to'xtatildi.")
