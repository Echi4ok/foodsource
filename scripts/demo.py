"""Serve or check a synthetic FoodSource catalog in a temporary SQLite database."""

import argparse
import json
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.foodsource.api import create_app
from backend.foodsource.domain import CatalogSnapshot, SupplierRecord
from backend.foodsource.repository import import_catalog

SCRIPT_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = SCRIPT_DIR.parent / "frontend" / "dist"
DEMO_CATALOG = SCRIPT_DIR / "demo_catalog.json"


def demo_snapshot(origin: str) -> CatalogSnapshot:
    data = json.loads(DEMO_CATALOG.read_text(encoding="utf-8"))
    for item in data["suppliers"]:
        source_url = f"{origin}/demo-sources/{item['id']}"
        item["sources"] = [{"label": "Демо · учебный первоисточник", "url": source_url}]
        item["observations"] = [
            {
                "provider": "demo-fixture",
                "external_id": item["id"],
                "url": source_url,
                "observed_at": f"{data['updated_at']}T00:00:00Z",
            }
        ]
        item["field_evidence"] = [
            {"field": field, "url": source_url}
            for field in (
                "name",
                "region_id",
                "category_ids",
                "products",
                "minimum_order",
                "price",
                "documents",
                "delivery",
                "service_region_ids",
            )
            if item.get(field)
        ]
        item["field_evidence"].extend(
            {"field": field, "url": source_url} for field in item.get("contacts", {})
        )
    return CatalogSnapshot.model_validate(data)


def source_html(
    supplier: SupplierRecord, region_labels: dict[str, str], category_labels: dict[str, str]
) -> str:
    name = escape(supplier.name)
    description = escape(supplier.description)
    products = "".join(f"<li>{escape(product)}</li>" for product in supplier.products)
    terms = "".join(
        f"<dt>{label}</dt><dd>{escape(value)}</dd>"
        for label, value in (
            ("Город", region_labels[supplier.region_id]),
            ("Категории", ", ".join(category_labels[id_] for id_ in supplier.category_ids)),
            ("Email", supplier.contacts.email),
            ("Телефон", supplier.contacts.phone),
            ("Минимальный заказ", supplier.minimum_order),
            ("Цена", supplier.price),
            ("Документы", supplier.documents),
            ("Доставка", supplier.delivery),
            (
                "География доставки",
                ", ".join(region_labels[id_] for id_ in supplier.service_region_ids),
            ),
        )
        if value
    )
    return f"""<!doctype html>
<html lang="ru">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>
<style>body{{font:1rem/1.5 system-ui;max-width:42rem;margin:3rem auto;padding:0 1.2rem}}dt{{font-weight:700}}dd{{margin:0 0 .5rem}}</style>
<main>
<p>Синтетический учебный источник. Компания, контакты и условия вымышлены.</p>
<h1>{name}</h1>
<p>{description}</p>
<h2>Ассортимент</h2>
<ul>{products}</ul>
<dl>{terms}</dl>
</main>
</html>"""


def demo_app(db_path: Path, snapshot: CatalogSnapshot) -> FastAPI:
    app = create_app(db_path=db_path)
    by_id = {supplier.id: supplier for supplier in snapshot.suppliers}
    region_labels = {region.id: region.label for region in snapshot.regions}
    category_labels = {category.id: category.label for category in snapshot.categories}

    @app.get("/demo-sources/{supplier_id}", response_class=HTMLResponse, include_in_schema=False)
    def demo_source(supplier_id: str) -> str:
        supplier = by_id.get(supplier_id)
        if supplier is None:
            raise HTTPException(status_code=404)
        return source_html(supplier, region_labels, category_labels)

    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return app


def check_demo(app: FastAPI, snapshot: CatalogSnapshot) -> None:
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/health").json()["status"] == "ok"
        meta = client.get("/api/catalog").json()
        assert meta["catalog_revision"] == 1
        assert meta["supplier_count"] == len(snapshot.suppliers)

        filters = {"region": "ekaterinburg", "category": "packaging", "q": "бумажные стаканы"}
        response = client.get("/api/suppliers", params=filters).json()
        entries = response["items"]
        assert response["total"] == 3
        assert {entry["id"] for entry in entries} == {
            "demo-cup-alpha",
            "demo-cup-beta",
            "demo-cup-gamma",
        }
        assert {entry["price"] is None for entry in entries} == {True, False}
        first_page = client.get("/api/suppliers", params={**filters, "limit": 2}).json()
        next_offset = first_page["next_offset"]
        assert next_offset == 2
        last_page = client.get(
            "/api/suppliers", params={**filters, "limit": 2, "offset": next_offset}
        ).json()
        assert last_page["next_offset"] is None and last_page["total"] == 3
        assert first_page["items"] + last_page["items"] == entries

        for entry in entries:
            product_fact = next(fact for fact in entry["evidence"] if fact["field"] == "products")
            assert product_fact["observed_at"] == meta["updated_at"]
            assert product_fact["url"] == entry["sources"][0]["url"]
            source = client.get(urlsplit(product_fact["url"]).path)
            assert source.status_code == 200 and "Синтетический" in source.text
            assert entry["name"] in source.text
            assert "Бумажные стаканы" in source.text

        shortlist_ids = ["demo-cup-gamma", "demo-cup-alpha", "demo-does-not-exist"]
        shortlist = client.get("/api/suppliers/shortlist", params={"id": shortlist_ids}).json()
        assert shortlist["catalog_revision"] == meta["catalog_revision"]
        assert [entry["id"] for entry in shortlist["items"]] == shortlist_ids[:2]

        assert (
            client.get("/api/suppliers", params={"q": "несуществующий товар"}).json()["total"] == 0
        )
        delivery = client.get(
            "/api/suppliers", params={"region": "ekaterinburg", "q": "стеклянные банки"}
        ).json()["items"]
        assert [entry["id"] for entry in delivery] == ["demo-glass"]
        assert any(reason["code"] == "delivery" for reason in delivery[0]["reasons"])
    print(f"Demo smoke: {meta['supplier_count']} synthetic suppliers, revision 1, OK")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--check", action="store_true", help="Run the offline API smoke check")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not (FRONTEND_DIST / "index.html").is_file():
        parser.error("Build the frontend first: npm run build --prefix frontend")

    origin = f"http://localhost:{args.port}"
    snapshot = demo_snapshot(origin)
    with TemporaryDirectory(prefix="foodsource-demo-") as directory:
        db_path = Path(directory) / "catalog.sqlite3"
        import_catalog(snapshot, db_path, expected_revision=0)
        app = demo_app(db_path, snapshot)
        if args.check:
            repeat = import_catalog(snapshot, db_path, expected_revision=1)
            assert not repeat.changed and repeat.revision == 1
            check_demo(app, snapshot)
        else:
            print(f"Synthetic FoodSource demo: {origin}", flush=True)
            uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
