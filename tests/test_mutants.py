"""Test the tests: inject real lookahead bugs and require the suite to catch them.

A suite that claims to prove causality is worth exactly what it catches, and
nothing about a green run tells you that. The only way to know is to break
causality on purpose and check the tests go red.

That is not hypothetical here. When this was first run against the original
15 tests, 4 of these 8 mutations survived untouched. All four sat in the same
two blind spots: the point-in-time universe builder, which every scramble test
bypassed by passing all_true_membership(), and the branches a smooth synthetic
fixture never reaches. tests/test_leak_coverage.py exists because of this file.

Two further mutations survived at first and turned out to be MY error, not the
suite's — expanding(min_periods=1) reads only rows 0..t and is therefore
causal, and cl.at[t] is legitimately known when an order is queued at the close
of t. Both are noted inline. A mutation that is not actually a leak proves
nothing when it survives, and quietly flatters the suite when it dies.

Slow by design: it runs the whole suite once per mutation in a throwaway copy.

    python -m pytest tests/test_mutants.py -v          # ~2 minutes
    python -m pytest tests/ -q -m "not slow"           # skip it
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# (id, file, find, replace, what it simulates)
MUTATIONS = [
    ("fill-at-signal-close", "src/backtest.py",
     "o_open = op.at[t, o.symbol] if o.symbol in op.columns else np.nan",
     "o_open = cl.at[o.queued_on, o.symbol] if o.symbol in cl.columns else np.nan",
     "fills at the signal bar's close instead of the next open"),

    ("fill-at-todays-close", "src/backtest.py",
     "o_open = op.at[t, o.symbol] if o.symbol in op.columns else np.nan",
     "o_open = cl.at[t, o.symbol] if o.symbol in cl.columns else np.nan",
     "fills at a close only knowable once the session has ended"),

    ("centered-sma", "src/strategy.py",
     "s.rolling(window, min_periods=window).mean().reindex(close.index)",
     "s.rolling(window, min_periods=window, center=True).mean().reindex(close.index)",
     "centered moving average, averaging future bars"),

    ("sma-peeks-one-ahead", "src/strategy.py",
     "s.rolling(window, min_periods=window).mean().reindex(close.index)",
     "s.rolling(window, min_periods=window).mean().shift(-1).reindex(close.index)",
     "SMA shifted one bar into the future"),

    ("centered-turnover", "src/universe.py",
     "(cl * vol).rolling(lookback, min_periods=lookback // 2).median()",
     "(cl * vol).rolling(lookback, min_periods=lookback // 2, center=True).median()",
     "universe selected on partly-future turnover"),

    # NOT expanding(min_periods=1): that reads rows 0..t only, so it is a
    # different universe rule but a causal one, and it correctly survives.
    # Reversing the series before rolling is the genuine lookahead.
    ("forward-looking-turnover", "src/universe.py",
     "(cl * vol).rolling(lookback, min_periods=lookback // 2).median()",
     "(cl * vol)[::-1].rolling(lookback, min_periods=lookback // 2).median()[::-1]",
     "universe ranked on turnover it has not seen yet"),

    ("prev-close-is-next-close", "src/backtest.py",
     "{c: cl[c].dropna().shift(1).reindex(cl.index) for c in cl.columns}",
     "{c: cl[c].dropna().shift(-1).reindex(cl.index) for c in cl.columns}",
     "circuit-band reference price taken from tomorrow"),

    # NOT the re-size-to-available-cash branch, which the fixture rarely takes.
    # ref_px is the real sizing decision, and cl.at[t] is legitimate there --
    # the order is queued at the close of t. Sizing off the next bar's open is
    # the leak: that is the fill price, used before anyone could know it.
    ("size-on-tomorrows-open", "src/backtest.py",
     "ref_px = float(cl.at[t, sym])",
     "ref_px = float(op.iloc[i + 1][sym]) if i + 1 < len(dates) else float(cl.at[t, sym])",
     "position sized from the fill price before it is knowable"),
]


@pytest.fixture(scope="module")
def sandbox():
    """A copy of the repo with this file removed, so mutants cannot recurse."""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(
            ".git", "data", "out", "__pycache__", ".pytest_cache", ".claude",
            "test_mutants.py"))
        yield repo


def _run(repo: Path) -> bool:
    """True if the suite is green."""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header", "-x"],
        cwd=repo, capture_output=True, text=True, timeout=1800)
    return r.returncode == 0


@pytest.mark.slow
def test_baseline_is_green(sandbox):
    """Every mutation result below is meaningless if this is red."""
    assert _run(sandbox), "the unmutated suite must pass before mutating it"


@pytest.mark.slow
@pytest.mark.parametrize("mid,rel,find,repl,what",
                         MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_leak_is_caught(sandbox, mid, rel, find, repl, what):
    path = sandbox / rel
    original = path.read_text(encoding="utf-8")
    try:
        assert find in original, (
            f"anchor for '{mid}' no longer exists in {rel}. The code moved; "
            f"update the mutation. An un-applied mutation looks exactly like a "
            f"mutation the suite killed.")
        path.write_text(original.replace(find, repl, 1), encoding="utf-8")
        assert not _run(sandbox), (
            f"LEAK NOT CAUGHT: {mid} - {what}. The suite stayed green with "
            f"this bug in {rel}, so it would not catch the same bug in real "
            f"code either.")
    finally:
        path.write_text(original, encoding="utf-8")
