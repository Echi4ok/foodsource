from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .domain import (
    CatalogId,
    Category,
    FieldEvidence,
    MatchReason,
    Region,
    SupplierProfile,
    SupplierRecord,
)
from .identity import company_id
from .pagination import PageRequest
from .repository import DEFAULT_DB_PATH, load_catalog, load_catalog_revision
from .search import SupplierSearch, UnknownFilter


class CatalogResponse(BaseModel):
    catalog_revision: int
    updated_at: date
    categories: tuple[Category, ...]
    regions: tuple[Region, ...]
    supplier_count: int


class SupplierEvidence(FieldEvidence):
    observed_at: date | None = None


class SupplierResponse(SupplierProfile):
    company_id: str
    reasons: tuple[MatchReason, ...]
    evidence: tuple[SupplierEvidence, ...] = ()


class SearchResponse(BaseModel):
    catalog_revision: int
    items: tuple[SupplierResponse, ...]
    total: int
    next_offset: int | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@dataclass(frozen=True)
class CatalogView:
    revision: int
    search: SupplierSearch


class SqliteSearch:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._view: CatalogView | None = None
        self._lock = Lock()

    def current(self) -> CatalogView:
        with self._lock:
            catalog = load_catalog_revision(
                self._path, after_revision=self._view.revision if self._view else None
            )
            if catalog is not None:
                self._view = CatalogView(catalog.revision, SupplierSearch(catalog.snapshot))
            if self._view is None:
                raise RuntimeError("No searchable catalog version found")
            return self._view


def get_catalog_view(request: Request) -> CatalogView:
    return request.app.state.search_provider()


CurrentCatalog = Annotated[CatalogView, Depends(get_catalog_view)]
router = APIRouter(prefix="/api")
PUBLIC_SUPPLIER_FIELDS = set(SupplierProfile.model_fields)
PUBLIC_EVIDENCE_FIELDS = frozenset(
    {
        "products",
        "region_id",
        "service_region_ids",
        "price",
        "minimum_order",
        "documents",
        "delivery",
        "phone",
        "email",
        "website",
    }
)


def supplier_response(
    supplier: SupplierRecord, reasons: tuple[MatchReason, ...] = ()
) -> SupplierResponse:
    observed_dates: dict[str, date] = {}
    for observation in supplier.observations:
        if observation.observed_at is not None:
            url = str(observation.url)
            day = observation.observed_at.date()
            observed_dates[url] = max(day, observed_dates.get(url, day))
    evidence = tuple(
        SupplierEvidence(
            field=record.field, url=record.url, observed_at=observed_dates.get(str(record.url))
        )
        for record in supplier.field_evidence
        if record.field in PUBLIC_EVIDENCE_FIELDS
    )
    return SupplierResponse(
        **supplier.model_dump(include=PUBLIC_SUPPLIER_FIELDS),
        company_id=company_id(supplier),
        reasons=reasons,
        evidence=evidence,
    )


@router.get("/health", summary="Проверить доступность API")
def health() -> HealthResponse:
    return HealthResponse()


@router.get("/catalog", summary="Получить категории, географию и дату обновления")
def catalog(view: CurrentCatalog) -> CatalogResponse:
    snapshot = view.search.snapshot
    return CatalogResponse(
        catalog_revision=view.revision,
        updated_at=snapshot.updated_at,
        categories=snapshot.categories,
        regions=snapshot.regions,
        supplier_count=view.search.company_count,
    )


@router.get("/suppliers", summary="Найти поставщиков по запросу, категории и географии")
def suppliers(
    view: CurrentCatalog,
    pagination: Annotated[PageRequest, Depends()],
    category: Annotated[str, Query(max_length=64)] = "",
    region: Annotated[str, Query(max_length=64)] = "",
    q: Annotated[str, Query(max_length=120)] = "",
) -> SearchResponse:
    try:
        matches = view.search.search(q.strip(), category.strip(), region.strip())
    except UnknownFilter as error:
        raise HTTPException(
            status_code=422,
            detail=[{"loc": ["query", error.field], "msg": str(error), "type": "unknown_filter"}],
        ) from error

    page = pagination.select(matches)
    items = tuple(supplier_response(match.supplier, match.reasons) for match in page.items)
    return SearchResponse(
        catalog_revision=view.revision, items=items, total=page.total, next_offset=page.next_offset
    )


@router.get(
    "/suppliers/shortlist", summary="Восстановить выбранных поставщиков из текущего каталога"
)
def shortlist(
    view: CurrentCatalog,
    supplier_ids: Annotated[list[CatalogId], Query(alias="id", min_length=1, max_length=3)],
) -> SearchResponse:
    requested = dict.fromkeys(supplier_ids)
    by_id = {
        supplier.id: supplier
        for supplier in view.search.snapshot.suppliers
        if supplier.id in requested
    }
    companies: dict[str, SupplierResponse] = {}
    for id_ in requested:
        if (supplier := by_id.get(id_)) is not None and (
            key := company_id(supplier)
        ) not in companies:
            companies[key] = supplier_response(supplier)
    items = tuple(companies.values())
    return SearchResponse(catalog_revision=view.revision, items=items, total=len(items))


def create_app(
    catalog_path: Path | None = None,
    frontend_dir: Path | None = None,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if catalog_path is None:
            search = SqliteSearch(db_path)
            search.current()
            app.state.search_provider = search.current
        else:
            fixture = CatalogView(1, SupplierSearch(load_catalog(catalog_path)))
            app.state.search_provider = lambda: fixture
        yield

    app = FastAPI(
        title="Foodsource API",
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        swagger_ui_oauth2_redirect_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.include_router(router)
    if frontend_dir is not None and (frontend_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    return app
