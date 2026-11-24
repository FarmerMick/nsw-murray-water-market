"""NSW Public Water Register: allocation assignments for NSW Murray (water source 11904).

Search (POST) returns the row count; export (GET) returns a legacy .xls of up to 1,000 rows.
Layout and behaviour are verified facts in BUILD_PROMPT.md section 3.1.
"""

import hashlib
import re
import urllib.parse
from datetime import date, datetime

import xlrd

from . import config
from .seasons import register_date, season_of


class SchemaError(Exception):
    """The source changed shape. Stop and report rather than store bad rows."""


# Column positions in the export (row 1 holds these headers)
EXPECTED_HEADERS = {
    0: "WAL No.", 1: "Water Source", 2: "Water Management Zone", 3: "Category",
    5: "WAL No.", 6: "Water Source", 7: "Water Management Zone", 8: "Category",
    10: "Application Number", 11: "Assigned", 12: "Trade Purpose", 13: "Volume (ML)", 14: "Price Paid",
}
N_COLS = 15
_COUNT = re.compile(r"listResult\.set\(\s*\d+\s*,\s*\d+\s*,\s*(\d+)\s*\)")


def _form(start: date, end: date) -> dict:
    return {"startDate": register_date(start), "endDate": register_date(end),
            "src": config.REGISTER_WATER_SOURCE_ID, "cat": ""}


def parse_count(html: str) -> int:
    match = _COUNT.search(html)
    if match:
        return int(match.group(1))
    if "<table" not in html and len(html.strip()) < 50:
        return 0  # empty result for a range with no trades
    raise SchemaError("register search: row count marker not found")


def parse_export(xls_bytes: bytes) -> list[list[str]]:
    try:
        sheet = xlrd.open_workbook(file_contents=xls_bytes).sheet_by_index(0)
    except xlrd.XLRDError as exc:
        raise SchemaError(f"register export is not a readable .xls: {exc}") from exc
    if sheet.nrows == 0:
        return []
    if sheet.ncols != N_COLS or sheet.nrows < 2:
        raise SchemaError(f"register export is {sheet.nrows} x {sheet.ncols}, expected a header and {N_COLS} columns")
    for col, name in EXPECTED_HEADERS.items():
        found = str(sheet.cell_value(1, col)).strip()
        if found != name:
            raise SchemaError(f"register export column {col} is '{found}', expected '{name}'")
    return [[str(sheet.cell_value(r, c)).strip() for c in range(N_COLS)] for r in range(2, sheet.nrows)]


def zone_of(wmz: str) -> str | None:
    text = wmz.lower()
    if "at picnic point" not in text:
        return None
    if "upstream" in text:
        return "Z10"
    if "downstream" in text:
        return "Z11"
    return None


def _number(text: str) -> float:
    """Volume or price; early seasons hold the literal text 'null' where nothing was recorded."""
    text = text.replace(",", "").replace("$", "").strip()
    if text.lower() in ("", "null", "-"):
        return 0.0
    try:
        return float(text)
    except ValueError as exc:
        raise SchemaError(f"register number '{text}' is not numeric") from exc


def classify(raw: list[str]) -> dict:
    """Derive side, zone, direction and flags from one raw export row (BUILD_PROMPT.md section 4)."""
    if len(raw) != N_COLS:
        raise SchemaError(f"register row has {len(raw)} fields")
    seller = raw[1].lower() == config.NSW_MURRAY
    buyer = raw[6].lower() == config.NSW_MURRAY
    if not (seller or buyer):
        raise SchemaError(f"register row {raw[10]} has no NSW Murray side")
    side = "seller" if seller else "buyer"
    wmz, category = (raw[2], raw[3]) if seller else (raw[7], raw[8])
    direction = "within" if seller and buyer else ("out" if seller else "in")
    try:
        traded = datetime.strptime(raw[11], "%d-%b-%Y").date()
    except ValueError as exc:
        raise SchemaError(f"register row {raw[10]} has date '{raw[11]}'") from exc
    purpose = raw[12]
    ml, price = _number(raw[13]), _number(raw[14])
    commercial = price > 0 and purpose.casefold() in ("standard commercial", "")
    return {
        "from_wal": raw[0], "from_ws": raw[1], "from_wmz": raw[2], "from_cat": raw[3],
        "to_wal": raw[5], "to_ws": raw[6], "to_wmz": raw[7], "to_cat": raw[8],
        "application_no": raw[10], "trade_date": traded.isoformat(), "purpose": purpose,
        "ml": ml, "price": price,
        "season": season_of(traded), "nswm_side": side, "zone": zone_of(wmz), "direction": direction,
        "nswm_category": category, "is_supp": int("supplementary" in category.lower()),
        "is_commercial": int(commercial),
        "row_hash": hashlib.sha1("\x1f".join(raw).encode("utf-8")).hexdigest(),
    }


def fetch_count(client, start: date, end: date) -> int:
    html = client.fetch(config.REGISTER_URL, form={"pageCommand": "search", "resultType": "modern",
                                                    **_form(start, end)})
    return parse_count(html.decode("utf-8", "ignore"))


def fetch_range(client, start: date, end: date) -> tuple[int, list[dict]]:
    """Return (register count, classified rows) for an inclusive date range."""
    expected = fetch_count(client, start, end)
    raw_rows = []
    for from_row in range(0, expected, config.REGISTER_EXPORT_MAX):
        to_row = min(from_row + config.REGISTER_EXPORT_MAX, expected) - 1
        query = {"pageCommand": "export", "resultType": "exel", "fromRow": from_row, "toRow": to_row,
                 **_form(start, end)}
        raw_rows.extend(parse_export(client.fetch(config.REGISTER_URL + "?" + urllib.parse.urlencode(query))))
    return expected, [classify(r) for r in raw_rows]
