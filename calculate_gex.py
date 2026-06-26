"""
Gamma Exposure (GEX) Calculator
================================
Pulls the full option chain from CBOE's free delayed-quotes endpoint
(includes per-contract gamma), then computes:

  - Net GEX (call gamma - put gamma) across the four nearest expirations
  - Per-strike net GEX profile
  - Gamma flip (zero-gamma) level
  - Call wall (largest positive per-strike GEX)
  - Put wall (largest negative per-strike GEX)
  - Peak GEX strike (largest absolute gamma)
  - Dealer regime (positive = pinning, negative = amplifying)
  - A separate 0DTE/nearest-expiry-only view for intraday levels

Dealer assumption: long calls / short puts (the standard naive convention).
  Call GEX per strike = gamma * OI * 100 * spot^2 * 0.01   (positive)
  Put  GEX per strike = gamma * OI * 100 * spot^2 * 0.01 * -1 (negative)

NOTE ON DATA: CBOE open interest is T-1 (yesterday's close). This is a
free, OI-based model and does NOT include real-time intraday flow. Treat
the output as market-structure terrain, not a live signal. Read it
alongside price action, exactly like SpotGamma / UW free tiers.

Output: writes gex_data.json to the repo root.
"""

import json
import sys
import time
from datetime import datetime, timezone

import requests

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

# Your watchlist. Index symbols use the _ prefix on CBOE (e.g. _SPX).
# Keep this lean for index/intraday focus; add equities as needed.
WATCHLIST = [
    "_SPX",   # S&P 500 index - your primary
    "SPY",    # S&P 500 ETF
    "_NDX",   # Nasdaq 100 index
    "QQQ",    # Nasdaq 100 ETF
    "TSLA",   # your secondary directional name
    "NVDA",
    "AAPL",
    "_VIX",
    "IWM",    # Russell 2000 ETF
    "META",
    "AMZN",
    "MSFT",
]

# How many of the nearest expirations to include in the "main" net GEX.
# Industry standard (SpotGamma, Barchart) is 4.
NEAREST_EXPIRIES = 4

CONTRACT_MULTIPLIER = 100
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}


# ----------------------------------------------------------------------------
# PARSING
# ----------------------------------------------------------------------------

def parse_option_symbol(option_sym):
    """
    CBOE option symbols look like: SPXW250620P05000000
    Layout: [ROOT][YY][MM][DD][C/P][STRIKE*1000 padded to 8].
    Returns (expiry_str 'YYYY-MM-DD', right 'C'/'P', strike float) or None.
    """
    s = option_sym
    # Walk from the right: last 8 digits = strike, before that 1 char C/P,
    # before that 6 digits = YYMMDD.
    try:
        strike = int(s[-8:]) / 1000.0
        right = s[-9]
        date_part = s[-15:-9]  # YYMMDD
        yy = int(date_part[0:2])
        mm = int(date_part[2:4])
        dd = int(date_part[4:6])
        expiry = f"20{yy:02d}-{mm:02d}-{dd:02d}"
        if right not in ("C", "P"):
            return None
        return expiry, right, strike
    except (ValueError, IndexError):
        return None


def fetch_chain(symbol):
    """Fetch and lightly validate a CBOE option-chain payload."""
    url = CBOE_URL.format(symbol=symbol)
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return r.json()
            # Some index roots need the underscore; some equities don't.
            if r.status_code == 404 and symbol.startswith("_"):
                alt = CBOE_URL.format(symbol=symbol.lstrip("_"))
                r2 = requests.get(alt, headers=HEADERS, timeout=20)
                if r2.status_code == 200:
                    return r2.json()
        except requests.RequestException as e:
            print(f"  [{symbol}] attempt {attempt+1} failed: {e}")
        time.sleep(2)
    return None


# ----------------------------------------------------------------------------
# GEX COMPUTE
# ----------------------------------------------------------------------------

def compute_gex(payload):
    """
    Given a CBOE payload, return a dict with the full GEX breakdown,
    or None if the data is unusable.
    """
    data = payload.get("data", {})
    spot = data.get("current_price") or data.get("close")
    options = data.get("options", [])
    if not spot or not options:
        return None
    spot = float(spot)

    # Collect per-contract records.
    records = []
    for opt in options:
        meta = parse_option_symbol(opt.get("option", ""))
        if not meta:
            continue
        expiry, right, strike = meta
        gamma = opt.get("gamma")
        oi = opt.get("open_interest")
        vol = opt.get("volume", 0)
        if gamma is None or oi is None:
            continue
        records.append({
            "expiry": expiry,
            "right": right,
            "strike": strike,
            "gamma": float(gamma),
            "oi": int(oi),
            "vol": int(vol or 0),
        })

    if not records:
        return None

    # Identify the nearest expirations.
    today = datetime.now(timezone.utc).date()
    expiries = sorted({r["expiry"] for r in records})
    future_expiries = [e for e in expiries
                       if datetime.strptime(e, "%Y-%m-%d").date() >= today]
    if not future_expiries:
        future_expiries = expiries
    nearest_set = set(future_expiries[:NEAREST_EXPIRIES])
    zero_dte = future_expiries[0] if future_expiries else None

    def strike_gex(rec, weight="oi"):
        # Dollar gamma per 1% move: gamma * weight * mult * spot^2 * 0.01
        w = rec["oi"] if weight == "oi" else rec["vol"]
        base = rec["gamma"] * w * CONTRACT_MULTIPLIER * (spot ** 2) * 0.01
        return base if rec["right"] == "C" else -base

    # --- Main view: four nearest expiries ---
    by_strike = {}
    net_gex = 0.0
    for rec in records:
        if rec["expiry"] not in nearest_set:
            continue
        g = strike_gex(rec)
        net_gex += g
        by_strike.setdefault(rec["strike"], 0.0)
        by_strike[rec["strike"]] += g

    # --- 0DTE / nearest-only view ---
    by_strike_0dte = {}
    net_gex_0dte = 0.0
    for rec in records:
        if rec["expiry"] != zero_dte:
            continue
        g = strike_gex(rec)
        net_gex_0dte += g
        by_strike_0dte.setdefault(rec["strike"], 0.0)
        by_strike_0dte[rec["strike"]] += g

    levels = compute_levels(by_strike, spot)
    levels_0dte = compute_levels(by_strike_0dte, spot)

    # --- Volume-weighted view (LIVE proxy) ---
    # Uses today's traded volume instead of T-1 open interest. Volume updates
    # intraday and captures 0DTE activity that never lands in official OI.
    # Caveat: volume has no opened-vs-closed or bought-vs-sold direction, so
    # this is a crude activity-weighted proxy, NOT a true positioning model.
    # We weight across the nearest expiries (where same-day volume concentrates).
    by_strike_vol = {}
    net_gex_vol = 0.0
    total_vol = 0
    for rec in records:
        if rec["expiry"] not in nearest_set:
            continue
        total_vol += rec["vol"]
        if rec["vol"] == 0:
            continue
        g = strike_gex(rec, weight="vol")
        net_gex_vol += g
        by_strike_vol.setdefault(rec["strike"], 0.0)
        by_strike_vol[rec["strike"]] += g

    levels_vol = compute_levels(by_strike_vol, spot)
    profile_vol = trim_profile(by_strike_vol, spot, width_pct=0.08)
    # Volume can be thin/absent pre-market; flag low-confidence reads.
    vol_confidence = "live" if total_vol > 500 else "thin"

    # Trim the per-strike profile to a window around spot for display
    # (keeps the JSON small and the chart readable).
    profile = trim_profile(by_strike, spot, width_pct=0.08)

    regime = "POSITIVE" if net_gex >= 0 else "NEGATIVE"

    return {
        "spot": round(spot, 2),
        "net_gex": net_gex,
        "net_gex_billions": round(net_gex / 1e9, 3),
        "regime": regime,
        "gamma_flip": levels["flip"],
        "call_wall": levels["call_wall"],
        "put_wall": levels["put_wall"],
        "peak_gex_strike": levels["peak"],
        "zero_dte_expiry": zero_dte,
        "net_gex_0dte_billions": round(net_gex_0dte / 1e9, 3),
        "flip_0dte": levels_0dte["flip"],
        "call_wall_0dte": levels_0dte["call_wall"],
        "put_wall_0dte": levels_0dte["put_wall"],
        "vol_confidence": vol_confidence,
        "total_volume": total_vol,
        "net_gex_vol_billions": round(net_gex_vol / 1e9, 3),
        "regime_vol": "POSITIVE" if net_gex_vol >= 0 else "NEGATIVE",
        "flip_vol": levels_vol["flip"],
        "call_wall_vol": levels_vol["call_wall"],
        "put_wall_vol": levels_vol["put_wall"],
        "profile_vol": profile_vol,
        "expiries_used": sorted(nearest_set),
        "profile": profile,
    }


def compute_levels(by_strike, spot):
    """Find flip, call wall, put wall, peak from a {strike: net_gex} map."""
    if not by_strike:
        return {"flip": None, "call_wall": None, "put_wall": None, "peak": None}

    strikes = sorted(by_strike.keys())

    # Call wall = strike with most positive net GEX.
    # Put wall  = strike with most negative net GEX.
    call_wall = max(strikes, key=lambda k: by_strike[k])
    put_wall = min(strikes, key=lambda k: by_strike[k])
    if by_strike[call_wall] <= 0:
        call_wall = None
    if by_strike[put_wall] >= 0:
        put_wall = None
    peak = max(strikes, key=lambda k: abs(by_strike[k]))

    # Gamma flip: walk cumulative net GEX from low strike to high; the
    # zero crossing (interpolated) is the flip. We approximate by finding
    # the strike nearest spot where cumulative sign changes.
    flip = compute_flip(by_strike, strikes, spot)

    return {
        "call_wall": call_wall,
        "put_wall": put_wall,
        "peak": peak,
        "flip": flip,
    }


def compute_flip(by_strike, strikes, spot):
    """
    Gamma flip = the strike level where the running net GEX profile crosses
    zero. We sum GEX from the lowest strike upward and look for the sign
    change bracketing, then linearly interpolate between the two strikes.
    """
    cumulative = 0.0
    prev_strike = None
    prev_cum = 0.0
    for k in strikes:
        cumulative += by_strike[k]
        if prev_strike is not None and (prev_cum < 0 <= cumulative or prev_cum > 0 >= cumulative):
            # linear interpolation of the zero crossing
            span = cumulative - prev_cum
            if span == 0:
                return round(k, 2)
            frac = -prev_cum / span
            flip = prev_strike + frac * (k - prev_strike)
            return round(flip, 2)
        prev_strike = k
        prev_cum = cumulative
    # No crossing: return the strike closest to spot as a fallback.
    return round(min(strikes, key=lambda k: abs(k - spot)), 2)


def trim_profile(by_strike, spot, width_pct=0.08):
    """Return a sorted list of {strike, gex} within +/- width_pct of spot."""
    lo = spot * (1 - width_pct)
    hi = spot * (1 + width_pct)
    out = []
    for k in sorted(by_strike.keys()):
        if lo <= k <= hi:
            out.append({"strike": round(k, 2), "gex": round(by_strike[k] / 1e9, 4)})
    return out


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    import os
    watchlist = list(WATCHLIST)
    adhoc = os.environ.get("ADHOC_TICKER", "").strip().upper()
    if adhoc and adhoc not in watchlist:
        watchlist.append(adhoc)
        print(f"Adding ad-hoc ticker for this run: {adhoc}")

    results = {}
    errors = []
    for symbol in watchlist:
        display = symbol.lstrip("_")
        print(f"Fetching {display} ...")
        payload = fetch_chain(symbol)
        if not payload:
            print(f"  [{display}] no data")
            errors.append(display)
            continue
        try:
            gex = compute_gex(payload)
        except Exception as e:  # noqa: BLE001
            print(f"  [{display}] compute error: {e}")
            errors.append(display)
            continue
        if not gex:
            print(f"  [{display}] unusable chain")
            errors.append(display)
            continue
        results[display] = gex
        print(f"  [{display}] spot={gex['spot']} regime={gex['regime']} "
              f"flip={gex['gamma_flip']} callwall={gex['call_wall']} "
              f"putwall={gex['put_wall']}")
        time.sleep(1)  # be polite to CBOE

    output = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "model": "naive OI-based, long calls / short puts, 4 nearest expiries",
        "data_note": "CBOE open interest is T-1. Structure, not a live signal.",
        "errors": errors,
        "tickers": results,
    }

    with open("gex_data.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nWrote gex_data.json with {len(results)} tickers. "
          f"Errors: {errors or 'none'}")


if __name__ == "__main__":
    main()
