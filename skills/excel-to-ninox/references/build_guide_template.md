# Build guide template

Copy this into the project working directory as `<project>_build_guide.md` and fill
**every** section before
building anything in Ninox. It is the contract: it captures what the spreadsheet
means, what the real process is, and exactly what will be built, so the user can
review and correct it first. Do not start building until the spreadsheet logic and
the open questions are resolved.

Each section below says what to include and then shows a short, deliberately
concrete example (marked _Example_) so the level of detail is unmistakable. The
examples use a small orders workbook — an Orders header block, an Order Items block
with a `Line Total = Qty × Unit Price` column and a running `Order Total`, and a
VAT rate looked up from a Rates sheet. Match this depth or exceed it. Thin,
one-line answers are not enough; if a section is short, that is a signal you haven't
finished understanding the workbook.

---

## 1. Source workbook

File name, sheets, size. Paste the inspector's logic map (or a trimmed version):
regions, column formula templates (including shared-follower resolutions),
formula-vs-literal counts with direction-classified mixed columns, summary cells
and likely_summary_rows, array formulas, inferred column types (with any
serial-check warnings), column cardinality with ratios, resolved data
validations, cached formula errors, hidden sheets/columns/rows,
conditional-formatting rules, comments, tables (with calculated columns), charts,
pivot row/column/value fields, defined names with scope, external links, the
1904 flag, VBA flag, sample values.

_Example:_ `orders.xlsx`, one sheet `Sheet1`, 2.1 MB, ~48k rows. Regions: `A1:F2`
(order header), `A4:E40000` (order items), `H1:I12` (VAT rates lookup). Column types:
`Date`→date, `Unit Price`→currency, `VAT %`→percent. Validation on `Status`:
list `Draft, Confirmed, Shipped, Cancelled`. Comment on `E1`: "running total, do not
edit". Sample values shown for the first rows.

## 2. Logic transcription (exact meaning)

For every region and every formula template, state in plain words what it computes,
and write a recomputation you can run in code. Decode array formulas fully. Then
classify every column/cell as exactly one of: **input** (entered by a person),
**derived** (computed by a formula), or **scratch / artifact** (helper columns,
running-total columns, manual comparison blocks, array-formula match tables, colour
coding — spreadsheet mechanisms, not domain logic).

Then reconcile: recompute the workbook's headline numbers from the raw inputs and
show they match the sheet to the cent. If they don't match, you don't understand the
logic yet — keep going.

Rule: every column classified **derived** must reappear in section 4 as live logic —
a function field's expression, or a value written by a named automation or button
(for deliberately frozen or accumulated state). A derived column that lands as a
plain data field with imported values is a design error, not a shortcut. And if a
computed-sounding column (Total, Balance, Remaining, Rate…) carries only literals,
it may be a formula pasted as values — classify it only after the user has answered
(section 7), never by guessing. Mixed columns get a row each, by direction:
`literal_overrides_in_formula_column` records the user's ruling on which value
wins and how the app should handle it; `summary_formula_in_literal_column` is a
totals row — classify the row as **scratch/artifact**, list it under the load
exclusions in section 6, and note that the app's own aggregation replaces it.
Columns with cached formula errors get a row recording the user's answer: is the
source broken there, or are the misses expected — and what guard the Ninox
formula therefore needs.

_Example:_

| Column | Formula (first row) | Meaning | Kind |
|---|---|---|---|
| Qty | — | units ordered | input |
| Unit Price | — | price per unit at order time | input |
| Line Total | `=B5*C5` | Qty × Unit Price | derived |
| Order Total | `=SUM($D$5:D5)` | running cumulative of Line Total | scratch (a database sums the lines; don't store a running column) |
| VAT % | `=VLOOKUP(F5,Rates!$H:$I,2,0)` | rate for this order's country, from the Rates sheet | derived → becomes a reference + lookup |

Reconciliation: order #1003 has lines 2×9.90 and 1×14.00 → Line Totals 19.80 and
14.00 → Order Total 33.80; sheet shows 33.80. ✓ (Show this for the headline figures.)

## 3. The real-world process

What the workbook is used for as a process over time, not just the snapshot it shows
now. Cover: who enters what and when; how often new data arrives; whether it is one
entity or many (one customer or a whole book?); what happens on the next transaction
and the one after; retention; validation; access. Anything the file can't answer
goes to section 7 — ask the user, don't invent it.

_Example:_ Sales clerks add an order and its line items daily. Orders accumulate
indefinitely; old orders are never recomputed. VAT rates change once or twice a year
and must apply to new orders only, so each order stores the rate at order time rather
than always reading the current rate. Multiple customers and countries. Read access
for all staff; only managers edit rates.

## 4. Target Ninox architecture

Open this section with a **plain-words description for a non-technical reader** —
this is what the user actually approves. What lists (tables) the app keeps, how
they connect, what it calculates by itself, and what a person does in it day to
day. No jargon; the field tables below are the technical backing, not the pitch.

_Example (plain words):_ "The app keeps four lists: your orders, the items on each
order, your customers, and the VAT rates per country. Each order knows which
customer it belongs to, and each item knows which order it sits on. Totals and VAT
are calculated automatically the moment an item is entered — nobody types a total
again. Day to day, a clerk opens Orders, adds an order, adds its items, done."

Then design as a senior modeller would: capture the meaning relationally, do not
mirror the sheet's layout. Propose **entity extractions**: a column of repeated
names or labels that denotes a thing with identity (customers, products,
countries…) becomes its own table plus a reference, even if the sheet never kept a
separate list — cite the inspector's cardinality as evidence and mark each
extraction for the user's approval ("`Customer`: 214 distinct values over 48k rows
→ own `customer` table"; "`Status`: 4 distinct values → choice field, not a
table"). State the tables, and for each one a field table with internal
name, label, type, and role. List the relationships. Mark each field as built by the
**API** (data fields, references, choice fields with their options, and `function`
fields with their NX expression — with or without the expression per the Phase-5
probe regime recorded in section 6) or in the **editor** (automations, buttons,
and in probe regimes B/C the formula expressions themselves), since the API can't
create the latter. Map
inferred column types to field types (date→date, currency/number→number, boolean→
yes/no, a list validation→choice with those exact options — but a range-fed
dropdown the inspector reports as too large for a choice is an entity list: extract
it as a table plus a reference). Conditional-formatting rules the user confirmed as
real business thresholds map to Ninox dynamic styling or a status formula field —
name each rule and its Ninox counterpart. If the workbook has charts or pivots,
**specify** each replacement view or dashboard, not just its name: source table,
view type, columns, grouping, filter, and (for charts) the chart type and series —
these are built by hand in the editor in Phase 6 because there is no views API, so
this spec is the only place they exist before then. End with two audits: a
**derived-coverage check** — walk
section 2's derived columns and confirm each one maps to a function field or a
named script, with none silently demoted to a static data column — and the
**future-proofing check**: confirm new rows, new
entities, and the next transaction need no formula edits.

_Example:_ Tables `order`, `order_item`, `rate`, `country`.

`order_item`
| internal name | label | type | role |
|---|---|---|---|
| order | Order | reference → order | API |
| qty | Qty | number | API (input) |
| unit_price | Unit Price | number | API (input) |
| line_total | Line Total | function | API, expression: `qty * unit_price` |

`order`
| internal name | label | type | role |
|---|---|---|---|
| customer | Customer | reference → customer | API |
| order_date | Order Date | date | API (input) |
| status | Status | choice (Draft, Confirmed, Shipped, Cancelled) | API (input) |
| vat_rate | VAT Rate | number | API; set by automation at create (frozen rate) |
| net_total | Net Total | function | API, expression: `sum((select order_item where order = this).line_total)` |
| order_total | Order Total | function | API, expression: `net_total * (1 + vat_rate)` |

Relationships: customer 1—N order, order 1—N order_item, country 1—N rate.
The running `Order Total` column from the sheet is dropped; `net_total` derives it
live. Future-proofing: a new order and its items need no formula changes; rate
changes don't touch historic orders because `vat_rate` is frozen per order.

## 5. Logic to build (exact NX)

For every formula field, automation, and button, give the exact NX, using internal
names (per the scripting conventions), and name the host (which field, or which
automation event, or which button). Formula-field expressions ship in the API
field-creation call as the `expression`; automations and buttons are pasted in the
logic editor from `zz_setup_` staging fields — API-created function fields whose
expression is the script as an inert raw string (`---...---`, pattern in the API
reference) — so name the staging field next to each editor-hosted script. Include
guards and reset logic for any process action. Don't
paraphrase the logic — write the actual script.

_Example — formula field `order.net_total` (API, `"type": "function"`):_
```
sum((select order_item where order = this).line_total)
```
_Example — automation `order` On create (freeze the rate at order time; ships as
staging field `zz_setup_order_on_create`):_
```
let r := first(select rate where country = this.customer.country and active = true);
vat_rate := if r != null then r.percent else 0
```
_Example — button `order.recalc_lines` (guarded; ships as staging field
`zz_setup_btn_recalc_lines`), if a process step is needed:_
```
if cnt(select order_item where order = this) = 0 then
  alert("No line items to process")
else
  ... the process step ...
end
```

## 6. API build plan

Ordered calls (see `ninox_api_reference.md`): probe the workspace and **record the
regime (A/B/C) here** → create module → tables → data fields (batch; choice fields
with their full options lists) → reference fields once both tables exist → function fields in
dependency order (with expressions in regime A, as placeholders in regime B,
editor-built in regime C) → regime A: `zz_setup_` staging fields for each
automation and button → export inputs with `scripts/extract_data.py`
(`--exclude-rows` = the summary rows from section 2; ISO dates, `true`/`false`
booleans, dot decimals → `numberFormat=us`) and load (CSV import or batched record
POSTs) → read back records, including the function fields' computed values, to
verify. Note the workspace id, internal names, batch sizes, and the name↔label map.

Close the section with the **editor checklist** the user works through in Phase 6:
paste each script (and, in regimes B/C, each formula expression) from its staging
field or from section 5; verify each choice field shows exactly the options
section 4 lists (set them by hand only if the API rejected `options` on creation);
build each view/dashboard to section 4's spec; then delete the staging fields.

_Example:_ POST `/modules` `orders_app`; POST tables `order`, `order_item`, `rate`,
`country`; POST `/order_item/fields/batch` `[qty:number, unit_price:number]` then the
`order` reference, then `[line_total:function]` with its expression; POST the
staging fields `zz_setup_order_on_create` and `zz_setup_btn_recalc_lines`;
CSV-import 48k order items mapped by header; GET back row counts and spot-check
`line_total` and `net_total` values. The user then pastes the staging scripts into
their hosts in the editor, after which the staging fields are removed via
`DELETE .../fields/batch`.

The build must follow this guide. If an error or unexpected Ninox behaviour forces
a deviation, stop, put the options to the user, and amend the guide before
continuing — no silent workarounds, no downgraded fields, no dropped batch members
(see the failure rule in the SKILL).

## 7. Open questions for the user

Everything ambiguous in the workbook or absent from it that affects the build —
and **only** that. A question belongs here when the file can't answer it, the
skill doesn't already decide it, and different answers change the build; each one
states its options and what they imply. These go to the user **in the
conversation, as direct questions** — this section is
where the answers get recorded, not a place to park questions and keep building.
The build does not start while anything here is unanswered. If nothing is
ambiguous, state that explicitly and record the user's confirmation of the process
facts instead.

_Example:_ Should `vat_rate` be frozen at order time or always reflect the current
rate? What determines a row's country — a column not present in the export? How are
cancelled orders treated in totals?

## 8. Verification targets

Two levels, both recorded here so anyone can re-check after changes.

**Test rows** — for every table, at least one designated row (ideally one that
populates every field, plus edge rows: empties, negatives, boundary dates) with the
expected value of **every field**: inputs, references (which record they must
resolve to), and formula fields' computed results. Include the type-fidelity
checks: dates on the right ISO day, percents as the fraction Excel stores, numbers
as numbers with the right decimal separator, booleans as true/false, choice values
spelled exactly as the options, empty as null rather than 0.

**Headline numbers** — the aggregate figures and record counts the finished app
must reproduce from the sample data, matching the spreadsheet to the cent, and how
each is checked.

_Example:_ 48,012 order_item rows and 9,640 order rows loaded. Test row `order`
#1003: `order_date` 2024-03-12 (not shifted a day), `status` "Confirmed" (exact
option spelling), `customer` → the "Acme GmbH" record, `vat_rate` 0.19 (the
fraction, not 19), `net_total` 33.80, `order_total` 40.22. Test row `order_item`
#7: `qty` 2, `unit_price` 9.90 (a number, not text), `order` → #1003, `line_total`
19.80. Edge rows: an order with no items (`net_total` per the sheet's behaviour), a
negative-qty return. Headlines: sum of all `net_total` matches the sheet's grand
total cell to the cent. All checked via GET records.
