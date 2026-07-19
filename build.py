"""
Accuvest Daily Dashboard Builder
Fetches live data from FRED + Yahoo Finance, renders the HTML dashboard.
Run manually or via GitHub Actions on a daily schedule.
"""

import os
import json
import datetime
import calendar
import csv
import io
import requests
import signal
import threading
from fredapi import Fred
import yfinance as yf
from jinja2 import Template

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# ---------------------------------------------------------------------------
# 1. FRED DATA
# ---------------------------------------------------------------------------

def fetch_fred_data():
    """Fetch macro indicators from FRED."""
    fred = Fred(api_key=FRED_API_KEY)
    today = datetime.date.today()
    start_10y = today - datetime.timedelta(days=365 * 11)
    start_5y = today - datetime.timedelta(days=365 * 6)
    start_2y = today - datetime.timedelta(days=365 * 3)
    start_30y = today - datetime.timedelta(days=365 * 31)

    data = {}

    series_map = {
        # Treasury yields (latest)
        "UST_1M": "DGS1MO", "UST_3M": "DGS3MO", "UST_6M": "DGS6MO",
        "UST_1Y": "DGS1", "UST_2Y": "DGS2", "UST_3Y": "DGS3",
        "UST_5Y": "DGS5", "UST_7Y": "DGS7", "UST_10Y": "DGS10",
        "UST_20Y": "DGS20", "UST_30Y": "DGS30",
        # Macro indicators
        "UNRATE": "UNRATE",
        "CPI_YOY": "CPIAUCSL",
        "CORE_CPI_YOY": "CPILFESL",
        "CORE_PCE_YOY": "PCEPILFE",
        "PAYEMS": "PAYEMS",
        "PERSONAL_SAVING": "PSAVERT",
        "UMCSENT": "UMCSENT",
        "GDP": "A191RL1Q225SBEA",
        # Credit spreads
        "HY_OAS": "BAMLH0A0HYM2",
        "IG_OAS": "BAMLC0A0CM",
        "BBB_OAS": "BAMLC0A4CBBB",
        # 2s10s spread
        "T10Y2Y": "T10Y2Y",
        # Fed funds
        "FEDFUNDS": "FEDFUNDS",
        # TIPS breakeven inflation
        "T5YIE": "T5YIE",
        "T10YIE": "T10YIE",
        # Additional macro
        "ICSA": "ICSA",
        "JTSJOL": "JTSJOL",
        "PPIACO": "PPIACO",
        # Michigan survey 1yr inflation expectations
        "INFL_EXP_1Y": "MICH",
    }

    for key, series_id in series_map.items():
        try:
            s = fred.get_series(series_id, observation_start=start_10y)
            s = s.dropna()
            if len(s) > 0:
                data[key] = {
                    "latest": round(float(s.iloc[-1]), 2),
                    "prior": round(float(s.iloc[-2]), 2) if len(s) > 1 else None,
                    "date": s.index[-1].strftime("%Y-%m-%d"),
                }
            else:
                data[key] = {"latest": None, "prior": None, "date": None}
        except Exception as e:
            print(f"  WARN: Failed to fetch {key} ({series_id}): {e}")
            data[key] = {"latest": None, "prior": None, "date": None}

    # ----- Yield curve history for charts (10Y treasury, 30 years) -----
    try:
        hist_10y = fred.get_series("DGS10", observation_start=start_30y)
        hist_10y = hist_10y.dropna()
        # Resample to monthly to keep data manageable
        monthly = hist_10y.resample("MS").last().dropna()
        data["YIELD_HISTORY"] = [
            {"date": d.strftime("%Y-%m"), "val": round(float(v), 2)}
            for d, v in monthly.items()
        ]
    except Exception as e:
        print(f"  WARN: Failed to fetch yield history: {e}")
        data["YIELD_HISTORY"] = []

    # ----- S&P 500 Valuation History (Shiller CAPE, Earnings Yield) -----
    val_hist_series = [
        ("CAPE_HIST", "MULTPL/SP500_PE_RATIO_MONTH"),  # Try Shiller PE first
        ("SP500_EY_HIST", "MULTPL/SP500_EARNINGS_YIELD_MONTH"),
    ]
    # CAPE from FRED (Quandl-sourced, may not be available — fallback below)
    try:
        cape = fred.get_series("CAPE", observation_start=start_30y)
        cape = cape.dropna().resample("MS").last().dropna()
        data["CAPE_HIST"] = [
            {"date": d.strftime("%Y-%m"), "val": round(float(v), 1)}
            for d, v in cape.items()
        ]
        print(f"    CAPE: {len(data['CAPE_HIST'])} monthly points")
    except Exception as e:
        print(f"  WARN: CAPE not available from FRED: {e}")
        data["CAPE_HIST"] = []

    # S&P 500 PE from price/earnings — calculate from SPY history
    try:
        spy = yf.Ticker("SPY")
        spy_hist = spy.history(period="max", interval="1mo")
        spy_info = spy.info
        trailing_eps = spy_info.get("trailingEps")
        if trailing_eps and trailing_eps > 0 and len(spy_hist) > 0:
            current_price = spy_hist["Close"].iloc[-1]
            current_pe = current_price / trailing_eps
            # Build approximate PE history using price history and assuming earnings grew linearly
            # This is a rough approximation but gives directional accuracy
            sp_prices = spy_hist["Close"].dropna()
            monthly = sp_prices.resample("MS").last().dropna()
            # Use price-to-current-earnings as proxy (not perfect but auto-updating)
            data["SP500_PE_HIST"] = [
                {"date": d.strftime("%Y-%m"), "val": round(float(p / trailing_eps), 1)}
                for d, p in monthly.items()
            ][-360:]  # Last 30 years
            print(f"    SP500 PE proxy: {len(data['SP500_PE_HIST'])} monthly points, current={current_pe:.1f}")
        else:
            data["SP500_PE_HIST"] = []
    except Exception as e:
        print(f"  WARN: Failed to build SP500 PE history: {e}")
        data["SP500_PE_HIST"] = []

    # Corporate profits history from FRED
    try:
        cp = fred.get_series("CP", observation_start=start_30y)
        cp = cp.dropna()
        cp_yoy = cp.pct_change(periods=4) * 100  # YoY quarterly
        cp_yoy = cp_yoy.dropna()
        data["CORP_PROFITS_HIST"] = [
            {"date": d.strftime("%Y-Q") + str((d.month - 1) // 3 + 1), "val": round(float(v), 1)}
            for d, v in cp_yoy.items()
        ]
        print(f"    Corp Profits YoY: {len(data['CORP_PROFITS_HIST'])} quarterly points")
    except Exception as e:
        print(f"  WARN: Failed to fetch corporate profits: {e}")
        data["CORP_PROFITS_HIST"] = []

    # ----- Credit spread histories (30 years) -----
    for label, sid in [("HY_HIST", "BAMLH0A0HYM2"), ("IG_HIST", "BAMLC0A0CM"), ("BBB_HIST", "BAMLC0A4CBBB"), ("T10Y2Y_HIST", "T10Y2Y")]:
        try:
            s = fred.get_series(sid, observation_start=start_30y)
            s = s.dropna().resample("MS").last().dropna()
            data[label] = [
                {"date": d.strftime("%Y-%m"), "val": round(float(v), 2)}
                for d, v in s.items()
            ]
        except Exception as e:
            print(f"  WARN: Failed to fetch {label}: {e}")
            data[label] = []

    # ----- CPI / Core PCE / Unemployment histories (30 years) -----
    for label, sid in [("CPI_HIST", "CPIAUCSL"), ("PCE_HIST", "PCEPILFE"), ("UNEMP_HIST", "UNRATE"), ("SAVINGS_HIST", "PSAVERT"), ("UMCSENT_HIST", "UMCSENT")]:
        try:
            s = fred.get_series(sid, observation_start=start_30y)
            s = s.dropna()
            if label in ("CPI_HIST", "PCE_HIST"):
                # Convert index level to YoY %
                s_yoy = s.pct_change(periods=12) * 100
                s_yoy = s_yoy.dropna()
                data[label] = [
                    {"date": d.strftime("%Y-%m"), "val": round(float(v), 1)}
                    for d, v in s_yoy.items()
                ]
            else:
                data[label] = [
                    {"date": d.strftime("%Y-%m"), "val": round(float(v), 1)}
                    for d, v in s.items()
                ]
        except Exception as e:
            print(f"  WARN: Failed to fetch {label}: {e}")
            data[label] = []

    # ----- Quarterly GDP history -----
    try:
        gdp = fred.get_series("A191RL1Q225SBEA", observation_start=start_30y)
        gdp = gdp.dropna()
        data["GDP_HIST"] = [
            {"date": d.strftime("%Y-Q") + str((d.month - 1) // 3 + 1), "val": round(float(v), 1)}
            for d, v in gdp.items()
        ]
    except Exception as e:
        print(f"  WARN: Failed to fetch GDP history: {e}")
        data["GDP_HIST"] = []

    # ----- Additional indicator histories (for indicator modal charts) -----
    indicator_hist_series = [
        ("PAYEMS_HIST", "PAYEMS"),           # Nonfarm payrolls (level)
        ("HOUST_HIST", "HOUST"),             # Housing starts
        ("PERMIT_HIST", "PERMIT"),           # Building permits
        ("BOPGSTB_HIST", "BOPGSTB"),         # Trade balance
        ("INDPRO_HIST", "INDPRO"),           # Industrial production index
        ("DGORDER_HIST", "DGORDER"),         # Durable goods orders
        ("RSAFS_HIST", "RSAFS"),             # Retail sales
        ("TOTALSL_HIST", "TOTALSL"),         # Consumer credit outstanding
        ("WAGE_HIST", "CES0500000003"),      # Average hourly earnings
        ("IMPORT_PRICE_HIST", "IR"),         # Import price index
        ("T5YIE_HIST", "T5YIE"),             # 5Y breakeven inflation
        ("T10YIE_HIST", "T10YIE"),           # 10Y breakeven inflation
        ("ICSA_HIST", "ICSA"),               # Initial jobless claims
        ("JTSJOL_HIST", "JTSJOL"),           # Job openings (JOLTS)
        ("PPIACO_HIST", "PPIACO"),           # PPI All Commodities
    ]
    for label, sid in indicator_hist_series:
        try:
            s = fred.get_series(sid, observation_start=start_30y)
            s = s.dropna()
            if label == "WAGE_HIST":
                s_yoy = s.pct_change(periods=12) * 100
                s_yoy = s_yoy.dropna()
                data[label] = [
                    {"date": d.strftime("%Y-%m"), "val": round(float(v), 1)}
                    for d, v in s_yoy.items()
                ]
            elif label == "IMPORT_PRICE_HIST":
                s_mom = s.pct_change() * 100
                s_mom = s_mom.dropna()
                data[label] = [
                    {"date": d.strftime("%Y-%m"), "val": round(float(v), 2)}
                    for d, v in s_mom.items()
                ]
            elif label == "RSAFS_HIST":
                s_mom = s.pct_change() * 100
                s_mom = s_mom.dropna()
                data[label] = [
                    {"date": d.strftime("%Y-%m"), "val": round(float(v), 2)}
                    for d, v in s_mom.items()
                ]
            elif label == "BOPGSTB_HIST":
                data[label] = [
                    {"date": d.strftime("%Y-%m"), "val": round(float(v) / 1000, 1)}
                    for d, v in s.items()
                ]
            elif label == "TOTALSL_HIST":
                s_mom = s.diff()
                s_mom = s_mom.dropna()
                data[label] = [
                    {"date": d.strftime("%Y-%m"), "val": round(float(v) / 1000, 1)}
                    for d, v in s_mom.items()
                ]
            else:
                monthly = s.resample("MS").last().dropna() if len(s) > 500 else s
                data[label] = [
                    {"date": d.strftime("%Y-%m"), "val": round(float(v), 1)}
                    for d, v in monthly.items()
                ]

            # DGORDER_HIST/INDPRO_HIST above are kept as raw levels for their
            # modal charts — the indicator table needs MoM% instead, computed
            # separately here so it doesn't disturb the existing chart data.
            if label in ("DGORDER_HIST", "INDPRO_HIST"):
                s_mom = s.pct_change().dropna() * 100
                if len(s_mom) > 0:
                    data[label.replace("_HIST", "_MOM")] = {
                        "latest": round(float(s_mom.iloc[-1]), 1),
                        "prior": round(float(s_mom.iloc[-2]), 1) if len(s_mom) > 1 else None,
                        "date": s_mom.index[-1].strftime("%Y-%m-%d"),
                    }
        except Exception as e:
            print(f"  WARN: Failed to fetch {label} ({sid}): {e}")
            data[label] = []

    return data


# ---------------------------------------------------------------------------
# 2. YAHOO FINANCE DATA
# ---------------------------------------------------------------------------

LOGO_CSV_URL = "https://logoetf.com/wp-content/uploads/data/TidalFG_Holdings_LOGO.csv"

def fetch_logo_holdings_csv():
    """Download LOGO ETF holdings CSV and parse tickers + weights."""
    try:
        resp = requests.get(LOGO_CSV_URL, timeout=30)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        holdings = []
        for i, row in enumerate(reader):
            ticker = row.get("StockTicker", "").strip()
            name = row.get("SecurityName", "").strip()
            weight_str = row.get("Weightings", "0").replace("%", "").strip()
            if not ticker or ticker == "Cash&Other" or any(c.isdigit() for c in ticker) or ticker == "ADDYY":
                continue
            try:
                weight = float(weight_str)
            except ValueError:
                weight = 0
            holdings.append({
                "ticker": ticker,
                "csv_name": name,
                "weight": weight,
                "rank": i + 1,
            })
        print(f"  Fetched {len(holdings)} holdings from LOGO CSV")
        return holdings
    except Exception as e:
        print(f"  WARN: Failed to fetch LOGO CSV: {e}")
        return []

LOGO_CSV_HOLDINGS = fetch_logo_holdings_csv()
LOGO_TICKERS = [h["ticker"] for h in LOGO_CSV_HOLDINGS] if LOGO_CSV_HOLDINGS else [
    "AAPL", "AVGO", "TSM", "NVDA", "LLY", "NFLX", "LNG", "PANW", "APP",
    "V", "AMZN", "MELI", "GOOGL", "APO", "BX", "ABBV", "AZN", "CBRE",
    "COST", "GE", "HEI", "TDG", "UBER", "QXO", "TLN", "GEV",
    "VST", "DASH", "AXON", "VIK", "CTVA", "TTWO", "JPM", "DE", "PWR",
    "RTX", "CAT", "MS", "SPOT", "TJX", "LHX", "FWONA", "HLT", "TT",
    "COF", "MAR", "ETN", "ASML", "AVAV",
]

INDEX_TICKERS = ["^GSPC", "^IXIC", "^RUA"]

COMMODITY_TICKERS = {
    "VIX": "^VIX",
    "WTI_OIL": "CL=F",
    "NAT_GAS": "NG=F",
    "DXY": "DX-Y.NYB",
}

SECTOR_ETFS = {
    "Technology": "XLK", "Healthcare": "XLV", "Financials": "XLF",
    "Energy": "XLE", "Materials": "XLB", "Industrials": "XLI",
    "Comm. Services": "XLC", "Cons. Staples": "XLP", "Utilities": "XLU",
    "Real Estate": "XLRE", "Cons. Discretionary": "XLY",
}


def safe_round(val, decimals=1):
    try:
        return round(float(val), decimals)
    except (TypeError, ValueError):
        return None


def fetch_yf_data():
    """Fetch stock/index prices, valuations, sector performance from Yahoo Finance."""
    data = {}

    # ----- Indices -----
    for sym in INDEX_TICKERS:
        try:
            t = yf.Ticker(sym)
            info = t.fast_info
            price = round(info.last_price, 2) if hasattr(info, 'last_price') else None
            prev = round(info.previous_close, 2) if hasattr(info, 'previous_close') else price
            chg = round((price - prev) / prev * 100, 2) if price and prev else 0
            data[sym] = {"price": price, "change_pct": chg}
        except Exception as e:
            print(f"  WARN: Failed to fetch index {sym}: {e}")
            data[sym] = {"price": None, "change_pct": 0}

    # ----- S&P 500 Valuation (via SPY) -----
    try:
        spy = yf.Ticker("SPY")
        spy_info = spy.info
        data["sp500_pe"] = safe_round(spy_info.get("trailingPE"), 1)
        data["sp500_fwd_pe"] = safe_round(spy_info.get("forwardPE"), 1)
        data["sp500_trailing_eps"] = safe_round(spy_info.get("trailingEps"), 2)
        data["sp500_forward_eps"] = safe_round(spy_info.get("forwardEps"), 2)
        data["sp500_ev_ebitda"] = safe_round(spy_info.get("enterpriseToEbitda"), 1)
        data["sp500_earnings_growth"] = safe_round(spy_info.get("earningsGrowth"), 3)
        data["sp500_revenue_growth"] = safe_round(spy_info.get("revenueGrowth"), 3)
        data["sp500_dividend_yield"] = safe_round(spy_info.get("dividendYield"), 4)
        data["sp500_beta"] = safe_round(spy_info.get("beta"), 2)
        data["sp500_52wk_high"] = safe_round(spy_info.get("fiftyTwoWeekHigh"), 2)
        data["sp500_52wk_low"] = safe_round(spy_info.get("fiftyTwoWeekLow"), 2)
        print(f"    SPY: PE={data['sp500_pe']}, FwdPE={data['sp500_fwd_pe']}, EV/EBITDA={data['sp500_ev_ebitda']}, EarningsGrowth={data['sp500_earnings_growth']}")
    except Exception as e:
        print(f"  WARN: Failed to fetch SPY valuation: {e}")
        data["sp500_pe"] = None
        data["sp500_fwd_pe"] = None

    # ----- Commodities / VIX -----
    data["commodities"] = {}
    for label, sym in COMMODITY_TICKERS.items():
        try:
            t = yf.Ticker(sym)
            info = t.fast_info
            price = round(info.last_price, 2) if hasattr(info, 'last_price') else None
            prev = round(info.previous_close, 2) if hasattr(info, 'previous_close') else price
            chg = round((price - prev) / prev * 100, 2) if price and prev else 0
            data["commodities"][label] = {"price": price, "change_pct": chg}
            print(f"    {label}: ${price} ({chg:+.2f}%)")
        except Exception as e:
            print(f"  WARN: Failed to fetch {label} ({sym}): {e}")
            data["commodities"][label] = {"price": None, "change_pct": 0}

    # ----- Sector ETF Performance (weekly, monthly, YTD, 1Y) -----
    sector_perf = {}
    for sector_name, etf in SECTOR_ETFS.items():
        try:
            t = yf.Ticker(etf)
            hist = t.history(period="1y")
            if len(hist) < 5:
                sector_perf[sector_name] = {"etf": etf, "weekly_chg": 0, "monthly_chg": 0, "ytd_chg": 0, "yearly_chg": 0}

                continue

            last = hist["Close"].iloc[-1]

            # Weekly (5 trading days)
            wk_start = hist["Close"].iloc[-5] if len(hist) >= 5 else hist["Close"].iloc[0]
            wk_chg = round((last - wk_start) / wk_start * 100, 2)

            # Monthly (~21 trading days)
            mo_start = hist["Close"].iloc[-21] if len(hist) >= 21 else hist["Close"].iloc[0]
            mo_chg = round((last - mo_start) / mo_start * 100, 2)

            # YTD (from first trading day of year)
            import pandas as pd
            ytd_data = hist[hist.index >= pd.Timestamp(f"{datetime.date.today().year}-01-01", tz=hist.index.tz)]
            ytd_start = ytd_data["Close"].iloc[0] if len(ytd_data) > 0 else hist["Close"].iloc[0]
            ytd_chg = round((last - ytd_start) / ytd_start * 100, 2)

            # 1Y (first data point)
            yr_start = hist["Close"].iloc[0]
            yr_chg = round((last - yr_start) / yr_start * 100, 2)

            sector_perf[sector_name] = {
                "etf": etf,
                "weekly_chg": wk_chg,
                "monthly_chg": mo_chg,
                "ytd_chg": ytd_chg,
                "yearly_chg": yr_chg,
            }
            print(f"    {sector_name}: W={wk_chg:+.2f}% M={mo_chg:+.2f}% YTD={ytd_chg:+.2f}% 1Y={yr_chg:+.2f}%")
        except Exception as e:
            print(f"  WARN: Failed to fetch sector {etf}: {e}")
            sector_perf[sector_name] = {"etf": etf, "weekly_chg": 0, "monthly_chg": 0, "ytd_chg": 0}
    data["sector_performance"] = sector_perf

    # ----- KPI Price Histories (for Market Snapshot modals) -----
    kpi_symbols = {
        "SP500_HIST": "^GSPC",
        "NASDAQ_HIST": "^IXIC",
        "RUSSELL_HIST": "^RUA",
        "VIX_HIST": "^VIX",
        "OIL_HIST": "CL=F",
        "GAS_HIST": "NG=F",
        "DXY_HIST": "DX-Y.NYB",
        "COPPER_HIST": "HG=F",
        "GOLD_HIST": "GC=F",
    }
    kpi_histories = {}
    for label, sym in kpi_symbols.items():
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="5y", interval="1d")
            if len(hist) > 0:
                weekly = hist["Close"].resample("W").last().dropna()
                kpi_histories[label] = [
                    {"date": d.strftime("%Y-%m-%d"), "close": round(float(v), 2)}
                    for d, v in weekly.items()
                ]
                print(f"    {label}: {len(kpi_histories[label])} weekly points")
        except Exception as e:
            print(f"  WARN: Failed to fetch {label} ({sym}): {e}")
            kpi_histories[label] = []
    data["kpi_histories"] = kpi_histories

    # ----- LOGO Holdings (full fundamentals) -----
    holdings = []
    def fetch_stock_data(sym, timeout=60):
        """Fetch all stock data with a timeout to prevent hangs."""
        result = [None]
        def _fetch():
            try:
                t = yf.Ticker(sym)
                info = t.info
                result[0] = (t, info)
            except Exception as e:
                print(f"    Thread error for {sym}: {e}")
        thread = threading.Thread(target=_fetch, daemon=True)
        thread.start()
        thread.join(timeout)
        if result[0] is None:
            print(f"  WARN: Timeout/error fetching {sym} after {timeout}s, skipping")
        return result[0]

    for sym in LOGO_TICKERS:
        try:
            stock_data = fetch_stock_data(sym)
            if stock_data is None:
                raise Exception(f"Timeout for {sym}")
            t, info = stock_data
            h = {
                "ticker": sym,
                "name": info.get("shortName", sym),
                "weight": 0,
                "rank": 999,
                "price": safe_round(info.get("currentPrice", info.get("regularMarketPrice", 0)), 2),
                "mktcap": info.get("marketCap", 0),
                "pe": safe_round(info.get("trailingPE")),
                "fwdpe": safe_round(info.get("forwardPE")),
                "ev_ebitda": safe_round(info.get("enterpriseToEbitda")),
                "earnings_growth": safe_round(info.get("earningsGrowth"), 3),
                "revenue_growth": safe_round(info.get("revenueGrowth"), 3),
                # All 4 margin types
                "gross_margin": safe_round(info.get("grossMargins"), 3),
                "ebitda_margin": safe_round(info.get("ebitdaMargins"), 3),
                "operating_margin": safe_round(info.get("operatingMargins"), 3),
                "profit_margin": safe_round(info.get("profitMargins"), 3),
                # Analyst consensus
                "target_price": safe_round(info.get("targetMeanPrice"), 2),
                "target_low": safe_round(info.get("targetLowPrice"), 2),
                "target_high": safe_round(info.get("targetHighPrice"), 2),
                "num_analysts": info.get("numberOfAnalystOpinions", 0),
                "recommendation": info.get("recommendationKey", ""),
                "recommendation_score": safe_round(info.get("recommendationMean"), 1),
                # Revenue & EBITDA
                "total_revenue": info.get("totalRevenue", 0),
                "ebitda": info.get("ebitda", 0),
                # Identity
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "beta": safe_round(info.get("beta"), 2),
                "dividend_yield": safe_round(info.get("dividendYield"), 4),
                "high_52wk": safe_round(info.get("fiftyTwoWeekHigh"), 2),
                "low_52wk": safe_round(info.get("fiftyTwoWeekLow"), 2),
                "avg_volume": info.get("averageVolume", 0),
                "roe": safe_round(info.get("returnOnEquity"), 3),
                "debt_to_equity": safe_round(info.get("debtToEquity"), 1),
                "short_pct": safe_round(info.get("shortPercentOfFloat"), 3),
                "insider_pct": safe_round(info.get("heldPercentInsiders"), 3),
                "description": info.get("longBusinessSummary", ""),
            }
            # Fetch multi-period price history for charts
            h["price_histories"] = {}
            period_configs = [
                ("6mo", "6mo", "1wk"),
                ("1y", "1y", "1wk"),
                ("5y", "5y", "1mo"),
            ]
            for label, period, interval in period_configs:
                try:
                    hist = t.history(period=period, interval=interval)
                    if len(hist) > 0:
                        h["price_histories"][label] = [
                            {"date": d.strftime("%Y-%m-%d"), "close": round(float(c), 2)}
                            for d, c in hist["Close"].items()
                        ]
                except:
                    pass
            h["price_history"] = h["price_histories"].get("1y", [])

            holdings.append(h)
            print(f"    {sym}: ${h['price']} PE={h['pe']} FwdPE={h['fwdpe']} GM={h['gross_margin']} Target=${h['target_price']}")
        except Exception as e:
            print(f"  WARN: Failed to fetch {sym}: {e}")
            holdings.append({
                "ticker": sym, "name": sym, "weight": 0, "rank": 999, "price": 0, "mktcap": 0,
                "pe": None, "fwdpe": None, "ev_ebitda": None,
                "earnings_growth": None, "revenue_growth": None,
                "gross_margin": None, "ebitda_margin": None,
                "operating_margin": None, "profit_margin": None,
                "target_price": None, "target_low": None, "target_high": None,
                "num_analysts": 0, "recommendation": "", "recommendation_score": None,
                "total_revenue": 0, "ebitda": 0,
                "sector": "", "industry": "",
                "beta": None, "dividend_yield": None,
                "high_52wk": None, "low_52wk": None,
                "avg_volume": 0, "description": "", "price_history": [],
            })

    # Merge CSV weights and ranks into holdings
    if LOGO_CSV_HOLDINGS:
        csv_map = {h["ticker"]: h for h in LOGO_CSV_HOLDINGS}
        for h in holdings:
            csv_data = csv_map.get(h["ticker"])
            if csv_data:
                h["weight"] = csv_data["weight"]
                h["rank"] = csv_data["rank"]
                if not h.get("name") or h["name"] == h["ticker"]:
                    h["name"] = csv_data["csv_name"]
        holdings.sort(key=lambda x: x.get("rank", 999))

    data["holdings"] = holdings

    return data


# ---------------------------------------------------------------------------
# 3. ECONOMIC RELEASE CALENDAR
# ---------------------------------------------------------------------------
# Rule-based recurring release schedule. BLS/Census/Conference Board release
# days follow well-known monthly conventions (e.g. jobs report = 1st Friday),
# so "next release" dates can be computed locally without a calendar API and
# never go stale like a hand-typed date would.

FOMC_DATES_2026 = [
    datetime.date(2026, 1, 28), datetime.date(2026, 3, 18), datetime.date(2026, 4, 29),
    datetime.date(2026, 6, 17), datetime.date(2026, 7, 29), datetime.date(2026, 9, 16),
    datetime.date(2026, 10, 28), datetime.date(2026, 12, 9),
]


def _nth_weekday(year, month, weekday, n):
    """weekday: Mon=0..Sun=6. n=1 -> first occurrence in month, n=-1 -> last."""
    cal = calendar.Calendar()
    days = [d for d in cal.itermonthdates(year, month) if d.month == month and d.weekday() == weekday]
    return days[n - 1] if n > 0 else days[n]


def _business_day_on_or_after(year, month, day):
    """Approximate release day, nudged off weekends onto the nearest prior weekday."""
    last_day = calendar.monthrange(year, month)[1]
    d = datetime.date(year, month, min(day, last_day))
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d


def _fmt_month_day(d):
    return f"{d.strftime('%b')} {d.day}"


def _gdp_quarter_label(release_date):
    return {1: f"Q4 {release_date.year - 1}", 4: f"Q1 {release_date.year}",
            7: f"Q2 {release_date.year}", 10: f"Q3 {release_date.year}"}.get(release_date.month, "")


# key -> (display name, description, release time, rule(year, month) -> date or None)
RELEASE_RULES = {
    "gdp": ("GDP Advance", "First look at quarterly GDP growth.", "8:30 AM",
            lambda y, m: _business_day_on_or_after(y, m, 30) if m in (1, 4, 7, 10) else None),
    "jobs": ("Employment Situation", "Nonfarm payrolls + unemployment rate.", "8:30 AM",
             lambda y, m: _nth_weekday(y, m, 4, 1)),
    "pmi": ("ISM Manufacturing PMI", "Manufacturing sector health gauge.", "10:00 AM",
            lambda y, m: _business_day_on_or_after(y, m, 1)),
    "retail": ("Retail Sales", "Consumer spending on goods.", "8:30 AM",
               lambda y, m: _business_day_on_or_after(y, m, 15)),
    "housing_starts": ("Housing Starts", "New residential construction.", "8:30 AM",
                        lambda y, m: _business_day_on_or_after(y, m, 18)),
    "permits": ("Building Permits", "Leading housing construction indicator.", "8:30 AM",
                lambda y, m: _business_day_on_or_after(y, m, 18)),
    "trade": ("Trade Balance", "Exports minus imports.", "8:30 AM",
              lambda y, m: _business_day_on_or_after(y, m, 5)),
    "credit": ("Consumer Credit", "Consumer borrowing outstanding.", "3:00 PM",
               lambda y, m: _business_day_on_or_after(y, m, 7)),
    "import_prices": ("Import Prices", "Price of imported goods.", "8:30 AM",
                       lambda y, m: _business_day_on_or_after(y, m, 15)),
    "lei": ("Leading Economic Index", "Conference Board composite index.", "10:00 AM",
            lambda y, m: _business_day_on_or_after(y, m, 20)),
    "cpi": ("CPI Report", "Critical inflation read.", "8:30 AM",
            lambda y, m: _business_day_on_or_after(y, m, 13)),
    "sentiment": ("Consumer Sentiment (Prelim)", "Michigan survey inflation expectations.", "10:00 AM",
                  lambda y, m: _nth_weekday(y, m, 4, 2)),
    "durables": ("Durable Goods Orders", "Business capex proxy.", "8:30 AM",
                 lambda y, m: _business_day_on_or_after(y, m, 25)),
    "indpro": ("Industrial Production", "Factory, mine, and utility output.", "9:15 AM",
               lambda y, m: _business_day_on_or_after(y, m, 16)),
    "confidence": ("Consumer Confidence", "Conference Board index.", "10:00 AM",
                   lambda y, m: _nth_weekday(y, m, 1, -1)),
    "new_home_sales": ("New Home Sales", "Key housing demand gauge.", "10:00 AM",
                        lambda y, m: _business_day_on_or_after(y, m, 24)),
}


def _next_monthly_occurrence(today, rule_fn, months_ahead=15):
    y, m = today.year, today.month
    for _ in range(months_ahead):
        d = rule_fn(y, m)
        if d and d >= today:
            return d
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return None


def _next_fomc(today):
    for d in FOMC_DATES_2026:
        if d >= today:
            return d
    return None


def compute_release_calendar(today):
    """Returns next-release labels for the indicator table plus a rolling
    'this week' / 'upcoming' event list for the Economic Calendar card."""
    events = {}
    for key, (name, desc, time, rule_fn) in RELEASE_RULES.items():
        d = _next_monthly_occurrence(today, rule_fn)
        if d:
            label = f"{name} — {_gdp_quarter_label(d)}" if key == "gdp" else name
            events[key] = {"date": d, "name": label, "desc": desc, "time": time}
    fomc_date = _next_fomc(today)
    if fomc_date:
        events["fomc"] = {"date": fomc_date, "name": "FOMC Rate Decision",
                           "desc": "Next policy meeting.", "time": "2:00 PM"}

    next_release = {k: _fmt_month_day(v["date"]) for k, v in events.items()}
    next_release["treasury"] = "Daily"

    week_start = today - datetime.timedelta(days=today.weekday())
    week_end = week_start + datetime.timedelta(days=4)
    ordered = sorted(events.values(), key=lambda e: e["date"])
    for e in ordered:
        e["day"] = e["date"].day
        e["month_abbr"] = e["date"].strftime("%b").upper()

    return {
        "next_release": next_release,
        "week_range_label": f"{_fmt_month_day(week_start)}–{week_end.day}",
        "calendar_this_week": [e for e in ordered if week_start <= e["date"] <= week_end],
        "calendar_upcoming": [e for e in ordered if e["date"] > week_end][:5],
    }


# ---------------------------------------------------------------------------
# 4. INDICATOR TABLE (Latest / Prior / Period)
# ---------------------------------------------------------------------------
# 13 of the 15 rows have a free live data source and get computed here.
# ISM Manufacturing PMI and the Conference Board LEI are proprietary indices
# with no free API, so those two rows stay manually curated in the template.

def _parse_date(s):
    parts = s.split("-")
    if len(parts) == 2:
        return datetime.date(int(parts[0]), int(parts[1]), 1)
    return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))


def _month_year_label(d):
    return d.strftime("%b %Y")


def _quarter_label(d):
    return f"Q{(d.month - 1) // 3 + 1} {d.year}"


def _fmt_signed_pct(val, decimals=1):
    return f"{val:+.{decimals}f}%" if val is not None else "N/A"


def _fmt_plain_pct(val, decimals=2):
    return f"{val:.{decimals}f}%" if val is not None else "N/A"


def _fmt_signed_dollar_b(val, decimals=1):
    if val is None:
        return "N/A"
    return f"{'-' if val < 0 else '+'}${abs(val):.{decimals}f}B"


def _fmt_millions(val_thousands, decimals=3):
    return f"{val_thousands / 1000:.{decimals}f}M" if val_thousands is not None else "N/A"


def _trend(latest, prior, polarity=1):
    """polarity: +1 = higher is favorable, -1 = higher is unfavorable, 0 = no read."""
    if latest is None or prior is None:
        return "—", "neutral"
    if latest > prior:
        return "Rising", ("neutral" if polarity == 0 else ("up" if polarity > 0 else "down"))
    if latest < prior:
        return "Falling", ("neutral" if polarity == 0 else ("down" if polarity > 0 else "up"))
    return "Flat", "neutral"


def compute_indicator_table(fred_data):
    table = {}

    gdp = fred_data.get("GDP", {})
    if gdp.get("latest") is not None:
        d = _parse_date(gdp["date"])
        trend, trend_cls = _trend(gdp["latest"], gdp["prior"], 1)
        table["gdp"] = {"latest": _fmt_signed_pct(gdp["latest"]), "prior": _fmt_signed_pct(gdp["prior"]),
                        "period": _quarter_label(d), "trend": trend, "trend_class": trend_cls}

    payems = fred_data.get("PAYEMS_HIST", [])
    if len(payems) >= 3:
        diff_latest = payems[-1]["val"] - payems[-2]["val"]
        diff_prior = payems[-2]["val"] - payems[-3]["val"]
        d = _parse_date(payems[-1]["date"])
        trend, trend_cls = _trend(diff_latest, diff_prior, 1)
        table["jobs"] = {"latest": f"{diff_latest:+.0f}K", "prior": f"{diff_prior:+.0f}K",
                          "period": _month_year_label(d), "trend": trend, "trend_class": trend_cls}

    retail = fred_data.get("RSAFS_HIST", [])
    if len(retail) >= 2:
        d = _parse_date(retail[-1]["date"])
        trend, trend_cls = _trend(retail[-1]["val"], retail[-2]["val"], 1)
        table["retail"] = {"latest": _fmt_signed_pct(retail[-1]["val"]), "prior": _fmt_signed_pct(retail[-2]["val"]),
                            "period": _month_year_label(d), "trend": trend, "trend_class": trend_cls}

    houst = fred_data.get("HOUST_HIST", [])
    if len(houst) >= 2:
        d = _parse_date(houst[-1]["date"])
        trend, trend_cls = _trend(houst[-1]["val"], houst[-2]["val"], 1)
        table["housing_starts"] = {"latest": _fmt_millions(houst[-1]["val"]), "prior": _fmt_millions(houst[-2]["val"]),
                                    "period": _month_year_label(d), "trend": trend, "trend_class": trend_cls}

    permit = fred_data.get("PERMIT_HIST", [])
    if len(permit) >= 2:
        d = _parse_date(permit[-1]["date"])
        trend, trend_cls = _trend(permit[-1]["val"], permit[-2]["val"], 1)
        table["permits"] = {"latest": _fmt_millions(permit[-1]["val"]), "prior": _fmt_millions(permit[-2]["val"]),
                             "period": _month_year_label(d), "trend": trend, "trend_class": trend_cls}

    trade = fred_data.get("BOPGSTB_HIST", [])
    if len(trade) >= 2:
        d = _parse_date(trade[-1]["date"])
        trend, trend_cls = _trend(trade[-1]["val"], trade[-2]["val"], 1)
        table["trade"] = {"latest": _fmt_signed_dollar_b(trade[-1]["val"]), "prior": _fmt_signed_dollar_b(trade[-2]["val"]),
                           "period": _month_year_label(d), "trend": trend, "trend_class": trend_cls}

    credit = fred_data.get("TOTALSL_HIST", [])
    if len(credit) >= 2:
        d = _parse_date(credit[-1]["date"])
        trend, trend_cls = _trend(credit[-1]["val"], credit[-2]["val"], 0)
        table["credit"] = {"latest": _fmt_signed_dollar_b(credit[-1]["val"]), "prior": _fmt_signed_dollar_b(credit[-2]["val"]),
                            "period": _month_year_label(d), "trend": trend, "trend_class": trend_cls}

    imp = fred_data.get("IMPORT_PRICE_HIST", [])
    if len(imp) >= 2:
        d = _parse_date(imp[-1]["date"])
        trend, trend_cls = _trend(imp[-1]["val"], imp[-2]["val"], -1)
        table["import_prices"] = {"latest": _fmt_signed_pct(imp[-1]["val"]), "prior": _fmt_signed_pct(imp[-2]["val"]),
                                   "period": _month_year_label(d), "trend": trend, "trend_class": trend_cls}

    wage = fred_data.get("WAGE_HIST", [])
    if len(wage) >= 2:
        d = _parse_date(wage[-1]["date"])
        trend, trend_cls = _trend(wage[-1]["val"], wage[-2]["val"], 1)
        table["wage"] = {"latest": _fmt_signed_pct(wage[-1]["val"]), "prior": _fmt_signed_pct(wage[-2]["val"]),
                          "period": _month_year_label(d), "trend": trend, "trend_class": trend_cls}

    ust10y = fred_data.get("UST_10Y", {})
    if ust10y.get("latest") is not None:
        d = _parse_date(ust10y["date"])
        trend, trend_cls = _trend(ust10y["latest"], ust10y["prior"], 0)
        table["treasury10y"] = {"latest": _fmt_plain_pct(ust10y["latest"]), "prior": _fmt_plain_pct(ust10y["prior"]),
                                 "period": _fmt_month_day(d), "trend": trend, "trend_class": trend_cls}

    infl_exp = fred_data.get("INFL_EXP_1Y", {})
    if infl_exp.get("latest") is not None:
        d = _parse_date(infl_exp["date"])
        trend, trend_cls = _trend(infl_exp["latest"], infl_exp["prior"], -1)
        table["sentiment"] = {"latest": _fmt_plain_pct(infl_exp["latest"], 1), "prior": _fmt_plain_pct(infl_exp["prior"], 1),
                               "period": _month_year_label(d), "trend": trend, "trend_class": trend_cls}

    dgorder_mom = fred_data.get("DGORDER_MOM", {})
    if dgorder_mom.get("latest") is not None:
        d = _parse_date(dgorder_mom["date"])
        trend, trend_cls = _trend(dgorder_mom["latest"], dgorder_mom["prior"], 1)
        table["durables"] = {"latest": _fmt_signed_pct(dgorder_mom["latest"]), "prior": _fmt_signed_pct(dgorder_mom["prior"]),
                              "period": _month_year_label(d), "trend": trend, "trend_class": trend_cls}

    indpro_mom = fred_data.get("INDPRO_MOM", {})
    if indpro_mom.get("latest") is not None:
        d = _parse_date(indpro_mom["date"])
        trend, trend_cls = _trend(indpro_mom["latest"], indpro_mom["prior"], 1)
        table["indpro"] = {"latest": _fmt_signed_pct(indpro_mom["latest"]), "prior": _fmt_signed_pct(indpro_mom["prior"]),
                            "period": _month_year_label(d), "trend": trend, "trend_class": trend_cls}

    return table


def format_mktcap(val):
    if not val or val == 0:
        return "N/A"
    if val >= 1e12:
        return f"${val/1e12:.2f}T"
    if val >= 1e9:
        return f"${val/1e9:.0f}B"
    if val >= 1e6:
        return f"${val/1e6:.0f}M"
    return f"${val:,.0f}"


# ---------------------------------------------------------------------------
# 3. BUILD
# ---------------------------------------------------------------------------

def build_dashboard():
    print(f"[{datetime.datetime.now()}] Starting dashboard build...")

    print("  Fetching FRED data...")
    fred_data = fetch_fred_data()

    print("  Fetching Yahoo Finance data...")
    yf_data = fetch_yf_data()

    # Format holdings for display
    def fmt_pct(v, mult=100):
        return f"{v*mult:.1f}%" if v else "N/A"
    def fmt_pct_signed(v, mult=100):
        return f"{v*mult:+.1f}%" if v else "N/A"

    for h in yf_data["holdings"]:
        h["mktcap_fmt"] = format_mktcap(h["mktcap"])
        h["price_fmt"] = f"${h['price']:,.2f}" if h.get("price") else "N/A"
        h["pe_fmt"] = str(h["pe"]) if h.get("pe") else "N/A"
        h["fwdpe_fmt"] = str(h["fwdpe"]) if h.get("fwdpe") else "N/A"
        h["ev_ebitda_fmt"] = str(h["ev_ebitda"]) if h.get("ev_ebitda") else "N/A"
        h["earnings_growth_fmt"] = fmt_pct_signed(h.get("earnings_growth"))
        h["revenue_growth_fmt"] = fmt_pct_signed(h.get("revenue_growth"))
        h["gross_margin_fmt"] = fmt_pct(h.get("gross_margin"))
        h["ebitda_margin_fmt"] = fmt_pct(h.get("ebitda_margin"))
        h["operating_margin_fmt"] = fmt_pct(h.get("operating_margin"))
        h["profit_margin_fmt"] = fmt_pct(h.get("profit_margin"))
        h["target_price_fmt"] = f"${h['target_price']:,.2f}" if h.get("target_price") else "N/A"
        h["total_revenue_fmt"] = format_mktcap(h.get("total_revenue"))
        h["ebitda_fmt"] = format_mktcap(h.get("ebitda"))
        # Upside to target
        if h.get("target_price") and h.get("price") and h["price"] > 0:
            h["upside"] = round((h["target_price"] - h["price"]) / h["price"] * 100, 1)
            h["upside_fmt"] = f"{h['upside']:+.1f}%"
        else:
            h["upside"] = 0
            h["upside_fmt"] = "N/A"

    # Format S&P earnings growth
    sp_eg = yf_data.get("sp500_earnings_growth")
    sp_eg_fmt = f"{sp_eg*100:+.1f}%" if sp_eg else "N/A"
    sp_rg = yf_data.get("sp500_revenue_growth")
    sp_rg_fmt = f"{sp_rg*100:+.1f}%" if sp_rg else "N/A"
    sp_dy = yf_data.get("sp500_dividend_yield")
    sp_dy_fmt = f"{sp_dy*100:.2f}%" if sp_dy else "N/A"

    print("  Computing release calendar...")
    calendar_data = compute_release_calendar(datetime.date.today())
    indicator_table = compute_indicator_table(fred_data)

    # Build context
    now = datetime.datetime.now()
    ctx = {
        "build_date": now.strftime("%A, %B %d, %Y"),
        "build_time": now.strftime("%I:%M %p ET"),
        "indicator_table": indicator_table,
        "next_release": calendar_data["next_release"],
        "week_range_label": calendar_data["week_range_label"],
        "calendar_this_week": calendar_data["calendar_this_week"],
        "calendar_upcoming": calendar_data["calendar_upcoming"],
        "fred": fred_data,
        "yf": yf_data,
        "sp500_price": yf_data.get("^GSPC", {}).get("price"),
        "sp500_chg": yf_data.get("^GSPC", {}).get("change_pct", 0),
        "nasdaq_price": yf_data.get("^IXIC", {}).get("price"),
        "nasdaq_chg": yf_data.get("^IXIC", {}).get("change_pct", 0),
        "russell_price": yf_data.get("^RUA", {}).get("price"),
        "russell_chg": yf_data.get("^RUA", {}).get("change_pct", 0),
        "sp500_pe": yf_data.get("sp500_pe"),
        "sp500_fwd_pe": yf_data.get("sp500_fwd_pe"),
        "sp500_ev_ebitda": yf_data.get("sp500_ev_ebitda"),
        "sp500_earnings_growth": sp_eg_fmt,
        "sp500_revenue_growth": sp_rg_fmt,
        "sp500_dividend_yield": sp_dy_fmt,
        "sp500_trailing_eps": yf_data.get("sp500_trailing_eps"),
        "sp500_forward_eps": yf_data.get("sp500_forward_eps"),
        "sector_performance": yf_data.get("sector_performance", {}),
        "commodities": yf_data.get("commodities", {}),
        "holdings": yf_data["holdings"],
        "fred_json": json.dumps(fred_data, default=str),
        "holdings_json": json.dumps(yf_data["holdings"], default=str),
        "sector_json": json.dumps(yf_data.get("sector_performance", {}), default=str),
        "commodities_json": json.dumps(yf_data.get("commodities", {}), default=str),
        "kpi_histories_json": json.dumps(yf_data.get("kpi_histories", {}), default=str),
    }

    # Load and render template
    template_path = os.path.join(os.path.dirname(__file__), "template.html")
    with open(template_path, "r", encoding="utf-8") as f:
        tmpl = Template(f.read())

    html = tmpl.render(**ctx)

    out_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  Dashboard written to {out_path}")
    print(f"[{datetime.datetime.now()}] Done.")


if __name__ == "__main__":
    build_dashboard()
