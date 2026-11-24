"""One refresh: fetch every source, store, compute, render. Exit 0 ok, 1 a source failed, 2 a schema changed."""

import json
import logging
import logging.handlers
import webbrowser
from datetime import date, datetime

from . import config, metrics, mil, permanent, register, render, store, waterfind
from .http import FetchError, PoliteClient
from .seasons import month_ranges, month_start, previous_month_start, season_bounds, season_label, season_start_year

log = logging.getLogger("murraywater")


def now_local() -> datetime:
    return datetime.now(config.TZ)


def stamp() -> str:
    return now_local().isoformat(timespec="seconds")


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(config.LOG_DIR / "refresh.log", maxBytes=1_000_000,
                                                        backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(file_handler)
    root.addHandler(console)


def register_plan(con, today: date, backfill_from: int, full: bool = False) -> list[tuple[date, date]]:
    """Ranges to fetch this run (BUILD_PROMPT.md section 5)."""
    current = season_start_year(today)
    sunday = today.weekday() == 6
    plan = []
    for year in range(backfill_from, current):
        start, end = season_bounds(year)
        if store.register_range_fetched(con, start, end, after=date(year + 1, 8, 31)):
            continue  # final
        if full or sunday or not store.register_range_fetched(con, start, end):
            plan.append((start, end))
    months = month_ranges(current, upto=today)
    if full or sunday or not store.season_has_rows(con, season_label(current)):
        plan.extend(months)
    else:
        keep = {month_start(today), previous_month_start(today)}
        plan.extend(r for r in months if r[0] in keep)
    return plan


def refresh_register(con, client, today: date, backfill_from: int, full: bool) -> tuple[str, str]:
    plan = register_plan(con, today, backfill_from, full)
    stored, failures = 0, []
    for start, end in plan:
        try:
            for attempt in (1, 2):
                expected, rows = register.fetch_range(client, start, end)
                if len(rows) == expected:
                    stored += store.replace_register_range(con, start, end, rows, expected, stamp())
                    break
                log.warning("register %s..%s: got %d rows, expected %d (attempt %d)", start, end, len(rows),
                            expected, attempt)
            else:
                store.record_register_failure(con, start, end, expected, len(rows), stamp(), "row count mismatch")
                failures.append(f"{start}..{end}")
        except FetchError as exc:  # one range failing shouldn't stop the others; schema changes still stop
            store.record_register_failure(con, start, end, None, None, stamp(), str(exc))
            failures.append(f"{start}..{end}")
            log.error("register %s..%s: %s", start, end, exc)
    detail = f"{len(plan)} ranges, {stored} rows stored"
    if failures:
        return "failed", detail + f"; kept old rows for {', '.join(failures)}"
    return "ok", detail


def refresh_permanent(con, client, today: date, backfill_from: int) -> tuple[str, str]:
    todo = permanent.plan(store.permanent_final(con), today, backfill_from)
    stored, failures = 0, []
    for kind, year in todo:
        label = season_label(year)
        try:
            expected, rows = permanent.fetch_season(client, kind, year)
            if len(rows) != expected:
                store.record_permanent_failure(con, kind, label, f"got {len(rows)} rows, expected {expected}", stamp())
                failures.append(f"{kind} {label}")
                continue
            stored += store.replace_permanent_season(con, kind, label, rows, expected, stamp())
        except FetchError as exc:
            store.record_permanent_failure(con, kind, label, str(exc), stamp())
            failures.append(f"{kind} {label}")
            log.error("permanent %s %s: %s", kind, label, exc)
    detail = f"{len(todo)} seasons, {stored} rows stored"
    if failures:
        return "failed", detail + f"; kept old rows for {', '.join(failures)}"
    return "ok", detail


def refresh_mil(con, client) -> tuple[str, str]:
    raw = mil.fetch(client)
    parsed = mil.parse_all(raw)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "mil_latest.json").write_text(json.dumps(raw), encoding="utf-8")
    at = stamp()
    store.upsert_wex_daily(con, parsed["daily"], at)
    store.upsert_wex_seasons(con, parsed["seasons"], at)
    store.replace_alloc_history(con, parsed["alloc_history"])
    store.insert_market_snapshot(con, parsed["orderbook"], parsed["summary"], at)
    return "ok", f"{len(parsed['daily'])} daily rows, {len(parsed['seasons'])} seasons"


def refresh_waterfind(con, client) -> tuple[str, str]:
    html = client.fetch(config.WATERFIND_URL).decode("utf-8", "ignore")
    rows = waterfind.murray_trades(waterfind.parse_zones(html))
    if not rows:
        return "warn", "no Zone 10/11 trade blocks found; page layout may have changed"
    added = store.upsert_waterfind(con, rows, stamp())
    return "ok", f"{len(rows)} listed, {added} new"


def render_only(no_open: bool = False) -> int:
    """Rebuild the dashboard from stored data without fetching anything."""
    setup_logging()
    con = store.connect(config.DB_PATH)
    path = render.write(metrics.build(con, now_local().date(), now_local()))
    log.info("dashboard written to %s (no fetch)", path)
    if not no_open:
        webbrowser.open(path.as_uri())
    return 0


def export_review(pdf: bool = True) -> int:
    """Write the shareable review copy (no Waterfind data) from stored data, without fetching."""
    setup_logging()
    con = store.connect(config.DB_PATH)
    for path in render.write_review(metrics.build(con, now_local().date(), now_local()), pdf=pdf):
        log.info("review copy written to %s", path)
    return 0


def publish_site(folder) -> int:
    """Write the public site from stored data, without fetching."""
    setup_logging()
    con = store.connect(config.DB_PATH)
    for path in render.write_public(metrics.build(con, now_local().date(), now_local()), folder):
        log.info("public site written to %s", path)
    return 0


def run(no_open: bool = False, backfill_from: int = config.REGISTER_FIRST_SEASON, full: bool = False,
        skip_waterfind: bool = False, client=None) -> int:
    setup_logging()
    con = store.connect(config.DB_PATH)
    client = client or PoliteClient()
    today = now_local().date()
    exit_code = 0
    steps = [("register", lambda: refresh_register(con, client, today, backfill_from, full)),
             ("permanent", lambda: refresh_permanent(con, client, today, backfill_from)),
             ("mil", lambda: refresh_mil(con, client))]
    if not skip_waterfind:
        steps.append(("waterfind", lambda: refresh_waterfind(con, client)))
    for source, step in steps:
        started, before = stamp(), client.requests_made
        try:
            status, detail = step()
        except register.SchemaError as exc:
            status, detail = "schema", str(exc)
            exit_code = 2
        except (FetchError, OSError, ValueError) as exc:
            status, detail = "failed", f"{type(exc).__name__}: {exc}"
        if status == "failed" and exit_code == 0:
            exit_code = 1
        store.log_fetch(con, source, started, stamp(), status, detail, client.requests_made - before)
        (log.info if status in ("ok", "warn") else log.error)("%s: %s (%s)", source, status, detail)
    data = metrics.build(con, today, now_local())
    path = render.write(data)
    log.info("dashboard written to %s", path)
    if not no_open:
        webbrowser.open(path.as_uri())
    return exit_code
