"""Performance statistics.

Every statistic is computed from the realised equity curve only. Nothing here
rescales by a full-sample constant that the strategy could not have known at the
time (no full-sample vol targeting, no in-sample normalisation).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def cagr(equity: pd.Series) -> float:
    if len(equity) < 2:
        return float("nan")
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return float("nan")
    return (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1


def volatility(equity: pd.Series) -> float:
    return _returns(equity).std() * np.sqrt(TRADING_DAYS)


def sharpe(equity: pd.Series, rf: float = 0.065) -> float:
    """Risk-free defaults to 6.5%, roughly the Indian short-rate over the
    sample. A strategy that sits in cash a lot must clear this bar."""
    r = _returns(equity)
    if r.std() == 0 or len(r) < 2:
        return float("nan")
    excess = r - (rf / TRADING_DAYS)
    return excess.mean() / r.std() * np.sqrt(TRADING_DAYS)


def sortino(equity: pd.Series, rf: float = 0.065) -> float:
    r = _returns(equity)
    excess = r - (rf / TRADING_DAYS)
    downside = r[r < 0].std()
    if not downside or np.isnan(downside):
        return float("nan")
    return excess.mean() / downside * np.sqrt(TRADING_DAYS)


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((equity / peak - 1).min())


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1


def longest_drawdown_days(equity: pd.Series) -> int:
    dd = drawdown_series(equity)
    longest = cur = 0
    for v in dd:
        cur = cur + 1 if v < -1e-9 else 0
        longest = max(longest, cur)
    return longest


def exposure(n_positions: pd.Series, max_positions: int) -> float:
    return float((n_positions / max_positions).mean())


def trade_stats(trades: list) -> dict:
    if not trades:
        return {"n_trades": 0}
    net = np.array([t.net_pnl for t in trades])
    rets = np.array([t.net_return_pct for t in trades])
    hold = np.array([t.holding_days for t in trades])
    wins, losses = net[net > 0], net[net <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    return {
        "n_trades": len(trades),
        "win_rate": len(wins) / len(net),
        "avg_return_pct": float(rets.mean()),
        "median_return_pct": float(np.median(rets)),
        "avg_win_pct": float(rets[net > 0].mean()) if len(wins) else 0.0,
        "avg_loss_pct": float(rets[net <= 0].mean()) if len(losses) else 0.0,
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "avg_holding_days": float(hold.mean()),
        "median_holding_days": float(np.median(hold)),
        "total_charges": float(sum(t.charges for t in trades)),
        "total_dividends": float(sum(t.dividends for t in trades)),
        "total_net_pnl": float(net.sum()),
        "best_trade_pct": float(rets.max()),
        "worst_trade_pct": float(rets.min()),
    }


def summarize(result, benchmark: pd.Series | None = None) -> dict:
    eq = result.equity
    out = {
        "label": result.label,
        "start": eq.index[0].date().isoformat(),
        "end": eq.index[-1].date().isoformat(),
        "years": round((eq.index[-1] - eq.index[0]).days / 365.25, 2),
        "initial": float(eq.iloc[0]),
        "final": float(eq.iloc[-1]),
        "total_return_pct": float((eq.iloc[-1] / eq.iloc[0] - 1) * 100),
        "cagr_pct": cagr(eq) * 100,
        "vol_pct": volatility(eq) * 100,
        "sharpe": sharpe(eq),
        "sortino": sortino(eq),
        "max_drawdown_pct": max_drawdown(eq) * 100,
        "longest_dd_days": longest_drawdown_days(eq),
        "avg_exposure": exposure(result.daily_positions,
                                 result.settings.portfolio.max_positions),
        "rejected_fills": result.rejected_fills,
    }
    out.update(trade_stats(result.trades))

    if benchmark is not None and len(benchmark) > 1:
        b = benchmark.reindex(eq.index).ffill().dropna()
        if len(b) > 1:
            b = b / b.iloc[0] * eq.iloc[0]
            out["benchmark_cagr_pct"] = cagr(b) * 100
            out["benchmark_total_return_pct"] = float((b.iloc[-1] / b.iloc[0] - 1) * 100)
            out["benchmark_max_dd_pct"] = max_drawdown(b) * 100
            out["benchmark_sharpe"] = sharpe(b)
            out["alpha_cagr_pct"] = out["cagr_pct"] - out["benchmark_cagr_pct"]
    return out


def format_summary(s: dict) -> str:
    def g(k, fmt="{:.2f}"):
        v = s.get(k)
        return fmt.format(v) if isinstance(v, (int, float)) and not np.isnan(v) else "-"

    lines = [
        f"  period            {s['start']} -> {s['end']}  ({s['years']}y)",
        f"  final equity      Rs {s['final']:,.0f}   (from Rs {s['initial']:,.0f})",
        f"  CAGR              {g('cagr_pct')}%"
        + (f"      benchmark {g('benchmark_cagr_pct')}%" if "benchmark_cagr_pct" in s else ""),
        f"  total return      {g('total_return_pct')}%"
        + (f"      benchmark {g('benchmark_total_return_pct')}%"
           if "benchmark_total_return_pct" in s else ""),
        f"  volatility        {g('vol_pct')}%",
        f"  Sharpe            {g('sharpe')}"
        + (f"       benchmark {g('benchmark_sharpe')}" if "benchmark_sharpe" in s else ""),
        f"  Sortino           {g('sortino')}",
        f"  max drawdown      {g('max_drawdown_pct')}%"
        + (f"     benchmark {g('benchmark_max_dd_pct')}%" if "benchmark_max_dd_pct" in s else ""),
        f"  longest drawdown  {s.get('longest_dd_days','-')} trading days",
        f"  avg exposure      {g('avg_exposure')}  (1.0 = fully invested)",
        f"  trades            {s.get('n_trades',0)}   win rate {g('win_rate')}"
        f"   profit factor {g('profit_factor')}",
        f"  avg / median hold {g('avg_holding_days')} / {g('median_holding_days')} days",
        f"  avg trade         {g('avg_return_pct')}%   "
        f"(win {g('avg_win_pct')}%, loss {g('avg_loss_pct')}%)",
        f"  charges paid      Rs {s.get('total_charges',0):,.0f}   "
        f"dividends Rs {s.get('total_dividends',0):,.0f}",
        f"  unfilled orders   {s.get('rejected_fills',0)}",
    ]
    if "alpha_cagr_pct" in s:
        lines.append(f"  ALPHA vs bench    {g('alpha_cagr_pct')}% CAGR")
    return "\n".join(lines)
