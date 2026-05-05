import yfinance as yf
import pandas as pd
from datetime import datetime, date
from typing import cast


# NSE index symbols mapping
INDEX_MAP = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "NIFTY 50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "BANK NIFTY": "^NSEBANK",
    "NIFTYNEXT50": "^NSMIDCP",
    "NIFTYIT": "^CNXIT",
    "SENSEX": "^BSESN",
    "GOLDBEES": "GOLDBEES",
    "SILVERBEES": "SILVERBEES"
}


def resolve_symbol(instrument: str) -> str:
    """Convert NSE instrument name to yfinance ticker symbol."""
    upper = instrument.strip().upper()
    if upper in INDEX_MAP:
        return INDEX_MAP[upper]
    # If already a yfinance symbol (starts with ^ or ends with .NS/.BO), use as-is
    if upper.startswith("^") or upper.endswith(".NS") or upper.endswith(".BO"):
        return upper
    # Default: treat as NSE equity
    return f"{upper}.NS"


def fetch_data(instrument: str, from_date: str, save_csv: bool = False) -> pd.DataFrame:
    """
    Fetch OHLCV data from NSE via yfinance.

    Parameters
    ----------
    instrument : str
        NSE symbol or index name (e.g. "RELIANCE", "NIFTY", "BANKNIFTY")
    from_date : str
        Start date in DD-MM-YYYY or YYYY-MM-DD format
    save_csv : bool
        If True, saves the result to a CSV file

    Returns
    -------
    pd.DataFrame
        OHLCV data from from_date to today
    """
    # Parse date
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            start = datetime.strptime(from_date.strip(), fmt).date()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"Unrecognised date format: '{from_date}'. Use DD-MM-YYYY or YYYY-MM-DD.")

    end = date.today()

    if start >= end:
        raise ValueError(f"from_date ({start}) must be before today ({end}).")

    ticker = resolve_symbol(instrument)
    print(f"Fetching  : {instrument}  →  {ticker}")
    print(f"Period    : {start}  to  {end}")

    df = cast(pd.DataFrame, yf.download(ticker, start=str(start), end=str(end), progress=False, auto_adjust=True))

    if df.empty:
        raise RuntimeError(
            f"No data returned for '{ticker}'. "
            "Check the symbol or try adding .NS / .BO suffix manually."
        )

    # Flatten multi-level columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.index.name = "Date"
    df = cast(pd.DataFrame, df[["Open", "High", "Low", "Close", "Volume"]].round(2))

    print(f"Rows      : {len(df)}")
    print(df.tail(5).to_string())

    if save_csv:
        filename = f"{instrument.replace(' ', '_').upper()}_{start}_{end}.csv"
        df.to_csv(filename)
        print(f"\nSaved to  : {filename}")

    return df


if __name__ == "__main__":
    instrument = input("Instrument (e.g. RELIANCE, NIFTY, BANKNIFTY): ").strip()
    from_date  = input("From date  (DD-MM-YYYY or YYYY-MM-DD)        : ").strip()
    save       = input("Save to CSV? (y/n)                           : ").strip().lower() == "y"

    data = fetch_data(instrument, from_date, save_csv=save)
