# CSV import

Use the dedicated import endpoint to load a CSV into a Ninox table. For a handful of simple
row inserts, use direct JSON record creates (Records section of the main skill) instead.

Endpoint:

```text
POST /api/v1/workspace/{workspaceId}/modules/{moduleName}/tables/{tableName}/records/import/csv
```

Send it as multipart form fields (`-F`), not a JSON body.

## Options

- `file` (required)
- `hasHeader` (default `true`)
- `delimiter` (auto-detected if not specified)
- `quoteChar` (default `"`)
- `encoding`: `utf8 | ascii | latin1 | iso-latin1 | utf16 | windows-cp1252` (default `utf8`)
- `importMode`: `append | update | upsert` (default `append`)
- `numberFormat`: `us | european` (default `us`)
- `batchSize`: `10..200` (default `100`)
- `mappings`: a JSON array **string**
- Documented file size limit: 50 MB.

## Pre-import workflow

1. Confirm the target module/table exists (discovery).
2. Fetch the table fields so you know the exact `fieldName` values — never guess.
3. Inspect the CSV header locally.
4. Choose the mode: `append` (new data loads), `update` (existing rows only), `upsert`
   (idempotent sync — update existing, insert missing).
5. For `update`/`upsert`, choose exactly one stable key field explicitly.

With `hasHeader=true` and no `mappings`, columns auto-map to field names by header text
(case-insensitively). That is only safe when the headers already equal the internal field
names — which is why steps 2–3 come first; whenever they differ, or for `update`/`upsert`
(which needs an explicit key mapping), pass `mappings`.

Inspect the header locally:

```bash
python3 - <<'PY' "$CSV"
import csv, sys
with open(sys.argv[1], newline='', encoding='utf-8') as f:
    print(next(csv.reader(f)))
PY
```

## Examples

Setup (same pattern as the main skill, plus the file path):

```bash
BASE="${NINOX_API_BASE:-https://go.ninox.com}"
WS="${NINOX_WORKSPACE_ID:?set NINOX_WORKSPACE_ID}"
AUTH=(-H "Authorization: Bearer $NINOX_API_KEY")
MODULE=korvik_hr; TABLE=employees; CSV=/absolute/path/to/file.csv
```

Append:

```bash
curl -s -X POST "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/records/import/csv" \
  "${AUTH[@]}" -F "file=@$CSV" -F "hasHeader=true" -F "importMode=append" \
  -F "encoding=utf8" -F "batchSize=100" | python3 -m json.tool
```

Upsert by a key column (build mappings safely in Python to avoid quoting mistakes):

```bash
MAPPINGS=$(python3 - <<'PY'
import json
print(json.dumps([
  {"csvColumnName": "Email",      "fieldName": "work_email", "updatePolicy": "key"},
  {"csvColumnName": "First Name", "fieldName": "first_name"},
  {"csvColumnName": "Last Name",  "fieldName": "last_name"},
]))
PY
)
curl -s -X POST "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/records/import/csv" \
  "${AUTH[@]}" -F "file=@$CSV" -F "hasHeader=true" -F "importMode=upsert" \
  -F "mappings=$MAPPINGS" | python3 -m json.tool
```

`update` mode uses the same shape — keep exactly one mapping with `"updatePolicy": "key"`.

Semicolon-delimited European CSV:

```bash
curl -s -X POST "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/records/import/csv" \
  "${AUTH[@]}" -F "file=@$CSV" -F "hasHeader=true" -F "delimiter=;" \
  -F "numberFormat=european" -F "encoding=utf8" -F "importMode=append" | python3 -m json.tool
```

No header row — map positional `column_1`, `column_2`, ... to field names:

```bash
curl -s -X POST "$BASE/api/v1/workspace/$WS/modules/$MODULE/tables/$TABLE/records/import/csv" \
  "${AUTH[@]}" -F "file=@$CSV" -F "hasHeader=false" \
  -F 'mappings=[{"csvColumnName":"column_1","fieldName":"first_name"},{"csvColumnName":"column_2","fieldName":"last_name"}]' \
  | python3 -m json.tool
```

## Mappings format

```json
[{"csvColumnName": "Email", "fieldName": "work_email", "updatePolicy": "key"}]
```

Map all important data columns explicitly; for `update`/`upsert` include exactly one `key`
field, and choose a genuinely stable unique key (email, external id, employee code).

## Expected response and verified behavior

Responses report three counts — summarize all three back to the user:

```json
{"data": {"rowsImported": 100, "rowsSkipped": 0, "rowsUpdated": 0}}
```

Observed in live testing:

- `append` with header mappings: imported rows, none skipped/updated.
- `update`: updates matching keys, skips rows whose key isn't found
  (e.g. `rowsImported: 0, rowsSkipped: 1, rowsUpdated: 1`).
- `upsert`: updates existing keys and inserts missing ones
  (e.g. `rowsImported: 1, rowsSkipped: 0, rowsUpdated: 1`).
- The skip counts show the import applies **per row**, not all-or-nothing: one call can
  update some rows and skip others. So never treat a completed call as "all rows landed" —
  reconcile the three counts against the CSV's row count, chase any skips, and treat a
  failed or interrupted import as possibly partially applied until a read proves otherwise.
  `upsert` with a stable key is the retry-safe mode: re-running it converges instead of
  duplicating.
- CSV import stores scalar cell values **literally**. For a `variant: "location"` field, a
  plain place string, `Place <lat,lon>`, `location("Title",lat,lon)`, and JSON-ish text all
  round-tripped as plain strings — no evidence the import hydrated true map metadata.

After import, query representative rows via the records endpoint to verify.

## Common pitfalls

1. Guessing field names — inspect the table first.
2. Using labels instead of API field names in mappings.
3. A bad key choice for update/upsert — use a stable unique field.
4. Skipping CSV header inspection — mappings fail when header text differs.
5. Encoding/delimiter mismatches — non-UTF-8 and semicolon CSVs are common.
6. No post-import verification.
