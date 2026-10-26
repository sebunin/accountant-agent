"""
init_db.py — инициализация базы данных субагента-бухгалтера ООО "РАМЕВА"
Запуск: python3 init_db.py [--db путь/к/rameva.db]
"""

import sqlite3
import os
import sys
import argparse
from pathlib import Path

DEFAULT_DB = Path("~/.hermes/skills/rameva/rameva.db").expanduser()


def init_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    cur = con.cursor()

    cur.executescript("""
    -- =========================================================
    -- Банковские транзакции (основа всего учёта на АУСН)
    -- =========================================================
    CREATE TABLE IF NOT EXISTS bank_transactions (
        id                  INTEGER PRIMARY KEY,
        doc_type            TEXT NOT NULL,      -- 'Платежное поручение' / 'Банковский ордер'
        doc_number          TEXT,
        doc_date            TEXT,               -- дата документа YYYY-MM-DD
        value_date          TEXT,               -- дата зачисления/списания YYYY-MM-DD
        amount              REAL NOT NULL,
        direction           TEXT NOT NULL CHECK(direction IN ('IN','OUT')),
        counterparty        TEXT,
        counterparty_inn    TEXT,
        counterparty_account TEXT,
        purpose             TEXT,               -- назначение платежа
        kbk                 TEXT,
        oktmo               TEXT,
        payment_basis       TEXT,               -- ПоказательОснования
        payment_period      TEXT,               -- ПоказательПериода
        code_nazn           TEXT,               -- КодНазПлатежа (1=зп, 2=алименты)
        priority            TEXT,               -- Очередность
        category            TEXT,               -- классификация (см. classify.py)
        taxable             INTEGER DEFAULT 1,  -- 1=учитывается в базе АУСН, 0=нет
        linked_doc_id       INTEGER,
        imported_at         TEXT DEFAULT (datetime('now','localtime')),
        source_file         TEXT,
        dedup_key           TEXT UNIQUE,        -- doc_number+doc_date+amount+direction
        notes               TEXT,
        FOREIGN KEY (linked_doc_id) REFERENCES documents(id)
    );
    CREATE INDEX IF NOT EXISTS idx_tx_date      ON bank_transactions(value_date);
    CREATE INDEX IF NOT EXISTS idx_tx_category  ON bank_transactions(category);
    CREATE INDEX IF NOT EXISTS idx_tx_inn       ON bank_transactions(counterparty_inn);

    -- =========================================================
    -- Контрагенты
    -- =========================================================
    CREATE TABLE IF NOT EXISTS counterparties (
        id          INTEGER PRIMARY KEY,
        name        TEXT NOT NULL,
        inn         TEXT,
        kpp         TEXT,
        type        TEXT DEFAULT 'other',
                    -- 'client' / 'supplier' / 'fund' / 'bank' / 'employee' / 'other'
        diadok_id   TEXT,
        account     TEXT,
        bank_bik    TEXT,
        notes       TEXT,
        created_at  TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_cp_inn ON counterparties(inn) WHERE inn IS NOT NULL;

    -- =========================================================
    -- Договоры
    -- =========================================================
    CREATE TABLE IF NOT EXISTS contracts (
        id              INTEGER PRIMARY KEY,
        counterparty_id INTEGER,
        number          TEXT,
        date            TEXT,
        subject         TEXT,
        amount          REAL,
        type            TEXT DEFAULT 'service',
                        -- 'service' / 'supply' / 'gpc' / 'license' / 'hosting'
        status          TEXT DEFAULT 'active',
                        -- 'active' / 'closed' / 'suspended'
        payment_terms   TEXT,
        notes           TEXT,
        created_at      TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (counterparty_id) REFERENCES counterparties(id)
    );

    -- =========================================================
    -- Первичные документы (акты, счета, УПД, счета-фактуры)
    -- =========================================================
    CREATE TABLE IF NOT EXISTS documents (
        id              INTEGER PRIMARY KEY,
        type            TEXT NOT NULL,
                        -- 'act' / 'invoice' / 'upd' / 'bill' / 'gpc_act'
        number          TEXT,
        date            TEXT,
        counterparty_id INTEGER,
        contract_id     INTEGER,
        amount          REAL,
        vat             REAL DEFAULT 0,
        direction       TEXT CHECK(direction IN ('IN','OUT')),
        status          TEXT DEFAULT 'draft',
                        -- 'draft' / 'sent' / 'signed' / 'rejected' / 'paid'
        diadok_id       TEXT,
        linked_tx_id    INTEGER,
        imported_at     TEXT DEFAULT (datetime('now','localtime')),
        notes           TEXT,
        FOREIGN KEY (counterparty_id) REFERENCES counterparties(id),
        FOREIGN KEY (contract_id) REFERENCES contracts(id),
        FOREIGN KEY (linked_tx_id) REFERENCES bank_transactions(id)
    );

    -- =========================================================
    -- Расчёт заработной платы
    -- =========================================================
    CREATE TABLE IF NOT EXISTS payroll (
        id              INTEGER PRIMARY KEY,
        period          TEXT NOT NULL,          -- 'YYYY-MM'
        employee_name   TEXT NOT NULL,
        type            TEXT DEFAULT 'salary',
                        -- 'salary' / 'advance' / 'vacation' / 'gpc' / 'alimony'
        gross           REAL,
        ndfl            REAL DEFAULT 0,
        alimony         REAL DEFAULT 0,
        sfr_ns          REAL DEFAULT 0,
        net             REAL,
        paid_date       TEXT,
        tx_id           INTEGER,
        notes           TEXT,
        FOREIGN KEY (tx_id) REFERENCES bank_transactions(id)
    );
    CREATE INDEX IF NOT EXISTS idx_payroll_period ON payroll(period);

    -- =========================================================
    -- Налоговые обязательства и платежи
    -- =========================================================
    CREATE TABLE IF NOT EXISTS tax_obligations (
        id          INTEGER PRIMARY KEY,
        period      TEXT NOT NULL,              -- 'YYYY-MM'
        type        TEXT NOT NULL,
                    -- 'АУСН' / 'ЕНП' / 'СФР_НС' / 'НДФЛ'
        amount      REAL,
        due_date    TEXT,
        paid_date   TEXT,
        paid_amount REAL DEFAULT 0,
        tx_id       INTEGER,
        status      TEXT DEFAULT 'pending',
                    -- 'pending' / 'paid' / 'overpaid' / 'overdue'
        notes       TEXT,
        FOREIGN KEY (tx_id) REFERENCES bank_transactions(id)
    );
    CREATE INDEX IF NOT EXISTS idx_tax_period ON tax_obligations(period);
    CREATE INDEX IF NOT EXISTS idx_tax_status ON tax_obligations(status);

    -- =========================================================
    -- Задачи и напоминания
    -- =========================================================
    CREATE TABLE IF NOT EXISTS tasks (
        id          INTEGER PRIMARY KEY,
        title       TEXT NOT NULL,
        category    TEXT DEFAULT 'other',
                    -- 'payment' / 'document' / 'report' / 'check' / 'other'
        due_date    TEXT,
        priority    TEXT DEFAULT 'normal',
                    -- 'high' / 'normal' / 'low'
        status      TEXT DEFAULT 'open',
                    -- 'open' / 'done' / 'snoozed'
        related_id  INTEGER,                   -- id связанной транзакции или документа
        notes       TEXT,
        created_at  TEXT DEFAULT (datetime('now','localtime')),
        updated_at  TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_due    ON tasks(due_date);
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

    -- =========================================================
    -- Сводное состояние (ключ → значение)
    -- =========================================================
    CREATE TABLE IF NOT EXISTS state (
        key     TEXT PRIMARY KEY,
        value   TEXT,
        updated TEXT DEFAULT (datetime('now','localtime'))
    );

    -- =========================================================
    -- Журнал импорта выписок
    -- =========================================================
    CREATE TABLE IF NOT EXISTS import_log (
        id          INTEGER PRIMARY KEY,
        filename    TEXT,
        period_from TEXT,
        period_to   TEXT,
        account     TEXT,
        balance_open  REAL,
        balance_close REAL,
        total_in      REAL,
        total_out     REAL,
        tx_added    INTEGER DEFAULT 0,
        tx_skipped  INTEGER DEFAULT 0,
        imported_at TEXT DEFAULT (datetime('now','localtime'))
    );
    """)

    # Заполнить справочник известных контрагентов из банковской выписки
    known_counterparties = [
        # (name, inn, type, notes)
        ('ООО "ГЛАВСКЛАД"', '5024243121', 'client', 'Оплата хостинга и техсопровождения'),
        ('АКЦИОНЕРНОЕ ОБЩЕСТВО "ИОТ"', '7731399513', 'supplier', 'Платёж по счёту 247581'),
        ('УФК по Липецкой области (ОСФР)', '4826004782', 'fund', 'СФР взносы НС и ПЗ'),
        ('Казначейство России (ФНС России)', '7727406020', 'fund', 'ЕНП'),
        ('ПАО "Банк ПСБ"', '7744000912', 'bank', 'Комиссии банка'),
        ('Бунин Сергей Юрьевич', '482505236204', 'employee', 'Генеральный директор, 0.25 ставки'),
        ('Бунина Ольга Юрьевна', '482417130223', 'other', 'Получатель алиментов'),
    ]

    for name, inn, ctype, notes in known_counterparties:
        cur.execute("""
            INSERT OR IGNORE INTO counterparties (name, inn, type, notes)
            VALUES (?, ?, ?, ?)
        """, (name, inn, ctype, notes))

    # Начальное состояние
    initial_state = {
        'balance_current': '0',
        'balance_date': '',
        'period_income': '0',
        'period_expense': '0',
        'period_tax_base': '0',
        'period_ausn_forecast': '0',
        'receivables_total': '0',
        'payables_total': '0',
        'last_import_date': '',
        'last_import_file': '',
    }
    for key, value in initial_state.items():
        cur.execute("""
            INSERT OR IGNORE INTO state (key, value) VALUES (?, ?)
        """, (key, value))

    con.commit()
    con.close()
    print(f"✅ База данных создана: {db_path}")
    print(f"   Таблицы: bank_transactions, counterparties, contracts,")
    print(f"            documents, payroll, tax_obligations, tasks, state, import_log")
    print(f"   Контрагентов из справочника: {len(known_counterparties)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Инициализация БД субагента Рамева")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Путь к файлу БД")
    args = parser.parse_args()
    init_db(args.db)
