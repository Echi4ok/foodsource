import re
import unicodedata

WORD = re.compile(r"[^\W_]+")
CLAUSE_BOUNDARY = re.compile(
    r"[.!?;]\s+|\n+|(?:,\s+(?:(?:а\s+)?также\s+)?|\s+и\s+)"
    r"(?=(?:мы\s+)?(?:производим|производит|поставля\w+|прода\w+|выпуска\w+|предлага\w+|изготавлива\w+)\b)"
)
APPLICATION = re.compile(
    r"\b(?:для|по\s+заказу|предназначен\w*\s+для|"
    r"в\s+(?:(?:бумажн\w+|пищев\w+|пластиков\w+)\s+)?упаков\w*)\b"
)


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")


def word_tokens(value: str) -> list[str]:
    return WORD.findall(normalize_text(value))


def offer_clauses(text: str) -> list[str]:
    return CLAUSE_BOUNDARY.split(normalize_text(text))


def offer_subject(clause: str) -> str:
    return APPLICATION.split(clause, maxsplit=1)[0]
