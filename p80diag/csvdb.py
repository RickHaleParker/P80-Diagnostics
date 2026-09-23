# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Rick Hale Parker
"""Edit the code database as spreadsheets (CSV) instead of JSON.

Two CSV files:

  codes CSV    one row per fault code. Same file that "Export CSV..." writes.
  systems CSV  one row per entry in the System menu.

Import rules (see DATABASE.md):
  * Codes: every table named in the file gets exactly the codes in the file
    (rows you delete are removed). Tables not in the file are left alone.
    A table name that doesn't exist yet makes a new table.
  * Systems: the file replaces the whole System menu.
  * Nothing is changed if any row has an error; the report lists them.
"""
from __future__ import annotations

import copy
import csv
import io
import time

from .extract import CSV_HEADER as CODES_HEADER, to_csv_rows
from .kline import odd_parity

SYSTEMS_HEADER = ["Car", "System", "ECU address", "5-baud init byte", "Selected by key", "Table"]


class ImportError_(ValueError):
    """Raised with a list of problems; nothing was changed."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


# ------------------------------------------------------------------ export
def export_codes(db: dict, path: str) -> int:
    rows = list(to_csv_rows(db))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(CODES_HEADER)
        w.writerows(rows)
    return len(rows)


def export_systems(db: dict, path: str) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(SYSTEMS_HEADER)
        for e in db["menu"]:
            w.writerow([e["car"], e["title"], f"0x{e['address']:02X}", f"0x{e['init_byte']:02X}",
                        e.get("key") or "", e["table"]])
    return len(db["menu"])


def systems_path_for(codes_path: str) -> str:
    base = codes_path[:-4] if codes_path.lower().endswith(".csv") else codes_path
    return base + "_systems.csv"


# ------------------------------------------------------------------ reading
def _read(path: str) -> tuple[list[str], list[list[str]]]:
    """Read a CSV saved by LibreOffice or Excel (comma, semicolon or tab; with or without BOM)."""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first = text.splitlines()[0] if text.strip() else ""
    delim = max(",;\t", key=first.count)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        raise ImportError_([f"{path}: the file is empty"])
    header = [h.strip() for h in rows[0]]
    return header, rows[1:]


def kind_of(path: str) -> str:
    """'codes', 'systems' or raises."""
    header, _ = _read(path)
    if "Table" in header and "Wire byte (hex)" in header and "DTC" in header:
        return "codes"
    if "Car" in header and "System" in header and "ECU address" in header:
        return "systems"
    raise ImportError_([f"{path}: not a P80 Diagnostics codes or systems CSV (header: {', '.join(header[:6])}…)"])


def _byte(text: str, what: str, lo=0, hi=0xFF) -> int:
    t = text.strip()
    if not t:
        raise ValueError(f"{what} is empty")
    try:
        v = int(t, 16)          # always hex: "0x2F", "2F" and "2f" all work
    except ValueError:
        raise ValueError(f"{what} '{t}' is not a hex number like 0x2F") from None
    if not lo <= v <= hi:
        raise ValueError(f"{what} '{t}' is out of range (0x{lo:02X}–0x{hi:02X})")
    return v


def _selection(text: str):
    """'key MOT-4.4' -> ('MOT-4.4', []); 'addr 0x2E/0x2F' -> (None, [0x2E, 0x2F])."""
    t = text.strip()
    if t.lower().startswith("key "):
        return t[4:].strip(), []
    if t.lower().startswith("addr "):
        return None, [_byte(x, "Selected by address", 0, 0x7F) for x in t[5:].replace(",", "/").split("/") if x.strip()]
    raise ValueError(f"Selected by '{t}' must be 'key NAME' or 'addr 0xNN' (e.g. addr 0x2E/0x2F)")


# ------------------------------------------------------------------ import
def _norm(entry):
    e = list(entry)
    while len(e) > 2 and not e[-1]:
        e.pop()
    return e


def import_codes(db: dict, path: str) -> tuple[dict, dict]:
    header, rows = _read(path)
    col = {h: i for i, h in enumerate(header)}
    for need in ("Table", "Wire byte (hex)", "DTC"):
        if need not in col:
            raise ImportError_([f"{path}: missing column '{need}'"])
    orig_col = "Original text" if "Original text" in col else "Description" if "Description" in col else None

    def cell(r, name):
        i = col.get(name)
        return r[i].strip() if i is not None and i < len(r) else ""

    problems, by_table, sel_for = [], {}, {}
    for n, r in enumerate(rows, start=2):
        if not any(c.strip() for c in r):
            continue
        tid = cell(r, "Table")
        try:
            if not tid:
                raise ValueError("Table is empty")
            b = _byte(cell(r, "Wire byte (hex)"), "Wire byte")
            dtc = cell(r, "DTC")
            if not dtc:
                raise ValueError("DTC is empty")
            original = cell(r, orig_col) if orig_col else ""
            updated = cell(r, "Updated text")
            desc = cell(r, "Description") if orig_col != "Description" else ""
            if desc and desc != (updated or original):
                # someone edited the Description column: that's the text the app shows
                if updated:
                    updated = desc
                else:
                    original = desc
            if not original and not updated:
                raise ValueError("no text (fill in Original text or Updated text)")
            key = f"0x{b:02X}"
            codes = by_table.setdefault(tid, {})
            if key in codes:
                raise ValueError(f"wire byte {key} appears twice in table {tid}")
            codes[key] = [dtc, original, updated] if updated else [dtc, original]
            if cell(r, "Selected by"):
                sel_for.setdefault(tid, cell(r, "Selected by"))
        except ValueError as e:
            problems.append(f"row {n}: {e}")

    new = copy.deepcopy(db)
    rep = {"tables_changed": [], "tables_added": [], "added": 0, "changed": 0, "removed": 0}
    for tid, codes in by_table.items():
        old = new["tables"].get(tid)
        if old is None:
            try:
                if tid not in sel_for:
                    raise ValueError("new table needs 'Selected by' (e.g. addr 0x2E or key MOT-4.4)")
                skey, saddr = _selection(sel_for[tid])
            except ValueError as e:
                problems.append(f"table {tid}: {e}")
                continue
            new["tables"][tid] = {"id": tid, "prefix": tid.split(":")[0], "select_key": skey,
                                  "select_addr": saddr, "codes": codes}
            rep["tables_added"].append(tid)
            rep["added"] += len(codes)
            continue
        before = old["codes"]
        a = sum(1 for k in codes if k not in before)
        c = sum(1 for k in codes if k in before and _norm(before[k]) != _norm(codes[k]))
        d = sum(1 for k in before if k not in codes)
        if a or c or d:
            rep["tables_changed"].append(tid)
        rep["added"] += a
        rep["changed"] += c
        rep["removed"] += d
        old["codes"] = dict(sorted(codes.items(), key=lambda kv: int(kv[0], 16)))
    if problems:
        raise ImportError_(problems)
    new["edited"] = time.strftime("%Y-%m-%d %H:%M")
    return new, rep


def import_systems(db: dict, path: str) -> tuple[dict, dict]:
    header, rows = _read(path)
    col = {h: i for i, h in enumerate(header)}
    for need in ("Car", "System", "ECU address", "Table"):
        if need not in col:
            raise ImportError_([f"{path}: missing column '{need}'"])

    def cell(r, name):
        i = col.get(name)
        return r[i].strip() if i is not None and i < len(r) else ""

    problems, menu = [], []
    for n, r in enumerate(rows, start=2):
        if not any(c.strip() for c in r):
            continue
        try:
            car, title, table = cell(r, "Car"), cell(r, "System"), cell(r, "Table")
            if not car or not title:
                raise ValueError("Car and System must be filled in")
            addr = _byte(cell(r, "ECU address"), "ECU address", 0, 0x7F)
            init_txt = cell(r, "5-baud init byte")
            init = _byte(init_txt, "5-baud init byte") if init_txt else odd_parity(addr)
            if table not in db["tables"]:
                raise ValueError(f"table '{table}' doesn't exist (add its codes first)")
            menu.append({"car": car, "title": title, "address": addr, "init_byte": init,
                         "key": cell(r, "Selected by key") or None, "table": table})
        except ValueError as e:
            problems.append(f"row {n}: {e}")
    if not menu and not problems:
        problems.append(f"{path}: no systems in the file")
    if problems:
        raise ImportError_(problems)
    old = {(e["car"], e["title"]) for e in db["menu"]}
    now = {(e["car"], e["title"]) for e in menu}
    new = copy.deepcopy(db)
    new["menu"] = menu
    new["edited"] = time.strftime("%Y-%m-%d %H:%M")
    return new, {"systems": len(menu), "systems_added": len(now - old), "systems_removed": len(old - now)}


def import_files(db: dict, paths: list[str]) -> tuple[dict, list[str]]:
    """Import one or both CSVs (codes first). Returns (new db, report lines)."""
    kinds = {p: kind_of(p) for p in paths}
    lines = []
    for p in sorted(paths, key=lambda p: kinds[p] != "codes"):
        if kinds[p] == "codes":
            db, r = import_codes(db, p)
            lines.append(f"Codes: {r['added']} added, {r['changed']} changed, {r['removed']} removed"
                         f" in {len(r['tables_changed'])} table(s)"
                         + (f"; new table(s): {', '.join(r['tables_added'])}" if r["tables_added"] else ""))
        else:
            db, r = import_systems(db, p)
            lines.append(f"Systems: {r['systems']} in the menu ({r['systems_added']} added, "
                         f"{r['systems_removed']} removed)")
    return db, lines
