"""Collect food-industry exhibitors from the public MVK catalogues."""

import json
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import scrapy
from lxml import etree, html
from pydantic import HttpUrl, ValidationError
from scrapy.exceptions import CloseSpider
from scrapy.http import HtmlResponse, Response
from twisted.python.failure import Failure

from foodsource.text import offer_clauses, offer_subject

from .normalize import official_domain


@dataclass(frozen=True)
class Exhibition:
    id: str
    label: str
    listing: str
    item_selector: str = ".eList a[data-item]::attr(data-item)"
    pager_selector: str = "a.page-link"
    detail_page: str = "exhibitorview.aspx"
    ingredients_only: bool = False


GLOBAL_INGREDIENTS = Exhibition(
    "global-ingredients-2026",
    "Global Ingredients Show 2026",
    "https://new.ingred.ru/ru-RU/about/exhibitor-list.aspx",
    item_selector=".card.participant[data-exib]::attr(data-exib)",
    pager_selector=".pagination a.carousel_btn",
    detail_page="exhibitorpopup.aspx",
    ingredients_only=True,
)

EXHIBITIONS = (
    Exhibition(
        "foodtech-ural",
        "Каталог участников FoodTech Ural",
        "https://foodtech-ural.ru/ru-RU/about/exhibitor-list.aspx",
    ),
    Exhibition(
        "interfood-ural",
        "Каталог участников InterFood Ural",
        "https://www.interfood-ural.ru/ru-RU/about/exhibitor-list.aspx",
    ),
    GLOBAL_INGREDIENTS,
)
PAGE_POSTBACK = re.compile(r"""javascript:__doPostBack\('([^']+)','([0-9]+)'\);?""")
DETAIL_ID = re.compile(r"[0-9]{1,12}")
SOURCE_ERRORS = "foodsource/source_errors"
MAX_WEBSITE_LEADS = 24
EXPLICIT_INN = re.compile(r"\bИНН\s*[:№–-]?\s*(\d{12}|\d{10})\b", re.IGNORECASE)
EMAIL = re.compile(r"[a-z0-9.!#$%&'*+/=?^_\x60{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)
MOQ = re.compile(
    r"\b(?:минимальн\w+\s+(?:заказ|объем(?:\s+заказа)?|тираж)|возможност[ьи]\s+заказа)"
    r"[\s:–—-]+(от\s+\d+(?:[ ,.]\d+)*\s+(?:единиц\w*|шт\.?|кг|тонн\w*|короб\w*|руб\w*|₽))",
    re.IGNORECASE,
)

TRADE = re.compile(r"\b(?:оптом|оптов\w+|поставщик\w*|поставл\w+|дистрибьют\w+|horeca|b2b)\b")
MANUFACTURING = re.compile(r"\b(?:производств\w+|производител\w+|производи\w+|изготавлива\w+)\b")
EQUIPMENT_TRADE = re.compile(
    r"\b(?:производств\w+|производител\w+|производи\w+|постав(?:щик|к)\w*|продаж\w*)"
    r"[^.!?;]{0,45}\bоборудовани\w+\b"
)
LEAD_BUSINESS = re.compile(
    r"\b(?:оптом|оптов\w+|поставщик\w*|дистрибьют\w+|производител\w+|"
    r"производим|производит|изготавлива\w+|"
    r"собственн\w+\s+производств\w+|(?:промышленн|производственн)\w+\s+предприяти\w+)\b"
    r"|(?:^|[.!?;:])\s*производство\s+"
)
RETAIL_ONLY = re.compile(r"\bтолько\s+(?:в\s+розницу|розничн\w+)\b")
NON_VENDOR = re.compile(
    r"\b(?:журнал\w*|издательств\w*|информационно[- ]аналитическ\w+\s+агентств\w*)\b"
)
INGREDIENT_TRADE = re.compile(r"\b(?:поставля(?:ем|ет|ют)|выпуска(?:ем|ет|ют))\b")
INGREDIENT_WORD = (
    r"(?:ингредиент|добавк|ароматизатор|красител|экстракт|эмульгатор|стабилизатор|"
    r"закваск|загустител|консервант|фермент|смес|сырь|компонент)\w*"
)
FOOD_DOMAIN = (
    r"(?:(?:предприяти\w+\s+)?пищев\w+\s+(?:промышленн|производст|продукц)\w+|"
    r"производств\w+\s+продукт\w+\s+питан\w+|"
    r"(?:мясн|молочн|кондитерск|хлебопекарн)\w+\s+(?:промышленн|производст|издели)\w+)"
)
FOOD_INGREDIENTS = re.compile(
    rf"\b(?:пищев\w+\s+(?:и\s+кормов\w+\s+)?{INGREDIENT_WORD}(?:\s+и\s+{INGREDIENT_WORD})?"
    rf"|{INGREDIENT_WORD}[^.!?;]{{0,85}}\bдля\s+{FOOD_DOMAIN})\b"
)
INGREDIENT_PRODUCTS = (
    ("Пищевые добавки", r"\bдобавк\w*\b"),
    ("Ароматизаторы", r"\bароматизатор\w*\b"),
    ("Пищевые красители", r"\bкрасител\w*\b"),
    ("Экстракты", r"\bэкстракт\w*\b"),
    ("Стабилизаторы", r"\bстабилизатор\w*\b"),
    ("Эмульгаторы", r"\bэмульгатор\w*\b"),
    ("Закваски", r"\bзакваск\w*\b"),
    ("Смеси", r"\bсмес\w*\b"),
    ("Ферменты", r"\bфермент\w*\b"),
)
FROZEN = re.compile(r"\b(?:заморожен\w+|шоков\w+\s+замороз\w+|морожен(?:ое|ого|ому|ым|ом))\b")
PACKAGING_BUSINESS = re.compile(
    r"\b(?:производств\w+|производител\w+|производи\w+|изготовлен\w+|"
    r"постав(?:щик|к|ля)\w*|продаж\w*)\s+(?:[\w-]+\s+){0,4}"
    r"(?:упаковк\w+|тар[аыуе]|пакет\w*|пленок|пленк\w+|контейнер\w+|короб\w+)\b"
)
FOOD_PACKAGING = re.compile(
    r"\b(?:пищев\w+\s+(?:(?:полимерн|пластиков|картонн|бумажн)\w+\s+)?"
    r"(?:упаковк|тар|пленк|контейнер)\w*|"
    r"(?:упаковк|тар|пленк|контейнер|пакет|короб)\w*\s+для\s+(?:пищев|продукт|пицц|салат|выпечк|зелени)\w*)"
)
PRODUCT_RULES = {
    "ingredients": (
        ("Орехи", r"\bорех\w*\b"),
        ("Сухофрукты", r"\bсухофрукт\w*\b"),
        ("Пищевые ингредиенты", r"\bпищев\w+\s+ингредиент\w*\b"),
        ("Пищевые добавки", r"\bпищев\w+\s+добавк\w*\b"),
        ("Закваски", r"\bзакваск\w*\b"),
        ("Сухие смеси", r"\bсух\w+\s+смес\w*\b"),
    ),
    "packaging": (
        ("Пищевая плёнка", r"\bпищев\w+\s+пленк\w*\b"),
        ("Упаковка для пиццы", r"\b(?:упаковк|короб)\w*\s+для\s+пицц\w*\b"),
        ("Пищевые контейнеры", r"\bпищев\w+\s+контейнер\w*\b"),
        ("Пищевая упаковка", FOOD_PACKAGING.pattern),
    ),
    "food": (
        (
            "Замороженные готовые блюда",
            r"\bзаморожен\w+\s+(?:(?:готов|обеден)\w+\s+){0,2}блюд\w*\b",
        ),
        ("Мясные полуфабрикаты", r"\bмясн\w+\s+полуфабрикат\w*\b"),
        ("Колбасные изделия", r"\bколбас\w*\b"),
        ("Полуфабрикаты", r"\bполуфабрикат\w*\b"),
        ("Мясная продукция", r"\bмяс(?:о|а|у|ом|н\w+\s+(?:продук|издел))\w*\b"),
        ("Рыбная продукция", r"\b(?:рыб[ауы]|рыбопродукт\w*|рыбн\w+\s+продук\w*)\b"),
        ("Молочная продукция", r"\bмолочн\w+\s+продук\w*\b"),
        ("Сливочное масло", r"\bсливочн\w+\s+масл\w*\b"),
        ("Сыры", r"\bсыр(?:ы|ов|ом|у|а|ами|ах)\b"),
        ("Йогурты", r"\bйогурт\w*\b"),
        ("Соусы", r"\bсоус\w*\b"),
        ("Томатная паста", r"\bтоматн\w+\s+паст\w*\b"),
        ("Майонез", r"\bмайонез\w*\b"),
        ("Чай", r"\b(?:чай|чая|чаев|чаи|чаю|чаем)\b"),
        ("Шоколад", r"\bшоколад\w*\b"),
        ("Кондитерские изделия", r"\b(?:кондитерск\w+\s+издел\w*|сладост\w*)\b"),
        ("Мороженое", r"\bморожен(?:ое|ого|ому|ым|ом)\b"),
        ("Хлеб", r"\bхлеб(?:а|ом|у|ов|ы)?\b"),
        ("Выпечка", r"\bвыпечк\w*\b"),
        ("Круассаны", r"\bкруассан\w*\b"),
    ),
}
BAKERY = {"Хлеб", "Выпечка", "Круассаны", "Кондитерские изделия"}
CITY_PREFIX = re.compile(r"^(?:г\.\s*|город\s+|г\s+)")


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    fragment = html.fragment_fromstring(value, create_parent="div")
    etree.strip_elements(fragment, "script", "style", with_tail=False)
    return " ".join(" ".join(fragment.itertext()).split())


def _city(value: str) -> str:
    return CITY_PREFIX.sub("", value.casefold()).strip(" .")


def _website(value: str) -> str | None:
    for item in re.split(r"[\s,;]+", value):
        candidate = item if "://" in item else f"https://{item}"
        try:
            website = HttpUrl(candidate)
        except ValidationError:
            continue
        if "." not in (website.host or "") or website.username or website.password:
            continue
        url = str(website)
        if (domain := official_domain(url)) and domain != "vk.ru" and not domain.endswith(".vk.ru"):
            return url
    return None


def _inn(value: Any, description: str) -> str | None:
    candidate = _text(str(value)) if value is not None else ""
    reported = EXPLICIT_INN.search(description)
    if not candidate and reported:
        candidate = reported.group(1)
    if not re.fullmatch(r"[0-9]{10}|[0-9]{12}", candidate) or len(set(candidate)) == 1:
        return None
    digits = tuple(map(int, candidate))
    weights = (
        ((2, 4, 10, 3, 5, 9, 4, 6, 8),)
        if len(digits) == 10
        else ((7, 2, 4, 10, 3, 5, 9, 4, 6, 8), (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8))
    )
    check = tuple(
        sum(left * right for left, right in zip(row, digits)) % 11 % 10 for row in weights
    )
    return candidate if check == digits[-len(check) :] else None


def _categories_and_products(description: str) -> tuple[list[str], list[str]]:
    text = description.casefold().replace("ё", "е")
    if not (TRADE.search(text) or MANUFACTURING.search(text)) or EQUIPMENT_TRADE.search(text):
        return [], []
    clauses = offer_clauses(description)
    subjects = [offer_subject(clause) for clause in clauses]
    matches = {
        category: [
            label
            for label, pattern in rules
            if any(
                re.search(pattern, clause)
                for clause in (clauses if category == "packaging" else subjects)
            )
        ]
        for category, rules in PRODUCT_RULES.items()
    }
    if matches["food"] and set(matches["food"]).issubset(BAKERY) and not TRADE.search(text):
        matches["food"] = []
    if not (
        any(PACKAGING_BUSINESS.search(subject) for subject in subjects)
        and FOOD_PACKAGING.search(text)
    ):
        matches["packaging"] = []
    if "Мясные полуфабрикаты" in matches["food"]:
        matches["food"].remove("Полуфабрикаты")
    categories = [category for category, products in matches.items() if products]
    if matches["food"] and any(
        FROZEN.search(subject)
        and any(re.search(pattern, subject) for _, pattern in PRODUCT_RULES["food"])
        for subject in subjects
    ):
        categories.insert(0, "frozen")
    products = list(dict.fromkeys(item for group in matches.values() for item in group))
    return categories, products[:6]


def _can_enrich(description: str) -> bool:
    text = description.casefold().replace("ё", "е")
    return not (EQUIPMENT_TRADE.search(text) or RETAIL_ONLY.search(text)) and bool(
        LEAD_BUSINESS.search(text) or PACKAGING_BUSINESS.search(text)
    )


def _food_ingredient_products(name: str, description: str) -> list[str]:
    text = description.casefold().replace("ё", "е")
    if (
        NON_VENDOR.search(name.casefold() + " " + text)
        or EQUIPMENT_TRADE.search(text)
        or RETAIL_ONLY.search(text)
        or not (LEAD_BUSINESS.search(text) or INGREDIENT_TRADE.search(text))
    ):
        return []
    evidence = " ".join(
        clause[match.start() : min(len(subject), match.end())]
        for clause in offer_clauses(description)
        if (subject := offer_subject(clause))
        for match in FOOD_INGREDIENTS.finditer(clause)
        if match.start() < len(subject)
    )
    if not evidence:
        return []
    products = [label for label, pattern in INGREDIENT_PRODUCTS if re.search(pattern, evidence)]
    return ["Пищевые ингредиенты", *products][:6]


class MvkSpider(scrapy.Spider):
    name = "mvk"
    allowed_domains = ["foodtech-ural.ru", "interfood-ural.ru", "new.ingred.ru"]
    exhibitions = EXHIBITIONS
    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "COOKIES_ENABLED": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": 0.35,
        "DOWNLOAD_TIMEOUT": 18,
        "DOWNLOAD_MAXSIZE": 2_000_000,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 0.35,
        "AUTOTHROTTLE_MAX_DELAY": 4,
        "RETRY_TIMES": 1,
    }

    def __init__(
        self,
        city: str | None = "Екатеринбург",
        region_id: str | None = "ekaterinburg",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.city = _city(city) if city else None
        self.region_id = region_id
        self.seen_ids: dict[str, set[str]] = {show.id: set() for show in self.exhibitions}
        self.website_leads = 0
        self.other_website_leads = 0

    async def start(self) -> AsyncIterator[scrapy.Request]:
        if bool(self.city) != bool(self.region_id):
            raise CloseSpider("missing_market")
        for show in self.exhibitions:
            yield scrapy.Request(
                show.listing,
                callback=self.parse_listing,
                errback=self.request_failed,
                cb_kwargs={"show": show, "page": 1},
                meta={"cookiejar": show.id},
            )

    def parse_listing(
        self, response: HtmlResponse, show: Exhibition, page: int
    ) -> Iterator[scrapy.Request]:
        data_ids = response.css(show.item_selector).getall()
        if not data_ids or any(not DETAIL_ID.fullmatch(item) for item in data_ids):
            self.source_error(response.url, "Missing or invalid exhibitor IDs")
            return
        discovered = set(data_ids) - self.seen_ids[show.id]
        if not discovered:
            self.source_error(response.url, f"Exhibitor page {page} repeated earlier IDs")
            return
        self.seen_ids[show.id].update(discovered)
        for detail_id in dict.fromkeys(data_ids):
            if detail_id not in discovered:
                continue
            endpoint = f"{show.listing.removesuffix('.aspx')}/{show.detail_page}?id={detail_id}"
            yield scrapy.Request(
                endpoint,
                callback=self.parse_detail,
                errback=self.request_failed,
                cb_kwargs={"show": show, "detail_id": detail_id},
            )

        links = response.css(show.pager_selector)
        events = [
            event
            for link in links
            if (event := PAGE_POSTBACK.fullmatch(link.attrib.get("href", ""))) is not None
        ]
        has_following_page = any(int(event.group(2)) > page for event in events) or any(
            label.isdigit() and int(label) > page
            for link in links
            if (label := link.xpath("normalize-space(string(.))").get())
        )
        if not has_following_page:
            return
        event = next((event for event in events if int(event.group(2)) == page + 1), None)
        if event is None:
            self.source_error(response.url, f"Unrecognized navigation to exhibitor page {page + 1}")
            return
        page_document = html.fromstring(response.text, base_url=response.url)
        form = page_document.get_element_by_id("form", None)
        if not isinstance(form, html.FormElement) or form.method != "POST":
            self.source_error(response.url, "Missing or invalid exhibitor navigation form")
            return
        action = form.action
        target, origin = urlsplit(action), urlsplit(show.listing)
        if (target.scheme, target.netloc, target.path) != (
            origin.scheme,
            origin.netloc,
            origin.path,
        ):
            self.source_error(response.url, "Unexpected exhibitor form action")
            return
        postback = {"__EVENTTARGET": event.group(1), "__EVENTARGUMENT": event.group(2)}
        fields = [(key, value) for key, value in form.form_values() if key not in postback]
        yield scrapy.Request(
            action,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode(fields + list(postback.items())),
            callback=self.parse_listing,
            errback=self.request_failed,
            cb_kwargs={"show": show, "page": page + 1},
            meta={"cookiejar": show.id},
        )

    def parse_detail(self, response: Response, show: Exhibition, detail_id: str) -> dict | None:
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, ValueError):
            self.source_error(response.url, "Invalid exhibitor JSON")
            return None
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            self.source_error(response.url, "Unexpected exhibitor response shape")
            return None
        card = payload[0]
        name = _text(card.get("Name"))
        if not name or any(
            key not in card or not isinstance(card[key], str | None)
            for key in ("Region", "Description")
        ):
            self.source_error(response.url, "Invalid exhibitor name, region or description")
            return None
        city = _text(card.get("Region"))
        if not city or (self.city and _city(city) != self.city):
            return None
        if show.ingredients_only and _text(card.get("Country")).casefold() not in {
            "россия",
            "рф",
            "russia",
            "russian federation",
        }:
            return None
        local = _city(city) == "екатеринбург"

        description = _text(card.get("Description"))
        if show.ingredients_only:
            products = _food_ingredient_products(name, description)
            if not products:
                return None
            categories = ["ingredients"]
        else:
            categories, products = _categories_and_products(description)
        website = _website(_text(card.get("Site")))
        if not categories:
            leads = self.website_leads if local else self.other_website_leads
            if not website or not _can_enrich(description) or leads >= MAX_WEBSITE_LEADS:
                return None
            if local:
                self.website_leads += 1
            else:
                self.other_website_leads += 1
            self.crawler.stats.inc_value("foodsource/mvk/website_leads")
        email = EMAIL.search(_text(card.get("Email")))
        minimum_order = MOQ.search(description.casefold().replace("ё", "е"))
        return {
            "provider": self.name,
            "external_id": f"{show.id}-{detail_id}",
            "name": name,
            "inn": _inn(card.get("INN", card.get("Inn")), description),
            "website": website,
            "description": description[:600].rsplit(" ", 1)[0] + "…"
            if len(description) > 600
            else description,
            "category_ids": categories,
            "products": products,
            "city": city,
            "address": _text(card.get("Address")),
            "region_id": self.region_id or ("ekaterinburg" if local else None),
            "enrich_website": local or not categories,
            "email": email.group(0).lower() if email else None,
            "phone": _text(card.get("Phone")) or _text(card.get("PhoneOne")) or None,
            "minimum_order": minimum_order.group(1) if minimum_order else None,
            "source_url": f"{show.listing}#{detail_id}",
            "source_label": show.label,
        }

    def request_failed(self, failure: Failure) -> None:
        self.source_error(failure.request.url, failure.getErrorMessage())

    def source_error(self, url: str, reason: str) -> None:
        self.crawler.stats.inc_value(SOURCE_ERRORS)
        self.logger.warning("%s: %s", reason, url)
