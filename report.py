"""Turn alerts into the Monday page, the per-team messages and a couple of CSVs."""
import html
import json
import math
from pathlib import Path

import pandas as pd

# the three problems from the brief, so the backtest can check we would have seen them
KNOWN = [
    dict(label="Blinkit Bengaluru: POs delivered about half", kind="FILL", platform="Blinkit",
         city="Bengaluru", noticed_weeks=5, who="Supply"),
    dict(label="Zepto Bengaluru: face wash fell from ~3rd to ~13th", kind="RANK", platform="Zepto",
         city="Bengaluru", noticed_weeks=4, who="Marketing"),
    dict(label="Instamart Hyderabad: sunscreen not ordered for 2 weeks", kind="NOPO",
         platform="Instamart", city="Hyderabad", noticed_weeks=3, who="Instamart KAM"),
]

KIND_TITLE = {
    "FILL": "Short deliveries",
    "NOPO": "Warehouse stopped ordering",
    "RANK": "Lost search position",
    "AVAIL": "Stock-outs, cause unclear",
}
CHIP = {"Supply": "#2f6f5e", "Platform KAM": "#8a5a14", "Marketing": "#6b3f8c"}


def rs(x):
    x = float(x)
    if abs(x) >= 100000:
        return f"Rs {x / 100000:.1f}L"
    if abs(x) >= 1000:
        return f"Rs {x / 1000:.0f}k"
    return f"Rs {x:.0f}"


def priority(a):
    # money first, but something that has been bleeding for 3+ weeks moves up a notch
    if a.at_risk >= 15000 or (a.at_risk >= 8000 and a.weeks_open >= 3):
        return "High"
    return "Medium" if a.at_risk >= 5000 else "Low"


def spark(vals, mark=None, w=150, h=38, invert=False, bars=False):
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    n = len(vals)
    pad = 4

    def xy(i, v):
        x = pad + i * (w - 2 * pad) / (n - 1)
        t = (v - lo) / (hi - lo)
        y = pad + t * (h - 2 * pad) if invert else h - pad - t * (h - 2 * pad)
        return x, y

    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">']
    if bars:
        bw = (w - 2 * pad) / n * 0.7
        for i, v in pts:
            x, y = xy(i, v)
            col = "#b3472f" if (mark is not None and i >= mark) else "#9aa5a0"
            out.append(f'<rect x="{x - bw / 2:.1f}" y="{min(y, h - pad):.1f}" width="{bw:.1f}" '
                       f'height="{max(h - pad - y, 1):.1f}" fill="{col}"/>')
    else:
        d = " ".join(("M" if k == 0 else "L") + f"{xy(i, v)[0]:.1f},{xy(i, v)[1]:.1f}"
                     for k, (i, v) in enumerate(pts))
        out.append(f'<path d="{d}" fill="none" stroke="#5d6b66" stroke-width="1.6"/>')
        if mark is not None:
            mp = [(i, v) for i, v in pts if i >= mark]
            if mp:
                d2 = " ".join(("M" if k == 0 else "L") + f"{xy(i, v)[0]:.1f},{xy(i, v)[1]:.1f}"
                              for k, (i, v) in enumerate(mp))
                out.append(f'<path d="{d2}" fill="none" stroke="#b3472f" stroke-width="2.2"/>')
        x, y = xy(*pts[-1])
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="#b3472f"/>')
    out.append("</svg>")
    return "".join(out)


def headline(a):
    n = len(a.lines)
    names = ", ".join(l["name"].replace("Joy ", "") for l in a.lines[:3])
    if n > 3:
        names += f" +{n - 3} more"
    where = f"{a.platform} {a.city} ({a.warehouse})"
    return {
        "FILL": f"{where}: {n} product{'s' if n > 1 else ''} short-delivered",
        "NOPO": f"{where}: no order for {names}",
        "RANK": f"{where}: {names} dropped in search",
        "AVAIL": f"{where}: availability fell on {n} product{'s' if n > 1 else ''}",
    }[a.kind]


def action(a):
    return {
        "FILL": "Supply: ship the balance and confirm an ETA with the warehouse today; "
                "check why this PO was only part-fulfilled.",
        "NOPO": "KAM: ask the platform why the warehouse stopped ordering (listing blocked? "
                "paused? PO missed?) and get it re-raised before stores empty out.",
        "RANK": "Marketing: look at the listing and competitor prices, and test a sponsored "
                "placement on the category search for the affected city.",
        "AVAIL": "KAM: stock is thin though POs look normal. Ask the platform whether it is "
                 "holding or mis-routing stock to these stores.",
    }[a.kind]


def message(a, data):
    """plain text, ready to paste into WhatsApp"""
    where = f"{a.platform} {a.city} ({a.warehouse})"
    if a.kind == "FILL":
        rows = "\n".join(f"- {l['name']}: {l['note']}" for l in a.lines)
        return (f"*Supply: short delivery, {where}*\n{rows}\n"
                f"Shelves are already thinning (see availability above). "
                f"Please ship what is pending and confirm ETA today.")
    if a.kind == "NOPO":
        rows = "\n".join(f"- {l['name']}: {l['note']}" for l in a.lines)
        return (f"*{a.platform} KAM: no PO from {a.warehouse} ({a.city})*\n{rows}\n"
                f"This warehouse ordered these every week before. Can you check if the product is "
                f"blocked or paused at your end and get ordering restarted? "
                f"Roughly {rs(a.at_risk)} a week of sales is at stake.")
    if a.kind == "RANK":
        l = a.lines[0]
        cat = data.prods.loc[data.prods["sku_code"] == l["sku"], "category"].iloc[0]
        return (f"*Marketing: {l['name']} on {a.platform} {a.city}*\n{l['note']}.\n"
                f"Slipping since week of {a.onset:%d %b} ({a.weeks_open} wks). Stock is fine, so this is "
                f"visibility, not supply. Sales are about {rs(a.at_risk)}/week below expected, "
                f"{rs(a.lost)} lost so far. Suggest ads on the '{cat}' search and a listing check.")
    rows = "\n".join(f"- {l['name']}: {l['note']}" for l in a.lines)
    return (f"*{a.platform} KAM: availability dropping, {a.city} ({a.warehouse})*\n{rows}\n"
            f"POs look normal, so please check stock allocation on your side.")


def chart_spec(a, unit):
    """everything the click-to-expand chart needs, as a dict (goes into a data attribute)"""
    vals = a.series
    nums = [v for v in vals if v is not None]
    if a.kind in ("FILL", "AVAIL"):
        y0, y1, ytitle = 0, 100, "%"
    elif a.kind == "NOPO":
        y0, y1, ytitle = 0, max(nums + [1]) * 1.15, "units"
    else:
        y0, y1, ytitle = 1, max(8, math.ceil(max(nums + [8])) + 1), "rank (1 = top)"
    onset_i = next((i for i, w in enumerate(a.series_weeks) if w >= a.onset), None)
    return dict(weeks=[f"{w:%d %b}" for w in a.series_weeks], vals=vals, onset=onset_i,
                y0=y0, y1=y1, ytitle=ytitle, invert=a.kind == "RANK", bars=a.kind == "NOPO",
                title=unit, xtitle="PO week (Monday)" if a.kind in ("FILL", "NOPO") else "week starting",
                vlabel={"FILL": "Fill rate", "NOPO": "Units ordered", "RANK": "Avg rank",
                        "AVAIL": "Availability"}[a.kind],
                suffix="%" if a.kind in ("FILL", "AVAIL") else "")


def card(a, data, muted=False):
    title = headline(a)
    col = CHIP[a.owner]
    inv = a.kind == "RANK"
    onset_i = next((i for i, w in enumerate(a.series_weeks) if w >= a.onset), None)
    sp = spark(a.series, mark=onset_i, invert=inv, bars=a.kind == "NOPO")
    unit = {"FILL": "fill % by PO week", "NOPO": "units ordered by PO week",
            "RANK": "avg rank (up = better)", "AVAIL": "availability %"}[a.kind]
    lines = "".join(f"<li><b>{html.escape(l['name'])}</b> <span>{html.escape(l['note'])}</span></li>"
                    for l in a.lines)
    if muted:
        meta = f"fixed &middot; lost about {rs(a.lost)} while it ran"
        tag = '<span class="pri res">Resolved</span>'
    else:
        meta = (f"open {a.weeks_open} wk &middot; since {a.onset:%d %b} &middot; "
                f"lost so far {rs(a.lost)}")
        tag = f'<span class="pri {priority(a).lower()}">{priority(a)}</span>'
    risk = "" if muted else f'<div class="risk">{rs(a.at_risk)}<small>/ week at risk</small></div>'
    act = "" if muted else f'<p class="act">{html.escape(action(a))}</p>'
    return f"""<article class="card{' muted' if muted else ''}">
  <div class="top"><div>{tag}<span class="chip" style="background:{col}">{a.owner}</span>
  <span class="kind">{KIND_TITLE[a.kind]}</span></div>{risk}</div>
  <h3>{html.escape(title)}</h3>
  <div class="meta">{meta}</div>
  <div class="mid"><ul>{lines}</ul><div class="sp" title="click to expand" data-chart="{html.escape(json.dumps(chart_spec(a, unit)))}">{sp}<small>{unit}</small><button type="button" class="xbtn">&#9662; Expand</button></div></div>
  <div class="big" hidden></div>
  {act}
</article>"""


def known_table(bt, alerts):
    rows = []
    for k in KNOWN:
        hit = bt if bt.empty else bt[(bt["kind"] == k["kind"]) & (bt["platform"] == k["platform"])
                 & (bt["city"] == k["city"])]
        fa = [a for a in alerts if a.kind == k["kind"] and a.platform == k["platform"]
              and a.city == k["city"]]
        if hit.empty or not fa:
            rows.append(f"<tr><td>{k['label']}</td><td colspan=4>not caught</td></tr>")
            continue
        first = pd.Timestamp(hit["asof"].min())
        a = min(fa, key=lambda x: x.onset)
        late = (first - a.onset).days // 7
        saved = sum(v for wk, v in a.lost_weekly.items() if pd.Timestamp(wk) >= first)
        rows.append(
            f"<tr><td>{k['label']}<br><small>{k['who']}</small></td>"
            f"<td>{a.onset:%d %b}</td><td><b>{first:%d %b}</b><br><small>{late} wk after it began"
            f"</small></td><td>{k['noticed_weeks']} wk</td><td>up to {rs(saved)}</td></tr>")
    return ("<table><tr>"
            "<th data-tooltip=\"One of the three known problems described in the assignment.\">Problem from the brief</th>"
            "<th data-tooltip=\"When the problem first began in the data.\">Began</th>"
            "<th data-tooltip=\"The first Monday replay in which the problem was detected.\">Our report would have flagged</th>"
            "<th data-tooltip=\"How late the problem was noticed before this tool was built.\">Actually noticed after</th>"
            "<th data-tooltip=\"Estimated sales that could have been saved if the alert was acted on immediately.\">Sales that could have been saved (if fixed that day)</th>"
            "</tr>"
            + "".join(rows) + "</table>")


def week_table(bt):
    if bt.empty:
        return "<p class='foot'>No alerts in the replay.</p>"
    allw = sorted(bt["asof"].unique())
    rows = "".join(
        f"<tr><td>{pd.Timestamp(w):%d %b}</td><td>{(bt['asof'] == w).sum()}</td>"
        f"<td>{', '.join(sorted(set(bt[bt['asof'] == w]['warehouse'])))}</td></tr>" for w in allw)
    return ("<table><tr>"
            "<th data-tooltip=\"Monday of the replayed report.\">Report date</th>"
            "<th data-tooltip=\"Number of open alert groups on this report date.\">Alerts</th>"
            "<th data-tooltip=\"Warehouses that had at least one open alert.\">Warehouses</th>"
            "</tr>" + rows + "</table>")


JS = r'''<script>
function chartSvg(d,host){
  var W=760,H=340,L=56,R=24,T=34,B=58,pw=W-L-R,ph=H-T-B,n=d.vals.length;
  function X(i){return L+(d.bars?(i+0.5)*pw/n:i*pw/(n-1));}
  function Y(v){var t=(v-d.y0)/(d.y1-d.y0);return d.invert?T+t*ph:T+ph-t*ph;}
  var s='<svg viewBox="0 0 '+W+' '+H+'" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI,sans-serif" font-size="12">';
  s+='<text x="'+L+'" y="18" font-size="13" font-weight="600" fill="#232b28">'+d.title+'</text>';
  for(var k=0;k<=4;k++){
    var v=d.y0+(d.y1-d.y0)*k/4, y=Y(v);
    s+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+y+'" y2="'+y+'" stroke="#e5dfd0"/>';
    s+='<text x="'+(L-8)+'" y="'+(y+4)+'" text-anchor="end" fill="#6a736f">'+(d.y1-d.y0>20?Math.round(v):v.toFixed(1).replace(/\.0$/,''))+'</text>';
  }
  s+='<text x="14" y="'+(T+ph/2)+'" transform="rotate(-90 14 '+(T+ph/2)+')" text-anchor="middle" fill="#6a736f">'+d.ytitle+'</text>';
  s+='<text x="'+(L+pw/2)+'" y="'+(H-6)+'" text-anchor="middle" fill="#6a736f">'+d.xtitle+'</text>';
  for(var i=0;i<n;i++) s+='<text x="'+X(i)+'" y="'+(T+ph+18)+'" text-anchor="middle" fill="#6a736f">'+d.weeks[i]+'</text>';
  if(d.onset!==null && d.onset!==undefined){
    var xo=d.bars?X(d.onset)-pw/n/2:X(d.onset);
    s+='<line x1="'+xo+'" x2="'+xo+'" y1="'+T+'" y2="'+(T+ph)+'" stroke="#b3472f" stroke-dasharray="4 3"/>';
    s+='<text x="'+(xo+4)+'" y="'+(T+12)+'" fill="#b3472f">problem starts</text>';
  }
  function col(i){return (d.onset!==null && d.onset!==undefined && i>=d.onset)?'#b3472f':'#5d6b66';}
  if(d.bars){
    var bw=pw/n*0.62;
    for(var i=0;i<n;i++){var v=d.vals[i]; if(v===null)continue; var y=Y(v);
      s+='<rect x="'+(X(i)-bw/2)+'" y="'+y+'" width="'+bw+'" height="'+Math.max(T+ph-y,1)+'" fill="'+col(i)+'" opacity=".85"></rect>';
      s+='<text x="'+X(i)+'" y="'+(y-5)+'" text-anchor="middle" fill="#232b28">'+Math.round(v)+'</text>';}
  } else {
    for(var i=0;i<n-1;i++){var a=d.vals[i],b=d.vals[i+1]; if(a===null||b===null)continue;
      s+='<line x1="'+X(i)+'" y1="'+Y(a)+'" x2="'+X(i+1)+'" y2="'+Y(b)+'" stroke="'+col(i+1)+'" stroke-width="2.4"/>';}
    for(var i=0;i<n;i++){var v=d.vals[i]; if(v===null)continue;
      s+='<circle cx="'+X(i)+'" cy="'+Y(v)+'" r="4" fill="'+col(i)+'"></circle>';
      s+='<text x="'+X(i)+'" y="'+(Y(v)+(d.invert?20:-9))+'" text-anchor="middle" fill="#232b28">'+(d.y1-d.y0>20?Math.round(v):v.toFixed(1))+'</text>';}
  }
  s+='<g class="tip" style="pointer-events:none" visibility="hidden"><line class="tl" y1="'+T+'" y2="'+(T+ph)+'" stroke="#232b28" stroke-dasharray="3 3"/><circle class="tc" r="6" fill="none" stroke="#232b28" stroke-width="2"/><rect class="tr" rx="5" width="150" height="42" fill="#232b28"/><text class="t1" fill="#fff" font-weight="600"/><text class="t2" fill="#fff"/></g></svg>';
  host.innerHTML=s;
  var svg=host.querySelector('svg'),tip=svg.querySelector('.tip');
  function show(e){
    var r=svg.getBoundingClientRect(),x=(e.clientX-r.left)*W/r.width,best=-1,bd=1e9;
    for(var i=0;i<n;i++){if(d.vals[i]===null)continue;var dd=Math.abs(X(i)-x);if(dd<bd){bd=dd;best=i;}}
    if(best<0)return;
    var v=d.vals[best],xi=X(best),yi=Y(v),bx=xi+12;
    if(bx+150>W-R)bx=xi-162;
    tip.setAttribute('visibility','visible');
    var l=tip.querySelector('.tl');l.setAttribute('x1',xi);l.setAttribute('x2',xi);
    var c=tip.querySelector('.tc');c.setAttribute('cx',xi);c.setAttribute('cy',yi);c.setAttribute('visibility',d.bars?'hidden':'visible');
    var rc=tip.querySelector('.tr');rc.setAttribute('x',bx);rc.setAttribute('y',T+6);
    var t1=tip.querySelector('.t1');t1.setAttribute('x',bx+10);t1.setAttribute('y',T+23);
    t1.textContent=(d.bars?'PO ':'')+'week of '+d.weeks[best];
    var t2=tip.querySelector('.t2');t2.setAttribute('x',bx+10);t2.setAttribute('y',T+39);
    var after=(d.onset!==null&&d.onset!==undefined&&best>=d.onset)?'  (after start)':'';
    t2.textContent=d.vlabel+': '+(d.y1-d.y0>20?Math.round(v):v.toFixed(1))+d.suffix+after;
  }
  svg.addEventListener('mousemove',show);
  svg.addEventListener('mouseleave',function(){tip.setAttribute('visibility','hidden');});
}
document.querySelectorAll('.sp').forEach(function(el){
  el.addEventListener('click',function(){
    var big=el.closest('.card').querySelector('.big');
    var btn=el.querySelector('.xbtn');
    if(big.hidden){big.hidden=false;chartSvg(JSON.parse(el.dataset.chart),big);btn.innerHTML='&#9652; Collapse';}
    else{big.hidden=true;btn.innerHTML='&#9662; Expand';}
  });
});
</script>'''

CSS = """
body{margin:0;background:#f6f3ec;color:#232b28;font:15px/1.5 'Segoe UI',system-ui,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:28px 18px 60px}
h1{font:600 30px/1.2 Georgia,serif;margin:0}
h2{font:600 20px Georgia,serif;margin:38px 0 10px;border-bottom:1px solid #d9d3c5;padding-bottom:6px}
.sub{color:#6a736f;margin:4px 0 20px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.stat{background:#fff;border:1px solid #e1dbcc;border-radius:8px;padding:10px 16px;min-width:140px}
.stat b{display:block;font:600 24px Georgia,serif}
.stat small{color:#6a736f}
.card{background:#fff;border:1px solid #e1dbcc;border-left:5px solid #b3472f;border-radius:8px;
 padding:14px 18px;margin:12px 0}
.card.muted{border-left-color:#aaa;opacity:.72}
.top{display:flex;justify-content:space-between;align-items:flex-start}
.risk{font:600 22px Georgia,serif;text-align:right;color:#b3472f}
.risk small{display:block;font:12px 'Segoe UI';color:#6a736f}
.chip{color:#fff;font-size:12px;padding:2px 8px;border-radius:10px;margin-right:8px}
.pri{font-size:12px;font-weight:600;margin-right:8px;text-transform:uppercase;letter-spacing:.04em}
.pri.high{color:#b3472f}.pri.medium{color:#b07a00}.pri.low,.pri.res{color:#6a736f}
.kind{font-size:12px;color:#6a736f}
h3{margin:8px 0 2px;font-size:17px}
.meta{font-size:13px;color:#6a736f}
.mid{display:flex;flex-wrap:wrap;gap:16px;justify-content:space-between;align-items:flex-end;margin-top:8px}
ul{margin:0;padding-left:18px;font-size:13.5px}li span{color:#5a635f}
.sp{cursor:pointer}.sp:hover{opacity:.75}.big{margin-top:10px;border-top:1px dashed #d9d3c5;padding-top:8px}.big svg{width:100%;height:auto;max-width:760px;cursor:crosshair}
.sp small{display:block;font-size:11px;color:#8a928e;text-align:right}
.xbtn{display:block;margin:4px 0 0 auto;background:#e8830c;color:#fff;border:0;border-radius:5px;padding:3px 10px;font:600 12px 'Segoe UI',sans-serif;cursor:pointer}.xbtn:hover{background:#c96d00}
.act{background:#f3efe4;border-radius:6px;padding:8px 12px;margin:10px 0 0;font-size:14px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13.5px}
td,th{border:1px solid #e1dbcc;padding:7px 10px;text-align:left;vertical-align:top}
th{background:#efe9da}
th[data-tooltip]{position:relative;cursor:help}
th[data-tooltip]:hover::after{content:attr(data-tooltip);position:absolute;z-index:1000;
 left:50%;bottom:calc(100% + 6px);transform:translateX(-50%);width:max-content;
 max-width:260px;padding:7px 9px;border-radius:6px;background:#1f2937;color:#fff;
 font-size:12px;font-weight:400;line-height:1.35;text-align:left;
 box-shadow:0 4px 12px rgba(0,0,0,.18)}
pre{background:#fff;border:1px solid #e1dbcc;border-radius:8px;padding:12px;white-space:pre-wrap;font:13px/1.45 Consolas,monospace}
.foot{font-size:13px;color:#6a736f}
.pill{display:inline-block;background:#fff;border:1px solid #e1dbcc;border-radius:12px;padding:1px 9px;margin:2px;font-size:12.5px}
"""


def render(data, alerts, bt, asof, n_lines, known=True):
    """returns (html page, text of the team messages)"""
    asof = pd.Timestamp(asof)
    open_a = sorted([a for a in alerts if a.status == "open"], key=lambda a: -a.at_risk)
    res_a = [a for a in alerts if a.status == "resolved" and a.recent]
    total = sum(a.at_risk for a in open_a)
    flagged = sum(len(a.lines) for a in open_a)

    body = [f"<h1>Joy &middot; Monday brief, {asof:%d %b %Y}</h1>",
            f'<div class="sub">Sales for {asof - pd.Timedelta(days=7):%d %b} to '
            f'{asof - pd.Timedelta(days=1):%d %b}. Latest purchase order used: {asof - pd.Timedelta(days=7):%d %b} '
            f'(delivered 2-4 days later; there is no newer PO yet). '
            f'All Rs figures are estimates, good to roughly +/-30%.</div>']
    body.append(f'<div class="stats"><div class="stat"><b>{rs(total)}</b><small>sales at risk per week</small></div>'
                f'<div class="stat"><b>{len(open_a)}</b><small>open issues</small></div>'
                f'<div class="stat"><b>{flagged} of {n_lines}</b><small>warehouse-product lines flagged</small></div>'
                f'<div class="stat"><b>{len(res_a)}</b><small>recently fixed</small></div></div>')

    body.append("<h2>Act on these, biggest first</h2>")
    body += [card(a, data) for a in open_a] or ["<p>Nothing needs action this week.</p>"]
    if res_a:
        body.append("<h2>Fixed recently (no action, for the record)</h2>")
        body += [card(a, data, muted=True) for a in res_a]

    if known:
        body.append("<h2>Would this have caught the three missed problems?</h2>")
        body.append(known_table(bt, alerts))
    body.append("<p class='foot'>Each past Monday was re-run using only the data that existed on "
                "that day.</p>")
    body.append("<h2>Alerts per week in that replay</h2>" + week_table(bt))
    body.append(f"<p class='foot'>Checked {n_lines} warehouse-product lines every week; "
                f"never more than {int(bt.groupby('asof').size().max()) if len(bt) else 0} alerts in one week "
                f"(after grouping by warehouse).</p>")

    body.append("<h2>Messages to send</h2>")
    for team in ["Supply", "Platform KAM", "Marketing"]:
        msgs = [message(a, data) for a in open_a if a.owner == team]
        if msgs:
            body.append(f"<h4>{team}</h4><pre>" + html.escape("\n\n".join(msgs)) + "</pre>")

    n = data.notes
    body.append("<h2>What was cleaned</h2><p class='foot'>"
                f"<span class='pill'>{n['duplicate sales rows dropped']} duplicate sales rows dropped</span>"
                f"<span class='pill'>city names {n['city spellings before -> after'][0]} spellings "
                f"merged into {n['city spellings before -> after'][1]}</span>"
                f"<span class='pill'>{n['stores opened after 7 Sep']} stores opened mid-period; their first "
                f"5 weeks are left out of the analysis ({n['share of rows left out']:.0%} of rows)</span>"
                f"<span class='pill'>product ids kept as text (Blinkit numeric, Instamart IM..., Zepto ZP...X)</span>"
                "</p>")

    page = (f"<!doctype html><html><head><meta charset='utf-8'><title>Joy Monday brief {asof:%d %b}</title>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{CSS}</style>"
            f"</head><body><div class='wrap'>{''.join(body)}</div>{JS}</body></html>")

    txt = []
    for team in ["Supply", "Platform KAM", "Marketing"]:
        msgs = [message(a, data) for a in open_a if a.owner == team]
        if msgs:
            txt.append(f"===== {team} =====\n" + "\n\n".join(msgs))
    return page, "\n\n".join(txt)


def alerts_frame(alerts):
    return pd.DataFrame([dict(
        kind=a.kind, status=a.status, responsible_team=a.owner, platform=a.platform, city=a.city,
        warehouse=a.warehouse, products="; ".join(l["sku"] for l in a.lines),
        onset=a.onset.date(), weeks_open=a.weeks_open, at_risk_per_week=round(a.at_risk),
        lost_so_far=round(a.lost)) for a in alerts])


def build(data, alerts, bt, asof, n_lines, out_dir, known=True):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    asof = pd.Timestamp(asof)
    page, messages = render(data, alerts, bt, asof, n_lines, known)
    path = out / f"monday_{asof:%Y-%m-%d}.html"
    path.write_text(page, encoding="utf-8")
    (out / "messages.txt").write_text(messages, encoding="utf-8")
    alerts_frame(alerts).to_csv(out / "alerts.csv", index=False)
    bt.to_csv(out / "backtest.csv", index=False)
    return path
