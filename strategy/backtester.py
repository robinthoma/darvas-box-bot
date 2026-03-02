from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from data.market_data import get_daily_ohlcv
from strategy.darvas_engine import find_52w_high, confirm_box, check_entry, check_exit
import config


@dataclass
class Trade:
    symbol: str
    entry_date: object
    exit_date: Optional[object]
    entry_price: float
    exit_price: Optional[float]
    qty: int
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
    Walk-forward backtest of Darvas box strategy over `days` of daily data.
    Simulates exactly as live logic: processes candle by candle.
    """
    df = get_daily_ohlcv(symbol, days=days)
    result = BacktestResult(symbol=symbol)

    if len(df) < 30:
        return result

    open_trades: list[Trade] = []
    closed_trades: list[Trade] = []
    active_box: Optional[dict] = None  # {box_top, box_bottom, high_idx, confirmed}

    capital_deployed = 0.0
    equity_curve = [0.0]

    for i in range(10, len(df)):
        slice_df = df.iloc[:i + 1].copy()
        latest = slice_df.iloc[-1]
        prev = slice_df.iloc[-2]

        # --- Check exits for open trades ---
        for trade in list(open_trades):
            if active_box and check_exit(latest, active_box["box_bottom"]):
                trade.close(latest["date"], latest["close"])
                closed_trades.append(trade)
                open_trades.remove(trade)
                active_box = None

        # Cap positions
        if len(open_trades) >= config.MAX_POSITIONS:
            equity_curve.append(equity_curve[-1])
            continue

        # --- Box detection ---
        high_result = find_52w_high(slice_df)
        if high_result is None:
            equity_curve.append(equity_curve[-1])
            continue

        high_idx, high_val = high_result

        if high_idx == slice_df.index[-1]:
            equity_curve.append(equity_curve[-1])
            continue

        confirmed, box_top, box_bottom, confirm_date = confirm_box(slice_df, high_idx)
        if not confirmed:
            equity_curve.append(equity_curve[-1])
            continue

        # Update active box (may be same or new higher box)
        if active_box is None or box_top > active_box["box_top"]:
            active_box = {
                "box_top": box_top,
                "box_bottom": box_bottom,
                "confirmed": True,
            }

        # --- Entry check ---
        if active_box and check_entry(latest, prev, active_box["box_top"]):
            qty = max(1, int(config.CAPITAL_PER_TRADE / latest["close"]))
            trade = Trade(
                symbol=symbol,
                entry_date=latest["date"],
                exit_date=None,
                entry_price=latest["close"],
                exit_price=None,
                qty=qty,
            )
            open_trades.append(trade)
            capital_deployed += config.CAPITAL_PER_TRADE

        pnl_so_far = sum(
            (latest["close"] - t.entry_price) * t.qty for t in open_trades
        ) + sum(t.pnl for t in closed_trades)
        equity_curve.append(pnl_so_far)

    # Close any remaining open trades at last price
    last_close = df.iloc[-1]["close"]
    last_date = df.iloc[-1]["date"]
    for trade in open_trades:
        trade.close(last_date, last_close)
        closed_trades.append(trade)

    all_trades = closed_trades
    result.trades = all_trades
    result.num_trades = len(all_trades)
    result.total_capital_deployed = capital_deployed

    if all_trades:
        total_pnl = sum(t.pnl for t in all_trades)
        result.total_return_pct = (
            (total_pnl / capital_deployed * 100) if capital_deployed else 0
        )
        wins = [t for t in all_trades if t.pnl > 0]
        result.win_rate_pct = len(wins) / len(all_trades) * 100
    else:
        result.total_return_pct = 0
        result.win_rate_pct = 0

    # Max drawdown from equity curve
    peak = 0.0
    max_dd = 0.0
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
        f"📊 *Backtest Report — {result.symbol}*",
        "",
        f"Trades executed: {result.num_trades}",
        f"Total return: {result.total_return_pct:+.2f}%",
        f"Win rate: {result.win_rate_pct:.1f}%",
        f"Max drawdown: {result.max_drawdown_pct:.2f}%",
        f"Capital deployed: ₹{result.total_capital_deployed:,.0f}",
        "",
    ]

    if result.trades:
        lines.append("*Recent trades (last 5):*")
        for t in result.trades[-5:]:
            status = "✅" if t.pnl > 0 else "❌"
            lines.append(
                f"{status} {t.entry_date} → {t.exit_date} | "
                f"Entry ₹{t.entry_price:.2f} Exit ₹{t.exit_price:.2f} | "
                f"P&L ₹{t.pnl:+.0f}"
            )
    else:
        lines.append("No trades found in this period.")

    return "\n".join(lines)
