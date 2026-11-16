import csv
from datetime import date
from pathlib import Path

import pytest

from foodsource.ingest.fsa import (
    DATASET,
    DECLARATION_FIELDS,
    PROVIDER,
    archives_from_manifest,
    declaration_item,
    manufacturer_address,
    product_name,
    update_declarations,
)

JULY = date(2026, 7, 31)
AUGUST = date(2026, 8, 31)
TODAY = date(2026, 11, 16)


@pytest.fixture
def row() -> dict[str, str]:
    return {
        "id": "21702911",
        "Статус": "Действует",
        "Номер ДС": "ЕАЭС N RU Д-RU.РА01.В.01234/26",
        "Дата рег": "24.07.2026",
        "Срок действия": "03.08.2029",
        "Тех регламенты": "О безопасности пищевой продукции",
        "Тип изготовителя": "Юридическое лицо",
        "Изготовитель": 'ООО "Испытательный производитель"',
        "Адрес изготовителя": "180021  РОССИЯ  ПСКОВСКАЯ ОБЛАСТЬ  Г. ПСКОВ  УЛ. ИНЖЕНЕРНАЯ  Д. 9",
        "ИНН производителя": "7707083893",
        "Происхождение продукции": "Российская Федерация",
        "Полное наименование": "Полуфабрикаты куриные рубленые замороженные",
        "Заявитель": "Заявитель",
        "Адрес Заявителя": "Москва",
    }


def test_manifest_uses_latest_dated_archives_of_the_official_dataset():
    links = [
        f"{DATASET}data-20260630-structure-20260818.7z",
        f"{DATASET}data-20260831-structure-20260907.7z",
        f"{DATASET}data-20250228-structure-20250305.7z",
        f"{DATASET}data-20260731-structure-20260818.7z ",
        f"{DATASET}data-20261231-structure-20270107.7z",
        "https://files.example.com/opendata/7736638268-rds/data-20261031-structure-20261111.7z",
    ]
    parts = "".join(f"<dataversion><source>{url}</source></dataversion>" for url in links)
    selected = archives_from_manifest(f"<meta><data>{parts}</data></meta>".encode(), TODAY)
    assert [day for day, _ in selected] == [date(2026, 6, 30), JULY, AUGUST]


def test_uses_producer_geography_and_declared_product(row):
    item = declaration_item(row, JULY, TODAY)
    assert item is not None
    assert item["provider"] == PROVIDER
    assert item["city"] == "Г. ПСКОВ"
    assert item["region_label"] == "ПСКОВСКАЯ ОБЛАСТЬ"
    assert item["inn"] == row["ИНН производителя"]
    assert item["products"] == [row["Полное наименование"]]
    assert item["category_ids"] == ["food", "frozen"]
    assert item["observed_at"] == JULY
    assert item["source_url"] == "https://pub.fsa.gov.ru/rds/declaration/view/21702911/common"
    assert item["source_links"][0]["url"] == DATASET
    assert row["Номер ДС"] in item["documents"]
    assert "phone" not in item and "email" not in item


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("Статус", "Приостановлен"),
        ("Срок действия", "01.11.2026"),
        ("Тип изготовителя", "Индивидуальный предприниматель"),
        ("Происхождение продукции", "Республика Беларусь"),
        ("ИНН производителя", ""),
        ("Дата рег", "01.08.2026"),
    ],
)
def test_rejects_invalid_or_unconfirmed_manufacturers(row, key, value):
    assert declaration_item({**row, key: value}, JULY, TODAY) is None


def test_excludes_industrial_packaging_but_accepts_food_packaging(row):
    pack = {**row, "Тех регламенты": "О безопасности упаковки"}
    assert (
        declaration_item(
            {**pack, "Полное наименование": "Упаковка картонная промышленного назначения"},
            JULY,
            TODAY,
        )
        is None
    )
    item = declaration_item(
        {**pack, "Полное наименование": "Упаковка из гофрокартона для пищевой продукции"},
        JULY,
        TODAY,
    )
    assert item and item["category_ids"] == ["packaging"]


def test_recognizes_village_without_guessing_district_or_street():
    city, address, region = manufacturer_address(
        "188370  РОССИЯ  ОБЛАСТЬ ЛЕНИНГРАДСКАЯ  ГАТЧИНСКИЙ Р-Н  Д ВЕРОЛАНЦЫ  Д. 4Н"
    )
    assert (city, region) == ("ВЕРОЛАНЦЫ", "ЛЕНИНГРАДСКАЯ ОБЛАСТЬ")
    assert "ЛЕНИНГРАДСКАЯ ОБЛАСТЬ" in address
    assert manufacturer_address("г. Москва; г. Псков")[0] == ""
    assert manufacturer_address("Московская область, г.о. Химки, г. Химки")[0] == "г. Химки"


@pytest.mark.parametrize(
    ("label", "product"),
    [
        ("Вода питьевая. Упаковано в: стеклянные бутылки", "Вода питьевая"),
        ("Сыр твердый ГОСТ 12345", "Сыр твердый"),
        ("Упаковка картонная для пиццы, маркировка: «Молоко»", "Упаковка картонная для пиццы"),
    ],
)
def test_removes_packaging_standards_and_labels_from_product_name(label, product):
    assert product_name(label) == product


def test_newer_export_removes_suspended_declaration(tmp_path: Path, row):
    table = tmp_path / "data.csv"
    active = {}

    def update(values, day):
        with table.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(
                file, sorted(DECLARATION_FIELDS), delimiter=";", extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerow(values)
        update_declarations(table, active, day, TODAY)

    update(row, JULY)
    assert list(active) == [row["id"]]
    update({**row, "Статус": "Приостановлен"}, AUGUST)
    assert not active


def test_truncated_archive_row_cannot_remove_the_previous_declaration(tmp_path: Path, row):
    table = tmp_path / "data.csv"
    previous = declaration_item(row, JULY, TODAY)
    active = {row["id"]: previous}
    table.write_text(
        ";".join(["id", "Статус", *sorted(DECLARATION_FIELDS - {"id", "Статус"})])
        + f"\n{row['id']};Действует\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Malformed FSA declarations row"):
        update_declarations(table, active, AUGUST, TODAY)
    assert active == {row["id"]: previous}
