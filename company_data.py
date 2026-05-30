"""
Настройки AI-ассистента и сборка system prompt.

База знаний о компании подгружается из company_data.txt (результат scraper.py).
Если файла нет — используется запасной текст FALLBACK_KNOWLEDGE.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Настройки бота (редактируйте здесь, не в txt) ---

COMPANY_NAME = "Центр Красок #1"
SITE_URL = "https://centr-krasok.kz/"
MANAGER_PHONE = "+7 778 061 50 00"

DATA_FILE = Path(
    os.getenv(
        "COMPANY_DATA_FILE",
        Path(__file__).resolve().parent / "company_data.txt",
    )
)

PROMPT_INTRO = f"""Ты — AI-ассистент интернет-магазина «{COMPANY_NAME}» ({SITE_URL}).
Твоя задача — отвечать на вопросы покупателей о компании, товарах и услугах.

СТРОГОЕ ПРАВИЛО: отвечай ТОЛЬКО на основе информации в базе знаний ниже.
Если ответа нет в данных — честно скажи: «Уточните у наших менеджеров: {MANAGER_PHONE}».
Не придумывай цены, наличие товаров или детали, которых нет в базе.
"""

PROMOTIONS_RULES = f"""
ПРАВИЛА ПРО АКЦИИ И СКИДКИ:
- Если спрашивают про акции, скидки, распродажи, Kaspi Жұма — смотри раздел «=== АКЦИИ ===» в базе знаний.
- Перечисляй акции из этого раздела: название, размер скидки, бренды, срок (если указан в тексте).
- Не отвечай «акций нет», если в «=== АКЦИИ ===» есть хотя бы одна запись.
- Если срок акции мог истечь — всё равно кратко опиши акцию из базы и добавь:
  «Актуальность уточните на {SITE_URL}promotions/ или по телефону {MANAGER_PHONE}».
- На главной странице сайта также есть блок «Товары со скидкой» — направляй смотреть каталог и акции на сайте.
"""

COMMUNICATION_RULES = """
ПРАВИЛА ОБЩЕНИЯ:
- Отвечай кратко и по делу
- Будь вежливым и дружелюбным
- Пиши на том же языке, на котором написал пользователь (рус/каз/eng)
- Если вопрос не связан с компанией — ответь: «Я могу помочь только с вопросами о Центре Красок 🎨»
- Не отвечай на политические, медицинские и любые другие посторонние темы
"""

# Запасная база, если company_data.txt ещё не создан
FALLBACK_KNOWLEDGE = """
НАЗВАНИЕ: Центр Красок #1
САЙТ: https://centr-krasok.kz/
ЮРИДИЧЕСКОЕ ЛИЦО: ТОО «SAMRUk Trade», БИН 140640024284
ЮРИДИЧЕСКИЙ АДРЕС: г. Алматы, Ауэзовский район, ул. Кабдолова, дом 1/8

КОНТАКТЫ:
Алматы: +7 778 061 50 00
Астана: +7 701 943 50 00
Email: info@centr-krasok.kz
Время работы: Пн–Вс, 10:00–20:00

УСЛУГИ: продажа красок и ЛКМ, колеровка (45 000+ оттенков), консультация,
доставка, самовывоз, программы для дизайнеров и строителей.
ОПЛАТА: Visa, Mastercard, Kaspi

АКЦИИ: актуальные предложения на странице https://centr-krasok.kz/promotions/
(запустите python scraper.py и задеплойте company_data.txt для полного списка в боте).
""".strip()

_cached_prompt: str | None = None
_cached_mtime: float | None = None


def load_knowledge_base() -> tuple[str, str]:
    """
    Читает базу знаний из файла.
    Возвращает (текст, источник): 'file' или 'fallback'.
    """
    if DATA_FILE.is_file():
        try:
            text = DATA_FILE.read_text(encoding="utf-8").strip()
            if text:
                return text, "file"
        except OSError as exc:
            logger.warning("Не удалось прочитать %s: %s", DATA_FILE, exc)

    logger.warning(
        "Файл %s не найден или пуст — используется запасная база. Запустите: python scraper.py",
        DATA_FILE,
    )
    return FALLBACK_KNOWLEDGE, "fallback"


def build_system_prompt(knowledge: str) -> str:
    return (
        f"{PROMPT_INTRO.strip()}\n\n"
        f"--- БАЗА ЗНАНИЙ (актуальные данные с {SITE_URL}) ---\n\n"
        f"{knowledge.strip()}\n\n"
        f"--- КОНЕЦ БАЗЫ ЗНАНИЙ ---"
        f"{PROMOTIONS_RULES}"
        f"{COMMUNICATION_RULES}"
    )


def get_system_prompt() -> str:
    """
    Собирает system prompt: настройки из этого файла + данные с сайта из txt.
    При изменении company_data.txt на диске промпт пересобирается автоматически.
    """
    global _cached_prompt, _cached_mtime

    mtime: float | None = None
    if DATA_FILE.is_file():
        try:
            mtime = DATA_FILE.stat().st_mtime
        except OSError:
            mtime = None

    if _cached_prompt is None or mtime != _cached_mtime:
        knowledge, source = load_knowledge_base()
        _cached_prompt = build_system_prompt(knowledge)
        _cached_mtime = mtime
        logger.info("System prompt загружен (источник: %s, файл: %s)", source, DATA_FILE)

    return _cached_prompt


def get_data_source() -> str:
    """Для логов при старте бота: 'file' или 'fallback'."""
    _, source = load_knowledge_base()
    return source


def __getattr__(name: str):
    """Совместимость: старый ai_handler импортировал SYSTEM_PROMPT."""
    if name == "SYSTEM_PROMPT":
        return get_system_prompt()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
