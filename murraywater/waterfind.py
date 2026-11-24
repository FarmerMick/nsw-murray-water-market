"""Waterfind public homepage: last 10 settled temporary trades per zone. Indicator only.

Waterfind's terms allow download for private, non-commercial use only (BUILD_PROMPT.md section 3.3).
Parsing is defensive: a changed page logs a warning and yields nothing; it never fails the run.
"""

import re
from datetime import datetime

from . import config

_BLOCK = re.compile(r'<div class="wf-tab-content[^"]*" id="tab_content_([^"]+)">')
_REGION = re.compile(r'wf-region-name[^>]*>([^<]+)<')
_TRADE = re.compile(r'wf-settled-trade-date">([^<]+)</span>\s*<span class="wf-settled-trade-volume">'
                    r'([\d.,]+)\s*ML</span>\s*@\s*<span class="wf-settled-trade-price">\$([\d.,]+)</span>')


def parse_zones(html: str) -> dict[str, dict]:
    """Every zone block on the page: {tab id: {"region": name, "temporary": [(date, ml, price), ...]}}."""
    starts = [(m.start(), m.group(1)) for m in _BLOCK.finditer(html)]
    zones = {}
    for i, (pos, tab) in enumerate(starts):
        block = html[pos: starts[i + 1][0] if i + 1 < len(starts) else len(html)]
        region = _REGION.search(block)
        temp_start = block.find(f'id="tab_content_temp_trades_{tab}"')
        perm_start = block.find(f'id="tab_content_perm_trades_{tab}"')
        if not region or temp_start < 0:
            continue
        temp = block[temp_start: perm_start if perm_start > temp_start else len(block)]
        trades = []
        for day, ml, price in _TRADE.findall(temp):
            trades.append((datetime.strptime(day.strip(), "%d/%m/%Y").date().isoformat(),
                           float(ml.replace(",", "")), float(price.replace(",", ""))))
        zones[tab] = {"region": region.group(1).strip(), "temporary": trades}
    return zones


def murray_trades(zones: dict[str, dict]) -> list[dict]:
    """Zone 10 and 11 temporary trades. A list identical to another zone's is flagged not zone-specific."""
    out = []
    for tab, zone in config.WATERFIND_ZONES.items():
        if tab not in zones:
            continue
        trades = zones[tab]["temporary"]
        shared = any(other["temporary"] == trades for t, other in zones.items() if t != tab and trades)
        seen = {}
        for pos, (day, ml, price) in enumerate(trades):
            key = (day, ml, price)
            seen[key] = seen.get(key, 0) + 1
            out.append({"zone": zone, "region": zones[tab]["region"], "trade_date": day, "ml": ml, "price": price,
                        "occurrence": seen[key], "list_pos": pos, "not_zone_specific": int(shared)})
    return out
