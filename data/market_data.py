import time
from datetime import datetime, timedelta

import pandas as pd
import pytz

from auth.fyers_auth import get_fyers_instance

IST = pytz.timezone("Asia/Kolkata")


def get_daily_ohlcv(symbol: str, days: int = 300) -> pd.DataFrame:
    """
    Fetch daily OHLCV candles for symbol.
    Returns DataFrame with columns: date, open, high, low, close, volume
    sorted ascending by date.
    """
    fyers = get_fyers_instance()

    end_dt = datetime.now(IST)
    start_dt = end_dt - timedelta(days=days + 50)  # buffer for weekends/holidays

    data = {
        "symbol": symbol,
        "resolution": "D",
        "date_format": "1",
        "range_from": start_dt.strftime("%Y-%m-%d"),
        "range_to": end_dt.strftime("%Y-%m-%d"),
        "cont_flag": "1",
    }

    response = fyers.history(data=data)
    if response.get("s") != "ok":
        raise RuntimeError(f"Failed to fetch OHLCV for {symbol}: {response}")

    candles = response.get("candles", [])
    if not candles:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    df = df.drop(columns=["timestamp"])
    df = df[["date", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("date").reset_index(drop=True)

    # Keep only requested number of trading days
    df = df.tail(days).reset_index(drop=True)
    return df
