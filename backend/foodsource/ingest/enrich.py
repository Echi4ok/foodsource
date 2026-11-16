import asyncio
import ipaddress
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import extruct
from protego import Protego
from scrapy import Request
from scrapy.crawler import Crawler
from scrapy.http import HtmlResponse, Response
from twisted.internet.error import DNSLookupError, TimeoutError

from .normalize import clean, company_url, email_address, official_domain, phone_number
from .qualification import qualifying_products, qualifying_wholesale_food
from .website_location import website_contact_page, website_location

SALES_LABEL = re.compile(
    r"(?:при[её]м заявок|при[её]м заказов|отдел (?:оптовых )?продаж|оптовый отдел)\s*:?",
    re.IGNORECASE,
)
RUSSIAN_PHONE = re.compile(r"(?<![\d+])(?:\+7|8)(?:[\s()\-]*\d){10}(?!\d)")


def structured_nodes(value: object) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for key in ("@graph", "items", "itemListElement", "contactPoint"):
            yield from structured_nodes(value.get(key))
    elif isinstance(value, list):
        for entry in value:
            yield from structured_nodes(entry)


def sales_phone(response: HtmlResponse) -> str | None:
    for label in response.xpath(
        "//*[not(*) and not(ancestor::script or ancestor::style or ancestor::noscript)]"
    ):
        if not SALES_LABEL.fullmatch(clean(label.xpath("string(.)").get())):
            continue
        for depth in (1, 2):
            section = label.xpath(f"ancestor::*[{depth}]")
            chunks = section.xpath(".//text()").getall()
            if sum(map(len, chunks)) > 350:
                break
            after_label = False
            for chunk in chunks:
                if SALES_LABEL.fullmatch(clean(chunk)):
                    after_label = True
                elif after_label and (match := RUSSIAN_PHONE.search(chunk)):
                    return phone_number(match[0])
    return None


def website_facts(response: HtmlResponse) -> dict[str, Any]:
    markup = extruct.extract(
        response.body,
        base_url=response.url,
        syntaxes=["json-ld", "microdata"],
        errors="ignore",
    )
    products = set()
    phones = {
        phone
        for link in response.css("a[href^='tel:']::attr(href)").getall()
        if (phone := phone_number(unquote(link[4:])))
    }
    emails = {
        email
        for link in response.css("a[href^='mailto:']::attr(href)").getall()
        if (email := email_address(unquote(link[7:].split("?", 1)[0])))
    }
    for node in structured_nodes(list(markup.values())):
        kinds = node.get("@type") or node.get("type") or ()
        if isinstance(kinds, str):
            kinds = (kinds,)
        if not isinstance(kinds, (list, tuple)):
            continue
        schema = {kind.rsplit("/", 1)[-1] for kind in kinds if isinstance(kind, str)}
        properties = node.get("properties") or node
        if not isinstance(properties, dict):
            continue
        if schema & {"Organization", "LocalBusiness", "ContactPoint", "Store", "FoodEstablishment"}:
            if phone := phone_number(properties.get("telephone")):
                phones.add(phone)
            if email := email_address(properties.get("email")):
                emails.add(email)
        if "Product" in schema and (name := clean(properties.get("name"))) and len(name) <= 120:
            products.add(name)
    visible_text = clean(
        " ".join(
            response.xpath(
                "//body//text()[not(ancestor::script or ancestor::style or ancestor::noscript)]"
            ).getall()
        )
    )[:80_000]
    groups = qualifying_products(visible_text + " " + " ".join(products))
    food = set()
    for section in response.xpath(
        "//title | //meta[@name='description']/@content | "
        "//*[self::h1 or self::h2 or self::h3 or self::p][not(ancestor::nav or ancestor::footer)]"
    )[:180]:
        if group := clean(section.xpath("string(.)").get()):
            food.update(qualifying_wholesale_food(group[:2000]))
    if food:
        groups["food"] = food
    return {
        "phone": next(iter(phones)) if len(phones) == 1 else sales_phone(response),
        "email": next(iter(emails)) if len(emails) == 1 else None,
        "products": sorted(products, key=str.casefold)[:8],
        "groups": groups,
    }


async def public_dns(url: str) -> bool:
    host = urlsplit(url).hostname
    if host is None or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        answers = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, None), timeout=4
        )
        return bool(answers) and all(
            ipaddress.ip_address(result[4][0]).is_global for result in answers
        )
    except (TimeoutError, OSError, ValueError, asyncio.TimeoutError):
        return False


class OfficialWebsitePipeline:
    def __init__(self, crawler: Crawler):
        self.crawler = crawler
        self.pending: dict[str, asyncio.Task[tuple[str, dict[str, Any]] | None]] = {}
        self.location_pending: dict[str, asyncio.Task[tuple[str, dict[str, Any]] | None]] = {}
        self.limit = asyncio.Semaphore(4)

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "OfficialWebsitePipeline":
        return cls(crawler)

    async def process_item(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("enrich_website") is False:
            return item
        website = company_url(item.get("website"))
        domain = official_domain(website)
        if item.get("locate_website") and website and domain:
            return await self.locate_item(item, website, domain)
        enriched = dict(item)
        if website and domain:
            if website not in self.pending:
                self.pending[website] = asyncio.create_task(self.inspect(website, domain))
            if result := await self.pending[website]:
                url, facts = result
                fields = []
                for field, normalize in (("phone", phone_number), ("email", email_address)):
                    if facts[field] and not normalize(item.get(field)):
                        enriched[field] = facts[field]
                        fields.append(field)

                groups = facts["groups"]
                categories = set(item["category_ids"])
                expected = set(item.get("candidate_categories", ()))
                if not categories:
                    groups = {
                        key: values
                        for key, values in groups.items()
                        if not expected or key in expected
                    }
                elif categories == {"food"}:
                    groups = {key: value for key, value in groups.items() if key == "frozen"}
                else:
                    groups = {key: value for key, value in groups.items() if key in categories}
                supplemental = set().union(*groups.values())
                if groups:
                    if set(groups) - categories:
                        enriched["category_ids"] = sorted(categories | set(groups))
                        fields.append("category_ids")
                    original = set(item.get("products", ()))
                    structured = set(facts["products"]) if categories else set()
                    products = original | supplemental | structured
                    if products != original:
                        enriched["products"] = sorted(products, key=str.casefold)
                        fields.append("products")
                if fields:
                    enriched.update(
                        enrichment_url=url,
                        enriched_fields=fields,
                        new_fields=[field for field in fields if not item.get(field)],
                    )
            else:
                enriched["website_unavailable"] = True
        return enriched

    async def inspect(self, url: str, domain: str) -> tuple[str, dict[str, Any]] | None:
        async with self.limit:
            try:
                for _ in range(3):
                    if not await self.robots_allow(url, domain):
                        return None
                    response = await self.download_from_origin(url, domain)
                    if response is None:
                        return None
                    if response.status in {301, 302, 303, 307, 308} and (
                        target := response.headers.get(b"Location")
                    ):
                        url = urljoin(url, target.decode("latin-1"))
                        continue
                    if response.status == 200 and isinstance(response, HtmlResponse):
                        facts = website_facts(response)
                        if facts["groups"]:
                            return response.url, facts
                        if related := candidate_product_page(response, domain):
                            url = related
                            continue
                        return response.url, facts
                    return None
            except (DNSLookupError, TimeoutError, OSError, ValueError):
                return None
            except Exception as error:
                if self.crawler.stats:
                    self.crawler.stats.inc_value(
                        f"foodsource/enrichment_skipped/{type(error).__name__}"
                    )
        return None

    async def locate_item(self, item: dict[str, Any], url: str, domain: str) -> dict[str, Any]:
        if url not in self.location_pending:
            self.location_pending[url] = asyncio.create_task(self.inspect_location(url, domain))
        result = await self.location_pending[url]
        if result is None:
            return item
        source, facts = result
        if self.crawler.stats:
            self.crawler.stats.inc_value(f"foodsource/{item['provider']}/located")
        contacts = {
            field: facts[field]
            for field in ("phone", "email")
            if facts.get(field) and not item.get(field)
        }
        fields = ["region_id", *contacts]
        return {
            **item,
            "city": facts["city"],
            "address": facts["address"],
            "region_label": facts["region_label"],
            **contacts,
            "enrichment_url": source,
            "enriched_fields": fields,
            "new_fields": fields,
        }

    async def inspect_location(self, url: str, domain: str) -> tuple[str, dict[str, Any]] | None:
        async with self.limit:
            try:
                for _ in range(5):
                    if not await self.robots_allow(url, domain):
                        return None
                    response = await self.download_from_origin(url, domain)
                    if response is None:
                        return None
                    if response.status in {301, 302, 303, 307, 308} and (
                        target := response.headers.get(b"Location")
                    ):
                        url = urljoin(url, target.decode("latin-1"))
                        continue
                    if response.status != 200 or not isinstance(response, HtmlResponse):
                        return None
                    if location := website_location(response):
                        city, address, region = location
                        contacts = website_facts(response)
                        return response.url, {
                            "city": city,
                            "address": address,
                            "region_label": region,
                            "phone": contacts["phone"],
                            "email": contacts["email"],
                        }
                    if related := website_contact_page(response, domain):
                        url = related
                        continue
                    return None
            except (DNSLookupError, TimeoutError, OSError, ValueError):
                return None
            except Exception as error:
                if self.crawler.stats:
                    self.crawler.stats.inc_value(
                        f"foodsource/location_skipped/{type(error).__name__}"
                    )
        return None

    async def robots_allow(self, url: str, domain: str) -> bool:
        robots_url = urljoin(url, "/robots.txt")
        for _ in range(6):
            robots = await self.download_from_origin(robots_url, domain)
            if robots is None:
                return False
            if robots.status == 200:
                rules = Protego.parse(robots.body.decode("utf-8", errors="replace"))
                return rules.can_fetch(url, self.crawler.settings.get("USER_AGENT"))
            if robots.status in {404, 410}:
                return True
            target = robots.headers.get(b"Location")
            if robots.status not in {301, 302, 303, 307, 308} or not target:
                return False
            robots_url = urljoin(robots_url, target.decode("latin-1"))
        return False

    async def download_from_origin(self, url: str, domain: str) -> Response | None:
        engine = self.crawler.engine
        parts = urlsplit(url)
        if (
            engine is None
            or official_domain(url) != domain
            or parts.scheme not in {"http", "https"}
            or parts.port not in {None, 80, 443}
            or not await public_dns(url)
        ):
            return None
        return await engine.download_async(
            Request(
                url,
                priority=-100,
                meta={
                    "allow_offsite": True,
                    "dont_obey_robotstxt": True,
                    "dont_redirect": True,
                    "dont_retry": True,
                    "download_timeout": 10,
                },
            )
        )


def candidate_product_page(response: HtmlResponse, domain: str) -> str | None:
    generic = None
    for anchor in response.css("a[href]"):
        name = clean(anchor.xpath("string(.)").get()).casefold()
        if not name or len(name) > 75:
            continue
        target = company_url(urljoin(response.url, anchor.attrib["href"]))
        if not target or target == response.url or official_domain(target) != domain:
            continue
        if any(word in name for word in ("пищев", "заморожен", "ингредиент", "для пиццы")):
            return target
        if generic is None and name in {"каталог", "продукция", "ассортимент"}:
            generic = target
    return generic
