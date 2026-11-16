"""Resolve Russian localities using the GeoNames country extract (CC BY 4.0)."""

import os
import re
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from io import TextIOWrapper
from math import isfinite
from pathlib import Path
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile

from foodsource.text import word_tokens

GEONAMES = "https://download.geonames.org/export/dump/"
FILES = {
    "ru-places.zip": "RU.zip",
    "ru-aliases.zip": "alternatenames/RU.zip",
    "admin1CodesASCII.txt": "admin1CodesASCII.txt",
}
ACTIVE_PLACES = frozenset({"PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLC", "PPLL"})
CAPITALS = frozenset({"PPLA", "PPLC"})
ADMIN_CENTRES = CAPITALS | {"PPLA2", "PPLA3", "PPLA4", "PPLA5"}
CITY_IDS = {"1486209": "ekaterinburg"}
AREA_IDS = {
    "1490542": "sverdlovsk-region",
    "524894": "city-524901",
    "536203": "city-498817",
}
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
CITY_PREFIX = re.compile(r"^(?:город|г)(?:\.\s*|\s+)", re.I)
PREFIX = re.compile(r"^(?:город|г|пос[её]лок|пос|пгт|рп|село|с)(?:\.\s*|\s+)", re.I)
POSTCODE = re.compile(r"^\d{6}(?:\s+|$)")
REGION_ABBREVIATIONS = {"обл": "область", "респ": "республика"}
REGION_TYPE = re.compile(r"\b(?:область|край|республика|округ)\b", re.I)


def _key(value: str) -> str:
    return " ".join(word_tokens(value))


def _locality_key(value: str) -> str:
    return _key(PREFIX.sub("", value.strip(), count=1))


def _area_key(value: str) -> str:
    words = _key(value).split()
    words = [REGION_ABBREVIATIONS.get(word, word) for word in words]
    if words and words[-1] == "республика":
        words = words[-1:] + words[:-1]
    return " ".join(words)


def _address_parts(value: str) -> tuple[str, ...]:
    return tuple(POSTCODE.sub("", part.strip()) for part in re.split(r"[,;\n]+", value))


@dataclass(frozen=True)
class Area:
    id: str
    label: str


@dataclass(frozen=True)
class Place:
    id: str
    label: str
    admin_code: str
    feature_code: str
    coordinates: tuple[float, float] | None


class Geography:
    def __init__(
        self,
        areas: dict[str, Area],
        area_aliases: dict[str, frozenset[str]],
        places: dict[str, tuple[Place, ...]],
    ) -> None:
        self._areas = areas
        self._area_aliases = area_aliases
        self._places = places
        self._places_by_id = {place.id: place for matches in places.values() for place in matches}

    def coordinates(self, region_id: str) -> tuple[float, float] | None:
        place = self._places_by_id.get(region_id)
        return place.coordinates if place is not None else None

    def resolve(
        self, city: str, address: str = "", region_label: str = ""
    ) -> tuple[dict[str, str], ...]:
        city_parts = _address_parts(city)
        area_parts = (*city_parts, *_address_parts(address))
        city_components = {_locality_key(part) for part in city_parts if PREFIX.match(part)}
        if len(city_components) > 1:
            return ()
        stated_localities = {_locality_key(part) for part in area_parts if PREFIX.match(part)}
        city_key = _locality_key(city)
        if city_components:
            (city_key,) = city_components
        matches = self._places.get(city_key, ())
        if not city_key:
            matches = tuple(
                {
                    place.id: place
                    for name in stated_localities
                    for place in self._places.get(name, ())
                }.values()
            )
        if not matches:
            return ()

        region_hints = self._area_aliases.get(_area_key(region_label), frozenset())
        if not region_hints:
            region_hints = frozenset(
                code
                for part in area_parts
                if not PREFIX.match(part)
                and _locality_key(part) not in {city_key, *stated_localities}
                for code in self._area_aliases.get(_area_key(part), ())
            )
        if region_hints:
            matches = tuple(place for place in matches if place.admin_code in region_hints)
            explicitly_city = any(
                CITY_PREFIX.match(part) and _locality_key(part) == city_key for part in area_parts
            )
            if len(matches) > 1 and explicitly_city:
                centres = tuple(place for place in matches if place.feature_code in ADMIN_CENTRES)
                if len(centres) == 1:
                    matches = centres
        elif len(matches) > 1 and city_key:
            capitals = tuple(
                place
                for place in matches
                if place.feature_code in CAPITALS and _locality_key(place.label) == city_key
            )
            if len(capitals) == 1:
                matches = capitals
        if len(matches) != 1:
            return ()

        (place,) = matches
        area = self._areas[place.admin_code]
        area_node = {"id": area.id, "label": area.label, "parent_id": "ru"}
        if place.id == area.id:
            return (area_node,)
        return area_node, {"id": place.id, "label": place.label, "parent_id": area.id}


@dataclass(frozen=True)
class LocalizedName:
    language: str
    name: str
    preferred: bool
    short: bool
    colloquial: bool


def _fields(lines: Iterable[str]) -> Iterator[list[str]]:
    return (line.rstrip("\r\n").split("\t") for line in lines if line.strip())


def _localized_names(lines: Iterable[str]) -> dict[str, list[LocalizedName]]:
    names = defaultdict(list)
    for row in _fields(lines):
        if len(row) < 8:
            continue
        _, place_id, language, name, preferred, short, colloquial, historic = row[:8]
        if language in {"ru", "en"} and name and historic != "1":
            names[place_id].append(
                LocalizedName(language, name, preferred == "1", short == "1", colloquial == "1")
            )
    return names


def _label(names: list[LocalizedName], fallback: str, *, area: bool = False) -> str:
    russian = (
        name
        for name in names
        if name.language == "ru"
        and (not name.colloquial or name.preferred)
        and CYRILLIC.search(name.name)
    )
    selected = min(
        russian,
        key=lambda name: (
            area and REGION_TYPE.search(name.name) is None,
            name.colloquial,
            not name.preferred,
            name.short,
            -len(name.name),
            name.name,
        ),
        default=None,
    )
    if selected is None:
        return fallback
    label = selected.name
    if area:
        label = re.sub(r"(?<=\s)(Область|Край|Округ)$", lambda match: match[0].lower(), label)
    return label


def _build_geography(
    place_lines: Iterable[str], admin_lines: Iterable[str], alternate_lines: Iterable[str]
) -> Geography:
    names = _localized_names(alternate_lines)
    areas = {}
    area_aliases = defaultdict(set)
    for row in _fields(admin_lines):
        if len(row) != 4 or not row[0].startswith("RU."):
            continue
        code, label, ascii_label, geoname_id = row
        localized = names.get(geoname_id, [])
        code = code.removeprefix("RU.")
        areas[code] = Area(
            AREA_IDS.get(geoname_id, f"region-{geoname_id}"),
            _label(localized, label, area=True),
        )
        for alias in {label, ascii_label, *(name.name for name in localized)}:
            if key := _area_key(alias):
                area_aliases[key].add(code)

    places = defaultdict(list)
    for row in _fields(place_lines):
        if len(row) != 19 or row[6] != "P" or row[7] not in ACTIVE_PLACES or row[8] != "RU":
            continue
        geoname_id, label, ascii_label = row[:3]
        code = row[10]
        if code not in areas:
            continue
        localized = names.get(geoname_id, [])
        place = Place(
            CITY_IDS.get(geoname_id, f"city-{geoname_id}"),
            _label(localized, label),
            code,
            row[7],
            _coordinates(row[4], row[5]),
        )
        aliases = {
            _locality_key(alias) for alias in (label, ascii_label, *(n.name for n in localized))
        }
        for key in aliases - {""}:
            places[key].append(place)

    if not areas or not places:
        raise ValueError("GeoNames extracts contain no matchable Russian places")
    return Geography(
        areas,
        {key: frozenset(matches) for key, matches in area_aliases.items()},
        {key: tuple(matches) for key, matches in places.items()},
    )


def _coordinates(latitude: str, longitude: str) -> tuple[float, float] | None:
    try:
        lat, lon = float(latitude), float(longitude)
    except ValueError:
        return None
    if isfinite(lat) and isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180:
        return lon, lat
    return None


def _validate_extract(path: Path, *, zipped: bool) -> None:
    if zipped:
        with ZipFile(path) as archive:
            archive.getinfo("RU.txt")
        return
    with path.open(encoding="utf-8") as text:
        if not any(
            len(row) == 4 and row[0].startswith("RU.") and row[3].isdigit() for row in _fields(text)
        ):
            raise ValueError(f"Invalid GeoNames administrative extract {path.name}")


def _download_if_missing(path: Path, url: str) -> None:
    zipped = path.suffix == ".zip"
    if path.is_file():
        try:
            _validate_extract(path, zipped=zipped)
            return
        except (BadZipFile, KeyError, ValueError):
            pass
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as target:
        temporary = Path(target.name)
    try:
        with urlopen(url, timeout=30) as source, temporary.open("wb") as target:
            shutil.copyfileobj(source, target)
        _validate_extract(temporary, zipped=zipped)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_geography(cache_dir: Path | None = None) -> Geography:
    """Fetch and reuse country extracts outside the repository; build one resolver per CLI run."""
    if cache_dir is None:
        cache_dir = Path(
            os.environ.get(
                "FOODSOURCE_GEOGRAPHY_CACHE",
                str(Path(tempfile.gettempdir()) / "foodsource-geonames"),
            )
        )
    cache_dir.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        _download_if_missing(cache_dir / name, GEONAMES + url)

    with (
        ZipFile(cache_dir / "ru-places.zip") as places,
        ZipFile(cache_dir / "ru-aliases.zip") as aliases,
        TextIOWrapper(places.open("RU.txt"), encoding="utf-8") as place_rows,
        TextIOWrapper(aliases.open("RU.txt"), encoding="utf-8") as alias_rows,
        (cache_dir / "admin1CodesASCII.txt").open(encoding="utf-8") as admin_rows,
    ):
        return _build_geography(place_rows, admin_rows, alias_rows)
