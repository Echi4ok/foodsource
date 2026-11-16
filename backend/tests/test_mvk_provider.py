import json
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs

import pytest
from scrapy.http import HtmlResponse, TextResponse

from foodsource.ingest.mvk import (
    EXHIBITIONS,
    MAX_WEBSITE_LEADS,
    SOURCE_ERRORS,
    MvkSpider,
    _categories_and_products,
)
from foodsource.ingest.qualification import qualifying_products

FOODTECH, INTERFOOD, INGREDIENTS = EXHIBITIONS


@pytest.fixture
def spider() -> MvkSpider:
    instance = MvkSpider()
    instance.crawler = SimpleNamespace(stats=Mock())
    return instance


def listing(content: str) -> HtmlResponse:
    return HtmlResponse(INTERFOOD.listing, body=content.encode(), encoding="utf-8")


def detail(payload: object) -> TextResponse:
    return TextResponse(
        "https://foodtech-ural.ru/ru-RU/about/exhibitor-list/exhibitorview.aspx?id=123",
        body=json.dumps(payload).encode(),
        encoding="utf-8",
    )


def test_page_follows_the_actual_form_postback_and_deduplicates_cards(spider: MvkSpider) -> None:
    response = listing("""
        <form id="form" method="post" action="./exhibitor-list.aspx?aliaspath=%2fabout%2flist">
          <input type="hidden" name="__VIEWSTATE" value="from-source">
          <div class="eList">
            <a data-item="2026">Supplier</a>
            <a data-item="2026">Same supplier</a>
            <a data-item="2268">Second supplier</a>
          </div>
          <a class="page-link" href="#">1</a>
          <a class="page-link" href="javascript:__doPostBack('catalog$pager','2')">2</a>
        </form>
    """)
    first, second, next_page = list(spider.parse_listing(response, INTERFOOD, page=1))

    assert first.url.endswith("/exhibitorview.aspx?id=2026")
    assert second.url.endswith("/exhibitorview.aspx?id=2268")
    assert first.callback == spider.parse_detail
    assert next_page.method == "POST"
    assert next_page.cb_kwargs == {"show": INTERFOOD, "page": 2}
    assert next_page.meta["cookiejar"] == INTERFOOD.id
    assert MvkSpider.custom_settings["COOKIES_ENABLED"] is True
    assert parse_qs(next_page.body.decode()) == {
        "__VIEWSTATE": ["from-source"],
        "__EVENTTARGET": ["catalog$pager"],
        "__EVENTARGUMENT": ["2"],
    }
    spider.crawler.stats.inc_value.assert_not_called()


def test_invalid_or_repeated_listing_is_counted_as_a_source_error(spider: MvkSpider) -> None:
    page = listing('<div class="eList"><a data-item="3184">Supplier</a></div>')

    assert len(list(spider.parse_listing(page, INTERFOOD, page=1))) == 1
    assert list(spider.parse_listing(page, INTERFOOD, page=2)) == []
    assert list(spider.parse_listing(listing("<h1>Error</h1>"), INTERFOOD, page=3)) == []
    assert spider.crawler.stats.inc_value.call_count == 2
    spider.crawler.stats.inc_value.assert_called_with(SOURCE_ERRORS)


def test_card_uses_registered_city_and_source_evidence(spider: MvkSpider) -> None:
    card = {
        "Name": "ООО &laquo;Северная тара&raquo;",
        "Region": "г. Екатеринбург",
        "Site": "Северная тара, www.example.com; https://vk.com/suppliers",
        "Email": "Отдел продаж: CONTACT@EXAMPLE.COM",
        "Phone": "+7 (343) 000-00-01",
        "Description": "<p>Производство упаковки для пищевой промышленности.</p>"
        "<p>Пищевая плёнка и коробки для пиццы.</p>"
        "<p>Минимальный тираж от 20 коробок.</p><script>Бытовые услуги</script>",
        "INN": "6672357965",
    }
    record = spider.parse_detail(detail([card]), FOODTECH, "123")

    assert record["provider"] == "mvk"
    assert record["external_id"] == "foodtech-ural-123"
    assert record["name"] == "ООО «Северная тара»"
    assert record["inn"] == "6672357965"
    assert record["region_id"] == "ekaterinburg"
    assert record["website"] == "https://www.example.com/"
    assert record["email"] == "contact@example.com"
    assert record["phone"] == "+7 (343) 000-00-01"
    assert record["category_ids"] == ["packaging"]
    assert "Пищевая плёнка" in record["products"]
    assert "Упаковка для пиццы" in record["products"]
    assert record["minimum_order"] == "от 20 коробок"
    assert "Бытовые услуги" not in record["description"]
    assert record["source_url"] == f"{FOODTECH.listing}#123"


@pytest.mark.parametrize(
    "region", ["Москва", "Екатеринбург, Москва", "Екатеринбургская область", None]
)
def test_shipping_mention_does_not_evidence_a_city(region: str | None, spider: MvkSpider) -> None:
    payload = [{"Name": "Оптовый чай", "Region": region, "Description": "Чай оптом в Екатеринбург"}]
    assert spider.parse_detail(detail(payload), INTERFOOD, "123") is None
    spider.crawler.stats.inc_value.assert_not_called()


@pytest.mark.parametrize(
    "description",
    [
        "Производитель упаковочного оборудования для производства хлеба.",
        "Поставщик оборудования для производства пищевой упаковки и замороженной выпечки.",
        "В нашей пекарне производим хлеб и выпечку для магазина.",
        "Первая коммерческая лаборатория по исследованиям пищевой продукции, сертификация.",
    ],
)
def test_excludes_equipment_and_retail_only_bakeries(description: str, spider: MvkSpider) -> None:
    payload = [
        {"Name": "Испытуемая компания", "Region": "Екатеринбург", "Description": description}
    ]
    assert spider.parse_detail(detail(payload), INTERFOOD, "123") is None


def test_incidental_packaging_and_unreported_freezing_are_not_inferred(spider: MvkSpider) -> None:
    payload = [
        {
            "Name": "Пищевое производство",
            "Region": "Екатеринбург",
            "Site": "Каталог продуктов",
            "INN": "6672357964",
            "Description": "Собственное производство мясных полуфабрикатов оптом. "
            "Продукция фасуется в пищевую упаковку для отгрузки.",
        }
    ]
    record = spider.parse_detail(detail(payload), INTERFOOD, "123")

    assert record["website"] is None
    assert record["inn"] is None
    assert record["category_ids"] == ["food"]
    assert record["products"] == ["Мясные полуфабрикаты"]


def test_foodtech_polyer_publishes_packaging_without_its_customer_industries() -> None:
    description = (
        "ПолиЭР - ведущий производитель пищевой полимерной упаковки и лент (ПП, ПЭТ, ПС) "
        "для автоматических линий. Мы создаем надежные решения для производителей мяса, "
        "рыбы, молочной продукции, кондитерских изделий и овощей."
    )
    assert _categories_and_products(description) == (["packaging"], ["Пищевая упаковка"])


def test_independent_food_offer_survives_after_a_packaging_use_case() -> None:
    description = (
        "Производим пищевую упаковку для производителей мяса и сыров, "
        "а также поставляем замороженную рыбу."
    )
    assert _categories_and_products(description) == (
        ["frozen", "packaging", "food"],
        ["Пищевая упаковка", "Рыбная продукция"],
    )


def test_explicit_frozen_ready_meals_qualify_without_another_product_in_the_clause() -> None:
    assert _categories_and_products(
        "Компания производит первые и вторые готовые обеденные замороженные блюда, "
        "а также производит большой ассортимент полуфабрикатов."
    ) == (["frozen", "food"], ["Замороженные готовые блюда", "Полуфабрикаты"])


def test_application_of_packaging_does_not_supply_frozen_goods_on_a_website() -> None:
    assert qualifying_products(
        "Производим пищевые контейнеры для замороженных полуфабрикатов. "
        "Поставляем смеси для хлебопечения."
    ) == {"packaging": {"Пищевая упаковка"}, "ingredients": {"Хлебопекарные смеси"}}


def test_uncategorized_local_manufacturer_with_an_explicit_website_is_an_enrichment_lead(
    spider: MvkSpider,
) -> None:
    card = {
        "Name": "Медная упаковка",
        "Region": "Екатеринбург",
        "Site": "www.example.com, https://vk.com/medpack",
        "Description": "<p>Промышленное предприятие.</p><p>Производство пакетов и клейкой ленты.</p>",
    }
    record = spider.parse_detail(detail([card]), FOODTECH, "123")

    assert record["category_ids"] == []
    assert record["products"] == []
    assert record["website"] == "https://www.example.com/"
    assert (
        record["description"] == "Промышленное предприятие. Производство пакетов и клейкой ленты."
    )


@pytest.mark.parametrize(
    ("region", "website", "description"),
    [
        ("Москва", "example.com", "Производство пакетов и клейкой ленты."),
        ("Екатеринбург", "", "Производство пакетов и клейкой ленты."),
        ("Екатеринбург", "https://vk.com/medpack", "Производство пакетов и клейкой ленты."),
        ("Екатеринбург", "example.com", "Первая лаборатория по анализу продуктов питания."),
        ("Екатеринбург", "example.com", "Производитель упаковочного оборудования."),
        ("Екатеринбург", "example.com", "Производим выпечку. Продажа только в розницу."),
    ],
)
def test_unqualified_website_observations_do_not_enter_enrichment(
    region: str, website: str, description: str, spider: MvkSpider
) -> None:
    card = {"Name": "Компания", "Region": region, "Site": website, "Description": description}
    assert spider.parse_detail(detail([card]), FOODTECH, "123") is None


def test_lead_limit_does_not_discard_already_qualified_suppliers(spider: MvkSpider) -> None:
    spider.website_leads = MAX_WEBSITE_LEADS - 1
    card = {
        "Name": "Медная упаковка",
        "Region": "Екатеринбург",
        "Site": "example.com",
        "Description": "Производство пакетов.",
    }

    assert spider.parse_detail(detail([card]), FOODTECH, "123") is not None
    assert spider.parse_detail(detail([card]), FOODTECH, "124") is None
    card["Description"] = "Производство упаковки для пищевой промышленности."
    record = spider.parse_detail(detail([card]), FOODTECH, "125")
    assert record["category_ids"] == ["packaging"]


@pytest.mark.parametrize(
    "payload", [None, [], {"Name": "Ошибочный ответ"}, [{"Name": "Без города"}]]
)
def test_invalid_card_increments_error_counter(payload: object, spider: MvkSpider) -> None:
    assert spider.parse_detail(detail(payload), INTERFOOD, "123") is None
    spider.crawler.stats.inc_value.assert_called_once_with(SOURCE_ERRORS)


def test_ingredients_pager_uses_public_popup_and_ellipsis_postback(spider: MvkSpider) -> None:
    response = HtmlResponse(
        INGREDIENTS.listing,
        body=b"""
            <form id="form" method="post" action="./exhibitor-list.aspx">
              <input type="hidden" name="__VIEWSTATE" value="state">
              <div class="card participant" data-exib="114001"></div>
              <div class="pagination">
                <a class="carousel_btn" href="javascript:__doPostBack('pager','4')">...</a>
                <a class="carousel_btn next" href="javascript:__doPostBack('pager','9')"></a>
              </div>
            </form>
        """,
        encoding="utf-8",
    )
    card, following = list(spider.parse_listing(response, INGREDIENTS, page=3))
    assert card.url.endswith("/exhibitorpopup.aspx?id=114001")
    assert following.method == "POST"
    assert following.cb_kwargs == {"show": INGREDIENTS, "page": 4}
    assert parse_qs(following.body.decode())["__EVENTARGUMENT"] == ["4"]


def test_ingredients_requires_evidence_of_a_food_supply_business(spider: MvkSpider) -> None:
    card = {
        "Name": "Деловые ингредиенты",
        "Country": "Россия",
        "Region": "Екатеринбург",
        "Description": "Поставляем ароматизаторы и стабилизаторы для пищевой промышленности.",
        "Address": "Свердловская область, г. Екатеринбург",
        "PhoneOne": "+7 (343) 000-00-01",
    }

    record = spider.parse_detail(detail([card]), INGREDIENTS, "123")
    assert record["category_ids"] == ["ingredients"]
    assert record["products"] == ["Пищевые ингредиенты", "Ароматизаторы", "Стабилизаторы"]
    assert record["address"] == "Свердловская область, г. Екатеринбург"
    assert record["phone"] == "+7 (343) 000-00-01"
    assert record["source_url"] == f"{INGREDIENTS.listing}#123"


@pytest.mark.parametrize(
    ("name", "country", "description"),
    [
        ("Фармфарм", "Россия", "Производитель экстрактов для производства БАД и косметики."),
        ("Корм", "Россия", "Поставщик кормовых добавок для сельского хозяйства."),
        ("Аппарат", "Россия", "Производитель оборудования для смешивания пищевых ингредиентов."),
        ("Тарный завод", "Россия", "Производитель упаковки для пищевых добавок."),
        ("Журнал «Пищевик»", "Россия", "Поставщик новостей и обзоров рынка пищевых ингредиентов."),
        ("Сырьё", "Беларусь", "Поставщик пищевых ингредиентов."),
    ],
)
def test_ingredients_excludes_nonfood_and_foreign_records(
    name: str, country: str, description: str, spider: MvkSpider
) -> None:
    assert (
        spider.parse_detail(
            detail(
                [
                    {
                        "Name": name,
                        "Country": country,
                        "Region": "Екатеринбург",
                        "Description": description,
                    }
                ]
            ),
            INGREDIENTS,
            "123",
        )
        is None
    )
