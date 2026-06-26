# GEX Tracker

Automated dealer gamma-exposure (GEX) tracker for your watchlist. Pulls free
option-chain data from CBOE, computes per-strike GEX, the gamma flip, and the
call/put walls, then renders a terminal-style dashboard on GitHub Pages.

Same architecture as your em-tracker: Python compute -> GitHub Actions ->
JSON -> static dashboard. $0 to run.

Live dashboard (after setup): `https://marquisbware-sys.github.io/gex-tracker/dashboard.html`

---

## What you get

- **Net GEX** across the four nearest expirations (industry standard)
- **Gamma flip** (zero-gamma level) — the regime boundary
- **Call wall** / **put wall** — your resistance / support magnets
- **Peak GEX strike** — the strongest pin
- **Regime** — POSITIVE (pinning, fade) vs NEGATIVE (amplifying, momentum)
- **0DTE-only view** — the intraday-relevant levels, broken out separately
- Per-strike profile bars so you can eyeball where gamma concentrates

---

## The two views (this is the key feature)

The dashboard has a **view toggle** in the top bar:

**OI (T-1 structure)** — built on official open interest. This is yesterday's
positioning: stable, reliable, the structural map. Use it pre-market to mark
levels. The levels rarely move overnight.

**Volume (live proxy)** — built on *today's* traded volume instead of OI.
Volume updates intraday and captures 0DTE activity that never lands in official
OI. This is the closest you get to "live" without a paid feed.

The honest caveat on the volume view: volume measures *activity*, not
*positioning*. It can't tell opened-from-closed or bought-from-sold, so it's a
crude proxy, not a true model like SpotGamma TRACE. A `LIVE` / `THIN` badge
tells you whether there's enough volume to trust the read (pre-market it'll
read THIN — lean on the OI view then).

**The divergence badge is where the value is.** Each card compares the OI flip
to the volume flip:
- `✓ live flow confirms structure` — today's flow agrees with yesterday's
  positioning. High confidence in the levels.
- `⚠ flip divergence X%` — today's volume is shifting the levels. Something is
  building intraday that the structural map doesn't show yet.
- `⚠ regime split` — the live flow and the structure disagree on whether you're
  in a pinning or amplifying regime. Trade smaller / wait for confirmation.

Workflow: mark levels from the **OI view** pre-market, then watch the **Volume
view** during the session. When they agree, trust the levels. When they
diverge, the live flow is your tell that something is moving.

---

## Setup (one-time, ~10 min)

### 1. Create the repo
On github.com create a **public** repo named `gex-tracker`, then:
```bash
git clone https://github.com/marquisbware-sys/gex-tracker.git
cd gex-tracker
```
Copy in: `calculate_gex.py`, `dashboard.html`, `.github/workflows/update-gex.yml`,
and this `README.md`.

### 2. Set the watchlist
Edit the `WATCHLIST` array near the top of `calculate_gex.py`. Index symbols
use a leading underscore on CBOE: `_SPX`, `_NDX`, `_VIX`, `_RUT`.

### 3. Allow Actions to push
Repo **Settings -> Actions -> General -> Workflow permissions** ->
select **Read and write permissions** -> Save.

### 4. Enable Pages
Repo **Settings -> Pages -> Build from branch -> main / root**.

### 5. Push
```bash
git add .
git commit -m "Initial GEX tracker"
git push origin main
```

### 6. First run
Go to the **Actions** tab -> `update-gex` -> **Run workflow**. After it finishes
it commits `gex_data.json`. Open your Pages URL and the dashboard fills in.

---

## Schedule

Runs automatically (UTC; CT is UTC-5 in summer):
- **13:00 UTC / 8:00 AM CT** — pre-market, ready before the open
- **17:30 UTC / 12:30 PM CT** — mid-day
- **21:30 UTC / 4:30 PM CT** — post-close

You can also trigger it by hand and pass one ad-hoc ticker in the run dialog
(e.g. `AMD` or `_RUT`) to add it just for that run.

---

## How to read it (your pre-market routine)

1. **Find the flip first.** Where is spot relative to the gamma flip?
   - Spot **above** flip = positive gamma = **pinning / mean-reversion**. Your
     VWAP-fade setups have an edge. Fade pushes into the call wall.
   - Spot **below** flip = negative gamma = **amplification / trend**. Your EMA
     breakout setups have an edge. Don't fade; ride momentum.
2. **Mark the walls as levels.** Call wall = upside resistance magnet. Put wall
   = downside support; a *hold* there is a high-probability long, a *break*
   often accelerates lower.
3. **Use the 0DTE row for intraday.** For SPX specifically, 0DTE drives the
   tape. The 0DTE flip and walls are your real intraday levels; the 4-expiry
   view is the broader regime.
4. **Confluence, not signal.** GEX is terrain, not a trigger. Take the trade
   only when a GEX level lines up with your own VWAP/EMA/order-block read.

---

## Important caveats (don't skip)

- **OI is T-1.** CBOE open interest reflects yesterday's close. This free model
  cannot see today's real-time flow the way SpotGamma TRACE or Unusual Whales
  Periscope can. It's a structure map, not a live tape.
- **The dealer assumption is naive.** This uses the standard "long calls / short
  puts" convention. Real dealer positioning isn't directly observable, which is
  why paid vendors' numbers differ. Cross-check levels against a second source
  (Barchart, FlashAlpha free tier) when something looks off.
- **Near OPEX the flip hardens.** In the last ~48h before monthly expiration the
  pinning/amplifying effect around the flip gets much stronger.

---

## Files

| File | Purpose |
|---|---|
| `calculate_gex.py` | Fetches CBOE chains, computes GEX + levels, writes `gex_data.json` |
| `dashboard.html` | GitHub Pages dashboard |
| `.github/workflows/update-gex.yml` | Scheduled + manual runner |
| `gex_data.json` | Generated output (committed by the workflow) |
