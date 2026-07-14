# Field types, variants, and verified value shapes

Read this before creating non-trivial fields or writing any non-scalar field type. It
consolidates the field-type catalogue, how to choose a type, and the empirically observed
write/read shapes for special types. Behaviors marked "observed" came from live testing
against real workspaces; confirm against the live field and a sample row before trusting them
in a new workspace, since Ninox versions and per-workspace state vary.

## Create-time field types

The live spec's full create-time enum: `string`, `number`, `boolean`, `choice`, `dchoice`,
`multi`, `dmulti`, `file`, `date`, `timestamp`, `time`, `timeinterval`, `appointment`,
`icon`, `color`, `user`, `reference`, `reverse`, `function`.

String variants: `text`, `multiline-text`, `phone`, `email`, `url`, `location`,
`signature`.

Discovery responses also expose internal/computed types — `rowId`, `lambda`, `html`,
`react`, and others. None of these are written through record CRUD or created via the
API. `function` is in the create enum and **verified working live** (2026-07-14): create
with `"type": "function"` + `expression`, values compute in record reads, and PATCHing
`expression` revises the logic in place — see the main skill (Schema admin).
`dchoice`/`dmulti` (dynamic choice — options sourced from another table's records rather
than a fixed list) are **verified working live** (2026-07-14): `options` carries a
selection **expression string** (not an array — e.g. `"select source_table"` or
`"(select source_table) order by label"`), and `optionName`/`optionIcon`/`optionColor`
carry display expressions evaluated per option record. The expressions persist on
read-back and PATCHing `options` works. Caveats observed: (1) create/PATCH may return
advisory `expressionErrors` for `optionName` (e.g. `Field not found: <field>`) even
though the expression is stored and correct for the source-record context — verify the
dropdown in the UI instead of trusting that error; (2) record writes are **not
validated** — any string is stored verbatim (a label like `"Red"` is accepted and stored
even though it is not a valid option value), so always write the source **record id**:
`dchoice` takes a single id string (`"3"`), `dmulti` an array of id strings (`["1","2"]`).
Prefer a plain `choice` (fixed list) or a real `reference` (link to a row) where either
fits.

## Choosing the right type

Ask: what kind of information is this, and what should users be able to do with it
(validation, links, filters, UI affordances, automation)? Don't default to `string`.

- Freeform text → `string` (`text` short, `multiline-text` for notes/addresses/comments)
- Email / phone / website / physical place → `string` with variant `email` / `phone` /
  `url` / `location`; hand-drawn signature capture → variant `signature`
- One value from a fixed list (status, stage, contact method) → `choice`
- Zero-to-many from a list (skills, tags) → `multi` (not a comma-separated string)
- Yes/no flag (is_hq) → `boolean`
- Quantity, money, percentage, probability, score, count → `number`
- Calendar date → `date`; date+time → `timestamp`; time-of-day → `time`; duration →
  `timeinterval`; scheduled event → `appointment`
- Link to a row in another table **in the same module** → `reference`
- Reverse side of a relationship → `reverse`
- Uploaded file → `file`; icon/color picker value on a record → `icon` / `color`
- Ninox user/person → `user`

### Translating SQL-style requests

When a user describes a table in SQL terms, map it to the closest Ninox schema rather than
asking them to restate it:

- `int` → `number`
- a requested business-key `PK` → Ninox already has a built-in row id, so emulate the custom
  key with `required: true` + `unique: true`
- uppercase display names (e.g. `OFFICES`) → lowercase machine name (`offices`), keep the
  requested casing in `labels`
- freeform postal address → `string` variant `multiline-text`; phone → variant `phone`

## Verified value shapes for writes

Inspect the field and a representative existing row first, then mirror the observed shape.

- **`string` / `number` / `boolean`** → the plain scalar.
- **date/time/timestamp** → a string in the appropriate textual format.
- **`reference`** → a **string row id**, e.g. `"department_link": "1"`. Reads back as a
  string row id too. Note: a `fields=`-projected read has been observed to return `null` for
  a reference field even when the full-record read returns a valid id string — for
  reference-dependent reporting, read full records rather than relying on `fields=`.
- **`multi`** → an **array of strings**, e.g. `"tags": ["Alpha", "Beta"]`; reads back as an
  array. (The spec's read schema types record `values` as scalars only, yet `multi`
  demonstrably round-trips an array — one more place live behavior outranks the documents.)
- **`user`** → a **string** value (e.g. `"owner_user": "1"`).
- **`file`** → a **string only**. Plain filename, raw Base64 text, a `data:` URL, and an HTTP
  URL have all written successfully as plain strings. JSON objects like
  `{"name": "...", "content": "..."}` are rejected (`expected a string or number`). There is
  **no documented binary upload into a `file` field through the JSON records API** (the
  documented multipart path is CSV import only); guessed routes like `/records/{id}/file`
  returned `404`. For API-only backfills, store metadata (document URL, external filename,
  hosted path) rather than raw bytes unless the user has a separate supported upload path.
- **`reverse` / `function`** → **read-only**. They reject writes
  (`field type 'reverse' is read-only and cannot be set directly`). Never include them in
  POST/PATCH payloads; if a write fails because one slipped in, remove it and retry.

### `choice` fields (label-sensitive)

- Write the **exact option label, including casing and punctuation**. `status: "active"` has
  failed where `status: "Active"` succeeded; `contract_type: "full_time"` failed against
  valid options `Full-Time`, `Part-Time`, `Contractor`, `Intern`, `Temporary`.
- A **newly created** choice field with no explicit options has accepted arbitrary label
  writes (e.g. `"Prospect"`), so freshly created fields are more permissive than established
  ones with a fixed option list.
- The `options` payload on create/update is **supported — verified live (2026-07-14)**:
  an array of `{"name": "...", "labels": {...}, "color"?, "textColor"?, "icon"?, "order"?}`
  objects (`name` required, `additionalProperties: false` — no extra keys; omit `id` on new
  options, the system assigns a stable one — created options read back as e.g.
  `{"id":"1","name":"Alpha","order":0}`). Declare the full option list at create time and
  verify it reads back. To extend later, PATCH `options` resending the existing entries
  **with their ids** plus new entries without ids — verified: ids survive and record
  writes accept the new labels immediately. (One older workspace was once seen rejecting
  `options: [...]` with `400 Unrecognized key(s)` — if that recurs, probe a scratch choice
  field, and where rejected create the field without options, load records using the exact
  intended labels, and have the user finish option setup in the UI.)
- **Anomaly to watch for:** a field can still report `type: "choice"` in schema while reading
  back stringified ids (`"1"`..`"9"`) instead of labels — often after repeated
  delete/recreate churn, with the id distribution matching some other table's distribution.
  Treat that as a red flag: stop destructive retries, compare the API field list and record
  payloads against the UI, and ask the user for confirmation/screenshots before continuing.
- If a choice field has wrong legacy options (e.g. German labels) and **no valuable stored
  values**, a backed-up delete/recreate can reset it to accept new labels. Never do this on a
  populated production field without explicit approval and a backup.

### `location` fields (variant, not a type)

A true Ninox location field is `type: "string"` with `variant: "location"`. Creating a field
with `type: "location"` is rejected.

- Record writes still take a **scalar value**, not a coordinate object. `{"latitude":...,
  "longitude":..., "address":...}`, `{"lat":..., "lon":...}`, and `[lat, lon]` are all
  rejected (`expected a string, number, or boolean`).
- The verified write format for coordinates through the Public records API is a single string
  shaped exactly like `Place Name <lat,lon>`, e.g.
  `Port of Singapore, Singapore <1.2804398,103.7578231>` or
  `North Atlantic Ocean <40.0,-40.0>`.
- Through the Public/CSV-import APIs these values round-trip as **literal text**; there is no
  evidence the API hydrates true Ninox map metadata. The documented way to produce a true
  location value with coordinates is Ninox scripting `location(title, latitude, longitude)`
  in a UI-managed formula/automation/button context.
- When the user asks for a LOCATION field: create/preserve `variant: "location"`, verify the
  field definition after mutation, populate with a **real geocodable place string** (prefer
  actual ports/airports/inland terminals over arbitrary text), and do not describe the result
  to the user as merely a "text field" just because the underlying API `type` is `string`.

## Icons: field vs object metadata

A field of `type: "icon"` is a record column. The database/app icon and table icon are
**metadata on the module or table object** (`"icon": {"icon": "..."}` in a module/table
PATCH; optional `color`/`filling` keys exist, only `icon` is required). When a user says
"database icon" they usually mean the module/app icon in workspace navigation — confirm the
level, then patch the correct object and re-read to confirm `data.icon`.

## Cross-module references and self-references

- `refTableName` resolves **within the current module**. Attempts to point a reference at a
  table in another module by name have failed with `404 table-not-found`. For cross-module
  links, create a mirror/lookup table inside the target module and reference that. Keep
  within-module links as real reference fields — don't introduce mirror tables to dodge field
  creation inside a single module.
- Self-reference fields (e.g. `reports_to` pointing at the same table) work and accept a
  string row id, but a malformed rollout has coincided with foreign-key errors before. Probe
  the pattern on a disposable table first, then create the production field, backfill with
  string row ids, and re-read sample rows to verify the links.
