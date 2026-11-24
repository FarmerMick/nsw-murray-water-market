"""Write output/dashboard.html (the template with the metrics embedded as JSON) and the shareable review copy."""

import copy
import html
import json
import re
import shutil
import subprocess
from pathlib import Path

from . import config

PLACEHOLDER = "/*__DASHBOARD_DATA__*/null"
ECHARTS_TAG = '<script src="../assets/echarts.min.js"></script>'
_PRIVATE = re.compile(r"<!--private-->.*?<!--/private-->", re.S)
_LOCAL_ONLY = re.compile(r"<!--local-only-->.*?<!--/local-only-->", re.S)
_REVIEW_ONLY = re.compile(r"<!--review-only (.*?) -->", re.S)
_PUBLIC_ONLY = re.compile(r"<!--public-only (.*?) -->", re.S)
PUBLIC_URL = "https://farmermick.github.io/nsw-murray-water-market/"
PUBLIC_META = ('<meta name="description" content="What NSW Murray water is selling for: allocation trades in Zones 10 and 11, '
               'Murray Irrigation\'s Water Exchange and permanent entitlement sales, rebuilt daily from public data.">\n'
               '<meta property="og:title" content="NSW Murray Water Market">\n'
               f'<meta property="og:url" content="{PUBLIC_URL}">\n'
               '<meta property="og:type" content="website">')
# Byte-identical to assets/echarts.min.js (sha256 bf4a2235…07b1), checked 23 Sep 2026
ECHARTS_CDN = '<script src="https://cdnjs.cloudflare.com/ajax/libs/echarts/5.6.0/echarts.min.js"></script>'
WEB_FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Carlito:wght@400;700'
             '&amp;family=Gelasio:wght@400;600&amp;display=swap">')
BROWSERS = (Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            # macOS
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"))


SUMMARY_MARK = "<!--__STATIC_SUMMARY__-->"
SEGMENT_NAMES = {"Z10": "River Zone 10 (above the Choke)", "Z11": "River Zone 11 (below the Choke)",
                 "WEX": "MIL Water Exchange"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _ml(v) -> str:
    return "–" if v is None else f"{v:,.0f} ML"


def _price(v) -> str:
    return "–" if v is None else f"${v:,.0f}/ML"


def _change(now, before) -> str:
    return "–" if now is None or not before else f"{(now - before) / before * 100:+.0f}%"


def _date(iso: str) -> str:
    y, m, d = (int(x) for x in iso[:10].split("-"))
    return f"{d} {MONTHS[m - 1]} {y}"


def static_summary(data: dict) -> str:
    """Headline figures as plain HTML, for viewers that block scripts (email and file previews)."""
    season = data.get("seasons", {}).get(data.get("current_season"))
    if not season:
        return ""
    k, prev = season["kpis"], season.get("prev_same_day") or {}
    esc = html.escape
    rows = "".join(
        f"<tr><td>{esc(SEGMENT_NAMES[s])}</td><td class='n'>{_ml(k[s]['ml'])}</td>"
        f"<td class='n'>{_price(k[s]['vwap'])}</td>"
        f"<td class='n'>{_change(k[s]['vwap'], (prev.get(s) or {}).get('vwap'))}</td>"
        f"<td class='n'>{_change(k[s]['ml'], (prev.get(s) or {}).get('ml'))}</td></tr>" for s in ("Z10", "Z11", "WEX"))
    rows += (f"<tr><td><b>Total</b></td><td class='n'><b>{_ml(k['total_ml'])}</b></td><td></td><td></td>"
             f"<td class='n'>{_change(k['total_ml'], prev.get('total_ml'))}</td></tr>")
    months = ""
    for month, segs in sorted(season["monthly"].items()):
        cells = "".join(f"<td class='n'>{_ml((segs.get(s) or {}).get('ml'))}</td>"
                        f"<td class='n'>{_price((segs.get(s) or {}).get('vwap'))}</td>" for s in ("Z10", "Z11", "WEX"))
        months += f"<tr><td>{MONTHS[int(month[5:]) - 1]} {month[:4]}</td>{cells}</tr>"
    previous = f"{int(data['current_season'][:4]) - 1}-{int(data['current_season'][:4]) % 100:02d}"
    return (
        f"<section><div class='head'><h2>Season to date, {esc(data['current_season'])}</h2>"
        f"<p>As of {esc(_date(data['as_of']))}. Changes are against the same point in {previous}.</p></div>"
        "<div class='card'><table><thead><tr><th>Segment</th><th class='n'>Volume sold</th>"
        "<th class='n'>Average price</th><th class='n'>Price change</th><th class='n'>Volume change</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "<p class='note'>Average prices are volume-weighted. River trades count when marked Standard Commercial and "
        "priced above $0, excluding supplementary water.</p></div></section>"
        "<section><div class='card'><h3>Month by month</h3><table><thead><tr><th>Month</th>"
        "<th class='n'>Zone 10</th><th class='n'>$/ML</th><th class='n'>Zone 11</th><th class='n'>$/ML</th>"
        f"<th class='n'>MIL</th><th class='n'>$/ML</th></tr></thead><tbody>{months}</tbody></table></div></section>")


def render(data: dict, template: str) -> str:
    if PLACEHOLDER not in template:
        raise ValueError("dashboard template is missing the data placeholder")
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    payload = payload.replace("</", "<\\/")  # never close the script element early
    page = template.replace(SUMMARY_MARK, static_summary(data)) if SUMMARY_MARK in template else template
    if data.get("as_of"):  # written in directly so it shows even when scripts are blocked
        page = page.replace('<span class="asof"></span>', f'<span class="asof">{html.escape(_date(data["as_of"]))}</span>')
    return page.replace(PLACEHOLDER, payload)


def write(data: dict, path: Path = config.DASHBOARD_PATH) -> Path:
    template = config.TEMPLATE_PATH.read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(render(data, template), encoding="utf-8")
    tmp.replace(path)
    return path


def review_data(data: dict) -> dict:
    """A copy safe to share: Waterfind's terms allow private, non-commercial use only, so its data is removed."""
    shared = copy.deepcopy(data)
    for season in shared["seasons"].values():
        season["waterfind"] = []
    shared["sources"].pop("waterfind", None)
    shared["review"] = True
    return shared


def render_review(data: dict, template: str, echarts_js: str) -> str:
    """Self-contained review copy: no Waterfind, draft labels switched on, ECharts inlined so one file works alone."""
    if ECHARTS_TAG not in template:
        raise ValueError("dashboard template is missing the ECharts script tag")
    if "</script" in echarts_js.lower():
        raise ValueError("ECharts bundle can't be inlined safely")
    page = _REVIEW_ONLY.sub(r"\1", _PRIVATE.sub("", template))
    page = page.replace(ECHARTS_TAG, "<script>" + echarts_js + "</script>")
    return render(review_data(data), page)


def render_web(data: dict, template: str) -> str:
    """The review copy as a web page for a private claude.ai link: fully interactive in any browser, phones included.

    The host wraps the page in its own skeleton and theme switch, so the page keeps only its title, styles, content
    and scripts; ECharts comes from cdnjs, the one script host its security policy allows.
    """
    if ECHARTS_TAG not in template:
        raise ValueError("dashboard template is missing the ECharts script tag")
    page = _REVIEW_ONLY.sub(r"\1", _LOCAL_ONLY.sub("", _PRIVATE.sub("", template)))
    page = render(review_data(data), page.replace(ECHARTS_TAG, ECHARTS_CDN))
    head = re.search(r"<head>(.*?)</head>", page, re.S).group(1)
    body = re.search(r"<body>(.*?)</body>", page, re.S).group(1)
    head = re.sub(r"<meta\b[^>]*>\s*", "", head).strip()  # the host's skeleton supplies charset and viewport
    title = re.search(r"<title>.*?</title>", head, re.S).group(0)
    return title + "\n" + WEB_FONTS + "\n" + head.replace(title, "", 1).strip() + "\n" + body.strip() + "\n"


def render_public(data: dict, template: str) -> str:
    """The public web page: no Waterfind data, public notes switched on, ECharts from cdnjs, one standalone file."""
    if ECHARTS_TAG not in template:
        raise ValueError("dashboard template is missing the ECharts script tag")
    page = _PUBLIC_ONLY.sub(r"\1", _REVIEW_ONLY.sub("", _PRIVATE.sub("", template)))
    page = page.replace(ECHARTS_TAG, ECHARTS_CDN).replace("</title>", "</title>\n" + PUBLIC_META + "\n" + WEB_FONTS, 1)
    shared = review_data(data)
    shared.pop("review", None)
    return render(shared, page)


def write_public(data: dict, folder: Path) -> list[Path]:
    """Write the public site: index.html, the same data as JSON for anyone who wants it, and .nojekyll."""
    folder.mkdir(parents=True, exist_ok=True)
    template = config.TEMPLATE_PATH.read_text(encoding="utf-8")
    index, blob = folder / "index.html", folder / "data.json"
    index.write_text(render_public(data, template), encoding="utf-8")
    shared = review_data(data)
    shared.pop("review", None)
    blob.write_text(json.dumps(shared, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    (folder / ".nojekyll").write_text("", encoding="utf-8")
    return [index, blob]


_CHART_BLOCK = re.compile(r'(<div class="chart[^"]*" id="chart-[a-z]+"[^>]*>)(.*?)(</div>\s*<div class="tableview")', re.S)
_SVG = re.compile(r"<svg\b[^>]*>.*?</svg>", re.S)
_SVG_SIZE = re.compile(r'width="(\d+(?:\.\d+)?)" height="(\d+(?:\.\d+)?)" style="[^"]*"')
SNAPSHOT_NOTE = ('<div class="draft snapshot-note">This is a static preview: the charts are pictures. Open the file in '
                 'Chrome, Edge, Safari or Firefox to switch seasons, hover for detail or see any chart as a table.</div>')


def freeze_snapshot(dom: str) -> str:
    """Turn a browser capture of the rendered page into a file whose charts show even when scripts are blocked.

    Each chart keeps only its drawn SVG, made to scale with its box. The page is pinned to the light theme the
    drawings were made in. The scripts stay, so a browser swaps in the live, interactive charts.
    """
    def chart(match):
        opening = re.sub(r'\s(?:_echarts_instance_|style)="[^"]*"', "", match.group(1))
        svg = _SVG.search(match.group(2))
        drawn = "" if not svg else _SVG_SIZE.sub(
            r'viewBox="0 0 \1 \2" width="100%" height="100%" preserveAspectRatio="xMidYMid meet" style="display:block"',
            svg.group(0), count=1)
        return opening + drawn + match.group(3)

    frozen, charts = _CHART_BLOCK.subn(chart, dom)
    if charts == 0 or "<svg" not in frozen:
        raise ValueError("the browser capture has no drawn charts")
    frozen = re.sub(r"<html([^>]*)>", r'<html\1 data-snapshot="1" data-theme="light">', frozen, count=1)
    frozen = frozen.replace('<main class="wrap">', '<main class="wrap">' + SNAPSHOT_NOTE, 1)
    return frozen if frozen.lstrip().lower().startswith("<!doctype") else "<!doctype html>\n" + frozen


def _browser() -> str | None:
    found = next((b for b in BROWSERS if b.exists()), None)
    return str(found) if found else (shutil.which("msedge") or shutil.which("chrome"))


def capture(browser: str, page: Path) -> str:
    """The page as a browser leaves it once its scripts have drawn everything."""
    done = subprocess.run([browser, "--headless=new", "--disable-gpu", "--window-size=1400,1000",
                           "--virtual-time-budget=6000", "--dump-dom", page.resolve().as_uri()],
                          check=True, timeout=180, capture_output=True)
    return done.stdout.decode("utf-8")


def write_review(data: dict, pdf: bool = True) -> list[Path]:
    """Write the review copy to output/review/: a pre-drawn, self-contained HTML file and, optionally, a PDF."""
    template = config.TEMPLATE_PATH.read_text(encoding="utf-8")
    echarts_js = (config.ASSETS_DIR / "echarts.min.js").read_text(encoding="utf-8")
    folder = config.OUTPUT_DIR / "review"
    folder.mkdir(parents=True, exist_ok=True)
    # A unique name per export, so an older copy can't be mistaken for the latest one
    for old in folder.glob("Murray Water Market - review*.*"):
        old.unlink()
    stem = f"Murray Water Market - review {data['generated_at'][:10]} {data['generated_at'][11:13]}{data['generated_at'][14:16]}"
    html_path, source = folder / f"{stem}.html", folder / ".review-source.html"
    page = render_review(data, template, echarts_js)
    source.write_text(page, encoding="utf-8")
    web_path = folder / "web-page.html"
    web_path.write_text(render_web(data, template), encoding="utf-8")
    browser = _browser()
    try:
        # Without a browser the copy still works, but its charts only appear where scripts can run.
        html_path.write_text(freeze_snapshot(capture(browser, source)) if browser else page, encoding="utf-8")
        written = [html_path, web_path]
        if pdf and browser:
            pdf_path = folder / f"{stem}.pdf"
            # ?print=1 draws the charts at paper size from the start; 1047 px is A4 landscape less 10 mm margins
            subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            "--window-size=1047,1400", "--virtual-time-budget=6000", f"--print-to-pdf={pdf_path}",
                            source.resolve().as_uri() + "?print=1"], check=True, timeout=180,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            written.append(pdf_path)
    finally:
        source.unlink(missing_ok=True)
    return written
