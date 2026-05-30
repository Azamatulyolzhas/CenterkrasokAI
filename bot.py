import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from ai_handler import get_ai_response
from company_data import DATA_FILE, get_system_prompt
from db import (
    add_or_update_user,
    close_pool,
    get_stats,
    increment_message_count,
    init_db,
    is_db_ready,
    save_message,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

WELCOME_MESSAGE = (
    "Здравствуйте! 👋\n\n"
    "Я — AI-ассистент интернет-магазина «Центр Красок #1».\n"
    "Спросите о товарах, доставке, брендах, колеровке или контактах — "
    "отвечу на основе нашей базы знаний.\n\n"
    "Напишите ваш вопрос текстом."
)


def _get_admin_chat_id() -> int | None:
    raw = os.getenv("ADMIN_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("ADMIN_CHAT_ID задан некорректно: %s", raw)
        return None


def _format_user_label(username: str | None, full_name: str | None) -> str:
    if username:
        return f"@{username}"
    if full_name:
        return full_name
    return "без имени"


def _format_stats_message(stats: dict) -> str:
    lines = [
        "📊 Статистика бота",
        "",
        f"👥 Всего пользователей: {stats['total_users']}",
        f"🟢 Активны сегодня: {stats['active_today']}",
        f"💬 Всего сообщений: {stats['total_messages']}",
        "",
        "🏆 Топ пользователей:",
    ]

    top_users = stats.get("top_users") or []
    if not top_users:
        lines.append("— пока нет данных")
    else:
        for index, user in enumerate(top_users, start=1):
            label = _format_user_label(user.get("username"), user.get("full_name"))
            count = user.get("message_count", 0)
            lines.append(f"{index}. {label} — {count} сообщений")

    return "\n".join(lines)


async def post_init(_application: Application) -> None:
    db_ok = await init_db()
    admin_id = _get_admin_chat_id()
    logger.info("PostgreSQL: %s", "подключена" if db_ok else "не подключена")
    if admin_id is None:
        logger.warning("ADMIN_CHAT_ID не задан — /stats покажет подсказку по настройке")
    else:
        logger.info("ADMIN_CHAT_ID задан (id=%s)", admin_id)


async def post_shutdown(_application: Application) -> None:
    await close_pool()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_MESSAGE)


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает chat_id — нужен для настройки ADMIN_CHAT_ID."""
    if not update.message or not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    user = update.effective_user
    username = f"@{user.username}" if user and user.username else "—"
    await update.message.reply_text(
        f"Ваш chat_id: `{chat_id}`\n"
        f"Username: {username}\n\n"
        f"Скопируйте chat_id в переменную ADMIN_CHAT_ID на Railway.",
        parse_mode="Markdown",
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    user_chat_id = update.effective_chat.id
    admin_id = _get_admin_chat_id()

    if admin_id is None:
        await update.message.reply_text(
            "⚠️ Статистика не настроена: переменная ADMIN_CHAT_ID не задана на сервере.\n\n"
            f"Ваш chat_id: {user_chat_id}\n"
            "Добавьте его в Railway → Variables → ADMIN_CHAT_ID и перезапустите бот.\n"
            "Подсказка: команда /myid"
        )
        return

    if user_chat_id != admin_id:
        await update.message.reply_text(
            f"⛔ Нет доступа к статистике.\n\n"
            f"Ваш chat_id: {user_chat_id}\n"
            f"Ожидается ADMIN_CHAT_ID: {admin_id}\n\n"
            "Если это ваш аккаунт — обновите ADMIN_CHAT_ID на Railway."
        )
        return

    if not is_db_ready():
        await update.message.reply_text(
            "⚠️ База данных не подключена.\n\n"
            "На Railway:\n"
            "1. Добавьте сервис PostgreSQL\n"
            "2. В сервисе бота → Variables → Reference → DATABASE_URL\n"
            "3. Перезапустите деплой"
        )
        return

    stats = await get_stats()
    if stats is None:
        await update.message.reply_text(
            "Не удалось получить статистику из БД. Смотрите логи Railway."
        )
        return

    await update.message.reply_text(_format_stats_message(stats))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()

    if not user_text:
        return

    user = update.effective_user
    await add_or_update_user(
        chat_id,
        user.username if user else None,
        user.full_name if user else None,
    )

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    reply = await get_ai_response(chat_id, user_text)
    await update.message.reply_text(reply)

    await increment_message_count(chat_id)
    await save_message(chat_id, user_text, reply)


def main() -> None:
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_TOKEN не задан в .env")

    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", myid_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    get_system_prompt()
    logger.info("База знаний: %s", DATA_FILE)
    logger.info("Бот запущен (polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
