import re

from foodsource.text import offer_clauses, offer_subject

COMMERCE = re.compile(
    r"\b(?:опт\w*|постав\w+|производ\w+|horeca|дистрибьют\w+|тираж\w*)\b",
    re.IGNORECASE,
)
B2B_OFFER = re.compile(
    r"\b(?:оптом|оптов\w+\s+(?:поставк\w+|продаж\w+|торговл\w+)|"
    r"поставщик|производитель|поставляем|производим|продаем\s+оптом)\b"
)
NEGATED_OFFER = re.compile(r"\bне\s+(?:поставля\w+|производ\w+|прода\w+)\b")
WHOLESALE_FOOD_RULES = tuple(
    (product, re.compile(pattern))
    for product, pattern in (
        ("Хлеб", r"\bхлеб(?:а|у|ом|е)?\b"),
        ("Хлебобулочные изделия", r"\bхлебобулочн\w+\s+издели\w+\b"),
        ("Кондитерские изделия", r"\bкондитерск\w+\s+издели\w+\b"),
        ("Молоко", r"\bмолок(?:о|а|у|ом|е)\b"),
        ("Молочная продукция", r"\bмолочн\w+\s+продукци\w+\b"),
        ("Сыр", r"\bсыр(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b"),
        ("Сливочное масло", r"\bсливочн\w+\s+масл\w+\b"),
        ("Мясная продукция", r"\bмясн\w+\s+продукци\w+\b"),
        ("Мясо", r"\bмяс(?:о|а|у|ом|е)\b"),
        ("Рыба", r"\bрыб(?:а|ы|у|ой|е)\b"),
        ("Морепродукты", r"\bморепродукт\w+\b"),
        ("Растительное масло", r"\bрастительн\w+\s+масл\w+\b"),
        ("Крупы", r"\bкруп(?:ы|а|ой|у|ами|ах)?\b"),
        ("Макаронные изделия", r"\bмакаронн\w+\s+издели\w+\b"),
        ("Овощи", r"\bовощ(?:и|ей|ами|ам|ах)\b"),
        ("Фрукты", r"\bфрукт(?:ы|ов|ами|ам|ах)\b"),
    )
)
PRODUCT_RULES = (
    (
        "packaging",
        "Пищевая упаковка",
        r"пищев\w+\s+(?:упаковк\w+|тар\w+|контейнер\w+)|"
        r"(?:упаковк\w+|контейнер\w+|тар\w+)\s+для\s+(?:ед\w*|пищев\w+|продукт\w+\s+питания|"
        r"готовых\s+блюд|кулинар\w+|выпечк\w+|зелени|салат\w*|кондитер\w+)",
    ),
    ("packaging", "Коробки для пиццы", r"короб\w+\s+для\s+пицц\w*"),
    (
        "packaging",
        "Контейнеры для готовых блюд",
        r"\bконтейнер\w*\s+для\s+(?:готов\w+\s+блюд\w*|обед\w*|еды\b)",
    ),
    ("packaging", "Бумажные стаканы", r"бумажн\w+\s+стакан\w*|стакан\w*\s+бумажн\w+"),
    ("packaging", "Одноразовая посуда", r"одноразов\w+\s+посуд\w+"),
    ("ingredients", "Пищевые ингредиенты", r"пищев\w+\s+(?:ингредиент|добавк)\w*"),
    (
        "ingredients",
        "Хлебопекарные смеси",
        r"хлебопекарн\w+\s+смес\w*|смес\w*\s+для\s+хлебопечени\w*",
    ),
    (
        "ingredients",
        "Кондитерские ингредиенты",
        r"(?:кондитерск\w+\s+ингредиент\w*|ингредиент\w*\s+для\s+кондитерск\w+)",
    ),
    ("ingredients", "Дрожжи и закваски", r"дрожж\w*\s+и\s+закваск\w*|закваск\w*\s+и\s+дрожж\w*"),
    ("frozen", "Замороженная выпечка", r"(?:глубоко)?замороженн\w+\s+выпечк\w*"),
    (
        "frozen",
        "Замороженные полуфабрикаты",
        r"(?:быстро)?замороженн\w+\s+(?:(?:мясн\w+|хлебобулочн\w+)\s+)?полуфабрикат\w*",
    ),
    ("frozen", "Замороженные продукты", r"(?:быстро)?замороженн\w+\s+продукт\w*"),
)
_PRODUCT_RULES = tuple(
    (category, product, re.compile(pattern, re.IGNORECASE))
    for category, product, pattern in PRODUCT_RULES
)


def qualifying_products(text: str) -> dict[str, set[str]]:
    if not COMMERCE.search(text):
        return {}
    clauses = [(clause, len(offer_subject(clause))) for clause in offer_clauses(text)]
    facts: dict[str, set[str]] = {}
    for category, product, pattern in _PRODUCT_RULES:
        if any(
            (match := pattern.search(clause)) and match.start() < length
            for clause, length in clauses
        ):
            facts.setdefault(category, set()).add(product)
    return facts


def qualifying_wholesale_food(text: str) -> set[str]:
    subjects = [offer_subject(clause) for clause in offer_clauses(text)]
    return {
        product
        for subject in subjects
        if B2B_OFFER.search(subject) and not NEGATED_OFFER.search(subject)
        for product, pattern in WHOLESALE_FOOD_RULES
        if pattern.search(subject)
    }
