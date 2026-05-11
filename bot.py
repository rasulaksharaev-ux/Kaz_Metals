import os
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, time as dt_time
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
AUTO_PUBLISH_PRICES = (clean_env_value(os.environ.get("AUTO_PUBLISH_PRICES")) or "true").lower() in ("1", "true", "yes", "y")

CATEGORIES = [
    "📈 Инвестиции и цены",
    "🏛 Слитки",
    "👑 Ювелирные украшения",
    "❤️ Кольца",
    "🧪 Лом и скупка",
    "🎖 Подвеска/Браслет",
    "🪙 Монеты",
    "🔥 Exclusive",
    "🔮 Часы",
    "💎 Камни",
]

CATEGORY_TOPICS = {
    "📈 Инвестиции и цены": optional_int_env("TOPIC_INVEST_PRICES", 12),
    "🏛 Слитки": optional_int_env("TOPIC_BARS", 4),
    "👑 Ювелирные украшения": optional_int_env("TOPIC_JEWELRY", 8),
    "❤️ Кольца": optional_int_env("TOPIC_RINGS", 0),
    "🧪 Лом и скупка": optional_int_env("TOPIC_SCRAP", 11),
    "🎖 Подвеска/Браслет": optional_int_env("TOPIC_PENDANTS_BRACELETS", 0),
    "🪙 Монеты": optional_int_env("TOPIC_COINS", 2),
    "🔥 Exclusive": optional_int_env("TOPIC_EXCLUSIVE", 0),
    "🔮 Часы": optional_int_env("TOPIC_WATCHES", 0),
    "💎 Камни": optional_int_env("TOPIC_STONES", 10),
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
        [InlineKeyboardButton("📊 Цены на металлы", callback_data="menu_prices")],
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
            f"Подтвердите связь с продавцом.{ad_info}\n\nПосле подтверждения оба участника получат контакты.",
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
            in_group = True  # если не можем проверить — пускаем

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
                    [InlineKeyboardButton("✅ Я вступил — проверить", callback_data="check_join")],
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
                [InlineKeyboardButton("✅ Я вступил — проверить", callback_data="check_join")],
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
    await update.message.reply_text("Команды: /start — меню, /rules — правила, /getid — узнать ID чата.")

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
            text="ℹ️ KAZ_METALS — доверенное сообщество участников рынка драгоценных металлов Казахстана.\n\n"
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
            text=f"🆕 Новый участник — KAZ_METALS\n\n"
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
                    [InlineKeyboardButton("✅ Я вступил — проверить", callback_data="check_join")],
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
        if data == "city_other":
            states[user_id]["step"] = "ad_city_text"
            await context.bot.send_message(chat_id=chat_id, text="Введите город:")
        else:
            city = data.replace("city_", "")
            states[user_id]["city"] = city
            states[user_id]["step"] = "ad_photo"
            await context.bot.send_message(chat_id=chat_id, text="📸 Отправьте 1 главное фото товара (или напишите -, чтобы пропустить).\n\nЭто фото будет показано в группе. Дополнительные фото и видео можно добавить позже.")
        return

    if data in ("skip_extra_media", "extra_done", "extra_skip"):
        await show_ad_preview(chat_id, context, user_id)
        return

    if data == "ad_cancel":
        states[user_id] = {"step": "menu"}
        await context.bot.send_message(chat_id=chat_id, text="❌ Отменено.", reply_markup=main_menu_keyboard())
        return

    if data == "ad_confirm":
        await publish_ad(context, chat_id, user_id)
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
        for ad in ads[:20]:
            title = f"№{ad.get('ad_id')} — {ad.get('category','')} — {ad.get('price','')} KZT"
            rows.append([InlineKeyboardButton(title[:60], callback_data=f"profile_ad_{ad.get('ad_id')}")])
        rows.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")])
        await context.bot.send_message(chat_id=chat_id, text="📦 Ваши активные объявления:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("profile_ad_"):
        ad_id = data.replace("profile_ad_", "")
        ad = db_fetchone("SELECT * FROM ads WHERE ad_id = %s AND user_id = %s", (int(ad_id), user_id)) if ad_id.isdigit() else None
        if not ad or ad.get("status") != "active":
            await context.bot.send_message(chat_id=chat_id, text="Объявление не найдено или уже удалено.", reply_markup=main_menu_keyboard())
            return
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📦 Объявление №{ad.get('ad_id')}\n\n{ad.get('category','')}\n📍 {ad.get('city','')}\n💰 {ad.get('price','')} KZT\n👁 Переходов: {ad.get('view_count', 0)}\n\n{ad.get('description','')}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Удалить объявление", callback_data=f"delete_ad_{ad_id}")],
                [InlineKeyboardButton("🔙 К моим объявлениям", callback_data="profile_ads")],
            ]),
        )
        return

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
                 "6. Объявление сразу появится в группе\n\n"
                 "🔷 Для покупателя\n"
                 "1. Зайдите в группу KAZ_METALS\n"
                 "2. Найдите нужное объявление\n"
                 "3. Выберите действие:\n"
                 "   📸 Фото и видео — смотреть медиа\n"
                 "   💰 Купить — по цене продавца\n"
                 "   🤝 Предложить цену — торговаться\n"
                 "4. После подтверждения оба получат контакты\n\n"
                 "🔷 Репутация\n"
                 "После каждой сделки через 3 дня оба участника получают запрос на оценку. Рейтинг виден в каждом объявлении.\n\n"
                 "🔷 Цены на металлы\n"
                 "В боте доступна кнопка 📊 Цены на металлы — актуальный курс золота, серебра, платины и палладия в KZT за грамм. Цены также публикуются ежедневно во вкладке Инвестиции и цены группы.\n\n"
                 "⚖️ Правовая оговорка\n"
                 "KAZ_METALS — платформа-посредник. Мы соединяем продавцов и покупателей, но не являемся стороной сделок. Безопасность участников — наш приоритет.",
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
            buttons.append([InlineKeyboardButton(f"@{username} — {r.get('status','')}", callback_data=f"admin_user_{r.get('user_id')}")])
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
            cur.execute("SELECT user_id, username, first_name FROM users WHERE status != 'reset' ORDER BY registered_at DESC LIMIT 30")
            all_users = cur.fetchall()
            cur.close()
            conn.close()
            if not all_users:
                await context.bot.send_message(chat_id=chat_id, text="⚠️ Нет пользователей в базе.")
                return
            keyboard = []
            for u in all_users:
                uname = u.get("username") or u.get("first_name") or str(u.get("user_id"))
                label = f"@{uname}"
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
            cur2.execute("UPDATE users SET joined = 'no', status = 'reset' WHERE user_id = %s", (target_id,))
            conn.commit()
            cur.close()
            cur2.close()
            conn.close()
            states.pop(str(target_id), None)
            uname = target_user.get("username") if target_user else target_id

            # Исключаем из группы
            try:
                await context.bot.ban_chat_member(chat_id=GROUP_ID, user_id=int(target_id))
                await context.bot.unban_chat_member(chat_id=GROUP_ID, user_id=int(target_id))
            except Exception as e:
                logger.error(f"Kick from group error: {e}")

            # Уведомляем пользователя
            try:
                await context.bot.send_message(
                    chat_id=int(target_id),
                    text="🔄 Ваша регистрация была сброшена администратором.\n\n"
                         "Для повторного доступа пройдите регистрацию заново — напишите /start"
                )
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Регистрация @{uname} сброшена! Пользователь исключён из группы и уведомлён.",
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
            text=f"✅ Контакты продавца:\n{seller.get('first_name','')} {seller.get('last_name','')}\n@{seller.get('username','')}\n📞 {seller.get('phone','')}",
        )
        await context.bot.send_message(
            chat_id=int(seller_id),
            text=f"📩 Покупатель заинтересовался объявлением #{ad_id}:\n{buyer.get('first_name','')} {buyer.get('last_name','')}\n@{buyer.get('username','')}\n📞 {buyer.get('phone','')}",
        )
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
            caption = f"📦 Доп. медиа к объявлению #{ad_id}\n{u.get('category','')} — {u.get('price','')}"
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
                [InlineKeyboardButton("✅ Я вступил — проверить", callback_data="check_join")],
            ]),
        )
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🆕 Новый пользователь KAZ_METALS\n{data.get('first_name','')} {data.get('last_name','')}\n@{username}\n📞 {text}\nID: {user_id}",
        )
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
        await show_ad_preview(update.message.chat_id, context, user_id)
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
        "Сделка завершена? Если да — оцените участника. Если нет — нажмите «Сделка не завершена», и я напомню снова через сутки."
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


async def fetch_metal_news() -> str:
    """Fetch metal news from Google News RSS."""
    import xml.etree.ElementTree as ET
    feeds = [
        "https://news.google.com/rss/search?q=gold+price+precious+metals&hl=ru&gl=KZ&ceid=KZ:ru",
        "https://news.google.com/rss/search?q=золото+серебро+металлы&hl=ru&gl=KZ&ceid=KZ:ru",
    ]
    articles = []
    try:
        async with aiohttp.ClientSession() as session:
            for feed_url in feeds:
                try:
                    headers = {"User-Agent": "Mozilla/5.0 (compatible; bot)"}
                    async with session.get(feed_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
                        logger.info(f"News RSS status: {r.status} for {feed_url}")
                        if r.status == 200:
                            text = await r.text()
                            root = ET.fromstring(text)
                            items = root.findall(".//item")
                            logger.info(f"Found {len(items)} news items")
                            for item in items[:5]:
                                title_el = item.find("title")
                                link_el = item.find("link")
                                if title_el is not None and link_el is not None:
                                    title = (title_el.text or "").split(" - ")[0].strip()
                                    link = link_el.text or ""
                                    if title and link and len(title) > 10:
                                        articles.append((title, link))
                                        if len(articles) >= 4:
                                            break
                except Exception as e:
                    logger.error(f"RSS fetch error: {e}")
                if len(articles) >= 4:
                    break

        if not articles:
            logger.warning("No news articles found")
            return ""

        lines = ["📰 Новости рынка металлов\n"]
        for title, link in articles[:4]:
            short = title[:120] + "..." if len(title) > 120 else title
            lines.append(f"• {short}\n  🔗 {link}\n")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"fetch_metal_news error: {e}")
        return ""

async def publish_prices(bot):
    global PRICE_MESSAGE_ID
    prices, usd_kzt = await get_metal_prices()
    if not prices:
        return
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    text = f"📊 Актуальные цены на металлы\n🕐 {now}\n💵 USD/KZT: {usd_kzt}\n\n"
    for _, (name, price, unit) in prices.items():
        text += f"{name}: {price:,} KZT/{unit}\n".replace(",", " ")
    text += "\nЦены за 1 грамм | Источник: LBMA + курс НБРК"
    try:
        topic_id = CATEGORY_TOPICS.get("📈 Инвестиции и цены")
        kwargs = {"message_thread_id": topic_id} if topic_id else {}

        # Получаем ID предыдущего сообщения из базы (переживает перезапуск)
        old_msg_id = PRICE_MESSAGE_ID
        if old_msg_id is None:
            saved = get_setting("price_message_id")
            if saved:
                try:
                    old_msg_id = int(saved)
                except ValueError:
                    old_msg_id = None

        # Удаляем старое сообщение если есть
        if old_msg_id:
            try:
                await bot.unpin_chat_message(chat_id=GROUP_ID, message_id=old_msg_id)
            except Exception:
                pass
            try:
                await bot.delete_message(chat_id=GROUP_ID, message_id=old_msg_id)
            except Exception as e:
                logger.info(f"Old prices message could not be deleted: {e}")

        # Отправляем новое сообщение без уведомления
        msg = await bot.send_message(chat_id=GROUP_ID, text=text, disable_notification=True, **kwargs)
        PRICE_MESSAGE_ID = msg.message_id
        set_setting("price_message_id", str(msg.message_id))

        # Закрепляем новое
        try:
            await bot.pin_chat_message(chat_id=GROUP_ID, message_id=msg.message_id, disable_notification=True)
        except Exception as e:
            logger.error(f"pin_chat_message error: {e}")
    except Exception as e:
        logger.error(f"publish_prices error: {e}")


async def daily_prices(context: ContextTypes.DEFAULT_TYPE):
    await publish_prices(context.bot)


async def publish_prices_cmd(update, context):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    await update.message.reply_text("⏳ Публикую цены и новости...")
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
        logger.warning("Автопубликация цен отключена: проверьте AUTO_PUBLISH_PRICES, GROUP_ID и python-telegram-bot[job-queue].")

    logger.info("KAZ_METALS Bot запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
