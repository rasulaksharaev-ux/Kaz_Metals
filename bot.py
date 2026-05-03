import os
import logging
import psycopg2
import psycopg2.extras
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "8537953320:AAGHBciqdEyYMo6-5f7CK18cT5FAwOTPYC8")
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
                created_at TEXT
            )
        """)
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
            INSERT INTO ads (user_id, username, type, category, description, price, photo_id, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING ad_id
        """, (
            str(ad_data.get("user_id")),
            ad_data.get("username", ""),
            ad_data.get("type", ""),
            ad_data.get("category", ""),
            ad_data.get("description", ""),
            ad_data.get("price", ""),
            ad_data.get("photo_id", ""),
            "active",
            datetime.now().strftime("%d.%m.%Y %H:%M")
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

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Подать объявление", callback_data="menu_ad")],
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
    if query.message.chat.type != "private":
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
        states[user_id] = {"step": "ad_type"}
        await context.bot.send_message(chat_id=chat_id,
            text="➕ Подать объявление\n\nВыберите тип:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🟢 Продаю", callback_data="ad_sell"),
                 InlineKeyboardButton("🔵 Покупаю", callback_data="ad_buy")]
            ]))
        return

    if data in ["ad_sell", "ad_buy"]:
        ad_type = "Продаю" if data == "ad_sell" else "Покупаю"
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

    if data == "ad_confirm":
        u = states.get(user_id, {})
        user = get_user(user_id)
        uname = user.get("username", username) if user else username
        emoji = "🟢" if u.get("ad_type") == "Продаю" else "🔵"
        ad_text = (
            f"{emoji} {u.get('ad_type','').upper()}\n"
            f"{u.get('category','')}\n\n"
            f"{u.get('description','')}\n\n"
            f"💰 {u.get('price','')}\n"
            f"👤 @{uname}"
        )
        topic_id = CATEGORY_TOPICS.get(u.get("category", ""))
        try:
            kwargs = {"message_thread_id": topic_id} if topic_id else {}
            buy_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("💬 Хочу купить", callback_data=f"buy_{user_id}")
            ]])
            if u.get("photo_id"):
                await context.bot.send_photo(chat_id=GROUP_ID, photo=u["photo_id"],
                    caption=ad_text, reply_markup=buy_markup, **kwargs)
            else:
                await context.bot.send_message(chat_id=GROUP_ID, text=ad_text,
                    reply_markup=buy_markup, **kwargs)
            ad_id = save_ad({
                "user_id": user_id, "username": uname,
                "type": u.get("ad_type"), "category": u.get("category"),
                "description": u.get("description"), "price": u.get("price"),
                "photo_id": u.get("photo_id", "")
            })
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

    if data.startswith("buy_") and not data.startswith("buy_confirm_"):
        seller_id = data.replace("buy_", "")
        if seller_id == user_id:
            await query.answer("Это ваше объявление!", show_alert=True)
            return
        seller = get_user(seller_id)
        seller_uname = seller.get("username", "") if seller else ""
        await context.bot.send_message(chat_id=chat_id,
            text=f"Вы хотите приобрести этот товар?\n\n"
                 f"Продавец: @{seller_uname}\n\n"
                 f"После подтверждения оба участника получат контакты друг друга.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Подтвердить", callback_data=f"buy_confirm_{seller_id}")],
                [InlineKeyboardButton("❌ Отмена", callback_data="buy_cancel")]
            ]))
        return

    if data == "buy_cancel":
        await context.bot.send_message(chat_id=chat_id, text="❌ Отменено.")
        return

    if data.startswith("buy_confirm_"):
        seller_id = data.replace("buy_confirm_", "")
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
        save_deal(seller_id, user_id, 0)

        # Покупателю — данные продавца
        await context.bot.send_message(chat_id=int(user_id),
            text=f"✅ Заявка отправлена продавцу!\n\n"
                 f"👤 Продавец: {seller_fname} {seller_lname}\n"
                 f"🔗 @{seller_uname}\n"
                 f"📞 Телефон: {seller_phone}\n\n"
                 f"Свяжитесь напрямую!\n\n"
                 f"Через 7 дней придёт запрос на оценку сделки.")

        # Продавцу — данные покупателя
        await context.bot.send_message(chat_id=int(seller_id),
            text=f"🔔 Новый покупатель на ваш товар!\n\n"
                 f"👤 Покупатель: {buyer_fname} {buyer_lname}\n"
                 f"🔗 @{buyer_uname}\n"
                 f"📞 Телефон: {buyer_phone}\n\n"
                 f"Свяжитесь напрямую!\n\n"
                 f"Через 7 дней придёт запрос на оценку сделки.")
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
        text = f"📢 Мои объявления\n\n🟢 Активных: {len(active)}\n"
        if not active:
            text += "\nУ вас нет активных объявлений."
        keyboard = []
        for a in active[:5]:
            text += f"\n#{a.get('ad_id')} {a.get('type')} {a.get('category')}\n💰 {a.get('price')}\n"
            keyboard.append([InlineKeyboardButton(
                f"🗑 Удалить #{a.get('ad_id')}", callback_data=f"del_ad_{a.get('ad_id')}"
            )])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="menu_profile")])
        await context.bot.send_message(chat_id=chat_id, text=text,
            reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("del_ad_"):
        ad_id = int(data.replace("del_ad_", ""))
        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("UPDATE ads SET status = 'deleted' WHERE ad_id = %s AND user_id = %s", (ad_id, str(user_id)))
            affected = cur.rowcount
            conn.commit()
            cur.close()
            conn.close()
            if affected > 0:
                await context.bot.send_message(chat_id=chat_id,
                    text=f"✅ Объявление #{ad_id} успешно удалено.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Мои объявления", callback_data="my_ads")]]))
            else:
                await context.bot.send_message(chat_id=chat_id,
                    text=f"⚠️ Объявление #{ad_id} не найдено.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Мои объявления", callback_data="my_ads")]]))
        except Exception as e:
            logger.error(f"del_ad error: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Ошибка удаления: {e}")
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
        states[user_id]["step"] = "ad_photo"
        await update.message.reply_text("📸 Отправьте фото товара\n(или напишите - чтобы пропустить)")
        return

    if step == "ad_photo" and text == "-":
        states[user_id]["photo_id"] = ""
        u = states[user_id]
        emoji = "🟢" if u.get("ad_type") == "Продаю" else "🔵"
        await update.message.reply_text(
            f"📋 Предварительный просмотр:\n\n"
            f"{emoji} {u.get('ad_type','').upper()}\n"
            f"{u.get('category','')}\n\n"
            f"{u.get('description','')}\n\n"
            f"💰 {u.get('price','')}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Опубликовать", callback_data="ad_confirm"),
                 InlineKeyboardButton("❌ Отменить", callback_data="ad_cancel")]
            ]))
        return

    await update.message.reply_text("Следуйте инструкциям. Если что-то пошло не так — напишите /start")

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    if states.get(user_id, {}).get("step") == "ad_photo":
        photo_id = update.message.photo[-1].file_id
        states[user_id]["photo_id"] = photo_id
        u = states[user_id]
        emoji = "🟢" if u.get("ad_type") == "Продаю" else "🔵"
        await update.message.reply_text(
            f"📋 Предварительный просмотр:\n\n"
            f"{emoji} {u.get('ad_type','').upper()}\n"
            f"{u.get('category','')}\n\n"
            f"{u.get('description','')}\n\n"
            f"💰 {u.get('price','')}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Опубликовать", callback_data="ad_confirm"),
                 InlineKeyboardButton("❌ Отменить", callback_data="ad_cancel")]
            ]))

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    logger.info("KAZMETALS Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
