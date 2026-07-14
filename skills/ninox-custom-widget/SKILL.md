---
name: ninox-custom-widget
description: Build a Custom Widget for the Ninox v4 PageBuilder — a sandboxed HTML/CSS/JS mini-app authored inside a Ninox module that reads and writes module data through the window.Ninox SDK. Use this whenever the user wants to create, scaffold, edit, or debug a custom widget, a "scripting" widget, a widget.json manifest, or asks how to use the widget bridge / window.Ninox API (getProperties, onPropertiesChanged, get, update, create, remove, modules/tables/fields). Also use for questions about widget properties, the sandboxed iframe, or serving widget assets. Do NOT use for the separate Dynamic HTML component (the Field tag, HeroUI tags, the react script type) — that is a different feature; see docs/dynamic-html-component.md instead.
---

# Building a Ninox Custom Widget

A **Custom Widget** is a small HTML/CSS/JS app authored inside a Ninox module and dropped onto a PageBuilder page as a first-class component. It runs in a **sandboxed iframe** (`sandbox="allow-scripts"`, no `allow-same-origin`) and talks to the host page over a typed `postMessage` bridge exposed as the global `window.Ninox`. Through that bridge it receives its configured **property values** and can **read/write module data** — always under the acting user's permissions, never with direct DB access.

This feature lives behind the `CustomWidgets` feature flag (`FLAG_CUSTOM_WIDGETS`). It is distinct from the **Dynamic HTML** component (the `<Field>` tag / HeroUI / `react` script type) — do not conflate them.

## When you're asked to build a widget

1. **Clarify the intent** if it's not obvious: what should the widget display or do, which module/table/row scope does it operate in, and what configurable **properties** should the builder be able to set on it (title, color, a field name, a toggle…). Properties are typed `string | number | boolean` only.
2. **Scaffold the four files** (below). The fastest correct start is to copy the template in `assets/template-widget/` and adapt it.
3. **Wire the data/logic** using the `window.Ninox` API. Read the quick reference below; for exact signatures, edge cases, and the bridge protocol, read `references/widget-api.md`.
4. **Verify** the manifest parses and the SDK calls match their signatures. If the user wants it live, explain the authoring path (in-app IDE) — see "How widgets are stored & served".

## Anatomy of a widget

A widget is a folder under a module's `widgets/` file tree. It always contains a `widget.json` manifest plus its assets. The canonical minimal widget is four files:

```
widgets/<folder>/
├── widget.json     # manifest: name, labels, icon, declared properties
├── index.html      # entry point (the iframe loads this)
├── script.js       # widget logic — uses window.Ninox
└── style.css       # styles
```

**`index.html`** — note the SDK is loaded from a path **relative to the widget** at `../_sdk/ninox-widget.js` (the host serves it there automatically; never bundle or copy it in):

```html
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>My Widget</title>
    <link rel="stylesheet" href="style.css" />
    <script src="../_sdk/ninox-widget.js"></script>
    <script src="script.js" defer></script>
  </head>
  <body>
    <h1 id="title"></h1>
  </body>
</html>
```

**`widget.json`** — `type` is limited to `string | number | boolean`; `labels` is a localized map (`""` is the default locale); `icon` is a material-symbols name:

```json
{
  "icon": "widgets",
  "labels": { "": "My Widget" },
  "name": "My Widget",
  "properties": [
    { "labels": { "": "Title" }, "name": "title", "type": "string" }
  ]
}
```

**`script.js`** — a minimal, correct usage of the API. `onPropertiesChanged` fires immediately if values already exist, and again whenever the builder changes a property. Always call `Ninox.ready()` once you're set up — it triggers the host handshake that delivers the initial values:

```js
Ninox.onPropertiesChanged(function (props) {
  document.getElementById('title').textContent = props.title || 'My Widget';
});
Ninox.ready();
```

## window.Ninox — quick reference

The SDK is a plain global (no import, no build step). Full details in `references/widget-api.md`.

**Lifecycle & properties**
- `Ninox.ready()` — call once after setup; triggers the host `init` handshake. Required.
- `Ninox.getProperties()` — synchronous snapshot of current property values (`Record<string, boolean|null|number|string>`).
- `Ninox.getPropertySchema()` — the declared `WidgetProperty[]` from `widget.json`.
- `Ninox.onPropertiesChanged(cb)` — subscribe to value changes; fires immediately if values exist. Returns an unsubscribe function. **This is how most widgets react to config.**

**Events (host ↔ widget)**
- `Ninox.on(event, cb)` — listen for a host-relayed event; returns unsubscribe.
- `Ninox.emit(event, payload)` — send an event to the host.

**Synchronous context** — the surrounding page scope as **names** (`row` is a stringified id; each is `null` when out of scope):
- `Ninox.module()`, `Ninox.table()`, `Ninox.row()`

**Async data-model API** — every call returns a Promise and round-trips to the host, which resolves names→ids and runs the operation under the acting user's permissions. Arguments are **names**, not ids.
- `Ninox.modules()` → `{ name }[]`
- `Ninox.tables(module)` → `{ name, fields: {name,type}[] }[]`
- `Ninox.fields(module, table)` → `{ name, type }[]`
- `Ninox.field(module, table, field)` → `{ name, type }`
- `Ninox.get(module, table, row, fields)` — `fields` may be one field name (→ bare value), a `string[]`, a `{name}[]`, or a `{name: true}` record (→ `{name: value}` record).
- `Ninox.update(module, table, field, row, value)` → `undefined`
- `Ninox.create(module, table)` → new row id (`string`)
- `Ninox.remove(module, table, row)` → `undefined`

Bad names reject with `Unknown module/table/field`; a non-finite row id rejects with `Invalid row id`. Always `await`/`.catch()` these.

**Example — read a field from the current row and write it back:**

```js
async function refresh() {
  const module = Ninox.module();
  const table = Ninox.table();
  const row = Ninox.row();
  if (!module || !table || !row) return; // not in a row scope

  const { title, done } = await Ninox.get(module, table, row, { title: true, done: true });
  render(title, done);
}

async function toggleDone() {
  await Ninox.update(Ninox.module(), Ninox.table(), 'done', Ninox.row(), true);
  await refresh();
}

Ninox.onPropertiesChanged(refresh);
Ninox.ready();
```

## How widgets are stored & served (context, not something you edit directly)

- Files live in S3, keyed `scripting/{workspaceId}/{moduleId}/{relativePath}`. Builders author them through the **in-app IDE** at `/:tenantId/:workspaceId/:moduleId/scripting` (file tree + tabbed CodeMirror editor + live preview) — not by hand-editing S3.
- Assets are served (member-readable, flag-gated) at `…/workspace/:workspaceId/modules/:moduleId/widgets/:name/*`; the SDK is at `…/widgets/_sdk/ninox-widget.js`.
- On a page, a widget is referenced by a `CustomWidget` component: `{ type: 'CustomWidget', widgetName, widgetProps?, visible? }`. `widgetProps` values are static JSON **or** `{$nx}` dynamic scripts evaluated against page/row scope. Changing a property value updates the widget over the bridge **without reloading the iframe**.
- Mutations to widget files require the workspace **admin** role; reads require membership.

## Security constraints you must respect when authoring

The widget runs untrusted-by-design in a locked-down sandbox. Design within these limits:
- No `allow-same-origin`: the widget **cannot** read the parent's cookies, session, or `localStorage`. Don't try.
- Reach module data **only** through the `window.Ninox` bridge — there is no direct DB or privileged network access.
- Don't embed secrets in widget source: any workspace member can read a widget's files.

## Reference files

- `references/widget-api.md` — full `window.Ninox` method reference, the postMessage bridge protocol, the `widget.json`/manifest schema and parsing rules, and worked examples. Read it when you need exact signatures, return shapes, or protocol-level detail.
- `assets/template-widget/` — a complete, copyable hello-world widget (the four files above). Start here.
