import hashlib
import json
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection

from .database import connect, ensure_schema
from .domain import CatalogSnapshot
from .identity import name_key, preserve_supplier_ids, resolve_suppliers

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_DB_PATH = DATA_DIR / "catalog.sqlite3"

_PROFILE_EXCLUDED = {
    "id",
    "region_id",
    "category_ids",
    "products",
    "inn",
    "official_domain",
    "observations",
    "field_evidence",
}


@dataclass(frozen=True)
class ImportResult:
    supplier_count: int
    observation_count: int
    revision: int
    changed: bool


@dataclass(frozen=True)
class PublishedCatalog:
    revision: int
    snapshot: CatalogSnapshot


class CatalogNotPublished(RuntimeError):
    """The SQLite schema exists but no import has been published."""


class CatalogRevisionConflict(RuntimeError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"The catalog changed during collection (revision {expected} → {actual}). "
            "Run the import again against the current catalog."
        )


def load_catalog(path: Path) -> CatalogSnapshot:
    """Read a JSON fixture or exchange file explicitly."""
    return CatalogSnapshot.model_validate_json(path.read_text(encoding="utf-8-sig"))


def import_catalog(
    snapshot: CatalogSnapshot,
    path: Path = DEFAULT_DB_PATH,
    *,
    expected_revision: int | None = None,
) -> ImportResult:
    """Resolve supplier identities and publish a complete normalized crawl in one transaction."""
    snapshot = CatalogSnapshot.model_validate(snapshot.model_dump())
    suppliers = resolve_suppliers(snapshot.suppliers)
    observation_count = sum(len(supplier.observations) for supplier in suppliers)
    with closing(connect(path, read_only=False)) as db:
        ensure_schema(db, writable=True)
        with db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT revision, content_hash FROM catalog_state WHERE singleton = 1"
            ).fetchone()
            if current is not None:
                previous_sources = db.execute(
                    "SELECT provider, external_id, supplier_id FROM source_observation"
                )
                suppliers = preserve_supplier_ids(
                    suppliers,
                    (
                        (row["provider"], row["external_id"], row["supplier_id"])
                        for row in previous_sources
                    ),
                )
            resolved = snapshot.model_copy(update={"suppliers": suppliers})
            content_hash = hashlib.sha256(resolved.model_dump_json().encode()).hexdigest()
            if current is not None and current["content_hash"] == content_hash:
                return ImportResult(len(suppliers), observation_count, current["revision"], False)
            current_revision = current["revision"] if current is not None else 0
            if expected_revision is not None and current_revision != expected_revision:
                raise CatalogRevisionConflict(expected_revision, current_revision)
            revision = current_revision + 1
            _replace_rows(db, resolved)
            db.execute(
                """
                INSERT INTO catalog_state (singleton, revision, updated_at, content_hash)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    revision = excluded.revision,
                    updated_at = excluded.updated_at,
                    content_hash = excluded.content_hash
                """,
                (revision, resolved.updated_at.isoformat(), content_hash),
            )
    return ImportResult(len(suppliers), observation_count, revision, True)


def _replace_rows(db: Connection, snapshot: CatalogSnapshot) -> None:
    db.execute("DELETE FROM supplier")
    db.execute("DELETE FROM category")
    db.execute("DELETE FROM region")
    db.executemany(
        "INSERT INTO category (id, label, position) VALUES (?, ?, ?)",
        [(item.id, item.label, index) for index, item in enumerate(snapshot.categories)],
    )
    db.executemany(
        "INSERT INTO region (id, label, parent_id, position) VALUES (?, ?, ?, ?)",
        [
            (item.id, item.label, item.parent_id, index)
            for index, item in enumerate(snapshot.regions)
        ],
    )
    db.executemany(
        """
        INSERT INTO supplier (id, region_id, name_key, inn, official_domain, profile_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                supplier.id,
                supplier.region_id,
                name_key(supplier.name),
                supplier.inn,
                supplier.official_domain,
                supplier.model_dump_json(exclude=_PROFILE_EXCLUDED),
            )
            for supplier in snapshot.suppliers
        ],
    )
    db.executemany(
        "INSERT INTO supplier_category (supplier_id, category_id, position) VALUES (?, ?, ?)",
        [
            (supplier.id, category_id, position)
            for supplier in snapshot.suppliers
            for position, category_id in enumerate(supplier.category_ids)
        ],
    )
    db.executemany(
        "INSERT INTO supplier_product (supplier_id, position, name) VALUES (?, ?, ?)",
        [
            (supplier.id, position, name)
            for supplier in snapshot.suppliers
            for position, name in enumerate(supplier.products)
        ],
    )
    db.executemany(
        """
        INSERT INTO source_observation (supplier_id, provider, external_id, url, observed_at, position)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                supplier.id,
                item.provider,
                item.external_id,
                str(item.url),
                item.observed_at.isoformat() if item.observed_at else None,
                position,
            )
            for supplier in snapshot.suppliers
            for position, item in enumerate(supplier.observations)
        ],
    )
    db.executemany(
        "INSERT INTO field_evidence (supplier_id, field, url, position) VALUES (?, ?, ?, ?)",
        [
            (supplier.id, item.field, str(item.url), position)
            for supplier in snapshot.suppliers
            for position, item in enumerate(supplier.field_evidence)
        ],
    )


def load_catalog_revision(
    path: Path, *, after_revision: int | None = None
) -> PublishedCatalog | None:
    """Read a consistent revision without rebuilding data for an unchanged catalog."""
    with closing(connect(path, read_only=True)) as db:
        with db:
            db.execute("BEGIN")
            schema_version = ensure_schema(db)
            if schema_version == 0:
                raise CatalogNotPublished(
                    "Catalog is empty. Run a successful supplier import first."
                )
            state = db.execute(
                "SELECT revision, updated_at FROM catalog_state WHERE singleton = 1"
            ).fetchone()
            if state is None:
                raise CatalogNotPublished(
                    "Catalog is empty. Run a successful supplier import first."
                )
            if state["revision"] == after_revision:
                return None
            snapshot = _read_snapshot(db, state["updated_at"], schema_version)
            return PublishedCatalog(state["revision"], snapshot)


def _read_snapshot(db: Connection, updated_at: str, schema_version: int) -> CatalogSnapshot:
    category_ids = defaultdict(list)
    for row in db.execute(
        "SELECT supplier_id, category_id FROM supplier_category ORDER BY supplier_id, position"
    ):
        category_ids[row["supplier_id"]].append(row["category_id"])

    products = defaultdict(list)
    if schema_version >= 2:
        for row in db.execute(
            "SELECT supplier_id, name FROM supplier_product ORDER BY supplier_id, position"
        ):
            products[row["supplier_id"]].append(row["name"])

    observations = defaultdict(list)
    for row in db.execute(
        """
        SELECT supplier_id, provider, external_id, url, observed_at FROM source_observation
        ORDER BY supplier_id, position
        """
    ):
        item = dict(row)
        observations[item.pop("supplier_id")].append(item)

    field_evidence = defaultdict(list)
    for row in db.execute(
        "SELECT supplier_id, field, url FROM field_evidence ORDER BY supplier_id, position"
    ):
        item = dict(row)
        field_evidence[item.pop("supplier_id")].append(item)

    suppliers = []
    for row in db.execute(
        "SELECT id, region_id, inn, official_domain, profile_json FROM supplier ORDER BY id"
    ):
        item = dict(row)
        supplier_id = item["id"]
        profile = json.loads(item.pop("profile_json"))
        if schema_version >= 2:
            profile["products"] = products[supplier_id]
        suppliers.append(
            {
                **profile,
                **item,
                "category_ids": category_ids[supplier_id],
                "observations": observations[supplier_id],
                "field_evidence": field_evidence[supplier_id],
            }
        )

    return CatalogSnapshot.model_validate(
        {
            "schema_version": 1,
            "updated_at": updated_at,
            "categories": [
                dict(row) for row in db.execute("SELECT id, label FROM category ORDER BY position")
            ],
            "regions": [
                dict(row)
                for row in db.execute("SELECT id, label, parent_id FROM region ORDER BY position")
            ],
            "suppliers": suppliers,
        }
    )


def load_sqlite_catalog(path: Path = DEFAULT_DB_PATH) -> CatalogSnapshot:
    publication = load_catalog_revision(path)
    if publication is None:
        raise CatalogNotPublished("No catalog version has been published")
    return publication.snapshot
