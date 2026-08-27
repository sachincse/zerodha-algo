"""Was the best pair in the grid actually good, or just the luckiest of 41?

Sweeping 41 parameter pairs and reporting the winner's CAGR is the textbook way
to manufacture an edge that does not exist. The maximum of 41 noisy Sharpe
ratios is biased upward even when every single pair is worthless, and the size
of that bias is computable rather than a matter of opinion.

Four things are reported, each answering a different question:

  DEFLATED SHARPE      Given that you tried N configurations, how much of the
                       winner's Sharpe survives the selection bias? A deflated
                       Sharpe near zero means the winner is what you would
                       expect from noise alone.

  PBO                  Across many train/test splits, how often does the pair
                       that ranked best in-sample land in the bottom half
                       out-of-sample? Above 0.5 means the selection procedure
                       is worse than picking at random.

  MINIMUM BACKTEST     Given N trials, how many years of data would you need
  LENGTH               before a Sharpe of 1.0 could be believed at all? If the
                       study is shorter than this, no amount of care in the
                       backtest rescues it.

  SPA / REALITY CHECK  Hansen's Superior Predictive Ability test: is the best
                       model genuinely better than the benchmark once the
                       entire search is accounted for? A high p-value means no.

The study's conclusion does not depend on any of these -- the rule loses by 8.8
points a year, which no correction is going to reverse. They are here because
"6/30 ranks 33rd of 41" is a multiple-comparisons claim, and making one without
the statistic that governs it is the same species of error as trusting a
causality suite nobody has mutation-tested.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class OverfittingReport:
    n_trials: int
    best_label: str
    best_sharpe: float
    deflated_sharpe: float | None
    psr_vs_zero: float | None
    min_backtest_years: float | None
    pbo: float | None
    spa_pvalue: float | None
    n_obs: int
    span_years: float
    notes: list = None

    def render(self) -> str:
        def fmt(x, suffix="", nd=3):
            return "n/a" if x is None else f"{x:.{nd}f}{suffix}"

        lines = [
            f"  trials searched          {self.n_trials}",
            f"  observations             {self.n_obs} ({self.span_years:.1f} years)",
            f"  best pair                {self.best_label}",
            f"  its Sharpe               {self.best_sharpe:.3f}",
            "",
            f"  deflated Sharpe          {fmt(self.deflated_sharpe)}"
            f"   {'<- selection-adjusted' if self.deflated_sharpe is not None else ''}",
            f"  P(Sharpe > 0)            {fmt(self.psr_vs_zero)}",
            f"  min backtest length      {fmt(self.min_backtest_years, ' years', 1)}"
            f"   (to trust Sharpe 1.0 after {self.n_trials} trials)",
            f"  P(backtest overfitting)  {fmt(self.pbo)}"
            f"   {'<- above 0.5 is worse than random' if self.pbo is not None else ''}",
            f"  SPA p-value              {fmt(self.spa_pvalue)}"
            f"   {'<- high means no skill survives the search' if self.spa_pvalue is not None else ''}",
        ]
        if self.notes:
            lines += [""] + [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def _sharpe(r: np.ndarray) -> float:
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0


def probability_of_backtest_overfitting(rets: pd.DataFrame, n_splits: int = 12,
                                        rng_seed: int = 7) -> float | None:
    """CSCV: how often the in-sample winner underperforms out-of-sample.

    Bailey/Lopez de Prado's combinatorially-symmetric cross-validation. The
    return matrix is cut into S contiguous blocks; every balanced split of
    those blocks into train/test halves is evaluated; for each split the pair
    with the best in-sample Sharpe is found and its out-of-sample RANK among
    all pairs is recorded. PBO is the fraction of splits where that rank falls
    in the bottom half.

    A local implementation rather than a dependency: the algorithm is twenty
    lines, and pinning it here means the number is reproducible from this repo
    alone rather than from whatever version of a library happens to install.
    """
    from itertools import combinations

    r = rets.dropna(how="any")
    if r.shape[1] < 2 or len(r) < 4 * n_splits:
        return None

    blocks = np.array_split(np.arange(len(r)), n_splits)
    half = n_splits // 2
    combos = list(combinations(range(n_splits), half))
    if len(combos) > 400:                       # keep it quick and deterministic
        rs = np.random.default_rng(rng_seed)
        combos = [combos[i] for i in rs.choice(len(combos), 400, replace=False)]

    logits = []
    arr = r.to_numpy()
    for train_blocks in combos:
        tr = np.concatenate([blocks[b] for b in train_blocks])
        te = np.concatenate([blocks[b] for b in range(n_splits)
                             if b not in train_blocks])
        is_sharpe = np.array([_sharpe(arr[tr, j]) for j in range(arr.shape[1])])
        oos_sharpe = np.array([_sharpe(arr[te, j]) for j in range(arr.shape[1])])

        best = int(np.argmax(is_sharpe))
        # relative rank of the chosen strategy out-of-sample, in [0, 1]
        rank = (oos_sharpe < oos_sharpe[best]).sum() / len(oos_sharpe)
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(np.log(rank / (1 - rank)))

    return float(np.mean(np.array(logits) < 0))


def analyse_grid(rets: pd.DataFrame, benchmark: pd.Series | None = None,
                 labels: dict | None = None) -> OverfittingReport:
    """`rets` is one column of daily returns per configuration tried."""
    notes = []
    r = rets.dropna(how="all").fillna(0.0)
    n_trials = r.shape[1]
    sharpes = {c: _sharpe(r[c].to_numpy()) for c in r.columns}
    best = max(sharpes, key=sharpes.get)
    best_sr = sharpes[best]
    span = (r.index[-1] - r.index[0]).days / 365.25

    dsr = psr = mbl = None
    eff_trials = n_trials
    try:
        from purgedcv import (deflated_sharpe_ratio, effective_n_trials,
                              minimum_backtest_length,
                              probabilistic_sharpe_ratio)
        srs = np.array(list(sharpes.values()), dtype=float)
        best_returns = r[best].to_numpy()

        # The 41 pairs are anything but independent: SMA 6/30 and 6/40 trade
        # almost the same days. effective_n_trials discounts that correlation,
        # so the deflation is not applied as though 41 genuinely separate bets
        # had been placed.
        try:
            eff_trials = int(effective_n_trials(srs))
            if eff_trials != n_trials:
                notes.append(f"{n_trials} pairs were tried but they overlap heavily; "
                             f"deflation uses {eff_trials} effective trials")
        except Exception:                                       # noqa: BLE001
            eff_trials = n_trials

        dsr = float(deflated_sharpe_ratio(
            best_returns, n_trials=max(eff_trials, 1),
            var_sharpe=float(np.var(srs, ddof=1)),
            bars_per_year=TRADING_DAYS))
        psr = float(probabilistic_sharpe_ratio(best_returns, 0.0))
        mbl = float(minimum_backtest_length(n_trials=max(eff_trials, 1),
                                            target_sharpe=1.0))
    except Exception as e:                                      # noqa: BLE001
        notes.append(f"purgedcv unavailable or its API changed ({type(e).__name__}: "
                     f"{str(e)[:80]}); deflated Sharpe and PSR skipped")

    pbo = probability_of_backtest_overfitting(r)
    if pbo is None:
        notes.append("PBO needs at least two configurations and a long enough "
                     "history; skipped")

    spa_p = None
    if benchmark is not None:
        try:
            from arch.bootstrap import SPA
            bm = benchmark.reindex(r.index).fillna(0.0)
            # SPA is framed in LOSSES: lower is better. Negated returns make
            # "beats the benchmark" the alternative hypothesis.
            spa = SPA(-bm.to_numpy(), -r.to_numpy(), reps=1000, seed=7)
            spa.compute()
            spa_p = float(np.asarray(spa.pvalues)[-1])
        except Exception as e:                                  # noqa: BLE001
            notes.append(f"SPA skipped ({type(e).__name__}: {str(e)[:80]})")

    return OverfittingReport(
        n_trials=n_trials,
        best_label=(labels or {}).get(best, best),
        best_sharpe=best_sr,
        deflated_sharpe=dsr, psr_vs_zero=psr, min_backtest_years=mbl,
        pbo=pbo, spa_pvalue=spa_p,
        n_obs=len(r), span_years=span, notes=notes)
