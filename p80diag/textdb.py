# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Rick Hale Parker
"""Read fault-code texts straight out of a SQL Server diagnostic database file.

No SQL Server needed. This reads the raw data file (.mdf, or a .zip that
contains one) page by page.

Steps:
  1. Undo torn-page protection. On every page, the last byte of each
     512-byte sector (sectors 1..15) had its low 2 bits swapped out. The
     original bits sit in the page header (m_tornBits at offset 60,
     bits 2k..2k+1).
  2. Read the system catalog (sysallocunits, sysrowsets, sysschobjs,
     syscolpars, sysrscols) to find each table's pages and its real
     physical column layout.
  3. Copy the tables we need into SQLite.
  4. For each K-line module, walk ECU variant -> DTC blocks -> values:
        CompareValue       = wire byte the ECU sends
        altDisplayValue    = 3-digit code number
        fkT190_Text_Value  = English description (language 19, en-US)

Usage:
  python -m p80diag.textdb database.zip -o updated_texts.json
  python -m p80diag.textdb --merge updated_texts.json p80diag/dtc_db.json
"""
from __future__ import annotations

import collections
import json
import mmap
import os
import sqlite3
import struct
import tempfile
import zipfile

PAGE = 8192

# P80 Diagnostics table id -> source T101_Ecu ids (K-line variants only).
# Left out on purpose: CI:SV40-CI and AT:6E (the source's codes differ), and
# Motronic 4.4, 850 ABS, 850 CI, SRS (not in the source).
TABLE_TO_SOURCE = {
    "IMM:41": [136], "PS:2E_2F": [120, 121], "ECC:SV70ECC": [139], "ECC:SV40ECC": [221],
    "GLA:SV70GLA": [133], "KEY:SV40KLE": [218], "RTI:62": [134], "ROP:59": [140],
    "CCU:42": [141], "HEA:18": [135], "EFI:EMS2000": [238], "EFI:11": [131],
    "ABS:SV40ABS": [215], "DSA:SV40DSA": [230],
}
KLINE_BUS = 6
LANG_EN_US, LANG_EN_GB = 19, 4

FIX = {48: 1, 52: 2, 56: 4, 127: 8, 61: 8, 58: 4, 62: 8, 59: 4, 36: 16, 60: 8, 122: 4, 40: 3}
VAR = {167, 231, 165, 35, 99, 34, 241, 98}


# ---------------------------------------------------------------- MDF file
def repair_torn_pages(src_path: str, dst_path: str, progress=None) -> int:
    fixed = 0
    with open(src_path, "rb") as src, open(dst_path, "wb") as dst:
        while True:
            buf = bytearray(src.read(PAGE * 1024))
            if not buf:
                break
            for o in range(0, len(buf) - PAGE + 1, PAGE):
                flags = struct.unpack_from("<H", buf, o + 4)[0]
                if not flags & 0x100:
                    continue
                torn = struct.unpack_from("<I", buf, o + 60)[0]
                pat = torn & 3
                if all(buf[o + k * 512 + 511] & 3 == pat for k in range(1, 16)):
                    for k in range(1, 16):
                        b = o + k * 512 + 511
                        buf[b] = (buf[b] & 0xFC) | ((torn >> (2 * k)) & 3)
                    fixed += 1
            dst.write(buf)
            if progress:
                progress(f"repairing pages… {fixed}")
    return fixed


class MDF:
    def __init__(self, path: str):
        self.f = open(path, "rb")
        self.m = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        self.n = len(self.m) // PAGE
        self.pidx = collections.defaultdict(list)
        m = self.m
        for p in range(self.n):
            o = p * PAGE
            if m[o + 1] != 1:  # data pages only
                continue
            idx = struct.unpack_from("<H", m, o + 6)[0]
            obj = struct.unpack_from("<I", m, o + 24)[0]
            self.pidx[(idx << 48) | (obj << 16)].append(o)
        self._catalog()

    def records(self, o):
        m = self.m
        for s in range(struct.unpack_from("<H", m, o + 22)[0]):
            ro = struct.unpack_from("<H", m, o + PAGE - 2 - 2 * s)[0]
            if ro < 96 or ro >= PAGE:
                continue
            r = o + ro
            a = m[r]
            if (a >> 1) & 7:
                continue  # not a primary record
            fend = struct.unpack_from("<H", m, r + 2)[0]
            if fend < 5 or fend > 8060:
                continue
            fixed = m[r + 4:r + fend]
            ncol = struct.unpack_from("<H", m, r + fend)[0]
            p = r + fend + 2
            nb = (ncol + 7) // 8
            nulls = m[p:p + nb]
            p += nb
            var = []
            if a & 0x20:
                nv = struct.unpack_from("<H", m, p)[0]
                p += 2
                if nv > 200 or p + 2 * nv > o + PAGE:
                    continue
                start = p + 2 * nv - r
                for i in range(nv):
                    e = struct.unpack_from("<H", m, p + 2 * i)[0]
                    cplx, e = e & 0x8000, e & 0x7FFF
                    if e > 8060 or r + e > o + PAGE:
                        break
                    var.append((m[r + start:r + e], bool(cplx)))
                    start = e
            yield fixed, nulls, var

    def _catalog(self):
        au, rs = {}, {}
        for o in self.pidx[7 << 16]:
            for fx, nl, var in self.records(o):
                auid, typ, owner = struct.unpack_from("<QBQ", fx, 0)
                au[auid] = (typ, owner)
        for o in self.pidx[5 << 16]:
            for fx, nl, var in self.records(o):
                rid, _, maj, mi = struct.unpack_from("<QBii", fx, 0)
                rs[rid] = (maj, mi)
        self.rowset_au = {own: auid for auid, (t, own) in au.items() if t == 1}
        self.rs = rs
        by_obj = {}
        for rid, (maj, mi) in rs.items():
            by_obj.setdefault((maj, mi), rid)
        self.by_obj = by_obj

        def pages(objid, mi=1):
            rid = by_obj.get((objid, mi))
            return self.pidx.get(self.rowset_au.get(rid), []) if rid else []

        self.tables = {}
        for o in pages(34):
            for fx, nl, var in self.records(o):
                oid = struct.unpack_from("<i", fx, 0)[0]
                typ = fx[13:15].decode("latin1")
                if typ == "U " and var:
                    self.tables[var[0][0].decode("utf-16le", "replace")] = oid
        self.cols = {}
        for o in pages(41):
            for fx, nl, var in self.records(o):
                if len(fx) < 17:
                    continue
                oid, _, colid, xt, _, ln = struct.unpack_from("<ihiBih", fx, 0)
                name = var[0][0].decode("utf-16le", "replace") if var else ""
                self.cols.setdefault(oid, []).append((colid, name, xt, ln))
        self.layout = {}
        for o in pages(3):
            for fx, nl, var in self.records(o):
                if len(fx) < 50:
                    continue
                rsid, rscolid = struct.unpack_from("<qi", fx, 0)
                off, nullbit, bitpos = struct.unpack_from("<iih", fx, 40)
                off16 = struct.unpack("<h", struct.pack("<H", off & 0xFFFF))[0]
                self.layout.setdefault(rsid, {})[rscolid] = (off16, nullbit, bitpos)

    def rows(self, table: str):
        oid = self.tables[table]
        cols = sorted(self.cols[oid])
        for mi in (0, 1):
            rid = self.by_obj.get((oid, mi))
            if rid and self.rowset_au.get(rid) in self.pidx:
                break
        lay = self.layout[rid]
        for o in self.pidx[self.rowset_au[rid]]:
            for fx, nl, var in self.records(o):
                row = []
                for cid, _, xt, ln in cols:
                    off, nb, bp = lay.get(cid, (None, 0, 0))
                    if off is None:
                        row.append(None)
                        continue
                    if nb and nb - 1 < len(nl) * 8 and (nl[(nb - 1) // 8] >> ((nb - 1) % 8)) & 1:
                        row.append(None)
                        continue
                    if off < 0:
                        k = -off - 1
                        data, cplx = var[k] if k < len(var) else (b"", False)
                        if cplx:
                            row.append(None)
                        elif xt in (231, 99):
                            row.append(data.decode("utf-16le", "replace"))
                        elif xt in (167, 35):
                            row.append(data.decode("latin1"))
                        else:
                            row.append(data.hex())
                        continue
                    p = off - 4
                    if xt == 104:
                        row.append((fx[p] >> bp) & 1 if p < len(fx) else None)
                        continue
                    size = FIX.get(xt, ln)
                    b = fx[p:p + size]
                    if len(b) < size:
                        row.append(None)
                    elif xt in (48, 52, 56, 127):
                        row.append(int.from_bytes(b, "little", signed=(xt != 48)))
                    elif xt == 175:
                        row.append(b.decode("latin1").rstrip())
                    else:
                        row.append(b.hex())
                yield row
        return

    def columns(self, table):
        return [c[1] for c in sorted(self.cols[self.tables[table]])]


NEEDED = ["T100_EcuVariant", "T101_Ecu", "T120_Config_EcuVariant", "T121_Config",
          "T141_Block", "T144_BlockChild", "T150_BlockValue", "T191_TextData"]


def to_sqlite(mdf: MDF, path: str, progress=None):
    db = sqlite3.connect(path)
    for t in NEEDED:
        cols = mdf.columns(t)
        db.execute(f'DROP TABLE IF EXISTS "{t}"')
        db.execute(f'CREATE TABLE "{t}" (' + ",".join(f'"{c}"' for c in cols) + ")")
        q = f'INSERT INTO "{t}" VALUES (' + ",".join("?" * len(cols)) + ")"
        batch, n = [], 0
        for r in mdf.rows(t):
            batch.append(r)
            n += 1
            if len(batch) >= 50000:
                db.executemany(q, batch)
                batch = []
                if progress:
                    progress(f"{t}: {n} rows")
        if batch:
            db.executemany(q, batch)
        db.commit()
    for sql in ("CREATE INDEX i1 ON T144_BlockChild(fkT100_EcuVariant)",
                "CREATE INDEX i2 ON T141_Block(id)",
                "CREATE INDEX i3 ON T191_TextData(fkT190_Text, fkT193_Language)",
                "CREATE INDEX i4 ON T150_BlockValue(fkT141_Block)",
                "CREATE INDEX i5 ON T120_Config_EcuVariant(fkT100_EcuVariant)"):
        db.execute(sql)
    db.commit()
    return db


def dtc_texts(db) -> dict:
    def txt(t):
        if not t:
            return ""
        for lang in (LANG_EN_US, LANG_EN_GB):
            r = db.execute("SELECT data FROM T191_TextData WHERE fkT190_Text=? AND fkT193_Language=?",
                           (t, lang)).fetchone()
            if r and r[0]:
                return " ".join(r[0].split())
        return ""

    out = {}
    for tid, ecus in TABLE_TO_SOURCE.items():
        per = collections.defaultdict(list)
        for ecu in ecus:
            variants = db.execute(
                """SELECT v.id, v.identifier FROM T100_EcuVariant v
                   JOIN T120_Config_EcuVariant ce ON ce.fkT100_EcuVariant = v.id
                   JOIN T121_Config c ON c.id = ce.fkT121_Config
                   WHERE v.fkT101_Ecu = ? AND c.fkT123_Bus = ?""", (ecu, KLINE_BUS)).fetchall()
            for vid, vident in variants:
                blocks = db.execute(
                    """SELECT b.id, b.name FROM T144_BlockChild bc
                       JOIN T141_Block b ON b.id = bc.fkT141_Block_Child
                       WHERE bc.fkT100_EcuVariant = ? AND b.fkT142_BlockType = 10""", (vid,)).fetchall()
                for bid, name in blocks:
                    for cv, tv, alt in db.execute(
                            "SELECT CompareValue, fkT190_Text_Value, altDisplayValue FROM T150_BlockValue "
                            "WHERE fkT141_Block = ?", (bid,)):
                        try:
                            byte = int(cv, 16)
                        except (TypeError, ValueError):
                            continue
                        per[f"0x{byte:02X}"].append({"variant": (vident or "").strip(), "code": alt or "",
                                                     "text": txt(tv), "name": name or ""})
        out[tid] = dict(per)
    return out


def build(path: str, progress=print) -> dict:
    """path: a .zip containing the .mdf, or the .mdf itself. Returns the per-table texts."""
    with tempfile.TemporaryDirectory() as tmp:
        src = path
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                name = [n for n in z.namelist() if n.lower().endswith(".mdf")][0]
                progress(f"unzipping {name}…")
                z.extract(name, tmp)
                src = os.path.join(tmp, name)
        fixed = os.path.join(tmp, "fixed.mdf")
        repair_torn_pages(src, fixed)
        if src != path:
            os.remove(src)
        progress("reading catalog…")
        mdf = MDF(fixed)
        db = to_sqlite(mdf, os.path.join(tmp, "texts.db"), progress)
        progress("collecting fault-code texts…")
        res = dtc_texts(db)
        db.close()
        return res


# ---------------------------------------------------------------- merging
def _best(entries, want_code=None):
    """Pick the most common (code, text) among the source's ECU variants."""
    c = collections.Counter((e["code"], e["text"]) for e in entries if e["text"])
    if want_code:
        nums = [n.strip() for n in want_code.split("/")]
        agree = collections.Counter({k: v for k, v in c.items() if k[0] and any(n.startswith(k[0]) for n in nums)})
        if agree:
            c = agree
        elif any(k[0] for k in c):
            return None  # the source uses a different code number for this byte
    if not c:
        return None
    top = max(c.values())
    return max((k for k, v in c.items() if v == top), key=lambda k: (bool(k[0]), len(k[1])))


def merge(db: dict, texts: dict) -> dict:
    """Add updated texts to a code database. codes[byte] becomes
    [code, original_text, updated_text]. Bytes only the source knows are added with
    an empty legacy text."""
    stats = collections.Counter()
    for tid, per in texts.items():
        t = db["tables"].get(tid)
        if not t:
            continue
        prefix = tid.split(":")[0]
        for byte, entries in per.items():
            cur = t["codes"].get(byte)
            if cur:
                num = cur[0].split("-", 1)[1] if "-" in cur[0] else ""
                best = _best(entries, num)
                if best:
                    t["codes"][byte] = [cur[0], cur[1], best[1]]
                    stats["improved"] += 1
                else:
                    stats["kept"] += 1
            else:
                best = _best(entries)
                if best:
                    code = f"{prefix}-{best[0]}" if best[0] else f"{prefix}-?"
                    t["codes"][byte] = [code, "", best[1]]
                    stats["added"] += 1
        t["updated_texts"] = True
    db["updated_texts"] = {"tables": sorted(texts), **stats}
    return db


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Read DTC texts from a SQL Server diagnostic database file")
    ap.add_argument("src", nargs="?", help=".zip containing the .mdf, or the .mdf itself")
    ap.add_argument("-o", "--out", default="updated_texts.json")
    ap.add_argument("--merge", nargs=2, metavar=("TEXTS_JSON", "DTC_DB_JSON"),
                    help="merge updated texts into a code database (edits DTC_DB_JSON)")
    a = ap.parse_args(argv)
    if a.merge:
        texts = json.load(open(a.merge[0]))
        db = json.load(open(a.merge[1]))
        db = merge(db, texts)
        json.dump(db, open(a.merge[1], "w"), indent=1)
        print(db["updated_texts"])
        return
    res = build(a.src)
    json.dump(res, open(a.out, "w"), indent=1)
    print(f"{sum(len(v) for v in res.values())} wire bytes in {len(res)} tables -> {a.out}")


if __name__ == "__main__":
    main()
