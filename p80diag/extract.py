# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Rick Hale Parker
"""Pull DTC tables and ECU addresses out of a legacy VB6 p-code fault-reader executable.

How it works
------------
The source program is Visual Basic 6 compiled to p-code. The DTC text lives in one big
procedure of the form ``frmCodeDisp``. Each table entry compiles to:

    LitStr <pool idx>   ; "IMM-112 Internal Fault"
    LitI4  <n>          ; n = raw DTC byte the ECU sends
    FLdRfVar <local>    ; the table (a local string array)
    Ary1LdRf / StFixedStr

After each table is filled, an ``If`` picks it. The test is either a string
compare against a module key (``"MOT-4.4"``) or a byte compare against the
ECU address (``= &H41``).

The per-car forms (frm850, frmSV70, ...) set two globals per menu entry:
the message address (index 0) and the 5-baud init byte with odd parity
(index 1), then a key string and a title.

Only the Python standard library is used.
"""
from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field, asdict


class PE:
    """Just enough PE parsing to map virtual addresses to file offsets."""

    def __init__(self, data: bytes):
        self.d = data
        if data[:2] != b"MZ":
            raise ValueError("Not an EXE file")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise ValueError("Not a PE file")
        nsec = struct.unpack_from("<H", data, pe + 6)[0]
        optsz = struct.unpack_from("<H", data, pe + 20)[0]
        opt = pe + 24
        self.base = struct.unpack_from("<I", data, opt + 28)[0]
        self.secs = []
        s = opt + optsz
        for i in range(nsec):
            va, vsz, rawp, rawsz = (struct.unpack_from("<I", data, s + i * 40 + k)[0]
                                    for k in (12, 8, 20, 16))
            self.secs.append((va, max(vsz, rawsz), rawp, rawsz))

    def off(self, va: int) -> int:
        rva = va - self.base
        for sva, size, rawp, rawsz in self.secs:
            if sva <= rva < sva + size and rva - sva < rawsz:
                return rawp + rva - sva
        raise ValueError(f"VA {va:#x} not in file")

    def u32(self, va, k=0):
        return struct.unpack_from("<I", self.d, self.off(va) + k)[0]

    def u16(self, va, k=0):
        return struct.unpack_from("<H", self.d, self.off(va) + k)[0]

    def cstr(self, va):
        x = self.off(va)
        return self.d[x:self.d.index(b"\0", x)].decode("latin1")

    def bstr(self, va):
        """VB string constant: UTF-16 text preceded by a 4-byte byte count."""
        try:
            x = self.off(va)
            n = struct.unpack_from("<I", self.d, x - 4)[0]
            if n > 4000 or n % 2:
                return None
            s = self.d[x:x + n].decode("utf-16le")
            return s if s.isprintable() else None
        except (ValueError, UnicodeDecodeError, struct.error):
            return None


@dataclass
class VBObject:
    name: str
    pool: list
    methods: list  # list of bytes (p-code) or None


def load_vb(pe: PE) -> dict[str, VBObject]:
    hdr = pe.d.find(b"VB5!")
    if hdr < 0:
        raise ValueError("Not a VB5/VB6 program")
    # file offset -> VA via the section that contains it
    hva = None
    for sva, size, rawp, rawsz in pe.secs:
        if rawp <= hdr < rawp + rawsz:
            hva = pe.base + sva + hdr - rawp
    proj = pe.u32(hva, 0x30)
    if pe.u32(proj, 0x20) != 0:
        raise ValueError("Program is native code, not p-code")
    ot = pe.u32(proj, 4)
    count = pe.u16(ot, 0x2A)
    arr = pe.u32(ot, 0x30)
    objs = {}
    for i in range(count):
        od = arr + i * 0x30
        oi = pe.u32(od, 0)
        name = pe.cstr(pe.u32(od, 0x18))
        nm, mt = pe.u16(oi, 0x20), pe.u32(oi, 0x24)
        nc, cp = pe.u16(oi, 0x28), pe.u32(oi, 0x34)
        pool = [pe.u32(cp, 4 * k) for k in range(nc)]
        methods = []
        for k in range(nm):
            try:
                pd = pe.u32(mt, 4 * k)
                size = pe.u16(pd, 8)
                x = pe.off(pd)
                methods.append(pe.d[x - size:x])
            except (ValueError, struct.error):
                methods.append(None)
        objs[name] = VBObject(name, pool, methods)
    return objs


# --- patterns -------------------------------------------------------------

LIT = rb"(?:\xf4(.)|\xf3(..)|\xf5(....))"          # LitI2_Byte / LitI2 / LitI4
RX_ENTRY = re.compile(rb"\x1b(..)" + LIT + rb"\x04(..)\x40\x47", re.S)
RX_KEYSEL = re.compile(rb"\x04(..)\x40\x33.\x00\x23\x00\xfd\x1b(..)\xfb\x30", re.S)
RX_KEYSEL2 = re.compile(rb"\x05\x62\x00\x33\x07\x00\x23\x00\xfd\x1b(..)\xfb\x30\x2f\x00\xfd\x1c(..)", re.S)
RX_ADDRSEL = re.compile(rb"\xf5\x00\x00\x00\x00\x05\x00\x00\xfc\x90\xfc\x14\xf4(.)\xc6"
                        rb"(?:\xf5\x00\x00\x00\x00\x05\x00\x00\xfc\x90\xfc\x14\xf4(.)\xc6\xc5)?\x1c", re.S)
RX_LOOPVAR = re.compile(rb"\xe7\x04(..)\x40\x33", re.S)
RX_MENU = re.compile(LIT + rb"\xfc\x0d\xf5\x00\x00\x00\x00\x05(..)\xfc\xa0" +
                     LIT + rb"\xfc\x0d\xf5\x00\x00\x00\x00\x05(..)\xfc\xa0"
                     rb"\x1b(..)\x05..\x47..\x1b(..)", re.S)


def odd_parity(addr: int) -> int:
    """Set bit 7 so the byte has an odd number of 1 bits."""
    addr &= 0x7F
    return addr | (0x80 if bin(addr).count("1") % 2 == 0 else 0)


def _lit(g1, g2, g3):
    if g1:
        return g1[0]
    if g2:
        return struct.unpack("<h", g2)[0]
    return struct.unpack("<i", g3)[0]


def _s16(b):
    return struct.unpack("<h", b)[0]


def _u16(b):
    return struct.unpack("<H", b)[0]


SYSTEM_NAMES = {
    "ABS": "ABS", "AT": "Auto Gearbox", "CCU": "Cab Control Unit", "CI": "Combined Instrument",
    "DSA": "Stability Control (DSA)", "ECC": "Climate Control", "EFI": "Engine", "GLA": "Guard Lock & Alarm",
    "HEA": "Additional Heater", "IMM": "Immobilizer", "KEY": "Keyless Entry", "PS": "Power Seat",
    "ROP": "Roll Over Protection", "RTI": "Road Traffic Info", "SRS": "SRS / Airbag",
}

CAR_FORMS = {"frm850": "850", "frmSV70": "S/V70", "frmC70": "C70", "frmSV90": "900 & S/V90", "frmSV40": "S/V40"}


@dataclass
class Table:
    id: str
    prefix: str
    select_key: str | None = None
    select_addr: list = field(default_factory=list)
    codes: dict = field(default_factory=dict)  # "0x01" -> [code, text]


@dataclass
class MenuEntry:
    car: str
    title: str
    address: int        # message target address
    init_byte: int      # 5-baud init byte (odd parity)
    key: str | None
    table: str | None = None


def extract(path: str) -> dict:
    pe = PE(open(path, "rb").read())
    objs = load_vb(pe)
    if "frmCodeDisp" not in objs:
        raise ValueError("frmCodeDisp not found - is this the right program?")
    cd = objs["frmCodeDisp"]
    P = cd.pool

    def pstr(i):
        return pe.bstr(P[i]) if i < len(P) else None

    # Largest method holds the tables.
    code = max((m for m in cd.methods if m), key=len)

    # 1. table entries, grouped by local array variable
    tables: dict[int, Table] = {}
    order = []
    for m in RX_ENTRY.finditer(code):
        text = pstr(_u16(m[1]))
        if not text:
            continue
        n = _lit(m[2], m[3], m[4])
        var = _s16(m[5])
        cm = re.match(r"([A-Z]{2,5})-(\S+)\s*(.*)", text)
        if not cm:
            continue
        if var not in tables:
            tables[var] = Table(id="", prefix=cm[1])
            order.append((m.start(), var))
        tables[var].codes[f"0x{n & 0xFF:02X}"] = [f"{cm[1]}-{cm[2]}", cm[3].strip()]

    # 2. selectors. Each table is followed by "If <cond> Then <display loop over table>".
    order.sort()
    bounds = [o for o, _ in order] + [len(code)]
    for i, (start, var) in enumerate(order):
        seg = code[start:bounds[i + 1]] if i + 1 < len(order) else code[start:]
        t = tables[var]
        k = RX_KEYSEL2.search(seg)
        a = RX_ADDRSEL.search(seg)
        if k and (not a or k.start() < a.start()):
            t.select_key = pstr(_u16(k[1]))
        elif a:
            t.select_addr = [a[1][0]] + ([a[2][0]] if a[2] else [])

    used = {}
    for var, t in tables.items():
        base = t.select_key or "_".join(f"{x:02X}" for x in t.select_addr) or str(var)
        t.id = f"{t.prefix}:{base}"
        used[t.id] = t

    # 3. menu entries from the car forms
    menu = []
    for fname, car in CAR_FORMS.items():
        ob = objs.get(fname)
        if not ob:
            continue
        seen = set()
        for mc in ob.methods:
            if not mc:
                continue
            for r in RX_MENU.finditer(mc):
                v1 = _lit(r[1], r[2], r[3])
                # The two values are the 7-bit message address and the same
                # address with odd parity in bit 7 (the 5-baud init byte).
                addr = v1 & 0x7F
                init = odd_parity(addr)
                key = pe.bstr(ob.pool[_u16(r[9])]) or None
                title = pe.bstr(ob.pool[_u16(r[10])]) or "?"
                if title in seen:
                    continue
                seen.add(title)
                menu.append(MenuEntry(car, title, addr, init, key))

    # 4. link menu entries to tables (key match first, else address)
    for e in menu:
        for t in used.values():
            if e.key and t.select_key == e.key:
                e.table = t.id
        if not e.table:
            for t in used.values():
                if not t.select_key and e.address in t.select_addr:
                    e.table = t.id

    return {
        "source": "legacy fault-reader EXE",
        "tables": {tid: asdict(t) for tid, t in used.items()},
        "menu": [asdict(e) for e in menu],
    }


def to_csv_rows(db: dict):
    users = {}
    for e in db["menu"]:
        users.setdefault(e["table"], []).append(e)
    for tid, t in db["tables"].items():
        es = users.get(tid, [])
        addrs = sorted({f"0x{e['address']:02X}" for e in es})
        inits = sorted({f"0x{e['init_byte']:02X}" for e in es})
        titles = " | ".join(e["title"] for e in es)
        sel = f"key {t['select_key']}" if t["select_key"] else "addr " + "/".join(f"0x{x:02X}" for x in t["select_addr"])
        for byte, entry in sorted(t["codes"].items(), key=lambda kv: int(kv[0], 16)):
            code, text = entry[0], entry[1]
            updated = entry[2] if len(entry) > 2 else ""
            yield [tid, sel, " / ".join(addrs), " / ".join(inits), titles, byte, int(byte, 16), code,
                   updated or text, text, updated]


CSV_HEADER = ["Table", "Selected by", "Message address", "5-baud init byte", "Menu entries",
              "Wire byte (hex)", "Wire byte (dec)", "DTC", "Description", "Original text", "Updated text"]


def main(argv=None):
    import argparse
    import csv
    ap = argparse.ArgumentParser(description="Extract DTC tables from a legacy fault-reader EXE")
    ap.add_argument("exe")
    ap.add_argument("-o", "--json", default="dtc_db.json")
    ap.add_argument("--csv")
    a = ap.parse_args(argv)
    db = extract(a.exe)
    json.dump(db, open(a.json, "w"), indent=1)
    n = sum(len(t["codes"]) for t in db["tables"].values())
    print(f"{len(db['tables'])} tables, {n} codes, {len(db['menu'])} menu entries -> {a.json}")
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.writer(f, quoting=csv.QUOTE_ALL)
            w.writerow(CSV_HEADER)
            w.writerows(to_csv_rows(db))


if __name__ == "__main__":
    main()
