"""
Парсер сайта centr-krasok.kz → company_data.txt для AI-ассистента.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup, Comment, Tag
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("SCRAPER_BASE_URL", "https://centr-krasok.kz").rstrip("/")
OUTPUT_FILE = os.getenv("SCRAPER_OUTPUT", "company_data.txt")
REQUEST_DELAY = float(os.getenv("SCRAPER_DELAY", "1"))
PROMO_MAX_AGE_DAYS = int(os.getenv("SCRAPER_PROMO_MAX_DAYS", "120"))

HEADERS = {
    "User-Agent": os.getenv(
        "SCRAPER_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

URLS = {
    "main": f"{BASE_URL}/",
    "about": f"{BASE_URL}/about/",
    "contacts": f"{BASE_URL}/about/contacts/",
    "promotions": f"{BASE_URL}/promotions/",
    "catalog": f"{BASE_URL}/catalog/",
}

# Точные строки интерфейса сайта (не база знаний)
NOISE_EXACT = frozenset(
    {
        "главная",
        "каталог",
        "корзина",
        "профиль",
        "бренды",
        "акции",
        "помощь",
        "контакты",
        "новости",
        "статьи",
        "рус",
        "русский",
        "қазақша",
        "english",
        "да",
        "нет",
        "все",
        "посмотреть",
        "посмотреть все",
        "в корзину",
        "фильтры",
        "0",
        "previous",
        "next",
        "не выбран",
        "загрузка карты...",
        "оставить заявку на консультацию",
        "имя*",
        "телефон*",
        "о магазине",
        "вдохновение",
        "партнерам",
        "дизайнерам",
        "строителям",
        "глоссарий",
        "оставить отзыв",
        "сканер штрихкода",
        "товары со скидкой",
        "лучшие предложения",
        "краски по категориям",
        "краски по интерьеру",
        "дизайнерам и строителям",
        "ваш город алматы?",
        "нет, выбрать другой",
        "выберите город:",
        "сортировать по:",
        "по умолчанию",
        "популярности",
        "цена по возрастанию",
        "цена по убыванию",
        "алфавиту",
        "найдено",
    }
)

CHROME_CLASS_RE = re.compile(
    r"(?:^|\s)(header|footer|top-menu|bottom-menu|main-menu|breadcrumb|"
    r"cookie|modal|popup|basket|cart-wrap|city-select|lang-select|"
    r"bx-header|bx-footer|search-title|auth-form)(?:\s|$)",
    re.I,
)

BRAND_FROM_FILTER_RE = re.compile(r"^(.+?)\s*\(\d+\)\s*$")

CITY_NAMES = frozenset(
    {
        "актау",
        "актобе",
        "алматы",
        "астана",
        "атырау",
        "жанаозен",
        "жезказган",
        "караганды",
        "кокшетау",
        "конаев",
        "костанай",
        "кызылорда",
        "павлодар",
        "петропавловск",
        "семей",
        "талдыкорган",
        "тараз",
        "туркестан",
        "темиртау",
        "уральск",
        "усть-каменогорск",
        "шымкент",
        "экибастуз",
    }
)

TOP_CATALOG_RE = re.compile(r"^/catalog/[^/]+/?$", re.I)


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\u00a0", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _is_noise_line(line: str) -> bool:
    low = line.lower().strip()
    if not low or len(low) < 2:
        return True
    if low in NOISE_EXACT:
        return True
    if low in CITY_NAMES and len(low) < 25:
        return True
    if re.fullmatch(r"\d{1,2}", low):
        return True
    if re.fullmatch(r"-?\d+%", low):
        return True
    if re.search(r"\d+\s*тг", low) or re.search(r"\d+\s*kzt", low, re.I):
        return True
    if re.search(r"артикул\s*\d", low):
        return True
    if re.search(r"остаток\s+", low):
        return True
    if "шт\n" in low or low.endswith(" шт"):
        return True
    if re.match(r"^[\d\s\+\-\(\)\.]+$", low):
        return True
    if len(low) <= 3 and low.isalpha():
        return True
    return False


def _unique_lines(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen and not _is_noise_line(item):
            seen.add(key)
            result.append(item)
    return result


def _text_of(element: Tag | None) -> str:
    if element is None:
        return ""
    return clean_text(element.get_text(separator="\n", strip=True))


def _strip_chrome(soup: BeautifulSoup, *, aggressive: bool = True) -> None:
    """Удаляет шапку, меню, футер и служебные блоки."""
    for tag_name in (
        "header",
        "footer",
        "nav",
        "aside",
        "form",
        "script",
        "style",
        "noscript",
        "iframe",
    ):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    if not aggressive:
        for tag in list(soup.find_all(True)):
            attrs = getattr(tag, "attrs", None)
            if not isinstance(attrs, dict):
                continue
            tag_id = str(attrs.get("id") or "")
            if tag_id.startswith("bx-panel"):
                tag.decompose()
        return

    for tag in list(soup.find_all(True)):
        if not isinstance(tag, Tag):
            continue
        attrs = getattr(tag, "attrs", None)
        if not isinstance(attrs, dict):
            continue
        tag_id = str(attrs.get("id") or "")
        if tag_id.startswith("bx-panel") or tag_id in ("header", "footer", "top-menu"):
            tag.decompose()
            continue
        classes = " ".join(attrs.get("class") or [])
        if classes and CHROME_CLASS_RE.search(classes):
            tag.decompose()


FALLBACK_BRANDS = [
    "Dulux",
    "Pinotex",
    "Hammerite",
    "Marshall",
    "Master Color",
    "Oikos",
    "Sikkens",
    "Levis",
    "Dufa",
    "KUDO",
    "Anza",
    "Profilux",
    "PUFAS",
    "MAKO",
    "TEKNOS",
    "Tytan",
    "Storch",
    "Vetonit",
    "Color Expert",
    "Wagner",
    "Argile",
    "Orac Decor",
    "Kelly-Moore",
    "TimberCare",
    "HYGGE",
    "Little Greene",
    "Swiss Lake",
    "TERRACO",
    "DANOGIPS",
    "Luxium",
    "Profilux",
]


def parse_page(url: str, *, aggressive_strip: bool = True) -> BeautifulSoup | None:
    print(f"Парсинг: {url}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        _strip_chrome(soup, aggressive=aggressive_strip)
        return soup
    except requests.RequestException as exc:
        print(f"  Ошибка запроса: {exc}")
        return None


def _find_content_root(soup: BeautifulSoup) -> Tag:
    for selector in (
        ".workarea",
        "#workarea",
        ".page-content",
        ".content-page",
        ".bx-content",
        "main .container",
        "main",
        "#content",
        ".content",
    ):
        node = soup.select_one(selector)
        if node and len(_text_of(node)) > 100:
            return node
    return soup.body or soup


def _paragraphs(root: Tag, min_len: int = 80, max_len: int = 4000) -> list[str]:
    """Только теги <p> — без огромных div с меню."""
    texts: list[str] = []
    for p in root.find_all("p"):
        if p.find_parent(["header", "footer", "nav", "form"]):
            continue
        text = _text_of(p)
        if min_len <= len(text) <= max_len and text not in texts:
            texts.append(text)
    return texts


def _section_after_heading(root: Tag, *keywords: str) -> Tag | None:
    for heading in root.find_all(["h1", "h2", "h3", "h4"]):
        title = heading.get_text(strip=True).lower()
        if any(kw in title for kw in keywords):
            return heading.find_parent(["section", "div"]) or heading
    return None


def _find_company_text(root: Tag) -> list[str]:
    """Ищет смысловые абзацы о компании."""
    found: list[str] = []
    skip_phrases = (
        "всегда рады общению",
        "оставьте контактные данные",
        "обратный звонок",
    )
    for tag in root.find_all(["p", "div"]):
        if len(tag.find_all(["div", "section", "ul"])) > 5:
            continue
        text = tag.get_text(" ", strip=True)
        low = text.lower()
        if any(p in low for p in skip_phrases):
            continue
        if 120 < len(text) < 5000 and (
            "центр красок" in low
            or ("интернет-магазин" in low and "краск" in low)
            or ("колеровк" in low and "бренд" in low)
        ):
            found.append(clean_text(text))
    return _unique_lines(found)


def parse_main() -> dict[str, str]:
    soup = parse_page(URLS["main"], aggressive_strip=False)
    if soup is None:
        return {"description": "", "brands": ""}

    root = _find_content_root(soup)
    description_parts: list[str] = []

    utp_keywords = (
        "доставка до двери",
        "сертифицирован",
        "консультац",
        "колеруем",
        "45 000",
        "45000",
    )
    for p in root.find_all("p"):
        text = _text_of(p)
        if 10 < len(text) < 120 and any(k in text.lower() for k in utp_keywords):
            description_parts.append(text)

    for text in _paragraphs(root, min_len=250, max_len=3500):
        low = text.lower()
        if "центр красок" in low and (
            "интернет-магазин" in low or "премиум" in low or "лакокрасочн" in low
        ):
            description_parts.append(text)

    description_parts.extend(_find_company_text(root))

    return {
        "description": clean_text("\n\n".join(_unique_lines(description_parts))),
        "brands": "",
    }


def parse_about() -> dict[str, str]:
    soup = parse_page(URLS["about"], aggressive_strip=False)
    if soup is None:
        return {"about": "", "advantages": ""}

    root = _find_content_root(soup)

    about_parts = _find_company_text(root)
    if not about_parts:
        for text in _paragraphs(root, min_len=80, max_len=2500):
            if "центр красок" in text.lower() or "колеровк" in text.lower():
                about_parts.append(text)

    advantages: list[str] = []
    for heading in root.find_all("h2"):
        if "возможност" not in heading.get_text(strip=True).lower():
            continue
        ul = heading.find_next("ul")
        if ul:
            for li in ul.find_all("li", recursive=False):
                line = li.get_text(strip=True)
                if 4 < len(line) < 80:
                    advantages.append(line)
        break

    stats: list[str] = []
    for block in root.find_all(["div", "li", "span"]):
        num_el = block.find(class_=re.compile(r"num|count|value|digit|number", re.I))
        label_el = block.find(class_=re.compile(r"text|title|label|desc", re.I))
        if num_el and label_el:
            stat = f"{num_el.get_text(strip=True)} {label_el.get_text(strip=True)}"
            if len(stat) < 80:
                stats.append(stat)
                continue
        t = block.get_text(" ", strip=True)
        if re.search(r"\d", t) and re.search(
            r"оттенк|бренд|лет|клиент|товар|реализован", t, re.I
        ) and len(t) < 80:
            stats.append(t)

    about_text = clean_text("\n\n".join(_unique_lines(about_parts)))
    good_stats = [s for s in _unique_lines(stats) if re.search(r"\d", s)]
    if not good_stats:
        good_stats = [
            "45 000+ оттенков колеровки",
            "20+ брендов ЛКМ",
            "Премиум и ультра-премиум сегмент",
        ]
    if good_stats:
        about_text += "\n\nПоказатели:\n" + "\n".join(f"- {s}" for s in good_stats[:8])

    return {
        "about": about_text,
        "advantages": "\n".join(f"- {a}" for a in _unique_lines(advantages)[:15]),
    }


def parse_contacts() -> str:
    soup = parse_page(URLS["contacts"])
    if soup is None:
        return ""

    root = _find_content_root(soup)
    blocks: list[str] = []
    contact_cities = ("Алматы", "Астана")
    contact_re = re.compile(
        r"\+7|info@centr-krasok|ул\.|улиц|бутик|10:00|20:00|кабдолова|мангилик",
        re.I,
    )

    for h2 in root.find_all("h2"):
        city = h2.get_text(strip=True)
        if city not in contact_cities:
            continue

        lines: list[str] = []
        for sib in h2.find_next_siblings():
            if isinstance(sib, Tag) and sib.name == "h2":
                break
            if not isinstance(sib, Tag):
                continue
            chunk = _text_of(sib)
            for line in chunk.splitlines():
                line = line.strip()
                if contact_re.search(line) and not _is_noise_line(line):
                    lines.append(line)

        lines = _unique_lines(lines)
        if lines:
            blocks.append(f"{city}:\n" + "\n".join(lines))

    footer = (
        "Общее:\n"
        "Email: info@centr-krasok.kz\n"
        "Время работы: Пн–Вс, 10:00–20:00\n"
        "Телефоны: Алматы +7 778 061 50 00, Астана +7 701 943 50 00"
    )
    if blocks:
        blocks.append(footer)

    return clean_text("\n\n".join(blocks))


def _parse_promotion_date(text: str) -> datetime | None:
    match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if not match:
        return None
    try:
        return datetime(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


def _extract_deadline_hint(text: str) -> str | None:
    """Извлекает срок из текста: «до 30 апреля», «только до 28 февраля»."""
    match = re.search(
        r"(?:до|по)\s+(\d{1,2})\s+"
        r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)",
        text,
        re.I,
    )
    if match:
        return f"Срок: до {match.group(1)} {match.group(2).lower()}"
    if re.search(r"весь\s+декабрь|весь\s+месяц|только\s+один\s+день", text, re.I):
        return "Срок: указан в описании акции"
    return None


def parse_promotions() -> str:
    soup = parse_page(URLS["promotions"])
    if soup is None:
        return ""

    root = _find_content_root(soup)
    promotions: list[tuple[datetime | None, str]] = []
    cutoff = datetime.now() - timedelta(days=PROMO_MAX_AGE_DAYS)
    today_label = datetime.now().strftime("%d.%m.%Y")

    for heading in root.find_all(["h4", "h3"]):
        title = heading.get_text(strip=True)
        if not title or len(title) < 8:
            continue
        if title.lower() in ("акции", "актуальные акции"):
            continue

        description_parts: list[str] = []
        promo_date: datetime | None = None

        for sib in heading.find_next_siblings():
            if isinstance(sib, Tag) and sib.name in ("h4", "h3", "h2"):
                break
            if not isinstance(sib, Tag):
                continue
            for p in sib.find_all("p") if sib.name != "p" else [sib]:
                text = _text_of(p)
                if not text:
                    continue
                if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text.strip()):
                    promo_date = _parse_promotion_date(text)
                    continue
                if len(text) > 25:
                    description_parts.append(text)

        if promo_date and promo_date < cutoff:
            continue

        desc = clean_text(" ".join(description_parts))
        if len(desc) > 400:
            desc = desc[:400].rsplit(" ", 1)[0] + "…"
        entry = f"• {title}"
        if desc:
            entry += f"\n  {desc}"
        deadline = _extract_deadline_hint(f"{title} {desc}")
        if deadline:
            entry += f"\n  {deadline}"
        if promo_date:
            entry += f"\n  Опубликовано: {promo_date.strftime('%d.%m.%Y')}"
        promotions.append((promo_date or datetime.min, entry))

    promotions.sort(key=lambda x: x[0], reverse=True)
    body = "\n\n".join(item[1] for item in promotions[:12])
    if not body:
        return "(акции на сайте не найдены — см. https://centr-krasok.kz/promotions/)"

    header = (
        f"Список со страницы {URLS['promotions']} (обновлено {today_label}).\n"
        "При ответе покупателю перечисляй эти акции; актуальность сроков уточняй у менеджеров.\n"
    )
    return clean_text(header + "\n" + body)


def _extract_brands_from_catalog(root: Tag) -> list[str]:
    brands: list[str] = []
    for line in _text_of(root).splitlines():
        match = BRAND_FROM_FILTER_RE.match(line.strip())
        if match:
            name = match.group(1).strip()
            if not _is_noise_line(name):
                brands.append(name)
    for node in root.find_all(string=BRAND_FROM_FILTER_RE):
        text = str(node).strip()
        match = BRAND_FROM_FILTER_RE.match(text)
        if match:
            brands.append(match.group(1).strip())
    return _unique_lines(brands)


def _extract_brands_from_main(root: Tag) -> list[str]:
    brands: list[str] = []
    brands_block = _section_after_heading(root, "бренд")
    search_root = brands_block if brands_block else root
    for img in search_root.find_all("img"):
        name = (img.get("alt") or img.get("title") or "").strip()
        if 2 < len(name) < 40 and "logo" not in name.lower():
            brands.append(name)
    return _unique_lines(brands)


def parse_catalog() -> tuple[str, list[str]]:
    soup = parse_page(URLS["catalog"])
    if soup is None:
        return "", []

    root = _find_content_root(soup)
    catalog_brands = _extract_brands_from_catalog(root)
    categories: list[str] = []

    for block in root.select(
        ".catalog-section-list-item-title a, "
        ".catalog-section-list a, "
        ".section-compact-list a"
    ):
        name = block.get_text(strip=True)
        href = block.get("href", "")
        if not name or not href:
            continue
        path = href.split("?")[0]
        if not TOP_CATALOG_RE.search(path.replace(BASE_URL, "")):
            continue
        if _is_noise_line(name) or re.search(r"\(\d+\)\s*$", name):
            continue
        if name not in categories:
            categories.append(name)

    # Заголовки h2 на странице каталога (верхний уровень)
    for h2 in root.find_all("h2"):
        name = h2.get_text(strip=True)
        if 4 < len(name) < 60 and not _is_noise_line(name):
            if name not in categories:
                categories.append(name)

    categories = _unique_lines(categories)

    product_keywords = (
        "краск",
        "лак",
        "грунт",
        "шпат",
        "штукат",
        "антисеп",
        "инструмент",
        "обои",
        "клей",
        "гермет",
        "пен",
        "раствор",
        "лепнин",
        "краскопульт",
        "декорат",
        "защит",
        "фасад",
        "фактур",
        "аэрозол",
        "металл",
        "дерев",
        "пропит",
        "эмаль",
        "масл",
    )
    filtered = [
        c
        for c in categories
        if any(k in c.lower() for k in product_keywords)
    ]
    if filtered:
        categories = filtered

    return "\n".join(f"- {c}" for c in categories), catalog_brands


def parse_brands(catalog_brands: list[str] | None = None) -> str:
    """Бренды: логотипы на главной + фильтр каталога."""
    brands: list[str] = list(catalog_brands or [])

    main_soup = parse_page(URLS["main"], aggressive_strip=False)
    if main_soup is not None:
        brands.extend(_extract_brands_from_main(_find_content_root(main_soup)))

    brands = _unique_lines(brands)
    if len(brands) < 5:
        brands = _unique_lines(brands + FALLBACK_BRANDS)

    return "\n".join(f"- {b}" for b in brands)


def save_to_file(data: dict[str, str]) -> None:
    description_parts = [data.get("description", "")]
    about = data.get("about", "")
    if about and about not in (description_parts[0] if description_parts else ""):
        if not any(about in part or part in about for part in description_parts if part):
            description_parts.append(about)
    if data.get("advantages"):
        description_parts.append("Преимущества и возможности:\n" + data["advantages"])

    static_info = (
        "Юридическая информация:\n"
        "ТОО «SAMRUk Trade», БИН 140640024284\n"
        "Адрес: г. Алматы, ул. Кабдолова, 1/8\n"
        "Сайт: https://centr-krasok.kz/"
    )
    description_parts.append(static_info)

    company_description = clean_text("\n\n".join(p for p in description_parts if p))

    sections = [
        ("=== ОПИСАНИЕ КОМПАНИИ ===", company_description),
        ("=== БРЕНДЫ ===", data.get("brands", "")),
        ("=== КОНТАКТЫ ===", data.get("contacts", "")),
        ("=== АКЦИИ ===", data.get("promotions", "")),
        ("=== КАТЕГОРИИ ТОВАРОВ ===", data.get("catalog", "")),
    ]

    parts: list[str] = []
    for header, body in sections:
        parts.append(header)
        parts.append(clean_text(body) if body else "(данные не получены)")
        parts.append("")

    content = clean_text("\n".join(parts)) + "\n"

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        file.write(content)

    print(f"\nГотово: {OUTPUT_FILE} ({len(content)} символов)")


def main() -> None:
    print("Скрапинг centr-krasok.kz\n")

    main_data = parse_main()
    time.sleep(REQUEST_DELAY)

    about_data = parse_about()
    time.sleep(REQUEST_DELAY)

    contacts = parse_contacts()
    time.sleep(REQUEST_DELAY)

    promotions = parse_promotions()
    time.sleep(REQUEST_DELAY)

    time.sleep(REQUEST_DELAY)
    catalog, catalog_brands = parse_catalog()
    time.sleep(REQUEST_DELAY)

    brands = parse_brands(catalog_brands)

    data = {
        "description": main_data.get("description", ""),
        "about": about_data.get("about", ""),
        "advantages": about_data.get("advantages", ""),
        "brands": brands,
        "contacts": contacts,
        "promotions": promotions,
        "catalog": catalog,
    }

    save_to_file(data)


if __name__ == "__main__":
    main()
