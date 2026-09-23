# P80 Diagnostics – To do

1. [ ] Teensy 4.1 interface (later). Define a simple serial protocol for the board and add the matching backend.
2. [ ] Mongoose JLR: all 4 wake-up methods fail (23 Sep run). Park it unless new info turns up; the DiCE covers J2534.
3. [ ] macOS: mark it "untested" in the README, then test on a Mac (see item 5).
   - Python from python.org (includes Tk), `pip3 install pyserial`, port `/dev/cu.usbserial-…`.
   - K-line cable: check the handshake timing with Apple's built-in FTDI driver (latency can't be set to 1 ms). Try FTDI's own Mac driver if connects fail.
   - ELM327 / OBDLink should be unaffected (the adapter does the timing). J2534 is Windows only.
4. [ ] Windows installer: run `packaging\windows\build_windows.bat` on the laptop, then install and test `P80Diagnostics-1.0.0-Setup.exe` (all three interfaces).
5. [ ] macOS version: someone with a Mac to build (PyInstaller .app) and test.

## Done

- [x] Database editing (23 Sep 2026): **Export CSV…** writes a codes file and a systems file; **Import CSV…** checks them (row numbers for any problem, nothing changed on error), saves a new database and uses it. The app remembers the chosen database; **Use built-in database** goes back. `DATABASE.md` is the reference for the JSON and CSV formats.
- [x] README: removed the rebuild-from-legacy-EXE commands and the extraction note (23 Sep 2026).

- [x] Installers (23 Sep 2026), version 1.0.0, with an app icon:
  - Linux `.deb` (`packaging/linux/build_deb.sh`): menu entry, `p80diag` command, depends on python3-tk and python3-serial. Install, run and remove tested.
  - Windows kit (`packaging/windows/build_windows.bat` + Inno Setup script): 32-bit one-file `P80Diagnostics.exe` and a Setup installer. Must be built on Windows.
  - `run_p80diag.bat` starts the source version with 32-bit Python.

- [x] License (23 Sep 2026): GPL v3 or later. `LICENSE` file, header in every source file, notice in Help → About and the README.

- [x] Car run 23 Sep 2026 11:45 (OBDLink): Combined Instrument ECU ID works (9148930 rev A, software 9168133 rev 23; 3 of 3 reads). Motronic 4.4 connects, reads codes (none) and ECU ID. First connect of the day still needed a second try.

- [x] Read ECU ID on the Combined Instrument (23 Sep 2026): it first answers "busy" (7E B9 23), then the ID. All three interfaces now wait for the real answer. C70 instrument: hardware 9148930 rev A, software 9168133 rev 23.
- [x] Failed reads, clears and ECU ID reads are now saved in the session too.

- [x] Service light reset tested on the 1998 C70 (23 Sep 2026, K-line cable): newer instrument, light went off.

- [x] ECU ID shown as Volvo part numbers (23 Sep 2026): hardware part no. + rev, software part no. + rev, plus the raw reply.
- [x] ELM on Motronic 4.4 (23 Sep 2026): fixed 600 ms reply timeout (AT AT0, AT ST 96) instead of the adaptive one; the first request is retried once. Needs a car test.
- [x] ELM port left open after a failed connect (23 Sep 2026): the port is now always closed, so the next try doesn't get "Access is denied".
- [x] ELM flaky first connect (23 Sep 2026): the baud search runs twice, and the wake-up is retried once after 2.5 s.

- [x] Car run 23 Sep 2026 (1998 C70): ECU ID read from ECC, both seats, SRS, ABS, gearbox, alarm (OBDLink) and Motronic (K-line). CI codes read: CI-131, CI-222 (speed signal). RTI gives no answer, probably not fitted.

- [x] ELM / OBDLink (23 Sep 2026): baud rates the port refuses are skipped (fixes "OSError 22, The parameter is incorrect"). Long replies split over several lines are joined; if the adapter cuts one short, the app says so.
- [x] Mongoose JLR (23 Sep 2026): if the wake-up fails, the app retries with a longer key-byte wait, then "K-line only", then the 7-bit address. The method that worked is logged and tried first next time. Needs a car test (item 2).
- [x] Service light reset (23 Sep 2026): decoded from the legacy program for 850, S/V70, C70 and S/V40. **Reset service light** button, enabled when connected to the Combined Instrument; asks "Are you sure?". Picks the 850-type or newer instrument sequence by itself. Needs a car test (item 4).
- [x] J2534 device list (23 Sep 2026): only devices with K-line (ISO9141) support and an existing driver file are shown. Hidden ones are listed in the log with the reason.

- [x] Car test of all interfaces, 1998 C70, 23 Sep 2026: K-line cable, OBDLink EX and DiCE (J2534) work. ELM read 3 ECC codes (ECC-311, 312, 414). Mongoose JLR fails (retries added, see item 2).

- [x] Code Database tab clean-up (22 Sep 2026): removed "Build from EXE…" (extractor is command-line only); each remaining button has a one-line description; intro text and README updated.

- [x] Interface choice (22 Sep 2026): K-line cable, ELM327 / OBDLink, J2534 pass-thru. Each has a demo mode. Session files record the interface used.

- [x] Disclaimer added (22 Sep 2026): shown at the bottom of the window, in Help → About, and at the top of the README.
- [x] Removed every reference to the text source's name from the app, database, CSVs, README and tests. The feature is now called "updated texts" (module `textdb.py`).
- [x] Checked two Scribd documents for cleaner descriptions (22 Sep 2026). Not usable: both are engine-only P-code conversion tables for 1999+ cars with 4-digit ECM codes, not the 1996–98 K-line systems.
- [x] Updated fault-code texts (22 Sep 2026). 371 texts improved and 38 codes added across 14 tables. Toggle on the Code Database tab; "Add updated texts…" rebuilds them from a SQL Server database file.
- [x] Renamed the app to **P80 Diagnostics** (22 Sep 2026). Package `p80diag`, launcher `run_p80diag.py`. Tab renamed to **Code Database**.
- [x] "Read ECU ID" results show in the results window and are saved with the session.
- [x] Fixed: results saved under the wrong system name. The system name is now stored at read time.
- [x] Every action in a session is kept (connect failures, reads, clears, ECU ID). **Save session…** writes all of it to one CSV. **New session** starts over. The app asks before closing unsaved work.
- [x] Hide systems a car doesn't have. Car options Body and Market; rules in `p80diag/fitment.json`; **Show all systems** overrides. Settings are remembered.
- [x] Clicks during a keep-alive were ignored (e.g. Disconnect). They now wait and run.
- [x] First real-car test, 1998 C70 coupe (NA), 22 Sep 2026: works on the fitted modules. Immobilizer, CCU and ROP give no response because they aren't fitted.
