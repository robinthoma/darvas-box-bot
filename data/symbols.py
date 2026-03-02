from auth.fyers_auth import get_fyers_instance


def format_symbol(raw: str) -> str:
    """Ensure the symbol is in NSE:SYMBOL-EQ format."""
    raw = raw.strip().upper()
    if raw.startswith("NSE:") and raw.endswith("-EQ"):
        return raw
    if raw.startswith("NSE:"):
        raw = raw[4:]
    if raw.endswith("-EQ"):
        raw = raw[:-3]
    return f"NSE:{raw}-EQ"


def search_symbol(query: str) -> list[dict]:
    """Search for symbols matching query. Returns list of {symbol, name} dicts."""
    fyers = get_fyers_instance()
    response = fyers.search(data={"symbol": query.upper(), "exchange": "NSE"})
    if response.get("s") != "ok":
        return []
    results = response.get("data", [])
    return [
        {"symbol": item.get("symbol", ""), "name": item.get("desc", "")}
        for item in results
        if item.get("symbol", "").endswith("-EQ")
    ]


def validate_symbol(symbol: str) -> bool:
    """Return True if the symbol exists and is tradeable."""
    symbol = format_symbol(symbol)
    fyers = get_fyers_instance()
    response = fyers.quotes(data={"symbols": symbol})
    if response.get("s") != "ok":
        return False
    quotes = response.get("d", [])
    return len(quotes) > 0
