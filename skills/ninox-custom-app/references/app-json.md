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
- [Names](#names)

## Complete example

A catalog that visitors read, with a hidden field and a publish flag, plus two inbox tables they write into:

```json
{
  "name": "Car rental",
  "entry": "dist/index.html",
  "routing": "static",
  "roles": { "anonymous": "000000000000000000000003" },
  "auth": { "anonymous": true },
  "data": {
    "cars": {
      "read": ["anonymous"],
      "fields": {
        "sku": {},
        "name": {},
        "daily_rate": {},
        "internal_cost": { "read": [] }
      },
      "rowFilter": { "anonymous": "published = true" }
    },
    "bookings": {
      "create": ["anonymous"],
      "fields": { "booking_number": {}, "car_sku": {}, "driver_name": {}, "total": {} }
    },
    "driver_documents": {
      "create": ["anonymous"],
      "fields": { "booking_number": {}, "document_type": {}, "file_name": {} }
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

**`routing`:** both values are accepted, and neither changes how files are served today. A URL path that is not a real file (for example `/booking/42`) is not answered with `index.html`. Use hash routes (`#/booking/42`) or real files (`/booking/index.html`, linked as `/booking/`).

## Roles

`roles` maps **app role names** to **workspace role IDs**.

- App role names match `^[a-z][a-z0-9-]{0,31}$`: lowercase, starting with a letter.
- Values are role **IDs**, never names. `"viewer"` fails with `Role viewer does not exist in this workspace.`
- Built-in IDs: viewer `000000000000000000000003`, editor `000000000000000000000002`. The admin ID `000000000000000000000001` is refused (`An app role may not map to the admin role.`).
- Use `"anonymous": "000000000000000000000003"` unless the user has a custom workspace role prepared for the app and knows its ID.

The app acts with that workspace role's permissions. `app.json` can only narrow them, never widen them.

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
condition = field operator literal
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

Match the literal to the field's type. `published = true` only works if `published` is a boolean field; a Text field holding `"true"` never matches. The check script warns when the inferred type and the literal disagree.

## actions

`actions` declares server-side scripts (`{ "<name>": { "script": "<module script name>", "roles": [...], "runAs": "<role>" } }`). The deploy checks that the named module script exists, but nothing can call an action yet. Leave `actions` out.

## What a deploy does to the module

On every deploy through the UI, for every table in `data`:

- **Missing tables** are created, with label = internal name.
- **Missing fields** are created: the fields in `fields` plus any field named in a `rowFilter`. Fields that appear only in a CSV are not created, and that CSV column is dropped.
- **Field types** are inferred from the seed CSV only when the table is created in the same deploy (first 10 rows, see the check script's "Field types" table). Otherwise new fields are Text. Existing fields are never retyped, renamed, or deleted, so a type fixed by hand in Ninox stays fixed.
- **Table permissions** are rewritten: the table's read roles become the workspace roles mapped from its `read` list, and its create roles those mapped from its `create` list. Manual changes to these two lists are overwritten on the next deploy. Expect workspace members in other roles to lose read or create access to these tables inside Ninox; workspace admins keep it. This is why the app should live in its own module.
- **Seed CSVs** are imported: appended on the deploy that creates the table, merged by the first column on later deploys. Rows are never deleted.

## Names

Table and field names that the deploy has to create must match:

```
^[a-z0-9_]+$, 1-100 characters, not starting or ending with "_", no "__"
```

`Products`, `costPrice`, or `unit-price` are refused with `app.json names a table or field that is not a valid internal name (lowercase letters, digits and underscores only)`. Names are matched against the internal name, not the label shown in Ninox. Labels can be changed freely in Ninox after the deploy.
