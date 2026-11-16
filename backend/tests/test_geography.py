from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from foodsource.ingest.geography import (
    Geography,
    _build_geography,
    _download_if_missing,
    load_geography,
)


def place(id_: int, label: str, code: str, feature: str = "PPL") -> str:
    return "\t".join(
        [str(id_), label, label, "", "0", "0", "P", feature, "RU", "", code] + [""] * 8
    )


ADMIN = [
    "RU.71\tSverdlovsk Oblast\tSverdlovsk Oblast\t1490542",
    "RU.48\tMoscow\tMoscow\t524894",
    "RU.77\tTver Oblast\tTver Oblast\t480041",
    "RU.13\tChelyabinsk Oblast\tChelyabinsk Oblast\t1508290",
    "RU.47\tMoscow Oblast\tMoscow Oblast\t524925",
    "RU.49\tMurmansk Oblast\tMurmansk Oblast\t524304",
]
PLACES = [
    place(1486209, "Yekaterinburg", "71", "PPLA"),
    place(524901, "Moscow", "48", "PPLC"),
    place(10, "Moskva", "77"),
    place(481608, "Troitsk", "48"),
    place(1489246, "Troitsk", "13", "PPLA2"),
    place(562319, "Fryazino", "47", "PPLA2"),
    place(562320, "Fryazino", "47"),
    place(70, "Abandoned", "71", "PPLQ"),
    place(524305, "Murmansk", "49", "PPLA"),
]
ALIASES = [
    "1\t1486209\tru\tЕкатеринбург\t1\t\t\t",
    "2\t1486209\tru\tИсторическое имя\t\t\t\t1",
    "3\t1486209\ten\tEkaterinburg\t\t\t\t",
    "4\t1490542\tru\tСвердловская Область\t1\t\t\t",
    "5\t524894\tru\tМосква\t1\t\t\t",
    "6\t524901\tru\tМосква\t1\t\t\t",
    "7\t10\tru\tМосква\t1\t\t\t",
    "8\t481608\tru\tТроицк\t1\t\t\t",
    "9\t1489246\tru\tТроицк\t1\t\t\t",
    "10\t1508290\tru\tЧелябинская область\t1\t\t\t",
    "11\t480041\tru\tТверская область\t1\t\t\t",
    "12\t524925\tru\tМосковская область\t1\t\t\t",
    "13\t562319\tru\tФрязино\t1\t\t\t",
    "14\t562320\tru\tФрязино\t1\t\t\t",
    "15\t1508290\tru\tЮжный Урал\t1\t1\t\t",
    "16\t524305\tru\tМурманск\t1\t1\t1\t",
    "17\t524305\tru\tстолица Заполярья\t\t\t1\t",
]


@pytest.fixture
def geography() -> Geography:
    return _build_geography(PLACES, ADMIN, ALIASES)


def test_exact_aliases_share_canonical_city_and_keep_existing_slug(geography: Geography) -> None:
    expected = (
        {"id": "sverdlovsk-region", "label": "Свердловская область", "parent_id": "ru"},
        {"id": "ekaterinburg", "label": "Екатеринбург", "parent_id": "sverdlovsk-region"},
    )
    assert geography.resolve("г.ЕКАТЕРИНБУРГ") == geography.resolve("Ekaterinburg") == expected
    assert geography.resolve("Екатерин") == ()
    assert geography.resolve("Историческое имя") == ()
    assert geography.resolve("Abandoned") == ()


def test_ambiguous_city_needs_address_or_region_disambiguation(geography: Geography) -> None:
    assert geography.resolve("Троицк") == ()
    chelyabinsk = geography.resolve("Троицк", region_label="Челябинская обл.")
    assert chelyabinsk[0]["label"] == "Челябинская область"
    assert chelyabinsk[-1]["id"] == "city-1489246"
    assert geography.resolve("", "456300, Челябинская обл., г. Троицк") == chelyabinsk
    assert geography.resolve("Троицк", "Москва, г. Троицк")[-1]["id"] == "city-481608"
    assert geography.resolve("Екатеринбург", region_label="Челябинская область") == ()


def test_federal_city_is_both_city_and_subject(geography: Geography) -> None:
    assert geography.resolve("Москва") == (
        {"id": "city-524901", "label": "Москва", "parent_id": "ru"},
    )
    assert geography.resolve("Москва", region_label="Тверская область")[-1]["id"] == "city-10"


def test_prefers_the_russian_name_when_geonames_also_marks_it_colloquial(
    geography: Geography,
) -> None:
    city = geography.resolve("Murmansk")[-1]
    assert city == {"id": "city-524305", "label": "Мурманск", "parent_id": "region-524304"}


def test_explicit_city_in_known_region_resolves_municipal_centre(geography: Geography) -> None:
    assert geography.resolve("Фрязино", region_label="Московская область") == ()
    assert (
        geography.resolve(
            "Фрязино", "Россия, 141190, Московская обл., г. Фрязино, Окружной проезд"
        )[-1]["id"]
        == "city-562319"
    )
    assert geography.resolve("г. Фрязино, Московская область")[-1]["id"] == "city-562319"
    assert geography.resolve("г. Троицк") == ()


def test_load_geography_reuses_cached_country_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with ZipFile(tmp_path / "ru-places.zip", "w") as zip_file:
        zip_file.writestr("RU.txt", "\n".join(PLACES))
    with ZipFile(tmp_path / "ru-aliases.zip", "w") as zip_file:
        zip_file.writestr("RU.txt", "\n".join(ALIASES))
    (tmp_path / "admin1CodesASCII.txt").write_text("\n".join(ADMIN), encoding="utf-8")

    def unwanted_network(*_args, **_kwargs):
        pytest.fail("A cached extract should not trigger another download")

    monkeypatch.setattr("foodsource.ingest.geography.urlopen", unwanted_network)
    assert load_geography(tmp_path).resolve("Екатеринбург")[-1]["id"] == "ekaterinburg"


def test_does_not_cache_html_and_refreshes_a_broken_cached_extract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "admin1CodesASCII.txt"
    source = "https://example.com/admin1CodesASCII.txt"
    monkeypatch.setattr(
        "foodsource.ingest.geography.urlopen", lambda *_args, **_kwargs: BytesIO(b"<html>")
    )
    with pytest.raises(ValueError, match="Invalid GeoNames administrative extract"):
        _download_if_missing(path, source)
    assert not path.exists()

    path.write_bytes(b"<html>")
    valid = "\n".join(ADMIN).encode()
    monkeypatch.setattr(
        "foodsource.ingest.geography.urlopen", lambda *_args, **_kwargs: BytesIO(valid)
    )
    _download_if_missing(path, source)
    assert path.read_bytes() == valid
