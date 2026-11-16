import argparse
import json
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from .domain import canonical_domain
from .repository import DEFAULT_DB_PATH, load_catalog_revision
from .search import SearchMatch, SupplierSearch

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "evaluation" / "search_cases.json"


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Label(EvaluationModel):
    domain: str
    evidence: HttpUrl
    rationale: str


class Case(EvaluationModel):
    id: str
    query: Annotated[str, Field(min_length=1, max_length=120)]
    category: str = ""
    region: str = ""
    relevant_any: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    empty: bool = False

    @model_validator(mode="after")
    def check_expectations(self) -> Self:
        if (
            not (self.relevant_any or self.excluded or self.empty)
            or (self.empty and self.relevant_any)
            or set(self.relevant_any).intersection(self.excluded)
        ):
            raise ValueError(f"Inconsistent evaluation expectations for {self.id}")
        return self


class Corpus(EvaluationModel):
    labels: dict[str, Label]
    cases: Annotated[tuple[Case, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def check_references(self) -> Self:
        for case in self.cases:
            unknown = set((*case.relevant_any, *case.excluded)) - self.labels.keys()
            if unknown:
                raise ValueError(f"Unknown supplier labels in {case.id}: {sorted(unknown)}")
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate evaluation case IDs")
        return self


def result_domain(match: SearchMatch) -> str | None:
    supplier = match.supplier
    host = supplier.official_domain or (supplier.website.host if supplier.website else None)
    return canonical_domain(host) if host else None


def evaluate(search: SupplierSearch, corpus: Corpus, k: int) -> dict:
    rows = []
    for case in corpus.cases:
        results = search.search(case.query, case.category, case.region)
        top_results = results[:k]
        domains = {result_domain(match) for match in results}
        top_domains = {result_domain(match) for match in top_results}
        positives = [key for key in case.relevant_any if corpus.labels[key].domain in top_domains]
        negatives = [key for key in case.excluded if corpus.labels[key].domain in domains]
        passed = (not case.relevant_any or bool(positives)) and not negatives
        rows.append(
            {
                "id": case.id,
                "query": case.query,
                "passed": passed and (not case.empty or not results),
                "total": len(results),
                "expected_positive": bool(case.relevant_any),
                "relevant_found": positives,
                "excluded_found": negatives,
                "top": [
                    {"name": match.supplier.name, "domain": result_domain(match)}
                    for match in top_results
                ],
            }
        )
    return {
        "k": k,
        "passed": sum(row["passed"] for row in rows),
        "case_count": len(rows),
        "known_positive_cases": sum(row["expected_positive"] for row in rows),
        "known_positive_hits": sum(bool(row["relevant_found"]) for row in rows),
        "cases_with_known_false_positives": sum(bool(row["excluded_found"]) for row in rows),
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate labelled search cases against a SQLite snapshot"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--k", type=int, choices=range(1, 21), default=5)
    parser.add_argument("--lexical-only", action="store_true", help="Use the lexical baseline")
    parser.add_argument("--strict", action="store_true", help="Fail if any labelled case fails")
    args = parser.parse_args()

    corpus = Corpus.model_validate_json(args.cases.read_text(encoding="utf-8-sig"))
    publication = load_catalog_revision(args.db)
    if publication is None:
        raise RuntimeError("No published snapshot")
    snapshot = publication.snapshot
    report = evaluate(
        SupplierSearch(snapshot, use_product_vocabulary=not args.lexical_only), corpus, args.k
    )
    report.update(
        catalog_revision=publication.revision,
        catalog_date=snapshot.updated_at.isoformat(),
        catalog_size=len(snapshot.suppliers),
        mode="lexical" if args.lexical_only else "product-vocabulary",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(args.strict and report["passed"] != report["case_count"])


if __name__ == "__main__":
    raise SystemExit(main())
