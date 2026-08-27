"""Render the equity-curve chart and a markdown report from the run outputs."""
from __future__ import annotations

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import pandas as pd                      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

INK = "#e6e8eb"
GRID = "#2a2f3a"
BG = "#0f1115"
COLORS = {
    "S0_naive": "#f0883e",
    "S1_realfill": "#d2a8ff",
    "S2_realcost": "#58a6ff",
    "S3_pit": "#f85149",
    "BENCHMARK_N100": "#3fb950",
    "PIT_BUY_HOLD": "#e3b341",
}
LABELS = {
    "S0_naive": "S0  naive backtest (same-bar fill, no costs, today's index)",
    "S1_realfill": "S1  + fills at next open",
    "S2_realcost": "S2  + Zerodha charges & slippage",
    "S3_pit": "S3  + point-in-time universe  (the honest one)",
    "BENCHMARK_N100": "Nifty 100 TRI, net of a 25bps index-fund fee",
    "PIT_BUY_HOLD": "same universe, no timing at all (the null)",
}


def style(ax):
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors="#8b929e", labelsize=9)
    ax.grid(True, color=GRID, lw=0.6, alpha=0.7)
    ax.set_axisbelow(True)


def chart_ladder():
    df = pd.read_csv(os.path.join(OUT, "equity_curves.csv"),
                     index_col=0, parse_dates=True)
    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(11, 8.5), facecolor=BG,
        gridspec_kw={"height_ratios": [2.4, 1], "hspace": 0.28})

    order = ["S0_naive", "S1_realfill", "S2_realcost", "S3_pit",
             "PIT_BUY_HOLD", "BENCHMARK_N100"]
    hero = ("S3_pit", "BENCHMARK_N100", "PIT_BUY_HOLD")
    for col in order:
        if col not in df:
            continue
        lw = 2.4 if col in hero else 1.3
        alpha = 1.0 if col in hero else 0.6
        ax.plot(df.index, df[col] / 1e5, color=COLORS[col], lw=lw,
                alpha=alpha, label=LABELS[col])

    style(ax)
    ax.set_yscale("log")
    ax.set_ylabel("portfolio value (Rs lakh, log scale)", color=INK, fontsize=10)
    ax.set_title("SMA(6)/SMA(30) on the Nifty 100 — what each assumption is worth\n"
                 "25 bps/side slippage, full Zerodha charges",
                 color=INK, fontsize=13, loc="left", pad=14)
    leg = ax.legend(facecolor="#161a21", edgecolor=GRID, fontsize=9, loc="upper left")
    for t in leg.get_texts():
        t.set_color(INK)

    dd = df["S3_pit"] / df["S3_pit"].cummax() - 1
    ddb = df["BENCHMARK_N100"] / df["BENCHMARK_N100"].cummax() - 1
    ax2.fill_between(df.index, dd * 100, 0, color="#f85149", alpha=0.55,
                     label="strategy (S3)")
    ax2.plot(df.index, ddb * 100, color="#3fb950", lw=1.4, label="Nifty 100 TRI")
    style(ax2)
    ax2.set_ylabel("drawdown %", color=INK, fontsize=10)
    leg2 = ax2.legend(facecolor="#161a21", edgecolor=GRID, fontsize=9, loc="lower left")
    for t in leg2.get_texts():
        t.set_color(INK)

    p = os.path.join(OUT, "equity_ladder.png")
    fig.savefig(p, dpi=150, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return p


def chart_walkforward():
    fp = os.path.join(OUT, "walkforward_equity.csv")
    if not os.path.exists(fp):
        return None
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    fig, ax = plt.subplots(figsize=(11, 5.2), facecolor=BG)
    palette = ["#58a6ff", "#f0883e", "#d2a8ff", "#3fb950"]
    nice = {"walk_forward": "walk-forward tuned (honest)",
            "fixed_6_30": "fixed SMA 6/30 (the video)",
            "nifty100": "Nifty 100 buy & hold"}
    for c, col in zip(df.columns, palette):
        lbl = nice.get(c, c.replace("hindsight_", "best in hindsight SMA ").replace("_", "/"))
        ax.plot(df.index, df[c] / 1e5, color=col, lw=2.0, label=lbl)
    style(ax)
    ax.set_yscale("log")
    ax.set_ylabel("portfolio value (Rs lakh, log)", color=INK, fontsize=10)
    ax.set_title("Tuning the parameters honestly vs tuning them with hindsight",
                 color=INK, fontsize=13, loc="left", pad=12)
    leg = ax.legend(facecolor="#161a21", edgecolor=GRID, fontsize=9, loc="upper left")
    for t in leg.get_texts():
        t.set_color(INK)
    p = os.path.join(OUT, "walkforward.png")
    fig.savefig(p, dpi=150, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return p


def chart_param_heat():
    fp = os.path.join(OUT, "param_grid.csv")
    if not os.path.exists(fp):
        return None
    g = pd.read_csv(fp)
    piv = g.pivot(index="short", columns="long", values="cagr_pct")
    fig, ax = plt.subplots(figsize=(8.5, 5), facecolor=BG)
    im = ax.imshow(piv.values, cmap="RdYlGn", aspect="auto", vmin=-2, vmax=14)
    ax.set_xticks(range(len(piv.columns)), piv.columns, color="#8b929e")
    ax.set_yticks(range(len(piv.index)), piv.index, color="#8b929e")
    ax.set_xlabel("long SMA", color=INK)
    ax.set_ylabel("short SMA", color=INK)
    ax.set_title("CAGR % across the parameter grid (all honest, all costed)",
                 color=INK, fontsize=12, loc="left", pad=12)
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            v = piv.values[i, j]
            if pd.notna(v):
                ax.text(j, i, f"{v:.1f}", ha="center", va="center",
                        fontsize=8, color="#101010")
    cb = fig.colorbar(im, ax=ax)
    cb.ax.tick_params(colors="#8b929e")
    for s in ax.spines.values():
        s.set_color(GRID)
    p = os.path.join(OUT, "param_grid.png")
    fig.savefig(p, dpi=150, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return p


if __name__ == "__main__":
    import sys
    sys.path.insert(0, HERE)
    from src.report import write_report

    for fn in (chart_ladder, chart_walkforward, chart_param_heat, write_report):
        try:
            p = fn()
            print("wrote", p) if p else print(f"skipped {fn.__name__} (no input)")
        except Exception as e:
            print(f"{fn.__name__} failed: {type(e).__name__}: {e}")
