# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Rick Hale Parker
"""ELM327 / OBDLink (STN) backend.

The adapter does the K-line work itself: 5-baud wake-up, 10400 baud,
echo handling and the checksum. We drive it with AT commands.

Setup, and why:
  AT SP 3        ISO 9141-2 framing. Its 3 header bytes are sent exactly as
                 given. (ISO 14230 mode rewrites the length in the first
                 byte, and these ECUs count the length differently.)
  AT IIA hh      wake-up address. The ELM sends the full 8 bits as given,
                 so we pass the init byte that already has odd parity.
  AT KW0         accept the Volvo key bytes D3 B0 (not ISO standard).
  AT SR 13       accept replies addressed to the tester, 0x13.
  AT SH 8L AA 13 header per request: length byte, ECU address, tester.
  AT WM ...      the adapter's own periodic wake-up message is our A1
                 keep-alive, so the session stays open between requests.
  AT SI          do the 5-baud wake-up now.

Status: tested on a 1998 C70 with an OBDLink EX (read codes). A long reply
split over several lines is joined back together; if the adapter cuts it
short, the app says so.
"""
from __future__ import annotations

import threading
import time

from .kline import DiagLink, KLineError, TESTER, check_reply, hexs, is_pending

BAUDS = (38400, 115200, 9600, 57600, 230400, 500000)
RETRY_PAUSE = 2.5   # s, let the ECU drop back to idle before a second wake-up


class ElmLink(DiagLink):
    name = "ELM327 / OBDLink"

    def __init__(self, port, log=print, baud: int | None = None):
        self.log = log or (lambda *_: None)
        self.lock = threading.Lock()
        self.addr = None
        self.header = None
        self.version = ""
        if isinstance(port, str):
            import serial
            self.ser = serial.Serial(timeout=0)
            self.ser.port = port
            try:
                try:
                    self._find_baud([baud] if baud else BAUDS)
                except KLineError as e:
                    if "Cannot open" in str(e):
                        raise
                    self.log("No adapter answer; trying the baud rates once more")
                    time.sleep(0.5)
                    self._find_baud([baud] if baud else BAUDS)
            except BaseException:
                self.ser.close()     # never leave the port open after a failure
                raise
        else:
            self.ser = port
            self.version = " ".join(self._cmd("ATI", 2.0))

    # -- low level -------------------------------------------------------
    def _cmd(self, text: str, timeout: float = 2.0) -> list[str]:
        """Send one command, return the reply lines (prompt '>' removed)."""
        self.ser.reset_input_buffer()
        self.ser.write((text + "\r").encode("ascii"))
        self.ser.flush()
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                if buf.endswith(b">") or b">" in buf[-3:]:
                    break
            else:
                time.sleep(0.002)
        else:
            raise KLineError(f"Adapter did not answer '{text}'")
        lines = [ln.strip() for ln in buf.decode("latin1").replace(">", "").replace("\n", "\r").split("\r")]
        return [ln for ln in lines if ln and ln != text]

    def _find_baud(self, bauds):
        skipped = []
        for b in bauds:
            try:
                self.ser.baudrate = b
                if not self.ser.is_open:
                    self.ser.open()
            except (OSError, ValueError) as e:
                # some USB drivers refuse some rates ("The parameter is incorrect")
                skipped.append(b)
                self.log(f"Port refused {b} baud ({e}); skipping")
                if "denied" in str(e).lower() or "busy" in str(e).lower():
                    raise KLineError(f"Cannot open the port: {e}") from e
                continue
            time.sleep(0.05)
            try:
                self.ser.write(b"\r")
                time.sleep(0.2)
                self.ser.reset_input_buffer()
                reply = " ".join(self._cmd("ATI", 1.0))
            except KLineError:
                continue
            if any(k in reply.upper() for k in ("ELM", "STN", "OBDLINK")):
                self.version = reply
                self.log(f"Adapter found at {b} baud: {reply}")
                return
        if skipped and not self.ser.is_open:
            raise KLineError("Cannot open the port at any baud rate. Is another program using it?")
        raise KLineError("No ELM327/OBDLink adapter answered on this port")

    def _ok(self, text: str):
        r = self._cmd(text)
        if not any("OK" in ln for ln in r):
            raise KLineError(f"Adapter rejected '{text}': {' '.join(r) or 'no reply'}")

    # -- connection ------------------------------------------------------
    def connect(self, address: int, init_byte: int | None = None) -> None:
        from .kline import odd_parity
        init_byte = odd_parity(address) if init_byte is None else init_byte
        with self.lock:
            self._cmd("ATZ", 3.0)
            time.sleep(0.5)
            # ATAT0 + ATST96: fixed 600 ms reply timeout. The adaptive default
            # was too short for Motronic 4.4 ("NO DATA" after a good wake-up).
            for c in ("ATE0", "ATL0", "ATS1", "ATH1", "ATAL", "ATSP3", "ATIB10",
                      f"ATIIA{init_byte:02X}", "ATKW0", f"ATSR{TESTER:02X}",
                      f"ATWM82{address:02X}{TESTER:02X}A1"):
                self._ok(c)
            for c in ("ATAT0", "ATST96"):
                try:
                    self._ok(c)
                except KLineError:
                    self.log(f"Adapter does not support {c}; carrying on")
            self._set_header(0x82, address)
        last = None
        for attempt in (1, 2):
            if attempt == 2:
                self.log(f"Retrying the wake-up ({last})")
                with self.lock:
                    try:
                        self._cmd("ATPC", 1.0)
                    except KLineError:
                        pass
                time.sleep(RETRY_PAUSE)
            try:
                self._wake(address, init_byte)
            except KLineError as e:
                last = e
                continue
            # first real exchange proves the link; give a slow ECU a second chance
            for _ in (1, 2):
                try:
                    if self.keep_alive():
                        self.log("Comms established")
                        return
                except KLineError as e:
                    last = e
                time.sleep(0.3)
            self.addr = None
            last = last or KLineError("ECU did not answer the first request")
        raise last

    def _wake(self, address: int, init_byte: int):
        with self.lock:
            self.log(f"5-baud init with {init_byte:02X} (via adapter)")
            r = " ".join(self._cmd("ATSI", 6.0))
            self.log(f"Adapter: {r}")
            if "ERROR" in r or "UNABLE" in r or ("BUS INIT" in r and "OK" not in r):
                raise KLineError("No response from car. Is the ignition at position II? "
                                 f"(adapter said: {r})")
            if "?" in r:
                self.log("Adapter has no AT SI; it will wake the ECU on the first request.")
            self.addr = address

    def _set_header(self, fmt: int, address: int):
        h = (fmt, address, TESTER)
        if h != self.header:
            self._ok("ATSH" + "".join(f"{x:02X}" for x in h))
            self.header = h

    def request(self, data: bytes, pre_delay: float | None = None) -> bytes:
        if self.addr is None:
            raise KLineError("Not connected")
        with self.lock:
            if pre_delay:
                time.sleep(pre_delay)
            self._set_header(0x80 | (len(data) + 1), self.addr)
            self.log(f"TX {self.header[0]:02X} {self.addr:02X} {TESTER:02X} {hexs(data)}")
            lines = self._cmd(hexs(data).replace(" ", ""), 3.0)
            raw = bytearray()
            for ln in lines:
                u = ln.upper()
                if "NO DATA" in u:
                    raise KLineError("Comms lost with car, time out (no data)")
                if "ERROR" in u or "UNABLE" in u or u == "?":
                    raise KLineError(f"Adapter error: {ln}")
                try:
                    part = bytes.fromhex(ln.replace(" ", ""))
                except ValueError:
                    continue
                self.log(f"RX {hexs(part)}")
                raw += part
            # A long reply may come split over several lines; walk the bytes frame by frame.
            i = 0
            pending = None
            while i + 4 <= len(raw):
                if raw[i] & 0xC0 != 0x80:
                    i += 1
                    continue
                need = (raw[i] & 0x3F) + 3
                frame = bytes(raw[i:i + need])
                if frame[1] == TESTER and frame[2] == self.addr:
                    if len(frame) < need:
                        raise KLineError(f"The adapter cut the reply short (got {len(frame)} of {need} bytes). "
                                         "Try an OBDLink or the K-line cable for this.")
                    out = check_reply(frame, self.addr)
                    if is_pending(out, data):
                        self.log("ECU busy, waiting for the answer")
                        pending = out
                    else:
                        return out
                i += need
            if pending:
                raise KLineError(f"ECU said busy ({hexs(pending)}) but the answer never came")
            raise KLineError(f"No valid reply ({' / '.join(lines)})")

    def _close_port(self):
        try:
            self._cmd("ATPC", 1.0)
        except KLineError:
            pass
        self.ser.close()
