"""Pull earnings dates + EPS estimate / reported / surprise % from yfinance for the daily-cache stock universe.
Saves D:/Kai/data/earnings_yf/<SYM>.csv (columns: when [NY time], eps_est, eps_rep, surprise_pct).
Built 10/11/2026 for the PEAD test (rules/pead_earnings.py). Sequential, ~2s per symbol.
Usage: python pull_earnings_yf.py [--force]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yfinance as yf  # noqa: E402
from strategy_lab import data as D  # noqa: E402

OUT = "D:/Kai/data/earnings_yf"


def main():
    force = "--force" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    syms = sorted(s for s in D.load("stocks", "1d") if s not in D.ETFS)
    t = time.time()
    for i, s in enumerate(syms):
        fn = os.path.join(OUT, f"{s}.csv")
        if os.path.exists(fn) and not force:
            continue
        for k in range(3):
            try:
                d = yf.Ticker(s.replace(".", "-")).get_earnings_dates(limit=60)
                break
            except Exception as e:  # noqa: BLE001
                d = None
                print(s, "retry", e, flush=True)
                time.sleep(10 * (k + 1))
        if d is None or d.empty:
            open(fn, "w").write("when,eps_est,eps_rep,surprise_pct\n")
            continue
        d = d.reset_index()
        d.columns = ["when", "eps_est", "eps_rep", "surprise_pct"]
        d["when"] = d["when"].dt.tz_convert("America/New_York").dt.tz_localize(None)
        d.drop_duplicates("when").sort_values("when").to_csv(fn, index=False)
        if i % 25 == 0:
            print(i, s, len(d), round(time.time() - t), flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
