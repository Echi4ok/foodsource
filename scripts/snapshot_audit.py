"""Report coverage and field completeness for a saved SQLite catalog, read-only."""

import argparse
import json
from collections import Counter
from pathlib import Path
from sqlite3 import DatabaseError

from backend.foodsource.domain import region_lineages
from backend.foodsource.identity import company_id
from backend.foodsource.repository import PublishedCatalog, load_catalog_revision


def audit(publication: PublishedCatalog) -> dict[str, object]:
    snapshot = publication.snapshot
    suppliers = snapshot.suppliers
    locations = Counter(supplier.region_id for supplier in suppliers)
    categories = Counter(category for supplier in suppliers for category in supplier.category_ids)
    source_labels = Counter(
        label for supplier in suppliers for label in {source.label for source in supplier.sources}
    )
    region_labels = {region.id: region.label for region in snapshot.regions}
    lineages = region_lineages(snapshot.regions)
    subject_ids = {region.id for region in snapshot.regions if region.parent_id == "ru"}
    subjects = Counter(
        country_subject
        for supplier in suppliers
        for country_subject in lineages[supplier.region_id] & subject_ids
    )
    providers = Counter(
        provider
        for supplier in suppliers
        for provider in {item.provider for item in supplier.observations}
    )

    return {
        "revision": publication.revision,
        "snapshot_date": snapshot.updated_at.isoformat(),
        "suppliers": len(suppliers),
        "companies": len({company_id(supplier) for supplier in suppliers}),
        "product_groups": sum(len(supplier.products) for supplier in suppliers),
        "locations_with_suppliers": len(locations),
        "locations_with_at_most_three_suppliers": sum(count <= 3 for count in locations.values()),
        "subjects_with_suppliers": len(subjects),
        "subjects_with_at_most_three_suppliers": sum(count <= 3 for count in subjects.values()),
        "locations": [
            {"id": region_id, "label": region_labels[region_id], "suppliers": count}
            for region_id, count in sorted(locations.items(), key=lambda item: (-item[1], item[0]))
        ],
        "categories": dict(sorted(categories.items())),
        "source_providers": dict(sorted(providers.items())),
        "source_labels": dict(sorted(source_labels.items())),
        "filled_fields": {
            "contact": sum(
                bool(supplier.contacts.phone or supplier.contacts.email) for supplier in suppliers
            ),
            "website": sum(bool(supplier.website) for supplier in suppliers),
            "product": sum(bool(supplier.products) for supplier in suppliers),
            "minimum_order": sum(bool(supplier.minimum_order) for supplier in suppliers),
            "price": sum(bool(supplier.price) for supplier in suppliers),
            "documents": sum(bool(supplier.documents) for supplier in suppliers),
            "delivery": sum(bool(supplier.delivery) for supplier in suppliers),
            "service_region_ids": sum(bool(supplier.service_region_ids) for supplier in suppliers),
        },
    }


def read_publication(path: Path) -> PublishedCatalog:
    publication = load_catalog_revision(path)
    if publication is None:
        raise RuntimeError(f"Missing published catalog in {path}")
    return publication


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--previous", type=Path, help="Optional baseline SQLite snapshot")
    args = parser.parse_args()
    try:
        publication = read_publication(args.db)
        report = audit(publication)
        if args.previous:
            previous = read_publication(args.previous)
            before = {supplier.id for supplier in previous.snapshot.suppliers}
            after = {supplier.id for supplier in publication.snapshot.suppliers}
            report["comparison"] = {
                "baseline_revision": previous.revision,
                "baseline_snapshot_date": previous.snapshot.updated_at.isoformat(),
                "baseline_suppliers": len(before),
                "retained_supplier_ids": len(before & after),
                "additional_supplier_ids": len(after - before),
                "absent_supplier_ids": len(before - after),
            }
    except (OSError, RuntimeError, ValueError, DatabaseError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
