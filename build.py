"""
Accuvest Daily Dashboard Builder
Fetches live data from FRED + Yahoo Finance, renders the HTML dashboard.
Run manually or via GitHub Actions on a daily schedule.
"""

import os
import json
import datetime
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

    # ----- Credit spread histories (10 years) -----
    for label, sid in [("HY_HIST", "BAMLH0A0HYM2"), ("IG_HIST", "BAMLC0A0CM"), ("BBB_HIST", "BAMLC0A4CBBB"), ("T10Y2Y_HIST", "T10Y2Y")]:
        try:
            s = fred.get_series(sid, observation_start=start_10y)
            s = s.dropna().resample("MS").last().dropna()
            data[label] = [
                {"date": d.strftime("%Y-%m"), "val": round(float(v), 2)}
                for d, v in s.items()
            ]
        except Exception as e:
            print(f"  WARN: Failed to fetch {label}: {e}")
            data[label] = []

    # ----- CPI / Core PCE / Unemployment histories (5 years) -----
    for label, sid in [("CPI_HIST", "CPIAUCSL"), ("PCE_HIST", "PCEPILFE"), ("UNEMP_HIST", "UNRATE"), ("SAVINGS_HIST", "PSAVERT"), ("UMCSENT_HIST", "UMCSENT")]:
        try:
            s = fred.get_series(sid, observation_start=start_5y)
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
        gdp = fred.get_series("A191RL1Q225SBEA", observation_start=start_5y)
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
            s = fred.get_series(sid, observation_start=start_10y)
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
        except Exception as e:
            print(f"  WARN: Failed to fetch {label} ({sid}): {e}")
            data[label] = []

    return data


# ---------------------------------------------------------------------------
# 2. YAHOO FINANCE DATA
# ---------------------------------------------------------------------------

LOGO_TICKERS = [
    "AMZN", "NFLX", "AAPL", "GOOGL", "LLY", "HEI", "TSM", "NVDA", "MELI",
    "UBER", "TDG", "V", "LNG", "APP", "APO", "BX", "CME", "QXO", "CBOE",
    "GE", "ROKU", "SHOP", "MSFT", "COST", "PANW", "MRVL", "ABNB", "INTU",
    "SBUX", "LULU", "NXPI", "MNST", "TJX", "RCL", "DKNG", "WYNN",
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
    for sym in LOGO_TICKERS:
        try:
            t = yf.Ticker(sym)
            info = t.info
            h = {
                "ticker": sym,
                "name": info.get("shortName", sym),
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
                "description": info.get("longBusinessSummary", ""),
            }
            # Fetch multi-period price history for charts
            h["price_histories"] = {}
            period_configs = [
                ("1wk", "5d", "5m"),
                ("1mo", "1mo", "1d"),
                ("6mo", "6mo", "1d"),
                ("1y", "1y", "1wk"),
                ("5y", "5y", "1wk"),
                ("10y", "10y", "1mo"),
                ("20y", "max", "1mo"),
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
                "ticker": sym, "name": sym, "price": 0, "mktcap": 0,
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

    data["holdings"] = holdings

    return data


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

    # Build context
    now = datetime.datetime.now()
    ctx = {
        "build_date": now.strftime("%A, %B %d, %Y"),
        "build_time": now.strftime("%I:%M %p ET"),
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
