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
    await init_db()


async def post_shutdown(_application: Application) -> None:
    await close_pool()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_MESSAGE)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    admin_id = _get_admin_chat_id()
    if admin_id is None:
        logger.warning("ADMIN_CHAT_ID не задан — команда /stats недоступна")
        return

    if update.effective_chat.id != admin_id:
        return

    stats = await get_stats()
    if stats is None:
        await update.message.reply_text(
            "База данных недоступна. Проверьте DATABASE_URL на Railway."
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
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    get_system_prompt()
    logger.info("База знаний: %s", DATA_FILE)
    logger.info("Бот запущен (polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
