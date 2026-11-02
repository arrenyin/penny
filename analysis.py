"""Turns raw filings and prices into yearly metrics, 3/5/10-year comparisons, growth rates and peer figures."""

import math
import statistics
from concurrent.futures import ThreadPoolExecutor

import market
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


def ratios_from(fig, price):
    """Ratios from one year of figures and a share price (used for competitors, from the SEC's all-company files)."""
    eps, dps, shares = fig.get("eps"), fig.get("dividends_per_share"), fig.get("shares")
    market_cap = price * shares if price and shares else None
    fcf = fig["operating_cash_flow"] - fig["capex"] if None not in (fig.get("operating_cash_flow"), fig.get("capex")) else None
    return market_cap, {
        "pe": div(price, eps) if eps and eps > 0 else None,
        "ps": div(market_cap, fig.get("revenue")),
        "pfcf": div(market_cap, fcf) if fcf and fcf > 0 else None,
        "div_yield": div(dps, price) if dps else 0,
        "gross_margin": div(fig.get("gross_profit"), fig.get("revenue")),
        "op_margin": div(fig.get("operating_income"), fig.get("revenue")),
        "net_margin": div(fig.get("net_income"), fig.get("revenue")),
        "payout": div(dps, eps) if dps and eps and eps > 0 else None,
    }


def today_values(latest, price):
    """Today's ratios: today's price against the latest fiscal year's results from the filings."""
    latest = latest or {}
    market_cap = price * latest["shares"] if latest.get("shares") else None
    values = {
        "pe": div(price, latest.get("eps")) if (latest.get("eps") or 0) > 0 else None,
        "ps": div(market_cap, latest.get("revenue")),
        "pfcf": div(market_cap, latest.get("fcf")) if (latest.get("fcf") or 0) > 0 else None,
        "div_yield": div(latest.get("dividends_per_share"), price) if latest.get("dividends_per_share") else 0,
    }
    for key in ("gross_margin", "op_margin", "net_margin", "roic", "debt_years", "interest_cover", "payout"):
        values[key] = latest.get(key)
    return values, market_cap


def peer_snapshot(cik, ticker, name):
    price = market.latest_close(ticker)
    if not price:
        return None
    market_cap, values = ratios_from(sec.frame_values(cik), price)
    return {"ticker": ticker, "name": name, "market_cap": market_cap, "values": values}


def peers_for(cik, limit=5):
    try:
        # A few extra candidates, since some (e.g. odd fiscal years) are missing from the SEC's calendar-year files.
        found = sec.competitors(cik, limit + 3) if cik else []
    except Exception:  # the SEC is sometimes slow; the page still works without competitors
        found = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        snaps = [s for s in pool.map(lambda c: _safe_peer(*c), found) if s and s["market_cap"]][:limit]
    medians = {}
    for _, key, *_ in RATIOS:
        values = [p["values"].get(key) for p in snaps if p["values"].get(key) is not None]
        medians[key] = statistics.median(values) if len(values) >= 2 else None
    return {"companies": snaps, "median": medians}


def _safe_peer(cik, ticker, name):
    try:
        return peer_snapshot(cik, ticker, name)
    except (market.MarketDataError, OSError):
        return None


def build(ticker):
    info = market.meta(ticker)
    if not info:
        return None
    prices, splits = market.monthly_prices(ticker)
    if not prices:
        return None
    benchmark, _ = market.monthly_prices("SPY")
    price = market.latest_close(ticker) or prices[-1][1]

    cik = None
    try:
        cik = sec.cik_for(ticker)
        raw = sec.annual_financials(ticker, splits) if cik else None
        company = sec.company_info(cik) if cik else {}
    except Exception:  # the SEC is sometimes slow or unreachable; the page still works without history
        raw, company = None, {}
    yearly = yearly_metrics(raw, [(d, c) for d, c, _ in prices]) if raw else []
    usual, growth = window_summary(yearly) if yearly else ({}, {})
    today, market_cap = today_values(yearly[-1] if yearly else None, price)
    # Months are labeled by their first day so the stock and the S&P 500 line up month for month.
    month = lambda d: d[:7] + "-01"

    return {
        "ticker": ticker,
        "name": info.get("name") or company.get("name") or ticker,
        "sector": None, "industry": (company.get("industry") or "").title() or None,
        "currency": "USD",
        "summary": (info.get("description") or "")[:600],
        "price": price, "market_cap": market_cap,
        "analyst": {"target_mean": None, "target_low": None, "target_high": None, "rating": None, "num_analysts": None},
        "has_history": bool(yearly),
        "yearly": yearly,
        "today": today,
        "usual": usual, "growth": growth,
        "peers": peers_for(cik),
        "prices": [{"date": month(d), "close": round(c, 4), "total": round(a, 4)} for d, c, a in prices],
        "benchmark": [{"date": month(d), "total": round(a, 4)} for d, _, a in benchmark],
        "ratios": [{"group": g, "key": k, "plain": p, "term": t_, "explain": e, "fmt": f, "better": b}
                   for g, k, p, t_, e, f, b in RATIOS],
        "growth_items": [{"key": k, "plain": p, "term": t_} for k, p, t_ in GROWTH],
    }
