import asyncio
import logging
import sqlite3
import aiohttp
from datetime import datetime, timedelta
from random import choice
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
import config

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# База данных
conn = sqlite3.connect('database.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблиц
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    attempts INTEGER DEFAULT 3,
    last_daily TEXT,
    invited_by INTEGER DEFAULT 0,
    is_admin INTEGER DEFAULT 0,
    subscribed INTEGER DEFAULT 0
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS payments (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount REAL,
    currency TEXT,
    invoice_id TEXT,
    attempts_count INTEGER,
    status TEXT DEFAULT 'pending',
    created_at TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS referrals (
    ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    ref_user_id INTEGER,
    created_at TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS games (
    game_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    chosen_gift INTEGER,
    attempts_used INTEGER DEFAULT 0,
    played_at TEXT,
    status TEXT DEFAULT 'lost'
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS admin_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    total_users INTEGER DEFAULT 0,
    total_games INTEGER DEFAULT 0,
    total_payments REAL DEFAULT 0,
    last_updated TEXT
)
''')

conn.commit()

# Константы
GIFTS = [50, 100, 200, 150, 15, 25]
STICKER_GRID = [["🎈", "🎁", "✨", "⭐"],
                ["🎯", "🎨", "🎪", "🎭"],
                ["🎲", "🎰", "🎮", "🕹️"],
                ["🧩", "🎪", "🎡", "🎠"],
                ["🎖️", "🏆", "🥇", "🏅"]]
ADMIN_IDS = config.ADMIN_IDS
REQUIRED_CHANNEL = "@MyBoog"  # Канал для подписки

# Цены за попытки (в USD)
ATTEMPT_PRICES = {
    5: 0.3,
    10: 0.5,
    20: 0.8
}

# CryptoPay API (CryptoBot)
CRYPTOPAY_TOKEN = config.CRYPTOPAY_TOKEN
CRYPTOPAY_API_URL = "https://pay.crypt.bot/api"

# ========== УТИЛИТЫ ==========
def is_admin(user_id):
    return user_id in ADMIN_IDS

def add_user(user_id, username, invited_by=0):
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    if invited_by:
        cursor.execute('UPDATE users SET invited_by=? WHERE user_id=?', (invited_by, user_id))
        cursor.execute('INSERT INTO referrals (user_id, ref_user_id, created_at) VALUES (?, ?, ?)', 
                      (invited_by, user_id, datetime.now().isoformat()))
        cursor.execute('UPDATE users SET attempts=attempts+1 WHERE user_id=?', (invited_by,))
        cursor.execute('UPDATE users SET attempts=attempts+1 WHERE user_id=?', (user_id,))
    conn.commit()

def get_user(user_id):
    cursor.execute('SELECT * FROM users WHERE user_id=?', (user_id,))
    return cursor.fetchone()

def update_attempts(user_id, change):
    cursor.execute('UPDATE users SET attempts=attempts+? WHERE user_id=?', (change, user_id))
    conn.commit()

def set_subscribed(user_id, status=1):
    cursor.execute('UPDATE users SET subscribed=? WHERE user_id=?', (status, user_id))
    conn.commit()

def check_daily(user_id):
    user = get_user(user_id)
    if user:
        last_daily = datetime.fromisoformat(user[4]) if user[4] else None
        if not last_daily or (datetime.now() - last_daily) >= timedelta(hours=24):
            cursor.execute('UPDATE users SET attempts=attempts+2, last_daily=? WHERE user_id=?', 
                         (datetime.now().isoformat(), user_id))
            conn.commit()
            return True
    return False

def get_user_stats(user_id):
    cursor.execute('SELECT COUNT(*) FROM games WHERE user_id=?', (user_id,))
    games_played = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM referrals WHERE user_id=?', (user_id,))
    ref_count = cursor.fetchone()[0]
    
    return games_played, ref_count

def get_admin_stats():
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM games')
    total_games = cursor.fetchone()[0]
    
    cursor.execute('SELECT SUM(amount) FROM payments WHERE status="paid"')
    total_payments = cursor.fetchone()[0] or 0
    
    return total_users, total_games, total_payments

# ========== ПРОВЕРКА ПОДПИСКИ ==========
async def check_subscription(user_id):
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        return False

# ========== CRYPTOPAY ФУНКЦИИ ==========
async def create_invoice(user_id, amount_usd, attempts_count):
    async with aiohttp.ClientSession() as session:
        url = f"{CRYPTOPAY_API_URL}/createInvoice"
        headers = {"Crypto-Pay-API-Token": CRYPTOPAY_TOKEN}
        
        data = {
            "asset": "USDT",
            "amount": str(amount_usd),
            "description": f"Покупка {attempts_count} попыток в StarGiver",
            "hidden_message": f"Пополнение попыток: +{attempts_count}",
            "paid_btn_name": "callback",
            "paid_btn_url": "https://t.me/StarGiverBot",
            "payload": f"{user_id}_{attempts_count}"
        }
        
        async with session.post(url, headers=headers, data=data) as response:
            result = await response.json()
            if result.get("ok"):
                return result["result"]
            else:
                logger.error(f"CryptoPay error: {result}")
                return None

async def check_invoice(invoice_id):
    async with aiohttp.ClientSession() as session:
        url = f"{CRYPTOPAY_API_URL}/getInvoices"
        headers = {"Crypto-Pay-API-Token": CRYPTOPAY_TOKEN}
        params = {"invoice_ids": invoice_id}
        
        async with session.get(url, headers=headers, params=params) as response:
            result = await response.json()
            if result.get("ok"):
                return result["result"]["items"][0]
            return None

# ========== ПРОВЕРКА ДОСТУПА ==========
async def check_access(user_id, callback=None, message=None):
    user = get_user(user_id)
    if not user:
        return False
    
    if not user[6]:  # subscribed = 0
        subscribed = await check_subscription(user_id)
        if subscribed:
            set_subscribed(user_id, 1)
            return True
        else:
            if callback:
                await callback.answer("❌ Сначала подпишитесь на канал!", show_alert=True)
            elif message:
                await message.answer("❌ Сначала подпишитесь на канал!")
            return False
    return True

# ========== КОМАНДА /START ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    
    invited_by = 0
    if len(message.text.split()) > 1:
        ref_code = message.text.split()[1]
        if ref_code.startswith('ref_'):
            try:
                invited_by = int(ref_code.split('_')[1])
            except:
                pass
    
    add_user(user_id, username, invited_by)
    
    # Проверяем подписку
    user = get_user(user_id)
    if not user[6]:  # subscribed = 0
        subscribed = await check_subscription(user_id)
        if subscribed:
            set_subscribed(user_id, 1)
            await show_main_menu(message, user_id)
        else:
            # Показываем кнопку подписки
            keyboard = InlineKeyboardBuilder()
            keyboard.row(InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"))
            keyboard.row(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription"))
            
            await message.answer_photo(
                photo=FSInputFile("images/subscribe.jpg"),
                caption="📢 **Для использования бота необходимо подписаться на наш канал!**\n\n"
                        f"Канал: {REQUIRED_CHANNEL}\n\n"
                        "1. Нажмите кнопку ниже\n"
                        "2. Подпишитесь на канал\n"
                        "3. Вернитесь сюда и нажмите 'Я подписался'",
                reply_markup=keyboard.as_markup()
            )
            return
    
    # Если подписан или уже проверяли
    await show_main_menu(message, user_id)

# ========== ПРОВЕРКА ПОДПИСКИ ==========
@dp.callback_query(F.data == "check_subscription")
async def verify_subscription(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    subscribed = await check_subscription(user_id)
    
    if subscribed:
        set_subscribed(user_id, 1)
        await callback.message.delete()
        await show_main_menu(callback.message, user_id)
        await callback.answer("✅ Спасибо за подписку!")
    else:
        await callback.answer("❌ Вы ещё не подписались на канал!", show_alert=True)

# ========== ГЛАВНОЕ МЕНЮ ==========
async def show_main_menu(message, user_id):
    check_daily(user_id)  # Проверяем ежедневный бонус
    
    photo = FSInputFile("images/welcome.jpg")
    user = get_user(user_id)
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="🎁 Выбрать подарок", callback_data="choose_gift"))
    keyboard.row(InlineKeyboardButton(text="📊 Мои попытки", callback_data="my_attempts"),
                 InlineKeyboardButton(text="👥 Пригласить друга", callback_data="invite_friend"))
    keyboard.row(InlineKeyboardButton(text="💰 Купить попытки", callback_data="buy_attempts"),
                 InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help"))
    if is_admin(user_id):
        keyboard.row(InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_panel"))
    
    attempts_info = f"Попытки: {user[2]}" if user else "Попытки: 3"
    
    await message.answer_photo(
        photo=photo,
        caption=f"✨ **Добро пожаловать в StarGiver!** 🎁\n\n"
                f"Здесь ты можешь получать звёзды в Telegram просто играя!\n"
                f"Выбирай подарок, ищи стикер — забирай награду! Удачи!\n\n"
                f"📢 **Обязательно:** Подписка на {REQUIRED_CHANNEL}\n"
                f"🎯 **{attempts_info}**\n\n"
                f"🎮 **Правила:**\n"
                f"• 3 попытки на игру\n"
                f"• +1 попытка за друга\n"
                f"• +2 попытки каждые 24 часа\n"
                f"• Купить попытки 💰\n"
                f"• Удачи! 😉",
        reply_markup=keyboard.as_markup()
    )

# ========== ВЫБОР ПОДАРКА ==========
@dp.callback_query(F.data == "choose_gift")
async def choose_gift(callback: types.CallbackQuery):
    if not await check_access(callback.from_user.id, callback=callback):
        return
    
    user = get_user(callback.from_user.id)
    if not user:
        return
    
    if user[2] <= 0:
        await callback.message.answer("😔 У вас закончились попытки!\n"
                                     "👥 Пригласите друга или подождите 24 часа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    for gift in GIFTS:
        keyboard.button(text=f"{gift} ⭐", callback_data=f"gift_{gift}")
    keyboard.adjust(3)
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu"))
    
    await callback.message.answer_photo(
        photo=FSInputFile("images/gifts.jpg"),
        caption="🎁 **Выберите подарок:**\n"
                "Нажмите на сумму звёзд, которую хотите выиграть!\n\n"
                "У вас есть 3 попытки чтобы найти правильный стикер!",
        reply_markup=keyboard.as_markup()
    )

# ========== ОБРАБОТКА ВЫБОРА ПОДАРКА ==========
user_games = {}  # Временное хранение игр

@dp.callback_query(F.data.startswith("gift_"))
async def process_gift_selection(callback: types.CallbackQuery):
    if not await check_access(callback.from_user.id, callback=callback):
        return
    
    user_id = callback.from_user.id
    gift_amount = int(callback.data.split("_")[1])
    
    # Сохраняем выбранный подарок
    user_games[user_id] = {
        'gift': gift_amount,
        'attempts': 3,
        'selected_stickers': []
    }
    
    # Создаем игровое поле
    await show_game_field(callback.message, user_id)

async def show_game_field(message, user_id):
    game = user_games.get(user_id)
    if not game:
        return
    
    # Создаем сетку стикеров
    keyboard = InlineKeyboardBuilder()
    for row_idx, row in enumerate(STICKER_GRID):
        for col_idx, sticker in enumerate(row):
            if (row_idx, col_idx) in game['selected_stickers']:
                keyboard.button(text="❌", callback_data=f"used_{row_idx}_{col_idx}")
            else:
                keyboard.button(text=sticker, callback_data=f"sticker_{row_idx}_{col_idx}")
        keyboard.adjust(4)
    
    keyboard.row(InlineKeyboardButton(text="🚪 Выйти из игры", callback_data="main_menu"))
    
    caption = f"🎁 **Подарок: {game['gift']} ⭐**\n"
    caption += f"🎯 **Попытки: {game['attempts']}/3**\n\n"
    caption += "🔍 **Найдите правильный стикер!**\n"
    caption += "❌ - уже выбрано\n"
    caption += "👇 Выберите стикер:"
    
    await message.answer_photo(
        photo=FSInputFile("images/game_field.jpg"),
        caption=caption,
        reply_markup=keyboard.as_markup()
    )

# ========== ОБРАБОТКА ВЫБОРА СТИКЕРА ==========
@dp.callback_query(F.data.startswith("sticker_"))
async def process_sticker_selection(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    game = user_games.get(user_id)
    
    if not game:
        await callback.answer("❌ Игра не найдена!")
        return
    
    # Парсим координаты
    _, row_idx, col_idx = callback.data.split("_")
    row_idx = int(row_idx)
    col_idx = int(col_idx)
    
    # Добавляем в выбранные
    game['selected_stickers'].append((row_idx, col_idx))
    game['attempts'] -= 1
    
    # Обновляем попытки в БД
    update_attempts(user_id, -1)
    
    # Сохраняем игру в БД
    cursor.execute('''
    INSERT INTO games (user_id, chosen_gift, attempts_used, played_at, status)
    VALUES (?, ?, ?, ?, ?)
    ''', (user_id, game['gift'], 1, datetime.now().isoformat(), 'lost'))
    conn.commit()
    
    if game['attempts'] <= 0:
        # Попытки закончились
        del user_games[user_id]
        
        keyboard = InlineKeyboardBuilder()
        keyboard.row(InlineKeyboardButton(text="👥 Пригласить друга (+1 попытка)", callback_data="invite_friend"))
        keyboard.row(InlineKeyboardButton(text="💰 Купить попытки", callback_data="buy_attempts"))
        keyboard.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
        
        await callback.message.edit_caption(
            caption=f"😔 **Попытки закончились!**\n\n"
                    f"Вы не нашли правильный стикер для подарка {game['gift']} ⭐\n\n"
                    f"🔄 **Получите больше попыток:**\n"
                    f"• Пригласите друга: +1 попытка\n"
                    f"• Купите попытки\n"
                    f"• Подождите 24 часа: +2 попытки",
            reply_markup=keyboard.as_markup()
        )
    else:
        # Обновляем игровое поле
        await callback.message.delete()
        await show_game_field(callback.message, user_id)
    
    await callback.answer("❌ Неверный стикер!")

# ========== МОИ ПОПЫТКИ ==========
@dp.callback_query(F.data == "my_attempts")
async def my_attempts(callback: types.CallbackQuery):
    if not await check_access(callback.from_user.id, callback=callback):
        return
    
    user_id = callback.from_user.id
    user = get_user(user_id)
    games_played, ref_count = get_user_stats(user_id)
    
    # Проверяем ежедневный бонус
    daily_available = check_daily(user_id)
    user = get_user(user_id)  # Обновляем данные
    
    caption = f"📊 **Ваша статистика**\n\n"
    caption += f"🎯 **Попыток:** {user[2]}\n"
    caption += f"🎮 **Игр сыграно:** {games_played}\n"
    caption += f"👥 **Друзей приглашено:** {ref_count}\n\n"
    
    if daily_available:
        caption += "✅ **Ежедневный бонус доступен!** (+2 попытки)\n"
    else:
        if user[4]:  # last_daily
            last_daily = datetime.fromisoformat(user[4])
            next_daily = last_daily + timedelta(hours=24)
            time_left = next_daily - datetime.now()
            hours = time_left.seconds // 3600
            minutes = (time_left.seconds % 3600) // 60
            caption += f"⏳ **Следующий бонус через:** {hours}ч {minutes}м\n"
    
    keyboard = InlineKeyboardBuilder()
    if daily_available:
        keyboard.row(InlineKeyboardButton(text="🎁 Получить ежедневный бонус", callback_data="get_daily"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu"))
    
    await callback.message.answer_photo(
        photo=FSInputFile("images/stats.jpg"),
        caption=caption,
        reply_markup=keyboard.as_markup()
    )

# ========== ЕЖЕДНЕВНЫЙ БОНУС ==========
@dp.callback_query(F.data == "get_daily")
async def get_daily_bonus(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if check_daily(user_id):
        user = get_user(user_id)
        await callback.answer(f"✅ Получено +2 попытки! Всего: {user[2]}", show_alert=True)
        await my_attempts(callback)
    else:
        await callback.answer("❌ Бонус уже получен!", show_alert=True)

# ========== ПРИГЛАСИТЬ ДРУГА ==========
@dp.callback_query(F.data == "invite_friend")
async def invite_friend(callback: types.CallbackQuery):
    if not await check_access(callback.from_user.id, callback=callback):
        return
    
    user_id = callback.from_user.id
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start=ref_{user_id}"
    
    caption = f"👥 **Пригласите друга и получите +1 попытку!**\n\n"
    caption += f"🔗 **Ваша реферальная ссылка:**\n`{ref_link}`\n\n"
    caption += "📢 **Как это работает:**\n"
    caption += "1. Отправьте ссылку другу\n"
    caption += "2. Друг переходит по ссылке и подписывается на канал\n"
    caption += "3. Вы и ваш друг получаете по +1 попытке!\n\n"
    caption += "🎯 **Вы пригласили:** 0 друзей (статистика обновляется)"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="📤 Поделиться ссылкой", url=f"https://t.me/share/url?url={ref_link}&text=Привет! Получай звёзды в Telegram с этим ботом! 🎁"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu"))
    
    await callback.message.answer_photo(
        photo=FSInputFile("images/invite.jpg"),
        caption=caption,
        reply_markup=keyboard.as_markup()
    )

# ========== ПОКУПКА ПОПЫТОК ==========
@dp.callback_query(F.data == "buy_attempts")
async def buy_attempts_menu(callback: types.CallbackQuery):
    if not await check_access(callback.from_user.id, callback=callback):
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="5 попыток - 0.3$", callback_data="buy_5"))
    keyboard.row(InlineKeyboardButton(text="10 попыток - 0.5$", callback_data="buy_10"))
    keyboard.row(InlineKeyboardButton(text="20 попыток - 0.8$", callback_data="buy_20"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu"))
    
    await callback.message.answer_photo(
        photo=FSInputFile("images/buy.jpg"),
        caption="💰 **Покупка попыток**\n\n"
                "Выберите количество попыток для покупки:\n\n"
                "• 5 попыток - 0.3$\n"
                "• 10 попыток - 0.5$\n"
                "• 20 попыток - 0.8$\n\n"
                "💳 **Оплата:** через CryptoBot (USDT)\n"
                "⚡ **Мгновенное начисление!**",
        reply_markup=keyboard.as_markup()
    )

# ========== ОБРАБОТКА ПОКУПКИ ==========
@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    if not await check_access(callback.from_user.id, callback=callback):
        return
    
    user_id = callback.from_user.id
    count = int(callback.data.split("_")[1])
    
    if count not in ATTEMPT_PRICES:
        await callback.answer("❌ Неверное количество попыток")
        return
    
    price = ATTEMPT_PRICES[count]
    
    # Создаём инвойс в CryptoPay
    invoice = await create_invoice(user_id, price, count)
    
    if not invoice:
        await callback.answer("❌ Ошибка создания счёта")
        return
    
    # Сохраняем платёж в БД
    cursor.execute('''
    INSERT INTO payments (user_id, amount, currency, invoice_id, attempts_count, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, price, "USDT", invoice["invoice_id"], count, datetime.now().isoformat()))
    conn.commit()
    
    # Отправляем ссылку на оплату
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="💳 Оплатить", url=invoice["pay_url"]))
    keyboard.row(InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_pay_{invoice['invoice_id']}"))
    keyboard.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_payment"))
    
    await callback.message.answer(
        f"💰 **Счёт на оплату**\n\n"
        f"📦 **Количество попыток:** {count}\n"
        f"💵 **Сумма:** {price} USDT\n"
        f"⏰ **Время на оплату:** 15 минут\n\n"
        f"🔗 **Ссылка для оплаты:**\n"
        f"После оплаты нажмите 'Проверить оплату'.",
        reply_markup=keyboard.as_markup()
    )

# ========== ПРОВЕРКА ОПЛАТЫ ==========
@dp.callback_query(F.data.startswith("check_pay_"))
async def check_payment(callback: types.CallbackQuery):
    invoice_id = callback.data.split("_")[2]
    
    # Проверяем статус в CryptoPay
    invoice = await check_invoice(invoice_id)
    
    if not invoice:
        await callback.answer("❌ Счёт не найден")
        return
    
    if invoice["status"] == "paid":
        # Обновляем статус в БД
        cursor.execute('UPDATE payments SET status=? WHERE invoice_id=?', ("paid", invoice_id))
        
        # Получаем данные о платеже
        cursor.execute('SELECT user_id, attempts_count FROM payments WHERE invoice_id=?', (invoice_id,))
        payment = cursor.fetchone()
        
        if payment:
            user_id = payment[0]
            attempts_count = payment[1]
            
            # Начисляем попытки
            update_attempts(user_id, attempts_count)
            
            # Обновляем статистику админа
            cursor.execute('SELECT SUM(amount) FROM payments WHERE status="paid"')
            total_payments = cursor.fetchone()[0] or 0
            
            await callback.message.edit_text(
                f"✅ **Оплата подтверждена!**\n\n"
                f"🎯 **Вам начислено:** {attempts_count} попыток\n"
                f"💰 **Сумма:** {ATTEMPT_PRICES[attempts_count]} USDT\n"
                f"📊 **Всего попыток:** {get_user(user_id)[2]}\n\n"
                f"Спасибо за покупку! 🎁"
            )
        else:
            await callback.answer("❌ Ошибка при обработке платежа")
    elif invoice["status"] == "active":
        await callback.answer("⏳ Ожидаем оплату...")
    else:
        await callback.answer("❌ Платёж не прошёл")

# ========== ОТМЕНА ПЛАТЕЖА ==========
@dp.callback_query(F.data == "cancel_payment")
async def cancel_payment(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer("❌ Платёж отменён")

# ========== ПОМОЩЬ ==========
@dp.callback_query(F.data == "help")
async def help_command(callback: types.CallbackQuery):
    caption = "❓ **Помощь по боту StarGiver**\n\n"
    caption += "🎮 **Как играть:**\n"
    caption += "1. Выберите подарок (сумму звёзд)\n"
    caption += "2. Найдите правильный стикер на поле\n"
    caption += "3. У вас есть 3 попытки\n"
    caption += "4. Если найдёте — получите звёзды!\n\n"
    caption += "🎯 **Попытки:**\n"
    caption += "• Начальные: 3\n"
    caption += "• За друга: +1\n"
    caption += "• Ежедневно: +2 (каждые 24ч)\n"
    caption += "• Купить: 5/10/20 за 0.3/0.5/0.8$\n\n"
    caption += "📢 **Обязательно:** Подписка на канал!\n"
    caption += "💬 **Поддержка:** @MyBoog"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu"))
    
    await callback.message.answer_photo(
        photo=FSInputFile("images/help.jpg"),
        caption=caption,
        reply_markup=keyboard.as_markup()
    )

# ========== ГЛАВНОЕ МЕНЮ (ВОЗВРАТ) ==========
@dp.callback_query(F.data == "main_menu")
async def return_to_main_menu(callback: types.CallbackQuery):
    await callback.message.delete()
    await show_main_menu(callback.message, callback.from_user.id)

# ========== АДМИН ПАНЕЛЬ ==========
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ Доступ запрещён!", show_alert=True)
        return
    
    total_users, total_games, total_payments = get_admin_stats()
    
    caption = f"🛠️ **Админ-панель StarGiver**\n\n"
    caption += f"📊 **Статистика:**\n"
    caption += f"• Пользователей: {total_users}\n"
    caption += f"• Игр сыграно: {total_games}\n"
    caption += f"• Выручка: ${total_payments:.2f}\n\n"
    caption += "⚙️ **Управление:**"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast"))
    keyboard.row(InlineKeyboardButton(text="👥 Поиск пользователя", callback_data="admin_search"))
    keyboard.row(InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="admin_panel"))
    keyboard.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    
    await callback.message.answer_photo(
        photo=FSInputFile("images/admin.jpg"),
        caption=caption,
        reply_markup=keyboard.as_markup()
    )

# ========== РАССЫЛКА ==========
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        return
    
    await callback.message.answer(
        "📢 **Отправьте сообщение для рассылки:**\n"
        "Можно отправить текст, фото, видео или документ.\n\n"
        "❌ **Для отмены отправьте /cancel**"
    )
    
    # Здесь нужно добавить FSM для рассылки, но для простоты опустим

# ========== ЗАПУСК БОТА ==========
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    print("🤖 Бот StarGiver запущен...")
    asyncio.run(main())