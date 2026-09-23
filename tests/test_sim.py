# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Rick Hale Parker
"""Run: python -m pytest tests  (or python tests/test_sim.py)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from p80diag.kline import KLine, KLineError, build_message, odd_parity  # noqa: E402
from p80diag.sim import SimSerial, SimECU  # noqa: E402

BT = 0.02  # fast 5-baud for tests

import p80diag.j2534 as _j  # noqa: E402
_j.RETRY_PAUSE = 0          # no pause between wake-up retries in tests
import p80diag.elm as _e  # noqa: E402
_e.RETRY_PAUSE = 0


def test_frame():
    assert build_message(0x7A, b"\xA1") == bytes([0x82, 0x7A, 0x13, 0xA1, 0xB0])
    assert odd_parity(0x41) == 0xC1 and odd_parity(0x58) == 0x58


def test_read_clear():
    for addr in (0x01, 0x41, 0x58, 0x7A, 0x2E):
        s = SimSerial(SimECU({addr: [0x01, 0x34]}, bit_time=BT))
        k = KLine(s, log=None, bit_time=BT)
        k.connect(addr)
        assert k.keep_alive()
        assert k.read_dtcs() == [0x01, 0x34]
        k.clear_dtcs()
        assert k.read_dtcs() == []
        assert k.read_id()["hw_part"]
        k.close()


def test_bad_parity_is_ignored():
    k = KLine(SimSerial(SimECU(bit_time=BT)), log=None, bit_time=BT)
    try:
        k.connect(0x41, init_byte=0x41)
    except KLineError:
        return
    raise AssertionError("expected KLineError")


def test_text_merge():
    from p80diag.textdb import merge
    db = {"tables": {"IMM:41": {"codes": {"0x02": ["IMM-211/213", "old"], "0x06": ["IMM-223", "keep"]}}}}
    texts = {"IMM:41": {"0x02": [{"code": "213", "text": "new", "variant": "", "name": ""}],
                       "0x06": [{"code": "999", "text": "other", "variant": "", "name": ""}],
                       "0x09": [{"code": "300", "text": "extra", "variant": "", "name": ""}]}}
    c = merge(db, texts)["tables"]["IMM:41"]["codes"]
    assert c["0x02"] == ["IMM-211/213", "old", "new"]
    assert c["0x06"] == ["IMM-223", "keep"]          # source disagrees: keep ours
    assert c["0x09"] == ["IMM-300", "", "extra"]     # source-only code added


def test_elm_and_j2534():
    from p80diag.elm import ElmLink
    from p80diag.j2534 import J2534Link
    from p80diag.sim import SimElmSerial, FakeJ2534
    for make in (lambda e: ElmLink(SimElmSerial(e), log=None), lambda e: J2534Link(FakeJ2534(e), log=None)):
        k = make(SimECU({0x41: [0x01, 0x34]}))
        k.connect(0x41)
        assert k.read_dtcs() == [0x01, 0x34]
        k.clear_dtcs()
        assert k.read_dtcs() == []
        assert k.read_id()["hw_part"]
        k.close()
        k = make(SimECU())
        try:
            k.connect(0x41, init_byte=0x41)   # wrong parity: ECU stays silent
            raise AssertionError("expected KLineError")
        except KLineError:
            pass


def test_id_decode():
    from p80diag.kline import part_number, revision
    assert part_number(bytes.fromhex("09171636")) == "9171636"
    assert revision(b"001") == "001" and revision(b"B  ") == "B" and revision(b"  A") == "A"
    assert revision(b"\x00\x01\x02") == "00 01 02"


def test_j2534_retry():
    """A picky device (like the Mongoose) that needs a longer key-byte wait."""
    from p80diag.j2534 import J2534Link, _worked
    from p80diag.sim import FakeJ2534
    _worked.clear()
    api = FakeJ2534(SimECU({0x7A: [0x12]}), need_w1=1000)
    k = J2534Link(api, log=None)
    k.connect(0x7A)
    assert api.inits == 2 and k.read_dtcs() == [0x12]
    k.close()
    api.inits = 0
    k = J2534Link(api, log=None)
    k.connect(0x7A)             # remembers what worked: one try
    assert api.inits == 1
    k.close()


def test_elm_long_reply():
    from p80diag.elm import ElmLink
    from p80diag.sim import SimElmSerial
    k = ElmLink(SimElmSerial(SimECU(), split=8), log=None)
    k.connect(0x51)
    assert k.read_id()["hw_part"]      # 22-byte frame over 3 lines
    k.close()
    k = ElmLink(SimElmSerial(SimECU(), cut=10), log=None)
    k.connect(0x51)
    try:
        k.read_id()
        raise AssertionError("expected KLineError")
    except KLineError as e:
        assert "cut the reply short" in str(e)


def test_elm_retries():
    from p80diag.elm import ElmLink
    from p80diag.sim import SimElmSerial
    for kw in ({"fail_inits": 1}, {"silent_first": 1}, {"silent_first": 2}):
        k = ElmLink(SimElmSerial(SimECU({0x7A: [0x12]}), **kw), log=None)
        k.connect(0x7A)
        assert k.read_dtcs() == [0x12], kw
        k.close()


def test_ci_busy_then_id():
    """C70 instrument: B9 F0 gets 7E B9 23 (busy) first, then the ID."""
    from p80diag.elm import ElmLink
    from p80diag.j2534 import J2534Link
    from p80diag.sim import SimElmSerial, FakeJ2534
    makers = (lambda e: KLine(SimSerial(e), log=None, bit_time=BT),
              lambda e: ElmLink(SimElmSerial(e), log=None),
              lambda e: J2534Link(FakeJ2534(e), log=None))
    for make in makers:
        k = make(SimECU(bit_time=BT))
        k.connect(0x51)
        assert k.read_id()["hw_part"] == "9475123"
        k.close()


def test_csv_import():
    import csv as _csv
    import json as _json
    import tempfile
    from p80diag import csvdb
    db = _json.load(open(os.path.join(os.path.dirname(__file__), "..", "p80diag", "dtc_db.json")))
    d = tempfile.mkdtemp()
    codes, systems = os.path.join(d, "c.csv"), os.path.join(d, "c_systems.csv")
    csvdb.export_codes(db, codes)
    csvdb.export_systems(db, systems)
    new, _ = csvdb.import_files(db, [codes, systems])          # unchanged round trip
    assert new["tables"] == db["tables"] and new["menu"] == db["menu"]

    rows = list(_csv.reader(open(codes, encoding="utf-8")))
    h = rows[0]
    T, B, DTC, DESC, ORIG, UPD = (h.index(x) for x in ("Table", "Wire byte (hex)", "DTC", "Description",
                                                      "Original text", "Updated text"))
    imm = [r for r in rows[1:] if r[T] == "IMM:41"]
    imm[0][DESC] = "My better text"                              # edit the shown text
    rows.remove(imm[1])                                          # delete a code
    rows.append(["IMM:41", "", "", "", "", "0xAB", "", "IMM-999", "", "New code", ""])       # add a code
    rows.append(["NEW:01", "addr 0x01", "", "", "", "2f", "", "NEW-111", "", "Hello", ""])  # new table
    with open(codes, "w", newline="", encoding="utf-8") as f:
        _csv.writer(f, delimiter=";").writerows(rows)           # semicolons, like some Excel setups
    srows = list(_csv.reader(open(systems, encoding="utf-8")))
    srows.append(["C70", "C 70 Test Module", "0x01", "", "", "NEW:01"])
    with open(systems, "w", newline="", encoding="utf-8") as f:
        _csv.writer(f).writerows(srows)
    new, lines = csvdb.import_files(db, [systems, codes])
    t = new["tables"]["IMM:41"]["codes"]
    b0 = imm[0][B]
    assert "My better text" in t[b0] and imm[1][B] not in t and t["0xAB"] == ["IMM-999", "New code"]
    assert new["tables"]["NEW:01"]["codes"]["0x2F"] == ["NEW-111", "Hello"]
    m = [e for e in new["menu"] if e["title"] == "C 70 Test Module"][0]
    assert m["init_byte"] == 0x01 and m["table"] == "NEW:01"   # 0x01 already has odd parity
    assert "1 added, 1 changed, 1 removed" in lines[0] or "2 added" in lines[0], lines

    rows.append(["IMM:41", "", "", "", "", "0xZZ", "", "IMM-1", "", "x", ""])                # bad byte
    with open(codes, "w", newline="", encoding="utf-8") as f:
        _csv.writer(f).writerows(rows)
    try:
        csvdb.import_files(db, [codes])
        raise AssertionError("expected an import error")
    except csvdb.ImportError_ as e:
        assert any("0xZZ" in p for p in e.problems), e.problems


def test_service_reset():
    from p80diag import service
    for car, ci in (("C70", "newer"), ("S/V70", "newer"), ("850", "850"), ("C70", "850"), ("S/V40", "newer")):
        ecu = SimECU(bit_time=BT, ci_type=ci)
        k = KLine(SimSerial(ecu), log=None, bit_time=BT)
        k.connect(0x51)
        r = service.reset(k, car, log=lambda *_: None)
        assert ecu.service_reset and k.addr is None and not r["refused"], (car, ci, r)
        k.close()
    assert not service.supported("900 & S/V90")


if __name__ == "__main__":
    test_frame(); test_read_clear(); test_bad_parity_is_ignored(); test_text_merge(); test_elm_and_j2534()
    test_id_decode(); test_j2534_retry(); test_elm_long_reply(); test_elm_retries(); test_ci_busy_then_id(); test_csv_import(); test_service_reset()
    print("all tests passed")
