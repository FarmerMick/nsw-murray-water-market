"""Permanent (entitlement) trades, the new price measures and the public web page."""

import json
import re
from datetime import date, datetime

import pytest

from murraywater import config, metrics, permanent, render, store
from test_metrics_render import seed

AT = "2026-11-24T19:00:00+11:00"


def test_parse_share_assignment_export(fixture_bytes):
    raw = permanent.parse_export("71Q", fixture_bytes("permanent_71Q_2025-26.xls"))
    assert len(raw) == 177  # the search for the same period reported 177
    rows = permanent.classify_all("71Q", raw)
    first = rows[0]
    assert (first["trade_date"], first["cat_code"], first["units"], first["price"]) == ("2025-07-01", "HS", 20.0, 10550.0)
    assert first["application_no"] == "1027569" and first["season"] == "2025-26" and first["is_sale"] == 1
    assert {r["cat_code"] for r in rows} == {"GS", "HS", "other"}  # one supplementary row
    assert not any(r["is_sale"] for r in rows if r["cat_code"] == "other")


def test_parse_transfer_export(fixture_bytes):
    raw = permanent.parse_export("71M", fixture_bytes("permanent_71M_2025-26.xls"))
    assert len(raw) == 144
    rows = permanent.classify_all("71M", raw)
    assert rows[0]["price"] == 0.0 and rows[0]["is_sale"] == 0  # a $0 transfer isn't a sale
    assert all(r["application_no"] == "" for r in rows)
    assert len({r["row_hash"] for r in rows}) == len(rows)


def test_identical_transfer_rows_kept_apart():
    row = ["4641", "New South Wales Murray Regulated River Water Source", "Regulated River (General Security)",
           "01-JUL-2025", "6", "2,000.00"]
    rows = permanent.classify_all("71M", [row, row])
    assert rows[0]["row_hash"] != rows[1]["row_hash"]


def test_export_layout_change_is_a_schema_error(fixture_bytes):
    with pytest.raises(Exception, match="expected 6 columns"):
        permanent.parse_export("71M", fixture_bytes("permanent_71Q_2025-26.xls"))


def test_search_count(fixture_bytes):
    from murraywater.register import parse_count
    assert parse_count(fixture_bytes("permanent_71Q_search_2025-26.html").decode("utf-8")) == 177


def test_plan_fetches_unfinished_seasons_and_always_the_current_one():
    final = {(k, f"{y}-{(y + 1) % 100:02d}") for k in permanent.KINDS for y in range(2004, 2025)}
    todo = permanent.plan(final, date(2026, 11, 24), 2004)
    assert sorted(todo) == sorted([(k, y) for k in permanent.KINDS for y in (2025, 2026)])
    assert permanent.period(2026) == "01-Jul-2026 to 30-Jun-2027"


def test_final_after_31_august_of_the_next_year(con):
    store.replace_permanent_season(con, "71Q", "2024-25", [], 0, "2025-09-01T10:00:00+10:00")
    store.replace_permanent_season(con, "71Q", "2025-26", [], 0, "2026-08-31T10:00:00+10:00")
    assert store.permanent_final(con) == {("71Q", "2024-25")}


def perm_row(day: str, cat: str, units: float, price: float, n: int) -> dict:
    raw = [str(n), "NSW Murray", f"Regulated River ({'General' if cat == 'GS' else 'High'} Security)", "x", "NSW Murray",
           "x", f"APP{n}", day, str(units), str(price)]
    return permanent.classify("71Q", raw)


def test_permanent_outliers_left_out_of_price_and_volume(con, mil_raw):
    seed(con, mil_raw)
    rows = [perm_row("15-SEP-2026", "GS", 100, 2300 + i, i) for i in range(6)]
    rows.append(perm_row("20-SEP-2026", "GS", 6000, 1, 99))  # a token-priced block
    store.replace_permanent_season(con, "71Q", "2026-27", rows, len(rows), AT)
    data = metrics.build(con, date(2026, 9, 23), datetime(2026, 9, 23, 19, 0, tzinfo=config.TZ))
    gs = data["seasons"]["2026-27"]["perm_kpis"]["GS"]
    assert gs["n"] == 6 and gs["units"] == 600 and gs["outliers"] == 1 and gs["outlier_units"] == 6000
    assert gs["vwap"] == pytest.approx(2302.5)
    lr = next(r for r in data["long_run"] if r["season"] == "2026-27")
    assert lr["GS"]["units"] == 600 and lr["HS"] is None


def test_too_few_permanent_trades_for_the_outlier_rule():
    trades = [{"season": "2026-27", "cat_code": "HS", "price": p} for p in (9000, 9100, 1)]
    metrics.flag_perm_outliers(trades)
    assert not any(t["outlier"] for t in trades)


def test_recent_window_and_spread():
    d = date(2026, 11, 20)
    seg = {"Z10": [(d, 100, 200, 0), (date(2026, 10, 1), 100, 250, 0)],
           "Z11": [(d, 100, 300, 0), (d, 50, 1, 1)], "WEX": []}
    recent = metrics._recent(seg, d)
    assert recent["Z10"]["now"] == {"vwap": 200.0, "ml": 100.0, "n": 1}
    assert recent["Z10"]["before"]["vwap"] == 250.0
    assert recent["Z11"]["now"]["n"] == 1  # the outlier is left out
    spread = metrics._spread(seg, date(2026, 11, 1), d)
    assert spread[-1] == ["2026-11-20", 200.0, 300.0, None]


def test_public_page_has_no_waterfind_and_no_draft_labels(con, mil_raw, tmp_path):
    seed(con, mil_raw)
    store.upsert_waterfind(con, [{"zone": "Z11", "region": "11 MURRAY", "trade_date": "2026-09-22", "ml": 10.0,
                                  "price": 335.0, "occurrence": 1, "list_pos": 0, "not_zone_specific": 1}], AT)
    data = metrics.build(con, date(2026, 9, 23), datetime(2026, 9, 23, 19, 0, tzinfo=config.TZ))
    index, blob = render.write_public(data, tmp_path)
    html = index.read_text(encoding="utf-8")
    payload = json.loads(re.search(r"const DATA = (\{.*?\});\n", html, re.S).group(1).replace("<\\/", "</"))
    assert all(s["waterfind"] == [] for s in payload["seasons"].values()) and "review" not in payload
    assert "Show Waterfind indicator" not in html and "Draft for review" not in html
    assert "<!--public-only" not in html and "About this dashboard" in html and "not financial advice" in html
    assert "cdnjs.cloudflare.com/ajax/libs/echarts/5.6.0/echarts.min.js" in html and "../assets" not in html
    assert html.lstrip().lower().startswith("<!doctype html>") and 'name="description"' in html
    assert "© State of New South Wales through WaterNSW" in html and "© Murray Irrigation Limited" in html
    assert json.loads(blob.read_text(encoding="utf-8"))["seasons"]["2026-27"]["waterfind"] == []
    assert (tmp_path / ".nojekyll").exists()
