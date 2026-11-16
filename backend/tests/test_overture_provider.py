import asyncio
import sys
import warnings
from functools import partial
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from scrapy.http import HtmlResponse, Response

from foodsource.ingest import overture
from foodsource.ingest.enrich import OfficialWebsitePipeline, website_facts
from foodsource.ingest.geography import _build_geography


@pytest.fixture
def geography():
    def row(id_, label, latitude, longitude, admin):
        first = [str(id_), label, label, "", latitude, longitude, "P", "PPLA", "RU", "", admin]
        return "\t".join([*first, *([""] * 8)])

    return _build_geography(
        [
            row(1486209, "Yekaterinburg", "56.85", "60.61", "71"),
            row(1496747, "Novosibirsk", "55.01", "82.94", "53"),
        ],
        [
            "RU.71\tSverdlovsk Oblast\tSverdlovsk Oblast\t1490542",
            "RU.53\tNovosibirsk Oblast\tNovosibirsk Oblast\t1496745",
        ],
        [
            "1\t1486209\tru\tЕкатеринбург\t1\t\t\t",
            "2\t1496747\tru\tНовосибирск\t1\t\t\t",
        ],
    )


@pytest.fixture
def screen(geography):
    (scope,) = overture.city_scopes(geography, overture.DEFAULT_CITIES)
    return partial(overture.screen_overture_places, scope=scope, geography=geography)


def place(identifier, **changes):
    return {
        "id": identifier,
        "names": {"primary": "Оптовая компания"},
        "addresses": [{"locality": "г. Екатеринбург", "postcode": "620014"}],
        "websites": ["https://supplier.example/"],
        "taxonomy": {"primary": "restaurant_wholesale"},
        "basic_category": "wholesaler",
        "confidence": 0.94,
        "phones": ["+73431234567"],
        "emails": ["info@supplier.example"],
        **changes,
    }


def test_exact_locality_and_source_attribution_before_website_verification(screen):
    rows = [
        place("documented"),
        place(
            "encoded",
            addresses=[{"locality": "Yekaterinburg", "country": "RU"}],
            websites=["https://another.example/"],
        ),
        place("bbox-only", addresses=[{"freeform": "Екатеринбург", "postcode": "620014"}]),
        place("wrong-city", addresses=[{"locality": "Берёзовский"}]),
        place("wrong-country", addresses=[{"locality": "Екатеринбург", "country": "KZ"}]),
        place("conflicting", addresses=[{"locality": "Екатеринбург"}, {"locality": "Пермь"}]),
        place("closed", operating_status="permanently_closed"),
        place("no-site", websites=None),
        place("social-only", websites=["https://t.me/foodstore"]),
    ]

    matching = screen(rows)

    assert [item["external_id"] for item in matching] == ["documented", "encoded"]
    lead = matching[0]
    assert lead["provider"] == "overture-places"
    assert lead["source_url"] == "https://docs.overturemaps.org/guides/places/"
    assert lead["source_label"] == "Overture Maps Places"
    assert lead["region_id"] == "ekaterinburg"
    assert lead["category_ids"] == []
    assert lead["products"] == []
    assert lead["email"] is lead["phone"] is None


def test_narrow_name_or_taxonomy_and_deterministic_branch_resolution(screen):
    rows = [
        place("alternate", taxonomy={"alternates": ["wholesale_grocer"]}),
        place("lower-confidence", confidence=0.3),
        place(
            "direct-packaging",
            names={"primary": "Бумажная упаковка"},
            taxonomy=None,
            basic_category="shopping",
        ),
        place("retail-restaurant", taxonomy={"primary": "restaurant"}, basic_category="restaurant"),
        place(
            "gift-wrap",
            names={"primary": "Упаковка подарков"},
            taxonomy={"primary": "packing_supply_store"},
            basic_category="office_supply_store",
        ),
    ]

    expected = ["alternate", "direct-packaging"]
    assert [entry["external_id"] for entry in screen(rows)] == expected
    assert [entry["external_id"] for entry in screen(reversed(rows))] == expected


def test_bounds_number_of_candidate_websites_and_refuses_oversized_input(monkeypatch, screen):
    monkeypatch.setattr(overture, "MAX_CANDIDATES", 2)
    monkeypatch.setattr(overture, "MAX_PLACES_SCANNED", 3)
    rows = [
        place(identifier, websites=[f"https://{identifier}.example/"])
        for identifier in ("third", "second", "first")
    ]

    assert [entry["external_id"] for entry in screen(rows)] == [
        "first",
        "second",
    ]
    with pytest.raises(RuntimeError, match="bounded city import"):
        screen([*rows, place("extra")])


def test_spider_reports_wire_failure(monkeypatch, geography):
    def fail(*_):
        raise OSError("test downloader failed")

    async def collect(spider):
        return [item async for item in spider.start()]

    monkeypatch.setattr(overture, "discover_overture", fail)
    spider = overture.OvertureSpider(geography=geography)
    spider.crawler = SimpleNamespace(stats=Mock())
    assert asyncio.run(collect(spider)) == []
    spider.crawler.stats.inc_value.assert_called_once_with("foodsource/source_errors")


def test_additional_geo_names_scope_keeps_only_explicit_local_businesses(geography):
    (scope,) = overture.city_scopes(geography, ["Новосибирск", "Novosibirsk"])
    assert scope.id == "city-1496747"
    xmin, ymin, xmax, ymax = scope.bbox
    assert xmin < 82.94 < xmax and ymin < 55.01 < ymax
    rows = [
        place("other"),
        place(
            "record", addresses=[{"locality": "Novosibirsk"}], bbox={"xmin": 82.94, "ymin": 55.01}
        ),
    ]
    (lead,) = overture.screen_overture_places(rows, scope, geography)
    assert (lead["city"], lead["region_id"]) == ("Новосибирск", "city-1496747")
    assert lead["source_url"].endswith("feature=places.place.record#18/55.010000/82.940000")
    with pytest.raises(ValueError, match="ambiguous Overture city"):
        overture.city_scopes(geography, ["Новосиб"])


def test_official_site_requires_published_own_wholesale_food_offer():
    text = """
        <html><body>
          <nav><h2>Морепродукты оптом</h2></nav>
          <h1>Надёжный поставщик хлеба и кондитерских изделий</h1>
          <h2>Производитель стаканов для молока</h2>
          <p>Не поставляем сыр оптом.</p>
          <p>В нашем магазине есть рыба.</p>
          <div><span>Справочная +7 900 111 22 33</span><div><span>Приём заявок:</span></div>
            <div>8 (3522) 123-456</div><div>+7 (912) 345 67 89</div>
          </div>
        </body></html>
    """
    site = HtmlResponse("https://supplier.example/", body=text.encode(), encoding="utf-8")
    facts = website_facts(site)
    assert facts["groups"] == {"food": {"Хлеб", "Кондитерские изделия"}}
    assert facts["phone"] == "+73522123456"


def test_source_robots_rules_follow_host_and_scheme_redirects():
    initial = "http://www.supplier.example/robots.txt"
    canonical_host = "http://supplier.example/robots.txt"
    https = "https://supplier.example/robots.txt"
    pipeline = OfficialWebsitePipeline(SimpleNamespace(settings={"USER_AGENT": "FoodSourceTest"}))
    pipeline.download_from_origin = AsyncMock(
        side_effect=[
            Response(initial, status=301, headers={"Location": canonical_host}),
            Response(canonical_host, status=301, headers={"Location": https}),
            Response(https, body=b"User-agent: *\nDisallow: /private"),
        ]
    )
    permitted = asyncio.run(
        pipeline.robots_allow("http://www.supplier.example/private", "supplier.example")
    )
    assert not permitted
    assert [call.args[0] for call in pipeline.download_from_origin.await_args_list] == [
        initial,
        canonical_host,
        https,
    ]


def test_broken_stac_index_stops_before_sdk_falls_back_to_global_scan(monkeypatch, geography):
    class StacIndexWarning(UserWarning):
        pass

    def missing_index(*args, **kwargs):
        warnings.warn("falling back to every file", StacIndexWarning, stacklevel=2)
        pytest.fail("The bounded reader must abort before the global scan")

    SDK = ModuleType("overturemaps.core")
    SDK.StacIndexWarning = StacIndexWarning
    SDK.record_batch_reader = missing_index
    monkeypatch.setitem(sys.modules, "overturemaps.core", SDK)

    with pytest.raises(RuntimeError, match="STAC index is unavailable"):
        (scope,) = overture.city_scopes(geography, overture.DEFAULT_CITIES)
        list(overture._download_places(scope, "2026-10-21.0"))
