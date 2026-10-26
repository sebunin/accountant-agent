"""
reports.py — отчёты и дашборд субагента-бухгалтера ООО «РАМЕВА»

Запуск:
    python3 reports.py status          — текущий статус (остаток, задачи)
    python3 reports.py transactions    — лента транзакций за период
    python3 reports.py receivables     — дебиторка и кредиторка
    python3 reports.py tasks           — открытые задачи
    python3 reports.py full            — полный дашборд
    python3 reports.py [--db path] [--period 2026-05]
"""

import sqlite3
import argparse
import sys
from pathlib import Path
from datetime import date, datetime
from typing import Optional

DEFAULT_DB = Path("~/.hermes/skills/rameva/rameva.db").expanduser()

# Ширина вывода
W = 60

# Названия категорий (для отображения)
CAT_NAMES = {
    "revenue_it":    "Выручка IT/хостинг",
    "revenue_other": "Выручка прочая",
    "salary":        "Зарплата",
    "alimony":       "Алименты",
    "ndfl":          "НДФЛ",
    "sfr_ns":        "Взносы СФР НС",
    "enp":           "ЕНП / налоги",
    "bank_fee":      "Комиссия банка",
    "loan_in":       "Получение займа",
    "loan_out":      "Выдача/возврат займа",
    "supplier":      "Оплата поставщику",
    "refund_in":     "Возврат от поставщика",
    "refund_out":    "Возврат покупателю",
    "internal":      "Внутренний перевод",
    "unclassified":  "❓ Не классифицировано",
}


def _db(db_path: Path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _state(con, key: str, default="—") -> str:
    row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] else default


def _sep(char="─", title=""):
    if title:
        side = (W - len(title) - 2) // 2
        return char * side + " " + title + " " + char * (W - side - len(title) - 2)
    return char * W


# ---------------------------------------------------------------------------
# 1. Текущий статус
# ---------------------------------------------------------------------------

def report_status(con, period: str):
    today = date.today().isoformat()

    balance   = float(_state(con, "balance_current", "0"))
    bal_date  = _state(con, "balance_date", "")
    last_file = _state(con, "last_import_file", "")

    # Открытые задачи
    tasks_hi  = con.execute(
        "SELECT COUNT(*) as c FROM tasks WHERE status='open' AND priority='high'"
    ).fetchone()["c"]
    tasks_all = con.execute(
        "SELECT COUNT(*) as c FROM tasks WHERE status='open'"
    ).fetchone()["c"]

    # Просроченные налоговые обязательства
    overdue = con.execute("""
        SELECT COUNT(*) as c FROM tax_obligations
        WHERE status='pending' AND due_date < ?
    """, (today,)).fetchone()["c"]

    # Ближайшие платежи (следующие 10 дней)
    upcoming = con.execute("""
        SELECT type, amount, due_date FROM tax_obligations
        WHERE status='pending' AND due_date >= ? AND due_date <= date(?, '+10 days')
        ORDER BY due_date
    """, (today, today)).fetchall()

    # Неклассифицированные транзакции
    unclassified = con.execute("""
        SELECT COUNT(*) as c FROM bank_transactions
        WHERE category IS NULL OR category = 'unclassified'
    """).fetchone()["c"]

    print()
    print("=" * W)
    print(f"  СТАТУС УЧЁТА  —  ООО «РАМЕВА»  —  {today}")
    print("=" * W)
    print(f"  {'Остаток по счёту ПСБ:':<38} {balance:>12,.2f} ₽")
    if bal_date:
        print(f"  {'Дата остатка:':<38} {bal_date}")
    if last_file:
        print(f"  {'Последняя выписка:':<38} {last_file[:30]}")
    print()

    alerts = []
    if overdue > 0:
        alerts.append(f"  🔴 Просрочено налоговых обязательств: {overdue}")
    if tasks_hi > 0:
        alerts.append(f"  🟠 Срочных задач: {tasks_hi}")
    if unclassified > 0:
        alerts.append(f"  ⚠️  Неклассифицированных транзакций: {unclassified}")
    if not alerts:
        alerts.append("  ✅ Нарушений не обнаружено")
    for a in alerts:
        print(a)

    if upcoming:
        print()
        print(f"  {'Ближайшие платежи (10 дней):'}")
        for u in upcoming:
            print(f"    {u['due_date']}  {u['type']:<22}  {u['amount']:>10,.2f} ₽")

    print(f"\n  Открытых задач: {tasks_all}  (срочных: {tasks_hi})")
    print("=" * W)
    print()


# ---------------------------------------------------------------------------
# 2. Лента транзакций за период
# ---------------------------------------------------------------------------

def report_transactions(con, period: str):
    year, month = period.split("-")
    date_from = f"{year}-{month}-01"
    import calendar
    last = calendar.monthrange(int(year), int(month))[1]
    date_to = f"{year}-{month}-{last:02d}"

    rows = con.execute("""
        SELECT value_date, direction, amount, category,
               counterparty, purpose
        FROM bank_transactions
        WHERE value_date BETWEEN ? AND ?
        ORDER BY value_date, id
    """, (date_from, date_to)).fetchall()

    total_in  = sum(r["amount"] for r in rows if r["direction"] == "IN")
    total_out = sum(r["amount"] for r in rows if r["direction"] == "OUT")

    print()
    print("=" * W)
    print(f"  ТРАНЗАКЦИИ — {period}  |  ООО «РАМЕВА»")
    print("=" * W)
    if not rows:
        print("  Нет транзакций за период.")
    else:
        for r in rows:
            arrow = "←" if r["direction"] == "IN" else "→"
            cat   = CAT_NAMES.get(r["category"] or "unclassified", r["category"] or "?")
            cp    = (r["counterparty"] or "")[:28]
            print(f"  {r['value_date']}  {arrow} {r['amount']:>10,.2f}  {cat:<24}")
            print(f"             {cp}")
        print(_sep())
        print(f"  {'Итого поступило:':<38} {total_in:>12,.2f} ₽")
        print(f"  {'Итого списано:':<38} {total_out:>12,.2f} ₽")
        print(f"  {'Оборот:':<38} {total_in - total_out:>12,.2f} ₽")
    print("=" * W)
    print()


# ---------------------------------------------------------------------------
# 3. Разбивка по категориям
# ---------------------------------------------------------------------------

def report_by_category(con, period: str):
    year, month = period.split("-")
    import calendar
    last = calendar.monthrange(int(year), int(month))[1]
    date_from = f"{year}-{month}-01"
    date_to   = f"{year}-{month}-{last:02d}"

    rows = con.execute("""
        SELECT direction, category, SUM(amount) as total, COUNT(*) as cnt
        FROM bank_transactions
        WHERE value_date BETWEEN ? AND ?
        GROUP BY direction, category
        ORDER BY direction DESC, total DESC
    """, (date_from, date_to)).fetchall()

    print()
    print("=" * W)
    print(f"  РАЗБИВКА ПО КАТЕГОРИЯМ — {period}  |  ООО «РАМЕВА»")
    print("=" * W)

    cur_dir = None
    dir_total = 0
    for r in rows:
        if r["direction"] != cur_dir:
            if cur_dir is not None:
                print(f"  {'Итого:':<38} {dir_total:>12,.2f} ₽")
                print(_sep("─"))
            cur_dir = r["direction"]
            label = "ПОСТУПЛЕНИЯ (IN)" if cur_dir == "IN" else "СПИСАНИЯ (OUT)"
            print(f"  {label}")
            dir_total = 0
        cat  = CAT_NAMES.get(r["category"] or "unclassified", r["category"] or "?")
        tx_s = f"({r['cnt']} оп.)"
        print(f"  {cat:<32} {tx_s:<8} {r['total']:>10,.2f} ₽")
        dir_total += r["total"]
    if cur_dir is not None:
        print(f"  {'Итого:':<38} {dir_total:>12,.2f} ₽")
    print("=" * W)
    print()


# ---------------------------------------------------------------------------
# 4. Дебиторка / кредиторка
# ---------------------------------------------------------------------------

def report_receivables(con):
    # Открытые документы без оплаты
    docs = con.execute("""
        SELECT d.type, d.number, d.date, d.amount, d.direction,
               d.status, c.name as cp_name
        FROM documents d
        LEFT JOIN counterparties c ON d.counterparty_id = c.id
        WHERE d.status NOT IN ('paid','rejected') AND d.linked_tx_id IS NULL
        ORDER BY d.direction, d.date
    """).fetchall()

    print()
    print("=" * W)
    print(f"  ДЕБИТОРКА И КРЕДИТОРКА  |  ООО «РАМЕВА»")
    print("=" * W)
    if not docs:
        print("  Открытых документов без оплаты нет.")
        print("  (Загрузите документы через: команда /rameva документ)")
    else:
        receivables = [d for d in docs if d["direction"] == "OUT"]
        payables    = [d for d in docs if d["direction"] == "IN"]
        if receivables:
            print("  ДЕБИТОРКА (нам должны):")
            total = 0
            for d in receivables:
                print(f"  {d['date']}  {d['cp_name']:<28}  {d['amount']:>10,.2f} ₽")
                print(f"           {d['type']} №{d['number']} [{d['status']}]")
                total += d["amount"]
            print(f"  {'ИТОГО дебиторка:':<38} {total:>12,.2f} ₽")
        if payables:
            print()
            print("  КРЕДИТОРКА (мы должны):")
            total = 0
            for d in payables:
                print(f"  {d['date']}  {d['cp_name']:<28}  {d['amount']:>10,.2f} ₽")
                print(f"           {d['type']} №{d['number']} [{d['status']}]")
                total += d["amount"]
            print(f"  {'ИТОГО кредиторка:':<38} {total:>12,.2f} ₽")
    print("=" * W)
    print()


# ---------------------------------------------------------------------------
# 5. Задачи
# ---------------------------------------------------------------------------

def report_tasks(con, priority: Optional[str] = None):
    where = "status = 'open'"
    if priority:
        where += f" AND priority = '{priority}'"

    tasks = con.execute(f"""
        SELECT id, title, category, due_date, priority, notes
        FROM tasks
        WHERE {where}
        ORDER BY
            CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
            due_date NULLS LAST
    """).fetchall()

    icons = {"high": "🔴", "normal": "🟡", "low": "⚪"}
    print()
    print("=" * W)
    print(f"  ОТКРЫТЫЕ ЗАДАЧИ  |  ООО «РАМЕВА»")
    print("=" * W)
    if not tasks:
        print("  Нет открытых задач. ✅")
    else:
        for t in tasks:
            icon  = icons.get(t["priority"], "○")
            due   = f"  до {t['due_date']}" if t["due_date"] else ""
            print(f"  {icon} #{t['id']}  {t['title'][:50]}{due}")
            if t["notes"]:
                for line in (t["notes"] or "").splitlines()[:2]:
                    print(f"       {line[:56]}")
    print("=" * W)
    print()


# ---------------------------------------------------------------------------
# 6. Налоговые обязательства
# ---------------------------------------------------------------------------

def report_taxes(con):
    rows = con.execute("""
        SELECT period, type, amount, due_date, paid_amount, status
        FROM tax_obligations
        ORDER BY due_date DESC
    """).fetchall()

    today = date.today().isoformat()
    print()
    print("=" * W)
    print(f"  НАЛОГОВЫЕ ОБЯЗАТЕЛЬСТВА  |  ООО «РАМЕВА»")
    print("=" * W)
    if not rows:
        print("  Нет данных. Запустите: python3 tax_calc.py --update")
    else:
        for r in rows:
            overdue = r["status"] == "pending" and r["due_date"] < today
            flag = "🔴" if overdue else ("✅" if r["status"] == "paid" else "🟡")
            print(f"  {flag} {r['period']}  {r['type']:<12}  "
                  f"к уплате: {r['amount']:>8,.2f} ₽  "
                  f"уплачено: {r['paid_amount']:>8,.2f} ₽  "
                  f"срок: {r['due_date']}")
    print("=" * W)
    print()


# ---------------------------------------------------------------------------
# 7. Полный дашборд
# ---------------------------------------------------------------------------

def report_full(db_path: Path, period: str):
    con = _db(db_path)
    report_status(con, period)
    report_by_category(con, period)
    report_taxes(con)
    report_tasks(con)
    report_receivables(con)
    con.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Отчёты субагента-бухгалтера Рамева")
    parser.add_argument("command", nargs="?", default="status",
                        choices=["status","transactions","categories",
                                 "receivables","tasks","taxes","full"],
                        help="Тип отчёта")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--period", default=datetime.now().strftime("%Y-%m"),
                        help="Период YYYY-MM")
    parser.add_argument("--priority", choices=["high","normal","low"],
                        help="Фильтр задач по приоритету")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"❌ БД не найдена: {args.db}"); sys.exit(1)

    con = _db(args.db)

    if args.command == "status":
        report_status(con, args.period)
    elif args.command == "transactions":
        report_transactions(con, args.period)
    elif args.command == "categories":
        report_by_category(con, args.period)
    elif args.command == "receivables":
        report_receivables(con)
    elif args.command == "tasks":
        report_tasks(con, args.priority)
    elif args.command == "taxes":
        report_taxes(con)
    elif args.command == "full":
        con.close()
        report_full(args.db, args.period)
        return

    con.close()


if __name__ == "__main__":
    main()
