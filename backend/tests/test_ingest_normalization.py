from datetime import date

import pytest
from scrapy.http import HtmlResponse

from foodsource.domain import CatalogSnapshot
from foodsource.ingest.enrich import website_facts
from foodsource.ingest.normalize import company_url, normalized_snapshot, official_domain, valid_inn
from foodsource.ingest.qualification import qualifying_products
from foodsource.ingest.refresh import preserve_temporarily_unavailable


@pytest.fixture
def item() -> dict:
    return {
        "provider": "mvk",
        "external_id": "foodtech:123",
        "name": " ООО  Поставщик ",
        "region_id": "ekaterinburg",
        "category_ids": ["packaging", "packaging"],
        "products": ["Стаканы", "Стаканы"],
        "website": "www.example.org",
        "inn": "6603025045",
        "email": "sales@example.org",
        "phone": "8 (343) 123-45-67",
        "source_url": "https://example.com/exhibitors#123",
        "source_label": "Каталог участников",
    }


def test_normalizes_contact_identity_and_preserves_clickable_source(item: dict) -> None:
    day = date(2026, 11, 16)
    snapshot = CatalogSnapshot.model_validate(normalized_snapshot([item, item], day))
    (supplier,) = snapshot.suppliers

    assert supplier.name == "ООО Поставщик"
    assert supplier.official_domain == "example.org"
    assert supplier.inn == "6603025045"
    assert supplier.contacts.phone == "+73431234567"
    assert supplier.products == ("Стаканы",)
    assert str(supplier.sources[0].url) == "https://example.com/exhibitors#123"
    assert supplier.observations[0].observed_at.date() == day
    assert len(snapshot.categories) == 4


def test_conflicting_source_rows_fail_before_publish(item: dict) -> None:
    with pytest.raises(ValueError, match="Conflicting records for source"):
        normalized_snapshot([item, {**item, "name": "Другая компания"}], date(2026, 11, 16))


def test_archive_can_supply_its_own_observation_date_and_registry_link(item: dict) -> None:
    archive_day = date(2026, 8, 31)
    import_day = date(2026, 11, 16)
    item.update(
        observed_at=archive_day,
        source_links=[{"label": "Паспорт", "url": "https://example.org/data/"}],
    )

    snapshot = CatalogSnapshot.model_validate(normalized_snapshot([item], import_day))
    (supplier,) = snapshot.suppliers

    assert snapshot.updated_at == import_day
    assert supplier.observations[0].observed_at.date() == archive_day
    assert [source.label for source in supplier.sources] == ["Каталог участников", "Паспорт"]


def test_only_the_pages_that_state_a_fact_are_its_evidence(item: dict) -> None:
    item.update(
        enrichment_url="https://example.org/catalog",
        enriched_fields=["phone", "products"],
        new_fields=["phone"],
    )
    snapshot = CatalogSnapshot.model_validate(normalized_snapshot([item], date(2026, 11, 16)))
    evidence = {(row.field, str(row.url)) for row in snapshot.suppliers[0].field_evidence}

    assert ("phone", "https://example.com/exhibitors#123") not in evidence
    assert ("phone", "https://example.org/catalog") in evidence
    assert ("products", "https://example.com/exhibitors#123") in evidence
    assert ("products", "https://example.org/catalog") in evidence
    assert {
        (str(observation.url), observation.observed_at.date())
        for observation in snapshot.suppliers[0].observations
    } == {
        ("https://example.com/exhibitors#123", date(2026, 11, 16)),
        ("https://example.org/catalog", date(2026, 11, 16)),
    }


def test_invalid_optional_website_cannot_invalidate_the_catalog(item: dict) -> None:
    item["website"] = "https://example.org:notaport"
    snapshot = CatalogSnapshot.model_validate(normalized_snapshot([item], date(2026, 11, 16)))
    assert snapshot.suppliers[0].website is None
    assert company_url("https://example.org:99999") is None


def test_shared_domains_are_not_official_with_a_trailing_dns_dot() -> None:
    assert official_domain("https://www.vk.com./company") is None


def test_identity_excludes_shared_domains_and_invalid_tax_ids() -> None:
    assert official_domain("https://vk.com/company") is None
    assert valid_inn("6603025046") is None
    assert valid_inn("0000000000") is None


def test_only_published_products_and_qualifying_business_terms_are_used() -> None:
    response = HtmlResponse(
        "https://example.org/",
        body="""
<html lang="ru"><body>
  <h1>Производство пищевой упаковки</h1><a href="tel:+73431234567">Связаться</a>
  <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"Коробка для пиццы 30 см"}
  </script>
</body></html>""".encode(),
        encoding="utf-8",
    )
    assert website_facts(response) == {
        "phone": "+73431234567",
        "email": None,
        "products": ["Коробка для пиццы 30 см"],
        "groups": {"packaging": {"Пищевая упаковка", "Коробки для пиццы"}},
    }
    assert qualifying_products("Магазин подарочной упаковки") == {}


def test_keeps_verified_website_facts_only_during_an_outage(item: dict) -> None:
    previous = CatalogSnapshot.model_validate(
        normalized_snapshot(
            [
                {
                    **item,
                    "enrichment_url": "https://example.org/packaging",
                    "enriched_fields": ["category_ids", "products", "phone"],
                    "new_fields": ["category_ids", "products", "phone"],
                },
            ],
            date(2026, 11, 16),
        )
    )
    lead = {**item, "category_ids": [], "products": [], "phone": None}
    transient = {**lead, "website_unavailable": True}
    selected, retained = preserve_temporarily_unavailable([transient], previous)

    assert retained == 1
    assert selected[0]["category_ids"] == ["packaging"]
    assert selected[0]["products"] == ["Стаканы"]
    assert selected[0]["phone"] == "+73431234567"
    refreshed = CatalogSnapshot.model_validate(
        normalized_snapshot(selected, date(2026, 11, 17))
    ).suppliers[0]
    assert {str(obs.url): obs.observed_at.date() for obs in refreshed.observations} == {
        "https://example.com/exhibitors#123": date(2026, 11, 17),
        "https://example.org/packaging": date(2026, 11, 16),
    }
    assert preserve_temporarily_unavailable([lead], previous) == ([], 0)
    changed_site = {**transient, "website": "https://new.example.net"}
    assert preserve_temporarily_unavailable([changed_site], previous) == ([], 0)


def test_keeps_website_phone_when_only_the_contact_was_enriched(item: dict) -> None:
    evidence_url = "https://example.org/contacts"
    previous = CatalogSnapshot.model_validate(
        normalized_snapshot(
            [
                {
                    **item,
                    "enrichment_url": evidence_url,
                    "enriched_fields": ["phone"],
                    "new_fields": ["phone"],
                }
            ],
            date(2026, 11, 16),
        )
    )
    incoming = {**item, "phone": None, "website_unavailable": True}
    selected, retained = preserve_temporarily_unavailable([incoming], previous)

    assert retained == 1
    assert selected[0]["phone"] == "+73431234567"
    assert selected[0]["enriched_fields"] == ["phone"]
    assert selected[0]["enrichment_url"] == evidence_url
    assert selected[0]["products"] == incoming["products"]
