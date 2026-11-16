import asyncio
from datetime import date
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from scrapy.http import HtmlResponse

from foodsource.ingest.__main__ import ensure_completed
from foodsource.ingest.enrich import OfficialWebsitePipeline
from foodsource.ingest.normalize import normalize_item
from foodsource.ingest.rosupack import DEFAULT_URL, RosUpackSpider, detail_item, packaging_groups
from foodsource.ingest.website_location import website_contact_page, website_location


def html(body: str, url: str = DEFAULT_URL) -> HtmlResponse:
    return HtmlResponse(url, body=body.encode(), encoding="utf-8")


def test_uses_published_filter_and_follows_declared_page_window():
    form = html(
        '<input id="selected_pass_event" value="17"><select id="country_id"><option value="Russia">'
        'Россия</option></select><option class="category-option" value="4033">'
        "Готовая упаковка (в т.ч. стеклянная тара)</option>"
    )
    spider = RosUpackSpider()
    spider.crawler = SimpleNamespace(stats=None)
    first = spider.parse_index(form)
    assert first is not None and first.method == "POST"
    assert parse_qs(first.body.decode())["event_prod_cat_id"] == ["4033"]
    assert parse_qs(first.body.decode())["event_id"] == ["17"]
    links = html(
        f'<div class="post-list"><h5 class="card-title"><a href="{DEFAULT_URL}/ExbDetails/abc=">'
        'Упаковка</a></h5></div><div class="pagination-info">Показано от 1 до 1 из 2</div>'
    )
    detail, next_page = list(spider.parse_list(links, first.cb_kwargs["form"], 0))
    assert detail.cb_kwargs == {"identifier": "abc="}
    assert next_page.cb_kwargs["offset"] == 1
    assert not list(spider.parse_list(links, first.cb_kwargs["form"], 1))
    unavailable = html(
        '<div class="post-list"><h5 class="card-title"><a href="javascript:void(0)">'
        'Нет публичной карточки</a></h5></div><div class="pagination-info">Показано от 2 до 2 из 2</div>'
    )
    assert not list(spider.parse_list(unavailable, first.cb_kwargs["form"], 1))


def test_failed_listed_detail_rejects_partial_publication():
    stats = {"finish_reason": "finished", "item_scraped_count": 1}

    def increment(key):
        stats[key] = stats.get(key, 0) + 1

    spider = RosUpackSpider()
    spider.crawler = SimpleNamespace(stats=SimpleNamespace(inc_value=increment))
    failure = SimpleNamespace(
        request=SimpleNamespace(url=DEFAULT_URL), getErrorMessage=lambda: "Timed out"
    )
    spider.detail_failed(failure)
    with pytest.raises(RuntimeError, match=r"rosupack: finished, 1 errors"):
        ensure_completed({spider.name: stats})


def test_checks_food_use_and_rejects_equipment_and_generic_logistics():
    assert packaging_groups("Производим оборудование для пищевой упаковки.", []) == []
    assert packaging_groups("Производим коробки для переезда и транспортировки.", []) == []
    assert packaging_groups("Выпускаем эко-упаковку для HoReCa.", []) == ["Пищевая упаковка"]
    assert packaging_groups("", ["ПЭТ тара для молока", "Станок для пищевой упаковки"]) == [
        "Пищевая упаковка"
    ]


def test_offer_matches_the_exhibitor_country_and_official_website():
    template = """
        <section class="company-details"><div><div><h1 class="company-title">Тара</h1></div></div>
        <h6>{country}</h6><table><tr><td class="parent-cat">Готовая упаковка</td></tr></table>
        <div class="company-info"><a href="https://tara.example.org/">Сайт</a></div></section>
        <div id="pills-About"><p>Производим пищевую упаковку.</p></div>
        <div class="product-gallery"><figcaption>Коробки для пиццы</figcaption></div>
    """
    item = detail_item(html(template.format(country="Россия")), "abc=", "2026")
    assert item is not None
    assert item["city"] == "" and item["locate_website"] is True
    assert "Коробки для пиццы" in item["products"]
    assert item["website"] == "https://tara.example.org/"
    assert detail_item(html(template.format(country="Беларусь")), "abc=", "2026") is None


@pytest.mark.parametrize(
    "body, city",
    [
        ("<section><span>Россия, Казань</span></section>", "Казань"),
        ("<h2>Адрес:</h2><p>141006, МО, г. Мытищи, проезд 12</p>", "Мытищи"),
        ("<address>г. Нижний Новгород, ул. Пробная, 3</address>", "Нижний Новгород"),
    ],
)
def test_extracts_an_explicit_city_from_own_contact_page(body, city):
    assert website_location(html(body, "https://tara.example.org/kontakty/"))[0] == city


def test_ambiguous_branches_do_not_create_a_fictional_primary_location():
    page = html(
        "<address>г. Казань, ул. Пробная, 3</address><address>г. Москва, ул. Пробная, 4</address>",
        "https://tara.example.org/contacts/",
    )
    assert website_location(page) is None
    home = html(
        '<a href="https://third-party.example.com/contacts">Контакты</a><a href="/contacts/">Контакты</a>',
        "https://tara.example.org/",
    )
    assert website_contact_page(home, "tara.example.org") == "https://tara.example.org/contacts/"


def test_removes_field_label_before_resolving_address():
    response = html("<address>Адрес: Свердловская область, г. Михайловск, ул. Пробная, 2</address>")
    city, address, _ = website_location(response)
    assert city == "Михайловск"
    assert address.startswith("Свердловская область,")


def test_only_official_contact_page_supports_derived_location_and_phone(monkeypatch):
    pipeline = OfficialWebsitePipeline(SimpleNamespace(stats=None))

    async def find_address(_url, _domain):
        return "https://tara.example.org/contacts/", {
            "city": "Казань",
            "address": "Казань, ул. Пробная, 3",
            "region_label": "Республика Татарстан",
            "phone": "+78432220000",
            "email": None,
        }

    monkeypatch.setattr(pipeline, "inspect_location", find_address)
    item = asyncio.run(
        pipeline.process_item(
            {
                "provider": "rosupack",
                "external_id": "2026:abc=",
                "name": "Тара",
                "city": "",
                "website": "https://tara.example.org/",
                "category_ids": ["packaging"],
                "products": ["Коробки для пиццы"],
                "source_url": DEFAULT_URL,
                "source_label": "Каталог RosUpack",
                "locate_website": True,
            }
        )
    )
    normalized = normalize_item({**item, "region_id": "city-551487"}, date(2026, 11, 16))
    by_field = {evidence["field"]: evidence["url"] for evidence in normalized["field_evidence"]}
    assert by_field["region_id"] == by_field["phone"] == item["enrichment_url"]
    assert by_field["products"] == DEFAULT_URL
