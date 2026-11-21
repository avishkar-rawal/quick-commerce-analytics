# Joy Monday brief

[View the live Streamlit app](https://quick-commerce-analytics-5pdelxiwjvtlprtvdthl65.streamlit.app/)

## The problem

Joy sells personal care products on Blinkit, Zepto and Swiggy Instamart. Every week the Quick
Commerce Manager downloads about 20,000 rows of sales and stock data, spends half a day in Excel,
and then sends WhatsApp messages to Supply, the platform account managers and Marketing. It is
slow and things are missed. In the last two months three problems were caught weeks late:

1. Blinkit Bengaluru delivered only about half of what its warehouse ordered for six products.
2. Zepto Bengaluru's best-selling face wash fell from about 3rd to 13th in search.
3. Instamart Hyderabad's warehouse stopped ordering sunscreen for two weeks.

## The solution

A tool that reads the four weekly exports and produces the Monday brief automatically:
a short list of problems, **biggest first** (by estimated sales at risk per week), with what to do
and a copy-paste message for each team. It is built to stay quiet: it only flags clear problems,
and groups them by warehouse, so a normal week gives zero to a handful of alerts.

| Problem | How it is flagged | Goes to |
|---|---|---|
| Short deliveries | PO fill under 70% and at least 20 units short | Supply |
| Warehouse stopped ordering | A product that was ordered every week has no PO | Platform KAM |
| Lost search position | Rank 3+ places worse than its own baseline, stock still 80%+, sales down | Marketing |
| Stock-outs, cause unclear | Availability 15+ points below baseline, no PO problem explains it | Platform KAM |

For 16 Nov 2026 it flags three things (about Rs 35k per week at risk): Zepto Mumbai has not
ordered Almond Milk Lotion 200ml for 2 weeks, Zepto Bengaluru Neem Face Wash 100ml is still around
rank 13, and Blinkit Delhi NCR short-delivered two products on the latest PO. Replaying every past
Monday shows the three original problems would each have been flagged in the first report after
they began (21 Sep, 28 Sep, 19 Oct), with never more than 3 alerts in a week.

All the data work is Python and pandas. Full details of every rule, threshold and formula are in
[HOW_IT_WORKS.md](HOW_IT_WORKS.md). The short submission note is in [NOTE.md](NOTE.md).

## Run it

Needs Python 3.9 or newer.

```
python -m venv .venv
.venv\Scripts\activate            # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

**Web app** (pick the week, upload your own CSVs, expand charts):

```
streamlit run app.py
```

By default it uses the CSVs in `data/`. In the sidebar you can switch to "Upload my own CSVs"
(all four files, same columns) and choose any Monday to report on. Tabs: the brief, an alerts
table, copy-paste messages, a replay of past weeks and data checks. Include a few weeks of
history before the week you care about, because the baselines need it.

**Command line** (writes files to `out/`):

```
python run.py                      # Monday 16 Nov 2026
python run.py --asof 2026-10-19    # any other Monday
python -m unittest discover -s tests
```

Output: `monday_<date>.html` (open in a browser), `messages.txt`, `alerts.csv`, `backtest.csv`.

## Data it expects

Four CSVs in the local `data/` folder: `daily_sales.csv`, `dark_stores.csv`,
`products.csv`, `purchase_orders.csv`, with the same columns as the originals. The data is messy on
purpose; what was found and how it is handled is in `HOW_IT_WORKS.md` section 3.

## Files

| File | What it does |
|---|---|
| `load.py` | Reads and cleans the CSVs (duplicates, city names, ids) |
| `weekly.py` | Builds the weekly table per warehouse and product |
| `detect.py` | The detectors, the rupee estimate and the replay |
| `report.py` | The HTML brief and the team messages |
| `run.py` | Command line entry point |
| `app.py` | Streamlit web app |
| `tests/` | Checks the three known problems are caught in their first week |
| `.streamlit/config.toml` | App theme |

## Deploying

The app is ready to deploy on Streamlit Community Cloud:

1. Put this project in a public GitHub repository, including `data/`, `.streamlit/`, and
   `requirements.txt`.
2. On share.streamlit.io choose "Create app", pick the repository, and set the main file to
   `app.py`.

`requirements.txt` is read automatically. GitHub Pages will not work, because the analysis runs in
Python on a server.

## Things to know

- A report on Monday M only uses sales up to Sunday M-1 and POs up to M-7, because later POs have
  not been delivered yet. The replay is therefore honest.
- Stores younger than 35 days are left out of the analysis, so the rupee numbers run a little low.
- Rupee figures are estimates (about +/-30%). They cannot see real stock, ads or competitors.
