import threading
import logging
from typing import Optional

from fyers_apiv3.FyersWebsocket import data_ws, order_ws

from auth.fyers_auth import get_access_token, get_fyers_instance
import config

logger = logging.getLogger(__name__)


class QuoteTracker:
    """
    Subscribes to live LTP for watchlist symbols via Fyers DataSocket.
    Runs in a background thread.
    """

    def __init__(self):
        self._prices: dict[str, float] = {}
        self._lock = threading.Lock()
        self._socket: Optional[data_ws.FyersDataSocket] = None
        self._thread: Optional[threading.Thread] = None

    def subscribe(self, symbols: list[str]):
        token = get_access_token()
        access_token = f"{config.FYERS_APP_ID}:{token}"

        self._socket = data_ws.FyersDataSocket(
            access_token=access_token,
            log_path="",
            litemode=True,
            write_to_file=False,
            reconnect=True,
            on_connect=lambda ws: self._on_connect(ws, symbols),
            on_close=self._on_close,
            on_error=self._on_error,
            on_message=self._on_message,
        )
        self._thread = threading.Thread(target=self._socket.connect, daemon=True)
        self._thread.start()

    def _on_connect(self, ws, symbols: list[str]):
        data_type = "SymbolUpdate"
        ws.subscribe(symbols=symbols, data_type=data_type)
        logger.info(f"QuoteTracker: subscribed to {symbols}")

    def _on_message(self, msg: dict):
        symbol = msg.get("symbol")
        ltp = msg.get("ltp")
        if symbol and ltp is not None:
            with self._lock:
                self._prices[symbol] = float(ltp)

    def _on_close(self, ws):
        logger.info("QuoteTracker: WebSocket closed")

    def _on_error(self, ws, error):
        logger.error(f"QuoteTracker error: {error}")

    def get_ltp(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._prices.get(symbol)

    def get_all(self) -> dict[str, float]:
        with self._lock:
            return dict(self._prices)

    def stop(self):
        if self._socket:
            try:
                self._socket.close_connection()
            except Exception:
                pass


class PortfolioTracker:
    """
    Tracks positions and P&L using Fyers OrderSocket WebSocket.
    Falls back to REST API for snapshots.
    """

    def __init__(self):
        self._positions: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._socket = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        # REST-only mode — WebSocket order feed skipped due to API version differences.
        # /portfolio command uses fyers.holdings() + fyers.positions() directly.
        logger.info("PortfolioTracker running in REST mode.")

    def get_portfolio_snapshot(self) -> dict:
        """Merge REST snapshot with live WebSocket cache."""
        fyers = get_fyers_instance()
        result = {"holdings": [], "positions": [], "total_pnl": 0.0}

        try:
            holdings_resp = fyers.holdings()
            if holdings_resp.get("s") == "ok":
                result["holdings"] = holdings_resp.get("holdings", [])
        except Exception as e:
            logger.error(f"Holdings fetch error: {e}")

        try:
            positions_resp = fyers.positions()
            if positions_resp.get("s") == "ok":
                net_positions = positions_resp.get("netPositions", [])
                # Merge with WebSocket cache for live LTP
                with self._lock:
                    ws_cache = dict(self._positions)
                for pos in net_positions:
                    sym = pos.get("symbol")
                    if sym in ws_cache:
                        pos["ltp"] = ws_cache[sym].get("ltp", pos.get("ltp", 0))
                result["positions"] = net_positions
        except Exception as e:
            logger.error(f"Positions fetch error: {e}")

        total_pnl = sum(p.get("pl", 0) for p in result["positions"])
        total_pnl += sum(h.get("pl", 0) for h in result["holdings"])
        result["total_pnl"] = total_pnl
        return result

    def stop(self):
        if self._socket:
            try:
                self._socket.close_connection()
            except Exception:
                pass


# Module-level singletons
_quote_tracker: Optional[QuoteTracker] = None
_portfolio_tracker: Optional[PortfolioTracker] = None


def get_quote_tracker() -> QuoteTracker:
    global _quote_tracker
    if _quote_tracker is None:
        _quote_tracker = QuoteTracker()
    return _quote_tracker


def get_portfolio_tracker() -> PortfolioTracker:
    global _portfolio_tracker
    if _portfolio_tracker is None:
        _portfolio_tracker = PortfolioTracker()
    return _portfolio_tracker
