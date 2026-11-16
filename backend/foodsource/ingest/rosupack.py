import re
from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlencode, urlsplit

import scrapy
from scrapy.http import HtmlResponse
from twisted.python.failure import Failure

from foodsource.text import offer_clauses

from .normalize import clean, company_url, official_domain
from .qualification import COMMERCE, qualifying_products

PROVIDER = "rosupack"
HOST = "exhibitors-itegroup.exhibitoronlinemanual.com"
EDITION_PATH = re.compile(r"/rosupack-(?P<year>20\d{2})/ru/Exhibitor/?")
DETAIL_ID = re.compile(r"[a-zA-Z0-9+%=_-]{1,60}")
DEFAULT_URL = f"https://{HOST}/rosupack-2026/ru/Exhibitor"
LIST_WINDOW = re.compile(r"от\s+(\d+)\s+до\s+(\d+)\s+из\s+(\d+)", re.I)
MAX_EXHIBITORS = 3000
EXCLUDED_OFFER = re.compile(
    r"\b(?:оборудовани\w*|станк\w*|пресс[- ]форм\w*|технологическ\w+\s+лини\w*|"
    r"представител\w+\s+пищев\w+|сервис\w*|заказчик\w*)\b",
    re.I,
)
PACKAGE = re.compile(
    r"\b(?:упаковк\w*|тар[аыуыое]|контейнер\w*|лот[ко]\w*|бан[ко]\w*|"
    r"бутыл\w*|пакет\w*|пленк\w*|стакан\w*|короб\w*)\b",
    re.I,
)
FOOD_APPLICATION = re.compile(
    r"\b(?:horeca|foodservice|пищев\w+\s+(?:продукт\w*|промышленност\w*|упаковк\w*)|"
    r"для\s+(?:(?:молочн\w+|мясн\w+|рыбн\w+)\s+продукт\w*|пицц\w*|сыр\w*|"
    r"молок\w*|десерт\w*|готов\w+\s+блюд\w*|напитк\w*|пищ\w*|ед[ыу]|суши|салат\w*)|"
    r"для\s+контакта\s+с\s+(?:пищ\w*|пищев\w+\s+продукт\w*))\b",
    re.I,
)


def page_form(response: HtmlResponse) -> dict[str, str] | None:
    event = response.css("#selected_pass_event::attr(value)").get("")
    sector = next(
        (
            option.attrib.get("value", "")
            for option in response.css("option.category-option")
            if (label := clean(option.xpath("string(.)").get()).casefold()).startswith(
                "готовая упаковка"
            )
            and "стеклянная тара" in label
        ),
        "",
    )
    if not event.isdecimal() or not sector.isdecimal():
        return None
    if "Russia" not in response.css("#country_id option::attr(value)").getall():
        return None
    return {
        "keywords": "",
        "keyword_search": "",
        "country_id": "Russia",
        "event_prod_cat_id": sector,
        "exb_listed_as": "",
        "sortBy": "",
        "event_id": event,
        "InitialKey": "",
        "ExhibitorDataView": response.css("#ExhibitorDataView::attr(value)").get("1"),
        "certificationId": "",
        "language": "ru",
        "sub_product": "",
        "sectorData": "{}",
        "sub_sub_product": "",
    }


def packaging_groups(description: str, titles: list[str]) -> list[str]:
    groups: set[str] = set()
    offer = [clause for clause in offer_clauses(description) if COMMERCE.search(clause)]
    for clause in (*offer, *(f"Поставляем {title}" for title in titles)):
        if EXCLUDED_OFFER.search(clause):
            continue
        groups.update(qualifying_products(clause).get("packaging", ()))
        if PACKAGE.search(clause) and FOOD_APPLICATION.search(clause):
            groups.add("Пищевая упаковка")
    return sorted(groups)


def detail_item(response: HtmlResponse, identifier: str, year: str) -> dict | None:
    header = response.css(".company-details")
    name = clean(header.css("h1.company-title").xpath("string(.)").get())
    country = clean(
        header.css("h1.company-title").xpath("../../following-sibling::h6[1]/text()").get()
    )
    if not name or country.casefold() not in {"россия", "russia"}:
        return None
    sectors = [
        clean(node.xpath("string(.)").get()).casefold() for node in header.css("td.parent-cat")
    ]
    if not any(sector.startswith("готовая упаковка") for sector in sectors):
        return None
    website = company_url(header.css(".company-info a:not(.connect_now)::attr(href)").get())
    if not website or not official_domain(website):
        return None
    description = clean(
        " ".join(response.css("#pills-About p ::text, #pills-About p::text").getall())
    )
    titles = [clean(title) for title in response.css(".product-gallery figcaption::text").getall()]
    groups = packaging_groups(description, titles)
    if not groups:
        return None
    return {
        "provider": PROVIDER,
        "external_id": f"{year}:{identifier}",
        "name": name,
        "city": "",
        "website": website,
        "description": description[:400],
        "category_ids": ["packaging"],
        "products": groups,
        "source_url": response.url,
        "source_label": f"Каталог участников RosUpack {year}",
        "locate_website": True,
    }


class RosUpackSpider(scrapy.Spider):
    name = PROVIDER
    allowed_domains = [HOST]
    custom_settings = {"CONCURRENT_REQUESTS_PER_DOMAIN": 2, "DOWNLOAD_DELAY": 0.45}

    def __init__(self, edition_url: str = DEFAULT_URL, **kwargs):
        super().__init__(**kwargs)
        url = urlsplit(edition_url)
        edition = EDITION_PATH.fullmatch(url.path)
        if url.scheme != "https" or url.netloc != HOST or not edition or url.query or url.fragment:
            raise ValueError("RosUpack requires a public Russian exhibitor listing URL")
        self.edition_url = edition_url.rstrip("/")
        self.year = edition["year"]
        self.seen: set[str] = set()

    async def start(self) -> AsyncIterator[scrapy.Request]:
        yield scrapy.Request(
            self.edition_url, callback=self.parse_index, errback=self.request_failed
        )

    def parse_index(self, response: HtmlResponse) -> scrapy.Request | None:
        form = page_form(response)
        if form is None:
            self.source_error(response.url, "Packaging or country filter is missing")
            return None
        return self.page_request(form, 0)

    def page_request(self, form: dict[str, str], offset: int) -> scrapy.Request:
        return scrapy.Request(
            f"https://{HOST}/ru/Exhibitor/ajaxPaginationData/{offset}",
            method="POST",
            body=urlencode({**form, "page": offset}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            callback=self.parse_list,
            cb_kwargs={"form": form, "offset": offset},
            errback=self.request_failed,
        )

    def parse_list(
        self, response: HtmlResponse, form: dict[str, str], offset: int
    ) -> Iterator[scrapy.Request]:
        status = clean(response.css(".pagination-info").xpath("string(.)").get())
        window = LIST_WINDOW.search(status)
        links = response.css(".post-list h5.card-title a::attr(href)").getall()
        if not window:
            self.source_error(response.url, "Pagination window missing")
            return
        first, last, total = map(int, window.groups())
        if (
            first != offset + 1
            or not first <= last <= total <= MAX_EXHIBITORS
            or len(links) != last - first + 1
        ):
            self.source_error(response.url, "Unexpected pagination window")
            return
        detail_prefix = f"{self.edition_url}/ExbDetails/"
        for link in links:
            if link == "javascript:void(0)":
                self.count("unpublished_detail")
                continue
            identifier = link.removeprefix(detail_prefix)
            if identifier == link or not DETAIL_ID.fullmatch(identifier) or identifier in self.seen:
                self.source_error(response.url, "Malformed or repeated exhibitor link")
                continue
            self.seen.add(identifier)
            self.count("listed")
            yield scrapy.Request(
                link,
                callback=self.parse_detail,
                cb_kwargs={"identifier": identifier},
                errback=self.detail_failed,
            )
        if last < total:
            yield self.page_request(form, last)

    def parse_detail(self, response: HtmlResponse, identifier: str) -> dict | None:
        if item := detail_item(response, identifier, self.year):
            self.count("qualified")
            return item
        return None

    def request_failed(self, failure: Failure) -> None:
        self.source_error(failure.request.url, failure.getErrorMessage())

    def detail_failed(self, failure: Failure) -> None:
        self.count("detail_unavailable")
        self.request_failed(failure)

    def count(self, key: str) -> None:
        if self.crawler.stats:
            self.crawler.stats.inc_value(f"foodsource/{PROVIDER}/{key}")

    def source_error(self, url: str, reason: str) -> None:
        if self.crawler.stats:
            self.crawler.stats.inc_value("foodsource/source_errors")
        self.logger.warning("%s: %s", reason, url)
