# Ninox scripting (NX) conventions

Ninox formula fields, automations, and buttons are written in Ninox script (NX), a
JavaScript-like language. Formula-field expressions deploy through the public API
(`"type": "function"` with an `"expression"` — verified working live 2026-07-14,
including revising the logic by PATCHing `expression`); the Phase-5 probe in the
SKILL confirms this per workspace, and in the rare workspace that rejects it,
expressions are pasted in the logic editor instead. Automations and buttons are
always authored in the logic editor. Either way the logic editor
validates syntax live, autocompletes identifiers, and is the final authority —
verify API-deployed expressions by reading computed values back, and fix anything
that misbehaves in the editor. Official references:

- Core elements (statements, variables, operators):
  https://docs.ninox.com/ninox-scripting/automate-your-workflows/explore-core-scripting-elements.md
- Records and tables: same tree, `/work-with-functions/records-and-tables.md`
- Automations (On create / On update / On delete): `.../automations.md`
- Best practices and pitfalls: `.../best-practices-and-common-pitfalls.md`
- Functions library: https://docs.ninox.com/ninox-api/... see the sitemap at
  https://docs.ninox.com/sitemap.md ; any page answers questions via `?ask=<question>`.

## Reference fields and tables by their internal name

In Ninox 4, every module, table, and field has an **internal name** matching
`^[a-z0-9_]+$` and a separate human **label** (display text). NX scripts and the
API both address things by the **internal name** — the same identifier on both
sides. Because internal names contain no spaces or special characters, they need no
quoting:

```
sum((select invoice_line where invoice = this).line_total)
```

Keep one name per thing and reuse it in the API calls and the scripts; the label is
only what users see. The logic editor autocompletes these names — trust it over
guesswork. (This differs from classic Ninox 3, where scripts referenced fields by
their display label and wrapped names containing spaces in single quotes. If you
are ever in a Ninox 3 app, use that older convention instead.)

## Core syntax

- Statements separated by `;`. A block is `do ... end`.
- `let name := value` declares; `:=` assigns or reassigns.
- `if cond then ... else ... end`; chain with `else if ... then ...`.
- `for x in <list> do ... end`; `while <cond> do ... end`; `break` exits a loop.
- `this` is the current record. `ref_field.target_field` reads across a reference.

## Querying records — prefer `where` over `[...]`

`select table where condition` applies the filter **during** selection and returns
only matching rows. `select table[condition]` selects every row first and then
filters the array, which on a large table wastes memory and time. The docs
recommend `where` as the faster form. Use it for selecting from a table:

```
select purchase where security = this.security and remaining_qty > 0
```

Reserve the bracket form for filtering a list you already hold, or for scoping
through a reference field (where it is the idiomatic choice):

```
let open_lots := select purchase where remaining_qty > 0;
open_lots[security = this.security]          // filter an array already in hand
this.line_items[qty > 0]                     // scope through a reference field
```

- Sort: `(select t where ...) order by field` (ascending); for descending on a
  number use `order by -field`, otherwise sort ascending and `reverse(...)`.
- Deterministic order: sort by one unambiguous key (an explicit sequence number, or
  a computed composite). Confirm multi-key behaviour in the editor.
- Helpers: `first(list)`, `last(list)`, `cnt(list)`, and `sum`, `avg`, `min`, `max`
  over a list of numbers.

## Creating and changing records

```
let r := (create invoice_line);
r.(invoice := this);                 // set a reference by assigning the record
r.(qty := take);
delete some_record;
```

## Common functions

`text(x)`, `number(x)`, `round(x, digits)`, `date(y, m, d)`, `today()`, `now()`,
`format(...)`, plus the text / number / date families in the functions library.
Test for empty with `= null`; clear a value by assigning `null`.

## Where logic lives

- **Formula (function) field** — a read-only value that recalculates live, e.g.
  `quantity * trade_price`. Use for derived values that must always reflect data.
- **Automation** (On create / On update / On delete) — runs when records change. Use
  to initialise fields, cascade updates, or advance a process.
- **Button** — runs on demand for an explicit user action.

Formula-field expressions ship through the API's field-creation call; automations
and buttons cannot be created through the public API and are authored in the logic
editor. Deliver each of those scripts inside the app as a `zz_setup_` staging field
— a function field whose expression is the script as an inert raw string
(`---...---`; pattern in the API reference) — so the user copies it into its host
within Ninox and deletes the staging field afterwards. The build guide must give
the exact NX for each one regardless of which channel delivers it.

## Performance and correctness

- Ninox runs a read phase then a write phase per transaction (see the best-practices
  page). Don't run a fresh `select` inside a tight loop over many records — read once
  into a list, then iterate.
- Make process scripts idempotent and guarded: an action that writes child records
  should check whether it already ran
  (`if cnt(select invoice_line where invoice = this) > 0 then alert("Already done")
  else ... end`) and pair with a reset that reverses its effect.
- After writing a process script, reconcile its output against the source
  spreadsheet's numbers. They must match exactly.
