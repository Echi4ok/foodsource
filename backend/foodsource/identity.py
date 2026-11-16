from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from hashlib import sha256

from .domain import SourceObservation, SupplierRecord, canonical_domain
from .text import word_tokens

PREFERRED_FIELDS = ("inn", "website", "minimum_order", "price", "documents", "delivery")
CONTACT_FIELDS = ("phone", "email")
LEGAL_FORMS = (
    ("ооо", ("общество", "с", "ограниченной", "ответственностью")),
    ("пао", ("публичное", "акционерное", "общество")),
    ("зао", ("закрытое", "акционерное", "общество")),
    ("оао", ("открытое", "акционерное", "общество")),
    ("ао", ("акционерное", "общество")),
)


def name_key(name: str) -> str:
    return " ".join(word_tokens(name))


def company_id(record: SupplierRecord) -> str:
    if record.inn:
        return "company-" + sha256(record.inn.encode()).hexdigest()[:24]
    return record.id


def company_name_parts(name: str) -> tuple[str, str]:
    words = tuple(word_tokens(name))
    for form, long_form in LEGAL_FORMS:
        for marker in (long_form, (form,)):
            if len(words) <= len(marker):
                continue
            if words[: len(marker)] == marker:
                return form, " ".join(words[len(marker) :])
            if words[-len(marker) :] == marker:
                return form, " ".join(words[: -len(marker)])
    return "", " ".join(words)


def _website_domain(supplier: SupplierRecord) -> str | None:
    host = supplier.website.host if supplier.website else None
    return canonical_domain(host) if host else None


def _official_domain(supplier: SupplierRecord) -> str | None:
    if supplier.official_domain is not None:
        return canonical_domain(supplier.official_domain)
    return None


def _resolve_groups(suppliers: tuple[SupplierRecord, ...]) -> list[list[SupplierRecord]]:
    owners = {item.id: item.id for item in suppliers}
    groups = {item.id: [item] for item in suppliers}
    names = {item.id: company_name_parts(item.name) for item in suppliers}

    def group_by(key: Callable[[SupplierRecord], str | None], *, check_names: bool = False) -> None:
        buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
        for item in suppliers:
            value = key(item)
            if value:
                buckets[(item.region_id, value)].append(item.id)
        for bucket in sorted(buckets):
            group_ids = sorted({owners[item_id] for item_id in buckets[bucket]})
            if len(group_ids) < 2:
                continue
            members = [item for group_id in group_ids for item in groups[group_id]]
            if len({item.inn for item in members if item.inn}) > 1:
                continue
            if check_names:
                domains = {host for item in members if (host := _website_domain(item))}
                forms = {names[item.id][0] for item in members} - {""}
                if len(domains) > 1 or len(forms) > 1:
                    continue
            owner_id = group_ids[0]
            for group_id in group_ids:
                del groups[group_id]
            groups[owner_id] = members
            for item in members:
                owners[item.id] = owner_id

    group_by(lambda item: item.inn)
    group_by(_official_domain)
    group_by(lambda item: " ".join(names[item.id]).strip(), check_names=True)
    group_by(
        lambda item: stem if " " in (stem := names[item.id][1]) else None,
        check_names=True,
    )
    return [groups[key] for key in sorted(groups)]


def _quality(supplier: SupplierRecord) -> tuple[int, int, str]:
    filled = sum(
        bool(value)
        for value in (
            supplier.contacts.email,
            supplier.contacts.phone,
            supplier.website,
            supplier.products,
            supplier.minimum_order,
            supplier.price,
            supplier.documents,
            supplier.delivery,
        )
    )
    return (-int(supplier.official_domain is not None), -filled, supplier.id)


def _merge(group: list[SupplierRecord]) -> SupplierRecord:
    records = sorted(group, key=_quality)
    preferred = records[0]
    data = preferred.model_dump()
    data["id"] = min(item.id for item in records)
    data["official_domain"] = _official_domain(preferred)
    for field in PREFERRED_FIELDS:
        data[field] = next(
            (value for item in records if (value := getattr(item, field)) is not None), None
        )
    data["contacts"] = {
        field: next(
            (value for item in records if (value := getattr(item.contacts, field)) is not None),
            None,
        )
        for field in CONTACT_FIELDS
    }
    for field in ("category_ids", "service_region_ids", "products", "keywords"):
        data[field] = tuple(
            dict.fromkeys(value for item in records for value in getattr(item, field))
        )

    sources = {}
    observations = {}
    evidence = {}
    for item in records:
        for source in item.sources:
            sources.setdefault(str(source.url), source)
        observations_for_item = item.observations or tuple(
            SourceObservation(provider="source-page", external_id=str(source.url), url=source.url)
            for source in item.sources
        )
        for observation in observations_for_item:
            observations.setdefault(
                (observation.provider, observation.external_id, str(observation.url)), observation
            )
        for fact in item.field_evidence:
            if fact.field in CONTACT_FIELDS and (
                getattr(item.contacts, fact.field) != data["contacts"][fact.field]
            ):
                continue
            if fact.field in (*PREFERRED_FIELDS, "name") and (
                getattr(item, fact.field) != data[fact.field]
            ):
                continue
            evidence.setdefault((fact.field, str(fact.url)), fact)
    data["sources"] = tuple(sources.values())
    data["observations"] = tuple(observations.values())
    data["field_evidence"] = tuple(evidence.values())
    return SupplierRecord.model_validate(data)


def resolve_suppliers(suppliers: tuple[SupplierRecord, ...]) -> tuple[SupplierRecord, ...]:
    """Resolve repeated observations while keeping ambiguous legal entities independent."""
    return tuple(_merge(group) for group in _resolve_groups(suppliers))


def preserve_supplier_ids(
    suppliers: tuple[SupplierRecord, ...],
    previous_sources: Iterable[tuple[str, str, str]],
) -> tuple[SupplierRecord, ...]:
    """Keep public IDs where exact source records establish one-to-one continuity."""
    previous_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    for provider, external_id, supplier_id in previous_sources:
        if provider != "source-page":
            previous_ids[provider, external_id].add(supplier_id)
    exact = {key: next(iter(ids)) for key, ids in previous_ids.items() if len(ids) == 1}

    candidates = {
        supplier.id: {
            previous_id
            for observation in supplier.observations
            if (previous_id := exact.get((observation.provider, observation.external_id)))
        }
        for supplier in suppliers
    }
    appearances = Counter(old_id for ids in candidates.values() for old_id in ids)
    reserved = set(candidates)
    stable = []
    for supplier in suppliers:
        neighbours = candidates[supplier.id]
        if len(neighbours) == 1:
            (old_id,) = neighbours
            if appearances[old_id] == 1 and old_id not in reserved:
                supplier = supplier.model_copy(update={"id": old_id})
        stable.append(supplier)
    return tuple(sorted(stable, key=lambda item: item.id))
