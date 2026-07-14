---
name: ninox
description: >-
  Use for any work with the Ninox Public API: inspecting workspace/module/table/field
  schema (discovery), reading/filtering/creating/updating/deleting records, creating or
  modifying modules/tables/fields (schema admin), importing CSV data (append/update/upsert),
  and writing Ninox script/formula expressions or migrating Excel formulas into Ninox.
  Covers the Ninox 4 API at go.ninox.com (workspace/modules generation, not the older
  api.ninox.com teams/databases API). Trigger this whenever the user mentions Ninox, a Ninox workspace/module/table, the
  go.ninox.com API, a NINOX_API_KEY, or asks to build, query, or modify a Ninox database or
  app structure through the API — even if they don't name the specific operation.
license: MIT
compatibility: "Requires curl and python3. Needs a NINOX_API_KEY environment variable or a saved ~/.ninox/.env file; NINOX_API_BASE (default https://go.ninox.com) and NINOX_WORKSPACE_ID are optional. Works on Linux, macOS, and Windows."
metadata:
  version: "2.4.0"
  tags: [ninox, api, database, discovery, records, crud, schema, csv, scripting]
---

# Ninox Public API

## What Ninox is (context)

Ninox is a low-code database platform for building custom business applications
(CRMs, trackers, inventory, HR tools) without traditional development. Users build apps in
a visual **builder UI**: data lives in **tables** of **records** with typed **fields**;
relationships are **reference** fields between tables; presentation is **views, forms,
pages, and dashboards**; and logic — formula fields, buttons, and automations (On create /
On update / etc.) — is written in **Ninox script (NX)**, a JavaScript-like expression
language, inside the builder's logic editor. In **Ninox 4**, apps are organized as
**modules** inside a **workspace**, and the platform exposes the **Public API** at
`go.ninox.com` that this skill operates: schema and data access for those workspaces.

**Scope note — two API generations exist.** This skill covers the **Ninox 4 Public API**
(`go.ninox.com`, path root `/api/v1/workspace/{workspaceId}`, hierarchy
workspace → modules → tables → fields/records, workspace-scoped API keys). Older Ninox
documentation (docs.ninox.com/en/api) describes a different, earlier-generation API at
`api.ninox.com/v1` with a `teams → databases → tables` hierarchy and different payload
shapes — do not mix the two: their base URLs, paths, auth scopes, and record formats are
not interchangeable. If an integration or doc snippet mentions `teams` or
`api.ninox.com`, it belongs to that other generation, not to this skill.

One skill for operating the **Ninox Public API** end to end: discover schema, read and
write records, manage modules/tables/fields, import CSV data, and author Ninox
script/formula expressions. Everything is driven through `curl` + `python3`.

## What the API does and does not cover

The documented Public API covers **data, schema, and workspace** resources:
workspace info, module CRUD, table CRUD, field CRUD (incl. batch), record CRUD, and CSV
import. It does **not** document endpoints for the UI/builder layer — views, pages,
dashboards, layouts, tabs, or saved presentation settings. Those are configured in the
Ninox builder UI, documented in the general product docs (start at
`https://docs.ninox.com/getting-started`). Schema/data changes can *indirectly* change what
users see, but there is no documented API for mutating UI constructs directly. Creation of
logic/`function` (formula) fields via the API **is supported**: create with
`"type": "function"` plus an `expression` (Ninox script), and revise the logic later by
PATCHing `expression`. Verified live (2026-07-14, workspace `h59x05245p0i`): the
expression persists on read-back, computed values appear in record reads, and a PATCHed
expression recomputes existing rows. If a particular (e.g. older) workspace ever rejects
it, the quick probe under Schema admin settles it and the scripting reference has the
fallback.

When a user asks to "create a database/app" in Ninox, there is no single create-database
endpoint. Implement it as: create a **module** → create **tables** → create **fields** →
seed rows or import CSV.

## Setup

Configure these as environment variables (only `NINOX_API_KEY` is strictly required):

```bash
NINOX_API_KEY=your_workspace_api_key
NINOX_API_BASE=https://go.ninox.com   # optional; this is the default
NINOX_WORKSPACE_ID=your_workspace_id  # optional but strongly recommended
```

Shell setup pattern used throughout this skill:

```bash
BASE="${NINOX_API_BASE:-https://go.ninox.com}"
WS="${NINOX_WORKSPACE_ID:?set NINOX_WORKSPACE_ID or pass a workspace id explicitly}"
AUTH=(-H "Authorization: Bearer $NINOX_API_KEY")
JSON=(-H "Content-Type: application/json")
```

`https://go.ninox.com` is the observed working base. The OpenAPI spec publishes no fixed
`servers` value, so keep the base URL configurable rather than hardcoding the host. The
workspace id lives in the Ninox UI where the API key was generated (Workspace →
Integrations) — read it from there or ask the user rather than guessing.

### Reading the workspace id from a Ninox app URL

Users often paste a `go.ninox.com` browser link instead of a raw workspace id. Its path
segments are ordered **organization first, workspace second**:

```text
https://go.ninox.com/{organizationId}/{workspaceId}/...
```

Example: in `https://go.ninox.com/akohj3gceovg/h59x05245p0i/Q/A`, the organization id is
`akohj3gceovg` and the **workspace id is `h59x05245p0i`** — the second path segment is
what goes into `NINOX_WORKSPACE_ID` and the `/api/v1/workspace/{workspaceId}` path root.
Any further segments (here `/Q/A`) are UI navigation state, not API identifiers. Don't
mix the two ids up: using the organization id as the workspace id fails discovery.

Note that the
`AUTH` pattern expands the key into curl's argument list, which is visible in process
listings on shared machines; where that matters, feed curl a config file instead:
`curl -K <(printf 'header = "Authorization: Bearer %s"\n' "$NINOX_API_KEY") ...`. The key
belongs only in the environment or in the `.env` file described below — never in scripts,
repos, command lines typed by hand, or any synced/shared folder.

### Persisting workspaces and API keys in `~/.ninox/.env`

Ask the user if they want credentials saved for future sessions. If yes, keep them in a
single well-known file:
**`~/.ninox/.env`** (Windows: `%USERPROFILE%\.ninox\.env`). This location is deliberate —
it is per-user, outside any repository (no accidental commit), and outside synced folders
like OneDrive/Dropbox (no cloud copies of secrets). Create the directory if missing and,
on POSIX systems, restrict it: `mkdir -p ~/.ninox && chmod 700 ~/.ninox` (then
`chmod 600 ~/.ninox/.env`).

File format — the default workspace uses the bare variable names; additional workspaces
get an uppercase suffix (a short alias you choose from the workspace's purpose):

```bash
# ~/.ninox/.env
NINOX_API_BASE=https://go.ninox.com

# Default workspace
NINOX_WORKSPACE_ID=h59x05245p0i
NINOX_API_KEY=xxxxxxxxxxxx
# org: akohj3gceovg  (from https://go.ninox.com/akohj3gceovg/h59x05245p0i/...)

# Additional workspace: CRM
NINOX_WORKSPACE_ID_CRM=abc123def456
NINOX_API_KEY_CRM=xxxxxxxxxxxx
```

Keep a `# org: ...` comment (and optionally the source URL) next to each workspace so the
id can be traced back to its app link later.

Working rules:

- **At the start of any Ninox task, check this file first.** If it exists and holds a
  matching workspace, load it instead of asking the user for credentials again:
  `set -a; . ~/.ninox/.env; set +a` (bash), or parse it line-by-line in PowerShell.
- **Never save credentials without asking first.** When the user supplies a new workspace
  id, app URL, or API key, ask them explicitly whether they want it saved to
  `~/.ninox/.env` — and write it only after a clear yes (append, don't overwrite other
  entries). Supplying a key for a task is not consent to persist it.
- **Never print the key back** in output or logs when reading the file — echo only the
  workspace id/alias to confirm which entry is in use.
- If the same workspace appears with a different key, ask the user which is current and
  update the entry rather than accumulating stale duplicates.

## Live API docs and the order of authority

The API evolves faster than any snapshot, this one included (spec last diffed against
`docs-json`: 2026-07-08). When live behavior and this document disagree, consult upward:

1. **A live probe of the target workspace** — highest authority, always wins.
2. **The live OpenAPI spec**: Swagger UI at `https://go.ninox.com/api/docs`,
   machine-readable at `https://go.ninox.com/api/docs-json` / `.../docs-yaml`.
3. **This skill** — a verified snapshot of spec + empirical testing.
4. **The markdown endpoint docs** at
   `https://docs.ninox.com/ninox-api/api-reference/api-endpoints.md` — observed to lag the
   live Swagger; useful for prose context. Any docs.ninox.com page also answers questions
   via `?ask=<question>`.

If a build plan depends on a capability this document marks as varying (function fields,
choice options, dchoice/dmulti), fetch `docs-json` and/or run the probe before committing
to the plan.

## The core operating loop: discover → mutate → verify

This is the single most important habit for safe Ninox work. For any non-trivial task:

1. **Discover** the live target before touching it (module/table/field names and types).
2. **Mutate** using exact machine `name` values, not display labels.
3. **Verify** by re-reading the affected object — schema *and* representative records.

Two rules that prevent most failures:

- **Use machine names, never UI labels, in paths, payloads, and formulas.** Paths and
  field keys use identifiers like `korvik_hr`, `employees`, `work_email` — not
  `Korvik HR` or `Work Email`. Labels are for humans; the API is name-driven.
- **Don't trust a stale schema.** If the user fixed something in the UI, recovered from an
  error, or you just ran a bulk write, re-read the table, its fields, and sample records
  before preparing the next change. Bulk writes can succeed at the HTTP layer while the
  surrounding table/module state becomes untrustworthy; if a table or module disappears
  from discovery right after a write, stop and ask the user before rebuilding.

## Resource hierarchy

```text
workspace
└── modules
    └── tables
        ├── fields
        └── records
```

Path root for everything: `/api/v1/workspace/{workspaceId}`.

---

## Discovery

Inspect structure before reading/writing data or changing schema.

```bash
# Workspace overview (richest top-level call)
curl -s "$BASE/api/v1/workspace/$WS" "${AUTH[@]}" | python3 -m json.tool

# Modules in the workspace
curl -s "$BASE/api/v1/workspace/$WS/modules" "${AUTH[@]}" | python3 -m json.tool

MODULE=korvik_hr
# One module
curl -s "$BASE/api/v1/workspace/$WS/modules/$MODULE" "${AUTH[@]}" | python3 -m json.tool
# Tables in a module
curl -s "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables" "${AUTH[@]}" | python3 -m json.tool

TABLE=employees
# One table (best single-table inspection before writes/mutations)
curl -s "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE" "${AUTH[@]}" | python3 -m json.tool
# Fields in a table
curl -s "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/fields" "${AUTH[@]}" | python3 -m json.tool
# One field
curl -s "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/fields/work_email" "${AUTH[@]}" | python3 -m json.tool
```

Responses wrap payloads in a top-level `data`. Useful nested keys: `modules[].name`,
`modules[].tables[].name`, `tables[].fields[].name`, and per field `type`, `variant`,
`required`, `unique`, `search`, `readRoles`, `writeRoles`.

Compact field-name + type listing for one table:

```bash
curl -s "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/fields" "${AUTH[@]}" \
| python3 - <<'PY'
import json, sys
for field in json.load(sys.stdin).get('data', []):
    print(f"{field['name']}: {field.get('type')} variant={field.get('variant')}")
PY
```

---

## Records

Core endpoint: `/api/v1/workspace/{workspaceId}/modules/{moduleName}/tables/{tableName}/records`

Create, update, and delete are **batch-shaped** — even a single record uses an array.

### Read, filter, paginate

```bash
# List
curl -s "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/records" "${AUTH[@]}" | python3 -m json.tool
```

Query params: `offset`, `limit` (1–100, tops out at 100 — paginate beyond that), `fields`
(comma-separated names), `sort`, and `filter` (a JSON **string**, so URL-encode it).

```bash
# Filter by a field (URL-encode the JSON filter)
FILTER_JSON='{"work_email":"alice@example.com"}'
FILTER_ENC=$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$FILTER_JSON")
curl -s "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/records?filter=$FILTER_ENC&limit=1" "${AUTH[@]}" | python3 -m json.tool
```

Read response shape: `{"data": [{"id": "123", "values": {...}}], "page_info": {"has_more": ..., "limit": ..., "offset": ...}}`.

### Create

```bash
curl -s -X POST "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/records" \
  "${AUTH[@]}" "${JSON[@]}" \
  -d '{"records": [
        {"first_name": "Alice", "work_email": "alice@example.com"},
        {"first_name": "Bob",   "work_email": "bob@example.com"}
      ]}' | python3 -m json.tool
```

A create response may return **only ids**, e.g. `{"data":{"ids":["105"]}}`, not full rows.
Parse `data.ids`, preserve order, then re-read to verify. Batch payloads top out around
**6 MB** (`413`) — split large loads into multiple calls, or use CSV import for bulk.

### Update (each record needs an `id`)

```bash
curl -s -X PATCH "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/records" \
  "${AUTH[@]}" "${JSON[@]}" \
  -d '{"records": [
        {"id": 123, "status": "Active"},
        {"id": 124, "status": "Inactive"}
      ]}' | python3 -m json.tool
```

Record ids: reads return them as **strings** (`data[].id`; create returns string ids too),
but the live spec types the *write* side numerically — PATCH `records[].id` and the DELETE
ids array are documented as positive **integers**. Both forms have been observed to be
accepted on writes; prefer numeric ids in PATCH/DELETE payloads to stay in-spec (convert
the strings you read), and treat string acceptance as leniency a stricter validator could
withdraw. This applies to the `id` key in record writes only — a `reference` *field value*
stays the string row id per the field-types reference.
Documented response shapes: PATCH returns `{"data": {"updatedIds": [...]}}` and DELETE
returns `{"data": {"deletedIds": [...]}}` (string ids). Live responses have been observed
to under-report (e.g. a shorter list than the number of rows patched) — treat any 2xx as
an acknowledgement and verify with a read.

### Delete (array of ids in a JSON body)

```bash
curl -s -X DELETE "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/records" \
  "${AUTH[@]}" "${JSON[@]}" \
  -d '{"records": [123, 124]}' | python3 -m json.tool
```

### Higher-level patterns (no native single-record or upsert endpoint exists)

- **Find by natural key:** filter on a stable unique field (email, external id, code) with
  `limit=1`. Note: `filter=` works only on real fields — filtering on the implicit row `id`
  returns `404 field-not-found`. To target a specific id, paginate or filter a stable key.
- **Update/delete by filter:** query matching rows → extract `data[].id` → PATCH/DELETE
  with those ids. Echo the count and ids back to the user before any destructive delete.
- **Pseudo-upsert:** query by stable key → update by id if found, else create.
- **Create-then-link:** prefer the ids returned by the create call, in batch order. If you
  must re-resolve by name, only do so on a guaranteed-unique value — duplicate names will
  collapse several new rows onto one id and leave children under-linked. After create,
  assert `len(created_ids) == len(source_rows)` before creating dependent rows.

Paginate a whole table:

```bash
OFFSET=0
while :; do
  RESP=$(curl -s "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/records?offset=$OFFSET&limit=100" "${AUTH[@]}")
  echo "$RESP" | python3 -c 'import json,sys;[print(r["id"], r.get("values",{})) for r in json.load(sys.stdin).get("data",[])]'
  echo "$RESP" | python3 -c 'import json,sys;p=json.load(sys.stdin).get("page_info",{});print("MORE" if p.get("has_more") else "DONE")' | grep -q MORE || break
  OFFSET=$((OFFSET+100))
done
```

### Writing special field types

Inspect the field (and a representative existing row) before writing non-scalar types, then
mirror the observed shape. The verified write shapes and their gotchas — `reference`
(string row id), `choice` (label-sensitive), `multi` (array), `user`/`file` (string only),
`location` (`Place Name <lat,lon>` string), and read-only `reverse`/`function` fields —
are documented in **`references/field-types-and-values.md`**. Read it before any write that
touches those types.

---

## Schema admin

Manage modules, tables, and fields. Discover first, confirm exact existing names, echo
destructive plans to the user, and re-read after every mutation.

### Modules

```bash
# Create
curl -s -X POST "$BASE/api/v1/workspace/$WS/modules" "${AUTH[@]}" "${JSON[@]}" \
  -d '{"name": "recruiting", "labels": {"": "Recruiting"}, "openRoles": ["admin", "editor"]}' | python3 -m json.tool
# Update (labels/visibility/icon). When a user says "database/app icon" they usually mean the module icon.
curl -s -X PATCH "$BASE/api/v1/workspace/$WS/modules/recruiting" "${AUTH[@]}" "${JSON[@]}" \
  -d '{"labels": {"": "Talent Acquisition"}, "icon": {"icon": "local_shipping"}}' | python3 -m json.tool
# Delete
curl -s -X DELETE "$BASE/api/v1/workspace/$WS/modules/recruiting" "${AUTH[@]}" | python3 -m json.tool
```

### Tables

```bash
# Create
curl -s -X POST "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables" "${AUTH[@]}" "${JSON[@]}" \
  -d '{"name": "candidates", "labels": {"": "Candidates"},
       "createRoles": ["admin","editor"], "deleteRoles": ["admin"],
       "readRoles": ["admin","editor"], "writeRoles": ["admin","editor"],
       "hasGlobalSearch": true, "hasHistory": true}' | python3 -m json.tool
# Update (labels/visibility/history/icon) and Delete follow the same /tables/{name} path with PATCH / DELETE.
```

Optional flags beyond the examples — modules also take `isHidden` and `hideNavigation`;
tables also take `hasFiles`, `isHidden`, and `isEmailLinkingEnabled`. Required keys per
the spec: module create needs only `name`; table create needs `name` + `labels`; field
create needs `name` + `labels` + `type`.

### Fields

```bash
# One field
curl -s -X POST "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/fields" "${AUTH[@]}" "${JSON[@]}" \
  -d '{"name": "work_email", "labels": {"": "Work Email"}, "type": "string", "variant": "email",
       "search": true, "unique": true, "required": false,
       "readRoles": ["admin","user"], "writeRoles": ["admin"]}' | python3 -m json.tool

# Batch create (POST .../fields/batch with a JSON array)
curl -s -X POST "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/fields/batch" "${AUTH[@]}" "${JSON[@]}" \
  -d '[{"name":"first_name","labels":{"":"First Name"},"type":"string","required":true},
       {"name":"desired_salary","labels":{"":"Desired Salary"},"type":"number"}]' | python3 -m json.tool

# Reference field (needs refTableName; resolves WITHIN the same module — see note below)
curl -s -X POST "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/fields" "${AUTH[@]}" "${JSON[@]}" \
  -d '{"name":"department_ref","labels":{"":"Department"},"type":"reference","refTableName":"departments"}' | python3 -m json.tool

# Update / delete one field: PATCH or DELETE .../fields/{fieldName}
# Batch delete: DELETE .../fields/batch  -d '{"fieldNames": ["legacy_code","old_status"]}'
```

### Probing function-field support (fallback diagnostic — normally not needed)

`function` field creation is **supported and verified live** (see the note at the top), so
you normally skip this. If a specific workspace behaves oddly (e.g. an older version
rejects the type or drops the expression), settle it empirically in under a minute, on a
disposable table (or a scratch table created for the purpose — never a production one):

```bash
# 1. Try to create a probe formula field
curl -s -X POST "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/fields" "${AUTH[@]}" "${JSON[@]}" \
  -d '{"name":"zz_probe_fx","labels":{"":"Probe"},"type":"function","expression":"1 + 1"}' | python3 -m json.tool
# 2. Read it back — is the type "function" and is the expression still there?
curl -s "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/fields/zz_probe_fx" "${AUTH[@]}" | python3 -m json.tool
# 3. Clean up
curl -s -X DELETE "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/fields/zz_probe_fx" "${AUTH[@]}"
```

Three possible outcomes, each with a different plan:

- **Accepted, and the expression reads back intact** → formula logic can be deployed
  through the API on this workspace (and scripts can be shipped in-app — see the staging
  pattern in the scripting reference).
- **Field created, but the expression is rejected or reads back empty** → create empty
  `function` placeholders via the API; the user pastes each expression in the UI editor.
- **`type: "function"` rejected outright** → create only the input fields via the API; the
  user creates the formula fields in the UI, with the script delivered as copy-paste text
  or persisted as notes (scripting reference).

Record which outcome this workspace gave. Probe per workspace — never carry the answer
over from a different workspace or from memory of an earlier session.

The same probe pattern settles any capability edge a workspace throws doubt on. `choice`
`options` are likewise verified supported (see the field bullet below) — probe them the
same way only if a workspace misbehaves: create a probe choice field with
`"options": [{"name": "Alpha"}, {"name": "Beta"}]`, read it back (do the options
survive?), delete it. `dchoice`/`dmulti` are verified too (see the create-enum note
above) — probe with an `options` expression string only if a workspace misbehaves.

### Field types, variants, and choosing the right one

Create-time field types (the live spec's full enum): `string`, `number`, `boolean`,
`choice`, `dchoice`, `multi`, `dmulti`, `file`, `date`, `timestamp`, `time`,
`timeinterval`, `appointment`, `icon`, `color`, `user`, `reference`, `reverse`,
`function`. String variants: `text`, `multiline-text`, `phone`, `email`, `url`,
`location`, `signature`.
(Discovery responses also surface internal types like `rowId`, `lambda`, `html`, `react` —
those are never written through record CRUD or created via the API. `function` is in the
create enum and verified working (see the field-creation bullet below). `dchoice`/`dmulti` —
dynamic choice sourced from another table's records — are also **verified working live
(2026-07-14)**: their `options` key carries a selection **expression string** (not an
array, e.g. `"select source_table"`), and `optionName`/`optionIcon`/`optionColor` carry
display expressions; the expressions persist and PATCHing `options` works. Two caveats:
the create/PATCH response may include advisory `expressionErrors` for `optionName` (it
appears to be validated in the wrong context) even though the expression is stored and
correct — verify in the UI rather than trusting that error; and record writes to these
fields are **not validated** — any string is stored verbatim, so write the source
**record id** (`dchoice`: `"3"`; `dmulti`: `["1","2"]`), never the display label. Prefer a
plain `choice` (fixed list) or a real `reference` (link to a row) where either fits.)

Reason from the *meaning* of the data, don't default everything to `string`: status from a
fixed list → `choice`; a link to another table → `reference`; tags → `multi`; a yes/no flag
→ `boolean`; money/probability → `number`; email/website/place → `string` with the matching
variant. Full selection guidance and SQL-style translation rules (`int`→`number`, business
`PK`→`required:true`+`unique:true`, etc.) are in `references/field-types-and-values.md`.

Key constraints and gotchas to know up front:

- **Names** match `^[a-z0-9_]+$` (max 100 chars). Keep the requested human casing in
  `labels`. `labels` is often required even when you only set the default — the
  empty-string key (`{"": "..."}`) is the observed-working default form; locale-keyed
  labels (`{"en": "..."}`) also appear in the docs and the wild. When editing existing
  objects, mirror whatever shape discovery returns.
- **Roles:** module/table role arrays use `admin`/`editor`; field role arrays use
  `admin`/`user`.
- **`choice` fields:** always decide and state the exact allowed labels up front (e.g.
  `Active`, `On Leave`, `Probation`, `Terminated`). The `options` array on field
  create/update **is supported — verified live (2026-07-14)**: objects of
  `{"name": "...", "labels"?, "color"?, "textColor"?, "icon"?, "order"?}` — `name` is
  required; omit `id` on new options and the system assigns a stable one (created options
  read back as e.g. `{"id":"1","name":"Alpha","order":0}`). To PATCH the option list,
  resend the existing options **with their ids** plus the new options without ids —
  verified: existing ids survive and record writes accept the new labels immediately.
  (Older workspaces were once seen rejecting `options` with `400 Unrecognized key(s)`; if
  that ever recurs, probe a scratch field and fall back to creating the field bare — a
  fresh choice field accepts arbitrary label writes — with the user finishing the option
  list in the UI.) Never ship a production `choice` field with an unspecified option set.
  See the field-types reference for the "choice field reads back as id-bucket" anomaly to
  watch for.
- **`reference` fields resolve within the current module.** Cross-module links by
  `refTableName` fail with `404 table-not-found`. For cross-module relationships, use a
  mirror/lookup table inside the target module. Within a module, always use real reference
  fields rather than mirror tables.
- **Logic/`function` (formula) fields are created directly via the API** with
  `"type": "function"` and an `expression` holding the Ninox script. Verified live
  (2026-07-14): the expression persists on read-back, computed values appear immediately
  in record reads, and PATCHing `expression` revises the logic in place (existing rows
  recompute). Note the key is `expression` — a `script` key is rejected. If an unusual
  workspace rejects the type, run the probe above and fall back per the scripting
  reference.
- **`type`, `variant`, and `refTableName` cannot be changed by PATCH** — the documented
  `UpdateFieldBody` simply doesn't include them (which matches the observed
  "variant PATCH doesn't take" behavior). To change any of them: back up the values,
  delete the field, recreate it with the desired shape, restore the values. What PATCH
  *does* document: `name` (rename — update every formula referencing the old name first),
  `labels`, `expression` (so a working function field's logic can be revised in place),
  `options`, `index`/`required`/`search`/`unique`, and the role arrays.
- **New-table visibility:** before creating a dependent reference that points at a
  just-created table, re-read both the module's tables listing and the direct table endpoint
  in the same run. New tables have been observed to read fine, then later vanish / return
  `404`. If either check fails, stop and ask the user to confirm UI visibility.

---

## CSV import

For loading a file into a table (as opposed to a handful of direct row inserts, which belong
in the Records section above). Supports `append`, `update`, and `upsert` with explicit
column→field mappings. Endpoint:
`POST .../tables/{tableName}/records/import/csv`. Inspect the table fields and the CSV header
first, then build mappings deliberately — never guess field names. The full option list,
encoding/delimiter handling, mapping format, and verified behavior are in
**`references/csv-import.md`**.

---

## Scripting and formulas

For Ninox script/formula expressions — logic-field bodies, button actions, validation, or
migrating Excel formulas into Ninox. **For the full Excel→Ninox use case** — porting a
workbook into a Ninox app rather than translating individual formulas — check whether the
companion **excel-to-ninox** skill is available and use it: it covers workbook inspection,
logic transcription, the build-guide discipline, and the phased API build, and it defers
to this skill for API mechanics. Formula-field logic deploys directly through the API
(`"type": "function"` with an `expression` — verified working, see Schema admin); only if
a workspace unexpectedly rejects that is the fallback copy-paste-ready Ninox script the
user pastes into a UI logic field (or that you persist in a notes/text field as a
precursor). Formulas must use internal `name` values and
durable business fields, never UI labels or import-only/positional fields. The language
patterns, the Excel→Ninox translation workflow, and the FIFO/inventory pattern are in
**`references/scripting-and-formulas.md`**. Read it whenever the task involves Ninox formulas
or Excel formula migration.

---

## Errors and limits

Standard HTTP codes; error body `{"error": {"message": "..."}}`. Read the message and fix
the actual cause — never mutate the design (a different field type, a dropped batch member)
to make a call pass. The usual causes, in rough order of frequency:

- a `name` violating `^[a-z0-9_]+$` or over 100 chars, or a missing `labels` object;
- a schema batch rolled back by one bad member — fix that member, resubmit the whole batch;
- `refTableName` pointing at a table that doesn't exist yet (order of operations) or that
  lives in another module (module-scoped — `404 table-not-found`);
- `filter` on the implicit row `id` (`404 field-not-found` — filter a real field);
- a write including a read-only `reverse`/`function` field;
- `413` — payload over ~6 MB (records) / 50 MB (CSV): split the batch or switch to CSV
  import;
- `429` — rate limited: back off (respect any `Retry-After` header, otherwise retry with
  increasing delay) and slow the call cadence; throttling is not a design problem.

Treat 2xx responses as acknowledgements, not proof (create may return only ids; PATCH may
under-report) — the verify step of the operating loop is what confirms the outcome.

---

## Common pitfalls

1. **Labels instead of machine names.** Paths, payloads, and formulas all use API `name`s.
2. **Skipping discovery.** Both row writes and schema changes are safer after inspection;
   re-read after every mutation, and never trust a schema from earlier in the conversation
   after a UI-side fix or a bulk write.
3. **Treating batch responses as proof.** Create may return ids only; PATCH may under-report.
   Verify with a follow-up read.
4. **Filtering on the implicit row id.** `filter={"id":...}` returns `404`; use a real field.
5. **Guessing special field shapes.** Confirm `reference`/`choice`/`multi`/`user`/`file`/
   `location` write shapes against the field-types reference and a live row.
6. **Writing read-only fields.** `reverse` and `function` fields reject writes — never
   include them in POST/PATCH payloads.
7. **Cross-module references.** `refTableName` is module-scoped; mirror/lookup tables are the
   fallback for cross-module links.
8. **Assuming capability edges either way.** Probe before the build plan depends on
   it. If a workspace ever contradicts a verified capability, the probe outcome wins; when
   in doubt, diff against the live spec (`https://go.ninox.com/api/docs-json`).
9. **Destructive churn on choice fields.** Repeated delete/recreate around a production
   choice field can desync API and UI state — stop, compare against the UI, and ask the user.

## Verification checklist

- [ ] `NINOX_API_KEY` set; base URL resolves to `${NINOX_API_BASE:-https://go.ninox.com}`
- [ ] `~/.ninox/.env` checked for saved credentials first; new workspace ids/keys saved
      there only after the user explicitly confirmed (keys never echoed back)
- [ ] Workspace, module, table, and field names read from the API, not guessed
- [ ] Discovery done before schema-dependent writes/imports
- [ ] Reads succeeded before writes; update/delete payloads carry the right `id`s
- [ ] Special field write shapes verified before writing them
- [ ] Destructive operations confirmed with the user beforehand
- [ ] Affected object re-read (schema and representative records) after mutation
