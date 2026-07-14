# Ninox Custom Widget API — Full Reference

The author-facing API is the global `window.Ninox`, injected by the guest SDK served at
`…/widgets/_sdk/ninox-widget.js` (loaded from `../_sdk/ninox-widget.js` relative to a widget).
It is a plain IIFE — no import, no bundler, no TypeScript build. Source of truth:
`packages/workspace/src/modules/scripting/helpers/widget-sdk.ts`.

## Table of contents
1. Lifecycle & handshake
2. Properties API
3. Events API
4. Synchronous context
5. Async data-model API
6. Error handling
7. The postMessage bridge protocol
8. Manifest (`widget.json`) schema
9. The `CustomWidget` page component
10. Worked examples

---

## 1. Lifecycle & handshake

A widget's lifecycle is a handshake over `postMessage`:

1. The host renders the widget in a sandboxed iframe and starts listening.
2. The widget loads, registers any listeners, then calls **`Ninox.ready()`**.
3. The host replies with an `init` message carrying the property **schema**, the resolved
   property **values**, and the **context** (module/table/row names).
4. From then on the host pushes `props` messages whenever a value or the context changes; the
   widget receives them via `onPropertiesChanged`.

**`Ninox.ready(): void`** — Signal that the widget is loaded and listening. Must be called
exactly once. Until it is called the widget will not receive its initial values. Call it *after*
registering `onPropertiesChanged`/`on` handlers so you don't miss the first delivery.

---

## 2. Properties API

Properties are the typed, builder-configurable inputs declared in `widget.json` and set per
placed instance in the PageBuilder. Values are `boolean | null | number | string`.

**`Ninox.getProperties(): Record<string, boolean | null | number | string>`**
Synchronous snapshot of the current resolved values. Returns `{}` before the `init` handshake
completes — prefer `onPropertiesChanged` for anything that must react to values.

**`Ninox.getPropertySchema(): WidgetProperty[]`**
The declared schema from the manifest (name, type, labels, optional default). Empty before
`init`.

**`Ninox.onPropertiesChanged(cb: (props) => void): () => void`**
Subscribe to value changes. Fires **immediately** if values already exist (so registering it
before `ready()` still delivers the initial values once they arrive), and again on every host
`props`/`init` update. Returns an unsubscribe function. This is the primary reactive entry
point for most widgets.

---

## 3. Events API

A lightweight relay for custom host↔widget messaging beyond properties/data.

**`Ninox.on(event: string, cb: (payload) => void): () => void`**
Listen for a host-relayed event of the given name. Returns an unsubscribe function.

**`Ninox.emit(event: string, payload?: any): void`**
Send an event (with optional payload) to the host.

---

## 4. Synchronous context

The data-model scope surrounding the widget, as **names** (not ids). Each returns `null` when
not applicable (e.g. the widget is on a page with no row scope). `row` is a **stringified row
id**.

- **`Ninox.module(): string | null`**
- **`Ninox.table(): string | null`**
- **`Ninox.row(): string | null`**

These are synchronous reads of the last context delivered by the host; they update when the host
pushes a new `props`/context message (observe via `onPropertiesChanged`, which also fires on
context change).

---

## 5. Async data-model API

Every method returns a `Promise` and round-trips a `request` to the host, which resolves
names→ids and executes the operation **under the acting user's permissions**. Arguments are
always **names** (module/table/field) except row ids. Concurrent calls are safe (each carries a
unique correlation id).

Host-side implementation: `useWidgetApi()` in
`packages/client/src/features/pagebuilder/components/custom-widget/useWidgetApi.ts`.

| Method | Signature | Resolves to |
|---|---|---|
| `modules` | `Ninox.modules()` | `{ name: string }[]` |
| `tables` | `Ninox.tables(module)` | `{ name: string, fields: { name, type }[] }[]` |
| `fields` | `Ninox.fields(module, table)` | `{ name: string, type: string }[]` |
| `field` | `Ninox.field(module, table, field)` | `{ name: string, type: string }` |
| `get` | `Ninox.get(module, table, row, fields)` | value **or** record (see below) |
| `update` | `Ninox.update(module, table, field, row, value)` | `undefined` |
| `create` | `Ninox.create(module, table)` | new row id (`string`) |
| `remove` | `Ninox.remove(module, table, row)` | `undefined` |

### `get` — the `fields` selector

The shape of the result mirrors the shape of the `fields` argument:

- **single field name** (`string`) → the bare value:
  `await Ninox.get(m, t, r, 'title')  // => "Hello"`
- **array of names** (`string[]`) → `{ name: value }` record.
- **array of descriptors** (`{ name }[]`) → `{ name: value }` record.
- **record** (`{ name: true }`) → `{ name: value }` record:
  `await Ninox.get(m, t, r, { title: true, done: true })  // => { title, done }`

Selector normalization is `selectorFieldNames()` in `useWidgetApi.ts`.

### `update`, `create`, `remove`

- `update` writes a single field on an existing row (via the row socket) and resolves to
  `undefined`.
- `create` inserts a new empty row in the table and resolves to its new **row id** (string);
  follow with `update` calls to populate it.
- `remove` deletes the row and resolves to `undefined`.

---

## 6. Error handling

The host replies to a `request` with either a `result` or an `error` string; the SDK turns an
`error` into a rejected Promise (`new Error(message)`). Known messages:

- `Unknown module` / `Unknown table` / `Unknown field` — a name didn't resolve.
- `Invalid row id` — a non-finite row id was passed.

Always `await` inside `try/catch` or attach `.catch()`. An unhandled rejection in a widget is
easy to miss because the widget is in a sandboxed iframe with no visible console for end users.

---

## 7. The postMessage bridge protocol

Defined in `packages/core/src/messages/widget-bridge.ts`. Every message carries a discriminating
`source` field. You normally never touch this directly — the SDK wraps it — but it's the
contract to consult when debugging the bridge.

- Sources: `WIDGET_HOST_SOURCE = 'ninox-host'`, `WIDGET_GUEST_SOURCE = 'ninox-widget'`.
- **Guest → host** (`WidgetToHostMessage`):
  - `ready`
  - `event` — `{ event, payload? }`
  - `request` — `{ id, method, args }`
- **Host → guest** (`HostToWidgetMessage`):
  - `init` — `{ properties, props, context }` (sent once, after `ready`)
  - `props` — `{ props, context }` (in-place updates)
  - `event` — `{ event, payload? }`
  - `response` — `{ id, result? , error? }`
- `WidgetContext = { module: string | null, table: string | null, row: string | null }` (names).
- `WidgetPropsPayload = Record<string, boolean | null | number | string>` (dynamic `{$nx}`
  scripts are already evaluated by the host before delivery).
- Type guards: `isHostToWidgetMessage`, `isWidgetToHostMessage`.

**Security note:** the guest posts with `targetOrigin: '*'` and the host authenticates inbound
messages by `event.source === iframe.contentWindow` (sandboxed iframes have an opaque origin, so
`event.origin` is unusable). The guest accepts any message whose `source === 'ninox-host'`.

---

## 8. Manifest (`widget.json`) schema

Types and parser: `packages/core/src/entities/widget.ts`.

```ts
type WidgetPropertyType = 'boolean' | 'number' | 'string';

type WidgetProperty = {
  defaultValue?: boolean | number | string;
  labels: Localized;        // { "": "Title", "de": "Titel" } — "" is the default locale
  name: string;
  type: WidgetPropertyType;
};

type WidgetManifest = {
  icon?: string;            // material-symbols icon name, e.g. 'widgets'
  labels: Localized;
  name: string;
  properties?: WidgetProperty[];
};

type WidgetCatalogEntry = WidgetManifest & {
  folder: string;           // the widgets/<folder> name — canonical identifier
};
```

`parseWidgetManifest(raw)` is deliberately forgiving:
- Accepts an object **or** a JSON string.
- Returns `undefined` if there's no usable `name` (the widget won't appear in the catalog).
- **Drops** individual malformed properties instead of failing the whole manifest.
- `Localized` falls back to `{ '': name }` when missing/invalid.

Practical implications when authoring: a typo'd property (e.g. bad `type`) silently disappears
rather than erroring — double-check the property actually shows up in the placed widget's
settings. Property types are limited to `string | number | boolean`; there is no field-picker,
color, or enum property type at the manifest level (model such inputs as `string` and validate
inside the widget).

---

## 9. The `CustomWidget` page component

`packages/core/src/entities/component.ts`:

```ts
type CustomWidget = BaseComponent & {
  type: 'CustomWidget';
  visible?: boolean | DynamicProperty;
  widgetName: string;                                              // folder under widgets/
  widgetProps?: Record<string, boolean | DynamicProperty | number | string>;
};
```

- `widgetName` is the widget's folder name.
- `widgetProps` maps a declared property name to either a static JSON value or a `{$nx}`
  `DynamicProperty` script evaluated against the current page/row scope (host resolves it before
  delivery). Dynamic props re-evaluate on row/scope change and stream in over the bridge —
  **the iframe is not reloaded** when a value changes (its `src` depends only on
  workspace+module+widget name).
- Type guard `isCustomWidget()` is in `widget.ts`.

---

## 10. Worked examples

### A. Property-driven display (no data access)

`widget.json`:
```json
{
  "icon": "label",
  "labels": { "": "Badge" },
  "name": "Badge",
  "properties": [
    { "labels": { "": "Text" }, "name": "text", "type": "string" },
    { "labels": { "": "Highlight" }, "name": "highlight", "type": "boolean" }
  ]
}
```

`script.js`:
```js
Ninox.onPropertiesChanged(function (props) {
  var el = document.getElementById('badge');
  el.textContent = props.text || '';
  el.classList.toggle('is-highlight', props.highlight === true);
});
Ninox.ready();
```

### B. Read the current row, edit a field, write back

```js
async function load() {
  const [m, t, r] = [Ninox.module(), Ninox.table(), Ninox.row()];
  if (!m || !t || !r) {
    document.body.textContent = 'Place me inside a row context.';
    return;
  }
  try {
    const { name, active } = await Ninox.get(m, t, r, { name: true, active: true });
    document.getElementById('name').value = name ?? '';
    document.getElementById('active').checked = active === true;
  } catch (err) {
    document.body.textContent = 'Error: ' + err.message;
  }
}

document.getElementById('active').addEventListener('change', async (e) => {
  await Ninox.update(Ninox.module(), Ninox.table(), 'active', Ninox.row(), e.target.checked);
});

Ninox.onPropertiesChanged(load); // reload when context/props change
Ninox.ready();
```

### C. Create a row in a related table

```js
async function addTask(title) {
  const rowId = await Ninox.create('project', 'tasks'); // returns new row id
  await Ninox.update('project', 'tasks', 'title', rowId, title);
  await Ninox.update('project', 'tasks', 'done', rowId, false);
  return rowId;
}
```

### D. Discover the schema at runtime

```js
async function listFields() {
  const modules = await Ninox.modules();               // [{ name }]
  const tables = await Ninox.tables(modules[0].name);  // [{ name, fields: [{name,type}] }]
  const fields = await Ninox.fields(modules[0].name, tables[0].name); // [{ name, type }]
  console.log(fields);
}
```
