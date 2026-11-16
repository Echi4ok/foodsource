"""Stage a read-only FoodSource release for Vercel."""

import argparse
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND_DIST = ROOT / "frontend" / "dist"
DEFAULT_DB = BACKEND / "data" / "catalog.sqlite3"
RUNTIME = BACKEND / "foodsource"

ENTRYPOINT = """\
from pathlib import Path

from foodsource.api import create_app

ROOT = Path(__file__).resolve().parent

app = create_app(frontend_dir=ROOT / "dist", db_path=ROOT / "data" / "catalog.sqlite3")
"""


def snapshot(database: Path, target: Path) -> int:
    with (
        closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(target)) as copy,
    ):
        source.backup(copy)
        copy.execute("PRAGMA journal_mode = DELETE")
        if copy.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("The staged catalog failed SQLite integrity checks.")
        if copy.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("The staged catalog contains broken references.")
        state = copy.execute("SELECT revision FROM catalog_state WHERE singleton = 1").fetchone()
        if (
            state is None
            or state[0] < 1
            or not copy.execute("SELECT 1 FROM supplier LIMIT 1").fetchone()
        ):
            raise ValueError(
                "The catalog has no published revision. Run the supplier import first."
            )
        return state[0]


def stage(output: Path, database: Path, frontend_dir: Path = FRONTEND_DIST) -> int:
    output = output.parent.resolve() / output.name
    database = database.resolve(strict=True)
    frontend_dir = frontend_dir.resolve(strict=True)
    if not database.is_file():
        raise ValueError("The catalog path must point to a SQLite file.")
    if not (frontend_dir / "index.html").is_file():
        raise ValueError("Build the frontend before staging a release.")
    if database.is_relative_to(frontend_dir) or output.is_relative_to(frontend_dir):
        raise ValueError("The catalog and release directory must be outside the frontend build.")
    if output.is_relative_to(RUNTIME.resolve()):
        raise ValueError("The release directory must be outside the runtime source tree.")
    output.mkdir()
    try:
        (output / "data").mkdir()
        revision = snapshot(database, output / "data" / "catalog.sqlite3")
        requirements = subprocess.run(
            [
                "uv",
                "export",
                "--locked",
                "--no-default-groups",
                "--no-emit-project",
                "--no-header",
                "--no-annotate",
                "--format",
                "requirements.txt",
            ],
            cwd=BACKEND,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        (output / "requirements.txt").write_bytes(requirements)
        (output / "app.py").write_text(ENTRYPOINT, encoding="utf-8")
        (output / ".python-version").write_text("3.12\n", encoding="utf-8")
        shutil.copytree(
            RUNTIME,
            output / "foodsource",
            ignore=shutil.ignore_patterns("ingest", "search_eval.py", "__pycache__", "*.pyc"),
        )
        shutil.copytree(frontend_dir, output / "dist")
    except (Exception, KeyboardInterrupt):
        shutil.rmtree(output)
        raise
    return revision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New staging directory")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Published catalog database")
    args = parser.parse_args()
    try:
        revision = stage(args.output, args.db)
    except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Could not stage the release: {error}\n")
    size = sum(path.stat().st_size for path in args.output.rglob("*") if path.is_file())
    print(f"Staged catalog revision {revision} in {args.output.resolve()} ({size:,} bytes).")


if __name__ == "__main__":
    main()
