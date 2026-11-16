import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from scrapy.http import HtmlResponse

from foodsource.identity import name_key

from .normalize import clean, company_url, official_domain

CITY_NAME = r"[А-ЯЁ][А-ЯЁа-яё-]{1,32}(?: +[А-ЯЁа-яё-]{1,24}){0,4}"
CITY = re.compile(
    rf"(?:^|[ ,;])(?:г\.\s*|город\s+|пос\.\s*|пгт\.\s*|село\s+)(?P<city>{CITY_NAME})(?=[,;]|$)"
)
COUNTRY_AND_CITY = re.compile(rf"^(?:Россия|РФ|Российская Федерация),\s*(?P<city>{CITY_NAME})$")
STREET = re.compile(
    r"\b(?:ул\.\s*|улица\s+|пр-т\s+|проезд\s+|шоссе\s+|пер\.\s*|проспект\s+|\d{6}\b)", re.I
)
STREET_AFTER_CITY = re.compile(
    r"\b(?:ул\.\s*|улица\s+|пр-т\s+|проезд\s+|шоссе\s+|пер\.\s*|проспект\s+)", re.I
)
LOCATION_LABEL = re.compile(
    r"^(?:(?:наш|почтовый|юридический|фактический)\s+)?адрес(?:\s+(?:офиса|производства|склада))?[:\s]*$",
    re.I,
)
CONTACT_LINKS = {
    "контакты",
    "наши контакты",
    "контактная информация",
    "contact",
    "contacts",
    "contact us",
}
CONTACT_PATH = re.compile(
    r"(?:^|/)(?:contacts?|kontakty|kontakti|kontaktyi|kontakt|контакты)(?:/|\.html?$|$)", re.I
)


def website_location(response: HtmlResponse) -> tuple[str, str, str] | None:
    locations = {
        name_key(city): (city, address, region)
        for city, address, region in structured_addresses(response)
    }
    if locations:
        return next(iter(locations.values())) if len(locations) == 1 else None
    visible = response.css("address, [itemprop='address'], [class*='address'], [class*='adress']")
    candidates = [clean(node.xpath("string(.)").get()) for node in visible]
    for label in response.css("h2, h3, h4, h5, dt, strong, b"):
        if LOCATION_LABEL.fullmatch(clean(label.xpath("string(.)").get())):
            candidates.append(clean(label.xpath("string(following-sibling::*[1])").get()))

    if CONTACT_PATH.search(urlsplit(response.url).path):
        candidates.extend(
            clean(text)
            for text in response.xpath(
                "//body//text()[not(ancestor::script or ancestor::style or ancestor::noscript)]"
            ).getall()
        )

    for value in candidates:
        label, separator, remainder = value.partition(":")
        if separator and LOCATION_LABEL.fullmatch(label):
            value = remainder.strip()
        if not value or len(value) > 260:
            continue
        if match := COUNTRY_AND_CITY.fullmatch(value):
            city = match["city"]
            locations.setdefault(name_key(city), (city, value, ""))
        elif STREET.search(value):
            if match := CITY.search(value):
                city = match["city"]
            else:
                first_words = re.sub(r"^\d{6}[,\s]+", "", value).split(",", 1)
                if len(first_words) != 2 or not STREET_AFTER_CITY.search(first_words[1]):
                    continue
                city = first_words[0].strip()
                if not re.fullmatch(CITY_NAME, city) or city.casefold() in {"россия", "рф"}:
                    continue
            locations.setdefault(name_key(city), (city, value, ""))
    return next(iter(locations.values())) if len(locations) == 1 else None


def postal_nodes(node: object) -> Iterator[dict[str, Any]]:
    if isinstance(node, list):
        for member in node:
            yield from postal_nodes(member)
    if isinstance(node, dict):
        kind = node.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(
            isinstance(value, str) and value.rsplit("/", 1)[-1] == "PostalAddress"
            for value in kinds
        ):
            yield node
        for key in ("@graph", "address", "mainEntity", "publisher", "location"):
            yield from postal_nodes(node.get(key))


def structured_addresses(response: HtmlResponse) -> Iterator[tuple[str, str, str]]:
    for script in response.css('script[type="application/ld+json"]::text').getall():
        try:
            document = json.loads(script)
        except (ValueError, RecursionError):
            continue
        for node in postal_nodes(document):
            city, region = clean(node.get("addressLocality")), clean(node.get("addressRegion"))
            street = clean(node.get("streetAddress"))
            country = node.get("addressCountry")
            if isinstance(country, dict):
                country = country.get("name") or country.get("identifier")
            if (
                city
                and street
                and clean(country).casefold()
                in {"", "ru", "russia", "россия", "рф", "russian federation"}
            ):
                yield city, ", ".join(filter(None, (region, city, street))), region


def website_contact_page(response: HtmlResponse, domain: str) -> str | None:
    for anchor in response.css("a[href]"):
        label = name_key(clean(anchor.xpath("string(.)").get()))
        target = company_url(response.urljoin(anchor.attrib["href"]))
        if (
            target
            and target != response.url
            and official_domain(target) == domain
            and (label in CONTACT_LINKS or CONTACT_PATH.search(urlsplit(target).path))
        ):
            return target
    return None
