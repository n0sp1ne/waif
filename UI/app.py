import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "IndexCalculator"))

import base64
import io
import streamlit as st
import pandas as pd

st.set_page_config(page_title="Dashboard", layout="wide", initial_sidebar_state="expanded")

# ── Session state ──────────────────────────────────────────────────────────────
if "datasets" not in st.session_state:
    st.session_state["datasets"] = {}   # key → DataFrame
if "last_fetched" not in st.session_state:
    st.session_state["last_fetched"] = None
if "screener_results" not in st.session_state:
    st.session_state["screener_results"] = {}  # industry → [stock_dicts]
if "ai_candidates" not in st.session_state:
    st.session_state["ai_candidates"] = None   # list[dict] when awaiting user choice
if "ai_pending_query" not in st.session_state:
    st.session_state["ai_pending_query"] = None
if "ai_pending_date" not in st.session_state:
    st.session_state["ai_pending_date"] = None

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <div style="
        background: linear-gradient(90deg, #1e1e2e 0%, #313244 100%);
        padding: 1rem 2rem;
        border-bottom: 2px solid #89b4fa;
        display: flex;
        align-items: center;
        gap: 1rem;
        margin: -1rem -1rem 2rem -1rem;
    ">
        <span style="font-size: 1.8rem;">🤖</span>
        <div>
            <h1 style="margin: 0; color: #cdd6f4; font-size: 1.5rem; font-weight: 700;">WAIF Dashboard</h1>
            <p style="margin: 0; color: #6c7086; font-size: 0.85rem;">Workflow AI Framework</p>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## Navigation")
    page = st.radio(
        label="Page",
        options=["Overview", "AI Fetch", "Screener", "Section 2", "Settings"],
        label_visibility="collapsed",
    )

    st.divider()
    st.markdown("### Status")
    st.success("System Online")
    st.metric("Uptime", "99.9%")
    st.metric("Tasks", "0 active")

# ── Helpers ───────────────────────────────────────────────────────────────────
def _dedup_datasets() -> None:
    """For each instrument, keep only the entry whose date starts earliest."""
    datasets = st.session_state["datasets"]
    groups: dict[str, list[str]] = {}
    for key in list(datasets.keys()):
        name, _, date_str = key.rpartition("_")
        group_key = name if name else key
        groups.setdefault(group_key, []).append(key)

    for keys in groups.values():
        if len(keys) <= 1:
            continue
        keys_sorted = sorted(keys, key=lambda k: k.rpartition("_")[2])
        to_keep = keys_sorted[0]
        for k in keys_sorted[1:]:
            del datasets[k]
            if st.session_state.get("last_fetched") == k:
                st.session_state["last_fetched"] = to_keep


def _copy_button_html(csv_str: str, button_label: str = "📋 Copy to Clipboard") -> str:
    """Return an HTML snippet with a JS-powered copy-to-clipboard button."""
    b64 = base64.b64encode(csv_str.encode("utf-8")).decode("utf-8")
    return f"""
    <button id="copy-btn" onclick="
        var t = atob('{b64}');
        var el = document.createElement('textarea');
        el.value = t;
        document.body.appendChild(el);
        el.select();
        document.execCommand('copy');
        document.body.removeChild(el);
        this.innerText = '✓ Copied!';
        var btn = this;
        setTimeout(function(){{ btn.innerText = '{button_label}'; }}, 2000);
    " style="
        background:#313244;
        color:#cdd6f4;
        border:1px solid #89b4fa;
        border-radius:0.35rem;
        padding:0.45rem 1rem;
        cursor:pointer;
        font-size:0.875rem;
        width:100%;
        margin-top:2px;
    ">{button_label}</button>
    """

# ── Main content ──────────────────────────────────────────────────────────────
if page == "Overview":
    st.subheader("Overview")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Tasks", "0", delta=None)
    with col2:
        st.metric("Completed", "0", delta=None)
    with col3:
        st.metric("Errors", "0", delta=None)

    st.divider()
    st.info("Welcome to the WAIF dashboard. Use the sidebar to navigate.")

# ─────────────────────────────────────────────────────────────────────────────
elif page == "AI Fetch":
    import os as _os  # noqa: PLC0415

    st.subheader("AI-Powered Data Fetch")
    st.caption(
        "Describe the stock or instrument in plain language. "
        "The agent searches Yahoo Finance and uses Gemini AI + Google Search to resolve the ticker, "
        "then downloads OHLCV data from yfinance (with stooq.com as fallback)."
    )

    # ── Gemini API key (never shown in UI) ────────────────────────────────
    _gemini_key: str = _os.getenv("GEMINI_API_KEY", "")
    try:
        _gemini_key = _gemini_key or st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        pass

    if not _gemini_key:
        st.error(
            "Gemini API key not configured. "
            "Set `GEMINI_API_KEY` in `.streamlit/secrets.toml` or your environment."
        )
        st.stop()

    # ── Query inputs ───────────────────────────────────────────────────────
    col_q, col_d = st.columns([3, 1])
    with col_q:
        ai_query = st.text_input(
            "Query",
            placeholder="e.g. Reliance Industries, Apple stock, Nifty Bank, Gold ETF",
            key="ai_query",
        )
    with col_d:
        ai_from_date = st.date_input("From Date", key="ai_from_date")

    # ── Fetch button ───────────────────────────────────────────────────────
    if st.button("Fetch with AI Agent", type="primary"):
        if not ai_query.strip():
            st.warning("Please enter a query.")
        else:
            # Reset candidate state for a fresh query
            st.session_state["ai_candidates"] = None
            st.session_state["ai_pending_query"] = ai_query.strip()
            st.session_state["ai_pending_date"] = str(ai_from_date)

            try:
                from fetch_data_agent import FetchDataAgent, ResolveResult  # noqa: PLC0415

                agent = FetchDataAgent(api_key=_gemini_key)
                _cache_key = ai_query.strip().upper()

                with st.status("Resolving ticker…", expanded=True) as _status:
                    def _log(msg: str) -> None:  # noqa: E306
                        st.write(msg)

                    result: ResolveResult = agent.resolve(ai_query.strip(), log=_log)

                    if result.status == "direct":
                        _status.update(
                            label=f"Ticker resolved: {result.ticker}", state="running"
                        )
                        try:
                            df = agent.fetch_ticker(
                                result.ticker,  # type: ignore[arg-type]
                                str(ai_from_date),
                                cache_key=_cache_key,
                                log=_log,
                            )
                            _status.update(
                                label=f"Done — fetched {len(df)} rows ({result.ticker})",
                                state="complete",
                            )
                            ai_key = f"{_cache_key.replace(' ', '_')}_{ai_from_date}"
                            st.session_state["datasets"][ai_key] = df
                            st.session_state["last_fetched"] = ai_key
                            _dedup_datasets()
                            st.success(
                                f"Fetched **{len(df)} rows** for "
                                f"**{result.ticker_name or ai_query}** "
                                f"(`{result.ticker}`) — added as `{ai_key}`"
                            )
                            st.dataframe(df, use_container_width=True)
                            _buf = io.StringIO()
                            df.to_csv(_buf)
                            _csv = _buf.getvalue()
                            _dl, _cp = st.columns(2)
                            with _dl:
                                st.download_button(
                                    "⬇ Download CSV", _csv,
                                    file_name=f"{ai_key}.csv", mime="text/csv",
                                    use_container_width=True,
                                )
                            with _cp:
                                st.components.v1.html(  # type: ignore
                                    _copy_button_html(_csv), height=46
                                )
                        except RuntimeError as _exc:
                            _status.update(label=f"Failed: {_exc}", state="error")
                            st.error(str(_exc))

                    elif result.status == "candidates":
                        _status.update(
                            label=(
                                f"Found {len(result.candidates)} possible matches "  # type: ignore[arg-type]
                                "— please select one below"
                            ),
                            state="complete",
                        )
                        st.session_state["ai_candidates"] = result.candidates

                    else:
                        _status.update(label=f"Could not resolve: {result.message}", state="error")
                        st.error(result.message)

            except Exception as exc:
                st.error(f"Unexpected error: {exc}")

    # ── Candidate selection UI (shown when resolve returned multiple matches) ─
    _candidates = st.session_state.get("ai_candidates")   # list[dict] or None
    _pq = st.session_state.get("ai_pending_query")        # str or None
    _pd = st.session_state.get("ai_pending_date")         # str or None

    if _candidates and _pq and _pd:
        st.divider()
        st.markdown("### Select the correct instrument")
        st.caption(
            f'Multiple matches found for **"{_pq}"**. '
            "Pick the one you meant, then click **Confirm & Fetch**."
        )

        _options = [
            f"{c['ticker']}  —  {c['name']}  ({c.get('description', '')})"
            for c in _candidates
        ]
        _sel_idx: int = st.radio(  # type: ignore[assignment]
            "Instrument",
            options=list(range(len(_options))),
            format_func=lambda i: _options[i],
            label_visibility="collapsed",
            key="ai_candidate_radio",
        )

        if st.button("Confirm & Fetch", type="primary", key="ai_confirm_btn"):
            _chosen = _candidates[_sel_idx]
            _ticker = _chosen["ticker"]

            try:
                from fetch_data_agent import FetchDataAgent  # noqa: PLC0415

                agent = FetchDataAgent(api_key=_gemini_key)

                with st.status(f"Fetching data for {_ticker}…", expanded=True) as _status:
                    def _log2(msg: str) -> None:  # noqa: E306
                        st.write(msg)

                    try:
                        df = agent.fetch_ticker(
                            _ticker, _pd,
                            cache_key=_pq.upper(),
                            log=_log2,
                        )
                        _status.update(
                            label=f"Done — fetched {len(df)} rows ({_ticker})",
                            state="complete",
                        )
                    except Exception as _exc:
                        _status.update(label=f"Failed: {_exc}", state="error")
                        raise

                # Clear candidate state
                st.session_state["ai_candidates"] = None
                st.session_state["ai_pending_query"] = None
                st.session_state["ai_pending_date"] = None

                ai_key = f"{_pq.upper().replace(' ', '_')}_{_pd}"
                st.session_state["datasets"][ai_key] = df
                st.session_state["last_fetched"] = ai_key
                _dedup_datasets()
                st.success(
                    f"Fetched **{len(df)} rows** for **{_chosen['name']}** "
                    f"(`{_ticker}`) — added as `{ai_key}`"
                )
                st.dataframe(df, use_container_width=True)
                _buf2 = io.StringIO()
                df.to_csv(_buf2)
                _csv2 = _buf2.getvalue()
                _dl2, _cp2 = st.columns(2)
                with _dl2:
                    st.download_button(
                        "⬇ Download CSV", _csv2,
                        file_name=f"{ai_key}.csv", mime="text/csv",
                        use_container_width=True,
                    )
                with _cp2:
                    st.components.v1.html(  # type: ignore
                        _copy_button_html(_csv2), height=46
                    )

            except RuntimeError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Unexpected error: {exc}")

# ─────────────────────────────────────────────────────────────────────────────
elif page == "Screener":
    st.subheader("Screener.in Stock Screener")
    st.caption(
        "Enter a screener.in query to fetch matching stocks. "
        "Results are grouped by industry using data from yfinance."
    )

    # ── Setup instructions ─────────────────────────────────────────────────────
    with st.expander("First-time setup (one-time)", expanded=False):
        st.markdown(
            """
**How to get your session cookie** (takes ~1 minute, only needed once):

1. Open **screener.in** in Chrome or Firefox and log in with Google
2. Press **F12** to open DevTools
3. Go to **Application** tab (Chrome) → **Cookies** → `https://www.screener.in`
   *(Firefox: Storage tab → Cookies)*
4. Find the cookie named **`sessionid`** and copy its **Value**
5. Open **`secrets.txt`** in the repo root and replace the placeholder:
   ```
   SCREENER_SESSION=paste_the_value_here
   ```

The session lasts ~2 weeks. When it expires, just repeat steps 1-5.
"""
        )

    # ── Query syntax help ───────────────────────────────────────────────────────
    with st.expander("Query syntax help", expanded=False):
        st.markdown(
            """
**Examples**
```
Market Capitalization > 500 AND PE < 20
ROCE > 15 AND Debt to equity < 1
Sales growth 3Years > 10 AND Return on equity > 16
```
**Common fields:** `Market Capitalization`, `PE`, `ROCE`, `Dividend Yield`,
`Debt to equity`, `Sales growth 3Years`, `Return on equity`, `Price to book value`.

Use `AND` / `OR` to combine conditions.
Full reference: [screener.in query docs](https://www.screener.in/screen/raw/)
"""
        )

    _DEFAULT_QUERY = (
        "Return on capital employed > 20\n"
        "AND Return on equity > 20\n"
        "AND Debt to equity < 0.3\n"
        "AND Sales growth 5Years > 12\n"
        "AND Profit growth 5Years > 15\n"
        "AND PEG Ratio < 2\n"
        "AND OPM  > 18\n"
        "AND Current ratio > 1.5\n"
        "AND Market Capitalization > 10000"
    )

    screener_query = st.text_area(
        "Query",
        value=_DEFAULT_QUERY,
        height=200,
        key="screener_query",
    )

    if st.button("Run Screener", type="primary", key="screener_run"):
        if not screener_query.strip():
            st.warning("Please enter a query.")
        else:
            try:
                from screener_fetch import run_screener_query  # noqa: PLC0415

                with st.status("Running screener query…", expanded=True) as _sc_status:
                    def _sc_log(msg: str) -> None:
                        st.write(msg)

                    try:
                        _screener_sid = st.secrets.get("SCREENER_SESSION") or None
                        grouped = run_screener_query(
                            screener_query.strip(),
                            log_callback=_sc_log,
                            session_id=_screener_sid,
                        )
                        total_stocks = sum(len(v) for v in grouped.values())
                        _sc_status.update(
                            label=f"Done — {total_stocks} stocks in {len(grouped)} industries",
                            state="complete",
                        )
                    except Exception as _exc:
                        _sc_status.update(label=f"Failed: {_exc}", state="error")
                        raise

                st.session_state["screener_results"] = grouped

            except RuntimeError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Unexpected error: {exc}")

    # ── Display results ────────────────────────────────────────────────────────
    grouped = st.session_state.get("screener_results", {})
    if grouped:
        total_stocks = sum(len(v) for v in grouped.values())
        st.markdown(f"### Results — {total_stocks} stocks across {len(grouped)} industries")

        # Sort industries by number of stocks descending
        sorted_industries = sorted(grouped.items(), key=lambda x: len(x[1]), reverse=True)

        # Metric columns returned by screener.in (display subset)
        DISPLAY_COLS = ["name", "ticker", "CMP Rs.", "P/E", "Mar Cap Rs.Cr.", "ROCE %"]

        for industry, stocks in sorted_industries:
            header = f"{industry}  ({len(stocks)} stock{'s' if len(stocks) != 1 else ''})"
            with st.expander(header, expanded=True):
                rows = []
                for s in stocks:
                    row: dict = {}
                    row["Name"] = s.get("name", "")
                    row["Ticker"] = s.get("ticker", "")
                    for col in ["CMP Rs.", "P/E", "Mar Cap Rs.Cr.", "ROCE %"]:
                        val = s.get(col)
                        if val is not None:
                            row[col] = val
                    row["Screener Link"] = s.get("screener_url", "")
                    rows.append(row)

                df_industry = pd.DataFrame(rows)

                # Make Screener Link clickable
                if "Screener Link" in df_industry.columns:
                    df_industry["Screener Link"] = df_industry["Screener Link"].apply(
                        lambda u: f'<a href="{u}" target="_blank">View</a>' if u else ""
                    )
                    st.markdown(
                        df_industry.to_html(escape=False, index=False),
                        unsafe_allow_html=True,
                    )
                else:
                    st.dataframe(df_industry, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
elif page == "Section 2":
    st.subheader("Portfolio Builder")

    datasets = st.session_state.get("datasets", {})

    if not datasets:
        st.info("No data fetched yet. Go to **Section 1** to fetch instrument data first.")
    else:
        st.markdown("### Instruments")
        st.caption(
            "Tick the instruments you want to include. "
            "Assign a weight — your initial position is **weight × 100 units**. "
            "Portfolio value for each day = Σ ( weight × 100 × Close[d] / Close[0] )."
        )

        # ── Benchmark picker ──────────────────────────────────────────────────
        st.markdown("#### Benchmark")
        st.caption("The benchmark receives the full 100 units — shown as a reference line on the chart.")
        benchmark_key = st.selectbox(
            "Benchmark instrument",
            options=["(none)"] + list(datasets.keys()),
            key="benchmark_select",
            label_visibility="collapsed",
        )

        st.divider()

        selected_keys    = []
        weight_map: dict = {}

        # Header row
        h1, h2, h3 = st.columns([0.5, 4, 2])
        h1.markdown("**✓**")
        h2.markdown("**Dataset**")
        h3.markdown("**Weight**")
        st.divider()

        for key in datasets:
            c1, c2, c3 = st.columns([0.5, 4, 2])
            with c1:
                checked = st.checkbox("", key=f"chk_{key}", label_visibility="collapsed")
            with c2:
                st.markdown(f"`{key}`")
            with c3:
                w = st.number_input(
                    "weight",
                    min_value=0.0,
                    max_value=1000.0,
                    value=1.0,
                    step=0.1,
                    format="%.2f",
                    key=f"wt_{key}",
                    label_visibility="collapsed",
                )
            if checked:
                selected_keys.append(key)
                weight_map[key] = w

        # ── Rebalancing config ─────────────────────────────────────────────────
        st.divider()
        st.markdown("#### Rebalancing")
        st.caption(
            "Set how many **trading days** between each rebalance. "
            "On a rebalance date the portfolio is sold/bought back to the original target weights. "
            "Set to **0** to disable rebalancing."
        )
        rebalance_days = int(st.number_input(
            "Rebalance every N trading days",
            min_value=0,
            max_value=3650,
            value=0,
            step=1,
            key="rebalance_days",
            label_visibility="collapsed",
        ))
        if rebalance_days > 0:
            st.info(f"Portfolio will be rebalanced every **{rebalance_days} trading days**.")

        # ── Portfolio calculation ──────────────────────────────────────────────
        if selected_keys:
            st.divider()
            st.markdown("### Portfolio Performance")

            # Gather close prices for selected instruments
            closes: dict = {}
            for key in selected_keys:
                df    = datasets[key]
                close = df["Close"].dropna()
                if close.empty:
                    st.warning(f"{key}: no Close data, skipped.")
                else:
                    closes[key] = close

            if closes:
                # Align on common trading dates
                combined_close = pd.DataFrame(closes)
                combined_close.index = pd.to_datetime(combined_close.index)
                combined_close = combined_close.sort_index().ffill().dropna()

                total_weight = sum(weight_map[k] for k in closes)

                # Initial share count for each instrument
                shares = {
                    key: (weight_map[key] * 100.0) / float(combined_close[key].iloc[0])
                    for key in closes
                }

                rebalance_log: list = []
                portfolio_values: dict = {key: [] for key in closes}
                last_rebalance_idx = 0
                dates = combined_close.index

                for i in range(len(dates)):
                    cur_vals = {
                        key: shares[key] * float(combined_close[key].iloc[i])
                        for key in closes
                    }

                    # Rebalance if threshold reached
                    if rebalance_days > 0 and i > 0 and (i - last_rebalance_idx) >= rebalance_days:
                        total_val  = sum(cur_vals.values())
                        before_vals = cur_vals.copy()
                        for key in closes:
                            target_frac = weight_map[key] / total_weight
                            shares[key] = (target_frac * total_val) / float(combined_close[key].iloc[i])
                        cur_vals = {
                            key: shares[key] * float(combined_close[key].iloc[i])
                            for key in closes
                        }
                        log_row: dict = {
                            "Date":         str(dates[i].date()),
                            "Total Before": round(sum(before_vals.values()), 2),
                            "Total After":  round(sum(cur_vals.values()), 2),
                        }
                        for key in closes:
                            log_row[f"{key} Before"] = round(before_vals[key], 2)
                            log_row[f"{key} After"]  = round(cur_vals[key], 2)
                        rebalance_log.append(log_row)
                        last_rebalance_idx = i

                    for key in closes:
                        portfolio_values[key].append(cur_vals[key])

                combined          = pd.DataFrame(portfolio_values, index=dates)
                combined["Total"] = combined.sum(axis=1)

                # ── Benchmark series (100 units, no weight) ────────────────
                has_benchmark = benchmark_key != "(none)" and benchmark_key in datasets
                if has_benchmark:
                    bm_close = datasets[benchmark_key]["Close"].dropna()
                    if not bm_close.empty:
                        bm_base = float(bm_close.iloc[0])
                        combined[f"Benchmark ({benchmark_key})"] = (bm_close / bm_base) * 100.0

                t_start = float(combined["Total"].iloc[0])
                t_end   = float(combined["Total"].iloc[-1])
                ret_pct = ((t_end - t_start) / t_start) * 100 if t_start else 0.0
                pnl     = t_end - t_start

                years = (dates[-1] - dates[0]).days / 365.25
                port_cagr = ((t_end / t_start) ** (1 / years) - 1) * 100 if years > 0 and t_start else 0.0

                # Benchmark comparison metrics
                if has_benchmark:
                    bm_col   = f"Benchmark ({benchmark_key})"
                    bm_start = float(combined[bm_col].iloc[0])
                    bm_end   = float(combined[bm_col].iloc[-1])
                    bm_ret   = ((bm_end - bm_start) / bm_start) * 100 if bm_start else 0.0
                    bm_cagr  = ((bm_end / bm_start) ** (1 / years) - 1) * 100 if years > 0 and bm_start else 0.0
                    alpha    = ret_pct - bm_ret

                    m1, m2, m3, m4, m5 = st.columns(5)
                    m1.metric("Initial Investment", f"₹{t_start:,.2f}")
                    m2.metric("Current Value",       f"₹{t_end:,.2f}")
                    m3.metric("P&L",                 f"₹{pnl:+,.2f}")
                    m4.metric("Portfolio Return",    f"{ret_pct:+.2f}%")
                    m5.metric("Alpha vs Benchmark",  f"{alpha:+.2f}%",
                              delta=f"Benchmark: {bm_ret:+.2f}%")

                    c1, c2, c3 = st.columns(3)
                    c1.metric("Holding Period", f"{years:.2f} yrs")
                    c2.metric("Index CAGR",     f"{port_cagr:+.2f}%")
                    c3.metric("Benchmark CAGR", f"{bm_cagr:+.2f}%",
                              delta=f"vs Index: {port_cagr - bm_cagr:+.2f}%")
                else:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Initial Investment", f"₹{t_start:,.2f}")
                    m2.metric("Current Value",       f"₹{t_end:,.2f}")
                    m3.metric("P&L",                 f"₹{pnl:+,.2f}")
                    m4.metric("Total Return",        f"{ret_pct:+.2f}%")

                    c1, c2 = st.columns(2)
                    c1.metric("Holding Period", f"{years:.2f} yrs")
                    c2.metric("Index CAGR",     f"{port_cagr:+.2f}%")

                st.divider()
                st.markdown("**Daily Portfolio Value**")

                # ── Series visibility checkboxes ───────────────────────────
                all_series = list(closes.keys()) + ["Total"]
                if has_benchmark:
                    bm_col_name = f"Benchmark ({benchmark_key})"
                    all_series.append(bm_col_name)

                st.caption("Select series to display:")
                cb_cols = st.columns(len(all_series))
                visible_series = []
                for idx, series in enumerate(all_series):
                    default = True  # show all by default
                    label = series if series in ("Total",) or series.startswith("Benchmark") else series
                    checked = cb_cols[idx].checkbox(
                        label,
                        value=default,
                        key=f"vis_{series}",
                    )
                    if checked:
                        visible_series.append(series)

                if visible_series:
                    chart_df = combined[[s for s in visible_series if s in combined.columns]]
                    st.line_chart(chart_df, use_container_width=True)
                else:
                    st.info("Select at least one series to display the chart.")

                # ── Rebalance log ──────────────────────────────────────────
                if rebalance_log:
                    st.divider()
                    with st.expander(f"Rebalance Log ({len(rebalance_log)} events)"):
                        st.caption(
                            "Each row shows the portfolio value immediately before and after rebalancing. "
                            "Total Before = Total After because the overall portfolio value is unchanged; "
                            "only the allocation between instruments shifts."
                        )
                        st.dataframe(pd.DataFrame(rebalance_log).set_index("Date"), use_container_width=True)

                st.divider()
                st.markdown("**Daily Values Table**")
                st.dataframe(combined.round(2), use_container_width=True)
            else:
                st.warning("No valid data for the selected instruments.")

# ─────────────────────────────────────────────────────────────────────────────
elif page == "Settings":
    st.subheader("Settings")
    st.text_input("App Name", value="WAIF Dashboard")
    st.toggle("Dark mode", value=True)
    st.slider("Log level", 0, 5, 2)
    if st.button("Save Settings"):
        st.success("Settings saved.")
