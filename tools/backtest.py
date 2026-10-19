"""
Replay the signal rules over recent real bars from the command line.

    python tools/backtest.py                      # last 60 days of 15-minute bars (yfinance GC=F)
    python tools/backtest.py --min-conf 55        # try a higher confidence bar
    python tools/backtest.py --json out.json      # keep the full report

Technical-only (no news history); see signals/backtest.py for the caveats.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402
from signals.backtest import fetch_history, params_from_settings, replay  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="XAU Sentinel – historical replay of the signal rules")
    ap.add_argument("--symbol", default=settings.yf_symbol)
    ap.add_argument("--days", default="60d", help="15-minute history to replay (yfinance period, max 60d)")
    ap.add_argument("--min-conf", type=int, default=None)
    ap.add_argument("--stop-mult", type=float, default=None)
    ap.add_argument("--target-mult", type=float, default=None)
    ap.add_argument("--no-session-filter", action="store_true")
    ap.add_argument("--json", default=None, help="write the full report here")
    ap.add_argument("--deep", type=float, default=None, metavar="YEARS", help="use Dukascopy spot history for this many years instead of Yahoo's 60 days")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

    if args.deep:
        from market.history import load_history

        h1, d1, m15 = load_history(years=args.deep, cache_path=Path(settings.db_path).parent / "history_m15.pkl.gz")
        sym = "XAU/USD spot (Dukascopy)"
    else:
        h1, d1, m15, sym = fetch_history(args.symbol, m15_period=args.days)
    p = params_from_settings(settings)
    if args.min_conf is not None:
        p.min_conf = args.min_conf
    if args.stop_mult is not None:
        p.atr_stop_mult = args.stop_mult
    if args.target_mult is not None:
        p.atr_target_mult = args.target_mult
    if args.no_session_filter:
        p.session_filter = False
    print(f"Replaying {len(m15)} 15-minute bars of {sym} with {p.to_dict()}")
    res = replay(h1, d1, m15, p, progress=lambda n, t: print(f"  {n}/{t}", end="\r"))
    s = res["summary"]
    print(f"\n{res['period']['from']} → {res['period']['to']}  ({res['period']['days']} days, {res['took_sec']} s)")
    print(f"setups {s['setups']}  traded {s['count']}  no-trade {s['no_trade']} {s['no_trade_breakdown']}")
    print(f"win rate {s['win_rate']}%  ({s['wins']} W / {s['losses']} L / {s['breakeven']} BE)  avg {s['avg_r']}R  total {s['total_r']}R  "
          f"PF {s['profit_factor']}  maxDD {s['max_drawdown_r']}R  TP1 {s['tp1_rate']}%  hold {s['avg_hold_h']} h")
    for key in ("by_year", "by_quarter", "by_confidence", "by_session", "by_trigger", "by_mode", "by_side", "by_confluence", "thresholds"):
        if key not in res:
            continue
        print(f"\n{key}:")
        for k, v in res[key].items():
            print(f"  {k:<34} n={v['count']:<3} win={v['win_rate']}%  avg={v['avg_r']}R  total={v['total_r']}R  PF={v['profit_factor']}")
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1))
        print(f"\nfull report → {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
