"""Discover food producers listed by the Sverdlovsk procurement portal."""

import hashlib
import re
from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlsplit

from parsel import Selector
from scrapy import Request, Spider
from scrapy.http import HtmlResponse
from twisted.python.failure import Failure

from ..identity import name_key
from ..text import normalize_text
from .normalize import company_url, email_address, phone_number, valid_inn

REGISTRY_URL = "https://torgi.egov66.ru/site/Menu/Page/526"
REGISTRY_LABEL = "Перечень товаропроизводителей Свердловской области"
PROVIDER = "sverdlovsk-registry"

HEADERS = {
    "наименование организации": "name",
    "адрес": "address",
    "контактный телефон": "phone",
    "электронная почта": "email",
    "официальный сайт": "website",
    "инн": "inn",
}

PRODUCT_GROUPS = {
    "птицефабрики": "Продукция птицеводства",
    "молочные комбинаты": "Молочная продукция",
    "хлебокомбинаты": "Хлебобулочные изделия",
    "мясокомбинаты": "Мясная продукция",
    "сыродельные комбинаты": "Сыры",
    "рыбоперерабатывающие предприятия": "Рыбная продукция",
    "организации занимающиеся переработкой и консервировапнием картофеля, плодов, ягод, овощей": (
        "Продукты переработки картофеля, овощей, плодов и ягод"
    ),
    "организации занимающиеся переработкой и консервированием картофеля, плодов, ягод, овощей": (
        "Продукты переработки картофеля, овощей, плодов и ягод"
    ),
}

EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE | re.ASCII)
PHONE = re.compile(r"(?<!\d)(?:\+?7|8)(?:[\s().-]*[0-9]){10}(?!\d)")
CITY_TOKEN = re.compile(r"(?<![\w-])екатеринбург(?![\w-])")


def _normalized(value: str) -> str:
    return " ".join(normalize_text(value).split())


def _cell_text(node: Selector) -> str:
    return " ".join(node.xpath("string(.)").get(default="").split())


def _parsed_contact(cell: Selector, pattern: re.Pattern[str]) -> str | None:
    values = [
        *cell.xpath(".//a/@href").getall(),
        *cell.xpath(".//text()").getall(),
        _cell_text(cell),
    ]
    for value in values:
        match = pattern.search(value)
        if match:
            return match.group()
    return None


def _website(cell: Selector) -> str | None:
    candidates = [*cell.xpath(".//a/@href").getall(), _cell_text(cell)]
    for candidate in candidates:
        value = candidate.strip()
        if value.startswith("//"):
            value = f"https:{value}"
        if url := company_url(value):
            return url
    return None


def _external_id(inn: str | None, name: str, address: str) -> str:
    if inn:
        return f"inn-{inn}"
    canonical = f"{name_key(name)}|{name_key(address)}"
    return f"name-{hashlib.sha256(canonical.encode()).hexdigest()[:24]}"


def _table_rows(response: HtmlResponse) -> tuple[list[Selector], dict[str, int]] | None:
    for table in response.xpath("//table"):
        rows = table.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")
        for index, row in enumerate(rows):
            columns = {
                HEADERS[label]: position
                for position, cell in enumerate(row.xpath("./td | ./th"))
                if (label := _normalized(_cell_text(cell))) in HEADERS
            }
            if len(columns) == len(HEADERS):
                return rows[index + 1 :], columns
    return None


class RegionalSpider(Spider):
    """Read Ekaterinburg companies from the official food producer groups."""

    name = "regional"
    allowed_domains = ["torgi.egov66.ru"]

    def __init__(self, city: str = "Екатеринбург", region_id: str = "ekaterinburg", **kwargs):
        super().__init__(**kwargs)
        self.city = city
        self.region_id = region_id

    async def start(self) -> AsyncIterator[Request]:
        if _normalized(self.city) != "екатеринбург" or self.region_id != "ekaterinburg":
            self._source_error("The regional registry adapter only supports Ekaterinburg")
            return
        yield Request(REGISTRY_URL, callback=self.parse, errback=self._request_error)

    def _source_error(self, reason: str) -> None:
        if (stats := getattr(getattr(self, "crawler", None), "stats", None)) is not None:
            stats.inc_value("foodsource/source_errors")
        self.logger.warning("%s: %s", PROVIDER, reason)

    def _request_error(self, failure: Failure) -> None:
        self._source_error(failure.getErrorMessage())

    def parse(self, response: HtmlResponse, **kwargs) -> Iterator[dict[str, object]]:
        source = urlsplit(response.url)
        if (
            response.status != 200
            or source.hostname != "torgi.egov66.ru"
            or source.path.rstrip("/").casefold() != "/site/menu/page/526"
        ):
            self._source_error("Unexpected registry URL or HTTP status")
            return
        parsed = _table_rows(response)
        if parsed is None:
            self._source_error("Registry producer table or required headings are missing")
            return

        rows, columns = parsed
        minimum_cells = max(columns.values()) + 1
        group = ""
        product = None
        supported_rows = 0
        for row in rows:
            cells = row.xpath("./td | ./th")
            if len(cells) == 1 and cells[0].attrib.get("colspan") == "7":
                group = _cell_text(cells[0])
                product = PRODUCT_GROUPS.get(_normalized(group))
                continue
            if product is None or len(cells) < minimum_cells:
                continue

            name = _cell_text(cells[columns["name"]])
            address = _cell_text(cells[columns["address"]])
            if not name or not address:
                continue
            supported_rows += 1
            if not CITY_TOKEN.search(_normalized(address)):
                continue

            inn = valid_inn(re.sub(r"\s+", "", _cell_text(cells[columns["inn"]])))
            phone = _parsed_contact(cells[columns["phone"]], PHONE)
            yield {
                "provider": PROVIDER,
                "external_id": _external_id(inn, name, address),
                "name": name,
                "inn": inn,
                "website": _website(cells[columns["website"]]),
                "description": f"В региональном перечне указан в группе «{group.lower()}».",
                "category_ids": ["food"],
                "products": [product],
                "region_id": self.region_id,
                "email": email_address(_parsed_contact(cells[columns["email"]], EMAIL)),
                "phone": phone_number(phone.replace(".", " ") if phone else None),
                "source_url": REGISTRY_URL,
                "source_label": REGISTRY_LABEL,
            }

        if supported_rows == 0:
            self._source_error("No rows under recognized food producer headings")
