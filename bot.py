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

# Telegram Bot kutubxonalari
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import FSInputFile, ReplyKeyboardMarkup, InlineKeyboardMarkup
import requests

# SQLite 3 ma'lumotlar bazasi klassi
from database import Database

# Jurnal yuritishni sozlash (Logging)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Muhit o'zgaruvchilari (Environment Variables) yoki sukutiy qiymatlar
BOT_TOKEN = os.getenv("BOT_TOKEN", "8715391910:AAGAGsm9Y9kBi-ZXzavWEFwB84FseVAiq0A")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5775388579"))

# SQLite bazasini ishga tushirish
db = Database()
logger.info("SQLite 3 ma'lumotlar bazasi muvaffaqiyatli ishga tushirildi.")

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
        movies_data = db.get_movies()
        channels_data = db.get_channels()
        users_data = db.get_users()
        
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
async def check_subscription(bot_instance: Bot, user_id: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    channels_list = db.get_channels()
    not_joined = []
    all_channels = []
    
    for data in channels_list:
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
                # SQLite-dagi join_requests-larni tekshirish
                try:
                    cleaned_chat_id = "".join([c if c.isalnum() else "_" for c in str(channel_id)])
                    req_id = f"{cleaned_chat_id}_{user_id}"
                    req_row = db.get_join_request(req_id)
                    if req_row:
                        has_subscribed = True
                except Exception as db_err:
                    logger.error(f"SQLite join_requests qidirish xatosi: {db_err}")
                    
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
    
    # Userni SQLite ga saqlash
    db.save_user(str(user_id), username)
    
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
        
        # SQLite-ga pending request sifatida yozish
        db.save_join_request(
            id=req_id,
            chat_id=chat_id,
            user_id=str(user_id),
            approved=False,
            timestamp=datetime.datetime.now().isoformat()
        )
        logger.info(f"Join request SQLite-ga yozildi: {req_id}")
    except Exception as e:
        logger.error(f"Chat join request xatosi: {e}")


# Kino qidirish (Raqamli kod kiritilganda)
@dp.message(F.text.regexp(r"^\d+$"))
async def movie_search_handler(message: types.Message):
    user_id = message.from_user.id
    
    # Avvalo obunani tekshirish (Admin bo'lmasa)
    if user_id != ADMIN_ID:
        not_joined, all_channels = await check_subscription(bot, user_id)
        if not_joined:
            beautiful_text = (
                f"👋 <b>Botdan foydalanish uchun kanallarga a'zo bo'ling!</b>\n\n"
                f"<i>A'zo bo'lgach, 'Obuna bo'ldim' tugmasini bosing va kodni qayta yuboring.</i>"
            )
            await message.answer(beautiful_text, reply_markup=get_sub_inline_keyboard(all_channels), parse_mode="HTML")
            return
            
    code = message.text.strip()
    movie = db.get_movie(code)
    
    if not movie:
        await message.reply(
            f"❌ <b>Kino topilmadi!</b>\n\n"
            f"Keltirilgan <code>{code}</code> kod ostida hech qanday kino topilmadi. Kodni tekshirib qaytadan yuboring.",
            parse_mode="HTML"
        )
        return
        
    # Ko'rishlar sonini oshirish
    db.increment_views(code)
    # Yangi ma'lumotni yuklab olamiz
    movie = db.get_movie(code)
    
    views_count = movie.get("views", 0)
    amplified = get_amplified_views(views_count, code)
    
    caption_text = (
        f"🎬 <b>{movie.get('name')}</b>\n\n"
        f"🔑 <b>Kino kodi:</b> <code>{code}</code>\n"
        f"👁 <b>Ko'rilgan:</b> {amplified} marta\n\n"
        f"🤖 @{(await bot.get_me()).username} - Sizning kino botingiz!"
    )
    
    file_id = movie.get("file_id")
    file_type = movie.get("file_type", "video")
    
    try:
        if file_type == "video":
            await message.reply_video(video=file_id, caption=caption_text, parse_mode="HTML")
        else:
            await message.reply_document(document=file_id, caption=caption_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Kino yuborishda xatolik: {e}")
        await message.reply("Kino faylini yuborishda muammo yuzaga keldi. Iltimos adminga xabar bering.")


# Tasodifiy kino (Random Movie)
@dp.message(F.text == "🎲 Tasodifiy kino")
async def random_movie_handler(message: types.Message):
    user_id = message.from_user.id
    
    # Obunani tekshirish
    if user_id != ADMIN_ID:
        not_joined, all_channels = await check_subscription(bot, user_id)
        if not_joined:
            beautiful_text = "👋 <b>Botdan foydalanish uchun kanallarga a'zo bo'ling!</b>"
            await message.answer(beautiful_text, reply_markup=get_sub_inline_keyboard(all_channels), parse_mode="HTML")
            return
            
    import random
    movies = db.get_movies()
    if not movies:
        await message.answer("Hozircha bazada kinolar yo'q.")
        return
        
    movie = random.choice(movies)
    code = movie.get("code")
    
    # Ko'rishlar sonini oshirish
    db.increment_views(code)
    movie = db.get_movie(code)
    
    views_count = movie.get("views", 0)
    amplified = get_amplified_views(views_count, code)
    
    caption_text = (
        f"🎲 <b>Tasodifiy Kino: {movie.get('name')}</b>\n\n"
        f"🔑 <b>Kino kodi:</b> <code>{code}</code>\n"
        f"👁 <b>Ko'rilgan:</b> {amplified} marta\n\n"
        f"🤖 @{(await bot.get_me()).username} - Sizning kino botingiz!"
    )
    
    file_id = movie.get("file_id")
    file_type = movie.get("file_type", "video")
    
    try:
        if file_type == "video":
            await message.reply_video(video=file_id, caption=caption_text, parse_mode="HTML")
        else:
            await message.reply_document(document=file_id, caption=caption_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Tasodifiy kino yuborish xatosi: {e}")
        await message.reply("Kino faylini yuklashda xatolik.")


# Top kinolar
@dp.message(F.text == "🔥 Top kinolar")
async def top_movies_user_handler(message: types.Message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        not_joined, all_channels = await check_subscription(bot, user_id)
        if not_joined:
            beautiful_text = "👋 <b>Botdan foydalanish uchun kanallarga a'zo bo'ling!</b>"
            await message.answer(beautiful_text, reply_markup=get_sub_inline_keyboard(all_channels), parse_mode="HTML")
            return
            
    movies = db.get_movies()
    if not movies:
        await message.answer("Bazada top kinolar mavjud emas.")
        return
        
    movies.sort(key=lambda x: get_amplified_views(x.get("views", 0), x.get("code", "")), reverse=True)
    top_list = movies[:10]
    
    text = "🔥 <b>ENG KO'P KO'RILGAN TOP 10 KINO:</b>\n\n"
    for idx, m in enumerate(top_list):
        code = m.get("code")
        name = m.get("name")
        views = get_amplified_views(m.get("views", 0), code)
        text += f"<b>{idx + 1}.</b> Kod: <code>{code}</code> — <b>{name}</b> (👁 {views} marta)\n"
        
    await message.answer(text, parse_mode="HTML")


# ------------------ ADMIN PANEL BO'LIMI ------------------

# Statistika tugmasi
@dp.message(F.text == "📊 Statistika")
async def stats_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    users_list = db.get_users()
    movies_list = db.get_movies()
    channels_list = db.get_channels()
    
    # Kanallarni formatlash
    channel_list_text = ""
    if not channels_list:
        channel_list_text = "<i>❌ Kanal qo'shilmagan</i>"
    else:
        for idx, data in enumerate(channels_list):
            is_private = data.get("type") == "private"
            name = data.get("title") or data.get("channel")
            invite_link = data.get("channel", "")
            
            url = invite_link
            if not is_private and invite_link.startswith("@"):
                url = f"https://t.me/{invite_link.replace('@', '')}"
                
            channel_list_text += f"<b>{idx + 1}.</b> <a href='{url}'>{name}</a> ({'🔒 Maxfiy' if is_private else '🌐 Ommaviy'})\n"
            
    # Top-5 kinolar
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
        f"📊 <b>BOT STATISTIKASI (SQLite)</b>\n\n"
        f"👤 <b>Jami foydalanuvchilar:</b> <code>{len(users_list)}</code> ta\n"
        f"🎬 <b>Jami kinolar:</b> <code>{len(movies_list)}</code> ta\n"
        f"📢 <b>Majburiy kanallar:</b> <code>{len(channels_list)}</code> ta\n\n"
        f"📢 <b>MAJBURIY OBUNA KANALLARI LISTI:</b>\n{channel_list_text}\n"
        f"🎬 <b>ENG KO'P KO'RILGAN TOP-5 KINO:</b>\n{top_movies_text}"
    )
    
    await message.answer(html_stats, reply_markup=get_admin_keyboard(), parse_mode="HTML")


# Userlar ro'yxati (oxirgi 50ta)
@dp.message(F.text == "👥 Userlar")
async def list_users_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    users_list = db.get_users(limit=50)
    if not users_list:
        await message.answer("Hozircha foydalanuvchilar yo'q.")
        return
        
    lst = "👤 Foydalanuvchilar ro'yxati (oxirgi 50ta):\n\n"
    for idx, u in enumerate(users_list):
        lst += f"{idx + 1}. ID: {u.get('user_id')} - @{u.get('username', 'username_yoq')}\n"
        
    await message.answer(lst)


# Kanal qo'shish tugmasi
@dp.message(F.text == "📢 Kanal qo‘shish")
async def add_channel_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BotStates.add_ch)
    info_msg = (
        f"📢 <b>KANAL QO'SHISH (SQLite)</b>\n\n"
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
        
        db.save_channel(
            id=channel_id,
            channel=channel_user,
            chat_id=channel_user,
            title=channel_user,
            ch_type="public"
        )
        
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
    
    db.save_channel(
        id=channel_id,
        channel=invite_link,
        chat_id=chat_id,
        title=title,
        ch_type="private"
    )
    
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
        
    channels_list = db.get_channels()
    if not channels_list:
        await message.answer("Kanallar yo'q.")
        return
        
    await state.set_state(BotStates.del_ch)
    lst = "O'chiriladigan kanalni tanlang (Nomi, havolasi yoki @username ini to'liq yozib yuboring):\n\n"
    for data in channels_list:
        lst += f"- <code>{data.get('channel')}</code>\n"
        
    await message.answer(lst, parse_mode="HTML")


# Kanal o'chirishni yakunlash
@dp.message(BotStates.del_ch)
async def delete_channel_process(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    target_input = message.text.strip()
    channels_list = db.get_channels()
    found_id = None
    display_channel = target_input
    
    for data in channels_list:
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
            found_id = data.get("id")
            display_channel = stored_channel
            break
            
    # Qisman qidiruv (Inclusion check fallback)
    if not found_id:
        for data in channels_list:
            stored_channel = (data.get("channel") or "").strip()
            if target_input.lower() in stored_channel.lower() or stored_channel.lower() in target_input.lower():
                found_id = data.get("id")
                display_channel = stored_channel
                break
                
    if found_id:
        db.delete_channel(found_id)
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
    
    db.save_movie(
        code=code,
        file_id=file_id,
        file_type=file_type,
        name=name,
        views=0
    )
    
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
        db.save_movie(
            code=code,
            file_id=file_id,
            file_type=file_type,
            name=f"Kino {code}",
            views=0
        )
        
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
    db.delete_movie(code)
    await state.clear()
    await message.answer(f"🗑 Kod [{code}] bo'lgan kino o'chirildi!", reply_markup=get_admin_keyboard())
    await send_auto_backup(bot)


# Reklama yuborish start
@dp.message(F.text == "📢 Reklama")
async def ad_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    users_list = db.get_users()
    await state.set_state(BotStates.wait_lim)
    await message.answer(f"📊 Jami foydalanuvchilar: {len(users_list)}\nNechtasiga yuboramiz? (Hammasi uchun 0 yozing):")


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
    
    users_list = db.get_users()
    users = [u.get("user_id") for u in users_list if u.get("user_id")]
    
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
        movies_data = db.get_movies()
        channels_data = db.get_channels()
        users_data = db.get_users()
        
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
                    db.save_movie(
                        code=item["code"],
                        file_id=item["file_id"],
                        file_type=item["file_type"],
                        name=item["name"],
                        views=item["views"]
                    )
                    imported_movies += 1
                    
        # Kanallarni tiklash
        if "channels" in backup_data and isinstance(backup_data["channels"], list):
            for raw_item in backup_data["channels"]:
                channel_handle = extract_channel(raw_item)
                if channel_handle:
                    channel_id = "".join([c if c.isalnum() else "_" for c in channel_handle])
                    db.save_channel(
                        id=channel_id,
                        channel=channel_handle,
                        chat_id=channel_handle,
                        title=channel_handle,
                        ch_type="public" # Sukut bo'yicha public
                    )
                    imported_channels += 1
                    
        # Foydalanuvchilarni tiklash
        if "users" in backup_data and isinstance(backup_data["users"], list):
            for item in backup_data["users"]:
                if item and isinstance(item, dict):
                    user_id_field = item.get("user_id") or item.get("userId") or item.get("id")
                    if user_id_field:
                        u_id = str(user_id_field)
                        db.save_user(
                            user_id=u_id,
                            username=item.get("username") or item.get("user_name") or "NoName"
                        )
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
        f"📢 <b>KANALLARGA KINO REJALASHTIRISH (SQLite)</b>\n\n"
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
    
    db.save_scheduled_post(
        id=sch_id,
        channel=state_data.get("channel"),
        file_id=state_data.get("file_id"),
        file_type=state_data.get("file_type"),
        name=state_data.get("name"),
        start_time=state_data.get("start_time_iso"),
        scheduled_at=parsed_target.isoformat() + "Z", # JavaScript ISO string bilan mos
        processed=False,
        created_at=datetime.datetime.utcnow().isoformat() + "Z"
    )
    
    # Holatni tozalab yana kanal bosqichiga qaytaramiz (ketma-ket rejalashtirish oson bo'lishi uchun)
    await state.set_state(BotStates.sch_channel)
    
    success_msg = (
        f"✅ <b>Kino muvaffaqiyatli rejalashtirildi!</b>\n\n"
        f"📢 <b>Kanal:</b> <code>{state_data.get('channel')}</code>\n"
        f"🎬 <b>Sarlavha:</b> {state_data.get('name')}\n"
        f"⏳ <b>Yuklanish vaqti:</b> <code>{message.text}</code>\n\n"
        f"<i>Siz yana boshqa kanalga kino rejalashtirishingiz mumkin. Agar yakunlamoqchi bo'lsangiz <b>/buldi</b> deb yozing!</i>"
    )
    await message.answer(success_msg, parse_mode="HTML")


# ------------------ FONDA REJALASHTIRILGAN POSTLAR LOOP ------------------

async def scheduler_loop():
    logger.info("Rejalashtirilgan postlarni tekshiruvchi fondagi jarayon (Scheduler) ishga tushdi.")
    while True:
        try:
            # Kutayotgan barcha postlarni olish
            pending_posts = db.get_unprocessed_scheduled_posts()
            
            for post in pending_posts:
                sch_id = post.get("id")
                scheduled_at_str = post.get("scheduled_at")
                
                # ISO formatni o'qish ("Z" bo'lsa uni olib tashlaymiz yoki parse qilamiz)
                clean_dt_str = scheduled_at_str.replace("Z", "")
                scheduled_dt = datetime.datetime.fromisoformat(clean_dt_str)
                
                # Farqni hisoblash (Uzbekiston vaqtidagi siljishni inobatga olgan holda)
                # start_time foydalanuvchining o'z Uzbekiston vaqti kiritgan, scheduled_at ham o'sha siljishda
                now_dt = datetime.datetime.now()
                
                if now_dt >= scheduled_dt:
                    logger.info(f"Rejalashtirilgan vaqt keldi! Post yuborilmoqda: {sch_id}")
                    channel = post.get("channel")
                    file_id = post.get("file_id")
                    file_type = post.get("file_type")
                    name = post.get("name")
                    
                    try:
                        # Kanal username yoki ID sini tozalash
                        chat_target = channel
                        if not chat_target.startswith("@") and not chat_target.startswith("-100"):
                            if chat_target.isdigit():
                                chat_target = int(chat_target)
                            elif "t.me/" in chat_target:
                                chat_target = "@" + chat_target.split("/")[-1]
                                
                        caption_text = f"🎬 <b>{name}</b>\n\n🤖 @{(await bot.get_me()).username} - Yangi kinolarni biz bilan tomosha qiling!"
                        
                        if file_type == "video":
                            await bot.send_video(chat_id=chat_target, video=file_id, caption=caption_text, parse_mode="HTML")
                        else:
                            await bot.send_document(chat_id=chat_target, document=file_id, caption=caption_text, parse_mode="HTML")
                            
                        # Muvaffaqiyatli deb belgilash
                        db.update_scheduled_post_processed(sch_id, processed=True)
                        logger.info(f"Kino kanalga muvaffaqiyatli yuborildi: {channel}")
                        
                        # Adminga xabar berish
                        await bot.send_message(
                            ADMIN_ID,
                            f"✅ <b>Kanalga rejalashtirilgan kino muvaffaqiyatli yuborildi!</b>\n\n"
                            f"📢 Kanal: {channel}\n"
                            f"🎬 Kino: <i>{name}</i>",
                            parse_mode="HTML"
                        )
                    except Exception as post_err:
                        err_msg = str(post_err)
                        logger.error(f"Kanalga yuborishda xato {sch_id}: {err_msg}")
                        # Xato bilan belgilash
                        db.update_scheduled_post_processed(sch_id, processed=True, error=err_msg)
                        
                        # Adminga ogohlantirish yuborish
                        await bot.send_message(
                            ADMIN_ID,
                            f"❌ <b>Rejalashtirilgan kinoni yuborishda xatolik!</b>\n\n"
                            f"📢 Kanal: {channel}\n"
                            f"🎬 Kino: <i>{name}</i>\n"
                            f"⚠️ Xatolik ta'rifi: <code>{err_msg}</code>",
                            parse_mode="HTML"
                        )
                        
        except Exception as loop_err:
            logger.error(f"Scheduler loop xatosi: {loop_err}")
            
        await asyncio.sleep(30)  # Har 30 soniyada tekshirish


# Botni ishga tushirish funksiyasi
async def main():
    logger.info("Botni ishga tushirishga urinish...")
    
    # Har qanday webhookni o'chiramiz
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Fondagi rejalashtirilgan vazifani (Scheduler) ishga tushiramiz
    asyncio.create_task(scheduler_loop())
    
    # Pollingni ishga tushirish
    try:
        await dp.start_polling(bot, allowed_updates=['message', 'callback_query', 'chat_join_request'])
    except Exception as e:
        logger.error(f"Bot polling xatoligi: {e}")

if __name__ == "__main__":
    asyncio.run(main())
