"""Penny: stock research with 10 years of filings, peer comparisons, a return calculator and Claude's plain-English take."""

import json
import os
import re
import time
from pathlib import Path

import anthropic
from flask import Flask, Response, jsonify, request, send_from_directory

# Load KEY=value lines from .env (if present) so keys don't have to be exported by hand.
env_file = Path(__file__).with_name(".env")
if env_file.exists():
    for line in env_file.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))

import analysis  # noqa: E402  (reads SEC_CONTACT_EMAIL from the environment loaded above)

MODEL = "claude-haiku-5-5"

SYSTEM_PROMPT = """You are Penny, a stock researcher who explains things to beginners and older investors.
The reader is looking at a page full of numbers about this company: today's ratios compared with the company's
own history and with its peers, 10 years of results, and growth rates. Your job is to read those numbers for them
and say what they add up to.

Write the way a patient, knowledgeable friend would talk:
- Short sentences and everyday words. Round numbers ("about $4.9 trillion", "roughly 28 times").
- The first time you use a term, explain it in a few words, e.g. "P/E (how many dollars you pay for each $1 of
  yearly profit)".
- Accurate and specific: cite the actual numbers, especially comparisons ("P/E is 28, above its usual 25 and
  the peers' 21").

How to write the first summary (keep it under about 250 words):
1. Start with a single line exactly like: VERDICT: Undervalued | Fairly valued | Overvalued (confidence: low/medium/high)
   Use exactly one of those three phrases with nothing added. Always give it, even for a close call (low
   confidence then). Judging whether the price looks high or low for the business is the job; whether to buy
   depends on the reader's own situation.
2. "## The short answer" - 2 or 3 plain sentences.
3. "## What the numbers show" - 3 to 5 bullets, each built on a comparison: today vs. its usual, vs. peers, or a
   multi-year trend. Mention how the business held up through events like COVID (2020) or rate hikes (2022) if
   the yearly data shows it.
4. "## Watch out for" - 2 bullets.

Rules:
- Use only the data provided plus general, well-established knowledge about the company and its industry.
  Don't invent news, events or figures. If data is missing, say so briefly.
- Honest and calm. No hype, no fear, no filler.
- For follow-up questions, answer briefly in the same plain style using the same data.
- End the first summary with one short line noting this is research, not advice tailored to anyone's finances.

DATA (JSON):
"""

client = anthropic.Anthropic()
app = Flask(__name__, static_folder="static")
app.json.sort_keys = False

_cache = {}  # ticker -> (fetched_at, data)
CACHE_SECONDS = 600
CACHE_MAX_ENTRIES = 100

# Limits on Claude calls, since every visitor's question is billed to this app's API key.
PER_VISITOR_PER_HOUR = int(os.environ.get("SUMMARY_LIMIT_PER_HOUR", 20))
PER_DAY_TOTAL = int(os.environ.get("SUMMARY_LIMIT_PER_DAY", 500))
MAX_MESSAGES = 20
MAX_MESSAGE_CHARS = 2000
_visitor_calls = {}  # visitor -> [timestamps in the last hour]
_daily = {"day": None, "count": 0}


def visitor_id():
    # Render's proxy appends the address it actually received from to X-Forwarded-For. Earlier entries come from the
    # visitor and can be faked, so only the last one is trusted.
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[-1].strip() or request.remote_addr or "unknown"


def over_limit():
    """Records a Claude call and returns a message if this visitor or the whole site is over its limit."""
    now = time.time()
    today = time.strftime("%Y-%m-%d")
    if _daily["day"] != today:
        _daily.update(day=today, count=0)
    if _daily["count"] >= PER_DAY_TOTAL:
        return "Penny has answered all the questions she can for today. Try again tomorrow."
    who = visitor_id()
    recent = [t for t in _visitor_calls.get(who, []) if now - t < 3600]
    if len(recent) >= PER_VISITOR_PER_HOUR:
        return "You've asked Penny a lot this hour. Try again in a little while."
    recent.append(now)
    _visitor_calls[who] = recent
    _daily["count"] += 1
    return None


def valid_messages(messages):
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        return False
    for i, m in enumerate(messages):
        if not isinstance(m, dict) or m.get("role") != ("user" if i % 2 == 0 else "assistant"):
            return False
        if not isinstance(m.get("content"), str) or not m["content"].strip():
            return False
        if m["role"] == "user" and len(m["content"]) > MAX_MESSAGE_CHARS:
            return False
    return messages[-1]["role"] == "user"


def clean_ticker(raw):
    ticker = (raw or "").strip().upper()
    return ticker if re.fullmatch(r"[A-Z0-9.\-^=]{1,12}", ticker) else None


def get_data(ticker):
    cached = _cache.get(ticker)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    data = analysis.build(ticker)
    if data:
        if len(_cache) >= CACHE_MAX_ENTRIES:
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
        # Kept for the follow-up questions either way, but a load without SEC history expires at once so a retry refetches.
        _cache[ticker] = (time.time() if data["has_history"] else 0, data)
    return data


def snapshot_for_claude(data):
    """The numbers the page shows, without the long price series."""
    r = lambda v: round(v, 4) if isinstance(v, float) else v
    keep = ("year", "revenue", "net_income", "fcf", "eps", "dividends_per_share", "shares",
            "gross_margin", "op_margin", "net_margin", "roic", "pe", "ps", "pfcf", "debt_years")
    total = {p["date"][:4]: p["total"] for p in data["prices"]}
    spy = {p["date"][:4]: p["total"] for p in data["benchmark"]}
    years = sorted(set(total) & set(spy))
    returns = {}
    for n in (1, 3, 5, 10):
        if len(years) > n:
            a, b = years[-n - 1], years[-1]
            returns[f"{n}y"] = {"stock": r(total[b] / total[a] - 1), "sp500": r(spy[b] / spy[a] - 1)}
    return {
        "company": {k: data[k] for k in ("ticker", "name", "sector", "industry", "price", "market_cap", "summary")},
        "analyst": data["analyst"],
        "today": {k: r(v) for k, v in data["today"].items()},
        "usual_median": {n: {k: r(v) for k, v in u.items()} for n, u in data["usual"].items()},
        "growth_per_year": {n: {k: r(v) for k, v in g.items()} for n, g in data["growth"].items()},
        "peers": {"median": {k: r(v) for k, v in data["peers"]["median"].items()},
                  "companies": [{"ticker": p["ticker"], **{k: r(v) for k, v in p["values"].items()}}
                                for p in data["peers"]["companies"]]},
        "yearly": [{k: r(y[k]) for k in keep} for y in data["yearly"]],
        "total_return_vs_sp500_calendar_years": returns,
    }


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/stock/<raw>")
def stock(raw):
    ticker = clean_ticker(raw)
    if not ticker:
        return jsonify(error="That doesn't look like a stock symbol."), 400
    try:
        data = get_data(ticker)
    except Exception as e:  # yfinance and the SEC raise a variety of network/parsing errors
        return jsonify(error=f"Couldn't load data for {ticker}: {e}"), 502
    if not data:
        return jsonify(error=f"No market data found for {ticker}."), 404
    return jsonify(data)


@app.post("/api/analyze")
def analyze():
    body = request.get_json(force=True)
    ticker = clean_ticker(body.get("ticker"))
    messages = body.get("messages", [])
    data = _cache.get(ticker, (0, None))[1] if ticker else None
    # Checked here, while the request context is available; only counts calls that will actually reach Claude.
    limited = over_limit() if data and valid_messages(messages) and os.environ.get("ANTHROPIC_API_KEY") else None

    def sse(payload):
        return f"data: {json.dumps(payload)}\n\n"

    def generate():
        if not os.environ.get("ANTHROPIC_API_KEY"):
            yield sse({"error": "ANTHROPIC_API_KEY is not set. Add it to .env and restart the server."})
        elif not data:
            yield sse({"error": "Load the stock's data first."})
        elif not valid_messages(messages):
            yield sse({"error": f"Questions can be up to {MAX_MESSAGE_CHARS} characters, and up to {MAX_MESSAGES // 2} per stock."})
        elif limited:
            yield sse({"error": limited})
        else:
            try:
                with client.messages.stream(
                    model=MODEL,
                    max_tokens=16000,
                    system=SYSTEM_PROMPT + json.dumps(snapshot_for_claude(data)),
                    messages=messages,
                    output_config={"effort": "low"},
                ) as stream:
                    for text in stream.text_stream:
                        yield sse({"text": text})
                    if stream.get_final_message().stop_reason == "refusal":
                        yield sse({"error": "Penny declined to answer that one. Try rephrasing."})
            except anthropic.AuthenticationError:
                yield sse({"error": "Invalid ANTHROPIC_API_KEY."})
            except anthropic.RateLimitError:
                yield sse({"error": "Rate limited. Wait a moment and try again."})
            except anthropic.APIStatusError as e:
                yield sse({"error": f"API error {e.status_code}: {e.message}"})
            except anthropic.APIConnectionError:
                yield sse({"error": "Could not reach the Claude API."})
        yield "data: [DONE]\n\n"

    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 5050)), debug=True, threaded=True)
