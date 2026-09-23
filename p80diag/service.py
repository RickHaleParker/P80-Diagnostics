# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Rick Hale Parker
"""Service light reset, through the combined instrument (address 0x51).

Sequences decoded from the legacy fault reader (one per car form). Each step
is a request and the positive reply's first byte (request service + 0x40).

850, S/V70, C70: the first step (B9 13) tells the two instrument types
apart. The 850-type instrument refuses it (7F B9 12) and is reset with B0 30.
The newer type answers F9 and gets the longer sequence. The legacy program
made you pick the other car if it guessed wrong; here it is automatic.

S/V40 has its own instrument and sequence. 900 and S/V90: no reset.

Every sequence ends with A0 (end of session), so the link is closed after.
"""
from __future__ import annotations

from .kline import KLineError, hexs

CI_ADDR = 0x51

NEWER = [bytes.fromhex(x) for x in ("AF04", "B8140A0F", "B230")]
OLDER_850 = [bytes.fromhex("B030")]
SV40 = [bytes.fromhex(x) for x in ("B906", "B908", "B907", "B224", "B80600", "B8080100", "B8070100")]


def supported(car: str) -> bool:
    return car in ("850", "S/V70", "C70", "S/V40")


def _step(link, data: bytes, log, n: int, refused: list):
    r = link.request(data)
    if r[:1] == bytes([data[0] + 0x40]):
        log(f"Step {n} ({hexs(data)}): OK")
        return r
    if r[:2] == bytes([0x7F, data[0]]):
        # the legacy program carries on after a refusal; so do we, but say so
        log(f"Step {n} ({hexs(data)}): refused by the instrument (code {r[2]:02X})" if len(r) > 2
            else f"Step {n} ({hexs(data)}): refused by the instrument")
        refused.append(n)
        return r
    raise KLineError(f"Service reset step {n} ({hexs(data)}): unexpected reply {hexs(r)}")


def reset(link, car: str, log=print) -> dict:
    """Run the reset on a link already connected to 0x51.
    Returns {"type": ..., "steps": n, "refused": [...]}."""
    if not supported(car):
        raise KLineError(f"No service light reset for the {car}")
    if link.addr != CI_ADDR:
        raise KLineError("Connect to the Combined Instrument (address 51) first")
    refused: list[int] = []
    if car == "S/V40":
        kind, steps = "S/V40", SV40
        for n, d in enumerate(steps, 1):
            _step(link, d, log, n, refused)
    else:
        r = link.request(b"\xB9\x13")
        if r[:1] == b"\xF9":
            kind, steps = "newer instrument", NEWER
            log("Step 1 (B9 13): OK, newer instrument")
        elif r[:2] == b"\x7F\xB9":
            kind, steps = "850-type instrument", OLDER_850
            log("Step 1 (B9 13): refused, so this is an 850-type instrument")
        else:
            raise KLineError(f"Service reset step 1 (B9 13): unexpected reply {hexs(r)}")
        for n, d in enumerate(steps, 2):
            _step(link, d, log, n, refused)
    # end of session; the instrument answers E0
    r = link.request(b"\xA0")
    link.addr = None
    if r[:1] != b"\xE0":
        raise KLineError(f"Service reset: end of session got {hexs(r)}")
    total = len(steps) + (0 if car == "S/V40" else 1) + 1
    return {"type": kind, "steps": total, "refused": refused}
