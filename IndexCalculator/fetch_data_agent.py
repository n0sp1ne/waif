"""
AI-powered stock data fetch agent.

Uses a local Ollama LLM to resolve natural-language queries to ticker symbols,
searches the web via DuckDuckGo when the LLM alone is uncertain, and downloads
OHLCV data from yfinance (primary) or stooq.com (fallback).

Setup
-----
1. Install Ollama:  https://ollama.com
2. Run:  ollama serve
3. Pull a model:  ollama pull llama3.2

Python deps (see requirements.txt): ollama, duckduckgo-search, requests
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from typing import Callable, cast

import pandas as pd
import requests
import yfinance as yf
from duckduckgo_search import DDGS

# ── Ticker cache ──────────────────────────────────────────────────────────────
# Discovered tickers are persisted here so future queries skip the LLM entirely.
_CACHE_PATH = Path(__file__).parent / "ticker_cache.json"

# Well-known NSE symbols (mirrors fetch_data.INDEX_MAP).
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
            # Base map takes priority so indices never get overwritten.
            return {**data, **_BASE_MAP}
        except Exception:
            pass
    return dict(_BASE_MAP)


def _save_cache(cache: dict[str, str]) -> None:
    # Persist only non-base entries (base map is always baked in at load time).
    to_save = {k: v for k, v in cache.items() if k not in _BASE_MAP}
    _CACHE_PATH.write_text(json.dumps(to_save, indent=2))


# ── Agent ─────────────────────────────────────────────────────────────────────

class FetchDataAgent:
    """
    Resolves natural-language stock/instrument queries to OHLCV DataFrames.

    Parameters
    ----------
    model : str
        Ollama model name.  Run ``ollama list`` to see what you have pulled.
        Recommended: ``llama3.2``  or  ``mistral``
    """

    def __init__(self, model: str = "llama3.1:8b") -> None:
        self.model = model
        self._cache = _load_cache()
        self._ollama_ok: bool | None = None

    # ── Ollama health-check ────────────────────────────────────────────────

    def _ensure_ollama(self) -> None:
        if self._ollama_ok:
            return
        try:
            import ollama as _ol
            _ol.list()
            self._ollama_ok = True
        except Exception as exc:
            raise RuntimeError(
                "Ollama server not reachable.\n"
                "  1. Install:  https://ollama.com\n"
                "  2. Start:    ollama serve\n"
                f"  3. Pull:     ollama pull {self.model}\n"
                f"Original error: {exc}"
            ) from exc

    # ── LLM: resolve query → ticker ────────────────────────────────────────

    def _llm_resolve(self, query: str, context: str = "") -> str:
        import ollama as _ol  # imported lazily so the class is importable without ollama
        self._ensure_ollama()

        system = (
            "You are a financial ticker resolver. "
            "Respond with ONLY the yfinance ticker symbol — "
            "no explanation, no punctuation, no extra words."
        )
        user = (
            f'Identify the yfinance ticker for: "{query}"\n\n'
            "Rules:\n"
            "- Indian NSE equities   → append .NS   (e.g. RELIANCE → RELIANCE.NS)\n"
            "- Indian BSE equities   → append .BO\n"
            "- Indian indices        → NIFTY 50 → ^NSEI | BANK NIFTY → ^NSEBANK | SENSEX → ^BSESN\n"
            "- US stocks             → standard symbol (AAPL, MSFT, GOOGL)\n"
            "- US ETFs               → SPY, QQQ, GLD, etc.\n"
            "- Global indices        → S&P 500 → ^GSPC | NASDAQ → ^IXIC | Dow Jones → ^DJI\n"
        )
        if context:
            user += f"\nAdditional context from a web search:\n{context}\n"
        user += "\nTicker:"

        resp = _ol.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            options={"temperature": 0},
        )
        raw = resp["message"]["content"].strip()
        # Strip quotes / whitespace / "Ticker:" prefix that some models add.
        ticker = re.sub(r'["`\'\s]', "", raw).upper()
        ticker = re.sub(r"^TICKER[:\-]?", "", ticker)
        return ticker

    # ── Tool: yfinance symbol search ───────────────────────────────────────

    def _yf_search(self, query: str) -> str | None:
        """
        Use Yahoo Finance's own search API to find the best matching ticker.
        Returns the top equity ticker or None if nothing useful is found.
        """
        try:
            results = yf.Search(query, max_results=5).quotes
            if not results:
                return None
            # Prefer NSE equity → BSE equity → any equity, in that order.
            for exchange_pref in ("NSI", "BSE", None):
                for r in results:
                    if r.get("quoteType") != "EQUITY":
                        continue
                    if exchange_pref is None or r.get("exchange") == exchange_pref:
                        return r.get("symbol")
            return None
        except Exception:
            return None

    # ── Tool: DuckDuckGo web search ────────────────────────────────────────

    def _search_web(self, query: str, n: int = 5) -> list[dict]:
        try:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=n))
        except Exception as exc:
            return [{"title": "Search error", "body": str(exc), "href": ""}]

    # ── Tool: yfinance ─────────────────────────────────────────────────────

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

    # ── Tool: stooq.com CSV download ───────────────────────────────────────

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
            url = (
                f"https://stooq.com/q/d/l/"
                f"?s={stooq_ticker.lower()}&d1={d1}&d2={d2}&i=d"
            )
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

    # ── Persist resolved ticker ────────────────────────────────────────────

    def _persist(self, cache_key: str, ticker: str) -> None:
        self._cache[cache_key] = ticker
        _save_cache(self._cache)

    # ── Public API ─────────────────────────────────────────────────────────

    def fetch(
        self,
        query: str,
        from_date: str,
        log: Callable[[str], None] | None = None,
    ) -> pd.DataFrame:
        """
        Resolve *query* to a stock ticker and return OHLCV data.

        Parameters
        ----------
        query : str
            Natural language or raw ticker — e.g. ``"Reliance"``, ``"AAPL"``,
            ``"Nifty Bank"``, ``"Apple Inc"``.
        from_date : str
            Start date — ``DD-MM-YYYY`` or ``YYYY-MM-DD``.
        log : callable, optional
            Status callback for progress messages (e.g. ``print`` or
            a Streamlit ``st.write``-like function).

        Returns
        -------
        pd.DataFrame
            Indexed by Date with columns: Open, High, Low, Close, Volume.
        """
        def _log(msg: str) -> None:
            (log or print)(msg)

        # ── Normalise date ──────────────────────────────────────────────────
        for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                norm_date = str(datetime.strptime(from_date.strip(), fmt).date())
                break
            except ValueError:
                continue
        else:
            raise ValueError(
                f"Unrecognised date format: '{from_date}'.  "
                "Use DD-MM-YYYY or YYYY-MM-DD."
            )

        cache_key = query.strip().upper()

        # ── Step 1: Cache lookup ────────────────────────────────────────────
        if cache_key in self._cache:
            ticker = self._cache[cache_key]
            _log(f"Cache hit: {cache_key!r} → {ticker}")
            df = self._yf_fetch(ticker, norm_date)
            if df is not None:
                _log(f"Fetched {len(df)} rows from yfinance ({ticker})")
                return df
            _log(f"Cached ticker {ticker!r} returned no data — re-resolving")

        # ── Step 2: Yahoo Finance symbol search ────────────────────────────
        _log(f"Searching Yahoo Finance for '{query}'…")
        ticker = self._yf_search(query)
        if ticker:
            _log(f"Yahoo Finance search → {ticker}")
            df = self._yf_fetch(ticker, norm_date)
            if df is not None:
                _log(f"Fetched {len(df)} rows from yfinance ({ticker})")
                self._persist(cache_key, ticker)
                return df
            _log(f"Ticker {ticker!r} found but returned no data — trying LLM")
        else:
            _log("No results from Yahoo Finance search — trying LLM")

        # ── Step 3: LLM first pass (no web context) ─────────────────────────
        _log(f"Asking LLM to resolve '{query}'…")
        ticker = self._llm_resolve(query)
        _log(f"LLM suggested: {ticker}")

        df = self._yf_fetch(ticker, norm_date)
        if df is not None:
            _log(f"Fetched {len(df)} rows from yfinance ({ticker})")
            self._persist(cache_key, ticker)
            return df

        # ── Step 4: DuckDuckGo search + LLM second pass ─────────────────────
        _log(f"yfinance returned nothing for {ticker!r} — searching web…")
        search_q = (
            f"{query} yahoo finance ticker symbol "
            "site:finance.yahoo.com OR site:in.finance.yahoo.com"
        )
        results = self._search_web(search_q)
        context = "\n".join(
            f"• {r.get('title', '')} — {r.get('body', '')[:200]}"
            for r in results[:4]
            if r.get("href")
        )
        _log(f"Web search returned {len(results)} results.  Re-asking LLM…")
        ticker = self._llm_resolve(query, context)
        _log(f"LLM (with context) → {ticker}")

        df = self._yf_fetch(ticker, norm_date)
        if df is not None:
            _log(f"Fetched {len(df)} rows from yfinance ({ticker})")
            self._persist(cache_key, ticker)
            return df

        # ── Step 5: stooq.com fallback ──────────────────────────────────────
        stooq_t = self._to_stooq_ticker(ticker)
        _log(f"Trying stooq.com ({stooq_t})…")
        df = self._stooq_fetch(stooq_t, norm_date)
        if df is not None:
            _log(f"Fetched {len(df)} rows from stooq ({stooq_t})")
            self._persist(cache_key, ticker)
            return df

        raise RuntimeError(
            f"Could not fetch data for '{query}' "
            f"(last attempted ticker: {ticker!r}).  "
            "Try the exact yfinance ticker, e.g. RELIANCE.NS, ^NSEI, AAPL."
        )


# ── Standalone CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== WAIF Fetch-Data Agent (Ollama + DuckDuckGo) ===\n")
    query     = input("Stock / Instrument (e.g. 'Reliance', 'Apple', 'Nifty'): ").strip()
    from_date = input("From date (DD-MM-YYYY or YYYY-MM-DD)                  : ").strip()
    save_csv  = input("Save to CSV? (y/n)                                     : ").strip().lower() == "y"
    model     = input("Ollama model [llama3.2]                                : ").strip() or "llama3.2"

    agent = FetchDataAgent(model=model)
    try:
        df = agent.fetch(query, from_date, log=print)
        print(f"\nFetched {len(df)} rows:")
        print(df.tail(5).to_string())
        if save_csv:
            fname = f"{query.replace(' ', '_').upper()}_{from_date}.csv"
            df.to_csv(fname)
            print(f"Saved → {fname}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
