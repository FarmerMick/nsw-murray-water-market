"""Dashboard metrics. Definitions are in BUILD_PROMPT.md section 4 and are shown in the dashboard footer."""

import statistics
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import config, store
from .seasons import day_of_season, label_start_year, season_bounds, season_of

DIRECTION_CODE = {"within": "W", "out": "O", "in": "I"}


def vwap(pairs) -> float | None:
    pairs = list(pairs)
    volume = sum(ml for ml, _ in pairs)
    return sum(ml * price for ml, price in pairs) / volume if volume else None


def median(values) -> float | None:
    values = list(values)
    return statistics.median(values) if values else None


def r1(x):
    return None if x is None else round(x, 1)


def r2(x):
    return None if x is None else round(x, 2)


def flag_outliers(trades: list[dict]) -> None:
    """Set t["outlier"] per the rule in BUILD_PROMPT.md section 4, within each season and zone."""
    groups = defaultdict(list)
    for t in trades:
        groups[(t["season"], t["zone"])].append(t)
    window = timedelta(days=config.OUTLIER_WINDOW_DAYS - 1)
    for group in groups.values():
        group.sort(key=lambda t: t["date"])
        dates = [t["date"] for t in group]
        prices = [t["price"] for t in group]
        for t in group:
            hi = bisect_right(dates, t["date"])
            lo = bisect_left(dates, t["date"] - window)
            if hi - lo >= config.OUTLIER_MIN_TRADES:
                ref = statistics.median(prices[lo:hi])
            elif hi >= config.OUTLIER_MIN_TRADES:
                ref = statistics.median(prices[:hi])
            else:
                ref = None
            t["outlier"] = int(ref is not None and not
                               (config.OUTLIER_LOW * ref <= t["price"] <= config.OUTLIER_HIGH * ref))


def flag_perm_outliers(trades: list[dict]) -> None:
    """Permanent trades are too sparse for a 30-day window, so the reference is the median of the same category's
    trades in the same season, once there are at least PERM_MIN_TRADES of them."""
    groups = defaultdict(list)
    for t in trades:
        groups[(t["season"], t["cat_code"])].append(t)
    for group in groups.values():
        ref = statistics.median(t["price"] for t in group) if len(group) >= config.PERM_MIN_TRADES else None
        for t in group:
            t["outlier"] = int(ref is not None and not
                               (config.OUTLIER_LOW * ref <= t["price"] <= config.OUTLIER_HIGH * ref))


def load(con) -> dict:
    trades = [dict(r) for r in con.execute(
        "SELECT application_no, trade_date, season, zone, direction, ml, price, purpose FROM register_trades"
        " WHERE is_commercial = 1 AND is_supp = 0 AND zone IS NOT NULL")]
    for t in trades:
        t["date"] = date.fromisoformat(t["trade_date"])
    flag_outliers(trades)
    unzoned = con.execute("SELECT COUNT(*) FROM register_trades WHERE is_commercial = 1 AND is_supp = 0"
                          " AND zone IS NULL").fetchone()[0]
    wex = [dict(r) for r in con.execute(
        "SELECT trade_date, season, ml, min_price, max_price, avg_price FROM wex_daily WHERE listing_type = 'WT'"
        " ORDER BY trade_date")]
    for w in wex:
        w["date"] = date.fromisoformat(w["trade_date"])
    purposes = [dict(r) for r in con.execute(
        "SELECT season, CASE WHEN purpose = '' THEN 'Not recorded' ELSE purpose END AS purpose, COUNT(*) AS n,"
        " SUM(ml) AS ml, SUM(CASE WHEN price > 0 THEN ml * price END) AS value,"
        " SUM(CASE WHEN price > 0 THEN ml END) AS priced_ml FROM register_trades GROUP BY 1, 2")]
    purpose_seasons = {r[0] for r in con.execute("SELECT DISTINCT season FROM register_trades WHERE purpose != ''")}
    # MIL publishes allocation history for finished seasons only; for the rest, a change between two of our
    # daily snapshots marks an announcement (dated the day it was first seen)
    alloc = [dict(r) for r in con.execute("SELECT season, announced, allocation FROM wex_alloc_history ORDER BY announced")]
    history_seasons = {a["season"] for a in alloc}
    previous, first_seen = None, {}
    for taken_at, value in con.execute("SELECT taken_at, allocation FROM wex_summary_snapshot ORDER BY taken_at"):
        day = date.fromisoformat(taken_at[:10])
        label = season_of(day)
        first_seen.setdefault(label, day.isoformat())
        if previous is not None and value != previous and label not in history_seasons:
            alloc.append({"season": label, "announced": day.isoformat(), "allocation": value})
        previous = value
    register_seasons = {r[0] for r in con.execute("SELECT DISTINCT season FROM register_trades")}
    perm = [dict(r) for r in con.execute(
        "SELECT kind, season, trade_date, cat_code, units, price, application_no FROM permanent_trades"
        " WHERE is_sale = 1 ORDER BY trade_date")]
    for p in perm:
        p["date"] = date.fromisoformat(p["trade_date"])
    flag_perm_outliers(perm)
    return {
        "perm": perm,
        "trades": trades, "unzoned": unzoned, "wex": wex, "purposes": purposes,
        "purpose_seasons": purpose_seasons, "register_seasons": register_seasons,
        "wex_seasons": {r["season"]: dict(r) for r in con.execute("SELECT * FROM wex_season")},
        "alloc": sorted(alloc, key=lambda a: a["announced"]), "alloc_history_seasons": history_seasons,
        "alloc_first_seen": first_seen,
        "waterfind": [dict(r) for r in con.execute("SELECT zone, trade_date, ml, price, not_zone_specific"
                                                   " FROM waterfind_trades ORDER BY trade_date")],
    }


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _segment_rows(season_trades, season_wex):
    """{segment: [(date, ml, price, outlier)]}; WEX rows use the daily volume-weighted average price."""
    seg = defaultdict(list)
    for t in season_trades:
        seg[t["zone"]].append((t["date"], t["ml"], t["price"], t["outlier"]))
    for w in season_wex:
        seg["WEX"].append((w["date"], w["ml"], w["avg_price"], 0))
    return seg


def _segment_stats(rows) -> dict:
    clean = [(ml, p) for _, ml, p, out in rows if not out]
    last = max(rows, key=lambda r: r[0]) if rows else None
    return {"ml": r1(sum(ml for _, ml, _, _ in rows)), "n": len(rows), "vwap": r2(vwap(clean)),
            "vwap_raw": r2(vwap((ml, p) for _, ml, p, _ in rows)), "median": r2(median(p for _, p in clean)),
            "outliers": sum(out for *_, out in rows),
            "outlier_ml": r1(sum(ml for _, ml, _, out in rows if out)),
            "last": None if last is None else {"date": last[0].isoformat(), "price": r2(last[2]), "ml": r1(last[1])}}


def _window_vwap(rows, end: date, days: int):
    """Volume-weighted price, volume and count of clean rows in the `days` days ending on `end`."""
    start = end - timedelta(days=days - 1)
    picked = [(ml, p) for d, ml, p, out in rows if start <= d <= end and not out]
    return {"vwap": r2(vwap(picked)), "ml": r1(sum(ml for ml, _ in picked)), "n": len(picked)}


def _recent(seg, as_of: date) -> dict:
    """Last 30 days to the as-of date, and the 30 days before that, per segment."""
    days = config.RECENT_DAYS
    return {s: {"now": _window_vwap(seg.get(s, []), as_of, days),
                "before": _window_vwap(seg.get(s, []), as_of - timedelta(days=days), days)} for s in config.SEGMENTS}


def _spread(seg, start: date, last: date) -> list:
    """Each Sunday: trailing 30-day VWAP of Zone 10, Zone 11 and MIL, so thin weeks don't leave gaps."""
    out, d = [], start + timedelta(days=(6 - start.weekday()) % 7)
    while d <= last:
        row = [d.isoformat()]
        for s in config.SEGMENTS:
            row.append(_window_vwap(seg.get(s, []), d, config.RECENT_DAYS)["vwap"])
        out.append(row)
        d += timedelta(days=7)
    if out and out[-1][0] != last.isoformat() and last >= start:
        out.append([last.isoformat()] + [_window_vwap(seg.get(s, []), last, config.RECENT_DAYS)["vwap"]
                                         for s in config.SEGMENTS])
    return out


def _perm_stats(rows) -> dict:
    """Outliers are left out of volume as well as price: with no trade purpose recorded, a $1 dealing is far more
    likely a related-party move than a sale."""
    kept = [t for t in rows if not t["outlier"]]
    clean = [(t["units"], t["price"]) for t in kept]
    last = max(kept, key=lambda t: t["date"]) if kept else None
    return {"units": r1(sum(u for u, _ in clean)), "n": len(kept), "vwap": r2(vwap(clean)),
            "median": r2(median(p for _, p in clean)), "outliers": sum(t["outlier"] for t in rows),
            "outlier_units": r1(sum(t["units"] for t in rows if t["outlier"])),
            "last": None if last is None else {"date": last["date"].isoformat(), "price": r2(last["price"]),
                                               "units": r1(last["units"])}}


def season_detail(label, data, as_of_day, generated: datetime) -> dict:
    start, end = season_bounds(label_start_year(label))
    trades = [t for t in data["trades"] if t["season"] == label]
    wex = [w for w in data["wex"] if w["season"] == label]
    seg = _segment_rows(trades, wex)
    upto = lambda rows: [r for r in rows if day_of_season(r[0]) <= as_of_day]
    kpis = {s: _segment_stats(upto(seg.get(s, []))) for s in config.SEGMENTS}
    kpis["total_ml"] = r1(sum(kpis[s]["ml"] or 0 for s in config.SEGMENTS))

    weekly = {}
    for s in config.SEGMENTS:
        buckets = defaultdict(list)
        for d, ml, p, out in seg.get(s, []):
            buckets[max(_week_start(d), start)].append((ml, p, out))  # the first week starts on 1 July
        weekly[s] = [[w.isoformat(), r1(sum(ml for ml, _, _ in b)), r2(vwap((ml, p) for ml, p, out in b if not out))]
                     for w, b in sorted(buckets.items())]

    monthly = defaultdict(dict)
    for s in config.SEGMENTS:
        buckets = defaultdict(list)
        for row in seg.get(s, []):
            buckets[row[0].strftime("%Y-%m")].append(row)
        for month, rows in buckets.items():
            st = _segment_stats(rows)
            monthly[month][s] = {k: st[k] for k in ("ml", "n", "vwap", "median")}

    days = (min(end, generated.date()) - start).days if label == season_of(generated.date()) else (end - start).days
    cumulative = {}
    for s in config.SEGMENTS:
        daily = defaultdict(float)
        for d, ml, _, _ in seg.get(s, []):
            daily[day_of_season(d)] += ml
        run, series = 0.0, []
        for day in range(days + 1):
            run += daily.get(day, 0.0)
            series.append(r1(run))
        cumulative[s] = series

    last_day = min(end, generated.date()) if label == season_of(generated.date()) else end
    perm = [t for t in data["perm"] if t["season"] == label]
    perm_kpis = {c: _perm_stats([t for t in perm if t["cat_code"] == c and day_of_season(t["date"]) <= as_of_day])
                 for c in config.PERM_CATEGORIES}

    direction = {z: {code: r1(sum(t["ml"] for t in trades if t["zone"] == z and t["direction"] == name))
                     for name, code in DIRECTION_CODE.items()} for z in ("Z10", "Z11")}
    purposes = sorted(({"purpose": p["purpose"], "n": p["n"], "ml": r1(p["ml"]),
                        "vwap": r2(p["value"] / p["priced_ml"]) if p["priced_ml"] else None}
                       for p in data["purposes"] if p["season"] == label), key=lambda p: -(p["ml"] or 0))
    return {
        "label": label, "start": start.isoformat(), "end": end.isoformat(), "as_of_day": as_of_day,
        "purpose_recorded": label in data["purpose_seasons"], "kpis": kpis,
        "trades": [[t["date"].isoformat(), t["zone"], DIRECTION_CODE[t["direction"]], r1(t["ml"]), r2(t["price"]),
                    t["outlier"], int(bool(t["purpose"]))] for t in sorted(trades, key=lambda t: t["date"])],
        "wex": [[w["date"].isoformat(), r1(w["ml"]), r2(w["min_price"]), r2(w["max_price"]), r2(w["avg_price"])]
                for w in wex],
        "weekly": weekly, "monthly": dict(sorted(monthly.items())), "cumulative": cumulative,
        "direction": direction, "purposes": purposes,
        "waterfind": [[w["trade_date"], w["zone"], r1(w["ml"]), r2(w["price"]), w["not_zone_specific"]]
                      for w in data["waterfind"] if season_of(date.fromisoformat(w["trade_date"])) == label],
        "alloc": [[a["announced"], a["allocation"]] for a in data["alloc"] if a["season"] == label],
        "alloc_tracked_from": None if label in data["alloc_history_seasons"] else data["alloc_first_seen"].get(label),
        "spread": _spread(seg, start, last_day),
        "perm_kpis": perm_kpis,
        "perm_trades": [[t["date"].isoformat(), t["cat_code"], r1(t["units"]), r2(t["price"]), t["outlier"], t["kind"]]
                        for t in perm],
    }


def long_run(data, current: str) -> list[dict]:
    labels = sorted({t["season"] for t in data["trades"]} | set(data["wex_seasons"]) | data["register_seasons"]
                    | {t["season"] for t in data["perm"]})
    out = []
    for label in labels:
        row = {"season": label, "partial": label == current, "purpose_recorded": label in data["purpose_seasons"],
               "register": label in data["register_seasons"]}
        for z in ("Z10", "Z11"):
            ts = [t for t in data["trades"] if t["season"] == label and t["zone"] == z]
            row[z] = {"ml": r1(sum(t["ml"] for t in ts)), "n": len(ts),
                      "vwap": r2(vwap((t["ml"], t["price"]) for t in ts if not t["outlier"]))} if ts else None
        ws = data["wex_seasons"].get(label)
        if ws:
            row["WEX"] = {"ml": r1(ws["ml"]), "n": ws["sales"], "vwap": r2(ws["vwap"]), "allocation": ws["allocation"]}
        elif label == current:
            days = [w for w in data["wex"] if w["season"] == label]
            row["WEX"] = {"ml": r1(sum(w["ml"] for w in days)), "n": None,
                          "vwap": r2(vwap((w["ml"], w["avg_price"]) for w in days)), "allocation": None}
        else:
            row["WEX"] = None
        for c in config.PERM_CATEGORIES:
            ps = [t for t in data["perm"] if t["season"] == label and t["cat_code"] == c and not t["outlier"]]
            row[c] = {"units": r1(sum(t["units"] for t in ps)), "n": len(ps),
                      "vwap": r2(vwap((t["units"], t["price"]) for t in ps))} if ps else None
        out.append(row)
    return out


def market_now(con) -> dict | None:
    taken = con.execute("SELECT MAX(taken_at) FROM wex_summary_snapshot").fetchone()[0]
    if not taken:
        return None
    summary = dict(con.execute("SELECT * FROM wex_summary_snapshot WHERE taken_at = ?", (taken,)).fetchone())
    book = [dict(r) for r in con.execute("SELECT side, ml, price FROM wex_orderbook_snapshot WHERE taken_at = ?"
                                         " AND listing_type = 'WT'", (taken,))]
    asks = sorted(([b["price"], b["ml"]] for b in book if b["side"] == "forsale"))
    bids = sorted(([b["price"], b["ml"]] for b in book if b["side"] == "offerstobuy"), reverse=True)
    today = [[b["price"], b["ml"]] for b in book if b["side"] == "salestoday"]
    return {"taken_at": taken, "summary": summary, "asks": asks, "bids": bids, "sales_today": today,
            "best_ask": asks[0][0] if asks else None, "best_bid": bids[0][0] if bids else None,
            "ml_for_sale": r1(sum(ml for _, ml in asks)),
            "today_ml": r1(sum(ml for _, ml in today)), "today_vwap": r2(vwap((ml, p) for p, ml in today))}


def sources(con, data, generated: datetime) -> dict:
    out = {}
    latest = {"register": max((t["trade_date"] for t in data["trades"]), default=None),
              "mil": max((w["trade_date"] for w in data["wex"]), default=None),
              "waterfind": max((w["trade_date"] for w in data["waterfind"]), default=None),
              "permanent": max((t["trade_date"] for t in data["perm"]), default=None)}
    for source in ("register", "permanent", "mil", "waterfind"):
        last = store.last_success(con, source)
        attempt = con.execute("SELECT status, detail, finished_at FROM fetch_log WHERE source = ?"
                              " ORDER BY id DESC LIMIT 1", (source,)).fetchone()
        stale = last is None or (generated - datetime.fromisoformat(last)).total_seconds() > config.STALE_HOURS * 3600
        out[source] = {"last_success": last, "stale": stale, "latest_trade": latest[source],
                       "last_status": attempt["status"] if attempt else None,
                       "last_detail": attempt["detail"] if attempt else None}
    return out


def build(con, today: date, generated: datetime) -> dict:
    data = load(con)
    current = season_of(today)
    labels = sorted({t["season"] for t in data["trades"]} | {w["season"] for w in data["wex"]} | {current},
                    reverse=True)
    detail_labels = [current] + [s for s in labels if s != current][: config.DETAIL_SEASONS - 1]
    last_dates = [t["date"] for t in data["trades"] if t["season"] == current] + \
                 [w["date"] for w in data["wex"] if w["season"] == current]
    as_of = max(last_dates) if last_dates else today
    seasons = {}
    for label in detail_labels:
        day = day_of_season(as_of) if label == current else 366
        seasons[label] = season_detail(label, data, day, generated)
    previous = f"{label_start_year(current) - 1}-{label_start_year(current) % 100:02d}"
    prev_detail = season_detail(previous, data, day_of_season(as_of), generated)
    seasons[current]["prev_same_day"] = prev_detail["kpis"]
    seasons[current]["prev_perm_same_day"] = prev_detail["perm_kpis"]
    seasons[current]["recent"] = _recent(_segment_rows([t for t in data["trades"] if t["season"] == current],
                                                       [w for w in data["wex"] if w["season"] == current]), as_of)
    seasons[current]["recent_days"] = config.RECENT_DAYS
    latest = sorted(data["trades"], key=lambda t: (t["date"], t["application_no"]), reverse=True)[:25]
    return {
        "generated_at": generated.isoformat(timespec="seconds"), "current_season": current,
        "as_of": as_of.isoformat(), "seasons": seasons, "season_order": detail_labels,
        "long_run": long_run(data, current), "market_now": market_now(con), "sources": sources(con, data, generated),
        "latest_trades": [[t["date"].isoformat(), t["zone"], t["direction"], r1(t["ml"]), r2(t["price"]),
                           t["purpose"] or "Not recorded", t["outlier"]] for t in latest],
        "latest_wex": [[w["date"].isoformat(), r1(w["ml"]), r2(w["min_price"]), r2(w["max_price"]),
                        r2(w["avg_price"])] for w in data["wex"][-10:][::-1]],
        "latest_perm": [[t["date"].isoformat(), t["cat_code"], r1(t["units"]), r2(t["price"]), t["kind"], t["outlier"]]
                        for t in sorted(data["perm"], key=lambda t: t["date"], reverse=True)[:12]],
        "perm_categories": {c: config.PERM_LABELS[c] for c in config.PERM_CATEGORIES},
        "unzoned_commercial_rows": data["unzoned"],
        "segments": {s: config.SEGMENT_LABELS[s] for s in config.SEGMENTS},
        "rules": {"outlier_low": config.OUTLIER_LOW, "outlier_high": config.OUTLIER_HIGH,
                  "window_days": config.OUTLIER_WINDOW_DAYS, "min_trades": config.OUTLIER_MIN_TRADES,
                  "perm_min_trades": config.PERM_MIN_TRADES, "recent_days": config.RECENT_DAYS},
    }
