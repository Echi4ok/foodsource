import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from foodsource.api import create_app
from foodsource.domain import CatalogSnapshot
from foodsource.repository import import_catalog


@pytest.fixture
def snapshot() -> dict[str, Any]:
    source = {"label": "Каталог", "url": "https://example.com/catalog"}
    return {
        "schema_version": 1,
        "updated_at": "2026-10-01",
        "categories": [
            {"id": "ingredients", "label": "Ингредиенты"},
            {"id": "packaging", "label": "Упаковка"},
        ],
        "regions": [
            {"id": "ru", "label": "Россия"},
            {"id": "moscow-region", "label": "Московская область", "parent_id": "ru"},
            {"id": "kolomna", "label": "Коломна", "parent_id": "moscow-region"},
            {"id": "tatarstan", "label": "Татарстан", "parent_id": "ru"},
            {"id": "kazan", "label": "Казань", "parent_id": "tatarstan"},
        ],
        "suppliers": [
            {
                "id": "local",
                "name": "Липовая пасека",
                "description": "Поставка мёда для пищевых производств.",
                "products": ["Липовый мёд", "Гречишный мёд"],
                "category_ids": ["ingredients"],
                "region_id": "kolomna",
                "service_region_ids": ["kolomna"],
                "contacts": {"email": "sales@example.com"},
                "keywords": ["медосбор"],
                "sources": [source],
            },
            {
                "id": "remote",
                "name": "Альфа",
                "description": "Оптовый поставщик продуктов.",
                "products": ["Липовый мёд"],
                "category_ids": ["ingredients"],
                "region_id": "kazan",
                "service_region_ids": ["ru"],
                "sources": [source],
            },
            {
                "id": "containers",
                "name": "Завод тары",
                "description": "Тара для пищевой промышленности. Также продаём мёд.",
                "products": ["Пищевые банки", "Бумажные стаканы"],
                "category_ids": ["packaging"],
                "region_id": "tatarstan",
                "sources": [source],
            },
            {
                "id": "city-delivery",
                "name": "Бумажная фабрика",
                "description": "Производитель бумажной упаковки.",
                "products": ["Крафт-пакеты"],
                "category_ids": ["packaging"],
                "region_id": "kazan",
                "service_region_ids": ["kolomna"],
                "sources": [source],
            },
        ],
    }


@pytest.fixture
def catalog_path(snapshot: dict[str, Any], tmp_path: Path) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def client(catalog_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(catalog_path)) as http:
        yield http


def test_health_and_catalog_metadata(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}
    metadata = client.get("/api/catalog").json()
    assert metadata["supplier_count"] == 4
    assert metadata["catalog_revision"] == 1
    assert metadata["updated_at"] == "2026-10-01"
    assert metadata["regions"][0] == {"id": "ru", "label": "Россия", "parent_id": None}


def test_initial_results_have_stable_order_and_explicit_unknowns(client: TestClient) -> None:
    payload = client.get("/api/suppliers", params={"category": "", "region": "", "q": "  "}).json()
    assert payload["total"] == 4
    assert [item["id"] for item in payload["items"]] == [
        "local",
        "remote",
        "city-delivery",
        "containers",
    ]
    local, remote, *_ = payload["items"]
    assert remote["contacts"] == {"email": None, "phone": None}
    assert remote["minimum_order"] is None
    assert remote["reasons"] == []
    assert "keywords" not in local
    assert local["sources"][0]["url"] == "https://example.com/catalog"


def test_search_paginates_the_stable_order_and_retains_match_count(client: TestClient) -> None:
    result = client.get("/api/suppliers", params={"limit": 2, "offset": 1}).json()
    assert result["total"] == 4
    assert result["next_offset"] == 3
    assert [item["id"] for item in result["items"]] == ["remote", "city-delivery"]
    assert client.get("/api/suppliers", params={"offset": 4}).json() == {
        "catalog_revision": 1,
        "items": [],
        "total": 4,
        "next_offset": None,
    }
    assert client.get("/api/suppliers", params={"limit": 101}).status_code == 422


def test_shortlist_reloads_records_in_selection_order_and_ignores_missing_ids(
    client: TestClient,
) -> None:
    response = client.get("/api/suppliers/shortlist?id=local&id=missing&id=containers")
    result = response.json()
    assert result["catalog_revision"] == 1
    assert result["total"] == 2
    assert [item["id"] for item in result["items"]] == ["local", "containers"]
    assert all(item["reasons"] == [] for item in result["items"])
    assert client.get("/api/suppliers/shortlist?id=local&id=local").json()["total"] == 1
    assert client.get("/api/suppliers/shortlist?id=1&id=2&id=3&id=4").status_code == 422
    assert client.get("/api/suppliers/shortlist?id=%20").status_code == 422


def test_deduplicates_verified_company_after_geography_and_before_pagination(
    snapshot: dict[str, Any], tmp_path: Path
) -> None:
    local, remote, *_ = snapshot["suppliers"]
    local["inn"] = remote["inn"] = "6671153584"
    remote["products"].append("Рапсовый мёд")
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    with TestClient(create_app(path)) as http:
        assert http.get("/api/catalog").json()["supplier_count"] == 3
        first = http.get("/api/suppliers", params={"limit": 2}).json()
        second = http.get("/api/suppliers", params={"offset": first["next_offset"]}).json()
        items = first["items"] + second["items"]
        assert first["total"] == len({item["company_id"] for item in items}) == 3

        params = {"category": "ingredients", "q": "мёд"}
        kolomna = http.get("/api/suppliers", params={**params, "region": "kolomna"}).json()
        kazan = http.get("/api/suppliers", params={**params, "region": "kazan"}).json()
        assert [item["id"] for item in kolomna["items"]] == ["local"]
        assert [item["id"] for item in kazan["items"]] == ["remote"]
        assert kolomna["items"][0]["company_id"] == kazan["items"][0]["company_id"]

        specialized = http.get("/api/suppliers", params={"q": "рапсовый мёд"}).json()
        assert [item["id"] for item in specialized["items"]] == ["remote"]
        shortlist = http.get("/api/suppliers/shortlist?id=remote&id=local").json()
        assert [item["id"] for item in shortlist["items"]] == ["remote"]


def test_exposes_public_evidence_without_inventing_dates(
    snapshot: dict[str, Any], tmp_path: Path
) -> None:
    source = str(snapshot["suppliers"][0]["sources"][0]["url"])
    other = "https://example.com/delivery"
    snapshot["suppliers"][0]["observations"] = [
        {"provider": "directory", "external_id": "1", "url": source, "observed_at": "2026-09-30"}
    ]
    snapshot["suppliers"][0]["field_evidence"] = [
        {"field": "products", "url": source},
        {"field": "delivery", "url": other},
        {"field": "inn", "url": source},
    ]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    with TestClient(create_app(path)) as client:
        items = client.get("/api/suppliers/shortlist?id=local").json()["items"]
        assert items[0]["evidence"] == [
            {"field": "products", "url": source, "observed_at": "2026-09-30"},
            {"field": "delivery", "url": other, "observed_at": None},
        ]
        assert "observations" not in items[0]
        assert "inn" not in items[0]


def test_search_pages_report_the_version_of_the_data_they_return(
    snapshot: dict[str, Any], tmp_path: Path
) -> None:
    path = tmp_path / "catalog.sqlite3"
    import_catalog(CatalogSnapshot.model_validate(snapshot), path)

    with TestClient(create_app(db_path=path)) as live:
        first = live.get("/api/suppliers", params={"limit": 1}).json()
        assert first["catalog_revision"] == 1

        snapshot["suppliers"].pop()
        import_catalog(CatalogSnapshot.model_validate(snapshot), path)
        second = live.get("/api/suppliers", params={"limit": 1, "offset": 1}).json()
        assert second["catalog_revision"] == 2
        assert second["total"] == 3
        assert live.get("/api/catalog").json()["catalog_revision"] == second["catalog_revision"]


def test_case_accents_and_word_forms_match_and_explain_geography(client: TestClient) -> None:
    response = client.get(
        "/api/suppliers",
        params={"category": "ingredients", "region": "kolomna", "q": "ЛИПОВОГО МЕДА"},
    )
    assert response.status_code == 200
    local, remote = response.json()["items"]
    assert [local["id"], remote["id"]] == ["local", "remote"]
    assert {reason["code"] for reason in local["reasons"]} == {"query", "category", "location"}
    assert remote["reasons"][-1] == {"code": "delivery", "label": "Зона доставки · Россия"}


def test_searches_genitive_plural_and_ignores_prepositions(client: TestClient) -> None:
    items = client.get("/api/suppliers", params={"q": "для бумажных стаканов"}).json()["items"]
    assert [item["id"] for item in items] == ["containers"]


@pytest.mark.parametrize(
    "query", ["пищевые контейнеры", "контейнерами для еды", "Контейнеры для пищевых продуктов"]
)
def test_search_resolves_product_phrases_within_the_correct_category(
    snapshot: dict[str, Any], tmp_path: Path, query: str
) -> None:
    target = snapshot["suppliers"][2]
    target["products"] = ["Контейнеры для пищевых продуктов"]
    target["category_ids"] = ["packaging", "ingredients"]
    snapshot["suppliers"][3]["products"] = [
        "Многоразовые контейнеры",
        "Этикетки для пищевых продуктов",
    ]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    with TestClient(create_app(path)) as client:
        packaging = client.get(
            "/api/suppliers", params={"q": query, "category": "packaging"}
        ).json()
        assert [item["id"] for item in packaging["items"]] == ["containers"]
        assert packaging["items"][0]["reasons"][0]["label"] == "Совпадение по ассортименту"
        ingredients = client.get("/api/suppliers", params={"q": query, "category": "ingredients"})
        assert ingredients.json()["total"] == 0
        absent = client.get("/api/suppliers", params={"q": f"{query} редкийразмер"})
        assert absent.json()["total"] == 0


def test_catalog_subtype_can_answer_a_broad_search_without_widening_a_specific_query(
    snapshot: dict[str, Any], tmp_path: Path
) -> None:
    snapshot["suppliers"][2]["description"] = "Упаковка для еды. Контейнеры из полипропилена."
    snapshot["suppliers"][3]["products"] = ["Пищевые контейнеры"]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    with TestClient(create_app(path)) as client:
        broad = client.get("/api/suppliers", params={"q": "контейнеры для еды"}).json()
        assert [item["id"] for item in broad["items"]] == ["city-delivery", "containers"]
        specific = client.get("/api/suppliers", params={"q": "контейнеры из полипропилена"}).json()
        assert [item["id"] for item in specific["items"]] == ["containers"]


def test_product_match_outranks_a_description_mention(client: TestClient) -> None:
    items = client.get("/api/suppliers", params={"q": "мёд"}).json()["items"]
    assert [item["id"] for item in items] == ["local", "remote", "containers"]
    assert items[0]["reasons"][0]["label"] == "Совпадение по ассортименту"
    assert items[-1]["reasons"][0]["label"] == "Совпадение по описанию"


def test_search_excludes_uses_but_preserves_explicit_offers(
    snapshot: dict[str, Any], tmp_path: Path
) -> None:
    snapshot["suppliers"][2]["description"] = (
        "Производим трубочки для молока, а также продаём коробки для пиццы."
    )
    snapshot["suppliers"][3]["description"] = (
        "Производим оборудование для производителей коробок для пиццы."
    )
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    with TestClient(create_app(path)) as client:
        assert client.get("/api/suppliers", params={"q": "молоко"}).json()["total"] == 0
        assert client.get("/api/suppliers", params={"q": "пицца"}).json()["total"] == 0
        items = client.get("/api/suppliers", params={"q": "упаковка для пиццы"}).json()["items"]
        assert [item["id"] for item in items] == ["containers"]
        assert items[0]["reasons"][0]["label"] == "Совпадение по описанию"


def test_commodity_search_distinguishes_a_published_product_from_its_application(
    snapshot: dict[str, Any], tmp_path: Path
) -> None:
    snapshot["suppliers"][2]["products"] = [
        "СТЕКЛЯННЫЕ БУТЫЛКИ ДЛЯ МОЛОКА",
        "Коробки для пиццы",
        "Контейнеры для мяса",
        "Упаковка для пищевых продуктов: пленка для хранения и запекания",
    ]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    with TestClient(create_app(path)) as client:
        assert client.get("/api/suppliers", params={"q": "молоко"}).json()["total"] == 0
        assert client.get("/api/suppliers", params={"q": "бутылки для мяса"}).json()["total"] == 0
        for query in ("коробки для пиццы", "стеклянная бутылка для молока", "пленка для запекания"):
            items = client.get("/api/suppliers", params={"q": query}).json()["items"]
            assert [item["id"] for item in items] == ["containers"]


def test_city_service_matches_its_region_without_exaggerating_coverage(client: TestClient) -> None:
    items = client.get(
        "/api/suppliers", params={"region": "moscow-region", "category": "packaging"}
    ).json()["items"]
    assert [item["id"] for item in items] == ["city-delivery"]
    assert items[0]["reasons"][-1] == {"code": "delivery", "label": "Зона доставки · Коломна"}


def test_regional_location_does_not_imply_presence_in_a_city(client: TestClient) -> None:
    items = client.get(
        "/api/suppliers", params={"region": "kazan", "category": "packaging"}
    ).json()["items"]
    assert [item["id"] for item in items] == ["city-delivery"]


@pytest.mark.parametrize("query", ["липового отсутствующий", "…"])
def test_every_query_term_is_required(client: TestClient, query: str) -> None:
    response = client.get("/api/suppliers", params={"q": query})
    assert response.status_code == 200
    assert response.json() == {"catalog_revision": 1, "items": [], "total": 0, "next_offset": None}


@pytest.mark.parametrize(
    "field,value", [("category", "missing"), ("region", "missing"), ("q", "а" * 121)]
)
def test_validates_query_fields(client: TestClient, field: str, value: str) -> None:
    response = client.get("/api/suppliers", params={field: value})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", field]


def test_rejects_unrecognized_taxonomy_references(snapshot: dict[str, Any]) -> None:
    snapshot["suppliers"][0]["category_ids"] = ["missing"]
    with pytest.raises(ValidationError, match="unknown categories"):
        CatalogSnapshot.model_validate(snapshot)


def test_rejects_cycles_in_region_taxonomy(snapshot: dict[str, Any]) -> None:
    snapshot["regions"][0]["parent_id"] = "kolomna"
    with pytest.raises(ValidationError, match="Cycle in region hierarchy"):
        CatalogSnapshot.model_validate(snapshot)


def test_rejects_duplicate_suppliers(snapshot: dict[str, Any]) -> None:
    snapshot["suppliers"].append(snapshot["suppliers"][0])
    with pytest.raises(ValidationError, match="Duplicate supplier IDs"):
        CatalogSnapshot.model_validate(snapshot)


def test_serves_frontend_and_keeps_api_routes(catalog_path: Path, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>Foodsource</main>", encoding="utf-8")
    with TestClient(create_app(catalog_path, frontend_dir=dist)) as http:
        assert http.get("/").text == "<main>Foodsource</main>"
        assert http.get("/api/catalog").json()["supplier_count"] == 4
        assert http.get("/api/docs").status_code == 200
        missing = http.get("/api/missing")
        assert missing.status_code == 404
        assert missing.json() == {"detail": "Not Found"}
