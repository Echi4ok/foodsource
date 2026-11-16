import json
from datetime import date

import pytest
from scrapy.http import HtmlResponse

from foodsource.ingest.normalize import normalize_item
from foodsource.ingest.producers import parse_catalog_page

URL = "https://packing.example.org/catalog/pizza/"
SELLER = {
    "@type": ["Organization", "LocalBusiness"],
    "name": "Бумажная упаковка",
    "url": "https://packing.example.org/",
    "address": {
        "@type": "PostalAddress",
        "addressLocality": "Екатеринбург",
        "addressRegion": "Свердловская область",
        "streetAddress": "ул. Тестовая, 10",
    },
    "contactPoint": [
        {"contactType": "procurement", "email": "buy@packing.example.org"},
        {"contactType": "sales", "telephone": "7 (343) 123-45-67"},
    ],
}


def page(title: str, nodes: list[dict], body: str = "") -> HtmlResponse:
    markup = json.dumps({"@graph": nodes}, ensure_ascii=False)
    return HtmlResponse(
        URL,
        encoding="utf-8",
        body=(
            f'<html><title>{title}</title><script type="application/ld+json">{markup}</script>'
            f"<body><nav>Контейнеры для готовых блюд</nav>{body}</body></html>"
        ).encode(),
    )


def test_publishes_own_category_and_local_sales_details_with_evidence() -> None:
    item = parse_catalog_page(page("Коробки для пиццы оптом", [SELLER]))

    assert item is not None
    assert item["city"] == "Екатеринбург"
    assert item["address"] == "ул. Тестовая, 10"
    assert item["products"] == ["Коробки для пиццы"]
    assert item["phone"] == "+73431234567"
    assert item["email"] is None
    record = normalize_item({**item, "region_id": "ekaterinburg"}, date(2026, 1, 1))
    fields = {entry["field"]: entry["url"] for entry in record["field_evidence"]}
    assert fields["category_ids"] == fields["products"] == fields["region_id"] == URL


def faq(question: str, answer: str) -> dict:
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            }
        ],
    }


def test_minimum_order_keeps_context_and_field_evidence() -> None:
    response = page(
        "Коробки для пиццы оптом",
        [SELLER],
        '<section><div data-role="item.button">Какая минимальная партия для заказа?</div>'
        '<div data-role="item.content">Минимальный заказ на изготовление: '
        "<strong>750&nbsp;шт.</strong></div></section>",
    )

    item = parse_catalog_page(response)

    assert item is not None
    record = normalize_item({**item, "region_id": "ekaterinburg"}, date(2026, 1, 1))
    assert record["minimum_order"] == "Минимальный заказ на изготовление: 750 шт."
    assert {"field": "minimum_order", "url": URL} in record["field_evidence"]


def test_accepts_structured_faq_and_deduplicates_matching_html_answer() -> None:
    response = page(
        "Коробки для пиццы оптом",
        [SELLER, faq("Какой минимальный объём заказа?", "От <b>25 упаковок</b>.")],
        "<details><summary>Какой минимальный объём заказа?</summary>"
        "<p>От 25 упаковок.</p></details>",
    )

    item = parse_catalog_page(response)

    assert item is not None and item["minimum_order"] == "От 25 упаковок."


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        ("Минимальная сумма для бесплатной доставки?", "От 10 000 руб."),
        ("Минимальная партия?", "Бесплатно доставляем от 100 коробок."),
        ("Минимальный заказ?", "До 80 штук."),
        ("Минимальный заказ?", "Обычно в наличии 80 коробок."),
        ("Минимальный заказ?", "Зависит от модели, от 80 до 120 коробок."),
    ],
)
def test_does_not_infer_minimum_from_other_quantities(question, answer) -> None:
    item = parse_catalog_page(page("Коробки для пиццы оптом", [SELLER, faq(question, answer)]))

    assert item is not None and item["minimum_order"] is None


def test_does_not_publish_conflicting_minimums() -> None:
    response = page(
        "Коробки для пиццы оптом",
        [SELLER, faq("Какой минимальный заказ?", "От 75 коробок.")],
        "<dl><dt>Какой минимальный заказ?</dt><dd>От 100 коробок.</dd></dl>",
    )

    item = parse_catalog_page(response)

    assert item is not None and item["minimum_order"] is None


@pytest.mark.parametrize(
    ("title", "sellers"),
    [
        ("Коробки для пиццы", [SELLER]),
        ("Станки для производства коробок для пиццы оптом", [SELLER]),
        ("Коробки для пиццы оптом", [{**SELLER, "url": "https://other.example.org/"}]),
        ("Коробки для пиццы оптом", [{**SELLER, "address": "Доставка в Екатеринбург"}]),
        ("Коробки для пиццы оптом", [SELLER, {**SELLER, "name": "Другая компания"}]),
    ],
)
def test_needs_a_commercial_product_and_unambiguous_same_origin_seller(title, sellers) -> None:
    assert parse_catalog_page(page(title, sellers)) is None
