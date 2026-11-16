import re
from collections import Counter
from collections.abc import Iterable
from datetime import date
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import HttpUrl, ValidationError

from foodsource.domain import canonical_domain

from .taxonomy import CATEGORIES, REGIONS

if TYPE_CHECKING:
    from .geography import Geography

SHARED_SITES = {
    "2gis.ru",
    "avito.ru",
    "dzen.ru",
    "mail.ru",
    "max.ru",
    "ok.ru",
    "ozon.ru",
    "taplink.cc",
    "t.me",
    "vk.com",
    "wa.me",
    "wildberries.ru",
    "yandex.ru",
}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")
WS = re.compile(r"\s+")


def clean(value: object) -> str:
    return WS.sub(" ", value).strip() if isinstance(value, str) else ""


def company_url(value: object, *, keep_fragment: bool = False) -> str | None:
    url = clean(value)
    if not url or " " in url or url.startswith(("#", "/", "mailto:", "tel:")):
        return None
    try:
        parts = urlsplit(url if "://" in url else f"https://{url}")
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not host or "." not in host:
        return None
    if parts.username or parts.password:
        return None
    fragment = parts.fragment if keep_fragment else ""
    try:
        return str(
            HttpUrl(urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, fragment)))
        )
    except ValidationError:
        return None


def official_domain(value: str | None) -> str | None:
    domain = canonical_domain(urlsplit(value).hostname or "") if value else ""
    if not domain or any(domain == name or domain.endswith("." + name) for name in SHARED_SITES):
        return None
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def phone_number(value: object) -> str | None:
    phone = clean(value)
    digits = re.sub(r"\D", "", phone)
    if not phone or len(digits) not in {10, 11} or not re.fullmatch(r"\+?[\d ()-]+", phone):
        return None
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    return "+7" + digits if len(digits) == 10 else "+" + digits


def email_address(value: object) -> str | None:
    email = clean(value).lower()
    return email if EMAIL.fullmatch(email) else None


def valid_inn(value: object) -> str | None:
    identifier = clean(value)
    if not identifier.isascii() or not identifier.isdigit() or len(set(identifier)) == 1:
        return None
    digits = [int(digit) for digit in identifier]

    def digit(weights: tuple[int, ...]) -> int:
        return sum(number * weight for number, weight in zip(digits, weights)) % 11 % 10

    if len(digits) == 10 and digit((2, 4, 10, 3, 5, 9, 4, 6, 8)) == digits[-1]:
        return identifier
    if (
        len(digits) == 12
        and digit((7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == digits[-2]
        and digit((3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == digits[-1]
    ):
        return identifier
    return None


def resolve_observation_locations(
    items: Iterable[dict[str, Any]], geography: "Geography"
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], Counter[str]]:
    located = []
    regions = {region["id"]: region for region in REGIONS}
    unresolved: Counter[str] = Counter()
    for item in items:
        if item.get("region_id") in regions:
            located.append(item)
            continue
        city = clean(item.get("city"))
        address = clean(item.get("address"))
        region = clean(item.get("region_label"))
        lineage = geography.resolve(city, address, region)
        if not lineage and not city and region:
            candidates = {
                match[-1]["id"]: match
                for part in address.split(",")
                if (match := geography.resolve(part.strip(), region_label=region))
            }
            if len(candidates) == 1:
                lineage = next(iter(candidates.values()))
        if not lineage:
            unresolved[city or "(missing)"] += 1
            continue
        for region in lineage:
            if regions.setdefault(region["id"], region) != region:
                raise ValueError(f"Conflicting geographic records for {region['id']}")
        located.append({**item, "region_id": lineage[-1]["id"]})
    return located, regions, unresolved


def normalized_snapshot(
    items: Iterable[dict[str, Any]],
    day: date,
    regions: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        record = normalize_item(item, day)
        key = (item["provider"], item["external_id"])
        if key in records and records[key] != record:
            raise ValueError(f"Conflicting records for source {key}")
        records[key] = record

    if regions is None:
        selected_regions = REGIONS
    else:
        used = {"ru"}
        for record in records.values():
            current = record["region_id"]
            while current and current not in used:
                used.add(current)
                current = regions[current].get("parent_id")
        selected_regions = sorted(
            (regions[key] for key in used), key=lambda area: (area["label"].casefold(), area["id"])
        )

    return {
        "schema_version": 1,
        "updated_at": day,
        "categories": CATEGORIES,
        "regions": selected_regions,
        "suppliers": [records[key] for key in sorted(records)],
    }


def normalize_item(item: dict[str, Any], day: date) -> dict[str, Any]:
    source_url = company_url(item["source_url"], keep_fragment=True)
    provider = clean(item["provider"])
    external_id = clean(item["external_id"])
    name = clean(item["name"])
    if not all((source_url, provider, external_id, name, clean(item["source_label"]))):
        raise ValueError(f"Missing supplier identity or evidence in {provider}/{external_id}")

    categories = sorted(set(item["category_ids"]))
    products = sorted(
        {word for entry in item.get("products", []) if (word := clean(entry))}, key=str.casefold
    )
    website = company_url(item.get("website"))
    contacts = {"email": email_address(item.get("email")), "phone": phone_number(item.get("phone"))}
    identifier = valid_inn(item.get("inn"))
    evidence_urls = {field: source_url for field in ("name", "region_id", "category_ids")}
    if products:
        evidence_urls["products"] = source_url
    if website:
        evidence_urls["website"] = source_url
    if identifier:
        evidence_urls["inn"] = source_url
    for field, value in contacts.items():
        if value:
            evidence_urls[field] = source_url
    sources = [{"label": clean(item["source_label"]), "url": source_url}]
    observations = [
        {
            "provider": provider,
            "external_id": external_id,
            "url": source_url,
            "observed_at": item.get("observed_at", day),
        }
    ]
    source_urls = {source_url}
    for link in item.get("source_links", ()):
        url = company_url(link.get("url"), keep_fragment=True)
        label = clean(link.get("label"))
        if url and label and url not in source_urls:
            sources.append({"label": label, "url": url})
            source_urls.add(url)
    evidence = {
        (field, url)
        for field, url in evidence_urls.items()
        if field not in item.get("new_fields", [])
    }
    source_extra = company_url(item.get("enrichment_url"))
    if source_extra:
        if source_extra != source_url:
            if source_extra not in source_urls:
                sources.append({"label": "Официальный сайт поставщика", "url": source_extra})
            observations.append(
                {
                    "provider": provider,
                    "external_id": external_id,
                    "url": source_extra,
                    "observed_at": item.get("enrichment_observed_at", day),
                }
            )
        for field in item.get("enriched_fields", []):
            evidence.add((field, source_extra))

    identity = sha256(f"{provider}\0{external_id}".encode("utf-8")).hexdigest()[:24]
    record = {
        "id": f"s-{identity}",
        "name": name,
        "description": clean(item.get("description"))
        or f"Поставщик в каталоге «{item['source_label']}».",
        "category_ids": categories,
        "region_id": item["region_id"],
        "products": products,
        "website": website,
        "contacts": contacts,
        "inn": identifier,
        "official_domain": official_domain(website),
        "sources": sources,
        "observations": observations,
    }
    for field in ("minimum_order", "price", "documents", "delivery"):
        if value := clean(item.get(field)):
            record[field] = value
            if field not in item.get("enriched_fields", []):
                evidence.add((field, source_url))
    record["field_evidence"] = [{"field": key, "url": url} for key, url in sorted(evidence)]
    return record
