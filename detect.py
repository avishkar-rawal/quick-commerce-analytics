"""Three detectors (fill rate, missing PO, rank drop) plus a catch-all for availability.

Everything is run "as of" a Monday: sales up to the Sunday before, purchase orders up to
the PO dated a week before (later POs are not delivered yet, so fill rate is unknown).
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

FILL_MIN = 0.70        # normal fill is 94-99%, nothing outside the real incidents goes below 0.74
FILL_MIN_UNITS = 20    # ignore tiny POs where rounding makes the % look bad
RANK_JUMP = 3          # positions worse than the clean baseline
RANK_MIN_AVAIL = 80    # rank problem only if stock is fine, otherwise it is a stock problem
AVAIL_DROP = 15        # points below the clean baseline
MIN_RS = 2000          # per week, for rank / availability alerts
NOPO_FLOOR = 1.0       # a missing PO means the line is heading to zero, so count all its expected sales

OWNER = {"FILL": "Supply", "NOPO": "Platform KAM", "RANK": "Marketing", "AVAIL": "Platform KAM"}
WEEK = pd.Timedelta(days=7)


@dataclass
class Alert:
    kind: str
    status: str                  # open / resolved
    platform: str
    city: str
    warehouse: str
    lines: list = field(default_factory=list)   # [{sku, name, note}]
    onset: pd.Timestamp = None
    weeks_open: int = 0
    at_risk: float = 0.0         # Rs per week still being lost / about to be lost
    lost: float = 0.0            # Rs lost since onset (estimate)
    lost_weekly: dict = field(default_factory=dict)
    series: list = field(default_factory=list)
    series_weeks: list = field(default_factory=list)
    recent: bool = True

    @property
    def owner(self):
        return OWNER[self.kind]


def track(values, is_bad):
    """Walk a weekly series with a baseline that only learns from 'clean' weeks.

    A plain rolling median slowly adopts a long running problem as the new normal
    (the rank alert vanished after a month when I tried it), so weeks that look bad are
    kept out of the baseline. Returns (baseline, index where the current bad run began).
    """
    clean, onset, base = [], None, None
    for i, v in enumerate(values):
        if np.isnan(v):
            continue
        if len(clean) >= 2:
            base = float(np.median(clean[-4:]))
            if is_bad(v, base):
                if onset is None:
                    onset = i
                continue
        clean.append(v)
        onset = None
    return base, onset


def episodes(dates, gap_days):
    """split sorted dates into runs where neighbours are at most gap_days apart"""
    out = []
    for d in sorted(dates):
        if out and (d - out[-1][-1]).days <= gap_days:
            out[-1].append(d)
        else:
            out.append([d])
    return out


def shortfall(g, onset, end):
    """Expected vs actual sales for a line from `onset`.

    Expected = own baseline (4 weeks before onset) scaled by how the same sku did on the
    same app elsewhere. Returns (lost Rs per week, expected Rs per week, frame) or None.
    """
    prior = g[g.index < onset].tail(4)
    if len(prior) < 1:      # early incidents only have a week or two of history
        return None
    w = g[(g.index >= onset) & (g.index <= end)]
    if w.empty:
        return None
    exp_ups = prior["ups"].median() * w["ctl"] / prior["ctl"].median()
    lost = ((exp_ups - w["ups"]).clip(lower=0) * w["store_days"] * w["price"])
    exp_rs = exp_ups * w["store_days"] * w["price"]
    return lost, exp_rs, w


def _mk_line(kind, status, wh, sku, onset, end, g, note, recent, floor=0.0):
    sf = shortfall(g, onset, end)
    if sf is None:
        lost, at_risk = pd.Series(dtype=float), 0.0
    else:
        lost, exp_rs, w = sf
        if kind in ("FILL", "NOPO"):
            note += f"; availability {w['avail'].iloc[-1]:.0f}% last week"
        if status == "open":
            if kind in ("FILL", "NOPO"):
                at_risk = exp_rs.iloc[-1] * max(1 - w["avail"].iloc[-1] / 100, floor)
            else:
                at_risk = lost.tail(2).mean()
        else:
            at_risk = 0.0
    return dict(kind=kind, status=status, wh=wh, sku=sku, onset=onset, lost=lost,
                at_risk=float(at_risk), note=note, recent=recent, end=end)


def fill_lines(po, G, last_week):
    out = []
    p = po[po["po_date"] <= last_week]
    bad = p[(p["fill"] < FILL_MIN) & ((p["qty_ordered"] - p["qty_delivered"]) >= FILL_MIN_UNITS)]
    for (wh, sku), b in bad.groupby(["warehouse_id", "sku_code"]):
        g = G.get((wh, sku))
        if g is None:
            continue
        g = g[g.index <= last_week]
        for ep in episodes(b["po_date"], 14):
            first, last = ep[0], ep[-1]
            is_open = last >= last_week      # a good PO after the bad one closes it
            end = last_week if is_open else min(last + WEEK, last_week)
            r = b[b["po_date"] == last].iloc[0]
            note = (f"PO {last:%d %b}: ordered {r.qty_ordered}, delivered {r.qty_delivered} "
                    f"({r.fill:.0%}), {r.qty_ordered - r.qty_delivered} units short")
            out.append(_mk_line("FILL", "open" if is_open else "resolved", wh, sku, first, end,
                                g, note, last >= last_week - 5 * WEEK))
    return out


def nopo_lines(po, G, last_week):
    out = []
    p = po[po["po_date"] <= last_week]
    mondays = pd.date_range(p["po_date"].min(), last_week, freq="7D")
    for (wh, sku), grp in p.groupby(["warehouse_id", "sku_code"]):
        have = set(grp["po_date"])
        present = [m in have for m in mondays]
        n, i = len(mondays), 0
        while i < n:
            if present[i]:
                i += 1
                continue
            j = i
            while j < n and not present[j]:
                j += 1
            before = present[max(0, i - 4):i]
            # only a problem if this line used to order regularly
            if len(before) >= 1 and sum(before) >= min(3, len(before)):
                g = G.get((wh, sku))
                if g is None:
                    i = j
                    continue
                g = g[g.index <= last_week]
                is_open = j == n
                end = last_week if is_open else min(mondays[j - 1] + WEEK, last_week)
                miss = ", ".join(f"{m:%d %b}" for m in mondays[i:j])
                note = f"no PO on {miss}"
                out.append(_mk_line("NOPO", "open" if is_open else "resolved", wh, sku,
                                    mondays[i], end, g, note, mondays[j - 1] >= last_week - 5 * WEEK,
                                    floor=NOPO_FLOOR))
            i = j
    return out


def rank_and_avail_lines(G, last_week, skip):
    out = []
    for (wh, sku), g in G.items():
        g = g[g.index <= last_week]
        if len(g) < 3:
            continue
        base, onset = track(g["rank"].tolist(), lambda v, b: v - b >= RANK_JUMP)
        if onset is not None:
            idx = g.index[onset]
            last = g.iloc[-1]
            sf = shortfall(g, idx, last_week)
            if sf is not None and last["avail"] >= RANK_MIN_AVAIL:
                lost, exp_rs, w = sf
                if last["ups"] < 0.8 * (exp_rs.iloc[-1] / (last["store_days"] * last["price"])):
                    ln = _mk_line("RANK", "open", wh, sku, idx, last_week, g,
                                  f"avg rank {base:.1f} -> {last['rank']:.1f}, availability {last['avail']:.0f}%",
                                  True)
                    if ln["at_risk"] >= MIN_RS:
                        out.append(ln)
                    continue
        if (wh, sku) in skip:
            continue
        base, onset = track(g["avail"].tolist(), lambda v, b: b - v >= AVAIL_DROP)
        if onset is not None:
            idx = g.index[onset]
            ln = _mk_line("AVAIL", "open", wh, sku, idx, last_week, g,
                          f"availability {base:.0f}% -> {g['avail'].iloc[-1]:.0f}%", True)
            if ln["at_risk"] >= MIN_RS:
                out.append(ln)
    return out


def _series(kind, lines, po, G, weeks):
    skus = [l["sku"] for l in lines]
    wh = lines[0]["wh"]
    vals = []
    if kind in ("FILL", "NOPO"):
        p = po[(po["warehouse_id"] == wh) & (po["sku_code"].isin(skus))]
        by = p.groupby("po_date")
        for w in weeks:
            if w not in by.groups:
                vals.append(0.0 if kind == "NOPO" else None)
                continue
            r = by.get_group(w)
            vals.append(float(r["qty_ordered"].sum()) if kind == "NOPO"
                        else float(r["qty_delivered"].sum() / r["qty_ordered"].sum() * 100))
    else:
        col = "rank" if kind == "RANK" else "avail"
        for w in weeks:
            xs = [G[(wh, s)].loc[w, col] for s in skus if w in G[(wh, s)].index]
            vals.append(float(np.mean(xs)) if xs else None)
    return vals


def run(data, G, asof):
    """All alerts for a report dated `asof` (a Monday)."""
    asof = pd.Timestamp(asof)
    last_week = asof - WEEK
    po = data.po

    lines = fill_lines(po, G, last_week) + nopo_lines(po, G, last_week)
    # a line that was fixed only recently can still have thin shelves, that is the same incident
    explained = {(l["wh"], l["sku"]) for l in lines if l["status"] == "open" or l["recent"]}
    lines += rank_and_avail_lines(G, last_week, explained)

    plat = data.stores.drop_duplicates("warehouse_id").set_index("warehouse_id")[["platform", "city"]]
    weeks = list(pd.date_range(po["po_date"].min(), last_week, freq="7D"))
    alerts = []
    keyf = lambda l: (l["kind"], l["wh"], l["status"])
    for key in sorted({keyf(l) for l in lines}):
        ls = [l for l in lines if keyf(l) == key]
        kind, wh, status = key
        a = Alert(kind=kind, status=status, platform=plat.loc[wh, "platform"],
                  city=plat.loc[wh, "city"], warehouse=wh)
        a.lines = [dict(sku=l["sku"], name=data.names[l["sku"]], note=l["note"]) for l in ls]
        a.onset = min(l["onset"] for l in ls)
        a.weeks_open = max(1, (asof - a.onset).days // 7) if status == "open" else             max(1, (max(l["end"] for l in ls) - a.onset).days // 7 + 1)
        a.at_risk = sum(l["at_risk"] for l in ls)
        a.recent = any(l["recent"] for l in ls)
        tot = pd.concat([l["lost"] for l in ls], axis=1).sum(axis=1) if ls else pd.Series(dtype=float)
        a.lost_weekly = {k: float(v) for k, v in tot.items()}
        a.lost = float(tot.sum())
        a.series, a.series_weeks = _series(kind, ls, po, G, weeks), weeks
        alerts.append(a)
    return alerts


def backtest(data, G, last, first=None):
    """replay every Monday from the second PO week up to `last`"""
    first = pd.Timestamp(first) if first is not None else data.po["po_date"].min() + WEEK
    rows = []
    for asof in pd.date_range(first, last, freq="7D"):
        for a in run(data, G, asof):
            if a.status == "open":
                rows.append(dict(asof=asof.date(), kind=a.kind, warehouse=a.warehouse,
                                 platform=a.platform, city=a.city, n_skus=len(a.lines),
                                 skus=", ".join(l["sku"] for l in a.lines),
                                 at_risk_in_Rupees=round(a.at_risk), onset=a.onset.date()))
    return pd.DataFrame(rows)
