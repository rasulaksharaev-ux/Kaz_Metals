import os
import logging
import psycopg2
import psycopg2.extras
import aiohttp
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== Railway Variables + диагностика Railway =====
# ВАЖНО: реальные токены/пароли не должны храниться в коде.
# Их нужно указывать только в Railway -> Variables.
#
# В этой версии добавлены отдельные имена переменных:
#   KAZMETALS_BOT_TOKEN
#   KAZMETALS_DATABASE_URL
# Они читаются ПЕРВЫМИ. Это помогает обойти ситуацию, когда Railway продолжает
# подхватывать старые BOT_TOKEN/DATABASE_URL из другого места/сервиса/окружения.

def clean_env_value(value: str | None) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    if (value.startswith('\"') and value.endswith('\"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1].strip()
    return value

def get_first_env(*names: str) -> tuple[str, str]:
    for name in names:
        value = clean_env_value(os.environ.get(name))
        if value:
            return value, name
    return "", names[0] if names else ""

def get_int_env(name: str, default: int = 0) -> int:
    value = clean_env_value(os.environ.get(name))
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning(f"Переменная {name} должна быть числом. Сейчас указано: {value!r}. Использую {default}.")
        return default

def mask_token(token: str) -> str:
    if not token:
        return "NONE"
    token = clean_env_value(token)
    if len(token) <= 16:
        return token[:4] + "..."
    return token[:10] + "..." + token[-6:]

def mask_db_url(url: str) -> str:
    if not url:
        return "NONE"
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        username = parts.username or "user"
        safe_netloc = f"{username}:***@{host}{port}"
        return urlunsplit((parts.scheme, safe_netloc, parts.path, "", ""))
    except Exception:
        return url.replace(url, "<invalid DATABASE_URL format>")

TOKEN, TOKEN_SOURCE = get_first_env("KAZMETALS_BOT_TOKEN", "BOT_TOKEN")
DATABASE_URL, DATABASE_URL_SOURCE = get_first_env("KAZMETALS_DATABASE_URL", "DATABASE_URL")

GOLDAPI_KEY = clean_env_value(os.environ.get("GOLDAPI_KEY"))
ADMIN_CHAT_ID = get_int_env("ADMIN_CHAT_ID", 0)
ADMIN_USERNAME = clean_env_value(os.environ.get("ADMIN_USERNAME"))
GROUP_LINK = clean_env_value(os.environ.get("GROUP_LINK"))
GROUP_ID = get_int_env("GROUP_ID", 0)
MEDIA_CHANNEL_ID = get_int_env("MEDIA_CHANNEL_ID", 0)
BOT_USERNAME = clean_env_value(os.environ.get("BOT_USERNAME")) or "KAZ_METALSbot"
MEDIA_CHANNEL_USERNAME = clean_env_value(os.environ.get("MEDIA_CHANNEL_USERNAME")) or "kaz_metalsphoto"
AUTO_PUBLISH_PRICES = clean_env_value(os.environ.get("AUTO_PUBLISH_PRICES")) or "true"
AUTO_PUBLISH_PRICES = AUTO_PUBLISH_PRICES.lower() in ("1", "true", "yes", "y")

logger.error("=== RAILWAY ENV CHECK START ===")
logger.error(f"TOKEN_SOURCE={TOKEN_SOURCE}; BOT_TOKEN_MASKED={mask_token(TOKEN)}")
logger.error(f"DATABASE_URL_SOURCE={DATABASE_URL_SOURCE}; DATABASE_URL_MASKED={mask_db_url(DATABASE_URL)}")
logger.error(f"ADMIN_CHAT_ID={ADMIN_CHAT_ID}; GROUP_ID={GROUP_ID}; MEDIA_CHANNEL_ID={MEDIA_CHANNEL_ID}")
logger.error("=== RAILWAY ENV CHECK END ===")

if not TOKEN:
    raise RuntimeError("Не задан токен бота. Добавьте KAZMETALS_BOT_TOKEN в Railway Variables.")
if not DATABASE_URL:
    raise RuntimeError("Не задан URL базы. Добавьте KAZMETALS_DATABASE_URL в Railway Variables.")
if "PORT" in DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL всё ещё содержит слово PORT. Вставьте реальный PostgreSQL URL с портом 5432. "
        "Лучше используйте новую переменную KAZMETALS_DATABASE_URL."
    )
if "USER" in DATABASE_URL or "PASSWORD" in DATABASE_URL or "HOST" in DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL похож на шаблон USER/PASSWORD/HOST/PORT/DATABASE, а не на реальный URL. "
        "Вставьте реальный PostgreSQL URL из Railway."
    )

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
                joined TEXT DEFAULT 'no',
                role TEXT DEFAULT 'user'
            )
        """)
        for col_sql in [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS joined TEXT DEFAULT 'no'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user'",
        ]:
            try:
                cur.execute(col_sql)
                conn.commit()
            except Exception:
                conn.rollback()

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
                city TEXT DEFAULT '',
                expires_at TEXT DEFAULT ''
            )
        """)
        for col_sql in [
            "ALTER TABLE ads ADD COLUMN IF NOT EXISTS message_id TEXT DEFAULT ''",
            "ALTER TABLE ads ADD COLUMN IF NOT EXISTS media_message_id TEXT DEFAULT ''",
            "ALTER TABLE ads ADD COLUMN IF NOT EXISTS city TEXT DEFAULT ''",
            "ALTER TABLE ads ADD COLUMN IF NOT EXISTS expires_at TEXT DEFAULT ''",
        ]:
            try:
                cur.execute(col_sql)
                conn.commit()
            except Exception:
                conn.rollback()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                deal_id SERIAL PRIMARY KEY,
                seller_id TEXT,
                buyer_id TEXT,
                ad_id TEXT,
                created_at TEXT,
                status TEXT DEFAULT 'contact_opened',
                notified BOOLEAN DEFAULT FALSE
            )
        """)
        # Если таблица deals была создана старой версией бота, добавляем новые колонки.
        for col_sql in [
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'contact_opened'",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS notified BOOLEAN DEFAULT FALSE",
        ]:
            try:
                cur.execute(col_sql)
                conn.commit()
            except Exception:
                conn.rollback()

        # ===== ИСПРАВЛЕНИЕ #6: Таблица отзывов =====
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                review_id SERIAL PRIMARY KEY,
                deal_id TEXT,
                from_user_id TEXT,
                to_user_id TEXT,
                rating INTEGER,
                comment TEXT DEFAULT '',
                created_at TEXT
            )
        """)
        # ===== ИСПРАВЛЕНИЕ #16: Таблица жалоб =====
        cur.execute("""
            CREATE TABLE IF NOT EXISTS complaints (
                complaint_id SERIAL PRIMARY KEY,
                ad_id TEXT,
                from_user_id TEXT,
                seller_id TEXT,
                reason TEXT,
                created_at TEXT
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
            INSERT INTO users (user_id, first_name, last_name, username, phone, status, registered_at,
                               subscriptions, rating, reviews_count, joined, role)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                username = EXCLUDED.username,
                phone = EXCLUDED.phone,
                status = EXCLUDED.status,
                subscriptions = EXCLUDED.subscriptions,
                rating = EXCLUDED.rating,
                reviews_count = EXCLUDED.reviews_count,
                joined = EXCLUDED.joined,
                role = EXCLUDED.role
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
            data.get("joined", "no"),
            data.get("role", "user")
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
        # ===== ИСПРАВЛЕНИЕ #11: Срок действия объявления 30 дней =====
        expires = (datetime.now() + timedelta(days=30)).strftime("%d.%m.%Y")
        cur.execute("""
            INSERT INTO ads (user_id, username, type, category, description, price, photo_id,
                             status, created_at, message_id, media_message_id, city, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING ad_id
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
            ad_data.get("city", ""),
            expires
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
    except Exception:
        return []

def save_deal(seller_id, buyer_id, ad_id):
    try:
        conn = get_conn()
        cur = conn.cursor()
        # ===== ИСПРАВЛЕНИЕ #5: Статус сделки contact_opened =====
        cur.execute("""
            INSERT INTO deals (seller_id, buyer_id, ad_id, created_at, status)
            VALUES (%s, %s, %s, %s, %s) RETURNING deal_id
        """, (str(seller_id), str(buyer_id), str(ad_id),
              datetime.now().strftime("%d.%m.%Y %H:%M"), "contact_opened"))
        deal_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return deal_id
    except Exception as e:
        logger.error(f"save_deal error: {e}")
        return None

def is_user_blocked(user_id):
    user = get_user(user_id)
    if not user:
        return False
    return user.get("status") == "blocked"

def is_admin(user_id):
    return str(user_id) == str(ADMIN_CHAT_ID)

states = {}

# ===== ИСПРАВЛЕНИЕ #3: Реальная проверка вступления в группу через Telegram API =====
async def check_user_in_group(bot, user_id: int, group_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=group_id, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"check_user_in_group error: {e}")
        return False

async def show_ad_preview(update_or_msg, context, user_id):
    u = states.get(user_id, {})
    extra_count = len(u.get("extra_media", []))
    extra_text = f"\n📸 Доп. медиа: {extra_count} шт." if extra_count > 0 else ""
    preview_text = (
        f"📋 Предварительный просмотр:\n\n"
        f"🟢 {u.get('ad_type','').upper()}\n"
        f"{u.get('category','')}\n"
        f"📍 {u.get('city','')}\n\n"
        f"{u.get('description','')}\n\n"
        f"💰 Цена: {u.get('price','')} KZT{extra_text}"
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
        usd_kzt = 520.0
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.exchangerate-api.com/v4/latest/USD") as r:
                    data = await r.json()
                    usd_kzt = data.get("rates", {}).get("KZT", 520.0)
        except Exception:
            pass

        metals = {
            "XAU": ("🥇 Золото", "г"),
            "XAG": ("🥈 Серебро", "г"),
            "XPT": ("💎 Платина", "г"),
            "XPD": ("🔩 Палладий", "г"),
        }
        results = {}
        if not GOLDAPI_KEY:
            return results, round(usd_kzt)
        async with aiohttp.ClientSession() as session:
            for symbol, (name, unit) in metals.items():
                try:
                    url = f"https://www.goldapi.io/api/{symbol}/USD"
                    headers = {"x-access-token": GOLDAPI_KEY, "Content-Type": "application/json"}
                    async with session.get(url, headers=headers) as r:
                        data = await r.json()
                        price_usd_oz = data.get("price", 0)
                        price_kzt_g = (price_usd_oz / 31.1035) * usd_kzt
                        results[symbol] = (name, round(price_kzt_g), unit)
                except Exception:
                    pass
        return results, round(usd_kzt)
    except Exception:
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

# ===== ИСПРАВЛЕНИЕ #15: Меню администратора =====
def admin_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Пользователи", callback_data="adm_users")],
        [InlineKeyboardButton("⚠️ Жалобы", callback_data="adm_complaints")],
        [InlineKeyboardButton("📊 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton("🚫 Заблокированные", callback_data="adm_blocked")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)

    # ===== ИСПРАВЛЕНИЕ #14: Проверка блокировки при старте =====
    if is_user_blocked(user_id):
        await update.message.reply_text(
            "🚫 Ваш доступ к KAZMETALS ограничен.\n\n"
            f"Для уточнения причины свяжитесь с администратором: @{ADMIN_USERNAME}"
        )
        return

    # Deep link: предложить цену
    if context.args and context.args[0].startswith("offer_"):
        parts = context.args[0].split("_")
        seller_id = parts[1] if len(parts) > 1 else ""
        ad_id_link = parts[2] if len(parts) > 2 else "0"
        if seller_id == user_id:
            await update.message.reply_text("❌ Это ваше объявление!")
            return
        states[user_id] = {"step": "offer_price", "seller_id": seller_id, "ad_id": ad_id_link}
        ad_info = ""
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM ads WHERE ad_id = %s", (int(ad_id_link),))
            ad = cur.fetchone()
            cur.close()
            conn.close()
            if ad:
                ad_info = f"\n\n📦 Объявление: {ad.get('description','')[:50]}\n💰 Цена продавца: {ad.get('price','')}"
        except Exception:
            pass
        # ===== ИСПРАВЛЕНИЕ #23: Подсказка — только цифры =====
        await update.message.reply_text(
            f"🤝 Предложить свою цену{ad_info}\n\n"
            "Введите вашу цену в KZT только цифрами (например: 600000):"
        )
        return

    # Deep link: купить/связаться
    if context.args and context.args[0].startswith("buy_"):
        parts = context.args[0].split("_")
        seller_id = parts[1] if len(parts) > 1 else ""
        ad_id_link = parts[2] if len(parts) > 2 else "0"
        if seller_id == user_id:
            await update.message.reply_text("❌ Это ваше объявление!")
            return
        ad_info = ""
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM ads WHERE ad_id = %s", (int(ad_id_link),))
            ad = cur.fetchone()
            cur.close()
            conn.close()
            if ad:
                ad_info = f"\n\n📦 {ad.get('category','')}\n📝 {ad.get('description','')[:50]}\n💰 {ad.get('price','')}"
        except Exception:
            pass
        # ===== ИСПРАВЛЕНИЕ #22: Кнопка "Связаться с продавцом" =====
        await update.message.reply_text(
            f"Вы хотите связаться с продавцом?{ad_info}\n\n"
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
            "⚠️ Вы зарегистрированы, но ещё не вступили в группу!\n\n"
            "Вступите в группу, чтобы получить доступ к платформе 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔓 Вступить в KAZMETALS", url=GROUP_LINK)],
                [InlineKeyboardButton("✅ Я вступил — проверить", callback_data="check_join")]
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
            "Нажмите кнопку, чтобы начать 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Начать регистрацию", callback_data="show_rules")]
            ])
        )

# ===== ИСПРАВЛЕНИЕ #29: Команды /help и /rules =====
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    await update.message.reply_text(
        "ℹ️ Помощь по KAZMETALS\n\n"
        "/start — Главное меню\n"
        "/help — Эта справка\n"
        "/rules — Правила платформы\n\n"
        f"По всем вопросам: @{ADMIN_USERNAME}"
    )

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    await update.message.reply_text(
        "📋 Правила платформы KAZMETALS\n\n"
        "1️⃣ Все участники проходят верификацию\n"
        "2️⃣ Запрещено размещать поддельные лоты\n"
        "3️⃣ Продавец несёт ответственность за достоверность описания\n"
        "4️⃣ Сделки совершаются между участниками напрямую\n"
        "5️⃣ При нарушении правил аккаунт блокируется\n"
        "6️⃣ Ваши данные хранятся конфиденциально\n\n"
        # ===== ИСПРАВЛЕНИЕ #18: Юридические дисклеймеры =====
        "⚖️ Юридический дисклеймер:\n\n"
        "KAZMETALS не является продавцом, покупателем, оценщиком, ломбардом, "
        "ювелирной организацией или финансовым посредником.\n\n"
        "Сделки совершаются напрямую между пользователями.\n\n"
        "Пользователи самостоятельно проверяют подлинность товара, документы, "
        "пробу, вес, состояние и законность происхождения товара.\n\n"
        "Администрация вправе удалить объявление, ограничить доступ пользователя "
        "или запросить дополнительную информацию при наличии подозрений.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
        ])
    )

# ===== ИСПРАВЛЕНИЕ #15: Команда /admin — только для администратора =====
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этому разделу.")
        return
    await update.message.reply_text(
        "🔧 Панель администратора KAZMETALS\n\nВыберите раздел:",
        reply_markup=admin_menu_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # ===== ИСПРАВЛЕНИЕ #8: data объявляется ДО первого использования =====
    data = query.data
    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id
    username = query.from_user.username or ""

    if query.message.chat.type != "private" and not data.startswith("buy_"):
        return

    logger.info(f"Button: {data} | User: {user_id}")

    # ===== ИСПРАВЛЕНИЕ #14: Блокировка =====
    if is_user_blocked(user_id):
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🚫 Ваш доступ к KAZMETALS ограничен.\n\nСвяжитесь с администратором: @{ADMIN_USERNAME}"
        )
        return

    # ===== ИСПРАВЛЕНИЕ #3: Реальная проверка членства в группе =====
    if data == "check_join":
        in_group = await check_user_in_group(context.bot, int(user_id), GROUP_ID)
        if in_group:
            user = get_user(user_id)
            if user:
                user["joined"] = "yes"
                save_user(user_id, user)
            states[user_id] = {"step": "menu"}
            fname = user.get('first_name', '') if user else ''
            lname = user.get('last_name', '') if user else ''
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Проверка пройдена. Добро пожаловать в KAZMETALS!\n\n"
                     f"👤 {fname} {lname}\n\nВыберите действие 👇",
                reply_markup=main_menu_keyboard()
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Мы пока не видим вас в группе. Пожалуйста, вступите и нажмите кнопку ещё раз.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔓 Вступить в KAZMETALS", url=GROUP_LINK)],
                    [InlineKeyboardButton("✅ Я вступил — проверить", callback_data="check_join")]
                ])
            )
        return

    if data == "show_rules":
        states[user_id] = {"step": "rules"}
        await context.bot.send_message(
            chat_id=chat_id,
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
            ])
        )
        return

    if data == "decline_rules":
        states.pop(user_id, None)
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Вы отказались от регистрации.\n\nЕсли передумаете — напишите /start 🙂"
        )
        return

    if data == "accept_rules":
        if not username:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ У вас нет username в Telegram!\n\n"
                     "Без него регистрация невозможна.\n\n"
                     "Как создать username:\n"
                     "1. Откройте Настройки в Telegram\n"
                     "2. Нажмите на ваше имя\n"
                     "3. Нажмите Имя пользователя\n"
                     "4. Придумайте @ник и сохраните\n"
                     "5. Вернитесь и напишите /start"
            )
            return
        states[user_id] = {"step": "confirm_username", "username": username}
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔗 Проверка username\n\nВаш Telegram ник: @{username}\n\nВсё верно?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_username"),
                 InlineKeyboardButton("✏️ Изменить в Telegram", callback_data="change_username")]
            ])
        )
        return

    if data == "change_username":
        await context.bot.send_message(
            chat_id=chat_id,
            text="✏️ Измените username в Настройках Telegram, затем напишите /start"
        )
        return

    if data == "confirm_username":
        u = states.get(user_id, {})
        user_data = {
            "first_name": "", "last_name": "",
            "username": u.get("username", username),
            "phone": "", "status": "active",
            "registered_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "subscriptions": "", "rating": 0, "reviews_count": 0,
            "joined": "no", "role": "user"
        }
        save_user(user_id, user_data)
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🆕 Новый участник — KAZMETALS\n\n"
                 f"🔗 Username: @{u.get('username', username)}\n"
                 f"🆔 Telegram ID: {user_id}"
        )
        states[user_id] = {"step": "awaiting_join"}
        await context.bot.send_message(
            chat_id=chat_id,
            text="🎉 Регистрация почти завершена!\n\nПоследний шаг — вступите в группу KAZMETALS 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔓 Вступить в KAZMETALS", url=GROUP_LINK)],
                [InlineKeyboardButton("✅ Я вступил — проверить", callback_data="check_join")]
            ])
        )
        return

    if data == "send_application":
        u = states.get(user_id, {})
        user_data = {
            "first_name": u.get("first_name", ""), "last_name": u.get("last_name", ""),
            "username": u.get("username", username), "phone": u.get("phone", ""),
            "status": "active",
            "registered_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "subscriptions": "", "rating": 0, "reviews_count": 0,
            "joined": "no", "role": "user"
        }
        save_user(user_id, user_data)
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🆕 Новый участник — KAZMETALS\n\n"
                 f"👤 Имя: {u.get('first_name','')} {u.get('last_name','')}\n"
                 f"🔗 Username: @{u.get('username', username)}\n"
                 f"📞 Телефон: {u.get('phone','')}\n"
                 f"🆔 Telegram ID: {user_id}"
        )
        states[user_id] = {"step": "awaiting_join"}
        await context.bot.send_message(
            chat_id=chat_id,
            text="🎉 Регистрация почти завершена!\n\nПоследний шаг — вступите в группу KAZMETALS 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔓 Вступить в KAZMETALS", url=GROUP_LINK)],
                [InlineKeyboardButton("✅ Я вступил — проверить", callback_data="check_join")]
            ])
        )
        return

    # ===== ГЛАВНОЕ МЕНЮ =====
    if data == "menu_ad":
        states[user_id] = {"step": "ad_category", "ad_type": "Продаю"}
        await context.bot.send_message(
            chat_id=chat_id,
            text="➕ Подать объявление\n\nВыберите категорию:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(cat, callback_data=f"cat_{i}")] for i, cat in enumerate(CATEGORIES)] +
                [[InlineKeyboardButton("🔙 Назад", callback_data="back_menu")]]
            )
        )
        return

    if data == "ad_sell":
        states[user_id] = {"step": "ad_category", "ad_type": "Продаю"}
        await context.bot.send_message(
            chat_id=chat_id,
            text="Выберите категорию:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(cat, callback_data=f"cat_{i}")] for i, cat in enumerate(CATEGORIES)] +
                [[InlineKeyboardButton("🔙 Назад", callback_data="menu_ad")]]
            )
        )
        return

    if data.startswith("cat_"):
        idx = int(data.replace("cat_", ""))
        states[user_id]["category"] = CATEGORIES[idx]
        states[user_id]["step"] = "ad_description"
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Категория: {CATEGORIES[idx]}\n\nВведите описание товара:"
        )
        return

    if data.startswith("city_"):
        city = data.replace("city_", "")
        if city == "other":
            states[user_id]["step"] = "ad_city_text"
            await context.bot.send_message(chat_id=chat_id, text="📍 Введите название вашего города:")
        else:
            states[user_id]["city"] = city
            states[user_id]["step"] = "ad_photo"
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Город: {city}\n\n📸 Отправьте фото товара\n(или напишите - чтобы пропустить)"
            )
        return

    if data == "skip_extra_media":
        states[user_id]["step"] = "ad_preview"
        u = states.get(user_id, {})
        extra_count = len(u.get("extra_media", []))
        extra_text = f"\n📸 Доп. медиа: {extra_count} шт." if extra_count > 0 else ""
        preview_text = (
            f"📋 Предварительный просмотр:\n\n"
            f"🟢 {u.get('ad_type','').upper()}\n"
            f"{u.get('category','')}\n"
            f"📍 {u.get('city','')}\n\n"
            f"{u.get('description','')}\n\n"
            f"💰 Цена: {u.get('price','')} KZT{extra_text}"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=preview_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Опубликовать", callback_data="ad_confirm"),
                 InlineKeyboardButton("❌ Отменить", callback_data="ad_cancel")]
            ])
        )
        return

    if data == "ad_confirm":
        u = states.get(user_id, {})
        user = get_user(user_id)
        uname = user.get("username", username) if user else username
        topic_id = CATEGORY_TOPICS.get(u.get("category", ""))
        try:
            kwargs = {"message_thread_id": topic_id} if topic_id else {}
            price_text = u.get("price", "")
            ad_id = save_ad({
                "user_id": user_id, "username": uname,
                "type": u.get("ad_type"), "category": u.get("category"),
                "description": u.get("description"), "price": price_text,
                "photo_id": u.get("photo_id", ""), "city": u.get("city", ""),
                "message_id": "0", "media_message_id": "0"
            })
            if ad_id is None:
                raise RuntimeError("Не удалось сохранить объявление в базе данных")

            seller_user = get_user(user_id)
            seller_rating = seller_user.get("rating", 0) if seller_user else 0
            seller_deals = seller_user.get("reviews_count", 0) if seller_user else 0
            city_line = f"\n📍 {u.get('city','')}" if u.get('city') else ""
            ad_text = (
                f"№ {ad_id}\n\n"
                f"{u.get('description','').upper()}\n\n"
                f"💰 Цена: {price_text} KZT\n"
                f"⭐ Рейтинг продавца: {seller_rating} | Сделок: {seller_deals}"
                f"{city_line}"
            )
            media_msg_id = ""
            extra_media = u.get("extra_media", [])
            if extra_media and MEDIA_CHANNEL_ID:
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

            buttons = []
            if media_msg_id:
                buttons.append([InlineKeyboardButton("📸 Фото и видео",
                    url=f"https://t.me/{MEDIA_CHANNEL_USERNAME}/{media_msg_id}")])
            # ===== ИСПРАВЛЕНИЕ #22: "Связаться с продавцом" вместо "Купить" =====
            buttons.append([InlineKeyboardButton(
                "📩 Связаться с продавцом",
                url=f"https://t.me/{BOT_USERNAME}?start=buy_{user_id}_{ad_id}")])
            buttons.append([InlineKeyboardButton(
                "🤝 Предложить цену",
                url=f"https://t.me/{BOT_USERNAME}?start=offer_{user_id}_{ad_id}")])
            buy_markup = InlineKeyboardMarkup(buttons)

            if u.get("photo_id"):
                sent_msg = await context.bot.send_photo(
                    chat_id=GROUP_ID, photo=u["photo_id"],
                    caption=ad_text, reply_markup=buy_markup, **kwargs)
            else:
                sent_msg = await context.bot.send_message(
                    chat_id=GROUP_ID, text=ad_text, reply_markup=buy_markup, **kwargs)

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

            # ===== ИСПРАВЛЕНИЕ #7: Уведомление подписчиков =====
            try:
                ad_for_notify = dict(u)
                ad_for_notify["user_id"] = user_id
                await notify_subscribers(context, u.get("category", ""), ad_id, ad_for_notify)
            except Exception as e:
                logger.error(f"Notify subscribers error: {e}")

            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Объявление опубликовано! Номер: #{ad_id}\n📅 Действует 30 дней",
                reply_markup=main_menu_keyboard()
            )
        except Exception as e:
            logger.error(f"Publish error: {e}")
            # ===== ИСПРАВЛЕНИЕ #27: Разные сообщения для пользователя и администратора =====
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Не удалось опубликовать объявление. Попробуйте позже или свяжитесь с администратором.",
                reply_markup=main_menu_keyboard()
            )
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"⚠️ Ошибка публикации от @{username} (ID: {user_id}):\n{e}"
                )
            except Exception:
                pass
        return

    if data == "ad_cancel":
        states[user_id] = {"step": "menu"}
        await context.bot.send_message(chat_id=chat_id, text="❌ Отменено.", reply_markup=main_menu_keyboard())
        return

    if data == "buy_cancel":
        await context.bot.send_message(chat_id=chat_id, text="❌ Отменено.")
        return

    if data.startswith("offer_yes_"):
        rest = data.replace("offer_yes_", "")
        parts = rest.split("_")
        buyer_id = parts[0]
        ad_id_o = parts[1] if len(parts) > 1 else "0"
        offer_price = parts[2] if len(parts) > 2 else ""
        seller = get_user(user_id)
        buyer = get_user(buyer_id)
        seller_uname = seller.get("username", "") if seller else ""
        seller_phone = seller.get("phone", "") if seller else ""
        seller_fname = seller.get("first_name", "") if seller else ""
        seller_lname = seller.get("last_name", "") if seller else ""
        buyer_uname = buyer.get("username", "") if buyer else ""
        buyer_phone = buyer.get("phone", "") if buyer else ""
        buyer_fname = buyer.get("first_name", "") if buyer else ""
        buyer_lname = buyer.get("last_name", "") if buyer else ""
        save_deal(user_id, buyer_id, ad_id_o)
        await context.bot.send_message(
            chat_id=int(buyer_id),
            text=f"🎉 Продавец СОГЛАСЕН на вашу цену {offer_price}!\n\n"
                 f"👤 Продавец: {seller_fname} {seller_lname}\n"
                 f"🔗 @{seller_uname}\n📞 {seller_phone}\n\nСвяжитесь напрямую!"
        )
        await context.bot.send_message(
            chat_id=int(user_id),
            text=f"✅ Вы согласились на цену {offer_price}.\n\n"
                 f"👤 Покупатель: {buyer_fname} {buyer_lname}\n"
                 f"🔗 @{buyer_uname}\n📞 {buyer_phone}"
        )
        await query.edit_message_text(text=query.message.text + f"\n\n✅ СОГЛАСИЛИСЬ за {offer_price}")
        return

    if data.startswith("offer_no_"):
        rest = data.replace("offer_no_", "")
        buyer_id = rest.split("_")[0]
        await context.bot.send_message(
            chat_id=int(buyer_id),
            text="😔 Продавец отказал в вашей цене.\n\nВы можете предложить другую цену."
        )
        await query.edit_message_text(text=query.message.text + "\n\n❌ ОТКАЗАЛИ")
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

        ad_info = ""
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM ads WHERE ad_id = %s", (int(ad_id_confirm),))
            ad = cur.fetchone()
            cur.close()
            conn.close()
            if ad:
                ad_info = f"\n\n📦 {ad.get('category','')}\n📝 {ad.get('description','')[:50]}\n💰 {ad.get('price','')}"
        except Exception:
            pass

        save_deal(seller_id, user_id, ad_id_confirm)
        await context.bot.send_message(
            chat_id=int(user_id),
            text=f"✅ Заявка отправлена!{ad_info}\n\n"
                 f"👤 Продавец: {seller_fname} {seller_lname}\n"
                 f"🔗 @{seller_uname}\n📞 {seller_phone}\n\n"
                 f"Свяжитесь напрямую!\n\nЧерез 7 дней придёт запрос на оценку сделки."
        )
        await context.bot.send_message(
            chat_id=int(seller_id),
            text=f"🔔 Новый покупатель на ваш товар!{ad_info}\n\n"
                 f"👤 Покупатель: {buyer_fname} {buyer_lname}\n"
                 f"🔗 @{buyer_uname}\n📞 {buyer_phone}\n\n"
                 f"Свяжитесь напрямую!\n\nЧерез 7 дней придёт запрос на оценку сделки."
        )
        return

    if data == "menu_prices":
        await context.bot.send_message(chat_id=chat_id, text="⏳ Получаю актуальные цены...")
        prices, usd_kzt = await get_metal_prices()
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        if prices:
            text = f"📊 Цены на металлы\n🕐 {now}\n💵 USD/KZT: {usd_kzt}\n\n"
            for symbol, (name, price, unit) in prices.items():
                text += f"{name}: {price:,} KZT/{unit}\n"
            text += "\nЦены за 1 грамм"
        else:
            text = "⚠️ Не удалось получить цены. Попробуйте позже."
        await context.bot.send_message(
            chat_id=chat_id, text=text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]])
        )
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
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔔 Мои подписки\n\nВыберите категории для уведомлений:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
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
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Подписки сохранены!\n\n{', '.join(subs) if subs else 'Нет подписок'}",
            reply_markup=main_menu_keyboard()
        )
        return

    if data == "menu_rep":
        user = get_user(user_id)
        rating = float(user.get("rating", 0)) if user else 0
        reviews = user.get("reviews_count", 0) if user else 0
        stars = "⭐" * int(rating) if rating > 0 else "нет оценок"
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⭐ Моя репутация\n\n"
                 f"Рейтинг: {stars} ({rating}/5)\n"
                 f"Отзывов: {reviews}\n\n"
                 f"Репутация формируется после завершённых сделок.",
            reply_markup=main_menu_keyboard()
        )
        return

    if data == "menu_profile":
        user = get_user(user_id)
        if user:
            ads = get_user_ads(user_id)
            active = [a for a in ads if a.get("status") == "active"]
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"👤 Мой профиль\n\n"
                     f"Username: @{user.get('username','')}\n"
                     f"📅 Регистрация: {user.get('registered_at','')}\n"
                     f"📢 Активных объявлений: {len(active)}\n"
                     f"⭐ Рейтинг: {user.get('rating',0)}/5 (отзывов: {user.get('reviews_count',0)})",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Мои объявления", callback_data="my_ads")],
                    [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
                ])
            )
        return

    if data == "my_ads":
        ads = get_user_ads(user_id)
        active = [a for a in ads if a.get("status") == "active"]
        if not active:
            await context.bot.send_message(
                chat_id=chat_id,
                text="📢 Мои объявления\n\nУ вас нет активных объявлений.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu_profile")]])
            )
            return
        keyboard = []
        for a in active[:8]:
            label = f"🟢 {a.get('category','')} — {a.get('price','')} | {a.get('description','')[:20]}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"ad_view_{a.get('ad_id')}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="menu_profile")])
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📢 Мои объявления\n\nАктивных: {len(active)}\n\nНажмите на объявление для управления:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
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
                expires = ad.get('expires_at', '')
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🟢 {ad.get('category','')}\n\n{ad.get('description','')}\n\n"
                         f"💰 {ad.get('price','')}\n"
                         f"📅 Размещено: {ad.get('created_at','')}\n"
                         f"⏳ Действует до: {expires}",
                    reply_markup=InlineKeyboardMarkup([
                        # ===== ИСПРАВЛЕНИЕ #12: Кнопки управления объявлением =====
                        [InlineKeyboardButton("✅ Отметить как продано", callback_data=f"sold_ad_{ad_id}")],
                        [InlineKeyboardButton("🗑 Удалить объявление", callback_data=f"del_ad_{ad_id}")],
                        [InlineKeyboardButton("🔙 Назад", callback_data="my_ads")]
                    ])
                )
        except Exception as e:
            logger.error(f"ad_view error: {e}")
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Не удалось загрузить объявление. Попробуйте позже.")
        return

    # ===== ИСПРАВЛЕНИЕ #12: Статус "Продано" =====
    if data.startswith("sold_ad_"):
        ad_id = int(data.replace("sold_ad_", ""))
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM ads WHERE ad_id = %s AND user_id = %s", (ad_id, str(user_id)))
            ad = cur.fetchone()
            if ad:
                msg_id = ad.get("message_id", "")
                if msg_id:
                    try:
                        sold_text = (f"✅ ПРОДАНО\n\n№ {ad_id}\n\n"
                                     f"{ad.get('description','').upper()}\n\n"
                                     f"💰 Цена: {ad.get('price','')} KZT\n📍 {ad.get('city','')}")
                        if ad.get("photo_id"):
                            await context.bot.edit_message_caption(
                                chat_id=GROUP_ID, message_id=int(msg_id), caption=sold_text)
                        else:
                            await context.bot.edit_message_text(
                                chat_id=GROUP_ID, message_id=int(msg_id), text=sold_text)
                    except Exception as e:
                        logger.error(f"Edit sold message error: {e}")
                cur2 = conn.cursor()
                cur2.execute("UPDATE ads SET status = 'sold' WHERE ad_id = %s", (ad_id,))
                conn.commit()
                cur2.close()
                cur.close()
                conn.close()
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Объявление #{ad_id} отмечено как ПРОДАНО.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Мои объявления", callback_data="my_ads")]])
                )
            else:
                cur.close()
                conn.close()
        except Exception as e:
            logger.error(f"sold_ad error: {e}")
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Не удалось обновить статус. Попробуйте позже.")
        return

    if data.startswith("del_ad_"):
        ad_id = int(data.replace("del_ad_", ""))
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM ads WHERE ad_id = %s AND user_id = %s", (ad_id, str(user_id)))
            ad = cur.fetchone()
            if ad:
                msg_id = ad.get("message_id", "")
                if msg_id:
                    try:
                        await context.bot.delete_message(chat_id=GROUP_ID, message_id=int(msg_id))
                    except Exception as e:
                        logger.error(f"Delete from group error: {e}")
                cur2 = conn.cursor()
                cur2.execute("UPDATE ads SET status = 'deleted' WHERE ad_id = %s", (ad_id,))
                conn.commit()
                cur2.close()
                cur.close()
                conn.close()
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Объявление #{ad_id} удалено.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Мои объявления", callback_data="my_ads")]])
                )
            else:
                cur.close()
                conn.close()
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Объявление #{ad_id} не найдено.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Мои объявления", callback_data="my_ads")]])
                )
        except Exception as e:
            logger.error(f"del_ad error: {e}")
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Не удалось удалить. Попробуйте позже.")
        return

    if data == "menu_how":
        await context.bot.send_message(
            chat_id=chat_id,
            text="📋 Как работает KAZMETALS\n\n"
                 "🔷 Для продавца:\n"
                 "1. Нажмите Подать объявление\n"
                 "2. Выберите категорию и город\n"
                 "3. Опишите товар и укажите цену\n"
                 "4. Загрузите главное фото\n"
                 "5. По желанию добавьте доп. фото и видео\n"
                 "6. Объявление появится в группе на 30 дней\n\n"
                 "🔷 Для покупателя:\n"
                 "1. Зайдите в группу KAZ_METALS\n"
                 "2. Найдите нужное объявление\n"
                 "3. Доступны три действия:\n"
                 "   📸 Фото и видео — посмотреть доп. медиа\n"
                 "   📩 Связаться с продавцом\n"
                 "   🤝 Предложить свою цену\n"
                 "4. После подтверждения оба получат контакты\n\n"
                 "🔔 Подписки — уведомления о новых объявлениях\n\n"
                 "📊 Цены на металлы — актуальный курс золота, серебра, платины, палладия\n\n"
                 "⭐ После сделки через 7 дней придёт запрос на оценку\n\n"
                 "⚠️ KAZMETALS — информационный посредник.\n"
                 "Сделки совершаются напрямую между пользователями.\n"
                 "Пользователи самостоятельно проверяют подлинность товара.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"📞 @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
            ])
        )
        return

    if data == "menu_admin":
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📞 Связь с администратором\n\n"
                 f"👉 @{ADMIN_USERNAME}\n\n"
                 f"Пн-Пт 9:00 - 20:00 (Алматы)\n"
                 f"Сб-Вс 10:00 - 18:00 (Алматы)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✉️ Написать @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
            ])
        )
        return

    if data == "back_menu":
        states[user_id] = {"step": "menu"}
        await context.bot.send_message(
            chat_id=chat_id,
            text="🏅 Главное меню KAZMETALS",
            reply_markup=main_menu_keyboard()
        )
        return

    # ===== ИСПРАВЛЕНИЕ #15: Панель администратора =====
    if data == "adm_stats":
        if not is_admin(user_id):
            return
        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0]
            today = datetime.now().strftime("%d.%m.%Y")
            cur.execute("SELECT COUNT(*) FROM users WHERE registered_at LIKE %s", (f"{today}%",))
            new_today = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM ads WHERE status = 'active'")
            active_ads = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM deals")
            total_deals = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE status = 'blocked'")
            blocked = cur.fetchone()[0]
            cur.close()
            conn.close()
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"📊 Статистика KAZMETALS\n\n"
                     f"👥 Всего пользователей: {total_users}\n"
                     f"🆕 Новых сегодня: {new_today}\n"
                     f"📢 Активных объявлений: {active_ads}\n"
                     f"🤝 Всего сделок/заявок: {total_deals}\n"
                     f"🚫 Заблокировано: {blocked}",
                reply_markup=admin_menu_keyboard()
            )
        except Exception as e:
            logger.error(f"adm_stats error: {e}")
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Не удалось загрузить статистику.")
        return

    if data == "adm_complaints":
        if not is_admin(user_id):
            return
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM complaints ORDER BY complaint_id DESC LIMIT 10")
            complaints = [dict(c) for c in cur.fetchall()]
            cur.close()
            conn.close()
            if not complaints:
                await context.bot.send_message(
                    chat_id=chat_id, text="⚠️ Жалоб нет.", reply_markup=admin_menu_keyboard())
                return
            text = "⚠️ Последние жалобы:\n\n"
            for c in complaints:
                text += (f"#{c.get('complaint_id')} | Объявление: #{c.get('ad_id')}\n"
                         f"Причина: {c.get('reason','')}\n"
                         f"От: {c.get('from_user_id','')}\n\n")
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=admin_menu_keyboard())
        except Exception as e:
            logger.error(f"adm_complaints error: {e}")
        return

    if data == "adm_blocked":
        if not is_admin(user_id):
            return
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM users WHERE status = 'blocked' LIMIT 10")
            blocked_users = [dict(u) for u in cur.fetchall()]
            cur.close()
            conn.close()
            if not blocked_users:
                await context.bot.send_message(
                    chat_id=chat_id, text="🚫 Заблокированных нет.", reply_markup=admin_menu_keyboard())
                return
            text = "🚫 Заблокированные пользователи:\n\n"
            for u in blocked_users:
                text += f"@{u.get('username','')} (ID: {u.get('user_id','')})\n"
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=admin_menu_keyboard())
        except Exception as e:
            logger.error(f"adm_blocked error: {e}")
        return

    if data == "adm_users":
        if not is_admin(user_id):
            return
        states[user_id] = {"step": "adm_find_user"}
        await context.bot.send_message(
            chat_id=chat_id,
            text="👥 Управление пользователями\n\nВведите Telegram ID или @username для поиска:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="adm_menu")]])
        )
        return

    if data == "adm_menu":
        if not is_admin(user_id):
            return
        await context.bot.send_message(
            chat_id=chat_id, text="🔧 Панель администратора:", reply_markup=admin_menu_keyboard())
        return

    if data.startswith("adm_block_"):
        if not is_admin(user_id):
            return
        target_id = data.replace("adm_block_", "")
        target = get_user(target_id)
        if target:
            target["status"] = "blocked"
            save_user(target_id, target)
            await context.bot.send_message(chat_id=chat_id, text=f"🚫 Пользователь {target_id} заблокирован.")
            try:
                await context.bot.send_message(
                    chat_id=int(target_id),
                    text=f"🚫 Ваш доступ к KAZMETALS ограничен.\n\nСвяжитесь с администратором: @{ADMIN_USERNAME}"
                )
            except Exception:
                pass
        return

    if data.startswith("adm_unblock_"):
        if not is_admin(user_id):
            return
        target_id = data.replace("adm_unblock_", "")
        target = get_user(target_id)
        if target:
            target["status"] = "active"
            save_user(target_id, target)
            await context.bot.send_message(chat_id=chat_id, text=f"🔓 Пользователь {target_id} разблокирован.")
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
            reply_markup=ReplyKeyboardRemove()
        )
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
                [InlineKeyboardButton("📨 Отправить заявку", callback_data="send_application")]
            ])
        )
        return

    if step == "offer_price":
        seller_id = states[user_id].get("seller_id")
        ad_id_offer = states[user_id].get("ad_id", "0")
        offer = text
        # ===== ИСПРАВЛЕНИЕ #23: Проверка — цена должна быть числом =====
        if not offer.isdigit() or int(offer) <= 0:
            await update.message.reply_text(
                "❌ Пожалуйста, введите сумму цифрами, например: 100000"
            )
            return
        buyer = get_user(user_id)
        buyer_uname = buyer.get("username", "") if buyer else ""
        buyer_fname = buyer.get("first_name", "") if buyer else ""
        buyer_lname = buyer.get("last_name", "") if buyer else ""
        ad_info = ""
        ad_price = ""
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM ads WHERE ad_id = %s", (int(ad_id_offer),))
            ad = cur.fetchone()
            cur.close()
            conn.close()
            if ad:
                ad_info = f"\n\n📦 №{ad_id_offer}: {ad.get('description','')[:50]}"
                ad_price = ad.get('price', '')
        except Exception:
            pass
        offer_formatted = f"{int(offer):,}".replace(",", " ")
        await context.bot.send_message(
            chat_id=int(seller_id),
            text=f"💼 Новое предложение цены!{ad_info}\n"
                 f"💰 Ваша цена: {ad_price}\n"
                 f"💸 Предложение: {offer_formatted} KZT\n\n"
                 f"👤 От: {buyer_fname} {buyer_lname}\n"
                 f"🔗 @{buyer_uname}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Согласиться", callback_data=f"offer_yes_{user_id}_{ad_id_offer}_{offer}"),
                 InlineKeyboardButton("❌ Отказать", callback_data=f"offer_no_{user_id}_{ad_id_offer}")]
            ])
        )
        await update.message.reply_text(
            f"✅ Ваше предложение {offer_formatted} KZT отправлено продавцу!\n\nЖдите ответа.",
            reply_markup=main_menu_keyboard()
        )
        states[user_id] = {"step": "menu"}
        return

    if step == "ad_description":
        states[user_id]["description"] = text
        states[user_id]["step"] = "ad_price"
        await update.message.reply_text("💰 Введите цену (например: 150000 KZT или 'договорная'):")
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

    # ===== ИСПРАВЛЕНИЕ #9: После ввода другого города — сразу к фото, не к описанию =====
    if step == "ad_city_text":
        states[user_id]["city"] = text
        states[user_id]["step"] = "ad_photo"
        await update.message.reply_text(
            f"✅ Город: {text}\n\n📸 Отправьте фото товара\nили напишите -, чтобы пропустить."
        )
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
            ])
        )
        return

    if step == "ad_extra_media" and text == "-":
        await show_ad_preview(update, context, user_id)
        return

    # ===== ИСПРАВЛЕНИЕ #15: Поиск пользователя администратором =====
    if step == "adm_find_user" and is_admin(user_id):
        target_id = text.lstrip("@")
        target = None
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if text.startswith("@"):
                cur.execute("SELECT * FROM users WHERE username = %s", (target_id,))
            else:
                cur.execute("SELECT * FROM users WHERE user_id = %s", (target_id,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                target = dict(row)
        except Exception as e:
            logger.error(f"adm_find_user error: {e}")

        if not target:
            await update.message.reply_text("❌ Пользователь не найден.", reply_markup=admin_menu_keyboard())
            states[user_id] = {"step": "menu"}
            return

        tid = target.get("user_id", "")
        status_text = "🚫 Заблокирован" if target.get("status") == "blocked" else "✅ Активен"
        block_btn = "🔓 Разблокировать" if target.get("status") == "blocked" else "🚫 Заблокировать"
        block_cb = f"adm_unblock_{tid}" if target.get("status") == "blocked" else f"adm_block_{tid}"
        await update.message.reply_text(
            f"👤 Пользователь\n\n"
            f"Username: @{target.get('username','')}\n"
            f"ID: {tid}\n"
            f"Статус: {status_text}\n"
            f"Регистрация: {target.get('registered_at','')}\n"
            f"Рейтинг: {target.get('rating',0)}/5 (отзывов: {target.get('reviews_count',0)})",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(block_btn, callback_data=block_cb)],
                [InlineKeyboardButton("🔙 Назад", callback_data="adm_menu")]
            ])
        )
        states[user_id] = {"step": "menu"}
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
            ])
        )

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
            ])
        )
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
            ])
        )

# ===== ИСПРАВЛЕНИЕ #7: Уведомление подписчиков при публикации объявления =====
async def notify_subscribers(context, category: str, ad_id: int, ad_data: dict):
    if not category:
        return
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT user_id FROM users WHERE subscriptions LIKE %s AND status = 'active'",
            (f"%{category}%",)
        )
        subscribers = [row["user_id"] for row in cur.fetchall()]
        cur.close()
        conn.close()
        msg = (
            f"🔔 Новое объявление в категории {category}\n\n"
            f"{ad_data.get('description','')[:80]}\n"
            f"💰 Цена: {ad_data.get('price','')} KZT\n"
            f"📍 {ad_data.get('city','')}"
        )
        seller_id = str(ad_data.get("user_id", ""))
        for sub_id in subscribers:
            if str(sub_id) == seller_id:
                continue
            try:
                await context.bot.send_message(chat_id=int(sub_id), text=msg)
            except Exception as e:
                logger.error(f"Notify subscriber {sub_id} error: {e}")
    except Exception as e:
        logger.error(f"notify_subscribers error: {e}")

# ===== ИСПРАВЛЕНИЕ #26: Редактируем одно закреплённое сообщение с ценами =====
PRICE_MESSAGE_ID = None

async def publish_prices(bot):
    global PRICE_MESSAGE_ID
    prices, usd_kzt = await get_metal_prices()
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    if not prices:
        return
    text = f"📊 Актуальные цены на металлы\n🕐 {now}\n💵 USD/KZT: {usd_kzt}\n\n"
    for symbol, (name, price, unit) in prices.items():
        text += f"{name}: {price:,} KZT/{unit}\n"
    text += "\nЦены за 1 грамм | Обновляется ежедневно"
    try:
        topic_id = CATEGORY_TOPICS.get("📈 Инвестиции и цены")
        kwargs = {"message_thread_id": topic_id} if topic_id else {}
        if PRICE_MESSAGE_ID:
            try:
                await bot.edit_message_text(
                    chat_id=GROUP_ID, message_id=PRICE_MESSAGE_ID, text=text)
                logger.info("Prices updated (edited existing message)!")
                return
            except Exception:
                pass
        msg = await bot.send_message(chat_id=GROUP_ID, text=text, **kwargs)
        PRICE_MESSAGE_ID = msg.message_id
        await bot.pin_chat_message(chat_id=GROUP_ID, message_id=msg.message_id, disable_notification=True)
        logger.info("Prices published and pinned!")
    except Exception as e:
        logger.error(f"Publish prices error: {e}")

async def daily_prices(context: ContextTypes.DEFAULT_TYPE):
    await publish_prices(context.bot)

async def getid(update, context):
    chat = update.message.chat
    await update.message.reply_text(f"Chat ID: {chat.id}\nType: {chat.type}\nTitle: {chat.title}")

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("getid", getid))
    app.add_handler(CommandHandler("start", start))
    # ===== ИСПРАВЛЕНИЕ #29: Новые команды =====
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("rules", rules_command))
    # ===== ИСПРАВЛЕНИЕ #15: Команда /admin =====
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VIDEO, video_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Автопубликация цен включается только если есть GROUP_ID и установлен job-queue.
    # Если requirements.txt не подтянул extra [job-queue], бот не должен падать при запуске.
    if AUTO_PUBLISH_PRICES and GROUP_ID and app.job_queue is not None:
        app.job_queue.run_once(daily_prices, when=10)
        from datetime import time as dt_time
        app.job_queue.run_daily(daily_prices, time=dt_time(hour=4, minute=0))
    else:
        logger.warning(
            "Автопубликация цен отключена: проверьте AUTO_PUBLISH_PRICES, GROUP_ID и python-telegram-bot[job-queue]."
        )

    logger.info("KAZMETALS Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
