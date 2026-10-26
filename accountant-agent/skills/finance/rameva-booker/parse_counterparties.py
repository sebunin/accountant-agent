"""
parse_counterparties.py — импорт справочника контрагентов из Excel (Контур.Экстерн)
Обновляет таблицу counterparties: добавляет новых, дополняет реквизиты существующих.

Запуск:
    python3 parse_counterparties.py <Список_контрагентов.xlsx> [--db rameva.db]
"""

import openpyxl
import sqlite3
import argparse
import re
import sys
from pathlib import Path

DEFAULT_DB = Path("~/.hermes/skills/rameva/rameva.db").expanduser()
OWN_INN = "[INN]"

FUND_KEYWORDS   = ["казначейство", "уфк", "ифнс", "фнс", "осфр", "офр", "пфр",
                   "фсс", "фонд социального", "пенсионного"]
CLIENT_KEYWORDS = ["главсклад"]

def detect_type(name: str) -> str:
    n = name.lower()
    if any(k in n for k in FUND_KEYWORDS): return "fund"
    if any(k in n for k in CLIENT_KEYWORDS): return "client"
    return "supplier"

def parse_bank_details(raw: str) -> tuple:
    if not raw: return "", "", ""
    account = bank_name = bik = ""
    m = re.search(r"Р/с\s+([\d]+)", raw)
    if m: account = m.group(1)
    m = re.search(r"Банк\s+([^,]+)", raw)
    if m: bank_name = m.group(1).strip()
    m = re.search(r"БИК\s+([\d]+)", raw)
    if m: bik = m.group(1)
    return account, bank_name, bik

def load_counterparties(filepath: Path) -> list:
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active
    result = []
    for row in list(ws.iter_rows(values_only=True))[1:]:
        if not row or not row[0]: continue
        name    = str(row[0]).strip()
        inn     = str(row[1]).strip() if row[1] else ""
        kpp     = str(row[2]).strip() if row[2] else ""
        address = str(row[4]).strip() if row[4] else ""
        bank_raw= str(row[5]).strip() if row[5] else ""
        phone   = str(row[6]).strip() if row[6] else ""
        email   = str(row[7]).strip() if row[7] else ""
        comment = str(row[8]).strip() if row[8] else ""
        if inn == OWN_INN: continue
        account, bank_name, bik = parse_bank_details(bank_raw)
        result.append({"name": name, "inn": inn, "kpp": kpp, "address": address,
                       "account": account, "bank": bank_name, "bik": bik,
                       "phone": phone, "email": email, "notes": comment,
                       "type": detect_type(name)})
    return result

def upsert_counterparties(db_path: Path, rows: list) -> tuple:
    con = sqlite3.connect(db_path)
    existing_cols = {r[1] for r in con.execute("PRAGMA table_info(counterparties)").fetchall()}
    for col, td in [("kpp","TEXT"),("address","TEXT"),("bank","TEXT"),("phone","TEXT"),("email","TEXT")]:
        if col not in existing_cols:
            con.execute(f"ALTER TABLE counterparties ADD COLUMN {col} {td}")
    added = updated = 0
    for r in rows:
        existing = None
        if r["inn"]:
            existing = con.execute("SELECT id FROM counterparties WHERE inn=?", (r["inn"],)).fetchone()
        if not existing:
            existing = con.execute("SELECT id FROM counterparties WHERE name=?", (r["name"],)).fetchone()
        if existing:
            con.execute("""UPDATE counterparties SET name=?,inn=?,kpp=?,address=?,account=?,
                bank=?,bank_bik=?,phone=?,email=?,notes=COALESCE(NULLIF(?,\"\"),notes) WHERE id=?""",
                (r["name"],r["inn"],r["kpp"],r["address"],r["account"],r["bank"],r["bik"],
                 r["phone"],r["email"],r["notes"],existing[0]))
            updated += 1
        else:
            con.execute("""INSERT OR IGNORE INTO counterparties (name,inn,kpp,address,account,bank,bank_bik,
                phone,email,type,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (r["name"],r["inn"],r["kpp"],r["address"],r["account"],r["bank"],r["bik"],
                 r["phone"],r["email"],r["type"],r["notes"]))
            added += 1
    con.commit(); con.close()
    return added, updated

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    if not args.db.exists(): print(f"❌ БД не найдена"); sys.exit(1)
    print(f"📂 {args.file.name}")
    rows = load_counterparties(args.file)
    added, updated = upsert_counterparties(args.db, rows)
    print(f"✅ Контрагентов: добавлено {added}, обновлено {updated}")
    from collections import Counter
    for t,c in Counter(r["type"] for r in rows).most_common():
        print(f"   {t}: {c}")

if __name__ == "__main__":
    main()
