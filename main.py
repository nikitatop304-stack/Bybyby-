import asyncio
import logging
import sqlite3
import aiohttp
import os
import io
from datetime import datetime, timedelta
from random import randint
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from PIL import Image, ImageDraw, ImageFont
import config

# ========== НАСТРОЙКА ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('database.db', check_same_thread=False)
cursor = conn.cursor()

# Таблица пользователей
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    attempts INTEGER DEFAULT 3,
    last_daily TEXT,
    invited_by INTEGER DEFAULT 0,
    is_admin INTEGER DEFAULT 0,
    subscribed INTEGER DEFAULT 0,
    balance INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
''')

# Таблица платежей
cursor.execute('''
CREATE TABLE IF NOT EXISTS payments (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount REAL,
    currency TEXT DEFAULT 'USDT',
    invoice_id TEXT,
    attempts_count INTEGER,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    paid_at TEXT
)
''')

# Таблица рефералов
cursor.execute('''
CREATE TABLE IF NOT EXISTS referrals (
    ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    ref_user_id INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
''')

# Таблица игр
cursor.execute('''
CREATE TABLE IF NOT EXISTS games (
    game_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    chosen_gift INTEGER,
    attempts_used INTEGER DEFAULT 0,
    played_at TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'lost',
    reward INTEGER DEFAULT 0
)
''')

conn.commit()

# ========== КОНСТАНТЫ ==========
GIFTS = [15, 25, 50, 100, 150, 200]
GRID_ROWS = 5
GRID_COLS = 4
ADMIN_IDS = config.ADMIN_IDS
REQUIRED_CHANNEL = "@MyBoog"
BOT_USERNAME = "StarGiverTestBot"  # Измени на свой

# Цены в USDT
ATTEMPT_PRICES = {
    5: 0.3,
    10: 0.5,
    20: 0.8
}

# CryptoPay API
CRYPTOPAY_TOKEN = config.CRYPTOPAY_TOKEN
CRYPTOPAY_API = "https://pay.crypt.bot/api"

# ========== УТИЛИТЫ ИЗОБРАЖЕНИЙ ==========
def create_image(title, subtitle="", bg_color=(40, 40, 60)):
    """Создает изображение с текстом"""
    width, height = 800, 400
    image = Image.new('RGB', (width, height), color=bg_color)
    draw = ImageDraw.Draw(image)
    
    try:
        font_large = ImageFont.truetype("arial.ttf", 48)
        font_medium = ImageFont.truetype("arial.ttf", 32)
        font_small = ImageFont.truetype("arial.ttf", 24)
    except:
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    # Заголовок
    draw.text((width//2, height//2 - 40), title, fill=(255, 255, 255), 
              font=font_large, anchor="mm")
    
    # Подзаголовок
    if subtitle:
        draw.text((width//2, height//2 + 30), subtitle, fill=(200, 200, 255), 
                  font=font_medium, anchor="mm")
    
    # Звезды на фоне
    for _ in range(20):
        x, y = randint(0, width), randint(0, height)
        size = randint(1, 3)
        draw.ellipse([x, y, x+size, y+size], fill=(255, 255, 200))
    
    # Сохраняем в буфер
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    buf.seek(0)
    return BufferedInputFile(buf.read(), filename="image.png")

def get_image_for_section(section):
    """Возвращает изображение для раздела"""
    images = {
        "start": create_image("🌟 STAR GIVER", "Добро пожаловать!", (30, 30, 60)),
        "gifts": create_image("🎁 ВЫБЕРИ ПОДАРОК", "50⭐ 100⭐ 200⭐", (60, 30, 60)),
        "game": create_image("🎮 НАЙДИ СТИКЕР", "3 попытки", (30, 60, 60)),
        "stats": create_image("📊 СТАТИСТИКА", "Твои попытки", (60, 60, 30)),
        "invite": create_image("👥 ПРИГЛАСИ ДРУГА", "+1 попытка", (30, 60, 30)),
        "buy": create_image("💰 КУПИТЬ ПОПЫТКИ", "5/10/20 попыток", (60, 30, 30)),
        "help": create_image("❓ ПОМОЩЬ", "Как играть?", (40, 40, 80)),
        "admin": create_image("🛠️ АДМИН ПАНЕЛЬ", "Управление", (80, 40, 40)),
        "subscribe": create_image("📢 ПОДПИШИСЬ", f"Канал: {REQUIRED_CHANNEL}", (80, 40, 80)),
        "payment": create_image("💳 ОПЛАТА", "CryptoBot", (40, 80, 40))
    }
    return images.get(section, images["start"])

# ========== УТИЛИТЫ БАЗЫ ДАННЫХ ==========
def add_user(user_id, username, invited_by=0):
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    if invited_by:
        cursor.execute('UPDATE users SET invited_by=? WHERE user_id=?', (invited_by, user_id))
        cursor.execute('INSERT INTO referrals (user_id, ref_user_id) VALUES (?, ?)', (invited_by, user_id))
        cursor.execute('UPDATE users SET attempts=attempts+1 WHERE user_id=?', (invited_by,))
        cursor.execute('UPDATE users SET attempts=attempts+1 WHERE user_id=?', (user_id,))
    conn.commit()

def get_user(user_id):
    cursor.execute('SELECT * FROM users WHERE user_id=?', (user_id,))
    return cursor.fetchone()

def update_attempts(user_id, delta):
    cursor.execute('UPDATE users SET attempts = attempts + ? WHERE user_id=?', (delta, user_id))
    conn.commit()

def set_subscribed(user_id, status=1):
    cursor.execute('UPDATE users SET subscribed=? WHERE user_id=?', (status, user_id))
    conn.commit()

def get_user_stats(user_id):
    cursor.execute('SELECT COUNT(*) FROM games WHERE user_id=?', (user_id,))
    games = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM referrals WHERE user_id=?', (user_id,))
    refs = cursor.fetchone()[0]
    
    cursor.execute('SELECT SUM(amount) FROM payments WHERE user_id=? AND status="paid"', (user_id,))
    spent = cursor.fetchone()[0] or 0
    
    return games, refs, spent

def get_admin_stats():
    cursor.execute('SELECT COUNT(*) FROM users')
    users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM games')
    games = cursor.fetchone()[0]
    
    cursor.execute('SELECT SUM(amount) FROM payments WHERE status="paid"')
    revenue = cursor.fetchone()[0] or 0
    
    cursor.execute('SELECT COUNT(*) FROM payments WHERE status="paid"')
    payments = cursor.fetchone()[0]
    
    return users, games, revenue, payments

# ========== CRYPTOPAY ФУНКЦИИ ==========
async def create_cryptopay_invoice(user_id, amount_usd, attempts):
    """Создает счет в CryptoPay"""
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Crypto-Pay-API-Token": CRYPTOPAY_TOKEN}
            data = {
                "asset": "USDT",
                "amount": str(amount_usd),
                "description": f"{attempts} попыток в StarGiver",
                "hidden_message": f"+{attempts} попыток",
                "payload": f"{user_id}_{attempts}"
            }
            
            async with session.post(
                f"{CRYPTOPAY_API}/createInvoice",
                headers=headers,
                data=data
            ) as resp:
                result = await resp.json()
                
                if result.get("ok"):
                    invoice = result["result"]
                    cursor.execute('''
                    INSERT INTO payments (user_id, amount, invoice_id, attempts_count)
                    VALUES (?, ?, ?, ?)
                    ''', (user_id, amount_usd, invoice["invoice_id"], attempts))
                    conn.commit()
                    return invoice
                else:
                    logger.error(f"CryptoPay error: {result}")
                    return None
    except Exception as e:
        logger.error(f"CryptoPay API error: {e}")
        return None

async def check_invoice_status(invoice_id):
    """Проверяет статус счета"""
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Crypto-Pay-API-Token": CRYPTOPAY_TOKEN}
            params = {"invoice_ids": invoice_id}
            
            async with session.get(
                f"{CRYPTOPAY_API}/getInvoices",
                headers=headers,
                params=params
            ) as resp:
                result = await resp.json()
                if result.get("ok") and result["result"]["items"]:
                    return result["result"]["items"][0]
    except Exception as e:
        logger.error(f"Check invoice error: {e}")
    return None

# ========== ПРОВЕРКА ПОДПИСКИ ==========
async def check_subscription(user_id):
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"Check subscription error: {e}")
        return False

async def require_subscription(func):
    """Декоратор для проверки подписки"""
    async def wrapper(*args, **kwargs):
        if isinstance(args[0], types.CallbackQuery):
            callback = args[0]
            user_id = callback.from_user.id
        elif isinstance(args[0], types.Message):
            message = args[0]
            user_id = message.from_user.id
        else:
            return
        
        user = get_user(user_id)
        if user and user[6]:  # subscribed
            return await func(*args, **kwargs)
        
        subscribed = await check_subscription(user_id)
        if subscribed:
            set_subscribed(user_id, 1)
            return await func(*args, **kwargs)
        else:
            # Показать кнопку подписки
            kb = InlineKeyboardBuilder()
            kb.row(InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"))
            kb.row(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub"))
            
            if isinstance(args[0], types.CallbackQuery):
                await args[0].answer("❌ Сначала подпишись на канал!", show_alert=True)
                await args[0].message.answer_photo(
                    photo=get_image_for_section("subscribe"),
                    caption=f"📢 **Подпишись на канал {REQUIRED_CHANNEL}**\n\n"
                           "Это обязательно для игры!\n"
                           "После подписки нажми 'Я подписался'",
                    reply_markup=kb.as_markup()
                )
            return False
    return wrapper

# ========== КОМАНДА /START ==========
@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Игрок"
    
    # Обработка реферальной ссылки
    invited_by = 0
    if len(message.text.split()) > 1:
        ref = message.text.split()[1]
        if ref.startswith('ref_'):
            try:
                invited_by = int(ref[4:])
            except:
                pass
    
    add_user(user_id, username, invited_by)
    
    # Проверка подписки
    subscribed = await check_subscription(user_id)
    if not subscribed:
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"))
        kb.row(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub"))
        
        await message.answer_photo(
            photo=get_image_for_section("subscribe"),
            caption=f"🎮 **Добро пожаловать в StarGiver, {username}!**\n\n"
                   f"📢 **Для игры нужно подписаться на наш канал:** {REQUIRED_CHANNEL}\n\n"
                   "✨ **Что внутри?**\n"
                   "• Выигрывай звёзды 🎁\n"
                   "• Приглашай друзей 👥\n"
                   "• Покупай попытки 💰\n\n"
                   "👇 **Действия:**",
            reply_markup=kb.as_markup()
        )
        return
    
    # Установить подписку
    set_subscribed(user_id, 1)
    
    # Проверить ежедневный бонус
    user = get_user(user_id)
    if user and user[3]:  # last_daily
        last_daily = datetime.fromisoformat(user[3])
        if datetime.now() - last_daily >= timedelta(hours=24):
            update_attempts(user_id, 2)
            cursor.execute('UPDATE users SET last_daily=? WHERE user_id=?', 
                          (datetime.now().isoformat(), user_id))
            conn.commit()
    
    # Главное меню
    await show_main_menu(message, user_id)

# ========== ГЛАВНОЕ МЕНЮ ==========
async def show_main_menu(message_or_callback, user_id):
    user = get_user(user_id)
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎁 Выбрать подарок", callback_data="choose_gift"))
    kb.row(
        InlineKeyboardButton(text="📊 Мои попытки", callback_data="my_stats"),
        InlineKeyboardButton(text="👥 Пригласить друга", callback_data="invite")
    )
    kb.row(InlineKeyboardButton(text="💰 Купить попытки", callback_data="buy_attempts"))
    kb.row(InlineKeyboardButton(text="❓ Помощь", callback_data="help"))
    
    if user_id in ADMIN_IDS:
        kb.row(InlineKeyboardButton(text="🛠️ Админ панель", callback_data="admin_panel"))
    
    caption = f"🌟 **Привет, {user[1] or 'Игрок'}!**\n\n"
    caption += f"🎯 **Твои попытки:** {user[2]}\n"
    caption += f"⭐ **Баланс звёзд:** {user[7]}\n\n"
    caption += "🎮 **Выбери действие:**\n"
    caption += "• 🎁 **Подарок** - найди стикер, получи звёзды\n"
    caption += "• 👥 **Друг** - пригласи и получи +1 попытку\n"
    caption += "• 💰 **Попытки** - купи больше попыток\n\n"
    caption += "📢 **Канал:** " + REQUIRED_CHANNEL
    
    if isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.answer_photo(
            photo=get_image_for_section("start"),
            caption=caption,
            reply_markup=kb.as_markup()
        )
    else:
        await message_or_callback.answer_photo(
            photo=get_image_for_section("start"),
            caption=caption,
            reply_markup=kb.as_markup()
        )

# ========== ВЫБОР ПОДАРКА ==========
@dp.callback_query(F.data == "choose_gift")
@require_subscription
async def choose_gift_handler(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    if not user or user[2] <= 0:
        await callback.answer("😔 Нет попыток! Купи или пригласи друга", show_alert=True)
        return
    
    kb = InlineKeyboardBuilder()
    for gift in GIFTS:
        kb.button(text=f"{gift} ⭐", callback_data=f"gift_{gift}")
    kb.adjust(3)
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu"))
    
    await callback.message.answer_photo(
        photo=get_image_for_section("gifts"),
        caption="🎁 **Выбери подарок:**\n\n"
               "Каждый подарок - это сумма звёзд!\n"
               "У тебя 3 попытки найти правильный стикер!\n\n"
               "👇 **Нажми на подарок:**",
        reply_markup=kb.as_markup()
    )

# ========== ОБРАБОТКА ВЫБОРА ПОДАРКА ==========
user_games = {}

@dp.callback_query(F.data.startswith("gift_"))
@require_subscription
async def process_gift(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    gift = int(callback.data.split("_")[1])
    
    # Проверка попыток
    user = get_user(user_id)
    if user[2] <= 0:
        await callback.answer("❌ Нет попыток!", show_alert=True)
        return
    
    # Создать игру
    game_id = f"{user_id}_{datetime.now().timestamp()}"
    user_games[user_id] = {
        "game_id": game_id,
        "gift": gift,
        "attempts": 3,
        "selected": [],
        "grid": [[f"emoji_{r}_{c}" for c in range(GRID_COLS)] for r in range(GRID_ROWS)]
    }
    
    await show_game_grid(callback.message, user_id)

async def show_game_grid(message, user_id):
    game = user_games.get(user_id)
    if not game:
        return
    
    kb = InlineKeyboardBuilder()
    
    # Создаем сетку (правильного стикера нет!)
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            if (r, c) in game["selected"]:
                text = "❌"
                callback_data = f"used_{r}_{c}"
            else:
                # Случайный эмодзи
                emojis = ["🎈", "🎁", "✨", "⭐", "🎯", "🎨", "🎪", "🎭", "🎲", "🎰", "🎮", "🕹️"]
                text = emojis[(r * GRID_COLS + c) % len(emojis)]
                callback_data = f"sticker_{r}_{c}"
            
            kb.button(text=text, callback_data=callback_data)
        kb.adjust(GRID_COLS)
    
    kb.row(InlineKeyboardButton(text="🚪 Выйти", callback_data="main_menu"))
    
    caption = f"🎁 **Подарок:** {game['gift']} ⭐\n"
    caption += f"🎯 **Попытки:** {game['attempts']}/3\n\n"
    caption += "🔍 **Найди правильный стикер!**\n"
    caption += "❌ - уже выбрано\n\n"
    caption += "👇 **Выбери стикер:**"
    
    await message.answer_photo(
        photo=get_image_for_section("game"),
        caption=caption,
        reply_markup=kb.as_markup()
    )

# ========== ОБРАБОТКА СТИКЕРА ==========
@dp.callback_query(F.data.startswith("sticker_"))
@require_subscription
async def process_sticker(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    game = user_games.get(user_id)
    
    if not game:
        await callback.answer("❌ Игра не найдена!", show_alert=True)
        return
    
    # Координаты
    _, r, c = callback.data.split("_")
    r, c = int(r), int(c)
    
    # Уменьшаем попытки
    game["attempts"] -= 1
    game["selected"].append((r, c))
    update_attempts(user_id, -1)
    
    # Сохраняем в БД
    cursor.execute('''
    INSERT INTO games (user_id, chosen_gift, attempts_used, status)
    VALUES (?, ?, ?, ?)
    ''', (user_id, game["gift"], 1, "lost"))
    conn.commit()
    
    if game["attempts"] <= 0:
        # Попытки закончились
        del user_games[user_id]
        
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="👥 Пригласить друга (+1)", callback_data="invite"))
        kb.row(InlineKeyboardButton(text="💰 Купить попытки", callback_data="buy_attempts"))
        kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="main_menu"))
        
        await callback.message.edit_caption(
            caption=f"😔 **Попытки закончились!**\n\n"
                   f"Ты не нашёл стикер для {game['gift']} ⭐\n\n"
                   "🔄 **Как получить попытки:**\n"
                   "• 👥 Пригласи друга: +1 попытка\n"
                   "• 💰 Купи попытки\n"
                   "• ⏳ Жди 24 часа: +2 попытки",
            reply_markup=kb.as_markup()
        )
    else:
        # Обновляем поле
        await callback.message.delete()
        await show_game_grid(callback.message, user_id)
    
    await callback.answer("❌ Неверный стикер!")

# ========== СТАТИСТИКА ==========
@dp.callback_query(F.data == "my_stats")
@require_subscription
async def my_stats_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    games, refs, spent = get_user_stats(user_id)
    
    # Проверка ежедневного бонуса
    daily_available = False
    if user[3]:  # last_daily
        last_daily = datetime.fromisoformat(user[3])
        daily_available = datetime.now() - last_daily >= timedelta(hours=24)
    
    caption = f"📊 **Статистика игрока**\n\n"
    caption += f"👤 **Имя:** {user[1] or 'Аноним'}\n"
    caption += f"🎯 **Попыток:** {user[2]}\n"
    caption += f"⭐ **Звёзд:** {user[7]}\n"
    caption += f"🎮 **Игр сыграно:** {games}\n"
    caption += f"👥 **Друзей приглашено:** {refs}\n"
    caption += f"💰 **Потрачено:** ${spent:.2f}\n\n"
    
    if daily_available:
        caption += "✅ **Ежедневный бонус доступен!** (+2 попытки)\n"
    elif user[3]:
        last_daily = datetime.fromisoformat(user[3])
        next_daily = last_daily + timedelta(hours=24)
        wait = next_daily - datetime.now()
        hours = wait.seconds // 3600
        minutes = (wait.seconds % 3600) // 60
        caption += f"⏳ **Следующий бонус через:** {hours}ч {minutes}м\n"
    
    kb = InlineKeyboardBuilder()
    if daily_available:
        kb.button(text="🎁 Получить бонус", callback_data="get_daily")
    kb.button(text="◀️ Назад", callback_data="main_menu")
    kb.adjust(1)
    
    await callback.message.answer_photo(
        photo=get_image_for_section("stats"),
        caption=caption,
        reply_markup=kb.as_markup()
    )

# ========== ЕЖЕДНЕВНЫЙ БОНУС ==========
@dp.callback_query(F.data == "get_daily")
@require_subscription
async def get_daily_bonus(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    
    if user[3]:  # last_daily
        last_daily = datetime.fromisoformat(user[3])
        if datetime.now() - last_daily < timedelta(hours=24):
            await callback.answer("❌ Бонус уже получен!", show_alert=True)
            return
    
    update_attempts(user_id, 2)
    cursor.execute('UPDATE users SET last_daily=? WHERE user_id=?', 
                  (datetime.now().isoformat(), user_id))
    conn.commit()
    
    await callback.answer("✅ +2 попытки!", show_alert=True)
    await my_stats_handler(callback)

# ========== ПРИГЛАШЕНИЕ ДРУЗЕЙ ==========
@dp.callback_query(F.data == "invite")
@require_subscription
async def invite_friend_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start=ref_{user_id}"
    
    cursor.execute('SELECT COUNT(*) FROM referrals WHERE user_id=?', (user_id,))
    ref_count = cursor.fetchone()[0]
    
    caption = f"👥 **Пригласи друга - получи +1 попытку!**\n\n"
    caption += f"🔗 **Твоя ссылка:**\n`{ref_link}`\n\n"
    caption += "📢 **Как работает:**\n"
    caption += "1. Отправь ссылку другу\n"
    caption += "2. Друг переходит и подписывается\n"
    caption += "3. Вы оба получаете по +1 попытке!\n\n"
    caption += f"🎯 **Приглашено:** {ref_count} друзей\n\n"
    caption += "⚡ **Бонус:** Каждый приглашённый друг = +1 попытка!"
    
    kb = InlineKeyboardBuilder()
    kb.button(text="📤 Поделиться", url=f"https://t.me/share/url?url={ref_link}&text=Привет! Играй в StarGiver и выигрывай звёзды! 🎁")
    kb.button(text="◀️ Назад", callback_data="main_menu")
    kb.adjust(1)
    
    await callback.message.answer_photo(
        photo=get_image_for_section("invite"),
        caption=caption,
        reply_markup=kb.as_markup()
    )

# ========== ПОКУПКА ПОПЫТОК ==========
@dp.callback_query(F.data == "buy_attempts")
@require_subscription
async def buy_attempts_handler(callback: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="5 попыток - 0.3$", callback_data="buy_5")
    kb.button(text="10 попыток - 0.5$", callback_data="buy_10")
    kb.button(text="20 попыток - 0.8$", callback_data="buy_20")
    kb.button(text="◀️ Назад", callback_data="main_menu")
    kb.adjust(1)
    
    caption = "💰 **Покупка попыток**\n\n"
    caption += "💵 **Цены в USDT:**\n"
    caption += "• 5 попыток - 0.3$\n"
    caption += "• 10 попыток - 0.5$\n"
    caption += "• 20 попыток - 0.8$\n\n"
    caption += "⚡ **Мгновенное начисление!**\n"
    caption += "💳 **Оплата через CryptoBot**\n\n"
    caption += "👇 **Выбери пакет:**"
    
    await callback.message.answer_photo(
        photo=get_image_for_section("buy"),
        caption=caption,
        reply_markup=kb.as_markup()
    )

# ========== ОБРАБОТКА ПОКУПКИ ==========
@dp.callback_query(F.data.startswith("buy_"))
@require_subscription
async def process_purchase(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    count = int(callback.data.split("_")[1])
    
    if count not in ATTEMPT_PRICES:
        await callback.answer("❌ Неверный пакет", show_alert=True)
        return
    
    price = ATTEMPT_PRICES[count]
    
    # Создаем счет
    invoice = await create_cryptopay_invoice(user_id, price, count)
    if not invoice:
        await callback.answer("❌ Ошибка создания счета", show_alert=True)
        return
    
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Оплатить", url=invoice["pay_url"])
    kb.button(text="🔄 Проверить", callback_data=f"check_{invoice['invoice_id']}")
    kb.button(text="❌ Отмена", callback_data="buy_attempts")
    kb.adjust(1)
    
    await callback.message.answer_photo(
        photo=get_image_for_section("payment"),
        caption=f"💵 **Счет #{invoice['invoice_id'][:8]}**\n\n"
               f"📦 **Пакет:** {count} попыток\n"
               f"💰 **Сумма:** {price} USDT\n"
               f"⏰ **Действует:** 15 минут\n\n"
               "👇 **Действия:**\n"
               "1. Нажми 'Оплатить'\n"
               "2. Оплати в CryptoBot\n"
               "3. Нажми 'Проверить'",
        reply_markup=kb.as_markup()
    )

# ========== ПРОВЕРКА ОПЛАТЫ ==========
@dp.callback_query(F.data.startswith("check_"))
async def check_payment(callback: types.CallbackQuery):
    invoice_id = callback.data.split("_")[1]
    
    invoice = await check_invoice_status(invoice_id)
    if not invoice:
        await callback.answer("❌ Счет не найден", show_alert=True)
        return
    
    if invoice["status"] == "paid":
        # Обновляем статус
        cursor.execute('UPDATE payments SET status="paid", paid_at=? WHERE invoice_id=?',
                      (datetime.now().isoformat(), invoice_id))
        
        # Получаем данные
        cursor.execute('SELECT user_id, attempts_count FROM payments WHERE invoice_id=?', (invoice_id,))
        payment = cursor.fetchone()
        
        if payment:
            user_id, attempts = payment[0], payment[1]
            update_attempts(user_id, attempts)
            
            user = get_user(user_id)
            
            await callback.message.edit_caption(
                caption=f"✅ **Оплата подтверждена!**\n\n"
                       f"🎁 **Начислено:** {attempts} попыток\n"
                       f"💰 **Сумма:** {ATTEMPT_PRICES[attempts]}$\n"
                       f"🎯 **Всего попыток:** {user[2]}\n\n"
                       "Спасибо за покупку! 🎮"
            )
            await callback.answer("✅ Попытки начислены!")
        else:
            await callback.answer("❌ Ошибка обработки", show_alert=True)
    elif invoice["status"] == "active":
        await callback.answer("⏳ Ожидаем оплату...", show_alert=False)
    else:
        await callback.answer("❌ Счет просрочен", show_alert=True)

# ========== ПОМОЩЬ ==========
@dp.callback_query(F.data == "help")
@require_subscription
async def help_handler(callback: types.CallbackQuery):
    caption = "❓ **Помощь по StarGiver**\n\n"
    caption += "🎮 **Как играть:**\n"
    caption += "1. Выбери подарок (звёзды)\n"
    caption += "2. Найди правильный стикер\n"
    caption += "3. У тебя 3 попытки\n"
    caption += "4. Найди - получи звёзды!\n\n"
    caption += "🎯 **Попытки:**\n"
    caption += "• Стартовые: 3\n"
    caption += "• За друга: +1\n"
    caption += "• Ежедневно: +2 (каждые 24ч)\n"
    caption += "• Купить: меню покупок\n\n"
    caption += "📢 **Требования:**\n"
    caption += f"• Подписка на {REQUIRED_CHANNEL}\n\n"
    caption += "💬 **Поддержка:** @MyBoog"
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад", callback_data="main_menu")
    
    await callback.message.answer_photo(
        photo=get_image_for_section("help"),
        caption=caption,
        reply_markup=kb.as_markup()
    )

# ========== АДМИН ПАНЕЛЬ ==========
@dp.callback_query(F.data == "admin_panel")
async def admin_panel_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if user_id not in ADMIN_IDS:
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    users, games, revenue, payments = get_admin_stats()
    
    caption = f"🛠️ **Админ панель StarGiver**\n\n"
    caption += f"📊 **Статистика:**\n"
    caption += f"• 👥 Пользователей: {users}\n"
    caption += f"• 🎮 Игр сыграно: {games}\n"
    caption += f"• 💰 Выручка: ${revenue:.2f}\n"
    caption += f"• 💳 Оплат: {payments}\n\n"
    caption += "⚙️ **Управление:**"
    
    kb = InlineKeyboardBuilder()
    kb.button(text="📢 Рассылка", callback_data="admin_broadcast")
    kb.button(text="👥 Пользователи", callback_data="admin_users")
    kb.button(text="💳 Платежи", callback_data="admin_payments")
    kb.button(text="◀️ Назад", callback_data="main_menu")
    kb.adjust(1)
    
    await callback.message.answer_photo(
        photo=get_image_for_section("admin"),
        caption=caption,
        reply_markup=kb.as_markup()
    )

# ========== РАССЫЛКА ==========
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    await callback.message.answer(
        "📢 **Режим рассылки**\n\n"
        "Отправь мне сообщение (текст, фото, видео).\n"
        "Я разошлю его всем пользователям.\n\n"
        "❌ Для отмены: /cancel"
    )

@dp.message(F.text == "/cancel")
async def cancel_broadcast(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    await message.answer("❌ Рассылка отменена")

# ========== ПРОВЕРКА ПОДПИСКИ (кнопка) ==========
@dp.callback_query(F.data == "check_sub")
async def check_subscription_button(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    subscribed = await check_subscription(user_id)
    
    if subscribed:
        set_subscribed(user_id, 1)
        await callback.message.delete()
        await show_main_menu(callback, user_id)
        await callback.answer("✅ Спасибо за подписку!", show_alert=True)
    else:
        await callback.answer("❌ Ты еще не подписался!", show_alert=True)

# ========== ВОЗВРАТ В МЕНЮ ==========
@dp.callback_query(F.data == "main_menu")
async def main_menu_handler(callback: types.CallbackQuery):
    await callback.message.delete()
    await show_main_menu(callback, callback.from_user.id)

# ========== ЗАПУСК БОТА ==========
async def main():
    print("=" * 50)
    print("🤖 STAR GIVER BOT")
    print(f"📢 Канал: {REQUIRED_CHANNEL}")
    print(f"👑 Админы: {ADMIN_IDS}")
    print("=" * 50)
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == "__main__":
    asyncio.run(main())