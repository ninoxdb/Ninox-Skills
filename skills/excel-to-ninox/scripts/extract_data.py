#!/usr/bin/env python3
"""
extract_data.py - stream a sheet's cells out of an .xlsx/.xlsm into a typed,
load-ready CSV. Pure standard library; same streaming approach as inspect_xlsx.py,
so it handles giant files with flat memory.

This is the bridge the port needs three times:
  - Phase 1: pull the raw inputs to recompute the workbook's headline numbers.
  - Phase 5: produce the CSV that Ninox's import actually loads.
  - Phase 7: read expected values for the designated test rows.

Type fidelity is the whole point - this is exactly where snapshot bugs sneak in:
  - date / datetime / time cells: Excel stores serial numbers; they come out as
    ISO 8601 (2024-03-12 / 2024-03-12T14:30:00 / 14:30:00), honouring the
    workbook's 1904 (Mac) date system automatically. (Serials below 61 in the
    1900 system sit around Excel's phantom 1900-02-29 and can be off by a day -
    flagged in the summary if seen.)
  - boolean cells -> true / false (never 1/0 - Ninox rejects those).
  - percent cells stay the stored fraction (19% -> 0.19); the summary names the
    percent columns so you and the user decide which form the app holds.
  - error cells (#DIV/0!, #N/A ...) -> empty, counted in the summary.
  - numbers keep Excel's canonical dot decimal - import with numberFormat=us.
  - shared and inline strings are resolved; empty cells stay empty (null, not 0).

Formula cells export their CACHED value, and every formula-bearing column is
called out in the summary: a derived column should be computed by a function
field in Ninox, not imported - only export it for verification targets or for
deliberately frozen state the build guide designates.

Usage:
  python extract_data.py FILE.xlsx --sheet Ledger --out ledger.csv
  python extract_data.py FILE.xlsx --sheet Ledger --cols A,C:E,I \\
      --range A1:I401 --exclude-rows 402 --out inputs.csv

  --range A1:I401     row/column window (default: everything on the sheet)
  --cols A,C:E,I      keep only these columns (default: all in the window)
  --header-row N      row whose values become the CSV header
                      (default: the first row of the window; --no-header for none)
  --exclude-rows      rows to skip, e.g. 402,500-510 - totals/summary rows
                      (inspect_xlsx.py lists them under likely_summary_rows)
  --out FILE.csv      output path (default: <workbook>_<sheet>.csv)
"""
import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

from inspect_xlsx import (A1, NS, col_to_num, local, num_to_col, open_workbook,
                          read_styles, shared_strings, workbook_info)


def serial_to_dt(serial, date1904):
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    return base + timedelta(days=serial)


def parse_cols(spec):
    """'A,C:E,I' -> sorted list of column numbers."""
    out = set()
    for part in spec.split(","):
        part = part.strip().upper()
        if not part:
            continue
        if ":" in part:
            a, b = part.split(":")
            for c in range(col_to_num(a), col_to_num(b) + 1):
                out.add(c)
        else:
            out.add(col_to_num(part))
    return sorted(out)


def parse_rows(spec):
    """'402,500-510' -> set of row numbers."""
    out = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def convert(raw, ctype, cat, has_f, date1904, col, stats):
    """One cell -> one CSV string, type-faithfully."""
    if has_f:
        stats["formula_cols"][col] += 1
    if ctype == "e":
        stats["error_cols"][col] += 1
        return ""
    if ctype == "b":
        stats["types"][col]["boolean"] += 1
        return "true" if raw in ("1", "true") else "false"
    if ctype in ("s", "inlineStr", "str"):
        stats["types"][col]["text"] += 1
        return raw
    # numeric-ish cell: the number format decides the meaning
    if cat in ("date", "datetime", "time"):
        try:
            fv = float(raw)
        except ValueError:
            stats["types"][col]["text"] += 1
            return raw
        if not date1904 and 0 <= fv < 61:
            stats["early_serials"] += 1
        dt = serial_to_dt(fv, date1904)
        secs = round((dt - dt.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds())
        if secs >= 86400:  # rounding pushed us over midnight
            dt += timedelta(days=1)
            secs = 0
        dt = dt.replace(hour=secs // 3600, minute=secs % 3600 // 60,
                        second=secs % 60, microsecond=0)
        stats["types"][col][cat] += 1
        if cat == "date":
            return dt.date().isoformat()
        if cat == "time":
            return dt.strftime("%H:%M:%S")
        return dt.isoformat(timespec="seconds")
    if cat == "percent":
        stats["types"][col]["percent"] += 1
        stats["percent_cols"].add(col)
        return raw  # the stored fraction: 19% -> 0.19
    stats["types"][col]["number" if cat in ("number", "currency", "general") else cat] += 1
    return raw


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--range", dest="range_", default=None, metavar="A1:H401")
    ap.add_argument("--cols", default=None, metavar="A,C:E,I")
    ap.add_argument("--header-row", type=int, default=None)
    ap.add_argument("--no-header", action="store_true")
    ap.add_argument("--exclude-rows", default=None, metavar="402,500-510")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    zf = open_workbook(args.file)
    sheets, _dn, date1904 = workbook_info(zf)
    if args.sheet:
        sheet = next((s for s in sheets if s["name"] == args.sheet), None)
        if not sheet:
            sys.exit(f"error: sheet {args.sheet!r} not found; sheets are: "
                     + ", ".join(s["name"] for s in sheets))
    elif len(sheets) == 1:
        sheet = sheets[0]
    else:
        sys.exit("error: workbook has several sheets; pick one with --sheet NAME: "
                 + ", ".join(s["name"] for s in sheets))

    r_lo, r_hi, c_lo, c_hi = 1, 1_048_576, 1, 16_384
    auto_bounds = not args.range_ and not args.cols  # bound via <dimension> / header
    dim_seen = False
    if args.range_:
        a, b = (args.range_.upper().replace("$", "").split(":") + [None])[:2]
        ma, mb = A1.match(a), A1.match(b or a)
        if not (ma and mb):
            sys.exit(f"error: --range {args.range_!r} is not an A1:B2 range")
        c_lo, c_hi = sorted((col_to_num(ma.group(1)), col_to_num(mb.group(1))))
        r_lo, r_hi = sorted((int(ma.group(2)), int(mb.group(2))))
    sel_cols = parse_cols(args.cols) if args.cols else None
    if sel_cols:
        sel_cols = [c for c in sel_cols if c_lo <= c <= c_hi] or sel_cols
    excluded = parse_rows(args.exclude_rows)
    header_row = None if args.no_header else (args.header_row or r_lo)

    out_path = args.out or (args.file.rsplit(".", 1)[0] + "_" + sheet["name"] + ".csv")
    sst = shared_strings(zf)
    styles = read_styles(zf)
    stats = {"types": defaultdict(Counter), "formula_cols": Counter(),
             "error_cols": Counter(), "percent_cols": set(), "early_serials": 0}
    header_vals, rows_written, skipped = {}, 0, 0
    cur_row_vals, cur_row_num = {}, None
    cur_ref = cur_type = cur_style = None
    cur_has_f, in_is, in_rph, val_parts = False, False, False, []

    def effective_cols():
        return sel_cols or list(range(c_lo, c_hi + 1))

    def flush_row(writer, rnum, vals):
        nonlocal rows_written, skipped
        if rnum is None or not (r_lo <= rnum <= r_hi):
            return
        if header_row is not None and rnum == header_row:
            header_vals.update(vals)
            return
        if rnum in excluded:
            skipped += 1
            return
        row = [vals.get(c, "") for c in effective_cols()]
        if any(x != "" for x in row):
            writer.writerow(row)
            rows_written += 1

    with zf, open(out_path, "w", newline="", encoding="utf-8") as fo:
        writer = csv.writer(fo)
        pending = []  # buffer rows until the header row has been seen
        wrote_header = args.no_header

        def emit(rnum, vals):
            nonlocal wrote_header, pending, c_hi
            if not wrote_header:
                if header_row is not None and rnum == header_row:
                    header_vals.update(vals)
                if header_vals or (header_row is not None and rnum >= header_row) \
                        or (header_row is None and args.no_header):
                    if auto_bounds and not dim_seen:
                        seen = (set(header_vals) | set(vals)
                                | {c for _, pv in pending for c in pv})
                        c_hi = max(seen) if seen else 1
                        print("warning: sheet has no dimension record - column window "
                              f"clamped to A:{num_to_col(c_hi)} (the header row's extent); "
                              "pass --range or --cols if later rows are wider", file=sys.stderr)
                    writer.writerow([header_vals.get(c, num_to_col(c))
                                     for c in effective_cols()])
                    wrote_header = True
                    for pr, pv in pending:
                        flush_row(writer, pr, pv)
                    pending = []
                    if header_row is not None and rnum == header_row:
                        return
                else:
                    pending.append((rnum, vals))
                    return
            flush_row(writer, rnum, vals)

        hdr_stats = {"types": defaultdict(Counter), "formula_cols": Counter(),
                     "error_cols": Counter(), "percent_cols": set(), "early_serials": 0}
        with zf.open(sheet["part"]) as fh:
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
                    elif tag == "row":
                        cur_row_num = int(el.get("r")) if el.get("r") else cur_row_num
                        cur_row_vals = {}
                    continue
                if tag == "dimension" and auto_bounds:
                    ref = (el.get("ref") or "").upper().replace("$", "")
                    if ":" in ref:
                        a, b = ref.split(":")
                        ma, mb = A1.match(a), A1.match(b)
                        if ma and mb:
                            c_lo, c_hi = sorted((col_to_num(ma.group(1)),
                                                 col_to_num(mb.group(1))))
                            dim_seen = True
                elif tag == "f":
                    cur_has_f = True
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
                    if m:
                        r, c = int(m.group(2)), col_to_num(m.group(1))
                        in_win = (r_lo <= r <= r_hi and c_lo <= c <= c_hi and
                                  (not sel_cols or c in sel_cols
                                   or (header_row and r == header_row)))
                        if in_win:
                            tgt = (hdr_stats if (header_row is not None and r == header_row)
                                   or r in excluded else stats)
                            if raw != "":
                                if cur_type == "s" and raw.isdigit() and int(raw) < len(sst):
                                    raw = sst[int(raw)]
                                    cur_type = "str"
                                cat = (styles[int(cur_style)]
                                       if cur_style and int(cur_style) < len(styles) else "general")
                                cur_row_vals[c] = convert(raw, cur_type, cat, cur_has_f,
                                                          date1904, num_to_col(c), tgt)
                            elif cur_has_f:
                                # formula with no cached value (file saved without
                                # recalculation) - still flag the derived column
                                tgt["formula_cols"][num_to_col(c)] += 1
                    el.clear()
                elif tag == "row":
                    emit(cur_row_num, cur_row_vals)
                    if cur_row_num is not None and cur_row_num > r_hi:
                        break
                    el.clear()
        if not wrote_header:  # header row never seen (empty window)
            cols = sel_cols or list(range(c_lo, c_hi + 1))
            writer.writerow([header_vals.get(c, num_to_col(c)) for c in cols])

    # ---- summary (stderr) ----
    e = sys.stderr
    print(f"wrote {rows_written} data rows -> {out_path}"
          f" ({'1904' if date1904 else '1900'} date system"
          f"{'' if not excluded else f'; {skipped} excluded rows skipped'})", file=e)
    cols = sel_cols or sorted({col_to_num(c) for c in stats["types"]}
                              | {col_to_num(c) for c in stats["formula_cols"]}
                              | {col_to_num(c) for c in stats["error_cols"]})
    for c in cols:
        colL = num_to_col(c)
        t = stats["types"].get(colL, Counter())
        name = header_vals.get(c, colL)
        parts = [f"{colL} ({name}): " + (", ".join(f"{k}×{v}" for k, v in t.most_common()) or "empty")]
        if stats["formula_cols"].get(colL):
            parts.append(f"  ⚠ {stats['formula_cols'][colL]} formula cells - derived? a "
                         "function field should compute this in Ninox; don't import it "
                         "unless it is designated frozen state or a verification target. "
                         "Empty output here means the file carries no cached results - "
                         "recompute from the inputs instead")
        if stats["error_cols"].get(colL):
            parts.append(f"  ⚠ {stats['error_cols'][colL]} error cells (#DIV/0!, #N/A ...) "
                         "exported as empty - the source is broken or the misses are "
                         "expected; ask the user")
        print("\n".join(parts), file=e)
    if stats["percent_cols"]:
        print("note: percent column(s) " + ", ".join(sorted(stats["percent_cols"]))
              + " hold the stored fraction (19% -> 0.19) - decide with the user which "
                "form the Ninox field holds and verify it in Phase 7", file=e)
    if stats["early_serials"]:
        print(f"note: {stats['early_serials']} date serial(s) below 61 - around Excel's "
              "phantom 1900-02-29 dates can be off by one day; verify those cells", file=e)
    print("note: numbers use '.' decimals - use numberFormat=us on the Ninox CSV import",
          file=e)


if __name__ == "__main__":
    main()
