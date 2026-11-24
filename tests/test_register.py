from datetime import date

import pytest

from murraywater import config, register
from murraywater.register import SchemaError

MURRAY = "New South Wales Murray Regulated River Water Source"
UP = "That Part Of The Water Source Upstream Of The River Murray At Picnic Point"
DOWN = "That Part Of The Water Source Downstream Of The River Murray At Picnic Point"


def raw(from_ws=MURRAY, from_wmz=DOWN, from_cat="Regulated River (General Security)", to_ws=MURRAY, to_wmz=UP,
        to_cat="Regulated River (General Security)", when="23-SEP-2026", purpose="Standard Commercial", ml="100",
        price="330", app="SWC1"):
    return ["1", from_ws, from_wmz, from_cat, "", "2", to_ws, to_wmz, to_cat, "", app, when, purpose, ml, price]


def test_parse_count_from_recorded_search():
    html = (config.ROOT / "tests" / "fixtures" / "register_search_2026-27.html").read_text(encoding="utf-8")
    assert register.parse_count(html) == 664


def test_parse_count_empty_and_unexpected():
    assert register.parse_count("") == 0
    with pytest.raises(SchemaError):
        register.parse_count("<table><tr><td>changed page</td></tr></table>")


def test_parse_export_recorded_file(fixture_bytes):
    rows = register.parse_export(fixture_bytes("register_2026-27_to_2026-09-23.xls"))
    assert len(rows) == 664
    assert all(len(r) == 15 for r in rows)
    assert rows[0][10] == "SWC623398" and rows[0][11] == "23-SEP-2026" and rows[0][14] == "334.4"


def test_parse_export_rejects_other_files():
    with pytest.raises(SchemaError):
        register.parse_export(b"<html>not a spreadsheet</html>")


def test_classify_zone_side_direction():
    within = register.classify(raw())
    assert (within["nswm_side"], within["zone"], within["direction"]) == ("seller", "Z11", "within")
    out = register.classify(raw(to_ws="Victoria Water Source", to_wmz=" ", to_cat="Interstate Transfer"))
    assert (out["zone"], out["direction"]) == ("Z11", "out")
    into = register.classify(raw(from_ws="Victoria Water Source", from_wmz=" ", from_cat="Interstate Transfer", to_wmz=UP))
    assert (into["nswm_side"], into["zone"], into["direction"]) == ("buyer", "Z10", "in")
    assert within["season"] == "2026-27" and within["trade_date"] == "2026-09-23"


def test_commercial_rule_both_eras():
    assert register.classify(raw())["is_commercial"] == 1
    assert register.classify(raw(price="0"))["is_commercial"] == 0
    assert register.classify(raw(purpose="Related Party"))["is_commercial"] == 0
    assert register.classify(raw(purpose="Private Lease (Not 71m)"))["is_commercial"] == 0
    assert register.classify(raw(purpose="", when="15-MAR-2013"))["is_commercial"] == 1  # before purpose was recorded
    assert register.classify(raw(purpose="", price="null"))["is_commercial"] == 0


def test_supplementary_flag_uses_nswm_side():
    assert register.classify(raw(from_cat="Supplementary Water"))["is_supp"] == 1
    assert register.classify(raw(to_cat="Supplementary Water"))["is_supp"] == 0  # seller side decides


def test_null_numbers_and_bad_rows():
    row = register.classify(raw(ml="null", price="null"))
    assert row["ml"] == 0.0 and row["price"] == 0.0
    with pytest.raises(SchemaError):
        register.classify(raw(price="abc"))
    with pytest.raises(SchemaError):
        register.classify(raw(from_ws="Victoria Water Source", to_ws="South Australia Water Source"))


def test_fetch_range_chunks_exports(monkeypatch, fake_client, fixture_bytes):
    monkeypatch.setattr(config, "REGISTER_EXPORT_MAX", 300)
    search = (config.ROOT / "tests" / "fixtures" / "register_search_2026-27.html").read_bytes()
    client = fake_client(lambda url, form: search if form else fixture_bytes("register_2026-27_to_2026-09-23.xls"))
    expected, rows = register.fetch_range(client, date(2026, 7, 1), date(2027, 6, 30))
    assert expected == 664
    exports = [url for url, form in client.calls if form is None]
    assert [u.split("fromRow=")[1].split("&")[0] for u in exports] == ["0", "300", "600"]
    assert exports[-1].count("toRow=663") == 1
    assert "startDate=01-JUL-2026" in exports[0] and "src=11904" in exports[0]
    assert len(rows) == 3 * 664  # the fake returns the whole file per chunk; the caller's count check catches this
