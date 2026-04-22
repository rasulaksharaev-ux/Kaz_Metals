import os
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "8537953320:AAHg9tebajVFYkWs-hiWwaPGYQEzP0D9c1I")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "612230948"))
GROUP_ID = int(os.environ.get("GROUP_ID", "-3852395090"))
GROUP_LINK = os.environ.get("GROUP_LINK", "https://t.me/+_pgc-XiGz7E4YTUy")

users = {}

# ─── /start ───
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

# ─── Кнопки ───
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id
    username = query.from_user.username
    data = query.data

    logger.info(f"Button: {data} | User: {user_id}")

    # Показать правила
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

    # Отказ от правил
    if data == "decline_rules":
        users.pop(user_id, None)
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Вы отказались от регистрации.\n\nЕсли передумаете — напишите /start 🙂"
        )
        return

    # Принял правила — проверяем username
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
                 f"Ваш Telegram ник: *@{username}*\n\nВсё верно?",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return

    # Изменить username
    if data == "change_username":
        await context.bot.send_message(
            chat_id=chat_id,
            text="✏️ Измените username в Настройках Telegram, затем напишите /start"
        )
        return

    # Username подтверждён — запрашиваем имя
    if data == "confirm_username":
        users[user_id]["step"] = "awaiting_first_name"
        await context.bot.send_message(
            chat_id=chat_id,
            text="👤 *Шаг 1 из 3 — Имя*\n\nВведите ваше *имя*:",
            parse_mode="Markdown"
        )
        return

    # Отправить заявку
    if data == "send_application":
        u = users.get(user_id, {})
        admin_text = (
            f"🆕 *Новая заявка — KAZMETALS*\n\n"
            f"👤 *Имя:* {u.get('first_name','—')} {u.get('last_name','—')}\n"
            f"🔗 *Username:* @{u.get('username','—')}\n"
            f"📞 *Телефон:* {u.get('phone','—')}\n"
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
                 "Обычно проверка занимает до 24 часов. ⏳",
            parse_mode="Markdown"
        )
        return

    # Админ одобрил
    if data.startswith("approve_"):
        target_id = int(data.replace("approve_", ""))
        try:
            invite = await context.bot.create_chat_invite_link(
                chat_id=GROUP_ID,
                member_limit=1,
                name=f"user_{target_id}"
            )
            if str(target_id) in users:
                users[str(target_id)]["step"] = "approved"
            await context.bot.send_message(
                chat_id=target_id,
                text="🎉 *Поздравляем! Регистрация завершена!*\n\n"
                     "Вы успешно верифицированы в KAZMETALS!\n\n"
                     "Нажмите кнопку чтобы вступить в группу 👇",
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
            logger.error(f"Ошибка approve: {e}")
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"⚠️ Ошибка создания ссылки: {e}\n\nДобавьте вручную: {GROUP_LINK}"
            )
        return

    # Админ отклонил
    if data.startswith("reject_"):
        target_id = int(data.replace("reject_", ""))
        if str(target_id) in users:
            users[str(target_id)]["step"] = "rejected"
        await context.bot.send_message(
            chat_id=target_id,
            text="😔 *Ваша заявка отклонена.*\n\n"
                 "Для повторной попытки: /start",
            parse_mode="Markdown"
        )
        await query.edit_message_text(
            text=query.message.text + "\n\n❌ *Отклонено. Пользователь уведомлён.*",
            parse_mode="Markdown"
        )
        return

# ─── Текстовые сообщения ───
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    text = update.message.text.strip()

    logger.info(f"Text: '{text}' | User: {user_id} | Step: {users.get(user_id, {}).get('step')}")

    if user_id not in users:
        await update.message.reply_text(
            "Напишите /start чтобы начать регистрацию."
        )
        return

    step = users[user_id].get("step")

    # Шаг 1 — имя
    if step == "awaiting_first_name":
        users[user_id]["first_name"] = text
        users[user_id]["step"] = "awaiting_last_name"
        await update.message.reply_text(
            f"👍 Имя *{text}* сохранено!\n\n"
            "👤 *Шаг 2 из 3 — Фамилия*\n\nВведите вашу фамилию:",
            parse_mode="Markdown"
        )
        return

    # Шаг 2 — фамилия
    if step == "awaiting_last_name":
        users[user_id]["last_name"] = text
        users[user_id]["step"] = "awaiting_phone"
        await update.message.reply_text(
            f"👍 Фамилия *{text}* сохранена!\n\n"
            "📞 *Шаг 3 из 3 — Номер телефона*\n\n"
            "Введите ваш номер телефона в формате:\n"
            "*+77001234567*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    # Шаг 3 — телефон
    if step == "awaiting_phone":
        users[user_id]["phone"] = text
        users[user_id]["step"] = "confirm_application"
        u = users[user_id]
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📨 Отправить заявку на вступление", callback_data="send_application")]
        ])
        await update.message.reply_text(
            "🏁 *Регистрация завершена!*\n\n"
            "Все ваши данные собраны:\n\n"
            f"👤 *Имя:* {u.get('first_name','—')} {u.get('last_name','—')}\n"
            f"🔗 *Username:* @{u.get('username','—')}\n"
            f"📞 *Телефон:* {text}\n\n"
            "Нажмите кнопку чтобы отправить заявку на вступление в KAZMETALS 👇",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return

    await update.message.reply_text(
        "Следуйте инструкциям бота. Если что-то пошло не так — напишите /start"
    )

# ─── Запуск ───
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    logger.info("KAZMETALS Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
