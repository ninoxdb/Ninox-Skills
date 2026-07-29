---
name: excel-to-ninox
description: >
  Turn an Excel workbook (.xlsx/.xlsm) into a working Ninox database app that
  replaces the spreadsheet and runs the process going forward. Use when someone
  wants to "port this xlsx to Ninox", "build a Ninox app from this spreadsheet",
  "move this calculator/tracker off Excel into Ninox", or "rebuild this workbook
  as a database". Also use to transcribe a workbook's logic (formulas, running
  totals, lookup/array logic, scattered calculator blocks) precisely enough to
  rebuild it.
  Handles giant workbooks (hundreds of MB, millions of rows) without loading every
  cell. Builds through the documented Ninox Public API. Do NOT use when the
  deliverable is just an edited spreadsheet, or a non-Ninox database.
---

# Excel → Ninox

The goal is not a copy of the spreadsheet. It is a Ninox app that captures what the
spreadsheet *means*, runs the same process on new data going forward, and lets the
user retire the Excel file. The new Ninox app must be intuitive to use for the users of the Excel. 

Keep labels and names mostly similar, and ask for approval to change namings, especially of tables/sheets.

Reproduce the exact logic; redesign the structure where necessary. 
Keep the app as simple as possible, not introducing unecessary tables or links. 
Think about a smart database schema for the final solution.

Work in phases. Do not skip ahead — in particular, do not build
anything before the logic is understood, the user has answered the open questions,
and the build guide is written.

## When something fails (applies to every phase)

Errors are information, not obstacles to route around. When a script, an API call,
or a verification check fails:

1. Read the actual error — the HTTP status, the `error.message` body, the
   traceback — diagnose the cause, fix the cause, retry. Iterate: most failures
   (a name violating `^[a-z0-9_]+$`, a batch rolled back by one bad member, an
   expression referencing a field that doesn't exist yet, an encoding or
   number-format mismatch on import) are fixable in a round or two. Repeating the
   same failing call unchanged is not iterating.
2. **NEVER SILENTLY FAIL OR IMPLEMENT A WORKAROUND.** Do not swap in "fallbacks"
   that change the design: a function field that won't create does not become a
   data field with imported values; a failing reference does not become a text
   column; a batch member that errors does not get dropped so the rest goes
   through; a rejected expression does not get simplified into something that
   parses but computes the wrong thing. Every one of those converts a visible
   error into silent wrong logic, which is strictly worse than the error.
3. If a few honest iterations (three to five *distinct* attempts) don't resolve
   it, stop. Tell the user exactly what failed, what was tried, and what the error
   says; offer concrete options (fix something in the workspace, pick an
   alternative design, hand this one piece to the editor, investigate further);
   and end the turn. The user decides — never the workaround.
4. Partial success is reported as exactly that, with counts — never rounded up to
   done. A verification mismatch in Phase 5 or 7 is a failure under this rule too.


## Phase 0 — inspect the workbook cheaply

Never start by dumping a whole file. Run the bundled inspector (pure standard
library, no installs, runs on any platform):

```bash
python scripts/inspect_xlsx.py FILE.xlsx
python scripts/inspect_xlsx.py FILE.xlsx --sheet SheetName
```

It prints a JSON logic map per sheet: populated-cell count, **regions**
(disconnected rectangular blocks — calculators scatter input, output, and scratch
blocks around one sheet), the **first formula in each column** (the template that
repeats down every row — including columns whose cells only *inherit* a shared
formula anchored in another column, which the inspector resolves and reports with
shifted references), every **array/shared formula**, **summary cells and
likely_summary_rows** (a `=SUM(C2:C401)` totals row is a bottom aggregate, not a
row template — its rows are excluded from any data load), and per-column
**formula-vs-literal counts** with **mixed-column flags classified by direction**:
`literal_overrides_in_formula_column` (values typed over a formula — a Phase 2
question) versus `summary_formula_in_literal_column` (a totals row in a
hand-entered column — an exclusion, not a question). It also extracts the
structural signals a database port needs: an inferred **type per column** from the
cell number formats (date / datetime / time / percent / currency / number / text /
boolean, with quoted-literal text stripped so `0.00" USD"` is a number, and a
serial sanity check on date columns), **column cardinality** with the
distinct-to-rows ratio (exact up to a few hundred, an estimate beyond — the raw
entity-extraction signal), **formula errors** cached in cells (#DIV/0!, #N/A),
**data validations** (dropdown lists — inline options and *resolved* range-fed
lists, defined-name indirection included), **hidden sheets, columns, and rows**
(one of the strongest scratch/artifact signals), **conditional-formatting rules**
(colour rules often encode real thresholds like "balance < 0"), **merged cells**,
**comments** (author intent), **Excel tables** (including formulas stored in the
table definition as calculated columns), **charts** (title, type, and the
series/category ranges they plot), workbook **defined names** (with scope), the
**1904 date-system flag**, **external workbook links** (logic or data living
outside this file), and workbook-level flags for **VBA macros** and **pivot
tables** — pivots now with their row/column/filter fields and value aggregations,
because the grid only holds a pivot's *results*. That is the whole logic and shape
of most workbooks in a few KB, whatever the file size.

Use those signals directly: a column typed `date` becomes a date field, not a
number; `currency`/`percent` inform the field and its display; a boolean column
becomes a yes/no field; a list validation becomes a choice field with exactly
those options — but a **large range-fed dropdown is usually an entity list**, so
when the inspector reports the referenced range as too big for a choice, plan an
extracted table plus a dynamic choice field (or in exceptions reference field) instead. Per-column cardinality separates
entity columns (thousands of rows, a low distinct-to-rows ratio → a table of
their own plus a reference) from label sets (a handful of distinct values → a
choice field) and identifiers (ratio near 1). Hidden sheets, columns, and rows are
where scratch logic and superseded data hide — inspect them, then usually leave
them behind as artifacts, and say so in the build guide. Conditional-formatting
rules carry thresholds the business cares about ("late", "over budget", "below
zero"): decide with the user whether each is decoration or a rule, and map the
real ones to Ninox dynamic styling or a status formula. `likely_summary_rows`
never load as data — the app's own aggregation replaces them. Merged cells across
the top of a region usually mean a **multi-row header** — combine the stacked
captions into one field name; merged label cells down a side mean grouped rows.
An Excel table marks a block that is already meant as a structured table;
comments often explain intent the formulas don't; and charts and pivots show what
the author wants to *see* — recreate them as Ninox views, charts, or a dashboard
so the reporting leaves the spreadsheet too.

When the full data itself is needed — recomputing headline numbers in Phase 1,
loading records in Phase 5, reading expected values in Phase 7 — use the bundled
`scripts/extract_data.py`: it streams any sheet (or window of it) to a typed CSV
with dates as ISO 8601 (1904 date system handled automatically), booleans as
`true`/`false`, error cells emptied and counted, and summary rows excluded via
`--exclude-rows`; it warns about every formula-bearing column so a derived column
is never imported by accident.

If you can't run the script, do the same by hand: read the file as a zip, stream
each `xl/worksheets/sheetN.xml`, record which cells carry a value or `<f>` formula,
and capture the first formula per column plus the array formulas. The method, not
the script, is the point.

## Phase 1 — understand the exact logic

Decode every column formula and array formula into plain words, and into a
recomputation you can run in code. Then classify every cell as one of three kinds:

- **Input** — entered by a person; the thing that changes.
- **Derived** — computed from inputs by a formula.
- **Scratch / artifact** — helper columns, running-total columns, manual comparison
  blocks, array-formula match tables, colour coding. These are spreadsheet
  *mechanisms*, not domain logic.

Only inputs and the *meaning* of derived cells carry over. Artifacts get left
behind.

One rule is absolute: **what is a formula in Excel MUST be a formula in Ninox** —
usually a function field's expression. In rare cases it can be a value written by a named automation or button
script. Never compute a derived value during the build and store the result in a
plain data field: a static number is correct exactly once and silently wrong from
the first edit onward, which defeats the entire port.

The inspector guarantees that a formula which *exists in the file* cannot pass as
literal data: it resolves shared-formula followers, reads table calculated-column
formulas, and counts formula vs literal cells per column. Several of its signals
are mandatory Phase 2 questions, never judgment calls:

- A mixed column flagged **literal_overrides_in_formula_column** — values typed
  over a formula (example cells are listed). Which wins, the formula or the typed
  value, and why were the overrides made? (The other direction,
  `summary_formula_in_literal_column`, is *not* a question — it is a totals row,
  and its row is excluded from the data load.)
- A **computed-sounding column with zero formula cells** (Total, Sum, Balance,
  Margin, Remaining, Days, Rate…). The formula may have been pasted as values, and
  no parser can recover a formula that is no longer in the file — the user must
  supply the rule, or confirm it really is hand-entered.
- **Cached formula errors** (`formula_errors`): cells holding #DIV/0!, #N/A and
  friends mean the source workbook is already broken there, or the misses are
  expected and handled by eye. Ask which — the answer decides the Ninox formula's
  guard clauses.
- **External workbook links**: formulas that read other files mean part of the
  logic or data lives outside this workbook. Get those files or the rules they
  supply before claiming the logic is understood.
- A **vba_macros flag or pivot_tables entry** — logic living outside cell formulas
  entirely. Get the macro code (or extract it), and read the pivot's reported
  row/column/value fields as its grouping intent, before claiming the logic is
  understood.

Reconcile before going further: pull the raw inputs with `scripts/extract_data.py`
(typed, summary rows excluded), recompute the workbook's headline numbers from
them, and confirm they match the sheet to the cent. If they don't match, the
logic isn't understood yet — keep going until they do.

## Phase 2 — ask the user (hard stop — do not assume)

Put the open questions to the user directly in the conversation, as questions, and WAIT for the answers. Do NOT fold unanswered questions into a
document and keep going — an unresolved question is a blocker, not a footnote, and
a wrong assumption baked into a schema is expensive to undo.

A spreadsheet rarely tells you everything. Ask about anything that isn't
unambiguous in the file, and about process details the file can't contain. For
example: undocumented abbreviations or codes; tie-breaking and ordering rules;
rounding; how edge cases should behave (overshoot, negatives, empties); and the
process around the file — who enters what and when, how often new data arrives,
whether it covers one entity or many, what happens on the next transaction,
retention, validation, and access.

If, after real scrutiny, nothing in the file is ambiguous, say so explicitly and
still confirm the process facts (who enters what, cadence, one entity or many)
before moving on. Silence is not confirmation, and reaching Phase 3 without having
asked the user a single thing is itself a signal that something was assumed.

Ask **only genuine ambiguity** — the gate is a quality bar in both directions.
Before a question makes the list it must pass three tests: the workbook cannot
answer it, this skill does not already decide it, and different answers would
produce a different build. Never ask whether an Excel formula should become a
formula field (that rule is absolute), which columns are computed (the inspector
shows it), or permission to follow the skill's own rules — those questions waste
the user's attention and bury the real ones. Keep the list short and
decision-ready: a handful of sharp questions, each stating the concrete options
and what each implies, related ones grouped. Five questions that change the schema
beat fifty reflexive ones; if the list genuinely grows large, lead with the
schema-changing ones and raise the rest during the build-guide review.

## Phase 3 — design the architecture (like a senior modeller)

Capture the meaning relationally. Do not mirror the sheet's layout. Common moves:

- A repeated row block (a ledger) → a table, one record per row.

- Header + line items → a parent table and a child table joined by a reference.

- A lookup between blocks (VLOOKUP, INDEX/MATCH, cross-sheet) → a reference field,
  then read across it.

- A column of repeated names or labels that denotes an **entity** — customers,
  products, countries, staff: things with identity that could carry their own
  attributes — → its own table plus a reference, even if the sheet never kept a
  separate list. Make sure not to overcomplicate the app by adding too many entities. 
  Only create tables where necessary and beneficial from a datamodelling perspective.
  
  The inspector's `column_cardinality` shows the candidates: 214
  distinct customers across 48k rows is a customer table; 4 distinct statuses is a
  choice field, not a table. Present each proposed extraction in the build guide,
  with its evidence, for the user to approve.

- A running total or a "match against earlier rows" array formula → usually **not**
  a stored column. Either a formula field that aggregates related records, or a
  **stateful** design where an automation or button writes child records once and
  persists state (such as a remaining quantity) so the process continues across
  future transactions. A spreadsheet recomputes everything from scratch on every
  edit; a database should hold state and accumulate.

Know the API capability seam (see `references/ninox_api_reference.md`): the API
creates modules, tables, data fields, references, records — and formula
(`function`) fields **with their NX expression**, which is verified working live
(2026-07-14: the expression persists, computes on record reads, and can be revised
by PATCHing `expression`). The cheap Phase-5 probe still runs per workspace as a
sanity check, since older workspaces have disagreed before. What the API can never
create are automations, buttons, views, pages, or dashboards, and it cannot run
scripts; that logic and presentation is authored in the editor.
Either way, the Phase-1 rule is binding: what is a formula in Excel is a formula
in Ninox. Computing a value during the build and storing it as a static number is
not a port, it is a snapshot. The only stored derived values allowed are the
deliberately frozen or accumulated ones (a rate frozen at order time, a remaining
quantity in a stateful design) — and those are written by a named automation or
button script, never typed in by the build. Native logic keeps the app correct as
new data arrives, which is the whole point of leaving the spreadsheet.

Future-proofing check: the model must accept new rows, new entities, and the next
transaction with no formula edits. If adding next month's data would mean editing
formulas, the design is still a spreadsheet in disguise.

## Phase 4 — write the build guide first (mandatory)

Before building, copy `references/build_guide_template.md` into the project working
directory as `<project>_build_guide.md` and fill it in completely: the logic
transcription, the real-world process, the proposed Ninox architecture, the exact
NX for every formula field and automation, the API build plan, the open questions,
and the verification targets. 
After creating it, ask the user regarding open questins and share the build guide for approval.
When sharing it, lead — in the chat message itself —
with a plain-words description of the proposed app for a non-technical reader: what
lists (tables) it has, how they connect ("each order knows which customer it
belongs to"), what happens automatically, and what the user will do day to day. No
jargon; the technical tables in the guide back it up, but the approval decision
must be understandable without them. Wait for the users answer.
This document is the thing the app is built from, not from a
mental model. Do not start Phase 5 until the build guides open-questions section is empty.

## Phase 5 — build the structure and data via the API

Follow `references/ninox_api_reference.md` — and if a standalone `ninox` skill is
installed alongside this one, read that too; where the two disagree on what the
API accepts, **the probe below decides, not either document**. Before the real
build, probe the workspace with a scratch module: create it, add one `function`
field with an expression, read it back, delete it. 

Then, in order:

1. Create the module (the app).
2. Create each table.
3. Create data fields in a batch per table; add `reference` fields once both tables
   exist (set `refTableName` to the target table's internal name). Remember that sometimes dynamic choice fields can be used instead of a reference field to improve UX.
   Create `choice` fields **with** their full options list:
   `options` is an array of `{"name": "..."}` objects, ids are assigned by the
   system, and to extend later you PATCH resending the existing options with their
   ids plus the new ones without. Choice writes are label-sensitive (`"active"`
   has failed where `"Active"` succeeded), so the load in step 6 must use the
   exact option spellings the build guide specifies. 
4. Create the formula fields, "type":
   "function"` with the NX from the build guide as the `expression`, in a
   batch of their own, in dependency order, once every field they reference
   exists.
5. Create a `zz_setup_` staging field for each automation and
   button — a function field whose expression is the script as an inert raw string
   (`---...---`), so the user copies the code inside Ninox rather than from a
   document (pattern in the API reference). Remember that for most things you do not need an automation and you can use a formula field directly.
6. Load data: export the input columns with `scripts/extract_data.py`
   (`--exclude-rows` takes the inspector's `likely_summary_rows`; dates arrive as
   ISO, booleans as `true`/`false`, decimals with a dot — import with
   `numberFormat=us`), then CSV import for bulk or batched record POSTs (respect
   the 6 MB limit and paginate reads with offset/limit). Load only the input
   columns, plus any frozen-state values the build guide designates for historic
   rows (an automation only fires for new records). Never import a column that a
   function field now computes — the formula produces it, and comparing its
   computed values against the sheet is the verification. 
   Check that all data was extracted by the script and flag if some data is missing.
7. Read records back and verify at row level: for **every table**, at least one
   test row, checked **field by field** against the source — inputs, references,
   and formula results alike (details and the type-fidelity traps in Phase 7). The
   API accepts an expression without proving it correct, so this read-back is what
   validates it. Correct where needed.

Builds should be resumable: `GET` the workspace tree first and skip or reconcile
anything a previous run already created — re-POSTing an existing name fails, and in
a batch one failure rolls back the whole call. If a run went wrong halfway, deleting
the module gives a clean restart. Never build into or modify an existing production
module without the user's explicit say-so; a new app gets a new module. Errors
during the build follow the failure rule at the top of this skill: diagnose, fix,
retry — never downgrade a design element to make a call pass, and when stuck, put
the options to the user and wait.

Remember the naming rule: internal names match `^[a-z0-9_]+$`; human text goes in
`labels`. The internal name is what both these API calls and the scripts in the
next phase use, so choose it once and keep a name↔label map. Pass the API key via
the environment (`NINOX_API_KEY`) or the consent-gated `~/.ninox/.env` pattern the
companion ninox skill defines; never hardcode it in scripts or command lines.

## Phase 6 — Review UX & remaining logic
Review the created app: Is it understandable by a user of the spreadsheet? Is it self explanatory? 
Is the UX excellent (e.g. using dynamic choice instead of reference fields where appropriate?).
Ensure the app is not introducing unnecessary complexity, e.g. by creating too many tables.

Repair any unfixd formula expression the Phase-5 read-back caught. 
Follow `references/ninox_scripting_conventions.md`: refer to fields by
their internal name, prefer `select ... where` over bracket filtering, guard
process actions and pair them with a reset, and validate everything in the logic
editor, which checks syntax live and is the source of truth.

Another editor task completes the app:

- **Views and dashboards.** Create the views the build guide specifies (each with
  its source table, view type, columns, grouping, and filter) — this is where the
  workbook's charts and pivots live on. There is no API for views, so the guide's
  spec is the deliverable and the editor is the tool.

Once everything is pasted, set, and verified, delete the staging fields via
`DELETE .../fields/batch`.

## Phase 7 — verify against the spreadsheet

Load the sample data and confirm the app reproduces the workbook exactly, at two
levels.

**Row level.** For every table, designate at least one test row — one that
populates every field, plus extra rows for edge cases (empties, negatives, boundary
dates) — and compare every field's read-back value with the source row: the inputs,
the references (resolving to the *right* record, not merely non-null), and the
formula fields' computed results. Check type fidelity, not just presence: dates
land on the right ISO day (watch the timezone off-by-one), percents arrive as the
fraction Excel stores (19% is 0.19 — decide which the app holds and verify it),
numbers survive as numbers with the right decimal separator (`numberFormat` on CSV
import), booleans are `true`/`false` rather than strings, choice values match the
option spelling exactly, and empty stays `null` rather than becoming 0.

**Aggregate level.** Recompute the workbook's headline numbers through the app and
match them to the cent, using read-back queries and the values of the formula
fields.

Record both — the designated test rows with every expected field value, and the
headline targets — in the build guide so anyone can re-check after changes.

## Working with giant workbooks

The thing that blows up memory is loading every cell into an object model. The
inspector avoids it by streaming the worksheet XML and clearing each element, so
memory stays flat — including the per-column distinct counts, which switch from
exact sets to a fixed-memory sketch (within a few percent) once a column passes a
few thousand distinct values. Two facts make this work: a data column repeats one
relative formula, so the first occurrence is the template and the other million
rows can be skipped; and populated-cell coordinates are enough to find the regions
without holding values. Above `--max-cells` the inspector drops region detection
and emits a per-column first/last/count profile instead, so it always returns.
`extract_data.py` streams the same way, so the data export scales identically. For
the data load, CSV import (up to 50 MB per file, chunked internally) beats per-row
POSTs.

## A small illustration

An invoices workbook with an "Invoices" block, an "Invoice Lines" block below it, a
`Line Total = Qty * Price` column, a running `Order Total` column, and a tax rate
looked up from a "Rates" sheet becomes: an Invoice table and an InvoiceLine table
joined by a reference; `Line Total` as a formula field on InvoiceLine; the running
`Order Total` dropped (it was an artifact) in favour of an `Order Total` formula
field on Invoice that sums its lines; Tax rates are part of the invoice table as either a number or a single choice, depending on the user choice.
The layout changed; the meaning didn't.

## Files

- `scripts/inspect_xlsx.py` — pure-stdlib streaming inspector of logic (regions,
  column formula templates with shared-follower resolution, array formulas,
  formula-vs-literal counts, direction-classified mixed columns, summary/totals
  rows, per-column cardinality with ratios) and structure (per-column types with
  boolean detection and a date-serial sanity check, resolved validations,
  conditional formatting, hidden sheets/columns/rows, cached formula errors,
  merged cells, comments, Excel tables with calculated columns, charts, pivot
  row/column/value fields, defined names with scope, external links, the 1904
  flag, VBA); scales to giant files; portable across platforms.
- `scripts/extract_data.py` — pure-stdlib streaming exporter of a sheet (or a
  window of it) to a typed, load-ready CSV: ISO dates honouring the 1904 system,
  `true`/`false` booleans, errors emptied and counted, percent kept as the stored
  fraction, summary rows excluded via `--exclude-rows`, and a warning for every
  formula-bearing column so derived data is never imported by accident.
- `references/ninox_api_reference.md` — the documented Ninox 4 API: auth, resource
  hierarchy, endpoints, field types, the function-field probe protocol, the
  automation/button/view limitation, build order.
- `references/ninox_scripting_conventions.md` — NX conventions for formula fields,
  automations, and buttons.
- `references/build_guide_template.md` — the per-project build guide to complete
  before building.
