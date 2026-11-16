from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scrapy.http import HtmlResponse

from foodsource.ingest.expocentr import INDEX, Exhibition, ExpocentrSpider

AGRO = Exhibition(
    "АГРОПРОДМАШ-2026",
    "agroprodmash-2026",
    f"{INDEX}/exhibitions/00000000-1000-2000-3000-000000000026",
)


def html(url: str, content: str) -> HtmlResponse:
    return HtmlResponse(url, body=content.encode(), encoding="utf-8")


@pytest.fixture
def spider() -> ExpocentrSpider:
    instance = ExpocentrSpider(exhibitions=("agroprodmash",))
    instance.crawler = SimpleNamespace(stats=Mock())
    return instance


def test_agro_mode_does_not_request_prodexpo(spider: ExpocentrSpider) -> None:
    listing = html(
        INDEX,
        f'<a href="{AGRO.url}">АГРОПРОДМАШ-2026</a>'
        f'<a href="{AGRO.url.replace("0026", "0025")}">ПРОДЭКСПО-2026</a>',
    )

    (request,) = tuple(spider.parse_index(listing))

    assert request.url == f"{AGRO.url}/list"


def test_listing_schedules_only_approved_material_rubrics(spider: ExpocentrSpider) -> None:
    rows = []
    for detail_id, code, label in [
        (11, "12.8.3", "Гибкая упаковка"),
        (12, "12.8.8.2", "Машины для палетизации"),
        (13, "12.8.10.2", "Системы оптического контроля"),
        (14, "5.2", "Ингредиенты по направлениям"),
    ]:
        rows.append(
            f'<tr><td><a href="{AGRO.url}/exhibitors/{detail_id}">Участник</a></td>'
            f"<td>Россия</td><td>3</td><td>A1</td>"
            f'<td><a href="{AGRO.url}/rubricator/{code}">{label}</a></td></tr>'
        )
    listing = html(
        f"{AGRO.url}/list", f'<table id="fresh-table"><tbody>{"".join(rows)}</tbody></table>'
    )

    (request,) = tuple(spider.parse_listing(listing, AGRO))

    assert request.url == f"{AGRO.url}/exhibitors/11"
    assert request.cb_kwargs["listing_rubrics"] == ["Гибкая упаковка"]


@pytest.mark.parametrize(
    ("description", "products"),
    [
        (
            "Выпускаем гибкую упаковку для пищевой промышленности.",
            ["Пищевая упаковка", "Гибкая упаковка"],
        ),
        ("Поставляем оборудование. Выпускаем колбасные оболочки.", ["Колбасные оболочки"]),
        ("Производим упаковку для непищевой и косметической продукции.", []),
        ("Поставляем гибкую упаковку для не пищевых продуктов.", []),
        ("Машины для упаковки пищевых продуктов.", []),
        ("Счет, дозирование и упаковка пищевых и промышленных продуктов.", []),
        ("Поставщик оборудования для пищевой промышленности. Производит пленку для палет.", []),
        ("Производитель гофротары и стрейч-пленки для перевозки грузов.", []),
        (
            "Упаковка из полимерных материалов для пищевой продукции: лотки под запайку, "
            "прозрачные и цветные контейнеры из полипропилена, ланч-боксы.",
            ["Пищевая упаковка", "Лотки под запайку", "Контейнеры из полипропилена", "Ланч-боксы"],
        ),
        ("Упаковка для пищевой продукции: линии для фасовки в контейнеры из полипропилена.", []),
        ("Оборудование для выпуска пищевой упаковки: лотки под запайку и ланч-боксы.", []),
        ("Упаковка из полимеров для непищевой продукции: контейнеры из полипропилена.", []),
        ("Упаковка для пищевой продукции: индивидуальный дизайн и консультации.", []),
    ],
)
def test_detail_requires_food_application(
    spider: ExpocentrSpider, description: str, products: list[str]
) -> None:
    card = html(
        f"{AGRO.url}/exhibitors/11",
        f'<h3>Производитель</h3><dl class="dl-horizontal">'
        "<dt>Страна:</dt><dd>Россия</dd><dt>Город:</dt><dd>Брянск</dd>"
        f"<dt>Описание:</dt><dd>{description}</dd></dl>",
    )

    record = spider.parse_exhibitor(card, AGRO, "11", ["Гибкая упаковка", "Жестяная упаковка"])

    if products:
        assert record["city"] == "Брянск"
        assert record["category_ids"] == ["packaging"]
        assert record["products"] == products
        assert record["source_url"] == card.url
        assert record["enrich_website"] is False
    else:
        assert record is None
