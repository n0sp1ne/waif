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
        options=["Overview", "Section 1", "Section 2", "Settings"],
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

        # ── Portfolio calculation ──────────────────────────────────────────────
        if selected_keys:
            st.divider()
            st.markdown("### Portfolio Performance")

            series_dict: dict = {}
            for key in selected_keys:
                df    = datasets[key]
                close = df["Close"].dropna()
                if close.empty:
                    st.warning(f"{key}: no Close data, skipped.")
                    continue
                base             = float(close.iloc[0])
                initial          = weight_map[key] * 100.0
                series_dict[key] = (close / base) * initial

            if series_dict:
                combined          = pd.DataFrame(series_dict)
                combined.index    = pd.to_datetime(combined.index)
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

                # Benchmark comparison metrics
                if has_benchmark:
                    bm_col = f"Benchmark ({benchmark_key})"
                    bm_start = float(combined[bm_col].iloc[0])
                    bm_end   = float(combined[bm_col].iloc[-1])
                    bm_ret   = ((bm_end - bm_start) / bm_start) * 100 if bm_start else 0.0
                    alpha    = ret_pct - bm_ret

                    m1, m2, m3, m4, m5 = st.columns(5)
                    m1.metric("Initial Investment", f"₹{t_start:,.2f}")
                    m2.metric("Current Value",       f"₹{t_end:,.2f}")
                    m3.metric("P&L",                 f"₹{pnl:+,.2f}")
                    m4.metric("Portfolio Return",    f"{ret_pct:+.2f}%")
                    m5.metric("Alpha vs Benchmark",  f"{alpha:+.2f}%",
                              delta=f"Benchmark: {bm_ret:+.2f}%")
                else:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Initial Investment", f"₹{t_start:,.2f}")
                    m2.metric("Current Value",       f"₹{t_end:,.2f}")
                    m3.metric("P&L",                 f"₹{pnl:+,.2f}")
                    m4.metric("Total Return",        f"{ret_pct:+.2f}%")

                st.divider()
                st.markdown("**Daily Portfolio Value**")
                st.line_chart(combined, use_container_width=True)

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
