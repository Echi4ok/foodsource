from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scrapy.http import HtmlResponse

from foodsource.ingest.expocentr import (
    INDEX,
    SOURCE_ERRORS,
    Exhibition,
    ExpocentrSpider,
    _rubrics,
    prodexpo_products,
)

EXHIBITION = Exhibition(
    "ПРОДЭКСПО-2026",
    "prodexpo-2026",
    f"{INDEX}/exhibitions/00000000-1000-2000-3000-000000000026",
)


def response(url: str, content: str) -> HtmlResponse:
    return HtmlResponse(url, body=content.encode(), encoding="utf-8")


@pytest.fixture
def spider() -> ExpocentrSpider:
    instance = ExpocentrSpider()
    instance.crawler = SimpleNamespace(stats=Mock())
    return instance


def row(code: int, country: str, rubrics: list[str]) -> str:
    href = f"{EXHIBITION.url}/exhibitors/{code}?stand=A1"
    labels = "".join(f'<a class="category">{label}</a>' for label in rubrics)
    return (
        f"<tr><td><a href='{href}'>Производитель</a></td><td>{country}</td>"
        f"<td>Павильон № 1</td><td>A1</td><td>{labels}</td></tr>"
    )


def test_latest_public_edition_is_discovered_without_exhibition_ids(
    spider: ExpocentrSpider,
) -> None:
    previous = EXHIBITION.url.removesuffix("26") + "24"
    future = EXHIBITION.url.removesuffix("26") + "99"
    index = response(
        INDEX,
        f"""
        <a href="{previous}">ПРОДЭКСПО-2024</a>
        <a href="{EXHIBITION.url}">ПРОДЭКСПО-2026</a>
        <a href="{future}">ПРОДЭКСПО-2099</a>
        <a href="https://example.invalid/ru/exhibitions/00000000-1000-2000-3000-000000000026">
          ПРОДЭКСПО-2026
        </a>
        """,
    )
    (request,) = list(spider.parse_index(index))

    assert request.url == f"{EXHIBITION.url}/list"
    assert request.cb_kwargs["exhibition"] == EXHIBITION


def test_national_listing_skips_equipment_and_non_russian_rows(spider: ExpocentrSpider) -> None:
    content = "".join(
        (
            row(1, "Россия", ["Мороженое, замороженные десерты"]),
            row(2, "Россия", ["Упаковочное оборудование и машины"]),
            row(3, "Китай", ["Гибкая упаковка"]),
            row(4, "Россия", ["Ингредиенты для пищевой промышленности"]),
            row(4, "Россия", ["Ингредиенты для пищевой промышленности"]),
        )
    )
    listing = response(
        f"{EXHIBITION.url}/list", f'<table id="fresh-table"><tbody>{content}</tbody></table>'
    )
    first, second = list(spider.parse_listing(listing, EXHIBITION))

    assert first.url == f"{EXHIBITION.url}/exhibitors/1"
    assert second.url == f"{EXHIBITION.url}/exhibitors/4"
    assert first.cb_kwargs["listing_rubrics"] == ["Мороженое, замороженные десерты"]
    assert second.cb_kwargs["detail_id"] == "4"
    spider.crawler.stats.inc_value.assert_any_call("foodsource/expocentr/russian_rows", 4)
    spider.crawler.stats.inc_value.assert_any_call("foodsource/expocentr/eligible_rows", 2)


def test_preserves_the_published_product_for_each_resulting_category() -> None:
    foods = ["Соки", "Сыры", "Хлеб", "Кефир", "Йогурты", "Молоко", "Соусы", "Макароны"]
    frozen = "Замороженные кондитерские и хлебобулочные изделия"

    categories, products = _rubrics([*foods, frozen])

    assert categories == ["food", "frozen"]
    assert products == [*foods[:-1], frozen]


def test_uses_exhibition_rubric_hierarchy_to_exclude_wine_varieties(
    spider: ExpocentrSpider,
) -> None:
    card = response(
        f"{EXHIBITION.url}/exhibitors/123",
        """
        <h3>Участник</h3><dl class="dl-horizontal">
          <dt>Страна</dt><dd>Россия</dd><dt>Город</dt><dd>Пенза</dd>
          <dt>Рубрики</dt><dd class="category"><a href="/rubricator/32.4.2">Виноградные</a></dd>
        </dl>
        """,
    )

    assert spider.parse_exhibitor(card, EXHIBITION, "123", ["Виноградные"]) is None


@pytest.mark.parametrize("city", ["Екатеринбург", "Рязань", "Хабаровск"])
def test_detail_uses_explicit_city_and_published_rubrics(
    city: str, spider: ExpocentrSpider
) -> None:
    card = response(
        f"{EXHIBITION.url}/exhibitors/123",
        f"""
        <h3>ООО «Пищевые материалы»</h3>
        <dl class="dl-horizontal">
          <dt>Страна: </dt><dd>Россия</dd>
          <dt>Город: </dt><dd>{city}</dd>
          <dt>Адрес: </dt><dd>Россия, Тестовая область, г. {city}</dd>
          <dt>Сайт: </dt><dd><a href="https://example.com/">https://example.com/</a></dd>
          <dt>Телефон: </dt><dd>+7 (343) 222-22-22</dd>
          <dt>E-mail: </dt><dd>sales@example.com</dd>
          <dt>Описание: </dt><dd>Изготавливаем замороженные хлебобулочные изделия. ИНН 6672357965.</dd>
          <dt class="category">Рубрики: </dt><dd>
            <a class="category">Гибкая упаковка</a>
            <a class="category">Упаковочное оборудование и машины</a>
            <a class="category">Замороженные кондитерские и хлебобулочные изделия</a>
            <a class="category">Российский экспортёр</a>
          </dd>
        </dl>
        """,
    )
    record = spider.parse_exhibitor(card, EXHIBITION, "123", [])

    assert record["provider"] == "expocentr"
    assert record["external_id"] == "prodexpo-2026-123"
    assert record["city"] == city
    assert record["region_label"] == "Тестовая область"
    assert record["phone"] == "+73432222222"
    assert record["email"] == "sales@example.com"
    assert record["website"] == "https://example.com/"
    assert record["inn"] == "6672357965"
    assert record["category_ids"] == ["frozen"]
    assert record["products"] == ["Замороженные кондитерские и хлебобулочные изделия"]
    assert record["source_url"] == card.url
    assert record["enrich_website"] is False


def test_detail_without_a_city_is_excluded(spider: ExpocentrSpider) -> None:
    card = response(
        f"{EXHIBITION.url}/exhibitors/321",
        (
            '<h3>ООО «Хлеб»</h3><dl class="dl-horizontal"><dt>Страна:</dt><dd>Россия</dd>'
            "<dt>Описание:</dt><dd>Отправляем выпечку в Москву</dd></dl>"
        ),
    )
    assert spider.parse_exhibitor(card, EXHIBITION, "321", ["Выпечка"]) is None
    spider.crawler.stats.inc_value.assert_not_called()


def test_broken_exhibition_is_visible_in_source_stats(spider: ExpocentrSpider) -> None:
    broken = response(f"{EXHIBITION.url}/list", "<h1>Страница недоступна</h1>")
    assert list(spider.parse_listing(broken, EXHIBITION)) == []
    spider.crawler.stats.inc_value.assert_called_once_with(SOURCE_ERRORS)


@pytest.mark.parametrize(
    ("description", "labels", "expected"),
    [
        (
            "Производство и оптовая продажа стеклянной тары.",
            ["Питьевая вода", "Стеклотара и бутилирование", "Упаковочные материалы"],
            (["packaging"], ["Стеклотара"]),
        ),
        (
            "Производство и продажа банок и бутылок из стекла для пищевой промышленности. "
            "Изготовление банок по чертежам заказчика, производство формокомплектов.",
            ["Консервы рыбные", "Мед и продукты пчеловодства", "Икра рыб"],
            (["packaging"], ["Стеклотара"]),
        ),
        (
            "Производитель оборудования для производства стеклотары и упаковки пищевых продуктов.",
            ["Консервы рыбные", "Гибкая упаковка"],
            ([], []),
        ),
        (
            "Производство подарочной упаковки для заказчиков из разных отраслей.",
            ["Готовая упаковка", "Кондитерские полуфабрикаты"],
            ([], []),
        ),
        (
            "Производство мороженого. Производство пластиковой упаковки для пищевых продуктов.",
            ["Мороженое, замороженные десерты", "Питьевая вода", "Жестяная упаковка, крышки"],
            (["frozen", "packaging"], ["Мороженое, замороженные десерты", "Пластиковая упаковка"]),
        ),
        (
            "Московская фабрика мороженого выпускает более 200 уникальных сортов мороженого.",
            [
                "Мороженое, замороженные десерты",
                "Замороженные кондитерские и хлебобулочные изделия",
            ],
            (["frozen"], ["Мороженое, замороженные десерты"]),
        ),
        (
            "Оптовые поставки хлебобулочных и кондитерских изделий глубокой шоковой заморозки.",
            ["Замороженные кондитерские и хлебобулочные изделия"],
            (["frozen"], ["Замороженные кондитерские и хлебобулочные изделия"]),
        ),
    ],
)
def test_publishes_own_products_instead_of_customer_industries(
    description, labels, expected
) -> None:
    assert prodexpo_products(description, labels) == expected


def test_trade_association_is_not_a_supplier(spider: ExpocentrSpider) -> None:
    card = response(
        f"{EXHIBITION.url}/exhibitors/321",
        '<h3>Ассоциация пищевой промышленности</h3><dl class="dl-horizontal">'
        "<dt>Страна</dt><dd>Россия</dd><dt>Город</dt><dd>Москва</dd>"
        "<dt>Описание</dt><dd>Объединяет производителей упаковки.</dd></dl>",
    )
    assert spider.parse_exhibitor(card, EXHIBITION, "321", ["Питьевая вода"]) is None
