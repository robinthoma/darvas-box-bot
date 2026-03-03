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
    """
    Try to find a matching NSE equity symbol.
    Fyers API has no search endpoint, so we validate the formatted symbol directly.
    Returns a list with one match if found, empty list otherwise.
    """
    sym = format_symbol(query)
    if validate_symbol(sym):
        return [{"symbol": sym, "name": sym}]
    return []


def validate_symbol(symbol: str) -> bool:
    """Return True if the symbol exists and is tradeable."""
    symbol = format_symbol(symbol)
    fyers = get_fyers_instance()
    response = fyers.quotes(data={"symbols": symbol})
    if response.get("s") != "ok":
        return False
    quotes = response.get("d", [])
    return len(quotes) > 0
