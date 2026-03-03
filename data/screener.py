import re
import logging
import requests
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def is_screener_url(value: str) -> bool:
    return "screener.in/screens/" in value


def build_screener_url(query: str) -> str:
    params = {"sort": "Market Capitalization", "order": "desc", "query": query, "limit": "50"}
    return f"https://www.screener.in/screen/raw/?{urlencode(params)}"


def _parse_symbols_from_html(html: str) -> list[dict]:
    """
    Extract company symbols and names from Screener.in HTML.
    Handles both /company/SYMBOL/ and /company/SYMBOL/consolidated/ URL formats.
    """
    # Match href="/company/SYMBOL/" or href="/company/SYMBOL/consolidated/"
    # Screener.in uses NSE ticker as the slug (uppercase)
    pattern = re.compile(
        r'href=["\']\/company\/([A-Za-z0-9&]+)\/?(?:consolidated\/?)?["\'][^>]*>\s*([^<\n]+?)\s*<\/a>',
        re.IGNORECASE,
    )

    results = []
    seen: set[str] = set()

    for match in pattern.finditer(html):
        sym = match.group(1).strip().upper()
        name = match.group(2).strip()

        # Skip non-ticker slugs (navigation links etc.)
        if not sym or len(sym) > 20 or sym in seen:
            continue
        # Skip obvious non-ticker words
        if sym.lower() in {"about", "peers", "documents", "forecasts", "login", "screen"}:
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
    Fetch stocks from a saved public Screener.in screen URL.
    e.g. https://www.screener.in/screens/174251/darvas-box-for-only-nifty-stocks/
    """
    # Use a session so cookies (CSRF etc.) are handled automatically
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        # First visit homepage to get session cookies
        session.get("https://www.screener.in/", timeout=10)
        # Then fetch the screen
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Network error: {e}")

    html = resp.text

    # Detect login wall
    if "/login/" in resp.url or 'name="login"' in html:
        raise RuntimeError("LOGIN_REQUIRED")

    results = _parse_symbols_from_html(html)
    logger.info(f"Screener.in: found {len(results)} symbols from {url}")
    return results


def fetch_screener_results(query: str, limit: int = 50) -> list[dict]:
    """
    Fetch stocks from Screener.in using a raw query string.
    Note: Screener.in requires login for custom queries.
    """
    raise RuntimeError("LOGIN_REQUIRED")
