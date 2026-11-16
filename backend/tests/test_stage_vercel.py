import runpy
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from foodsource.domain import CatalogSnapshot
from foodsource.repository import import_catalog

STAGER = Path(__file__).resolve().parents[2] / "scripts" / "stage_vercel.py"


def test_stages_published_wal_catalog_and_runs_from_release_directory(tmp_path: Path) -> None:
    stage = runpy.run_path(str(STAGER))["stage"]
    database = tmp_path / "source.sqlite3"
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("Packaged frontend", encoding="utf-8")
    output = tmp_path / "release"
    catalog = CatalogSnapshot.model_validate(
        {
            "schema_version": 1,
            "updated_at": "2026-11-16",
            "categories": [{"id": "packaging", "label": "Packaging"}],
            "regions": [{"id": "ekaterinburg", "label": "Ekaterinburg"}],
            "suppliers": [
                {
                    "id": "factory",
                    "name": "Factory",
                    "description": "Pizza boxes",
                    "category_ids": ["packaging"],
                    "region_id": "ekaterinburg",
                    "products": ["Pizza boxes"],
                    "sources": [{"label": "Catalog", "url": "https://example.com/catalog"}],
                }
            ],
        }
    )

    import_catalog(catalog, database)
    with closing(sqlite3.connect(database)) as writer:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        with writer:
            writer.execute("UPDATE catalog_state SET revision = 2 WHERE singleton = 1")
        assert Path(f"{database}-wal").stat().st_size > 0
        assert stage(output, database, frontend) == 2

        with pytest.raises(FileExistsError):
            stage(output, database, frontend)

    assert not (output / "foodsource" / "ingest").exists()
    assert (output / "foodsource" / "search_vocabulary.json").is_file()
    assert not list(output.rglob("*-wal"))
    requirements = (output / "requirements.txt").read_text(encoding="utf-8")
    assert "fastapi==" in requirements
    assert "scrapy==" not in requirements

    probe = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            """\
from app import app
from fastapi.testclient import TestClient

with TestClient(app) as client:
    assert client.get('/').text == 'Packaged frontend'
    assert client.get('/data/catalog.sqlite3').status_code == 404
    catalog = client.get('/api/catalog').json()
    assert catalog['catalog_revision'] == 2
    items = client.get('/api/suppliers', params={'region': 'ekaterinburg'}).json()['items']
    assert items[0]['id'] == 'factory'
""",
        ],
        cwd=output,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
