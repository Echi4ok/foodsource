"""Find local leads in Overture Places for independent website verification."""

import asyncio
import math
import re
import warnings
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from contextlib import closing
from dataclasses import dataclass
from typing import Any

from scrapy import Spider

from ..identity import name_key
from .geography import Geography, load_geography
from .normalize import clean, company_url, official_domain

PROVIDER = "overture-places"
SOURCE_URL = "https://docs.overturemaps.org/guides/places/"
FEATURE_URL = "https://explore.overturemaps.org/?mode=inspect&feature=places.place."
SOURCE_LABEL = "Overture Maps Places"
DEFAULT_CITIES = ("Екатеринбург",)
MAX_AREAS = 12
RADIUS_KM = 18
MAX_CANDIDATES = 24
MAX_PLACES_SCANNED = 100_000

FOOD_BUSINESS_TAGS = {
    "coffee_and_tea_supplies",
    "food_beverage_distributor",
    "restaurant_wholesale",
    "wholesale_grocer",
}
NAME_SIGNALS = re.compile(
    r"хлебокомбинат|хлебозавод|мясокомбинат|птицефабрик|молочн\w* завод|"
    r"кондитерск\w* фабрик|пищев\w* ингредиент|пищев\w* ароматизатор|"
    r"бумажн\w* упаковк|пищев\w* упаковк|упаковк\w* для (?:еды|пищи|продуктов)|"
    r"одноразов\w* посуд|посуд\w* оптом|продукт\w* оптом"
)
FEATURE_ID = re.compile(r"[a-zA-Z0-9_-]{1,100}")


@dataclass(frozen=True)
class CityScope:
    id: str
    label: str
    region_label: str
    bbox: tuple[float, float, float, float]


def _sequence(value: object) -> list[Any] | tuple[Any, ...]:
    return value if isinstance(value, (list, tuple)) else ()


def city_scopes(geography: Geography, cities: Iterable[str]) -> tuple[CityScope, ...]:
    scopes = {}
    for city in cities:
        locality, _, region_hint = city.partition(",")
        lineage = geography.resolve(locality.strip(), region_label=region_hint.strip())
        if not lineage or (coordinates := geography.coordinates(lineage[-1]["id"])) is None:
            raise ValueError(f"Unknown or ambiguous Overture city: {city}")
        selected = lineage[-1]
        longitude, latitude = coordinates
        dy = RADIUS_KM / 111.2
        dx = dy / math.cos(math.radians(latitude))
        scope = CityScope(
            selected["id"],
            selected["label"],
            lineage[0]["label"],
            (
                max(-180, longitude - dx),
                max(-90, latitude - dy),
                min(180, longitude + dx),
                min(90, latitude + dy),
            ),
        )
        scopes.setdefault(scope.id, scope)
        if len(scopes) > MAX_AREAS:
            raise ValueError(f"At most {MAX_AREAS} cities per Overture import")
    return tuple(scopes.values())


def _address_in_scope(
    row: Mapping[str, Any], scope: CityScope, geography: Geography
) -> Mapping[str, Any] | None:
    matches = []
    for address in _sequence(row.get("addresses")):
        if not isinstance(address, Mapping):
            continue
        if clean(address.get("country")).upper() not in {"", "RU"}:
            return None
        locality = clean(address.get("locality"))
        if not locality:
            continue
        lineage = geography.resolve(
            locality, clean(address.get("freeform")), clean(address.get("region"))
        )
        if not lineage or lineage[-1]["id"] != scope.id:
            return None
        matches.append(address)
    return next(iter(matches), None)


def _is_relevant(row: Mapping[str, Any], name: str) -> bool:
    taxonomy = row.get("taxonomy")
    tags = {clean(row.get("basic_category"))}
    if isinstance(taxonomy, Mapping):
        tags.add(clean(taxonomy.get("primary")))
        for field in ("hierarchy", "alternates"):
            tags.update(clean(tag) for tag in _sequence(taxonomy.get(field)))
    return bool(tags & FOOD_BUSINESS_TAGS or NAME_SIGNALS.search(name_key(name)))


def _feature_source(row: Mapping[str, Any], identifier: str) -> str:
    bounds = row.get("bbox")
    if isinstance(bounds, Mapping):
        longitude = bounds.get("xmin")
        latitude = bounds.get("ymin")
        if (
            isinstance(longitude, (int, float))
            and abs(longitude) <= 180
            and isinstance(latitude, (int, float))
            and abs(latitude) <= 90
        ):
            return f"{FEATURE_URL}{identifier}#18/{latitude:.6f}/{longitude:.6f}"
    return SOURCE_URL


def _candidate(
    row: Mapping[str, Any], scope: CityScope, geography: Geography
) -> dict[str, Any] | None:
    names = row.get("names")
    name = clean(names.get("primary")) if isinstance(names, Mapping) else ""
    identifier = clean(row.get("id"))
    if not name or not FEATURE_ID.fullmatch(identifier):
        return None
    status = name_key(clean(row.get("operating_status")))
    if status and set(status.split()) & {"closed", "inactive"}:
        return None
    if not _is_relevant(row, name) or (address := _address_in_scope(row, scope, geography)) is None:
        return None
    website = next(
        (
            url
            for value in _sequence(row.get("websites"))
            if (url := company_url(value)) and official_domain(url)
        ),
        None,
    )
    if not website:
        return None

    return {
        "provider": PROVIDER,
        "external_id": identifier,
        "name": name,
        "inn": None,
        "website": website,
        "description": "Организация из открытых данных Overture Maps Places.",
        "category_ids": [],
        "products": [],
        "region_id": scope.id,
        "city": scope.label,
        "region_label": scope.region_label,
        "address": clean(address.get("freeform")),
        "email": None,
        "phone": None,
        "source_url": _feature_source(row, identifier),
        "source_label": SOURCE_LABEL,
    }


def _confidence(value: object) -> float:
    if isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1:
        return float(value)
    return 0.0


def screen_overture_places(
    rows: Iterable[Mapping[str, Any]], scope: CityScope, geography: Geography
) -> list[dict[str, Any]]:
    """Select bounded domain leads without taking contact or assortment claims as evidence."""
    leads: list[tuple[float, dict[str, Any]]] = []
    for index, row in enumerate(rows, start=1):
        if index > MAX_PLACES_SCANNED:
            raise RuntimeError("Overture returned more places than the bounded city import permits")
        if lead := _candidate(row, scope, geography):
            leads.append((_confidence(row.get("confidence")), lead))

    leads.sort(key=lambda pair: (-pair[0], pair[1]["external_id"]))
    unique = {}
    for _, lead in leads:
        key = (name_key(lead["name"]), official_domain(lead["website"]))
        unique.setdefault(key, lead)
        if len(unique) >= MAX_CANDIDATES:
            break
    return list(unique.values())


def _download_places(scope: CityScope, release: str) -> Iterator[Mapping[str, Any]]:
    from overturemaps.core import StacIndexWarning, record_batch_reader

    try:
        with warnings.catch_warnings(action="error", category=StacIndexWarning):
            reader = record_batch_reader(
                "place", bbox=scope.bbox, release=release, connect_timeout=8, request_timeout=14
            )
    except StacIndexWarning as error:
        raise RuntimeError("Overture STAC index is unavailable for the bounded import") from error
    if reader is None:
        raise RuntimeError("Overture GeoParquet reader could not open the current Places release")
    with closing(reader):
        required = {"id", "names", "addresses", "websites", "basic_category", "taxonomy"}
        if not required <= set(reader.schema.names):
            raise ValueError("Overture Places schema is missing fields needed for verification")
        for batch in reader:
            yield from batch.to_pylist()


def discover_overture(cities: tuple[str, ...], geography: Geography) -> Iterator[dict[str, Any]]:
    from overturemaps.core import get_latest_release

    scopes = city_scopes(geography, cities)
    release = get_latest_release()
    for scope in scopes:
        yield from screen_overture_places(_download_places(scope, release), scope, geography)


class OvertureSpider(Spider):
    """Feed open-data leads into the shared Scrapy website-verification pipeline."""

    name = "overture"

    def __init__(
        self, cities: tuple[str, ...] = DEFAULT_CITIES, geography: Geography | None = None, **kwargs
    ):
        super().__init__(**kwargs)
        self.cities = cities
        self.geography = geography

    async def start(self) -> AsyncIterator[dict[str, Any]]:
        try:
            leads = await asyncio.to_thread(self.collect)
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            self._source_error(str(error))
            return
        if (stats := getattr(getattr(self, "crawler", None), "stats", None)) is not None:
            stats.set_value("foodsource/overture_candidates", len(leads))
        for lead in leads:
            yield lead

    def collect(self) -> list[dict[str, Any]]:
        geography = self.geography if self.geography is not None else load_geography()
        return list(discover_overture(self.cities, geography))

    def _source_error(self, reason: str) -> None:
        if (stats := getattr(getattr(self, "crawler", None), "stats", None)) is not None:
            stats.inc_value("foodsource/source_errors")
        self.logger.warning("%s: %s", PROVIDER, reason)
