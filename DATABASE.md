# The code database

P80 Diagnostics gets all its fault codes and its System menu from one file, `p80diag/dtc_db.json`. This page explains how to change it: first the easy way with a spreadsheet, then the file format itself.

## Changing it with a spreadsheet

You don't need to touch the JSON file. On the **Code Database** tab:

1. Click **Export CSV…** and pick a name, for example `my_codes.csv`. The app saves two files:
   - `my_codes.csv`: every fault code, one per row.
   - `my_codes_systems.csv`: every entry in the System menu, one per row.
2. Open them in LibreOffice Calc or Excel and make your changes. See the examples below.
3. Save them as **CSV** (keep the format when asked).
4. Click **Import CSV…** and pick one file or both.
5. The app checks every row. If something is wrong, it lists the row numbers and changes nothing. Fix them and import again.
6. If all is well, it shows what changed and asks you to save a new database, for example `my_dtc_db.json`. It uses the new database right away and every time it starts after that.

To go back to the database that came with the app, click **Use built-in database**. The built-in database is never changed.

You can share your `.json` file. Others load it with **Open JSON database…**.

### Codes file (`my_codes.csv`)

| Column | Used on import? | Meaning |
|---|---|---|
| Table | **yes** | Which code list the row belongs to, for example `IMM:41` |
| Selected by | only for a new table | `key NAME` or `addr 0xNN` (see Tables below) |
| Message address, 5-baud init byte, Menu entries | no | For your information only |
| Wire byte (hex) | **yes** | The byte the ECU sends for this fault, for example `0x2F` (`2F` also works; always hex) |
| Wire byte (dec) | no | The same byte in decimal |
| DTC | **yes** | The code shown to the user, for example `ECC-312` |
| Description | yes | The text the app shows. If you change it, the change is kept |
| Original text | **yes** | The text from the original tables |
| Updated text | **yes** | A better text, if there is one. Leave empty if not |

Rules:

- A table in the file gets **exactly** the rows in the file. Delete a row to remove that code.
- Tables that aren't in the file stay as they are. You can import just the rows of one table.
- The same wire byte can't appear twice in one table.
- Every row needs a DTC and at least one text.

### Systems file (`my_codes_systems.csv`)

| Column | Meaning |
|---|---|
| Car | Car in the **Car** menu: `850`, `S/V70`, `C70`, `900 & S/V90`, `S/V40`, or a new name |
| System | Name in the **System** menu, for example `C 70 Electronic Climate Control` |
| ECU address | The module's K-line address, `0x01`–`0x7F` |
| 5-baud init byte | The wake-up byte. Leave empty and the app uses the address with odd parity, which is right for almost every module |
| Selected by key | For your information only |
| Table | Which code table this system uses. It must exist in the codes file or the database |

The systems file replaces the **whole** System menu, so keep all the rows you want.

Import the codes file and the systems file together when you add a new table, so the new system can point at it.

## Examples

**Fix a description.** In the codes file, find the row (filter the Table column, then look at DTC). Type the new text in **Description**. Import.

**Add a code.** Add a row with the Table, Wire byte, DTC and Original text. Import.

**Add a new module.**

1. In the codes file, add its codes with a new table name, for example `ABC:33`, and put `addr 0x33` in **Selected by** on the first row.
2. In the systems file, add a row: Car `C70`, System `C 70 My Module`, ECU address `0x33`, Table `ABC:33`.
3. Import both files together.

## The JSON file

For programmers. The CSV route above writes this for you.

```json
{
  "source": "where the data came from",
  "edited": "2026-09-23 14:00",
  "tables": {
    "IMM:41": {
      "id": "IMM:41",
      "prefix": "IMM",
      "select_key": null,
      "select_addr": [65],
      "codes": {
        "0x01": ["IMM-112", "Internal Fault", "Control module Internal fault"],
        "0x34": ["IMM-121", "PIN cannot be programmed"]
      }
    }
  },
  "menu": [
    {"car": "C70", "title": "C 70 Immobilizer 2/3", "address": 65, "init_byte": 193,
     "key": null, "table": "IMM:41"}
  ],
  "updated_texts": {"tables": ["IMM:41"], "improved": 18, "added": 0, "kept": 0}
}
```

### Tables

`tables` maps a table id to one code list.

- **id**: the same as the key. By convention `PREFIX:SELECTOR`, for example `IMM:41` or `EFI:MOT-4.4`.
- **prefix**: the part before the colon. Informational.
- **select_key** / **select_addr**: how the original program picked this table, by a module key string or by ECU address (decimal numbers in JSON). Shown on the Code Database tab; the app doesn't use them to read codes.
- **codes**: maps the wire byte (a string like `"0x2F"`, two upper-case hex digits) to a list:
  1. the DTC, for example `"ECC-312"`;
  2. the original text;
  3. optional: the updated text. Leave it out, or use `""`, if there is none.

  The app shows the updated text when there is one and **Show updated texts when available** is ticked; otherwise the original text. A byte the ECU sends that isn't in the table is shown as unknown.

### Menu

`menu` is the System menu, in order. Each entry:

- **car**: the Car menu name. Cars appear in the order they first occur.
- **title**: the System menu name. Hiding rules in `fitment.json` match on words in this title.
- **address**: the ECU address used in every message (decimal in JSON, `0x01`–`0x7F`).
- **init_byte**: the 5-baud wake-up byte (decimal). Normally the address with odd parity: `0x41` → `0xC1` (193).
- **key**: informational (the original program's module key), or `null`.
- **table**: the id of the code table to use. It must exist in `tables`.

### Other fields

- **source**, **edited**: notes about where the file came from. Not used by the app.
- **updated_texts**: statistics from the last updated-texts merge, shown in the log. Optional.

The app refuses a file without `tables` and `menu`.

## Related file: fitment.json

`p80diag/fitment.json` holds the rules that hide systems a car doesn't have (Body and Market). It's separate from the code database and explained inside the file itself.
