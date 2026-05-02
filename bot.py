import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "8537953320:AAHg9tebajVFYkWs-hiWwaPGYQEzP0D9c1I")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "612230948"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "ras_0104")
GROUP_LINK = os.environ.get("GROUP_LINK", "https://t.me/+_pgc-XiGz7E4YTUy")
GROUP_ID = int(os.environ.get("GROUP_ID", "-1003852395090"))
SHEET_ID = os.environ.get("SHEET_ID", "15sD9r_UBrW9o4gZ_2qnSP8mThkAmyyeNcwFwVicVcaY")

GOOGLE_CREDS = json.loads(os.environ.get("GOOGLE_CREDS", "{}"))

CATEGORIES = ["🪙 Монеты", "🥇 Слитки", "💍 Ювелирные изделия", "💎 Камни", "⌚ Часы"]
CATEGORY_TOPICS = {
    "🪙 Монеты": os.environ.get("TOPIC_COINS", ""),
    "🥇 Слитки": os.environ.get("TOPIC_BARS", ""),
    "💍 Ювелирные изделия": os.environ.get("TOPIC_JEWELRY", ""),
    "💎 Камни": os.environ.get("TOPIC_STONES", ""),
    "⌚ Часы": os.environ.get("TOPIC_WATCHES", ""),
}

# Google Sheets
def get_sheet():
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(GOOGLE_CREDS, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)

def init_sheets():
    try:
        sh = get_sheet()
        existing = [w.title for w in sh.worksheets()]
        sheets_needed = {
            "Users": ["user_id", "first_name", "last_name", "username", "phone", "status", "registered_at", "subscriptions", "rating", "reviews_count"],
            "Ads": ["ad_id", "user_id", "username", "type", "category", "description", "price", "photo_id", "status", "created_at", "message_id"],
            "Deals": ["deal_id", "seller_id", "buyer_id", "ad_id", "created_at", "notified", "seller_rated", "buyer_rated"],
            "Reviews": ["review_id", "from_user", "to_user", "rating", "comment", "created_at"]
        }
        for name, headers in sheets_needed.items():
            if name not in existing:
                ws = sh.add_worksheet(title=name, rows=1000, cols=len(headers))
                ws.append_row(headers)
        logger.info("Sheets initialized")
    except Exception as e:
        logger.error(f"Sheet init error: {e}")

def get_user(user_id):
    try:
        sh = get_sheet()
        ws = sh.worksheet("Users")
        records = ws.get_all_records()
        for i, r in enumerate(records):
            if str(r.get("user_id")) == str(user_id):
                return r, i + 2
        return None, None
    except Exception as e:
        logger.error(f"get_user error: {e}")
        return None, None

def save_user(data):
    try:
        sh = get_sheet()
        ws = sh.worksheet("Users")
        user, row = get_user(data["user_id"])
        row_data = [
            str(data.get("user_id", "")),
            data.get("first_name", ""),
            data.get("last_name", ""),
            data.get("username", ""),
            data.get("phone", ""),
            data.get("status", "active"),
            data.get("registered_at", datetime.now().strftime("%d.%m.%Y %H:%M")),
            data.get("subscriptions", ""),
            data.get("rating", "0"),
            data.get("reviews_count", "0")
        ]
        if user and row:
            ws.update(f"A{row}:J{row}", [row_data])
        else:
            ws.append_row(row_data)
        return True
    except Exception as e:
        logger.error(f"save_user error: {e}")
        return False

def save_ad(data):
    try:
        sh = get_sheet()
        ws = sh.worksheet("Ads")
        records = ws.get_all_records()
        ad_id = len(records) + 1
        row_data = [
            ad_id,
            str(data.get("user_id", "")),
            data.get("username", ""),
            data.get("type", ""),
            data.get("category", ""),
            data.get("description", ""),
            data.get("price", ""),
            data.get("photo_id", ""),
            "active",
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            data.get("message_id", "")
        ]
        ws.append_row(row_data)
        return ad_id
    except Exception as e:
        logger.error(f"save_ad error: {e}")
        return None

def get_user_ads(user_id):
    try:
        sh = get_sheet()
        ws = sh.worksheet("Ads")
        records = ws.get_all_records()
        return [r for r in records if str(r.get("user_id")) == str(user_id)]
    except:
        return []

def save_deal(seller_id, buyer_id, ad_id):
    try:
        sh = get_sheet()
        ws = sh.worksheet("Deals")
        records = ws.get_all_records()
        deal_id = len(records) + 1
        ws.append_row([deal_id, str(seller_id), str(buyer_id), str(ad_id),
                       datetime.now().strftime("%d.%m.%Y %H:%M"), "no", "no", "no"])
        return deal_id
    except Exception as e:
        logger.error(f"save_deal error: {e}")
        return None

def get_pending_reviews():
    try:
        sh = get_sheet()
        ws = sh.worksheet("Deals")
        records = ws.get_all_records()
        week_ago = datetime.now() - timedelta(days=7)
        pending = []
        for i, r in enumerate(records):
            if r.get("notified") == "no":
                try:
                    deal_date = datetime.strptime(r["created_at"], "%d.%m.%Y %H:%M")
                    if deal_date <= week_ago:
                        pending.append((r, i + 2))
                except:
                    pass
        return pending
    except:
        return []

def mark_deal_notified(row):
    try:
        sh = get_sheet()
        ws = sh.worksheet("Deals")
        ws.update_cell(row, 6, "yes")
    except:
        pass

def save_review(from_user, to_user, rating, comment):
    try:
        sh = get_sheet()
        ws = sh.worksheet("Reviews")
        records = ws.get_all_records()
        review_id = len(records) + 1
        ws.append_row([review_id, str(from_user), str(to_user), rating, comment,
                       datetime.now().strftime("%d.%m.%Y %H:%M")])
        # Обновляем рейтинг
        user_reviews = [r for r in ws.get_all_records() if str(r.get("to_user")) == str(to_user)]
        if user_reviews:
            avg = sum(int(r["rating"]) for r in user_reviews) / len(user_reviews)
            user, row = get_user(to_user)
            if user and row:
                u_sh = get_sheet()
                u_ws = u_sh.worksheet("Users")
                u_ws.update_cell(row, 9, round(avg, 1))
                u_ws.update_cell(row, 10, len(user_reviews))
        return True
    except Exception as e:
        logger.error(f"save_review error: {e}")
        return False

def get_user_reviews(user_id):
    try:
        sh = get_sheet()
        ws = sh.worksheet("Reviews")
        records = ws.get_all_records()
        return [r for r in records if str(r.get("to_user")) == str(user_id)]
    except:
        return []

# Состояния
users_state = {}

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
    user, _ = get_user(user_id)
    if user and user.get("status") == "active":
        users_state[user_id] = {"step": "menu"}
        await update.message.reply_text(
            f"🏅 Добро пожаловать в KAZMETALS!\n\n"
            f"👤 {user.get('first_name','')} {user.get('last_name','')}\n"
            f"🔗 @{user.get('username','')}\n\n"
            f"Выберите действие 👇",
            reply_markup=main_menu_keyboard()
        )
    else:
        users_state[user_id] = {"step": "start"}
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

    # ── РЕГИСТРАЦИЯ ──
    if data == "show_rules":
        users_state[user_id] = {"step": "rules"}
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
        users_state.pop(user_id, None)
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
        users_state[user_id] = {"step": "confirm_username", "username": username}
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
        users_state[user_id]["step"] = "awaiting_first_name"
        await context.bot.send_message(chat_id=chat_id,
            text="👤 Шаг 1 из 3 — Имя\n\nВведите ваше имя:")
        return

    if data == "send_application":
        u = users_state.get(user_id, {})
        save_user({
            "user_id": user_id,
            "first_name": u.get("first_name", ""),
            "last_name": u.get("last_name", ""),
            "username": u.get("username", username),
            "phone": u.get("phone", ""),
            "status": "active",
            "registered_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "subscriptions": "",
            "rating": "0",
            "reviews_count": "0"
        })
        admin_text = (
            f"🆕 Новый участник — KAZMETALS\n\n"
            f"👤 Имя: {u.get('first_name','')} {u.get('last_name','')}\n"
            f"🔗 Username: @{u.get('username', username)}\n"
            f"📞 Телефон: {u.get('phone','')}\n"
            f"🆔 Telegram ID: {user_id}"
        )
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)
        users_state[user_id]["step"] = "approved"
        await context.bot.send_message(chat_id=chat_id,
            text="🎉 Добро пожаловать в KAZMETALS!\n\nВаша заявка принята.\n\nНажмите кнопку чтобы вступить в группу 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔓 Вступить в KAZMETALS", url=GROUP_LINK)]
            ]))
        await context.bot.send_message(chat_id=chat_id,
            text="После вступления в группу напишите /start чтобы открыть главное меню 👇")
        return

    # ── ГЛАВНОЕ МЕНЮ ──
    if data == "menu_ad":
        users_state[user_id] = {"step": "ad_type"}
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 Продаю", callback_data="ad_type_sell"),
             InlineKeyboardButton("🔵 Покупаю", callback_data="ad_type_buy")]
        ])
        await context.bot.send_message(chat_id=chat_id,
            text="➕ Подать объявление\n\nВыберите тип:", reply_markup=keyboard)
        return

    if data in ["ad_type_sell", "ad_type_buy"]:
        ad_type = "Продаю" if data == "ad_type_sell" else "Покупаю"
        users_state[user_id] = {"step": "ad_category", "ad_type": ad_type}
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(cat, callback_data=f"ad_cat_{i}")] for i, cat in enumerate(CATEGORIES)
        ] + [[InlineKeyboardButton("🔙 Назад", callback_data="menu_ad")]])
        await context.bot.send_message(chat_id=chat_id,
            text="Выберите категорию:", reply_markup=keyboard)
        return

    if data.startswith("ad_cat_"):
        idx = int(data.replace("ad_cat_", ""))
        cat = CATEGORIES[idx]
        users_state[user_id]["category"] = cat
        users_state[user_id]["step"] = "ad_description"
        await context.bot.send_message(chat_id=chat_id,
            text=f"Категория: {cat}\n\nВведите описание товара:\n(например: Золотая монета СССР 1980г, 8.6г)")
        return

    if data == "ad_confirm":
        u = users_state.get(user_id, {})
        user_data, _ = get_user(user_id)
        uname = user_data.get("username", username) if user_data else username
        ad_type = u.get("ad_type", "")
        category = u.get("category", "")
        description = u.get("description", "")
        price = u.get("price", "")
        photo_id = u.get("photo_id", "")
        emoji = "🟢" if ad_type == "Продаю" else "🔵"

        ad_text = (
            f"{emoji} {ad_type.upper()}\n"
            f"{category}\n\n"
            f"{description}\n\n"
            f"💰 {price}\n"
            f"👤 @{uname}"
        )

        topic_id = CATEGORY_TOPICS.get(category)
        try:
            if photo_id:
                if topic_id:
                    msg = await context.bot.send_photo(chat_id=GROUP_ID, photo=photo_id,
                        caption=ad_text, message_thread_id=int(topic_id),
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("💬 Хочу купить", callback_data=f"buy_{user_id}_0")
                        ]]))
                else:
                    msg = await context.bot.send_photo(chat_id=GROUP_ID, photo=photo_id,
                        caption=ad_text,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("💬 Хочу купить", callback_data=f"buy_{user_id}_0")
                        ]]))
            else:
                if topic_id:
                    msg = await context.bot.send_message(chat_id=GROUP_ID, text=ad_text,
                        message_thread_id=int(topic_id),
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("💬 Хочу купить", callback_data=f"buy_{user_id}_0")
                        ]]))
                else:
                    msg = await context.bot.send_message(chat_id=GROUP_ID, text=ad_text,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("💬 Хочу купить", callback_data=f"buy_{user_id}_0")
                        ]]))

            ad_id = save_ad({
                "user_id": user_id, "username": uname, "type": ad_type,
                "category": category, "description": description,
                "price": price, "photo_id": photo_id, "message_id": msg.message_id
            })

            # Уведомляем подписчиков
            await notify_subscribers(context, category, ad_text, user_id)

            await context.bot.send_message(chat_id=chat_id,
                text=f"✅ Объявление опубликовано в группе!\n\nНомер объявления: #{ad_id}",
                reply_markup=main_menu_keyboard())
        except Exception as e:
            logger.error(f"Publish error: {e}")
            await context.bot.send_message(chat_id=chat_id,
                text=f"⚠️ Ошибка публикации: {e}\n\nПроверьте что бот является администратором группы.",
                reply_markup=main_menu_keyboard())
        return

    if data == "ad_cancel":
        users_state[user_id] = {"step": "menu"}
        await context.bot.send_message(chat_id=chat_id,
            text="❌ Объявление отменено.", reply_markup=main_menu_keyboard())
        return

    if data.startswith("buy_"):
        parts = data.split("_")
        seller_id = parts[1]
        buyer_id = user_id
        if seller_id == buyer_id:
            await query.answer("Это ваше объявление!", show_alert=True)
            return
        seller_data, _ = get_user(seller_id)
        seller_username = seller_data.get("username", "") if seller_data else ""
        buyer_data, _ = get_user(buyer_id)
        buyer_username = buyer_data.get("username", username) if buyer_data else username
        save_deal(seller_id, buyer_id, 0)
        await context.bot.send_message(chat_id=int(buyer_id),
            text=f"✅ Отлично! Свяжитесь с продавцом напрямую:\n👉 @{seller_username}\n\nЧерез 7 дней вам придёт запрос на оценку сделки.")
        await context.bot.send_message(chat_id=int(seller_id),
            text=f"🔔 Покупатель заинтересован в вашем объявлении!\n👉 @{buyer_username}\n\nЧерез 7 дней вам придёт запрос на оценку сделки.")
        return

    # ── ПОДПИСКИ ──
    if data == "menu_subs":
        user_data, _ = get_user(user_id)
        subs = user_data.get("subscriptions", "").split(",") if user_data and user_data.get("subscriptions") else []
        keyboard = []
        for cat in CATEGORIES:
            check = "☑️" if cat in subs else "☐"
            keyboard.append([InlineKeyboardButton(f"{check} {cat}", callback_data=f"sub_toggle_{cat}")])
        keyboard.append([InlineKeyboardButton("💾 Сохранить подписки", callback_data="sub_save")])
        keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")])
        users_state[user_id] = {"step": "subs", "subs": subs}
        await context.bot.send_message(chat_id=chat_id,
            text="🔔 Мои подписки\n\nВыберите категории для уведомлений о новых объявлениях:",
            reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("sub_toggle_"):
        cat = data.replace("sub_toggle_", "")
        if "subs" not in users_state.get(user_id, {}):
            users_state[user_id] = {"step": "subs", "subs": []}
        subs = users_state[user_id]["subs"]
        if cat in subs:
            subs.remove(cat)
        else:
            subs.append(cat)
        users_state[user_id]["subs"] = subs
        keyboard = []
        for c in CATEGORIES:
            check = "☑️" if c in subs else "☐"
            keyboard.append([InlineKeyboardButton(f"{check} {c}", callback_data=f"sub_toggle_{c}")])
        keyboard.append([InlineKeyboardButton("💾 Сохранить подписки", callback_data="sub_save")])
        keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "sub_save":
        subs = users_state.get(user_id, {}).get("subs", [])
        user_data, _ = get_user(user_id)
        if user_data:
            user_data["subscriptions"] = ",".join(subs)
            save_user(user_data)
        await context.bot.send_message(chat_id=chat_id,
            text=f"✅ Подписки сохранены!\n\nВы подписаны на: {', '.join(subs) if subs else 'ничего'}",
            reply_markup=main_menu_keyboard())
        return

    # ── РЕПУТАЦИЯ ──
    if data == "menu_rep":
        user_data, _ = get_user(user_id)
        rating = user_data.get("rating", "0") if user_data else "0"
        reviews_count = user_data.get("reviews_count", "0") if user_data else "0"
        reviews = get_user_reviews(user_id)
        stars = "⭐" * int(float(rating)) if float(rating) > 0 else "нет оценок"
        text = (
            f"⭐ Моя репутация\n\n"
            f"Рейтинг: {stars} ({rating})\n"
            f"Отзывов: {reviews_count}\n\n"
        )
        if reviews:
            text += "📋 Последние отзывы:\n\n"
            for r in reviews[-3:]:
                text += f"⭐ {r.get('rating',0)} — {r.get('comment','')}\n"
        else:
            text += "Репутация формируется после завершённых сделок.\nПокупатели и продавцы оставляют друг другу отзывы."
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=main_menu_keyboard())
        return

    # ── ПРОФИЛЬ ──
    if data == "menu_profile":
        user_data, _ = get_user(user_id)
        if user_data:
            ads = get_user_ads(user_id)
            active_ads = [a for a in ads if a.get("status") == "active"]
            text = (
                f"👤 Мой профиль\n\n"
                f"Имя: {user_data.get('first_name','')} {user_data.get('last_name','')}\n"
                f"Username: @{user_data.get('username','')}\n"
                f"Телефон: {user_data.get('phone','')}\n"
                f"✅ Статус: Верифицирован\n"
                f"📅 Регистрация: {user_data.get('registered_at','')}\n"
                f"📢 Активных объявлений: {len(active_ads)}\n"
                f"⭐ Рейтинг: {user_data.get('rating','0')} (отзывов: {user_data.get('reviews_count','0')})"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Мои объявления", callback_data="my_ads")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
            ])
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        return

    if data == "my_ads":
        ads = get_user_ads(user_id)
        active = [a for a in ads if a.get("status") == "active"]
        archive = [a for a in ads if a.get("status") != "active"]
        text = f"📢 Мои объявления\n\n🟢 Активных: {len(active)}\n📦 В архиве: {len(archive)}\n\n"
        for a in active[:5]:
            text += f"#{a.get('ad_id')} {a.get('type')} {a.get('category')} — {a.get('price')}\n"
        await context.bot.send_message(chat_id=chat_id, text=text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu_profile")]]))
        return

    # ── КАК ЭТО РАБОТАЕТ ──
    if data == "menu_how":
        await context.bot.send_message(chat_id=chat_id,
            text="📋 Как работает KAZMETALS\n\n"
                 "1️⃣ Подайте объявление через бота в нужную категорию\n\n"
                 "2️⃣ Объявление появляется в группе KAZ_METALS автоматически\n\n"
                 "3️⃣ Покупатель нажимает Хочу купить под объявлением\n\n"
                 "4️⃣ Вы получаете контакт покупателя и договариваетесь напрямую\n\n"
                 "5️⃣ Через 7 дней после сделки оба получают запрос на оценку\n\n"
                 "⚠️ KAZMETALS — информационный посредник.\n"
                 "Мы не несём ответственности за сделки между участниками.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"📞 Написать администратору @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
            ]))
        return

    # ── АДМИНИСТРАТОР ──
    if data == "menu_admin":
        await context.bot.send_message(chat_id=chat_id,
            text=f"📞 Связь с администратором\n\n"
                 f"По всем вопросам пишите напрямую:\n"
                 f"👉 @{ADMIN_USERNAME}\n\n"
                 f"Время работы:\n"
                 f"Пн-Пт 9:00 - 20:00 (Алматы)\n"
                 f"Сб-Вс 10:00 - 18:00 (Алматы)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✉️ Написать @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_menu")]
            ]))
        return

    if data == "back_menu":
        users_state[user_id] = {"step": "menu"}
        await context.bot.send_message(chat_id=chat_id,
            text="🏅 Главное меню KAZMETALS", reply_markup=main_menu_keyboard())
        return

    # ── ОЦЕНКА СДЕЛКИ ──
    if data.startswith("rate_"):
        parts = data.split("_")
        deal_id = parts[1]
        partner_id = parts[2]
        rating = parts[3]
        users_state[user_id] = {"step": "review_comment", "deal_id": deal_id,
                                  "partner_id": partner_id, "rating": rating}
        await context.bot.send_message(chat_id=chat_id,
            text=f"Вы поставили оценку: {'⭐' * int(rating)}\n\nНапишите короткий отзыв (или отправьте - чтобы пропустить):")
        return

async def notify_subscribers(context, category, ad_text, exclude_user_id):
    try:
        sh = get_sheet()
        ws = sh.worksheet("Users")
        records = ws.get_all_records()
        for user in records:
            uid = str(user.get("user_id", ""))
            if uid == str(exclude_user_id):
                continue
            subs = user.get("subscriptions", "")
            if category in subs:
                try:
                    await context.bot.send_message(
                        chat_id=int(uid),
                        text=f"🔔 Новое объявление в категории {category}:\n\n{ad_text}"
                    )
                except:
                    pass
    except Exception as e:
        logger.error(f"notify_subscribers error: {e}")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    text = update.message.text.strip()

    if user_id not in users_state:
        user, _ = get_user(user_id)
        if user:
            users_state[user_id] = {"step": "menu"}
        else:
            await update.message.reply_text("Напишите /start чтобы начать регистрацию.")
            return

    step = users_state[user_id].get("step")

    # Регистрация
    if step == "awaiting_first_name":
        users_state[user_id]["first_name"] = text
        users_state[user_id]["step"] = "awaiting_last_name"
        await update.message.reply_text(f"👍 Имя {text} сохранено!\n\n👤 Шаг 2 из 3 — Фамилия\n\nВведите вашу фамилию:")
        return

    if step == "awaiting_last_name":
        users_state[user_id]["last_name"] = text
        users_state[user_id]["step"] = "awaiting_phone"
        await update.message.reply_text(
            f"👍 Фамилия {text} сохранена!\n\n📞 Шаг 3 из 3 — Номер телефона\n\nВведите номер в формате:\n+77001234567",
            reply_markup=ReplyKeyboardRemove())
        return

    if step == "awaiting_phone":
        users_state[user_id]["phone"] = text
        users_state[user_id]["step"] = "confirm_application"
        u = users_state[user_id]
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📨 Отправить заявку на вступление в канал", callback_data="send_application")]
        ])
        await update.message.reply_text(
            "🏁 Регистрация завершена!\n\n"
            "Все ваши данные собраны:\n\n"
            f"👤 Имя: {u.get('first_name','')} {u.get('last_name','')}\n"
            f"🔗 Username: @{u.get('username','')}\n"
            f"📞 Телефон: {text}\n\n"
            "Нажмите кнопку чтобы вступить в KAZMETALS 👇",
            reply_markup=keyboard)
        return

    # Объявление — описание
    if step == "ad_description":
        users_state[user_id]["description"] = text
        users_state[user_id]["step"] = "ad_price"
        await update.message.reply_text("💰 Введите цену:\n(например: 150 000 KZT или торг)")
        return

    # Объявление — цена
    if step == "ad_price":
        users_state[user_id]["price"] = text
        users_state[user_id]["step"] = "ad_photo"
        await update.message.reply_text("📸 Отправьте фото товара\n(или напишите - чтобы пропустить)")
        return

    # Объявление — пропуск фото
    if step == "ad_photo" and text == "-":
        users_state[user_id]["photo_id"] = ""
        await show_ad_preview(update, context, user_id)
        return

    # Отзыв
    if step == "review_comment":
        partner_id = users_state[user_id].get("partner_id")
        rating = users_state[user_id].get("rating")
        comment = "" if text == "-" else text
        save_review(user_id, partner_id, rating, comment)
        await update.message.reply_text(
            "✅ Спасибо за отзыв! Ваша оценка сохранена.",
            reply_markup=main_menu_keyboard())
        users_state[user_id] = {"step": "menu"}
        return

    await update.message.reply_text("Следуйте инструкциям бота. Если что-то пошло не так — напишите /start")

async def show_ad_preview(update, context, user_id):
    u = users_state.get(user_id, {})
    chat_id = update.message.chat_id if update.message else update.callback_query.message.chat_id
    emoji = "🟢" if u.get("ad_type") == "Продаю" else "🔵"
    preview = (
        f"📋 Предварительный просмотр:\n\n"
        f"{emoji} {u.get('ad_type','').upper()}\n"
        f"{u.get('category','')}\n\n"
        f"{u.get('description','')}\n\n"
        f"💰 {u.get('price','')}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Опубликовать", callback_data="ad_confirm"),
         InlineKeyboardButton("❌ Отменить", callback_data="ad_cancel")]
    ])
    await context.bot.send_message(chat_id=chat_id, text=preview, reply_markup=keyboard)

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    user_id = str(update.message.from_user.id)
    step = users_state.get(user_id, {}).get("step")
    if step == "ad_photo":
        photo_id = update.message.photo[-1].file_id
        users_state[user_id]["photo_id"] = photo_id
        users_state[user_id]["step"] = "ad_confirm"
        await show_ad_preview(update, context, user_id)

async def check_reviews(context: ContextTypes.DEFAULT_TYPE):
    pending = get_pending_reviews()
    for deal, row in pending:
        seller_id = deal.get("seller_id")
        buyer_id = deal.get("buyer_id")
        seller_data, _ = get_user(seller_id)
        buyer_data, _ = get_user(buyer_id)
        seller_username = seller_data.get("username", "") if seller_data else ""
        buyer_username = buyer_data.get("username", "") if buyer_data else ""

        rating_keyboard = lambda uid, partner_id: InlineKeyboardMarkup([[
            InlineKeyboardButton(f"{i}⭐", callback_data=f"rate_{deal['deal_id']}_{partner_id}_{i}")
            for i in range(1, 6)
        ]])

        try:
            await context.bot.send_message(
                chat_id=int(seller_id),
                text=f"⭐ Оцените сделку с @{buyer_username}\n\nВыберите оценку:",
                reply_markup=rating_keyboard(seller_id, buyer_id)
            )
        except:
            pass

        try:
            await context.bot.send_message(
                chat_id=int(buyer_id),
                text=f"⭐ Оцените сделку с @{seller_username}\n\nВыберите оценку:",
                reply_markup=rating_keyboard(buyer_id, seller_id)
            )
        except:
            pass

        mark_deal_notified(row)

def main():
    init_sheets()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Проверка отзывов каждые 6 часов
    job_queue = app.job_queue
    job_queue.run_repeating(check_reviews, interval=21600, first=60)

    logger.info("KAZMETALS Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
