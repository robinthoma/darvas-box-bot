from typing import Optional
import pandas as pd

import config


def find_52w_high(df: pd.DataFrame) -> Optional[tuple[int, float]]:
    """
    Find the most recent candle where high >= max high of last 252 candles,
    but only if that candle is within the last 5 candles.

    Returns (index, high_value) or None.
    """
    if len(df) < 10:
        return None

    window = df.tail(252)
    max_high = window["high"].max()
    recent = df.tail(5)

    # Find latest candle in recent where high >= max_high
    hits = recent[recent["high"] >= max_high]
    if hits.empty:
        return None

    idx = hits.index[-1]
    return idx, df.loc[idx, "high"]


def confirm_box(
    df: pd.DataFrame, high_idx: int
) -> tuple[bool, Optional[float], Optional[float], Optional[object]]:
    """
    After the high candle at high_idx, check that 3 consecutive candles
    have high <= box_top (the high at high_idx).

    Returns (confirmed, box_top, box_bottom, confirm_date).
    box_bottom = low of the high candle.
    """
    box_top = df.loc[high_idx, "high"]
    box_bottom = df.loc[high_idx, "low"]

    subsequent = df[df.index > high_idx].head(3)
    if len(subsequent) < 3:
        return False, box_top, box_bottom, None

    for _, row in subsequent.iterrows():
        if row["high"] > box_top:
            return False, box_top, box_bottom, None

    confirm_date = subsequent.iloc[-1]["date"]
    return True, box_top, box_bottom, confirm_date


def check_entry(row: pd.Series, prev_row: pd.Series, box_top: float) -> bool:
    """
    Entry: close > box_top * (1 + BREAKOUT_BUFFER) AND volume > prev volume.
    """
    return (
        row["close"] > box_top * (1 + config.BREAKOUT_BUFFER)
        and row["volume"] > prev_row["volume"]
    )


def check_exit(row: pd.Series, box_bottom: float) -> bool:
    """
    Exit: close < box_bottom.
    """
    return row["close"] < box_bottom


def analyze_symbol(df: pd.DataFrame, active_box_state: Optional[dict]) -> Optional[dict]:
    """
    Full pipeline for a single symbol.

    active_box_state: dict with keys {box_top, box_bottom, status, high_idx, confirm_date}
                      or None if no active box.

    Returns a signal dict or None:
      {type, price, box_top, box_bottom, volume_ratio, date, details}
    signal types: 'ENTRY', 'EXIT', 'BOX_FORMED', 'NEW_HIGH'
    """
    if df.empty or len(df) < 5:
        return None

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    # --- Check exit if position / confirmed box exists ---
    if active_box_state and active_box_state.get("status") == "confirmed":
        box_bottom = active_box_state["box_bottom"]
        box_top = active_box_state["box_top"]

        if check_exit(latest, box_bottom):
            return {
                "type": "EXIT",
                "price": latest["close"],
                "box_top": box_top,
                "box_bottom": box_bottom,
                "date": latest["date"],
                "details": f"Close {latest['close']:.2f} < Box Bottom {box_bottom:.2f}",
            }

        if check_entry(latest, prev, box_top):
            vol_ratio = latest["volume"] / prev["volume"] if prev["volume"] else 0
            return {
                "type": "ENTRY",
                "price": latest["close"],
                "box_top": box_top,
                "box_bottom": box_bottom,
                "volume_ratio": vol_ratio,
                "date": latest["date"],
                "details": f"Breakout above {box_top:.2f} with {vol_ratio:.1f}x volume",
            }

        return None

    # --- Detect a new 52-week high and attempt box formation ---
    result = find_52w_high(df)
    if result is None:
        return None

    high_idx, high_val = result

    # If high is the latest candle, box can't be confirmed yet
    if high_idx == df.index[-1]:
        return {
            "type": "NEW_HIGH",
            "price": high_val,
            "box_top": high_val,
            "box_bottom": df.loc[high_idx, "low"],
            "date": latest["date"],
            "details": f"New 52W high at {high_val:.2f}, awaiting box confirmation",
        }

    confirmed, box_top, box_bottom, confirm_date = confirm_box(df, high_idx)
    if confirmed:
        signal = {
            "type": "BOX_FORMED",
            "price": latest["close"],
            "box_top": box_top,
            "box_bottom": box_bottom,
            "date": latest["date"],
            "confirm_date": confirm_date,
            "details": f"Box confirmed: Top={box_top:.2f} Bottom={box_bottom:.2f}",
        }

        # Also check if breakout already happened on latest candle
        if check_entry(latest, prev, box_top):
            vol_ratio = latest["volume"] / prev["volume"] if prev["volume"] else 0
            signal["type"] = "ENTRY"
            signal["volume_ratio"] = vol_ratio
            signal["details"] = f"Breakout above {box_top:.2f} with {vol_ratio:.1f}x volume"

        return signal

    return None


def find_all_boxes_for_chart(df: pd.DataFrame) -> list[dict]:
    """
    Walk through the full DataFrame and collect all confirmed Darvas boxes.
    Returns list of dicts suitable for draw_darvas_chart().
    """
    boxes = []
    seen_highs: set[int] = set()

    for i in range(10, len(df)):
        slice_df = df.iloc[:i + 1].copy()
        result = find_52w_high(slice_df)
        if result is None:
            continue

        high_idx, high_val = result
        if high_idx in seen_highs:
            continue

        confirmed, box_top, box_bottom, confirm_date = confirm_box(slice_df, high_idx)
        if not confirmed:
            continue

        seen_highs.add(high_idx)
        high_date = slice_df.loc[high_idx, "date"]

        # Determine status: broken if a later candle closed below box_bottom
        status = "confirmed"
        subsequent = df[df["date"] > confirm_date] if confirm_date else df.iloc[i + 1:]
        for _, row in subsequent.iterrows():
            if row["close"] < box_bottom:
                status = "broken"
                break

        boxes.append({
            "box_top": box_top,
            "box_bottom": box_bottom,
            "high_date": str(high_date),
            "confirm_date": str(confirm_date) if confirm_date else str(high_date),
            "status": status,
            "signals": [],
        })

    return boxes
