from scrapy.http import HtmlResponse

from foodsource.ingest.regional_catalogs import primorye_item, siberia_category, siberia_items


def html(url: str, body: str) -> HtmlResponse:
    return HtmlResponse(url, body=body.encode(), encoding="utf-8")


def test_siberian_listing_keeps_company_and_its_product_section() -> None:
    response = html(
        "https://prod-week.ru/exponents-2026",
        """
        <table class="dataTable"><tbody>
        <tr><td data-search="Сибирский завод"></td><td data-search="Томск"></td>
        <td data-search="A12"></td><td data-search="Производство молочных продуктов"></td>
        <td><a href="https://example.org">Сайт</a></td></tr>
        <tr><td data-search="Коллективный стенд"></td><td data-search="Томск"></td>
        <td></td><td data-search="Продукты питания"></td><td></td></tr>
        </tbody></table>
        """,
    )

    (entry,) = tuple(siberia_items(response))
    assert entry["category_ids"] == ["food"]
    assert entry["products"] == ["Производство молочных продуктов"]
    assert entry["city"] == "Томск"
    assert entry["source_url"].startswith("https://prod-week.ru/exponents-2026#:~:text=")
    assert siberia_category("Оборудование для переработки мяса") is None
    assert (
        siberia_category("Изготовление пластиковой упаковки для пищевой промышленности")
        == "packaging"
    )


def test_primorye_card_keeps_food_goods_and_company_contact() -> None:
    response = html(
        "https://primexport.ru/ru/exporters/fish-company",
        """
        <h3><b>ООО «Рыбный завод»</b></h3>
        <h3><b>Перечень товаров/услуг с кодами ТН ВЭД:</b></h3>
        <div><p>0303 – Рыба мороженая</p><p>0309: рыбная мука</p><p>4407 – Древесина</p></div>
        <p><span class="font-medium">Юридический и фактический адрес:</span>
            Приморский край, Владивосток, улица Главная, 10</p>
        <p><span class="font-medium">Контакты:</span> +7 (423) 100-20-30 sale@example.org</p>
        <p><span class="font-medium">Сайт:</span><a href="example.org">example.org</a></p>
        """,
    )

    entry = primorye_item(response)
    assert entry is not None
    assert entry["category_ids"] == ["frozen"]
    assert entry["products"] == ["Рыба мороженая"]
    assert entry["email"] == "sale@example.org"
    assert entry["phone"] == "+74231002030"
    assert entry["address"] == "Приморский край, Владивосток, улица Главная, 10"
