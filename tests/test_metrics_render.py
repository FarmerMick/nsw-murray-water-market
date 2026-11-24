import json
import re
from datetime import date, datetime, timedelta

import pytest

from murraywater import config, metrics, mil, register, render, store
from test_register import UP, raw


def trade(day, price, zone="Z11", season="2026-27"):
    return {"date": day, "price": price, "zone": zone, "season": season}


def test_vwap_and_median():
    assert metrics.vwap([(100, 300), (300, 200)]) == pytest.approx(225)
    assert metrics.vwap([]) is None
    assert metrics.median([1, 3, 2]) == 2


def test_outlier_needs_ten_trades():
    d = date(2026, 8, 1)
    few = [trade(d, 300) for _ in range(8)] + [trade(d, 1)]
    metrics.flag_outliers(few)
    assert not any(t["outlier"] for t in few)


def test_outlier_window_and_thresholds():
    d = date(2026, 8, 1)
    ts = [trade(d + timedelta(days=i), 300) for i in range(12)]
    low, high, edge = trade(d + timedelta(days=12), 149), trade(d + timedelta(days=12), 601), trade(d + timedelta(days=12), 150)
    group = ts + [low, high, edge]
    metrics.flag_outliers(group)
    assert (low["outlier"], high["outlier"], edge["outlier"]) == (1, 1, 0)
    assert not any(t["outlier"] for t in ts)


def test_outlier_groups_by_zone_and_season():
    d = date(2026, 8, 1)
    z11 = [trade(d, 330) for _ in range(12)]
    z10 = [trade(d, 220, zone="Z10") for _ in range(12)]
    metrics.flag_outliers(z11 + z10)
    assert not any(t["outlier"] for t in z11 + z10)  # $220 is normal for Zone 10 even though Zone 11 sits at $330


def seed(con, mil_raw):
    batch = []
    for i in range(12):
        batch.append(register.classify(raw(app=f"A{i}", when="10-SEP-2026", price=str(320 + i), ml="100")))
    batch.append(register.classify(raw(app="OUT", when="10-SEP-2026", price="1", ml="40")))
    batch.append(register.classify(raw(app="REL", when="10-SEP-2026", purpose="Related Party", price="0", ml="5000")))
    batch.append(register.classify(raw(app="PREV", when="10-SEP-2025", price="250", ml="50", from_wmz=UP)))
    store.replace_register_range(con, date(2025, 7, 1), date(2026, 9, 30), batch, len(batch), "2026-09-23T19:00:00+10:00")
    parsed = mil.parse_all(mil_raw)
    at = "2026-09-23T19:00:00+10:00"
    store.upsert_wex_daily(con, parsed["daily"], at)
    store.upsert_wex_seasons(con, parsed["seasons"], at)
    store.replace_alloc_history(con, parsed["alloc_history"])
    store.insert_market_snapshot(con, parsed["orderbook"], parsed["summary"], at)


def test_build_numbers(con, mil_raw):
    seed(con, mil_raw)
    data = metrics.build(con, date(2026, 9, 23), datetime(2026, 9, 23, 19, 0, tzinfo=config.TZ))
    k = data["seasons"]["2026-27"]["kpis"]
    assert k["Z11"]["n"] == 13 and k["Z11"]["ml"] == pytest.approx(1240)  # the $1 outlier stays in volume
    assert k["Z11"]["outliers"] == 1
    expected = sum((320 + i) * 100 for i in range(12)) / 1200
    assert k["Z11"]["vwap"] == pytest.approx(expected, abs=0.01)  # the outlier is out of clean price
    assert k["WEX"]["ml"] == pytest.approx(24106.95, abs=0.1)
    assert k["total_ml"] == pytest.approx(1240 + 24106.95, abs=0.2)
    purposes = {p["purpose"]: p for p in data["seasons"]["2026-27"]["purposes"]}
    assert purposes["Related Party"]["ml"] == 5000  # recorded, but not counted as sold
    assert data["market_now"]["best_bid"] == 208 and data["market_now"]["ml_for_sale"] == pytest.approx(2928.4)
    assert data["seasons"]["2026-27"]["prev_same_day"]["Z10"]["ml"] == 50
    json.dumps(data)  # must serialise


def test_first_week_starts_on_season_start(con):
    batch = [register.classify(raw(app="J1", when="01-JUL-2026")), register.classify(raw(app="J2", when="06-JUL-2026"))]
    store.replace_register_range(con, date(2026, 7, 1), date(2026, 7, 31), batch, 2, "2026-07-06T19:00:00+10:00")
    data = metrics.build(con, date(2026, 7, 6), datetime(2026, 7, 6, 19, 0, tzinfo=config.TZ))
    weeks = [w[0] for w in data["seasons"]["2026-27"]["weekly"]["Z11"]]
    assert weeks == ["2026-07-01", "2026-07-06"]  # 1 Jul 2026 is a Wednesday; not "29 Jun"


def test_alloc_changes_tracked_from_snapshots(con, mil_raw):
    seed(con, mil_raw)
    parsed = mil.parse_all(mil_raw)
    later = dict(parsed["summary"], allocation=33)
    store.insert_market_snapshot(con, [], later, "2026-10-01T19:00:00+10:00")
    data = metrics.build(con, date(2026, 10, 1), datetime(2026, 10, 1, 19, 0, tzinfo=config.TZ))
    s = data["seasons"]["2026-27"]
    assert s["alloc"] == [["2026-10-01", 33.0]]
    assert s["alloc_tracked_from"] == "2026-09-23"
    assert data["seasons"]["2025-26"]["alloc_tracked_from"] is None  # MIL history covers finished seasons


def test_render_embeds_data_safely():
    template = (config.TEMPLATE_PATH).read_text(encoding="utf-8")
    html = render.render({"x": "</script><b>"}, template)
    assert render.PLACEHOLDER not in html
    assert "</script><b>" not in html and "<\\/script><b>" in html
    assert "© State of New South Wales through WaterNSW" in html
    assert "private, non-commercial" in html
    scripts = re.findall(r'<script src="([^"]+)"', html)
    assert scripts == ["../assets/echarts.min.js"]  # works offline: no CDN at view time
    assert (config.ASSETS_DIR / "echarts.min.js").exists()


def test_review_copy_is_self_contained_and_has_no_waterfind(con, mil_raw):
    seed(con, mil_raw)
    store.upsert_waterfind(con, [{"zone": "Z11", "region": "11 MURRAY", "trade_date": "2026-09-22", "ml": 10.0,
                                  "price": 335.0, "occurrence": 1, "list_pos": 0, "not_zone_specific": 1}],
                           "2026-09-23T19:00:00+10:00")
    store.log_fetch(con, "waterfind", "2026-09-23T19:00:00+10:00", "2026-09-23T19:00:01+10:00", "ok", "", 1)
    data = metrics.build(con, date(2026, 9, 23), datetime(2026, 9, 23, 19, 0, tzinfo=config.TZ))
    assert data["seasons"]["2026-27"]["waterfind"] and "waterfind" in data["sources"]  # the private copy has it
    template = config.TEMPLATE_PATH.read_text(encoding="utf-8")
    html = render.render_review(data, template, "/* echarts stand-in */")
    payload = json.loads(re.search(r"const DATA = (\{.*?\});\n", html, re.S).group(1).replace("<\\/", "</"))
    assert payload["review"] is True
    assert all(s["waterfind"] == [] for s in payload["seasons"].values())
    assert "waterfind" not in payload["sources"]
    assert "Show Waterfind indicator" not in html and "Waterfind public market information" not in html
    assert "Draft for review" in html and "<!--review-only" not in html
    assert '<script src="' not in html and "/* echarts stand-in */" in html  # one file, nothing external
    assert "© State of New South Wales through WaterNSW" in html and "Murray Irrigation Limited" in html
    assert data["seasons"]["2026-27"]["waterfind"]  # the private data wasn't modified


def test_static_summary_for_script_blocking_viewers(con, mil_raw):
    seed(con, mil_raw)
    data = metrics.build(con, date(2026, 9, 23), datetime(2026, 9, 23, 19, 0, tzinfo=config.TZ))
    template = config.TEMPLATE_PATH.read_text(encoding="utf-8")
    for page in (render.render(data, template), render.render_review(data, template, "/* echarts */")):
        without_scripts = re.sub(r"<script\b.*?</script>", "", page, flags=re.S)
        assert render.SUMMARY_MARK not in page
        assert "blocked the page's scripts" in without_scripts
        assert f"{data['seasons']['2026-27']['kpis']['total_ml']:,.0f} ML" in without_scripts  # the headline shows
        assert "MIL Water Exchange" in without_scripts and "Month by month" in without_scripts
    assert 'fallback.remove()' in template  # the interactive page takes its place when scripts run


def test_freeze_snapshot_keeps_drawn_charts_for_script_blocking_viewers():
    dom = ('<html lang="en-AU" class="js"><head></head><body><main class="wrap">'
           '<div class="chart tall" id="chart-price" role="img" aria-label="Price" _echarts_instance_="ec_1" '
           'style="position: relative;"><div style="position: relative; width: 1254px; height: 420px;">'
           '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="1254" height="420" '
           'style="position: absolute; left: 0px;"><circle r="4"/></svg><div class="tip"></div></div></div>'
           '<div class="tableview" id="table-price"></div></main></body></html>')
    frozen = render.freeze_snapshot(dom)
    assert frozen.startswith("<!doctype html>")
    assert 'data-snapshot="1" data-theme="light"' in frozen
    assert "_echarts_instance_" not in frozen and 'class="tip"' not in frozen
    assert 'viewBox="0 0 1254 420" width="100%" height="100%"' in frozen and "<circle r=\"4\"/>" in frozen
    assert 'id="chart-price" role="img" aria-label="Price">' in frozen
    assert "snapshot-note" in frozen
    with pytest.raises(ValueError):
        render.freeze_snapshot("<html><body><main class=\"wrap\"></main></body></html>")


def test_web_page_fits_the_artifact_host(con, mil_raw):
    seed(con, mil_raw)
    store.upsert_waterfind(con, [{"zone": "Z11", "region": "11 MURRAY", "trade_date": "2026-09-22", "ml": 10.0,
                                  "price": 335.0, "occurrence": 1, "list_pos": 0, "not_zone_specific": 1}],
                           "2026-09-23T19:00:00+10:00")
    data = metrics.build(con, date(2026, 9, 23), datetime(2026, 9, 23, 19, 0, tzinfo=config.TZ))
    page = render.render_web(data, config.TEMPLATE_PATH.read_text(encoding="utf-8"))
    assert page.startswith("<title>NSW Murray Water Market</title>")  # the host reads the title from the top
    for tag in (r"<!doctype", r"<html[\s>]", r"<head[\s>]", r"<body[\s>]", r"<meta\b"):
        assert not re.search(tag, page, re.I)  # the host supplies the skeleton
    assert render.ECHARTS_CDN in page and "../assets/" not in page
    assert re.findall(r'<script src="([^"]+)"', page) == ["https://cdnjs.cloudflare.com/ajax/libs/echarts/5.6.0/echarts.min.js"]
    assert 'id="theme"' not in page  # the host's theme switch applies instead
    payload = json.loads(re.search(r"const DATA = (\{.*?\});\n", page, re.S).group(1).replace("<\\/", "</"))
    assert all(s["waterfind"] == [] for s in payload["seasons"].values()) and "waterfind" not in payload["sources"]
    assert ':root:not([data-theme="light"])' in page and ':root[data-theme="dark"]' in page


def test_render_requires_placeholder():
    with pytest.raises(ValueError):
        render.render({}, "<html></html>")
