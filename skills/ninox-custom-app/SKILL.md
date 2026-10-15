---
name: ninox-custom-app
description: >-
  Build a Ninox Custom App: a standalone web app served on its own subdomain and backed by
  tables in a Ninox 4 module. Produces the complete upload folder (app.json data model,
  seed CSVs, a built dist/ folder with the app SDK) and walks the user through the
  "Deploy custom app" dialog in module Settings. Use when the user wants a custom app,
  a public web page, portal, catalog, booking or order form, or landing page that reads
  from or writes into Ninox; mentions app.json, createNinoxApp, @ninox/app-sdk, a
  ninoxapps.com subdomain, or "Deploy custom app"; or needs help fixing a custom app
  deploy that fails. Not for Custom Widgets inside the PageBuilder (window.Ninox,
  widget.json): that is the ninox-custom-widget skill.
license: MIT
compatibility: "Needs python3 for the bundled check script (standard library only). No API key needed: the user deploys through the Ninox web UI. Deploying requires a workspace admin and the Custom Apps feature enabled for the workspace."
metadata:
  version: "0.1.0"
  tags: [ninox, custom-app, app.json, deploy, web-app, sdk]
---

# Building a Ninox Custom App

A **Custom App** is a static web app (HTML, CSS, JS) that Ninox hosts at `https://<subdomain>.ninoxapps.com`. Visitors open it without a Ninox account. The app reads and writes rows in the tables of one Ninox module through a small SDK, and `app.json` declares exactly which tables, fields, and rows a visitor may touch. Ninox enforces that declaration on the server.

The user uploads one folder in the module's Settings. This skill produces that folder:

```
<app-folder>/                 ← the folder the user selects in "Choose folder"
├── app.json                  ← data model + access rules; tables are created from it
├── <table>-import.csv        ← optional seed data, one CSV per table, top level only
└── dist/                     ← the built app; uploaded as-is
    ├── index.html            ← must sit at the dist/ root
    ├── ninox-app-sdk.js      ← copied from assets/, never installed from npm
    └── …                     ← text files only: .html .js .css .svg .json
```

## The capability fence

Check the request against what custom apps do today before designing anything. Everything outside this fence fails at deploy or at runtime, so name the gap to the user up front and agree on a scope that fits.

- **Visitors are anonymous.** There is no sign-in. Every visitor gets the same role, and anything the app can read is readable by anyone with the URL.
- **Two data operations:** `read` (list rows, filtered server-side by a fixed row filter) and `create` (insert rows, attach a PDF or image of up to 1 MiB to a row the page just created).
- **No update or delete** of rows, no server-side logic, no `actions`. Prices and totals computed in the browser are written as submitted.
- **Files in `dist/` must be text.** The upload reads every file as text, so PNG, JPG, and font files arrive corrupted. Use inline SVG, CSS, emoji, or images hosted at an external `https://` URL.
- **No server-side routing fallback.** Use one `index.html` with hash routing (`#/step-2`), or real `.html` files per page.

## Workflow

### 1. Scope the app

Settle with the user: who visits, what they see, what they submit, and which Ninox module the app lives in. Use a **dedicated, new module**: each module holds at most one custom app, and every deploy rewrites the read and create roles of the declared tables (see `references/app-json.md`, "Roles").

Done when you have a table list where every table is either a **catalog** (read, maybe with a publish flag) or an **inbox** (create only), each with its field names, and every requested feature sits inside the capability fence.

### 2. Write `app.json`

Read `references/app-json.md` before writing it: the parser rejects unknown keys at every level and returns all errors at once.

- Names of tables and fields are lowercase `snake_case`. The tables are created from these names.
- Declare **every** field the app reads, filters, sorts, or writes. An undeclared field is invisible to the app.
- Hide sensitive catalog fields with `{ "read": [] }`; filter catalog rows with a `rowFilter` such as `"published = true"`.
- Map the `anonymous` role to the built-in viewer role ID `000000000000000000000003`, and set `"auth": { "anonymous": true }`.

Done when every table and field the UI will touch is declared with the verb it needs, and nothing else is.

### 3. Write the seed CSVs

One CSV per catalog table, named `<table>-import.csv`, next to `app.json`. The first deploy creates the table, **infers each field's type from the first 10 rows**, and imports the rows. Later deploys merge rows by the first column.

- First column: a unique, non-empty business key (`sku`, `code`). Never padded with spaces.
- Every CSV column is declared in `app.json`. Undeclared columns are dropped silently.
- Booleans `true`/`false`, dates `YYYY-MM-DD`, numbers with a `.` decimal point, no thousands separators. No empty cells in the first 10 rows of a number or boolean column, or it becomes Text.
- UTF-8 without BOM, comma-delimited, `"` quoting.

Inbox tables have no CSV, so all their fields are created as **Text**. Tell the user. If typed fields matter (numbers, dates), offer to create those tables with typed fields before the first deploy using the `ninox` skill (Public API); the deploy keeps existing fields and only adds missing ones.

### 4. Build `dist/`

Start from `assets/template-app/` (a catalog plus request form that already passes the check), or adapt its patterns. Read `references/app-sdk.md` for the SDK API and the read/write patterns.

- Load the SDK from the copy in `dist/`: `import { createNinoxApp } from './ninox-app-sdk.js'` in a `<script type="module">`. The `@ninox/app-sdk` package on npm is a different, older SDK and does not work here.
- Read with `table(name).list(...)`, up to 200 rows per page. `get(id)` is refused on tables with a `rowFilter`.
- Write children before the parent row, and attach files right after `create`, in the same page session.
- Escape every value you put into HTML; catalog data is user-editable in Ninox.
- With a framework (Vite, React, Svelte): set `base: './'`, inline assets, bundle the SDK from the local copy, and build into `dist/`. The check script then validates the build output.

Done when every screen the user asked for exists and every SDK call targets a declared table and field.

### 5. Check and package

```bash
python3 scripts/check_app.py <app-folder> --subdomain <subdomain> --package <output-folder>
```

The script replays the deploy's validation offline: the `app.json` rules, table and field names, row filters, CSV keys and the field types the first deploy will infer, binary or credential files in `dist/`, broken relative links, SDK calls against undeclared tables or fields, and the subdomain rules. `--package` writes a clean upload folder (no `__MACOSX`, `.DS_Store`, or stray files); add `--zip` for a copy to send around.

Done when the script reports **0 errors**, and every warning is either fixed or explained to the user in one line. Show the user the "Field types" table it prints.

### 6. Guide the deploy

Walk the user through `references/deploy-guide.md` in their language, one stage at a time, and wait for their confirmation or screenshot between stages:

1. **Before:** confirm the subdomain (permanent, globally unique), the target module, and that they are workspace admin.
2. **Deploy:** module Settings → "Custom app" → "Deploy custom app" → subdomain → "Choose folder" (the app folder itself) → "Deploy custom app".
3. **Verify:** open the app, check that the catalog loads, submit one test entry, find it in Ninox.
4. **Fix types** of Text fields in inbox tables if needed.

When something fails, match the message against the troubleshooting table in `references/deploy-guide.md`, fix the folder, rerun the check, and redeploy with "Update custom app".

Done when the user has opened the live app and seen a test entry arrive in Ninox.

## Reference files

- `references/app-json.md`: the full manifest schema, row filter grammar, roles, and what each deploy does to the module's tables. Read before writing or changing `app.json`.
- `references/app-sdk.md`: `createNinoxApp` API, list filters, create and attach, errors, rate limits, and the returned value shapes. Read before writing app code.
- `references/deploy-guide.md`: the user-facing deploy walkthrough (English and German UI labels), redeploys and rollbacks, and a troubleshooting table of error messages. Read when guiding the deploy or diagnosing a failure.
- `scripts/check_app.py`: offline validator and packager.
- `assets/template-app/`: a complete, deployable starter app, including `dist/ninox-app-sdk.js`.
