"""
parse_nomenclature.py — импорт справочника номенклатуры из Excel (Контур.Экстерн)
361 позиция: входящие/исходящие услуги, товары, материалы.

Запуск:
    python3 parse_nomenclature.py <Список_номенклатуры.xlsx> [--db rameva.db]
"""

import openpyxl, sqlite3, argparse, sys
from pathlib import Path

DEFAULT_DB = Path("~/.hermes/skills/rameva/rameva.db").expanduser()

# Тип + счёт затрат из первой колонки: "Входящая услуга\n44 Расходы..."
def parse_type_and_account(raw: str) -> tuple:
    if not raw: return "other", ""
    parts = str(raw).split("\n")
    ntype = parts[0].strip()
    account = parts[1].strip() if len(parts) > 1 else ""
    type_map = {
        "Входящая услуга":  "service_in",
        "Исходящая услуга": "service_out",
        "Товар":            "good",
        "Материалы и сырье":"material",
        "Прочие материалы": "material",
    }
    return type_map.get(ntype, "other"), account

def load_nomenclature(filepath: Path) -> list:
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active
    result = []
    for row in list(ws.iter_rows(values_only=True))[1:]:
        if not row or not row[2]: continue
        ntype, account = parse_type_and_account(row[0])
        result.append({
            "type":       ntype,
            "account":    account,
            "article":    str(row[1]).strip() if row[1] and str(row[1]) != "None" else "",
            "name":       str(row[2]).strip(),
            "unit":       str(row[3]).strip() if row[3] else "",
            "vat_rate":   str(row[4]).strip() if row[4] else "",
            "country":    str(row[5]).strip() if row[5] else "",
            "comment":    str(row[7]).strip() if row[7] else "",
            "group_name": str(row[8]).strip() if row[8] else "",
        })
    return result

def import_nomenclature(db_path: Path, rows: list) -> tuple:
    con = sqlite3.connect(db_path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS nomenclature (
        id          INTEGER PRIMARY KEY,
        type        TEXT,
        account     TEXT,
        article     TEXT,
        name        TEXT NOT NULL,
        unit        TEXT,
        vat_rate    TEXT,
        country     TEXT,
        comment     TEXT,
        group_name  TEXT,
        active      INTEGER DEFAULT 1
    );
    CREATE INDEX IF NOT EXISTS idx_nom_name ON nomenclature(name);
    CREATE INDEX IF NOT EXISTS idx_nom_type ON nomenclature(type);
    """)
    added = updated = 0
    for r in rows:
        ex = con.execute("SELECT id FROM nomenclature WHERE name=?", (r["name"],)).fetchone()
        if ex:
            con.execute("""UPDATE nomenclature SET type=?,account=?,article=?,unit=?,
                vat_rate=?,country=?,comment=?,group_name=? WHERE id=?""",
                (r["type"],r["account"],r["article"],r["unit"],
                 r["vat_rate"],r["country"],r["comment"],r["group_name"],ex[0]))
            updated += 1
        else:
            con.execute("""INSERT INTO nomenclature (type,account,article,name,unit,vat_rate,
                country,comment,group_name) VALUES (?,?,?,?,?,?,?,?,?)""",
                (r["type"],r["account"],r["article"],r["name"],r["unit"],
                 r["vat_rate"],r["country"],r["comment"],r["group_name"]))
            added += 1
    con.commit(); con.close()
    return added, updated

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    if not args.db.exists(): print("❌ БД не найдена"); sys.exit(1)
    print(f"📂 {args.file.name}")
    rows = load_nomenclature(args.file)
    added, updated = import_nomenclature(args.db, rows)
    print(f"✅ Номенклатура: добавлено {added}, обновлено {updated}")
    from collections import Counter
    for t,c in Counter(r["type"] for r in rows).most_common():
        print(f"   {t}: {c}")

if __name__ == "__main__":
    main()
