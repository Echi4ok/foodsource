import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pymorphy3 import MorphAnalyzer

from .domain import CatalogSnapshot, MatchReason, SupplierRecord, region_lineages
from .identity import company_id
from .search_vocabulary import SearchVocabulary
from .text import normalize_text, offer_clauses, offer_subject, word_tokens

RUSSIAN_WORD = re.compile(r"[а-я]+")
FUNCTION_POS = frozenset({"PREP", "CONJ", "PRCL"})
ASSORTMENT_LABEL = "ассортименту"
ASSORTMENT_WEIGHT = 8

type OfferContext = tuple[frozenset[str], frozenset[str]]


@dataclass(frozen=True)
class Terms:
    words: frozenset[str]
    lemmas: frozenset[str]


class RussianTokenizer:
    """Lemmatize Russian content words and keep exact tokens for ranking."""

    def __init__(self) -> None:
        self._morph = MorphAnalyzer(lang="ru")
        self._cached_lemma = lru_cache(maxsize=32_768)(self._lemma)

    def __call__(self, texts: tuple[str, ...]) -> Terms:
        words: set[str] = set()
        lemmas: set[str] = set()
        tokens = {word for text in texts for word in word_tokens(text)}
        for token in tokens:
            lemma = self._cached_lemma(token)
            if lemma is not None:
                words.add(token)
                lemmas.add(lemma)
        return Terms(frozenset(words), frozenset(lemmas))

    def _lemma(self, token: str) -> str | None:
        if not RUSSIAN_WORD.fullmatch(token):
            return token
        parse = self._morph.parse(token)[0]
        return None if parse.tag.POS in FUNCTION_POS else normalize_text(parse.normal_form)

    def sequence(self, text: str) -> tuple[str, ...]:
        return tuple(
            lemma for word in word_tokens(text) if (lemma := self._cached_lemma(word)) is not None
        )


@dataclass(frozen=True)
class SearchField:
    label: str
    weight: int
    terms: Terms
    concepts: frozenset[str] = frozenset()


@dataclass(frozen=True)
class IndexedSupplier:
    supplier: SupplierRecord
    fields: tuple[SearchField, ...]
    offers: tuple[OfferContext, ...]


@dataclass(frozen=True)
class SearchMatch:
    supplier: SupplierRecord
    reasons: tuple[MatchReason, ...]


class UnknownFilter(ValueError):
    def __init__(self, field: Literal["category", "region"]) -> None:
        self.field = field
        super().__init__(
            "Неизвестная категория." if field == "category" else "Неизвестный город или регион."
        )


class SupplierSearch:
    def __init__(self, snapshot: CatalogSnapshot, *, use_product_vocabulary: bool = True) -> None:
        self.snapshot = snapshot
        self.company_count = len({company_id(supplier) for supplier in snapshot.suppliers})
        self._categories = {category.id: category.label for category in snapshot.categories}
        self._regions = {region.id: region.label for region in snapshot.regions}
        self._lineages = region_lineages(snapshot.regions)
        self._tokenize = RussianTokenizer()
        self._vocabulary = (
            SearchVocabulary(self._tokenize.sequence) if use_product_vocabulary else None
        )
        tokenize = self._tokenize
        self._index = tuple(
            IndexedSupplier(
                supplier,
                (
                    self._search_field("названию", 10, (supplier.name,), supplier),
                    self._search_field(
                        ASSORTMENT_LABEL,
                        ASSORTMENT_WEIGHT,
                        supplier.products,
                        supplier,
                        scope_offer=True,
                    ),
                    self._search_field("поисковым терминам", 6, supplier.keywords, supplier),
                    self._search_field(
                        "описанию",
                        4,
                        tuple(offer_clauses(supplier.description)),
                        supplier,
                        scope_offer=True,
                    ),
                    SearchField(
                        "категории",
                        2,
                        tokenize(tuple(self._categories[id_] for id_ in supplier.category_ids)),
                    ),
                ),
                tuple(
                    context
                    for product in supplier.products
                    for clause in offer_clauses(product)
                    for phrase in clause.split(":")
                    if (context := self._offer_context(phrase)) is not None
                ),
            )
            for supplier in snapshot.suppliers
        )

    def _search_field(
        self,
        label: str,
        weight: int,
        values: tuple[str, ...],
        supplier: SupplierRecord,
        *,
        scope_offer: bool = False,
    ) -> SearchField:
        if scope_offer:
            values = tuple(map(normalize_text, values))
        subjects = tuple(map(offer_subject, values)) if scope_offer else values
        terms = self._tokenize(subjects)
        if self._vocabulary is None:
            return SearchField(label, weight, terms)
        categories = frozenset(supplier.category_ids)
        concepts = (
            frozenset().union(
                *(
                    self._vocabulary.normalize(
                        self._tokenize.sequence(value),
                        categories,
                        start_before=(
                            len(self._tokenize.sequence(subject)) if subject != value else None
                        ),
                    )
                    for value, subject in zip(values, subjects, strict=True)
                )
            )
            - terms.lemmas
        )
        return SearchField(label, weight, terms, concepts)

    def _offer_context(self, text: str) -> OfferContext | None:
        text = normalize_text(text)
        subject = offer_subject(text)
        if subject == text:
            return None
        head = self._tokenize((subject,)).lemmas
        purpose = self._tokenize((text[len(subject) :],)).lemmas
        return (head, purpose) if head and purpose else None

    def search(
        self, query: str = "", category_id: str = "", region_id: str = ""
    ) -> tuple[SearchMatch, ...]:
        if category_id and category_id not in self._categories:
            raise UnknownFilter("category")
        if region_id and region_id not in self._regions:
            raise UnknownFilter("region")

        query = query.strip()
        query_terms = self._tokenize((query,))
        if query and not query_terms.words:
            return ()
        normalized_terms = query_terms.lemmas
        if self._vocabulary is not None:
            normalized_terms = self._vocabulary.normalize(self._tokenize.sequence(query))
            if category_id and not self._vocabulary.allows_category(normalized_terms, category_id):
                return ()

        query_offer = self._offer_context(query)
        ranked: list[tuple[tuple[int, int, int, str, str], SearchMatch]] = []
        for indexed in self._index:
            supplier = indexed.supplier
            if category_id and category_id not in supplier.category_ids:
                continue
            place = self._match_region(supplier, region_id) if region_id else None
            if region_id and place is None:
                continue
            text_match = self._match_terms(indexed.fields, normalized_terms, query_terms.words)
            if text_match is None and query_offer is not None:
                head, purpose = query_offer
                if any(head <= offered and purpose <= usage for offered, usage in indexed.offers):
                    text_match = (
                        ASSORTMENT_WEIGHT * len(query_terms.lemmas),
                        MatchReason(code="query", label=f"Совпадение по {ASSORTMENT_LABEL}"),
                    )
            if text_match is None:
                continue

            text_score, text_reason = text_match
            reasons: list[MatchReason] = []
            if text_reason:
                reasons.append(text_reason)
            if category_id:
                reasons.append(MatchReason(code="category", label=self._categories[category_id]))
            if place:
                reasons.append(place)
            location_priority = int(place is not None and place.code == "location")
            has_contact = bool(
                supplier.contacts.phone or supplier.contacts.email or supplier.website
            )
            sort_key = (
                -location_priority,
                -text_score,
                -int(has_contact),
                normalize_text(supplier.name),
                supplier.id,
            )
            ranked.append((sort_key, SearchMatch(supplier, tuple(reasons))))

        ranked.sort(key=lambda result: result[0])
        companies: dict[str, SearchMatch] = {}
        for _, match in ranked:
            companies.setdefault(company_id(match.supplier), match)
        return tuple(companies.values())

    def _match_region(self, supplier: SupplierRecord, requested_id: str) -> MatchReason | None:
        if requested_id in self._lineages[supplier.region_id]:
            return MatchReason(
                code="location", label=f"Местоположение · {self._regions[supplier.region_id]}"
            )

        for service_id in supplier.service_region_ids:
            if (
                requested_id in self._lineages[service_id]
                or service_id in self._lineages[requested_id]
            ):
                return MatchReason(
                    code="delivery", label=f"Зона доставки · {self._regions[service_id]}"
                )
        return None

    @staticmethod
    def _match_terms(
        fields: tuple[SearchField, ...], lemmas: frozenset[str], exact_words: frozenset[str]
    ) -> tuple[int, MatchReason | None] | None:
        if not lemmas:
            return 0, None

        score = 0
        labels: set[str] = set()
        for lemma in lemmas:
            field = next(
                (
                    field
                    for field in fields
                    if lemma in field.terms.lemmas or lemma in field.concepts
                ),
                None,
            )
            if field is None:
                return None
            labels.add(field.label)
            score += field.weight
        score += sum(any(word in field.terms.words for field in fields) for word in exact_words)

        matched_fields = ", ".join(field.label for field in fields if field.label in labels)
        return score, MatchReason(code="query", label=f"Совпадение по {matched_fields}")
