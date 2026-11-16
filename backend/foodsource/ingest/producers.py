"""Collect attributable food-packaging offers from official catalog pages."""

import json
import re
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path
from typing import Any

import scrapy
from scrapy.http import HtmlResponse
from twisted.python.failure import Failure

from .enrich import structured_nodes
from .normalize import clean, company_url, email_address, official_domain, phone_number, valid_inn
from .purchase_terms import minimum_order, schema_type
from .qualification import qualifying_products

PROVIDER = "producer-sites"
CATEGORY_SOURCES = Path(__file__).with_name("producer_catalogs.json")
NON_OFFER_HEADLINE = re.compile(
    r"\b(?:оборудовани\w*|станк\w*|машин\w*|инструкци\w*|как изготовить|своими руками)\b",
    re.IGNORECASE,
)


def catalog_pages() -> tuple[str, ...]:
    urls = json.loads(CATEGORY_SOURCES.read_text(encoding="utf-8"))
    if (
        not isinstance(urls, list)
        or not urls
        or any(
            not isinstance(url, str)
            or not url.startswith("https://")
            or not official_domain(company_url(url))
            for url in urls
        )
    ):
        raise ValueError("Producer catalog configuration must contain public HTTPS URLs")
    return tuple(dict.fromkeys(urls))


def jsonld_nodes(response: HtmlResponse) -> Iterator[dict[str, Any]]:
    for value in response.css('script[type="application/ld+json"]::text').getall():
        try:
            document = json.loads(value)
        except ValueError:
            continue
        yield from structured_nodes(document)


def local_seller(
    response: HtmlResponse, nodes: Iterable[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    matches: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    domain = official_domain(response.url)
    for node in nodes:
        if not (schema_type(node, "Organization") or schema_type(node, "LocalBusiness")):
            continue
        site = company_url(node.get("url"))
        address = node.get("address")
        if not site or official_domain(site) != domain or not isinstance(address, dict):
            continue
        if not all(
            (
                clean(node.get("name")),
                clean(address.get("addressLocality")),
                clean(address.get("streetAddress")),
            )
        ):
            continue
        if clean(address.get("addressCountry")).casefold() not in {"", "ru", "russia", "россия"}:
            continue
        match = (node, address, site)
        if match not in matches:
            matches.append(match)
    return matches[0] if len(matches) == 1 else None


def seller_contact(company: dict[str, Any], field: str) -> str | None:
    normalize = phone_number if field == "telephone" else email_address
    contacts = {
        normalized
        for node in structured_nodes(company.get("contactPoint"))
        if clean(node.get("contactType")).casefold() == "sales"
        if (normalized := normalize(node.get(field)))
    }
    return next(iter(contacts)) if len(contacts) == 1 else normalize(company.get(field))


def parse_catalog_page(response: HtmlResponse) -> dict[str, Any] | None:
    title = clean(response.css("title::text").get())
    heading = clean(response.xpath("normalize-space(string((//h1)[1]))").get())
    description = clean(response.css('meta[name="description"]::attr(content)').get())
    headline = f"{title}. {heading}"
    if NON_OFFER_HEADLINE.search(headline):
        return None
    products = qualifying_products(headline).get("packaging", set())
    nodes = tuple(jsonld_nodes(response))
    seller = local_seller(response, nodes)
    if not products or seller is None:
        return None
    company, address, site = seller
    return {
        "provider": PROVIDER,
        "external_id": response.url,
        "name": clean(company["name"]),
        "city": clean(address["addressLocality"]),
        "address": clean(address["streetAddress"]),
        "region_label": clean(address.get("addressRegion")),
        "website": site,
        "inn": valid_inn(company.get("taxID")),
        "phone": seller_contact(company, "telephone"),
        "email": seller_contact(company, "email"),
        "description": (description or heading or title)[:600],
        "category_ids": ["packaging"],
        "products": sorted(products),
        "minimum_order": minimum_order(response, nodes),
        "source_url": response.url,
        "source_label": "Официальный каталог поставщика",
        "enrich_website": False,
    }


class ProducerSitesSpider(scrapy.Spider):
    name = PROVIDER
    custom_settings = {"REDIRECT_ENABLED": False, "METAREFRESH_ENABLED": False}

    async def start(self) -> AsyncIterator[scrapy.Request]:
        for url in catalog_pages():
            yield scrapy.Request(url, callback=self.parse, errback=self.request_failed)

    def parse(self, response: HtmlResponse, **kwargs: Any) -> dict[str, Any] | None:
        item = parse_catalog_page(response)
        if item is None:
            self.source_error(response.url, "Offer or local seller evidence is missing")
        return item

    def request_failed(self, failure: Failure) -> None:
        self.source_error(failure.request.url, failure.getErrorMessage())

    def source_error(self, url: str, reason: str) -> None:
        if self.crawler.stats:
            self.crawler.stats.inc_value("foodsource/source_errors")
        self.logger.warning("%s: %s", reason, url)
