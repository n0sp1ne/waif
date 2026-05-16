"""
screener_fetch.py
-----------------
Scrapes screener.in with a user-supplied query, then groups the
resulting stocks by industry (fetched via yfinance).

Authentication uses a session cookie read from secrets.txt — no
password required. See secrets.txt for one-time setup instructions.

Public API
----------
run_screener_query(query, log_callback=None) -> dict[str, list[dict]]
    Returns {industry_name: [stock_dict, ...]}
"""

import os
import re
import time
from collections import defaultdict
from pathlib import Path

import requests
import yfinance as yf
from bs4 import BeautifulSoup

LOGIN_URL    = "https://www.screener.in/login/"
SCREENER_URL = "https://www.screener.in/screen/raw/"

# secrets.txt lives one level above this file (repo root)
_SECRETS_PATH = Path(__file__).resolve().parent.parent / "secrets.txt"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.screener.in/",
}


# ── Secrets ────────────────────────────────────────────────────────────────────

def _load_session_id() -> str:
    """
    Read SCREENER_SESSION from secrets.txt.
    Raises RuntimeError with actionable guidance if missing or not set.
    """
    if not _SECRETS_PATH.exists():
        raise RuntimeError(
            f"secrets.txt not found at {_SECRETS_PATH}.\n"
            "Create it and add your screener.in session cookie — "
            "see the instructions inside the file."
        )

    for raw_line in _SECRETS_PATH.read_text().splitlines():
        line = raw_line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "SCREENER_SESSION":
            value = value.strip()
            if value and value != "paste_your_sessionid_value_here":
                return value

    raise RuntimeError(
        "SCREENER_SESSION not set in secrets.txt.\n"
        "Open secrets.txt and paste your screener.in sessionid cookie value."
    )


# ── Session setup ──────────────────────────────────────────────────────────────

def _make_session(session_id: str) -> requests.Session:
    """Return a requests.Session pre-loaded with the screener.in auth cookie."""
    session = requests.Session()
    session.cookies.set("sessionid", session_id, domain="www.screener.in")
    return session


def _verify_session(session: requests.Session) -> None:
    """
    Hit the screener home page and confirm we are logged in.
    Raises RuntimeError if the session has expired.
    """
    resp = session.get("https://www.screener.in/", headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    # When logged in, screener.in shows the user's name / dashboard links.
    # When logged out, it shows the "Login" link in the nav.
    soup = BeautifulSoup(resp.text, "html.parser")
    # /user/account/ is only present for authenticated users; /login is shown when logged out
    logged_in = (
        soup.find("a", href="/user/account/") is not None
        or soup.find("a", href=re.compile(r"/logout")) is not None
    )
    if not logged_in:
        raise RuntimeError(
            "screener.in session has expired.\n"
            "Log in again via Google in your browser, copy the new sessionid "
            "cookie, and update secrets.txt."
        )


# ── Scraping ───────────────────────────────────────────────────────────────────

def _scrape_page(session: requests.Session, query: str, page: int) -> list[dict]:
    """Fetch one page of screener.in results; return list of raw stock dicts."""
    params = {
        "sort": "",
        "source": "",
        "query": query,
        "limit": 50,
        "page": page,
    }
    resp = session.get(SCREENER_URL, params=params, headers=_HEADERS, timeout=30)
    resp.raise_for_status()

    if "/login" in resp.url or "/register" in resp.url:
        raise RuntimeError(
            "screener.in redirected to login — session may have expired.\n"
            "Update SCREENER_SESSION in secrets.txt."
        )

    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.find("table", {"class": re.compile(r"data-table")})
    if not table:
        table = soup.find("table")
    if not table:
        return []

    col_names: list[str] = []
    thead = table.find("thead")
    if thead:
        col_names = [th.get_text(separator=" ", strip=True) for th in thead.find_all("th")]

    tbody = table.find("tbody")
    if not tbody:
        return []

    # screener.in embeds the header row inside <tbody> using <th> cells (no <thead>)
    if not col_names:
        first_row = tbody.find("tr")
        if first_row:
            ths = first_row.find_all("th")
            if ths:
                col_names = [th.get_text(separator=" ", strip=True) for th in ths]

    stocks: list[dict] = []
    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        name_cell = cells[1] if len(cells) > 1 else cells[0]
        link = name_cell.find("a")
        if not link:
            continue

        name = link.get_text(strip=True)
        href = link.get("href", "")

        m = re.search(r"/company/([^/]+)/", href)
        ticker_slug = m.group(1) if m else ""

        stock: dict = {
            "name": name,
            "ticker": ticker_slug,
            "screener_url": (
                f"https://www.screener.in{href}"
                if href.startswith("/")
                else href
            ),
        }

        for i, col in enumerate(col_names):
            if i < 2:
                continue
            if i < len(cells):
                stock[col] = cells[i].get_text(strip=True)

        stocks.append(stock)

    return stocks


def _fetch_all_stocks(
    session: requests.Session, query: str, log_callback=None
) -> list[dict]:
    """Paginate through screener.in and collect all matching stocks."""

    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    all_stocks: list[dict] = []
    page = 1

    while True:
        log(f"Fetching page {page} from screener.in…")
        batch = _scrape_page(session, query, page)

        if not batch:
            log("No more results.")
            break

        all_stocks.extend(batch)
        log(f"Page {page}: {len(batch)} stocks (total: {len(all_stocks)})")

        if len(batch) < 50:
            break

        page += 1
        time.sleep(0.5)

    return all_stocks


# ── Industry enrichment ────────────────────────────────────────────────────────

def _enrich_with_industry(
    stocks: list[dict], log_callback=None
) -> dict[str, list[dict]]:
    """Fetch industry/sector for each stock via yfinance and group them."""

    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    by_industry: dict[str, list[dict]] = defaultdict(list)

    for i, stock in enumerate(stocks):
        ticker = stock.get("ticker", "")
        if not ticker:
            stock.setdefault("industry", "Unknown")
            stock.setdefault("sector", "Unknown")
            by_industry["Unknown"].append(stock)
            continue

        nse_ticker = f"{ticker}.NS"
        log(f"[{i + 1}/{len(stocks)}] Industry lookup: {ticker}…")

        try:
            info = yf.Ticker(nse_ticker).info
            industry = info.get("industry") or info.get("sector") or "Unknown"
            sector   = info.get("sector") or "Unknown"
        except Exception:
            industry = "Unknown"
            sector   = "Unknown"

        stock["industry"] = industry
        stock["sector"]   = sector
        by_industry[industry].append(stock)

        if i < len(stocks) - 1:
            time.sleep(0.2)

    return dict(by_industry)


# ── Public API ─────────────────────────────────────────────────────────────────

def run_screener_query(
    query: str,
    log_callback=None,
    session_id: str | None = None,
) -> dict[str, list[dict]]:
    """
    Run a screener.in query and return stocks grouped by industry.

    Reads the session cookie from secrets.txt automatically, or uses
    the session_id argument if provided (e.g. from st.secrets in deployment).

    Parameters
    ----------
    query        : screener.in query string
    log_callback : optional callable(str) for progress updates
    session_id   : screener.in sessionid cookie; if None, loaded from secrets.txt

    Returns
    -------
    dict[str, list[dict]]
        {industry_name: [stock_dict, ...]}
    """

    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    if session_id is None:
        session_id = _load_session_id()
    session    = _make_session(session_id)

    log("Verifying screener.in session…")
    _verify_session(session)
    log("Session OK.")

    log(f'Running query: "{query}"')
    stocks = _fetch_all_stocks(session, query, log_callback=log)

    if not stocks:
        log("No stocks returned for this query.")
        return {}

    log(f"Fetching industry info for {len(stocks)} stocks via yfinance…")
    grouped = _enrich_with_industry(stocks, log_callback=log)

    total = sum(len(v) for v in grouped.values())
    log(f"Done — {total} stocks across {len(grouped)} industries.")
    return grouped
