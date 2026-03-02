import asyncio
import io
import logging
from datetime import date

from telegram import Update, InputFile
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import config
from data.symbols import search_symbol, validate_symbol, format_symbol
from data.market_data import get_daily_ohlcv
from data.realtime import get_portfolio_tracker
from state.db_manager import (
    add_to_watchlist,
    remove_from_watchlist,
    get_watchlist,
    get_active_box,
    get_all_confirmed_boxes,
    get_today_signals,
    get_open_positions,
)
from strategy.backtester import run_backtest, format_backtest_report
from visualisation.chart import draw_darvas_chart

logger = logging.getLogger(__name__)

# Will be set by main.py
_scan_callback = None
_paused = False
_last_scan_time = "Never"


def set_scan_callback(fn):
    global _scan_callback
    _scan_callback = fn


def set_last_scan_time(t: str):
    global _last_scan_time
    _last_scan_time = t


def is_paused() -> bool:
    return _paused


# ── Guard decorator ────────────────────────────────────────────────────────

def authorized(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        if chat_id != config.TELEGRAM_CHAT_ID:
            await update.message.reply_text("Unauthorized.")
            return
        await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper


# ── Commands ───────────────────────────────────────────────────────────────

@authorized
async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    symbols = get_watchlist()
    if not symbols:
        await update.message.reply_text("Watchlist is empty. Use /add SYMBOL to add stocks.")
        return
    lines = ["📋 *Watchlist*", ""] + [f"• {s}" for s in symbols]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /add SYMBOL (e.g. /add RELIANCE)")
        return

    query = ctx.args[0].upper()
    formatted = format_symbol(query)

    # First try direct validation
    if validate_symbol(formatted):
        add_to_watchlist(formatted)
        await update.message.reply_text(
            f"✅ Added *{formatted}* to watchlist.", parse_mode=ParseMode.MARKDOWN
        )
        return

    # Search for matches
    results = search_symbol(query)
    if not results:
        await update.message.reply_text(
            f"❌ Symbol `{query}` not found on NSE. Check the symbol name.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if len(results) == 1:
        sym = results[0]["symbol"]
        add_to_watchlist(sym)
        await update.message.reply_text(
            f"✅ Added *{sym}* to watchlist.", parse_mode=ParseMode.MARKDOWN
        )
        return

    # Multiple results — show list
    lines = [f"Multiple results for `{query}`. Did you mean:"]
    for i, r in enumerate(results[:5], 1):
        lines.append(f"{i}. *{r['symbol']}* — {r['name']}")
    lines.append("\nUse /add with the full symbol, e.g. /add NSE:RELIANCE-EQ")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /remove SYMBOL")
        return
    symbol = format_symbol(ctx.args[0])
    remove_from_watchlist(symbol)
    await update.message.reply_text(f"Removed *{symbol}* from watchlist.", parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    signals = get_today_signals()
    if not signals:
        await update.message.reply_text("No signals today.")
        return
    lines = [f"📡 *Today's Signals ({date.today().isoformat()})*", ""]
    for s in signals:
        lines.append(f"• {s['signal_type']} {s['symbol']} @ ₹{s['price']:.2f} — {s['details'] or ''}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_boxes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    boxes = get_all_confirmed_boxes()
    if not boxes:
        await update.message.reply_text("No confirmed boxes active.")
        return
    lines = ["📦 *Active Confirmed Boxes*", ""]
    for b in boxes:
        lines.append(
            f"• *{b['symbol']}* — Top ₹{b['box_top']:.2f} | Bottom ₹{b['box_bottom']:.2f}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    positions = get_open_positions()
    if not positions:
        await update.message.reply_text("No open positions tracked.")
        return
    lines = ["💼 *Open Positions*", ""]
    for p in positions:
        lines.append(
            f"• *{p['symbol']}* — {p['quantity']} qty @ ₹{p['entry_price']:.2f} "
            f"(Level {p['pyramid_level']}) — {p['entry_date']}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fetching portfolio…")
    try:
        tracker = get_portfolio_tracker()
        snapshot = tracker.get_portfolio_snapshot()

        lines = ["💰 *Portfolio Snapshot*", ""]

        holdings = snapshot.get("holdings", [])
        if holdings:
            lines.append("*Holdings:*")
            for h in holdings:
                sym = h.get("symbol", "?")
                qty = h.get("quantity", 0)
                avg = h.get("costPrice", 0)
                ltp = h.get("ltp", 0)
                pl = h.get("pl", 0)
                lines.append(
                    f"  {sym} | {qty} qty | Avg ₹{avg:.2f} | LTP ₹{ltp:.2f} | P&L ₹{pl:+.0f}"
                )
            lines.append("")

        positions = snapshot.get("positions", [])
        if positions:
            lines.append("*Positions:*")
            for p in positions:
                sym = p.get("symbol", "?")
                qty = p.get("netQty", 0)
                avg = p.get("avgPrice", 0)
                ltp = p.get("ltp", 0)
                pl = p.get("pl", 0)
                lines.append(
                    f"  {sym} | {qty} qty | Avg ₹{avg:.2f} | LTP ₹{ltp:.2f} | P&L ₹{pl:+.0f}"
                )
            lines.append("")

        total_pnl = snapshot.get("total_pnl", 0)
        lines.append(f"*Total P&L: ₹{total_pnl:+,.0f}*")

        if not holdings and not positions:
            lines.append("No holdings or positions found.")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Portfolio error: {e}")
        await update.message.reply_text(f"Error fetching portfolio: {e}")


@authorized
async def cmd_chart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /chart SYMBOL")
        return

    symbol = format_symbol(ctx.args[0])
    await update.message.reply_text(f"Generating chart for {symbol}…")

    try:
        df = get_daily_ohlcv(symbol, days=120)
        if df.empty:
            await update.message.reply_text(f"No data found for {symbol}.")
            return

        box = get_active_box(symbol)
        boxes = []
        if box:
            boxes.append({
                "box_top": box["box_top"],
                "box_bottom": box["box_bottom"],
                "high_date": box.get("high_date"),
                "confirm_date": box.get("confirmed_date"),
                "status": box.get("status", "confirmed"),
                "signals": [],
            })

        png_bytes = draw_darvas_chart(symbol, df, boxes)
        await update.message.reply_photo(
            photo=InputFile(io.BytesIO(png_bytes), filename=f"{symbol}.png"),
            caption=f"Darvas Box Chart — {symbol}",
        )
    except Exception as e:
        logger.error(f"Chart error for {symbol}: {e}")
        await update.message.reply_text(f"Error generating chart: {e}")


@authorized
async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /backtest SYMBOL")
        return

    symbol = format_symbol(ctx.args[0])
    await update.message.reply_text(f"Running backtest for {symbol} (2 years)… this may take a moment.")

    try:
        result = run_backtest(symbol, days=504)
        report = format_backtest_report(result)
        await update.message.reply_text(report, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Backtest error for {symbol}: {e}")
        await update.message.reply_text(f"Error running backtest: {e}")


@authorized
async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if _scan_callback is None:
        await update.message.reply_text("Scan not configured.")
        return
    await update.message.reply_text("Triggering manual scan…")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _scan_callback)
        await update.message.reply_text("Scan complete. Check for new signals with /signals.")
    except Exception as e:
        logger.error(f"Manual scan error: {e}")
        await update.message.reply_text(f"Scan error: {e}")


@authorized
async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global _paused
    _paused = True
    await update.message.reply_text("⏸ Daily scan paused. Use /resume to re-enable.")


@authorized
async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global _paused
    _paused = False
    await update.message.reply_text("▶️ Daily scan resumed.")


@authorized
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    status = "⏸ PAUSED" if _paused else "▶️ RUNNING"
    watchlist_count = len(get_watchlist())
    positions_count = len(get_open_positions())
    lines = [
        "🤖 *Bot Status*",
        "",
        f"State: {status}",
        f"Watchlist: {watchlist_count} symbols",
        f"Open positions: {positions_count}",
        f"Last scan: {_last_scan_time}",
        f"Scan time: {config.EVAL_TIME} IST (Mon–Fri)",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ── Alert senders ──────────────────────────────────────────────────────────

async def send_alert(app: Application, text: str):
    await app.bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
    )


def format_entry_alert(symbol: str, signal: dict) -> str:
    price = signal["price"]
    box_top = signal.get("box_top", 0)
    box_bottom = signal.get("box_bottom", 0)
    vol_ratio = signal.get("volume_ratio", 0)
    qty = max(1, int(config.CAPITAL_PER_TRADE / price)) if price else 0
    pct_above = ((price - box_top) / box_top * 100) if box_top else 0

    vol_icon = "✅" if vol_ratio >= 1 else "⚠️"
    return (
        f"🚨 *ENTRY SIGNAL — {symbol}*\n"
        f"Price: ₹{price:,.2f} (Breakout +{pct_above:.2f}% above ₹{box_top:,.2f})\n"
        f"Volume: {vol_icon} {vol_ratio:.1f}x prev day\n"
        f"Box: ₹{box_top:,.2f} → ₹{box_bottom:,.2f}\n"
        f"Qty: {qty} shares (₹{config.CAPITAL_PER_TRADE:,} allocation)\n"
        f"Stop Loss: ₹{box_bottom:,.2f}"
    )


def format_exit_alert(symbol: str, signal: dict) -> str:
    price = signal["price"]
    box_bottom = signal.get("box_bottom", 0)
    positions = get_open_positions()
    qty = sum(p["quantity"] for p in positions if p["symbol"] == symbol and p["status"] == "open")
    return (
        f"⚠️ *EXIT SIGNAL — {symbol}*\n"
        f"Price: ₹{price:,.2f} < Box Bottom ₹{box_bottom:,.2f}\n"
        f"Action: SELL {qty} shares"
    )


def format_box_alert(symbol: str, signal: dict) -> str:
    box_top = signal.get("box_top", 0)
    box_bottom = signal.get("box_bottom", 0)
    breakout_level = box_top * (1 + config.BREAKOUT_BUFFER)
    return (
        f"📦 *BOX CONFIRMED — {symbol}*\n"
        f"Top: ₹{box_top:,.2f} | Bottom: ₹{box_bottom:,.2f}\n"
        f"Watch breakout above ₹{breakout_level:,.2f}"
    )


def format_daily_summary(entry_count: int, exit_count: int, box_count: int, position_count: int) -> str:
    return (
        f"📊 *DAILY SUMMARY — {config.EVAL_TIME} IST*\n"
        f"Signals today: {entry_count} entry, {exit_count} exit\n"
        f"Active boxes: {box_count} | Open positions: {position_count}"
    )


# ── App builder ────────────────────────────────────────────────────────────

def build_app() -> Application:
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("boxes", cmd_boxes))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("chart", cmd_chart))
    app.add_handler(CommandHandler("backtest", cmd_backtest))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("status", cmd_status))

    return app
