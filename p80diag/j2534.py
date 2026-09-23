# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Rick Hale Parker
"""J2534 pass-thru backend (Windows): Mongoose, DiCE and other J2534 devices.

Uses the device's J2534 04.04 driver DLL through ctypes.

Setup, and why:
  PassThruConnect ISO9141, flag ISO9141_NO_CHECKSUM, 10400 baud
                 raw frames: we add and check the checksum ourselves.
  Pass-all filter (mask 00 / pattern 00)
                 ISO9141 channels receive nothing without a filter.
  Ioctl FIVE_BAUD_INIT with the init byte
                 the device sends it at 5 baud, reads the key bytes
                 (D3 B0), sends ~KB2 and checks the ECU's ~address.
  Loopback off   the device hides the K-line echo.

If the wake-up fails, the app retries with a longer key-byte wait (W1),
then with ISO9141_K_LINE_ONLY, then with the 7-bit address. The method
that worked is tried first next time.

Most J2534 DLLs are 32-bit. Then the app has to run on 32-bit Python.

Status: DiCE tested on a 1998 C70. Mongoose Pro JLR failed with the\nstandard wake-up; the retries are untested on it.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time

from .kline import DiagLink, KLineError, build_message, check_reply, hexs, is_pending

ISO9141 = 3
ISO9141_NO_CHECKSUM = 0x00000200
ISO9141_K_LINE_ONLY = 0x00001000
SET_CONFIG, FIVE_BAUD_INIT, CLEAR_TX_BUFFER, CLEAR_RX_BUFFER = 2, 4, 7, 8
PASS_FILTER = 1
LOOPBACK, DATA_BITS, PARITY = 0x03, 0x20, 0x16
W1, W2, W3, W5 = 0x0E, 0x0F, 0x10, 0x12   # 5-baud timing (ms)

# Wake-up variants, tried in order until one works. Some devices (seen with a
# Mongoose Pro JLR) give up on the Volvo ECUs with the default settings.
#   name, extra connect flags, extra config, send 7-bit address (no parity)
WAKE_VARIANTS = [
    ("standard", 0, {}, False),
    ("longer key-byte wait", 0, {W1: 1000, W2: 100, W3: 100, W5: 500}, False),
    ("K-line only", ISO9141_K_LINE_ONLY, {W1: 1000, W2: 100, W3: 100, W5: 500}, False),
    ("7-bit address", ISO9141_K_LINE_ONLY, {W1: 1000, W2: 100, W3: 100, W5: 500}, True),
]
RETRY_PAUSE = 2.5   # s, let the ECU drop back to idle before the next wake-up
_worked = {}        # driver -> index of the variant that worked last time
TX_MSG_TYPE, START_OF_MESSAGE = 0x01, 0x02
ERR_TIMEOUT, ERR_BUFFER_EMPTY = 0x09, 0x10


def list_devices(k_line_only: bool = True, skipped: list | None = None) -> list[dict]:
    """J2534 04.04 devices registered in Windows.

    With k_line_only, leave out devices whose registry entry doesn't say
    ISO9141 = 1, and devices whose driver DLL is missing. Each one left out is
    added to ``skipped`` as (name, reason).
    """
    if sys.platform != "win32":
        return []
    import os
    import winreg
    found, seen = [], set()
    skipped = [] if skipped is None else skipped
    for base in (r"SOFTWARE\WOW6432Node\PassThruSupport.04.04", r"SOFTWARE\PassThruSupport.04.04"):
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        except OSError:
            continue
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                k = winreg.OpenKey(root, sub)
            except OSError:
                continue
            info = {"key": sub}
            for v in ("Name", "Vendor", "FunctionLibrary", "ISO9141"):
                try:
                    info[v] = winreg.QueryValueEx(k, v)[0]
                except OSError:
                    info[v] = None
            dll = info.get("FunctionLibrary")
            name = info.get("Name") or sub
            if not dll or dll.lower() in seen:
                continue
            seen.add(dll.lower())
            if k_line_only:
                if not os.path.isfile(dll):
                    skipped.append((name, f"driver file missing: {dll}"))
                    continue
                if info.get("ISO9141") != 1:
                    skipped.append((name, "no K-line (ISO9141) support listed"))
                    continue
            found.append(info)
    return found


class PASSTHRU_MSG(ctypes.Structure):
    _fields_ = [("ProtocolID", ctypes.c_ulong), ("RxStatus", ctypes.c_ulong), ("TxFlags", ctypes.c_ulong),
                ("Timestamp", ctypes.c_ulong), ("DataSize", ctypes.c_ulong), ("ExtraDataIndex", ctypes.c_ulong),
                ("Data", ctypes.c_ubyte * 4128)]


class SCONFIG(ctypes.Structure):
    _fields_ = [("Parameter", ctypes.c_ulong), ("Value", ctypes.c_ulong)]


class SCONFIG_LIST(ctypes.Structure):
    _fields_ = [("NumOfParams", ctypes.c_ulong), ("ConfigPtr", ctypes.POINTER(SCONFIG))]


class SBYTE_ARRAY(ctypes.Structure):
    _fields_ = [("NumOfBytes", ctypes.c_ulong), ("BytePtr", ctypes.POINTER(ctypes.c_ubyte))]


class J2534Api:
    """Thin ctypes wrapper around a J2534 04.04 DLL."""

    def __init__(self, dll_path: str):
        self.dll_path = dll_path
        try:
            self.dll = ctypes.WinDLL(dll_path)
        except OSError as e:
            bits = 8 * ctypes.sizeof(ctypes.c_void_p)
            raise KLineError(f"Could not load the J2534 driver ({dll_path}): {e}. "
                             f"This Python is {bits}-bit; most J2534 drivers need 32-bit Python.") from e

    def _check(self, status, what):
        if status != 0:
            buf = ctypes.create_string_buffer(256)
            try:
                self.dll.PassThruGetLastError(buf)
                detail = buf.value.decode("latin1", "replace")
            except Exception:  # noqa: BLE001
                detail = ""
            raise KLineError(f"J2534 {what} failed (error {status:#x}) {detail}".strip())

    def open(self):
        dev = ctypes.c_ulong()
        self._check(self.dll.PassThruOpen(None, ctypes.byref(dev)), "PassThruOpen")
        return dev.value

    def close(self, dev):
        self.dll.PassThruClose(ctypes.c_ulong(dev))

    def connect(self, dev, protocol, flags, baud):
        ch = ctypes.c_ulong()
        self._check(self.dll.PassThruConnect(ctypes.c_ulong(dev), ctypes.c_ulong(protocol), ctypes.c_ulong(flags),
                                             ctypes.c_ulong(baud), ctypes.byref(ch)), "PassThruConnect")
        return ch.value

    def disconnect(self, chan):
        self.dll.PassThruDisconnect(ctypes.c_ulong(chan))

    def set_config(self, chan, params: dict):
        arr = (SCONFIG * len(params))(*[SCONFIG(k, v) for k, v in params.items()])
        lst = SCONFIG_LIST(len(params), arr)
        self._check(self.dll.PassThruIoctl(ctypes.c_ulong(chan), ctypes.c_ulong(SET_CONFIG), ctypes.byref(lst), None),
                    "SET_CONFIG")

    def try_config(self, chan, params: dict) -> list:
        """Set each parameter on its own; return the ones the device refused."""
        refused = []
        for k, v in params.items():
            try:
                self.set_config(chan, {k: v})
            except KLineError:
                refused.append(k)
        return refused

    def start_pass_all(self, chan):
        mask, pattern = PASSTHRU_MSG(), PASSTHRU_MSG()
        for m in (mask, pattern):
            m.ProtocolID, m.DataSize, m.Data[0] = ISO9141, 1, 0
        fid = ctypes.c_ulong()
        self._check(self.dll.PassThruStartMsgFilter(ctypes.c_ulong(chan), ctypes.c_ulong(PASS_FILTER),
                                                    ctypes.byref(mask), ctypes.byref(pattern), None,
                                                    ctypes.byref(fid)), "StartMsgFilter")

    def clear_rx(self, chan):
        self.dll.PassThruIoctl(ctypes.c_ulong(chan), ctypes.c_ulong(CLEAR_RX_BUFFER), None, None)

    def five_baud_init(self, chan, byte: int) -> bytes:
        inbuf = (ctypes.c_ubyte * 1)(byte)
        outbuf = (ctypes.c_ubyte * 2)()
        ina = SBYTE_ARRAY(1, inbuf)
        outa = SBYTE_ARRAY(2, outbuf)
        self._check(self.dll.PassThruIoctl(ctypes.c_ulong(chan), ctypes.c_ulong(FIVE_BAUD_INIT),
                                           ctypes.byref(ina), ctypes.byref(outa)), "FIVE_BAUD_INIT")
        return bytes(outbuf[:outa.NumOfBytes])

    def write(self, chan, data: bytes, timeout_ms=100):
        m = PASSTHRU_MSG()
        m.ProtocolID, m.TxFlags, m.DataSize = ISO9141, 0, len(data)
        for i, b in enumerate(data):
            m.Data[i] = b
        n = ctypes.c_ulong(1)
        self._check(self.dll.PassThruWriteMsgs(ctypes.c_ulong(chan), ctypes.byref(m), ctypes.byref(n),
                                               ctypes.c_ulong(timeout_ms)), "PassThruWriteMsgs")

    def read(self, chan, timeout_ms=100) -> list[bytes]:
        m = PASSTHRU_MSG()
        n = ctypes.c_ulong(1)
        st = self.dll.PassThruReadMsgs(ctypes.c_ulong(chan), ctypes.byref(m), ctypes.byref(n),
                                       ctypes.c_ulong(timeout_ms))
        if st in (ERR_TIMEOUT, ERR_BUFFER_EMPTY) or n.value == 0:
            return []
        self._check(st, "PassThruReadMsgs")
        if m.RxStatus & (TX_MSG_TYPE | START_OF_MESSAGE) or m.DataSize == 0:
            return []  # loopback copy or start-of-message indication
        return [bytes(m.Data[:m.DataSize])]


class J2534Link(DiagLink):
    name = "J2534 pass-thru"

    def __init__(self, api, log=print):
        """api: a J2534Api (or a stand-in with the same methods)."""
        self.api = api
        self.log = log or (lambda *_: None)
        self.lock = threading.Lock()
        self.addr = None
        self.dev = self.chan = None

    @classmethod
    def from_dll(cls, dll_path: str, log=print):
        return cls(J2534Api(dll_path), log)

    def connect(self, address: int, init_byte: int | None = None) -> None:
        from .kline import odd_parity
        init_byte = odd_parity(address) if init_byte is None else init_byte
        key = getattr(self.api, "dll_path", id(self.api))
        first = _worked.get(key, 0)
        order = [first] + [i for i in range(len(WAKE_VARIANTS)) if i != first]
        errors = []
        with self.lock:
            for n, idx in enumerate(order):
                name, flags, cfg, seven_bit = WAKE_VARIANTS[idx]
                byte = (address & 0x7F) if seven_bit else init_byte
                if n:
                    self._close_port()
                    time.sleep(RETRY_PAUSE)
                    self.log(f"Retrying wake-up: {name}")
                if self.dev is None:
                    self.dev = self.api.open()
                self.chan = self.api.connect(self.dev, ISO9141, ISO9141_NO_CHECKSUM | flags, 10400)
                self.api.set_config(self.chan, {LOOPBACK: 0, DATA_BITS: 0, PARITY: 0})
                if cfg:
                    refused = self.api.try_config(self.chan, cfg)
                    if refused:
                        self.log("Device refused timing settings " + ", ".join(f"{p:#x}" for p in refused))
                self.api.start_pass_all(self.chan)
                self.api.clear_rx(self.chan)
                self.log(f"5-baud init with {byte:02X} (via J2534, {name})")
                try:
                    kb = self.api.five_baud_init(self.chan, byte)
                except (KLineError, OSError) as e:
                    self.log(f"No answer ({e})")
                    errors.append(f"{name}: {e}")
                    continue
                self.log(f"Key bytes: {hexs(kb)}")
                if kb[:2] != bytes([0xD3, 0xB0]):
                    self.log("Warning: unexpected key bytes (expected D3 B0)")
                if idx:
                    self.log(f"Wake-up worked with: {name}")
                _worked[key] = idx
                self.addr = address
                break
            else:
                self._close_port()
                raise KLineError("No response from car. Is the ignition at position II? ("
                                 + "; ".join(errors[:1]) + f"; {len(errors)} wake-up methods tried)")
        if not self.keep_alive():
            self.addr = None
            raise KLineError("ECU did not answer the first request")
        self.log("Comms established")

    def request(self, data: bytes, pre_delay: float | None = None) -> bytes:
        if self.addr is None:
            raise KLineError("Not connected")
        msg = build_message(self.addr, data)
        with self.lock:
            if pre_delay:
                time.sleep(pre_delay)
            self.api.clear_rx(self.chan)
            self.log(f"TX {hexs(msg)}")
            self.api.write(self.chan, msg)
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                for frame in self.api.read(self.chan, 100):
                    self.log(f"RX {hexs(frame)}")
                    if frame == msg:
                        continue  # echo, if the device passes it through
                    if len(frame) >= 4 and frame[2] == self.addr:
                        out = check_reply(frame, self.addr)
                        if is_pending(out, data):
                            self.log("ECU busy, waiting for the answer")
                            deadline = time.monotonic() + 3.0
                            continue
                        return out
            raise KLineError("Comms lost with car, time out")

    def _close_port(self):
        try:
            if self.chan is not None:
                self.api.disconnect(self.chan)
        finally:
            if self.dev is not None:
                self.api.close(self.dev)
            self.chan = self.dev = None
