"""Emit theme-aware inline-SVG chart fragments for the published report.

A matplotlib PNG is baked to one theme and one resolution. These are polylines
that inherit CSS custom properties, so they resolve correctly in light and dark
and stay crisp at any width.

X is mapped from ROW POSITION, not from the datetime's integer value. Under
pandas 3 a DatetimeIndex may carry second or microsecond resolution, so
``astype("int64")`` no longer agrees with ``Timestamp.value`` (always
nanoseconds) and mixing the two silently throws every point off the canvas.
Trading days are evenly spaced for our purposes anyway.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

W, H = 1000.0, 420.0
PAD_L, PAD_R, PAD_T, PAD_B = 8.0, 8.0, 14.0, 26.0


def _positions(n: int, target: int = 520) -> np.ndarray:
    if n <= target:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, target).astype(int))


def equity_svg() -> dict:
    df = pd.read_csv(os.path.join(OUT, "equity_curves.csv"),
                     index_col=0, parse_dates=True)
    n = len(df)
    pos = _positions(n)

    series = [
        ("S0_naive", "s0"),
        ("S2_realcost", "s2"),
        ("PIT_BUY_HOLD", "null"),
        ("BENCHMARK_N100", "bench"),
        ("S3_pit", "s3"),
    ]
    present = [(c, k) for c, k in series if c in df.columns]

    lo = min(np.log10(df[c].min()) for c, _ in present)
    hi = max(np.log10(df[c].max()) for c, _ in present)
    pad = (hi - lo) * 0.04
    lo, hi = lo - pad, hi + pad

    def px(p):
        return PAD_L + p / (n - 1) * (W - PAD_L - PAD_R)

    def py(v):
        return PAD_T + (hi - np.log10(v)) / (hi - lo) * (H - PAD_T - PAD_B)

    paths = []
    for c, k in present:
        v = df[c].to_numpy()[pos]
        pts = " ".join(f"{px(a):.1f},{py(b):.1f}" for a, b in zip(pos, v))
        emph = k in ("s3", "bench", "null")
        paths.append(f'<polyline class="ln ln-{k}{" ln-emph" if emph else ""}" '
                     f'points="{pts}" />')

    grid, ticks = [], []
    for yr in range(df.index[0].year + 1, df.index[-1].year + 1, 3):
        ts = pd.Timestamp(f"{yr}-01-01")
        p = int(df.index.searchsorted(ts))
        if not (0 < p < n):
            continue
        x = px(p)
        grid.append(f'<line class="gl" x1="{x:.1f}" y1="{PAD_T}" '
                    f'x2="{x:.1f}" y2="{H - PAD_B}" />')
        ticks.append(f'<text class="tk" x="{x:.1f}" y="{H - 8}" '
                     f'text-anchor="middle">{yr}</text>')

    cap = float(df.iloc[0].max())
    for mult, lbl in [(1, "1x"), (2, "2x"), (4, "4x"), (8, "8x")]:
        v = cap * mult
        if not (10 ** lo <= v <= 10 ** hi):
            continue
        y = py(v)
        grid.append(f'<line class="gl" x1="{PAD_L}" y1="{y:.1f}" '
                    f'x2="{W - PAD_R}" y2="{y:.1f}" />')
        ticks.append(f'<text class="tk" x="{PAD_L + 4}" y="{y - 4:.1f}">{lbl}</text>')

    svg = (f'<svg class="chart" viewBox="0 0 {W:.0f} {H:.0f}" '
           f'preserveAspectRatio="none" role="img" '
           f'aria-label="Equity curves 2011 to 2026: the honest strategy ends far '
           f'below both the index and a no-timing hold of the same universe, while '
           f'the naive backtest ends above them">'
           + "".join(grid) + "".join(paths) + "".join(ticks) + "</svg>")
    return {"svg": svg, "n_points": len(pos)}


def drawdown_svg() -> str:
    df = pd.read_csv(os.path.join(OUT, "equity_curves.csv"),
                     index_col=0, parse_dates=True)
    n, h = len(df), 150.0
    pos = _positions(n)

    def px(p):
        return PAD_L + p / (n - 1) * (W - PAD_L - PAD_R)

    def pts(col):
        dd = ((df[col] / df[col].cummax() - 1) * 100).to_numpy()[pos]
        return " ".join(f"{px(a):.1f},{6 + (-b) / 50.0 * (h - 20):.1f}"
                        for a, b in zip(pos, dd))

    return (f'<svg class="chart chart-dd" viewBox="0 0 {W:.0f} {h:.0f}" '
            f'preserveAspectRatio="none" role="img" '
            f'aria-label="Drawdown 2011 to 2026: the strategy is underwater almost '
            f'continuously while the index repeatedly recovers to new highs">'
            f'<polygon class="dd-fill" points="{px(pos[0]):.1f},6 {pts("S3_pit")} '
            f'{px(pos[-1]):.1f},6" />'
            f'<polyline class="dd-bench" points="{pts("BENCHMARK_N100")}" />'
            f'</svg>')


if __name__ == "__main__":
    d = equity_svg()
    payload = {"equity": d["svg"], "drawdown": drawdown_svg()}
    json.dump(payload, open(os.path.join(OUT, "charts.json"), "w", encoding="utf-8"))

    # sanity: every x must land inside the canvas
    import re
    xs = [float(m) for m in re.findall(r'points="([^"]+)"', payload["equity"])[0]
          .split()[0::1][0:0]] or []
    allpts = re.findall(r"(-?\d+\.\d+),(-?\d+\.\d+)", payload["equity"])
    xs = [float(a) for a, _ in allpts]
    ys = [float(b) for _, b in allpts]
    print(f"equity svg: {len(allpts)} points, "
          f"x in [{min(xs):.1f}, {max(xs):.1f}], y in [{min(ys):.1f}, {max(ys):.1f}]")
    assert -1 <= min(xs) and max(xs) <= W + 1, "x out of canvas"
    assert -1 <= min(ys) and max(ys) <= H + 1, "y out of canvas"
    print("wrote", os.path.join(OUT, "charts.json"))
