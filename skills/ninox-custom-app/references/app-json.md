# app.json reference

`app.json` sits at the top level of the app folder. It does two jobs on every deploy:

1. **Schema:** Ninox creates every declared table and field the module does not have yet.
2. **Access contract:** the app's server-side gateway serves only what `app.json` declares, to the roles it names.

The parser is strict. It rejects any key it does not know, at every level, and reports all errors at once (`app.json is not valid`, with one entry per problem).

## Contents

- [Complete example](#complete-example)
- [Top-level keys](#top-level-keys)
- [Roles](#roles)
- [auth](#auth)
- [data: tables](#data-tables)
- [fields](#fields)
- [rowFilter](#rowfilter)
- [actions](#actions)
- [What a deploy does to the module](#what-a-deploy-does-to-the-module)
- [Seed CSVs and type inference](#seed-csvs-and-type-inference)
- [Typed fields without seed data](#typed-fields-without-seed-data)
- [Names](#names)
- [Limits](#limits)

## Complete example

An event registration app: a read table of published events with an internal field hidden, a create table for registrations, and a create table for uploaded documents:

```json
{
  "name": "Event registration",
  "entry": "dist/index.html",
  "routing": "static",
  "roles": { "anonymous": "000000000000000000000003" },
  "auth": { "anonymous": true },
  "data": {
    "events": {
      "read": ["anonymous"],
      "fields": {
        "code": {},
        "title": {},
        "starts_on": {},
        "seats": {},
        "internal_budget": { "read": [] }
      },
      "rowFilter": { "anonymous": "published = true" }
    },
    "registrations": {
      "create": ["anonymous"],
      "fields": { "registration_number": {}, "event_code": {}, "attendee_name": {}, "attendee_email": {} }
    },
    "registration_documents": {
      "create": ["anonymous"],
      "fields": { "registration_number": {}, "document_type": {}, "file_name": {} }
    }
  }
}
```

`published` is not in `fields`, yet it is still created, because the row filter names it. Visitors cannot read it.

## Top-level keys

| Key | Required | Rule | Default |
|---|---|---|---|
| `name` | yes | string, 1-64 characters after trimming, no control characters | none |
| `entry` | no | relative path ending in `.html`; no leading `/`, no `\`, no `.` or `..` segments | `"dist/index.html"` |
| `routing` | no | `"spa"` or `"static"` | `"spa"` |
| `roles` | yes | object, 1-32 entries | none |
| `auth` | yes in practice | object, see [auth](#auth) | none |
| `data` | no | object, at most 200 tables | none |
| `actions` | no | object, at most 100 entries | none |

Keys such as `version`, `description`, `icon`, or `$schema` are rejected with `unknown_key`.

**`entry`:** keep `dist/index.html`. The leading `dist/` is stripped and the rest must exist in the uploaded files. The app's root URL `/` always serves `dist/index.html`, so a different entry file deploys but is never shown at `/`.

**`routing`:** both values are accepted, and neither changes how files are served today. A URL path that is not a real file (for example `/booking/42`) gets an error page, not `index.html`. Use hash routes (`#/booking/42`) or real files (`/booking/index.html`, linked as `/booking/`).

## Roles

`roles` maps **app role names** to **workspace role IDs**.

- App role names match `^[a-z][a-z0-9-]{0,31}$`: lowercase, starting with a letter.
- Values are role **IDs**, never names. `"viewer"` fails with `Role viewer does not exist in this workspace.`
- Built-in IDs: viewer `000000000000000000000003`, editor `000000000000000000000002`. The admin ID `000000000000000000000001` is refused (`An app role may not map to the admin role.`).
- Use `"anonymous": "000000000000000000000003"` unless the user has a custom workspace role prepared for the app and knows its ID.

The app acts with that workspace role's permissions, and each deploy **sets** that role's access to the declared tables (see [What a deploy does to the module](#what-a-deploy-does-to-the-module)). So `app.json` is expected to do more than narrow access: mapping a `create` verb to the viewer role gives every workspace viewer create on that table inside Ninox too (unverified on a live deploy, see below).

## auth

| Key | Rule |
|---|---|
| `anonymous` | boolean |
| `providers` | array of `"app-user"`, `"ninox"` |
| `signInRedirect` | path starting with `/`, not `//` |

- The role `anonymous` must exist in `roles` **exactly when** `auth.anonymous` is `true`. Either one without the other is an error.
- An app with neither `anonymous: true` nor a provider fails with `no_principal_possible`.
- Only anonymous sessions work today. `providers` and `signInRedirect` are accepted and ignored, so leave them out: `"auth": { "anonymous": true }`.

## data: tables

Each key of `data` is a table's internal name. Each value may hold:

| Key | Meaning |
|---|---|
| `read` | app roles that may list and get rows |
| `create` | app roles that may create rows and attach files to rows they just created |
| `write` | accepted, not served yet (no update endpoint exists) |
| `delete` | accepted, not served yet |
| `fields` | the fields the app may see or write, see [fields](#fields) |
| `rowFilter` | per role, a filter every read is forced through |

Rules:

- A table must grant at least one verb to at least one role (`table_grants_nothing`).
- Every role in a verb list must be declared in `roles`, and listed once.
- There is no `update` or `upsert` verb; both are `unknown_key`.
- A table with no `fields` block exposes nothing. Reads return 403 `This app declares no readable field on <table>`.
- A table granting `read` and `create` to the same role works, but every row anyone submits is then readable by everyone. Keep personal data in create-only tables.

## fields

Each key is a field's internal name; each value is an object:

| Value | Read by | Written by |
|---|---|---|
| `{}` | every role in the table's `read` | every role in the table's `create` |
| `{ "read": [] }` | nobody (hidden) | the table's `create` roles |
| `{ "read": ["anonymous"], "write": [] }` | `anonymous` | nobody |

At most 500 fields per table. Any field the app names in a `list` filter, sort, `fields` option, or `create` payload must be declared here, or the request fails with 400 `<table> declares no field named X that may be read / filtered on / sorted by / written`.

## rowFilter

An object of app role → filter expression. Every read by that role gets the filter applied on the server, so unpublished rows never reach the browser.

Grammar (anything else fails the deploy with `unenforceable_row_filter`):

```
condition [and condition …]          at most 20 conditions, keyword "and" (any case)
condition = field operator literal   field starts with a letter or "_"
operator  = "=" | "!=" | ">" | ">=" | "<" | "<="
literal   = true | false              only with = or !=
          | null                      "= null" is empty, "!= null" is not empty
          | 42 | -3.5                 numbers
          | 'text' | "text"           no backslash escapes
```

- No `or`, parentheses, functions, arithmetic, or references to other tables.
- Each field appears at most once per filter.
- The role must have a verb on the table, or the filter is refused because it "would never apply".
- At most 2000 characters.
- The app cannot add its own filter on a field the row filter already constrains (400).
- `get(id)` on a table with a row filter is always 403; read through `list`.

Values are compared as stored. `published = true` matches a boolean field that is on and a Text field holding `true`. Range operators on a Text field compare text, so `seats > 20` is false for `"100"`. Seed numeric fields with numbers so they are created as Number.

## actions

`actions` declares server-side scripts (`{ "<name>": { "script": "<module script name>", "roles": [...], "runAs": "<role>" } }`). The deploy checks that the named module script exists, but nothing can call an action yet. Leave `actions` out.

## What a deploy does to the module

A deploy through the dialog runs these steps in order. Steps 1 and 2 run before `app.json` is checked against the module, so a deploy that fails at step 3 has still applied them.

1. **Upload** the `dist/` files into a new release.
2. **Ensure the schema**, for every table in `data`:
   - **Missing tables** are created, labelled with their internal name. A name that differs from an existing table's internal name (a typo, a label instead of the internal name) creates a new, empty table rather than failing.
   - **Missing fields** are created: the fields in `fields` plus any field named in a `rowFilter`. Fields that appear only in a CSV are not created, and that CSV column is dropped.
   - **Field types** come from the seed CSV only when the table is created in the same deploy. Otherwise new fields are Text. Existing fields are never retyped, renamed, or deleted, so a type fixed in Ninox stays fixed.
   - **Table permissions** are set: the table's read roles become the workspace roles mapped from its `read` list, and its create roles those mapped from its `create` list. A set list replaces Ninox's built-in grants. With `anonymous` mapped to viewer, editors lose read on read tables and create on create tables, and viewers gain create on create tables. Admins keep everything. Manual changes to these two lists are overwritten on the next deploy; field permissions and update or delete permissions are not touched. **Unverified:** these effects on editors and viewers follow from Ninox's permission rules but have not yet been observed on a live deploy. Until they are, deploy only into a dedicated module, and check the table permissions in Ninox after the first deploy.
   - **Seed CSVs** are imported: appended on the deploy that creates the table, merged by the first column on later deploys. Rows are never deleted.
3. **Check** `app.json` against the module (tables, fields, roles, row filters, entry file). A failure here stops the deploy, but the schema, permission, and data changes of step 2 stay.
4. **Publish** the release. Only now does the subdomain serve the new files. Until then, the previous version keeps serving.

## Seed CSVs and type inference

A CSV seeds a table when it sits at the top level of the folder and its name, minus `.csv` and an optional `-import` or `_import`, matches a declared table name, ignoring case: `events.csv`, `Events-import.csv`, and `events_import.csv` all seed `events`. Any top-level CSV named like a table is imported, so keep unrelated CSVs out of the folder.

On the deploy that creates the table, each field's type is inferred from the first 10 data rows of its column, in this order:

| Values in the first 10 rows | Field type |
|---|---|
| all of `true`, `false`, `yes`, `no`, `0`, `1` | Yes/No. A column of only `0` and `1` becomes Yes/No, not Number |
| more than 80% date ranges `2026-05-01 - 2026-05-03` | Appointment |
| more than 80% dates (`YYYY-MM-DD`, `D.M.YYYY`) or 10-13 digit numbers | Date, or Timestamp when a time or an epoch number is present. EANs and phone numbers without a prefix become Timestamp |
| all numbers (`.` or `,` decimals) | Number. One empty cell makes it Text |
| more than 70% `#rgb` / `#rrggbb` | Color |
| anything else | Text; email, phone, or URL variant when the column name contains `email`/`mail`, `phone`/`tel`, `url`/`website`/`link`, or most values look like one |

Choice, multiple choice, and reference fields are never inferred.

The first deploy appends without checking the key column. Every later deploy matches rows on the **first column** and refuses the whole CSV if that column has an empty, padded, or repeated value. A CSV with duplicate keys therefore deploys once and then blocks every update. `check_app.py` reports this as an error up front.

## Typed fields without seed data

Tables without a seed CSV, typically the create tables, get Text fields only. Visitors can still submit numbers and booleans into them; they are stored as text. To get real types:

- **After the first deploy:** retype the fields in the Ninox data model. Later deploys keep the types.
- **Before the first deploy:** create the module's tables yourself with the `ninox` skill (Public API), using exactly the internal table and field names from `app.json` and the types you want (number, date, boolean, choice). The deploy then finds them, adds only missing fields, and sets the permissions.

After retyping, values must match the type: a Yes/No field accepts only `true`/`false`, a Number field a number or numeric text, a Date field `YYYY-MM-DD` or an epoch number, and a choice field an option's name. A mismatch reaches the visitor as 500 `Internal server error`, so send typed values from the form.

## Names

Table and field names that the deploy has to create must match:

```
^[a-z0-9_]+$, 1-100 characters, not starting or ending with "_", no "__"
```

`Products`, `costPrice`, or `unit-price` are refused with `app.json names a table or field that is not a valid internal name (lowercase letters, digits and underscores only)`. Names are matched against the internal name, not the label shown in Ninox; labels can be changed freely after the deploy.

## Limits

| What | Limit |
|---|---|
| `app.json` | 1,000,000 characters |
| Seed CSVs | 20 per deploy, 5,000,000 characters each, and `app.json` plus all CSVs about 6 MB together |
| One file in `dist/` | about 5.5 MB (each file travels in one request of at most 6 MB) |
| A release | 250 MiB and 5,000 files, paths up to 1,024 characters |
| Releases kept | 20 |
