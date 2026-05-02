import os
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "8537953320:AAHg9tebajVFYkWs-hiWwaPGYQEzP0D9c1I")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "612230948"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "ras_0104")
GROUP_LINK = os.environ.get("GROUP_LINK", "https://t.me/+_pgc-XiGz7E4YTUy")
GROUP_ID = int(os.environ.get("GROUP_ID", "-1003852395090"))

TOPIC_COINS = int(os.environ.get("TOPIC_COINS", "2"))
TOPIC_BARS = int(os.environ.get("TOPIC_BARS", "4"))
TOPIC_JEWELRY = int(os.environ.get("TOPIC_JEWELRY", "8"))
TOPIC_STONES = int(os.environ.get("TOPIC_STONES", "10"))
TOPIC_WATCHES = int(os.environ.get("TOPIC_WATCHES", "11"))

CATEGORIES = ["🪙 Монеты", "🥇 Слитки", "💍 Ювелирные изделия", "💎 Камни", "⌚ Часы"]
CATEGORY_TOPICS = {
    "🪙 Монеты": TOPIC_COINS,
    "🥇 Слитки": TOPIC_BARS,
    "💍 Ювелирные изделия": TOPIC_JEWELRY,
    "💎 Камни": TOPIC_STONES,
    "⌚ Часы": TOPIC_WATCHES,
}

DB_FILE = "users_db.json"

def load_db():
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {"users": {}, "ads": [], "deals": []}

def save_db(db):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"save_db error: {e}")

def get_user(user_id):
    db = load_db()
    return db["users"].get(str(user_id))

def save_user(user_id, data):
    db = load_db()
    db["users"][str(user_id)] = data
    save_db(db)

def save_ad(ad_data):
    db = load_db()
    ad_data["ad_id"] = len(db["ads"]) + 1
    db["ads"].append(ad_data)
    save_db(db)
    return ad_data["ad_id"]

def get_user_ads(user_id):
    db = load_db()
    return [a for a in db["ads"] if str(a.get("user_id")) == str(user_id)]

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
    
    if user and user.get("status") == "active":
        states[user_id] = {"step": "menu"}
        await update.message.reply_text(
            f"🏅 Добро пожаловать в KAZMETALS!\n\n"
            f"👤 {user.get('first_name','')} {user.get('last_name','')}\n"
            f"🔗 @{user.get('username','')}\n\n"
            f"Выберите действие 👇",
            reply_markup=main_menu_keyboard()
        )
    else:
        states[user_id] = {"step": "start"}
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Начать регистрацию", callback_data="show_rules")]
        ])
        await update.message.reply_text(
            "🏅 Добро пожаловать в KAZMETALS!\n\n"
            "Мы — закрытая платформа аукционов и продаж драгоценных металлов в Казахстане.\n\n"
            "📦 Категории:\n"
            "• 🪙 Монеты\n• 🥇 Слитки\n• 💍 Ювелирные изделия\n"
            "• 💎 Камни\n• ⌚ Часы\n• 📈 Инвестиции\n• 💬 Обсуждения\n\n"
            "Нажмите кнопку чтобы начать 👇",
            reply_markup=keyboard
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

    # РЕГИСТРАЦИЯ
    if data == "show_rules":
        states[user_id] = {"step": "rules"}
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Принимаю правила", callback_data="accept_rules"),
             InlineKeyboardButton("❌ Отказаться", callback_data="decline_rules")]
        ])
        await context.bot.send_message(chat_id=chat_id,
            text="📋 Правила платформы KAZMETALS\n\n"
                 "1️⃣ Все участники проходят верификацию\n"
                 "2️⃣ Запрещено размещать поддельные лоты\n"
                 "3️⃣ Продавец несёт ответственность за достоверность описания\n"
                 "4️⃣ Сделки совершаются между участниками напрямую\n"
                 "5️⃣ При нарушении правил аккаунт блокируется\n"
                 "6️⃣ Ваши данные хранятся конфиденциально\n\n"
                 "Принимаете правила платформы?",
            reply_markup=keyboard)
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
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_username"),
             InlineKeyboardButton("✏️ Изменить в Telegram", callback_data="change_username")]
        ])
        await context.bot.send_message(chat_id=chat_id,
            text=f"🔗 Проверка username\n\nВаш Telegram ник: @{username}\n\nВсё верно?",
            reply_markup=keyboard)
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
            "user_id": user_id,
            "first_name": u.get("first_name", ""),
            "last_name": u.get("last_name", ""),
            "username": u.get("username", username),
            "phone": u.get("phone", ""),
            "status": "active",
            "registered_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "subscriptions": [],
            "rating": 0,
            "reviews_count": 0
        }
        save_user(user_id, user_data)
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID,
            text=f"🆕 Новый участник — KAZMETALS\n\n"
                 f"👤 Имя: {u.get('first_name','')} {u.get('last_name','')}\n"
                 f"🔗 Username: @{u.get('username', username)}\n"
                 f"📞 Телефон: {u.get('phone','')}\n"
                 f"🆔 Telegram ID: {user_id}")
        states[user_id] = {"step": "approved"}
        await context.bot.send_message(chat_id=chat_id,
            text="🎉 Добро пожаловать в KAZMETALS!\n\nВаша заявка принята.\n\nНажмите кнопку чтобы вступить в группу 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔓 Вступить в KAZMETALS", url=GROUP_LINK)]
            ]))
        await context.bot.send_message(chat_id=chat_id,
            text="После вступления напишите /start чтобы открыть главное меню 👇")
        return

    # ГЛАВНОЕ МЕНЮ
    if data == "menu_ad":
        states[user_id] = {"step": "ad_type"}
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 Продаю", callback_data="ad_sell"),
             InlineKeyboardButton("🔵 Покупаю", callback_data="ad_buy")]
        ])
        await context.bot.send_message(chat_id=chat_id,
            text="➕ Подать объявление\n\nВыберите тип:", reply_markup=keyboard)
        return

    if data in ["ad_sell", "ad_buy"]:
        ad_type = "Продаю" if data == "ad_sell" else "Покупаю"
        states[user_id] = {"step": "ad_category", "ad_type": ad_type}
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(cat, callback_data=f"cat_{i}")] for i, cat in enumerate(CATEGORIES)] +
            [[InlineKeyboardButton("🔙 Назад", callback_data="menu_ad")]]
        )
        await context.bot.send_message(chat_id=chat_id,
            text="Выберите категорию:", reply_markup=keyboard)
        return

    if data.startswith("cat_"):
        idx = int(data.replace("cat_", ""))
        cat = CATEGORIES[idx]
        states[user_id]["category"] = cat
        states[user_id]["step"] = "ad_description"
        await context.bot.send_message(chat_id=chat_id,
            text=f"Категория: {cat}\n\nВведите описание товара:")
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
            if u.get("photo_id"):
                msg = await context.bot.send_photo(
                    chat_id=GROUP_ID, photo=u["photo_id"], caption=ad_text,
                    message_thread_id=topic_id,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("💬 Хочу купить", callback_data=f"buy_{user_id}")
                    ]]))
            else:
                msg = await context.bot.send_message(
                    chat_id=GROUP_ID, text=ad_text,
                    message_thread_id=topic_id,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("💬 Хочу купить", callback_data=f"buy_{user_id}")
                    ]]))
            ad_id = save_ad({
                "user_id": user_id, "username": uname,
                "type": u.get("ad_type"), "category": u.get("category"),
                "description": u.get("description"), "price": u.get("price"),
                "photo_id": u.get("photo_id", ""), "status": "active",
                "created_at": datetime.now().strftime("%d.%m.%Y %H:%M")
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
            text="❌ Объявление отменено.", reply_markup=main_menu_keyboard())
        return

    if data.startswith("buy_"):
        seller_id = data.replace("buy_", "")
        buyer_id = user_id
        if seller_id == buyer_id:
            await query.answer("Это ваше объявление!", show_alert=True)
            return
        seller = get_user(seller_id)
        buyer = get_user(buyer_id)
        seller_username = seller.get("username", "") if seller else ""
        buyer_username = buyer.get("username", username) if buyer else username
        await context.bot.send_message(chat_id=int(buyer_id),
            text=f"✅ Свяжитесь с продавцом:\n👉 @{seller_username}\n\nЧерез 7 дней придёт запрос на оценку сделки.")
        await context.bot.send_message(chat_id=int(seller_id),
            text=f"🔔 Покупатель заинтересован!\n👉 @{buyer_username}\n\nЧерез 7 дней придёт запрос на оценку сделки.")
        return

    # ПОДПИСКИ
    if data == "menu_subs":
        user = get_user(user_id)
        subs = user.get("subscriptions", []) if user else []
        if isinstance(subs, str):
            subs = subs.split(",") if subs else []
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

    if data.startswith("sub_") and not data == "sub_save":
        cat = data.replace("sub_", "")
        if "subs" not in states.get(user_id, {}):
            states[user_id] = {"step": "subs", "subs": []}
        subs = states[user_id]["subs"]
        if cat in subs:
            subs.remove(cat)
        else:
            subs.append(cat)
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
            user["subscriptions"] = subs
            save_user(user_id, user)
        await context.bot.send_message(chat_id=chat_id,
            text=f"✅ Подписки сохранены!\n\n{', '.join(subs) if subs else 'Нет активных подписок'}",
            reply_markup=main_menu_keyboard())
        return

    # РЕПУТАЦИЯ
    if data == "menu_rep":
        user = get_user(user_id)
        rating = user.get("rating", 0) if user else 0
        reviews = user.get("reviews_count", 0) if user else 0
        stars = "⭐" * int(float(rating)) if float(rating) > 0 else "нет оценок"
        await context.bot.send_message(chat_id=chat_id,
            text=f"⭐ Моя репутация\n\n"
                 f"Рейтинг: {stars} ({rating})\n"
                 f"Отзывов: {reviews}\n\n"
                 f"Репутация формируется после завершённых сделок.",
            reply_markup=main_menu_keyboard())
        return

    # ПРОФИЛЬ
    if data == "menu_profile":
        user = get_user(user_id)
        if user:
            ads = get_user_ads(user_id)
            active = [a for a in ads if a.get("status") == "active"]
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Мои объявления", callback_data="my_ads")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
            ])
            await context.bot.send_message(chat_id=chat_id,
                text=f"👤 Мой профиль\n\n"
                     f"Имя: {user.get('first_name','')} {user.get('last_name','')}\n"
                     f"Username: @{user.get('username','')}\n"
                     f"Телефон: {user.get('phone','')}\n"
                     f"✅ Статус: Верифицирован\n"
                     f"📅 Регистрация: {user.get('registered_at','')}\n"
                     f"📢 Активных объявлений: {len(active)}\n"
                     f"⭐ Рейтинг: {user.get('rating',0)} (отзывов: {user.get('reviews_count',0)})",
                reply_markup=keyboard)
        return

    if data == "my_ads":
        ads = get_user_ads(user_id)
        active = [a for a in ads if a.get("status") == "active"]
        text = f"📢 Мои объявления\n\n🟢 Активных: {len(active)}\n\n"
        for a in active[:5]:
            text += f"#{a.get('ad_id')} {a.get('type')} {a.get('category')} — {a.get('price')}\n"
        await context.bot.send_message(chat_id=chat_id, text=text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu_profile")]]))
        return

    # КАК РАБОТАЕТ
    if data == "menu_how":
        await context.bot.send_message(chat_id=chat_id,
            text="📋 Как работает KAZMETALS\n\n"
                 "1️⃣ Подайте объявление через бота\n\n"
                 "2️⃣ Объявление появляется в группе автоматически\n\n"
                 "3️⃣ Покупатель нажимает Хочу купить\n\n"
                 "4️⃣ Вы получаете контакт и договариваетесь напрямую\n\n"
                 "5️⃣ Через 7 дней оба получают запрос на оценку\n\n"
                 "⚠️ KAZMETALS — информационный посредник.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"📞 @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
            ]))
        return

    # АДМИНИСТРАТОР
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
            await update.message.reply_text("Напишите /start чтобы начать регистрацию.")
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
        states[user_id]["step"] = "ad_preview"
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
        states[user_id]["step"] = "ad_preview"
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
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    logger.info("KAZMETALS Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
