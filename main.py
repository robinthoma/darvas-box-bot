import asyncio
import logging
import threading
from datetime import datetime

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

import config
from state.db_manager import (
    init_db,
    get_watchlist,
    upsert_box,
    add_signal,
    get_all_confirmed_boxes,
    get_open_positions,
    close_position,
    get_active_box,
)
from data.market_data import get_daily_ohlcv
from data.realtime import get_portfolio_tracker
from strategy.darvas_engine import analyze_symbol
from notifications.telegram_bot import (
    build_app,
    set_scan_callback,
    set_last_scan_time,
    is_paused,
    send_alert,
    format_entry_alert,
    format_exit_alert,
    format_box_alert,
    format_daily_summary,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# Telegram app (set at startup)
_tg_app = None


def run_daily_scan():
    """Core scan logic: fetch data, detect signals, send alerts."""
    if is_paused():
        logger.info("Scan skipped — bot is paused.")
        return

    logger.info("Starting daily scan…")
    watchlist = get_watchlist()
    if not watchlist:
        logger.info("Watchlist is empty, nothing to scan.")
        return

    entry_alerts = []
    exit_alerts = []
    box_alerts = []

    for symbol in watchlist:
        try:
            df = get_daily_ohlcv(symbol, days=300)
            if df.empty:
                logger.warning(f"No data for {symbol}, skipping.")
                continue

            active_box = get_active_box(symbol)
            signal = analyze_symbol(df, active_box)

            if signal is None:
                continue

            sig_type = signal["type"]
            logger.info(f"{symbol}: {sig_type} @ {signal['price']:.2f}")

            # Persist box state
            if sig_type in ("BOX_FORMED", "ENTRY", "NEW_HIGH"):
                high_date = signal.get("date")
                confirm_date = signal.get("confirm_date")
                status = "confirmed" if sig_type in ("BOX_FORMED", "ENTRY") else "forming"
                upsert_box(
                    symbol=symbol,
                    box_top=signal["box_top"],
                    box_bottom=signal["box_bottom"],
                    high_date=str(high_date) if high_date else None,
                    confirmed_date=str(confirm_date) if confirm_date else None,
                    status=status,
                )

            # Persist signal
            qty = None
            if sig_type == "ENTRY" and signal["price"]:
                qty = max(1, int(config.CAPITAL_PER_TRADE / signal["price"]))

            add_signal(
                symbol=symbol,
                signal_type=sig_type,
                price=signal["price"],
                quantity=qty,
                details=signal.get("details"),
            )

            # Format alerts for Telegram
            if sig_type == "ENTRY":
                entry_alerts.append((symbol, signal))
            elif sig_type == "EXIT":
                exit_alerts.append((symbol, signal))
            elif sig_type == "BOX_FORMED":
                box_alerts.append((symbol, signal))

        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}", exc_info=True)

    # Send alerts
    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    set_last_scan_time(now_str)

    if _tg_app is None:
        logger.warning("Telegram app not ready, skipping notifications.")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _send_all():
        for symbol, sig in entry_alerts:
            await send_alert(_tg_app, format_entry_alert(symbol, sig))
        for symbol, sig in exit_alerts:
            await send_alert(_tg_app, format_exit_alert(symbol, sig))
        for symbol, sig in box_alerts:
            await send_alert(_tg_app, format_box_alert(symbol, sig))

        # Daily summary
        confirmed_boxes = get_all_confirmed_boxes()
        open_positions = get_open_positions()
        summary = format_daily_summary(
            entry_count=len(entry_alerts),
            exit_count=len(exit_alerts),
            box_count=len(confirmed_boxes),
            position_count=len(open_positions),
        )
        await send_alert(_tg_app, summary)

    try:
        loop.run_until_complete(_send_all())
    finally:
        loop.close()

    logger.info(
        f"Scan complete: {len(entry_alerts)} entries, {len(exit_alerts)} exits, "
        f"{len(box_alerts)} boxes."
    )


def main():
    global _tg_app

    # 1. Init database
    logger.info("Initialising database…")
    init_db()

    # 2. Build Telegram app
    logger.info("Building Telegram bot…")
    _tg_app = build_app()
    set_scan_callback(run_daily_scan)

    # 3. Start PortfolioTracker WebSocket in background thread
    logger.info("Starting PortfolioTracker…")
    portfolio_tracker = get_portfolio_tracker()
    portfolio_thread = threading.Thread(target=portfolio_tracker.start, daemon=True)
    portfolio_thread.start()

    # 4. APScheduler — daily scan at EVAL_TIME IST Mon–Fri
    hour, minute = map(int, config.EVAL_TIME.split(":"))
    scheduler = BackgroundScheduler(timezone=IST)
    scheduler.add_job(
        run_daily_scan,
        trigger="cron",
        day_of_week="mon-fri",
        hour=hour,
        minute=minute,
        id="daily_scan",
    )
    scheduler.start()
    logger.info(f"Scheduler started — daily scan at {config.EVAL_TIME} IST (Mon–Fri)")

    # 5. Run Telegram bot (blocking)
    logger.info("Starting Telegram polling…")
    _tg_app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
