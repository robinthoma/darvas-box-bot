from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from data.market_data import get_daily_ohlcv
import config


@dataclass
class Trade:
    symbol: str
    entry_date: object
    exit_date: Optional[object]
    entry_price: float
    exit_price: Optional[float]
    qty: int
    box_top: float
    box_bottom: float
    pnl: float = 0.0
    open: bool = True

    def close(self, exit_date, exit_price: float):
        self.exit_date = exit_date
        self.exit_price = exit_price
        self.pnl = (exit_price - self.entry_price) * self.qty
        self.open = False


@dataclass
class BacktestResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    total_return_pct: float = 0.0
    win_rate_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    num_trades: int = 0
    total_capital_deployed: float = 0.0


def run_backtest(symbol: str, days: int = 504) -> BacktestResult:
    """
    Walk-forward backtest using corrected Darvas box logic.

    Rules applied:
    - Box Top    = 52W high
    - Box Bottom = lowest low of entire consolidation (locked at entry)
    - Entry      = 3 consecutive closes above Box Top
    - Expansion  = new high before 3 confirms → expand, reset count
    - Exit       = close below Box Bottom (stop loss)
    """
    df = get_daily_ohlcv(symbol, days=days)
    result = BacktestResult(symbol=symbol)

    if len(df) < 30:
        return result

    closed_trades: list[Trade] = []
    capital_deployed = 0.0
    equity_curve = [0.0]

    # ── Incremental state ──────────────────────────────────────────────────
    box_top: Optional[float] = None
    box_bottom_running: float = float("inf")
    box_bottom_locked: Optional[float] = None
    confirm_count: int = 0
    in_position: bool = False
    current_trade: Optional[Trade] = None
    box_start_i: int = 0

    def reset_box(i: int, new_top: float, low: float):
        nonlocal box_top, box_bottom_running, box_bottom_locked, confirm_count
        nonlocal in_position, current_trade
        box_top = new_top
        box_bottom_running = low
        box_bottom_locked = None
        confirm_count = 0
        box_start_i = i

    # ── Walk forward ───────────────────────────────────────────────────────
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        # Rolling 52W high detection
        lookback_start = max(0, i - 251)
        rolling_max = df.iloc[lookback_start:i + 1]["high"].max()

        # Initialise or expand box on new 52W high (only when NOT in position)
        if not in_position:
            if box_top is None or row["high"] >= rolling_max:
                if box_top is None or row["high"] > box_top:
                    box_top = row["high"]
                    confirm_count = 0  # Reset confirmation on new high

            # Track lowest low during consolidation
            box_bottom_running = min(box_bottom_running, row["low"])

            # Count consecutive closes above box top
            if box_top and row["close"] > box_top:
                confirm_count += 1
            else:
                confirm_count = 0

            # Entry: 3 consecutive closes above box top
            if confirm_count >= 3 and box_top and len(closed_trades) < config.MAX_POSITIONS:
                box_bottom_locked = box_bottom_running
                qty = max(1, int(config.CAPITAL_PER_TRADE / row["close"]))
                current_trade = Trade(
                    symbol=symbol,
                    entry_date=row["date"],
                    exit_date=None,
                    entry_price=row["close"],
                    exit_price=None,
                    qty=qty,
                    box_top=box_top,
                    box_bottom=box_bottom_locked,
                )
                in_position = True
                capital_deployed += config.CAPITAL_PER_TRADE

        else:
            # In position — only watch for stop loss
            if box_bottom_locked and row["close"] < box_bottom_locked:
                current_trade.close(row["date"], row["close"])
                closed_trades.append(current_trade)
                current_trade = None
                in_position = False
                # Reset box state for next opportunity
                box_top = None
                box_bottom_running = float("inf")
                box_bottom_locked = None
                confirm_count = 0

        # Equity snapshot
        open_pnl = 0.0
        if in_position and current_trade:
            open_pnl = (row["close"] - current_trade.entry_price) * current_trade.qty
        total_pnl = sum(t.pnl for t in closed_trades) + open_pnl
        equity_curve.append(total_pnl)

    # Close any open trade at last price
    if in_position and current_trade:
        current_trade.close(df.iloc[-1]["date"], df.iloc[-1]["close"])
        closed_trades.append(current_trade)

    result.trades = closed_trades
    result.num_trades = len(closed_trades)
    result.total_capital_deployed = capital_deployed

    if closed_trades:
        total_pnl = sum(t.pnl for t in closed_trades)
        result.total_return_pct = (
            (total_pnl / capital_deployed * 100) if capital_deployed else 0
        )
        wins = [t for t in closed_trades if t.pnl > 0]
        result.win_rate_pct = len(wins) / len(closed_trades) * 100

    # Max drawdown
    peak, max_dd = 0.0, 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    result.max_drawdown_pct = max_dd

    return result


def format_backtest_report(result: BacktestResult) -> str:
    lines = [
        f"📊 <b>Backtest — {result.symbol}</b>",
        "",
        f"Trades: {result.num_trades}",
        f"Total return: {result.total_return_pct:+.2f}%",
        f"Win rate: {result.win_rate_pct:.1f}%",
        f"Max drawdown: {result.max_drawdown_pct:.2f}%",
        f"Capital deployed: ₹{result.total_capital_deployed:,.0f}",
        "",
    ]

    if result.trades:
        lines.append("<b>Recent trades (last 5):</b>")
        for t in result.trades[-5:]:
            icon = "✅" if t.pnl > 0 else "❌"
            lines.append(
                f"{icon} {t.entry_date} → {t.exit_date} | "
                f"₹{t.entry_price:.2f} → ₹{t.exit_price:.2f} | "
                f"P&L ₹{t.pnl:+.0f}"
            )
    else:
        lines.append("No trades found in this period.")

    return "\n".join(lines)
