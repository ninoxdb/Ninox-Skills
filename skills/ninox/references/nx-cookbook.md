# NX cookbook — idioms for building an app

The existing `scripting-and-formulas.md` is organised around **migrating Excel formulas**.
This file covers the other job: writing the formulas a normal Ninox app needs. Every
pattern below was used in a live 9-table build (`printops`, 2026-09-21/22).

Read this when you are writing `function` field expressions for an app you are building.

---

## 1. Aggregating children — use the reverse relation

Creating a `reference` field on the child auto-creates a **reverse** field on the parent,
named after the **child table** (not the reference field). Check it before writing formulas:

```bash
GET /modules/{m}/tables/{parent}/fields    # look for "type": "reverse"
```

```ninox
cnt(machines)                     -- how many children
sum(production_runs.good_qty)     -- aggregate a child column
avg(production_runs.yield_pct)    -- works on computed child columns too
```

Two references from the same child table to the same parent produce two reverse fields —
read the schema rather than guessing the name.

## 2. Filtering children

Square brackets filter a relation in place:

```ninox
machine_events[started_at >= today() - 30]
orders[status != "Versendet" and status != "Storniert"]
sum(machine_events[started_at >= today() - 30].duration_min)
```

A `timestamp` field compares directly against date arithmetic — no cast needed.

## 3. Traversing two levels

Chain relation names to reach grandchildren:

```ninox
-- on a site: every run on every machine at that site
sum(machines.production_runs[started_at >= today() - 30].good_qty)
```

## 4. Null-safe aggregates

`sum()` over an empty relation returns `null`, which renders as an empty cell that reads
as "unknown" rather than "none". Normalise it:

```ninox
let v := sum(production_runs.good_qty);
if v > 0 then v else 0 end
```

Guard every divisor the same way — a percentage over a zero base should be `0`, not an error:

```ninox
let q := quantity;
let v := sum(production_runs.good_qty);
if q > 0 and v > 0 then round(v * 100 / q) else 0 end
```

## 5. Dates

```ninox
days(today(), due_date)      -- difference in days; negative = in the past
today() - 30                 -- date arithmetic in day units
```

## 6. Status buckets

```ninox
if stock_qty <= reorder_point then "Nachbestellen"
else if stock_qty <= reorder_point * 1.25 then "Niedrig" else "OK" end end
```

Return **strings** from a `function` field for status labels — a function field cannot be a
`choice`, and a downstream filter comparing against the literal is easier to read than a
numeric code.

## 7. Building a string from many rows

`join` + `for` is the workhorse (used for CSV lines, summaries, and widget payloads):

```ninox
join(for s in (select sites) do s.site_name + ": " + s.output_30d end, ", ")
```

## 8. Naming rules that affect formulas

Field and table `name` values must match `^[a-z0-9_]+$`. Beyond that, **avoid names that
collide with NX keywords** — they create fine but break expressions that reference them:

```
order  select  where  let  if  then  else  end  for  do  while  break
and  or  not  null  this  function
```

Suffix instead: `order_ref`, `select_mode`. Renaming later is a PATCH on `name`, but every
formula referencing the old name must change in the same pass, so get it right at design time.

## 9. Formula fields are pure

No `create`, no field assignment, no `delete` inside a `function` expression — they
recalculate constantly. Side effects belong in buttons and automations.

---

## Verifying a formula (do not skip)

A formula that compiles is not a formula that is correct.

1. **Check `expressionErrors`** in the create/PATCH response body — a field can be created
   with a non-working expression and still return 201.
2. **Read records back** and compare against a hand-calculated expectation, on at least one
   row where the answer is non-trivial *and* one edge row (no children, zero quantity).
3. Only then build the next formula on top of it.

```python
st, body, errs = nx.create_function('m', 't', 'pct', 'Pct', expr)
assert not errs, errs
rows = nx.all_records('m', 't', ['quantity', 'pct'])
assert rows[0]['values']['pct'] == 42   # hand-calculated
```
