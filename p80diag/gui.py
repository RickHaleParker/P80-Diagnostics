# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Rick Hale Parker
"""P80 Diagnostics - cross-platform GUI (Tkinter).

Two tabs:
  * Read Codes: talk to a 1996-98 Volvo over a K-line cable. Read and clear
    DTCs, read the ECU ID. Every read in the session is kept and can be
    saved to one file.
  * Code Database: build the DTC tables from a legacy fault-reader EXE,
    open a saved JSON database, or export CSV.
"""
from __future__ import annotations

import csv
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .kline import KLine, KLineError, hexs

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "dtc_db.json")
FITMENT = os.path.join(HERE, "fitment.json")
SETTINGS = os.path.join(os.path.expanduser("~"), ".p80diag.json")
SIM_PORT = "Demo (simulated car)"
IFACE_KKL = "K-line cable (KKL / FTDI)"
IFACE_ELM = "ELM327 / OBDLink"
IFACE_J2534 = "J2534 pass-thru (Windows)"
INTERFACES = [IFACE_KKL, IFACE_ELM, IFACE_J2534]
APP_NAME = "P80 Diagnostics"
from . import __version__  # noqa: E402
DISCLAIMER = ("This app is an independent, open-source enthusiast project. It is not affiliated with, "
              "authorized, or endorsed by Volvo Car Corporation.")

SRS_WARNING = ("SAFETY WARNING - DANGER of personal injury.\n\n"
               "It will not be possible to clear SRS DTCs if the fault has not been corrected. "
               "Doing so will result in loss of communication with the car.\n"
               "Please correct the fault, then read/clear codes again.")
ABS_WARNING = "DO NOT access the ABS module when the car is in motion."

SESSION_HEADER = ["Time", "Interface", "Car", "System", "ECU address", "Action", "Wire byte", "DTC / Field", "Description / Value"]


def list_ports():
    try:
        from serial.tools import list_ports as lp
        return [p.device for p in lp.comports()]
    except ImportError:
        return []


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class Session:
    """Every action on the car in this session, in order."""

    def __init__(self):
        self.reads = []
        self.saved = True

    def add(self, car, entry, action, rows, iface=""):
        r = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "iface": iface, "car": car, "system": entry["title"],
             "address": entry["address"], "action": action, "rows": list(rows)}
        self.reads.append(r)
        self.saved = False
        return r

    def clear(self):
        self.reads = []
        self.saved = True

    def csv_rows(self):
        for r in self.reads:
            for byte, code, text in r["rows"] or [("", "", "")]:
                yield [r["time"], r.get("iface", ""), r["car"], r["system"], f"0x{r['address']:02X}", r["action"], byte, code, text]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        try:
            self._icon = tk.PhotoImage(file=os.path.join(HERE, "icon.png")).subsample(8)
            self.iconphoto(True, self._icon)
        except Exception:  # noqa: BLE001 - icon is optional
            pass
        self.geometry("960x680")
        self.minsize(760, 520)
        self.db = None
        self.fit = load_json(FITMENT, {"markets": [], "car_options": {}, "rules": []})
        self.settings = load_json(SETTINGS, {})
        self.kl: KLine | None = None
        self.entry = None
        self.conn_car = None
        self.conn_iface = None
        self.entries = []
        self.q = queue.Queue()
        self.busy = False
        self.last_io = 0.0
        self.session = Session()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_read = ttk.Frame(nb)
        self.tab_db = ttk.Frame(nb)
        nb.add(self.tab_read, text="Read Codes")
        nb.add(self.tab_db, text="Code Database")
        self._build_read()
        self._build_db()

        self.status = tk.StringVar(value="Not connected")
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", padx=10, pady=(0, 2))
        ttk.Label(self, text=DISCLAIMER, anchor="w", foreground="gray",
                  wraplength=900).pack(fill="x", padx=10, pady=(0, 6))

        menubar = tk.Menu(self)
        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label=f"About {APP_NAME}", command=self.show_about)
        menubar.add_cascade(label="Help", menu=helpmenu)
        self.config(menu=menubar)

        mine = self.settings.get("db_path")
        if mine and os.path.exists(mine) and self.load_db(mine):
            self.log(f"Using your database: {mine}  (Code Database tab → Use built-in database to go back)")
        elif os.path.exists(DEFAULT_DB):
            self.load_db(DEFAULT_DB)
        self.after(50, self._pump)
        self.after(1500, self._keepalive_tick)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- UI build -----------------
    def _build_read(self):
        f = self.tab_read
        top = ttk.Frame(f)
        top.pack(fill="x", pady=4)
        ttk.Label(top, text="Port").grid(row=0, column=0, sticky="w")
        self.port = ttk.Combobox(top, width=28)
        self.port.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Button(top, text="Refresh", command=self.refresh_ports).grid(row=0, column=2, padx=4, sticky="w")
        ifr = ttk.Frame(top)
        ifr.grid(row=0, column=3, columnspan=2, sticky="w")
        ttk.Label(ifr, text="Interface").pack(side="left", padx=(4, 2))
        self.iface = ttk.Combobox(ifr, state="readonly", width=26, values=INTERFACES)
        self.iface.pack(side="left")
        want = self.settings.get("iface")
        self.iface.set(want if want in INTERFACES else IFACE_KKL)
        self.iface.bind("<<ComboboxSelected>>", lambda e: self.refresh_ports(reset=True))
        self.j2534_devices = {}

        ttk.Label(top, text="Car").grid(row=1, column=0, sticky="w", pady=4)
        self.car = ttk.Combobox(top, state="readonly", width=28)
        self.car.grid(row=1, column=1, sticky="w", padx=4)
        self.car.bind("<<ComboboxSelected>>", lambda e: self.on_car())

        opts = ttk.Frame(top)
        opts.grid(row=1, column=2, columnspan=3, sticky="w")
        ttk.Label(opts, text="Body").pack(side="left", padx=(4, 2))
        self.body = ttk.Combobox(opts, state="readonly", width=12)
        self.body.pack(side="left")
        self.body.bind("<<ComboboxSelected>>", lambda e: self.fill_modules())
        ttk.Label(opts, text="Market").pack(side="left", padx=(10, 2))
        self.market = ttk.Combobox(opts, state="readonly", width=20, values=self.fit.get("markets", []))
        self.market.pack(side="left")
        self.market.bind("<<ComboboxSelected>>", lambda e: self.fill_modules())

        ttk.Label(top, text="System").grid(row=2, column=0, sticky="w")
        self.module = ttk.Combobox(top, state="readonly", width=48)
        self.module.grid(row=2, column=1, columnspan=2, sticky="w", padx=4)
        self.module.bind("<<ComboboxSelected>>", lambda e: self.on_module())
        self.show_all = tk.BooleanVar(value=False)
        self.cb_all = ttk.Checkbutton(top, text="Show all systems", variable=self.show_all, command=self.fill_modules)
        self.cb_all.grid(row=2, column=3, sticky="w", padx=8)
        self.modinfo = tk.StringVar()
        ttk.Label(top, textvariable=self.modinfo, foreground="gray").grid(row=3, column=1, columnspan=4, sticky="w", padx=4)

        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=6)
        self.b_conn = ttk.Button(btns, text="Connect", command=self.do_connect)
        self.b_read = ttk.Button(btns, text="Read codes", command=self.do_read)
        self.b_clear = ttk.Button(btns, text="Clear codes", command=self.do_clear)
        self.b_id = ttk.Button(btns, text="Read ECU ID", command=self.do_id)
        self.b_svc = ttk.Button(btns, text="Reset service light", command=self.do_service)
        self.b_disc = ttk.Button(btns, text="Disconnect", command=self.do_disconnect)
        for b in (self.b_conn, self.b_read, self.b_clear, self.b_id, self.b_svc, self.b_disc):
            b.pack(side="left", padx=3)
        ttk.Button(btns, text="Save session…", command=self.save_session).pack(side="right", padx=3)
        ttk.Button(btns, text="New session", command=self.new_session).pack(side="right", padx=3)

        pw = ttk.PanedWindow(f, orient="vertical")
        pw.pack(fill="both", expand=True)
        tf = ttk.Frame(pw)
        self.tree = ttk.Treeview(tf, columns=("byte", "dtc", "desc"), show="tree headings", height=10)
        self.tree.heading("#0", text="Session")
        self.tree.column("#0", width=380, stretch=False)
        for c, t, w in (("byte", "Wire byte", 75), ("dtc", "DTC / Field", 135), ("desc", "Description / Value", 360)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, stretch=(c == "desc"))
        sb = ttk.Scrollbar(tf, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        pw.add(tf, weight=3)
        lf = ttk.Frame(pw)
        self.logbox = tk.Text(lf, height=7, font=("Courier", 9), state="disabled")
        sb2 = ttk.Scrollbar(lf, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=sb2.set)
        self.logbox.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        pw.add(lf, weight=2)
        self.refresh_ports()
        self._set_conn_state(False)

    def _build_db(self):
        f = self.tab_db
        ttk.Label(f, text="The fault-code tables and ECU addresses the app uses. You normally don't need to "
                          "change anything here. To edit codes: Export CSV, change the files in a spreadsheet, "
                          "then Import CSV. DATABASE.md explains the columns.",
                  wraplength=860).pack(anchor="w", pady=6)
        acts = ttk.Frame(f)
        acts.pack(fill="x")
        for i, (label, cmd, desc) in enumerate((
                ("Export CSV…", self.export_csv,
                 "Save the database as two spreadsheet files: every code, and the System menu."),
                ("Import CSV…", self.import_csv,
                 "Load edited spreadsheet files back in, check them, and save as a new database."),
                ("Open JSON database…", self.open_json,
                 "Use a different code database file (.json). The app remembers it."),
                ("Use built-in database", self.use_builtin,
                 "Go back to the database that came with the app."),
                ("Add updated texts…", self.do_updated,
                 "Merge better fault-code descriptions from a database file or a saved updated-texts file."))):
            ttk.Button(acts, text=label, command=cmd, width=22).grid(row=i, column=0, sticky="w", padx=3, pady=2)
            ttk.Label(acts, text=desc, foreground="gray").grid(row=i, column=1, sticky="w", padx=8)
        self.use_updated = tk.BooleanVar(value=True)
        ttk.Checkbutton(acts, text="Show updated texts when available", variable=self.use_updated,
                        command=self.save_settings).grid(row=5, column=0, columnspan=2, sticky="w", padx=3, pady=(4, 0))
        self.dbinfo = tk.StringVar(value="No database loaded")
        ttk.Label(f, textvariable=self.dbinfo).pack(anchor="w", pady=6)
        self.db_tree = ttk.Treeview(f, columns=("table", "sel", "n", "upd", "users"), show="headings")
        for c, t, w in (("table", "Table", 140), ("sel", "Selected by", 130), ("n", "Codes", 60),
                        ("upd", "Updated", 70), ("users", "Used by", 440)):
            self.db_tree.heading(c, text=t)
            self.db_tree.column(c, width=w, stretch=(c == "users"))
        self.db_tree.pack(fill="both", expand=True)

    # ---------------- settings -----------------
    def save_settings(self):
        self.settings.update(port=self.port.get(), iface=self.iface.get(), car=self.car.get(), body=self.body.get(),
                             market=self.market.get(), show_all=self.show_all.get(),
                             use_updated=self.use_updated.get())
        try:
            with open(SETTINGS, "w") as f:
                json.dump(self.settings, f)
        except OSError:
            pass

    # ---------------- database -----------------
    def load_db(self, path, remember=False):
        db = load_json(path, None)
        if not db or "tables" not in db or "menu" not in db:
            messagebox.showerror("Database", f"Could not read {path}")
            return False
        self.db = db
        self._db_loaded(path)
        if remember:
            self.settings["db_path"] = None if os.path.abspath(path) == os.path.abspath(DEFAULT_DB) else path
            self.save_settings()
            self.log(f"Using database: {path}")
        return True

    def _db_loaded(self, where):
        tables, menu = self.db["tables"], self.db["menu"]
        n = sum(len(t["codes"]) for t in tables.values())
        nv = sum(1 for t in tables.values() for c in t["codes"].values() if len(c) > 2 and c[2])
        extra = f", {nv} with updated texts" if nv else ""
        self.dbinfo.set(f"{len(tables)} tables, {n} codes{extra}, {len(menu)} systems  ({os.path.basename(where)})")
        self.db_tree.delete(*self.db_tree.get_children())
        users = {}
        for e in menu:
            users.setdefault(e["table"], []).append(e["title"])
        for tid, t in tables.items():
            sel = f"key {t['select_key']}" if t["select_key"] else "addr " + "/".join(f"{x:02X}" for x in t["select_addr"])
            nv = sum(1 for c in t["codes"].values() if len(c) > 2 and c[2])
            self.db_tree.insert("", "end", values=(tid, sel, len(t["codes"]), nv or "", "; ".join(users.get(tid, []))))
        cars = []
        for e in menu:
            if e["car"] not in cars:
                cars.append(e["car"])
        self.car["values"] = cars
        if cars:
            want = self.settings.get("car")
            self.car.set(want if want in cars else cars[0])
            if self.settings.get("market") in self.market["values"]:
                self.market.set(self.settings["market"])
            elif self.market["values"]:
                self.market.current(0)
            self.show_all.set(bool(self.settings.get("show_all")))
            self.use_updated.set(self.settings.get("use_updated", True))
            self.on_car(restore=True)

    def do_updated(self):
        if not self.db:
            return
        p = filedialog.askopenfilename(
            title="Diagnostic database file (.zip or .mdf) or an updated-texts JSON",
            filetypes=[("Database", "*.zip *.MDF *.mdf"), ("Updated texts JSON", "*.json"), ("All", "*")])
        if not p:
            return
        from . import textdb

        def job():
            if p.lower().endswith(".json"):
                texts = load_json(p, None)
                if not texts:
                    raise ValueError(f"Could not read {p}")
            else:
                texts = textdb.build(p, progress=self.log)
            return textdb.merge(self.db, texts)

        def done(db):
            self.db = db
            self._db_loaded(p)
            s = db.get("updated_texts", {})
            self.log(f"Updated texts: {s.get('improved', 0)} improved, {s.get('added', 0)} added, "
                     f"{s.get('kept', 0)} kept (source code number differed)")
            out = filedialog.asksaveasfilename(title="Save database", defaultextension=".json",
                                               initialfile="dtc_db.json", filetypes=[("JSON", "*.json")])
            if out:
                with open(out, "w", encoding="utf-8") as f:
                    json.dump(self.db, f, indent=1)
                self.load_db(out, remember=True)
        self.run_bg(job, done, "Reading database (this takes several minutes)…")

    def open_json(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if p:
            self.load_db(p, remember=True)

    def use_builtin(self):
        self.load_db(DEFAULT_DB, remember=True)

    def export_csv(self):
        if not self.db:
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="P80_codes.csv",
                                         filetypes=[("CSV", "*.csv")])
        if not p:
            return
        from . import csvdb
        sp = csvdb.systems_path_for(p)
        try:
            n = csvdb.export_codes(self.db, p)
            m = csvdb.export_systems(self.db, sp)
        except OSError as e:
            messagebox.showerror("Export CSV", str(e))
            return
        self.log(f"Exported {n} codes to {p} and {m} systems to {sp}")
        messagebox.showinfo("Export CSV", f"Saved two files:\n\n{os.path.basename(p)}  ({n} codes)\n"
                                          f"{os.path.basename(sp)}  ({m} systems)\n\n"
                                          "Edit them in LibreOffice or Excel, save as CSV, "
                                          "then use Import CSV… to load them back.")

    def import_csv(self):
        if not self.db:
            return
        paths = filedialog.askopenfilenames(title="Pick the codes CSV, the systems CSV, or both",
                                            filetypes=[("CSV", "*.csv"), ("All", "*")])
        if not paths:
            return
        from . import csvdb
        try:
            db, lines = csvdb.import_files(self.db, list(paths))
        except csvdb.ImportError_ as e:
            shown = e.problems[:15]
            more = f"\n…and {len(e.problems) - 15} more" if len(e.problems) > 15 else ""
            for ln in e.problems:
                self.log(f"Import: {ln}")
            messagebox.showerror("Import CSV", "Nothing was changed. Fix these and try again:\n\n"
                                 + "\n".join(shown) + more)
            return
        except OSError as e:
            messagebox.showerror("Import CSV", str(e))
            return
        for ln in lines:
            self.log(f"Import: {ln}")
        if not messagebox.askyesno("Import CSV", "\n".join(lines) + "\n\nSave this as a new database and use it?"):
            return
        out = filedialog.asksaveasfilename(title="Save the new database", defaultextension=".json",
                                           initialfile="my_dtc_db.json", filetypes=[("JSON", "*.json")])
        if not out:
            return
        if os.path.abspath(out) == os.path.abspath(DEFAULT_DB):
            messagebox.showerror("Import CSV", "Save it under a different name; the built-in database stays as it is.")
            return
        with open(out, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=1)
        self.load_db(out, remember=True)

    # ---------------- car / system selection -----------------
    def on_car(self, restore=False):
        car = self.car.get()
        bodies = self.fit.get("car_options", {}).get(car, {}).get("body", [])
        self.body["values"] = bodies
        if bodies:
            self.body.state(["!disabled", "readonly"])
            want = self.settings.get("body") if restore else self.body.get()
            self.body.set(want if want in bodies else bodies[0])
        else:
            self.body.set("")
            self.body.state(["disabled"])
        self.fill_modules()

    def hidden_reason(self, e):
        opts = {"body": self.body.get(), "market": self.market.get()}
        for rule in self.fit.get("rules", []):
            if e["car"] not in rule.get("cars", []):
                continue
            if not any(t.lower() in e["title"].lower() for t in rule.get("titles", [])):
                continue
            if all(opts.get(k) == v for k, v in rule.get("hide_when", {}).items()):
                return rule.get("why", "not fitted")
        return None

    def fill_modules(self):
        if not self.db:
            return
        car = self.car.get()
        all_entries = [e for e in self.db["menu"] if e["car"] == car]
        if self.show_all.get():
            self.entries = all_entries
        else:
            self.entries = [e for e in all_entries if not self.hidden_reason(e)]
        titles = [e["title"] for e in self.entries]
        self.module["values"] = titles
        prev = self.entry["title"] if self.entry else None
        if titles:
            self.module.current(titles.index(prev) if prev in titles else 0)
            self.on_module()
        else:
            self.entry = None
            self.modinfo.set("No systems")
        self.save_settings()

    def on_module(self):
        i = self.module.current()
        if i < 0:
            return
        self.entry = self.entries[i]
        e = self.entry
        info = f"ECU address {e['address']:02X}  ·  init byte {e['init_byte']:02X}  ·  table {e['table']}"
        hidden = sum(1 for x in self.db["menu"] if x["car"] == e["car"] and self.hidden_reason(x))
        if hidden and not self.show_all.get():
            info += f"  ·  {hidden} system(s) hidden as not fitted"
        why = self.hidden_reason(e)
        if why and self.show_all.get():
            info += f"  ·  probably not fitted: {why}"
        self.modinfo.set(info)

    def refresh_ports(self, reset=False):
        if self.iface.get() == IFACE_J2534:
            from . import j2534
            skipped = []
            self.j2534_devices = {(d.get("Name") or d["key"]): d["FunctionLibrary"]
                                  for d in j2534.list_devices(skipped=skipped)}
            for name, why in skipped:
                self.log(f"J2534 device hidden: {name} ({why})")
            ports = list(self.j2534_devices) + [SIM_PORT]
        else:
            ports = list_ports() + [SIM_PORT]
        self.port["values"] = ports
        want = self.settings.get("port")
        if reset or not self.port.get() or self.port.get() not in ports:
            self.port.set(want if want in ports else ports[0])
        if hasattr(self, "use_updated"):
            self.save_settings()

    def make_link(self, entry, kind, port, j2534_dll=None):
        """Open the chosen interface. Returns a DiagLink (not yet connected).
        Runs in the worker thread, so it must not touch Tk widgets."""
        demo = port == SIM_PORT
        if demo:
            from .sim import SimECU
            ecu = SimECU({entry["address"]: self._demo_codes(entry)},
                         ci_type="850" if entry.get("car") == "850" else "newer")
        if kind == IFACE_ELM:
            from .elm import ElmLink
            if demo:
                from .sim import SimElmSerial
                return ElmLink(SimElmSerial(ecu), log=self.log)
            return ElmLink(port, log=self.log)
        if kind == IFACE_J2534:
            from .j2534 import J2534Link
            if demo:
                from .sim import FakeJ2534
                return J2534Link(FakeJ2534(ecu), log=self.log)
            dll = j2534_dll
            if not dll:
                raise KLineError("No J2534 device selected (none registered in Windows?)")
            return J2534Link.from_dll(dll, log=self.log)
        if demo:
            from .sim import SimSerial
            return KLine(SimSerial(ecu), log=self.log)
        return KLine(port, log=self.log)

    # ---------------- session / results -----------------
    def add_read(self, action, rows):
        r = self.session.add(self.conn_car or self.car.get(), self.entry, action, rows,
                             self.conn_iface or self.iface.get())
        label = f"{r['time'][11:]}  {r['system']}  –  {action}"
        for iid in self.tree.get_children():
            self.tree.item(iid, open=False)
        parent = self.tree.insert("", "end", text=label, open=True)
        for byte, code, text in r["rows"] or [("", "", "")]:
            self.tree.insert(parent, "end", values=(byte, code, text))
        self.tree.see(parent)

    def new_session(self):
        if not self.session.saved and not messagebox.askyesno("New session", "Discard the unsaved session?"):
            return
        self.session.clear()
        self.tree.delete(*self.tree.get_children())

    def save_session(self, path=None):
        if not self.session.reads:
            messagebox.showinfo("Save session", "Nothing to save yet.")
            return False
        p = path or filedialog.asksaveasfilename(defaultextension=".csv",
                                                 initialfile=time.strftime("p80_session_%Y%m%d_%H%M.csv"),
                                                 filetypes=[("CSV", "*.csv")])
        if not p:
            return False
        with open(p, "w", newline="") as f:
            w = csv.writer(f, quoting=csv.QUOTE_ALL)
            w.writerow(SESSION_HEADER)
            w.writerows(self.session.csv_rows())
        self.session.saved = True
        self.status.set(f"Session saved to {os.path.basename(p)}")
        return True

    # ---------------- comms (worker thread) -----------------
    def log(self, msg):
        self.q.put(("log", msg))

    def _pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.logbox.configure(state="normal")
                    self.logbox.insert("end", time.strftime("%H:%M:%S ") + val + "\n")
                    self.logbox.see("end")
                    self.logbox.configure(state="disabled")
                elif kind == "call":
                    val()
        except queue.Empty:
            pass
        self.after(50, self._pump)

    def run_bg(self, job, done=None, label="Working…", on_error=None):
        if self.busy:
            # a keep-alive is probably in flight; try again shortly instead of dropping the click
            self.after(100, lambda: self.run_bg(job, done, label, on_error))
            return
        self.busy = True
        self.status.set(label)

        def worker():
            try:
                res, err = job(), None
            except Exception as e:  # noqa: BLE001
                res, err = None, e
            self.last_io = time.monotonic()

            def fin():
                self.busy = False
                if err:
                    self.log(f"ERROR: {err}")
                    if on_error:
                        on_error(err)
                    self.status.set("Error" if not self.kl or self.kl.addr is None else "Connected")
                    messagebox.showerror("Error", str(err))
                elif done:
                    done(res)
            self.q.put(("call", fin))
        threading.Thread(target=worker, daemon=True).start()

    def _set_conn_state(self, on):
        for b in (self.b_read, self.b_clear, self.b_id, self.b_disc):
            b.state(["!disabled"] if on else ["disabled"])
        from . import service
        svc_ok = (on and self.entry and self.entry["address"] == service.CI_ADDR
                  and service.supported(self.conn_car or self.car.get()))
        self.b_svc.state(["!disabled"] if svc_ok else ["disabled"])
        self.b_conn.state(["disabled"] if on else ["!disabled"])
        self.port.state(["disabled"] if on else ["!disabled"])
        self.iface.state(["disabled"] if on else ["!disabled", "readonly"])
        self.cb_all.state(["disabled"] if on else ["!disabled"])
        for w in (self.car, self.module, self.market):
            w.state(["disabled"] if on else ["!disabled", "readonly"])
        if on:
            self.body.state(["disabled"])
        elif self.body["values"]:
            self.body.state(["!disabled", "readonly"])

    def do_connect(self):
        if not self.entry:
            return
        e = self.entry
        if e["table"] and e["table"].startswith("ABS") and not messagebox.askokcancel("ABS Safety Warning", ABS_WARNING):
            return
        self.save_settings()

        kind, port = self.iface.get(), self.port.get()
        dll = self.j2534_devices.get(port)

        def job():
            kl = self.make_link(e, kind, port, dll)
            self.log(f"Interface: {kl.name}")
            try:
                kl.connect(e["address"], e["init_byte"])
            except Exception:
                kl.close()
                raise
            return kl

        def done(kl):
            self.kl = kl
            self.conn_car = self.car.get()
            self.conn_iface = kind
            self._set_conn_state(True)
            self.status.set(f"Connected to {e['title']}")

        def failed(err):
            self.conn_car = self.car.get()
            self.conn_iface = kind
            self.add_read("Connect failed", [("", "", str(err))])
        self.run_bg(job, done, "Initialising ECU (takes ~3 s)…", on_error=failed)

    def _demo_codes(self, e):
        t = self.db["tables"].get(e["table"]) or {"codes": {}}
        known = [int(k, 16) for k in t["codes"] if int(k, 16) != 0]
        unknown = [b for b in range(0xFF, 0, -1) if f"0x{b:02X}" not in t["codes"]][:1]
        return known[:3] + unknown  # the last one is deliberately unknown

    def describe(self, byte):
        t = self.db["tables"].get(self.entry["table"] or "", {"codes": {}})
        hit = t["codes"].get(f"0x{byte:02X}")
        if not hit:
            return ["?", f"Unknown DTC {byte:02X}"]
        code, legacy = hit[0], hit[1]
        updated = hit[2] if len(hit) > 2 else ""
        if self.use_updated.get() and updated:
            return [code, updated]
        return [code, legacy or updated]

    def do_read(self):
        def done(codes):
            rows = [(f"0x{b:02X}", *self.describe(b)) for b in codes] or [("", "", "No DTCs logged")]
            self.add_read("Read codes", rows)
            self.status.set(f"{len(codes)} DTC(s) read from {self.entry['title']}")
            if any(self.describe(b)[0] == "?" for b in codes):
                self.log("A DTC was not recognised. Note the ECU, module ID and software and add it to the table.")
        self.run_bg(lambda: self.kl.read_dtcs(), done, "Reading codes…",
                    on_error=lambda err: self.add_read("Read codes failed", [("", "", str(err))]))

    def do_clear(self):
        e = self.entry
        if e["table"] and e["table"].startswith("SRS"):
            if not messagebox.askokcancel("SRS Safety Warning", SRS_WARNING):
                return
        elif not messagebox.askyesno("Clear codes", f"Clear all DTCs in {e['title']}?"):
            return

        def done(r):
            self.log(f"Clear reply: {hexs(r)}")
            self.add_read("Clear codes", [("", "", f"Clear sent. Reply: {hexs(r) or 'none'}")])
            self.status.set("Clear sent. Read codes again to check.")
        self.run_bg(lambda: self.kl.clear_dtcs(), done, "Clearing codes…",
                    on_error=lambda err: self.add_read("Clear codes failed", [("", "", str(err))]))

    def do_id(self):
        def done(info):
            self.log(f"ECU ID: hardware {info['hw_part']} rev {info['hw_rev']}, "
                     f"software {info['sw_part']} rev {info['sw_rev']}")
            self.add_read("Read ECU ID", [
                ("", "Hardware part no.", info["hw_part"]),
                ("", "Hardware rev", info["hw_rev"]),
                ("", "Software part no.", info["sw_part"]),
                ("", "Software rev", info["sw_rev"]),
                ("", "Raw reply", info["raw"]),
            ])
            self.status.set(f"ECU ID read from {self.entry['title']}")
        self.run_bg(lambda: self.kl.read_id(), done, "Reading ECU ID…",
                    on_error=lambda err: self.add_read("Read ECU ID failed", [("", "", str(err))]))

    def do_service(self):
        from . import service
        car = self.conn_car or self.car.get()
        if not messagebox.askyesno("Service Light Reset",
                                   f"Reset the service light on this {car}?\n\nAre you sure?"):
            return
        kl = self.kl

        def done(res):
            note = ""
            if res["refused"]:
                note = f" The instrument refused step(s) {', '.join(map(str, res['refused']))}; check the light."
            self.log(f"Service light reset done ({res['type']}).{note}")
            self.add_read("Service light reset", [("", "Instrument", res["type"]),
                                                  ("", "Result", "Reset sent. Start the car to check." + note)])
            messagebox.showinfo("Service Light Reset", "Service light reset sent.\nStart the car to check." + note)
            self._after_session_end(kl)

        def failed(err):
            self.add_read("Service light reset", [("", "Result", f"Failed: {err}")])
            self._after_session_end(kl)
        self.run_bg(lambda: service.reset(kl, car, self.log), done, "Resetting service light…", on_error=failed)

    def _after_session_end(self, kl):
        """The reset ends the ECU session (A0). Close the link."""
        def job():
            if kl:
                kl.close()

        def done(_):
            self.kl = None
            self._set_conn_state(False)
            self.status.set("Not connected")
        self.run_bg(job, done, "Disconnecting…")

    def do_disconnect(self):
        kl = self.kl

        def job():
            if kl:
                kl.close()

        def done(_):
            self.kl = None
            self._set_conn_state(False)
            self.status.set("Not connected")
        self.run_bg(job, done, "Disconnecting…")

    def _keepalive_tick(self):
        if self.kl and self.kl.addr is not None and not self.busy and time.monotonic() - self.last_io > 1.5:
            kl = self.kl
            self.busy = True

            def worker():
                try:
                    err = None if kl.keep_alive() else "keep-alive not acknowledged"
                except Exception as e:  # noqa: BLE001
                    err = str(e)
                self.last_io = time.monotonic()

                def fin():
                    self.busy = False
                    if err:
                        self.log(f"Comms lost: {err}")
                        self.add_read("Comms lost", [("", "", err)])
                        self.kl = None
                        self._set_conn_state(False)
                        self.status.set("Comms lost")
                self.q.put(("call", fin))
            threading.Thread(target=worker, daemon=True).start()
        self.after(500, self._keepalive_tick)

    def show_about(self):
        messagebox.showinfo(f"About {APP_NAME}",
                            f"{APP_NAME} {__version__}\n\nFault code reader for 1996–98 Volvo cars over K-line.\n\n"
                            "Copyright (C) 2026 Rick Hale Parker. Free software under the GNU General Public "
                            "License v3 or later, with NO WARRANTY. See the LICENSE file.\n\n"
                            f"{DISCLAIMER}")

    def on_close(self):
        if not self.session.saved:
            ans = messagebox.askyesnocancel(APP_NAME, "Save this session before closing?")
            if ans is None:
                return
            if ans and not self.save_session():
                return
        self.save_settings()
        if self.kl:
            try:
                self.kl.close()
            except Exception:  # noqa: BLE001
                pass
        self.destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
