# P80 Diagnostics

> **Disclaimer:** This app is an independent, open-source enthusiast project. It is not affiliated with, authorized, or endorsed by Volvo Car Corporation.

A cross-platform fault code reader for 1996–98 Volvo cars (850, S/V70, C70, 900/S/V90, S/V40).

It has two tabs:

- **Read Codes:** talks to the car over K-line. Reads and clears DTCs, reads the ECU ID and resets the service light. Every action in the session is kept, and **Save session…** writes them all to one CSV file.
- **Code Database:** the fault-code tables and ECU addresses the app uses. A built-in database loads at start. You can open a different JSON database, export all codes to CSV, or add updated fault-code texts.

## Install

**Linux Mint / Ubuntu / Debian.** Double-click `p80-diagnostics_1.0.0_all.deb`, or run `sudo apt install ./p80-diagnostics_1.0.0_all.deb`. It pulls in `python3-tk` and `python3-serial`, and adds **P80 Diagnostics** to the menu (Utilities). You can also start it with `p80diag` in a terminal. To use a USB cable, your user must be in the `dialout` group: `sudo usermod -aG dialout $USER`, then log out and in. Remove with `sudo apt remove p80-diagnostics`.

**Windows.** Run `P80Diagnostics-1.0.0-Setup.exe`. It installs to Program Files and adds a Start menu entry (and a desktop icon if you tick it). No Python needed. The program is 32-bit, so J2534 drivers work. Windows may warn that the file is from an unknown publisher, because it isn't code-signed: click **More info → Run anyway**.

**From source (any system).** Needs Python 3.8+ with Tk (included with the python.org installers on Windows and macOS; on Linux install `python3-tk`).

```
pip install -r requirements.txt
python run_p80diag.py
```

On Windows, double-click `run_p80diag.bat`; it uses 32-bit Python 3.11 (`py -3.11-32`).

### Building the installers

- **.deb:** `sh packaging/linux/build_deb.sh` → `dist/p80-diagnostics_<version>_all.deb`.
- **Windows:** double-click `packaging\windows\build_windows.bat` on a Windows PC with 32-bit Python 3.11 (`winget install Python.Python.3.11 --architecture x86`) and Inno Setup 6 (`winget install JRSoftware.InnoSetup`). It makes `dist\P80Diagnostics.exe` (one file) and `dist\P80Diagnostics-<version>-Setup.exe`.
- **macOS:** not done yet.

The version number is in `p80diag/__init__.py`.

A ready-made `p80diag/dtc_db.json` is included. The app loads it at start.

The tables were originally extracted from a legacy VB6 p-code fault-reader EXE. That extractor is kept as a command-line tool only (see Command line).

## Interfaces

Pick one under **Interface** on the Read Codes tab.

| Interface | Platforms | Status |
|---|---|---|
| **K-line cable (KKL / FTDI)** | Windows, Linux, macOS | Tested on a 1998 C70 |
| **ELM327 / OBDLink** (e.g. OBDLink EX) | Windows, Linux, macOS | Tested on a 1998 C70 |
| **J2534 pass-thru** (e.g. Mongoose, DiCE) | Windows only | DiCE tested on a 1998 C70. Mongoose JLR failed at wake-up; retries added, needs a retest |

Every interface has a **Demo (simulated car)** port for trying it without a car.

**K-line cable.** A "dumb" USB (FTDI) or RS-232 to K-line adapter on OBD pin 7. The cable must echo what it sends; the app checks this first ("Interface Error" if not). The 5-baud wake-up is sent by holding the serial Break line for 200 ms per bit, then the link runs at 10400 baud. On Windows, set the FTDI port's **Latency Timer to 1 ms** (Device Manager → port → Properties → Port Settings → Advanced).

**ELM327 / OBDLink.** Pick the adapter's serial port; the app finds its baud rate. It uses ISO 9141 mode with a custom header per message, the car's wake-up address (`AT IIA`), relaxed key-byte checking (`AT KW0`) and replies addressed to the tester (`AT SR 13`). Cheap ELM327 clones often have weak K-line support. The app uses a fixed 600 ms reply timeout and retries the wake-up and the first request once. Long replies split over several lines are joined back together. If an adapter cuts a reply short (for example the ECU ID), the app says so.

**J2534.** Pick the device from the list; it comes from the Windows registry. Only devices that list K-line (ISO9141) support and whose driver file exists are shown; the log says which were hidden and why. The app opens an ISO 9141 channel at 10400 baud and uses the device's own 5-baud wake-up. If the wake-up fails, it retries with a longer key-byte wait, then "K-line only", then the 7-bit address, and remembers what worked. Most J2534 drivers are 32-bit, so install **32-bit Python** for this. The app says so if the driver won't load.

## Service light reset

Connect to the **Combined Instrument**, then click **Reset service light** and confirm. Works on the 850, S/V70, C70 and S/V40 (not the 900 / S/V90). On the 850, S/V70 and C70 the app detects the 850-type or newer instrument by itself. The reset ends the session, so the app disconnects afterwards. Start the car to check the light. Tested on a 1998 C70: the light went off.

## Car options and hidden systems

Pick your car, then its **Body** and **Market**. Systems your car can't have are hidden from the System menu. For example, a C70 coupe hides the convertible's Cab Control Unit and Roll Over Protection. A North American C70 hides the immobilizer.

The rules live in `p80diag/fitment.json` and can be edited. Tick **Show all systems** to ignore them.

The app remembers your port, car, body and market in `.p80diag.json` in your home folder.

## Sessions

Every connect attempt, read, clear, ECU ID read and service light reset, and every failed one, is added to the session list, with its time and system name. The newest one is expanded. **Save session…** writes everything to one CSV. **New session** starts over. The app asks before closing if the session hasn't been saved.

## Updated texts

The included database has updated fault-code texts merged in. They cover 14 of the 20 code tables (immobilizer, seats, climate, alarm, keyless, RTI, roll-over, Cab Control Unit, heater, EMS 2000, MSA 15.7, S/V40 ABS and DSA). 371 texts were replaced and 38 extra codes were added. Updated texts are only used where the code number matches.

Untick **Show updated texts when available** on the Code Database tab to see the original texts instead. Saved CSVs keep both.

**Add updated texts…** can rebuild them from a SQL Server diagnostic database file (`.zip` containing the `.mdf`, or the `.mdf` itself). No SQL Server is needed; the app reads the file directly. It takes several minutes. From the command line:

```
python -m p80diag.textdb database.zip -o updated_texts.json
python -m p80diag.textdb --merge updated_texts.json p80diag/dtc_db.json
```

No updated texts yet for Motronic 4.4, 850-type ABS, 850-type Combined Instrument, SRS, the S/V40 Combined Instrument or the gearbox.

## Demo mode

Pick **Demo (simulated car)** as the port. A pretend ECU answers with three known codes and one unknown byte.

## Command line

Rebuild the code database from the legacy fault-reader EXE (only needed if `dtc_db.json` is lost):

```
python -m p80diag.extract legacy.exe -o dtc_db.json --csv P80_DTC_wire_map.csv
python -m p80diag.textdb --merge updated_texts.json dtc_db.json
```

## Protocol

| Step | Detail |
|---|---|
| Cable check | 9600 8N1, send `55`, expect `55` echo within 100 ms |
| Wake-up | wait 500 ms, 5-baud init byte (address + odd parity, e.g. `41`→`C1`), 200 ms/bit via Break |
| Handshake | 10400 baud. ECU: `55 D3 B0` within 1.5 s. Tester waits 30 ms, sends `4F`. ECU answers `~init` |
| Frame | `[80+L] [target] [13] data… [sum]`. L = bytes after target, not counting the sum. Reply: `[80+L] [13] [ecu] data… [sum]` |
| Timing | 50 ms before each request, 5 ms between bytes |
| Keep-alive | `A1` → `E1` |
| Read DTCs | `AE 01` → `EE 01 b1 b2 …` (one byte per DTC) |
| Clear DTCs | `AF 01` |
| ECU ID | `B9 F0` → `F9 F0 00` + hardware part no. (4 bytes BCD) + rev (3 ASCII) + software part no. (4 bytes BCD) + rev (3 ASCII) |
| Service reset, newer CI (0x51) | `B9 13` → `F9…`, `AF 04`, `B8 14 0A 0F`, `B2 30`, `A0` |
| Service reset, 850-type CI | `B9 13` → `7F B9 12`, `B0 30`, `A0` |
| Service reset, S/V40 CI | `B9 06`, `B9 08`, `B9 07`, `B2 24`, `B8 06 00`, `B8 08 01 00`, `B8 07 01 00`, `A0` |
| Busy | `7E <service> xx` = request received, answer follows (C70 instrument on `B9 F0`). The app waits for the real answer |
| End | `A0` |

ECU addresses: `01` ABS, `02` DSA, `11` MSA 15.7 diesel, `18` heater, `29` ECC, `2D` alarm/keyless, `2E`/`2F` seats, `41` immobilizer, `42` CCU, `51` instrument, `58` SRS, `59` ROP, `62` RTI, `6E` gearbox, `7A` Motronic 4.4 / EMS 2000.

## Status and caveats

- Tested on a 1998 C70 coupe (North America) with the K-line cable, OBDLink EX and DiCE. Works on the fitted modules.
- The ECU ID is shown as Volvo hardware and software part numbers with revisions, plus the raw reply.
- Clearing SRS codes with the fault still present can drop communication. The app warns first.
- Don't open the ABS module while the car is moving.

## License

Copyright (C) 2026 Rick Hale Parker.

The program code is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. It comes with NO WARRANTY. See `LICENSE` for the full text.
