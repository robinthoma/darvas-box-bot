import re
import logging
import requests
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

SCREENER_RAW_URL = "https://www.screener.in/screen/raw/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.screener.in/",
}


def _parse_company_links(html: str) -> list[dict]:
    """Extract {name, screener_symbol, nse_symbol} from Screener.in HTML."""
    pattern = r'href=["\']\/company\/([A-Z0-9]+)\/[^"\']*["\'][^>]*>\s*([^<]+)\s*<\/a>'
    matches = re.findall(pattern, html, re.IGNORECASE)

    results = []
    seen: set[str] = set()
    for sym, name in matches:
        sym = sym.strip().upper()
        name = name.strip()
        if len(sym) < 2 or len(sym) > 20 or sym in seen:
            continue
        seen.add(sym)
        results.append({
            "name": name,
            "screener_symbol": sym,
            "nse_symbol": f"NSE:{sym}-EQ",
        })
    return results


def fetch_screen_by_url(url: str) -> list[dict]:
    """
    Fetch stocks from a saved Screener.in screen URL.
    e.g. https://www.screener.in/screens/174251/darvas-box-for-only-nifty-stocks/
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Network error: {e}")

    if "login" in resp.url.lower():
        raise RuntimeError("LOGIN_REQUIRED")

    results = _parse_company_links(resp.text)
    return results


def fetch_screener_results(query: str, limit: int = 50) -> list[dict]:
    """
    Fetch stocks from Screener.in using a raw query string.
    """
    params = {
        "sort": "Market Capitalization",
        "order": "desc",
        "query": query,
        "limit": str(limit),
    }
    url = f"{SCREENER_RAW_URL}?{urlencode(params)}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Network error: {e}")

    if "login" in resp.url.lower():
        raise RuntimeError("LOGIN_REQUIRED")

    return _parse_company_links(resp.text)


def build_screener_url(query: str, limit: int = 50) -> str:
    params = {
        "sort": "Market Capitalization",
        "order": "desc",
        "query": query,
        "limit": str(limit),
    }
    return f"{SCREENER_RAW_URL}?{urlencode(params)}"


def is_screener_url(value: str) -> bool:
    """Return True if the value looks like a Screener.in screen URL."""
    return value.startswith("https://www.screener.in/screens/")
