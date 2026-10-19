"""
Chart image for Telegram: last ~120 H1 candles with EMA20/50/200, nearest
support/resistance, and the signal's reference levels.  Dark theme, gold
accent, rendered with matplotlib (no GUI needed).
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from market import indicators as ind  # noqa: E402
from market.analysis import TechSnapshot  # noqa: E402
from signals.engine import Signal  # noqa: E402

BG = "#0E131B"
PANEL = "#151C26"
GRID = "#243040"
INK = "#E8ECF2"
INK_2 = "#9AA6B8"
GOLD = "#E3B341"
BULL = "#1FA88F"
BEAR = "#D9472E"
EMA20, EMA50, EMA200 = "#B8882A", "#2F7FD4", "#D4569C"


def render_chart(hourly: pd.DataFrame, tech: TechSnapshot, sig: Signal, bars: int = 120) -> bytes:
    df = hourly.tail(bars).copy()
    close = hourly["close"]
    e20 = ind.ema(close, 20).reindex(df.index)
    e50 = ind.ema(close, 50).reindex(df.index)
    e200 = ind.ema(close, 200).reindex(df.index)

    fig, ax = plt.subplots(figsize=(11, 5.6), dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(PANEL)

    x = mdates.date2num(df.index.to_pydatetime())
    width = (x[1] - x[0]) * 0.6 if len(x) > 1 else 0.02
    for xi, (_, row) in zip(x, df.iterrows()):
        up = row["close"] >= row["open"]
        color = BULL if up else BEAR
        ax.vlines(xi, row["low"], row["high"], color=color, linewidth=0.9, alpha=0.9)
        body_low = min(row["open"], row["close"])
        body_h = abs(row["close"] - row["open"]) or (row["high"] - row["low"]) * 0.02
        ax.bar(xi, body_h, bottom=body_low, width=width, color=color, linewidth=0)

    ax.plot(x, e20, color=EMA20, linewidth=1.3, label="EMA 20")
    ax.plot(x, e50, color=EMA50, linewidth=1.3, label="EMA 50")
    ax.plot(x, e200, color=EMA200, linewidth=1.3, label="EMA 200")

    lo, hi = float(df["low"].min()), float(df["high"].max())
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad

    def hline(y: float | None, color: str, label: str, style: str = "--") -> None:
        if y is None or not (lo < y < hi):
            return
        ax.axhline(y, color=color, linestyle=style, linewidth=0.9, alpha=0.8)
        ax.text(x[-1], y, f"  {label} {y:,.1f}", color=color, fontsize=7.5, va="center", ha="left")

    hline(tech.nearest_support(), INK_2, "S")
    hline(tech.nearest_resistance(), INK_2, "R")
    if sig.direction != "flat":
        hline(sig.stop, BEAR, "stop", ":")
        hline(sig.target1, BULL, "T1", ":")
        hline(sig.target2, BULL, "T2", ":")

    ax.set_xlim(x[0], x[-1] + (x[-1] - x[0]) * 0.14)
    ax.set_ylim(lo, hi)
    ax.grid(color=GRID, linewidth=0.6, alpha=0.6)
    ax.tick_params(colors=INK_2, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))

    title = f"XAU/USD  {tech.price:,.2f}   {sig.label}  ·  confidence {sig.confidence}%"
    ax.set_title(title, color=INK, fontsize=11, loc="left", pad=24, fontweight="bold")
    ax.text(
        0.0, 1.012, f"H1 · tech {tech.score:+d} · news {sig.news_score:+d} · RSI {tech.rsi:.0f} · ATR {tech.atr:.1f}",
        transform=ax.transAxes, color=INK_2, fontsize=8, va="bottom",
    )
    leg = ax.legend(loc="upper left", fontsize=8, frameon=False, ncol=3)
    for text in leg.get_texts():
        text.set_color(INK_2)
    fig.text(0.99, 0.01, "XAU Sentinel · decision support only", color=INK_2, fontsize=7, ha="right", alpha=0.7)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()
