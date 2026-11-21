"""python run.py [--asof 2026-11-16] [--data <folder with the 4 csvs>] [--out out]"""
import argparse
from pathlib import Path

import pandas as pd

import detect
import report
from load import load
from weekly import build_weekly, line_frames

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2026-11-16", help="a Monday; report uses data before it")
    ap.add_argument("--data", default="data",
                    help="folder with the four CSVs; relative to this script")
    ap.add_argument("--out", default="out",
                    help="output folder; relative to this script")
    a = ap.parse_args()

    asof = pd.Timestamp(a.asof)
    if asof.dayofweek != 0:
        raise SystemExit("asof must be a Monday")

    data_path = Path(a.data)
    if not data_path.is_absolute():
        data_path = HERE / data_path
    data = load(data_path)
    if asof > data.sales["date"].max() + pd.Timedelta(days=1):
        raise SystemExit(f"no sales data after {data.sales['date'].max():%d %b %Y}, pick an earlier Monday")
    G = line_frames(build_weekly(data.sales))
    n_lines = len(G)

    alerts = detect.run(data, G, asof)
    bt = detect.backtest(data, G, last=asof)
    out_path = Path(a.out)
    if not out_path.is_absolute():
        out_path = HERE / out_path
    path = report.build(data, alerts, bt, asof, n_lines, out_path)

    open_a = [x for x in alerts if x.status == "open"]
    print(f"{len(open_a)} open issues, {sum(x.at_risk for x in open_a):,.0f} Rs/week at risk")
    for x in sorted(open_a, key=lambda x: -x.at_risk):
        print(f"  {x.owner:13s} {x.kind:5s} {x.platform} {x.city} {x.warehouse}  "
              f"{x.at_risk:,.0f}/wk  since {x.onset:%d %b}")
    print("report:", path)


if __name__ == "__main__":
    main()
