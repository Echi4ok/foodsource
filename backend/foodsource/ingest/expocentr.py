"""Discover Russian food suppliers in the organizer's public exhibition catalogue."""

import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import scrapy
from scrapy.http import HtmlResponse
from twisted.python.failure import Failure

from foodsource.text import offer_clauses, offer_subject

from .normalize import clean, company_url, email_address, official_domain, phone_number, valid_inn

INDEX = "https://icatalog.expocentr.ru/ru"
SOURCE_ERRORS = "foodsource/source_errors"
EXHIBITION_PATH = re.compile(r"/ru/exhibitions/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
EXHIBITOR_ID = re.compile(r"[0-9]{1,12}")
EXHIBITION_LABEL = re.compile(r"(?P<name>ПРОДЭКСПО|АГРОПРОДМАШ)[\s–—-]+(?P<year>[0-9]{4})")
EXHIBITION_NAMES = {"ПРОДЭКСПО": "prodexpo", "АГРОПРОДМАШ": "agroprodmash"}
RUSSIA = {"россия", "российская федерация"}
INN = re.compile(r"\bИНН\s*[:№–-]?\s*(\d{12}|\d{10})\b", re.IGNORECASE)
PACKAGING = re.compile(
    r"^(?:гибкая упаковка|готовая упаковка|одноразовая упаковка|жестяная упаковка|"
    r"упаковочные материалы|пленка для упаковочного оборудования|пищевая упаковка|"
    r"стеклотара|укупорочные средства|тара для транспортировки|"
    r"(?:бумажная|картонная|пластиковая|полимерная) упаковка)"
)
INGREDIENTS = re.compile(
    r"^(?:ингредиенты(?: для пищевой промышленности)?|"
    r"компоненты для полуфабрикатов|кондитерские полуфабрикаты|"
    r"пищевые добавки|мукомольная продукция|крахмало-паточная продукция|"
    r"пряности|специи, приправы|сахар и сахарозаменители|"
    r"концентрированное и сухое молоко|орехи, семечки|зерно и зернопродукты)"
)
FROZEN = re.compile(r"^(?:замороженн\w+|мороженое(?:,|\b)|рыба замороженная|мясо замороженное)")
NON_PRODUCT = re.compile(
    r"^(?:российский экспортер|торговые дома|оптово-распределительные центры|"
    r"кухни мира|национальная кухня|семейный формат|продукция халяль|халяль|"
    r"кошерная продукция|производство(?: лицензионной продукции| продукции под стм)|"
    r"биопродукты|бакалея|гастрономия|безалкогольные|сми|франшизы)$"
    r"|(?:^|\b)(?:оборудование|погрузчики|корм\w*|упаковочные системы|"
    r"банк(?:овск\w+)? услуги|рекламные услуги|логистика|маркировка|"
    r"новогодняя и подарочная упаковка|программное обеспечение|"
    r"сервисы по доставке|табак|сигареты|фудтраки|приборы|отходы|"
    r"алкогольные напитки|аперитивы|бренди|вина|виски|водка|джин|коньяк|"
    r"пиво|ликеры|медовуха|ром|сидр|спиртовые настойки|шампанское)(?:\b|$)"
)
REGION = re.compile(r"^(?:[^,]+\s(?:область|край|автономный округ)|республика\s+[^,]+)$", re.I)
AGRO_MATERIAL_RUBRICS = frozenset(
    "12.8 12.8.1 12.8.2 12.8.3 12.8.4 12.8.5 12.8.6 12.8.7 "
    "12.8.9.3 12.8.9.4 12.8.11 12.8.12 20.6.11 20.6.12 55.24".split()
)
AGRO_PACKAGING = re.compile(
    r"\b(?:упаковк\w+|упаковочн\w+\s+(?:материал|пленк|решени)\w*|"
    r"тар[аыуе]|лотк\w*|контейнер\w*|полимерн\w+\s+плен\w*|"
    r"термоусадочн\w+\s+пакет\w*|крышк\w*|колпачк\w*|одноразов\w+\s+посуд\w*)\b"
)
AGRO_FOOD_USE = re.compile(
    r"\b(?:пищев\w+|продукт\w+\s+питания|готов\w+\s+(?:ед\w*|блюд\w*)|"
    r"мяс\w*|рыб\w*|сыров|сыродел\w+|колбас\w+|молочн\w+|"
    r"напит\w+|кондитер\w+|horeca)\b"
)
AGRO_EQUIPMENT_OR_PROCESS = re.compile(
    r"\b(?:оборудовани\w*|машин\w*|картонатор\w*|робот\w*|автоматизир\w+|"
    r"дозировани\w*|счет|фасовк\w*|маркировк\w*|укладк\w*|розлив\w*|"
    r"(?:упаковочн\w+|технологическ\w+|автоматическ\w+)\s+лини\w*)\b"
)
AGRO_CASINGS = re.compile(r"\b(?:колбасн\w+\s+оболоч\w+|оболоч\w+\s+для\s+колбас\w*)\b")
AGRO_CATALOG_HEADING = re.compile(
    r"^(?:пищев\w+\s+(?:упаковка|тара)\b|(?:упаковка|тара)"
    r"(?:\s+из\s+[^:]{1,100})?\s+для\s+(?:пищев\w+|продукт\w+\s+питания|готов\w+\s+блюд\w*))"
)
AGRO_FINISHED_GOODS = (
    ("Лотки под запайку", re.compile(r"\bлотк\w*\s+под\s+запайк\w*\b")),
    ("Контейнеры из полипропилена", re.compile(r"\bконтейнер\w*\s+из\s+полипропилен\w*\b")),
    ("Ланч-боксы", re.compile(r"\bланч[ -]бокс\w*\b")),
)
COLLECTIVE_STAND = re.compile(
    r"\b(?:центр поддержки экспорта|торгово-промышленная палата|"
    r"ассоциация|союз\s+(?:производителей|поставщиков|предприятий))\b",
    re.IGNORECASE,
)
OFFER = re.compile(
    r"\b(?:производ\w*|изготов\w*|изготавлива\w*|выпуска\w*|выпуск|"
    r"постав\w*|прода\w*|предлага\w*|ассортимент|дистрибьют\w*)\b"
)
SERVICE = re.compile(
    r"\b(?:упаковыва\w*|фасу\w*|оборудовани\w*|станк\w*|"
    r"машин\w*|формокомплект\w*|пресс-форм\w*|типограф\w*|декорировани\w*|"
    r"реклам\w*|технологи\w*)\b"
)
PACKAGING_GOODS = (
    (
        "Стеклотара",
        re.compile(
            r"\b(?:стеклотар\w*|стеклянн\w+\s+тар[аыуео]\w*|"
            r"стеклобутыл\w*|стеклобан\w*|стеклянн\w+\s+(?:бутыл|бан)\w*)\b"
            r"|\b(?:бан\w+|бутыл\w+)\b[^.;]{0,45}\bиз стекла\b"
        ),
    ),
    ("Жестяная упаковка", re.compile(r"\b(?:жестян\w+|металлическ\w+)\s+(?:упаков|бан|тар)\w*\b")),
    (
        "Укупорочные средства",
        re.compile(
            r"\b(?:укупорочн\w+\s+(?:средств|продукц|издел)\w*|"
            r"крыш[ке]\w*|колпач\w+|корков\w+\s+проб\w*)\b"
        ),
    ),
    (
        "Гибкая упаковка",
        re.compile(
            r"\b(?:гибк\w+\s+(?:(?:полимерн\w+|пищев\w+)\s+)?упаков\w*|"
            r"термоусадочн\w+\s+плен\w*|дой-пак\w*)\b"
        ),
    ),
    (
        "Картонная упаковка",
        re.compile(
            r"\b(?:(?:гофро)?картонн\w+\s+(?:упаков|тар|короб)\w*|"
            r"гофроупаков\w*|гофротар\w*|"
            r"упаков\w*\s+из\s+(?:гофро)?картон\w*)\b"
        ),
    ),
    (
        "Пластиковая упаковка",
        re.compile(
            r"\b(?:пластиков\w+|полимерн\w+|термоформованн\w+)\s+"
            r"(?:имл[- ]|iml[- ])?(?:упаков|тар|контейнер)\w*\b"
        ),
    ),
    (
        "Пищевая упаковка",
        re.compile(
            r"\b(?:пищев\w+\s+(?:(?:полимерн\w+|пластиков\w+)\s+)?"
            r"(?:упаков|тар|контейнер)\w*|(?:упаков|тар)\w*\s+для\s+"
            r"(?:(?:пищев|молочн)\w+\s+\w+|продукт\w+\s+питания))\b"
        ),
    ),
)
GENERAL_PACKAGING = re.compile(
    r"\b(?:упаковк\w+|упаковочн\w+\s+(?:продук|издел|материал)\w+|"
    r"тар[аыуе]\w*|гофротар\w*|стеклотар\w*)\b"
)
NON_FOOD_PACKAGING = re.compile(
    r"\b(?:непищев\w*|подарочн\w+\s+упаков\w*|курьерск\w+\s+пакет\w*|"
    r"сувенирн\w+\s+упаков\w*|упаков\w*\s+для\s+(?:косметик\w*|фарм\w*|хими\w*))\b"
)
FROZEN_BAKERY_RUBRIC = "замороженные кондитерские и хлебобулочные изделия"
FROZEN_BAKERY = re.compile(
    r"\b(?:заморож\w*|замороз\w*)\b[^.!?;]{0,100}"
    r"\b(?:выпечк|хлеб|кондитер|тест[аоу]|чизкейк|пирог|пирож|торт)\w*\b|"
    r"\b(?:выпечк|хлеб|кондитер|тест[аоу]|чизкейк|пирог|пирож|торт)\w*\b"
    r"[^.!?;]{0,100}\b(?:заморож|замороз)\w*\b"
)


@dataclass(frozen=True)
class Exhibition:
    title: str
    slug: str
    url: str


def _selector_text(selector: Any) -> str:
    return clean(selector.xpath("normalize-space(string(.))").get())


def _agro_material_labels(container: Any) -> list[str]:
    labels = []
    for anchor in container.css("a[href*='/rubricator/']"):
        code = urlsplit(anchor.attrib["href"]).path.rsplit("/rubricator/", 1)[-1]
        if code in AGRO_MATERIAL_RUBRICS and (label := _selector_text(anchor)):
            labels.append(label)
    return labels


def _agro_packaging_products(description: str, rubric_labels: list[str]) -> list[str]:
    if not rubric_labels:
        return []
    for sentence in re.split(r"[.!?;]\s+", description.casefold().replace("ё", "е")):
        if AGRO_EQUIPMENT_OR_PROCESS.search(sentence):
            continue
        has_offer = OFFER.search(sentence) is not None
        if has_offer and AGRO_CASINGS.search(sentence):
            return ["Колбасные оболочки"]
        sentence = re.sub(r"\bне\s+пищев\w+", "", sentence)
        if AGRO_PACKAGING.search(sentence) and AGRO_FOOD_USE.search(sentence):
            heading, separator, goods = sentence.partition(":")
            if (
                separator
                and AGRO_CATALOG_HEADING.match(heading)
                and not NON_FOOD_PACKAGING.search(heading)
            ):
                groups = [name for name, pattern in AGRO_FINISHED_GOODS if pattern.search(goods)]
                if groups:
                    return ["Пищевая упаковка", *groups]
            if not has_offer:
                continue
            offered = SERVICE.split(offer_subject(sentence), maxsplit=1)[0]
            groups = [name for name, pattern in PACKAGING_GOODS if pattern.search(offered)]
            return list(dict.fromkeys(["Пищевая упаковка", *groups]))[:8]
    return []


def _rubric_labels(container: Any, exhibition: Exhibition) -> list[str]:
    labels = []
    for anchor in container.css("a.category, a[href*='/rubricator/']"):
        code = urlsplit(anchor.attrib.get("href", "")).path.rsplit("/rubricator/", 1)[-1]
        if exhibition.slug.startswith("prodexpo-") and (code == "32" or code.startswith("32.")):
            continue
        labels.append(_selector_text(anchor))
    return labels


def _rubrics(labels: list[str]) -> tuple[list[str], list[str]]:
    first_by_category: dict[str, str] = {}
    products: list[str] = []
    for label in dict.fromkeys(labels):
        text = label.casefold().replace("ё", "е")
        if PACKAGING.search(text):
            category = "packaging"
        elif INGREDIENTS.search(text):
            category = "ingredients"
        elif FROZEN.search(text):
            category = "frozen"
        elif text and not NON_PRODUCT.search(text):
            category = "food"
        else:
            continue
        first_by_category.setdefault(category, label)
        products.append(label)
    representative = set(first_by_category.values())
    remaining = 8 - len(representative)
    selected = []
    for label in products:
        if label in representative:
            selected.append(label)
        elif remaining > 0:
            selected.append(label)
            remaining -= 1
    return sorted(first_by_category), selected


def prodexpo_products(description: str, labels: list[str]) -> tuple[list[str], list[str]]:
    """Publish packaging from the exhibitor's own offer, not its customer industries."""
    text = description.casefold().replace("ё", "е")
    rubric_packaging = any(PACKAGING.search(label.casefold().replace("ё", "е")) for label in labels)
    food_labels = []
    for label in labels:
        normalized = label.casefold().replace("ё", "е")
        if PACKAGING.search(normalized):
            continue
        if normalized == FROZEN_BAKERY_RUBRIC and not FROZEN_BAKERY.search(text):
            continue
        food_labels.append(label)
    packaging: list[str] = []
    independent_offers: list[str] = []
    packaging_context = False
    for sentence in offer_clauses(description):
        scope = SERVICE.split(offer_subject(sentence), maxsplit=1)[0]
        for offer in OFFER.finditer(scope):
            offered = OFFER.split(scope[offer.end() :], maxsplit=1)[0]
            groups = [name for name, pattern in PACKAGING_GOODS if pattern.search(offered)]
            if GENERAL_PACKAGING.search(offered) or groups:
                packaging_context = True
                food_use = AGRO_FOOD_USE.search(re.sub(r"\bне\s+пищев\w+", "", sentence))
                if NON_FOOD_PACKAGING.search(sentence) and not food_use:
                    continue
                if food_use or rubric_packaging:
                    packaging.extend(groups)
                    if not groups and food_use:
                        packaging.append("Пищевая упаковка")
            elif offered.strip(" ,—–:-"):
                independent_offers.append(offered)

    if packaging_context or rubric_packaging:
        supported = []
        for label in food_labels:
            tokens = re.findall(r"[а-я]{5,}", label.casefold().replace("ё", "е"))
            if any(
                re.search(r"\b" + token[:5], offer)
                for token in tokens
                for offer in independent_offers
            ):
                supported.append(label)
        categories, products = _rubrics(supported)
        packaging = list(dict.fromkeys(packaging))
        if packaging:
            categories.append("packaging")
            products = products[: max(1, 8 - len(packaging))] + packaging
        return sorted(categories), products[:8]
    return _rubrics(food_labels)


class ExpocentrSpider(scrapy.Spider):
    name = "expocentr"
    allowed_domains = ["icatalog.expocentr.ru"]
    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "DOWNLOAD_DELAY": 0.16,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 3.0,
        "AUTOTHROTTLE_START_DELAY": 0.16,
        "AUTOTHROTTLE_MAX_DELAY": 8,
        "DOWNLOAD_TIMEOUT": 20,
        "DOWNLOAD_MAXSIZE": 8_000_000,
        "RETRY_TIMES": 2,
    }

    def __init__(
        self,
        max_details: str | int = 0,
        include_agroprodmash: str | bool = False,
        exhibitions: str | tuple[str, ...] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.max_details = int(max_details)
        if self.max_details < 0:
            raise ValueError("max_details must be non-negative")
        include_agro = str(include_agroprodmash).casefold() in {"true", "1"}
        if exhibitions is None:
            exhibitions = ("prodexpo", "agroprodmash") if include_agro else ("prodexpo",)
        if isinstance(exhibitions, str):
            exhibitions = tuple(slug.strip() for slug in exhibitions.split(","))
        if not exhibitions or set(exhibitions) - set(EXHIBITION_NAMES.values()):
            raise ValueError("exhibitions must contain prodexpo or agroprodmash")
        self.exhibitions = exhibitions

    async def start(self) -> AsyncIterator[scrapy.Request]:
        yield scrapy.Request(INDEX, callback=self.parse_index, errback=self.request_failed)

    def parse_index(self, response: HtmlResponse) -> Iterator[scrapy.Request]:
        editions: dict[str, tuple[int, Exhibition]] = {}
        names = {name: slug for name, slug in EXHIBITION_NAMES.items() if slug in self.exhibitions}
        for anchor in response.css("a[href]"):
            title = _selector_text(anchor)
            match = EXHIBITION_LABEL.fullmatch(title)
            if not match:
                continue
            name, year = match["name"], int(match["year"])
            if name not in names or year > date.today().year:
                continue
            url = response.urljoin(anchor.attrib["href"])
            parts = urlsplit(url)
            if parts.netloc != self.allowed_domains[0] or not EXHIBITION_PATH.fullmatch(parts.path):
                continue
            exhibition = Exhibition(title, f"{names[name]}-{year}", url.rstrip("/"))
            if name not in editions or year > editions[name][0]:
                editions[name] = (year, exhibition)

        for name in names:
            if name not in editions:
                self.source_error(response.url, f"Exhibition not found: {name}")
                continue
            _, exhibition = editions[name]
            yield scrapy.Request(
                f"{exhibition.url}/list",
                callback=self.parse_listing,
                errback=self.request_failed,
                cb_kwargs={"exhibition": exhibition},
            )

    def parse_listing(
        self, response: HtmlResponse, exhibition: Exhibition
    ) -> Iterator[scrapy.Request]:
        rows = response.css("table#fresh-table > tbody > tr")
        if not rows:
            self.source_error(response.url, "Exhibition company table is missing or empty")
            return
        expected = urlsplit(exhibition.url)
        detail_prefix = expected.path + "/exhibitors/"
        requests: dict[str, tuple[str, list[str]]] = {}
        russian_rows = 0
        is_agro = exhibition.slug.startswith("agroprodmash-")
        for row in rows:
            columns = row.xpath("./td")
            if len(columns) != 5 or _selector_text(columns[1]).casefold() not in RUSSIA:
                continue
            russian_rows += 1
            if COLLECTIVE_STAND.search(_selector_text(columns[0])):
                continue
            if is_agro:
                rubrics = _agro_material_labels(columns[4])
            else:
                rubrics = _rubric_labels(columns[4], exhibition)
            if not rubrics or (not is_agro and not _rubrics(rubrics)[0]):
                continue
            href = columns[0].css("a::attr(href)").get()
            target = urlsplit(response.urljoin(href)) if href else None
            if not target or (target.scheme, target.netloc) != (expected.scheme, expected.netloc):
                self.source_error(response.url, "An exhibitor URL has an unexpected origin")
                continue
            detail_id = target.path.removeprefix(detail_prefix)
            if not EXHIBITOR_ID.fullmatch(detail_id):
                self.source_error(response.url, "An exhibitor URL has an unexpected format")
                continue
            canonical = urlunsplit((target.scheme, target.netloc, target.path, "", ""))
            if detail_id not in requests:
                requests[detail_id] = (canonical, rubrics)
        if not russian_rows or not requests:
            self.source_error(response.url, "No Russian exhibitor rows with product rubrics")
            return
        if self.crawler.stats:
            self.crawler.stats.inc_value("foodsource/expocentr/russian_rows", russian_rows)
            self.crawler.stats.inc_value("foodsource/expocentr/eligible_rows", len(requests))
        selected = list(requests.items())
        if self.max_details:
            selected = selected[: self.max_details]
        for detail_id, (url, rubrics) in selected:
            yield scrapy.Request(
                url,
                callback=self.parse_exhibitor,
                errback=self.request_failed,
                cb_kwargs={
                    "exhibition": exhibition,
                    "detail_id": detail_id,
                    "listing_rubrics": rubrics,
                },
            )

    def parse_exhibitor(
        self,
        response: HtmlResponse,
        exhibition: Exhibition,
        detail_id: str,
        listing_rubrics: list[str],
    ) -> dict[str, Any] | None:
        declarations = response.css("dl.dl-horizontal > dt")
        if not declarations:
            self.source_error(response.url, "Exhibitor facts are missing")
            return None
        facts = {
            _selector_text(term).rstrip(":").casefold(): term.xpath(
                "following-sibling::*[1][self::dd]"
            )
            for term in declarations
        }

        def value(key: str) -> str:
            return _selector_text(facts[key]) if facts.get(key) else ""

        name = clean(response.xpath("normalize-space((//h3)[1])").get())
        country, city = value("страна"), value("город")
        if not name or not country:
            self.source_error(response.url, "Exhibitor identity is incomplete")
            return None
        if country.casefold() not in RUSSIA or not city or COLLECTIVE_STAND.search(name):
            return None

        details = facts.get("рубрики")
        source_description = value("описание")
        if exhibition.slug.startswith("agroprodmash-"):
            material_labels = _agro_material_labels(details) if details else []
            products = _agro_packaging_products(
                source_description, material_labels or listing_rubrics
            )
            categories = ["packaging"] if products else []
        else:
            labels = _rubric_labels(details, exhibition) if details else listing_rubrics
            categories, products = prodexpo_products(source_description, labels)
        if not products:
            return None
        address = value("адрес")
        region = next(
            (part.strip() for part in address.split(",") if REGION.fullmatch(part.strip())), None
        )
        description = source_description or "Ассортимент по каталогу выставки: " + ", ".join(
            products
        )
        inn_candidate = INN.search(description)
        site = company_url(value("сайт"))
        return {
            "provider": self.name,
            "external_id": f"{exhibition.slug}-{detail_id}",
            "name": name,
            "city": city,
            "address": address,
            "region_label": region,
            "inn": valid_inn(inn_candidate.group(1)) if inn_candidate else None,
            "website": site if official_domain(site) else None,
            "description": (description[:600].rsplit(" ", 1)[0] + "…")
            if len(description) > 600
            else description,
            "category_ids": categories,
            "products": products,
            "email": email_address(value("e-mail")),
            "phone": phone_number(value("телефон")),
            "source_url": response.url,
            "source_label": f"Каталог участников {exhibition.title}",
            "enrich_website": False,
        }

    def request_failed(self, failure: Failure) -> None:
        self.source_error(failure.request.url, failure.getErrorMessage())

    def source_error(self, url: str, reason: str) -> None:
        if self.crawler.stats:
            self.crawler.stats.inc_value(SOURCE_ERRORS)
        self.logger.warning("%s: %s", reason, url)
