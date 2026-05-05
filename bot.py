import os
import logging
import psycopg2
import psycopg2.extras
import aiohttp
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "8537953320:AAGHBciqdEyYMo6-5f7CK18cT5FAwOTPYC8")
GOLDAPI_KEY = os.environ.get("GOLDAPI_KEY", "goldapi-b2c2b3ee9be7edf064783199656cf7ae-io")
MEDIA_CHANNEL_ID = int(os.environ.get("MEDIA_CHANNEL_ID", "-3862533044"))
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "612230948"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "ras_0104")
GROUP_LINK = os.environ.get("GROUP_LINK", "https://t.me/+_pgc-XiGz7E4YTUy")
GROUP_ID = int(os.environ.get("GROUP_ID", "-1003852395090"))
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:yHPCeeDEJgWEeUXakKjoORZLoVzZNecl@postgres.railway.internal:5432/railway")

CATEGORIES = [
    "💍 Ювелирка",
    "🥇 Слитки",
    "💎 Камни",
    "🗑 Лом и скупка",
    "📈 Инвестиции и цены",
    "🪙 Монеты драг"
]

CATEGORY_TOPICS = {
    "💍 Ювелирка": 8,
    "🥇 Слитки": 4,
    "💎 Камни": 10,
    "🗑 Лом и скупка": 11,
    "📈 Инвестиции и цены": 12,
    "🪙 Монеты драг": 2,
}

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                first_name TEXT,
                last_name TEXT,
                username TEXT,
                phone TEXT,
                status TEXT DEFAULT 'active',
                registered_at TEXT,
                subscriptions TEXT DEFAULT '',
                rating REAL DEFAULT 0,
                reviews_count INTEGER DEFAULT 0,
                joined TEXT DEFAULT 'no'
            )
        """)
        # Add joined column if not exists (for existing tables)
        try:
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS joined TEXT DEFAULT 'no'")
            conn.commit()
        except:
            pass
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ads (
                ad_id SERIAL PRIMARY KEY,
                user_id TEXT,
                username TEXT,
                type TEXT,
                category TEXT,
                description TEXT,
                price TEXT,
                photo_id TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT,
                message_id TEXT DEFAULT '',
                media_message_id TEXT DEFAULT '',
                city TEXT DEFAULT ''
            )
        """)
        try:
            cur.execute("ALTER TABLE ads ADD COLUMN IF NOT EXISTS message_id TEXT DEFAULT ''")
            cur.execute("ALTER TABLE ads ADD COLUMN IF NOT EXISTS media_message_id TEXT DEFAULT ''")
            cur.execute("ALTER TABLE ads ADD COLUMN IF NOT EXISTS city TEXT DEFAULT ''")
            conn.commit()
        except:
            pass
        cur.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                deal_id SERIAL PRIMARY KEY,
                seller_id TEXT,
                buyer_id TEXT,
                ad_id TEXT,
                created_at TEXT,
                notified BOOLEAN DEFAULT FALSE
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("DB initialized")
    except Exception as e:
        logger.error(f"init_db error: {e}")

def get_user(user_id):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM users WHERE user_id = %s", (str(user_id),))
        user = cur.fetchone()
        cur.close()
        conn.close()
        return dict(user) if user else None
    except Exception as e:
        logger.error(f"get_user error: {e}")
        return None

def save_user(user_id, data):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (user_id, first_name, last_name, username, phone, status, registered_at, subscriptions, rating, reviews_count, joined)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                username = EXCLUDED.username,
                phone = EXCLUDED.phone,
                status = EXCLUDED.status,
                subscriptions = EXCLUDED.subscriptions,
                rating = EXCLUDED.rating,
                reviews_count = EXCLUDED.reviews_count,
                joined = EXCLUDED.joined
        """, (
            str(user_id),
            data.get("first_name", ""),
            data.get("last_name", ""),
            data.get("username", ""),
            data.get("phone", ""),
            data.get("status", "active"),
            data.get("registered_at", datetime.now().strftime("%d.%m.%Y %H:%M")),
            data.get("subscriptions", ""),
            data.get("rating", 0),
            data.get("reviews_count", 0),
            data.get("joined", "no")
        ))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"save_user error: {e}")
        return False

def save_ad(ad_data):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ads (user_id, username, type, category, description, price, photo_id, status, created_at, message_id, media_message_id, city)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING ad_id
        """, (
            str(ad_data.get("user_id")),
            ad_data.get("username", ""),
            ad_data.get("type", ""),
            ad_data.get("category", ""),
            ad_data.get("description", ""),
            ad_data.get("price", ""),
            ad_data.get("photo_id", ""),
            "active",
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            str(ad_data.get("message_id", "")),
            str(ad_data.get("media_message_id", "")),
            ad_data.get("city", "")
        ))
        ad_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return ad_id
    except Exception as e:
        logger.error(f"save_ad error: {e}")
        return None

def get_user_ads(user_id):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM ads WHERE user_id = %s ORDER BY ad_id DESC", (str(user_id),))
        ads = [dict(a) for a in cur.fetchall()]
        cur.close()
        conn.close()
        return ads
    except:
        return []

def save_deal(seller_id, buyer_id, ad_id):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO deals (seller_id, buyer_id, ad_id, created_at)
            VALUES (%s, %s, %s, %s)
        """, (str(seller_id), str(buyer_id), str(ad_id), datetime.now().strftime("%d.%m.%Y %H:%M")))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"save_deal error: {e}")

states = {}

async def show_ad_preview(update_or_msg, context, user_id):
    u = states.get(user_id, {})
    emoji = "🟢" if u.get("ad_type") == "Продаю" else "🔵"
    extra_count = len(u.get("extra_media", []))
    extra_text = f"\n📸 Доп. медиа: {extra_count} шт." if extra_count > 0 else ""
    preview_text = (
        f"📋 Предварительный просмотр:\n\n"
        f"{emoji} {u.get('ad_type','').upper()}\n"
        f"{u.get('category','')}\n"
        f"📍 {u.get('city','')}\n\n"
        f"{u.get('description','')}\n\n"
        f"💰 {u.get('price','')}{extra_text}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Опубликовать", callback_data="ad_confirm"),
         InlineKeyboardButton("❌ Отменить", callback_data="ad_cancel")]
    ])
    if hasattr(update_or_msg, 'message') and update_or_msg.message:
        await update_or_msg.message.reply_text(preview_text, reply_markup=keyboard)
    else:
        chat_id = update_or_msg.message.chat_id if hasattr(update_or_msg, 'message') else update_or_msg
        await context.bot.send_message(chat_id=chat_id, text=preview_text, reply_markup=keyboard)

async def get_metal_prices():
    try:
        # Получаем курс USD/KZT
        usd_kzt = 520.0
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.exchangerate-api.com/v4/latest/USD") as r:
                    data = await r.json()
                    usd_kzt = data.get("rates", {}).get("KZT", 520.0)
        except:
            pass

        metals = {
            "XAU": ("🥇 Золото", "г"),
            "XAG": ("🥈 Серебро", "г"),
            "XPT": ("💎 Платина", "г"),
            "XPD": ("🔩 Палладий", "г"),
        }
        results = {}
        async with aiohttp.ClientSession() as session:
            for symbol, (name, unit) in metals.items():
                try:
                    url = f"https://www.goldapi.io/api/{symbol}/USD"
                    headers = {"x-access-token": GOLDAPI_KEY, "Content-Type": "application/json"}
                    async with session.get(url, headers=headers) as r:
                        data = await r.json()
                        price_usd_oz = data.get("price", 0)
                        # Переводим из тройской унции (31.1г) в граммы и в KZT
                        price_kzt_g = (price_usd_oz / 31.1035) * usd_kzt
                        results[symbol] = (name, round(price_kzt_g), unit)
                except:
                    pass
        return results, round(usd_kzt)
    except Exception as e:
        return {}, 520

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Подать объявление", callback_data="menu_ad")],
        [InlineKeyboardButton("📊 Цены на металлы", callback_data="menu_prices")],
        [InlineKeyboardButton("🔔 Мои подписки", callback_data="menu_subs")],
        [InlineKeyboardButton("⭐ Моя репутация", callback_data="menu_rep")],
        [InlineKeyboardButton("👤 Мой профиль", callback_data="menu_profile")],
        [InlineKeyboardButton("📋 Как это работает", callback_data="menu_how")],
        [InlineKeyboardButton("📞 Администратор", callback_data="menu_admin")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)

    # Handle deep link buy_SELLERID_ADID
    if context.args and context.args[0].startswith("buy_"):
        parts = context.args[0].split("_")
        # format: buy_SELLERID_ADID
        seller_id = parts[1] if len(parts) > 1 else ""
        ad_id_link = parts[2] if len(parts) > 2 else "0"
        if seller_id == user_id:
            await update.message.reply_text("❌ Это ваше объявление!")
            return
        # Get ad info
        ad_info = ""
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM ads WHERE ad_id = %s", (int(ad_id_link),))
            ad = cur.fetchone()
            cur.close()
            conn.close()
            if ad:
                ad_info = f"\n\n📦 Объявление: {ad.get('category','')}\n📝 {ad.get('description','')[:50]}\n💰 {ad.get('price','')}"
        except:
            pass
        await update.message.reply_text(
            f"Вы хотите приобрести этот товар?{ad_info}\n\n"
            "После подтверждения оба участника получат контакты друг друга.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Подтвердить", callback_data=f"buy_confirm_{seller_id}_{ad_id_link}")],
                [InlineKeyboardButton("❌ Отмена", callback_data="buy_cancel")]
            ])
        )
        return

    user = get_user(user_id)

    if user and user.get("status") == "active" and user.get("joined") == "yes":
        states[user_id] = {"step": "menu"}
        await update.message.reply_text(
            f"🏅 Добро пожаловать в KAZMETALS!\n\n"
            f"👤 {user.get('first_name','')} {user.get('last_name','')}\n"
            f"🔗 @{user.get('username','')}\n\n"
            f"Выберите действие 👇",
            reply_markup=main_menu_keyboard()
        )
    elif user and user.get("status") == "active" and user.get("joined") != "yes":
        states[user_id] = {"step": "awaiting_join"}
        await update.message.reply_text(
            "⚠️ Вы зарегистрированы но ещё не вступили в группу!\n\n"
            "Вступите в группу чтобы получить доступ к платформе 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔓 Вступить в KAZMETALS", url=GROUP_LINK)],
                [InlineKeyboardButton("✅ Я вступил", callback_data="check_join")]
            ])
        )
    else:
        states[user_id] = {"step": "start"}
        await update.message.reply_text(
            "🏅 Добро пожаловать в KAZMETALS!\n\n"
            "Мы — закрытая платформа аукционов и продаж драгоценных металлов в Казахстане.\n\n"
            "📦 Категории:\n"
            "• 💍 Ювелирка\n• 🥇 Слитки\n• 💎 Камни\n"
            "• 🗑 Лом и скупка\n• 📈 Инвестиции и цены\n• 🪙 Монеты драг\n\n"
            "Нажмите кнопку чтобы начать 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Начать регистрацию", callback_data="show_rules")]
            ])
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Allow buy_ callbacks from group chat
    if query.message.chat.type != "private" and not data.startswith("buy_"):
        return
    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id
    username = query.from_user.username or ""
    data = query.data

    logger.info(f"Button: {data} | User: {user_id}")

    # Подтверждение вступления в группу
    if data == "check_join":
        user = get_user(user_id)
        if user:
            user["joined"] = "yes"
            save_user(user_id, user)
        states[user_id] = {"step": "menu"}
        await context.bot.send_message(chat_id=chat_id,
            text=f"✅ Добро пожаловать в KAZMETALS!\n\n"
                 f"👤 {user.get('first_name','') if user else ''} {user.get('last_name','') if user else ''}\n\n"
                 f"Выберите действие 👇",
            reply_markup=main_menu_keyboard())
        return

    if data == "show_rules":
        states[user_id] = {"step": "rules"}
        await context.bot.send_message(chat_id=chat_id,
            text="📋 Правила платформы KAZMETALS\n\n"
                 "1️⃣ Все участники проходят верификацию\n"
                 "2️⃣ Запрещено размещать поддельные лоты\n"
                 "3️⃣ Продавец несёт ответственность за достоверность описания\n"
                 "4️⃣ Сделки совершаются между участниками напрямую\n"
                 "5️⃣ При нарушении правил аккаунт блокируется\n"
                 "6️⃣ Ваши данные хранятся конфиденциально\n\n"
                 "Принимаете правила платформы?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Принимаю правила", callback_data="accept_rules"),
                 InlineKeyboardButton("❌ Отказаться", callback_data="decline_rules")]
            ]))
        return

    if data == "decline_rules":
        states.pop(user_id, None)
        await context.bot.send_message(chat_id=chat_id,
            text="❌ Вы отказались от регистрации.\n\nЕсли передумаете — напишите /start 🙂")
        return

    if data == "accept_rules":
        if not username:
            await context.bot.send_message(chat_id=chat_id,
                text="❌ У вас нет username в Telegram!\n\n"
                     "Без него регистрация невозможна.\n\n"
                     "Как создать username:\n"
                     "1. Откройте Настройки в Telegram\n"
                     "2. Нажмите на ваше имя\n"
                     "3. Нажмите Имя пользователя\n"
                     "4. Придумайте @ник и сохраните\n"
                     "5. Вернитесь и напишите /start")
            return
        states[user_id] = {"step": "confirm_username", "username": username}
        await context.bot.send_message(chat_id=chat_id,
            text=f"🔗 Проверка username\n\nВаш Telegram ник: @{username}\n\nВсё верно?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_username"),
                 InlineKeyboardButton("✏️ Изменить в Telegram", callback_data="change_username")]
            ]))
        return

    if data == "change_username":
        await context.bot.send_message(chat_id=chat_id,
            text="✏️ Измените username в Настройках Telegram, затем напишите /start")
        return

    if data == "confirm_username":
        states[user_id]["step"] = "awaiting_first_name"
        await context.bot.send_message(chat_id=chat_id,
            text="👤 Шаг 1 из 3 — Имя\n\nВведите ваше имя:")
        return

    if data == "send_application":
        u = states.get(user_id, {})
        user_data = {
            "first_name": u.get("first_name", ""),
            "last_name": u.get("last_name", ""),
            "username": u.get("username", username),
            "phone": u.get("phone", ""),
            "status": "active",
            "registered_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "subscriptions": "",
            "rating": 0,
            "reviews_count": 0,
            "joined": "no"
        }
        save_user(user_id, user_data)
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID,
            text=f"🆕 Новый участник — KAZMETALS\n\n"
                 f"👤 Имя: {u.get('first_name','')} {u.get('last_name','')}\n"
                 f"🔗 Username: @{u.get('username', username)}\n"
                 f"📞 Телефон: {u.get('phone','')}\n"
                 f"🆔 Telegram ID: {user_id}")
        states[user_id] = {"step": "awaiting_join"}
        await context.bot.send_message(chat_id=chat_id,
            text="🎉 Регистрация почти завершена!\n\n"
                 "Последний шаг — вступите в группу KAZMETALS 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔓 Вступить в KAZMETALS", url=GROUP_LINK)],
                [InlineKeyboardButton("✅ Я вступил — проверить", callback_data="check_join")]
            ]))
        return

    # ГЛАВНОЕ МЕНЮ
    if data == "menu_ad":
        states[user_id] = {"step": "ad_category", "ad_type": "Продаю"}
        await context.bot.send_message(chat_id=chat_id,
            text="➕ Подать объявление\n\nВыберите категорию:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(cat, callback_data=f"cat_{i}")] for i, cat in enumerate(CATEGORIES)] +
                [[InlineKeyboardButton("🔙 Назад", callback_data="back_menu")]]
            ))
        return

    if data in ["ad_sell"]:
        ad_type = "Продаю"
        states[user_id] = {"step": "ad_category", "ad_type": ad_type}
        await context.bot.send_message(chat_id=chat_id,
            text="Выберите категорию:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(cat, callback_data=f"cat_{i}")] for i, cat in enumerate(CATEGORIES)] +
                [[InlineKeyboardButton("🔙 Назад", callback_data="menu_ad")]]
            ))
        return

    if data.startswith("cat_"):
        idx = int(data.replace("cat_", ""))
        states[user_id]["category"] = CATEGORIES[idx]
        states[user_id]["step"] = "ad_description"
        await context.bot.send_message(chat_id=chat_id,
            text=f"Категория: {CATEGORIES[idx]}\n\nВведите описание товара:")
        return

    if data.startswith("city_"):
        city = data.replace("city_", "")
        if city == "other":
            states[user_id]["step"] = "ad_city_text"
            await context.bot.send_message(chat_id=chat_id,
                text="📍 Введите название вашего города:")
        else:
            states[user_id]["city"] = city
            states[user_id]["step"] = "ad_photo"
            await context.bot.send_message(chat_id=chat_id,
                text=f"✅ Город: {city}\n\n📸 Отправьте фото товара\n(или напишите - чтобы пропустить)")
        return

    if data == "skip_extra_media":
        states[user_id]["step"] = "ad_preview"
        u = states.get(user_id, {})
        extra_count = len(u.get("extra_media", []))
        extra_text = f"\n📸 Доп. медиа: {extra_count} шт." if extra_count > 0 else ""
        preview_text = (
            f"📋 Предварительный просмотр:\n\n"
            f"{u.get('category','')}\n"
            f"📍 {u.get('city','')}\n\n"
            f"{u.get('description','')}\n\n"
            f"💰 Цена: {u.get('price','')} KZT{extra_text}"
        )
        await context.bot.send_message(chat_id=chat_id,
            text=preview_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Опубликовать", callback_data="ad_confirm"),
                 InlineKeyboardButton("❌ Отменить", callback_data="ad_cancel")]
            ]))
        return

    if data == "ad_confirm":
        u = states.get(user_id, {})
        user = get_user(user_id)
        uname = user.get("username", username) if user else username
        emoji = "🟢" if u.get("ad_type") == "Продаю" else "🔵"
        topic_id = CATEGORY_TOPICS.get(u.get("category", ""))
        try:
            kwargs = {"message_thread_id": topic_id} if topic_id else {}
            price_text = u.get("price", "")

            # Сначала сохраняем объявление чтобы получить ad_id
            ad_id = save_ad({
                "user_id": user_id, "username": uname,
                "type": u.get("ad_type"), "category": u.get("category"),
                "description": u.get("description"), "price": price_text,
                "photo_id": u.get("photo_id", ""),
                "city": u.get("city", ""),
                "message_id": "0", "media_message_id": "0"
            })

            # Получаем рейтинг продавца
            seller_user = get_user(user_id)
            seller_rating = seller_user.get("rating", 0) if seller_user else 0
            seller_deals = seller_user.get("reviews_count", 0) if seller_user else 0
            city_line = f"\n📍 {u.get('city','')}" if u.get('city') else ""

            # Чистый текст объявления
            ad_text = (
                f"№ {ad_id}\n\n"
                f"{u.get('description','').upper()}\n\n"
                f"💰 Цена: {price_text} KZT\n"
                f"⭐ Рейтинг продавца: {seller_rating} | Сделок: {seller_deals}"
                f"{city_line}"
            )

            # Публикуем доп. медиа в медиа канал
            media_msg_id = ""
            extra_media = u.get("extra_media", [])
            if extra_media:
                try:
                    media_caption = f"📦 Доп. медиа к объявлению #{ad_id}\n{u.get('category', '')} — {price_text}"
                    first = extra_media[0]
                    if first["type"] == "photo":
                        media_msg = await context.bot.send_photo(
                            chat_id=MEDIA_CHANNEL_ID, photo=first["file_id"], caption=media_caption)
                    else:
                        media_msg = await context.bot.send_video(
                            chat_id=MEDIA_CHANNEL_ID, video=first["file_id"], caption=media_caption)
                    media_msg_id = str(media_msg.message_id)
                    for m in extra_media[1:]:
                        if m["type"] == "photo":
                            await context.bot.send_photo(chat_id=MEDIA_CHANNEL_ID, photo=m["file_id"])
                        else:
                            await context.bot.send_video(chat_id=MEDIA_CHANNEL_ID, video=m["file_id"])
                except Exception as e:
                    logger.error(f"Media channel error: {e}")
                    media_msg_id = ""

            # Формируем кнопки
            buttons = []
            if media_msg_id:
                buttons.append([InlineKeyboardButton("📸 Фото и видео",
                    url=f"https://t.me/c/{str(MEDIA_CHANNEL_ID).replace('-100','')}/{media_msg_id}")])
            buttons.append([InlineKeyboardButton(
                f"💰 Купить за {price_text}",
                url=f"https://t.me/KAZ_METALSbot?start=buy_{user_id}_{ad_id}")])
            buy_markup = InlineKeyboardMarkup(buttons)

            if u.get("photo_id"):
                sent_msg = await context.bot.send_photo(chat_id=GROUP_ID, photo=u["photo_id"],
                    caption=ad_text, reply_markup=buy_markup, **kwargs)
            else:
                sent_msg = await context.bot.send_message(chat_id=GROUP_ID, text=ad_text,
                    reply_markup=buy_markup, **kwargs)

            # Обновляем message_id в базе
            try:
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("UPDATE ads SET message_id = %s, media_message_id = %s WHERE ad_id = %s",
                    (str(sent_msg.message_id), media_msg_id, ad_id))
                conn.commit()
                cur.close()
                conn.close()
            except Exception as e:
                logger.error(f"Update message_id error: {e}")

            await context.bot.send_message(chat_id=chat_id,
                text=f"✅ Объявление опубликовано! Номер: #{ad_id}",
                reply_markup=main_menu_keyboard())
        except Exception as e:
            logger.error(f"Publish error: {e}")
            await context.bot.send_message(chat_id=chat_id,
                text=f"⚠️ Ошибка публикации: {e}", reply_markup=main_menu_keyboard())
        return

    if data == "ad_cancel":
        states[user_id] = {"step": "menu"}
        await context.bot.send_message(chat_id=chat_id,
            text="❌ Отменено.", reply_markup=main_menu_keyboard())
        return

    if data == "buy_cancel":
        await context.bot.send_message(chat_id=chat_id, text="❌ Отменено.")
        return

    if data.startswith("buy_confirm_"):
        rest = data.replace("buy_confirm_", "")
        parts = rest.split("_")
        seller_id = parts[0]
        ad_id_confirm = parts[1] if len(parts) > 1 else "0"
        if seller_id == user_id:
            await query.answer("Это ваше объявление!", show_alert=True)
            return
        seller = get_user(seller_id)
        buyer = get_user(user_id)
        seller_fname = seller.get("first_name", "") if seller else ""
        seller_lname = seller.get("last_name", "") if seller else ""
        seller_uname = seller.get("username", "") if seller else ""
        seller_phone = seller.get("phone", "") if seller else ""
        buyer_fname = buyer.get("first_name", "") if buyer else ""
        buyer_lname = buyer.get("last_name", "") if buyer else ""
        buyer_uname = buyer.get("username", username) if buyer else username
        buyer_phone = buyer.get("phone", "") if buyer else ""

        # Получаем информацию об объявлении
        ad_info = ""
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM ads WHERE ad_id = %s", (int(ad_id_confirm),))
            ad = cur.fetchone()
            cur.close()
            conn.close()
            if ad:
                ad_info = f"\n\n📦 Объявление: {ad.get('category','')}\n📝 {ad.get('description','')[:50]}\n💰 {ad.get('price','')}"
        except:
            pass

        save_deal(seller_id, user_id, ad_id_confirm)

        # Покупателю — данные продавца + объявление
        await context.bot.send_message(chat_id=int(user_id),
            text=f"✅ Заявка отправлена продавцу!{ad_info}\n\n"
                 f"👤 Продавец: {seller_fname} {seller_lname}\n"
                 f"🔗 @{seller_uname}\n"
                 f"📞 Телефон: {seller_phone}\n\n"
                 f"Свяжитесь напрямую!\n\n"
                 f"Через 7 дней придёт запрос на оценку сделки.")

        # Продавцу — данные покупателя + объявление
        await context.bot.send_message(chat_id=int(seller_id),
            text=f"🔔 Новый покупатель на ваш товар!{ad_info}\n\n"
                 f"👤 Покупатель: {buyer_fname} {buyer_lname}\n"
                 f"🔗 @{buyer_uname}\n"
                 f"📞 Телефон: {buyer_phone}\n\n"
                 f"Свяжитесь напрямую!\n\n"
                 f"Через 7 дней придёт запрос на оценку сделки.")
        return

    if data == "menu_prices":
        await query.answer()
        await context.bot.send_message(chat_id=chat_id,
            text="⏳ Получаю актуальные цены...")
        prices, usd_kzt = await get_metal_prices()
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        if prices:
            text = f"📊 Цены на металлы\n🕐 {now}\n💵 USD/KZT: {usd_kzt}\n\n"
            for symbol, (name, price, unit) in prices.items():
                text += f"{name}: {price:,} KZT/{unit}\n"
            text += "\nЦены за 1 грамм"
        else:
            text = "⚠️ Не удалось получить цены. Попробуйте позже."
        await context.bot.send_message(chat_id=chat_id, text=text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]]))
        return

    if data == "menu_subs":
        user = get_user(user_id)
        subs_str = user.get("subscriptions", "") if user else ""
        subs = subs_str.split(",") if subs_str else []
        states[user_id] = {"step": "subs", "subs": list(subs)}
        keyboard = []
        for cat in CATEGORIES:
            check = "☑️" if cat in subs else "☐"
            keyboard.append([InlineKeyboardButton(f"{check} {cat}", callback_data=f"sub_{cat}")])
        keyboard.append([InlineKeyboardButton("💾 Сохранить", callback_data="sub_save")])
        keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")])
        await context.bot.send_message(chat_id=chat_id,
            text="🔔 Мои подписки\n\nВыберите категории для уведомлений:",
            reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("sub_") and data != "sub_save":
        cat = data.replace("sub_", "")
        subs = states.get(user_id, {}).get("subs", [])
        if cat in subs:
            subs.remove(cat)
        else:
            subs.append(cat)
        states[user_id]["subs"] = subs
        keyboard = []
        for c in CATEGORIES:
            check = "☑️" if c in subs else "☐"
            keyboard.append([InlineKeyboardButton(f"{check} {c}", callback_data=f"sub_{c}")])
        keyboard.append([InlineKeyboardButton("💾 Сохранить", callback_data="sub_save")])
        keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "sub_save":
        subs = states.get(user_id, {}).get("subs", [])
        user = get_user(user_id)
        if user:
            user["subscriptions"] = ",".join(subs)
            save_user(user_id, user)
        await context.bot.send_message(chat_id=chat_id,
            text=f"✅ Подписки сохранены!\n\n{', '.join(subs) if subs else 'Нет подписок'}",
            reply_markup=main_menu_keyboard())
        return

    if data == "menu_rep":
        user = get_user(user_id)
        rating = float(user.get("rating", 0)) if user else 0
        reviews = user.get("reviews_count", 0) if user else 0
        stars = "⭐" * int(rating) if rating > 0 else "нет оценок"
        await context.bot.send_message(chat_id=chat_id,
            text=f"⭐ Моя репутация\n\n"
                 f"Рейтинг: {stars} ({rating})\n"
                 f"Отзывов: {reviews}\n\n"
                 f"Репутация формируется после завершённых сделок.",
            reply_markup=main_menu_keyboard())
        return

    if data == "menu_profile":
        user = get_user(user_id)
        if user:
            ads = get_user_ads(user_id)
            active = [a for a in ads if a.get("status") == "active"]
            await context.bot.send_message(chat_id=chat_id,
                text=f"👤 Мой профиль\n\n"
                     f"Имя: {user.get('first_name','')} {user.get('last_name','')}\n"
                     f"Username: @{user.get('username','')}\n"
                     f"Телефон: {user.get('phone','')}\n"
                     f"✅ Статус: Верифицирован\n"
                     f"📅 Регистрация: {user.get('registered_at','')}\n"
                     f"📢 Активных объявлений: {len(active)}\n"
                     f"⭐ Рейтинг: {user.get('rating',0)} (отзывов: {user.get('reviews_count',0)})",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Мои объявления", callback_data="my_ads")],
                    [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
                ]))
        return

    if data == "my_ads":
        ads = get_user_ads(user_id)
        active = [a for a in ads if a.get("status") == "active"]
        if not active:
            await context.bot.send_message(chat_id=chat_id,
                text="📢 Мои объявления\n\nУ вас нет активных объявлений.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu_profile")]]))
            return
        keyboard = []
        for a in active[:8]:
            label = f"🟢 {a.get('category','')} — {a.get('price','')} | {a.get('description','')[:20]}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"ad_view_{a.get('ad_id')}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="menu_profile")])
        await context.bot.send_message(chat_id=chat_id,
            text=f"📢 Мои объявления\n\nАктивных: {len(active)}\n\nНажмите на объявление чтобы удалить:",
            reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("ad_view_"):
        ad_id = int(data.replace("ad_view_", ""))
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM ads WHERE ad_id = %s AND user_id = %s", (ad_id, str(user_id)))
            ad = cur.fetchone()
            cur.close()
            conn.close()
            if ad:
                cat = ad.get('category','')
                desc = ad.get('description','')
                price = ad.get('price','')
                created = ad.get('created_at','')
                await context.bot.send_message(chat_id=chat_id,
                    text=f"🟢 {cat}\n\n{desc}\n\n💰 {price}\n📅 {created}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🗑 Удалить объявление", callback_data=f"del_ad_{ad_id}")],
                        [InlineKeyboardButton("🔙 Назад", callback_data="my_ads")]
                    ]))
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Ошибка: {e}")
        return

    if data.startswith("del_ad_"):
        ad_id = int(data.replace("del_ad_", ""))
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM ads WHERE ad_id = %s AND user_id = %s", (ad_id, str(user_id)))
            ad = cur.fetchone()
            if ad:
                # Удаляем из группы
                msg_id = ad.get("message_id", "")
                if msg_id:
                    try:
                        await context.bot.delete_message(chat_id=GROUP_ID, message_id=int(msg_id))
                    except Exception as e:
                        logger.error(f"Delete from group error: {e}")
                # Помечаем как удалённое в базе
                cur2 = conn.cursor()
                cur2.execute("UPDATE ads SET status = 'deleted' WHERE ad_id = %s", (ad_id,))
                conn.commit()
                cur2.close()
                cur.close()
                conn.close()
                await context.bot.send_message(chat_id=chat_id,
                    text=f"✅ Объявление #{ad_id} удалено из группы и из списка.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Мои объявления", callback_data="my_ads")]]))
            else:
                cur.close()
                conn.close()
                await context.bot.send_message(chat_id=chat_id,
                    text=f"⚠️ Объявление #{ad_id} не найдено.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Мои объявления", callback_data="my_ads")]]))
        except Exception as e:
            logger.error(f"del_ad error: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Ошибка: {e}")
        return

    if data == "menu_how":
        await context.bot.send_message(chat_id=chat_id,
            text="📋 Как работает KAZMETALS\n\n"
                 "🔷 Для продавца:\n"
                 "1. Нажмите Подать объявление\n"
                 "2. Выберите тип и категорию\n"
                 "3. Опишите товар, укажите цену и фото\n"
                 "4. Объявление сразу появится в группе\n"
                 "5. Покупатель нажмёт Хочу купить и получит ваш контакт\n\n"
                 "🔷 Для покупателя:\n"
                 "1. Зайдите в группу KAZ_METALS\n"
                 "2. Найдите нужное объявление\n"
                 "3. Нажмите Хочу купить\n"
                 "4. Получите контакт продавца и договоритесь напрямую\n\n"
                 "⭐ После сделки через 7 дней придёт запрос на оценку\n\n"
                 "⚠️ KAZMETALS — информационный посредник.\n"
                 "Мы не несём ответственности за сделки между участниками.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"📞 @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
            ]))
        return

    if data == "menu_admin":
        await context.bot.send_message(chat_id=chat_id,
            text=f"📞 Связь с администратором\n\n"
                 f"👉 @{ADMIN_USERNAME}\n\n"
                 f"Пн-Пт 9:00 - 20:00 (Алматы)\n"
                 f"Сб-Вс 10:00 - 18:00 (Алматы)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✉️ Написать @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
            ]))
        return

    if data == "back_menu":
        states[user_id] = {"step": "menu"}
        await context.bot.send_message(chat_id=chat_id,
            text="🏅 Главное меню KAZMETALS", reply_markup=main_menu_keyboard())
        return

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    text = update.message.text.strip()

    if user_id not in states:
        user = get_user(user_id)
        if user and user.get("status") == "active":
            states[user_id] = {"step": "menu"}
        else:
            await update.message.reply_text("Напишите /start чтобы начать.")
            return

    step = states[user_id].get("step")

    if step == "awaiting_first_name":
        states[user_id]["first_name"] = text
        states[user_id]["step"] = "awaiting_last_name"
        await update.message.reply_text(f"👍 Имя {text} сохранено!\n\n👤 Шаг 2 из 3\n\nВведите фамилию:")
        return

    if step == "awaiting_last_name":
        states[user_id]["last_name"] = text
        states[user_id]["step"] = "awaiting_phone"
        await update.message.reply_text(
            f"👍 Фамилия {text} сохранена!\n\n📞 Шаг 3 из 3\n\nВведите номер телефона:\n+77001234567",
            reply_markup=ReplyKeyboardRemove())
        return

    if step == "awaiting_phone":
        states[user_id]["phone"] = text
        states[user_id]["step"] = "confirm_application"
        u = states[user_id]
        await update.message.reply_text(
            "🏁 Регистрация завершена!\n\n"
            f"👤 Имя: {u.get('first_name','')} {u.get('last_name','')}\n"
            f"🔗 Username: @{u.get('username','')}\n"
            f"📞 Телефон: {text}\n\n"
            "Нажмите кнопку чтобы вступить в KAZMETALS 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📨 Отправить заявку на вступление в канал", callback_data="send_application")]
            ]))
        return

    if step == "ad_description":
        states[user_id]["description"] = text
        states[user_id]["step"] = "ad_price"
        await update.message.reply_text("💰 Введите цену:\n(например: 150 000 KZT или торг)")
        return

    if step == "ad_price":
        states[user_id]["price"] = text
        states[user_id]["step"] = "ad_city"
        await update.message.reply_text(
            "📍 Укажите город продажи:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏙 Алматы", callback_data="city_Алматы"),
                 InlineKeyboardButton("🏛 Астана", callback_data="city_Астана")],
                [InlineKeyboardButton("🌆 Шымкент", callback_data="city_Шымкент"),
                 InlineKeyboardButton("🏘 Другой город", callback_data="city_other")]
            ])
        )
        return

    if step == "ad_city_text":
        states[user_id]["city"] = text
        states[user_id]["step"] = "ad_description"
        await update.message.reply_text(
            f"✅ Город: {text}\n\n📝 Введите описание товара:\n(например: Золотое кольцо 585 проба, 5г)")
        return

    if step == "ad_photo" and text == "-":
        states[user_id]["photo_id"] = ""
        states[user_id]["extra_media"] = []
        states[user_id]["step"] = "ad_extra_media"
        await update.message.reply_text(
            "📸 Хотите добавить дополнительные фото и видео?\n\n"
            "Отправьте фото/видео (можно несколько) или нажмите Пропустить",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭ Пропустить", callback_data="skip_extra_media")]
            ]))
        return

    if step == "ad_extra_media" and text == "-":
        await show_ad_preview(update, context, user_id)
        return

    await update.message.reply_text("Следуйте инструкциям. Если что-то пошло не так — напишите /start")

async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    step = states.get(user_id, {}).get("step")
    if step == "ad_extra_media":
        video_id = update.message.video.file_id
        if "extra_media" not in states[user_id]:
            states[user_id]["extra_media"] = []
        states[user_id]["extra_media"].append({"type": "video", "file_id": video_id})
        count = len(states[user_id]["extra_media"])
        await update.message.reply_text(
            f"✅ Видео добавлено! Всего медиа: {count}\n\nДобавьте ещё или нажмите Готово",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово", callback_data="skip_extra_media")]
            ]))

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    step = states.get(user_id, {}).get("step")

    if step == "ad_extra_media":
        photo_id = update.message.photo[-1].file_id
        if "extra_media" not in states[user_id]:
            states[user_id]["extra_media"] = []
        states[user_id]["extra_media"].append({"type": "photo", "file_id": photo_id})
        count = len(states[user_id]["extra_media"])
        await update.message.reply_text(
            f"✅ Фото добавлено! Всего медиа: {count}\n\nДобавьте ещё или нажмите Готово",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово", callback_data="skip_extra_media")]
            ]))
        return

    if step == "ad_photo":
        photo_id = update.message.photo[-1].file_id
        states[user_id]["photo_id"] = photo_id
        states[user_id]["extra_media"] = []
        states[user_id]["step"] = "ad_extra_media"
        await update.message.reply_text(
            "✅ Главное фото сохранено!\n\n"
            "📸 Хотите добавить дополнительные фото и видео?\n"
            "Отправьте медиа или нажмите Пропустить",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭ Пропустить", callback_data="skip_extra_media")]
            ]))

async def publish_prices(bot):
    prices, usd_kzt = await get_metal_prices()
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    if prices:
        text = f"📊 Актуальные цены на металлы\n🕐 {now}\n💵 USD/KZT: {usd_kzt}\n\n"
        for symbol, (name, price, unit) in prices.items():
            text += f"{name}: {price:,} KZT/{unit}\n"
        text += "\nЦены за 1 грамм | Обновляется ежедневно"
        try:
            topic_id = CATEGORY_TOPICS.get("📈 Инвестиции и цены")
            msg = await bot.send_message(
                chat_id=GROUP_ID,
                text=text,
                message_thread_id=topic_id
            )
            # Закрепляем сообщение
            await bot.pin_chat_message(
                chat_id=GROUP_ID,
                message_id=msg.message_id,
                disable_notification=True
            )
            logger.info("Prices published and pinned!")
        except Exception as e:
            logger.error(f"Publish prices error: {e}")

async def daily_prices(context: ContextTypes.DEFAULT_TYPE):
    await publish_prices(context.bot)

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VIDEO, video_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Публикуем цены сразу при старте
    app.job_queue.run_once(daily_prices, when=10)
    # Ежедневная публикация цен в 9:00 по Алматы (UTC+5 = 04:00 UTC)
    from datetime import time as dt_time
    app.job_queue.run_daily(daily_prices, time=dt_time(hour=4, minute=0))

    logger.info("KAZMETALS Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
