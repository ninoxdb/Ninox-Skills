#!/usr/bin/env python3
"""
inspect_xlsx.py - map the logic AND structure of an .xlsx/.xlsm without loading it whole.

Pure standard library (zipfile + xml.etree). No openpyxl, no pandas, no installs.
Runs unchanged on any Python 3 platform, so it travels with the skill. Streams the
worksheet XML and clears each element, so memory stays flat on huge files.

Per sheet it reports everything a database port needs:
  - populated_cells, regions (disconnected rectangular blocks), or a column_profile
    fallback above --max-cells
  - first_formula_per_column (the column template that repeats down every row),
    array_formulas, shared_formula_masters. Shared-formula *followers* are resolved:
    a column whose cells only inherit a formula anchored in another column still
    reports its (reference-shifted) formula, so no formula-bearing column can pass
    as literal data.
  - summary_cells / likely_summary_rows: bottom aggregates over their own column
    (a =SUM(C2:C401) totals row). These are NOT row templates and their rows must
    be excluded from any data load.
  - formula_vs_literal_per_column and mixed_columns, each classified by direction:
    literal_overrides_in_formula_column (values typed over a formula - ask the user
    which wins) versus summary_formula_in_literal_column (a totals row inside a
    hand-entered column - exclude the row, no override question to ask).
  - data_table_formulas (what-if data tables), table calculated_columns (formulas
    stored in the table definition), and workbook-level flags for vba_macros,
    pivot_tables (with row/column/value fields and aggregation functions), and
    external_links - logic that lives outside this sheet's cell formulas entirely.
  - column_cardinality: exact distinct count up to a few hundred, a KMV estimate
    beyond that (never a bare ">64"), plus the distinct/rows ratio - the raw signal
    for entity extraction (ratio near 0 with many rows -> its own table or a choice;
    ratio near 1 -> an identifier or free text). Values listed when <= 12.
  - sample_values (shared strings resolved; sampled from the top AND the bottom of
    the sheet so trailing totals/notes rows are visible)
  - column_types: inferred type per column from the cell number formats
    (date / datetime / time / percent / currency / number / text / boolean), with
    quoted literals, escape sequences and [..] sections stripped first so a format
    like 0.00" USD" is a number, not a date. Date columns get a serial sanity
    check; boolean cell types are detected from the cells themselves.
  - formula_errors: cached error values (#DIV/0!, #N/A, ...) per column - the
    source workbook is already broken there, or the misses are expected; ask.
  - data_validations: dropdown / list rules -> candidate choice fields. Inline
    lists give their options directly; range-fed lists (the common case) are
    resolved by reading the referenced cells, one level of defined-name
    indirection included. A large referenced range is usually an entity list ->
    a reference to an extracted table, not a choice field.
  - hidden_columns, hidden_rows, and (workbook level) hidden sheet state - hidden
    is one of the strongest scratch/artifact signals there is.
  - conditional_formatting rules (range, type, operator, formulas): colour rules
    often encode real business thresholds ("balance < 0"), not just decoration.
  - merged_cells, comments (author intent), excel_tables (named ListObjects)
Workbook level: defined_names (with scope), date_system_1904 flag, vba/pivot/
external-link flags.

Usage:
  python inspect_xlsx.py FILE.xlsx [--sheet NAME] [--max-cells N] [--sample N]
"""
import argparse
import heapq
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict, deque
from xml.etree import ElementTree as ET

A1 = re.compile(r"^([A-Z]+)(\d+)$")
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
# Built-in number-format codes that have no entry in styles.xml
BUILTIN_FMT = {
    0: "General", 1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00",
    5: '"$"#,##0_);("$"#,##0)', 6: '"$"#,##0_);[Red]("$"#,##0)',
    7: '"$"#,##0.00_);("$"#,##0.00)', 8: '"$"#,##0.00_);[Red]("$"#,##0.00)',
    9: "0%", 10: "0.00%", 11: "0.00E+00", 12: "# ?/?", 13: "# ??/??",
    14: "mm-dd-yy", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy",
    18: "h:mm AM/PM", 19: "h:mm:ss AM/PM", 20: "h:mm", 21: "h:mm:ss",
    22: "m/d/yy h:mm", 37: "#,##0;(#,##0)", 38: "#,##0;[Red](#,##0)",
    39: "#,##0.00;(#,##0.00)", 40: "#,##0.00;[Red](#,##0.00)",
    41: '_(* #,##0_);_(* (#,##0);_(* "-"_);_(@_)',
    42: '_("$"* #,##0_);_("$"* (#,##0);_("$"* "-"_);_(@_)',
    43: '_(* #,##0.00_);_(* (#,##0.00);_(* "-"??_);_(@_)',
    44: '_("$"* #,##0.00_);_("$"* (#,##0.00);_("$"* "-"??_);_(@_)',
    45: "mm:ss", 46: "[h]:mm:ss", 47: "mmss.0", 48: "##0.0E+0", 49: "@",
}
KMV_K = 256          # K-minimum-values sketch size (estimate error ~ +/-6%)
KMV_BUF = 8192       # keep exact hashes up to this many, then switch to the sketch
MAXH = (1 << 64) - 1
# Excel serials for 1927..9999 - the "looks like a real date" plausibility window
SERIAL_LO, SERIAL_HI = 10000, 2958465


def col_to_num(c):
    n = 0
    for ch in c:
        n = n * 26 + (ord(ch) - 64)
    return n


def num_to_col(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def local(tag):
    return tag.rsplit("}", 1)[-1]


def open_workbook(path):
    """Open an .xlsx/.xlsm with clear diagnostics instead of a raw traceback."""
    try:
        head = open(path, "rb").read(8)
    except OSError as e:
        sys.exit(f"error: cannot read {path}: {e}")
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        if head.startswith(b"\xd0\xcf\x11\xe0"):
            sys.exit(f"error: {path} is a legacy binary .xls workbook. Convert it to "
                     ".xlsx (Excel or LibreOffice 'Save As') and rerun.")
        sys.exit(f"error: {path} is not an .xlsx/.xlsm workbook (not a zip archive). "
                 "This skill takes Excel workbooks; convert the file to .xlsx first.")
    names = set(zf.namelist())
    if "xl/workbook.bin" in names:
        sys.exit(f"error: {path} is a binary .xlsb workbook, which this inspector "
                 "cannot stream. Convert it to .xlsx and rerun.")
    if "xl/workbook.xml" not in names:
        sys.exit(f"error: {path} is a zip but not an Excel workbook "
                 "(no xl/workbook.xml - an .ods or other package?). Convert to .xlsx.")
    return zf


QUOTED = re.compile(r'("(?:[^"]|"")*"|\'(?:[^\']|\'\')*\')')
REF = re.compile(r'(?<![A-Za-z0-9_.$])(\$?)([A-Za-z]{1,3})(\$?)([1-9][0-9]{0,6})(?![A-Za-z0-9_(])')
RANGE = re.compile(r'(\$?)([A-Za-z]{1,3})\$?(\d+):(\$?)([A-Za-z]{1,3})\$?(\d+)')
SHEETRANGE = re.compile(
    r"^(?:'((?:[^']|'')+)'!|([A-Za-z0-9_.]+)!)?"
    r"(\$?[A-Za-z]{1,3}\$?[0-9]+(?::\$?[A-Za-z]{1,3}\$?[0-9]+)?)$")


def shift_formula(text, drow, dcol):
    """Translate a shared-formula master's text to a follower cell by shifting the
    relative A1 references (respecting $ anchors). Skips quoted strings and quoted
    sheet names; guards against function names (LOG10) and out-of-range results.
    Best effort — the point is to make an inherited formula visible, not to be a
    full Excel parser."""
    def repl(m):
        cd, colL, rd, row = m.groups()
        c, r = col_to_num(colL.upper()), int(row)
        nc = c if cd else c + dcol
        nr = r if rd else r + drow
        if not (1 <= nc <= 16384 and 1 <= nr <= 1048576):
            return m.group(0)
        return f"{cd}{num_to_col(nc)}{rd}{nr}"
    parts = QUOTED.split(text)
    return "".join(p if i % 2 else REF.sub(repl, p) for i, p in enumerate(parts))


def is_summary_formula(text, own_col, own_row):
    """True when the formula aggregates a multi-row range in its OWN column that
    lies entirely above it — the signature of a bottom totals cell. A running
    total (=SUM($D$2:D5) in D5) includes its own row, so it does not match."""
    for m in RANGE.finditer(text):
        c1, c2 = col_to_num(m.group(2).upper()), col_to_num(m.group(5).upper())
        r1, r2 = sorted((int(m.group(3)), int(m.group(6))))
        if min(c1, c2) <= own_col <= max(c1, c2) and (r2 - r1) >= 2 and r2 < own_row:
            return True
    return False


def categorize(code):
    """Map a number-format code string to a coarse type. Quoted literals ("USD"),
    escape sequences (\\d), padding tokens (_x, *x) and [..] sections are stripped
    BEFORE the date/time letter checks, so literal text can't fake a date; the
    currency check runs on the raw code, where the symbol usually lives."""
    if code is None:
        return "general"
    c = code.split(";")[0].strip()  # first (positive) section
    if c in ("", "General") or c.lower() == "general":
        return "general"
    has_cur = "[$" in c or any(sym in c for sym in "$€£¥₩₪₹")
    s = re.sub(r'"[^"]*"', "", c)
    s = re.sub(r"\\.", "", s)
    s = re.sub(r"[_*].", "", s)
    s = re.sub(r"\[[^\]]*\]", "", s)
    if "@" in s:
        return "text"
    if "%" in s:
        return "percent"
    low = s.lower()
    has_date = ("y" in low) or ("d" in low) or ("mmm" in low)
    has_time = ("h" in low) or ("s" in low and ":" in low)
    if has_date and has_time:
        return "datetime"
    if has_date:
        return "date"
    if has_time:
        return "time"
    if has_cur:
        return "currency"
    return "number"


def read_styles(zf):
    """Return list indexed by cellXfs position -> number-format category."""
    if "xl/styles.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/styles.xml"))
    custom = dict(BUILTIN_FMT)
    for nf in root.iter(f"{NS}numFmt"):
        custom[int(nf.get("numFmtId"))] = nf.get("formatCode")
    out = []
    cellxfs = root.find(f"{NS}cellXfs")
    if cellxfs is not None:
        for xf in cellxfs.findall(f"{NS}xf"):
            fid = int(xf.get("numFmtId", "0"))
            out.append(categorize(custom.get(fid)))
    return out


def workbook_info(zf):
    """Sheets (name, part path, visibility state), defined names with scope, and
    the date1904 flag in one pass over workbook.xml."""
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {r.get("Id"): r.get("Target") for r in rels}
    sheets = []
    for el in wb.iter(f"{NS}sheet"):
        rid = next((v for k, v in el.attrib.items() if local(k) == "id"), None)
        tgt = rid_to_target.get(rid, "") or ""
        if tgt and not tgt.startswith("/"):
            tgt = "xl/" + tgt.lstrip("./")
        sheets.append({"name": el.get("name"), "part": tgt.lstrip("/"),
                       "state": el.get("state", "visible")})
    pr = wb.find(f"{NS}workbookPr")
    date1904 = pr is not None and pr.get("date1904") in ("1", "true")
    dn = []
    for d in wb.iter(f"{NS}definedName"):
        sid = d.get("localSheetId")
        scope = (sheets[int(sid)]["name"] if sid is not None and int(sid) < len(sheets)
                 else "workbook")
        dn.append({"name": d.get("name"), "ref": (d.text or "").strip(), "scope": scope})
    return sheets, dn, date1904


def shared_strings(zf):
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    sst = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{NS}t")) for si in sst]


def resolve_path(base_part, target):
    """Resolve a rels Target. Absolute (/xl/..) is from the zip root; relative is
    resolved against the referencing part's folder."""
    if target.startswith("/"):
        return target.lstrip("/")
    base_dir = base_part.rsplit("/", 1)[0]
    parts = (base_dir + "/" + target).split("/")
    stack = []
    for p in parts:
        if p == "..":
            stack and stack.pop()
        elif p not in (".", ""):
            stack.append(p)
    return "/".join(stack)


def sheet_extras(zf, part):
    """Comments, Excel tables, and charts linked to a sheet via its rels."""
    rels_path = part.rsplit("/", 1)[0] + "/_rels/" + part.rsplit("/", 1)[1] + ".rels"
    comments, tables, charts = [], [], []
    if rels_path not in zf.namelist():
        return comments, tables, charts
    rels = ET.fromstring(zf.read(rels_path))
    for r in rels:
        ttype, tgt = r.get("Type", ""), r.get("Target", "")
        path = resolve_path(part, tgt)
        if path not in zf.namelist():
            continue
        if ttype.endswith("/comments"):
            croot = ET.fromstring(zf.read(path))
            for cm in croot.iter(f"{NS}comment"):
                txt = "".join(t.text or "" for t in cm.iter(f"{NS}t"))
                comments.append({"cell": cm.get("ref"), "text": txt})
        elif ttype.endswith("/table"):
            troot = ET.fromstring(zf.read(path))
            cols, calc = [], {}
            for c in troot.iter(f"{NS}tableColumn"):
                nm = c.get("name")
                cols.append(nm)
                cf = c.find(f"{NS}calculatedColumnFormula")
                if cf is not None and cf.text:
                    calc[nm] = "=" + cf.text.strip()
            entry = {"name": troot.get("displayName") or troot.get("name"),
                     "range": troot.get("ref"), "columns": cols}
            if calc:
                entry["calculated_columns"] = calc
            tables.append(entry)
        elif ttype.endswith("/drawing"):
            charts.extend(drawing_charts(zf, path))
    return comments, tables, charts


def drawing_charts(zf, drawing_path):
    """A drawing references charts through its own rels; parse each chart."""
    out = []
    draw_rels = drawing_path.rsplit("/", 1)[0] + "/_rels/" + drawing_path.rsplit("/", 1)[1] + ".rels"
    if draw_rels not in zf.namelist():
        return out
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(zf.read(draw_rels))
            if r.get("Type", "").endswith("/chart")}
    for tgt in rels.values():
        path = resolve_path(drawing_path, tgt)
        if path in zf.namelist():
            out.append(parse_chart(zf, path))
    return out


def parse_chart(zf, chart_path):
    """Extract chart type(s), title, and series ranges. Uses local-name matching to
    sidestep the c:/a: namespace prefixes."""
    root = ET.fromstring(zf.read(chart_path))
    title, types, series = None, [], []
    for el in root.iter():
        tag = local(el.tag)
        if tag == "title" and title is None:
            t = "".join(x.text or "" for x in el.iter() if local(x.tag) == "t")
            title = t or None
        elif tag.endswith("Chart") and tag != "chart":
            types.append(tag[:-5])  # barChart -> bar
        elif tag == "ser":
            name = ref = cat = val = None
            for sub in el.iter():
                st = local(sub.tag)
                if st == "tx":
                    name = "".join(x.text or "" for x in sub.iter() if local(x.tag) == "v") or None
                    ref = next((x.text for x in sub.iter() if local(x.tag) == "f"), None)
                elif st == "cat" and cat is None:
                    cat = next((x.text for x in sub.iter() if local(x.tag) == "f"), None)
                elif st == "val" and val is None:
                    val = next((x.text for x in sub.iter() if local(x.tag) == "f"), None)
            series.append({"name": name, "name_ref": ref, "categories": cat, "values": val})
    return {"part": chart_path.rsplit("/", 1)[-1], "title": title,
            "types": sorted(set(types)), "series": series}


def pivot_details(zf):
    """Row/column/filter fields and value aggregations per pivot table, with field
    names resolved through the pivot cache definition. The grid only holds the
    pivot's RESULTS; this is where the grouping logic actually lives."""
    out = []
    for pn in sorted(n for n in zf.namelist()
                     if re.match(r"xl/pivotTables/pivotTable\d+\.xml$", n)):
        entry = {"part": pn}
        try:
            pr = ET.fromstring(zf.read(pn))
            entry["name"] = pr.get("name")
            loc = pr.find(f"{NS}location")
            entry["location"] = loc.get("ref") if loc is not None else None
            names = []
            rels_path = "xl/pivotTables/_rels/" + pn.rsplit("/", 1)[1] + ".rels"
            if rels_path in zf.namelist():
                for r in ET.fromstring(zf.read(rels_path)):
                    if r.get("Type", "").endswith("/pivotCacheDefinition"):
                        cd = resolve_path(pn, r.get("Target"))
                        if cd in zf.namelist():
                            croot = ET.fromstring(zf.read(cd))
                            names = [cf.get("name") for cf in croot.iter(f"{NS}cacheField")]

            def fname(i):
                if i == -2:
                    return "(values)"
                return names[i] if 0 <= i < len(names) else f"field_{i}"

            def axis(tagname):
                p = pr.find(f"{NS}{tagname}")
                return [fname(int(f.get("x"))) for f in p] if p is not None else []

            entry["rows"] = axis("rowFields")
            entry["cols"] = axis("colFields")
            page = pr.find(f"{NS}pageFields")
            entry["filters"] = ([fname(int(f.get("fld"))) for f in page]
                                if page is not None else [])
            entry["values"] = [{"field": fname(int(df.get("fld"))),
                                "function": df.get("subtotal", "sum"),
                                "name": df.get("name")}
                               for df in pr.iter(f"{NS}dataField")]
        except Exception as e:  # a malformed pivot part must not kill the report
            entry["parse_error"] = str(e)
        out.append(entry)
    return out


def external_links(zf):
    """Other workbooks this file's formulas read ([1]Sheet1!A1 style). Their logic
    and data live OUTSIDE this file."""
    out = []
    for n in sorted(x for x in zf.namelist()
                    if re.match(r"xl/externalLinks/externalLink\d+\.xml$", x)):
        target = None
        rels = "xl/externalLinks/_rels/" + n.rsplit("/", 1)[1] + ".rels"
        if rels in zf.namelist():
            for r in ET.fromstring(zf.read(rels)):
                if "externalLinkPath" in r.get("Type", ""):
                    target = r.get("Target")
        out.append({"part": n, "target": target})
    return out


class Distinct:
    """Distinct-value counter with flat memory: exact up to KMV_BUF hashes, then a
    K-minimum-values sketch (error ~ +/-6% at K=256). Also keeps the values
    themselves while there are few enough to list."""
    __slots__ = ("h", "vals", "pruned", "thr")

    def __init__(self):
        self.h, self.vals, self.pruned, self.thr = set(), set(), False, None

    def add(self, v):
        if len(self.vals) <= 12:
            self.vals.add(v)
        hv = hash(v) & MAXH
        if self.pruned:
            if hv <= self.thr:
                self.h.add(hv)
                if len(self.h) > 2 * KMV_K:
                    self._prune()
        else:
            self.h.add(hv)
            if len(self.h) > KMV_BUF:
                self._prune()
                self.pruned = True

    def _prune(self):
        self.h = set(heapq.nsmallest(KMV_K, self.h))
        self.thr = max(self.h)

    def result(self):
        """(count, exact) - exact while under the buffer, KMV estimate beyond."""
        if not self.pruned:
            return len(self.h), True
        self._prune()
        return int((KMV_K - 1) * ((MAXH + 1) / max(self.thr, 1))), False


def parse_sheet_range(ref):
    """'Rates'!$A$1:$A$4 / Rates!A1:A4 / $H$1:$H$12 -> (sheet_or_None, c1, c2, r1, r2)."""
    m = SHEETRANGE.match(ref.strip())
    if not m:
        return None
    sheet = m.group(1) or m.group(2)
    if m.group(1):
        sheet = sheet.replace("''", "'")
    rng = m.group(3).replace("$", "")
    a, b = (rng.split(":") + [rng])[:2] if ":" in rng else (rng, rng)
    ma, mb = A1.match(a.upper()), A1.match(b.upper())
    if not (ma and mb):
        return None
    c1, c2 = sorted((col_to_num(ma.group(1)), col_to_num(mb.group(1))))
    r1, r2 = sorted((int(ma.group(2)), int(mb.group(2))))
    return sheet, c1, c2, r1, r2


def read_range_values(zf, part, sst, c1, c2, r1, r2):
    """Stream one sheet and return the non-empty values inside a small box, in
    row-major order. Used to resolve range-fed validation dropdowns."""
    got = []
    cur_ref = cur_type = None
    parts = []
    with zf.open(part) as fh:
        for ev, el in ET.iterparse(fh, events=("start", "end")):
            tag = local(el.tag)
            if ev == "start" and tag == "c":
                cur_ref, cur_type, parts = el.get("r"), el.get("t"), []
            elif ev == "end" and tag in ("v", "t") and cur_ref:
                parts.append(el.text or "")
            elif ev == "end" and tag == "c":
                m = A1.match(cur_ref or "")
                if m and parts:
                    r, c = int(m.group(2)), col_to_num(m.group(1))
                    if r > r2:
                        return got
                    if r1 <= r <= r2 and c1 <= c <= c2:
                        raw = "".join(parts)
                        if cur_type == "s" and raw.isdigit() and int(raw) < len(sst):
                            raw = sst[int(raw)]
                        elif cur_type == "b":
                            raw = "true" if raw in ("1", "true") else "false"
                        if raw != "":
                            got.append(raw)
                el.clear()
            elif ev == "end" and tag == "row":
                el.clear()
    return got


def resolve_validation_options(zf, sheets, dnames, sst, current_sheet, formula1, cap=64):
    """Resolve a range-fed list validation to its option values (one level of
    defined-name indirection). Returns (options_or_None, note)."""
    ref = (formula1 or "").strip().lstrip("=")
    for _ in range(2):
        hit = next((d for d in dnames if d["name"] == ref
                    and d["scope"] in ("workbook", current_sheet)), None)
        if hit:
            ref = hit["ref"].lstrip("=")
        else:
            break
    pr = parse_sheet_range(ref)
    if not pr:
        return None, "unresolved (not a plain range reference)"
    sheet, c1, c2, r1, r2 = pr
    sheet = sheet or current_sheet
    part = next((s["part"] for s in sheets if s["name"] == sheet), None)
    if not part or part not in zf.namelist():
        return None, f"unresolved (sheet {sheet!r} not found)"
    size = (c2 - c1 + 1) * (r2 - r1 + 1)
    if size > cap:
        return None, (f"range {ref} spans {size} cells - too many for a choice field; "
                      "this is usually an entity list -> extract it as its own table "
                      "and use a reference field instead")
    vals = read_range_values(zf, part, sst, c1, c2, r1, r2)
    return vals, f"resolved from {ref}"


def detect_regions(populated):
    if not populated:
        return []
    parent = {c: c for c in populated}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (r, col) in populated:
        for dr in (-2, -1, 0, 1, 2):
            for dc in (-2, -1, 0, 1, 2):
                nb = (r + dr, col + dc)
                if nb != (r, col) and nb in parent:
                    parent[find((r, col))] = find(nb)
    groups = defaultdict(list)
    for c in populated:
        groups[find(c)].append(c)
    regions = []
    for g in groups.values():
        rows, cols = [r for r, _ in g], [c for _, c in g]
        regions.append({
            "range": f"{num_to_col(min(cols))}{min(rows)}:{num_to_col(max(cols))}{max(rows)}",
            "rows": [min(rows), max(rows)], "cols": [num_to_col(min(cols)), num_to_col(max(cols))],
            "cell_count": len(g)})
    regions.sort(key=lambda x: (x["rows"][0], col_to_num(x["cols"][0])))
    return regions


def scan_sheet(zf, part, sst, styles, max_cells, sample):
    populated, too_big = set(), False
    col_profile = defaultdict(lambda: {"first": None, "last": None, "count": 0})
    style_counts = defaultdict(Counter)     # col -> Counter(category)
    header_cat = {}                          # col -> category of its first (top) cell
    first_by_col, shared_masters, arrays = {}, {}, []
    pending_shared, datatables = {}, []      # follower cells inheriting a shared formula
    fcount, litcount = defaultdict(int), defaultdict(int)   # per column-number
    fcells = defaultdict(list)               # col -> [(row, formula_text)] while few
    first_kind, overrides = {}, defaultdict(list)  # topmost cell kind; literal overrides
    distinct = defaultdict(Distinct)         # entity/choice detection, flat memory
    boolcount, errcount = defaultdict(int), defaultdict(int)
    errex = defaultdict(list)
    date_serial = defaultdict(lambda: [0, 0])  # col -> [plausible, total] date-styled numerics
    hidden_cols, hidden_row_count, hidden_row_ex = set(), 0, []
    cfs, cf_total = [], 0
    merged, validations, n = [], [], 0
    head_cap = max(sample * 2 // 3, 1)
    values_head, values_tail = {}, deque(maxlen=max(sample - head_cap, 1))
    cur_ref = cur_type = cur_style = None
    cur_has_f, in_is, in_rph = False, False, False
    val_parts = []

    with zf.open(part) as fh:
        for ev, el in ET.iterparse(fh, events=("start", "end")):
            tag = local(el.tag)
            if ev == "start":
                if tag == "c":
                    cur_ref, cur_type, cur_style = el.get("r"), el.get("t"), el.get("s")
                    cur_has_f, val_parts = False, []
                elif tag == "is":
                    in_is = True
                elif tag == "rPh":
                    in_rph = True
                elif tag == "row" and el.get("hidden") in ("1", "true"):
                    hidden_row_count += 1
                    if len(hidden_row_ex) < 10 and el.get("r"):
                        hidden_row_ex.append(int(el.get("r")))
                continue

            # end events
            if tag == "f":
                cur_has_f = True
                ftext = (el.text or "").strip()
                m = A1.match(cur_ref or "")
                colL = m.group(1) if m else "?"
                if m:
                    cnum = col_to_num(colL)
                    fcount[cnum] += 1
                    first_kind.setdefault(cnum, "formula")
                    if ftext and fcount[cnum] <= 8:
                        fcells[cnum].append((int(m.group(2)), ftext))
                ft = el.get("t")
                if ft == "array":
                    arrays.append({"cell": cur_ref, "ref": el.get("ref"), "formula": "=" + ftext})
                elif ft == "shared":
                    if ftext:
                        shared_masters[el.get("si")] = {"cell": cur_ref, "formula": "=" + ftext}
                    elif colL not in first_by_col and colL not in pending_shared:
                        # follower cell: formula text lives at the master; resolve after the scan
                        pending_shared[colL] = {"si": el.get("si"), "cell": cur_ref}
                elif ft == "dataTable":
                    datatables.append({"cell": cur_ref, "ref": el.get("ref")})
                if ftext and colL not in first_by_col and colL not in pending_shared:
                    first_by_col[colL] = {"cell": cur_ref, "formula": "=" + ftext}
            elif tag == "v" and cur_ref:
                val_parts.append(el.text or "")
            elif tag == "t" and cur_ref and in_is and not in_rph:
                val_parts.append(el.text or "")
            elif tag == "is":
                in_is = False
            elif tag == "rPh":
                in_rph = False
            elif tag == "c":
                raw = "".join(val_parts)
                m = A1.match(cur_ref or "")
                if raw != "" and m:
                    r, c = int(m.group(2)), col_to_num(m.group(1))
                    n += 1
                    cp = col_profile[c]
                    first_cell_of_col = cp["count"] == 0
                    if not cur_has_f:
                        litcount[c] += 1
                        first_kind.setdefault(c, "literal")
                        if fcount[c] > 0 and len(overrides[c]) < 3:
                            overrides[c].append(cur_ref)  # literal typed inside a formula column
                    cp["first"] = r if cp["first"] is None else min(cp["first"], r)
                    cp["last"] = r if cp["last"] is None else max(cp["last"], r)
                    cp["count"] += 1
                    cat = styles[int(cur_style)] if (cur_style and int(cur_style) < len(styles)) else "general"
                    if cat != "general":
                        style_counts[c][cat] += 1
                    if c not in header_cat:
                        header_cat[c] = cat
                    if cat in ("date", "datetime") and cur_type in (None, "n"):
                        try:
                            fv = float(raw)
                            ds = date_serial[c]
                            ds[1] += 1
                            if SERIAL_LO <= fv <= SERIAL_HI:
                                ds[0] += 1
                        except ValueError:
                            pass
                    if not too_big:
                        populated.add((r, c))
                        if len(populated) > max_cells:
                            too_big = True
                            populated = set()
                    if cur_type == "e":
                        errcount[c] += 1
                        if len(errex[c]) < 3:
                            errex[c].append({"cell": cur_ref, "value": raw})
                    else:
                        if cur_type == "s" and raw.isdigit() and int(raw) < len(sst):
                            v = sst[int(raw)]
                        elif cur_type == "b":
                            v = "true" if raw in ("1", "true") else "false"
                            boolcount[c] += 1
                        else:
                            v = raw
                        if not (first_cell_of_col and not cur_has_f):
                            distinct[c].add(v)  # header (first literal cell) excluded
                        if len(values_head) < head_cap:
                            values_head[cur_ref] = v
                        else:
                            values_tail.append((cur_ref, v))
                el.clear()
            elif tag == "row":
                el.clear()
            elif tag == "col":
                if el.get("hidden") in ("1", "true"):
                    lo, hi = int(el.get("min")), int(el.get("max"))
                    for cc in range(lo, min(hi, lo + 49) + 1):
                        hidden_cols.add(num_to_col(cc))
            elif tag == "mergeCell":
                merged.append(el.get("ref"))
            elif tag == "dataValidation":
                f1 = el.find(f"{NS}formula1")
                opts = None
                if f1 is not None and f1.text and el.get("type") == "list":
                    s = f1.text.strip()
                    if s.startswith('"'):  # inline list: "Draft,Confirmed,..."
                        opts = s.strip('"').split(",")
                    # unquoted = a range or defined name; resolved after the scan
                validations.append({"sqref": el.get("sqref"), "type": el.get("type"),
                                    "formula1": (f1.text if f1 is not None else None),
                                    "options": opts})
            elif tag == "conditionalFormatting":
                for rule in el.findall(f"{NS}cfRule"):
                    cf_total += 1
                    if len(cfs) < 50:
                        cfs.append({"range": el.get("sqref"), "type": rule.get("type"),
                                    "operator": rule.get("operator"),
                                    "formulas": [f.text for f in rule.findall(f"{NS}formula") if f.text]})
                el.clear()

    # dominant inferred type per column (drop the header cell once)
    column_types = {}
    for c, counts in style_counts.items():
        cc = Counter(counts)
        if header_cat.get(c) in cc:
            cc[header_cat[c]] -= 1
        cc += Counter()  # drop zeros
        if cc:
            column_types[num_to_col(c)] = {"inferred": cc.most_common(1)[0][0], "counts": dict(cc)}
    # boolean cells are typed by the cell itself, not the number format
    for c, bc in boolcount.items():
        body = max(col_profile[c]["count"] - 1, 1)
        if bc >= body * 0.6:
            column_types[num_to_col(c)] = {"inferred": "boolean", "counts": {"boolean_cells": bc}}
    # sanity-check date columns against the plausible-serial window
    for colL, info in column_types.items():
        if info["inferred"] in ("date", "datetime"):
            plaus, tot = date_serial[col_to_num(colL)]
            if tot and plaus / tot < 0.5:
                info["serial_check"] = ("implausible - cells carry a date number format but "
                                        "the values do not look like Excel date serials; "
                                        "verify the real type before mapping it")

    # resolve shared-formula followers: a column whose first formula cell only
    # inherits a master anchored elsewhere still gets its (shifted) formula reported
    for colL, info in pending_shared.items():
        mrec = shared_masters.get(info["si"])
        mm = A1.match(mrec["cell"]) if mrec else None
        fm = A1.match(info["cell"] or "")
        if mrec and mm and fm:
            drow = int(fm.group(2)) - int(mm.group(2))
            dcol = col_to_num(fm.group(1)) - col_to_num(mm.group(1))
            first_by_col[colL] = {"cell": info["cell"],
                                  "formula": shift_formula(mrec["formula"], drow, dcol),
                                  "resolved_from_shared_master": mrec["cell"]}
        else:
            first_by_col[colL] = {"cell": info["cell"], "formula": None,
                                  "note": f"shared-formula follower; master si={info['si']} not found"}

    # bottom aggregates over their own column are totals cells, not row templates
    summary_cells, summary_by_col = [], defaultdict(list)
    for cnum, lst in fcells.items():
        if fcount[cnum] > 8:
            continue  # a formula-dense column is a template column, not a totals cell
        lastrow = col_profile[cnum]["last"] or 0
        for row, txt in lst:
            if row >= lastrow - 1 and is_summary_formula(txt, cnum, row):
                cell = f"{num_to_col(cnum)}{row}"
                summary_cells.append({"cell": cell, "formula": "=" + txt})
                summary_by_col[cnum].append((row, txt))
    for cnum, hits in summary_by_col.items():
        colL = num_to_col(cnum)
        fb = first_by_col.get(colL)
        if fb and any(f"{colL}{row}" == fb.get("cell") for row, _ in hits):
            alt = next(((r, t) for r, t in fcells[cnum]
                        if (r, t) not in hits), None)
            if alt:
                first_by_col[colL] = {"cell": f"{colL}{alt[0]}", "formula": "=" + alt[1],
                                      "note": (f"template taken from {colL}{alt[0]}; "
                                               f"{fb['cell']} is a bottom aggregate, reported "
                                               "under summary_cells")}
            else:
                fb["kind"] = "summary_over_own_column"
                fb["note"] = ("aggregate over this column - a totals/summary cell, NOT a "
                              "repeating row template; exclude its row from any data load")
    likely_summary_rows = sorted({int(A1.match(s["cell"]).group(2)) for s in summary_cells})

    # mixed columns, classified by direction - the two cases mean opposite things
    mixed = []
    for c in sorted(set(fcount) & set(litcount)):
        lits_beyond_header = litcount[c] - (1 if first_kind.get(c) == "literal" else 0)
        if fcount[c] > 0 and lits_beyond_header > 0:
            n_sum = len(summary_by_col.get(c, []))
            if n_sum and n_sum == fcount[c]:
                pattern = "summary_formula_in_literal_column"
                note = ("the only formula(s) here are bottom totals over a hand-entered "
                        "column - exclude those rows from the data load; there is no "
                        "override question to ask")
            elif n_sum == 0:
                pattern = "literal_overrides_in_formula_column"
                note = ("literal values typed over a formula - ask the user which wins "
                        "and why the overrides were made")
            else:
                pattern = "mixed_unclear"
                note = "both overrides and summary cells present - untangle cell by cell"
            mixed.append({"column": num_to_col(c), "pattern": pattern,
                          "formula_cells": fcount[c],
                          "literal_cells_beyond_header": lits_beyond_header,
                          "override_examples": overrides.get(c, []),
                          "summary_cells": [f"{num_to_col(c)}{r}" for r, _ in summary_by_col.get(c, [])],
                          "note": note})

    cardinality = {}
    for c, d in sorted(distinct.items()):
        count, exact = d.result()
        body = max(col_profile[c]["count"] - 1, 1)
        entry = {"distinct": count, "exact": exact,
                 "ratio_to_rows": round(min(count / body, 1.0), 3)}
        if len(d.vals) <= 12:
            entry["values"] = sorted(d.vals)
        cardinality[num_to_col(c)] = entry

    sample_values = dict(values_head)
    sample_values.update(dict(values_tail))

    comments, tables, charts = sheet_extras(zf, part)
    regions = None if too_big else detect_regions(populated)
    out = {
        "populated_cells": n,
        "region_detection": "column_profile_fallback" if regions is None else "full",
        "regions": regions,
        "column_profile": ({num_to_col(c): {"first_row": v["first"], "last_row": v["last"], "count": v["count"]}
                            for c, v in sorted(col_profile.items())} if regions is None else None),
        "column_types": dict(sorted(column_types.items(), key=lambda kv: col_to_num(kv[0]))),
        "first_formula_per_column": dict(sorted(first_by_col.items(), key=lambda kv: col_to_num(kv[0]))),
        "formula_vs_literal_per_column": {num_to_col(c): {"formula_cells": fcount.get(c, 0),
                                                          "literal_cells": litcount.get(c, 0)}
                                          for c in sorted(set(fcount) | set(litcount))},
        "mixed_columns": mixed,
        "summary_cells": summary_cells,
        "likely_summary_rows": ({"rows": likely_summary_rows,
                                 "note": "totals/summary rows - exclude from any data load"}
                                if likely_summary_rows else None),
        "column_cardinality": cardinality,
        "formula_errors": ({num_to_col(c): {"count": errcount[c], "examples": errex[c]}
                            for c in sorted(errcount)} or None),
        "data_table_formulas": datatables,
        "array_formulas": arrays,
        "shared_formula_masters": shared_masters,
        "data_validations": validations,
        "conditional_formatting": ({"rules": cfs, "total_rules": cf_total} if cf_total else None),
        "hidden_columns": sorted(hidden_cols, key=col_to_num) or None,
        "hidden_rows": ({"count": hidden_row_count, "examples": hidden_row_ex}
                        if hidden_row_count else None),
        "merged_cells": merged,
        "comments": comments,
        "excel_tables": tables,
        "charts": charts,
        "sample_values": dict(sorted(sample_values.items(),
                              key=lambda kv: (int(A1.match(kv[0]).group(2)), col_to_num(A1.match(kv[0]).group(1)))
                              if A1.match(kv[0]) else (0, 0))),
    }
    return {k: v for k, v in out.items() if v is not None or k in ("regions", "column_profile")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--max-cells", type=int, default=2_000_000)
    ap.add_argument("--sample", type=int, default=300)
    args = ap.parse_args()
    report = {"file": args.file, "sheets": {}}
    zf = open_workbook(args.file)
    with zf:
        names = set(zf.namelist())
        sheets, dnames, date1904 = workbook_info(zf)
        if date1904:
            report["date_system_1904"] = ("this workbook uses the 1904 (Mac) date system - "
                                          "serial 0 is 1904-01-01, not 1899-12-31; every "
                                          "serial-to-date conversion must use the 1904 epoch "
                                          "(extract_data.py handles it automatically)")
        if "xl/vbaProject.bin" in names:
            report["vba_macros"] = ("PRESENT — this workbook contains VBA; part of its logic lives in "
                                    "macros, not in cell formulas, and this inspector cannot read it. "
                                    "Extract it (e.g. oletools/olevba) or ask the user for the macro code "
                                    "before claiming the logic is fully understood.")
        pivots = pivot_details(zf)
        if pivots:
            report["pivot_tables"] = pivots
            report["pivot_tables_note"] = ("Pivot tables aggregate outside cell formulas; the cells only "
                                           "hold results. Recreate their grouping/aggregation as Ninox "
                                           "views or formula fields — the logic is in the pivot, not the grid.")
        ext = external_links(zf)
        if ext:
            report["external_links"] = ext
            report["external_links_note"] = ("formulas in this workbook read OTHER workbooks "
                                             "([n]Sheet!A1 references); part of the logic or data "
                                             "lives outside this file - get those files or the "
                                             "rules they supply before claiming the logic is "
                                             "understood")
        if dnames:
            report["defined_names"] = dnames
        sst = shared_strings(zf)
        styles = read_styles(zf)
        hidden_sheets = [s["name"] for s in sheets if s["state"] != "visible"]
        if hidden_sheets:
            report["hidden_sheets"] = {"names": hidden_sheets,
                                       "note": ("hidden sheets usually hold scratch/helper logic "
                                                "or lookup data the author didn't want touched - "
                                                "inspect them, never skip them")}
        for s in sheets:
            name, part = s["name"], s["part"]
            if args.sheet and name != args.sheet:
                continue
            if not part or part not in zf.namelist():
                continue
            res = scan_sheet(zf, part, sst, styles, args.max_cells, args.sample)
            if s["state"] != "visible":
                res["sheet_state"] = s["state"]
            for dv in res.get("data_validations", []):
                if dv["type"] == "list" and dv["options"] is None and dv["formula1"]:
                    opts, note = resolve_validation_options(zf, sheets, dnames, sst,
                                                            name, dv["formula1"])
                    dv["options"] = opts
                    dv["options_source"] = note
            report["sheets"][name] = res
    json.dump(report, sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
