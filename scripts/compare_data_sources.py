"""
Compare Alpaca vs Polygon historical daily bars for SPY.
Assesses: OHLCV alignment, VWAP, trade_count, missing days, and drift.
"""
import os, sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np
from dotenv import load_dotenv

# Load .env from project root
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

# ── Alpaca ───────────────────────────────────────────────────────────
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# ── Polygon ──────────────────────────────────────────────────────────
from polygon import RESTClient as PolygonClient

SYMBOL = "SPY"
START = date(2025, 1, 2)
END   = date(2025, 12, 31)

def fetch_alpaca(symbol, start, end):
    client = StockHistoricalDataClient(
        os.getenv("ALPACA_API_KEY"),
        os.getenv("ALPACA_SECRET_KEY"),
    )
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc),
        end=datetime.combine(end, datetime.min.time()).replace(tzinfo=timezone.utc),
    )
    df = client.get_stock_bars(req).df.reset_index()
    if "symbol" in df.columns:
        df = df.drop(columns=["symbol"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["date"] = df["timestamp"].dt.date
    df = df.sort_values("date").reset_index(drop=True)
    return df

def fetch_polygon(symbol, start, end):
    client = PolygonClient(os.getenv("POLYGON_API_KEY"))
    aggs = list(client.list_aggs(
        ticker=symbol,
        multiplier=1,
        timespan="day",
        from_=str(start),
        to=str(end),
        limit=50000,
    ))
    rows = []
    for a in aggs:
        rows.append({
            "timestamp": pd.Timestamp(a.timestamp, unit="ms", tz="UTC"),
            "open": a.open,
            "high": a.high,
            "low": a.low,
            "close": a.close,
            "volume": a.volume,
            "vwap": a.vwap,
            "trade_count": getattr(a, "transactions", None),
        })
    df = pd.DataFrame(rows)
    df["date"] = df["timestamp"].dt.date
    df = df.sort_values("date").reset_index(drop=True)
    return df

# ── Fetch ────────────────────────────────────────────────────────────
print(f"Fetching {SYMBOL} daily bars  {START} -> {END}\n")

print("  Alpaca ...", end=" ", flush=True)
alp = fetch_alpaca(SYMBOL, START, END)
print(f"{len(alp)} bars")

print("  Polygon ...", end=" ", flush=True)
poly = fetch_polygon(SYMBOL, START, END)
print(f"{len(poly)} bars")

# ── Calendar alignment ───────────────────────────────────────────────
alp_dates  = set(alp["date"])
poly_dates = set(poly["date"])
only_alp   = sorted(alp_dates - poly_dates)
only_poly  = sorted(poly_dates - alp_dates)
common     = sorted(alp_dates & poly_dates)

print(f"\n{'='*60}")
print(f"CALENDAR ALIGNMENT")
print(f"{'='*60}")
print(f"  Common trading days : {len(common)}")
print(f"  Only in Alpaca      : {len(only_alp)}  {only_alp[:5]}")
print(f"  Only in Polygon     : {len(only_poly)}  {only_poly[:5]}")

# ── Merge on common dates ────────────────────────────────────────────
ma = alp[alp["date"].isin(common)].set_index("date").sort_index()
mp = poly[poly["date"].isin(common)].set_index("date").sort_index()

fields = ["open", "high", "low", "close", "volume", "vwap"]
print(f"\n{'='*60}")
print(f"FIELD-BY-FIELD COMPARISON  ({len(common)} common days)")
print(f"{'='*60}")
print(f"{'Field':<10} {'Match%':>8} {'MaxAbsDiff':>12} {'MeanAbsDiff':>13} {'MaxPctDiff':>12}")
print(f"{'-'*58}")

for f in fields:
    a_vals = ma[f].astype(float).values
    p_vals = mp[f].astype(float).values
    diff   = np.abs(a_vals - p_vals)
    pct    = np.where(p_vals != 0, diff / np.abs(p_vals) * 100, 0)
    exact  = np.sum(np.isclose(a_vals, p_vals, atol=1e-4)) / len(a_vals) * 100
    print(f"{f:<10} {exact:>7.1f}% {diff.max():>12.4f} {diff.mean():>13.6f} {pct.max():>11.4f}%")

# ── Trade count comparison ───────────────────────────────────────────
if "trade_count" in ma.columns and "trade_count" in mp.columns:
    tc_a = ma["trade_count"].dropna().astype(float)
    tc_p = mp["trade_count"].dropna().astype(float)
    common_tc = tc_a.index.intersection(tc_p.index)
    if len(common_tc) > 0:
        tc_diff = np.abs(tc_a.loc[common_tc].values - tc_p.loc[common_tc].values)
        tc_pct  = tc_diff / np.abs(tc_p.loc[common_tc].values) * 100
        print(f"\n{'trade_count':<10} — MaxPctDiff: {tc_pct.max():.2f}%, MeanPctDiff: {tc_pct.mean():.2f}%")

# ── Close price correlation ──────────────────────────────────────────
corr = np.corrcoef(ma["close"].astype(float), mp["close"].astype(float))[0, 1]
print(f"\n{'='*60}")
print(f"CLOSE PRICE CORRELATION:  {corr:.10f}")

# ── Return series comparison ─────────────────────────────────────────
alp_ret  = ma["close"].astype(float).pct_change().dropna()
poly_ret = mp["close"].astype(float).pct_change().dropna()
ret_diff = (alp_ret - poly_ret).abs()

print(f"\n{'='*60}")
print(f"DAILY RETURN DIVERGENCE")
print(f"{'='*60}")
print(f"  Max absolute return diff : {ret_diff.max():.8f}  ({ret_diff.max()*100:.6f}%)")
print(f"  Mean absolute return diff: {ret_diff.mean():.8f}  ({ret_diff.mean()*100:.6f}%)")
print(f"  Return correlation       : {np.corrcoef(alp_ret, poly_ret)[0,1]:.10f}")

# ── Worst divergence days ────────────────────────────────────────────
worst = ret_diff.nlargest(5)
print(f"\n  Top 5 worst divergence days:")
for dt, val in worst.items():
    a_close = ma.loc[dt, "close"]
    p_close = mp.loc[dt, "close"]
    print(f"    {dt}  ret_diff={val:.8f}  alpaca_close={a_close}  polygon_close={p_close}")

# ── VWAP divergence ──────────────────────────────────────────────────
vwap_a = ma["vwap"].astype(float)
vwap_p = mp["vwap"].astype(float)
vwap_diff_bps = (vwap_a - vwap_p).abs() / vwap_p * 10000

print(f"\n{'='*60}")
print(f"VWAP DIVERGENCE (basis points)")
print(f"{'='*60}")
print(f"  Max  : {vwap_diff_bps.max():.2f} bps")
print(f"  Mean : {vwap_diff_bps.mean():.2f} bps")
print(f"  >1bp : {(vwap_diff_bps > 1).sum()} days")

# ── Summary verdict ──────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"VERDICT")
print(f"{'='*60}")
close_match = np.allclose(ma["close"].astype(float), mp["close"].astype(float), atol=0.02)
vol_match   = np.allclose(ma["volume"].astype(float), mp["volume"].astype(float), rtol=0.01)
vwap_ok     = vwap_diff_bps.mean() < 1.0

checks = {
    "Close prices within $0.02"    : close_match,
    "Volume within 1%"             : vol_match,
    "VWAP mean diff < 1 bps"       : vwap_ok,
    "Calendar fully aligned"       : len(only_alp) == 0 and len(only_poly) == 0,
    "Return correlation > 0.9999"  : np.corrcoef(alp_ret, poly_ret)[0,1] > 0.9999,
}
for check, passed in checks.items():
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {check}")

all_pass = all(checks.values())
print(f"\n  Overall: {'DATA SOURCES ARE EQUIVALENT' if all_pass else 'DIFFERENCES DETECTED — review above'}")
