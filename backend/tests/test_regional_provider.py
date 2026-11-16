import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scrapy.http import HtmlResponse
from twisted.python.failure import Failure

from foodsource.ingest.regional import REGISTRY_URL, RegionalSpider

HEADER = """
<tr>
  <td>Наименование организации</td><td>Адрес</td><td>Контактный телефон</td>
  <td>Электронная почта</td><td>Официальный сайт</td><td>ИНН</td>
</tr>
"""


def _response(html: str, url: str = REGISTRY_URL, status: int = 200) -> HtmlResponse:
    return HtmlResponse(url=url, body=html.encode(), encoding="utf-8", status=status)


def _spider(**kwargs) -> RegionalSpider:
    spider = RegionalSpider(**kwargs)
    spider.crawler = SimpleNamespace(stats=Mock())
    return spider


def test_collects_only_local_food_producers_from_real_table_shape() -> None:
    response = _response(f"""
        <table><tr><td>Меню сайта</td></tr></table>
        <table><tbody>{HEADER}
        <tr><td colspan="7" rowspan="3">МОЛОЧНЫЕ КОМБИНАТЫ</td></tr>
        <tr></tr><tr></tr>
        <tr>
          <td>АО «Пример 1»</td><td>г.&nbsp;Екатеринбург, ул. Ленина, 1</td>
          <td><a href="tel:+73432223344">8 (343) 222-33-44</a></td>
          <td><a href="mailto:sales@example.org">Отдел продаж</a></td>
          <td><a href="https://example.org/">Сайт</a></td><td>6670 123 457</td>
        </tr>
        <tr><td>Строка адреса без colspan</td></tr>
        <tr>
          <td>АО «Пример 2»</td><td>город Екатеринбург, ул. Ленина, 2</td><td>—</td>
          <td>—</td><td>www.example.net</td><td>6670123458</td>
        </tr>
        <tr>
          <td>АО «Другая территория»</td><td>г. Серов, ул. Екатеринбургская, 3</td>
          <td></td><td></td><td></td><td></td>
        </tr>
        <tr><td colspan="7">БУМАГА И ИЗДЕЛИЯ ИЗ БУМАГИ</td></tr>
        <tr>
          <td>АО «Производитель бумаги»</td><td>Екатеринбург</td>
          <td></td><td></td><td></td><td></td>
        </tr>
        <tr><td colspan="7">ХЛЕБОКОМБИНАТЫ</td></tr>
        <tr>
          <td>АО «Пример 3»</td><td>Екатеринбург, ул. Ленина, 3</td><td></td>
          <td></td><td>https://example.org/</td><td>6670123464</td>
        </tr>
        </tbody></table>
    """)
    spider = _spider()
    first, second, third = rows = list(spider.parse(response))

    assert all(row["provider"] == "sverdlovsk-registry" for row in rows)
    assert all(row["region_id"] == "ekaterinburg" for row in rows)
    assert all(row["source_url"] == REGISTRY_URL for row in rows)
    assert all(row["category_ids"] == ["food"] for row in rows)
    assert [row["products"] for row in rows] == [
        ["Молочная продукция"],
        ["Молочная продукция"],
        ["Хлебобулочные изделия"],
    ]
    assert first["inn"] == "6670123457"
    assert first["external_id"] == "inn-6670123457"
    assert first["phone"] == "+73432223344"
    assert first["email"] == "sales@example.org"
    assert second["inn"] is None
    assert second["phone"] is None
    assert second["website"] == "https://www.example.net/"
    assert str(second["external_id"]).startswith("name-")
    assert second["external_id"] == list(spider.parse(response))[1]["external_id"]
    assert third["website"] == first["website"]
    assert third["external_id"] == "inn-6670123464"
    spider.crawler.stats.inc_value.assert_not_called()


@pytest.mark.parametrize(
    ("html", "url", "status"),
    [
        ("<html><title>Межсетевой экран</title></html>", REGISTRY_URL, 200),
        (
            f"<table>{HEADER}<tr><td colspan='7'>Неизвестная группа</td></tr></table>",
            REGISTRY_URL,
            200,
        ),
        (f"<table>{HEADER}</table>", "https://torgi.egov66.ru/site/Menu/Page/550", 200),
        ("<html></html>", REGISTRY_URL, 503),
    ],
)
def test_reports_source_failure_instead_of_silent_empty_result(
    html: str, url: str, status: int
) -> None:
    spider = _spider()

    assert list(spider.parse(_response(html, url, status))) == []
    spider.crawler.stats.inc_value.assert_called_once_with("foodsource/source_errors")


def test_valid_source_with_no_local_company_does_not_count_as_failure() -> None:
    spider = _spider()
    response = _response(f"""
        <table>{HEADER}<tr><td colspan="7">МЯСОКОМБИНАТЫ</td></tr>
        <tr><td>АО «Пример»</td><td>г. Серов</td><td></td><td></td><td></td><td></td></tr>
        </table>
    """)

    assert list(spider.parse(response)) == []
    spider.crawler.stats.inc_value.assert_not_called()


def test_uses_only_pilot_city_and_records_downloader_errors() -> None:
    async def request_list(spider: RegionalSpider):
        return [request async for request in spider.start()]

    supported = _spider(city="екатеринбург", region_id="ekaterinburg")
    [request] = asyncio.run(request_list(supported))
    assert request.url == REGISTRY_URL
    request.errback(Failure(TimeoutError("downloader timeout")))
    supported.crawler.stats.inc_value.assert_called_once_with("foodsource/source_errors")

    unsupported = _spider(city="Москва", region_id="moscow")
    assert asyncio.run(request_list(unsupported)) == []
    unsupported.crawler.stats.inc_value.assert_called_once_with("foodsource/source_errors")
