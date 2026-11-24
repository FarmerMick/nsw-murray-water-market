"""Irrigation season (financial year, 1 Jul-30 Jun) helpers."""

import calendar
from datetime import date

MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def season_start_year(d: date) -> int:
    return d.year if d.month >= 7 else d.year - 1


def season_label(start_year: int) -> str:
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def season_of(d: date) -> str:
    return season_label(season_start_year(d))


def label_start_year(label: str) -> int:
    return int(label[:4])


def season_bounds(start_year: int) -> tuple[date, date]:
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)


def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def previous_month_start(d: date) -> date:
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


def month_ranges(start_year: int, upto: date | None = None) -> list[tuple[date, date]]:
    """Calendar-month ranges of a season, stopping at the month containing `upto`."""
    first, last = season_bounds(start_year)
    if upto is not None and upto < last:
        last = upto
    ranges, d = [], first
    while d <= last:
        ranges.append((d, month_end(d)))
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return ranges


def day_of_season(d: date) -> int:
    """Days since 1 July of the trade's season (1 July is day 0)."""
    return (d - date(season_start_year(d), 7, 1)).days


def register_date(d: date) -> str:
    """Register form format, e.g. 01-JUL-2026."""
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"
