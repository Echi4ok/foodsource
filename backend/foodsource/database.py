import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

PRODUCT_SCHEMA = """
CREATE TABLE IF NOT EXISTS supplier_product (
    supplier_id TEXT NOT NULL REFERENCES supplier(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK(position >= 0),
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    PRIMARY KEY(supplier_id, position),
    UNIQUE(supplier_id, name)
)
"""

SCHEMA = f"""
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS catalog_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    revision INTEGER NOT NULL CHECK(revision > 0),
    updated_at TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS category (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    position INTEGER NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS region (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    position INTEGER NOT NULL UNIQUE,
    parent_id TEXT REFERENCES region(id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE IF NOT EXISTS supplier (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL REFERENCES region(id),
    name_key TEXT NOT NULL,
    inn TEXT,
    official_domain TEXT,
    profile_json TEXT NOT NULL CHECK(json_valid(profile_json))
);
CREATE UNIQUE INDEX IF NOT EXISTS supplier_inn_per_region
    ON supplier(region_id, inn) WHERE inn IS NOT NULL;
CREATE TABLE IF NOT EXISTS supplier_category (
    supplier_id TEXT NOT NULL REFERENCES supplier(id) ON DELETE CASCADE,
    category_id TEXT NOT NULL REFERENCES category(id),
    position INTEGER NOT NULL,
    PRIMARY KEY(supplier_id, category_id),
    UNIQUE(supplier_id, position)
);
{PRODUCT_SCHEMA};
CREATE TABLE IF NOT EXISTS source_observation (
    supplier_id TEXT NOT NULL REFERENCES supplier(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    observed_at TEXT,
    position INTEGER NOT NULL,
    PRIMARY KEY(supplier_id, provider, external_id, url),
    UNIQUE(supplier_id, position)
);
CREATE TABLE IF NOT EXISTS field_evidence (
    supplier_id TEXT NOT NULL REFERENCES supplier(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    url TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY(supplier_id, field, url),
    UNIQUE(supplier_id, position)
);
PRAGMA user_version = {SCHEMA_VERSION};
COMMIT;
"""


def connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        if not path.is_file():
            raise FileNotFoundError(
                f"Catalog database {path} is missing. Run the supplier import first."
            )
        db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def ensure_schema(db: sqlite3.Connection, *, writable: bool = False) -> int:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return version
    if version == 1:
        return _migrate_products(db) if writable else version
    if writable and version == 0:
        db.executescript(SCHEMA)
        return SCHEMA_VERSION
    if version == 0 and db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone() is None:
        return 0
    raise RuntimeError(f"Unsupported catalog database schema {version}; expected {SCHEMA_VERSION}")


def _migrate_products(db: sqlite3.Connection) -> int:
    with db:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return version
        if version != 1:
            raise RuntimeError(f"Cannot migrate catalog database schema {version}")

        db.execute(PRODUCT_SCHEMA)
        db.execute(
            """
            INSERT INTO supplier_product (supplier_id, position, name)
            SELECT supplier.id, product.key, product.value
            FROM supplier CROSS JOIN json_each(supplier.profile_json, '$.products') AS product
            """
        )
        db.execute("UPDATE supplier SET profile_json = json_remove(profile_json, '$.products')")
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return SCHEMA_VERSION
