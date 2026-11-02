"""Turns raw filings and prices into yearly metrics, 3/5/10-year comparisons, growth rates and peer figures."""

import math
import statistics
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

import sec

WINDOWS = (3, 5, 10)


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def div(a, b):
    a, b = num(a), num(b)
    return a / b if a is not None and b not in (None, 0) else None


# Ratio metrics shown with today / usual / peers. "better" says which direction is favourable, for the labels.
RATIOS = [
    # group, key, plain name, technical term, explanation, format, better
    ("Is the price high or low?", "pe", "Price vs. profit", "P/E",
     "Dollars you pay for each $1 of yearly profit.", "x", "lower"),
    ("Is the price high or low?", "ps", "Price vs. sales", "Price / Sales",
     "Dollars you pay for each $1 of yearly sales.", "x", "lower"),
    ("Is the price high or low?", "pfcf", "Price vs. cash left over", "Price / Free cash flow",
     "Dollars you pay for each $1 of cash the business has left after running and investing in itself.", "x", "lower"),
    ("Is the price high or low?", "div_yield", "Yearly dividend", "Dividend yield",
     "Cash paid to shareholders each year, as a percent of the share price.", "pct", "higher"),
    ("Is it a quality business?", "gross_margin", "Profit on each sale", "Gross margin",
     "Out of each $1 of sales, what's left after the cost of making the product.", "pct", "higher"),
    ("Is it a quality business?", "op_margin", "Profit from running the business", "Operating margin",
     "Out of each $1 of sales, what's left after all day-to-day costs.", "pct", "higher"),
    ("Is it a quality business?", "net_margin", "Final profit on each sale", "Net margin",
     "Out of each $1 of sales, what ends up as profit after everything, including taxes.", "pct", "higher"),
    ("Is it a quality business?", "roic", "Return on money invested", "ROIC",
     "Profit made on all the money put into the business (by owners and lenders). Many investors see this as the best single test of a good business.",
     "pct", "higher"),
    ("Is it financially safe?", "debt_years", "Years to pay off debt", "Net debt / operating income",
     "How many years of operating profit it would take to pay off debt (after using its cash). Zero or less means it has more cash than debt.",
     "years", "lower"),
    ("Is it financially safe?", "interest_cover", "Interest covered", "Interest coverage",
     "How many times over its operating profit covers the interest on its debt.", "x", "higher"),
    ("Is it good to shareholders?", "payout", "Share of profit paid as dividends", "Payout ratio",
     "The part of each year's profit paid out as dividends. Very high can mean the dividend is at risk.", "pct", None),
]

# Growth measured as yearly average rate (CAGR) over each window.
GROWTH = [
    ("revenue", "Sales", "Revenue"),
    ("eps", "Profit per share", "EPS (diluted)"),
    ("fcf_ps", "Cash left over per share", "Free cash flow per share"),
    ("dividends_per_share", "Dividend per share", "Dividends per share"),
    ("shares", "Number of shares", "Diluted shares outstanding"),
]

# Yearly series charted on the page.
SERIES = ["revenue", "net_income", "fcf", "eps", "fcf_ps", "dividends_per_share", "shares",
          "gross_margin", "op_margin", "net_margin", "roic", "pe", "ps", "pfcf", "debt", "cash"]


def yearly_metrics(years, monthly_close):
    """Adds derived ratios to each fiscal year, valuing it at the share price on its fiscal year end."""
    out = []
    for y in years:
        price = None
        for when, close in monthly_close:
            if when <= y["end"]:
                price = close
        op = y["operating_income"]
        if op is None and y["pretax_income"] is not None:
            op = y["pretax_income"] + (y["interest_expense"] or 0)
        tax_rate = div(y["income_tax"], y["pretax_income"])
        tax_rate = min(max(tax_rate, 0), 0.35) if tax_rate is not None else 0.21
        fcf = y["operating_cash_flow"] - y["capex"] if None not in (y["operating_cash_flow"], y["capex"]) else None
        invested = None
        if y["equity"] is not None:
            invested = y["equity"] + (y["debt"] or 0) - (y["cash"] or 0)
        net_debt = (y["debt"] or 0) - (y["cash"] or 0)
        market_cap = price * y["shares"] if price and y["shares"] else None
        eps = y["eps"]
        out.append({
            "year": y["year"], "end": y["end"], "price": price,
            "revenue": y["revenue"], "net_income": y["net_income"], "fcf": fcf, "eps": eps,
            "fcf_ps": div(fcf, y["shares"]), "dividends_per_share": y["dividends_per_share"], "shares": y["shares"],
            "debt": y["debt"], "cash": y["cash"],
            "gross_margin": div(y["gross_profit"], y["revenue"]),
            "op_margin": div(op, y["revenue"]),
            "net_margin": div(y["net_income"], y["revenue"]),
            "roic": div(op * (1 - tax_rate), invested) if op is not None and invested and invested > 0 else None,
            "debt_years": div(net_debt, op) if op and op > 0 else None,
            "interest_cover": div(op, y["interest_expense"]) if y["interest_expense"] else None,
            "payout": div(y["dividends_per_share"], eps) if eps and eps > 0 else None,
            "pe": div(price, eps) if eps and eps > 0 else None,
            "ps": div(market_cap, y["revenue"]),
            "pfcf": div(market_cap, fcf) if fcf and fcf > 0 else None,
            "div_yield": div(y["dividends_per_share"], price) if y["dividends_per_share"] is not None else 0,
        })
    return out


def window_summary(yearly):
    """Median of each ratio and yearly growth rate of each growth item, over the last 3, 5 and 10 years."""
    usual, growth = {}, {}
    for n in WINDOWS:
        recent = yearly[-n:]
        usual[n] = {}
        for _, key, *_rest in RATIOS:
            values = [y[key] for y in recent if y[key] is not None]
            usual[n][key] = statistics.median(values) if len(values) >= min(n, 3) else None
        growth[n] = {}
        for key, *_ in GROWTH:
            if len(yearly) <= n:
                growth[n][key] = None
                continue
            start, end = yearly[-n - 1][key], yearly[-1][key]
            growth[n][key] = (end / start) ** (1 / n) - 1 if start and end and start > 0 and end > 0 else None
    return usual, growth


def today_values(info, latest, price):
    """Current ratios: price-based ones use today's price, business ones use the latest fiscal year."""
    shares = num(info.get("sharesOutstanding")) or (latest or {}).get("shares")
    market_cap = num(info.get("marketCap")) or (price * shares if shares else None)
    latest = latest or {}
    dps = num(info.get("dividendRate"))
    values = {
        "pe": num(info.get("trailingPE")) or div(price, latest.get("eps")),
        "ps": num(info.get("priceToSalesTrailing12Months")) or div(market_cap, latest.get("revenue")),
        "pfcf": div(market_cap, latest.get("fcf")) if (latest.get("fcf") or 0) > 0 else None,
        "div_yield": div(dps, price) if dps is not None else (num(info.get("dividendYield")) or 0) / 100,
    }
    for key in ("gross_margin", "op_margin", "net_margin", "roic", "debt_years", "interest_cover", "payout"):
        values[key] = latest.get(key)
    if not latest:  # no SEC history (e.g. non-US company): fall back to Yahoo's snapshot
        values.update(gross_margin=num(info.get("grossMargins")), op_margin=num(info.get("operatingMargins")),
                      net_margin=num(info.get("profitMargins")), payout=num(info.get("payoutRatio")))
    return values


def peer_snapshot(ticker):
    info = yf.Ticker(ticker).info or {}
    price = num(info.get("currentPrice") or info.get("regularMarketPrice"))
    if price is None:
        return None
    fcf, mcap = num(info.get("freeCashflow")), num(info.get("marketCap"))
    dps = num(info.get("dividendRate"))
    return {
        "ticker": ticker, "name": info.get("shortName") or ticker, "market_cap": mcap,
        "values": {
            "pe": num(info.get("trailingPE")),
            "ps": num(info.get("priceToSalesTrailing12Months")),
            "pfcf": div(mcap, fcf) if fcf and fcf > 0 else None,
            "div_yield": div(dps, price) if dps is not None else 0,
            "gross_margin": num(info.get("grossMargins")),
            "op_margin": num(info.get("operatingMargins")),
            "net_margin": num(info.get("profitMargins")),
            "revenue_growth": num(info.get("revenueGrowth")),
            "payout": num(info.get("payoutRatio")),
        },
    }


def peers_for(ticker, industry_key, limit=5):
    try:
        top = yf.Industry(industry_key).top_companies if industry_key else None
    except Exception:
        top = None
    symbols = [s for s in (list(top.index) if top is not None else []) if s.upper() != ticker.upper()][:limit]
    with ThreadPoolExecutor(max_workers=limit or 1) as pool:
        snaps = [s for s in pool.map(_safe_peer, symbols) if s]
    medians = {}
    for _, key, *_ in RATIOS:
        values = [p["values"].get(key) for p in snaps if p["values"].get(key) is not None]
        medians[key] = statistics.median(values) if len(values) >= 2 else None
    return {"companies": snaps, "median": medians}


def _safe_peer(symbol):
    try:
        return peer_snapshot(symbol)
    except Exception:
        return None


def monthly(ticker, period="max"):
    """[(YYYY-MM-DD, close, total-return close)] month by month."""
    h = yf.Ticker(ticker).history(period=period, interval="1mo", auto_adjust=False)
    rows = []
    for when, r in h.iterrows():
        close, adj = num(r.get("Close")), num(r.get("Adj Close"))
        if close is not None and adj is not None:
            rows.append((when.strftime("%Y-%m-%d"), close, adj))
    return rows


def build(ticker):
    t = yf.Ticker(ticker)
    info = t.info or {}
    price = num(info.get("currentPrice") or info.get("regularMarketPrice"))
    if price is None:
        return None

    prices = monthly(ticker)
    splits = [(d.strftime("%Y-%m-%d"), float(r)) for d, r in t.splits.items()] if t.splits is not None else []
    try:
        raw = sec.annual_financials(ticker, splits)
    except Exception:
        raw = None
    yearly = yearly_metrics(raw, [(d, c) for d, c, _ in prices]) if raw else []
    usual, growth = window_summary(yearly) if yearly else ({}, {})

    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector"), "industry": info.get("industry"),
        "currency": info.get("currency", "USD"),
        "summary": (info.get("longBusinessSummary") or "")[:600],
        "price": price, "market_cap": num(info.get("marketCap")),
        "analyst": {"target_mean": num(info.get("targetMeanPrice")), "target_low": num(info.get("targetLowPrice")),
                    "target_high": num(info.get("targetHighPrice")), "rating": info.get("recommendationKey"),
                    "num_analysts": info.get("numberOfAnalystOpinions")},
        "has_history": bool(yearly),
        "yearly": yearly,
        "today": today_values(info, yearly[-1] if yearly else None, price),
        "usual": usual, "growth": growth,
        "peers": peers_for(ticker, info.get("industryKey")),
        "prices": [{"date": d, "close": round(c, 4), "total": round(a, 4)} for d, c, a in prices],
        "benchmark": [{"date": d, "total": round(a, 4)} for d, _, a in monthly("SPY")],
        "ratios": [{"group": g, "key": k, "plain": p, "term": t_, "explain": e, "fmt": f, "better": b}
                   for g, k, p, t_, e, f, b in RATIOS],
        "growth_items": [{"key": k, "plain": p, "term": t_} for k, p, t_ in GROWTH],
    }
