# NSW Murray Water Market

**Live dashboard: https://farmermick.github.io/nsw-murray-water-market/**

What NSW Murray water is selling for, rebuilt every evening from public data:

- **Allocation (temporary) water** sold in River Zone 10 (above the Barmah Choke) and Zone 11 (below it), from the NSW Public Water Register, and on Murray Irrigation's Water Exchange.
- **Permanent entitlement** sales of General and High Security share components, from the register's share assignments (s71Q) and licence transfers (s71M).
- Season-to-date and last-30-day prices, the Barmah Choke spread, weekly and cumulative volume, and every season back to 1999–00 (MIL) and 2004–05 (register).

## How it works

A GitHub Actions workflow runs daily at 09:00 UTC. It restores the SQLite database from the Actions cache, fetches new trades politely (one request every 1.5 s per host), rebuilds one static page and publishes it to GitHub Pages. If a source fails, the page says so; if the page itself goes more than 36 hours without a rebuild, it warns the reader.

The page also publishes its data as [`data.json`](https://farmermick.github.io/nsw-murray-water-market/data.json).

## Definitions that matter

- **Not every registered trade is a sale.** Most registered allocation volume moves between related parties, to environmental use or out of carryover parking at no or token prices. Only trades marked Standard Commercial and priced above $0 count, excluding supplementary water.
- **MIL's Water Exchange sits outside the state register**, so its volume adds to the register's. That's derived from the data, not confirmed by MIL or WaterNSW.
- **Price** is the volume-weighted average with outliers excluded: a trade priced below 0.5× or above 2× the recent median for its segment. For entitlements, which have no recorded trade purpose, outliers are also left out of volume.

Full definitions are in the page footer.

## Run it yourself

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m murraywater refresh --no-open --skip-waterfind   # first run backfills from 2004-05
.venv/bin/python -m murraywater publish --out site                   # writes site/index.html
.venv/bin/python -m pytest -q
```

## Sources and attribution

- NSW Public Water Register: © State of New South Wales through WaterNSW. WaterNSW doesn't guarantee the accuracy of pricing data.
- Murray Irrigation Limited Water Exchange data: © Murray Irrigation Limited, reproduced with attribution from murrayirrigation.com.au.

Independent and unofficial; not affiliated with WaterNSW or Murray Irrigation. General information only, not financial advice. Charts use [Apache ECharts](https://echarts.apache.org/) (Apache License 2.0).
