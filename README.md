# P80 Diagnostics

> **Disclaimer:** This app is an independent, open-source enthusiast project. It is not affiliated with, authorized, or endorsed by Volvo Car Corporation.

A cross-platform fault code reader for 1996–98 Volvo cars (850, S/V70, C70, 900/S/V90, S/V40).

It has two tabs:

- **Read Codes:** talks to the car over K-line. Reads and clears DTCs, reads the ECU ID, resets the service light and programs IMMO3 immobilizer keys (’99–’00 cars). Every action in the session is kept, and **Save session…** writes them all to one CSV file.
- **Code Database:** the fault-code tables and ECU addresses the app uses. A built-in database loads at start. You can export it to spreadsheet files, edit them, and import them back as your own database. See [DATABASE.md](DATABASE.md).

## Install

**Linux Mint / Ubuntu / Debian.** Double-click `p80-diagnostics_1.2.1_all.deb`, or run `sudo apt install ./p80-diagnostics_1.2.1_all.deb`. It pulls in `python3-tk` and `python3-serial`, and adds **P80 Diagnostics** to the menu (Utilities). You can also start it with `p80diag` in a terminal. To use a USB cable, your user must be in the `dialout` group: `sudo usermod -aG dialout $USER`, then log out and in. Remove with `sudo apt remove p80-diagnostics`.

**Windows.** Run `P80Diagnostics-1.2.1-Setup.exe`. It installs to Program Files and adds a Start menu entry (and a desktop icon if you tick it). No Python needed. The program is 32-bit, so J2534 drivers work. Windows may warn that the file is from an unknown publisher, because it isn't code-signed: click **More info → Run anyway**.

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

A ready-made `p80diag/dtc_db.json` is included. The app loads it at start. [DATABASE.md](DATABASE.md) explains its format and how to change it.

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

## Key programming (IMMO3)

> **UNTESTED ON A REAL CAR.** This is IMMO3 only — the immobilizer on **1999–2000** S/V70, C70 and S/V40 cars. It does **not** apply to the IMMO1/2 immobilizer on 1996–98 cars, or to later CEM cars. The developer's own P80 is a 1998 C70 (pre-IMMO3), so this feature has only ever run against the built-in Demo immobox. Use it at your own risk: a mistake can stop the car starting.

Connect to the **Immobilizer** system on an S/V70, C70 or S/V40, then click **Key programming…**. The button only appears for those cars when connected to the immobilizer (address `0x41`). A dedicated window opens and runs the mandatory lockout-timer check first.

- **You supply the PIN.** The app does not read it. The 4-digit PIN comes from a VIDA memory dump (see below). Type it in; the window shows the exact bytes it will send so you can check them against your dump.
- **Lockout timer.** A wrong PIN arms an anti-scan lockout (about 30 units, counting down roughly one unit every 10 seconds, so ~5 minutes). While it is armed **every** unlock is refused (`7F A3 33`), even with the correct PIN. The app reads the timer (`A5 08 01`) before it will send any PIN, and it **never retries** a PIN by itself. If it arms, cycle the ignition to position II and wait for it to clear.
- **Unlock.** `A3 02 00 <pinHi> <pinLo>` → `E3 02`. The leading `00` is a fixed selector, not part of the PIN.
- **Add key (next slot).** Non-destructive. Teaches the key now in the ignition into the next free slot (`B2 01` → `F2 01`). A `7F B2 22` reply means that key is already stored — expected when re-adding, and it confirms the unlock worked.
- **Erase ALL keys + learn.** **Destructive** (`B2 18`). It wipes every stored key and learns only the key now in the ignition into slot 1. Every other key must be added back afterward, each with that key in the ignition. The app makes you type `ERASE` to confirm. If the re-learn fails, the car will not start.
- **Close.** Sends `A0` and disconnects, so there is never a second 5-baud init in the same key cycle (a second init gives `BUS INIT: ERROR`; cycle the ignition to retry).

You can try the whole flow against the Demo immobox: pick **Demo (simulated car)**, connect to a C70 (or S/V70, S/V40) immobilizer, and open **Key programming…**. The demo's correct PIN is **1234**.

### Getting the PIN from VIDA

The app does not obtain the PIN; this is a separate step done outside it, once, in VIDA:

1. In VIDA, start the immobilizer key-programming function for the car.
2. Dump the memory of the `VIDA.Desktop` process while it runs.
3. In the dump, find the DiCE send buffer bytes `86 41 13 A3 02 00 <pin>`. The two bytes after `00` are the PIN (BCD), e.g. `12 34` = PIN 1234.

Keep the PIN safe; it is specific to the car.

## Car options and hidden systems

Pick your car, then its **Body** and **Market**. Systems your car can't have are hidden from the System menu. For example, a C70 coupe hides the convertible's Cab Control Unit and Roll Over Protection.

The rules live in `p80diag/fitment.json` and can be edited. Tick **Show all systems** to ignore them.

The app remembers your port, car, body and market in `.p80diag.json` in your home folder.

## Sessions

Every connect attempt, read, clear, ECU ID read and service light reset, and every failed one, is added to the session list, with its time and system name. The newest one is expanded. **Save session…** writes everything to one CSV. **New session** starts over. The app asks before closing if the session hasn't been saved.

## Updated texts

The included database has updated fault-code texts merged in. They cover 14 of the 20 code tables (immobilizer, seats, climate, alarm, keyless, RTI, roll-over, Cab Control Unit, heater, EMS 2000, MSA 15.7, S/V40 ABS and DSA). 371 texts were replaced and 38 extra codes were added. Updated texts are only used where the code number matches.

Untick **Show updated texts when available** on the Code Database tab to see the original texts instead. Saved CSVs keep both.

**Add updated texts…** can rebuild them from a SQL Server diagnostic database file (`.zip` containing the `.mdf`, or the `.mdf` itself). No SQL Server is needed; the app reads the file directly. It takes several minutes.

No updated texts yet for Motronic 4.4, 850-type ABS, 850-type Combined Instrument, SRS, the S/V40 Combined Instrument or the gearbox.

## Demo mode

Pick **Demo (simulated car)** as the port. A pretend ECU answers with three known codes and one unknown byte. On the immobilizer of an S/V70, C70 or S/V40 it also behaves as an IMMO3 immobox, so **Key programming…** can be tried end to end (lockout, unlock, add, erase); its correct PIN is **1234**.

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
| IMMO3 lockout (0x41) | `A5 08 01` → `E5 08 NN`. `NN` must be `00`; non-zero = armed (~1 unit/10 s) |
| IMMO3 open session | `AE 01` → `EE 01` |
| IMMO3 unlock | `A3 02 00 <pinHi> <pinLo>` → `E3 02`. Errors: `7F A3 33` locked out, `7F A3 12` bad format |
| IMMO3 add key | `B2 01` → `F2 01`. `7F B2 22` = key already in table (expected on re-add) |
| IMMO3 erase + learn | `B2 18` (destructive: wipes all keys, learns slot 1) |
| IMMO3 key count / status | `B9 05`, `A5 11 01` |
| Busy | `7E <service> xx` = request received, answer follows (C70 instrument on `B9 F0`). The app waits for the real answer |
| End | `A0` |

ECU addresses: `01` ABS, `02` DSA, `11` MSA 15.7 diesel, `18` heater, `29` ECC, `2D` alarm/keyless, `2E`/`2F` seats, `41` immobilizer, `42` CCU, `51` instrument, `58` SRS, `59` ROP, `62` RTI, `6E` gearbox, `7A` Motronic 4.4 / EMS 2000.

## Status and caveats

- Tested on a 1998 C70 coupe (North America) with the K-line cable, OBDLink EX and DiCE. Works on the fitted modules.
- The ECU ID is shown as Volvo hardware and software part numbers with revisions, plus the raw reply.
- Clearing SRS codes with the fault still present can drop communication. The app warns first.
- Don't open the ABS module while the car is moving.
- **IMMO3 key programming is untested on a real car.** The protocol was decoded from a matthewsvolvosite writeup, verified by its author on a 2000 V70XC (Bosch immobox 9459037 / 9459074, EEPROM 93C56). Here it has only run against the Demo immobox. A 2000 V70XC owner is the candidate first real-car tester. Treat the feature as experimental until a real car confirms it.

## License

Copyright (C) 2026 Rick Hale Parker.

The program code is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. It comes with NO WARRANTY. See `LICENSE` for the full text.
