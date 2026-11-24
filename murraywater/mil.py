"""Murray Irrigation Water Exchange (WEX) market API. Undocumented; see BUILD_PROMPT.md section 3.2."""

import json
from datetime import date, datetime

from . import config
from .register import SchemaError
from .seasons import label_start_year, season_of

DAILY_KEYS = {"Date", "MLSold", "MLMaxPrice", "MLMinPrice", "MLAvgPrice", "ListingType"}
SEASON_KEYS = {"Season", "Sales", "ML", "TotalCost", "AvePrice", "LowSale", "HighSale", "Allocation"}
HISTORY_KEYS = {"Season", "Date", "Allocation"}
SUMMARY_KEYS = {"sellLow", "sellHigh", "MLForSale", "soldToday", "buyOffer", "allocation"}
# side -> (serial field, volume field); buy offers use different names from listings and sales
BOOK_FIELDS = {"forsale": ("SerialNo", "ML"), "salestoday": ("SerialNo", "ML"),
               "offerstobuy": ("BidSerial", "UnitBalance")}


def _require(rows, keys, what):
    if not isinstance(rows, list):
        raise SchemaError(f"MIL {what}: expected a list")
    for row in rows:
        missing = keys - set(row)
        if missing:
            raise SchemaError(f"MIL {what}: missing {sorted(missing)}")


def fetch(client) -> dict:
    out = {}
    for name in config.MIL_ENDPOINTS:
        body = client.fetch(config.MIL_BASE + name)
        try:
            out[name] = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"MIL {name}: not JSON") from exc
    return out


def _trade_day(value: str) -> date:
    # "2026-09-23T00:00:00.000Z": the date part is MIL's trading day; don't shift time zones
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def parse_daily(rows) -> list[dict]:
    _require(rows, DAILY_KEYS, "daily sales")
    out = []
    for row in rows:
        day = _trade_day(row["Date"])
        out.append({"trade_date": day.isoformat(), "season": season_of(day), "ml": float(row["MLSold"]),
                    "min_price": float(row["MLMinPrice"]), "max_price": float(row["MLMaxPrice"]),
                    "avg_price": float(row["MLAvgPrice"]), "listing_type": row["ListingType"]})
    return out


def parse_seasons(rows) -> list[dict]:
    _require(rows, SEASON_KEYS, "season data")
    out = []
    for row in rows:
        ml, cost = float(row["ML"] or 0), float(row["TotalCost"] or 0)
        out.append({"season": row["Season"], "sales": int(row["Sales"] or 0), "ml": ml, "total_cost": cost,
                    "vwap": cost / ml if ml else None,  # AvePrice is not volume-weighted; never use it
                    "ave_price_reported": row["AvePrice"], "low_price": row["LowSale"],
                    "high_price": row["HighSale"], "allocation": row["Allocation"]})
    return out


def parse_alloc_history(rows) -> list[dict]:
    _require(rows, HISTORY_KEYS, "allocation history")
    out = []
    for row in rows:
        start = label_start_year(row["Season"])
        day_month = datetime.strptime(row["Date"], "%d-%b")
        year = start if day_month.month >= 7 else start + 1
        out.append({"season": row["Season"], "announced": date(year, day_month.month, day_month.day).isoformat(),
                    "allocation": float(row["Allocation"])})
    return out


def parse_orderbook(tables) -> list[dict]:
    if not isinstance(tables, dict):
        raise SchemaError("MIL exchange tables: expected an object")
    out = []
    for side, (serial, volume) in BOOK_FIELDS.items():
        rows = tables.get(side, [])
        _require(rows, {serial, volume, "UnitPrice", "ListingType"}, f"exchange tables ({side})")
        out.extend({"side": side, "serial": r[serial], "ml": float(r[volume]), "price": float(r["UnitPrice"]),
                    "listing_type": r["ListingType"]} for r in rows)
    return out


def parse_summary(summary) -> dict:
    if not isinstance(summary, dict) or SUMMARY_KEYS - set(summary):
        raise SchemaError("MIL summary: missing fields")
    return {"sell_low": summary["sellLow"], "sell_high": summary["sellHigh"], "ml_for_sale": summary["MLForSale"],
            "sold_today": summary["soldToday"], "buy_offer": summary["buyOffer"],
            "allocation": summary["allocation"]}


def parse_all(raw: dict) -> dict:
    water = raw["waterdata"]
    if not isinstance(water, dict):
        raise SchemaError("MIL waterdata: expected an object")
    return {"daily": parse_daily(raw["dailysales"]) + parse_daily(water.get("dailyData", [])),
            "seasons": parse_seasons(water.get("seasonData", [])),
            "alloc_history": parse_alloc_history(water.get("historyData", [])),
            "orderbook": parse_orderbook(raw["exchangetables"]),
            "summary": parse_summary(raw["summary"])}
