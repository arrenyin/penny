"""Free SEC EDGAR data (US-listed companies): annual financials, industry codes, and same-industry competitors."""

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

_tickers = None
_facts = {}  # cik -> (fetched_at, facts)
FACTS_CACHE_SECONDS = 24 * 3600

# Companies label the same item differently, so each field lists the tags to try, in order.
DURATION_TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForProceedsFromProductiveAssets", "PaymentsToAcquireOtherPropertyPlantAndEquipment",
              "PaymentsForCapitalImprovements"],
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "dividends_per_share": ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"],
}
INSTANT_TAGS = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "debt": ["LongTermDebt", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
             "DebtLongtermAndShorttermCombinedAmount", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
}


def _get(url, as_json=True):
    contact = os.environ.get("SEC_CONTACT_EMAIL", "")
    req = urllib.request.Request(url, headers={"User-Agent": f"Penny personal stock research {contact}".strip()})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp) if as_json else resp.read().decode("latin-1")


def _cik_maps():
    global _tickers
    if _tickers is None:
        raw = _get("https://www.sec.gov/files/company_tickers.json")
        by_ticker = {v["ticker"].upper(): v["cik_str"] for v in raw.values()}
        by_cik = {}
        for v in raw.values():  # the file lists each company's main ticker first
            by_cik.setdefault(v["cik_str"], (v["ticker"].upper(), v["title"]))
        _tickers = (by_ticker, by_cik)
    return _tickers


def cik_for(ticker):
    return _cik_maps()[0].get(ticker.upper().replace(".", "-"))


CACHE_DIR = Path(__file__).with_name(".cache")
WEEK = 7 * 24 * 3600


def _disk_cached(name, ttl, fetch):
    """Small JSON files the SEC updates rarely, kept on disk so they're downloaded about once a week."""
    path = CACHE_DIR / name
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_text())
    value = fetch()
    CACHE_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(value))
    return value


def _company_facts(cik):
    """A company's full filing history. These files are large and the SEC can be slow, so they're kept on disk for a day."""
    cached = _facts.get(cik)
    if cached and time.time() - cached[0] < FACTS_CACHE_SECONDS:
        return cached[1]
    path = CACHE_DIR / f"facts-{cik}.json"
    if path.exists() and time.time() - path.stat().st_mtime < FACTS_CACHE_SECONDS:
        facts = json.loads(path.read_text())
    else:
        facts = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json").get("facts", {}).get("us-gaap", {})
        CACHE_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(facts))
    _facts[cik] = (time.time(), facts)
    return facts


def _entries(facts, tag):
    units = facts.get(tag, {}).get("units", {})
    for unit in ("USD", "USD/shares", "shares"):
        if unit in units:
            return [e for e in units[unit] if str(e.get("form", "")).startswith("10-K")]
    return []


def _annual_by_end(facts, tags, duration):
    """{period end date: (filed date, value)} for full-year (or year-end) values, trying tags in order and filling gaps."""
    out = {}
    for tag in tags:
        found = {}
        for e in _entries(facts, tag):
            if duration:
                if "start" not in e:
                    continue
                days = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
                if not 350 <= days <= 380:
                    continue
            elif "start" in e:
                continue
            # A later filing restates earlier years; keep the most recently filed value.
            prev = found.get(e["end"])
            if prev is None or e["filed"] >= prev[0]:
                found[e["end"]] = (e["filed"], e["val"])
        for end, filed_val in found.items():
            out.setdefault(end, filed_val)
    return out


PER_SHARE = {"eps", "dividends_per_share"}


def annual_financials(ticker, splits=(), max_years=11):
    """List of fiscal years (oldest first), each a dict of raw values. None if the SEC has no data.

    splits: (date, ratio) pairs. Per-share values and share counts are reported on the share basis at the time of
    filing, so each one is adjusted for the splits that happened after it was filed.
    """
    cik = cik_for(ticker)
    if not cik:
        return None
    facts = _company_facts(cik)
    duration = {k: _annual_by_end(facts, tags, True) for k, tags in DURATION_TAGS.items()}
    instant = {k: _annual_by_end(facts, tags, False) for k, tags in INSTANT_TAGS.items()}

    # Fiscal year ends come from the income statement; balance-sheet values are matched to them.
    ends = sorted(set(duration["net_income"]) | set(duration["revenue"]))[-max_years:]
    def split_factor(filed):
        factor = 1.0
        for when, ratio in splits:
            if when > filed:
                factor *= ratio
        return factor

    years = []
    for end in ends:
        row = {"end": end, "year": int(end[:4])}
        for k, values in {**duration, **instant}.items():
            filed, val = values.get(end, (None, None))
            if val is not None and k in PER_SHARE:
                val /= split_factor(filed)
            elif val is not None and k == "shares":
                val *= split_factor(filed)
            row[k] = val
        years.append(row)
    return years or None


# ---------- industry and competitors ----------

def company_info(cik):
    """Name, industry code (SIC) and industry description."""
    def fetch():
        d = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        return {"name": d.get("name"), "sic": d.get("sic"), "industry": d.get("sicDescription")}
    return _disk_cached(f"info-{cik}.json", WEEK, fetch)


def ciks_in_industry(sic):
    """Every company filed under an industry code, from EDGAR's company browser (100 per page)."""
    def fetch():
        found = []
        for start in range(0, 1000, 100):
            page = _get("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=10-K&owner=include"
                        f"&count=100&output=atom&SIC={sic}&start={start}", as_json=False)
            ciks = [int(c) for c in re.findall(r"<cik>(\d+)</cik>", page)]
            found += ciks
            if len(ciks) < 100:
                break
        return found
    return _disk_cached(f"sic-{sic}.json", WEEK, fetch)


FRAME_CONCEPTS = {
    # field: (unit, tags tried in order)
    "revenue": ("USD", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]),
    "gross_profit": ("USD", ["GrossProfit"]),
    "operating_income": ("USD", ["OperatingIncomeLoss"]),
    "net_income": ("USD", ["NetIncomeLoss"]),
    "eps": ("USD-per-shares", ["EarningsPerShareDiluted"]),
    "operating_cash_flow": ("USD", ["NetCashProvidedByUsedInOperatingActivities"]),
    "capex": ("USD", ["PaymentsToAcquirePropertyPlantAndEquipment"]),
    "shares": ("shares", ["WeightedAverageNumberOfDilutedSharesOutstanding"]),
    "dividends_per_share": ("USD-per-shares", ["CommonStockDividendsPerShareDeclared"]),
}


def _frame(tag, unit, year):
    """One figure for every company for a calendar year: {cik: value}."""
    def fetch():
        try:
            d = _get(f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/{unit}/CY{year}.json")
        except urllib.error.HTTPError:
            return {}
        return {str(r["cik"]): r["val"] for r in d.get("data", [])}
    return _disk_cached(f"frame-{tag}-{unit}-{year}.json", WEEK, fetch)


def latest_full_year():
    """The most recent calendar year that most companies have finished reporting (annual reports come out by spring)."""
    today = date.today()
    return today.year - 1 if today.month >= 5 else today.year - 2


def frame_values(cik, year=None):
    """A company's main yearly figures from the all-company files (no per-company download needed)."""
    year = year or latest_full_year()
    out = {}
    for field, (unit, tags) in FRAME_CONCEPTS.items():
        out[field] = next((v for tag in tags if (v := _frame(tag, unit, year).get(str(cik))) is not None), None)
    return out


def competitors(cik, limit=5):
    """The largest listed companies (by last year's revenue) in the same industry, widening to related codes if needed.

    Returns [(cik, ticker, name)].
    """
    sic = company_info(cik).get("sic")
    if not sic:
        return []
    _, by_cik = _cik_maps()
    year = latest_full_year()
    revenue = {}
    for tag in FRAME_CONCEPTS["revenue"][1]:
        for c, v in _frame(tag, "USD", year).items():
            revenue.setdefault(int(c), v)

    def ranked(codes):
        pool = {c for code in codes for c in ciks_in_industry(code)}
        pool.discard(cik)
        listed = [c for c in pool if c in by_cik and revenue.get(c)]
        return sorted(listed, key=lambda c: revenue[c], reverse=True)

    best = ranked([sic])
    if len(best) < limit:  # e.g. 2080 Beverages -> also 2082 Malt beverages, 2086 Soft drinks...
        related = [f"{sic[:3]}{d}" for d in range(10) if f"{sic[:3]}{d}" != sic]
        best += [c for c in ranked(related) if c not in best]
    return [(c, *by_cik[c]) for c in best[:limit]]
