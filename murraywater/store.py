"""SQLite store. Register data is replaced range by range; MIL and Waterfind are upserted or snapshotted."""

import sqlite3
from datetime import date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS register_trades (
    row_hash TEXT PRIMARY KEY,
    application_no TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    season TEXT NOT NULL,
    from_wal TEXT, from_ws TEXT, from_wmz TEXT, from_cat TEXT,
    to_wal TEXT, to_ws TEXT, to_wmz TEXT, to_cat TEXT,
    purpose TEXT, ml REAL NOT NULL, price REAL NOT NULL,
    nswm_side TEXT, zone TEXT, direction TEXT, nswm_category TEXT,
    is_supp INTEGER NOT NULL, is_commercial INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_register_date ON register_trades(trade_date);
CREATE INDEX IF NOT EXISTS ix_register_season ON register_trades(season);
CREATE TABLE IF NOT EXISTS register_fetch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    range_start TEXT NOT NULL, range_end TEXT NOT NULL,
    expected INTEGER, stored INTEGER, fetched_at TEXT NOT NULL, status TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS wex_daily (
    trade_date TEXT PRIMARY KEY, season TEXT NOT NULL, ml REAL NOT NULL,
    min_price REAL, max_price REAL, avg_price REAL NOT NULL, listing_type TEXT NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wex_season (
    season TEXT PRIMARY KEY, sales INTEGER, ml REAL, total_cost REAL, vwap REAL, ave_price_reported REAL,
    low_price REAL, high_price REAL, allocation REAL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wex_alloc_history (
    season TEXT NOT NULL, announced TEXT NOT NULL, allocation REAL NOT NULL, PRIMARY KEY (season, announced)
);
CREATE TABLE IF NOT EXISTS wex_orderbook_snapshot (
    taken_at TEXT NOT NULL, side TEXT NOT NULL, serial TEXT, ml REAL, price REAL, listing_type TEXT
);
CREATE INDEX IF NOT EXISTS ix_orderbook_taken ON wex_orderbook_snapshot(taken_at);
CREATE TABLE IF NOT EXISTS wex_summary_snapshot (
    taken_at TEXT PRIMARY KEY, sell_low REAL, sell_high REAL, ml_for_sale REAL, sold_today REAL,
    buy_offer REAL, allocation REAL
);
CREATE TABLE IF NOT EXISTS waterfind_trades (
    zone TEXT NOT NULL, region TEXT, trade_date TEXT NOT NULL, ml REAL NOT NULL, price REAL NOT NULL,
    occurrence INTEGER NOT NULL, not_zone_specific INTEGER NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    PRIMARY KEY (zone, trade_date, ml, price, occurrence)
);
CREATE TABLE IF NOT EXISTS permanent_trades (
    row_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, season TEXT NOT NULL, trade_date TEXT NOT NULL,
    category TEXT, cat_code TEXT NOT NULL, units REAL NOT NULL, price REAL NOT NULL, application_no TEXT,
    from_wal TEXT, to_wal TEXT, is_sale INTEGER NOT NULL, fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_permanent_season ON permanent_trades(season);
CREATE TABLE IF NOT EXISTS permanent_fetch (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, season TEXT NOT NULL,
    expected INTEGER, stored INTEGER, fetched_at TEXT NOT NULL, status TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL, status TEXT NOT NULL, detail TEXT, requests INTEGER
);
"""

REGISTER_COLUMNS = ("row_hash", "application_no", "trade_date", "season", "from_wal", "from_ws", "from_wmz",
                    "from_cat", "to_wal", "to_ws", "to_wmz", "to_cat", "purpose", "ml", "price", "nswm_side",
                    "zone", "direction", "nswm_category", "is_supp", "is_commercial")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def replace_register_range(con, start: date, end: date, rows: list[dict], expected: int, fetched_at: str) -> int:
    """Swap every stored row in [start, end] for the fresh set. Identical rows collapse; returns rows stored."""
    unique = {r["row_hash"]: r for r in rows}
    cols = ", ".join(REGISTER_COLUMNS + ("fetched_at",))
    marks = ", ".join("?" for _ in range(len(REGISTER_COLUMNS) + 1))
    with con:
        con.execute("DELETE FROM register_trades WHERE trade_date BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))
        con.executemany(f"INSERT OR REPLACE INTO register_trades ({cols}) VALUES ({marks})",
                        [tuple(r[c] for c in REGISTER_COLUMNS) + (fetched_at,) for r in unique.values()])
        detail = f"{len(rows) - len(unique)} identical rows collapsed" if len(unique) < len(rows) else None
        con.execute("INSERT INTO register_fetch (range_start, range_end, expected, stored, fetched_at, status, detail)"
                    " VALUES (?, ?, ?, ?, ?, 'ok', ?)",
                    (start.isoformat(), end.isoformat(), expected, len(unique), fetched_at, detail))
    return len(unique)


def record_register_failure(con, start: date, end: date, expected, got, fetched_at: str, detail: str) -> None:
    with con:
        con.execute("INSERT INTO register_fetch (range_start, range_end, expected, stored, fetched_at, status, detail)"
                    " VALUES (?, ?, ?, ?, ?, 'failed', ?)",
                    (start.isoformat(), end.isoformat(), expected, got, fetched_at, detail))


def register_range_fetched(con, start: date, end: date, after: date | None = None) -> bool:
    sql = "SELECT 1 FROM register_fetch WHERE status = 'ok' AND range_start = ? AND range_end = ?"
    args = [start.isoformat(), end.isoformat()]
    if after is not None:
        sql += " AND substr(fetched_at, 1, 10) > ?"
        args.append(after.isoformat())
    return con.execute(sql + " LIMIT 1", args).fetchone() is not None


def season_has_rows(con, season: str) -> bool:
    return con.execute("SELECT 1 FROM register_trades WHERE season = ? LIMIT 1", (season,)).fetchone() is not None


def upsert_wex_daily(con, rows: list[dict], seen_at: str) -> None:
    with con:
        con.executemany(
            "INSERT INTO wex_daily (trade_date, season, ml, min_price, max_price, avg_price, listing_type, first_seen,"
            " last_seen) VALUES (:trade_date, :season, :ml, :min_price, :max_price, :avg_price, :listing_type, :seen,"
            " :seen) ON CONFLICT(trade_date) DO UPDATE SET season = excluded.season, ml = excluded.ml,"
            " min_price = excluded.min_price, max_price = excluded.max_price, avg_price = excluded.avg_price,"
            " listing_type = excluded.listing_type, last_seen = excluded.last_seen",
            [dict(r, seen=seen_at) for r in rows])


def upsert_wex_seasons(con, rows: list[dict], at: str) -> None:
    with con:
        con.executemany(
            "INSERT OR REPLACE INTO wex_season (season, sales, ml, total_cost, vwap, ave_price_reported, low_price,"
            " high_price, allocation, updated_at) VALUES (:season, :sales, :ml, :total_cost, :vwap,"
            " :ave_price_reported, :low_price, :high_price, :allocation, :at)",
            [dict(r, at=at) for r in rows])


def replace_alloc_history(con, rows: list[dict]) -> None:
    with con:
        con.execute("DELETE FROM wex_alloc_history")
        con.executemany("INSERT OR REPLACE INTO wex_alloc_history (season, announced, allocation)"
                        " VALUES (:season, :announced, :allocation)", rows)


def insert_market_snapshot(con, orderbook: list[dict], summary: dict, taken_at: str) -> None:
    with con:
        con.executemany("INSERT INTO wex_orderbook_snapshot (taken_at, side, serial, ml, price, listing_type)"
                        " VALUES (:taken_at, :side, :serial, :ml, :price, :listing_type)",
                        [dict(r, taken_at=taken_at) for r in orderbook])
        con.execute("INSERT OR REPLACE INTO wex_summary_snapshot (taken_at, sell_low, sell_high, ml_for_sale,"
                    " sold_today, buy_offer, allocation) VALUES (:taken_at, :sell_low, :sell_high, :ml_for_sale,"
                    " :sold_today, :buy_offer, :allocation)", dict(summary, taken_at=taken_at))


def upsert_waterfind(con, rows: list[dict], seen_at: str) -> int:
    before = con.execute("SELECT COUNT(*) FROM waterfind_trades").fetchone()[0]
    with con:
        con.executemany(
            "INSERT INTO waterfind_trades (zone, region, trade_date, ml, price, occurrence, not_zone_specific,"
            " first_seen, last_seen) VALUES (:zone, :region, :trade_date, :ml, :price, :occurrence,"
            " :not_zone_specific, :seen, :seen) ON CONFLICT(zone, trade_date, ml, price, occurrence) DO UPDATE SET"
            " not_zone_specific = excluded.not_zone_specific, last_seen = excluded.last_seen",
            [dict(r, seen=seen_at) for r in rows])
    return con.execute("SELECT COUNT(*) FROM waterfind_trades").fetchone()[0] - before


def log_fetch(con, source: str, started_at: str, finished_at: str, status: str, detail: str | None,
              requests: int | None) -> None:
    with con:
        con.execute("INSERT INTO fetch_log (source, started_at, finished_at, status, detail, requests)"
                    " VALUES (?, ?, ?, ?, ?, ?)", (source, started_at, finished_at, status, detail, requests))


def last_success(con, source: str) -> str | None:
    row = con.execute("SELECT MAX(finished_at) FROM fetch_log WHERE source = ? AND status IN ('ok', 'warn')",
                      (source,)).fetchone()
    return row[0] if row else None


PERMANENT_COLUMNS = ("row_hash", "kind", "season", "trade_date", "category", "cat_code", "units", "price",
                     "application_no", "from_wal", "to_wal", "is_sale")


def replace_permanent_season(con, kind: str, season: str, rows: list[dict], expected: int, fetched_at: str) -> int:
    """Swap every stored row of one report and season for the fresh set; returns rows stored."""
    cols = ", ".join(PERMANENT_COLUMNS + ("fetched_at",))
    marks = ", ".join("?" for _ in range(len(PERMANENT_COLUMNS) + 1))
    with con:
        con.execute("DELETE FROM permanent_trades WHERE kind = ? AND season = ?", (kind, season))
        con.executemany(f"INSERT OR REPLACE INTO permanent_trades ({cols}) VALUES ({marks})",
                        [tuple(r[c] for c in PERMANENT_COLUMNS) + (fetched_at,) for r in rows])
        con.execute("INSERT INTO permanent_fetch (kind, season, expected, stored, fetched_at, status)"
                    " VALUES (?, ?, ?, ?, ?, 'ok')", (kind, season, expected, len(rows), fetched_at))
    return len(rows)


def record_permanent_failure(con, kind: str, season: str, detail: str, fetched_at: str) -> None:
    with con:
        con.execute("INSERT INTO permanent_fetch (kind, season, fetched_at, status, detail) VALUES (?, ?, ?, 'failed', ?)",
                    (kind, season, fetched_at, detail))


def permanent_final(con) -> set[tuple[str, str]]:
    """(kind, season) pairs fetched after 31 August of the year after the season ended."""
    final = set()
    for kind, season, fetched in con.execute("SELECT kind, season, MAX(fetched_at) FROM permanent_fetch"
                                             " WHERE status = 'ok' GROUP BY kind, season"):
        if fetched[:10] > f"{int(season[:4]) + 1}-08-31":
            final.add((kind, season))
    return final
