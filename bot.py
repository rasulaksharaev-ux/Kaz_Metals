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
AUTO_PUBLISH_PRICES = (clean_env_value(os.environ.get("AUTO_PUBLISH_PRICES")) or "true").lower() in ("1", "true", "yes", "y")

CATEGORIES = [
    "💍 Ювелирка",
    "🥇 Слитки",
    "💎 Камни",
    "🗑 Лом и скупка",
    "📈 Инвестиции и цены",
    "🪙 Монеты драг",
]

CATEGORY_TOPICS = {
    "💍 Ювелирка": 8,
    "🥇 Слитки": 4,
    "💎 Камни": 10,
    "🗑 Лом и скупка": 11,
    "📈 Инвестиции и цены": 12,
    "🪙 Монеты драг": 2,
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
            expires_at TEXT DEFAULT ''
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
            notified BOOLEAN DEFAULT FALSE
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

    # На случай если таблицы уже были созданы старой версией.
    alter_sql = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS joined TEXT DEFAULT 'no'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user'",
        "ALTER TABLE ads ADD COLUMN IF NOT EXISTS message_id TEXT DEFAULT ''",
        "ALTER TABLE ads ADD COLUMN IF NOT EXISTS media_message_id TEXT DEFAULT ''",
        "ALTER TABLE ads ADD COLUMN IF NOT EXISTS city TEXT DEFAULT ''",
        "ALTER TABLE ads ADD COLUMN IF NOT EXISTS expires_at TEXT DEFAULT ''",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'contact_opened'",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS notified BOOLEAN DEFAULT FALSE",
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


def save_deal(seller_id: str, buyer_id: str, ad_id: str) -> Optional[int]:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO deals (seller_id, buyer_id, ad_id, created_at, status)
            VALUES (%s, %s, %s, %s, %s) RETURNING deal_id
            """,
            (str(seller_id), str(buyer_id), str(ad_id), datetime.now().strftime("%d.%m.%Y %H:%M"), "contact_opened"),
        )
        deal_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return deal_id
    except Exception as e:
        logger.error(f"save_deal error: {e}")
        return None


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
            headers={"User-Agent": "KAZMETALSBot/1.0"},
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
            headers={"User-Agent": "KAZMETALSBot/1.0"},
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
        [InlineKeyboardButton("🔔 Мои подписки", callback_data="menu_subs")],
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
    if user and user.get("status") == "active" and user.get("joined") == "yes":
        states[user_id] = {"step": "menu"}
        await update.message.reply_text("🏅 Главное меню KAZMETALS", reply_markup=main_menu_keyboard())
        return

    if user and user.get("status") == "active" and user.get("joined") != "yes":
        states[user_id] = {"step": "awaiting_join"}
        await update.message.reply_text(
            "✅ Вы зарегистрированы. Теперь вступите в группу и нажмите проверку.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔓 Вступить в KAZMETALS", url=GROUP_LINK)],
                [InlineKeyboardButton("✅ Я вступил — проверить", callback_data="check_join")],
            ]),
        )
        return

    states[user_id] = {"step": "start"}
    await update.message.reply_text(
        "🏅 Добро пожаловать в KAZMETALS!\n\n"
        "Закрытая платформа объявлений и обсуждений по драгоценным металлам.\n\n"
        "Для доступа нужно пройти регистрацию.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Зарегистрироваться", callback_data="reg_start")],
            [InlineKeyboardButton("❓ О платформе", callback_data="about")],
        ]),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Команды: /start — меню, /rules — правила, /getid — узнать ID чата.")


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Правила KAZMETALS\n\n"
        "1. Запрещены поддельные товары.\n"
        "2. Продавец отвечает за описание и подлинность.\n"
        "3. Сделки совершаются напрямую между участниками.\n"
        "4. KAZMETALS является информационным посредником.\n"
        "5. За нарушение правил доступ может быть ограничен."
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(str(update.message.from_user.id)):
        await update.message.reply_text("Команда доступна только администратору.")
        return
    users_count = db_fetchone("SELECT COUNT(*) AS c FROM users") or {"c": 0}
    ads_count = db_fetchone("SELECT COUNT(*) AS c FROM ads") or {"c": 0}
    await update.message.reply_text(f"Админ-панель\n👥 Пользователи: {users_count['c']}\n📦 Объявления: {ads_count['c']}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id
    data = query.data

    if data == "about":
        await context.bot.send_message(
            chat_id=chat_id,
            text="ℹ️ KAZMETALS — платформа для объявлений и обсуждений по металлам.\n\n"
                 "Вы можете продавать, покупать, смотреть цены и подписываться на категории.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Зарегистрироваться", callback_data="reg_start")]]),
        )
        return

    if data == "reg_start":
        states[user_id] = {"step": "awaiting_first_name"}
        await context.bot.send_message(chat_id=chat_id, text="📝 Шаг 1 из 3\nВведите ваше имя:")
        return

    if data == "check_join":
        ok = await check_user_in_group(context.bot, int(user_id), GROUP_ID)
        if ok:
            update_user_field(user_id, "joined", "yes")
            states[user_id] = {"step": "menu"}
            await context.bot.send_message(chat_id=chat_id, text="✅ Доступ открыт.", reply_markup=main_menu_keyboard())
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Пока не вижу вас в группе. Вступите и нажмите проверку ещё раз.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔓 Вступить в KAZMETALS", url=GROUP_LINK)],
                    [InlineKeyboardButton("✅ Я вступил — проверить", callback_data="check_join")],
                ]),
            )
        return

    if data == "back_menu":
        states[user_id] = {"step": "menu"}
        await context.bot.send_message(chat_id=chat_id, text="🏅 Главное меню", reply_markup=main_menu_keyboard())
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
            await context.bot.send_message(chat_id=chat_id, text="📸 Отправьте главное фото товара или напишите -, чтобы пропустить.")
        return

    if data == "skip_extra_media":
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
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=main_menu_keyboard())
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
                 f"@{user.get('username','')}\n📞 {user.get('phone','')}\n"
                 f"⭐ Рейтинг: {user.get('rating', 0)} | Отзывов: {user.get('reviews_count', 0)}",
            reply_markup=main_menu_keyboard(),
        )
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
            text="📋 Как работает KAZMETALS\n\n"
                 "1. Подайте объявление.\n"
                 "2. Укажите категорию, описание, цену, город и фото.\n"
                 "3. Объявление публикуется в группе.\n"
                 "4. Покупатель связывается с продавцом или предлагает цену.\n\n"
                 "⚠️ Сделки совершаются напрямую между пользователями.",
            reply_markup=main_menu_keyboard(),
        )
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
        save_deal(seller_id, user_id, ad_id)
        await context.bot.send_message(
            chat_id=int(user_id),
            text=f"✅ Контакты продавца:\n{seller.get('first_name','')} {seller.get('last_name','')}\n@{seller.get('username','')}\n📞 {seller.get('phone','')}",
        )
        await context.bot.send_message(
            chat_id=int(seller_id),
            text=f"📩 Покупатель заинтересовался объявлением #{ad_id}:\n{buyer.get('first_name','')} {buyer.get('last_name','')}\n@{buyer.get('username','')}\n📞 {buyer.get('phone','')}",
        )
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
        f"📸 Доп. медиа: {extra_count} шт."
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Опубликовать", callback_data="ad_confirm"), InlineKeyboardButton("❌ Отменить", callback_data="ad_cancel")],
        ]),
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
        buttons.append([InlineKeyboardButton("📸 Фото и видео", url=f"https://t.me/{MEDIA_CHANNEL_USERNAME}/{media_msg_id}")])
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
                [InlineKeyboardButton("🔓 Вступить в KAZMETALS", url=GROUP_LINK)],
                [InlineKeyboardButton("✅ Я вступил — проверить", callback_data="check_join")],
            ]),
        )
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🆕 Новый пользователь KAZMETALS\n{data.get('first_name','')} {data.get('last_name','')}\n@{username}\n📞 {text}\nID: {user_id}",
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
        await update.message.reply_text("📸 Отправьте главное фото товара или напишите -, чтобы пропустить.")
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
        offer_line = f"💸 Предложение: {int(text):,} KZT".replace(",", " ")
        await context.bot.send_message(
            chat_id=int(seller_id),
            text=(
                f"💼 Новое предложение цены по объявлению #{ad_id}\n"
                f"📦 {(ad or {}).get('description','')[:80]}\n"
                f"{offer_line}\n\n"
                f"👤 {buyer.get('first_name','')} {buyer.get('last_name','')}\n@{buyer.get('username','')}"
            ),
        )
        states[user_id] = {"step": "menu"}
        await update.message.reply_text("✅ Ваше предложение отправлено продавцу.", reply_markup=main_menu_keyboard())
        return

    await update.message.reply_text("Напишите /start для главного меню.")


async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    if states.get(user_id, {}).get("step") == "ad_extra_media":
        states[user_id].setdefault("extra_media", []).append({"type": "video", "file_id": update.message.video.file_id})
        await update.message.reply_text(
            f"✅ Видео добавлено. Всего медиа: {len(states[user_id]['extra_media'])}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data="skip_extra_media")]]),
        )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    step = states.get(user_id, {}).get("step")
    if step == "ad_extra_media":
        states[user_id].setdefault("extra_media", []).append({"type": "photo", "file_id": update.message.photo[-1].file_id})
        await update.message.reply_text(
            f"✅ Фото добавлено. Всего медиа: {len(states[user_id]['extra_media'])}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data="skip_extra_media")]]),
        )
        return
    if step == "ad_photo":
        states[user_id]["photo_id"] = update.message.photo[-1].file_id
        states[user_id]["extra_media"] = []
        states[user_id]["step"] = "ad_extra_media"
        await update.message.reply_text(
            "✅ Главное фото сохранено. Добавьте доп. фото/видео или нажмите Готово.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data="skip_extra_media")]]),
        )


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
        if PRICE_MESSAGE_ID:
            try:
                await bot.edit_message_text(chat_id=GROUP_ID, message_id=PRICE_MESSAGE_ID, text=text)
                return
            except Exception:
                pass
        msg = await bot.send_message(chat_id=GROUP_ID, text=text, **kwargs)
        PRICE_MESSAGE_ID = msg.message_id
        await bot.pin_chat_message(chat_id=GROUP_ID, message_id=msg.message_id, disable_notification=True)
    except Exception as e:
        logger.error(f"publish_prices error: {e}")


async def daily_prices(context: ContextTypes.DEFAULT_TYPE):
    await publish_prices(context.bot)


async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.message.chat
    await update.message.reply_text(f"Chat ID: {chat.id}\nType: {chat.type}\nTitle: {chat.title}")


def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("getid", getid))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("rules", rules_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VIDEO, video_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    if AUTO_PUBLISH_PRICES and GROUP_ID and app.job_queue is not None:
        app.job_queue.run_once(daily_prices, when=10)
        app.job_queue.run_daily(daily_prices, time=dt_time(hour=4, minute=0))
    else:
        logger.warning("Автопубликация цен отключена: проверьте AUTO_PUBLISH_PRICES, GROUP_ID и python-telegram-bot[job-queue].")

    logger.info("KAZMETALS Bot запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
