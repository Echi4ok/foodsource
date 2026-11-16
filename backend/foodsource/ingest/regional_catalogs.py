import re
from collections.abc import AsyncIterator, Iterator
from datetime import date
from urllib.parse import quote, urlsplit

import scrapy
from scrapy.http import HtmlResponse
from twisted.python.failure import Failure

from foodsource.identity import name_key
from foodsource.text import normalize_text

from .normalize import clean, company_url, email_address, phone_number

SIBERIA = "https://prod-week.ru/"
PRIMORYE = "https://primexport.ru"
PRIMORYE_SECTIONS = ("ryba-i-moreprodukty", "apk-agropromyshlennyj-kompleks")
SIBERIA_LIST = re.compile(r"/exponents-(20\d{2})/?")
EXPORTER_PAGE = re.compile(r"/ru/exporters/[a-z0-9-]+/?")
COLLECTIVE = re.compile(r"\b(?:коллективн\w+ стенд|ярмарк\w+ товаропроизводител\w+)\b")
SERVICES = re.compile(r"\b(?:оборудовани\w*|маркировк\w*|банковск\w+ услуг\w*|маркетплейс\w*)\b")
PACKAGING = re.compile(r"\b(?:упаковк\w*|тар[ауы]|пленк\w*)\s+для\s+(?:пищев\w*|ресторан\w*)")
INGREDIENTS = re.compile(r"\bпищев\w*\s+(?:ингридиент|ингредиент|добавк)\w*")
FOOD = re.compile(
    r"\b(?:продукт\w+ питани\w*|кондитерск\w+|молочн\w+|масложиров\w+|хлебопекарн\w*|"
    r"мяс\w*|рыб\w*|морепродукт\w*|овощ\w*|фрукт\w*|орех\w*|ягод\w*|снек\w*|"
    r"питьев\w+ вод\w*|минеральн\w+ вод\w*|лечебно-столов\w+ вод\w*|"
    r"напитк\w*|сок\w*|сироп\w*|растительн\w+ масл\w*|(?:эко-)?масл\w*|"
    r"мед[ау]?|шоколад\w*|печень\w*|кофе|форел\w*|яйц\w*)\b"
)
FROZEN = re.compile(r"\b(?:замороженн\w*|морожен\w*)\b")
ALCOHOL = re.compile(r"\b(?:пив[оау]|алкогол\w+|винодели\w*)\b")
NON_FOOD = re.compile(r"\b(?:косметическ\w*|корм\w*|рыбн\w+ мук\w*|техническ\w*)\b")
COMMODITY = re.compile(r"^(\d{4,10}(?:[ \u00a0]\d{2,6})*)\s*[:—–-]\s*(.+)$")
FOOD_CHAPTERS = frozenset(
    {"02", "03", "04", "07", "08", "09", "10", "11", "15", "16", "17", "18", "19", "20", "21"}
)
PUBLIC_EMAIL = re.compile(r"[\w.!#$%&'+/=-]+@[\w.-]+\.[A-Za-z]{2,}", re.ASCII)
PUBLIC_PHONE = re.compile(r"(?<!\d)(?:\+7|8)[ ()-]*(?:\d[ ()-]*){10}(?!\d)")


def siberia_category(section: str) -> str | None:
    text = normalize_text(section)
    if SERVICES.search(text) or NON_FOOD.search(text) or ALCOHOL.search(text):
        return None
    if PACKAGING.search(text):
        return "packaging"
    if INGREDIENTS.search(text):
        return "ingredients"
    if FOOD.search(text):
        return "frozen" if FROZEN.search(text) else "food"
    return None


def siberia_items(response: HtmlResponse) -> Iterator[dict]:
    for row in response.css("table.dataTable tbody > tr"):
        cells = row.xpath("./td")
        if len(cells) != 5:
            continue
        name, city, _, section, _ = (clean(cell.attrib.get("data-search")) for cell in cells)
        category = siberia_category(section)
        if not name or not city or not category or COLLECTIVE.search(normalize_text(name)):
            continue
        website = company_url(
            cells[4].css("a::attr(href)").get() or cells[4].attrib.get("data-search")
        )
        yield {
            "provider": SiberianFoodWeekSpider.name,
            "external_id": f"{name_key(name)} | {name_key(city)}",
            "name": name,
            "city": city,
            "description": section,
            "products": [section],
            "category_ids": [category],
            "website": website,
            "source_url": f"{response.url}#:~:text={quote(name, safe='')}",
            "source_label": f"Сибирская продовольственная неделя {urlsplit(response.url).path.rsplit('-', 1)[-1].strip('/')}",
        }


def _facts(response: HtmlResponse) -> dict[str, scrapy.Selector]:
    return {
        normalize_text(clean(label.xpath("string(.)").get())).rstrip(":"): label.xpath("parent::p")[
            0
        ]
        for label in response.xpath("//p/span[contains(@class, 'font-medium')]")
    }


def _value(facts: dict[str, scrapy.Selector], label: str) -> str:
    paragraph = facts.get(label)
    return clean(" ".join(paragraph.xpath("./text()").getall())) if paragraph is not None else ""


def _commodities(response: HtmlResponse) -> Iterator[tuple[str, str]]:
    headings = response.xpath("//h3[contains(normalize-space(.), 'Перечень товаров')]")
    for raw in headings.xpath("following-sibling::div[1]//*[self::p or self::li]"):
        text = clean(raw.xpath("string(.)").get())
        match = COMMODITY.fullmatch(text)
        if not match or match[1][:2] not in FOOD_CHAPTERS:
            continue
        product = clean(match[2])
        normalized = normalize_text(product)
        if NON_FOOD.search(normalized) or ALCOHOL.search(normalized):
            continue
        yield ("frozen" if FROZEN.search(normalized) else "food"), product


def primorye_item(response: HtmlResponse) -> dict | None:
    facts = _facts(response)
    name = clean(response.xpath("normalize-space(string((//h3[b])[1]))").get())
    address = _value(facts, "фактический адрес") or _value(facts, "юридический и фактический адрес")
    if not address:
        address = _value(facts, "адрес")
    commodities = tuple(_commodities(response))
    if not name or not address or not commodities:
        return None
    contacts = _value(facts, "контакты")
    phones = {phone for value in PUBLIC_PHONE.findall(contacts) if (phone := phone_number(value))}
    emails = {email for value in PUBLIC_EMAIL.findall(contacts) if (email := email_address(value))}
    site = facts.get("сайт")
    return {
        "provider": PrimoryeExportSpider.name,
        "external_id": urlsplit(response.url).path.rstrip("/").rsplit("/", 1)[-1],
        "name": name,
        "address": address,
        "region_label": "Приморский край",
        "inn": _value(facts, "инн"),
        "products": sorted({product for _, product in commodities}, key=str.casefold),
        "category_ids": sorted({category for category, _ in commodities}),
        "website": company_url(site.css("a::attr(href)").get()) if site is not None else None,
        "email": next(iter(emails)) if len(emails) == 1 else None,
        "phone": next(iter(phones)) if len(phones) == 1 else None,
        "source_url": response.url,
        "source_label": "Центр поддержки экспорта Приморского края",
    }


class DirectorySpider(scrapy.Spider):
    def source_error(self, url: str, message: str) -> None:
        if self.crawler.stats:
            self.crawler.stats.inc_value("foodsource/source_errors")
        self.logger.warning("%s: %s", message, url)

    def request_failed(self, failure: Failure) -> None:
        self.source_error(failure.request.url, failure.getErrorMessage())


class SiberianFoodWeekSpider(DirectorySpider):
    name = "siberian-food-week"
    allowed_domains = ["prod-week.ru"]

    async def start(self) -> AsyncIterator[scrapy.Request]:
        yield scrapy.Request(SIBERIA, errback=self.request_failed)

    def parse(self, response: HtmlResponse, **kwargs) -> Iterator[scrapy.Request]:
        editions = {
            int(match[1]): response.urljoin(href)
            for href in response.css("a[href]::attr(href)").getall()
            if (parts := urlsplit(response.urljoin(href))).hostname in self.allowed_domains
            and (match := SIBERIA_LIST.fullmatch(parts.path))
            and int(match[1]) <= date.today().year
        }
        if not editions:
            self.source_error(response.url, "Exhibitor listing not found")
            return
        yield scrapy.Request(
            editions[max(editions)], self.parse_edition, errback=self.request_failed
        )

    def parse_edition(self, response: HtmlResponse) -> Iterator[dict]:
        if not response.css("table.dataTable tbody > tr"):
            self.source_error(response.url, "Exhibitor table not found")
            return
        yield from siberia_items(response)


class PrimoryeExportSpider(DirectorySpider):
    name = "primorye-export"
    allowed_domains = ["primexport.ru"]

    async def start(self) -> AsyncIterator[scrapy.Request]:
        for section in PRIMORYE_SECTIONS:
            yield scrapy.Request(f"{PRIMORYE}/ru/katalog/{section}", errback=self.request_failed)

    def parse(self, response: HtmlResponse, **kwargs) -> Iterator[scrapy.Request]:
        urls = set()
        for href in response.css("h2 a::attr(href)").getall():
            url = response.urljoin(href)
            target = urlsplit(url)
            if target.hostname in self.allowed_domains and EXPORTER_PAGE.fullmatch(target.path):
                urls.add(url)
        if not urls:
            self.source_error(response.url, "Exporter listing not found")
        for url in sorted(urls):
            yield scrapy.Request(url, self.parse_exporter, errback=self.request_failed)

    def parse_exporter(self, response: HtmlResponse) -> dict | None:
        return primorye_item(response)
