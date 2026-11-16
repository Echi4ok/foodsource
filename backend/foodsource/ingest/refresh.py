from typing import Any

from foodsource.domain import CatalogSnapshot, SupplierRecord

from .normalize import company_url, official_domain


def preserve_temporarily_unavailable(
    raw: list[dict[str, Any]], previous: CatalogSnapshot | None
) -> tuple[list[dict[str, Any]], int]:
    last = {}
    if previous:
        for record in previous.suppliers:
            identities = {(source.provider, source.external_id) for source in record.observations}
            if len(identities) == 1:
                last[next(iter(identities))] = record

    retained = 0
    selected = []
    for item in raw:
        prior = last.get((item["provider"], item["external_id"]))
        if prior and item.get("website_unavailable"):
            confirmed = retain_website_facts(item, prior)
            if confirmed != item:
                retained += 1
            item = confirmed
        if item["category_ids"]:
            selected.append(item)
    return selected, retained


def retain_website_facts(item: dict[str, Any], prior: SupplierRecord) -> dict[str, Any]:
    website = company_url(item.get("website"))
    domain = official_domain(website)
    if not domain or domain != prior.official_domain or item["region_id"] != prior.region_id:
        return item

    fields = {
        field.field: str(field.url)
        for field in prior.field_evidence
        if official_domain(str(field.url)) == domain
    }
    evidence_url = next(
        (
            fields[field]
            for field in ("category_ids", "products", "phone", "email")
            if field in fields
        ),
        None,
    )
    if evidence_url is None:
        return item
    updated = dict(item)
    enriched = []
    if fields.get("category_ids") == evidence_url:
        categories = sorted(set(item["category_ids"]) | set(prior.category_ids))
        if set(categories) != set(item["category_ids"]):
            updated["category_ids"] = categories
            enriched.append("category_ids")
    if updated["category_ids"] and fields.get("products") == evidence_url:
        products = sorted(set(item.get("products", ())) | set(prior.products), key=str.casefold)
        if set(products) != set(item.get("products", ())):
            updated["products"] = products
            enriched.append("products")
    for field in ("phone", "email"):
        value = getattr(prior.contacts, field)
        if value and not item.get(field) and fields.get(field) == evidence_url:
            updated[field] = value
            enriched.append(field)
    if not enriched:
        return item
    updated.update(
        enrichment_url=evidence_url,
        enrichment_observed_at=max(
            (
                source.observed_at.date()
                for source in prior.observations
                if str(source.url) == evidence_url and source.observed_at is not None
            ),
            default=None,
        ),
        enriched_fields=enriched,
        new_fields=[field for field in enriched if not item.get(field)],
    )
    return updated
