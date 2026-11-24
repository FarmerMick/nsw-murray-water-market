from datetime import date
from pathlib import Path

import pytest

from murraywater import mil, waterfind
from murraywater.register import SchemaError
from murraywater.seasons import day_of_season, month_ranges, register_date, season_of


def test_mil_parse_all(mil_raw):
    parsed = mil.parse_all(mil_raw)
    seasons = {s["season"]: s for s in parsed["seasons"]}
    s = seasons["2025-26"]
    assert s["ml"] == pytest.approx(102320.86)
    assert s["vwap"] == pytest.approx(s["total_cost"] / s["ml"])  # never MIL's AvePrice
    assert s["vwap"] == pytest.approx(287.20, abs=0.01) and s["ave_price_reported"] == 289.5
    daily = [d for d in parsed["daily"] if d["season"] == "2025-26"]
    assert sum(d["ml"] for d in daily) == pytest.approx(s["ml"])
    value = sum(d["ml"] * d["avg_price"] for d in daily)
    assert abs(value - s["total_cost"]) / s["total_cost"] < 0.005  # daily averages are volume-weighted
    current = [d for d in parsed["daily"] if d["season"] == "2026-27"]
    assert len(current) == 60 and current[0]["trade_date"] == "2026-09-23"


def test_mil_orderbook_sides_and_summary(mil_raw):
    parsed = mil.parse_all(mil_raw)
    book = parsed["orderbook"]
    bids = [b for b in book if b["side"] == "offerstobuy"]
    assert bids and bids[0]["serial"] == "B000287" and bids[0]["ml"] == 88.2  # BidSerial / UnitBalance fields
    wt_for_sale = sum(b["ml"] for b in book if b["side"] == "forsale" and b["listing_type"] == "WT")
    assert wt_for_sale == pytest.approx(parsed["summary"]["ml_for_sale"], abs=0.1)  # DE listings excluded
    assert parsed["summary"]["allocation"] == 28


def test_mil_alloc_history_dates(mil_raw):
    history = mil.parse_alloc_history(mil_raw["waterdata"]["historyData"])
    by_season = [h for h in history if h["season"] == "2025-26"]
    assert by_season[0]["announced"] == "2025-07-01" and by_season[-1]["announced"] == "2026-04-15"


def test_mil_schema_change_is_loud(mil_raw):
    broken = dict(mil_raw, dailysales=[{"Date": "2026-09-23T00:00:00.000Z", "MLSold": 1}])
    with pytest.raises(SchemaError):
        mil.parse_all(broken)


@pytest.mark.skipif(not (Path(__file__).parent / "fixtures" / "waterfind_home_2026-09-23.html").exists(),
                    reason="Waterfind's page isn't redistributed in the public repository")
def test_waterfind_parse_and_flag(fixture_bytes):
    html = fixture_bytes("waterfind_home_2026-09-23.html").decode("utf-8")
    zones = waterfind.parse_zones(html)
    assert zones["10"]["region"].startswith("10 MURRAY R. (ABOVE BARMAH CHOKE)")
    assert zones["10"]["temporary"][0] == ("2026-02-26", 200.0, 339.0)
    rows = waterfind.murray_trades(zones)
    z10 = [r for r in rows if r["zone"] == "Z10"]
    z11 = [r for r in rows if r["zone"] == "Z11"]
    assert len(z10) == 10 and len(z11) == 10
    assert all(r["not_zone_specific"] == 0 for r in z10)
    assert all(r["not_zone_specific"] == 1 for r in z11)  # matched the Zone 7, SA and Zone 14 lists


def test_waterfind_changed_page_yields_nothing():
    assert waterfind.murray_trades(waterfind.parse_zones("<html><body>redesigned</body></html>")) == []


def test_season_helpers():
    assert season_of(date(2026, 6, 30)) == "2025-26" and season_of(date(2026, 7, 1)) == "2026-27"
    assert season_of(date(2099, 7, 1)) == "2099-00"
    assert day_of_season(date(2026, 7, 1)) == 0 and day_of_season(date(2026, 9, 23)) == 84
    assert register_date(date(2026, 7, 1)) == "01-JUL-2026"
    assert month_ranges(2026, upto=date(2026, 9, 23))[-1] == (date(2026, 9, 1), date(2026, 9, 30))
    assert len(month_ranges(2025)) == 12
