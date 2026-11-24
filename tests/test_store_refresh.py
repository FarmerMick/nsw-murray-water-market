from datetime import date

from murraywater import refresh, store
from murraywater.seasons import season_bounds
from test_register import raw

from murraywater import register


def rows(*raws):
    return [register.classify(r) for r in raws]


def count(con):
    return con.execute("SELECT COUNT(*) FROM register_trades").fetchone()[0]


def test_replace_range_is_idempotent_and_replaces(con):
    start, end = date(2026, 9, 1), date(2026, 9, 30)
    batch = rows(raw(app="A"), raw(app="B"), raw(app="C", when="02-SEP-2026"))
    assert store.replace_register_range(con, start, end, batch, 3, "2026-09-23T19:00:00+10:00") == 3
    assert store.replace_register_range(con, start, end, batch, 3, "2026-09-24T19:00:00+10:00") == 3
    assert count(con) == 3
    store.replace_register_range(con, start, end, rows(raw(app="A")), 1, "2026-09-25T19:00:00+10:00")
    assert count(con) == 1  # rows no longer in the register for that range are removed


def test_identical_rows_collapse_but_same_application_different_rows_stay(con):
    start, end = date(2019, 7, 1), date(2020, 6, 30)
    same = raw(app="X", when="01-AUG-2019")
    batch = rows(same, same, raw(app="Y", when="01-AUG-2019", ml="10"), raw(app="Y", when="01-AUG-2019", ml="20"))
    assert store.replace_register_range(con, start, end, batch, 4, "2026-09-23T19:00:00+10:00") == 3
    detail = con.execute("SELECT detail FROM register_fetch").fetchone()[0]
    assert detail == "1 identical rows collapsed"


def test_register_plan(con):
    today = date(2026, 9, 23)  # a Wednesday
    first = refresh.register_plan(con, today, backfill_from=2024)
    assert first[:2] == [season_bounds(2024), season_bounds(2025)]
    assert first[2:] == [(date(2026, 7, 1), date(2026, 7, 31)), (date(2026, 8, 1), date(2026, 8, 31)),
                         (date(2026, 9, 1), date(2026, 9, 30))]
    stamp = "2026-09-23T19:00:00+10:00"
    for start, end in first:
        store.replace_register_range(con, start, end, rows(raw(when=start.strftime("%d-%b-%Y").upper())), 1, stamp)
    daily = refresh.register_plan(con, date(2026, 9, 24), backfill_from=2024)
    assert daily == [(date(2026, 8, 1), date(2026, 8, 31)), (date(2026, 9, 1), date(2026, 9, 30))]
    sunday = refresh.register_plan(con, date(2026, 9, 27), backfill_from=2024)
    assert season_bounds(2024) not in sunday and season_bounds(2025) not in sunday  # fetched after 31 Aug: final
    assert len(sunday) == 3


def test_previous_season_refetched_on_sundays_until_final(con):
    stamp = "2026-07-02T19:00:00+10:00"  # fetched before 31 Aug, so 2025-26 is not final yet
    start, end = season_bounds(2025)
    store.replace_register_range(con, start, end, rows(raw(when="02-JUL-2025")), 1, stamp)
    store.replace_register_range(con, date(2026, 7, 1), date(2026, 7, 31), rows(raw(when="01-JUL-2026")), 1, stamp)
    assert season_bounds(2025) not in refresh.register_plan(con, date(2026, 7, 8), backfill_from=2025)  # Wednesday
    assert season_bounds(2025) in refresh.register_plan(con, date(2026, 7, 12), backfill_from=2025)  # Sunday
