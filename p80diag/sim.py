# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Rick Hale Parker
"""A fake serial port with a pretend Volvo ECU behind it.

Behaves like a dumb K-line cable: every byte written is echoed. It decodes
the 5-baud init from the Break timing, answers the key-byte handshake and
replies to the diagnostic services used here. Good for testing without a car.
"""
from __future__ import annotations

import random
import threading
import time

from .kline import checksum, TESTER


class SimECU:
    def __init__(self, dtcs_by_addr: dict[int, list[int]] | None = None, bit_time=0.2, drop_rate=0.0,
                 ci_type="newer"):
        """ci_type: "newer" or "850", the kind of combined instrument (0x51)."""
        self.dtcs = dtcs_by_addr if dtcs_by_addr is not None else {}
        self.ci_type = ci_type
        self.ci_pending = True      # like the real C70 instrument: 7E B9 23 before the ID
        self.service_reset = False
        self.bit_time = bit_time
        self.drop_rate = drop_rate

    def dtcs_for(self, addr):
        return self.dtcs.setdefault(addr, [])


class SimSerial:
    def __init__(self, ecu: SimECU | None = None):
        self.ecu = ecu or SimECU()
        self._baud = 9600
        self._rx = bytearray()
        self._lock = threading.Lock()
        self._break = False
        self._edges = []  # (time, level) level 0 = low
        self._rxmsg = bytearray()
        self.awake = None       # address of the ECU that answered
        self._init = None
        self._stage = "idle"
        self.timeout = 0
        self.is_open = True

    # pyserial-ish API
    @property
    def baudrate(self):
        return self._baud

    @baudrate.setter
    def baudrate(self, v):
        self._baud = v
        if v == 10400 and self._edges:
            self._decode_init()

    @property
    def break_condition(self):
        return self._break

    @break_condition.setter
    def break_condition(self, on):
        if on != self._break:
            self._edges.append((time.monotonic(), 0 if on else 1))
        self._break = on

    @property
    def in_waiting(self):
        with self._lock:
            return len(self._rx)

    def read(self, n=1):
        with self._lock:
            out = bytes(self._rx[:n])
            del self._rx[:n]
            return out

    def reset_input_buffer(self):
        with self._lock:
            self._rx.clear()

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    def _push(self, data, delay=0.0):
        def go():
            if delay:
                time.sleep(delay)
            with self._lock:
                self._rx += data
        if delay:
            threading.Thread(target=go, daemon=True).start()
        else:
            go()

    def write(self, data):
        data = bytes(data)
        self._push(data)  # K-line echo
        for b in data:
            self._on_byte(b)
        return len(data)

    # ECU behaviour
    def _decode_init(self):
        bt = self.ecu.bit_time
        edges = self._edges
        self._edges = []
        bits = []
        for (t0, lvl), (t1, _) in zip(edges, edges[1:]):
            bits += [lvl] * max(1, round((t1 - t0) / bt))
        # bits: start(0) + 8 data. The last high run (1 bits + stop) has no
        # closing edge, so pad with 1s.
        bits += [1] * max(0, 9 - len(bits))
        if not bits or bits[0] != 0:
            return
        byte = sum(b << i for i, b in enumerate(bits[1:9]))
        if bin(byte).count("1") % 2 != 1:
            return  # bad parity: ECU ignores it
        self._init = byte
        self._stage = "kb"
        self._push(bytes([0x55, 0xD3, 0xB0]), delay=0.1)

    def _on_byte(self, b):
        if self._baud == 9600:
            return
        if self._stage == "kb":
            if b == 0x4F:
                self.awake = self._init & 0x7F
                self._stage = "session"
                self._push(bytes([(~self._init) & 0xFF]), delay=0.03)
            return
        if self._stage != "session":
            return
        self._rxmsg.append(b)
        m = self._rxmsg
        if len(m) >= 1 and m[0] & 0xC0 != 0x80:
            m.clear()
            return
        if len(m) >= 1 and len(m) == (m[0] & 0x3F) + 3:
            msg = bytes(m)
            m.clear()
            if checksum(msg[:-1]) != msg[-1] or msg[1] != self.awake or msg[2] != TESTER:
                return
            self._reply(msg[3:-1])

    def _reply(self, data):
        frame = ecu_reply(self.ecu, self.awake, data)
        if frame is None:
            return
        if data[0] == 0xA0:
            self._stage = "idle"
        self._push(frame, delay=0.03)


def ecu_reply(ecu: SimECU, addr: int, data: bytes):
    """The pretend ECU's reply frame to a request, or None if it stays silent."""
    if ecu.drop_rate and random.random() < ecu.drop_rate:
        return None
    svc = data[0]
    if svc == 0xA1:
        out = b"\xE1"
    elif svc == 0xAE:
        out = bytes([0xEE, data[1]]) + bytes(ecu.dtcs_for(addr))
    elif svc == 0xAF:
        ecu.dtcs[addr] = []
        out = bytes([0xEF, data[1]])
    elif svc == 0xB9 and data[1:2] == b"\x13" and addr == 0x51 and ecu.ci_type == "850":
        out = b"\x7F\xB9\x12"
    elif svc in (0xB0, 0xB2, 0xB8) and addr == 0x51:
        out = bytes([svc + 0x40]) + bytes(data[1:])
        if svc in (0xB0, 0xB8):
            ecu.service_reset = True
    elif svc == 0xB9:
        out = bytes.fromhex("F9F000") + bytes.fromhex("09475123") + b"001" + bytes.fromhex("09475124") + b"002"
    elif svc == 0xA0:
        out = b"\xE0"
    else:
        out = bytes([0x7F, svc, 0x11])
    body = bytes([0x80 | (len(out) + 1), TESTER, addr]) + out
    frame = body + bytes([checksum(body)])
    if svc == 0xB9 and addr == 0x51 and ecu.ci_pending:
        busy = bytes([0x84, TESTER, addr, 0x7E, 0xB9, 0x23])
        frame = busy + bytes([checksum(busy)]) + frame     # 'busy', then the answer
    return frame


def split_frames(raw: bytes) -> list[bytes]:
    out, i = [], 0
    while i < len(raw):
        n = (raw[i] & 0x3F) + 3
        out.append(raw[i:i + n])
        i += n
    return out


def odd(b):
    return bin(b).count("1") % 2 == 1


class SimElmSerial:
    """A pretend ELM327 / OBDLink on a serial port, with the same ECU behind it.
    Understands the AT commands the ELM backend uses."""

    def __init__(self, ecu: SimECU | None = None, split: int = 0, cut: int = 0,
                 fail_inits: int = 0, silent_first: int = 0):
        """split: print long replies over lines of this many bytes.
        cut: drop reply bytes after this many (a weak adapter)."""
        self.ecu = ecu or SimECU()
        self.split, self.cut = split, cut
        self.fail_inits = fail_inits        # BUS INIT ERROR this many times first
        self.silent_first = silent_first    # NO DATA to this many requests first
        self._out = bytearray()
        self._line = bytearray()
        self._lock = threading.Lock()
        self.baudrate = 38400
        self.timeout = 0
        self.is_open = True
        self.reset()

    def reset(self):
        self.header = [0x68, 0x6A, 0xF1]
        self.iia = 0x33
        self.proto = 0
        self.awake = None
        self.headers_on = False
        self.kw_check = True

    @property
    def in_waiting(self):
        with self._lock:
            return len(self._out)

    def read(self, n=1):
        with self._lock:
            out = bytes(self._out[:n])
            del self._out[:n]
            return out

    def reset_input_buffer(self):
        with self._lock:
            self._out.clear()

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    def write(self, data):
        for b in bytes(data):
            if b == 0x0D:
                reply = self._command(self._line.decode("latin1").strip().upper().replace(" ", ""))
                self._line.clear()
                with self._lock:
                    self._out += (reply + "\r\r>").encode("latin1")
            else:
                self._line.append(b)
        return len(data)

    def _init(self):
        # 5-baud init to self.iia; the ECU answers if the byte has odd parity
        if not odd(self.iia):
            return False
        self.awake = self.iia & 0x7F
        return True

    def _command(self, c):
        if c.startswith("AT"):
            a = c[2:]
            if a in ("Z", "WS"):
                self.reset()
                return "\r\rELM327 v1.5"
            if a == "I":
                return "ELM327 v1.5"
            if a.startswith("SP"):
                self.proto = int(a[2:], 16) if a[2:] else 0
                return "OK"
            if a == "H1":
                self.headers_on = True
            elif a == "H0":
                self.headers_on = False
            elif a.startswith("IIA"):
                self.iia = int(a[3:], 16)
            elif a == "KW0":
                self.kw_check = False
            elif a.startswith("SH"):
                h = a[2:]
                self.header = [int(h[i:i + 2], 16) for i in range(0, len(h), 2)]
            elif a == "SI":
                if self.fail_inits:
                    self.fail_inits -= 1
                    return "BUS INIT: ...ERROR"
                return "BUS INIT: ...OK" if self._init() else "BUS INIT: ...ERROR"
            elif a == "PC":
                self.awake = None
            return "OK"
        try:
            data = bytes.fromhex(c)
        except ValueError:
            return "?"
        if self.awake is None and not self._init():
            return "BUS INIT: ...ERROR"
        if self.proto != 3 or self.header[1] != self.awake or self.header[2] != TESTER:
            return "NO DATA"
        sent = bytes(self.header) + data
        if (sent[0] & 0x3F) != len(data) + 1:
            return "NO DATA"  # the ECU ignores a frame with the wrong length
        if self.silent_first:
            self.silent_first -= 1
            return "NO DATA"
        frame = ecu_reply(self.ecu, self.awake, data)
        if frame is None:
            return "NO DATA"
        lines = []
        for fr in split_frames(frame):
            shown = fr if self.headers_on else fr[3:-1]
            if self.cut:
                shown = shown[:self.cut]
            n = self.split or len(shown)
            lines += [" ".join(f"{x:02X}" for x in shown[i:i + n]) for i in range(0, len(shown), n)]
        return "\r".join(lines)


class FakeJ2534:
    """Stands in for a J2534 DLL (same methods as j2534.J2534Api)."""

    def __init__(self, ecu: SimECU | None = None, need_w1: int = 0):
        """need_w1: act like a picky device that only wakes the ECU when W1
        (key-byte wait, config 0x0E) is at least this many ms."""
        self.ecu = ecu or SimECU()
        self.awake = None
        self.rx = []
        self.need_w1 = need_w1
        self.w1 = 300
        self.inits = 0

    def open(self):
        return 1

    def close(self, dev):
        pass

    def connect(self, dev, protocol, flags, baud):
        return 1

    def disconnect(self, chan):
        self.awake = None
        self.w1 = 300

    def set_config(self, chan, params):
        self.w1 = params.get(0x0E, self.w1)

    def try_config(self, chan, params):
        self.set_config(chan, params)
        return []

    def start_pass_all(self, chan):
        pass

    def clear_rx(self, chan):
        self.rx.clear()

    def five_baud_init(self, chan, byte):
        self.inits += 1
        if self.w1 < self.need_w1:
            raise OSError("J2534 FIVE_BAUD_INIT failed (error 0x7) Sent 5 baud init, got no response")
        if not odd(byte):
            raise OSError("J2534 error: ERR_FAILED (no response to init)")
        self.awake = byte & 0x7F
        return bytes([0xD3, 0xB0])

    def write(self, chan, data, timeout_ms=100):
        if self.awake is None or data[1] != self.awake:
            return
        if (data[0] & 0x3F) != len(data) - 3 or checksum(data[:-1]) != data[-1]:
            return
        frame = ecu_reply(self.ecu, self.awake, data[3:-1])
        if frame:
            self.rx += split_frames(frame)

    def read(self, chan, timeout_ms=100):
        if self.rx:
            return [self.rx.pop(0)]
        time.sleep(timeout_ms / 1000)
        return []
