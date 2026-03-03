from typing import Optional
import pandas as pd

import config


def find_current_box(df: pd.DataFrame) -> Optional[dict]:
    """
    Find the current Darvas box state from full OHLCV history.

    Rules (from chart analysis):
    - Box Top    = 52-week high (highest high in last 252 candles)
    - Box Bottom = lowest low from the 52W high date to today (or entry date)
    - Entry      = 3 consecutive daily closes above Box Top
    - Expansion  = if price makes a new high before 3 confirms,
                   update Box Top, reset confirm count, keep tracking lows
    - Stop Loss  = Box Bottom (locked at the moment of entry confirmation)

    Returns dict or None.
    """
    if len(df) < 10:
        return None

    window = min(252, len(df))
    lookback = df.tail(window)
    max_high = lookback["high"].max()

    # Most recent candle that achieved the 52W high
    high_mask = lookback["high"] >= max_high
    high_pos = high_mask[high_mask].index[-1]
    high_date = df.loc[high_pos, "date"]

    # All candles from the 52W high onwards
    box_df = df.loc[high_pos:].copy()
    if len(box_df) < 2:
        return None

    box_top = max_high
    box_bottom_running = float("inf")   # Tracks lowest low (updates every candle)
    box_bottom_locked = None            # Locked when entry is triggered
    confirm_count = 0
    entry_triggered = False
    entry_date = None

    for i, (idx, row) in enumerate(box_df.iterrows()):
        # Track lowest low throughout the period
        box_bottom_running = min(box_bottom_running, row["low"])

        if i == 0:
            # The 52W high candle itself — initialise only
            continue

        if not entry_triggered:
            # Expand box if new high made before entry confirmed
            if row["high"] > box_top:
                box_top = row["high"]
                confirm_count = 0   # Reset — must see 3 fresh consecutive closes

            # Count consecutive closes above box top
            if row["close"] > box_top:
                confirm_count += 1
                if confirm_count >= 3:
                    entry_triggered = True
                    entry_date = row["date"]
                    box_bottom_locked = box_bottom_running  # Lock stop loss
            else:
                confirm_count = 0

    box_bottom = box_bottom_locked if entry_triggered else box_bottom_running

    return {
        "box_top": box_top,
        "box_bottom": box_bottom,
        "confirm_count": min(confirm_count, 3),
        "entry_triggered": entry_triggered,
        "entry_date": str(entry_date) if entry_date else None,
        "high_date": str(high_date),
    }


def analyze_symbol(df: pd.DataFrame, active_box_state: Optional[dict]) -> Optional[dict]:
    """
    Analyse a symbol against its current box state and return a signal or None.

    Signal types: ENTRY | EXIT | BOX_FORMED | BOX_FORMING
    """
    if df.empty or len(df) < 10:
        return None

    box = find_current_box(df)
    if box is None:
        return None

    latest = df.iloc[-1]
    db_status = active_box_state.get("status") if active_box_state else None

    box_top = box["box_top"]
    box_bottom = box["box_bottom"]
    confirm_count = box["confirm_count"]
    entry_triggered = box["entry_triggered"]

    # ── In position: only watch for exit ──────────────────────────────────
    if db_status == "entry_signaled":
        if latest["close"] < box_bottom:
            return {
                "type": "EXIT",
                "price": latest["close"],
                "box_top": box_top,
                "box_bottom": box_bottom,
                "date": latest["date"],
                "details": (
                    f"Close {latest['close']:.2f} below stop loss {box_bottom:.2f}"
                ),
            }
        return None

    # ── Entry just confirmed (3 consecutive closes above box top) ─────────
    if entry_triggered and db_status != "entry_signaled":
        return {
            "type": "ENTRY",
            "price": latest["close"],
            "box_top": box_top,
            "box_bottom": box_bottom,
            "confirm_count": 3,
            "date": latest["date"],
            "details": (
                f"3-candle breakout confirmed above {box_top:.2f} | "
                f"Stop loss {box_bottom:.2f}"
            ),
        }

    # ── Breakout in progress (1 or 2 candles above box top) ───────────────
    if confirm_count > 0:
        return {
            "type": "BOX_FORMING",
            "price": latest["close"],
            "box_top": box_top,
            "box_bottom": box_bottom,
            "confirm_count": confirm_count,
            "date": latest["date"],
            "details": (
                f"Breakout in progress: {confirm_count}/3 candles "
                f"above {box_top:.2f}"
            ),
        }

    # ── Box formed — watching for breakout ────────────────────────────────
    return {
        "type": "BOX_FORMED",
        "price": latest["close"],
        "box_top": box_top,
        "box_bottom": box_bottom,
        "confirm_count": 0,
        "date": latest["date"],
        "details": (
            f"Box Top {box_top:.2f} | Bottom {box_bottom:.2f} | "
            f"Entry above {box_top:.2f} (3 closes needed)"
        ),
    }


def find_all_boxes_for_chart(df: pd.DataFrame) -> list[dict]:
    """
    Walk forward through history and collect all Darvas boxes for charting.
    Returns list of box dicts suitable for draw_darvas_chart().
    """
    if len(df) < 20:
        return []

    boxes = []
    window = min(252, len(df))
    seen_high_dates: set = set()

    # Slide through the data in chunks to find historical boxes
    for end_i in range(window, len(df) + 1, 5):  # step 5 for performance
        slice_df = df.iloc[:end_i]
        box = find_current_box(slice_df)
        if box is None:
            continue

        high_date = box["high_date"]
        if high_date in seen_high_dates:
            continue
        seen_high_dates.add(high_date)

        status = "confirmed" if box["entry_triggered"] else "forming"

        boxes.append({
            "box_top": box["box_top"],
            "box_bottom": box["box_bottom"],
            "high_date": high_date,
            "confirm_date": box["entry_date"] or str(df.iloc[-1]["date"]),
            "status": status,
            "signals": [],
        })

    # Deduplicate by box_top (keep latest entry per price level)
    seen_tops: set = set()
    unique = []
    for b in reversed(boxes):
        key = round(b["box_top"], 1)
        if key not in seen_tops:
            seen_tops.add(key)
            unique.append(b)

    return list(reversed(unique))
