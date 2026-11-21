"""Weekly table per warehouse x sku, using only mature stores."""
import pandas as pd


def build_weekly(sales):
    s = sales[sales["mature"]]
    grp = ["platform", "warehouse_id", "city", "sku_code", "week"]
    w = s.groupby(grp).agg(
        units=("units_sold", "sum"),
        sales=("sales_inr", "sum"),
        store_days=("units_sold", "size"),
        avail=("availability_pct", "mean"),
        rank=("search_rank", "mean"),
        price=("selling_price_inr", "mean"),
    ).reset_index()
    w["ups"] = w["units"] / w["store_days"]          # units per store-day

    # "control": same sku on the same app, every warehouse except this one.
    # It soaks up festive season swings so we only react to local problems.
    tot = w.groupby(["platform", "sku_code", "week"])[["units", "store_days"]].transform("sum")
    w["ctl"] = (tot["units"] - w["units"]) / (tot["store_days"] - w["store_days"])
    return w


def line_frames(w):
    """dict of (warehouse, sku) -> frame indexed by week"""
    out = {}
    for key, g in w.groupby(["warehouse_id", "sku_code"]):
        out[key] = g.set_index("week").sort_index()
    return out
