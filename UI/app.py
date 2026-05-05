import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "IndexCalculator"))
from fetch_data import fetch_data

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
        options=["Overview", "Section 1", "AI Fetch", "Section 2", "Settings"],
        label_visibility="collapsed",
    )

    st.divider()
    st.markdown("### Status")
    st.success("System Online")
    st.metric("Uptime", "99.9%")
    st.metric("Tasks", "0 active")

# ── Helpers ───────────────────────────────────────────────────────────────────
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
elif page == "Section 1":
    st.subheader("Fetch NSE Data")

    instrument = st.text_input("Instrument", placeholder="e.g. RELIANCE, NIFTY, BANKNIFTY")
    from_date  = st.date_input("From Date")

    if st.button("Fetch Data"):
        if not instrument:
            st.warning("Please enter an instrument symbol.")
        else:
            with st.spinner("Fetching data from NSE..."):
                try:
                    df  = fetch_data(instrument, str(from_date))
                    key = f"{instrument.strip().upper()}_{from_date}"
                    st.session_state["datasets"][key]  = df
                    st.session_state["last_fetched"]   = key
                    st.success(f"Fetched {len(df)} rows for **{instrument.upper()}**")
                except Exception as e:
                    st.error(f"Error: {e}")

    # Show data + export options for the most recently fetched dataset
    key = st.session_state["last_fetched"]
    if key and key in st.session_state["datasets"]:
        df = st.session_state["datasets"][key]
        st.dataframe(df, use_container_width=True)

        # Build CSV string once
        buf = io.StringIO()
        df.to_csv(buf)
        csv_str = buf.getvalue()

        dl_col, cp_col = st.columns(2)
        with dl_col:
            st.download_button(
                label="⬇ Download CSV",
                data=csv_str,
                file_name=f"{key}.csv",
                mime="text/csv",
                width="stretch",
            )
        with cp_col:
            st.components.v1.html( # type: ignore
                _copy_button_html(csv_str),
                height=46,
            )

# ─────────────────────────────────────────────────────────────────────────────
elif page == "AI Fetch":
    st.subheader("AI-Powered Data Fetch")
    st.caption(
        "Describe the stock or instrument in plain language. "
        "The agent uses a local Ollama LLM + DuckDuckGo to resolve the ticker, "
        "then downloads OHLCV data from yfinance (with stooq.com as fallback)."
    )

    col_q, col_d = st.columns([3, 1])
    with col_q:
        ai_query = st.text_input(
            "Query",
            placeholder="e.g. Reliance Industries, Apple stock, Nifty Bank, Gold ETF",
            key="ai_query",
        )
    with col_d:
        ai_from_date = st.date_input("From Date", key="ai_from_date")

    with st.expander("Advanced", expanded=False):
        ai_model = st.text_input(
            "Ollama model",
            value="llama3.1:8b",
            help="Run `ollama list` to see installed models. Pull with `ollama pull llama3.1:8b`.",
            key="ai_model",
        )

    if st.button("Fetch with AI Agent", type="primary"):
        if not ai_query.strip():
            st.warning("Please enter a query.")
        else:
            try:
                from fetch_data_agent import FetchDataAgent  # noqa: PLC0415

                agent = FetchDataAgent(model=ai_model)

                with st.status("Running AI agent…", expanded=True) as _status:
                    def _log(msg: str) -> None:
                        st.write(msg)

                    try:
                        df = agent.fetch(ai_query.strip(), str(ai_from_date), log=_log)
                        _status.update(
                            label=f"Done — fetched {len(df)} rows", state="complete"
                        )
                    except Exception as _exc:
                        _status.update(label=f"Failed: {_exc}", state="error")
                        raise

                ai_key = f"{ai_query.strip().upper().replace(' ', '_')}_{ai_from_date}"
                st.session_state["datasets"][ai_key] = df
                st.session_state["last_fetched"] = ai_key
                st.success(f"Fetched **{len(df)} rows** for **{ai_query}** — added to datasets as `{ai_key}`")

                st.dataframe(df, use_container_width=True)

                buf = io.StringIO()
                df.to_csv(buf)
                csv_str = buf.getvalue()

                dl_col, cp_col = st.columns(2)
                with dl_col:
                    st.download_button(
                        label="⬇ Download CSV",
                        data=csv_str,
                        file_name=f"{ai_key}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                with cp_col:
                    st.components.v1.html(  # type: ignore
                        _copy_button_html(csv_str),
                        height=46,
                    )

            except RuntimeError as exc:
                if "Ollama" in str(exc):
                    st.error(str(exc))
                    st.info(
                        "**Quick setup:**\n"
                        "1. Download Ollama → https://ollama.com\n"
                        "2. In a terminal: `ollama serve`\n"
                        f"3. Pull the model: `ollama pull llama3.1:8b`"
                    )
                else:
                    st.error(str(exc))
            except Exception as exc:
                st.error(f"Unexpected error: {exc}")

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
