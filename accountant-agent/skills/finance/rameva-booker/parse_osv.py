"""
parse_osv.py — импорт оборотно-сальдовой ведомости (CSV из Контур.Экстерн)
и сверка с данными банковских транзакций в БД.

Запуск:
    python3 parse_osv.py <файл.csv> [--db rameva.db] [--reconcile] [--period 2026-01]
"""

import csv
import sqlite3
import argparse
import sys
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

DEFAULT_DB = Path("~/.hermes/skills/rameva/rameva.db").expanduser()

# Счета, которые особо важны для контроля
WATCHLIST = {
    "51":    "Расчётный счёт",
    "62":    "Дебиторка (покупатели)",
    "60":    "Кредиторка (поставщики)",
    "68":    "Налоги",
    "68.енп":"ЕНП",
    "68.усн":"УСН/АУСН",
    "68.фл": "НДФЛ",
    "69":    "Страховые взносы",
    "70":    "Зарплата",
    "71":    "Подотчётные лица",
    "76.ал": "Алименты",
    "66":    "Краткосрочные займы",
    "67":    "Долгосрочные займы",
    "84":    "Нераспределённая прибыль",
    "99":    "Прибыли и убытки",
}


def parse_amount(s: str) -> float:
    """'1 234,56' → 1234.56"""
    if not s or not s.strip():
        return 0.0
    return float(s.strip().replace(" ", "").replace("\xa0", "").replace(",", "."))


def parse_osv_csv(filepath: Path) -> tuple[dict, list[dict]]:
    """
    Читает ОСВ из CSV (Windows-1251, разделитель ;).
    Возвращает (header_info, rows).
    """
    with open(filepath, encoding="windows-1251") as f:
        content = f.read()

    lines = content.splitlines()
    header_info = {}
    rows = []
    data_started = False

    # Разбор заголовка
    for i, line in enumerate(lines):
        parts = [p.strip().strip('"').replace('""', '"') for p in line.split(";")]
        text = parts[0]

        if "Сформирована" in text or "Сформировано" in text:
            m = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
            if m:
                header_info["generated"] = m.group(1)
        elif "Период:" in text:
            header_info["period"] = text.replace("Период:", "").strip()
        elif "Организация:" in text:
            header_info["org"] = text.replace("Организация:", "").strip()
        elif text.strip() == "" and i > 3:
            data_started = True
            break

    # Найти строку с заголовком колонок
    col_start = 0
    for i, line in enumerate(lines):
        if "Начальное сальдо" in line or "Дебет" in line.split(";")[2:3]:
            col_start = i + 1   # следующая строка — метки Дебет/Кредит
            break

    # Парсим строки данных
    in_data = False
    for line in lines:
        parts = [p.strip().replace('"', '') for p in line.split(";")]
        if not parts or len(parts) < 7:
            continue

        code = parts[0].strip()
        if not code or code.startswith("В") or "Начальное" in code or "Дебет" in code:
            if "Начальное" in code:
                in_data = True
            continue
        if "Всего:" in code or "Забалансовые" in code:
            in_data = True   # Забалансовые продолжаем
            if "Всего:" in code:
                continue

        if not in_data:
            in_data = True

        # Имя счёта — второй элемент или пустой (субсчёт отступает пробелами)
        name = parts[1].strip() if len(parts) > 1 else ""
        is_sub = parts[0].startswith(" ") or (code.startswith("  "))
        code = code.strip()

        if not code or not name:
            continue

        try:
            rows.append({
                "code":         code,
                "name":         name,
                "is_sub":       is_sub,
                "open_debit":   parse_amount(parts[2] if len(parts) > 2 else ""),
                "open_credit":  parse_amount(parts[3] if len(parts) > 3 else ""),
                "turn_debit":   parse_amount(parts[4] if len(parts) > 4 else ""),
                "turn_credit":  parse_amount(parts[5] if len(parts) > 5 else ""),
                "close_debit":  parse_amount(parts[6] if len(parts) > 6 else ""),
                "close_credit": parse_amount(parts[7] if len(parts) > 7 else ""),
            })
        except (ValueError, IndexError):
            continue

    return header_info, rows


def save_osv_to_db(db_path: Path, header_info: dict, rows: list[dict], source_file: str):
    """Сохраняет ОСВ в таблицу osv_snapshots."""
    con = sqlite3.connect(db_path)

    # Создаём таблицу если нет
    con.executescript("""
    CREATE TABLE IF NOT EXISTS osv_snapshots (
        id              INTEGER PRIMARY KEY,
        generated_date  TEXT,
        period          TEXT,
        source_file     TEXT,
        imported_at     TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS osv_rows (
        id          INTEGER PRIMARY KEY,
        snapshot_id INTEGER,
        code        TEXT,
        name        TEXT,
        is_sub      INTEGER DEFAULT 0,
        open_debit  REAL DEFAULT 0,
        open_credit REAL DEFAULT 0,
        turn_debit  REAL DEFAULT 0,
        turn_credit REAL DEFAULT 0,
        close_debit REAL DEFAULT 0,
        close_credit REAL DEFAULT 0,
        FOREIGN KEY (snapshot_id) REFERENCES osv_snapshots(id)
    );
    CREATE INDEX IF NOT EXISTS idx_osv_rows_code ON osv_rows(code);
    """)

    cur = con.cursor()
    cur.execute("""
        INSERT INTO osv_snapshots (generated_date, period, source_file)
        VALUES (?, ?, ?)
    """, (header_info.get("generated"), header_info.get("period"), source_file))
    snap_id = cur.lastrowid

    for r in rows:
        cur.execute("""
            INSERT INTO osv_rows (snapshot_id, code, name, is_sub,
                open_debit, open_credit, turn_debit, turn_credit,
                close_debit, close_credit)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (snap_id, r["code"], r["name"], int(r["is_sub"]),
              r["open_debit"], r["open_credit"], r["turn_debit"], r["turn_credit"],
              r["close_debit"], r["close_credit"]))

    con.commit()
    con.close()
    return snap_id


def reconcile(db_path: Path, snap_id: Optional[int] = None):
    """
    Сверяет данные ОСВ с данными банковских транзакций.
    Фокус: счёт 51 (расчётный счёт).
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    # Берём последний снимок если не указан
    if snap_id is None:
        row = con.execute(
            "SELECT id, generated_date, period FROM osv_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            print("❌ ОСВ не загружена. Сначала запустите parse_osv.py <файл>")
            con.close()
            return
        snap_id = row["id"]
        osv_date = row["generated_date"]
        osv_period = row["period"]
    else:
        row = con.execute(
            "SELECT generated_date, period FROM osv_snapshots WHERE id=?", (snap_id,)
        ).fetchone()
        osv_date = row["generated_date"]
        osv_period = row["period"]

    print()
    print("=" * 60)
    print(f"  СВЕРКА ОСВ ↔ БАНКОВСКИЕ ТРАНЗАКЦИИ")
    print(f"  ОСВ от {osv_date}  |  Период: {osv_period}")
    print("=" * 60)

    # 1. Счёт 51 — расчётный счёт
    osv_51 = con.execute("""
        SELECT close_debit, close_credit FROM osv_rows
        WHERE snapshot_id=? AND code='51'
    """, (snap_id,)).fetchone()

    db_balance = con.execute(
        "SELECT value FROM state WHERE key='balance_current'"
    ).fetchone()
    db_bal_date = con.execute(
        "SELECT value FROM state WHERE key='balance_date'"
    ).fetchone()

    if osv_51 and db_balance:
        osv_bal = osv_51["close_debit"] - osv_51["close_credit"]
        our_bal = float(db_balance["value"] or 0)
        diff = abs(osv_bal - our_bal)
        our_date = db_bal_date["value"] if db_bal_date else "?"

        status = "✅" if diff < 1.0 else "⚠️ "
        print(f"\n  Счёт 51 — Расчётный счёт")
        print(f"  {'ОСВ (остаток):':<30} {osv_bal:>14,.2f} ₽")
        print(f"  {'Наши данные (выписка {}):':<30}".format(our_date) + f" {our_bal:>14,.2f} ₽")
        print(f"  {'Расхождение:':<30} {diff:>14,.2f} ₽  {status}")
        if diff > 1.0:
            print("  → Загрузите актуальную банковскую выписку.")

    # 2. Ключевые счета из WATCHLIST
    print(f"\n  Ключевые остатки по ОСВ:")
    print(f"  {'Счёт':<12} {'Название':<30} {'Дебет':>12} {'Кредит':>12}")
    print("  " + "─" * 56)

    for code, label in WATCHLIST.items():
        r = con.execute("""
            SELECT close_debit, close_credit FROM osv_rows
            WHERE snapshot_id=? AND code=?
        """, (snap_id, code)).fetchone()
        if r and (r["close_debit"] != 0 or r["close_credit"] != 0):
            print(f"  {code:<12} {label:<30} "
                  f"{r['close_debit']:>12,.2f} {r['close_credit']:>12,.2f}")

    # 3. Особые предупреждения
    print()
    warnings = []

    # Подотчёт (71)
    r71 = con.execute(
        "SELECT close_debit FROM osv_rows WHERE snapshot_id=? AND code='71'", (snap_id,)
    ).fetchone()
    if r71 and r71["close_debit"] > 0:
        warnings.append(f"  🟡 Счёт 71 (подотчёт): {r71['close_debit']:,.2f} ₽ не закрыты документами")

    # Долги по НДФЛ (68.фл)
    r68fl = con.execute(
        "SELECT close_credit FROM osv_rows WHERE snapshot_id=? AND code='68.фл'", (snap_id,)
    ).fetchone()
    if r68fl and r68fl["close_credit"] > 0:
        warnings.append(f"  🟡 НДФЛ к уплате: {r68fl['close_credit']:,.2f} ₽")

    # УСН долг
    r68usn = con.execute(
        "SELECT close_credit FROM osv_rows WHERE snapshot_id=? AND code='68.усн'", (snap_id,)
    ).fetchone()
    if r68usn and r68usn["close_credit"] > 0:
        warnings.append(f"  🟠 УСН (старый долг): {r68usn['close_credit']:,.2f} ₽ — уточнить у бухгалтера")

    # Пени СФР
    r69pe = con.execute(
        "SELECT close_debit FROM osv_rows WHERE snapshot_id=? AND code='69.пе'", (snap_id,)
    ).fetchone()
    if r69pe and r69pe["close_debit"] > 0:
        warnings.append(f"  🟡 Пени по взносам СФР: {r69pe['close_debit']:,.2f} ₽")

    # Дебиторка > 30 дней без документов
    r62 = con.execute(
        "SELECT close_debit FROM osv_rows WHERE snapshot_id=? AND code='62'", (snap_id,)
    ).fetchone()
    if r62 and r62["close_debit"] > 0:
        warnings.append(f"  🔴 Дебиторка (62): {r62['close_debit']:,.2f} ₽ — нужны акты/УПД")

    if warnings:
        print("  Требует внимания:")
        for w in warnings:
            print(w)
    else:
        print("  ✅ Критичных расхождений не обнаружено.")

    print("=" * 60)
    print()
    con.close()


def print_osv_report(db_path: Path, snap_id: Optional[int] = None):
    """Выводит ОСВ в читаемом виде."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    if snap_id is None:
        row = con.execute(
            "SELECT id, generated_date, period FROM osv_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            print("❌ ОСВ не загружена.")
            con.close()
            return
        snap_id = row["id"]
        print(f"\n  ОСВ от {row['generated_date']}  |  Период: {row['period']}\n")

    rows = con.execute("""
        SELECT code, name, open_debit, open_credit,
               turn_debit, turn_credit, close_debit, close_credit
        FROM osv_rows WHERE snapshot_id=?
        ORDER BY id
    """, (snap_id,)).fetchall()

    print(f"  {'Счёт':<14}{'Нач.Дт':>12}{'Нач.Кт':>12}{'Обор.Дт':>12}{'Обор.Кт':>12}{'Кон.Дт':>12}{'Кон.Кт':>12}")
    print("  " + "─" * 86)
    for r in rows:
        indent = "  " if r["code"].strip() != r["code"] else ""
        cd = r["close_debit"] or 0
        ck = r["close_credit"] or 0
        if cd == 0 and ck == 0:
            continue
        print(f"  {indent}{r['code']:<12}"
              f"{(r['open_debit'] or 0):>12,.0f}{(r['open_credit'] or 0):>12,.0f}"
              f"{(r['turn_debit'] or 0):>12,.0f}{(r['turn_credit'] or 0):>12,.0f}"
              f"{cd:>12,.0f}{ck:>12,.0f}")
    con.close()


def main():
    parser = argparse.ArgumentParser(description="Импорт ОСВ и сверка — ООО «РАМЕВА»")
    parser.add_argument("file", nargs="?", type=Path, help="CSV файл ОСВ")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--reconcile", action="store_true", help="Провести сверку")
    parser.add_argument("--show", action="store_true", help="Показать последнюю ОСВ")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"❌ БД не найдена: {args.db}"); sys.exit(1)

    if args.file:
        print(f"📂 Читаю ОСВ: {args.file.name}")
        header_info, rows = parse_osv_csv(args.file)
        snap_id = save_osv_to_db(args.db, header_info, rows, args.file.name)
        print(f"✅ ОСВ загружена: {len(rows)} счетов/субсчетов, snapshotID={snap_id}")
        print(f"   Период: {header_info.get('period')}, дата: {header_info.get('generated')}")
        reconcile(args.db, snap_id)
    elif args.reconcile:
        reconcile(args.db)
    elif args.show:
        print_osv_report(args.db)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
