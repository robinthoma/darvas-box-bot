import asyncio
import io
import logging
import time
import uuid
from datetime import date

from telegram import (
    BotCommand,
    InputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.request import HTTPXRequest
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import config
from data.symbols import search_symbol, validate_symbol, format_symbol
from data.market_data import get_daily_ohlcv
from data.realtime import get_portfolio_tracker
from strategy.darvas_engine import find_all_boxes_for_chart
from state.db_manager import (
    add_to_watchlist,
    remove_from_watchlist,
    get_watchlist,
    get_active_box,
    get_all_confirmed_boxes,
    get_today_signals,
    get_open_positions,
    add_position,
    get_setting,
    set_setting,
)
from strategy.backtester import run_backtest, format_backtest_report
from visualisation.chart import draw_darvas_chart
from data.screener import fetch_screener_results, fetch_screen_by_url, build_screener_url, is_screener_url

DEFAULT_SCREEN_QUERY = (
    "Market Capitalization > 100000 AND "
    "Down from 52w high < 85 AND "
    "Price to Earning > 20"
)

logger = logging.getLogger(__name__)

# Injected by main.py
_scan_callback = None
_paused = False
_main_loop = None  # Captured in _post_init for cross-thread async calls


def get_main_loop():
    return _main_loop
_last_scan_time = "Never"

# Pending buy confirmations: {order_id: {symbol, qty, price, box_bottom, ts}}
_pending_orders: dict[str, dict] = {}
ORDER_TIMEOUT = 300  # 5 minutes


def set_scan_callback(fn):
    global _scan_callback
    _scan_callback = fn


def set_last_scan_time(t: str):
    global _last_scan_time
    _last_scan_time = t


def is_paused() -> bool:
    return _paused


# ── Auth guard ─────────────────────────────────────────────────────────────

def authorized(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_chat.id) != config.TELEGRAM_CHAT_ID:
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
    lines = ["📋 <b>Watchlist</b>", ""] + [f"• {s}" for s in symbols]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@authorized
async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /add SYMBOL  e.g. /add RELIANCE")
        return

    query = ctx.args[0].upper()
    formatted = format_symbol(query)
    add_to_watchlist(formatted)
    await update.message.reply_text(
        f"✅ Added <b>{formatted}</b> to watchlist.",
        parse_mode=ParseMode.HTML,
    )


@authorized
async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /remove SYMBOL")
        return
    symbol = format_symbol(ctx.args[0])
    remove_from_watchlist(symbol)
    await update.message.reply_text(
        f"Removed <b>{symbol}</b> from watchlist.", parse_mode=ParseMode.HTML
    )


@authorized
async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    signals = get_today_signals()
    if not signals:
        await update.message.reply_text("No signals today.")
        return
    lines = [f"📡 <b>Today's Signals ({date.today().isoformat()})</b>", ""]
    for s in signals:
        details = (s['details'] or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        lines.append(
            f"• {s['signal_type']} {s['symbol']} @ ₹{s['price']:.2f} — {details}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@authorized
async def cmd_boxes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    boxes = get_all_confirmed_boxes()
    if not boxes:
        await update.message.reply_text("No confirmed boxes active.")
        return
    lines = ["📦 <b>Active Confirmed Boxes</b>", ""]
    for b in boxes:
        lines.append(
            f"• <b>{b['symbol']}</b> — Top ₹{b['box_top']:.2f} | Bottom ₹{b['box_bottom']:.2f}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@authorized
async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    positions = get_open_positions()
    if not positions:
        await update.message.reply_text("No open positions tracked.")
        return
    lines = ["💼 <b>Open Positions</b>", ""]
    for p in positions:
        lines.append(
            f"• <b>{p['symbol']}</b> — {p['quantity']} qty @ ₹{p['entry_price']:.2f} "
            f"(Level {p['pyramid_level']}) — {p['entry_date']}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@authorized
async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fetching portfolio…")
    try:
        tracker = get_portfolio_tracker()
        snapshot = tracker.get_portfolio_snapshot()
        lines = ["💰 <b>Portfolio Snapshot</b>", ""]

        holdings = snapshot.get("holdings", [])
        if holdings:
            lines.append("<b>Holdings:</b>")
            for h in holdings:
                lines.append(
                    f"  {h.get('symbol','?')} | {h.get('quantity',0)} qty | "
                    f"Avg ₹{h.get('costPrice',0):.2f} | LTP ₹{h.get('ltp',0):.2f} | "
                    f"P&L ₹{h.get('pl',0):+.0f}"
                )
            lines.append("")

        positions = snapshot.get("positions", [])
        if positions:
            lines.append("<b>Positions:</b>")
            for p in positions:
                lines.append(
                    f"  {p.get('symbol','?')} | {p.get('netQty',0)} qty | "
                    f"Avg ₹{p.get('avgPrice',0):.2f} | LTP ₹{p.get('ltp',0):.2f} | "
                    f"P&L ₹{p.get('pl',0):+.0f}"
                )
            lines.append("")

        total_pnl = snapshot.get("total_pnl", 0)
        lines.append(f"<b>Total P&L: ₹{total_pnl:+,.0f}</b>")

        if not holdings and not positions:
            lines.append("No holdings or positions found.")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
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
        df = get_daily_ohlcv(symbol, days=300)
        if df.empty:
            await update.message.reply_text(f"No data for {symbol}.")
            return
        # Compute boxes directly from price data — no prior scan needed
        boxes = find_all_boxes_for_chart(df)
        # Show only last 120 days on chart but use full data for box detection
        df_chart = df.tail(120).reset_index(drop=True)
        png_bytes = draw_darvas_chart(symbol, df_chart, boxes)
        await update.message.reply_photo(
            photo=InputFile(io.BytesIO(png_bytes), filename=f"{symbol}.png"),
            caption=f"Darvas Box Chart — {symbol}",
        )
    except Exception as e:
        logger.error(f"Chart error: {e}")
        await update.message.reply_text(f"Error generating chart: {e}")


@authorized
async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /backtest SYMBOL")
        return
    symbol = format_symbol(ctx.args[0])
    await update.message.reply_text(f"Running backtest for {symbol}…")
    try:
        result = run_backtest(symbol, days=504)
        report = format_backtest_report(result)
        await update.message.reply_text(report, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Backtest error: {e}")
        await update.message.reply_text(f"Error: {e}")


@authorized
async def cmd_setscreen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Save a Screener.in URL or query. Usage: /setscreen URL_OR_QUERY"""
    if ctx.args:
        value = " ".join(ctx.args)
    else:
        value = DEFAULT_SCREEN_QUERY

    set_setting("screener_query", value)

    if is_screener_url(value):
        await update.message.reply_text(
            f"✅ Screener URL saved. Run /screen to fetch stocks.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            f"✅ Screener query saved:\n`{value}`",
            parse_mode=ParseMode.HTML,
        )


@authorized
async def cmd_screen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Run the saved Screener.in query and show matching stocks."""
    # Allow inline query override: /screen YOUR QUERY
    if ctx.args:
        query = " ".join(ctx.args)
    else:
        query = get_setting("screener_query", DEFAULT_SCREEN_QUERY)

    await update.message.reply_text(
        f"🔍 Screening Screener.in…\n`{query}`",
        parse_mode=ParseMode.HTML,
    )

    try:
        if is_screener_url(query):
            results = fetch_screen_by_url(query)
        else:
            results = fetch_screener_results(query, limit=20)
    except RuntimeError as e:
        if "LOGIN_REQUIRED" in str(e):
            await update.message.reply_text(
                "⚠️ <b>Screener.in requires login</b> for this screen.\n"
                "Try using a public saved screen URL with /setscreen.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(f"❌ Screener.in error: {e}")
        return
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        return

    if not results:
        await update.message.reply_text(
            "No results found. Check your query or URL."
        )
        return

    watchlist = set(get_watchlist())
    lines = [f"📊 <b>Screen Results</b> ({len(results)} stocks)\n"]
    keyboard_rows = []

    for r in results[:15]:
        sym = r["nse_symbol"]
        status = "✅" if sym in watchlist else "➕"
        lines.append(f"{status} <b>{r['screener_symbol']}</b> — {r['name']}")
        if sym not in watchlist:
            keyboard_rows.append([
                InlineKeyboardButton(
                    f"➕ {r['screener_symbol']}",
                    callback_data=f"sadd_{r['screener_symbol']}",
                )
            ])

    # Add "Add All" button if there are stocks not yet in watchlist
    new_symbols = [r for r in results[:15] if r["nse_symbol"] not in watchlist]
    if new_symbols:
        all_syms = ",".join(r["screener_symbol"] for r in new_symbols[:10])
        keyboard_rows.append([
            InlineKeyboardButton(
                f"➕ Add All ({len(new_symbols)})",
                callback_data=f"saddall_{all_syms}",
            )
        ])

    keyboard = InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


@authorized
async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if _scan_callback is None:
        await update.message.reply_text("Scan not configured.")
        return
    await update.message.reply_text("Triggering manual scan…")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _scan_callback)
        await update.message.reply_text("Scan complete. Check /signals for results.")
    except Exception as e:
        logger.error(f"Scan error: {e}")
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
    lines = [
        "🤖 <b>Bot Status</b>", "",
        f"State: {status}",
        f"Watchlist: {len(get_watchlist())} symbols",
        f"Open positions: {len(get_open_positions())}",
        f"Last scan: {_last_scan_time}",
        f"Auto-scan: {config.EVAL_TIME} IST Mon–Fri",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── Buy confirmation callback ──────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("buy_"):
        order_id = data[4:]
        order = _pending_orders.get(order_id)

        if not order:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("⚠️ Order no longer available (expired or already acted on).")
            return

        if time.time() - order["ts"] > ORDER_TIMEOUT:
            _pending_orders.pop(order_id, None)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("⏰ Order timed out (5 min). Signal still valid — check /signals.")
            return

        _pending_orders.pop(order_id, None)
        await query.edit_message_reply_markup(reply_markup=None)

        try:
            from data.orders import place_buy_order
            response = place_buy_order(order["symbol"], order["qty"])
            if response.get("s") == "ok":
                fyers_order_id = response.get("id", "N/A")
                add_position(
                    symbol=order["symbol"],
                    entry_price=order["price"],
                    quantity=order["qty"],
                    entry_date=date.today().isoformat(),
                )
                await query.message.reply_text(
                    f"✅ <b>Buy order placed!</b>\n"
                    f"{order['symbol']} — {order['qty']} shares @ market\n"
                    f"Fyers Order ID: `{fyers_order_id}`",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await query.message.reply_text(
                    f"❌ Order failed: {response.get('message', 'Unknown error')}"
                )
        except Exception as e:
            logger.error(f"Buy order error: {e}")
            await query.message.reply_text(f"❌ Order error: {e}")

    elif data.startswith("skip_"):
        order_id = data[5:]
        _pending_orders.pop(order_id, None)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Signal skipped.")

    elif data.startswith("sadd_"):
        sym_raw = data[5:]
        nse_sym = f"NSE:{sym_raw}-EQ"
        add_to_watchlist(nse_sym)
        await query.answer(f"Added {sym_raw} to watchlist")
        # Remove just this button row from the keyboard
        existing = query.message.reply_markup
        if existing:
            new_rows = [
                row for row in existing.inline_keyboard
                if not any(btn.callback_data == data for btn in row)
            ]
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(new_rows) if new_rows else None
            )

    elif data.startswith("saddall_"):
        syms_raw = data[8:].split(",")
        added = []
        for sym in syms_raw:
            sym = sym.strip()
            if sym:
                add_to_watchlist(f"NSE:{sym}-EQ")
                added.append(sym)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"✅ Added {len(added)} stocks to watchlist:\n" +
            ", ".join(added)
        )


# ── Alert senders (called from main.py scan) ───────────────────────────────

async def send_entry_alert(app: Application, symbol: str, signal: dict):
    price = signal["price"]
    box_top = signal.get("box_top", 0)
    box_bottom = signal.get("box_bottom", 0)
    vol_ratio = signal.get("volume_ratio", 0)
    qty = max(1, int(config.CAPITAL_PER_TRADE / price)) if price else 0
    pct_above = ((price - box_top) / box_top * 100) if box_top else 0
    vol_icon = "✅" if vol_ratio >= 1 else "⚠️"

    order_id = uuid.uuid4().hex[:8]
    _pending_orders[order_id] = {
        "symbol": symbol,
        "qty": qty,
        "price": price,
        "box_bottom": box_bottom,
        "ts": time.time(),
    }

    text = (
        f"🚨 <b>ENTRY SIGNAL — {symbol}</b>\n"
        f"Price: ₹{price:,.2f} (+{pct_above:.2f}% above ₹{box_top:,.2f})\n"
        f"Volume: {vol_icon} {vol_ratio:.1f}x prev day\n"
        f"Box: ₹{box_top:,.2f} → ₹{box_bottom:,.2f}\n"
        f"Qty: {qty} shares (₹{config.CAPITAL_PER_TRADE:,} allocation)\n"
        f"Stop Loss: ₹{box_bottom:,.2f}\n\n"
        f"⏳ Expires in 5 minutes"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Buy {qty} shares", callback_data=f"buy_{order_id}"),
        InlineKeyboardButton("❌ Skip", callback_data=f"skip_{order_id}"),
    ]])

    await app.bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def send_exit_alert(app: Application, symbol: str, signal: dict):
    price = signal["price"]
    box_bottom = signal.get("box_bottom", 0)
    positions = get_open_positions()
    qty = sum(p["quantity"] for p in positions if p["symbol"] == symbol)

    text = (
        f"⚠️ <b>EXIT SIGNAL — {symbol}</b>\n"
        f"Price: ₹{price:,.2f} < Box Bottom ₹{box_bottom:,.2f}\n"
        f"Action: SELL {qty} shares\n\n"
        f"_Delivery shares require CDSL TPIN authorisation on Fyers app before selling._"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📱 Open Fyers to Sell", url="https://trade.fyers.in"),
    ]])

    await app.bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def send_box_alert(app: Application, symbol: str, signal: dict):
    box_top = signal.get("box_top", 0)
    box_bottom = signal.get("box_bottom", 0)
    breakout_level = box_top * (1 + config.BREAKOUT_BUFFER)
    text = (
        f"📦 <b>BOX CONFIRMED — {symbol}</b>\n"
        f"Top: ₹{box_top:,.2f} | Bottom: ₹{box_bottom:,.2f}\n"
        f"Watch breakout above ₹{breakout_level:,.2f}"
    )
    await app.bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode=ParseMode.HTML,
    )


async def send_daily_summary(app: Application, entry_count: int, exit_count: int):
    boxes = get_all_confirmed_boxes()
    positions = get_open_positions()
    text = (
        f"📊 <b>DAILY SUMMARY — {config.EVAL_TIME} IST</b>\n"
        f"Signals today: {entry_count} entry, {exit_count} exit\n"
        f"Active boxes: {len(boxes)} | Open positions: {len(positions)}"
    )
    await app.bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode=ParseMode.HTML,
    )


# ── App builder ────────────────────────────────────────────────────────────

async def _post_init(app: Application):
    """Capture main event loop and register bot command menu."""
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    commands = [
        BotCommand("watchlist",  "List tracked symbols"),
        BotCommand("add",        "Add symbol — /add RELIANCE"),
        BotCommand("remove",     "Remove symbol — /remove RELIANCE"),
        BotCommand("signals",    "Today's signals"),
        BotCommand("boxes",      "Active confirmed boxes"),
        BotCommand("positions",  "Open positions"),
        BotCommand("portfolio",  "Live portfolio & P&L"),
        BotCommand("chart",      "Darvas chart — /chart RELIANCE"),
        BotCommand("backtest",   "Backtest — /backtest RELIANCE"),
        BotCommand("screen",     "Screen stocks from Screener.in"),
        BotCommand("setscreen",  "Save screener query"),
        BotCommand("scan",       "Run manual scan now"),
        BotCommand("pause",      "Pause daily scan"),
        BotCommand("resume",     "Resume daily scan"),
        BotCommand("status",     "Bot health & last scan time"),
    ]
    await app.bot.set_my_commands(commands)


def build_app() -> Application:
    request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=30,
    )
    # Separate request object for long-polling (needs higher read_timeout)
    get_updates_request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=35,
        write_timeout=30,
        pool_timeout=30,
    )
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .request(request)
        .get_updates_request(get_updates_request)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("add",       cmd_add))
    app.add_handler(CommandHandler("remove",    cmd_remove))
    app.add_handler(CommandHandler("signals",   cmd_signals))
    app.add_handler(CommandHandler("boxes",     cmd_boxes))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("chart",     cmd_chart))
    app.add_handler(CommandHandler("backtest",  cmd_backtest))
    app.add_handler(CommandHandler("screen",    cmd_screen))
    app.add_handler(CommandHandler("setscreen", cmd_setscreen))
    app.add_handler(CommandHandler("scan",      cmd_scan))
    app.add_handler(CommandHandler("pause",     cmd_pause))
    app.add_handler(CommandHandler("resume",    cmd_resume))
    app.add_handler(CommandHandler("status",    cmd_status))
    app.add_handler(CallbackQueryHandler(handle_callback))

    return app
