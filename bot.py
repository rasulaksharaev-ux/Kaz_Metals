import os
import logging
import xml.etree.ElementTree as ET
import email.utils
from datetime import datetime, timedelta, time as dt_time, timezone
from typing import Optional, Dict, Any, List, Tuple

import aiohttp
import psycopg2
import psycopg2.extras
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== Railway Variables =====
# ВАЖНО: реальные токены/пароли не должны храниться в коде.
# Их нужно указывать только в Railway -> Variables.

def clean_env_value(value: Optional[str]) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1].strip()
    return value


def must_env(name: str) -> str:
    value = clean_env_value(os.environ.get(name))
    if not value:
        raise RuntimeError(f"{name} не задан в Railway Variables!")
    return value


def must_int_env(name: str) -> int:
    value = must_env(name)
    try:
        return int(value)
    except ValueError as e:
        raise RuntimeError(f"{name} должен быть числом, получено: {value!r}") from e


def optional_int_env(name: str, default: int = 0) -> int:
    value = clean_env_value(os.environ.get(name))
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


TOKEN = must_env("BOT_TOKEN")
# В этой версии GOLDAPI_KEY больше не нужен для цен, но переменную можно оставить в Railway без вреда.
GOLDAPI_KEY = clean_env_value(os.environ.get("GOLDAPI_KEY"))
ADMIN_CHAT_ID = must_int_env("ADMIN_CHAT_ID")
ADMIN_USERNAME = must_env("ADMIN_USERNAME").lstrip("@")
GROUP_LINK = must_env("GROUP_LINK")
GROUP_ID = must_int_env("GROUP_ID")
MEDIA_CHANNEL_ID = must_int_env("MEDIA_CHANNEL_ID")
DATABASE_URL = must_env("DATABASE_URL")
BOT_USERNAME = clean_env_value(os.environ.get("BOT_USERNAME")) or "KAZ_METALSbot"
MEDIA_CHANNEL_USERNAME = clean_env_value(os.environ.get("MEDIA_CHANNEL_USERNAME")) or "kaz_metalsphoto"
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "ad9967247f8a43a4b11eec20760ae18f")
NEWS_TOPIC_ID = optional_int_env("NEWS_TOPIC_ID", 12)
DISCUSSIONS_TOPIC_ID = optional_int_env("DISCUSSIONS_TOPIC_ID", 0)
WANT_BUY_TOPIC_ID   = 1173  # Ищу / Хочу купить
ALERT_TOPIC_ID = optional_int_env("ALERT_TOPIC_ID", NEWS_TOPIC_ID)
ALERT_THRESHOLD = float(os.environ.get("ALERT_THRESHOLD", "1.5"))
AUTO_PUBLISH_PRICES = (clean_env_value(os.environ.get("AUTO_PUBLISH_PRICES")) or "true").lower() in ("1", "true", "yes", "y")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")  # бесплатно: console.groq.com

CATEGORIES = [
    "❤️ Кольца",
    "⚡ Серьги",
    "🏅 Подвеска/Браслет",
    "🔥 Exclusive",
    "🪙 Монеты",
    "💎 Камни",
    "⌚ Часы",
    "♻️ Лом и скупка",
]

CATEGORY_TOPICS = {
    "❤️ Кольца": 141,
    "⚡ Серьги": 331,
    "🏅 Подвеска/Браслет": 129,
    "🔥 Exclusive": 126,
    "🪙 Монеты": 2,
    "💎 Камни": 10,
    "⌚ Часы": 77,
    "♻️ Лом и скупка": 11,
}

states: Dict[str, Dict[str, Any]] = {}
PRICE_MESSAGE_ID: Optional[int] = None


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            first_name TEXT DEFAULT '',
            last_name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            registered_at TEXT DEFAULT '',
            subscriptions TEXT DEFAULT '',
            rating REAL DEFAULT 0,
            reviews_count INTEGER DEFAULT 0,
            joined TEXT DEFAULT 'no',
            role TEXT DEFAULT 'user'
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ads (
            ad_id SERIAL PRIMARY KEY,
            user_id TEXT,
            username TEXT DEFAULT '',
            type TEXT DEFAULT '',
            category TEXT DEFAULT '',
            description TEXT DEFAULT '',
            price TEXT DEFAULT '',
            photo_id TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT '',
            message_id TEXT DEFAULT '',
            media_message_id TEXT DEFAULT '',
            city TEXT DEFAULT '',
            expires_at TEXT DEFAULT '',
            view_count INTEGER DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS deals (
            deal_id SERIAL PRIMARY KEY,
            seller_id TEXT,
            buyer_id TEXT,
            ad_id TEXT,
            created_at TEXT DEFAULT '',
            status TEXT DEFAULT 'contact_opened',
            offer_amount TEXT DEFAULT '',
            notified BOOLEAN DEFAULT FALSE,
            review_due_at TEXT DEFAULT '',
            seller_reviewed BOOLEAN DEFAULT FALSE,
            buyer_reviewed BOOLEAN DEFAULT FALSE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reviews (
            review_id SERIAL PRIMARY KEY,
            deal_id TEXT,
            from_user_id TEXT,
            to_user_id TEXT,
            rating INTEGER,
            comment TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS complaints (
            complaint_id SERIAL PRIMARY KEY,
            ad_id TEXT,
            from_user_id TEXT,
            seller_id TEXT,
            reason TEXT,
            created_at TEXT DEFAULT ''
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS news_published (
            id SERIAL PRIMARY KEY,
            title_hash TEXT UNIQUE,
            title TEXT,
            domain TEXT,
            published_at TIMESTAMP DEFAULT NOW()
        )
        """
    )

    # На случай если таблицы уже были созданы старой версией.
    alter_sql = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS joined TEXT DEFAULT 'no'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user'",
        "ALTER TABLE ads ADD COLUMN IF NOT EXISTS message_id TEXT DEFAULT ''",
        "ALTER TABLE ads ADD COLUMN IF NOT EXISTS media_message_id TEXT DEFAULT ''",
        "ALTER TABLE ads ADD COLUMN IF NOT EXISTS city TEXT DEFAULT ''",
        "ALTER TABLE ads ADD COLUMN IF NOT EXISTS expires_at TEXT DEFAULT ''",
        "ALTER TABLE ads ADD COLUMN IF NOT EXISTS view_count INTEGER DEFAULT 0",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'contact_opened'",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS offer_amount TEXT DEFAULT ''",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS notified BOOLEAN DEFAULT FALSE",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS review_due_at TEXT DEFAULT ''",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS seller_reviewed BOOLEAN DEFAULT FALSE",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS buyer_reviewed BOOLEAN DEFAULT FALSE",
    ]
    for sql in alter_sql:
        cur.execute(sql)

    conn.commit()
    cur.close()
    conn.close()
    logger.info("DB initialized")


def db_fetchone(query: str, params: tuple = ()) -> Optional[dict]:
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params)
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"db_fetchone error: {e}")
        return None


def db_fetchall(query: str, params: tuple = ()) -> List[dict]:
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"db_fetchall error: {e}")
        return []


def get_user(user_id: str) -> Optional[dict]:
    return db_fetchone("SELECT * FROM users WHERE user_id = %s", (str(user_id),))


def save_user(user_id: str, data: dict) -> bool:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
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
            """,
            (
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
                data.get("role", "user"),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"save_user error: {e}")
        return False


def update_user_field(user_id: str, field: str, value: str) -> None:
    allowed = {"status", "joined", "subscriptions", "role", "phone", "username"}
    if field not in allowed:
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE users SET {field} = %s WHERE user_id = %s", (value, str(user_id)))
    conn.commit()
    cur.close()
    conn.close()


def save_ad(ad_data: dict) -> Optional[int]:
    try:
        conn = get_conn()
        cur = conn.cursor()
        expires = (datetime.now() + timedelta(days=30)).strftime("%d.%m.%Y")
        cur.execute(
            """
            INSERT INTO ads (user_id, username, type, category, description, price, photo_id,
                             status, created_at, message_id, media_message_id, city, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING ad_id
            """,
            (
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
                expires,
            ),
        )
        ad_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return ad_id
    except Exception as e:
        logger.error(f"save_ad error: {e}")
        return None


def update_ad_message(ad_id: int, message_id: str, media_message_id: str = "") -> None:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE ads SET message_id = %s, media_message_id = %s WHERE ad_id = %s",
            (str(message_id), str(media_message_id), int(ad_id)),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"update_ad_message error: {e}")


def get_user_ads(user_id: str) -> List[dict]:
    return db_fetchall("SELECT * FROM ads WHERE user_id = %s ORDER BY ad_id DESC", (str(user_id),))


def save_deal(
    seller_id: str,
    buyer_id: str,
    ad_id: str,
    status: str = "contact_opened",
    offer_amount: str = "",
) -> Optional[int]:
    try:
        conn = get_conn()
        cur = conn.cursor()
        review_due_at = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S") if status in ("contact_opened", "offer_accepted") else ""
        cur.execute(
            """
            INSERT INTO deals (seller_id, buyer_id, ad_id, created_at, status, offer_amount, review_due_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING deal_id
            """,
            (
                str(seller_id),
                str(buyer_id),
                str(ad_id),
                datetime.now().strftime("%d.%m.%Y %H:%M"),
                status,
                str(offer_amount),
                review_due_at,
            ),
        )
        deal_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return deal_id
    except Exception as e:
        logger.error(f"save_deal error: {e}")
        return None


def update_deal_status(deal_id: str, status: str) -> bool:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE deals SET status = %s WHERE deal_id = %s", (status, int(deal_id)))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"update_deal_status error: {e}")
        return False


def set_deal_review_due(deal_id: str, days: int = 1) -> None:
    try:
        due = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE deals SET review_due_at = %s WHERE deal_id = %s", (due, str(deal_id)))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"set_deal_review_due error: {e}")


def increment_ad_view(ad_id: str) -> None:
    try:
        if not str(ad_id).isdigit():
            return
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE ads SET view_count = COALESCE(view_count, 0) + 1 WHERE ad_id = %s", (int(ad_id),))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"increment_ad_view error: {e}")


def mark_deal_reviewed(deal_id: str, user_id: str) -> None:
    deal = db_fetchone("SELECT * FROM deals WHERE deal_id = %s", (int(deal_id),)) if str(deal_id).isdigit() else None
    if not deal:
        return
    field = "seller_reviewed" if str(deal.get("seller_id")) == str(user_id) else "buyer_reviewed"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE deals SET {field} = TRUE WHERE deal_id = %s", (int(deal_id),))
    conn.commit()
    cur.close()
    conn.close()


def update_user_rating_from_review(to_user_id: str, rating: int) -> None:
    user = get_user(to_user_id) or {}
    old_rating = float(user.get("rating") or 0)
    old_count = int(user.get("reviews_count") or 0)
    new_count = old_count + 1
    new_rating = round(((old_rating * old_count) + int(rating)) / new_count, 2)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET rating = %s, reviews_count = %s WHERE user_id = %s", (new_rating, new_count, str(to_user_id)))
    conn.commit()
    cur.close()
    conn.close()


def admin_ads_views_text() -> str:
    ads = db_fetchall("""
        SELECT ad_id, category, price, city, status, COALESCE(view_count, 0) AS view_count
        FROM ads
        ORDER BY ad_id DESC
        LIMIT 30
    """)
    if not ads:
        return "👁 Пока нет объявлений."
    lines = ["👁 Просмотры объявлений", "", "Важно: это не реальные просмотры Telegram, а переходы по кнопкам объявления.", ""]
    for ad in ads:
        lines.append(f"№{ad.get('ad_id')} | {ad.get('category','')} | 👁 {ad.get('view_count', 0)} | {ad.get('status','')}")
    return "\n".join(lines)


def is_admin(user_id: str) -> bool:
    return str(user_id) == str(ADMIN_CHAT_ID)


def is_user_blocked(user_id: str) -> bool:
    user = get_user(user_id)
    return bool(user and user.get("status") == "blocked")


async def check_user_in_group(bot, user_id: int, group_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=group_id, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"check_user_in_group error: {e}")
        return False


async def fetch_usd_kzt(session: aiohttp.ClientSession) -> Optional[float]:
    """Берём официальный курс USD/KZT из RSS Национального Банка РК."""
    try:
        async with session.get(
            "https://nationalbank.kz/rss/rates_all.xml",
            timeout=aiohttp.ClientTimeout(total=20),
            headers={"User-Agent": "KAZ_METALSBot/1.0"},
        ) as r:
            xml_text = await r.text()
        root = ET.fromstring(xml_text)
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip().upper()
            if title == "USD":
                value = (item.findtext("description") or "").replace(",", ".").strip()
                quant = (item.findtext("quant") or "1").replace(",", ".").strip()
                return float(value) / max(float(quant), 1.0)
    except Exception as e:
        logger.error(f"NBRK FX fetch error: {e}")
    return None


def extract_lbma_usd_price(data: Any) -> Optional[float]:
    """LBMA JSON обычно приходит списком: latest item -> v[0] = USD/oz."""
    try:
        if isinstance(data, list):
            for item in reversed(data):
                values = item.get("v") if isinstance(item, dict) else None
                if isinstance(values, list) and values and values[0] not in (None, ""):
                    return float(values[0])
        if isinstance(data, dict):
            for key in ("gold_usd", "silver_usd", "platinum_usd", "palladium_usd", "usd", "price"):
                if data.get(key) not in (None, "", 0):
                    return float(data[key])
    except Exception as e:
        logger.error(f"LBMA price parse error: {e}")
    return None


async def fetch_lbma_price(session: aiohttp.ClientSession, url: str) -> Optional[float]:
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=20),
            headers={"User-Agent": "KAZ_METALSBot/1.0"},
        ) as r:
            if r.status != 200:
                logger.error(f"LBMA status {r.status} for {url}")
                return None
            data = await r.json(content_type=None)
        return extract_lbma_usd_price(data)
    except Exception as e:
        logger.error(f"LBMA fetch error for {url}: {e}")
        return None


async def get_metal_prices() -> Tuple[Dict[str, Tuple[str, int, str]], int]:
    """Цены LBMA в USD/oz переводятся в KZT/г по курсу НБРК."""
    lbma_feeds = {
        "XAU": "https://prices.lbma.org.uk/json/gold_pm.json",
        "XAG": "https://prices.lbma.org.uk/json/silver.json",
        "XPT": "https://prices.lbma.org.uk/json/platinum_pm.json",
        "XPD": "https://prices.lbma.org.uk/json/palladium_pm.json",
    }
    metals = {
        "XAU": ("🥇 Золото", "г"),
        "XAG": ("🥈 Серебро", "г"),
        "XPT": ("💎 Платина", "г"),
        "XPD": ("🔩 Палладий", "г"),
    }

    async with aiohttp.ClientSession() as session:
        usd_kzt = await fetch_usd_kzt(session)
        prices_oz: Dict[str, float] = {}
        for symbol, url in lbma_feeds.items():
            price = await fetch_lbma_price(session, url)
            if price and price > 0:
                prices_oz[symbol] = price

    results: Dict[str, Tuple[str, int, str]] = {}
    if not usd_kzt or not prices_oz:
        return results, round(usd_kzt) if usd_kzt else 0

    for symbol, (name, unit) in metals.items():
        price_usd_oz = prices_oz.get(symbol)
        if price_usd_oz and price_usd_oz > 0:
            price_kzt_g = (price_usd_oz / 31.1035) * usd_kzt
            results[symbol] = (name, round(price_kzt_g), unit)

    return results, round(usd_kzt)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Подать объявление", callback_data="menu_ad")],
        [InlineKeyboardButton("🔍 Хочу купить", callback_data="menu_want_buy")],
        [InlineKeyboardButton("📊 Цены на металлы", callback_data="menu_prices"),
         InlineKeyboardButton("🧮 Калькулятор", callback_data="menu_calc")],
        [InlineKeyboardButton("⭐ Моя репутация", callback_data="menu_rep")],
        [InlineKeyboardButton("👤 Мой профиль", callback_data="menu_profile")],
        [InlineKeyboardButton("📋 Как это работает", callback_data="menu_how")],
        [InlineKeyboardButton("📞 Администратор", callback_data="menu_admin")],
    ])


def categories_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(cat, callback_data=f"{prefix}_{i}")] for i, cat in enumerate(CATEGORIES)]
    rows.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")])
    return InlineKeyboardMarkup(rows)


def city_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏙 Алматы", callback_data="city_Алматы"), InlineKeyboardButton("🏛 Астана", callback_data="city_Астана")],
        [InlineKeyboardButton("🌆 Шымкент", callback_data="city_Шымкент"), InlineKeyboardButton("🏘 Другой город", callback_data="city_other")],
    ])


def ad_type_keyboard() -> InlineKeyboardMarkup:
    # Аукцион отключён: бот работает только с обычными объявлениями о продаже.
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Продажа", callback_data="adtype_sale")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)

    if is_user_blocked(user_id):
        await update.message.reply_text(f"🚫 Ваш доступ ограничен. Напишите администратору @{ADMIN_USERNAME}")
        return

    if context.args and context.args[0].startswith("buy_"):
        parts = context.args[0].split("_")
        seller_id = parts[1] if len(parts) > 1 else ""
        ad_id = parts[2] if len(parts) > 2 else "0"
        if seller_id == user_id:
            await update.message.reply_text("❌ Это ваше объявление.")
            return
        ad = db_fetchone("SELECT * FROM ads WHERE ad_id = %s", (int(ad_id),)) if ad_id.isdigit() else None
        ad_info = f"\n\n📦 {ad.get('description','')[:80]}\n💰 {ad.get('price','')}" if ad else ""
        await update.message.reply_text(
            f"Вы подтверждаете покупку?{ad_info}\n\nПосле подтверждения оба участника получат контакты друг друга.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Подтвердить", callback_data=f"buy_confirm_{seller_id}_{ad_id}")],
                [InlineKeyboardButton("❌ Отмена", callback_data="buy_cancel")],
            ]),
        )
        return

    if context.args and context.args[0].startswith("offer_"):
        parts = context.args[0].split("_")
        seller_id = parts[1] if len(parts) > 1 else ""
        ad_id = parts[2] if len(parts) > 2 else "0"
        if seller_id == user_id:
            await update.message.reply_text("❌ Это ваше объявление.")
            return
        states[user_id] = {"step": "offer_price", "seller_id": seller_id, "ad_id": ad_id}
        await update.message.reply_text("🤝 Введите вашу цену в KZT только цифрами, например: 600000")
        return

    user = get_user(user_id)
    if user and user.get("status") in ("active",) and user.get("joined") == "yes":
        # Проверяем реальное членство в группе
        try:
            member = await context.bot.get_chat_member(chat_id=GROUP_ID, user_id=int(user_id))
            in_group = member.status in ["member", "administrator", "creator", "restricted"]
        except Exception:
            in_group = True  # если не можем проверить - пускаем

        if not in_group:
            # Обновляем статус в базе
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("UPDATE users SET joined = 'no' WHERE user_id = %s", (str(user_id),))
            conn.commit()
            cur.close()
            conn.close()
            states[user_id] = {"step": "awaiting_join"}
            await update.message.reply_text(
                "⛔ Вы были исключены из группы KAZ_METALS.\n\n"
                "Для получения доступа вступите в группу снова.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔓 Вступить в KAZ_METALS", url=GROUP_LINK)],
                    [InlineKeyboardButton("✅ Я вступил - проверить", callback_data="check_join")],
                ]),
            )
            return

        states[user_id] = {"step": "menu"}
        await update.message.reply_text("🏅 Главное меню KAZ_METALS", reply_markup=main_menu_keyboard())
        return

    if user and user.get("status") == "reset":
        states[user_id] = {"step": "start"}
        await update.message.reply_text(
            "🏅 Добро пожаловать в KAZ_METALS!\n\n"
            "Закрытая площадка для покупки и продажи драгоценных металлов в Казахстане.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Зарегистрироваться", callback_data="reg_start")],
                [InlineKeyboardButton("❓ О платформе", callback_data="about")],
            ]),
        )
        return

    if user and user.get("status") == "active" and user.get("joined") != "yes":
        states[user_id] = {"step": "awaiting_join"}
        await update.message.reply_text(
            "✅ Вы зарегистрированы. Теперь вступите в группу и нажмите проверку.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔓 Вступить в KAZ_METALS", url=GROUP_LINK)],
                [InlineKeyboardButton("✅ Я вступил - проверить", callback_data="check_join")],
            ]),
        )
        return

    states[user_id] = {"step": "start"}
    await update.message.reply_text(
        "🏅 Добро пожаловать в KAZ_METALS!\n\n"
        "Закрытая площадка для покупки и продажи драгоценных металлов в Казахстане.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Зарегистрироваться", callback_data="reg_start")],
            [InlineKeyboardButton("❓ О платформе", callback_data="about")],
        ]),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Команды: /start - меню, /rules - правила, /getid - узнать ID чата.")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Команда недоступна.")
        return
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
        conn.commit()
        cur.close()
        conn.close()
        states.pop(user_id, None)
        await update.message.reply_text("✅ Аккаунт сброшен. Напишите /start чтобы зарегистрироваться заново.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {e}")


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Правила KAZ_METALS\n\n"
        "1. Запрещены поддельные товары.\n"
        "2. Продавец отвечает за описание и подлинность.\n"
        "3. Сделки совершаются напрямую между участниками.\n"
        "4. KAZ_METALS является информационным посредником.\n"
        "5. За нарушение правил доступ может быть ограничен."
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(str(update.message.from_user.id)):
        await update.message.reply_text("Команда доступна только владельцу.")
        return
    users_count = db_fetchone("SELECT COUNT(*) AS c FROM users") or {"c": 0}
    ads_count = db_fetchone("SELECT COUNT(*) AS c FROM ads") or {"c": 0}
    complaints_count = db_fetchone("SELECT COUNT(*) AS c FROM complaints") or {"c": 0}
    blocked_count = db_fetchone("SELECT COUNT(*) AS c FROM users WHERE status = 'blocked'") or {"c": 0}
    pending_count = db_fetchone("SELECT COUNT(*) AS c FROM ads WHERE status = 'pending'") or {"c": 0}
    disputed_count = db_fetchone("SELECT COUNT(*) AS c FROM deals WHERE status IN ('dispute', 'review_not_completed')") or {"c": 0}
    await update.message.reply_text(
        "🛠 Админ-панель KAZ_METALS\n\n"
        f"👥 Пользователи: {users_count['c']}\n"
        f"📦 Объявления: {ads_count['c']}\n"
        f"📢 На модерации: {pending_count['c']}\n"
        f"⚠️ Жалобы: {complaints_count['c']}\n"
        f"🚫 Заблокированные: {blocked_count['c']}\n"
        f"🤝 Спорные сделки: {disputed_count['c']}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
            [InlineKeyboardButton("📢 Объявления на модерации", callback_data="admin_pending_ads")],
            [InlineKeyboardButton("⚠️ Жалобы", callback_data="admin_complaints")],
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("🚫 Заблокированные", callback_data="admin_blocked")],
            [InlineKeyboardButton("✏️ Отправить на доработку", callback_data="admin_revision")],
            [InlineKeyboardButton("🗑 Удалить объявление", callback_data="admin_delete_ad")],
            [InlineKeyboardButton("💥 УДАЛИТЬ ВСЕ объявления", callback_data="admin_delete_all_ads")],
            [InlineKeyboardButton("🔄 Сбросить пользователя", callback_data="admin_reset_user")],
            [InlineKeyboardButton("🤝 Спорные сделки", callback_data="admin_disputes")],
            [InlineKeyboardButton("👁 Просмотры объявлений", callback_data="admin_ad_views")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")],
        ]),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id
    data = query.data

    if data == "about":
        await context.bot.send_message(
            chat_id=chat_id,
            text="ℹ️ KAZ_METALS - доверенное сообщество участников рынка драгоценных металлов Казахстана.\n\n"
                 "Здесь вы можете:\n"
                 "• Продавать и покупать металлы напрямую\n"
                 "• Смотреть актуальные цены на металлы\n"
                 "• Предлагать свою цену и торговаться\n"
                 "• Получать уведомления о новых объявлениях\n"
                 "• Строить репутацию через систему отзывов\n\n"
                 "Доступ только для верифицированных участников.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Зарегистрироваться", callback_data="reg_start")]]),
        )
        return

    if data == "reg_start":
        username = query.from_user.username
        if not username:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ У вас нет username в Telegram!\n\n"
                     "Без него регистрация невозможна.\n\n"
                     "Как создать username:\n"
                     "1. Откройте Настройки в Telegram\n"
                     "2. Нажмите Аккаунт\n"
                     "3. Нажмите Имя пользователя\n"
                     "4. Придумайте @ник и сохраните\n\n"
                     "После этого напишите /start чтобы продолжить.",
            )
            return
        # Сохраняем username и сразу регистрируем
        save_user(user_id, {
            "username": username,
            "first_name": query.from_user.first_name or "",
            "last_name": query.from_user.last_name or "",
            "phone": "",
            "status": "active",
            "joined": "no",
        })
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🆕 Новый участник - KAZ_METALS\n\n"
                 f"🔗 Username: @{username}\n"
                 f"🆔 Telegram ID: {user_id}"
        )
        states[user_id] = {"step": "awaiting_join"}
        await context.bot.send_message(
            chat_id=chat_id,
            text="🎉 Регистрация завершена!\n\n"
                 "Осталось вступить в группу KAZ_METALS.\n\n"
                 "1️⃣ Нажмите Вступить в KAZ_METALS\n"
                 "2️⃣ Вступите в группу\n"
                 "3️⃣ Вернитесь и нажмите Я вступил 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔓 Вступить в KAZ_METALS", url=GROUP_LINK)],
                [InlineKeyboardButton("✅ Я вступил", callback_data="check_join")],
            ])
        )
        return

    if data == "check_join":
        ok = await check_user_in_group(context.bot, int(user_id), GROUP_ID)
        if ok:
            update_user_field(user_id, "joined", "yes")
            states[user_id] = {"step": "menu"}
            await context.bot.send_message(
                chat_id=chat_id,
                text="🏅 Добро пожаловать в KAZ_METALS!\n\nВыберите действие 👇",
                reply_markup=main_menu_keyboard()
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Пока не вижу вас в группе. Вступите и нажмите проверку ещё раз.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔓 Вступить в KAZ_METALS", url=GROUP_LINK)],
                    [InlineKeyboardButton("✅ Я вступил - проверить", callback_data="check_join")],
                ]),
            )
        return

    if data == "back_menu":
        states[user_id] = {"step": "menu"}
        await context.bot.send_message(chat_id=chat_id, text="🏅 Главное меню", reply_markup=main_menu_keyboard())
        return

    if data == "back_admin":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        users_count = db_fetchone("SELECT COUNT(*) AS c FROM users") or {"c": 0}
        ads_count = db_fetchone("SELECT COUNT(*) AS c FROM ads") or {"c": 0}
        complaints_count = db_fetchone("SELECT COUNT(*) AS c FROM complaints") or {"c": 0}
        blocked_count = db_fetchone("SELECT COUNT(*) AS c FROM users WHERE status = 'blocked'") or {"c": 0}
        pending_count = db_fetchone("SELECT COUNT(*) AS c FROM ads WHERE status = 'pending'") or {"c": 0}
        disputed_count = db_fetchone("SELECT COUNT(*) AS c FROM deals WHERE status IN ('dispute', 'review_not_completed')") or {"c": 0}
        await context.bot.send_message(
            chat_id=chat_id,
            text="🛠 Админ-панель KAZ_METALS\n\n"
                 f"👥 Пользователи: {users_count['c']}\n"
                 f"📦 Объявления: {ads_count['c']}\n"
                 f"📢 На модерации: {pending_count['c']}\n"
                 f"⚠️ Жалобы: {complaints_count['c']}\n"
                 f"🚫 Заблокированные: {blocked_count['c']}\n"
                 f"🤝 Спорные сделки: {disputed_count['c']}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
                [InlineKeyboardButton("📢 Объявления на модерации", callback_data="admin_pending_ads")],
                [InlineKeyboardButton("⚠️ Жалобы", callback_data="admin_complaints")],
                [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
                [InlineKeyboardButton("🚫 Заблокированные", callback_data="admin_blocked")],
                [InlineKeyboardButton("✏️ Отправить на доработку", callback_data="admin_revision")],
                [InlineKeyboardButton("🗑 Удалить объявление", callback_data="admin_delete_ad")],
                [InlineKeyboardButton("💥 УДАЛИТЬ ВСЕ объявления", callback_data="admin_delete_all_ads")],
                [InlineKeyboardButton("🔄 Сбросить пользователя", callback_data="admin_reset_user")],
                [InlineKeyboardButton("🤝 Спорные сделки", callback_data="admin_disputes")],
                [InlineKeyboardButton("👁 Просмотры объявлений", callback_data="admin_ad_views")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")],
            ]),
        )
        return

    if data == "menu_ad":
        # Аукцион отключён: сразу создаём объявление типа "продажа".
        states[user_id] = {"step": "ad_category", "ad_type": "продажа"}
        await context.bot.send_message(chat_id=chat_id, text="Выберите категорию:", reply_markup=categories_keyboard("adcat"))
        return

    if data.startswith("adtype_"):
        # Оставлено на случай старых inline-кнопок у пользователей.
        states[user_id] = {"step": "ad_category", "ad_type": "продажа"}
        await context.bot.send_message(chat_id=chat_id, text="Выберите категорию:", reply_markup=categories_keyboard("adcat"))
        return

    if data.startswith("adcat_"):
        idx = int(data.split("_")[1])
        states[user_id]["category"] = CATEGORIES[idx]
        states[user_id]["step"] = "ad_description"
        await context.bot.send_message(chat_id=chat_id, text="📝 Опишите товар:")
        return

    if data.startswith("city_"):
        is_wb = states.get(user_id, {}).get("step", "").startswith("wb_")
        if data == "city_other":
            states[user_id]["step"] = "wb_city_text" if is_wb else "ad_city_text"
            await context.bot.send_message(chat_id=chat_id, text="Введите город:")
        else:
            city = data.replace("city_", "")
            states[user_id]["city"] = city
            if is_wb:
                states[user_id]["step"] = "wb_confirm"
                await show_want_buy_preview(chat_id, context, user_id)
            else:
                states[user_id]["step"] = "ad_photo"
                await context.bot.send_message(chat_id=chat_id, text="📸 Отправьте 1 главное фото товара (или напишите -, чтобы пропустить).\n\nЭто фото будет показано в группе. Дополнительные фото и видео можно добавить позже.")
        return

    if data in ("skip_extra_media", "extra_done", "extra_skip"):
        states[user_id]["step"] = "ad_certificate"
        await context.bot.send_message(
            chat_id=chat_id,
            text="📋 Прикрепите фото сертификата подлинности (или напишите -, чтобы пропустить).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить", callback_data="skip_certificate")]]),
        )
        return

    if data == "skip_certificate":
        states[user_id]["cert_file_id"] = ""
        await show_ad_preview(chat_id, context, user_id)
        return

    if data == "ad_cancel":
        states[user_id] = {"step": "menu"}
        await context.bot.send_message(chat_id=chat_id, text="❌ Отменено.", reply_markup=main_menu_keyboard())
        return

    if data == "ad_confirm":
        row = db_fetchone("SELECT COUNT(*) AS cnt FROM ads WHERE user_id=%s AND status='active'", (user_id,))
        if row and int(row.get("cnt", 0)) >= 3:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ У вас уже 3 активных объявления — это максимум.\nУдалите одно в разделе <b>Мой профиль → Мои объявления</b>, затем добавьте новое.",
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
            return
        await publish_ad(context, chat_id, user_id)
        return

    if data == "menu_want_buy":
        states[user_id] = {"step": "wb_desc"}
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔍 <b>Хочу купить</b>\n\nОпишите что именно ищете — бренд, размер, проба, состояние, особые пожелания.\n\n<i>Пример: Rolex Submariner, б/у, желательно с документами</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="wb_cancel")]]),
        )
        return

    if data == "wb_cancel":
        states[user_id] = {"step": "menu"}
        await context.bot.send_message(chat_id=chat_id, text="❌ Отменено.", reply_markup=main_menu_keyboard())
        return

    if data == "wb_confirm_yes":
        await publish_want_buy(context, chat_id, user_id)
        return

    if data == "menu_calc":
        states[user_id] = {"step": "calc_metal"}
        await context.bot.send_message(
            chat_id=chat_id,
            text="🧮 Калькулятор металлов\n\nВыберите металл:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🥇 Золото", callback_data="calc_XAU"),
                 InlineKeyboardButton("🥈 Серебро", callback_data="calc_XAG")],
                [InlineKeyboardButton("💎 Платина", callback_data="calc_XPT"),
                 InlineKeyboardButton("🔩 Палладий", callback_data="calc_XPD")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
            ])
        )
        return

    if data.startswith("calc_") and len(data) == 8:
        metal = data.replace("calc_", "")
        metal_names = {
            "XAU": "🥇 Золото",
            "XAG": "🥈 Серебро",
            "XPT": "💎 Платина",
            "XPD": "🔩 Палладий"
        }
        # Probas by metal
        probas = {
            "XAU": ["999", "750", "585", "375", "Другая"],
            "XAG": ["999", "925", "875", "800", "Другая"],
            "XPT": ["999", "950", "850", "Другая"],
            "XPD": ["999", "950", "Другая"],
        }
        states[user_id] = {"step": "calc_purity", "calc_metal": metal}
        purity_buttons = []
        row = []
        for p in probas.get(metal, ["999"]):
            row.append(InlineKeyboardButton(p, callback_data=f"cpurity_{metal}_{p}"))
            if len(row) == 3:
                purity_buttons.append(row)
                row = []
        if row:
            purity_buttons.append(row)
        purity_buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="menu_calc")])
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🧮 {metal_names.get(metal, metal)}\n\nВыберите пробу:",
            reply_markup=InlineKeyboardMarkup(purity_buttons)
        )
        return

    if data.startswith("cpurity_"):
        parts = data.split("_")
        metal = parts[1]
        purity_str = parts[2]
        metal_names = {
            "XAU": "🥇 Золото",
            "XAG": "🥈 Серебро",
            "XPT": "💎 Платина",
            "XPD": "🔩 Палладий"
        }
        if purity_str == "Другая":
            states[user_id] = {"step": "calc_custom_purity", "calc_metal": metal}
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🧮 {metal_names.get(metal, metal)}\n\nВведите пробу числом:\n(например: 585 или 925)"
            )
        else:
            states[user_id] = {"step": "calc_weight", "calc_metal": metal, "calc_purity": purity_str}
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🧮 {metal_names.get(metal, metal)} | Проба {purity_str}\n\nВведите вес в граммах:\n(например: 5 или 12.5)"
            )
        return

    if data == "menu_prices":
        prices, usd_kzt = await get_metal_prices()
        if not prices:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Сейчас не удалось получить цены. Попробуйте позже.")
            return
        text = f"📊 Актуальные цены на металлы\n💵 USD/KZT: {usd_kzt}\n\n"
        for _, (name, price, unit) in prices.items():
            text += f"{name}: {price:,} KZT/{unit}\n".replace(",", " ")
        text += "\nИсточник: LBMA + курс НБРК."
        await context.bot.send_message(chat_id=chat_id, text=text)
        return

    if data == "menu_subs":
        await context.bot.send_message(chat_id=chat_id, text="Выберите категорию для подписки/отписки:", reply_markup=categories_keyboard("sub"))
        return

    if data.startswith("sub_"):
        idx = int(data.split("_")[1])
        cat = CATEGORIES[idx]
        user = get_user(user_id) or {}
        subs = [s for s in (user.get("subscriptions") or "").split("|") if s]
        if cat in subs:
            subs.remove(cat)
            msg = f"🔕 Подписка на {cat} отключена."
        else:
            subs.append(cat)
            msg = f"🔔 Подписка на {cat} включена."
        update_user_field(user_id, "subscriptions", "|".join(subs))
        await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=main_menu_keyboard())
        return

    if data == "menu_profile":
        user = get_user(user_id)
        if not user:
            await context.bot.send_message(chat_id=chat_id, text="Профиль не найден. Напишите /start")
            return
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"👤 Профиль\n\n{user.get('first_name','')} {user.get('last_name','')}\n"
                 f"@{user.get('username','')}\n"
                 f"⭐ Рейтинг: {user.get('rating', 0)} | Отзывов: {user.get('reviews_count', 0)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📦 Мои объявления", callback_data="profile_ads")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")],
            ]),
        )
        return

    if data == "profile_ads":
        ads = db_fetchall("SELECT * FROM ads WHERE user_id = %s AND status = 'active' ORDER BY ad_id DESC", (user_id,))
        if not ads:
            await context.bot.send_message(chat_id=chat_id, text="У вас пока нет активных объявлений.", reply_markup=main_menu_keyboard())
            return
        rows = []
        for ad in ads:
            ad_id_str = str(ad.get("ad_id",""))
            label = f"№{ad_id_str}  {ad.get('category','')}  —  {ad.get('price','')} KZT"
            rows.append([InlineKeyboardButton(label[:60], callback_data=f"profile_ad_{ad_id_str}")])
        rows.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")])
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📦 <b>Мои объявления</b> ({len(ads)}/3):",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("profile_ad_"):
        ad_id = data.replace("profile_ad_", "")
        ad = db_fetchone("SELECT * FROM ads WHERE ad_id = %s AND user_id = %s", (int(ad_id), user_id)) if ad_id.isdigit() else None
        if not ad or ad.get("status") not in ("active","sold"):
            await context.bot.send_message(chat_id=chat_id, text="Объявление не найдено.", reply_markup=main_menu_keyboard())
            return
        cat   = ad.get("category","")
        city  = ad.get("city","")
        desc  = (ad.get("description","") or "")[:300]
        price = ad.get("price","")
        exp   = ad.get("expires_at","")
        views = ad.get("view_count", 0)
        caption = f"{cat}  ·  {city}\n\n{desc}\n\n💰 {price} KZT\n📅 До: {exp}\n👁 Просмотров: {views}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_ad_{ad_id}")],
            [InlineKeyboardButton("🔙 К списку", callback_data="profile_ads")],
        ])
        try:
            if ad.get("photo_id"):
                await context.bot.send_photo(chat_id=chat_id, photo=ad["photo_id"], caption=caption, reply_markup=kb)
            else:
                await context.bot.send_message(chat_id=chat_id, text=caption, reply_markup=kb)
        except Exception as e:
            logger.error(f"profile_ad display: {e}")
        return

    if data.startswith("mark_sold_"):
        ad_id = data.replace("mark_sold_", "")
        ad = db_fetchone("SELECT * FROM ads WHERE ad_id = %s AND user_id = %s", (int(ad_id), user_id)) if ad_id.isdigit() else None
        if not ad:
            await context.bot.send_message(chat_id=chat_id, text="Объявление не найдено.")
            return
        try:
            conn_s = get_conn(); cur_s = conn_s.cursor()
            cur_s.execute("UPDATE ads SET status='sold' WHERE ad_id=%s AND user_id=%s", (int(ad_id), user_id))
            conn_s.commit(); cur_s.close(); conn_s.close()
        except Exception as e:
            logger.error(f"mark_sold: {e}")
        msg_id = ad.get("message_id")
        if msg_id and GROUP_ID:
            s_cat  = ad.get("category","")
            s_desc = ad.get("description","")
            s_price = ad.get("price","")
            sold_text = f"🔴 <b>ПРОДАНО</b>\n━━━━━━━━━━━━━━━━\n{s_cat}\n\n{s_desc}\n\n💰 {s_price} KZT"
            empty_kb = InlineKeyboardMarkup([])
            try:
                await context.bot.edit_message_caption(chat_id=GROUP_ID, message_id=int(msg_id), caption=sold_text, parse_mode="HTML", reply_markup=empty_kb)
            except Exception:
                try:
                    await context.bot.edit_message_text(chat_id=GROUP_ID, message_id=int(msg_id), text=sold_text, parse_mode="HTML", reply_markup=empty_kb)
                except Exception as e:
                    logger.error(f"mark_sold group edit: {e}")
        await context.bot.send_message(chat_id=chat_id, text="✅ Объявление отмечено как проданное.", reply_markup=main_menu_keyboard())
        return

    if data.startswith("profile_ad_"):
        pass  # legacy, handled above now

    if data.startswith("delete_ad_"):
        ad_id = data.replace("delete_ad_", "")
        ad = db_fetchone("SELECT * FROM ads WHERE ad_id = %s AND user_id = %s", (int(ad_id), user_id)) if ad_id.isdigit() else None
        if not ad or ad.get("status") != "active":
            await context.bot.send_message(chat_id=chat_id, text="Объявление не найдено или уже удалено.", reply_markup=main_menu_keyboard())
            return
        try:
            if ad.get("message_id"):
                await context.bot.delete_message(chat_id=GROUP_ID, message_id=int(ad.get("message_id")))
        except Exception as e:
            logger.error(f"delete group ad message error: {e}")
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE ads SET status = 'deleted' WHERE ad_id = %s AND user_id = %s", (int(ad_id), user_id))
        conn.commit()
        cur.close()
        conn.close()
        await context.bot.send_message(chat_id=chat_id, text=f"✅ Объявление №{ad_id} удалено.", reply_markup=main_menu_keyboard())
        return

    if data == "menu_rep":
        user = get_user(user_id) or {}
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⭐ Ваша репутация: {user.get('rating', 0)}\nОтзывов: {user.get('reviews_count', 0)}",
            reply_markup=main_menu_keyboard(),
        )
        return

    if data == "menu_how":
        await context.bot.send_message(
            chat_id=chat_id,
            text="📋 Как работает KAZ_METALS\n\n"
                 "🔷 Регистрация\n"
                 "Подтвердите свой @username и вступите в группу.\n\n"
                 "🔷 Для продавца\n"
                 "1. Нажмите Подать объявление\n"
                 "2. Выберите категорию и город\n"
                 "3. Опишите товар и укажите цену\n"
                 "4. Загрузите 1 главное фото\n"
                 "5. По желанию добавьте доп. фото и видео\n"
                 "6. При наличии — прикрепите сертификат подлинности\n"
                 "7. Объявление сразу появится в группе\n\n"
                 "🔷 Для покупателя\n"
                 "1. Зайдите в группу KAZ_METALS\n"
                 "2. Найдите нужное объявление\n"
                 "3. Выберите действие:\n"
                 "   📸 Фото и видео - смотреть медиа\n"
                 "   💰 Купить - по цене продавца\n"
                 "   🤝 Предложить цену - торговаться\n"
                 "4. После подтверждения оба получат контакты\n\n"
                 "🔷 Репутация\n"
                 "После каждой сделки через 3 дня оба участника получают запрос на оценку. Рейтинг виден в каждом объявлении.\n\n"
                 "🔷 Цены и алерты\n"
                 "В боте — кнопка 📊 Цены на металлы с актуальным курсом золота, серебра, платины и палладия в KZT за грамм. В группе — вкладка ⚡ Ценовые алерты: бот публикует уведомление когда цена любого металла резко меняется.\n\n"
                 "⚖️ Правовая оговорка\n"
                 "KAZ_METALS - платформа-посредник. Мы соединяем продавцов и покупателей, но не являемся стороной сделок. Безопасность участников - наш приоритет.",
            reply_markup=main_menu_keyboard(),
        )
        return

    if data == "admin_ad_views":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        await context.bot.send_message(chat_id=chat_id, text=admin_ads_views_text())
        return

    if data == "admin_stats":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        users_count = db_fetchone("SELECT COUNT(*) AS c FROM users") or {"c": 0}
        active_ads = db_fetchone("SELECT COUNT(*) AS c FROM ads WHERE status = 'active'") or {"c": 0}
        deals_count = db_fetchone("SELECT COUNT(*) AS c FROM deals") or {"c": 0}
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📊 Статистика KAZ_METALS\n\n👥 Пользователи: {users_count['c']}\n📦 Активные объявления: {active_ads['c']}\n🤝 Сделки/контакты: {deals_count['c']}",
        )
        return

    if data == "admin_users":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        rows = db_fetchall("SELECT user_id, first_name, last_name, username, status FROM users ORDER BY registered_at DESC LIMIT 20")
        if not rows:
            await context.bot.send_message(chat_id=chat_id, text="👥 Пользователей пока нет.")
            return
        text = "👥 Последние пользователи\n\n"
        buttons = []
        for r in rows:
            username = r.get("username") or "без username"
            text += f"ID: {r.get('user_id')} | @{username} | {r.get('status','')}\n"
            buttons.append([InlineKeyboardButton(f"@{username} - {r.get('status','')}", callback_data=f"admin_user_{r.get('user_id')}")])
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("admin_user_"):
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        target_id = data.replace("admin_user_", "")
        u = get_user(target_id) or {}
        if not u:
            await context.bot.send_message(chat_id=chat_id, text="Пользователь не найден.")
            return
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"👤 Пользователь\nID: {target_id}\n@{u.get('username','')}\nСтатус: {u.get('status','')}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Верифицировать", callback_data=f"admin_verify_{target_id}")],
                [InlineKeyboardButton("🚫 Заблокировать", callback_data=f"admin_block_{target_id}")],
                [InlineKeyboardButton("🔓 Разблокировать", callback_data=f"admin_unblock_{target_id}")],
                [InlineKeyboardButton("🤝 Сделки пользователя", callback_data=f"admin_user_deals_{target_id}")],
            ]),
        )
        return

    if data.startswith("admin_verify_") or data.startswith("admin_block_") or data.startswith("admin_unblock_"):
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        if data.startswith("admin_verify_"):
            target_id = data.replace("admin_verify_", "")
            status = "active"
            msg = "✅ Пользователь верифицирован."
        elif data.startswith("admin_block_"):
            target_id = data.replace("admin_block_", "")
            status = "blocked"
            msg = "🚫 Пользователь заблокирован."
        else:
            target_id = data.replace("admin_unblock_", "")
            status = "active"
            msg = "🔓 Пользователь разблокирован."
        update_user_field(target_id, "status", status)
        await context.bot.send_message(chat_id=chat_id, text=msg)
        return

    if data.startswith("admin_user_deals_"):
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        target_id = data.replace("admin_user_deals_", "")
        deals = db_fetchall("SELECT * FROM deals WHERE seller_id = %s OR buyer_id = %s ORDER BY deal_id DESC LIMIT 20", (target_id, target_id))
        if not deals:
            await context.bot.send_message(chat_id=chat_id, text="🤝 Сделок по пользователю пока нет.")
            return
        text = f"🤝 Сделки пользователя {target_id}\n\n"
        for d in deals:
            text += f"№{d.get('deal_id')} | объявление {d.get('ad_id')} | {d.get('status','')} | {d.get('offer_amount','')}\n"
        await context.bot.send_message(chat_id=chat_id, text=text)
        return

    if data == "admin_pending_ads":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        ads = db_fetchall("SELECT * FROM ads WHERE status = 'pending' ORDER BY ad_id DESC LIMIT 20")
        if not ads:
            await context.bot.send_message(chat_id=chat_id, text="📢 Объявлений на модерации нет.")
            return
        text = "📢 Объявления на модерации\n\n" + "\n".join([f"№{a.get('ad_id')} | {a.get('category','')} | {a.get('price','')}" for a in ads])
        await context.bot.send_message(chat_id=chat_id, text=text)
        return

    if data == "admin_complaints":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        complaints = db_fetchall("SELECT * FROM complaints ORDER BY complaint_id DESC LIMIT 20")
        if not complaints:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Жалоб пока нет.")
            return
        text = "⚠️ Жалобы\n\n" + "\n".join([f"№{c.get('complaint_id')} | объявление {c.get('ad_id')} | {c.get('reason','')}" for c in complaints])
        await context.bot.send_message(chat_id=chat_id, text=text)
        return

    if data == "admin_blocked":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        blocked = db_fetchall("SELECT user_id, username FROM users WHERE status = 'blocked' ORDER BY registered_at DESC LIMIT 30")
        if not blocked:
            await context.bot.send_message(chat_id=chat_id, text="🚫 Заблокированных пользователей нет.")
            return
        buttons = [[InlineKeyboardButton(f"🔓 @{b.get('username') or b.get('user_id')}", callback_data=f"admin_unblock_{b.get('user_id')}")] for b in blocked]
        await context.bot.send_message(chat_id=chat_id, text="🚫 Заблокированные:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "admin_delete_ad":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        states[user_id] = {"step": "admin_delete_ad_waiting"}
        await context.bot.send_message(chat_id=chat_id, text="🗑 Введите номер объявления, которое нужно удалить:")
        return

    if data == "admin_reset_user":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT user_id, username, first_name, status FROM users ORDER BY registered_at DESC LIMIT 50")
            all_users = cur.fetchall()
            cur.close()
            conn.close()
            if not all_users:
                await context.bot.send_message(chat_id=chat_id, text="⚠️ Нет пользователей в базе.")
                return
            keyboard = []
            for u in all_users:
                uname = u.get("username") or u.get("first_name") or str(u.get("user_id"))
                status = u.get("status") or ""
                tag = " ↺" if status == "reset" else ""
                label = f"@{uname}{tag}"
                keyboard.append([InlineKeyboardButton(label, callback_data=f"reset_confirm_{u.get('user_id')}")])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_admin")])
            await context.bot.send_message(
                chat_id=chat_id,
                text="🔄 Выберите пользователя для сброса регистрации:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Ошибка: {e}")
        return

    if data.startswith("reset_confirm_"):
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        target_id = data.replace("reset_confirm_", "")
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM users WHERE user_id = %s", (target_id,))
            target_user = cur.fetchone()
            cur2 = conn.cursor()
            cur2.execute("DELETE FROM users WHERE user_id = %s", (target_id,))
            conn.commit()
            cur.close()
            cur2.close()
            conn.close()
            states.pop(str(target_id), None)
            uname = target_user.get("username") if target_user else target_id

            # Исключаем из группы
            try:
                await context.bot.ban_chat_member(chat_id=GROUP_ID, user_id=int(target_id))
                await _asyncio.sleep(1)
                await context.bot.unban_chat_member(chat_id=GROUP_ID, user_id=int(target_id))
            except Exception as e:
                logger.error(f"Kick from group error: {e}")

            # Уведомляем пользователя
            try:
                await context.bot.send_message(
                    chat_id=int(target_id),
                    text="🔄 Ваша регистрация была сброшена администратором.\n\n"
                         "Для повторного доступа пройдите регистрацию заново - напишите /start"
                )
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Данные @{uname} полностью удалены. Пользователь исключён из группы и может пройти регистрацию заново.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Админ-панель", callback_data="back_admin")]])
            )
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Ошибка: {e}")
        return

    if data == "admin_delete_all_ads":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        active_count = db_fetchone("SELECT COUNT(*) AS c FROM ads WHERE status = 'active'") or {"c": 0}
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"💥 ВНИМАНИЕ!\n\nВы собираетесь удалить ВСЕ активные объявления ({active_count['c']} шт.) из группы и из базы.\n\n⚠️ Это действие необратимо!\n\nПодтверждаете?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Да, удалить ВСЁ", callback_data="admin_delete_all_confirm")],
                [InlineKeyboardButton("❌ Отмена", callback_data="back_menu")],
            ])
        )
        return

    if data == "admin_delete_all_confirm":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        await context.bot.send_message(chat_id=chat_id, text="🗑 Удаляю все объявления, подождите...")
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT ad_id, message_id, media_message_id FROM ads WHERE status = 'active'")
            all_ads = cur.fetchall()
            cur.close()
            conn.close()

            total = len(all_ads)
            deleted_db = 0
            deleted_group = 0
            deleted_media = 0
            errors_log = []

            for ad in all_ads:
                ad_id = ad.get("ad_id")
                msg_id = ad.get("message_id")
                media_id = ad.get("media_message_id")

                # Удаляем сообщение из группы
                if msg_id and str(msg_id).strip() and str(msg_id) != "0":
                    try:
                        await context.bot.delete_message(chat_id=GROUP_ID, message_id=int(msg_id))
                        deleted_group += 1
                    except Exception as e:
                        errors_log.append(f"#{ad_id}: {str(e)[:80]}")
                        logger.error(f"Delete ad #{ad_id} from group: {e}")

                # Удаляем сообщение из медиа канала
                if media_id and str(media_id).strip() and str(media_id) != "0":
                    try:
                        await context.bot.delete_message(chat_id=MEDIA_CHANNEL_ID, message_id=int(media_id))
                        deleted_media += 1
                    except Exception as e:
                        logger.error(f"Delete ad #{ad_id} from media: {e}")

                # Помечаем в базе как удалённое
                try:
                    conn2 = get_conn()
                    cur2 = conn2.cursor()
                    cur2.execute("UPDATE ads SET status = 'deleted' WHERE ad_id = %s", (ad_id,))
                    conn2.commit()
                    cur2.close()
                    conn2.close()
                    deleted_db += 1
                except Exception as e:
                    logger.error(f"Mark ad #{ad_id} deleted in DB: {e}")

            err_text = ""
            if errors_log:
                err_text = "\n\n⚠️ Первые ошибки:\n" + "\n".join(errors_log[:5])

            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Готово!\n\n"
                     f"📊 Всего объявлений: {total}\n"
                     f"🗑 Удалено из БД: {deleted_db}\n"
                     f"🗑 Удалено из группы: {deleted_group}\n"
                     f"🗑 Удалено из медиа канала: {deleted_media}{err_text}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Админ-панель", callback_data="back_admin")]])
            )
        except Exception as e:
            logger.error(f"admin_delete_all_confirm error: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Ошибка: {e}")
        return

    if data == "admin_revision":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        states[user_id] = {"step": "admin_revision_ad_waiting"}
        await context.bot.send_message(chat_id=chat_id, text="✏️ Введите номер объявления, которое нужно отправить на доработку:")
        return

    if data == "admin_disputes":
        if not is_admin(user_id):
            await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно только владельцу.")
            return
        deals = db_fetchall("SELECT * FROM deals WHERE status IN ('dispute', 'review_not_completed') ORDER BY deal_id DESC LIMIT 20")
        if not deals:
            await context.bot.send_message(chat_id=chat_id, text="🤝 Спорных/незавершённых сделок нет.")
            return
        text = "🤝 Спорные/незавершённые сделки\n\n" + "\n".join([f"№{d.get('deal_id')} | объявление {d.get('ad_id')} | {d.get('status','')}" for d in deals])
        await context.bot.send_message(chat_id=chat_id, text=text)
        return

    if data == "menu_admin":
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📞 Администратор: @{ADMIN_USERNAME}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"Написать @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")],
            ]),
        )
        return

    if data == "buy_cancel":
        await context.bot.send_message(chat_id=chat_id, text="❌ Отменено.")
        return

    if data.startswith("buy_confirm_"):
        parts = data.replace("buy_confirm_", "").split("_")
        seller_id = parts[0]
        ad_id = parts[1] if len(parts) > 1 else "0"
        seller = get_user(seller_id) or {}
        buyer = get_user(user_id) or {}
        deal_id = save_deal(seller_id, user_id, ad_id)
        if deal_id and context.application.job_queue is not None:
            context.application.job_queue.run_once(review_prompt_job, when=timedelta(days=3), data={"deal_id": str(deal_id)}, name=f"review_{deal_id}")
        await context.bot.send_message(
            chat_id=int(user_id),
            text=f"✅ Контакты продавца:\n{seller.get('first_name','')} {seller.get('last_name','')}\n@{seller.get('username','')}\n\nСвяжитесь между собой 🤝",
        )
        await context.bot.send_message(
            chat_id=int(seller_id),
            text=f"📩 Покупатель заинтересовался объявлением #{ad_id}:\n{buyer.get('first_name','')} {buyer.get('last_name','')}\n@{buyer.get('username','')}\n\nСвяжитесь между собой 🤝",
        )
        # Помечаем объявление как проданное и редактируем сообщение в группе
        ad = db_fetchone("SELECT * FROM ads WHERE ad_id = %s", (int(ad_id),)) if str(ad_id).isdigit() else None
        if ad:
            try:
                conn_s = get_conn(); cur_s = conn_s.cursor()
                cur_s.execute("UPDATE ads SET status = 'sold' WHERE ad_id = %s", (int(ad_id),))
                conn_s.commit(); cur_s.close(); conn_s.close()
            except Exception as _e:
                logger.error(f"mark sold: {_e}")
            msg_id = ad.get("message_id")
            if msg_id and GROUP_ID:
                original  = (ad.get("description") or "").strip()
                price     = ad.get("price") or ""
                category  = ad.get("category") or ""
                sold_text = (
                    f"🔴 <b>ПРОДАНО</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"{category}\n\n"
                    f"{original}\n\n"
                    f"💰 {price} KZT"
                )
                empty_kb = InlineKeyboardMarkup([])
                try:
                    # Фото-объявление → edit_message_caption
                    await context.bot.edit_message_caption(
                        chat_id=GROUP_ID,
                        message_id=int(msg_id),
                        caption=sold_text,
                        parse_mode="HTML",
                        reply_markup=empty_kb,
                    )
                except Exception:
                    try:
                        # Текстовое объявление → edit_message_text
                        await context.bot.edit_message_text(
                            chat_id=GROUP_ID,
                            message_id=int(msg_id),
                            text=sold_text,
                            parse_mode="HTML",
                            reply_markup=empty_kb,
                        )
                    except Exception as _e:
                        logger.error(f"edit sold msg: {_e}")
        return

    if data.startswith("offer_accept_"):
        deal_id = data.replace("offer_accept_", "")
        deal = db_fetchone("SELECT * FROM deals WHERE deal_id = %s", (int(deal_id),)) if deal_id.isdigit() else None
        if not deal:
            await context.bot.send_message(chat_id=chat_id, text="❌ Предложение не найдено или уже недоступно.")
            return
        if str(deal.get("seller_id")) != user_id:
            await context.bot.send_message(chat_id=chat_id, text="❌ Это предложение адресовано не вам.")
            return
        if deal.get("status") not in ("offer_pending", ""):
            await context.bot.send_message(chat_id=chat_id, text="ℹ️ По этому предложению уже принято решение.")
            return

        update_deal_status(deal_id, "offer_accepted")
        set_deal_review_due(deal_id, days=1)
        if context.application.job_queue is not None:
            context.application.job_queue.run_once(review_prompt_job, when=timedelta(days=3), data={"deal_id": str(deal_id)}, name=f"review_{deal_id}")
        seller = get_user(deal.get("seller_id")) or {}
        buyer = get_user(deal.get("buyer_id")) or {}
        buyer_username = buyer.get("username") or "без username"
        seller_username = seller.get("username") or "без username"
        offer_amount = deal.get("offer_amount") or ""
        ad_id = deal.get("ad_id") or ""

        await context.bot.send_message(
            chat_id=int(deal.get("buyer_id")),
            text=(
                f"✅ Продавец согласился на вашу цену по объявлению #{ad_id}.\n"
                f"💸 Цена: {offer_amount} KZT\n\n"
                f"👤 Контакт продавца: @{seller_username}"
            ),
        )
        await context.bot.send_message(
            chat_id=int(deal.get("seller_id")),
            text=(
                f"✅ Вы согласились на предложение по объявлению #{ad_id}.\n"
                f"💸 Цена: {offer_amount} KZT\n\n"
                f"👤 Контакт покупателя: @{buyer_username}"
            ),
        )
        await query.edit_message_text((query.message.text or "") + "\n\n✅ Вы приняли предложение. Контакты отправлены обеим сторонам.")
        return

    if data.startswith("offer_reject_"):
        deal_id = data.replace("offer_reject_", "")
        deal = db_fetchone("SELECT * FROM deals WHERE deal_id = %s", (int(deal_id),)) if deal_id.isdigit() else None
        if not deal:
            await context.bot.send_message(chat_id=chat_id, text="❌ Предложение не найдено или уже недоступно.")
            return
        if str(deal.get("seller_id")) != user_id:
            await context.bot.send_message(chat_id=chat_id, text="❌ Это предложение адресовано не вам.")
            return
        if deal.get("status") not in ("offer_pending", ""):
            await context.bot.send_message(chat_id=chat_id, text="ℹ️ По этому предложению уже принято решение.")
            return

        update_deal_status(deal_id, "offer_rejected")
        offer_amount = deal.get("offer_amount") or ""
        ad_id = deal.get("ad_id") or ""
        await context.bot.send_message(
            chat_id=int(deal.get("buyer_id")),
            text=(
                f"❌ Продавец отказался от вашего предложения по объявлению #{ad_id}.\n"
                f"💸 Ваша цена: {offer_amount} KZT\n\n"
                "Вы можете предложить новую цену."
            ),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🤝 Предложить новую цену", url=f"https://t.me/{BOT_USERNAME}?start=offer_{deal.get('seller_id')}_{ad_id}")]]),
        )
        await query.edit_message_text((query.message.text or "") + "\n\n❌ Вы отказались от предложения. Покупатель уведомлён.")
        return

    if data.startswith("deal_notdone_"):
        deal_id = data.replace("deal_notdone_", "")
        deal = db_fetchone("SELECT * FROM deals WHERE deal_id = %s", (int(deal_id),)) if deal_id.isdigit() else None
        if not deal or user_id not in (str(deal.get("seller_id")), str(deal.get("buyer_id"))):
            await context.bot.send_message(chat_id=chat_id, text="Сделка не найдена.")
            return
        update_deal_status(deal_id, "review_not_completed")
        set_deal_review_due(deal_id, days=1)
        await context.bot.send_message(chat_id=chat_id, text="✅ Понял. Напомню оценить сделку снова через сутки.")
        return

    if data.startswith("deal_done_"):
        deal_id = data.replace("deal_done_", "")
        deal = db_fetchone("SELECT * FROM deals WHERE deal_id = %s", (int(deal_id),)) if deal_id.isdigit() else None
        if not deal or user_id not in (str(deal.get("seller_id")), str(deal.get("buyer_id"))):
            await context.bot.send_message(chat_id=chat_id, text="Сделка не найдена.")
            return
        await context.bot.send_message(
            chat_id=chat_id,
            text="Поставьте оценку участнику сделки:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(str(i), callback_data=f"rating_{deal_id}_{i}") for i in range(1, 6)]]),
        )
        return

    if data.startswith("rating_"):
        parts = data.split("_")
        deal_id = parts[1] if len(parts) > 1 else ""
        rating = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        deal = db_fetchone("SELECT * FROM deals WHERE deal_id = %s", (int(deal_id),)) if str(deal_id).isdigit() else None
        if not deal or rating < 1 or rating > 5 or user_id not in (str(deal.get("seller_id")), str(deal.get("buyer_id"))):
            await context.bot.send_message(chat_id=chat_id, text="Оценка не сохранена.")
            return
        to_user_id = str(deal.get("buyer_id")) if user_id == str(deal.get("seller_id")) else str(deal.get("seller_id"))
        existing = db_fetchone("SELECT review_id FROM reviews WHERE deal_id = %s AND from_user_id = %s", (str(deal_id), user_id))
        if existing:
            await context.bot.send_message(chat_id=chat_id, text="Вы уже оценили эту сделку.")
            return
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO reviews (deal_id, from_user_id, to_user_id, rating, comment, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (str(deal_id), user_id, to_user_id, rating, "", datetime.now().strftime("%d.%m.%Y %H:%M")),
        )
        conn.commit()
        cur.close()
        conn.close()
        update_user_rating_from_review(to_user_id, rating)
        mark_deal_reviewed(deal_id, user_id)
        await context.bot.send_message(chat_id=chat_id, text="✅ Спасибо, оценка сохранена.")
        return

async def show_ad_preview(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    u = states.get(user_id, {})
    extra_count = len(u.get("extra_media", []))
    text = (
        "📋 Предварительный просмотр:\n\n"
        f"🟢 {u.get('ad_type','').upper()}\n"
        f"{u.get('category','')}\n"
        f"📍 {u.get('city','')}\n\n"
        f"{u.get('description','')}\n\n"
        f"💰 Цена: {u.get('price','')} KZT\n"
        f"📌 ДОП. ФОТО/ВИДЕО: {extra_count} шт."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Опубликовать", callback_data="ad_confirm"), InlineKeyboardButton("❌ Отменить", callback_data="ad_cancel")],
    ])
    photo_id = u.get("photo_id", "")
    if photo_id:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo_id,
                caption=text,
                reply_markup=keyboard,
            )
            return
        except Exception as e:
            logger.error(f"Preview photo error: {e}")
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
    )


async def publish_ad(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: str):
    u = states.get(user_id, {})
    user = get_user(user_id) or {}
    username = user.get("username") or ""
    ad_id = save_ad({
        "user_id": user_id,
        "username": username,
        "type": u.get("ad_type", ""),
        "category": u.get("category", ""),
        "description": u.get("description", ""),
        "price": u.get("price", ""),
        "photo_id": u.get("photo_id", ""),
        "city": u.get("city", ""),
    })
    if not ad_id:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Не удалось сохранить объявление.")
        return

    media_msg_id = ""
    extra_media = u.get("extra_media", [])
    if extra_media:
        try:
            first = extra_media[0]
            caption = f"📦 Доп. медиа к объявлению #{ad_id}\n{u.get('category','')} - {u.get('price','')}"
            if first["type"] == "photo":
                msg = await context.bot.send_photo(chat_id=MEDIA_CHANNEL_ID, photo=first["file_id"], caption=caption)
            else:
                msg = await context.bot.send_video(chat_id=MEDIA_CHANNEL_ID, video=first["file_id"], caption=caption)
            media_msg_id = str(msg.message_id)
            for item in extra_media[1:]:
                if item["type"] == "photo":
                    await context.bot.send_photo(chat_id=MEDIA_CHANNEL_ID, photo=item["file_id"])
                else:
                    await context.bot.send_video(chat_id=MEDIA_CHANNEL_ID, video=item["file_id"])
        except Exception as e:
            logger.error(f"media channel error: {e}")

    cert_msg_id = ""
    cert_file_id = u.get("cert_file_id", "")
    if cert_file_id:
        try:
            cert_msg = await context.bot.send_photo(
                chat_id=MEDIA_CHANNEL_ID,
                photo=cert_file_id,
                caption=f"📋 Сертификат к объявлению #{ad_id}",
            )
            cert_msg_id = str(cert_msg.message_id)
        except Exception as e:
            logger.error(f"certificate upload error: {e}")

    seller_rating = user.get("rating", 0)
    seller_deals = user.get("reviews_count", 0)
    text = (
        f"№ {ad_id}\n\n"
        f"{u.get('description','').upper()}\n\n"
        f"💰 Цена: {u.get('price','')} KZT\n"
        f"📍 {u.get('city','')}\n"
        f"⭐ Рейтинг продавца: {seller_rating} | Сделок: {seller_deals}"
    )
    buttons = []
    if media_msg_id:
        buttons.append([InlineKeyboardButton("📸 Доп. фото/видео", url=f"https://t.me/{MEDIA_CHANNEL_USERNAME}/{media_msg_id}")])
    if cert_msg_id:
        buttons.append([InlineKeyboardButton("📋 Сертификат", url=f"https://t.me/{MEDIA_CHANNEL_USERNAME}/{cert_msg_id}")])
    buttons.append([InlineKeyboardButton(f"💰 Купить за {u.get('price','')}", url=f"https://t.me/{BOT_USERNAME}?start=buy_{user_id}_{ad_id}")])
    buttons.append([InlineKeyboardButton("🤝 Предложить цену", url=f"https://t.me/{BOT_USERNAME}?start=offer_{user_id}_{ad_id}")])

    topic_id = CATEGORY_TOPICS.get(u.get("category", ""))
    kwargs = {"message_thread_id": topic_id} if topic_id else {}
    try:
        if u.get("photo_id"):
            msg = await context.bot.send_photo(chat_id=GROUP_ID, photo=u.get("photo_id"), caption=text, reply_markup=InlineKeyboardMarkup(buttons), **kwargs)
        else:
            msg = await context.bot.send_message(chat_id=GROUP_ID, text=text, reply_markup=InlineKeyboardMarkup(buttons), **kwargs)
        update_ad_message(ad_id, str(msg.message_id), media_msg_id)
        await notify_subscribers(context, u.get("category", ""), ad_id, u)
        states[user_id] = {"step": "menu"}
        await context.bot.send_message(chat_id=chat_id, text="✅ Объявление опубликовано.", reply_markup=main_menu_keyboard())
    except Exception as e:
        logger.error(f"publish_ad error: {e}")
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Не удалось опубликовать объявление. Проверьте права бота в группе/канале.")


async def notify_subscribers(context: ContextTypes.DEFAULT_TYPE, category: str, ad_id: int, ad_data: dict):
    if not category:
        return
    subscribers = db_fetchall("SELECT user_id FROM users WHERE subscriptions LIKE %s AND status = 'active'", (f"%{category}%",))
    seller_id = str(ad_data.get("user_id", ""))
    for row in subscribers:
        sub_id = str(row.get("user_id", ""))
        if not sub_id or sub_id == seller_id:
            continue
        try:
            await context.bot.send_message(
                chat_id=int(sub_id),
                text=f"🔔 Новое объявление в категории {category}\n\n{ad_data.get('description','')[:80]}\n💰 {ad_data.get('price','')} KZT",
            )
        except Exception as e:
            logger.error(f"notify subscriber error: {e}")


async def show_want_buy_preview(chat_id: int, context, user_id: str):
    s = states.get(user_id, {})
    desc   = s.get("wb_desc", "")
    budget = s.get("wb_budget", "")
    city   = s.get("city", "")
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "📋 <b>Ваш запрос:</b>\n\n"
            f"🔍 {desc}\n"
            f"💰 Бюджет: до {budget} ₸\n"
            f"📍 {city}\n\n"
            "Опубликовать?"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Опубликовать", callback_data="wb_confirm_yes"),
             InlineKeyboardButton("❌ Отмена",       callback_data="wb_cancel")],
        ]),
    )


async def publish_want_buy(context, chat_id: int, user_id: str):
    s = states.get(user_id, {})
    user     = get_user(user_id)
    username = user.get("username", "") if user else ""
    desc     = s.get("wb_desc", "")
    budget   = s.get("wb_budget", "")
    city     = s.get("city", "")
    post = (
        f"🔍 {desc}\n\n"
        f"💰 до {budget} ₸ · 📍 {city}\n\n"
        f"📩 @{username}"
    )
    try:
        kwargs = dict(chat_id=GROUP_ID, text=post, parse_mode="HTML")
        if WANT_BUY_TOPIC_ID:
            kwargs["message_thread_id"] = WANT_BUY_TOPIC_ID
        await context.bot.send_message(**kwargs)
        states[user_id] = {"step": "menu"}
        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ Запрос опубликован! Продавцы увидят и смогут написать вам.",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as e:
        logger.error(f"publish_want_buy: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Ошибка: {e}")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    text = (update.message.text or "").strip()
    username = update.message.from_user.username or ""
    step = states.get(user_id, {}).get("step")

    if step == "admin_delete_ad_waiting" and is_admin(user_id):
        if not text.isdigit():
            await update.message.reply_text("Введите только номер объявления.")
            return
        ad = db_fetchone("SELECT * FROM ads WHERE ad_id = %s", (int(text),))
        if not ad:
            await update.message.reply_text("Объявление не найдено.")
            return
        try:
            if ad.get("message_id"):
                await context.bot.delete_message(chat_id=GROUP_ID, message_id=int(ad.get("message_id")))
        except Exception as e:
            logger.error(f"admin delete ad message error: {e}")
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE ads SET status = 'deleted' WHERE ad_id = %s", (int(text),))
        conn.commit()
        cur.close()
        conn.close()
        states[user_id] = {"step": "menu"}
        await update.message.reply_text(f"✅ Объявление №{text} удалено.")
        return

    if step == "admin_revision_ad_waiting" and is_admin(user_id):
        if not text.isdigit():
            await update.message.reply_text("Введите только номер объявления.")
            return
        ad = db_fetchone("SELECT * FROM ads WHERE ad_id = %s", (int(text),))
        if not ad:
            await update.message.reply_text("Объявление не найдено.")
            return
        states[user_id] = {"step": "admin_revision_text_waiting", "revision_ad_id": text, "revision_seller_id": str(ad.get("user_id", ""))}
        await update.message.reply_text("Напишите текст замечания для продавца:")
        return

    if step == "admin_revision_text_waiting" and is_admin(user_id):
        ad_id = states[user_id].get("revision_ad_id")
        seller_id = states[user_id].get("revision_seller_id")
        try:
            await context.bot.send_message(
                chat_id=int(seller_id),
                text=f"✏️ Объявление №{ad_id} отправлено на доработку.\n\nКомментарий администратора:\n{text}",
            )
        except Exception as e:
            logger.error(f"admin revision notify error: {e}")
        states[user_id] = {"step": "menu"}
        await update.message.reply_text("✅ Сообщение о доработке отправлено продавцу.")
        return

    if step == "awaiting_first_name":
        states[user_id]["first_name"] = text
        states[user_id]["step"] = "awaiting_last_name"
        await update.message.reply_text("👤 Шаг 2 из 3\nВведите фамилию:")
        return

    if step == "awaiting_last_name":
        states[user_id]["last_name"] = text
        states[user_id]["step"] = "awaiting_phone"
        await update.message.reply_text("📞 Шаг 3 из 3\nВведите номер телефона, например +77001234567:", reply_markup=ReplyKeyboardRemove())
        return

    if step == "awaiting_phone":
        states[user_id]["phone"] = text
        data = states[user_id]
        save_user(user_id, {
            "first_name": data.get("first_name", ""),
            "last_name": data.get("last_name", ""),
            "username": username,
            "phone": text,
            "status": "active",
            "joined": "no",
        })
        states[user_id] = {"step": "awaiting_join"}
        await update.message.reply_text(
            "✅ Регистрация сохранена. Теперь вступите в группу и нажмите проверку.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔓 Вступить в KAZ_METALS", url=GROUP_LINK)],
                [InlineKeyboardButton("✅ Я вступил - проверить", callback_data="check_join")],
            ]),
        )
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🆕 Новый пользователь KAZ_METALS\n{data.get('first_name','')} {data.get('last_name','')}\n@{username}\n📞 {text}\nID: {user_id}",
        )
        return

    if step == "wb_desc":
        states[user_id]["wb_desc"] = text
        states[user_id]["step"] = "wb_budget"
        await update.message.reply_text("💰 Укажите бюджет в тенге:\n<i>Пример: 500000</i>", parse_mode="HTML")
        return

    if step == "wb_budget":
        states[user_id]["wb_budget"] = text.replace(" ", "")
        states[user_id]["step"] = "wb_city"
        await update.message.reply_text("📍 Ваш город?", reply_markup=city_keyboard())
        return

    if step == "wb_city_text":
        states[user_id]["city"] = text
        states[user_id]["step"] = "wb_confirm"
        await show_want_buy_preview(update.message.chat_id, context, user_id)
        return

    if step == "ad_description":
        states[user_id]["description"] = text
        states[user_id]["step"] = "ad_price"
        await update.message.reply_text("💰 Введите цену, например 150000 или договорная:")
        return

    if step == "ad_price":
        states[user_id]["price"] = text
        states[user_id]["step"] = "ad_city"
        await update.message.reply_text("📍 Укажите город продажи:", reply_markup=city_keyboard())
        return

    if step == "ad_city_text":
        states[user_id]["city"] = text
        states[user_id]["step"] = "ad_photo"
        await update.message.reply_text("📸 Отправьте 1 главное фото товара (или напишите -, чтобы пропустить).\n\nЭто фото будет показано в группе. Дополнительные фото и видео можно добавить позже.")
        return

    if step == "ad_photo" and text == "-":
        states[user_id]["photo_id"] = ""
        states[user_id]["extra_media"] = []
        await show_ad_preview(update.message.chat_id, context, user_id)
        return

    if step == "ad_extra_media" and text == "-":
        states[user_id]["step"] = "ad_certificate"
        await update.message.reply_text(
            "📋 Прикрепите фото сертификата подлинности (или напишите -, чтобы пропустить).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить", callback_data="skip_certificate")]]),
        )
        return

    if step == "ad_certificate" and text == "-":
        states[user_id]["cert_file_id"] = ""
        await show_ad_preview(update.message.chat_id, context, user_id)
        return

    if step == "calc_custom_purity":
        try:
            purity = int(text.strip())
            if not 1 <= purity <= 999:
                raise ValueError
            metal = states[user_id].get("calc_metal", "XAU")
            states[user_id]["calc_purity"] = str(purity)
            states[user_id]["step"] = "calc_weight"
            metal_names = {"XAU": "🥇 Золото", "XAG": "🥈 Серебро", "XPT": "💎 Платина", "XPD": "🔩 Палладий"}
            await update.message.reply_text(
                f"🧮 {metal_names.get(metal, metal)} | Проба {purity}\n\nВведите вес в граммах:\n(например: 5 или 12.5)"
            )
        except ValueError:
            await update.message.reply_text("⚠️ Введите пробу числом от 1 до 999. Например: 585")
        return

    if step == "calc_weight":
        try:
            weight = float(text.replace(",", "."))
            metal = states[user_id].get("calc_metal", "XAU")
            purity_str = states[user_id].get("calc_purity", "999")
            purity = int(purity_str)
            metal_names = {
                "XAU": "🥇 Золото",
                "XAG": "🥈 Серебро",
                "XPT": "💎 Платина",
                "XPD": "🔩 Палладий"
            }
            prices, usd_kzt = await get_metal_prices()
            if metal in prices:
                name, price_per_gram, unit = prices[metal]
                # Adjust for purity: price * purity/1000
                adjusted_price = price_per_gram * purity / 1000
                total = round(weight * adjusted_price)
                await update.message.reply_text(
                    f"🧮 Результат:\n\n"
                    f"{metal_names.get(metal, metal)}\n"
                    f"⚖️ Вес: {weight} г\n"
                    f"🔬 Проба: {purity}\n"
                    f"💰 Цена за грамм ({purity}/1000): {round(adjusted_price):,} KZT\n"
                    f"💵 Итого: {total:,} KZT\n\n"
                    f"📅 Курс USD/KZT: {usd_kzt}".replace(",", " "),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Другой вес", callback_data=f"cpurity_{metal}_{purity_str}")],
                        [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "⚠️ Не удалось получить цены. Попробуйте позже.",
                    reply_markup=main_menu_keyboard()
                )
        except ValueError:
            await update.message.reply_text("⚠️ Введите число. Например: 5 или 12.5")
        return

    if step == "offer_price":
        if not text.isdigit():
            await update.message.reply_text("Введите цену только цифрами, например: 600000")
            return
        seller_id = states[user_id].get("seller_id")
        ad_id = states[user_id].get("ad_id", "0")
        buyer = get_user(user_id) or {}
        ad = db_fetchone("SELECT * FROM ads WHERE ad_id = %s", (int(ad_id),)) if str(ad_id).isdigit() else None
        formatted_price = f"{int(text):,}".replace(",", " ")
        deal_id = save_deal(seller_id, user_id, ad_id, status="offer_pending", offer_amount=formatted_price)
        if not deal_id:
            await update.message.reply_text("⚠️ Не удалось отправить предложение. Попробуйте позже.", reply_markup=main_menu_keyboard())
            states[user_id] = {"step": "menu"}
            return

        await context.bot.send_message(
            chat_id=int(seller_id),
            text=(
                f"💼 Новое предложение цены по объявлению #{ad_id}\n"
                f"📦 {(ad or {}).get('description','')[:80]}\n"
                f"💸 Предложение: {formatted_price} KZT\n\n"
                "Контакты покупателя будут открыты только после вашего согласия.\n\n"
                f"Согласны принять это предложение?"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Согласен", callback_data=f"offer_accept_{deal_id}")],
                [InlineKeyboardButton("❌ Отказаться", callback_data=f"offer_reject_{deal_id}")],
            ]),
        )
        states[user_id] = {"step": "menu"}
        await update.message.reply_text(
            "✅ Ваше предложение отправлено продавцу. Если продавец согласится, бот откроет username обеим сторонам.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await update.message.reply_text("Напишите /start для главного меню.")


async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    if states.get(user_id, {}).get("step") == "ad_extra_media":
        states[user_id].setdefault("extra_media", []).append({"type": "video", "file_id": update.message.video.file_id})
        await update.message.reply_text(
            f"✅ Видео добавлено. Всего: {len(states[user_id]['extra_media'])}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово", callback_data="extra_done")],
                [InlineKeyboardButton("⏭ Пропустить", callback_data="extra_skip")],
            ]),
        )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    step = states.get(user_id, {}).get("step")
    if step == "ad_extra_media":
        states[user_id].setdefault("extra_media", []).append({"type": "photo", "file_id": update.message.photo[-1].file_id})
        await update.message.reply_text(
            f"✅ Фото добавлено. Всего: {len(states[user_id]['extra_media'])}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово", callback_data="extra_done")],
                [InlineKeyboardButton("⏭ Пропустить", callback_data="extra_skip")],
            ]),
        )
        return
    if step == "ad_certificate":
        states[user_id]["cert_file_id"] = update.message.photo[-1].file_id
        await update.message.reply_text("✅ Сертификат прикреплён.")
        await show_ad_preview(update.message.chat_id, context, user_id)
        return
    if step == "ad_photo":
        states[user_id]["photo_id"] = update.message.photo[-1].file_id
        states[user_id]["extra_media"] = []
        states[user_id]["step"] = "ad_extra_media"
        await update.message.reply_text(
            "✅ Главное фото сохранено.\n\n📎 Отправьте доп. фото/видео или нажмите «Пропустить».",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово", callback_data="extra_done")],
                [InlineKeyboardButton("⏭ Пропустить", callback_data="extra_skip")],
            ]),
        )


async def send_review_prompt(bot, deal: dict) -> None:
    deal_id = str(deal.get("deal_id"))
    ad_id = str(deal.get("ad_id", ""))
    text = (
        f"⭐ Оценка сделки №{deal_id}\n"
        f"Объявление №{ad_id}\n\n"
        "Сделка завершена? Если да - оцените участника. Если нет - нажмите «Сделка не завершена», и я напомню снова через сутки."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Сделка завершена", callback_data=f"deal_done_{deal_id}")],
        [InlineKeyboardButton("⏳ Сделка не завершена", callback_data=f"deal_notdone_{deal_id}")],
    ])
    if deal.get("seller_id") and not deal.get("seller_reviewed"):
        try:
            await bot.send_message(chat_id=int(deal.get("seller_id")), text=text, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"send_review_prompt seller error: {e}")
    if deal.get("buyer_id") and not deal.get("buyer_reviewed"):
        try:
            await bot.send_message(chat_id=int(deal.get("buyer_id")), text=text, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"send_review_prompt buyer error: {e}")


async def review_prompt_job(context: ContextTypes.DEFAULT_TYPE):
    deal_id = str((context.job.data or {}).get("deal_id", ""))
    deal = db_fetchone("SELECT * FROM deals WHERE deal_id = %s", (int(deal_id),)) if deal_id.isdigit() else None
    if not deal:
        return
    if deal.get("seller_reviewed") and deal.get("buyer_reviewed"):
        return
    await send_review_prompt(context.bot, deal)


async def review_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    deals = db_fetchall("""
        SELECT * FROM deals
        WHERE review_due_at <> ''
          AND review_due_at <= %s
          AND NOT (seller_reviewed = TRUE AND buyer_reviewed = TRUE)
          AND status IN ('contact_opened', 'offer_accepted', 'review_not_completed')
        ORDER BY deal_id DESC
        LIMIT 50
    """, (now_text,))
    for deal in deals:
        await send_review_prompt(context.bot, deal)
        set_deal_review_due(str(deal.get("deal_id")), days=1)


def get_setting(key: str) -> Optional[str]:
    row = db_fetchone("SELECT value FROM settings WHERE key = %s", (key,))
    return row.get("value") if row else None


def set_setting(key: str, value: str) -> None:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"set_setting error: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  НОВОСТИ - полностью переписанный блок
# ═══════════════════════════════════════════════════════════════════════════
import asyncio as _asyncio
import hashlib as _hashlib

# Глобальный Lock - гарантирует что fetch/post не запустятся параллельно
_NEWS_LOCK = _asyncio.Lock()

# In-memory кэш отправленных хэшей - живёт пока процесс не перезапустится
_SENT_HASHES: set = set()


def _title_hash(title: str) -> str:
    import re as _re_h
    t = title.lower().strip()
    t = _re_h.sub(r'\s+', ' ', t)                 # collapse whitespace
    t = t.replace('\u00a0', ' ')                  # non-breaking space
    t = t.replace('\u2013', '-').replace('\u2014', '-')  # en/em-dash
    t = t.replace('\u2026', '...')                # ellipsis
    return _hashlib.md5(t.encode()).hexdigest()


# ── Источники: KZ > США/Европа > Агрегаторы > RU-финансы > RU-СМИ ─────────
KZ_DOMAINS = {
    "nationalbank.kz", "kapital.kz", "kursiv.kz",
    "tengrinews.kz", "inform.kz", "zakon.kz",
    "forbes.kz", "bizmedia.kz", "finprom.kz", "dknews.kz",
    "marieclaire.kz", "elle.kz", "vogue.kz",
}

US_EU_DOMAINS = {
    "bloomberg.com", "reuters.com", "ft.com", "wsj.com",
    "cnbc.com", "kitco.com", "gold-eagle.com", "mining.com",
    "silverseek.com", "bullionstar.com", "goldprice.org",
    "marketwatch.com", "investing.com", "coindesk.com",
}

_WORLD_AGGREGATORS = {"google.news", "yandex.news"}

_RU_FINANCE = {
    "profinance.ru", "1prime.ru", "finmarket.ru",
    "finanz.ru", "gold.ru", "metalinfo.ru",
}


def _source_tier(domain: str) -> int:
    if domain in KZ_DOMAINS:      return 1   # Казахстан
    if domain in US_EU_DOMAINS:   return 2   # США / Европа
    if domain in _WORLD_AGGREGATORS: return 3  # Google/Яндекс News
    if domain in _RU_FINANCE:     return 4   # RU финансы
    return 5                                  # RU СМИ и остальные


# ── RSS-источники: только доверенные источники, без общего Google News ─────
from urllib.parse import quote as _urlquote


def _gnews(query: str, gl: str = "RU") -> str:
    """Google News RSS на русском."""
    return (f"https://news.google.com/rss/search?q={_urlquote(query)}"
            f"&hl=ru&gl={gl}&ceid={gl}:ru")


def _gnews_en(query: str) -> str:
    """Google News RSS на английском — контент переводится автоматически."""
    return (f"https://news.google.com/rss/search?q={_urlquote(query)}"
            f"&hl=en&gl=US&ceid=US:en")


_RSS_FEEDS = [
    # ── Казахстан: прямые ленты ──
    ("https://kapital.kz/rss.xml",   "kapital.kz"),
    ("https://tengrinews.kz/rss/",   "tengrinews.kz"),
    ("https://forbes.kz/rss/",       "forbes.kz"),

    # ── Цены драгметаллов: Kitco + MarketWatch + Profinance ──
    (_gnews_en("site:kitco.com gold silver platinum palladium"),        "google.news.en"),
    (_gnews_en("site:marketwatch.com gold silver precious metals"),     "google.news.en"),
    (_gnews("site:profinance.ru золото серебро платина палладий"),      "google.news"),
    ("https://ru.investing.com/rss/news_14.rss",                       "investing.com"),

    # ── Часы: Hodinkee + WatchPro ──
    (_gnews_en("site:hodinkee.com"),                                    "google.news.en"),
    (_gnews_en("site:watchpro.com"),                                    "google.news.en"),

    # ── Ювелирика и аукционы: JCK + Sotheby's + Christie's ──
    (_gnews_en("site:jckonline.com"),                                   "google.news.en"),
    (_gnews_en("site:sothebys.com jewelry watches diamonds"),           "google.news.en"),
    (_gnews_en("site:christies.com jewelry watches gems"),              "google.news.en"),

    # ── Монеты: CoinWorld + Нацбанк КЗ ──
    (_gnews_en("site:coinworld.com"),                                   "google.news.en"),
    (_gnews("site:nationalbank.kz монеты слитки",           "KZ"),     "google.news"),
]

# Мусорные источники: авто-переводной SEO-спам, не наш рынок.
_JUNK_DOMAINS = {
    # Вьетнамский SEO-спам
    "vietnam.vn", "vietnamnet.vn", "vneconomy.vn", "cafef.vn",
    # Украинские таблоиды/PR-площадки (не релевантны нашей аудитории)
    "obozrevatel.com", "segodnya.ua", "ukranews.com", "glavred.info",
    "politeka.net", "znaj.ua", "ukr.net",
}

# Заголовки-спам (напр. вьетнамский бренд SJC, дежурные «обновления цены»).
_JUNK_TITLE_PHRASES = [
    "sjc", "вьетнам", "обновление цен на золото сегодня",
    "под арестом", "суд оставил", "оставил под стражей",
    "скандального бизнесмена",
    # Корпоративный PR и скучные отраслевые отчёты
    "надёжный партнёр", "надежный партнер",
    "потенциал слияний", "слияний и поглощений",
    "говорят аналитики", "подчёркивают аналитики", "подчеркивают аналитики",
    "технический анализ акций", "инвестиционный рейтинг",
    "геологоразведочные работы", "геологоразведка",
    # Корпоративные операционные новости шахт и рудников (не КЗ)
    "капитальный ремонт шахты", "ремонт шахты",
    "расширение рудника", "строительство рудника",
    "запуск рудника", "ввод рудника",
    "планирует капитальный ремонт",
    "объём добычи за квартал", "объем добычи за квартал",
    "добыча компании выросла", "добыча компании снизилась",
]

# Международные англоязычные источники — заголовки переводятся перед фильтрацией.
# Всё остальное не-русское молча пропускается.
_INTL_SOURCES = {
    "kitco.com", "mining.com", "gold.org",
    "reuters.com", "bloomberg.com", "ft.com",
    "marketwatch.com", "cnbc.com", "investing.com",
    "bullionstar.com", "silverseek.com", "sothebys.com",
    "christies.com", "rolex.com",
    # Часы
    "hodinkee.com", "watchpro.com", "fratellowatches.com", "deployant.com",
    "worldtempus.com", "revolution.watch", "monochrome-watches.com",
    # Камни / ювелирика
    "rapaport.com", "idexonline.com", "jckonline.com", "professionaljeweller.com",
    "jewellerynet.com", "diamonds.net",
    # Медь / металлы
    "lme.com", "fastmarkets.com", "metalbulletin.com", "metalshub.com",
}
# ── Ключевые слова темы ──────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════════════════
#  ФИЛЬТРАЦИЯ НОВОСТЕЙ
#  Базовый фильтр ловит любое упоминание темы.
#  Groq AI решает что реально интересно аудитории.
# ═══════════════════════════════════════════════════════════════════════════

# Все ключевые слова по сегментам группы (AI фильтрует контекст)
_KW_TOPICS = [
    # Металлы - все формы
    "золото", "золот", "золотой", "золотых", "золотые",
    "серебро", "серебр", "серебряный", "серебряных",
    "платина", "платин", "платиновый",
    "палладий", "драгоценн", "драгметалл",
    "слиток", "слитк", "аффинаж",
    # Монеты
    "монета", "монеты", "монет",
    # Лом и скупка
    "лом золота", "лом серебра", "скупка золота", "скупка серебра",
    "скупка драгоценн", "ломбард",
    "ювелир", "кольцо", "кольца", "серьги", "серьга",
    "браслет", "подвеска", "цепочка", "украшени",
    # Камни
    "бриллиант", "алмаз", "рубин", "изумруд", "сапфир",
    "жемчуг", "янтарь", "драгоценный камень",
    # Часы
    "часы", "rolex", "ролекс", "patek", "audemars",
    "hublot", "breguet", "vacheron", "cartier", "richard mille",
    "швейцарские часы", "люксовые часы",
    # Медь и промышленные металлы
    "медь", "меди", "медн", "copper", "никель", "цинк", "алюмин",
    # Казахстан
    "нбрк", "нацбанк казахст",
    # Рыночные термины
    "фьючерс", "xau", "xag", "унци", "спот",
]

# Стоп: явный криминал/провоз - до AI
_KW_STOP = [
    # Криминал - действия
    "контрабанд", "уголовн", "задержан", "мошенничест",
    "изъяли", "конфисков", "таможн",
    "украл", "украли", "украла", "кражу", "кражи",
    "ограбил", "ограбили", "ограблен", "похитил", "похитили",
    "арестован", "осудили", "осуждён",
    # Провоз
    "штанах", "джинсах", "джинсов", "шереметьево",
    "домодедово", "внуково", "в посылке", "в кармане",
    # Спорт
    "рпл", "футбол", "чемпионат", "турнир", "матч",
    "хоккей", "баскетбол", "олимпиад", "медаль",
    "кубок", "лига", "клуб", "спортсмен", "тренер",
]

_STOP_PHRASES = [
    "золотой купол", "железный купол",
    "как инвестировать", "инвестировать или нет", "стоит ли покупать",
    # Умные часы - не наша тема
    "умные часы", "смарт-часы", "smartwatch", "apple watch",
    "huawei watch", "samsung watch", "galaxy watch", "xiaomi watch",
    "фитнес-браслет", "фитнес браслет",
]

import re as _re

def _has_smuggling(title: str) -> bool:
    """Ловит контрабанду/находки: проверяет наличие камня + криминального контекста."""
    tl = title.lower()
    gems = ["бриллиант", "алмаз", "сапфир", "рубин", "изумруд", "золото", "серебро"]
    crime = ["нашли", "нашла", "нашел", "обнаружил", "обнаружили",
             "изъяли", "конфисков", "задержали", "провез", "вывез",
             "штан", "джинс", "карман", "посылк", "багаж", "аэропорт"]
    has_gem = any(g in tl for g in gems)
    has_crime = any(c in tl for c in crime)
    return has_gem and has_crime

_STOP_PATTERNS = [
    _re.compile(r'пытал\w+\s+(вывезти|провезти|перевезти)', _re.I),
    _re.compile(r'\(фото\)|\(видео\)|\(фотогалерея\)', _re.I),
]


def _is_relevant(title: str) -> bool:
    """Базовый фильтр: есть ли тема группы + нет ли криминала/спорта."""
    tl = title.lower()
    if _has_smuggling(title):
        return False
    for pat in _STOP_PATTERNS:
        if pat.search(title):
            return False
    if any(sw in tl for sw in _KW_STOP):
        return False
    if any(ph in tl for ph in _STOP_PHRASES):
        return False
    if any(j in tl for j in _JUNK_TITLE_PHRASES):
        return False
    return any(kw in tl for kw in _KW_TOPICS)






def _score(title: str) -> int:
    t = title.lower()
    s = 0
    # Казахстан - наивысший приоритет
    if any(w in t for w in _KW_KZ):                             s += 130
    # США и Европа - второй приоритет
    if any(w in t for w in ["сша", "вашингтон", "федрезерв", "фрс",
                              "европа", "лондон", "евросоюз", "китай",
                              "нью-йорк", "уолл-стрит"]):        s += 70
    # Рекорды / движение цен
    if any(w in t for w in ["рекорд", "максимум", "минимум",
                              "впервые", "исторически", "обвал",
                              "рост", "падение", "скачок"]):      s += 80
    # Цены и рынок
    if any(w in t for w in ["цена", "курс", "котировк",
                              "стоимость", "торги"]):             s += 50
    # Инвестиции и резервы
    if any(w in t for w in ["инвестиц", "резервы", "запасы",
                              "биржа", "рынок"]):                 s += 30
    return s

# ── Ключевые слова KZ (для определения «казахстанская» ли новость) ────────
_KW_KZ = ["казахстан", "казахск", "нбрк", "астана", "алматы", "тенге"]


def _is_russian(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if '\u0400' <= c <= '\u04ff') / len(letters) >= 0.5


def _is_recent(pub_date_str: str, days: int = 3) -> bool:
    try:
        dt = email.utils.parsedate_to_datetime(pub_date_str)
        return (datetime.now(timezone.utc) - dt) <= timedelta(days=days)
    except Exception:
        return True  # не можем распарсить > берём


def _parse_item(item, feed_domain: str) -> tuple:
    """
    Разбирает RSS-элемент. Для Google/Yandex News:
    - извлекает реальный источник из тега <source>
    - очищает заголовок от суффикса "- Источник"
    Возвращает (clean_title, url, display_domain).
    """
    from urllib.parse import urlparse

    raw_title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    if not link.startswith("http"):
        guid_el = item.find("guid")
        if guid_el is not None:
            g = (guid_el.text or "").strip()
            if g.startswith("http"):
                link = g

    # Для Google News и Yandex News - получаем реальный источник
    real_domain = feed_domain
    source_name = ""
    if feed_domain in ("google.news", "google.news.en", "yandex.news"):
        source_el = item.find("source")
        if source_el is not None:
            source_name = (source_el.text or "").strip()
            src_url = source_el.get("url", "").strip()
            if src_url.startswith("http"):
                d = urlparse(src_url).netloc.lower().lstrip("www.")
                if d:
                    real_domain = d
            elif source_name:
                # Нет URL - используем имя источника как домен
                real_domain = source_name.lower().replace(" ", "")

    # Очищаем заголовок от суффикса "- Источник" (Google News добавляет его)
    clean_title = raw_title
    if source_name and raw_title.endswith(f" - {source_name}"):
        clean_title = raw_title[: -len(f" - {source_name}")].strip()
    elif feed_domain in ("google.news", "google.news.en", "yandex.news") and " - " in raw_title:
        # Обрезаем последний суффикс если он короткий (= имя источника)
        parts = raw_title.rsplit(" - ", 1)
        if len(parts) == 2 and len(parts[1]) <= 40:
            clean_title = parts[0].strip()

    # Описание из RSS - чистим HTML, берём первые 180 символов
    import html as _html
    raw_desc = (item.findtext("description") or "").strip()
    clean_desc = _re.sub(r'<[^>]+>', '', raw_desc)
    clean_desc = _html.unescape(clean_desc).strip()
    if len(clean_desc) > 180:
        cut = clean_desc[:180].rfind('.')
        clean_desc = clean_desc[:cut + 1] if cut > 80 else clean_desc[:180]
    # Убираем если описание совпадает с заголовком
    if clean_desc.startswith(clean_title[:40]):
        clean_desc = ""

    return clean_title, clean_desc, link, real_domain



import re as _re

_STOP_WORDS_SIM = {
    "что","это","как","для","при","будет","после","также","свои",
    "этом","млрд","млн","году","года","рублей","более","менее",
    "впервые","впервы","котор","своих","своим","этого","таким",
}

_METAL_KEYS = {"золот","серебр","платин","палладий","драгмет","драгоцен"}
_PRICE_RE = _re.compile(r'[\$₽€£]?\s*(\d[\d\s,.]+)\s*(доллар|руб|тенге|тыс|млн)?')


def _normalize(title: str) -> str:
    """Нормализует заголовок: заменяет цены на токен, убирает (фото)/(видео)."""
    t = title.lower()
    t = _re.sub(r'\(фото\)|\(видео\)|\(фотогалерея\)|\(фоторепортаж\)', '', t)
    # Нормализуем ценовые выражения > PRICE + число (убираем пробелы/запятые)
    def _norm_price(m):
        num = _re.sub(r'[\s,]', '', m.group(1))
        return f'PRICE{num}'
    t = _PRICE_RE.sub(_norm_price, t)
    return t.strip()


def _stem(word: str) -> str:
    """Простой стемминг - первые 5 символов слова."""
    return word[:5] if len(word) > 5 else word


def _words(title: str) -> set:
    normalized = _normalize(title)
    tokens = _re.findall(r'[а-яёa-z]{4,}|PRICE\d+', normalized)
    return {_stem(w) for w in tokens if w not in _STOP_WORDS_SIM}


def _same_topic(t1: str, t2: str) -> bool:
    """
    Определяет одну ли это новость. Два уровня проверки:
    1. Оба упоминают один металл + одну цену > дубль
    2. Jaccard схожесть стемов > 0.28 > дубль
    """
    w1, w2 = _words(t1), _words(t2)
    if not w1 or not w2:
        return False

    # Уровень 1: одинаковая цена + один металл
    prices1 = {w for w in w1 if w.startswith("PRICE")}
    prices2 = {w for w in w2 if w.startswith("PRICE")}
    metals1  = {w for w in w1 if any(w.startswith(m[:5]) for m in _METAL_KEYS)}
    metals2  = {w for w in w2 if any(w.startswith(m[:5]) for m in _METAL_KEYS)}
    if prices1 & prices2 and metals1 & metals2:
        return True

    # Уровень 2: Jaccard на стемах
    return len(w1 & w2) / len(w1 | w2) >= 0.22


# Слова ценового падения и роста (для детектора "одно рыночное событие")
_TREND_FALL = {
    "упал","упала","упало","упали","подешевел","подешевела","подешевело","подешевели",
    "снизился","снизилась","снизилось","снизились","опустился","опустилась","опустило",
    "падение","падает","обвал","обвалил","коррекция","рекордно низк",
}
_TREND_RISE = {
    "вырос","выросла","выросло","выросли","подорожал","подорожала","подорожало","подорожали",
    "поднялся","поднялась","поднялось","поднялись","рост","растет","растёт",
    "рекорд","максимум","обновил максимум","исторический",
}
_METAL_ROOTS = ["золот","серебр","платин","палладий"]


def _same_price_trend(t1: str, t2: str) -> bool:
    """Два заголовка об ОДНОМ рыночном событии — даже с разными словами.
    Пример: «золото и серебро подешевели» + «цена золота опустилась» → True.
    Условие: общий металл И одинаковое направление движения цены.
    """
    l1, l2 = t1.lower(), t2.lower()
    if not any(m in l1 and m in l2 for m in _METAL_ROOTS):
        return False
    fall1 = any(w in l1 for w in _TREND_FALL)
    fall2 = any(w in l2 for w in _TREND_FALL)
    rise1 = any(w in l1 for w in _TREND_RISE)
    rise2 = any(w in l2 for w in _TREND_RISE)
    return (fall1 and fall2) or (rise1 and rise2)


def _emoji(title: str, idx: int) -> str:
    t = title.lower()
    pools = {
        "gold":    ["🥇","🌕","✨","💛","🏆","🔆"],
        "silver":  ["🥈","🌙","⚪","🔘"],
        "platinum":["💎","🔵","🪩"],
        "palladium":["🔩","⚙️"],
        "gems":    ["💎","🔮","✴️","🌈","🪩"],
        "jewelry": ["💍","👑","💫","🌸","🎀"],
        "watch":   ["⌚","🕰","⏱"],
        "bullion": ["🏅","📦"],
        "coin":    ["🪙","💰","🎖"],
        "scrap":   ["♻️","🔄"],
        "kz":      ["🇰🇿","🌿","🏔"],
        "price":   ["📈","📉","💹","📊","💵"],
        "default": ["📰","🌐","💡","⚡","🎯","🔑"],
    }
    if any(w in t for w in ["золото","золот"]):          cat = "gold"
    elif any(w in t for w in ["серебро","серебр"]):       cat = "silver"
    elif "платин" in t:                                   cat = "platinum"
    elif "палладий" in t:                                 cat = "palladium"
    elif any(w in t for w in ["бриллиант","алмаз","рубин",
                               "изумруд","сапфир","жемчуг","янтарь"]): cat = "gems"
    elif any(w in t for w in ["ювелир","кольц","серьг",
                               "брасле","украшен","цепочк"]): cat = "jewelry"
    elif any(w in t for w in ["часы","rolex","ролекс","patek","audemars",
                               "vacheron","cartier","breguet","hublot",
                               "richard mille","jaeger"]): cat = "watch"
    elif any(w in t for w in ["слиток","аффинаж"]):      cat = "bullion"
    elif "монет" in t:                                    cat = "coin"
    elif any(w in t for w in ["лом","скупка"]):           cat = "scrap"
    elif any(w in t for w in _KW_KZ):                    cat = "kz"
    elif any(w in t for w in ["цена","курс","биржа","рынок"]): cat = "price"
    else:                                                 cat = "default"
    p = pools[cat]
    return p[idx % len(p)]


def _art_category(title: str) -> str:
    """Канонический сегмент новости (для квот разнообразия и подписи карточки).

    ВАЖЕН ПОРЯДОК: украшения/часы/камни/монеты/слитки/лом проверяются ДО
    золота, чтобы «золотое кольцо» попало в украшения, а «золотая монета» —
    в монеты, иначе всё с корнем «золот» схлопывается в одну рубрику.
    """
    t = title.lower()

    # Если в заголовке упоминаются 3+ разных металла/инструмента —
    # это общерыночная сводка, а не новость конкретного сегмента.
    _multi = sum([
        any(w in t for w in ["золот", "xau"]),
        any(w in t for w in ["серебр", "xag"]),
        any(w in t for w in ["платин"]),
        any(w in t for w in ["палладий"]),
        any(w in t for w in ["медь", "меди", "медн"]),
        any(w in t for w in ["нефть", "природный газ"]),
        any(w in t for w in ["никель", "цинк", "алюмин"]),
    ])
    if _multi >= 3:
        return "market"

    # 1) Названное ювелирное изделие — всегда украшения (даже «кольцо с бриллиантом»
    #    или «кольцо Cartier»: изделие важнее бренда/камня).
    if any(w in t for w in ["кольц", "серьг", "серёг", "брасле", "подвеск",
                            "колье", "цепочк"]):
        return "jewelry"
    # 2) Часы: слово «часы» + однозначно часовые бренды (Cartier убран — он делает
    #    и часы, и украшения, поэтому решает наличие слова «часы» либо изделия).
    if any(w in t for w in ["часы", "rolex", "ролекс", "patek", "audemars",
                            "hublot", "vacheron", "breguet", "jaeger", "omega",
                            "richard mille", "швейцарские часы"]):
        return "watch"
    # 3) Ювелирная тема в целом / ювелирные дома.
    if any(w in t for w in ["ювелир", "украшен", "tiffany", "bvlgari", "van cleef"]):
        return "jewelry"
    # 4) Свободные драгоценные камни.
    if any(w in t for w in ["бриллиант", "алмаз", "рубин", "изумруд", "сапфир",
                            "жемчуг", "янтарь", "самоцвет", "драгоценный камень"]):
        return "gems"
    if "монет" in t:
        return "coins"
    if any(w in t for w in ["слиток", "слитк", "аффинаж"]):
        return "bullion"
    if any(w in t for w in ["лом золота", "лом серебра", "скупка", "ломбард",
                            "переработк"]):
        return "scrap"
    if any(w in t for w in ["медь", "меди", "медн", "copper"]):
        return "copper"
    if any(w in t for w in ["никель", "цинк", "алюмин"]):
        return "metals"
    if any(w in t for w in ["серебро", "серебр"]):
        return "silver"
    if "платин" in t:
        return "platinum"
    if "палладий" in t:
        return "palladium"
    if any(w in t for w in ["золото", "золот"]):
        return "gold"
    return "market"


def _get_published_titles() -> list:
    try:
        rows = db_fetchall(
            "SELECT title FROM news_published "
            "WHERE published_at > NOW() - INTERVAL '24 hours'"
        )
        return [r["title"] for r in rows if r.get("title")]
    except Exception as e:
        logger.error(f"_get_published_titles: {e}")
        return []


def _save_published(title: str, domain: str) -> None:
    h = _title_hash(title)
    _SENT_HASHES.add(h)
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO news_published (title_hash, title, domain) "
            "VALUES (%s,%s,%s) ON CONFLICT (title_hash) DO NOTHING",
            (h, title, domain),
        )
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        logger.error(f"_save_published: {e}")


def _claim_for_posting(title: str, domain: str) -> bool:
    """Атомарно резервирует новость через UNIQUE-индекс БД.

    True  — эта реплика первой застолбила новость, можно постить;
    False — новость уже застолблена другой репликой/прогоном, пропускаем.

    Главная защита от дублей между ДВУМЯ репликами Railway: даже если оба
    процесса одновременно решат запостить одну статью, успешно вставит строку
    только один — у второго сработает ON CONFLICT и RETURNING вернёт пусто.
    """
    h = _title_hash(title)
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO news_published (title_hash, title, domain) "
            "VALUES (%s,%s,%s) ON CONFLICT (title_hash) DO NOTHING "
            "RETURNING title_hash",
            (h, title, domain),
        )
        row = cur.fetchone()
        conn.commit(); cur.close(); conn.close()
        if row:
            _SENT_HASHES.add(h)
            return True
        _SENT_HASHES.add(h)
        return False
    except Exception as e:
        logger.error(f"_claim_for_posting: {e}")
        return True   # при сбое БД не блокируем публикацию


async def _translate_to_ru(session, text: str) -> str:
    """Переводит заголовок на русский если он не на русском."""
    if _is_russian(text):
        return text
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx", "sl": "auto", "tl": "ru",
            "dt": "t", "q": text,
        }
        async with session.get(
            url, params=params,
            timeout=aiohttp.ClientTimeout(total=5),
            headers={"User-Agent": "Mozilla/5.0"},
        ) as r:
            if r.status == 200:
                data = await r.json()
                translated = data[0][0][0]
                return translated if translated else text
    except Exception:
        pass
    return text


async def _collect_raw() -> list:
    """Парсит все RSS, переводит заголовки на русский, фильтрует."""
    raw = []
    seen: set = set()
    async with aiohttp.ClientSession() as session:
        for feed_url, feed_domain in _RSS_FEEDS:
            try:
                async with session.get(
                    feed_url,
                    timeout=aiohttp.ClientTimeout(total=8),
                    headers={"User-Agent": "Mozilla/5.0 (compatible; KAZ_METALSBot/1.0)"},
                ) as r:
                    if r.status != 200:
                        logger.warning(f"RSS {feed_domain}: HTTP {r.status}")
                        continue
                    content = await r.text(errors="replace")
                root = ET.fromstring(content)
                count_before = len(raw)
                for item in root.findall(".//item"):
                    pub_date = (item.findtext("pubDate") or "").strip()
                    if not _is_recent(pub_date, days=7):
                        continue

                    title, desc, link, real_domain = _parse_item(item, feed_domain)
                    if not title or not link:
                        continue

                    # Нерусский заголовок: переводим если:
                    # (а) лента помечена как английская (google.news.en), или
                    # (б) реальный источник — известный международный сайт.
                    # Во всех остальных случаях пропускаем.
                    if not _is_russian(title):
                        is_en_feed = (feed_domain == "google.news.en"
                                      or feed_domain in ("kitco.com", "mining.com"))
                        if is_en_feed or real_domain in _INTL_SOURCES:
                            title = await _translate_to_ru(session, title)
                            if desc and not _is_russian(desc):
                                desc = await _translate_to_ru(session, desc)
                            if not _is_russian(title):   # перевод не сработал
                                continue
                        else:
                            continue

                    if not _is_relevant(title):
                        continue

                    if real_domain in _JUNK_DOMAINS:
                        continue

                    h = _title_hash(title)
                    if h in seen or h in _SENT_HASHES:
                        continue
                    if any(_same_topic(title, r["title"]) or
                               _same_price_trend(title, r["title"])
                               for r in raw[-60:]):
                        continue
                    seen.add(h)
                    raw.append({
                        "title":  title,
                        "desc":   desc,
                        "url":    link,
                        "domain": real_domain,
                        "hash":   h,
                        "tier":   _source_tier(real_domain),   # реальный источник точнее
                        "score":  _score(title),
                    })
                logger.info(f"RSS {feed_domain}: +{len(raw)-count_before} статей")
            except ET.ParseError:
                logger.warning(f"RSS {feed_domain}: ошибка парсинга XML")
                continue
            except Exception as e:
                logger.error(f"RSS {feed_domain}: {e}")
    return raw


async def news_debug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /news_debug - показывает статус каждого RSS источника."""
    if update.message.chat.type != "private":
        return
    if not is_admin(str(update.message.from_user.id)):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    await update.message.reply_text("⏳ Проверяю каждый источник...")

    results = []
    async with aiohttp.ClientSession() as session:
        for feed_url, domain in _RSS_FEEDS:
            try:
                async with session.get(
                    feed_url,
                    timeout=aiohttp.ClientTimeout(total=8),
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as r:
                    if r.status != 200:
                        results.append(f"❌ {domain}: HTTP {r.status}")
                        continue
                    content = await r.text(errors="replace")
                    root = ET.fromstring(content)
                    items = root.findall(".//item")
                    # Считаем сколько прошло базовый фильтр
                    passed = 0
                    for item in items[:20]:
                        t = (item.findtext("title") or "").strip()
                        if _is_relevant(t):
                            passed += 1
                    results.append(f"✅ {domain}: {len(items)} статей, {passed} по теме")
            except ET.ParseError:
                results.append(f"⚠️ {domain}: ошибка XML")
            except Exception as e:
                results.append(f"❌ {domain}: {type(e).__name__}: {str(e)[:50]}")

    report = "📊 Статус источников:\n\n" + "\n".join(results)
    await update.message.reply_text(report)


def _deduplicate(raw: list, recent_titles: Optional[list] = None) -> list:
    """Чистит кандидатов ПЕРЕД Groq:
    - точные хэш-дубли в выборке;
    - СМЫСЛОВЫЕ дубли (_same_topic) внутри выборки;
    - СМЫСЛОВЫЕ дубли против уже опубликованного (recent_titles) — это и
      убирает повторы вроде «золото упало» / «золото резко упало»;
    - сортирует Казахстан выше, потом по важности;
    - размечает категорию (cat) каждой новости для балансировки.
    """
    recent_titles = recent_titles or []
    seen_hashes: set = set()
    accepted: list = []

    for art in raw:
        if art["hash"] in seen_hashes:
            continue
        if any(_same_topic(art["title"], a["title"]) or
               _same_price_trend(art["title"], a["title"])
               for a in accepted):
            continue
        if any(_same_topic(art["title"], t) or
               _same_price_trend(art["title"], t)
               for t in recent_titles):
            continue
        seen_hashes.add(art["hash"])
        art["cat"] = _art_category(art["title"])
        accepted.append(art)

    accepted.sort(key=lambda x: (x["tier"], -x["score"]))
    for i, a in enumerate(accepted):
        a["emoji"] = _emoji(a["title"], i)

    return accepted[:30]  # Groq получает максимум 30


# ── Кулдаун по теме: одна новость про одну тему раз в 2 часа ────────────
_TOPIC_COOLDOWN: dict = {}
_COOLDOWN_HOURS = 2


def _get_topic_key(title: str) -> str:
    return _art_category(title)


def _topic_on_cooldown(title: str) -> bool:
    key = _get_topic_key(title)
    if key == "other":
        return False
    last = _TOPIC_COOLDOWN.get(key)
    if last is None:
        return False
    return (datetime.now(timezone.utc) - last).total_seconds() < _COOLDOWN_HOURS * 3600


def _mark_topic(title: str) -> None:
    _TOPIC_COOLDOWN[_get_topic_key(title)] = datetime.now(timezone.utc)


# ── Закреплённые цены ────────────────────────────────────────────────────
_PRICES_PIN_KEY = "prices_pin_msg_id"


async def _fetch_prices_for_pin() -> dict:
    """Получает цены металлов через metals.live (бесплатно, без ключа)."""
    result = {}
    labels = {
        "gold":      "🥇 Золото (XAU/USD)",
        "silver":    "🥈 Серебро (XAG/USD)",
        "platinum":  "💎 Платина (XPT/USD)",
        "palladium": "🔩 Палладий (XPD/USD)",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.metals.live/v1/spot",
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"User-Agent": "Mozilla/5.0"},
            ) as r:
                if r.status != 200:
                    logger.error(f"metals.live HTTP {r.status}")
                    return {}
                data = await r.json()

        # metals.live возвращает список [{"gold": X, "silver": Y, ...}]
        # или словарь напрямую
        if isinstance(data, list):
            prices = data[0] if data else {}
        else:
            prices = data

        for key, label in labels.items():
            val = prices.get(key)
            if val:
                result[label] = f"${float(val):,.2f}"
                logger.info(f"{label}: ${float(val):.2f}")

    except Exception as e:
        logger.error(f"_fetch_prices_for_pin: {e}")
    return result


async def post_pinned_prices(bot) -> str:
    """Публикует цены из LBMA+НБРК и закрепляет в вкладке Новости."""
    if not NEWS_TOPIC_ID or not GROUP_ID:
        return "❌ NEWS_TOPIC_ID или GROUP_ID не заданы"

    try:
        prices, usd_kzt = await get_metal_prices()
    except Exception as e:
        return f"❌ Ошибка получения цен: {e}"

    if not prices:
        return "❌ Не удалось получить цены (LBMA/НБРК недоступны)"

    # Тот же формат что показывает бот в личке
    text = f"📊 Актуальные цены на металлы\n💵 USD/KZT: {usd_kzt}\n\n"
    for _, (name, price, unit) in prices.items():
        text += f"{name}: {price:,} KZT/{unit}\n".replace(",", " ")
    text += "\nИсточник: LBMA + курс НБРК."

    # Удаляем старое закреплённое сообщение
    old_id = get_setting(_PRICES_PIN_KEY)
    if old_id:
        try:
            await bot.delete_message(chat_id=GROUP_ID, message_id=int(old_id))
        except Exception as e:
            logger.warning(f"Не удалось удалить старое: {e}")

    # Отправляем новое
    try:
        msg = await bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=NEWS_TOPIC_ID,
            text=text,
        )
    except Exception as e:
        return f"❌ Ошибка отправки: {e}"

    # Закрепляем
    try:
        await bot.pin_chat_message(
            chat_id=GROUP_ID,
            message_id=msg.message_id,
            disable_notification=True,
        )
        set_setting(_PRICES_PIN_KEY, str(msg.message_id))
        return f"✅ Закреплено ({len(prices)} металлов, USD/KZT: {usd_kzt})"
    except Exception as e:
        set_setting(_PRICES_PIN_KEY, str(msg.message_id))
        return f"⚠️ Отправлено но не закреплено: {e}"


async def pinned_prices_job(context: ContextTypes.DEFAULT_TYPE):
    result = await post_pinned_prices(context.bot)
    logger.info(f"pinned_prices_job: {result}")


async def pin_prices_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    if not is_admin(str(update.message.from_user.id)):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    await update.message.reply_text("⏳ Получаю цены с Yahoo Finance...")
    result = await post_pinned_prices(context.bot)
    await update.message.reply_text(result)


async def _groq_filter(articles: list) -> list:
    """
    Groq фильтрует + убирает дубли. Если Groq недоступен или
    блокирует всё - возвращает топ-5 из базового фильтра.
    """
    if not articles:
        return []

    if not GROQ_API_KEY:
        logger.info("GROQ_API_KEY не задан - берём топ-5 напрямую")
        return articles[:5]

    titles_text = "\n".join(
        f"{i+1}. [{a['domain']}] {a['title']}"
        for i, a in enumerate(articles)
    )

    prompt = (
        "Ты — редактор Telegram-канала о драгоценных металлах для розничных инвесторов Казахстана.\n\n"
        "ПОРТРЕТ ЧИТАТЕЛЯ: покупает монеты, слитки, ювелирные украшения и часы.\n"
        "Следит за рынком чтобы понять когда покупать и продавать.\n"
        "Спроси себя: 'Захочет ли обычный человек это прочитать за утренним кофе?'\n\n"
        "✅ БРАТЬ — интересно читателю:\n"
        "• Прогнозы и ожидания: 'золото может вырасти до...', 'эксперты ждут...'\n"
        "• Рекорды: 'бриллиант продан за $50 млн', 'Rolex побил ценовой рекорд на аукционе'\n"
        "• Решения центробанков и НБК о золоте и резервах\n"
        "• Новые коллекции известных брендов (Cartier, Tiffany, Rolex, Patek Philippe...)\n"
        "• Новости Казахстана: НБРК, добыча в КЗ, казахстанские ювелирные тренды\n"
        "• Удивительные факты рынка, необычные события\n\n"
        "❌ НЕ БРАТЬ — скучно, не для обывателя:\n"
        "• Корпоративные новости горнодобывающих компаний НЕ из Казахстана\n"
        "  (слияния, поглощения, аналитика акций конкретной компании, месторождения)\n"
        "• Рекламные и PR-статьи: 'компания X — надёжный партнёр', 'компания X подчёркивает'\n"
        "• Новости конкретной страны (Украина, Австралия, Канада...) если не мировой тренд\n"
        "• Технические отчёты о геологоразведке за пределами Казахстана\n"
        "• Дублирующиеся новости об одном событии — оставить только лучшую\n"
        "• Одно ценовое движение = одна новость ('золото упало' + 'серебро упало' = одна)\n\n"
        "ПРАВИЛА ОТБОРА:\n"
        "• 5-7 заголовков, максимум 2 про цену золота\n"
        "• Разнообразие рубрик: золото, серебро, украшения, часы, камни, монеты, медь...\n"
        "• Казахстан > весь мир > Россия\n"
        "• Убрать: кражи, контрабанду, спорт, политику\n\n"
        f"Заголовки:\n{titles_text}\n\n"
        "Ответь ТОЛЬКО JSON-массивом номеров, например: [2,4,5,9,11]. Только JSON."
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0.1,
                },
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=25),
            ) as r:
                if r.status != 200:
                    logger.error(f"Groq HTTP {r.status} - fallback топ-5")
                    return articles[:5]
                data = await r.json()
                text = data["choices"][0]["message"]["content"].strip()
                import json as _json, re as _re3
                m = _re3.search(r'\[[\d,\s]*\]', text)
                if m:
                    approved = _json.loads(m.group())
                    if not approved:
                        # Groq вернул [] - fallback топ-5
                        logger.warning("Groq вернул [] - fallback топ-5")
                        return articles[:5]
                    seen_g: set = set()
                    result = []
                    for i in approved:
                        if 1 <= i <= len(articles) and i not in seen_g:
                            seen_g.add(i)
                            result.append(articles[i-1])
                    logger.info(f"Groq: {len(articles)} -> {len(result)}")
                    return result
    except Exception as e:
        logger.error(f"Groq error: {e} - fallback топ-5")

    return articles[:5]


# ── Подписи рубрик для карточек новостей ────────────────────────────────────
_CAT_LABELS = {
    "gold":      ("🥇", "Золото"),
    "silver":    ("🥈", "Серебро"),
    "platinum":  ("💎", "Платина"),
    "palladium": ("🔩", "Палладий"),
    "jewelry":   ("💍", "Украшения"),
    "watch":     ("⌚", "Часы"),
    "gems":      ("💎", "Камни"),
    "coins":     ("🪙", "Монеты"),
    "bullion":   ("🏅", "Слитки"),
    "scrap":     ("♻️", "Лом и скупка"),
    "market":    ("📊", "Рынок металлов"),
    "copper":    ("🟤", "Медь"),
    "metals":    ("⚙️",  "Металлы"),
}

# Цветной маркер рубрики (вариант Г) — заменяет цвет текста, которого нет в Telegram
_CAT_COLOR = {
    "gold":      "🟡",
    "silver":    "🔘",
    "platinum":  "🔵",
    "palladium": "🔩",
    "jewelry":   "🟢",
    "watch":     "🔵",
    "gems":      "🟣",
    "coins":     "🟠",
    "bullion":   "🟡",
    "scrap":     "♻️",
    "market":    "⚪",
}

import html as _html_mod


def _esc(s: str) -> str:
    """Экранирует текст для parse_mode=HTML (заголовки могут содержать & < >)."""
    return _html_mod.escape(s or "", quote=False)


def _clean_context(desc: str, title: str) -> str:
    """Готовит короткую строку-контекст из RSS-описания.
    Возвращает '' если описание мусорное/нерусское/повторяет заголовок —
    лучше без подзаголовка, чем с кривым.
    """
    d = (desc or "").strip()
    if len(d) < 25 or not _is_russian(d):
        return ""
    if d[:30].lower() == (title or "")[:30].lower():
        return ""
    d = _re.split(r"(?<=[.!?])\s", d)[0].strip()   # первое предложение
    if len(d) > 160:
        d = d[:157].rstrip() + "…"
    return d


def _load_published_into_cache(hours: int = 168) -> list:
    """Подтягивает уже опубликованные заголовки из БД в _SENT_HASHES.

    Это ключевая защита от повторов ПОСЛЕ перезапуска: раньше кэш жил только
    в памяти и обнулялся при рестарте, из-за чего бот заново постил всё, что
    ещё висело в RSS. Возвращает недавние заголовки для смысловой дедупликации.
    """
    titles: list = []
    try:
        rows = db_fetchall(
            f"SELECT title FROM news_published "
            f"WHERE published_at > NOW() - INTERVAL '{int(hours)} hours'"
        )
        for r in rows:
            t = r.get("title")
            if t:
                titles.append(t)
                _SENT_HASHES.add(_title_hash(t))
    except Exception as e:
        logger.error(f"_load_published_into_cache: {e}")
    return titles


def _balance_categories(articles: list, max_total: int = 5, gold_cap: int = 2) -> list:
    """Делает ленту разнообразной.

    Сначала берём по одной новости из каждой НЕ-золотой рубрики (максимум
    разнообразия), потом добиваем по второй на рубрику, и только в конце —
    золото, но не больше gold_cap. Так лента перестаёт быть «только золото».
    """
    if not articles:
        return []
    non_gold = [a for a in articles if a.get("cat") != "gold"]
    gold     = [a for a in articles if a.get("cat") == "gold"]

    result: list = []
    taken: set = set()
    used: dict = {}

    def add(a):
        result.append(a)
        taken.add(a["hash"])
        c = a.get("cat", "market")
        used[c] = used.get(c, 0) + 1

    for a in non_gold:                                  # проход 1: разнообразие
        if used.get(a.get("cat", "market"), 0) == 0:
            add(a)
            if len(result) >= max_total:
                return result
    for a in non_gold:                                  # проход 2: добор
        if a["hash"] in taken:
            continue
        if used.get(a.get("cat", "market"), 0) < 2:
            add(a)
            if len(result) >= max_total:
                return result
    for a in gold[:gold_cap]:                           # проход 3: золото, ≤ cap
        add(a)
        if len(result) >= max_total:
            break
    return result[:max_total]


_NEWS_RUNNING: bool = False


async def _do_fetch_and_post(bot, max_total: int = 5) -> list:
    """Защита от параллельного запуска задачи."""
    global _NEWS_RUNNING
    if _NEWS_RUNNING:
        logger.warning("news: предыдущий прогон ещё идёт, пропускаем")
        return []
    _NEWS_RUNNING = True
    try:
        return await _do_fetch_and_post_inner(bot, max_total=max_total)
    finally:
        _NEWS_RUNNING = False


async def _do_fetch_and_post_inner(bot, max_total: int = 5) -> list:
    """Сбор → дедуп → Groq → баланс → пост."""
    recent_titles = _load_published_into_cache(hours=168)
    raw = await _collect_raw()
    logger.info(f"news: {len(raw)} кандидатов")

    if not raw or not NEWS_TOPIC_ID:
        return []

    articles = _deduplicate(raw, recent_titles)
    logger.info(f"news: после dedup {len(articles)}")
    if not articles:
        return []

    articles = await _groq_filter(articles)
    logger.info(f"news: после Groq {len(articles)}")
    if not articles:
        return []

    articles = _balance_categories(articles, max_total=max_total, gold_cap=max(2, max_total//4))
    logger.info(f"news: после баланса {len(articles)} "
                f"({', '.join(a.get('cat', '?') for a in articles)})")
    if not articles:
        return []

    posted = []
    months_ru = {
        1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "мая", 6: "июн",
        7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек",
    }
    # Финальный защитный фильтр: страхует от дублей,
    # которые просочились сквозь предыдущие слои дедупликации
    # (разные хэши из-за пробела/тире, но один смысл).
    _batch_hashes: set = set()
    _batch_titles: list = []

    for art in articles:
        if art["hash"] in _batch_hashes:
            logger.info(f"batch dedup (hash): {art['title'][:50]}")
            continue
        if any(_same_topic(art["title"], t) for t in _batch_titles):
            logger.info(f"batch dedup (topic): {art['title'][:50]}")
            continue
        _batch_hashes.add(art["hash"])
        _batch_titles.append(art["title"])

        now = datetime.now()
        date_str = f"{now.day} {months_ru[now.month]}, {now.strftime('%H:%M')}"
        _, cat_label = _CAT_LABELS.get(art.get("cat", "market"), ("📊", "Рынок металлов"))

        # Вариант Г: компактный маркет-вайр
        text = "\n".join([
            f"◆ <b>{cat_label.upper()}</b>  ·  {date_str}",
            "",
            f"{_esc(art['title'])}",
            "",
            f"↗ <a href=\"{art['url']}\">{_esc(art['domain'])}</a>",
        ])

        # Атомарная заявка на публикацию — защита от дублей между репликами
        if not _claim_for_posting(art["title"], art["domain"]):
            logger.info(f"уже застолблено другой репликой: {art['title'][:50]}")
            continue

        try:
            await bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=NEWS_TOPIC_ID,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            _SENT_HASHES.add(art["hash"])
            posted.append(art)
        except Exception as e:
            logger.error(f"send news: {e}")

    return posted



# ═══════════════════════════════════════════════════════════════════════════
#  ЦЕНОВЫЕ АЛЕРТЫ - резкое изменение цены металла
# ═══════════════════════════════════════════════════════════════════════════

# Металлы: (Yahoo Finance тикер, отображаемое имя, emoji роста, emoji падения)
_ALERT_METALS = [
    ("XAUUSD=X", "Золото",    "📈", "📉"),
    ("XAGUSD=X", "Серебро",   "📈", "📉"),
    ("XPTUSD=X", "Платина",   "📈", "📉"),
    ("XPDUSD=X", "Палладий",  "📈", "📉"),
    ("HG=F",     "Медь",      "📈", "📉"),
]

# Кэш последних отправленных алертов - не спамить одним и тем же
_ALERTED_TODAY: set = set()   # "XAUUSD=X_up" / "XAUUSD=X_down"


async def _fetch_price_change(session, symbol: str) -> float | None:
    """
    Возвращает % изменения цены за день через Yahoo Finance.
    Положительное = рост, отрицательное = падение.
    """
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval=1d&range=5d"
    )
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"User-Agent": "Mozilla/5.0 (compatible; KAZ_METALSBot/1.0)"},
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()

        result = data.get("chart", {}).get("result", [])
        if not result:
            return None

        closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes = [c for c in closes if c is not None]

        if len(closes) < 2:
            return None

        prev_close = closes[-2]
        current    = closes[-1]
        if prev_close == 0:
            return None

        return (current - prev_close) / prev_close * 100

    except Exception as e:
        logger.error(f"_fetch_price_change {symbol}: {e}")
        return None


async def check_metal_alerts(bot) -> None:
    """Проверяет цены металлов. При изменении > ALERT_THRESHOLD% постит алерт."""
    if not NEWS_TOPIC_ID or not GROUP_ID:
        return
    # Берём KZT-цены для золота/серебра/платины/палладия
    try:
        prices_kzt, usd_kzt = await get_metal_prices()
    except Exception:
        prices_kzt, usd_kzt = {}, 0
    # Маппинг Yahoo-символа → LBMA-ключ
    symbol_to_lbma = {
        "XAUUSD=X": "XAU", "XAGUSD=X": "XAG",
        "XPTUSD=X": "XPT", "XPDUSD=X": "XPD",
    }
    async with aiohttp.ClientSession() as session:
        for symbol, name, emoji_up, emoji_down in _ALERT_METALS:
            change = await _fetch_price_change(session, symbol)
            if change is None:
                continue
            abs_change = abs(change)
            if abs_change < ALERT_THRESHOLD:
                continue
            alert_key = f"{symbol}_{'up' if change > 0 else 'down'}"
            if alert_key in _ALERTED_TODAY:
                continue
            sign  = "+" if change > 0 else ""
            emoji = emoji_up if change > 0 else emoji_down
            # Строим текст
            lbma_key = symbol_to_lbma.get(symbol)
            price_line = ""
            if lbma_key and lbma_key in prices_kzt:
                p = prices_kzt[lbma_key][1]
                price_line = f"Цена: {p:,} ₸ за грамм\n"
            rate_line = f"Курс USD/KZT: {usd_kzt:,}\n\n" if usd_kzt else ""
            text = (
                f"{emoji} <b>{name} {sign}{abs_change:.1f}%</b>\n\n"
                f"{price_line}"
                f"{rate_line}"
                f"Актуальные цены → @KAZ_METALSBOT"
            )
            try:
                await bot.send_message(
                    chat_id=GROUP_ID,
                    message_thread_id=NEWS_TOPIC_ID,
                    text=text,
                    parse_mode="HTML",
                )
                _ALERTED_TODAY.add(alert_key)
                logger.info(f"Алерт: {name} {sign}{abs_change:.1f}%")
            except Exception as e:
                logger.error(f"check_metal_alerts send: {e}")


async def _reset_daily_alerts():
    """Сбрасывает кэш алертов в полночь."""
    global _ALERTED_TODAY
    _ALERTED_TODAY = set()


async def metal_alert_job(context: ContextTypes.DEFAULT_TYPE):
    await check_metal_alerts(context.bot)


async def reset_alerts_job(context: ContextTypes.DEFAULT_TYPE):
    await _reset_daily_alerts()



async def force_alert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительно публикует алерты по всем металлам игнорируя порог."""
    if update.message.chat.type != "private" or not is_admin(str(update.message.from_user.id)):
        return
    await update.message.reply_text("⏳ Получаю данные...")
    sent = 0
    async with aiohttp.ClientSession() as session:
        for symbol, name, emoji_up, emoji_down in _ALERT_METALS:
            change = await _fetch_price_change(session, symbol)
            if change is None:
                continue
            prices, usd_kzt = await get_metal_prices()
            price_kzt = prices.get(symbol, (None, None, None))[1] if prices else None
            if not price_kzt:
                continue
            emoji = emoji_up if change > 0 else emoji_down
            sign = "+" if change > 0 else ""
            text = (
                f"{emoji} <b>{name} {sign}{change:.1f}%</b>\n\n"
                f"Цена: <b>{price_kzt:,} ₸</b> за грамм\n"
                f"Курс USD/KZT: {usd_kzt:,}\n\n"
                f"Актуальные цены — @KAZ_METALSBOT"
            )
            try:
                await context.bot.send_message(
                    chat_id=GROUP_ID,
                    message_thread_id=NEWS_TOPIC_ID,
                    text=text,
                    parse_mode="HTML",
                )
                sent += 1
            except Exception as e:
                logger.error(f"force_alert send: {e}")
    await update.message.reply_text(f"✅ Опубликовано алертов: {sent}")

async def check_alerts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /check_alerts - ручная проверка алертов (только для админа)."""
    if update.message.chat.type != "private":
        return
    if not is_admin(str(update.message.from_user.id)):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    await update.message.reply_text(
        f"⏳ Проверяю цены металлов (порог: {ALERT_THRESHOLD}%)..."
    )
    # Временно сбрасываем кэш чтобы принудительно отправить
    _ALERTED_TODAY.clear()
    await check_metal_alerts(context.bot)
    await update.message.reply_text(
        "✅ Готово. Алерты отправлены если было изменение > "
        f"{ALERT_THRESHOLD}%"
    )



async def startup_setup_job(context: ContextTypes.DEFAULT_TYPE):
    """Закрывает форум-топики при старте, оставляя Обсуждения открытыми."""
    if not GROUP_ID:
        return
    from telegram import ChatPermissions
    try:
        await context.bot.set_chat_permissions(
            chat_id=GROUP_ID,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_other_messages=True,
                can_send_polls=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False,
                can_manage_topics=False,
            ),
        )
    except Exception as e:
        logger.error(f"startup_setup permissions: {e}")
    for tid in [t for t in list(CATEGORY_TOPICS.values()) + [NEWS_TOPIC_ID, WANT_BUY_TOPIC_ID] if t]:
        try:
            await context.bot.close_forum_topic(chat_id=GROUP_ID, message_thread_id=tid)
        except Exception as e:
            logger.error(f"startup_setup close {tid}: {e}")
    # Переименовываем вкладку новостей
    if NEWS_TOPIC_ID:
        try:
            await context.bot.edit_forum_topic(
                chat_id=GROUP_ID, message_thread_id=NEWS_TOPIC_ID, name="⚡ Ценовые алерты"
            )
        except Exception:
            pass
        try:
            await context.bot.unpin_all_chat_messages(
                chat_id=GROUP_ID, message_thread_id=NEWS_TOPIC_ID
            )
        except Exception:
            pass
    # Архивируем объявления из удалённых категорий (напр. Слитки)
    valid_cats = set(CATEGORIES) | set(CATEGORY_TOPICS.keys())
    stale = db_fetchall("SELECT ad_id, category, message_id FROM ads WHERE status='active'")
    for ad in stale:
        if ad.get("category") and ad.get("category") not in valid_cats:
            try:
                if ad.get("message_id") and GROUP_ID:
                    await context.bot.delete_message(chat_id=GROUP_ID, message_id=int(ad["message_id"]))
            except Exception:
                pass
            try:
                conn_c = get_conn(); cur_c = conn_c.cursor()
                cur_c.execute("UPDATE ads SET status='archived' WHERE ad_id=%s", (ad["ad_id"],))
                conn_c.commit(); cur_c.close(); conn_c.close()
                logger.info(f"startup cleanup: archived ad #{ad['ad_id']} category={ad.get('category')}")
            except Exception as e:
                logger.error(f"startup cleanup: {e}")
    # Снимаем закреп цен из группы
    old_pin = get_setting(_PRICES_PIN_KEY)
    if old_pin and GROUP_ID:
        try:
            await context.bot.unpin_chat_message(chat_id=GROUP_ID, message_id=int(old_pin))
            await context.bot.delete_message(chat_id=GROUP_ID, message_id=int(old_pin))
            set_setting(_PRICES_PIN_KEY, "")
        except Exception:
            pass
    # Удаляем сохранённые новостные сообщения из группы
    rows = db_fetchall("SELECT message_id FROM news_published WHERE message_id IS NOT NULL")
    deleted_news = 0
    for r in rows:
        mid = r.get("message_id")
        if mid and GROUP_ID:
            try:
                await context.bot.delete_message(chat_id=GROUP_ID, message_id=int(mid))
                deleted_news += 1
            except Exception:
                pass
    if deleted_news:
        try:
            conn_cl = get_conn(); cur_cl = conn_cl.cursor()
            cur_cl.execute("DELETE FROM news_published")
            conn_cl.commit(); cur_cl.close(); conn_cl.close()
        except Exception as e:
            logger.error(f"startup clear news: {e}")
    logger.info("startup_setup_job done")


async def expire_ads_job(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневно архивирует объявления у которых истёк срок (30 дней)."""
    from datetime import datetime
    today = datetime.now().date()
    ads = db_fetchall("SELECT * FROM ads WHERE status='active' AND expires_at != ''")
    expired = []
    for ad in ads:
        try:
            exp_date = datetime.strptime(ad.get("expires_at",""), "%d.%m.%Y").date()
            if exp_date < today:
                expired.append(ad)
        except Exception:
            continue

    for ad in expired:
        ad_id = ad.get("ad_id")
        # Удаляем из группы
        msg_id = ad.get("message_id")
        if msg_id and GROUP_ID:
            try:
                await context.bot.delete_message(chat_id=GROUP_ID, message_id=int(msg_id))
            except Exception:
                pass
        # Помечаем как истёкшее
        try:
            conn_e = get_conn(); cur_e = conn_e.cursor()
            cur_e.execute("UPDATE ads SET status='expired' WHERE ad_id=%s", (ad_id,))
            conn_e.commit(); cur_e.close(); conn_e.close()
        except Exception as e:
            logger.error(f"expire_ads: {e}")
        # Уведомляем продавца
        try:
            await context.bot.send_message(
                chat_id=int(ad.get("user_id")),
                text=f"⏰ Ваше объявление №{ad_id} «{(ad.get('description','') or '')[:40]}» истекло через 30 дней и было удалено. Вы можете подать новое.",
            )
        except Exception:
            pass
    if expired:
        logger.info(f"expire_ads_job: архивировано {len(expired)} объявлений")

async def daily_news_job(context: ContextTypes.DEFAULT_TYPE):
    await _do_fetch_and_post(context.bot)


async def publish_news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    if not is_admin(str(update.message.from_user.id)):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    await update.message.reply_text("⏳ Собираю новости за последние 3 дня...")
    posted = await _do_fetch_and_post(context.bot)
    if not posted:
        cnt = len(_get_published_titles())
        await update.message.reply_text(
            f"⚠️ Новостей не найдено.\n"
            f"В базе уже {cnt} записей за 7 дней.\n\n"
            f"👉 /reset_news - сбросить историю и опубликовать заново"
        )
        return
    lines = "\n".join(f"{a['emoji']} [{a['domain']}] {a['title'][:45]}…" for a in posted)
    await update.message.reply_text(f"✅ Опубликовано {len(posted)}:\n\n{lines}")




async def get_topic_id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отвечает ID текущего топика — напиши эту команду внутри нужного топика."""
    thread_id = getattr(update.message, "message_thread_id", None)
    chat_id = update.message.chat_id
    if thread_id:
        await update.message.reply_text(f"📌 Topic ID этого топика: `{thread_id}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ Это не топик. Chat ID: `{chat_id}`", parse_mode="Markdown")


async def pin_discussions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Закрепляет приветственное сообщение в топике Обсуждения."""
    if update.message.chat.type != "private":
        return
    if not is_admin(str(update.message.from_user.id)):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    if not GROUP_ID:
        await update.message.reply_text("⚠️ GROUP_ID не задан.")
        return
    text = (
        "💬 <b>Обсуждения</b>\n"
        "Это наше место для живого общения. Задавайте вопросы, делитесь наблюдениями, "
        "обсуждайте рынок — золото, серебро, платину, украшения, часы, монеты, инвестиции.\n\n"
        "Если хочешь что-то купить или продать — в бота 🤖"
    )
    try:
        msg = await context.bot.send_message(
            chat_id=GROUP_ID,
            text=text,
            parse_mode="HTML",
        )
        await context.bot.pin_chat_message(
            chat_id=GROUP_ID,
            message_id=msg.message_id,
            disable_notification=True,
        )
        await update.message.reply_text("✅ Сообщение закреплено в Обсуждениях.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {e}")


async def open_discussions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открывает ТОЛЬКО Обсуждения для участников:
    1) Разрешает писать в группе (group permission)
    2) Закрывает все форум-топики (только admin может туда писать)
    Итог: участники пишут только в Обсуждениях."""
    if update.message.chat.type != "private":
        return
    if not is_admin(str(update.message.from_user.id)):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    if not GROUP_ID:
        await update.message.reply_text("⚠️ GROUP_ID не задан.")
        return

    # Шаг 1: разрешаем писать в группе (нужно для Обсуждений)
    try:
        from telegram import ChatPermissions
        await context.bot.set_chat_permissions(
            chat_id=GROUP_ID,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_other_messages=True,
                can_send_polls=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False,
                can_manage_topics=False,
            ),
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка прав группы: {e}")
        return

    # Шаг 2: закрываем все форум-топики — участники смогут только читать
    to_close = list(CATEGORY_TOPICS.values()) + [NEWS_TOPIC_ID]
    to_close = [t for t in to_close if t]
    closed, failed = 0, 0
    for tid in to_close:
        try:
            await context.bot.close_forum_topic(chat_id=GROUP_ID, message_thread_id=tid)
            closed += 1
        except Exception as e:
            logger.error(f"close topic {tid}: {e}")
            failed += 1

    await update.message.reply_text(
        f"✅ Готово!\n"
        f"• Обсуждения открыты для всех участников\n"
        f"• Закрыто форум-топиков: {closed}"
        + (f"\n⚠️ Ошибок: {failed}" if failed else "")
    )


async def lock_topics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Закрывает все категорийные топики — писать смогут только администраторы.
    Обсуждения остаются открытыми. Вызывай после деплоя один раз."""
    if update.message.chat.type != "private":
        return
    if not is_admin(str(update.message.from_user.id)):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    if not GROUP_ID:
        await update.message.reply_text("⚠️ GROUP_ID не задан.")
        return

    # Все топики которые нужно закрыть (кроме Обсуждений)
    to_close = list(CATEGORY_TOPICS.values()) + [NEWS_TOPIC_ID]
    to_close = [t for t in to_close if t]  # убираем None/0

    closed, failed = 0, 0
    for tid in to_close:
        try:
            await context.bot.close_forum_topic(chat_id=GROUP_ID, message_thread_id=tid)
            closed += 1
        except Exception as e:
            logger.error(f"close_forum_topic {tid}: {e}")
            failed += 1

    await update.message.reply_text(
        f"✅ Закрыто топиков: {closed}\n"
        f"{'⚠️ Ошибок: ' + str(failed) if failed else ''}\n\n"
        "Участники теперь могут писать только в Обсуждениях.\n"
        "Бот по-прежнему публикует в закрытые топики как администратор."
    )


async def setup_alerts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настраивает вкладку: переименовывает, удаляет старые сообщения, снимает закреп."""
    if update.message.chat.type != "private" or not is_admin(str(update.message.from_user.id)):
        return
    report = []
    # 1) Переименовать тему
    if NEWS_TOPIC_ID and GROUP_ID:
        try:
            await context.bot.edit_forum_topic(
                chat_id=GROUP_ID,
                message_thread_id=NEWS_TOPIC_ID,
                name="⚡ Ценовые алерты",
            )
            report.append("✅ Вкладка переименована")
        except Exception as e:
            report.append(f"⚠️ Переименование: {e}")
    # 2) Снять и удалить закреплённые цены
    pin_id = get_setting(_PRICES_PIN_KEY)
    if pin_id and GROUP_ID:
        for mid in [pin_id]:
            try:
                await context.bot.unpin_chat_message(chat_id=GROUP_ID, message_id=int(mid))
                await context.bot.delete_message(chat_id=GROUP_ID, message_id=int(mid))
            except Exception:
                pass
        set_setting(_PRICES_PIN_KEY, "")
        report.append("✅ Закреплённые цены удалены")
    # 3) Удалить сохранённые новостные сообщения из БД
    deleted = 0
    rows = db_fetchall("SELECT message_id FROM news_published WHERE message_id IS NOT NULL")
    for r in rows:
        mid = r.get("message_id")
        if mid:
            try:
                await context.bot.delete_message(chat_id=GROUP_ID, message_id=int(mid))
                deleted += 1
            except Exception:
                pass
    try:
        conn_c = get_conn(); cur_c = conn_c.cursor()
        cur_c.execute("DELETE FROM news_published")
        conn_c.commit(); cur_c.close(); conn_c.close()
    except Exception as e:
        logger.error(f"setup_alerts clear: {e}")
    report.append(f"✅ Удалено {deleted} сообщений из вкладки")
    report.append("\nСтарые сообщения без ID удали вручную в Telegram.")
    await update.message.reply_text("\n".join(report))

async def reset_news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    if not is_admin(str(update.message.from_user.id)):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    global _SENT_HASHES, _TOPIC_COOLDOWN
    _SENT_HASHES = set()
    _TOPIC_COOLDOWN = {}
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("DELETE FROM news_published")
        conn.commit(); cur.close(); conn.close()
        await update.message.reply_text(
            "✅ Кэш и история очищены.\n"
            "Используй /publish_news для немедленной публикации."
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {e}")


    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


# ── Ключевые слова ТЕМ (хотя бы одно должно быть в заголовке) ──────────────
NEWS_KEYWORDS_PRIMARY = [
    "золото", "золот", "серебро", "серебр",
    "платина", "платин", "палладий", "драгоценн", "драгметалл",
    "ювелир", "кольцо", "кольца", "серьги", "серьга",
    "браслет", "подвеска", "украшени",
    "слиток", "слитк", "аффинаж", "пробирн",
    "проба золота", "проба серебра",
    "лом золота", "лом серебра", "скупка золота",
    "монета золот", "монеты золот",
    "нбрк золото", "нацбанк золото",
    "цена золота", "курс золота", "цена серебра",
    "запасы золота", "резервы золота",
]

# ── Стоп-слова: если есть - новость отбрасывается ───────────────────────────
NEWS_STOP_WORDS = [
    "контрабанд", "осудили", "осуждён", "осуждена", "арестован",
    "уголовн", "задержан", "обвиняем", "преступлен", "мошенничест",
    "украл", "украли", "кража", "ограбил", "ограблен",
    "взятк", "коррупц", "санкц",
]

async def publish_prices(bot):
    logger.info("publish_prices: prices available in bot only")


async def daily_prices(context: ContextTypes.DEFAULT_TYPE):
    await publish_prices(context.bot)


async def publish_prices_cmd(update, context):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    await update.message.reply_text("⏳ Публикую цены...")
    await publish_prices(context.bot)
    await update.message.reply_text("✅ Готово!")


async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.message.chat
    await update.message.reply_text(f"Chat ID: {chat.id}\nType: {chat.type}\nTitle: {chat.title}")


def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("getid", getid))
    app.add_handler(CommandHandler("publish_prices", publish_prices_cmd))
    app.add_handler(CommandHandler("lock_topics", lock_topics_cmd))
    app.add_handler(CommandHandler("get_topic_id", get_topic_id_cmd))
    app.add_handler(CommandHandler("check_alerts", check_alerts_cmd))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("rules", rules_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VIDEO, video_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    if AUTO_PUBLISH_PRICES and GROUP_ID and app.job_queue is not None:
        app.job_queue.run_once(daily_prices, when=10)
        app.job_queue.run_daily(daily_prices, time=dt_time(hour=4, minute=0))
        app.job_queue.run_daily(review_reminder_job, time=dt_time(hour=12, minute=0))
    else:
        logger.warning("Автопубликация цен отключена.")

    if GROUP_ID and app.job_queue is not None:
        app.job_queue.run_once(startup_setup_job, when=15)
        app.job_queue.run_repeating(metal_alert_job, interval=3600, first=120)
        app.job_queue.run_daily(reset_alerts_job, time=dt_time(hour=0, minute=0))
        app.job_queue.run_repeating(expire_ads_job, interval=86400, first=3600)

    logger.info("KAZ_METALS Bot запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
