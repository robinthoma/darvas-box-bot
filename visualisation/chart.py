import io
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def draw_darvas_chart(
    symbol: str,
    df: pd.DataFrame,
    boxes: list[dict],
) -> bytes:
    """
    Draw a Darvas box candlestick chart and return PNG bytes.

    boxes: list of dicts with keys:
        box_top, box_bottom, high_date, confirm_date, status
        ('confirmed' | 'forming' | 'broken')
    """
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.03,
    )

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df["date"].astype(str),
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="Price",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ),
        row=1,
        col=1,
    )

    # Volume bars
    colors = [
        "#26a69a" if c >= o else "#ef5350"
        for c, o in zip(df["close"], df["open"])
    ]
    fig.add_trace(
        go.Bar(
            x=df["date"].astype(str),
            y=df["volume"],
            name="Volume",
            marker_color=colors,
            opacity=0.7,
        ),
        row=2,
        col=1,
    )

    # Draw Darvas boxes
    for box in boxes:
        status = box.get("status", "confirmed")
        if status == "confirmed":
            fill_color = "rgba(0,200,100,0.15)"
            line_color = "rgba(0,200,100,0.8)"
            line_dash = "solid"
        elif status == "forming":
            fill_color = "rgba(255,165,0,0.10)"
            line_color = "rgba(255,165,0,0.8)"
            line_dash = "dash"
        else:
            fill_color = "rgba(150,150,150,0.08)"
            line_color = "rgba(150,150,150,0.5)"
            line_dash = "dot"

        high_date = str(box.get("high_date", df["date"].iloc[-30]))
        confirm_date = str(box.get("confirm_date", df["date"].iloc[-1]))

        fig.add_shape(
            type="rect",
            x0=high_date,
            x1=confirm_date,
            y0=box["box_bottom"],
            y1=box["box_top"],
            fillcolor=fill_color,
            line=dict(color=line_color, width=1.5, dash=line_dash),
            row=1,
            col=1,
        )

        # Box top label
        fig.add_annotation(
            x=confirm_date,
            y=box["box_top"],
            text=f"  ₹{box['box_top']:.2f}",
            showarrow=False,
            font=dict(size=9, color=line_color),
            xanchor="left",
            row=1,
            col=1,
        )

    # Entry/exit markers from signal history (if passed via box 'signals' key)
    entry_dates, entry_prices, exit_dates, exit_prices = [], [], [], []
    for box in boxes:
        for sig in box.get("signals", []):
            if sig["type"] == "ENTRY":
                entry_dates.append(str(sig["date"]))
                entry_prices.append(sig["price"])
            elif sig["type"] == "EXIT":
                exit_dates.append(str(sig["date"]))
                exit_prices.append(sig["price"])

    if entry_dates:
        fig.add_trace(
            go.Scatter(
                x=entry_dates,
                y=entry_prices,
                mode="markers",
                name="Entry",
                marker=dict(symbol="triangle-up", size=12, color="#00c853"),
            ),
            row=1,
            col=1,
        )

    if exit_dates:
        fig.add_trace(
            go.Scatter(
                x=exit_dates,
                y=exit_prices,
                mode="markers",
                name="Exit",
                marker=dict(symbol="triangle-down", size=12, color="#d50000"),
            ),
            row=1,
            col=1,
        )

    fig.update_layout(
        title=dict(text=f"Darvas Box Chart — {symbol}", font=dict(size=16)),
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        legend=dict(orientation="h", y=1.02),
        height=700,
        width=1100,
        margin=dict(l=60, r=60, t=60, b=40),
    )
    fig.update_xaxes(showgrid=True, gridcolor="#2d2d44")
    fig.update_yaxes(showgrid=True, gridcolor="#2d2d44")

    png_bytes = fig.to_image(format="png", scale=1.5)
    return png_bytes
