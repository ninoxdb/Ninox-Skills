---
name: ninox-custom-app
description: >-
  Build a Ninox Custom App: a standalone web app served on its own subdomain and backed by
  tables in a Ninox 4 module. Produces the complete upload folder (app.json data model,
  seed CSVs, a built dist/ folder with the app SDK), tests it locally against a mock of the
  Ninox gateway, and walks the user through the "Deploy custom app" dialog in module Settings.
  Use when the user wants a custom app or any public web app that reads from or writes into
  Ninox: a directory, catalog, form, survey, booking or order request, multi-step wizard with
  uploads, portal, or public dashboard; mentions app.json, createNinoxApp, @ninox/app-sdk, a
  ninoxapps.com subdomain, or "Deploy custom app"; or needs help fixing a custom app deploy.
  Not for Custom Widgets inside the PageBuilder (window.Ninox, widget.json): that is the
  ninox-custom-widget skill.
license: MIT
compatibility: "Needs Python 3 for the bundled scripts (standard library only); on Windows run them with `python` if `python3` opens the Microsoft Store. No API key needed: the user deploys through the Ninox web UI. Deploying requires a workspace admin and Custom Apps enabled for the workspace."
metadata:
  version: "0.2.0"
  tags: [ninox, custom-app, app.json, deploy, web-app, sdk]
---

# Building a Ninox Custom App

A **Custom App** is a static web app (HTML, CSS, JS) that Ninox hosts at `https://<subdomain>.ninoxapps.com`. Visitors open it without a Ninox account. The app reads and writes rows in the tables of one Ninox module through a small SDK, and `app.json` declares exactly which tables, fields, and rows a visitor may touch. Ninox enforces that declaration on the server.

The user uploads one folder in the module's Settings. This skill produces that folder:

```
<app-folder>/                 ← the folder the user selects in "Choose folder"
├── app.json                  ← data model + access rules; tables are created from it
├── <table>.csv               ← optional seed data, one CSV per table, top level only
└── dist/                     ← the built app; uploaded as-is
    ├── index.html            ← must sit at the dist/ root
    ├── ninox-app-sdk.js      ← copied from assets/template-app/dist/, never installed from npm
    └── …                     ← text files only: .html .js .css .svg .json
```

## The capability fence

Every table in a custom app grants visitors one or both of two operations. Everything else fails at deploy or at runtime, so check the request against this list before designing anything.

- **read:** list rows, filtered on the server by a fixed row filter (for example `published = true`). Everything a visitor can read, anyone with the URL can read: all matching rows, not only "their own".
- **create:** insert rows, and attach a PDF or image of up to 1 MiB to a row the page just created.
- **Visitors are anonymous.** There is no sign-in, no "my account", and no update or delete of rows. Prices, totals, and availability computed in the browser are written as submitted.
- **Create tables are open to everyone.** Anyone with the URL can submit, as often as they like; there is no captcha or de-duplication. Only rate limits apply.
- **Files in `dist/` must be text.** The upload reads every file as text, so PNG, JPG, and font files arrive corrupted. Use inline SVG, CSS, emoji, or images hosted at an external `https://` URL.
- **No server-side routing fallback.** Use one `index.html` with hash routing (`#/step-2`), or real `.html` files per page.

## Workflow

### 1. Map the request onto read and create tables

Settle with the user who visits, what they see, what they submit, and which Ninox module the app lives in. Use a **dedicated, new module**: each module holds one custom app, and every deploy rewrites the permissions of the declared tables (`references/app-json.md`, "What a deploy does to the module").

Most requests are one of these shapes, or a combination:

| The user asks for | Tables | Pattern |
|---|---|---|
| Directory, catalog, menu, price list, FAQ, event calendar, job board, knowledge base | read, with a publish flag in a row filter | load with `list`, search and filter in the browser |
| Contact or lead form, order or booking request, registration, survey, feedback, application, incident report | create; read tables for the options to pick from | form → `create` |
| Multi-step wizard with uploads (claims, onboarding, rentals) | several create tables joined by a key the browser generates | write children first, the parent row last, attach right after each `create` |
| Public dashboard, status board, statistics, map | read only | aggregate in the browser, page through up to a few thousand rows |
| Catalog plus requests (shop without payment, room booking) | read + create | both patterns side by side |

Requests outside the fence get a named gap and an alternative that fits:

- **Personal views** ("my bookings", "track my order"): not possible. Never make a table of submitted personal data readable to support a lookup; `list` hands every row to anyone. Confirm by email from inside Ninox instead.
- **Editing, cancelling, approving:** done by the team inside Ninox; the app only submits.
- **Payments, emails, guaranteed availability (no double booking):** handled in Ninox after a submission arrives, or outside the app.

Before moving on, agree with the user that the app is public, that create tables accept anything from anyone, and that collecting personal data needs a privacy notice in the app. Whether a notice is legally sufficient is the operator's call (their data protection officer), not the skill's.

Done when every table is read, create, or both, with its field names; readable fields hold only personal data the operator means to publish (a staff directory, say) and has a legal basis for; and the user has accepted the points above.

### 2. Write `app.json`

Read `references/app-json.md` before writing it: the parser rejects unknown keys at every level and returns all errors at once.

- Names of tables and fields are lowercase `snake_case`. Ninox creates the tables from these names, and a name that differs from an existing table's internal name silently creates a new, empty table.
- Declare **every** field the app reads, filters, sorts, or writes. An undeclared field is invisible to the app.
- Hide internal fields of read tables with `{ "read": [] }`; filter rows with a `rowFilter` such as `"published = true"`.
- Map the `anonymous` role to the built-in viewer role ID `000000000000000000000003`, and set `"auth": { "anonymous": true }`. This is expected to change permissions inside Ninox too: workspace viewers gain create on the create tables, and editors lose read on the read tables and create on the create tables. That follows from Ninox's permission rules but is not yet confirmed on a live deploy, so tell the user and keep the app in its own module.

Done when every table and field the UI will touch is declared with the verb it needs, and nothing else is.

### 3. Write the seed CSVs

One CSV per table that starts with data, named `<table>.csv` or `<table>-import.csv`, next to `app.json`. The first deploy creates the table, **infers each field's type from the first 10 rows**, and imports the rows. Later deploys merge rows by the first column.

- First column: a unique, non-empty business key (`sku`, `code`). Never padded with spaces.
- Every CSV column is declared in `app.json`. Undeclared columns are dropped silently.
- Booleans `true`/`false`, dates `YYYY-MM-DD`, numbers with a `.` decimal point and no thousands separators. No empty cells in the first 10 rows of a number or boolean column. Codes of 10 to 13 digits (EANs, phone numbers) need a prefix, or they become timestamps.
- UTF-8 without BOM, comma-delimited, `"` quoting.

Tables without a CSV get **Text** fields only. If typed fields matter (numbers, dates, choices), follow "Typed fields without seed data" in `references/app-json.md` before the first deploy.

### 4. Build `dist/`

Start from `assets/template-app/` (a catalog plus a request form with a privacy notice), or reuse its patterns for any other shape. Read `references/app-sdk.md` for the SDK API and patterns.

- Copy `assets/template-app/dist/ninox-app-sdk.js` into `dist/` and import it: `import { createNinoxApp } from './ninox-app-sdk.js'` in a `<script type="module">`. It is a patched build; take no other copy, and not the `@ninox/app-sdk` npm package, which is a different SDK.
- Pass **literal table names** to `app.table('orders')`. The check verifies every literal name, and the verb and fields of calls made directly on it (`app.table('orders').create({...})`) or on a handle bound once (`const orders = app.table('orders')`). Calls inside a helper such as the template's `listAll` get the name check only. Read through `listAll`, which pages 200 rows at a time and stops when a page brings nothing new.
- Read rows of a filtered table with `list`; `get(id)` is refused there.
- Escape every value you put into HTML; data in Ninox is editable by the team and, in create tables, by anyone.
- Put the operator's privacy notice next to every form that collects personal data.
- Keep the `[hidden] { display: none !important; }` rule from the template CSS when views toggle `hidden`.
- With a framework (Vite, React, Svelte): set `base: './'`, inline assets, bundle the SDK from the local copy, and build into `dist/`.

Done when every screen the user asked for exists and every SDK call targets a declared table and field.

### 5. Run it locally

```bash
python3 scripts/mock_gateway.py <app-folder> 8787
```

The mock serves `dist/` at `http://localhost:8787/` and answers the SDK like the Ninox gateway, from the app's own `app.json` and seed CSVs: verbs, declared and hidden fields, row filters, paging, the attach rules, and an error instead of `index.html` for paths that are not files. `GET /_mock/created` lists what the app created. Use a browser tool if you have one; otherwise give the user the command and URL.

Done when every screen loads its data, every form's submission appears in `/_mock/created` with the expected fields, and the console shows no errors you did not provoke.

### 6. Check and package

```bash
python3 scripts/check_app.py <app-folder> --subdomain <subdomain> --package <output-folder> --overwrite
```

The script replays the deploy's validation offline: the `app.json` rules, table and field names, row filters, CSV keys and the field types the first deploy will infer, binary or credential files in `dist/`, broken links and imports, SDK calls against undeclared tables or fields, readable fields that look like personal data, and the subdomain rules. It also notes how each deploy will change the tables' permissions inside Ninox. `--package` writes a clean upload folder next to the app folder; `--overwrite` replaces an earlier package; `--zip` adds a zip for sending around.

Done when the script reports **0 errors**, and every warning is either fixed or explained to the user in one line. Show the user the "Field types" table and the permission notes.

### 7. Guide the deploy

Walk the user through `references/deploy-guide.md` in their language, one stage at a time, and wait for their confirmation or screenshot between stages:

1. **Before:** confirm the subdomain (permanent, globally unique), the target module, and that they are workspace admin.
2. **Deploy:** module Settings → "Custom app" → "Deploy custom app" → subdomain → "Choose folder" (the packaged folder itself) → "Deploy custom app".
3. **Verify:** open the app, check that data loads, submit one test entry, find it in Ninox.
4. **Tidy up:** retype Text fields in create tables if needed, and check the permission changes.

When something fails, match the message against the troubleshooting table in `references/deploy-guide.md`, fix the folder, rerun steps 5 and 6, and redeploy with "Update custom app".

Done when the user has opened the live app and seen a test entry arrive in Ninox.

## Reference files

- `references/app-json.md`: manifest schema, roles and permission effects, row filter grammar, what each deploy does to the module, typed tables without seed data. Read before writing or changing `app.json`.
- `references/app-sdk.md`: `createNinoxApp` API, list filters, create and attach, value shapes, errors and limits, patterns. Read before writing app code.
- `references/deploy-guide.md`: the user-facing deploy walkthrough (English and German UI labels), updates, and a troubleshooting table of error messages. Read when guiding the deploy or diagnosing a failure.
- `scripts/check_app.py`: offline validator and packager.
- `scripts/mock_gateway.py`: local stand-in for the Ninox gateway.
- `assets/template-app/`: a complete, deployable starter app, including the patched `dist/ninox-app-sdk.js`.
