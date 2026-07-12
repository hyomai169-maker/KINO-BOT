# -*- coding: utf-8 -*-
import os
import re
import json
import math
import asyncio
import logging
import datetime
import tempfile
from typing import Any, Dict, List, Optional, Tuple

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
firebase_service_account_path = "firebase-service-account.json"

if os.path.exists(firebase_service_account_path):
    cred = credentials.Certificate(firebase_service_account_path)
    firebase_admin.initialize_app(cred)
    logger.info("Firebase Admin SDK muvaffaqiyatli ishga tushirildi (Service Account orqali).")
else:
    try:
        if os.path.exists("./firebase-applet-config.json"):
            with open("./firebase-applet-config.json", "r", encoding="utf-8") as f:
                web_config = json.load(f)
            firebase_admin.initialize_app()
            logger.info("Firebase Admin SDK standart muhit ma'lumotlari orqali ishga tushirildi.")
        else:
            raise FileNotFoundError("Hech qanday Firebase sozlamalari topilmadi.")
    except Exception as e:
        logger.error(f"Firebase init xatoligi: {e}. Iltimos 'firebase-service-account.json' faylini yuklang.")
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
    add_ch = State()
    add_ch_link = State()
    del_ch = State()
    one_video = State()
    one_code = State()
    one_name = State()
    batch_codes = State()
    batch_vids = State()
    del_m = State()
    wait_lim = State()
    wait_ad = State()
    import_backup = State()
    sch_channel = State()
    sch_media = State()
    sch_name = State()
    sch_start_time = State()
    sch_target_time = State()


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


# Ko'rishlar sonini vizual oshirish formulasi
def get_amplified_views(views_count: int, code: str) -> int:
    views = views_count or 0
    code_str = str(code)
    
    hash_val = 0
    for char in code_str:
        hash_val = ord(char) + ((hash_val << 5) - hash_val)
        hash_val = hash_val & 0xFFFFFFFF
        
    base = abs(hash_val % 1260) + 240
    
    today = datetime.date.today()
    week_num = math.ceil(today.day / 7) + today.month + 1
    week_factor = 1 + (week_num * 0.05)
    
    return math.floor((base + (views * 8)) * week_factor)


# Kanal tozalash kaliti
def get_channel_key(channel_str: str) -> str:
    if not channel_str:
        return ""
    s = channel_str.strip().lower()
    s = re.sub(r"^(https?://)?(www\.)?(t\.me/joinchat/|t\.me/|telegram\.me/joinchat/|telegram\.me/)", "", s)
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


# Uzbek vaqt formatini parse qilish
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
                
            movies_file = FSInputFile(movies_path, filename=movies_fn)
            stats_file = FSInputFile(stats_path, filename=stats_fn)
            
            caption_movies = (
                f"🎬 <b>AVTOMATIK KINOLAR VA KANALLAR ZAXIRASI</b>\n\n"
                f"Ma'lumotlar bazasida o'zgarish bo'ldi.\n\n"
                f"📊 <b>Hozirgi holat:</b>\n"
                f"🎬 Jami kinolar: <b>{len(movies_data)}</b> ta\n"
                f"📢 Jami kanallar: <b>{len(channels_data)}</b> ta\n\n"
                f"<i>Kinolar va Kanallarni tiklash uchun ushbu fayldan foydalaning!</i>"
            )
            
            caption_stats = (
                f"👥 <b>AVTOMATIK FOYDALANUVCHILAR VA STATISTIKA ZAXIRASI</b>\n\n"
                f"Foydalanuvchilar zaxira nusxasi.\n\n"
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
async def check_subscription(bot_instance: Bot, user_id: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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
                chat_target = channel_id
                if isinstance(chat_target, str) and (chat_target.startswith("-100") or chat_target.isdigit()):
                    chat_target = int(chat_target)
                
                member = await bot_instance.get_chat_member(chat_id=chat_target, user_id=user_id)
                if member.status not in ["member", "administrator", "creator"]:
                    not_joined.append(ch_item)
            except Exception as e:
                logger.warning(f"Bot ommaviy kanalda a'zolikni tekshira olmadi {channel_id}: {e}")
        else:
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

@dp.message(Command("buldi", "stop"))
async def cancel_handler(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    await message.answer("👌 Tushundim. Barcha harakatlar to'xtatildi.", reply_markup=get_admin_keyboard())


@dp.message(Command("start"))
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "NoName"
    
    db.collection("users").document(str(user_id)).set({
        "user_id": str(user_id),
        "username": username
    }, merge=True)
    
    if user_id == ADMIN_ID:
        await message.answer("🔥 ADMIN PANEL", reply_markup=get_admin_keyboard())
        return
        
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


@dp.chat_join_request()
async def chat_join_request_handler(update: types.ChatJoinRequest):
    try:
        user_id = update.from_user.id
        chat_id = str(update.chat.id)
        
        logger.info(f"Yangi chat qo'shilish so'rovi: User {user_id} -> {chat_id}")
        
        cleaned_chat_id = "".join([c if c.isalnum() else "_" for c in chat_id])
        req_id = f"{cleaned_chat_id}_{user_id}"
        
        db.collection("join_requests").document(req_id).set({
            "chat_id": chat_id,
            "user_id": str(user_id),
            "approved": False,
            "timestamp": datetime.datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Chat join request xatosi: {e}")


# ------------------ ADMIN PANEL BO'LIMI ------------------

@dp.message(F.text == "📊 Statistika")
async def stats_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    users_snap = db.collection("users").get()
    movies_snap = db.collection("movies").get()
    channels_snap = db.collection("channels").get()
    
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


@dp.message(F.text == "📢 Kanal qo‘shish")
async def add_channel_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.add_ch)
    info_msg = (
        f"📢 <b>KANAL QO'SHISH</b>\n\n"
        f"Kanal qo'shish usulini tanlang:\n\n"
        f"1. 🌐 <b>Ommaviy kanal:</b> havola yozing yoki @username yuboring.\n"
        f"2. 🔒 <b>Maxfiy (yopiq) kanal:</b> ushbu kanaldagi biror bir xabarni botga <b>FORWARD</b> qilib yuboring!"
    )
    await message.answer(info_msg, parse_mode="HTML")


@dp.message(BotStates.add_ch)
async def add_channel_process(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    if message.forward_from_chat:
        chat_id = message.forward_from_chat.id
        title = message.forward_from_chat.title or "Maxfiy Kanal"
        await state.update_data(chat_id=str(chat_id), title=title)
        await state.set_state(BotStates.add_ch_link)
        await message.answer(f"🔒 <b>Maxfiy kanal aniqlandi!</b>\n\nNomi: <b>{title}</b>\nID: <code>{chat_id}</code>\n\nTaklif havolasini yuboring:", parse_mode="HTML")
        return
        
    if message.text:
        text = message.text.strip()
        channel_user = text
        if channel_user.startswith("http://") or channel_user.startswith("https://"):
            parts = channel_user.split("/")
            last_part = parts[-1]
            if last_part: channel_user = f"@{last_part}"
        elif not channel_user.startswith("@"):
            channel_user = f"@{channel_user}"
            
        channel_id = "".join([c if c.isalnum() else "_" for c in channel_user])
        db.collection("channels").document(channel_id).set({"channel": channel_user, "chat_id": channel_user, "title": channel_user, "type": "public"})
        
        await state.clear()
        await message.answer(f"✅ <b>Kanal muvaffaqiyatli qo'shildi!</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")
        await send_auto_backup(bot)


@dp.message(BotStates.add_ch_link)
async def add_channel_private_link(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    invite_link = message.text.strip()
    data = await state.get_data()
    chat_id = data.get("chat_id")
    title = data.get("title")
    channel_id = "".join([c if c.isalnum() else "_" for c in str(chat_id)])
    
    db.collection("channels").document(channel_id).set({"channel": invite_link, "chat_id": chat_id, "title": title, "type": "private"})
    await state.clear()
    await message.answer(f"✅ <b>Maxfiy kanal qo'shildi!</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")
    await send_auto_backup(bot)


@dp.message(F.text == "❌ Kanal o‘chirish")
async def delete_channel_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    channels_snap = db.collection("channels").get()
    if not channels_snap:
        await message.answer("Kanallar yo'q.")
        return
    await state.set_state(BotStates.del_ch)
    lst = "O'chiriladigan kanalni yuboring:\n\n"
    for doc in channels_snap:
        lst += f"- <code>{doc.to_dict().get('channel')}</code>\n"
    await message.answer(lst, parse_mode="HTML")


@dp.message(BotStates.del_ch)
async def delete_channel_process(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    target_input = message.text.strip()
    channels_snap = db.collection("channels").get()
    found_doc_id = None
    
    for doc_snap in channels_snap:
        data = doc_snap.to_dict()
        if (data.get("channel") or "").strip().lower() == target_input.lower():
            found_doc_id = doc_snap.id
            break
            
    if found_doc_id:
        db.collection("channels").document(found_doc_id).delete()
        await state.clear()
        await message.answer(f"❌ Kanal o'chirildi.", reply_markup=get_admin_keyboard())
        await send_auto_backup(bot)
    else:
        await state.clear()
        await message.answer("❌ Bunday kanal topilmadi.", reply_markup=get_admin_keyboard())


@dp.message(F.text == "🎬 Kino qo‘shish")
async def add_movie_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.one_video)
    await message.answer("Kino videosini yuboring:")


@dp.message(BotStates.one_video)
async def add_movie_file(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    file_id = message.video.file_id if message.video else (message.document.file_id if message.document else None)
    if not file_id:
        await message.answer("Faqat video yoki fayl yuboring:")
        return
    await state.update_data(file_id=file_id, file_type="video" if message.video else "document")
    await state.set_state(BotStates.one_code)
    await message.answer("Kino kodini kiriting:")


@dp.message(BotStates.one_code)
async def add_movie_code(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.update_data(code=message.text.strip())
    await state.set_state(BotStates.one_name)
    await message.answer("Kino nomini kiriting:")


@dp.message(BotStates.one_name)
async def add_movie_name(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    data = await state.get_data()
    db.collection("movies").document(data.get("code")).set({
        "code": data.get("code"),
        "file_id": data.get("file_id"),
        "file_type": data.get("file_type"),
        "name": message.text.strip(),
        "views": 0
    })
    await state.clear()
    await message.answer("✅ Kino saqlandi!", reply_markup=get_admin_keyboard())
    await send_auto_backup(bot)


@dp.message(F.text == "📦 Ommaviy qo‘shish")
async def bulk_add_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.batch_codes)
    await message.answer("Kodlarni probel bilan yuboring:")


@dp.message(BotStates.batch_codes)
async def bulk_add_codes(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    codes = re.findall(r"\d+", message.text)
    await state.update_data(codes=codes)
    await state.set_state(BotStates.batch_vids)
    await message.answer(f"✅ {len(codes)} ta kod qabul qilindi. Videolarni ketma-ket yuboring.")


@dp.message(BotStates.batch_vids)
async def bulk_add_videos(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    file_id = message.video.file_id if message.video else (message.document.file_id if message.document else None)
    data = await state.get_data()
    codes = data.get("codes", [])
    if codes:
        code = codes.pop(0)
        db.collection("movies").document(code).set({"code": code, "file_id": file_id, "file_type": "video" if message.video else "document", "name": f"Kino {code}", "views": 0})
        if codes:
            await state.update_data(codes=codes)
            await message.answer(f"Keyingi videoni tashlang. Yana {len(codes)} ta qoldi...")
        else:
            await state.clear()
            await message.answer("✅ Barcha kinolar saqlandi!", reply_markup=get_admin_keyboard())
            await send_auto_backup(bot)


@dp.message(F.text == "🗑 Kino o‘chirish")
async def delete_movie_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.del_m)
    await message.answer("O'chiriladigan kino kodini kiriting:")


@dp.message(BotStates.del_m)
async def delete_movie_process(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    code = message.text.strip()
    db.collection("movies").document(code).delete()
    await state.clear()
    await message.answer("🗑 O'chirildi!", reply_markup=get_admin_keyboard())
    await send_auto_backup(bot)


@dp.message(F.text == "📢 Reklama")
async def ad_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.wait_lim)
    await message.answer("Nechtasiga yuboramiz? (Hammasi uchun 0):")


@dp.message(BotStates.wait_lim)
async def ad_limit(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.update_data(limit=int(message.text.strip()))
    await state.set_state(BotStates.wait_ad)
    await message.answer("Endi reklama xabarini yuboring:")


@dp.message(BotStates.wait_ad)
async def ad_send(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    state_data = await state.get_data()
    limit = state_data.get("limit", 0)
    users = [d.to_dict().get("user_id") for d in db.collection("users").get()]
    target = users if limit == 0 else users[:limit]
    for u_id in target:
        try: await message.copy_to(chat_id=u_id)
        except: continue
        await asyncio.sleep(0.05)
    await state.clear()
    await message.answer("🏁 Reklama yakunlandi!", reply_markup=get_admin_keyboard())


@dp.message(F.text == "📥 Backup olish")
async def get_backup_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    movies_data = [d.to_dict() for d in db.collection("movies").stream()]
    channels_data = [d.to_dict() for d in db.collection("channels").stream()]
    
    now = int(datetime.datetime.now().timestamp())
    fn = f"backup_{now}.json"
    with open(fn, "w", encoding="utf-8") as f:
        json.dump({"movies": movies_data, "channels": channels_data}, f, ensure_ascii=False)
    await message.reply_document(FSInputFile(fn))
    os.remove(fn)


@dp.message(F.text == "📤 Backup yuklash")
async def import_backup_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.import_backup)
    await message.answer("Faylni yuboring:")


@dp.message(BotStates.import_backup)
async def import_backup_process(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID or not message.document:
        return
    file_info = await bot.get_file(message.document.file_id)
    res = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}")
    data = res.json()
    for m in data.get("movies", []):
        db.collection("movies").document(m["code"]).set(m)
    await state.clear()
    await message.answer("✅ Backup tiklandi!", reply_markup=get_admin_keyboard())


@dp.message(F.text == "🎲 Tasodifiy kino")
async def random_movie_handler(message: types.Message):
    import random
    movies_snap = db.collection("movies").get()
    if not movies_snap: return
    movie = random.choice(movies_snap).to_dict()
    caption = f"🎬 {movie.get('name')}\n🔑 {movie.get('code')}"
    if movie.get("file_type") == "document": await message.answer_document(movie.get("file_id"), caption=caption)
    else: await message.answer_video(movie.get("file_id"), caption=caption)


@dp.message()
async def movie_lookup_handler(message: types.Message):
    movie_doc = db.collection("movies").document(message.text.strip()).get()
    if movie_doc.exists:
        movie = movie_doc.to_dict()
        db.collection("movies").document(movie.get("code")).update({"views": firestore.Increment(1)})
        caption = f"🎬 {movie.get('name')}"
        if movie.get("file_type") == "document": await message.answer_document(movie.get("file_id"), caption=caption)
        else: await message.answer_video(movie.get("file_id"), caption=caption)

async def check_scheduled_posts():
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            query_snap = db.collection("scheduled_posts").where("processed", "==", False).stream()
            for doc in query_snap:
                post = doc.to_dict()
                if datetime.datetime.fromisoformat(post["scheduled_at"].replace("Z", "+00:00")) <= now:
                    try:
                        if post.get("file_type") == "document": await bot.send_document(post.get("channel"), post.get("file_id"))
                        else: await bot.send_video(post.get("channel"), post.get("file_id"))
                        db.collection("scheduled_posts").document(post.get("id")).update({"processed": True})
                    except: pass
        except: pass
        await asyncio.sleep(60)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(check_scheduled_posts())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
