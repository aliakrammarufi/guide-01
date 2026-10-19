"""Shared fixtures: synthetic OHLC data so tests run fully offline."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Make the project root importable when running `pytest` from anywhere
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market.data import MarketData  # noqa: E402
from market.synthetic import make_market as _make_market  # noqa: E402

FIXED_END = datetime(2026, 10, 17, 21, tzinfo=timezone.utc)


def make_market(drift_h: float = 0.01, vol: float = 0.15, seed: int = 7, bars: int = 6000) -> MarketData:
    return _make_market(drift_h=drift_h, vol=vol, seed=seed, bars=bars, end=FIXED_END)


@pytest.fixture
def bull_market() -> MarketData:
    return make_market(drift_h=0.012, seed=3)


@pytest.fixture
def bear_market() -> MarketData:
    return make_market(drift_h=-0.012, seed=5)


@pytest.fixture
def flat_market() -> MarketData:
    return make_market(drift_h=0.0, seed=11)
