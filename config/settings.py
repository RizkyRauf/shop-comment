"""Konfigurasi default crawler Shopee."""

from __future__ import annotations

import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
PROFILE_DIR = BASE_DIR / ".shopee_session"       
CDP_PROFILE_DIR = BASE_DIR / ".chrome_session"      
COOKIES_FILE = BASE_DIR / "cookies.json"            
DATA_DIR = BASE_DIR / "data"


def _find_chrome() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    for path in ("/home/rauf/.local/bin/google-chrome", "/usr/bin/google-chrome"):
        if Path(path).exists():
            return path
    return "google-chrome"


CHROME_BIN = _find_chrome()

SITE = "https://shopee.co.id"
RATINGS_ENDPOINT = f"{SITE}/api/v2/item/get_ratings"

PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 50  
MAX_REVIEWS = 500
REQUEST_DELAY = 1.5
PRODUCT_DELAY = 4.0
MAX_EMPTY_PAGES = 4
LOGIN_TIMEOUT = 600

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)

DISABLE_FEATURES = "WaylandWpColorManagerV1"

CSV_COLUMNS = [
    "review_id",
    "review_text",
    "rating",
    "date_review",
    "author_username",
    "like_count",
    "product_name",
    "product_category",
    "shopid",
    "itemid",
    "product_url",
    "scraped_at",
]
