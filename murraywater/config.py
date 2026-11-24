"""Paths and constants. Source facts are documented in BUILD_PROMPT.md section 3."""

from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
ASSETS_DIR = ROOT / "assets"
TEMPLATE_PATH = ROOT / "templates" / "dashboard.html"
DB_PATH = DATA_DIR / "murray_water.sqlite"
DASHBOARD_PATH = OUTPUT_DIR / "dashboard.html"

TZ = ZoneInfo("Australia/Sydney")

USER_AGENT = "MurrayWaterDashboard/1.2 (+https://github.com/FarmerMick/nsw-murray-water-market)"
MIN_GAP_SECONDS = 1.5  # between requests to the same host
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3

# NSW Public Water Register (WaterNSW)
REGISTER_URL = "https://waterregister.waternsw.com.au/AllocationResult"
REGISTER_WATER_SOURCE_ID = "11904"  # New South Wales Murray Regulated River
REGISTER_EXPORT_MAX = 1000
REGISTER_FIRST_SEASON = 2004  # 2004-05, the register's first financial year
NSW_MURRAY = "new south wales murray regulated river water source"

# Murray Irrigation Water Exchange (undocumented API behind the public page)
MIL_BASE = "https://api01.murrayirrigation.com.au/v1.0/market/"
MIL_ENDPOINTS = ("summary", "dailysales", "exchangetables", "waterdata")

# Waterfind public homepage (private, non-commercial use only)
WATERFIND_URL = "https://www.waterfind.com.au/"
WATERFIND_ZONES = {"10": "Z10", "11": "Z11"}  # homepage tab id -> zone

STALE_HOURS = 36
DETAIL_SEASONS = 5  # seasons embedded at trade level in the dashboard

# Outlier rule [A]: see BUILD_PROMPT.md section 4
OUTLIER_LOW = 0.5
OUTLIER_HIGH = 2.0
OUTLIER_WINDOW_DAYS = 30
OUTLIER_MIN_TRADES = 10

RECENT_DAYS = 30  # "last 30 days" price cards and the trailing window behind the Choke spread
PERM_MIN_TRADES = 5  # permanent-trade outlier rule needs this many trades in the season and category
PERM_CATEGORIES = ("GS", "HS")
PERM_LABELS = {"GS": "General Security", "HS": "High Security"}

SEGMENTS = ("Z10", "Z11", "WEX")
SEGMENT_LABELS = {"Z10": "River Zone 10", "Z11": "River Zone 11", "WEX": "MIL Water Exchange"}
