# Note for the Joy team

**What I flag, and why.** Three main checks, one per problem in the brief (plus a catch-all for availability drops that no PO problem explains; it did not fire): POs
delivered under 70% (Supply), a product a warehouse used to order every week with no PO (KAM), and a product whose search rank got
at least 3 places worse while stock is fine and sales are below expectation (Marketing). Alerts are
grouped by warehouse, ranked by estimated sales at risk per week, and tiny ones are dropped. On 16 Nov
that is 3 alerts: ZEP-WH-08 has not ordered Almond Milk Lotion 200ml for two weeks (about Rs 15k/week),
Zepto Bengaluru Neem Face Wash 100ml is still around rank 13 (about Rs 10k/week, ~Rs 49k lost so far),
and BLK-WH-03 short-delivered Night Cream and Almond Milk Lotion 400ml on the 9 Nov PO (about Rs 10k/week);
that one is new, not one of the brief's three.
The Blinkit Bengaluru problem is fixed from the 2 Nov PO and is shown as resolved. I re-ran every
Monday using only the data available that day: all three original problems would have been flagged
in the first report after they began (21 Sep, 28 Sep, 19 Oct), never more than 3 alerts in a week.

**What was messy.** 765 exact duplicate sales rows (dropped, none conflicted); 11 spellings of 6
cities (merged); product ids that are numeric for Blinkit, IM... for Instamart and ZP...X for Zepto
(kept as text); 51 stores opened mid-period, so their first 5 weeks are left out (this slightly
understates sales at risk); Diwali pushes demand up then down, so I compare a product against the same product in other cities instead
of against last week. A "stopped ordering" shows up as a missing PO row, not a zero.

**Assumptions.** Rupee loss is an estimate (+/-30%). A missing PO row means the warehouse did not
order. Fill rate for the latest PO week is unknown on Monday because delivery takes 2-4 days, so the
newest PO I use is the one dated a week earlier.

**How I used AI.** I used Claude (Sonnet for building the code, Opus for brainstorming the design and
for reviewing it). It helped explore the data, suggest the baseline and rupee-loss approach, and write
the first version of the code and report. *[Edit this part: say what you personally checked, e.g.
re-computed fill rates and missing POs in Excel/pandas for the flagged warehouses, and read the
Opus review fixes.]*
