# App SDK reference

The SDK is one ES module file, `ninox-app-sdk.js` (version 0.1.0), shipped in `assets/template-app/dist/`. Copy it into the app's `dist/` and import it relatively. The app host does not serve it, and the `@ninox/app-sdk` package on npm is an older, unrelated iframe bridge.

```html
<script type="module">
  import { createNinoxApp, isRateLimited } from './ninox-app-sdk.js';
  const app = createNinoxApp();
</script>
```

With a bundler, import from the local file (`import { createNinoxApp } from './lib/ninox-app-sdk.js'`) so the SDK ends up in the bundle.

## Contents

- [Session](#session)
- [createNinoxApp](#createninoxapp)
- [list](#list)
- [get](#get)
- [create](#create)
- [attach](#attach)
- [Values that come back](#values-that-come-back)
- [Errors and limits](#errors-and-limits)
- [Patterns](#patterns)

## Session

The first SDK call starts an anonymous session (`POST /_ninox/api/v1/session/anonymous`) and keeps the token in memory for 15 minutes. On a 401 it starts a new session once and retries. Nothing is stored in cookies or `localStorage`.

All calls go to `/_ninox/api/v1` on the app's own origin. The gateway sends no CORS headers, so the app must be served from its Ninox subdomain to talk to Ninox; opening `dist/index.html` from disk shows the layout but every data call fails. Ship no files under a `_ninox/` folder.

A redeploy ends every open session; the SDK recovers by itself on the next call.

## createNinoxApp

```ts
createNinoxApp(options?: { baseUrl?: string; fetch?: typeof fetch }): NinoxApp

interface NinoxApp {
  readonly appRole: string | null;  // "anonymous" once a session exists
  start(): Promise<void>;           // optional: start the session up front
  table(name: string): NinoxTable;  // name = table key in app.json
}
```

Leave `options` empty in production.

## list

```ts
table(name).list({
  fields?: string[],                         // default: every readable field; max 200 names
  filter?: Record<string, value | { operator, value? }>,
  limit?: number,                            // 1-200, default 50
  offset?: number,                           // >= 0
  sort?: { field: string, direction: 'asc' | 'desc' }[],
}) → Promise<{ rows: { id: string, values: object }[], hasMore: boolean, limit: number, offset: number }>
```

Needs the table's `read` verb. Filter operators: `IS`, `IS_NOT`, `CONTAINS`, `DOES_NOT_CONTAIN`, `GREATER_THAN`, `GREATER_THAN_OR_EQUAL`, `LESS_THAN`, `LESS_THAN_OR_EQUAL`, `IN`, `NOT_IN`, `IS_EMPTY`, `IS_NOT_EMPTY`. A bare value is shorthand for `IS`.

```js
const page = await app.table('extras').list({
  filter: { category: 'insurance', price: { operator: 'LESS_THAN', value: 20 } },
  sort: [{ field: 'price', direction: 'asc' }],
  limit: 200,
});
```

Every filtered or sorted field must be declared and readable, and must not be one the table's `rowFilter` already constrains.

To read more than 200 rows, page with `offset` until `hasMore` is false.

## get

```ts
table(name).get(id, { fields?: string[] }) → Promise<{ id: string, values: object }>
```

Always 403 on a table with a `rowFilter`. Read those through `list` with a filter on a key field.

## create

```ts
table(name).create(values: Record<string, unknown>) → Promise<{ id: string }>
```

Needs the table's `create` verb, and every key must be a declared, writable field. Send plain JSON values: strings, numbers, booleans, `YYYY-MM-DD` strings for dates.

## attach

```ts
table(name).attach(id, file: Blob | File, fileName?: string) → Promise<{ documentId: string }>
```

Adds the file to the row's documents in Ninox.

- Only for a row **this page instance created** with `create`. The proof is kept in memory, so a page reload loses it and `attach` throws `Files can only be attached to a row this page created`.
- Needs the table's `create` verb.
- PDF, JPEG, PNG, GIF, or WebP, detected from the file content, at most **1 MiB**. Anything else is 415 `Only PDF and image files (JPEG, PNG, GIF, WebP) are accepted`. Check type and size in the browser before submitting.

## Values that come back

`values` is keyed by internal field name and holds the stored value:

- Numbers come back as numbers.
- Choice and multiple-choice fields return the option **ID**, not the label. Store display text in Text fields when the app has to show it.
- File fields return `{ documentId, documentVersionId, fileName, fileSize, fileType }`, not a URL.
- Treat booleans and dates defensively: compare with `value === true || value === 'true'`, and format dates with `String(value).slice(0, 10)`.
- Hidden fields (`{ "read": [] }`) and undeclared fields are absent.
- Never insert values into HTML unescaped.

## Errors and limits

Every failure throws `NinoxAppError` with `status`, `message`, and `code` (`FORBIDDEN`, `BAD_REQUEST`, `NOT_FOUND`, `TOO_MANY_REQUESTS`, `UNAUTHORIZED`, `UNEXPECTED_ERROR`). Helpers: `isForbidden(error)` (403), `isRateLimited(error)` (429).

| Status | Message (start) | Cause |
|---|---|---|
| 403 | `This app does not declare the table X` | table missing from `data` |
| 403 | `This app does not grant anonymous read access to X` | verb missing on the table |
| 403 | `This app declares no readable field on X` | no `fields` block, or every field hidden |
| 403 | `X has a row filter, so a record on it is read through the collection route` | `get` on a filtered table |
| 403 | `The row filter declared for anonymous on X cannot be enforced` | row filter outside the grammar |
| 400 | `X declares no field named F that may be …` | field not declared, hidden, or not writable |
| 404 | `No app is served at this address` | app unpublished, suspended, or Custom Apps off |
| 413 / 415 | upload too large / wrong type | see [attach](#attach) |
| 429 | rate limited | see below |

Rate limits per minute: 300 requests per visitor IP, 6000 per app, 20 session starts per visitor. A 429 carries `Retry-After`. Load catalogs once per page view and keep them in memory.

## Patterns

**Load several catalogs in parallel**, then render:

```js
async function listAll(table, options = {}) {
  const rows = [];
  for (let offset = 0; ; offset += 200) {
    const page = await app.table(table).list({ ...options, limit: 200, offset });
    rows.push(...page.rows.map((row) => ({ id: row.id, ...row.values })));
    if (!page.hasMore) return rows;
  }
}
const [cars, extras] = await Promise.all([listAll('cars'), listAll('extras')]);
```

**Link rows with a shared key.** The deploy never creates reference fields, so related rows carry a common key instead. Generate a key in the browser (`CR-${Date.now().toString(36).toUpperCase()}`) and write it into a Text field on every related row.

**Write children first, parent last,** so a parent row exists only when everything it needs was saved:

```js
const bookingNumber = `CR-${Date.now().toString(36).toUpperCase()}`;
for (const doc of documents) {
  const row = await app.table('driver_documents').create({ booking_number: bookingNumber, document_type: doc.type });
  await app.table('driver_documents').attach(row.id, doc.file, doc.file.name);
}
await app.table('bookings').create({ booking_number: bookingNumber, total });
```

**Disable the submit button while saving,** and show `error.message` on failure; there is no server-side de-duplication.
