"""NSW Public Water Register: permanent (entitlement) trades for NSW Murray (water source 11904).

Two register reports carry them, each searched one water year at a time:
- s71Q share assignments (WaterShareIntraWSResult): part or all of a licence's share component moves to another
  licence. Two header rows, ten columns, with an application number.
- s71M licence transfers (WaterShareTransferResult, transferType 10): a whole licence or holding changes hands.
  One header row, six columns, no application number.
Price is dollars per unit of share component. Layout verified 24 Nov 2026 (BUILD_PROMPT.md section 3.4).
"""

import hashlib
import urllib.parse
from datetime import date, datetime

import xlrd

from . import config
from .register import SchemaError
from .seasons import season_label

BASE = "https://waterregister.waternsw.com.au/"
KINDS = {
    "71Q": {"path": "WaterShareIntraWSResult", "extra": {}, "header_rows": 2,
            "headers": {0: "WAL No.", 2: "Category", 3: "WAL No.", 5: "Category", 6: "Application Number",
                        7: "Transferred", 8: "Share", 9: "Price Paid"},
            "cols": 10, "date": 7, "units": 8, "price": 9, "category": 2, "from": 0, "to": 3, "app": 6},
    "71M": {"path": "WaterShareTransferResult", "extra": {"transferType": "10"}, "header_rows": 1,
            "headers": {0: "WAL No.", 2: "Category", 3: "Transferred"},
            "cols": 6, "date": 3, "units": 4, "price": 5, "category": 2, "from": 0, "to": None, "app": None},
}
CATEGORY_CODES = (("general security", "GS"), ("high security", "HS"))


def category_code(category: str) -> str:
    text = category.lower()
    return next((code for key, code in CATEGORY_CODES if key in text), "other")


def period(start_year: int) -> str:
    """The register's water-year period, e.g. '01-Jul-2026 to 30-Jun-2027'."""
    return f"01-Jul-{start_year} to 30-Jun-{start_year + 1}"


def _form(kind: str, start_year: int) -> dict:
    return {"src": config.REGISTER_WATER_SOURCE_ID, **KINDS[kind]["extra"], "period": period(start_year)}


def _number(text: str) -> float:
    text = text.replace(",", "").replace("$", "").strip()
    if text.lower() in ("", "null", "-"):
        return 0.0
    try:
        return float(text)
    except ValueError as exc:
        raise SchemaError(f"permanent trade number '{text}' is not numeric") from exc


def parse_export(kind: str, xls_bytes: bytes) -> list[list[str]]:
    spec = KINDS[kind]
    try:
        sheet = xlrd.open_workbook(file_contents=xls_bytes).sheet_by_index(0)
    except xlrd.XLRDError as exc:
        raise SchemaError(f"{kind} export is not a readable .xls: {exc}") from exc
    if sheet.nrows == 0:
        return []
    if sheet.ncols != spec["cols"] or sheet.nrows < spec["header_rows"]:
        raise SchemaError(f"{kind} export is {sheet.nrows} x {sheet.ncols}, expected {spec['cols']} columns")
    header = spec["header_rows"] - 1
    for col, name in spec["headers"].items():
        found = str(sheet.cell_value(header, col)).strip()
        if not found.startswith(name):
            raise SchemaError(f"{kind} export column {col} is '{found}', expected '{name}'")
    return [[str(sheet.cell_value(r, c)).strip() for c in range(sheet.ncols)]
            for r in range(spec["header_rows"], sheet.nrows)]


def classify(kind: str, raw: list[str], occurrence: int = 0) -> dict:
    spec = KINDS[kind]
    try:
        traded = datetime.strptime(raw[spec["date"]].title(), "%d-%b-%Y").date()
    except ValueError as exc:
        raise SchemaError(f"{kind} row has date '{raw[spec['date']]}'") from exc
    category = raw[spec["category"]]
    units, price = _number(raw[spec["units"]]), _number(raw[spec["price"]])
    code = category_code(category)
    start = traded.year if traded.month >= 7 else traded.year - 1
    return {
        "row_hash": hashlib.sha1("\x1f".join([kind, str(occurrence)] + raw).encode("utf-8")).hexdigest(),
        "kind": kind, "season": season_label(start), "trade_date": traded.isoformat(), "category": category,
        "cat_code": code, "units": units, "price": price,
        "application_no": raw[spec["app"]] if spec["app"] is not None else "",
        "from_wal": raw[spec["from"]], "to_wal": raw[spec["to"]] if spec["to"] is not None else "",
        "is_sale": int(price > 0 and code in ("GS", "HS")),
    }


def classify_all(kind: str, raw_rows: list[list[str]]) -> list[dict]:
    """Identical rows (possible in s71M, which has no application number) are kept apart by an occurrence count."""
    seen: dict[tuple, int] = {}
    out = []
    for raw in raw_rows:
        key = tuple(raw)
        seen[key] = seen.get(key, -1) + 1
        out.append(classify(kind, raw, seen[key]))
    return out


def fetch_count(client, kind: str, start_year: int) -> int:
    from .register import parse_count
    body = client.fetch(BASE + KINDS[kind]["path"],
                        form={"pageCommand": "search", "resultType": "modern", **_form(kind, start_year)})
    text = body.decode("utf-8", "ignore")
    if "No records were found" in text and "listResult" not in text:
        return 0  # e.g. 2004-05, before s71Q dealings were recorded for NSW Murray
    return parse_count(text)


def fetch_season(client, kind: str, start_year: int) -> tuple[int, list[dict]]:
    """Return (register count, classified rows) for one water year."""
    expected = fetch_count(client, kind, start_year)
    raw_rows = []
    for from_row in range(0, expected, config.REGISTER_EXPORT_MAX):
        to_row = min(from_row + config.REGISTER_EXPORT_MAX, expected) - 1
        query = {"pageCommand": "export", "resultType": "exel", "fromRow": from_row, "toRow": to_row,
                 **_form(kind, start_year)}
        raw_rows.extend(parse_export(kind, client.fetch(BASE + KINDS[kind]["path"] + "?"
                                                        + urllib.parse.urlencode(query))))
    return expected, classify_all(kind, raw_rows)


def plan(final: set[tuple[str, str]], today: date, backfill_from: int) -> list[tuple[str, int]]:
    """(kind, season start year) pairs to fetch: every season not yet final, plus always the current one.

    A season counts as final once fetched after 31 August of the following year, as for allocation trades.
    """
    current = today.year if today.month >= 7 else today.year - 1
    return [(kind, year) for kind in KINDS for year in range(backfill_from, current + 1)
            if year == current or (kind, season_label(year)) not in final]
