import asyncio
import os
from typing import Any

from dotenv import load_dotenv
from groq import Groq

from company_data import get_system_prompt

load_dotenv()

GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_HISTORY_MESSAGES = 10

conversation_history: dict[int, list[dict[str, str]]] = {}


def _get_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY не задан в .env")
    return Groq(api_key=api_key)


def _trim_history(chat_id: int) -> None:
    history = conversation_history.get(chat_id, [])
    if len(history) > MAX_HISTORY_MESSAGES:
        conversation_history[chat_id] = history[-MAX_HISTORY_MESSAGES:]


def add_user_message(chat_id: int, text: str) -> None:
    if chat_id not in conversation_history:
        conversation_history[chat_id] = []
    conversation_history[chat_id].append({"role": "user", "content": text})
    _trim_history(chat_id)


def add_assistant_message(chat_id: int, text: str) -> None:
    conversation_history[chat_id].append({"role": "assistant", "content": text})
    _trim_history(chat_id)


def build_messages(chat_id: int) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": get_system_prompt()}]
    messages.extend(conversation_history.get(chat_id, []))
    return messages


async def get_ai_response(chat_id: int, user_message: str) -> str:
    add_user_message(chat_id, user_message)
    messages = build_messages(chat_id)

    try:
        client = _get_client()

        def _call_groq() -> str:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
            )
            return response.choices[0].message.content or ""

        reply = await asyncio.to_thread(_call_groq)
        add_assistant_message(chat_id, reply)
        return reply
    except ValueError as e:
        return str(e)
    except Exception:
        conversation_history[chat_id].pop()
        return (
            "Извините, сейчас не удалось получить ответ. "
            "Попробуйте позже или позвоните: +7 778 061 50 00"
        )
