# How the alerts work

A walk-through of every rule, threshold and formula in the tool, written so you can explain it
in an interview. File and function names are given so you can open the code next to this.

Contents: 1. The big picture, 2. What data is used when, 3. Cleaning, 4. The weekly table,
5. Expected sales and rupee loss, 6. The four detectors, 7. Grouping and priority, 8. The replay,
9. Threshold cheat sheet, 10. Worked examples, 11. Limits and assumptions.

---

## 1. The big picture

Every Monday the tool answers one question: **where are we losing sales, who needs to act, and
how much is it costing?** It does this in five steps:

1. Load and clean the four CSVs (`load.py`).
2. Build one weekly row per warehouse and product (`weekly.py`).
3. Run the detectors, which find the problem lines (`detect.py`).
4. Estimate the rupees lost for each problem (`detect.py`, `shortfall()`).
5. Group the lines into alerts, rank them by money, and write the page and messages (`report.py`).

There are three main problems plus one catch-all.

| Code | Problem in plain words | Who acts |
|---|---|---|
| `FILL` | We delivered much less than the warehouse ordered | Supply |
| `NOPO` | A warehouse stopped ordering a product it always ordered | Platform KAM |
| `RANK` | A product fell down the app's search results while stock was fine | Marketing |
| `AVAIL` | Shelves going empty and no PO problem explains it (catch-all) | Platform KAM |

---

## 2. What data is used when ("as of" rule)

A report is dated a Monday, called `asof`. The tool only uses data that existed on that day:

- **Sales:** whole weeks that ended before `asof`. For 16 Nov that is the week of 9 to 15 Nov.
- **Purchase orders (POs):** only POs dated `asof` minus 7 days or earlier (so up to 9 Nov for a
  16 Nov report). A PO is delivered 2 to 4 days after its date, so the PO dated a week earlier is
  the newest one that is fully delivered. This was confirmed in the data: every `delivery_date` is
  2, 3 or 4 days after `po_date`.

This is why `--asof` (or the week picker in the app) can safely be set to any past Monday: it
replays what the manager would really have seen that day. The replay is how we prove the tool
would have caught the three original problems.

Why it matters: without this rule the tool would "see the future" and look better than it
really is.

---

## 3. Cleaning (`load.py`)

| What was wrong | What the tool does |
|---|---|
| 765 exact duplicate rows in `daily_sales.csv` | Dropped. Checked that no duplicate disagrees with another (same date, store and product with different numbers). An `assert` stops the run if that ever happens. |
| 11 spellings for 6 cities (Bangalore/Bengaluru, Delhi/New Delhi/Delhi NCR, `mumbai ` with a space, HYDERABAD) | Strip spaces, lowercase, then look up in `CITY_MAP`. An unknown name raises an error. City is only a label. Alerts are grouped by `warehouse_id`. |
| Product ids are numeric for Blinkit, `IM...` for Instamart, `ZP...X` for Zepto | Read as text. Reading as numbers would fail or lose characters. |
| 51 stores opened after 7 Sep | A store younger than 35 days is left out of all calculations (see below). |
| Sales rows may not match a product or store | `assert` that every sales row maps to a sku and a warehouse. |

**Why leave out young stores (`MIN_STORE_AGE_DAYS = 35`):** a new store ramps up slowly (about
0.25 units per store-day in the first week to about 0.65 after five weeks). If included, a
warehouse with new stores would look like it is growing and its baseline would be wrong.
Side effect: sales from those stores are not counted, so rupee figures are a little low
(about 12% of store-days are dropped).

---

## 4. The weekly table (`weekly.py`)

Only mature store-days are used. For every **warehouse x product x week** (week starts Monday):

| Column | Meaning |
|---|---|
| `units`, `sales` | Total units and rupees sold |
| `store_days` | Number of store-days in the sum (rows) |
| `ups` | **Units per store-day** = `units / store_days`. Takes care of different store counts |
| `avail` | Average availability % |
| `rank` | Average search rank (1 is best) |
| `price` | Average selling price |
| `ctl` | **Control**: units per store-day for the same product on the same app in every other warehouse |

**Why a control (`ctl`):** sales rise and fall for reasons that have nothing to do with one
warehouse (Diwali pushed demand up about 13 to 30% and then down about 20% in the week of 9 Nov).
If we compared a warehouse only against its own past, almost every line would look like a
crash on 16 Nov. Comparing against the same product elsewhere removes the season.

---

## 5. Expected sales and rupee loss (`detect.shortfall`)

For a problem that started in week `onset`:

1. **Baseline:** take up to the 4 weeks before `onset` (at least 1). Compute
   `base_ups` = median `ups` and `ctl_base` = median `ctl` over those weeks.
2. **Expected units per store-day** for each later week `w`:

   `exp_ups(w) = base_ups x ctl(w) / ctl_base`

   Meaning: "what this line normally sells, adjusted for how that product is doing everywhere
   else this week".
3. **Expected rupees:** `exp_rs(w) = exp_ups(w) x store_days(w) x price(w)`
4. **Lost rupees in a week:** `lost(w) = max(0, exp_ups(w) - ups(w)) x store_days(w) x price(w)`
   (never negative; an unexpectedly good week does not cancel earlier losses)
5. **Lost so far** (shown on cards) = sum of `lost(w)` from onset to the latest week.
6. Actual selling price is used, not MRP.

### At risk per week (`at_risk`)

This is the number that ranks the alerts. It means "what we are losing, or about to lose, per
week if nothing is done". It depends on the type:

| Type | Formula | Reason |
|---|---|---|
| `FILL` | `exp_rs(latest week) x (1 - availability/100)` | The emptier the shelves, the more is already lost |
| `NOPO` | `exp_rs(latest week)` in full (`NOPO_FLOOR = 1.0`) | With no order coming, the line heads to zero. Using only the current gap understated this about 3x in the first week |
| `RANK`, `AVAIL` | average of `lost` over the latest 2 weeks | This is the gap we are actually seeing now |
| Resolved alerts | 0 | Nothing more is being lost |

Rupee numbers are estimates, good to about +/-30%. They agree within about 15% with an
independent hand calculation done during review.

---

## 6. The four detectors

### 6.1 `FILL`: short deliveries (`fill_lines`)

**Fill rate** = `qty_delivered / qty_ordered` for one PO line.

A PO line is a breach when **both** are true:

- `fill < 0.70` (`FILL_MIN`)
- `qty_ordered - qty_delivered >= 20` units (`FILL_MIN_UNITS`)

Why these numbers: normal fill is 94 to 99%, and even the 5th percentile is about 89%. Outside the
real incidents the lowest fill in the data is 0.74, caused by rounding on a tiny order (12 to 10
units). The real incidents are 45 to 52%. A line at 0.70 separates them cleanly. The 20 unit
minimum stops tiny POs from raising alarms.

Episodes: breaches for the same warehouse and product are grouped into one episode if they are
at most 14 days apart. The first breach is the **onset**.

Status:
- **Open** if the last breach is the latest PO week we can see. A good PO after the bad one
  closes it. (Earlier the alert stayed open an extra week and told Supply to "ship the balance" for
  a PO that had already recovered.)
- **Resolved** otherwise. Shown under "Fixed recently" if it ended within 5 weeks.

Card text includes the ordered, delivered, % and units short for the latest bad PO, plus last
week's availability.

### 6.2 `NOPO`: a warehouse stopped ordering (`nopo_lines`)

There is no zero-quantity PO in the data. A warehouse that stops ordering simply has **no row**
for that week. So the tool builds a calendar of every Monday from the first PO date to the latest
visible PO week, and marks each warehouse and product as `present` or not.

A run of missing Mondays counts as a problem only if the line was a regular orderer:
looking at up to the 4 weeks before the gap, it ordered in at least `min(3, number of weeks
available)` of them. (This is what lets it work in week 2, when only 1 earlier week exists.)

- **Onset:** the first missing Monday.
- **Open** if the missing run reaches the latest visible Monday. Otherwise resolved.
- There is no rupee floor. A missing PO is always shown. In the whole dataset there are only 6
  missing PO rows, all real (IM-WH-16 sunscreens on 14 and 21 Sep, ZEP-WH-08 Almond Milk Lotion
  200ml on 2 and 9 Nov).

### 6.3 `RANK`: lost search position (`rank_and_avail_lines`, using `track`)

Search rank is stored per store and day. The tool takes the weekly average per warehouse and
product.

**The clean baseline (`track`):** walk through the weekly series in order. The baseline is the
median of the last 4 "clean" weeks, and it starts once there are 2 clean weeks. A week that looks
bad (rank at least 3 worse than the baseline) is **not** added to the clean list. This matters:
a normal rolling median slowly accepts a long problem as the new normal, and the alert would vanish
after about a month (it did when I tried it). The start of the current bad run is the onset.

**A line is flagged only if all of these are true:**

1. Latest week rank is `RANK_JUMP = 3` or more places worse than the clean baseline. Normal
   weekly movement is tiny (standard deviation about 0.33, maximum about 0.5), so 3 is a very
   wide margin.
2. Availability last week is at least `RANK_MIN_AVAIL = 80%`. If stock is low, it is a stock
   problem, not a search problem.
3. Units per store-day last week are below 0.8 of the expected units (sales really are down).
4. `at_risk` is at least `MIN_RS = 2000` per week.

The rank gets worse in steps, so the message shows the baseline and current rank
(for example 2.9 to 13.2).

### 6.4 `AVAIL`: availability catch-all (`rank_and_avail_lines`)

For lines that did not trigger a rank alert, the same clean-baseline walk is run on availability.
Flag if:

- availability is at least `AVAIL_DROP = 15` points below the clean baseline, and
- the line is **not already explained** by an open `FILL` or `NOPO` alert, or one that was
  fixed within the last 5 weeks (shelves take a while to refill after a fix and it is the same
  incident), and
- `at_risk` is at least `MIN_RS = 2000`.

It is a safety net for platform-side stock-outs or listing problems. In the current data it never
fires, which is a good sign: every availability dip is already explained by a `FILL` or `NOPO`
problem.

---

## 7. Grouping, priority and ordering

**One alert per warehouse and type** (`detect.run`). Six products short-delivered at BLK-WH-01 is
one alert with six lines. For each alert:

- onset = earliest onset among its lines
- `at_risk` and `lost` = sums over its lines
- weeks open = whole weeks from onset to the report date (at least 1)

**Priority** (`report.priority`):

| Tag | Rule |
|---|---|
| High | `at_risk` at least Rs 15,000, or at least Rs 8,000 and open 3+ weeks |
| Medium | `at_risk` at least Rs 5,000 |
| Low | below that |

Priority is only a tag. **The order on the page is always by `at_risk`, biggest first.**

**Owners:** `FILL` goes to Supply (thinks in warehouse, product and units, so its message is in
units), `NOPO` and `AVAIL` go to the platform's KAM, `RANK` goes to Marketing (message in rupees).

**There is no cap.** If 11 alerts are real, 11 cards are shown. The list stays short because the
thresholds are strict and alerts are grouped.

---

## 8. The replay and the "would we have caught it" table

`detect.backtest` re-runs the detectors for every Monday from the second PO week to the report
date, using only the data that existed on each day. The result (open alerts per week) is the
"Alerts per week" table. The "three missed problems" table (bundled data only) works out:

- **Began:** onset of the matching alert (for example 21 Sep).
- **Flagged:** the first Monday it appears in the replay.
- **Sales that could have been saved:** sum of weekly `lost` from the flag date onward. This is
  an upper bound, because it assumes the problem is fixed instantly.

Result: all three original problems appear in the first report after they begin (21 Sep, 28 Sep,
19 Oct), and no week has more than 3 alerts.

---

## 9. Threshold cheat sheet (all in `detect.py` unless noted)

| Constant | Value | What it controls | Why this value |
|---|---|---|---|
| `FILL_MIN` | 0.70 | Fill rate below this is a breach | Normal 94-99%, worst non-incident 0.74, incidents 45-52% |
| `FILL_MIN_UNITS` | 20 | Minimum units short | Ignore tiny POs |
| `RANK_JUMP` | 3 | Places worse than baseline | Normal weekly movement under 0.5 |
| `RANK_MIN_AVAIL` | 80 | Stock must be at least this for a rank alert | Separates search issues from stock issues |
| sales factor | 0.8 | Sales must be below 80% of expected | Confirms the rank drop really hurts sales |
| `AVAIL_DROP` | 15 | Points below baseline | Ignores ordinary dips |
| `MIN_RS` | 2000 | Minimum Rs/week for RANK and AVAIL | Cuts noise |
| `NOPO_FLOOR` | 1.0 | Share of expected sales counted for a missing PO | Line is heading to zero |
| episode gap | 14 days | Breaches this close are one episode | Consecutive weekly POs |
| recent window | 5 weeks | How long a fixed problem is still shown | Keep recent fixes visible |
| baseline length | 4 weeks | Weeks used for the baseline | About a month, smooths noise |
| `MIN_STORE_AGE_DAYS` (`load.py`) | 35 | Young stores are ignored | New-store ramp-up |
| priority cut-offs (`report.py`) | 15k / 8k+3wk / 5k | High/Medium/Low | Roughly 1% of weekly platform sales |

If the tool is too noisy, raise `MIN_RS`, `RANK_JUMP` or `FILL_MIN_UNITS`. If it misses things,
lower them. The tests in `tests/` should be rerun after any change.

---

## 10. Worked examples (from the bundled data, report of 16 Nov)

**FILL, BLK-WH-03 (Blinkit Delhi NCR).** The PO of 9 Nov: Night Cream ordered 72, delivered 33
(46%), 39 units short. Almond Milk Lotion 400ml ordered 120, delivered 60 (50%), 60 units short.
Both are below 70% and above 20 units short, so both breach. One episode, onset 9 Nov, still the
latest PO week, so it is open. Availability last week was 59 and 69%, so `at_risk` is the expected
weekly sales times about 0.4 each, about Rs 10k per week.

**NOPO, ZEP-WH-08 (Zepto Mumbai).** Almond Milk Lotion 200ml was ordered every week from 7 Sep to
26 Oct (units grew from 60 to 132). There is no row for 2 Nov or 9 Nov. The line was regular, so
this is a missing run reaching the latest week: open for 2 weeks. Availability is 16%.
`at_risk` is the full expected weekly sales, about Rs 15k per week.

**RANK, Zepto Bengaluru Neem Face Wash 100ml (ZEP-WH-07).** Weekly average rank was 2.9 to 3.1 for
five weeks (clean baseline about 2.9). In the week of 12 Oct it jumped to 10.0, then about 13.
That is more than 3 worse, availability about 89%, and sales are well below expected. The bad weeks
are kept out of the baseline, so the alert stays open (5 weeks, about Rs 10k per week, about Rs
49k lost so far).

---

## 11. Limits and assumptions

Facts from the data:
- Fill rates, PO dates, delivery dates and ranks were all checked against the CSVs.
- The three original problems show up in the data as the brief describes them.

Assumptions (made by me, written down):
- A missing PO row means the warehouse did not order (it could also be a missing data row).
- Rupee loss is a model, not a measurement. It cannot see real stock, customers, ads or
  competitors. Treat it as +/-30%.
- "Expected" sales assume the product would have followed the same trend as in other cities.
- Dropping stores younger than 35 days slightly understates the rupees at risk.
- The tool cannot say **why** something happened (platform decision, our miss, listing issue).
  The action text tells the right team where to look first.
- Search rank is assumed to be the rank in the product's category search.

What the tool does not do: no forecasting, no live data feed, no automatic sending of messages.
Messages are written and ready to copy.
