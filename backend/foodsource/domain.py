from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

CatalogId = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$", max_length=64)
]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Category(CatalogModel):
    id: CatalogId
    label: NonEmptyText


class Region(CatalogModel):
    id: CatalogId
    label: NonEmptyText
    parent_id: CatalogId | None = None


class Source(CatalogModel):
    label: NonEmptyText
    url: HttpUrl


class Contacts(CatalogModel):
    email: NonEmptyText | None = None
    phone: NonEmptyText | None = None


class SupplierProfile(CatalogModel):
    id: CatalogId
    name: NonEmptyText
    description: NonEmptyText
    category_ids: tuple[CatalogId, ...] = Field(min_length=1)
    region_id: CatalogId
    service_region_ids: tuple[CatalogId, ...] = ()
    products: tuple[NonEmptyText, ...] = ()
    website: HttpUrl | None = None
    contacts: Contacts = Field(default_factory=Contacts)
    minimum_order: NonEmptyText | None = None
    price: NonEmptyText | None = None
    documents: NonEmptyText | None = None
    delivery: NonEmptyText | None = None
    sources: tuple[Source, ...] = Field(min_length=1)


class SourceObservation(CatalogModel):
    provider: CatalogId
    external_id: NonEmptyText
    url: HttpUrl
    observed_at: datetime | None = None


class FieldEvidence(CatalogModel):
    field: NonEmptyText
    url: HttpUrl


def canonical_domain(host: str) -> str:
    return host.casefold().rstrip(".").removeprefix("www.")


class SupplierRecord(SupplierProfile):
    keywords: tuple[NonEmptyText, ...] = ()
    inn: Annotated[str, StringConstraints(pattern=r"^(?:[0-9]{10}|[0-9]{12})$")] | None = None
    official_domain: NonEmptyText | None = None
    observations: tuple[SourceObservation, ...] = ()
    field_evidence: tuple[FieldEvidence, ...] = ()

    @model_validator(mode="after")
    def validate_official_domain(self) -> Self:
        if self.official_domain is not None:
            host = self.website.host if self.website is not None else None
            if host is None or canonical_domain(host) != canonical_domain(self.official_domain):
                raise ValueError("The verified official domain must match the supplier website")
        return self


def region_lineages(regions: tuple[Region, ...]) -> dict[str, frozenset[str]]:
    """Each region's ID and all of its ancestors, from city to country."""
    parents = {region.id: region.parent_id for region in regions}
    lineages: dict[str, frozenset[str]] = {}
    for region_id in parents:
        lineage: set[str] = set()
        current_id: str | None = region_id
        while current_id is not None:
            if current_id in lineage:
                raise ValueError(f"Cycle in region hierarchy at {current_id!r}")
            if current_id not in parents:
                raise ValueError(f"Unknown parent region {current_id!r}")
            lineage.add(current_id)
            current_id = parents[current_id]
        lineages[region_id] = frozenset(lineage)
    return lineages


class CatalogSnapshot(CatalogModel):
    schema_version: Literal[1]
    updated_at: date
    categories: tuple[Category, ...] = Field(min_length=1)
    regions: tuple[Region, ...] = Field(min_length=1)
    suppliers: tuple[SupplierRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        category_ids = {category.id for category in self.categories}
        region_ids = {region.id for region in self.regions}
        supplier_ids = {supplier.id for supplier in self.suppliers}
        if len(category_ids) != len(self.categories):
            raise ValueError("Duplicate category IDs")
        if len(region_ids) != len(self.regions):
            raise ValueError("Duplicate region IDs")
        if len(supplier_ids) != len(self.suppliers):
            raise ValueError("Duplicate supplier IDs")

        region_lineages(self.regions)
        for supplier in self.suppliers:
            unknown_categories = set(supplier.category_ids) - category_ids
            unknown_regions = {supplier.region_id, *supplier.service_region_ids} - region_ids
            if unknown_categories:
                raise ValueError(
                    f"Supplier {supplier.id!r} uses unknown categories {sorted(unknown_categories)}"
                )
            if unknown_regions:
                raise ValueError(
                    f"Supplier {supplier.id!r} uses unknown regions {sorted(unknown_regions)}"
                )
        return self


class MatchReason(CatalogModel):
    code: Literal["query", "category", "location", "delivery"]
    label: NonEmptyText
