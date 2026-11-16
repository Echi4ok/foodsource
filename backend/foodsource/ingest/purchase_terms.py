import re
from collections.abc import Iterable, Iterator
from typing import Any

from scrapy import Selector
from scrapy.http import HtmlResponse

from .enrich import structured_nodes
from .normalize import clean

ORDER_TERM = (
    r"минимальн(?:ый (?:заказ|объем (?:заказа|партии))|"
    r"ая (?:партия|сумма заказа|стоимость заказа)|ое количество (?:товара|для заказа))"
)
MINIMUM_QUESTION = re.compile(
    r"^(?:(?:какой|какая|какое|есть ли) (?:у вас )?)?"
    rf"{ORDER_TERM}(?: (?:для заказа|при заказе))?[\s?!.:]*$"
)
NON_MINIMUM = re.compile(
    r"\b(?:доставк\w*|самовывоз\w*|скидк\w*|не более|до \d+|зависит)\b"
    r"|\d\s*[-\u2013\u2014]\s*\d"
)
ANSWER_PREFIX = re.compile(r"(?:от|не менее|минимум|заказ от)?")
EXPLICIT_MINIMUM = re.compile(rf"\b{ORDER_TERM}\b")
QUANTITY = re.compile(
    r"(?<![\w.,-])(?P<amount>\d+(?: \d{3})*(?:[,.]\d+)?)\s*"
    r"(?:шт(?:ук(?:а|и)?)?|кг|килограмм(?:а|ов)?|грамм(?:а|ов)?|короб(?:ка|ки|ок)|"
    r"ящик(?:а|ов)?|упаков(?:ка|ки|ок)|пач(?:ка|ки|ек)|палл?ет(?:а|ы)?|"
    r"л|литр(?:а|ов)?|тонн(?:а|ы)?|руб(?:ль|ля|лей)?|₽)(?!\w)"
)
VISIBLE_TEXT = ".//text()[not(ancestor::script or ancestor::style or ancestor::noscript)]"


def schema_type(node: dict[str, Any], name: str) -> bool:
    kinds = node.get("@type")
    return any(
        isinstance(kind, str) and kind.rsplit("/", 1)[-1] == name
        for kind in (kinds if isinstance(kinds, list) else [kinds])
    )


def element_text(elements: Iterable[Selector]) -> str:
    text = clean(
        " ".join(value for element in elements for value in element.xpath(VISIBLE_TEXT).getall())
    )
    return re.sub(r" +([.,;:!?])", r"\1", text)


def faq_answers(
    response: HtmlResponse, nodes: Iterable[dict[str, Any]]
) -> Iterator[tuple[str, str]]:
    for node in nodes:
        if not schema_type(node, "FAQPage"):
            continue
        for question in structured_nodes(node.get("mainEntity")):
            answer = question.get("acceptedAnswer")
            if schema_type(question, "Question") and isinstance(answer, dict):
                text = answer.get("text")
                if isinstance(text, str):
                    yield clean(question.get("name")), element_text([Selector(text=text)])

    for questions, answers in (
        ("//details/summary[1]", "following-sibling::*[not(self::summary)]"),
        ("//dl/dt", "following-sibling::*[1][self::dd]"),
        ("//*[@data-role='item.button']", "following-sibling::*[1][@data-role='item.content']"),
    ):
        for question in response.xpath(questions):
            yield element_text([question]), element_text(question.xpath(answers))


def minimum_order(response: HtmlResponse, nodes: Iterable[dict[str, Any]]) -> str | None:
    values: dict[str, str] = {}
    for question, answer in faq_answers(response, nodes):
        if not MINIMUM_QUESTION.fullmatch(question.casefold().replace("ё", "е")):
            continue
        key = answer.casefold().replace("ё", "е").rstrip(".! ")
        if not key or len(answer) > 240 or NON_MINIMUM.search(key):
            continue
        amounts = list(QUANTITY.finditer(key))
        if len(amounts) != 1 or not any(digit in "123456789" for digit in amounts[0]["amount"]):
            continue
        prefix = key[: amounts[0].start()].strip()
        if ANSWER_PREFIX.fullmatch(prefix) or EXPLICIT_MINIMUM.search(prefix):
            values.setdefault(key, answer)
    return next(iter(values.values())) if len(values) == 1 else None
