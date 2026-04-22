import os
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "8537953320:AAHg9tebajVFYkWs-hiWwaPGYQEzP0D9c1I")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "612230948"))
GROUP_ID = int(os.environ.get("GROUP_ID", "-3852395090"))
GROUP_LINK = os.environ.get("GROUP_LINK", "https://t.me/+_pgc-XiGz7E4YTUy")

users = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    users[user_id] = {"step": "start"}
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Начать регистрацию", callback_data="show_rules")]
    ])
    await update.message.reply_text(
        "🏅 *Добро пожаловать в KAZMETALS!*\n\n"
        "Мы — закрытая платформа аукционов и продаж драгоценных металлов в Казахстане.\n\n"
        "📦 *Категории:*\n"
        "• 🪙 Монеты\n"
        "• 🥇 Слитки\n"
        "• 💍 Ювелирные изделия\n"
        "• 💎 Камни\n"
        "• ⌚ Часы\n"
        "• 📈 Инвестиции\n"
        "• 💬 Обсуждения\n\n"
        "Нажмите кнопку чтобы начать 👇",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id
    username = query.from_user.username
    data = query.data

    if data == "show_rules":
        users[user_id] = {"step": "rules"}
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Принимаю правила", callback_data="accept_rules"),
                InlineKeyboardButton("❌ Отказаться", callback_data="decline_rules")
            ]
        ])
        await context.bot.send_message(
            chat_id=chat_id,
            text="📋 *Правила платформы KAZMETALS*\n\n"
                 "1️⃣ Все участники проходят верификацию\n"
                 "2️⃣ Запрещено размещать поддельные лоты\n"
                 "3️⃣ Продавец несёт ответственность за достоверность описания\n"
                 "4️⃣ Сделки совершаются между участниками напрямую\n"
                 "5️⃣ При нарушении правил аккаунт блокируется\n"
                 "6️⃣ Ваши данные хранятся конфиденциально\n\n"
                 "Принимаете правила платформы?",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return

    if data == "decline_rules":
        users.pop(user_id, None)
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Вы отказались от регистрации.\n\nЕсли передумаете — напишите /start 🙂"
        )
        return

    if data == "accept_rules":
        if not username:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ *У вас нет username в Telegram!*\n\n"
                     "Без него регистрация невозможна.\n\n"
                     "Как создать username:\n"
                     "1. Откройте *Настройки* в Telegram\n"
                     "2. Нажмите на ваше имя\n"
                     "3. Нажмите *Имя пользователя*\n"
                     "4. Придумайте @ник и сохраните\n"
                     "5. Вернитесь и напишите /start",
                parse_mode="Markdown"
            )
            return

        users[user_id] = {"step": "confirm_username", "username": username}
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_username"),
                InlineKeyboardButton("✏️ Изменить в Telegram", callback_data="change_username")
            ]
        ])
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔗 *Проверка username*\n\n"
                 f"Ваш Telegram ник: *@{username}*\n\n"
                 f"Всё верно?",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return

    if data == "change_username":
        await context.bot.send_message(
            chat_id=chat_id,
            text="✏️ *Как изменить username:*\n\n"
                 "1. Откройте *Настройки* в Telegram\n"
                 "2. Нажмите на ваше имя\n"
                 "3. Нажмите *Имя пользователя*\n"
                 "4. Измените @ник и сохраните\n"
                 "5. Вернитесь и напишите /start",
            parse_mode="Markdown"
        )
        return

    if data == "confirm_username":
        users[user_id]["step"] = "awaiting_first_name"
        await context.bot.send_message(
            chat_id=chat_id,
            text="👤 *Шаг 1 из 2 — Имя*\n\n"
                 "Введите ваше *имя*:",
            parse_mode="Markdown"
        )
        return

    if data == "send_application":
        u = users.get(user_id, {})
        admin_text = (
            f"🆕 *Новая заявка — KAZMETALS*\n\n"
            f"👤 *Имя:* {u.get('first_name','')} {u.get('last_name','')}\n"
            f"🔗 *Username:* @{u.get('username','')}\n"
            f"📞 *Телефон:* {u.get('phone','')}\n"
            f"🆔 *Telegram ID:* {user_id}"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{user_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{user_id}")
            ]
        ])
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        users[user_id]["step"] = "awaiting_approval"
        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ *Заявка отправлена!*\n\n"
                 "Ваша заявка передана администратору.\n"
                 "Обычно проверка занимает до 24 часов.\n\n"
                 "Как только вас одобрят — вы получите персональную ссылку для вступления в группу. ⏳",
            parse_mode="Markdown"
        )
        return

    if data.startswith("approve_"):
        target_id = int(data.replace("approve_", ""))
        try:
            invite = await context.bot.create_chat_invite_link(
                chat_id=GROUP_ID,
                member_limit=1,
                name=f"user_{target_id}"
            )
            users[str(target_id)]["step"] = "approved"
            await context.bot.send_message(
                chat_id=target_id,
                text="🎉 *Поздравляем! Регистрация завершена!*\n\n"
                     "Вы успешно верифицированы в KAZMETALS!\n\n"
                     "Нажмите кнопку ниже чтобы вступить в группу 👇",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔓 Вступить в KAZMETALS", url=invite.invite_link)]
                ])
            )
            await query.edit_message_text(
                text=query.message.text + "\n\n✅ *Одобрено! Ссылка отправлена.*",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"⚠️ Не удалось создать ссылку: {e}\n\nДобавьте вручную: {GROUP_LINK}"
            )
        return

    if data.startswith("reject_"):
        target_id = int(data.replace("reject_", ""))
        if str(target_id) in users:
            users[str(target_id)]["step"] = "rejected"
        await context.bot.send_message(
            chat_id=target_id,
            text="😔 *Ваша заявка отклонена.*\n\n"
                 "Администратор KAZMETALS отклонил вашу регистрацию.\n\n"
                 "Для повторной попытки: /start",
            parse_mode="Markdown"
        )
        await query.edit_message_text(
            text=query.message.text + "\n\n❌ *Отклонено. Пользователь уведомлён.*",
            parse_mode="Markdown"
        )
        return

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    text = update.message.text.strip()

    if user_id not in users:
        await update.message.reply_text("Напишите /start чтобы начать регистрацию.")
        return

    step = users[user_id].get("step")

    if step == "awaiting_first_name":
        users[user_id]["first_name"] = text
        users[user_id]["step"] = "awaiting_last_name"
        await update.message.reply_text(
            f"👍 Имя *{text}* сохранено!\n\n"
            "👤 *Шаг 2 из 2 — Фамилия*\n\n"
            "Введите вашу фамилию:",
            parse_mode="Markdown"
        )
        return

    if step == "awaiting_last_name":
        users[user_id]["last_name"] = text
        users[user_id]["step"] = "awaiting_phone"
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("📲 Поделиться номером телефона", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await update.message.reply_text(
            f"👍 Фамилия *{text}* сохранена!\n\n"
            "📞 *Номер телефона*\n\n"
            "Нажмите кнопку ниже чтобы поделиться номером 👇",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return

    await update.message.reply_text(
        "Пожалуйста следуйте инструкциям. Если что-то пошло не так — напишите /start"
    )

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    phone = update.message.contact.phone_number

    if user_id not in users:
        await update.message.reply_text("Пожалуйста начните регистрацию заново: /start")
        return

    users[user_id]["phone"] = phone
    users[user_id]["step"] = "confirm_application"
    u = users[user_id]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📨 Отправить заявку на вступление", callback_data="send_application")]
    ])

    await update.message.reply_text(
        "🏁 *Регистрация завершена!*\n\n"
        "Все ваши данные собраны:\n\n"
        f"👤 *Имя:* {u.get('first_name','')} {u.get('last_name','')}\n"
        f"🔗 *Username:* @{u.get('username','')}\n"
        f"📞 *Телефон:* {phone}\n\n"
        "Нажмите кнопку чтобы отправить заявку на вступление в группу KAZMETALS 👇",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    await update.message.reply_text(
        "👇",
        reply_markup=keyboard
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    logger.info("KAZMETALS Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
