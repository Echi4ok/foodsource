import csv
import re
from collections import deque
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import scrapy
from py7zr import SevenZipFile
from py7zr.exceptions import ArchiveError
from scrapy.http import Response
from twisted.python.failure import Failure

from foodsource.text import normalize_text

from .normalize import clean, valid_inn
from .qualification import qualifying_products

PROVIDER = "fsa-declarations"
DATASET = "https://fsa.gov.ru/opendata/7736638268-rds/"
REGISTER = "https://pub.fsa.gov.ru/rds/declaration/view"
ARCHIVE_URL = re.compile(re.escape(DATASET) + r"data-(\d{8})-structure-\d{8}\.7z")
MONTHLY_CSV = re.compile(r"rds_\d{8}_\d{8}\.csv", re.I)
ARCHIVES = 3
MAX_UNPACKED_BYTES = 1024**3
MAX_CELL_LENGTH = 4 * 1024**2

DECLARATION_FIELDS = frozenset(
    {
        "id",
        "Статус",
        "Номер ДС",
        "Дата рег",
        "Срок действия",
        "Тех регламенты",
        "Тип изготовителя",
        "Изготовитель",
        "Адрес изготовителя",
        "ИНН производителя",
        "Происхождение продукции",
        "Полное наименование",
    }
)
FOOD_REGULATIONS = (
    "о безопасности пищевой продукции",
    "о безопасности отдельных видов специализированной пищевой продукции",
    "о безопасности молока и молочной продукции",
    "о безопасности мяса и мясной продукции",
    "о безопасности мяса птицы и продукции его переработки",
    "о безопасности рыбы и рыбной продукции",
    "о безопасности масложировой продукции",
    "о безопасности упакованной питьевой воды",
    "требования безопасности пищевых добавок",
)
FOOD_PACKAGING = re.compile(
    r"\b(?:упаков\w*|тар[аы]\b|контейнер\w*|укупорочн\w+|пленк\w*)[\s\S]{0,145}?"
    r"\bдля\s+(?:(?:упаков\w+|фасов\w+|хранени\w+|укупорив\w+|контакта\s+с)\s+)?"
    r"(?:пищев\w+|продукт\w+\s+питания|консервов\b|готовых\s+блюд)",
)
FROZEN_PRODUCT = re.compile(r"\b(?:глубоко|быстро|свеже)?замороженн\w*\b")
FROZEN_CONTEXT = re.compile(r"\b(?:из|при хранении|хранить)\s+замороженн\w*")
INGREDIENT = re.compile(
    r"\b(?:пищев\w+\s+(?:добавк\w+|ингредиент\w+)|ферментн\w+\s+препарат\w*|"
    r"технологическ\w+\s+вспомогательн\w+\s+средств\w*)\b"
)
ADDRESS_SEPARATOR = re.compile(r"[,;\n]+|\s{2,}")
POSTCODE = re.compile(r"^\d{6}\b\s*")
CITY = re.compile(r"^(?:город|г)(?:\.\s*|\s+)(?!округ\b|[оп]\.)(?=[а-яё])", re.I)
VILLAGE = re.compile(
    r"^(?:деревня|д|село|с|станица|ст-ца|поселок|посёлок|пос|пгт|хутор|х|рп)"
    r"(?:\.\s*|\s+)(?=[а-яё])",
    re.I,
)
AREA = re.compile(r"\b(?:обл\.?|область|край|респ\.?|республика|автономный округ)\b", re.I)
REVERSED_AREA = re.compile(r"^(область|обл\.?|край)\s+(.+)$", re.I)
TECHNICAL_TAIL = re.compile(
    r"\b(?:упаковано в|маркировк[аи]\s*:|ассортимент продукции указан|"
    r"согласно приложению|в соответствии с (?:гост|ту)\b)"
    r"|\b(?:гост(?: р)?|ту)\s+\d",
    re.I,
)


def archives_from_manifest(
    body: bytes, as_of: date, limit: int = ARCHIVES
) -> tuple[tuple[date, str], ...]:
    if limit < 1:
        raise ValueError("FSA archive count must be positive")
    root = ElementTree.fromstring(body)
    archives = set()
    for source in root.findall("./data/dataversion/source"):
        url = clean(source.text)
        if match := ARCHIVE_URL.fullmatch(url):
            day = datetime.strptime(match[1], "%Y%m%d").date()
            if day <= as_of:
                archives.add((day, url))
    selected = dict(sorted(archives))
    if not selected:
        raise ValueError("FSA archive links are missing from the manifest")
    return tuple(selected.items())[-limit:]


def manufacturer_address(value: str) -> tuple[str, str, str]:
    parts = [
        part for chunk in ADDRESS_SEPARATOR.split(value) if (part := POSTCODE.sub("", clean(chunk)))
    ]
    area = next((part for part in parts if AREA.search(part)), "")
    if match := REVERSED_AREA.fullmatch(area):
        parts[parts.index(area)] = area = f"{match[2]} {match[1]}"
    cities = list(dict.fromkeys(part for part in parts if CITY.match(part)))
    villages = list(
        dict.fromkeys(VILLAGE.sub("", part, count=1) for part in parts if VILLAGE.match(part))
    )
    localities = cities or villages
    return (localities[0] if len(localities) == 1 else ""), ", ".join(parts), area


def product_name(value: str) -> str:
    product = clean(value)
    if match := TECHNICAL_TAIL.search(product):
        product = product[: match.start()]
    if len(product) > 320:
        product = product[:320].rsplit(" ", 1)[0]
    return product.strip(" .,:;")


def classify_product(product: str, regulations: str) -> tuple[list[str], list[str]]:
    rules = normalize_text(regulations)
    title = normalize_text(product)
    facts = qualifying_products(f"Производство {product}")
    if any(regulation in rules for regulation in FOOD_REGULATIONS):
        categories = ["food"]
        if INGREDIENT.search(title) or "ingredients" in facts:
            categories.append("ingredients")
        if "frozen" in facts or FROZEN_PRODUCT.search(FROZEN_CONTEXT.sub("", title)):
            categories.append("frozen")
        return categories, [product]
    if "о безопасности упаковки" in rules and (
        "packaging" in facts or FOOD_PACKAGING.search(title)
    ):
        return ["packaging"], [product, *sorted(facts.get("packaging", ()))]
    return [], []


def declaration_item(row: dict[str, str], archive_day: date, as_of: date) -> dict[str, Any] | None:
    if (
        row["Статус"] != "Действует"
        or row["Тип изготовителя"] != "Юридическое лицо"
        or row["Происхождение продукции"] != "Российская Федерация"
    ):
        return None
    try:
        registered = datetime.strptime(row["Дата рег"], "%d.%m.%Y").date()
        expires = datetime.strptime(row["Срок действия"], "%d.%m.%Y").date()
    except ValueError:
        return None
    number = clean(row["Номер ДС"])
    doc_id = row["id"]
    inn = valid_inn(row["ИНН производителя"])
    name = clean(row["Изготовитель"])
    if not (registered <= archive_day <= as_of <= expires and number and inn and name):
        return None
    product = product_name(row["Полное наименование"])
    if not product:
        return None
    categories, products = classify_product(product, row["Тех регламенты"])
    if not categories:
        return None
    city, address, region = manufacturer_address(row["Адрес изготовителя"])
    if not city:
        return None
    return {
        "provider": PROVIDER,
        "external_id": doc_id,
        "name": name,
        "inn": inn,
        "city": city,
        "address": address,
        "region_label": region,
        "category_ids": categories,
        "products": products,
        "description": "Производитель. Ассортимент указан в декларациях соответствия.",
        "documents": f"Декларация {number}, до {expires:%d.%m.%Y}",
        "source_url": f"{REGISTER}/{doc_id}/common",
        "source_label": "Декларация Росаккредитации",
        "source_links": [{"label": "Открытая выгрузка Росаккредитации", "url": DATASET}],
        "observed_at": archive_day,
    }


def update_declarations(
    path: Path, active: dict[str, dict[str, Any]], archive_day: date, as_of: date
) -> None:
    limit = csv.field_size_limit(MAX_CELL_LENGTH)
    try:
        with path.open(encoding="utf-8-sig", newline="") as table:
            reader = csv.DictReader(table, delimiter=";")
            if not DECLARATION_FIELDS.issubset(reader.fieldnames or ()):
                raise ValueError("FSA declarations file is missing required columns")
            count = 0
            for row in reader:
                count += 1
                doc_id = row["id"]
                if (
                    None in row
                    or any(row[field] is None for field in DECLARATION_FIELDS)
                    or not doc_id
                    or not doc_id.isascii()
                    or not doc_id.isdecimal()
                ):
                    raise ValueError(f"Malformed FSA declarations row {reader.line_num}")
                if item := declaration_item(row, archive_day, as_of):
                    active[doc_id] = item
                else:
                    active.pop(doc_id, None)
            if not count:
                raise ValueError("FSA declarations file is empty")
    finally:
        csv.field_size_limit(limit)


class FsaDeclarationsSpider(scrapy.Spider):
    name = PROVIDER
    allowed_domains = ["fsa.gov.ru"]
    custom_settings = {
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_MAXSIZE": 128 * 1024**2,
        "DOWNLOAD_WARNSIZE": 80 * 1024**2,
        "DOWNLOAD_TIMEOUT": 120,
    }

    def __init__(self, archive_count: int = ARCHIVES, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.archive_count = archive_count
        self.as_of = datetime.now(ZoneInfo("Europe/Moscow")).date()
        self.archives: deque[tuple[date, str]] = deque()
        self.active: dict[str, dict[str, Any]] = {}

    async def start(self) -> AsyncIterator[scrapy.Request]:
        yield scrapy.Request(f"{DATASET}meta.xml", callback=self.parse, errback=self.request_failed)

    def parse(self, response: Response, **kwargs: Any) -> scrapy.Request:
        self.archives.extend(archives_from_manifest(response.body, self.as_of, self.archive_count))
        return self.request_archive()

    def request_archive(self) -> scrapy.Request:
        archive_day, url = self.archives.popleft()
        return scrapy.Request(
            url,
            callback=self.parse_archive,
            errback=self.request_failed,
            cb_kwargs={"archive_day": archive_day},
        )

    def parse_archive(
        self, response: Response, archive_day: date
    ) -> Iterator[dict[str, Any] | scrapy.Request]:
        try:
            with SevenZipFile(
                BytesIO(response.body), max_extract_size=MAX_UNPACKED_BYTES
            ) as archive:
                members = archive.list()
                if len(members) != 1 or not MONTHLY_CSV.fullmatch(members[0].filename):
                    raise ValueError("Unexpected contents of FSA declarations archive")
                member = members[0]
                if not member.is_file or not 0 < member.uncompressed <= MAX_UNPACKED_BYTES:
                    raise ValueError("Unexpected size or type of FSA declarations file")
                with TemporaryDirectory(prefix="foodsource-fsa-") as directory:
                    path = Path(directory)
                    archive.extract(path=path, targets=[member.filename])
                    update_declarations(
                        path / member.filename, self.active, archive_day, self.as_of
                    )
        except (ArchiveError, csv.Error, OSError, ValueError) as error:
            self.source_error(response.url, str(error))
            return
        if self.archives:
            yield self.request_archive()
        else:
            yield from self.active.values()

    def request_failed(self, failure: Failure) -> None:
        self.source_error(failure.request.url, failure.getErrorMessage())

    def source_error(self, url: str, reason: str) -> None:
        if self.crawler.stats:
            self.crawler.stats.inc_value("foodsource/source_errors")
        self.logger.warning("%s: %s", reason, url)
