# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Rick Hale Parker
"""K-line protocol for 1996-98 Volvo ECUs, rebuilt from legacy p-code.

Sequence (all numbers taken from the legacy program):

1. Cable check at 9600 8N1: send 0x55, the K-line cable must echo 0x55
   within 100 ms.
2. Wait 500 ms, then send the ECU's init byte at 5 baud by toggling the
   serial Break line (Break on = line low). 200 ms per bit: start bit,
   8 data bits LSB first, stop bit. The init byte is the 7-bit address
   with odd parity in bit 7.
3. Switch to 10400 baud. Within 1.5 s the ECU sends 0x55, KB1=0xD3,
   KB2=0xB0.
4. Wait 30 ms, send ~KB2 (0x4F). Within 120 ms the ECU answers with
   ~init_byte.
5. Messages: [0x80 | L, target, 0x13, data..., checksum]
   L = number of bytes after the target byte, not counting the checksum
   (so the 0x13 source byte counts). Checksum = sum of all bytes mod 256.
   Replies come back as [0x80 | L, 0x13, ecu, data..., checksum].
   Every byte the tester sends is echoed on the K-line.
6. Services:
      A1        keep-alive      -> E1
      AE 01     read DTCs       -> EE 01 dtc dtc ...   (one byte per DTC)
      AF 01     clear DTCs
      B9 F0     read ECU ID     -> F9 F0 ...
      A0        end session
   Before each message the tester waits 50 ms; bytes are 5 ms apart.
"""
from __future__ import annotations

import threading
import time

TESTER = 0x13
SYNC, KB1, KB2 = 0x55, 0xD3, 0xB0


class KLineError(Exception):
    pass


def odd_parity(addr: int) -> int:
    addr &= 0x7F
    return addr | (0x80 if bin(addr).count("1") % 2 == 0 else 0)


def checksum(data) -> int:
    return sum(data) & 0xFF


def build_message(target: int, data: bytes) -> bytes:
    body = bytes([0x80 | (len(data) + 1), target & 0xFF, TESTER]) + bytes(data)
    return body + bytes([checksum(body)])


def hexs(b) -> str:
    return " ".join(f"{x:02X}" for x in b)


class DiagLink:
    """Services shared by every interface. Subclasses provide connect(),
    request() and _close_port()."""
    name = "interface"
    addr = None

    def connect(self, address: int, init_byte: int | None = None) -> None:
        raise NotImplementedError

    def request(self, data: bytes, pre_delay: float | None = None) -> bytes:
        raise NotImplementedError

    def _close_port(self):
        pass

    def keep_alive(self) -> bool:
        r = self.request(b"\xA1")
        return bool(r) and r[0] == 0xE1

    def read_dtcs(self) -> list[int]:
        r = self.request(b"\xAE\x01")
        if not r or r[0] != 0xEE:
            raise KLineError(f"Invalid data (reply {hexs(r)})")
        return list(r[2:])

    def clear_dtcs(self) -> bytes:
        return self.request(b"\xAF\x01")

    def read_id(self) -> dict:
        """ECU ID. Reply: F9 F0 00, hardware part no. (4 bytes BCD), hardware
        rev (3 ASCII), software part no. (4 bytes BCD), software rev (3 ASCII).
        Decoded from a 1998 C70, e.g. 09 17 16 36 = Volvo part 9171636."""
        r = self.request(b"\xB9\xF0", pre_delay=0.65)
        if not r or r[0] != 0xF9:
            raise KLineError(f"Invalid data (reply {hexs(r)})")
        d = r[2:]
        return {
            "raw": hexs(r),
            "hw_part": part_number(d[1:5]),
            "hw_rev": revision(d[5:8]),
            "sw_part": part_number(d[8:12]),
            "sw_rev": revision(d[12:15]),
        }

    def disconnect(self):
        try:
            if self.addr is not None:
                self.request(b"\xA0")
        except KLineError:
            pass
        finally:
            self.addr = None

    def close(self):
        self.disconnect()
        try:
            self._close_port()
        except Exception:  # noqa: BLE001
            pass


def part_number(b: bytes) -> str:
    """BCD bytes to a Volvo part number: 09 17 16 36 -> 9171636."""
    digits = "".join(f"{x:02X}" for x in b)
    if not digits.isdigit():
        return hexs(b)                  # not BCD: show the bytes
    return digits.lstrip("0") or "0"


def revision(b: bytes) -> str:
    """3 ASCII characters, e.g. 30 30 31 -> 001, 42 20 20 -> B."""
    if b and all(0x20 <= x < 0x7F for x in b):
        return b.decode("ascii").strip() or "-"
    return hexs(b)


def is_pending(reply: bytes, request: bytes) -> bool:
    """7E <service> xx: 'request received, answer follows'. Seen from the C70
    Combined Instrument on B9 F0 (7E B9 23), followed by the real F9 F0 reply."""
    return len(reply) >= 2 and reply[0] == 0x7E and reply[1] == request[0]


def check_reply(frame: bytes, ecu: int) -> bytes:
    """Validate a reply frame [80+L][13][ecu] data... [sum]; return data."""
    if len(frame) < 4 or frame[0] & 0xC0 != 0x80:
        raise KLineError(f"Received header byte invalid ({hexs(frame[:1])})")
    if len(frame) != (frame[0] & 0x3F) + 3:
        raise KLineError(f"Reply length wrong ({hexs(frame)})")
    if checksum(frame[:-1]) != frame[-1]:
        raise KLineError("Received checksum error")
    if frame[1] != TESTER or frame[2] != ecu:
        raise KLineError(f"Reply from wrong address ({hexs(frame[:3])})")
    return bytes(frame[3:-1])


class KLine(DiagLink):
    """Talks to one ECU through a serial port with a dumb K-line cable.

    ``port`` is either a device name (``/dev/ttyUSB0``, ``COM3``) or an
    already-open pyserial-like object (used by the simulator).
    """

    def __init__(self, port, log=print, bit_time=0.2, p3=0.05, p4=0.005):
        if isinstance(port, str):
            import serial  # pyserial
            self.ser = serial.Serial(port, 9600, bytesize=8, parity="N", stopbits=1, timeout=0)
        else:
            self.ser = port
        self.log = log or (lambda *_: None)
        self.bit_time = bit_time
        self.p3, self.p4 = p3, p4
        self.echo = True
        self.addr = None
        self.lock = threading.Lock()

    # -- low level -------------------------------------------------------
    def _read(self, n: int, first_timeout: float, byte_timeout: float | None = None) -> bytes:
        """Read up to n bytes. Stop at first_timeout if nothing comes,
        or byte_timeout after the last byte received."""
        buf = bytearray()
        byte_timeout = byte_timeout or first_timeout
        deadline = time.monotonic() + first_timeout
        while len(buf) < n:
            waiting = self.ser.in_waiting
            if waiting:
                buf += self.ser.read(min(waiting, n - len(buf)))
                deadline = time.monotonic() + byte_timeout
                continue
            if time.monotonic() > deadline:
                break
            time.sleep(0.001)
        return bytes(buf)

    def _write(self, data: bytes, gap: float = 0.0):
        if gap:
            for b in data:
                self.ser.write(bytes([b]))
                self.ser.flush()
                time.sleep(gap)
        else:
            self.ser.write(data)
            self.ser.flush()

    # -- connection ------------------------------------------------------
    def check_interface(self) -> bool:
        """Cable test: 0x55 at 9600 baud must echo back."""
        self.ser.baudrate = 9600
        self.ser.reset_input_buffer()
        self._write(b"\x55")
        got = self._read(1, 0.1)
        self.echo = got == b"\x55"
        self.log(f"Interface check: sent 55, got {hexs(got) or 'nothing'}")
        return self.echo

    def _five_baud(self, byte: int):
        bits = [0] + [(byte >> i) & 1 for i in range(8)] + [1]  # start, data LSB first, stop
        # merge runs so each level change is one sleep, like the original
        runs = []
        for b in bits:
            if runs and runs[-1][0] == b:
                runs[-1][1] += 1
            else:
                runs.append([b, 1])
        for level, count in runs:
            self.ser.break_condition = (level == 0)
            time.sleep(self.bit_time * count)
        self.ser.break_condition = False

    def connect(self, address: int, init_byte: int | None = None) -> None:
        init_byte = odd_parity(address) if init_byte is None else init_byte
        with self.lock:
            if not self.check_interface():
                raise KLineError("Interface Error, check connections (no echo at 9600 baud)")
            time.sleep(0.5)
            self.ser.reset_input_buffer()
            self.log(f"5-baud init with {init_byte:02X}")
            self._five_baud(init_byte)
            self.ser.baudrate = 10400
            self.ser.reset_input_buffer()
            kb = self._read(3, 1.5, 0.3)
            self.log(f"Sync/key bytes: {hexs(kb) or 'nothing'}")
            if len(kb) < 1:
                raise KLineError("No response from car. Is the ignition at position II? Wait 3 s and retry.")
            if kb[0] != SYNC:
                raise KLineError(f"No sync byte (got {kb[0]:02X}). Is the ignition at position II?")
            if len(kb) < 3:
                raise KLineError("Key bytes missing")
            if kb[1] != KB1:
                raise KLineError(f"KB1 invalid ({kb[1]:02X}, expected {KB1:02X})")
            if kb[2] != KB2:
                raise KLineError(f"KB2 invalid ({kb[2]:02X}, expected {KB2:02X})")
            time.sleep(0.03)
            inv = (~kb[2]) & 0xFF
            self._write(bytes([inv]))
            want = 2 if self.echo else 1
            r = self._read(want, 0.12 + 0.05, 0.1)
            self.log(f"Sent {inv:02X}, got {hexs(r) or 'nothing'}")
            if self.echo:
                if not r or r[0] != inv:
                    raise KLineError("KB2 complement not sent (no echo)")
                r = r[1:]
            if not r:
                raise KLineError("No response to KB2 complement, time out")
            if r[0] != (~init_byte) & 0xFF:
                raise KLineError(f"Complement of address byte incorrect ({r[0]:02X})")
            self.addr = address
            self.log("Comms established")

    # -- messages --------------------------------------------------------
    def request(self, data: bytes, pre_delay: float | None = None) -> bytes:
        if self.addr is None:
            raise KLineError("Not connected")
        msg = build_message(self.addr, data)
        with self.lock:
            time.sleep(self.p3 if pre_delay is None else pre_delay)
            self.ser.reset_input_buffer()
            self.log(f"TX {hexs(msg)}")
            self._write(msg, self.p4)
            if self.echo:
                e = self._read(len(msg), 0.3, 0.1)
                if e != msg:
                    raise KLineError(f"Echo mismatch: {hexs(e)}")
            deadline = time.monotonic() + 5.0
            while True:
                hdr = self._read(3, 1.0, 0.2)
                if len(hdr) < 3:
                    raise KLineError("Comms lost with car, time out")
                if hdr[0] & 0xC0 != 0x80:
                    raise KLineError(f"Received header byte invalid ({hdr[0]:02X})")
                n = hdr[0] & 0x3F  # bytes after target: source + data
                rest = self._read(n, 0.3, 0.2)  # (n-1) data + checksum
                frame = hdr + rest
                self.log(f"RX {hexs(frame)}")
                if len(rest) < n:
                    raise KLineError("Comms lost with car, time out (TD)")
                if checksum(frame[:-1]) != frame[-1]:
                    raise KLineError("Received checksum error")
                out = bytes(frame[3:-1])
                if is_pending(out, data) and time.monotonic() < deadline:
                    self.log("ECU busy, waiting for the answer")
                    continue
                return out

    name = "K-line cable"

    def _close_port(self):
        self.ser.close()
