# Accuvest Daily Market Dashboard

## Tech Stack
- **Frontend**: Single-file HTML/CSS/JS (no framework, no build tools)
- **Templating**: Jinja2 — `template.html` is the source, `build.py` renders it into `index.html`
- **Data**: FRED API (macro/rates/spreads) + Yahoo Finance via `yfinance` (stocks/indices/commodities/sectors)
- **Hosting**: GitHub Pages (static HTML served from `main` branch root)
- **CI/CD**: GitHub Actions — `daily-update.yml` runs weekdays at 6:30am ET, fetches live data, regenerates `index.html`, commits and pushes

## File Structure
```
├── CLAUDE.md              # This file
├── build.py               # Python data fetcher + Jinja2 renderer
├── template.html          # Jinja2 template (the source of truth for the dashboard)
├── index.html             # Generated output (committed by build bot, DO NOT edit directly)
├── requirements.txt       # Python deps: requests, yfinance, fredapi, jinja2
├── .env.example           # Shows required env vars
├── .gitignore
├── index_static_backup.html  # Pre-automation snapshot (gitignored)
└── .github/
    └── workflows/
        └── daily-update.yml  # GitHub Actions daily build
```

## Key Architectural Decisions

**Template + hydration pattern**: `template.html` contains both the static HTML structure and Jinja2 variables. At build time, `build.py` injects live data as JSON into a `<script>` block. A JS `hydrate()` function then updates DOM elements (KPIs, tables, charts) from that JSON. This means the dashboard works both as a static page (with fallback hardcoded values) and as a live-data page (after a build runs).

**`{% raw %}` block**: All JavaScript that uses template literals (`${}`) is wrapped in `{% raw %}...{% endraw %}` to prevent Jinja2 from trying to parse JS syntax as template variables. The Jinja2 data injection happens ABOVE the `{% raw %}` block.

**Charts are SVG rendered by JS**: All charts (yield curve, credit spreads, indicator trends, stock price history) are rendered as SVG by JavaScript functions using data from the `LIVE_DATA` JSON object. No chart library — just manual SVG path generation. This keeps the page dependency-free.

**Static analysis stays manual**: The structural forces narratives, risk radar assessments, framework commentary, and brand theses are hardcoded in `template.html`. They don't auto-update — Hailey updates them when the narrative changes. Everything else (prices, yields, spreads, fundamentals, charts) is auto-updated.

**FRED API key**: Stored as GitHub Secret `FRED_API_KEY`, accessed via `os.environ` in `build.py`. Never committed to code.

**Yahoo Finance (yfinance)**: No API key needed — it scrapes public Yahoo endpoints. Free but unofficial; can break if Yahoo changes their site. Acceptable for a daily internal dashboard.

## Coding Conventions

- **Edit `template.html`, never `index.html`** — index.html is auto-generated and will be overwritten
- CSS uses custom properties (`:root` vars) for Accuvest branding: `--navy`, `--accent-blue`, `--accent-orange`, `--green`, `--red`
- Font: Inter (Google Fonts)
- All new charts should use the `renderSpreadChart()` or `renderIndicatorChart()` pattern — generic JS functions that take a data array and SVG container ID
- Holdings data lives in a JS `holdings` array in the template with a separate `brandTheses` lookup object merged at runtime
- Indicator metadata (descriptions, FRED series, colors) lives in the `indicatorMeta` JS object
- Source links go inline in commentary boxes as small `[Source]` links, not footnotes

## Dashboard Tabs (Current State)

### Tab 1: Macro Overview — DONE
- KPI strip (S&P, Nasdaq, Russell, 10Y, Fed Funds, VIX, Oil, NatGas — all clickable to TradingView/FRED)
- GDP breakdown with quarterly history bar chart
- S&P 500 valuation cards (PE, Fwd PE, EV/EBITDA, earnings/revenue growth, EPS)
- Sector returns table (weekly, monthly, YTD from ETF history)
- Yield curve (SVG, all maturities) + FOMC/FedWatch
- Treasury yield history (10Y/20Y/30Y toggle)
- Credit spread charts (HY, IG, BBB, 2s/10s — all from FRED data)

### Tab 2: Economic Indicators — DONE
- Top 5 KPI cards
- 5 dynamic charts from FRED with time period toggles (CPI, Unemployment, Savings, Sentiment, Core PCE)
- Inflation breakdown + expectations cards
- 15-indicator table with hover tooltips and click-to-expand modals (description + chart + source)
- Economic calendar

### Tab 3: Alpha Brands (LOGO) — DONE
- Fund overview stats
- Sector allocation pie chart
- Holdings table (36 stocks, all from Yahoo Finance: price, mkt cap, PE, fwd PE, EV/EBITDA, target)
- Click any row → modal with: multi-period price chart (1W–Max with return %), 4 margins, growth metrics, analyst consensus, brand thesis, company description

### Tab 4: Structural Forces — DONE
- AI & Compute Buildout (sourced: CNBC, FactSet)
- GLP-1 & Healthcare (sourced: JPMorgan, Morgan Stanley, CNBC)
- Energy Security / ME Crisis (sourced: IMF, World Bank)
- Deglobalization & Reshoring (sourced: Atlantic Council)
- Consumer Bifurcation (sourced: Deloitte, CNBC)

### Tab 5: Macro Framework — DONE
- Growth/inflation quadrant with scored indicators
- Risk radar (6 tail risks with likelihood/impact)
- Sector synthesis table (cyclical + structural + risk = net signal)
- Framework summary with disclaimer

## What's Next / Known Issues
- Some LOGO holdings show N/A for fundamentals (esp. QXO — pre-revenue company, Yahoo has no data)
- FRED recently limited ICE BofA spread series to 3 years of history — charts may show less than 10 years
- yfinance can occasionally fail on rate limits if all 36 stocks + 7 periods each are fetched too fast
- FedWatch probabilities are hardcoded (no clean free API for CME futures-implied probabilities)
- Indicator table values are still hardcoded in HTML — hydration updates KPIs and charts but not the table cells themselves
- Build takes ~5 min due to multi-period stock history fetches; could optimize by batching or caching
