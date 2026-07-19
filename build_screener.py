"""
Russell 3000 Stock Screener Builder
Fetches sector-tagged Russell 3000 constituents (iShares IWV holdings, falling
back to a bundled snapshot) plus price/momentum data from Yahoo Finance, and
writes screener_data.json for the dashboard's Stock Screener tab to fetch
client-side.

Runs on its own schedule (see .github/workflows/screener-update.yml),
separate from the main daily build, since fetching ~2,600 tickers is slower
and more exposed to Yahoo/iShares rate limits than the main dashboard build.
"""

import os
import csv
import json
import datetime
import time
import requests
import pandas as pd
import yfinance as yf

IWV_API_URL = (
    "https://www.ishares.com/varnish-api/blk-one01-product-data/product-data/api/v2/get-product-data"
    "?appSubType=ISHARES&appType=PRODUCT_PAGE&component=holdings.all&locale=en_US"
    "&portfolioId=239714&targetSite=us-ishares&userType=individual&excludeContent=true&includeConfig=true"
)
IWV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.ishares.com/us/products/239714/ishares-russell-3000-etf",
}
FALLBACK_CSV = os.path.join(os.path.dirname(__file__), "russell3000_holdings.csv")

# iShares reports dual-class tickers without the separator Yahoo expects
# (e.g. "BRKB" vs "BRK-B"). Only the highest-weight names are worth mapping;
# any other mismatches just quietly drop out of the momentum fetch.
YAHOO_TICKER_FIXUPS = {
    "BRKB": "BRK-B",
    "BRKA": "BRK-A",
}

# GICS sector name -> dashboard's existing sector labels (matches SECTOR_ETFS in build.py)
SECTOR_NORMALIZE = {
    "Information Technology": "Technology",
    "Health Care": "Healthcare",
    "Communication": "Comm. Services",
    "Consumer Discretionary": "Cons. Discretionary",
    "Consumer Staples": "Cons. Staples",
    "Financials": "Financials",
    "Energy": "Energy",
    "Materials": "Materials",
    "Industrials": "Industrials",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
}


def fetch_russell3000_constituents():
    """Ticker/name/sector/weight for the Russell 3000, from iShares' live IWV
    holdings feed. Falls back to the bundled CSV snapshot if that feed is
    unavailable (bot-protected APIs can start blocking without notice)."""
    try:
        resp = requests.get(IWV_API_URL, headers=IWV_HEADERS, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        dp = payload["componentsByNameMap"]["holdings"]["containersByNameMap"]["all"]["dataPointsByNameMap"]
        tickers = dp["ticker"]["formattedValue"]
        names = dp["issueName"]["formattedValue"]
        sectors = dp["sectorName"]["formattedValue"]
        weights = dp["holdingPercent"]["formattedValue"]
        asset_classes = dp["assetClass"]["formattedValue"]

        rows = []
        seen = set()
        for i in range(len(tickers)):
            t = (tickers[i] or "").strip()
            if not t or t in seen or asset_classes[i] != "Equity":
                continue
            seen.add(t)
            try:
                weight = float(weights[i])
            except (TypeError, ValueError):
                weight = 0.0
            rows.append({
                "ticker": t,
                "name": names[i].strip() if names[i] else t,
                "sector": SECTOR_NORMALIZE.get(sectors[i], sectors[i] or "Other"),
                "weight": weight,
            })
        if len(rows) < 1000:
            raise ValueError(f"Only got {len(rows)} holdings, expected ~2,500+ — falling back")
        print(f"  Fetched {len(rows)} live Russell 3000 constituents from iShares")
        return rows
    except Exception as e:
        print(f"  WARN: Live IWV holdings fetch failed ({e}), using bundled snapshot")
        rows = []
        with open(FALLBACK_CSV, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    weight = float(row["weight"])
                except (TypeError, ValueError):
                    weight = 0.0
                sector = row["sector"]
                rows.append({
                    "ticker": row["ticker"].strip(),
                    "name": row["name"].strip(),
                    "sector": SECTOR_NORMALIZE.get(sector, sector or "Other"),
                    "weight": weight,
                })
        print(f"  Loaded {len(rows)} constituents from bundled snapshot")
        return rows


def _chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def fetch_price_momentum(tickers, chunk_size=150):
    """Batched price history via yf.download (one request per chunk, not per
    ticker) — this is what keeps a 2,600-name screen feasible. Returns
    {ticker: {price, ytd_pct, vs_50dma_pct}}; tickers that fail silently drop
    out rather than blocking the rest of the batch."""
    result = {}
    year_start = pd.Timestamp(datetime.date.today().year, 1, 1)
    yahoo_tickers = [YAHOO_TICKER_FIXUPS.get(t, t) for t in tickers]
    yahoo_to_orig = dict(zip(yahoo_tickers, tickers))

    for i, chunk in enumerate(_chunked(yahoo_tickers, chunk_size)):
        try:
            data = yf.download(
                tickers=" ".join(chunk), period="1y", interval="1d",
                group_by="ticker", threads=True, progress=False, auto_adjust=True,
            )
        except Exception as e:
            print(f"  WARN: chunk {i} download failed: {e}")
            continue

        for yt in chunk:
            try:
                closes = data[yt]["Close"].dropna() if len(chunk) > 1 else data["Close"].dropna()
                if len(closes) < 2:
                    continue
                latest = float(closes.iloc[-1])
                ytd_slice = closes[closes.index >= year_start]
                ytd_start = float(ytd_slice.iloc[0]) if len(ytd_slice) > 0 else float(closes.iloc[0])
                ma50 = float(closes.tail(50).mean())
                result[yahoo_to_orig[yt]] = {
                    "price": round(latest, 2),
                    "ytd_pct": round((latest - ytd_start) / ytd_start * 100, 1) if ytd_start else None,
                    "vs_50dma_pct": round((latest - ma50) / ma50 * 100, 1) if ma50 else None,
                }
            except Exception:
                continue

        print(f"  Chunk {i+1}: {len(chunk)} tickers requested, {len(result)} total resolved so far")
        time.sleep(1)  # brief pause between chunks, polite to Yahoo's endpoint

    return result


def build_screener():
    print(f"[{datetime.datetime.now()}] Building stock screener...")

    constituents = fetch_russell3000_constituents()
    tickers = [c["ticker"] for c in constituents]

    print(f"  Fetching price/momentum for {len(tickers)} tickers...")
    momentum = fetch_price_momentum(tickers)

    stocks = []
    for c in constituents:
        m = momentum.get(c["ticker"])
        if not m:
            continue
        stocks.append({**c, **m})

    stocks.sort(key=lambda s: (s["sector"], -s["weight"]))

    out = {
        "generated_at": datetime.datetime.now().isoformat(),
        "count": len(stocks),
        "stocks": stocks,
    }
    out_path = os.path.join(os.path.dirname(__file__), "screener_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))

    print(f"  Wrote {len(stocks)} stocks to {out_path}")
    print(f"[{datetime.datetime.now()}] Done.")


if __name__ == "__main__":
    build_screener()
