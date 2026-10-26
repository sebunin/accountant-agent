"""
parse_inventory.py — импорт остатков ТМЦ из Excel (Контур.Экстерн → Отчёт по остаткам)
Таблицы: inventory_items (справочник), inventory_snapshots (снимки остатков по датам)

Запуск:
    python3 parse_inventory.py <Отчет_по_остаткам.xlsx> [--db rameva.db]
"""

import openpyxl, sqlite3, argparse, sys, re
from pathlib import Path
from datetime import datetime

DEFAULT_DB = Path("~/.hermes/skills/rameva/rameva.db").expanduser()

def load_inventory(filepath: Path) -> tuple:
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    snap_date = ""
    for row in rows[:10]:
        if row[0] and "Дата:" in str(row[0]):
            m = re.search(r"(\d{2}\.\d{2}\.\d{4})", str(row[0]))
            if m: snap_date = m.group(1)

    # Найти строку заголовков
    header_idx = next(i for i,r in enumerate(rows) if r[0] == "Артикул")
    data = []
    for row in rows[header_idx+1:]:
        if not row or row[2] is None: continue
        qty = row[2]
        if not isinstance(qty, (int, float)): continue
        data.append({
            "article":   str(row[0]).strip() if row[0] else "",
            "name":      str(row[1]).strip() if row[1] else "",
            "qty":       float(qty),
            "unit":      str(row[3]).strip() if row[3] else "",
            "account":   str(row[4]).strip() if row[4] else "",
            "unit_cost": float(row[5]) if row[5] else 0.0,
            "item_type": str(row[6]).strip() if row[6] else "",
            "total_cost": float(qty) * (float(row[5]) if row[5] else 0.0),
        })
    return snap_date, data

def import_inventory(db_path: Path, snap_date: str, rows: list) -> tuple:
    con = sqlite3.connect(db_path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS inventory_snapshots (
        id          INTEGER PRIMARY KEY,
        snap_date   TEXT,
        source_file TEXT,
        imported_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS inventory_rows (
        id          INTEGER PRIMARY KEY,
        snapshot_id INTEGER,
        article     TEXT,
        name        TEXT,
        qty         REAL DEFAULT 0,
        unit        TEXT,
        account     TEXT,
        unit_cost   REAL DEFAULT 0,
        total_cost  REAL DEFAULT 0,
        item_type   TEXT,
        FOREIGN KEY (snapshot_id) REFERENCES inventory_snapshots(id)
    );
    CREATE INDEX IF NOT EXISTS idx_inv_name ON inventory_rows(name);
    CREATE INDEX IF NOT EXISTS idx_inv_acc  ON inventory_rows(account);
    """)
    cur = con.cursor()
    cur.execute("INSERT INTO inventory_snapshots (snap_date) VALUES (?)", (snap_date,))
    snap_id = cur.lastrowid
    nonzero = 0
    for r in rows:
        cur.execute("""INSERT INTO inventory_rows
            (snapshot_id,article,name,qty,unit,account,unit_cost,total_cost,item_type)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (snap_id,r["article"],r["name"],r["qty"],r["unit"],
             r["account"],r["unit_cost"],r["total_cost"],r["item_type"]))
        if r["qty"] > 0: nonzero += 1
    con.commit(); con.close()
    return snap_id, nonzero

def print_inventory_summary(rows: list, snap_date: str):
    nonzero = [r for r in rows if r["qty"] > 0]
    total = sum(r["total_cost"] for r in rows)
    total_nz = sum(r["total_cost"] for r in nonzero)

    print(f"\n  Остатки ТМЦ на {snap_date}")
    print("=" * 60)
    print(f"  Всего позиций в номенклатуре: {len(rows)}")
    print(f"  С ненулевым остатком:         {len(nonzero)}")
    print(f"  Общая стоимость остатков:     {total_nz:>12,.2f} ₽")
    print()
    if nonzero:
        print(f"  {'Счёт':<10} {'Название':<38} {'Кол-во':>6} {'Сумма':>10}")
        print("  " + "─" * 68)
        for r in sorted(nonzero, key=lambda x: -x["total_cost"]):
            name = r["name"][:37] if r["name"] else "(без названия)"
            acc  = r["account"].split()[0] if r["account"] else ""
            print(f"  {acc:<10} {name:<38} {r['qty']:>6.0f} {r['total_cost']:>10,.2f}")
    print("=" * 60)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    if not args.db.exists(): print("❌ БД не найдена"); sys.exit(1)
    print(f"📂 {args.file.name}")
    snap_date, rows = load_inventory(args.file)
    print_inventory_summary(rows, snap_date)
    snap_id, nonzero = import_inventory(args.db, snap_date, rows)
    print(f"\n✅ Загружено: {len(rows)} позиций (ненулевых: {nonzero}), snapshot #{snap_id}")

if __name__ == "__main__":
    main()
