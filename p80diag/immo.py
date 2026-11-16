# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Rick Hale Parker
#
# ######################################################################
# ##   IMMO3 IMMOBILIZER KEY PROGRAMMING  --  THE PROTOCOL LIVES HERE  ##
# ##   Every K-line command / response for IMMO3 is in this one file.  ##
# ######################################################################
"""IMMO3 immobilizer key programming for 1999-2000 P80 cars (address 0x41).

Decoded from a matthewsvolvosite writeup ("Keys, IMMO3, VIDA and you"),
verified by its author on a 2000 V70XC (Bosch immobox 9459037 / 9459074,
EEPROM 93C56). IMMO3 only: NOT the IMMO1/2 immobilizers on pre-1999 cars,
NOT the later CEM cars.

UNTESTED here on a real car. The app owner's own P80 is a 1998 C70 (pre-IMMO3),
so this whole module has only ever run against the Demo immobox in sim.py.

The app does NOT obtain the PIN. The user types it in. The PIN comes from a
VIDA memory dump done outside this app; the README documents that procedure.

Safety, built into this module:
  * A wrong PIN arms a lockout timer (~30 units, counting down ~1 unit / 10 s).
    While it is armed EVERY unlock returns 7F A3 33, even with the correct PIN.
    So unlock() reads the timer first (A5 08 01) and refuses to send a PIN while
    it is armed. It never retries a PIN by itself.
  * erase_and_learn() (B2 18) wipes EVERY stored key and learns only the key now
    in the ignition into slot 1. Every other key must be re-taught afterward, or
    it will not start the car. The GUI guards this behind a type-to-confirm.

Every request goes through link.request(data), which already works on all three
backends (K-line cable, ELM327/OBDLink, J2534). The ELM backend's connect(0x41)
already issues the init the writeup requires (ATIIA C1, ATSR 13, ATKW0, ATSP3,
ATWM 82 41 13 A1) and sets the per-request header length byte itself, so no
backend needs changing for IMMO3.
"""
from __future__ import annotations

from .kline import KLineError, hexs

IMMO_ADDR = 0x41
LOCKOUT_UNIT_SECONDS = 10           # the timer counts down ~1 unit every 10 s
IMMO3_CARS = ("S/V70", "C70", "S/V40")   # the P80 forms that reached IMMO3 ('99-'00)


def supports(car: str) -> bool:
    """True for the car forms that can have an IMMO3 immobilizer."""
    return car in IMMO3_CARS


def parse_pin(text: str) -> bytes:
    """4 decimal digits -> 2 BCD bytes. '1234' -> b'\\x12\\x34'."""
    t = text.strip()
    if len(t) != 4 or not t.isdigit():
        raise ValueError("The PIN must be exactly 4 digits (0-9).")
    return bytes([int(t[:2], 16), int(t[2:], 16)])


def unlock_request_hex(pin_bytes: bytes) -> str:
    """The exact bytes unlock() will send, for the user to check against the dump."""
    return hexs(b"\xA3\x02\x00" + bytes(pin_bytes))


def _noop(*_a, **_k):
    pass


def _require_addr(link):
    if getattr(link, "addr", None) != IMMO_ADDR:
        raise KLineError("Connect to the Immobilizer (address 41) first")


def _nrc(reply: bytes, service: int):
    """Negative response code of a 7F <service> NN reply, else None."""
    if len(reply) >= 3 and reply[0] == 0x7F and reply[1] == service:
        return reply[2]
    return None


def check_lockout(link, log=print) -> dict:
    """Read the anti-scan lockout timer. A5 08 01 -> E5 08 NN.

    Returns {"units", "locked", "seconds", "raw"}. NN must be 0 before a PIN may
    be sent; any non-zero value means a wrong PIN was entered earlier and the box
    will refuse every unlock until it counts back down."""
    log = log or _noop
    _require_addr(link)
    r = link.request(b"\xA5\x08\x01")
    if r[:2] != b"\xE5\x08" or len(r) < 3:
        raise KLineError(f"Lockout read got an unexpected reply: {hexs(r)}")
    units = r[2]
    if units:
        log(f"Lockout timer ARMED: {units} unit(s), about {units * LOCKOUT_UNIT_SECONDS} s left")
    else:
        log("Lockout timer clear (00)")
    return {"units": units, "locked": units != 0,
            "seconds": units * LOCKOUT_UNIT_SECONDS, "raw": hexs(r)}


def open_session(link, log=print) -> bytes:
    """Open the programming session. AE 01 -> EE 01."""
    log = log or _noop
    _require_addr(link)
    r = link.request(b"\xAE\x01")
    if r[:1] != b"\xEE":
        raise KLineError(f"Open session got an unexpected reply: {hexs(r)}")
    log("Session open (AE 01 -> EE 01)")
    return r


def unlock(link, pin_bytes: bytes, log=print) -> dict:
    """Unlock with the 2-byte BCD PIN, safely.

    Reads the lockout timer first and refuses to send the PIN while it is armed.
    Then AE 01 (open session) and A3 02 00 <pinHi> <pinLo> -> E3 02. The leading
    00 is a fixed selector, not part of the PIN. Never retries.

    Raises KLineError, with no PIN sent, if the lockout is armed. Raises on a
    wrong PIN too (which will have armed the lockout); do NOT call again."""
    log = log or _noop
    _require_addr(link)
    pin_bytes = bytes(pin_bytes)
    if len(pin_bytes) != 2:
        raise ValueError("The PIN must be exactly 2 bytes")

    lock = check_lockout(link, log)
    if lock["locked"]:
        raise KLineError(
            f"Lockout timer is armed ({lock['units']} unit(s), about {lock['seconds']} s left). "
            "A wrong PIN was entered earlier. The PIN was NOT sent. Wait for the timer to "
            "clear, or cycle the ignition to position II to start a new key cycle.")

    open_session(link, log)
    req = b"\xA3\x02\x00" + pin_bytes
    log(f"Unlock: {hexs(req)}")
    r = link.request(req)
    if r[:1] == b"\xE3":
        log("Unlocked (E3)")
        return {"ok": True, "raw": hexs(r)}
    nrc = _nrc(r, 0xA3)
    if nrc == 0x33:
        raise KLineError(
            "Unlock refused (7F A3 33). Either the lockout was already armed, or the PIN was "
            "wrong and has just armed it. Do NOT retry. Cycle the ignition to II and wait for "
            "the lockout to clear before trying the correct PIN.")
    if nrc == 0x12:
        raise KLineError("Unlock refused (7F A3 12): invalid request format.")
    raise KLineError(f"Unlock got an unexpected reply: {hexs(r)}")


def key_count(link, log=print) -> dict:
    """Number of keys taught. B9 05 -> F9 05 NN.

    NN is best-effort: the positive-response service byte (F9) is verified, but
    the exact payload layout is not confirmed on a real car."""
    log = log or _noop
    _require_addr(link)
    r = link.request(b"\xB9\x05")
    if r[:1] != b"\xF9":
        raise KLineError(f"Key count got an unexpected reply: {hexs(r)}")
    count = r[2] if len(r) >= 3 else None
    log(f"Key count reply: {hexs(r)}" + (f" ({count} key(s))" if count is not None else ""))
    return {"count": count, "raw": hexs(r)}


def teach_status(link, log=print) -> dict:
    """Teach/learn status. A5 11 01 -> E5 11 ... (payload layout unconfirmed)."""
    log = log or _noop
    _require_addr(link)
    r = link.request(b"\xA5\x11\x01")
    if r[:1] != b"\xE5":
        raise KLineError(f"Teach status got an unexpected reply: {hexs(r)}")
    log(f"Teach status reply: {hexs(r)}")
    return {"status": r[2] if len(r) >= 3 else None, "raw": hexs(r)}


def add_key(link, log=print) -> dict:
    """Teach the key now in the ignition into the next free slot. Non-destructive.

    B2 01 -> F2 01. A 7F B2 22 ("conditions not correct") means the key is
    already in the table; that is expected when re-adding a key and also confirms
    the unlock worked, so it is reported as success, not an error."""
    log = log or _noop
    _require_addr(link)
    r = link.request(b"\xB2\x01")
    if r[:1] == b"\xF2":
        log("Key added (F2 01)")
        return {"ok": True, "already": False, "raw": hexs(r)}
    if _nrc(r, 0xB2) == 0x22:
        log("7F B2 22: key already in the table (this also confirms the unlock worked)")
        return {"ok": True, "already": True, "raw": hexs(r)}
    raise KLineError(f"Add key got an unexpected reply: {hexs(r)}")


def erase_and_learn(link, log=print) -> dict:
    """DESTRUCTIVE. Erase ALL keys and learn the key now in the ignition into
    slot 1. B2 18 -> F2 18.

    After this only the key that was in the ignition will start the car. Every
    other key must be re-added with add_key(), each with that key present."""
    log = log or _noop
    _require_addr(link)
    r = link.request(b"\xB2\x18")
    if r[:1] == b"\xF2":
        log("Erased all keys and learned the present key into slot 1 (F2 18)")
        return {"ok": True, "raw": hexs(r)}
    raise KLineError(f"Erase + learn got an unexpected reply: {hexs(r)}")


def close(link, log=print) -> bytes:
    """Close the programming session. A0 -> E0, then mark the link closed."""
    log = log or _noop
    r = link.request(b"\xA0")
    if r[:1] != b"\xE0":
        raise KLineError(f"Close got an unexpected reply: {hexs(r)}")
    log("Session closed (A0 -> E0)")
    link.addr = None
    return r
