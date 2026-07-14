# Ninox scripting and formulas

Read this whenever the task involves Ninox script/formula language — logic-field bodies,
button actions, validation, formula documentation, or migrating Excel formulas into Ninox.
The goal is **copy-paste-ready Ninox script**, not pseudo-code and not raw Excel formulas.

API creation of logic/`function` fields varies by workspace/version — probe a scratch field
first (recipe in the main skill, Schema admin). The probe's outcome decides the delivery
channel: where the workspace **accepts** function fields with expressions, deploy the
expression through the API directly, and ship editor-hosted scripts inside the app itself
(staging pattern below). Where it **rejects** them, the deliverable is copy-paste-ready
Ninox script the user pastes into a UI logic/function field — persisted, if needed, in a
text field or a `formula_notes` table as a precursor for manual UI setup later.

Official language references (docs.ninox.com; append `?ask=<question>` to any page to
query it directly):

- Core elements (statements, variables, operators):
  https://docs.ninox.com/ninox-scripting/automate-your-workflows/explore-core-scripting-elements.md
- Records and tables: same tree, `/work-with-functions/records-and-tables.md`
- Automations (On create / On update / On delete): `.../automations.md`
- Best practices and pitfalls: `.../best-practices-and-common-pitfalls.md`
- Functions library: find it via the sitemap at https://docs.ninox.com/sitemap.md

## Non-negotiable rules

### 1. Reference internal `name`, never UI labels

Formulas must use the API `name` for tables and fields. Before generating scripts for an
existing app, re-read the schema and use `tables[].name` / `fields[].name` exactly. Include
labels only as human-readable notes.

```ninox
quantity * trade_price            // good — uses field names
```
```ninox
Quantity * 'Trade Price'          // bad — uses labels
```

Cross-table examples (using table names `purchase_transactions`, `sale_inputs`):

```ninox
first(select sale_inputs).sale_quantity
sum((select purchase_transactions where ticker = "BTC").quantity)
```

### 2. Depend on durable business fields, not import-only/positional ones

Generated formulas must work for **records created after an import**, not just the original
spreadsheet snapshot. Never base business logic on provenance fields like `source_row`,
`excel_row`, `cell_ref`, `sheet_name`, `import_order`, or raw workbook coordinates — those are
fine for audit/traceability but break for new records. If logic needs ordering, matching, or
grouping, use durable fields such as `transaction_date`, `trade_date`, `settlement_date`,
`lot_sequence`, `transaction_sequence`, `document_number`, `ticker`, or explicit references.
If workbook order is semantically meaningful, convert it into a real field (e.g.
`lot_sequence`), backfill imported rows once, and populate it for future records via UI/automation.

### 3. Translate the business meaning, not the cell mechanics

Excel formulas often encode positions (`ROW`, `INDEX`, `SMALL`, `A1`, expanding ranges).
Translate the intent into Ninox `select`/filters/ordering/aggregates:

- `SUMIF($B$3:B3,B3,$E$3:E3)` → cumulative quantity by key and a durable sequence/date
- `INDEX(...ROW(A1))` → nth row by `lot_sequence` or a child reference, not source row
- dashboard totals → `sum(select table where condition).field`

### 4. Preserve raw Excel formulas only as traceability

For migration, keep the original Excel formula as an audit note alongside the Ninox
equivalent — but a raw Excel formula alone is not a finished migration. Don't call a formula
migrated until a Ninox script equivalent exists or a blocker is explicitly documented.

## Syntax patterns

Core elements:

- Statements are separated by `;`; a multi-statement block is `do ... end`.
- `let name := value` declares a variable; `:=` assigns or reassigns.
- `if cond then ... else ... end`; chain with `else if ... then ...`.
- `for x in <list> do ... end`; `while <cond> do ... end`; `break` exits a loop.
- `this` is the current record; `ref_field.target_field` reads across a reference.
- Test for empty with `= null`; clear a value by assigning `null`.
- Common functions: `text(x)`, `number(x)`, `round(x, digits)`, `date(y, m, d)`,
  `today()`, `now()`, `format(...)`, plus the text/number/date families in the
  functions library.

Variables and `this`:

```ninox
let tx := this;
let totalQty := sum((select purchase_transactions where ticker = tx.ticker).quantity);
totalQty
```

Select / filter / order (order by a durable field):

```ninox
select purchase_transactions
select purchase_transactions where ticker = tx.ticker
select purchase_transactions where ticker = tx.ticker order by lot_sequence
```

Prefer `select t where cond` over the bracket form `select t[cond]`: `where` filters
*during* selection, while brackets select every row first and filter the array afterwards
— wasteful on large tables. Reserve `[...]` for filtering a list already in hand, or for
scoping through a reference field, where it is the idiomatic choice:

```ninox
let open_lots := select purchase_transactions where remaining_qty > 0;
open_lots[ticker = tx.ticker]        // filter an array already in hand
this.line_items[qty > 0]             // scope through a reference field
```

Ordering: `order by field` sorts ascending; for descending on a number use
`order by -field`, otherwise sort ascending and wrap in `reverse(...)`. For
FIFO-style logic, order by one unambiguous durable key.

First record, aggregate sum:

```ninox
first(select sale_inputs)
sum((select cost_layers where method = "FIFO").total_value)
```

Conditional:

```ninox
if remainingQty <= 0 then
    0
else
    if remainingQty >= quantity then quantity else remainingQty end
end
```

Navigating references — prefer following the reference over re-selecting by text:

```ninox
customer.name
```

For reverse/child rows, use the relationship the schema exposes or a filtered `select` on a
durable key.

## Creating and changing records (editor scripts only)

Record writes belong in automations and buttons — never in formula-field expressions,
which recalculate constantly and must stay side-effect free:

```ninox
let r := (create invoice_line);
r.(invoice := this);              // set a reference by assigning the record
r.(qty := take);
delete some_record;
```

Correctness and performance rules for process scripts:

- Ninox runs a read phase then a write phase per transaction. Don't run a fresh `select`
  inside a tight loop over many records — read once into a list, then iterate.
- Make process scripts idempotent and guarded: an action that writes child records should
  check whether it already ran
  (`if cnt(select invoice_line where invoice = this) > 0 then alert("Already done")
  else ... end`) and be paired with a reset that reverses its effect.
- After writing a process script, reconcile its output against the source numbers
  exactly — to the cent.

## Cumulative / running-total pattern

```ninox
let tx := this;
sum((select purchase_transactions where ticker = tx.ticker and lot_sequence <= tx.lot_sequence).quantity)
```

Never write the same calculation against `source_row` or spreadsheet row numbers.

## FIFO / inventory pattern

Don't rely on Excel row numbers. Use durable lot/order fields on purchase/lot records:
`ticker` (or item key), `trade_date`/`transaction_date`, `settlement_date` when relevant,
`lot_sequence`/`transaction_sequence`, `quantity`, `trade_price`.

```ninox
quantity * trade_price        // principal
price * quantity              // cost-layer total
```

If a formula needs the nth lot, use a durable `lot_sequence`/`layer_index` convention and
document that users must maintain the sequence on new records. If no durable order exists, add
one or mark the formula blocked rather than ordering by row position.

## Excel-to-Ninox migration workflow

1. Inventory the formula cells and classify the business intent of each.
2. Inspect or create the target Ninox schema.
3. Re-read the target schema; collect exact table/field `name` values.
4. Identify durable formula dependencies; if only import-only fields exist, add a durable
   field first (e.g. replace `source_row` with `lot_sequence`).
5. Generate copy-paste Ninox script using only durable names.
6. Deliver per the probe outcome: deploy expressions via the API and ship editor scripts
   as staging fields where supported; otherwise store the script in a text field or a
   `formula_notes` table, with notes on which UI logic field to create and which field
   names it depends on.
7. Verify against representative records / backfilled values where possible.

## Shipping scripts inside the app (staging pattern)

Where the probe shows function fields create with expressions, don't deliver editor-hosted
scripts (button actions, automation bodies) as loose text: ship each one into the app as a
**staging field** — a function field whose expression is the script wrapped as an inert raw
string, so the user copies the code inside Ninox instead of out of a document:

```json
{ "name": "zz_setup_order_on_create",
  "labels": { "": "SETUP — paste into order → On create" },
  "type": "function",
  "expression": "---let r := first(select rate where active = true);\nvat_rate := r.percent---" }
```

The field *displays* the script; the user copies the code between the `---` markers into
its real host (the button or automation event) and confirms it in the editor. Rules:

- Never deploy a write-effect script as live expression code — formula fields recalculate
  constantly and must stay side-effect free; the `---...---` raw-string wrapper is what
  keeps the script inert.
- Trap: `{ ... }` inside `---...---` is template interpolation, so a script containing
  literal braces (say, JSON for `http()`) needs a plain quoted string with escaped quotes
  and `\n` instead of the raw-string form.
- Prefix the names (`zz_setup_`) so staging fields sort together and are unmistakable, and
  delete them via `DELETE .../fields/batch` once everything is pasted and verified.

Where the probe shows function fields don't create, fall back to the `formula_notes`
persistence below.

## `formula_notes` table shape (optional persistence)

When persisting generated formulas for later manual UI setup, a useful table has:
`cell_ref` (source traceability), `area` (workbook/app area), `excel_formula` (original, for
audit), `target_table` (internal name), `target_logic_field` (internal name),
`ninox_script_formula` (copy-paste Ninox), and `formula_notes` (setup/dependency notes).

## Common pitfalls

1. Using labels in formulas — formulas need internal `name`s.
2. Depending on `source_row`/cell coordinates — breaks for new records.
3. Copying Excel syntax (`SUMIF`, `INDEX`, `ROW`, ranges) instead of translating to `select`/
   filters/ordering/aggregates.
4. Writing formulas before the target table/field names exist and are verified.
5. Ambiguous ordering for FIFO/LIFO/running balances — add a durable order field or mark blocked.
6. Assuming formula-field API support either way — probe first; where rejected, store as
   notes or plan UI creation.
7. Putting record writes (`create`, `:=` on fields, `delete`) inside a formula-field
   expression — side effects belong in automations and buttons; formula fields
   recalculate constantly and must stay pure.
