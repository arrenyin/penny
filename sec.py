"""Annual financials from SEC EDGAR's free XBRL "company facts" API (US-listed companies only)."""

import json
import os
import time
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


def _get(url):
    contact = os.environ.get("SEC_CONTACT_EMAIL", "")
    req = urllib.request.Request(url, headers={"User-Agent": f"Penny personal stock research {contact}".strip()})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def cik_for(ticker):
    global _tickers
    if _tickers is None:
        raw = _get("https://www.sec.gov/files/company_tickers.json")
        _tickers = {v["ticker"].upper(): v["cik_str"] for v in raw.values()}
    return _tickers.get(ticker.upper().replace(".", "-"))


CACHE_DIR = Path(__file__).with_name(".cache")


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
