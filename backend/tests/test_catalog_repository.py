import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from foodsource.api import create_app
from foodsource.database import SCHEMA_VERSION
from foodsource.domain import CatalogSnapshot, SupplierRecord
from foodsource.repository import (
    CatalogNotPublished,
    CatalogRevisionConflict,
    import_catalog,
    load_catalog_revision,
    load_sqlite_catalog,
)


def supplier(
    id_: str,
    name: str,
    *,
    city: str = "ekaterinburg",
    domain: str | None = None,
    inn: str | None = None,
    category: str = "ingredients",
) -> SupplierRecord:
    url = f"https://example.com/entries/{id_}"
    return SupplierRecord.model_validate(
        {
            "id": id_,
            "name": name,
            "description": "Поставщик для предприятий пищевой промышленности.",
            "region_id": city,
            "category_ids": [category],
            "products": ["Мука" if category == "ingredients" else "Тара"],
            "website": f"https://{domain}/" if domain else None,
            "official_domain": domain,
            "inn": inn,
            "sources": [{"label": "Отраслевая выставка", "url": url}],
            "observations": [
                {
                    "provider": "expo",
                    "external_id": id_,
                    "url": url,
                    "observed_at": "2026-11-16T12:00:00+03:00",
                }
            ],
            "field_evidence": [{"field": "products", "url": url}],
        }
    )


def snapshot(*records: SupplierRecord, day: int = 16) -> CatalogSnapshot:
    return CatalogSnapshot.model_validate(
        {
            "schema_version": 1,
            "updated_at": date(2026, 11, day),
            "categories": [
                {"id": "ingredients", "label": "Ингредиенты"},
                {"id": "packaging", "label": "Упаковка"},
            ],
            "regions": [
                {"id": "ekaterinburg", "label": "Екатеринбург", "parent_id": "sverdlovsk-region"},
                {"id": "berezovsky", "label": "Берёзовский", "parent_id": "sverdlovsk-region"},
                {"id": "sverdlovsk-region", "label": "Свердловская область"},
            ],
            "suppliers": records,
        }
    )


def test_import_merges_verified_identity_and_retains_provenance(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    original = supplier("supplier-a", "Ромашка", inn="6671153584", domain="www.romashka.ru")
    additional = supplier("supplier-b", "Ромашка Трейд", inn="6671153584", category="packaging")

    first = import_catalog(snapshot(original, additional), path)
    retry = import_catalog(snapshot(additional, original), path)

    assert (first.supplier_count, first.observation_count, first.revision, first.changed) == (
        1,
        2,
        1,
        True,
    )
    assert (retry.revision, retry.changed) == (1, False)
    (merged,) = load_sqlite_catalog(path).suppliers
    assert merged.id == "supplier-a"
    assert merged.inn == "6671153584"
    assert merged.official_domain == "romashka.ru"
    assert set(merged.category_ids) == {"ingredients", "packaging"}
    assert set(merged.products) == {"Мука", "Тара"}
    assert {item.external_id for item in merged.observations} == {"supplier-a", "supplier-b"}
    assert len(merged.field_evidence) == len(merged.sources) == 2


def test_import_matches_verified_site_and_exact_normalized_name_by_city(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    result = import_catalog(
        snapshot(
            supplier("domain-a", "Локал", domain="local.ru"),
            supplier("domain-b", "Local", domain="www.local.ru"),
            supplier("name-a", "Тёплый дом"),
            supplier("name-b", "«Теплый дом»", category="packaging"),
            supplier("other-city", "Тёплый дом", city="berezovsky"),
        ),
        path,
    )
    assert result.supplier_count == 3
    assert {item.id for item in load_sqlite_catalog(path).suppliers} == {
        "domain-a",
        "name-a",
        "other-city",
    }


def test_import_preserves_conflicting_inns_and_same_name_at_distinct_sites(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    result = import_catalog(
        snapshot(
            supplier("inn-a", "Смак", domain="shared.ru", inn="6671153584"),
            supplier("inn-b", "Смак", domain="shared.ru", inn="6671093708"),
            supplier("inn-uncertain", "Смак", domain="shared.ru"),
            supplier("name-a", "Заря", domain="zarya1.ru"),
            supplier("name-b", "Заря", domain="zarya2.ru"),
        ),
        path,
    )
    assert result.supplier_count == 5


def test_import_normalizes_legal_form_order_without_merging_distinct_entities(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.sqlite3"
    result = import_catalog(
        snapshot(
            supplier(
                "registry", "Общество с ограниченной ответственностью «Вкус»", inn="6671153584"
            ),
            supplier("exhibition", "ВКУС, ООО"),
            supplier("trade-name", "Вкус продуктов"),
            supplier("legal-name", "ООО «Вкус продуктов»", inn="6671093708"),
            supplier("ambiguous-alias", "Вкус"),
            supplier("other-form", "АО «Вкус»"),
            supplier("other-city", "ВКУС, ООО", city="berezovsky"),
        ),
        path,
    )
    assert result.supplier_count == 5
    records = {row.inn: row for row in load_sqlite_catalog(path).suppliers if row.inn}
    assert {observation.external_id for observation in records["6671153584"].observations} == {
        "registry",
        "exhibition",
    }
    assert {observation.external_id for observation in records["6671093708"].observations} == {
        "trade-name",
        "legal-name",
    }


def test_import_rolls_back_failed_write_to_previous_publication(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    original = supplier("old", "Старый поставщик")
    import_catalog(snapshot(original), path)

    with sqlite3.connect(path) as db:
        db.execute(
            """
            CREATE TRIGGER stop_write BEFORE INSERT ON supplier
            WHEN NEW.id = 'fail'
            BEGIN
                SELECT RAISE(ABORT, 'simulated failed write');
            END
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="simulated failed write"):
        import_catalog(snapshot(supplier("fail", "Новый поставщик"), day=17), path)

    published = load_sqlite_catalog(path)
    assert published.updated_at.day == 16
    assert [item.id for item in published.suppliers] == ["old"]
    recovered = import_catalog(snapshot(supplier("next", "Новый поставщик"), day=17), path)
    assert recovered.revision == 2


def test_concurrent_identical_imports_publish_one_revision(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    staged = snapshot(supplier("one", "Поставщик"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: import_catalog(staged, path), range(4)))
    assert {result.revision for result in results} == {1}
    assert sum(result.changed for result in results) == 1


def test_import_refuses_stale_writes_but_accepts_identical_retries(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    original = snapshot(supplier("original", "Исходный поставщик"))
    fresh = snapshot(supplier("fresh", "Новый поставщик"))
    stale = snapshot(supplier("stale", "Устаревший снимок"))
    first = import_catalog(original, path, expected_revision=0)
    second = import_catalog(fresh, path, expected_revision=first.revision)
    assert second.revision == first.revision + 1

    with pytest.raises(CatalogRevisionConflict, match="Run the import again") as conflict:
        import_catalog(stale, path, expected_revision=first.revision)
    assert (conflict.value.expected, conflict.value.actual) == (first.revision, second.revision)
    assert [item.id for item in load_sqlite_catalog(path).suppliers] == ["fresh"]

    retry = import_catalog(fresh, path, expected_revision=first.revision)
    assert not retry.changed
    assert retry.revision == second.revision


def test_empty_database_is_an_unpublished_base_revision(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    with closing(sqlite3.connect(path)):
        pass
    with pytest.raises(CatalogNotPublished):
        load_catalog_revision(path)

    published = import_catalog(snapshot(supplier("first", "Первый")), path, expected_revision=0)
    assert published.revision == 1
    with pytest.raises(CatalogRevisionConflict):
        import_catalog(snapshot(supplier("late", "Устаревший сбор")), path, expected_revision=0)
    assert load_sqlite_catalog(path).suppliers[0].id == "first"


def test_import_keeps_id_when_one_exact_observation_survives(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    first = supplier("a", "Ромашка", inn="6672357965")
    survivor = supplier("b", "Домашняя ромашка", inn="6672357965")
    initial = import_catalog(snapshot(first, survivor), path)
    staged = snapshot(survivor)
    updated = import_catalog(staged, path, expected_revision=initial.revision)

    (published,) = load_sqlite_catalog(path).suppliers
    assert published.id == "a"
    assert published.observations == survivor.observations
    assert not import_catalog(staged, path, expected_revision=updated.revision).changed


def test_identity_split_does_not_reassign_previous_id_to_one_branch(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    first = supplier("a", "Ромашка", inn="6672357965")
    second = supplier("b", "Ромашка", inn="6672357965")
    third = supplier("c", "Ромашка", inn="6672357965")
    import_catalog(snapshot(first, second, third), path)
    second_company = third.model_copy(update={"inn": "6671093708"})
    import_catalog(snapshot(second, second_company), path)
    assert {record.id for record in load_sqlite_catalog(path).suppliers} == {"b", "c"}


def test_ambiguous_historical_merge_uses_provisional_id(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    first = supplier("b", "Ромашка", inn="6672357965")
    second = supplier("c", "Василёк", inn="6671093708")
    import_catalog(snapshot(first, second), path)
    third = supplier("a", "Ромашка и Василёк", inn="6672357965")
    import_catalog(snapshot(first, second.model_copy(update={"inn": first.inn}), third), path)
    assert [record.id for record in load_sqlite_catalog(path).suppliers] == ["a"]


def test_api_reads_new_sqlite_revision_and_hides_internal_metadata(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    original = supplier("old", "Ромашка", inn="6671153584", domain="romashka.ru")
    import_catalog(snapshot(original), path)
    with TestClient(create_app(db_path=path)) as client:
        assert client.get("/api/catalog").json()["updated_at"] == "2026-11-16"
        (item,) = client.get("/api/suppliers").json()["items"]
        assert item["id"] == "old"
        assert item["sources"][0]["url"] == "https://example.com/entries/old"
        assert not {"inn", "official_domain", "observations", "field_evidence"} & item.keys()

        import_catalog(snapshot(supplier("new", "Новый поставщик"), day=17), path)
        assert client.get("/api/catalog").json()["updated_at"] == "2026-11-17"
        assert client.get("/api/suppliers").json()["items"][0]["id"] == "new"


def test_api_shows_evidence_for_selected_merged_values(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    records = []
    for id_, phone, price in [("a", "+73431234567", "150 ₽"), ("b", "+73439876543", "200 ₽")]:
        record = supplier(id_, "Поставщик", domain="company.example.org").model_dump()
        url = str(record["sources"][0]["url"])
        record.update(
            contacts={"phone": phone, "email": "sales@example.org"},
            price=price,
            field_evidence=[{"field": field, "url": url} for field in ("phone", "email", "price")],
        )
        records.append(SupplierRecord.model_validate(record))
    import_catalog(snapshot(*records), path)

    with TestClient(create_app(db_path=path)) as client:
        (record,) = client.get("/api/suppliers").json()["items"]
    assert record["contacts"]["phone"] == "+73431234567"
    assert record["price"] == "150 ₽"
    assert {entry["url"].rsplit("/", 1)[-1] for entry in record["sources"]} == {"a", "b"}
    assert {(entry["field"], entry["url"].rsplit("/", 1)[-1]) for entry in record["evidence"]} == {
        ("phone", "a"),
        ("price", "a"),
        ("email", "a"),
        ("email", "b"),
    }


def test_sqlite_startup_fails_without_published_database(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError, match="Run the supplier import first"):
        with TestClient(create_app(db_path=path)):
            pass
    assert not path.exists()


def test_migrates_existing_products_without_publishing_new_revision(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    original = supplier("one", "Ромашка").model_copy(update={"products": ("Тесто", "Мука")})
    staged = snapshot(original, supplier("two", "Пшеница").model_copy(update={"products": ()}))
    first = import_catalog(staged, path)

    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "UPDATE supplier SET profile_json=json_set(profile_json, '$.products', json(?)) WHERE id=?",
            ('["Тесто","Мука"]', "one"),
        )
        db.execute(
            "UPDATE supplier SET profile_json=json_set(profile_json, '$.products', json('[]')) WHERE id=?",
            ("two",),
        )
        db.execute("DROP TABLE supplier_product")
        db.execute("PRAGMA user_version = 1")

    legacy = load_sqlite_catalog(path)
    assert legacy.suppliers[0].products == ("Тесто", "Мука")
    assert legacy.suppliers[1].products == ()

    retry = import_catalog(staged, path)
    assert retry.revision == first.revision
    assert not retry.changed
    assert load_sqlite_catalog(path) == legacy

    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute(
            "SELECT supplier_id, position, name FROM supplier_product ORDER BY position"
        ).fetchall() == [("one", 0, "Тесто"), ("one", 1, "Мука")]
        assert (
            db.execute(
                "SELECT COUNT(*) FROM supplier WHERE json_type(profile_json, '$.products') IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
