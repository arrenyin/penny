"""Prices from Tiingo (one free API key): daily history with dividends and splits, latest close, company name."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

_cache = {}  # url -> (fetched_at, value)


class MarketDataError(Exception):
    pass


def tiingo(path, ttl=6 * 3600, **params):
    key = os.environ.get("TIINGO_API_KEY")
    if not key:
        raise MarketDataError("TIINGO_API_KEY is not set.")
    url = f"https://api.tiingo.com{path}?{urllib.parse.urlencode(params)}"
    cached = _cache.get(url)
    if cached and time.time() - cached[0] < ttl:
        return cached[1]
    req = urllib.request.Request(url, headers={"Authorization": f"Token {key}", "Content-Type": "application/json",
                                               "User-Agent": "Penny stock research"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            value = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 429:
            raise MarketDataError("The price service is busy (too many requests this hour). Try again later.")
        if e.code in (401, 403):
            raise MarketDataError(f"The price service refused the request ({e.code}). Check TIINGO_API_KEY.")
        raise MarketDataError(f"Price service error {e.code}.")
    _cache[url] = (time.time(), value)
    return value


def meta(ticker):
    """Name and description, or None for an unknown symbol."""
    return tiingo(f"/tiingo/daily/{urllib.parse.quote(ticker)}", ttl=24 * 3600)


def latest_close(ticker):
    rows = tiingo(f"/tiingo/daily/{urllib.parse.quote(ticker)}/prices", ttl=3 * 3600) or []
    return rows[-1]["close"] if rows else None


def daily_prices(ticker, start="1990-01-01"):
    """Every trading day: raw close, total-return close (splits and dividends) and that day's split factor."""
    rows = tiingo(f"/tiingo/daily/{urllib.parse.quote(ticker)}/prices", startDate=start,
                  columns="date,close,adjClose,splitFactor") or []
    return [(r["date"][:10], r["close"], r["adjClose"], r.get("splitFactor") or 1.0) for r in rows if r.get("close")]


def monthly_prices(ticker):
    """[(YYYY-MM-DD, split-adjusted close, total-return close)] at each month end, plus [(date, split ratio)].

    Tiingo's raw close isn't adjusted for splits, so it's divided by the splits that happened later, putting old
    prices on today's share basis (matching how per-share figures from filings are adjusted).
    """
    days = daily_prices(ticker)
    splits = [(d, f) for d, _, _, f in days if f and f != 1.0]
    month_end = {}
    for d, close, adj, _ in days:
        month_end[d[:7]] = (d, close, adj)
    rows = []
    for _, (d, close, adj) in sorted(month_end.items()):
        factor = 1.0
        for when, ratio in splits:
            if when > d:
                factor *= ratio
        rows.append((d, close / factor, adj))
    return rows, splits
