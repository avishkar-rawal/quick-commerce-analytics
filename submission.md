# Submission

- [Live Streamlit app](https://quick-commerce-analytics-5pdelxiwjvtlprtvdthl65.streamlit.app/)
- [GitHub repository](https://github.com/avishkar-rawal/quick-commerce-analytics)

The Monday brief is available in the **Brief** section of the Streamlit app. The same output is
also saved as `monday_2026-11-16.html` inside the out folder.

The app also has a dedicated **Messages** tab with copy-ready messages grouped by department:
**Supply**, **Platform KAM** (key account managers for the quick-commerce platforms), and
**Marketing**. Each message identifies the affected warehouse, product and recommended next action.

**What I chose to flag, and why.** I kept the brief focused on problems that are clear enough to
act on. It flags POs delivered at less than 70% fill with at least 20 units short, warehouses that
stopped ordering a product they had been buying regularly, and search-rank drops of at least three
places where stock and sales are also weak. It also includes a catch-all for unexplained availability
drops. Alerts are grouped by warehouse and ranked by estimated sales at risk, so the first item is
usually the most valuable one for the team to address.

**What was messy in the data.** The main issues were duplicate sales rows, inconsistent city and
store details, mixed product-ID formats, and stores that were too new to have meaningful demand
history. I dropped 765 exact duplicate sales rows after checking that they did not conflict, read
product IDs as text, standardized city spellings, and joined sales to valid products and warehouses.
I left out stores younger than 35 days because their ramp-up can make a warehouse look artificially
low or high.

> **Example:** if a new store sells 2 units in week 1 and 8 units in week 5, that rise is mainly
> natural store growth. Including it too early could make normal growth look like a big increase
> in demand, or make a warehouse look weaker while the store is still getting started. I only
> include a store after it has been open for 35 days.

Missing PO rows are treated as missing orders, and I adjusted for seasonal demand by comparing a
warehouse with the same product in other warehouses.

**How I used AI, and what I checked myself.** I used Claude Sonnet to help draft the Python code
and report, and Claude Opus to brainstorm the baselines and rupee-loss approach and review the
design. I used the model output as a starting point and did not treat it as correct by default.
I ran the finished tool and checked that the Streamlit app generates consistently. I also reviewed
the tables, charts and column definitions to make sure they are formatted clearly and the metrics
shown in the app are explained in a way that matches the data.
I also added an option to upload your own CSV files and verified that uploaded data
produces the expected tables, copy-ready messages and charts.

**Assumptions.** Rupee loss is an estimate (+/-30%). This is a modelling assumption, not a
statistically measured confidence interval; it leaves room for factors the tool cannot see, such as
promotions, ads, competitor activity or real shelf stock. A missing PO row means the warehouse did
not order. Fill rate for the latest PO week is unknown on Monday because delivery takes 2-4 days,
so the newest PO I use is the one dated a week earlier.

