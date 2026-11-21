"""Read the four exports, clean them, and keep a log of what was fixed."""
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

# same city, different spellings across apps / rows
CITY_MAP = {
    "bangalore": "Bengaluru", "bengaluru": "Bengaluru",
    "delhi": "Delhi NCR", "new delhi": "Delhi NCR", "delhi ncr": "Delhi NCR",
    "mumbai": "Mumbai", "pune": "Pune", "hyderabad": "Hyderabad", "kolkata": "Kolkata",
}

# a store needs about 5 weeks to ramp up, before that its sales say nothing about demand
MIN_STORE_AGE_DAYS = 35


FILES = ["daily_sales", "dark_stores", "products", "purchase_orders"]


def load(src):
    """src: a folder holding the four csvs, or a dict {name: path or file object}"""
    if isinstance(src, dict):
        f = {k: src[k] for k in FILES}
    else:
        f = {k: Path(src) / f"{k}.csv" for k in FILES}
    notes = {}

    sales = pd.read_csv(f["daily_sales"], dtype={"platform_product_id": str},
                        parse_dates=["date"])
    stores = pd.read_csv(f["dark_stores"], parse_dates=["opened_date"])
    prods = pd.read_csv(f["products"], dtype={"platform_product_id": str})
    po = pd.read_csv(f["purchase_orders"], parse_dates=["po_date", "delivery_date"])

    # exact duplicate rows in sales (checked: none of them conflict with each other)
    n0 = len(sales)
    sales = sales.drop_duplicates().reset_index(drop=True)
    notes["duplicate sales rows dropped"] = n0 - len(sales)
    key = ["date", "dark_store_id", "platform_product_id"]
    assert not sales.duplicated(key).any(), "conflicting duplicates in daily_sales"

    stores["city_raw"] = stores["city"]
    stores["city"] = stores["city"].str.strip().str.lower().map(CITY_MAP)
    if stores["city"].isna().any():
        bad = stores.loc[stores["city"].isna(), "city_raw"].unique()
        raise ValueError(f"unmapped city names: {bad}")
    notes["city spellings before -> after"] = (stores["city_raw"].nunique(),
                                               stores["city"].nunique())

    sales = sales.merge(prods[["platform", "platform_product_id", "sku_code"]],
                        on=["platform", "platform_product_id"], how="left", validate="m:1")
    assert sales["sku_code"].notna().all(), "sales rows with unknown product id"
    sales = sales.merge(stores[["dark_store_id", "warehouse_id", "city", "opened_date"]],
                        on="dark_store_id", how="left", validate="m:1")
    assert sales["warehouse_id"].notna().all(), "sales rows with unknown store"

    sales["store_age"] = (sales["date"] - sales["opened_date"]).dt.days
    sales["mature"] = sales["store_age"] >= MIN_STORE_AGE_DAYS
    sales["week"] = sales["date"] - pd.to_timedelta(sales["date"].dt.dayofweek, unit="D")

    notes["stores opened after 7 Sep"] = int((stores["opened_date"] > "2026-09-07").sum())
    notes["store-days left out as too new"] = int((~sales["mature"]).sum())
    notes["share of rows left out"] = round(float((~sales["mature"]).mean()), 3)

    po["fill"] = po["qty_delivered"] / po["qty_ordered"]
    names = prods.drop_duplicates("sku_code").set_index("sku_code")["product_name"].to_dict()

    return SimpleNamespace(sales=sales, stores=stores, prods=prods, po=po,
                           names=names, notes=notes)
