"""
AI-powered stock data fetch agent.

Uses Google Gemini API (free tier, gemini-2.0-flash) with built-in Google Search
grounding to resolve natural-language queries to ticker symbols, then downloads
OHLCV data from yfinance (primary) or stooq.com (fallback).

Setup
-----
1. Get a free Gemini API key: https://aistudio.google.com/apikey
2. Set GEMINI_API_KEY in your environment, a .env file, or pass directly.

Python deps: google-genai, yfinance, requests, pandas
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from typing import Callable, Literal, cast

import pandas as pd
import requests
import yfinance as yf

# ── Ticker cache ──────────────────────────────────────────────────────────────
# Confirmed tickers are persisted here so future lookups skip the AI entirely.
_CACHE_PATH = Path(__file__).parent / "ticker_cache.json"

_BASE_MAP: dict[str, str] = {
    "NIFTY":       "^NSEI",
    "NIFTY50":     "^NSEI",
    "NIFTY 50":    "^NSEI",
    "BANKNIFTY":   "^NSEBANK",
    "BANK NIFTY":  "^NSEBANK",
    "NIFTYNEXT50": "^NSMIDCP",
    "NIFTYIT":     "^CNXIT",
    "SENSEX":      "^BSESN",
    "GOLDBEES":    "GOLDBEES.NS",
    "SILVERBEES":  "SILVERBEES.NS",
}


def _load_cache() -> dict[str, str]:
    if _CACHE_PATH.exists():
        try:
            data = json.loads(_CACHE_PATH.read_text())
            return {**data, **_BASE_MAP}
        except Exception:
            pass
    return dict(_BASE_MAP)


def _save_cache(cache: dict[str, str]) -> None:
    to_save = {k: v for k, v in cache.items() if k not in _BASE_MAP}
    _CACHE_PATH.write_text(json.dumps(to_save, indent=2))


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class ResolveResult:
    """Returned by FetchDataAgent.resolve()."""
    status: Literal["direct", "candidates", "failed"]
    # "direct": one confident match
    ticker: str | None = None
    ticker_name: str | None = None
    # "candidates": user must choose
    candidates: list[dict] | None = field(default=None)
    # human-readable summary of what happened
    message: str = ""


# ── Agent ─────────────────────────────────────────────────────────────────────

class FetchDataAgent:
    """
    Resolves natural-language stock/instrument queries to OHLCV DataFrames.

    Parameters
    ----------
    api_key : str
        Google Gemini API key.  Free key: https://aistudio.google.com/apikey
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError(
                "Gemini API key is required.\n"
                "Get a free key at https://aistudio.google.com/apikey\n"
                "Then set GEMINI_API_KEY in your environment."
            )
        self.api_key = api_key
        self._cache = _load_cache()

    # ── Yahoo Finance symbol search ────────────────────────────────────────

    def _yf_search(self, query: str) -> list[dict]:
        """Return up to 5 yfinance search results (raw dicts)."""
        try:
            results = yf.Search(query, max_results=5).quotes
            return [r for r in (results or []) if r.get("symbol")]
        except Exception:
            return []

    # ── Gemini + Google Search grounding ──────────────────────────────────

    def _gemini_resolve(self, query: str, log: Callable[[str], None]) -> ResolveResult:
        """
        Ask Gemini (with Google Search grounding) to find the yfinance ticker.
        Returns a ResolveResult with status "direct", "candidates", or "failed".
        """
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise RuntimeError(
                "google-genai not installed.  Run: pip install google-genai"
            )

        client = genai.Client(api_key=self.api_key)

        prompt = (
            f'Search the web and find the yfinance ticker symbol for: "{query}"\n\n'
            "Rules for ticker format:\n"
            "- Indian NSE equities  → append .NS   (RELIANCE → RELIANCE.NS)\n"
            "- Indian BSE equities  → append .BO\n"
            "- Indian indices       → NIFTY 50 = ^NSEI | BANK NIFTY = ^NSEBANK | SENSEX = ^BSESN\n"
            "- US stocks            → standard symbol (AAPL, MSFT, GOOGL)\n"
            "- US ETFs              → SPY, QQQ, GLD, etc.\n"
            "- Global indices       → S&P 500 = ^GSPC | NASDAQ = ^IXIC | Dow Jones = ^DJI\n\n"
            "If you are confident there is ONE correct answer, respond with ONLY this JSON:\n"
            '{"status":"direct","ticker":"TICKER_SYMBOL","name":"Full Instrument Name"}\n\n'
            "If the query is ambiguous or there are multiple plausible matches, respond with ONLY:\n"
            '{"status":"candidates","candidates":[\n'
            '  {"ticker":"T1","name":"Name 1","description":"Exchange · type"},\n'
            '  {"ticker":"T2","name":"Name 2","description":"Exchange · type"}\n'
            "]}\n\n"
            "Output ONLY valid JSON — no markdown fences, no explanation."
        )

        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0,
                ),
            )
            raw = (response.text or "").strip()
            log(f"AI response: {raw[:300]}")

            # Strip markdown code fences if the model added them
            raw = re.sub(r"^```(?:json)?\s*", "", raw).strip()
            raw = re.sub(r"\s*```$", "", raw).strip()

            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return ResolveResult(
                    status="failed",
                    message=f"Could not parse AI response: {raw[:120]}"
                )

            data = json.loads(match.group())

            if data.get("status") == "direct":
                ticker = str(data.get("ticker", "")).strip().upper()
                name = str(data.get("name", ticker))
                return ResolveResult(
                    status="direct",
                    ticker=ticker,
                    ticker_name=name,
                    message=f"AI found: {name} ({ticker})",
                )

            if data.get("status") == "candidates":
                candidates = data.get("candidates", [])
                return ResolveResult(
                    status="candidates",
                    candidates=candidates,
                    message=f"AI found {len(candidates)} possible matches",
                )

            return ResolveResult(
                status="failed",
                message=f"Unexpected AI response structure: {raw[:120]}"
            )

        except Exception as exc:
            return ResolveResult(status="failed", message=f"Gemini API error: {exc}")

    # ── yfinance data download ─────────────────────────────────────────────

    def _yf_fetch(self, ticker: str, from_date: str) -> pd.DataFrame | None:
        try:
            df = cast(
                pd.DataFrame,
                yf.download(
                    ticker,
                    start=from_date,
                    end=str(date.today()),
                    progress=False,
                    auto_adjust=True,
                ),
            )
            if df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index.name = "Date"
            df = cast(pd.DataFrame, df[["Open", "High", "Low", "Close", "Volume"]].round(2))
            return df if not df.empty else None
        except Exception:
            return None

    # ── stooq.com fallback ─────────────────────────────────────────────────

    _YF_TO_STOOQ: dict[str, str] = {
        "^NSEI":    "^NF",
        "^NSEBANK": "^BNF",
        "^BSESN":   "^SENSEX",
        "^GSPC":    "^SPX",
        "^IXIC":    "^NDX",
        "^DJI":     "^DJI",
    }

    def _to_stooq_ticker(self, yf_ticker: str) -> str:
        t = yf_ticker.upper()
        if t in self._YF_TO_STOOQ:
            return self._YF_TO_STOOQ[t]
        if t.endswith(".NS"):
            return t.replace(".NS", ".IN")
        if t.endswith(".BO"):
            return t.replace(".BO", ".IN")
        return t

    def _stooq_fetch(self, stooq_ticker: str, from_date: str) -> pd.DataFrame | None:
        try:
            d1 = datetime.strptime(from_date, "%Y-%m-%d").strftime("%Y%m%d")
            d2 = date.today().strftime("%Y%m%d")
            url = f"https://stooq.com/q/d/l/?s={stooq_ticker.lower()}&d1={d1}&d2={d2}&i=d"
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return None
            text = resp.text.strip()
            if not text or "No data" in text or len(text.splitlines()) < 2:
                return None
            df = pd.read_csv(StringIO(text), parse_dates=["Date"], index_col="Date")
            if df.empty:
                return None
            df.columns = [c.strip().title() for c in df.columns]
            for col in ["Open", "High", "Low", "Close"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            if "Volume" not in df.columns:
                df["Volume"] = 0
            df = df[["Open", "High", "Low", "Close", "Volume"]]
            df = df[df["Close"].notna()]
            df.index.name = "Date"
            return df.sort_index().round(2) if not df.empty else None
        except Exception:
            return None

    def _persist(self, cache_key: str, ticker: str) -> None:
        self._cache[cache_key] = ticker
        _save_cache(self._cache)

    # ── Public API ─────────────────────────────────────────────────────────

    def resolve(
        self,
        query: str,
        log: Callable[[str], None] | None = None,
    ) -> ResolveResult:
        """
        Phase 1 — resolve a natural-language query to a ticker symbol.

        Returns
        -------
        ResolveResult
            status="direct"     → single confident match; .ticker is populated
            status="candidates" → multiple plausible matches; .candidates is populated
            status="failed"     → could not resolve; .message explains why
        """
        def _log(msg: str) -> None:
            (log or print)(msg)

        cache_key = query.strip().upper()

        # ── Cache ──────────────────────────────────────────────────────────
        if cache_key in self._cache:
            ticker = self._cache[cache_key]
            _log(f"Cache hit: {cache_key!r} → {ticker}")
            return ResolveResult(
                status="direct",
                ticker=ticker,
                ticker_name=ticker,
                message=f"Cached: {ticker}",
            )

        # ── Yahoo Finance symbol search ────────────────────────────────────
        _log(f"Searching Yahoo Finance for '{query}'…")
        yf_results = self._yf_search(query)

        if yf_results:
            first = yf_results[0]
            first_ticker = first.get("symbol", "")
            first_name = (
                first.get("longname") or first.get("shortname") or first_ticker
            )

            # Exact ticker entered (e.g. "AAPL", "RELIANCE", "RELIANCE.NS")
            query_norm = cache_key.replace(" ", "")
            ticker_base = re.sub(r"\.(NS|BO)$", "", first_ticker.upper())
            is_exact = (
                ticker_base == query_norm
                or first_ticker.upper() == query_norm
            )
            if is_exact:
                _log(f"Exact ticker match: {first_name} ({first_ticker})")
                return ResolveResult(
                    status="direct",
                    ticker=first_ticker,
                    ticker_name=first_name,
                    message=f"Exact match: {first_name}",
                )

            # Single unambiguous result
            if len(yf_results) == 1:
                _log(f"Single Yahoo Finance result: {first_name} ({first_ticker})")
                return ResolveResult(
                    status="direct",
                    ticker=first_ticker,
                    ticker_name=first_name,
                    message=f"Best match: {first_name}",
                )

            # Multiple results → surface as candidates for the user
            candidates = [
                {
                    "ticker": r.get("symbol", ""),
                    "name": (
                        r.get("longname") or r.get("shortname") or r.get("symbol", "")
                    ),
                    "description": (
                        f"{r.get('exchange', '?')} · {r.get('quoteType', '?')}"
                    ),
                }
                for r in yf_results[:4]
                if r.get("symbol")
            ]
            if len(candidates) > 1:
                _log(
                    f"Yahoo Finance returned {len(candidates)} candidates "
                    "— waiting for user selection"
                )
                return ResolveResult(
                    status="candidates",
                    candidates=candidates,
                    message=f"Found {len(candidates)} possible matches on Yahoo Finance",
                )
            if candidates:
                return ResolveResult(
                    status="direct",
                    ticker=candidates[0]["ticker"],
                    ticker_name=candidates[0]["name"],
                    message=f"Best match: {candidates[0]['name']}",
                )

        # ── Gemini + Google Search ─────────────────────────────────────────
        _log("No confident match from Yahoo Finance — querying AI with web search…")
        return self._gemini_resolve(query, _log)

    def fetch_ticker(
        self,
        ticker: str,
        from_date: str,
        cache_key: str | None = None,
        log: Callable[[str], None] | None = None,
    ) -> pd.DataFrame:
        """
        Phase 2 — download OHLCV data for a confirmed ticker.

        Parameters
        ----------
        ticker : str
            yfinance-format ticker (e.g. "RELIANCE.NS", "^NSEI", "AAPL").
        from_date : str
            Start date — DD-MM-YYYY or YYYY-MM-DD.
        cache_key : str, optional
            Original query string; if given, the ticker is cached for future use.
        log : callable, optional
            Progress callback.
        """
        def _log(msg: str) -> None:
            (log or print)(msg)

        for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                norm_date = str(datetime.strptime(from_date.strip(), fmt).date())
                break
            except ValueError:
                continue
        else:
            raise ValueError(
                f"Unrecognised date format: '{from_date}'.  Use YYYY-MM-DD."
            )

        _log(f"Downloading data for {ticker} from yfinance…")
        df = self._yf_fetch(ticker, norm_date)
        if df is not None:
            _log(f"Fetched {len(df)} rows from yfinance ({ticker})")
            if cache_key:
                self._persist(cache_key, ticker)
            return df

        stooq_t = self._to_stooq_ticker(ticker)
        _log(f"yfinance returned no data — trying stooq.com ({stooq_t})…")
        df = self._stooq_fetch(stooq_t, norm_date)
        if df is not None:
            _log(f"Fetched {len(df)} rows from stooq ({stooq_t})")
            if cache_key:
                self._persist(cache_key, ticker)
            return df

        raise RuntimeError(
            f"Could not fetch data for '{ticker}'.  "
            "Try the exact yfinance ticker, e.g. RELIANCE.NS, ^NSEI, AAPL."
        )


# ── Standalone CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os, sys

    print("=== WAIF Fetch-Data Agent (Gemini + Google Search) ===\n")
    api_key = os.getenv("GEMINI_API_KEY") or input("Gemini API key: ").strip()
    query = input("Stock / Instrument (e.g. 'Reliance', 'Apple', 'Nifty'): ").strip()
    from_date = input("From date (DD-MM-YYYY or YYYY-MM-DD)              : ").strip()

    agent = FetchDataAgent(api_key=api_key)
    result = agent.resolve(query, log=print)
    print(f"\nResolve result: {result}\n")

    if result.status == "direct":
        ticker = result.ticker or ""
    elif result.status == "candidates" and result.candidates:
        print("Candidates:")
        for i, c in enumerate(result.candidates):
            print(f"  [{i}] {c['ticker']} — {c['name']} ({c.get('description','')})")
        idx = int(input("Select [0]: ").strip() or "0")
        ticker = result.candidates[idx]["ticker"]
    else:
        print(f"Failed: {result.message}", file=sys.stderr)
        sys.exit(1)

    try:
        df = agent.fetch_ticker(ticker, from_date, cache_key=query.upper(), log=print)
        print(f"\nFetched {len(df)} rows:")
        print(df.tail(5).to_string())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
