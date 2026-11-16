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


# ======================================================================
# IMMO3 TESTS  --  drive p80diag/immo.py through all three backends
# against the demo immobox in sim.py.  Run: python3 tests/test_sim.py
# ======================================================================
def test_immo3():
    """IMMO3 key programming on all three backends, against the demo immobox."""
    from p80diag import immo
    from p80diag.elm import ElmLink
    from p80diag.j2534 import J2534Link
    from p80diag.sim import SimElmSerial, FakeJ2534
    makers = (lambda e: KLine(SimSerial(e), log=None, bit_time=BT),
              lambda e: ElmLink(SimElmSerial(e), log=None),
              lambda e: J2534Link(FakeJ2534(e), log=None))
    for make in makers:
        # happy path: clear lockout, unlock, add, re-add, erase, counts, close
        ecu = SimECU(bit_time=BT, immo3=True, immo_pin=b"\x12\x34", immo_keys=2)
        k = make(ecu)
        k.connect(0x41)
        assert not immo.check_lockout(k, log=None)["locked"]
        assert immo.unlock(k, b"\x12\x34", log=None)["ok"]
        assert immo.key_count(k, log=None)["count"] == 2
        assert immo.teach_status(k, log=None)["status"] == 0x01   # unlocked
        r1 = immo.add_key(k, log=None)                            # new key -> F2 01
        assert r1["ok"] and not r1["already"]
        assert immo.key_count(k, log=None)["count"] == 3
        r2 = immo.add_key(k, log=None)                            # same key -> 7F B2 22
        assert r2["ok"] and r2["already"]
        assert immo.erase_and_learn(k, log=None)["ok"]
        assert immo.key_count(k, log=None)["count"] == 1
        immo.close(k, log=None)
        assert k.addr is None
        k.close()

        # a wrong PIN arms the lockout; it then refuses even the correct PIN
        ecu = SimECU(bit_time=BT, immo3=True, immo_pin=b"\x12\x34")
        k = make(ecu)
        k.connect(0x41)
        try:
            immo.unlock(k, b"\x99\x99", log=None)
            raise AssertionError("wrong PIN should raise")
        except KLineError as e:
            assert "A3 33" in str(e) or "lockout" in str(e).lower(), e
        assert immo.check_lockout(k, log=None)["locked"]
        try:
            immo.unlock(k, b"\x12\x34", log=None)                 # correct, but armed: not sent
            raise AssertionError("armed lockout should block unlock")
        except KLineError as e:
            assert "armed" in str(e).lower() or "lockout" in str(e).lower(), e
        k.close()

        # the 4-byte A3 variant is an invalid format -> 7F A3 12
        ecu = SimECU(bit_time=BT, immo3=True)
        k = make(ecu)
        k.connect(0x41)
        immo.open_session(k, log=None)
        assert k.request(b"\xA3\x02\x00\x12")[:3] == b"\x7F\xA3\x12"
        k.close()

        # add/erase before unlock is refused
        ecu = SimECU(bit_time=BT, immo3=True)
        k = make(ecu)
        k.connect(0x41)
        try:
            immo.add_key(k, log=None)
            raise AssertionError("add before unlock should raise")
        except KLineError:
            pass
        k.close()


def test_immo3_gating():
    from p80diag import immo
    assert immo.supports("C70") and immo.supports("S/V70") and immo.supports("S/V40")
    assert not immo.supports("850") and not immo.supports("900 & S/V90")
    assert immo.parse_pin("1234") == b"\x12\x34" and immo.parse_pin(" 0907 ") == b"\x09\x07"
    for bad in ("123", "12345", "12ab", ""):
        try:
            immo.parse_pin(bad)
            raise AssertionError(f"{bad!r} should be rejected")
        except ValueError:
            pass
    assert immo.unlock_request_hex(b"\x12\x34") == "A3 02 00 12 34"


def test_immo3_read_codes_still_work():
    """With the immobox on, the 0x41 module still reads/clears DTCs and the ECU ID."""
    ecu = SimECU({0x41: [0x02, 0x06]}, bit_time=BT, immo3=True)
    k = KLine(SimSerial(ecu), log=None, bit_time=BT)
    k.connect(0x41)
    assert k.read_dtcs() == [0x02, 0x06]
    k.clear_dtcs()
    assert k.read_dtcs() == []
    assert k.read_id()["hw_part"]
    k.close()
# ------------------------- end IMMO3 tests ----------------------------


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
    test_id_decode(); test_j2534_retry(); test_elm_long_reply(); test_elm_retries(); test_ci_busy_then_id(); test_csv_import()
    test_immo3(); test_immo3_gating(); test_immo3_read_codes_still_work(); test_service_reset()
    print("all tests passed")
