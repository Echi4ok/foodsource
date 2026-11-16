import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

VOCABULARY_PATH = Path(__file__).with_name("search_vocabulary.json")


@dataclass(frozen=True)
class ProductConcept:
    token: str
    categories: frozenset[str]


class SearchVocabulary:
    def __init__(self, lemmatize: Callable[[str], tuple[str, ...]]) -> None:
        self._query_forms: dict[tuple[str, ...], ProductConcept] = {}
        self._catalog_forms: dict[tuple[str, ...], ProductConcept] = {}
        self._concepts: dict[str, ProductConcept] = {}
        for entry in json.loads(VOCABULARY_PATH.read_text(encoding="utf-8")):
            token = f"concept:{entry['id']}"
            concept = ProductConcept(token, frozenset(entry["categories"]))
            if token in self._concepts or not concept.categories:
                raise ValueError(f"Invalid search concept: {token}")
            self._concepts[token] = concept
            aliases = set(entry["phrases"])
            for phrase in sorted(aliases | set(entry.get("catalog_phrases", ()))):
                form = tuple(sorted(lemmatize(phrase)))
                if len(form) < 2 or self._catalog_forms.setdefault(form, concept) != concept:
                    raise ValueError(f"Invalid or ambiguous search phrase: {phrase}")
                if phrase in aliases:
                    self._query_forms[form] = concept
        self._widths = sorted({len(form) for form in self._catalog_forms}, reverse=True)

    def normalize(
        self,
        lemmas: tuple[str, ...],
        categories: frozenset[str] | None = None,
        *,
        start_before: int | None = None,
    ) -> frozenset[str]:
        forms = self._query_forms if categories is None else self._catalog_forms
        terms: set[str] = set()
        offset = 0
        while offset < len(lemmas) and (start_before is None or offset < start_before):
            for width in self._widths:
                if offset + width > len(lemmas):
                    continue
                form = tuple(sorted(lemmas[offset : offset + width]))
                concept = forms.get(form)
                if concept and (categories is None or categories & concept.categories):
                    terms.add(concept.token)
                    offset += width
                    break
            else:
                terms.add(lemmas[offset])
                offset += 1
        return frozenset(terms)

    def allows_category(self, terms: frozenset[str], category: str) -> bool:
        return all(
            category in concept.categories
            for term in terms
            if (concept := self._concepts.get(term)) is not None
        )
