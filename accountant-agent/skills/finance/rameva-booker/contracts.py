"""
contracts.py — модуль управления договорами и контроля исполнения обязательств
ООО «РАМЕВА». Отслеживает циклы: счёт → акт/УПД → оплата.

Команды:
    python3 contracts.py list                      — список активных договоров
    python3 contracts.py card ГЛАВСКЛАД            — карточка контрагента
    python3 contracts.py status                    — что выставить / что просрочено
    python3 contracts.py add                       — добавить договор интерактивно
    python3 contracts.py import <реестр.xlsx>      — загрузить историю из реестра
    python3 contracts.py reconcile                 — сверить документы с оплатами
"""

import openpyxl, sqlite3, argparse, sys, re
from pathlib import Path
from datetime import date, datetime, timedelta

DEFAULT_DB = Path("~/.hermes/skills/rameva/rameva.db").expanduser()

# ─────────────────────────────────────────────────────────────────────────────
# Инициализация таблиц
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts_v2 (
    id                  INTEGER PRIMARY KEY,
    counterparty_id     INTEGER,
    number              TEXT,
    date                TEXT,
    subject             TEXT,
    type                TEXT DEFAULT 'service',
    direction           TEXT DEFAULT 'OUT',  -- OUT=мы оказываем, IN=нам оказывают
    status              TEXT DEFAULT 'active',
    amount_monthly      REAL,                -- сумма в месяц (0 если разовый)
    amount_total        REAL,                -- общая сумма (0 если бессрочный)
    billing_day         INTEGER DEFAULT 27,  -- день выставления счёта
    billing_period      TEXT DEFAULT 'month',-- 'month'/'quarter'/'once'
    act_day             TEXT DEFAULT 'last', -- 'last'=последний день месяца или число
    payment_days        INTEGER DEFAULT 5,   -- срок оплаты в днях после акта
    notes               TEXT,
    created_at          TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (counterparty_id) REFERENCES counterparties(id)
);

CREATE TABLE IF NOT EXISTS contract_obligations (
    id              INTEGER PRIMARY KEY,
    contract_id     INTEGER NOT NULL,
    period          TEXT NOT NULL,       -- 'YYYY-MM' за какой месяц
    bill_date       TEXT,                -- когда выставлен счёт
    bill_doc_id     INTEGER,             -- id в таблице documents
    act_date        TEXT,                -- когда подписан акт/УПД
    act_doc_id      INTEGER,
    pay_due_date    TEXT,                -- срок оплаты
    pay_date        TEXT,                -- когда оплачено
    pay_tx_id       INTEGER,             -- id в bank_transactions
    amount          REAL,
    status          TEXT DEFAULT 'pending',
    -- pending / billed / acted / paid / overdue / cancelled
    notes           TEXT,
    FOREIGN KEY (contract_id) REFERENCES contracts_v2(id)
);
CREATE INDEX IF NOT EXISTS idx_obl_period   ON contract_obligations(period);
CREATE INDEX IF NOT EXISTS idx_obl_status   ON contract_obligations(status);
CREATE INDEX IF NOT EXISTS idx_obl_contract ON contract_obligations(contract_id);
"""

def init_tables(db_path: Path):
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()

def db(db_path: Path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con

# ─────────────────────────────────────────────────────────────────────────────
# Импорт истории из реестра документов Excel
# ─────────────────────────────────────────────────────────────────────────────

INCOMING_PAYMENT = "Входящее платежное поручение"
OUTGOING_ACT     = "Исходящий акт приемки услуг"
OUTGOING_UPD     = "Исходящий УПД"
OUTGOING_BILL    = "Исходящий счет на оплату"
OUTGOING_CONTRACT= "Исходящий договор"

def import_from_registry(db_path: Path, xlsx_path: Path) -> dict:
    """
    Читает реестр документов Excel, строит историю обязательств по контрагентам.
    Автоматически создаёт contracts_v2 и contract_obligations.
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    # Найти заголовок
    header_idx = next((i for i,r in enumerate(rows) if r and r[0]=="Дата"), None)
    if header_idx is None:
        print("❌ Не найдена строка заголовков в файле")
        return {}

    # Метаинформация
    meta = {}
    for r in rows[:header_idx]:
        if r and r[0]:
            text = str(r[0])
            if "По контрагенту:" in text:
                meta["counterparty"] = text.replace("По контрагенту:","").strip()

    con = db(db_path)
    data_rows = []
    for row in rows[header_idx+1:]:
        if not row or not row[0]: continue
        doc_date   = row[0].date() if hasattr(row[0],"date") else None
        doc_type   = str(row[3]).strip() if row[3] else ""
        operation  = str(row[4]).strip() if row[4] else ""
        number     = str(row[5]).strip() if row[5] else ""
        cp_name    = str(row[6]).strip() if row[6] else ""
        amount     = float(row[7]) if row[7] else 0.0
        comment    = str(row[8]).strip() if row[8] else ""
        if doc_type: data_rows.append((doc_date, doc_type, number, cp_name, amount, comment, operation))

    # Группируем по контрагентам
    from collections import defaultdict
    by_cp = defaultdict(list)
    for row in data_rows:
        by_cp[row[3]].append(row)

    stats = {"contracts": 0, "obligations": 0, "counterparties": 0}

    for cp_name, cp_rows in by_cp.items():
        if not cp_name: continue

        # Найти или создать контрагента
        cp = con.execute("SELECT id FROM counterparties WHERE name=? OR name LIKE ?",
                         (cp_name, f"%{cp_name.split('\"')[1][:10]}%"
                          if '"' in cp_name else cp_name)).fetchone()
        if not cp:
            con.execute("INSERT INTO counterparties (name, type) VALUES (?,?)",
                        (cp_name, "client"))
            con.commit()
            cp = con.execute("SELECT id FROM counterparties WHERE name=?", (cp_name,)).fetchone()
        cp_id = cp[0]
        stats["counterparties"] += 1

        # Найти договор из реестра
        contract_rows = [r for r in cp_rows if r[1] == OUTGOING_CONTRACT]
        contract_number = contract_rows[0][2] if contract_rows else "б/н"
        contract_date   = contract_rows[0][0].isoformat() if contract_rows else ""

        # Определить ежемесячную сумму из актов
        act_rows = [r for r in cp_rows if r[1] in (OUTGOING_ACT, OUTGOING_UPD)]
        monthly_amount = 0.0
        if act_rows:
            amounts = [r[4] for r in act_rows if r[4] > 0]
            if amounts:
                from statistics import median
                monthly_amount = median(amounts)

        # Создать договор если нет
        existing_contract = con.execute(
            "SELECT id FROM contracts_v2 WHERE counterparty_id=? AND number=?",
            (cp_id, contract_number)
        ).fetchone()

        if not existing_contract:
            con.execute("""INSERT INTO contracts_v2
                (counterparty_id, number, date, subject, direction, amount_monthly, billing_day, billing_period)
                VALUES (?,?,?,?,?,?,?,?)""",
                (cp_id, contract_number, contract_date,
                 "Услуги по договору", "OUT", monthly_amount, 27, "month"))
            con.commit()
            existing_contract = con.execute(
                "SELECT id FROM contracts_v2 WHERE counterparty_id=? AND number=?",
                (cp_id, contract_number)
            ).fetchone()
            stats["contracts"] += 1

        contract_id = existing_contract[0]

        # Строим обязательства по периодам (из актов)
        for row in act_rows:
            doc_date, doc_type, number, _, amount, comment, operation = row
            if not doc_date: continue

            # Период = месяц акта
            period = doc_date.strftime("%Y-%m")
            ex = con.execute(
                "SELECT id FROM contract_obligations WHERE contract_id=? AND period=?",
                (contract_id, period)
            ).fetchone()
            if ex: continue

            # Ищем оплату в этом или следующем месяце
            pay_rows = [r for r in cp_rows if r[1] == INCOMING_PAYMENT and r[0] and
                        r[0].strftime("%Y-%m") in (period,
                        (doc_date.replace(day=1) + timedelta(days=32)).strftime("%Y-%m"))]
            pay_date_str = pay_rows[0][0].isoformat() if pay_rows else None
            status = "paid" if pay_date_str else "acted"

            # Ищем счёт
            bill_rows = [r for r in cp_rows if r[1] == OUTGOING_BILL and r[0] and
                         abs((r[0] - doc_date).days) < 45]
            bill_date_str = bill_rows[0][0].isoformat() if bill_rows else None

            con.execute("""INSERT INTO contract_obligations
                (contract_id, period, bill_date, act_date, pay_date, amount, status, notes)
                VALUES (?,?,?,?,?,?,?,?)""",
                (contract_id, period, bill_date_str,
                 doc_date.isoformat(), pay_date_str,
                 amount, status, comment))
            stats["obligations"] += 1

    con.commit(); con.close()
    return stats

# ─────────────────────────────────────────────────────────────────────────────
# Отчёты
# ─────────────────────────────────────────────────────────────────────────────

def report_list(db_path: Path):
    con = db(db_path)
    rows = con.execute("""
        SELECT c.id, c.number, c.date, c.amount_monthly, c.status, c.direction,
               cp.name as cp_name
        FROM contracts_v2 c
        JOIN counterparties cp ON c.counterparty_id = cp.id
        WHERE c.status = 'active'
        ORDER BY cp.name
    """).fetchall()
    print("\n" + "="*60)
    print("  АКТИВНЫЕ ДОГОВОРЫ  |  ООО «РАМЕВА»")
    print("="*60)
    if not rows:
        print("  Нет договоров. Добавьте через: python3 contracts.py add")
    else:
        for r in rows:
            arrow = "→" if r["direction"]=="OUT" else "←"
            print(f"  #{r['id']} {arrow} {r['cp_name']}")
            print(f"     №{r['number']} от {r['date'] or '—'}  |  {r['amount_monthly'] or 0:,.0f} ₽/мес")
    print("="*60 + "\n")
    con.close()

def report_card(db_path: Path, search: str):
    con = db(db_path)
    cp = con.execute("""SELECT id, name, inn, email, phone
        FROM counterparties WHERE name LIKE ? LIMIT 1""",
        (f"%{search}%",)).fetchone()
    if not cp:
        print(f"❌ Контрагент не найден: {search}"); con.close(); return

    print(f"\n{'='*60}")
    print(f"  КАРТОЧКА: {cp['name']}")
    print(f"  ИНН: {cp['inn'] or '—'}  |  {cp['email'] or '—'}  |  {cp['phone'] or '—'}")
    print(f"{'='*60}")

    # Договоры
    contracts = con.execute("""SELECT id, number, date, amount_monthly FROM contracts_v2
        WHERE counterparty_id=?""", (cp["id"],)).fetchall()
    for c in contracts:
        print(f"\n  Договор №{c['number']} от {c['date'] or '—'}  |  {c['amount_monthly'] or 0:,.0f} ₽/мес")

        # Обязательства — последние 12 периодов
        obls = con.execute("""SELECT period, bill_date, act_date, pay_date, amount, status
            FROM contract_obligations WHERE contract_id=?
            ORDER BY period DESC LIMIT 12""", (c["id"],)).fetchall()

        status_icon = {"paid":"✅","acted":"📋","billed":"📤","pending":"⏳","overdue":"🔴"}
        print(f"  {'Период':<8} {'Счёт':^10} {'Акт':^10} {'Оплата':^10} {'Сумма':>8}  Статус")
        print("  " + "─"*56)
        for o in obls:
            icon = status_icon.get(o["status"],"○")
            print(f"  {o['period']:<8} "
                  f"{(o['bill_date'] or '—')[:10]:^10} "
                  f"{(o['act_date'] or '—')[:10]:^10} "
                  f"{(o['pay_date'] or '—')[:10]:^10} "
                  f"{o['amount']:>8,.0f}  {icon} {o['status']}")

    # Банковские транзакции от контрагента
    txs = con.execute("""SELECT value_date, direction, amount, category
        FROM bank_transactions WHERE counterparty_inn=?
        ORDER BY value_date DESC LIMIT 10""", (cp["inn"],)).fetchall()
    if txs:
        print(f"\n  Последние платежи:")
        for t in txs:
            arrow = "←" if t["direction"]=="IN" else "→"
            print(f"  {t['value_date']}  {arrow}  {t['amount']:>10,.2f} ₽  [{t['category'] or '?'}]")

    print("="*60 + "\n")
    con.close()

def report_status(db_path: Path):
    """Что нужно сделать прямо сейчас по договорам."""
    today = date.today()
    con = db(db_path)
    current_period = today.strftime("%Y-%m")

    print(f"\n{'='*60}")
    print(f"  СТАТУС ДОГОВОРОВ  |  {today}")
    print("="*60)

    # Активные договоры — проверяем текущий период
    contracts = con.execute("""
        SELECT c.id, c.number, c.amount_monthly, c.billing_day, c.billing_period,
               cp.name as cp_name
        FROM contracts_v2 c
        JOIN counterparties cp ON c.counterparty_id = cp.id
        WHERE c.status='active' AND c.direction='OUT'
    """).fetchall()

    alerts = []
    for c in contracts:
        obl = con.execute("""SELECT status, bill_date, act_date, pay_date
            FROM contract_obligations WHERE contract_id=? AND period=?""",
            (c["id"], current_period)).fetchone()

        status = obl["status"] if obl else "pending"
        amount = c["amount_monthly"] or 0

        # Проверяем что нужно сделать
        bill_deadline = today.replace(day=min(c["billing_day"], 28))

        if not obl or status == "pending":
            if today >= bill_deadline:
                alerts.append(("🔴 ВЫСТАВИТЬ СЧЁТ", c["cp_name"],
                    f"{amount:,.0f} ₽/мес | Счёт должен быть выставлен до {bill_deadline}"))
        elif status == "billed":
            alerts.append(("📋 ОЖИДАЕМ АКТ", c["cp_name"],
                f"Счёт выставлен {obl['bill_date']}"))
        elif status == "acted":
            alerts.append(("💳 ОЖИДАЕМ ОПЛАТУ", c["cp_name"],
                f"Акт подписан {obl['act_date']} | {amount:,.0f} ₽"))

    # Просроченные из прошлых периодов
    overdue = con.execute("""
        SELECT o.period, o.amount, o.status, cp.name as cp_name
        FROM contract_obligations o
        JOIN contracts_v2 c ON o.contract_id = c.id
        JOIN counterparties cp ON c.counterparty_id = cp.id
        WHERE o.status NOT IN ('paid','cancelled')
          AND o.period < ?
        ORDER BY o.period DESC
    """, (current_period,)).fetchall()
    for o in overdue:
        alerts.append(("🔴 ПРОСРОЧЕНО", o["cp_name"],
            f"Период {o['period']} | {o['amount']:,.0f} ₽ | статус: {o['status']}"))

    if not alerts:
        print("  ✅ Все обязательства в порядке.")
    else:
        for flag, cp, detail in alerts:
            print(f"\n  {flag}")
            print(f"  Контрагент: {cp}")
            print(f"  {detail}")

    print("="*60 + "\n")
    con.close()

def report_receivables(db_path: Path):
    """Сводная дебиторка по договорам."""
    con = db(db_path)
    rows = con.execute("""
        SELECT cp.name, o.period, o.amount, o.status, o.pay_date
        FROM contract_obligations o
        JOIN contracts_v2 c ON o.contract_id = c.id
        JOIN counterparties cp ON c.counterparty_id = cp.id
        WHERE o.status NOT IN ('paid','cancelled')
        ORDER BY cp.name, o.period DESC
    """).fetchall()
    total = sum(r["amount"] for r in rows)
    print(f"\n  ДЕБИТОРКА ПО ДОГОВОРАМ:  {total:,.2f} ₽\n")
    for r in rows:
        print(f"  {r['name']:<35} {r['period']}  {r['amount']:>8,.0f} ₽  [{r['status']}]")
    print()
    con.close()

# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Управление договорами ООО «РАМЕВА»")
    parser.add_argument("command", choices=["list","card","status","import","receivables"],
                        help="Команда")
    parser.add_argument("arg", nargs="?", help="Аргумент (имя контрагента или файл)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    if not args.db.exists():
        print(f"❌ БД не найдена: {args.db}"); sys.exit(1)

    init_tables(args.db)

    if args.command == "list":
        report_list(args.db)
    elif args.command == "card":
        if not args.arg: print("Укажите имя контрагента"); sys.exit(1)
        report_card(args.db, args.arg)
    elif args.command == "status":
        report_status(args.db)
    elif args.command == "receivables":
        report_receivables(args.db)
    elif args.command == "import":
        if not args.arg: print("Укажите файл реестра"); sys.exit(1)
        path = Path(args.arg)
        if not path.exists(): print(f"❌ Файл не найден: {path}"); sys.exit(1)
        print(f"📂 Импорт истории из {path.name}...")
        stats = import_from_registry(args.db, path)
        print(f"✅ Обработано контрагентов: {stats['counterparties']}")
        print(f"   Договоров создано:       {stats['contracts']}")
        print(f"   Обязательств загружено:  {stats['obligations']}")

if __name__ == "__main__":
    main()
