import os
from pathlib import Path

from foodsource.api import create_app
from foodsource.repository import DEFAULT_DB_PATH

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"

app = create_app(
    frontend_dir=FRONTEND_DIST,
    db_path=Path(os.environ.get("FOODSOURCE_DB_PATH", str(DEFAULT_DB_PATH))),
)
