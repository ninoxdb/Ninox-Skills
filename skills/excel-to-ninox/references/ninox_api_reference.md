# Ninox Public API reference (Ninox 4)

Condensed from the official OpenAPI spec. Always confirm against the live Swagger,
since Ninox can add fields and endpoints:

- Swagger UI: https://go.ninox.com/api/docs
- OpenAPI JSON / YAML: https://go.ninox.com/api/docs-json , https://go.ninox.com/api/docs-yaml
- Endpoint docs (markdown): https://docs.ninox.com/ninox-api/api-reference/api-endpoints.md
  (every page also answers questions via `?ask=<question>&goal=<goal>`)

The markdown endpoint docs lag the live Swagger: as of July 2026 they still show
`CreateFieldBody` without `function` or `expression`, both of which the live spec
has been seen to accept. When they disagree, the Swagger spec wins over the
markdown docs — and a live probe of the target workspace wins over both.
Capabilities move faster than docs, so before a real build, probe the target
workspace with a throwaway function field (the Phase-5 probe in the SKILL) to
confirm what *this* workspace accepts.

A standalone `ninox` skill may be installed alongside this one (look for it among
the available skills). If it exists, read it as well — it covers the same API in
more operational depth and is kept verified against the live API. As of
2026-07-14 the two skills agree: `function` fields with an `expression` and
`choice` fields with an `options` array are both **verified working live** (that
skill's own probes on a real workspace). Older versions of either document
recorded the opposite; if a workspace ever contradicts this, do not argue from
any document — run the probe, record its outcome in the build guide, and follow
the regime (A/B/C in the SKILL's Phase 5) that the probe establishes.

## Auth and base URL

- Authentication: a **Workspace API Key** in the header `Authorization: Bearer <key>`.
  Keys are generated in the Ninox app under **Workspace → Integrations**, and each
  key is scoped to a single workspace. Treat it like a password.
- All requests are HTTPS with JSON bodies, standard verbs (GET, POST, PATCH, DELETE).
- Path shape: `/api/v1/workspace/{workspaceId}/...`. Confirm the exact server host
  from the OpenAPI `servers` block; the spec is published under `go.ninox.com/api`,
  so the base is `https://go.ninox.com/api/v1`. Verify before a real run.
- The `workspaceId` is the workspace the key belongs to; read it from the workspace
  endpoint or the integration settings.

## Resource hierarchy

```
Workspace ─┬─ Module (an "app")
           │     └─ Table
           │           ├─ Field   (a column / definition)
           │           └─ Record  (a row)
```

A "new app" = a new **module**.

## Naming rule (important)

Every module / table / field has an **internal name** matching `^[a-z0-9_]+$`
(lowercase, digits, underscore; max 100) and a human **label** via the localized
`labels` object, e.g. `{"en": "Trade Price"}` — a default label under the
empty-string key (`{"": "Trade Price"}`) is also accepted; mirror whatever shape
discovery returns when editing existing objects. The internal name is the single
identifier used by **both** the API and Ninox scripts/formulas; the label is only
display text shown to users. Pick the internal name once and reuse it everywhere.
Keep a name↔label map so API payloads and scripts stay consistent.

## Endpoints

All paths below are prefixed with `/api/v1/workspace/{workspaceId}`.

### Workspace
| Method | Path | Purpose |
|---|---|---|
| GET | `` (root) | Read workspace info: modules → tables → fields tree |

### Modules (apps)
| Method | Path | Body |
|---|---|---|
| GET | `/modules` | — |
| POST | `/modules` | `{ "name": "invoice_tracker", "labels": {"en":"Invoice Tracker"}, "icon"?, "openRoles"?, "isHidden"?, "hideNavigation"? }` |
| GET / PATCH / DELETE | `/modules/{moduleName}` | update body mirrors create |

### Tables
| Method | Path | Body |
|---|---|---|
| GET | `/modules/{m}/tables` | — |
| POST | `/modules/{m}/tables` | `{ "name":"purchase", "labels":{"en":"Purchase"}, "icon"?, "hasFiles"?, "hasHistory"?, "hasGlobalSearch"?, "createRoles"?, "readRoles"?, "writeRoles"?, "deleteRoles"? }` |
| GET / PATCH / DELETE | `/modules/{m}/tables/{tableName}` | update body mirrors create |

### Fields
| Method | Path | Body |
|---|---|---|
| GET | `/modules/{m}/tables/{t}/fields` | — |
| POST | `/modules/{m}/tables/{t}/fields` | single `CreateFieldBody` |
| POST | `/modules/{m}/tables/{t}/fields/batch` | array of `CreateFieldBody` (one transaction; any failure rolls back all) |
| DELETE | `/modules/{m}/tables/{t}/fields/batch` | `{ "fieldNames": ["a","b"] }` |
| GET / PATCH / DELETE | `/modules/{m}/tables/{t}/fields/{fieldName}` | — / `UpdateFieldBody` / — |

`CreateFieldBody`:
```json
{
  "name": "trade_price",
  "labels": { "en": "Trade Price" },
  "type": "number",
  "refTableName": null,
  "expression": null,
  "variant": null,
  "index": false, "required": false, "unique": false, "search": false,
  "readRoles": ["admin","user"], "writeRoles": ["admin","user"]
}
```

`refTableName` names the target table (internal name) for `reference`/`reverse` fields;
`expression` carries the NX formula for `function` (logic) fields; `variant` applies to
`string` (multiline-text | text | phone | email | url | location | signature).

**Writable field types** (what the API can create):
`string, number, boolean, choice, dchoice, multi, dmulti, file, date, timestamp,
time, timeinterval, appointment, icon, color, user, reference, reverse, function`.

**Choice options — verified supported** (live-tested 2026-07-14 via the companion
`ninox` skill's probes):

- Create the choice field **with** its full options list: `"options"` is an array
  of `{"name": "...", "labels"?, "color"?, "textColor"?, "icon"?, "order"?}`
  objects. Omit `id` on new options — the system assigns a stable one (they read
  back as e.g. `{"id":"1","name":"Alpha","order":0}`).
- To extend the list later, PATCH `options` resending the existing entries **with
  their ids** plus the new entries without ids; existing ids survive, and record
  writes accept the new labels immediately.
- Choice writes are label-sensitive on established fields (`"active"` fails where
  `"Active"` succeeds), so every loaded value must use the exact spelling the
  build guide specifies; the Phase-7 checks compare read-back choice values
  against the option spellings.
- Fallback: one older workspace was once seen rejecting `options` with `400
  Unrecognized key(s)`. If that recurs, create the field bare (a fresh choice
  field accepts arbitrary string labels on record writes) and set the options in
  the editor during Phase 6, marked editor-finished in the build guide.

**Function (formula / logic) fields — verified supported** (live-tested
2026-07-14: the expression persists on read-back, computed values appear in
record reads, and PATCHing `expression` revises the logic in place with existing
rows recomputing). The SKILL's cheap Phase-5 probe still confirms the regime per
workspace — older workspaces have been observed rejecting the type, and the
probe's result overrides this document and the companion skill alike. In regime A
(the expected case), create with an `"expression"` carrying the NX script the
field computes:

```json
{ "name": "full_name", "labels": { "en": "Full Name" }, "type": "function",
  "expression": "first_name + \" \" + last_name" }
```

- The expression addresses fields by their internal name, exactly as editor scripts
  do, including reads across references (`ref_field.target_field`) and aggregates
  (`sum((select invoice_line where invoice = this).line_total)`).
- `expression` is optional; omitted, the API creates an empty placeholder logic
  field to fill in the editor. In regime A don't ship placeholders — ship the
  expression from the build guide. In regime B, placeholders are exactly what
  gets shipped, and the expressions are pasted in Phase 6.
- Create function fields only after everything they read exists: the data fields,
  the reference fields, the fields on related tables, and any other function field
  they depend on. Use a separate `fields/batch` call after the data and reference
  batches, ordered by dependency; the batch is one transaction, so a single bad
  expression rolls back the lot.
- The API is a deployment channel, not a validator. Prove each expression by
  reading computed values back (`GET .../records`) against the reconciliation
  targets; fix anything wrong in the logic editor, which stays the syntax
  authority.
- Changing an expression later: `PATCH .../fields/{name}` with
  `{"expression": "..."}` is verified working — the new expression persists and
  existing rows recompute immediately. (The key is `expression`; a `script` key
  is rejected.)

**NOT writable through the API — build these in the logic editor:**
automations (On create / On update / On delete), buttons, **views, pages, and
dashboards** (there is no views API — the build guide's view specs are built by
hand in Phase 6), and the `lambda` / `html` / `react` / `styled` field kinds.
There is also no script-execution endpoint.

**Staging pattern for editor-only logic.** Ship the automation and button scripts
into the app itself, so the user copies code inside Ninox instead of out of a
document. For each editor-hosted script, create a function field whose expression
is the script wrapped as a raw string:

```json
{ "name": "zz_setup_order_on_create",
  "labels": { "en": "SETUP — paste into order → On create" },
  "type": "function",
  "expression": "---let r := first(select rate where country = this.customer.country and active = true);\nvat_rate := if r != null then r.percent else 0---" }
```

The field *displays* the script; the user opens it in the logic editor, copies the
code between the `---` markers into its real host, and confirms it there. Never
deploy a write-effect script as live expression code — formula fields recalculate
constantly and must stay side-effect free; the raw-string wrapper is what keeps the
script inert. One trap: `{ ... }` inside `---...---` is template interpolation, so
a script containing literal braces (say, JSON for `http()`) needs a plain quoted
string with escaped quotes and `\n` instead. Prefix the names (`zz_setup_`) so the
staging fields sort together and are unmistakable, and remove them with
`DELETE .../fields/batch` once everything is pasted and verified.

References: create a `reference` field with `refTableName` set to the target
table's internal name; Ninox creates the matching reverse side automatically.

### Records (rows)
| Method | Path | Notes |
|---|---|---|
| GET | `/modules/{m}/tables/{t}/records` | query params: `offset` (from 0), `limit` (1–100), `fields` (CSV of names), `sort` (field name), `filter` (JSON string, e.g. `{"status":"open"}`). Returns `{data:[{id,values}], page_info:{has_more,limit,offset}}`. Page with offset+limit until `has_more` is false. |
| POST | `/modules/{m}/tables/{t}/records` | `{ "records": [ { "<field>": value, ... } ] }`. Max **6 MB** per call → batch large loads. Returns `{data:{ids:[...]}}`. |
| PATCH | `/modules/{m}/tables/{t}/records` | `{ "records": [ { "id": <id>, "<field>": value } ] }`. Transactional, max 6 MB. |
| DELETE | `/modules/{m}/tables/{t}/records` | `{ "records": [ <id>, ... ] }`. Transactional. |
| POST | `/modules/{m}/tables/{t}/records/import/csv` | `multipart/form-data`: `file` (≤50 MB), `hasHeader`, `delimiter`, `encoding`, `importMode` (`append`/`update`/`upsert`), `numberFormat` (`us`/`european`), `batchSize` (10–200), `mappings` (JSON). Auto-maps CSV headers to field names (case-insensitive) when no `mappings` given. Applies **per row** — the response's `rowsImported`/`rowsSkipped`/`rowsUpdated` counts must be reconciled against the CSV's row count (skips are silent otherwise); `upsert` on a stable key is the retry-safe mode. Best path for bulk data load. |

Value rules when writing records: record `id`s round-trip as **strings** — pass
them back as received (numeric ids have also been accepted); `number` = number or
numeric string; `boolean`
= `true`/`false` (not 1/0); `choice` = a string or array of strings; `multi` =
array of strings; `date`/`timestamp`/`appointment` = ISO 8601 or Unix ms; `null`
clears a value. For a `reference` field, set the target record's id; confirm the
shape against a GET of an existing row's `values` for that field. `dchoice`/`dmulti`
(dynamic choice, options sourced from another table — verified working 2026-07-14):
on field create, `options` is a selection **expression string** (e.g.
`"select rates"`) with `optionName` as a display expression, and advisory
`expressionErrors` in the response can be spurious — verify the dropdown in the UI;
on record writes pass the source **record id** (`dchoice`: `"3"`, `dmulti`:
`["1","2"]`), never the display label — these writes are not validated and a wrong
string is stored verbatim.

## Errors — and the discipline for handling them
Standard HTTP codes. `2xx` success, `4xx` client error, `5xx` server, `413` payload
over 6 MB (records) / 50 MB (CSV), `429` rate limited. Error body:
`{ "error": { "message": "..." } }`.

Read the message and fix the actual cause; never mutate the design to make the call
pass. The usual suspects, worth checking before anything exotic: a name violating
`^[a-z0-9_]+$` or over 100 chars; a batch rolled back by one bad member (fix that
member, resubmit the whole batch); `refTableName` pointing at a table that doesn't
exist yet (order of operations); an expression referencing a field not created yet
(dependency order); `413` → split the payload; `429` → back off (respect any
`Retry-After` header, otherwise wait and retry with increasing delay) and slow the
batch cadence — a rate limit is throttling, not a design problem; CSV import
mismatches from `numberFormat`, encoding, or header mapping. A failing element is
never downgraded — a function field does not become a data field, a reference does
not become text. If a few distinct attempts don't fix it, stop and put the options
to the user, per the failure rule in the SKILL (never silently fail or implement a
workaround).

## A typical end-to-end build order
0. Probe: scratch module → one `function` field with an expression → read back →
   delete. Confirms the key, the workspace id, and which regime (A/B/C, SKILL
   Phase 5) this workspace is in, before the real run depends on it. Also `GET`
   the workspace root: skip or reconcile anything a previous run already created
   — re-POSTing an existing name fails, and one failure rolls back a whole batch.
1. `POST /modules` — create the app.
2. `POST /modules/{m}/tables` per table.
3. `POST .../fields/batch` per table — data fields first (choice fields **with**
   their full options lists), then `reference` fields once both tables exist.
4. `POST .../fields/batch` — `function` fields, in dependency order, once every
   field they reference exists: with their NX expressions (regime A) or as empty
   placeholders (regime B); skipped entirely in regime C.
5. Regime A only: `POST .../fields/batch` — the `zz_setup_` staging fields
   carrying each automation and button script as a raw string.
6. Load data: export inputs with `scripts/extract_data.py` (summary rows
   excluded, ISO dates, `true`/`false` booleans, dot decimals → `numberFormat=us`),
   then CSV import, or batched `POST .../records`.
7. Read back with `GET .../records` and verify: row counts per table, **and at
   least one test row per table checked field by field** against the source —
   inputs, references resolving to the right record, and the function fields'
   computed values, with type fidelity (dates, fractions vs percents, decimal
   separators, booleans, choice spelling, null vs 0).
8. In the **logic editor**: the user pastes each staging field's script into its
   automation or button host and confirms it (in regimes B/C, first the formula
   expressions from the build guide); verifies each choice field shows exactly the
   options the guide lists (setting them by hand only if the API rejected
   `options`); builds the views/dashboards the guide specifies; repairs any
   expression the read-back caught. Then `DELETE .../fields/batch` the staging
   fields.
