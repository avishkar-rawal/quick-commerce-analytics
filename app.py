"""streamlit run app.py"""
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import detect
import report
from load import FILES, load
from weekly import build_weekly, line_frames

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

NEEDED = {
    "daily_sales": ["date", "platform", "dark_store_id", "platform_product_id", "availability_pct",
                    "units_sold", "selling_price_inr", "sales_inr", "search_rank"],
    "dark_stores": ["dark_store_id", "platform", "city", "warehouse_id", "opened_date"],
    "products": ["platform", "platform_product_id", "sku_code", "product_name", "category"],
    "purchase_orders": ["po_id", "po_date", "platform", "warehouse_id", "sku_code",
                        "qty_ordered", "qty_delivered", "delivery_date"],
}

st.set_page_config(page_title="Joy Monday brief", layout="wide")


@st.cache_data(show_spinner="Reading data...")
def prepare(blobs):
    """blobs: None for the bundled files, else {name: bytes}"""
    src = str(DATA_DIR) if blobs is None else {k: BytesIO(v) for k, v in blobs.items()}
    data = load(src)
    return data, line_frames(build_weekly(data.sales))


@st.cache_data(show_spinner="Replaying past Mondays...")
def analyse(blobs, asof):
    data, G = prepare(blobs)
    asof = pd.Timestamp(asof)
    alerts = detect.run(data, G, asof)
    bt = detect.backtest(data, G, last=asof)
    return alerts, bt, len(G)


st.title("Joy Monday brief")
st.caption("Finds short deliveries, warehouses that stopped ordering, and lost search rank, "
           "ranked by sales at risk.")

with st.sidebar:
    st.header("Data")
    mode = st.radio("Source", ["Bundled data (7 Sep - 15 Nov 2026)", "Upload my own CSVs"])
    blobs = None
    if mode.startswith("Upload"):
        st.caption("All four files, same columns as the originals. Include a few weeks of history "
                   "before the week you care about: the baselines need it.")
        up = {k: st.file_uploader(f"{k}.csv", type="csv", key=k) for k in FILES}
        if not all(up.values()):
            st.info("Waiting for all four files.")
            st.stop()
        blobs = {k: v.getvalue() for k, v in up.items()}
        for k, raw in blobs.items():
            missing = [c for c in NEEDED[k] if c not in pd.read_csv(BytesIO(raw), nrows=1).columns]
            if missing:
                st.error(f"{k}.csv is missing columns: {', '.join(missing)}")
                st.stop()

try:
    data, G = prepare(blobs)
except Exception as e:
    st.error(f"Could not read the data: {e}")
    st.stop()

first = data.po["po_date"].min() + pd.Timedelta(days=14)
last_day = data.sales["date"].max()
mondays = [d for d in pd.date_range(first, last_day + pd.Timedelta(days=1), freq="7D")]
if not mondays:
    st.error("Not enough history: need at least 3 weeks of purchase orders.")
    st.stop()

with st.sidebar:
    st.header("Report week")
    asof = st.selectbox("Report date (Monday)", mondays[::-1], index=0,
                        format_func=lambda d: f"{d:%a %d %b %Y}")
    st.caption(f"Uses sales up to {asof - pd.Timedelta(days=1):%d %b} and purchase orders up to "
               f"{asof - pd.Timedelta(days=7):%d %b}.")
    if len(mondays) < 3:
        st.warning("Very little history, results will be thin.")

alerts, bt, n_lines = analyse(blobs, asof)
is_bundled = blobs is None
page, messages = report.render(data, alerts, bt, asof, n_lines, known=is_bundled)

open_a = sorted([a for a in alerts if a.status == "open"], key=lambda a: -a.at_risk)
c1, c2, c3 = st.columns(3)
c1.metric("Sales at risk / week", report.rs(sum(a.at_risk for a in open_a)))
c2.metric("Open issues", len(open_a))
c3.metric("Lines checked", n_lines)

t_brief, t_table, t_msg, t_replay, t_data = st.tabs(
    ["Brief", "Alerts table", "Messages", "Past weeks", "Data checks"])

with t_brief:
    components.html(page, height=1700, scrolling=True)
    st.download_button("Download this page (HTML)", page, f"monday_{asof:%Y-%m-%d}.html", "text/html")

with t_table:
    st.subheader("Alert kind definitions")
    components.html(
        """
        <style>
        body { margin: 0; font-family: "Source Sans Pro", sans-serif; font-size: 14px;
               line-height: 1.55; color: #262730; }
        p { margin: 0 0 10px 0; }
        .baseline { position: relative; color: #3b82f6; text-decoration: underline;
                    text-decoration-thickness: 1px; text-underline-offset: 3px;
                    cursor: help; }
        .baseline::after { content: attr(data-tooltip); position: absolute; z-index: 10000;
                           left: 50%; bottom: calc(100% + 8px); transform: translateX(-50%);
                           width: max-content; max-width: 280px; padding: 8px 10px;
                           border-radius: 6px; background: #1f2937; color: #fff;
                           font-size: 12px; line-height: 1.35; text-align: left;
                           box-shadow: 0 4px 12px rgba(0,0,0,.18); opacity: 0;
                           visibility: hidden; pointer-events: none;
                           transition: opacity .12s ease; }
        .baseline:hover::after, .baseline:focus::after { opacity: 1; visibility: visible; }
        </style>
        <p><b>FILL</b> — The warehouse received much less than it ordered: fill is below 70% and at least 20 units are short.</p>
        <p><b>NOPO</b> — The warehouse stopped placing purchase orders for a product it previously ordered regularly.</p>
        <p><b>RANK</b> — The product's search rank worsened by at least 3 places while stock was fine (at least 80% availability) and sales fell below expected.</p>
        <p><b>AVAIL</b> — Availability fell at least 15 percentage points below its
           <span class="baseline" tabindex="0" data-tooltip="The clean baseline is the median of the latest 4 non-problem availability weeks; it starts after 2 clean weeks.">baseline</span>
           and the shortage is not already explained by a FILL or NOPO alert.</p>
        """,
        height=180,
        scrolling=False,
    )
    df = report.alerts_frame(alerts)
    st.dataframe(
        df,
        hide_index=True,
        column_config={
            "kind": st.column_config.TextColumn(
                "Kind",
                help="Alert type: FILL (short delivery), NOPO (orders stopped), RANK (search position fell), or AVAIL (availability fell)"),
            "status": st.column_config.TextColumn(
                "Status",
                help="Open means the issue is still active; resolved means it has recovered"),
            "responsible_team": st.column_config.TextColumn(
                "Responsible team",
                help="The team responsible for taking action on this alert"),
            "platform": st.column_config.TextColumn(
                "Platform",
                help="Quick-commerce platform for this alert"),
            "city": st.column_config.TextColumn(
                "City",
                help="City for the affected warehouse"),
            "warehouse": st.column_config.TextColumn(
                "Warehouse",
                help="Platform warehouse affected by the alert"),
            "products": st.column_config.TextColumn(
                "Products",
                help="Internal SKUs grouped under this alert"),
            "onset": st.column_config.TextColumn(
                "Onset",
                help="Date the alert problem was first detected"),
            "weeks_open": st.column_config.NumberColumn(
                "Weeks open",
                help="Number of weeks the alert has been open"),
            "at_risk_per_week": st.column_config.NumberColumn(
                "At risk per week",
                help="Estimated rupees of sales at risk each week"),
            "lost_so_far": st.column_config.NumberColumn(
                "Lost so far",
                help="Estimated rupees lost since the alert began"),
        },
    )
    st.download_button("Download alerts.csv", df.to_csv(index=False), "alerts.csv", "text/csv")

with t_msg:
    if not messages:
        st.write("Nothing to send this week.")
    for team in ["Supply", "Platform KAM", "Marketing"]:
        msgs = [report.message(a, data) for a in open_a if a.owner == team]
        if msgs:
            st.subheader(team)
            st.code("\n\n".join(msgs), language=None)

with t_replay:
    st.caption("Every earlier Monday re-run using only the data that existed that day.")
    st.caption("**at_risk_in_Rupees**: estimated sales in rupees per week that were being lost, or about "
               "to be lost, for that alert on that date (rough estimate, about +/-30%).\n\n"
               "**skus**: the product codes in the alert. A SKU is Joy's own internal code for one "
               "product and pack size, e.g. JY-FW-NEEM-100 = Neem Face Wash 100ml.")
    st.subheader("Alert kind definitions")
    components.html(
        """
        <style>
        body { margin: 0; font-family: "Source Sans Pro", sans-serif; font-size: 14px;
               line-height: 1.55; color: #262730; }
        p { margin: 0 0 10px 0; }
        .baseline { position: relative; color: #3b82f6; text-decoration: underline;
                    text-decoration-thickness: 1px; text-underline-offset: 3px;
                    cursor: help; }
        .baseline::after { content: attr(data-tooltip); position: absolute; z-index: 10000;
                           left: 50%; bottom: calc(100% + 8px); transform: translateX(-50%);
                           width: max-content; max-width: 280px; padding: 8px 10px;
                           border-radius: 6px; background: #1f2937; color: #fff;
                           font-size: 12px; line-height: 1.35; text-align: left;
                           box-shadow: 0 4px 12px rgba(0,0,0,.18); opacity: 0;
                           visibility: hidden; pointer-events: none;
                           transition: opacity .12s ease; }
        .baseline:hover::after, .baseline:focus::after { opacity: 1; visibility: visible; }
        </style>
        <p><b>FILL</b> — The warehouse received much less than it ordered: fill is below 70% and at least 20 units are short.</p>
        <p><b>NOPO</b> — The warehouse stopped placing purchase orders for a product it previously ordered regularly.</p>
        <p><b>RANK</b> — The product's search rank worsened by at least 3 places while stock was fine (at least 80% availability) and sales fell below expected.</p>
        <p><b>AVAIL</b> — Availability fell at least 15 percentage points below its
           <span class="baseline" tabindex="0" data-tooltip="The clean baseline is the median of the latest 4 non-problem availability weeks; it starts after 2 clean weeks.">baseline</span>
           and the shortage is not already explained by a FILL or NOPO alert.</p>
        """,
        height=180,
        scrolling=False,
    )
    st.dataframe(bt, hide_index=True, column_config={
        "asof": st.column_config.TextColumn(
            "asof",
            help="Monday for which this past report was replayed"),
        "kind": st.column_config.TextColumn(
            "kind",
            help="Alert type: FILL, NOPO, RANK, or AVAIL"),
        "warehouse": st.column_config.TextColumn(
            "warehouse",
            help="Platform warehouse affected by the alert"),
        "platform": st.column_config.TextColumn(
            "platform",
            help="Quick-commerce platform for this alert"),
        "city": st.column_config.TextColumn(
            "city",
            help="City for the affected warehouse"),
        "n_skus": st.column_config.NumberColumn(
            "n_skus",
            help="Number of products grouped in the alert"),
        "at_risk_in_Rupees": st.column_config.NumberColumn(
            "at_risk_in_Rupees", help="Estimated Rs per week at risk on that report date", format="%d"),
        "skus": st.column_config.TextColumn(
            "skus", help="Joy's internal product codes (SKU = stock keeping unit) in this alert"),
        "onset": st.column_config.TextColumn(
            "onset", help="Date the alert problem was first detected")})

with t_data:
    n = data.notes
    st.write(f"Duplicate sales rows dropped: **{n['duplicate sales rows dropped']}**")
    st.write(f"City spellings merged: **{n['city spellings before -> after'][0]} -> "
             f"{n['city spellings before -> after'][1]}**")
    st.write(f"Stores opened after the first week: **{n['stores opened after 7 Sep']}**; "
             f"{n['share of rows left out']:.0%} of sales rows come from stores under 35 days old and "
             "are left out.")
    st.write(f"Sales from {data.sales['date'].min():%d %b %Y} to {last_day:%d %b %Y}; "
             f"POs from {data.po['po_date'].min():%d %b %Y} to {data.po['po_date'].max():%d %b %Y}.")
