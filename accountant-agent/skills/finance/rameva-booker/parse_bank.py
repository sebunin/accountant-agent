"""
parse_bank.py — парсер банковских выписок в формате 1CClientBankExchange
Поддерживаемый формат: версия 1.01, кодировка Windows-1251 (ПСБ)

Запуск:
    python3 parse_bank.py выписка.txt [--db rameva.db] [--dry-run]
"""

import re
import sqlite3
import hashlib
import argparse
import sys
from pathlib import Path
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_DB = Path("~/.hermes/skills/rameva/rameva.db").expanduser()
OWN_ACCOUNT = "40702810602000129531"   # расчётный счёт ООО "РАМЕВА"


# ---------------------------------------------------------------------------
# Модели данных
# ---------------------------------------------------------------------------

@dataclass
class BankHeader:
    period_from: Optional[date] = None
    period_to: Optional[date] = None
    account: str = ""
    balance_open: float = 0.0
    balance_close: float = 0.0
    total_in: float = 0.0
    total_out: float = 0.0


@dataclass
class BankTransaction:
    doc_type: str = ""
    doc_number: str = ""
    doc_date: Optional[date] = None
    value_date: Optional[date] = None
    amount: float = 0.0
    direction: str = ""          # 'IN' или 'OUT'
    counterparty: str = ""
    counterparty_inn: str = ""
    counterparty_account: str = ""
    purpose: str = ""
    kbk: str = ""
    oktmo: str = ""
    payment_basis: str = ""
    payment_period: str = ""
    code_nazn: str = ""          # КодНазПлатежа (1=зп, 2=алименты)
    priority: str = ""
    source_file: str = ""


# ---------------------------------------------------------------------------
# Парсер
# ---------------------------------------------------------------------------

def parse_date(s: str) -> Optional[date]:
    """Конвертирует дату формата DD.MM.YYYY в date."""
    if not s or s.strip() == "":
        return None
    s = s.strip()
    try:
        return datetime.strptime(s, "%d.%m.%Y").date()
    except ValueError:
        return None


def parse_amount(s: str) -> float:
    if not s:
        return 0.0
    return float(s.strip().replace(",", "."))


def parse_1c_file(filepath: Path) -> tuple[BankHeader, list[BankTransaction]]:
    """
    Читает файл формата 1CClientBankExchange.
    Возвращает заголовок и список транзакций.
    """
    try:
        text = filepath.read_text(encoding="windows-1251")
    except UnicodeDecodeError:
        text = filepath.read_text(encoding="utf-8")

    lines = text.splitlines()
    header = BankHeader()
    transactions = []

    # --- Разбираем заголовок файла и секцию РасчСчет ---
    in_rassch = False
    section_data: dict[str, str] = {}
    current_doc: Optional[dict[str, str]] = None

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line == "КонецФайла":
            break

        # Начало секции документа
        if line.startswith("СекцияДокумент="):
            if current_doc is not None:
                transactions.append(_build_tx(current_doc, filepath.name))
            doc_type = line.split("=", 1)[1]
            current_doc = {"_тип": doc_type}
            i += 1
            continue

        if line == "КонецДокумента":
            if current_doc is not None:
                transactions.append(_build_tx(current_doc, filepath.name))
                current_doc = None
            i += 1
            continue

        if line == "СекцияРасчСчет":
            in_rassch = True
            i += 1
            continue

        if line == "КонецРасчСчет":
            in_rassch = False
            # Разбираем итоги секции расчётного счёта
            header.balance_open  = parse_amount(section_data.get("НачальныйОстаток", "0"))
            header.balance_close = parse_amount(section_data.get("КонечныйОстаток", "0"))
            header.total_in      = parse_amount(section_data.get("ВсегоПоступило", "0"))
            header.total_out     = parse_amount(section_data.get("ВсегоСписано", "0"))
            header.account       = section_data.get("РасчСчет", "")
            i += 1
            continue

        # Ключ=Значение
        if "=" in line:
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip()

            if in_rassch:
                section_data[key] = value
            elif current_doc is not None:
                current_doc[key] = value
            else:
                # Заголовок файла
                if key == "ДатаНачала":
                    header.period_from = parse_date(value)
                elif key == "ДатаКонца":
                    header.period_to = parse_date(value)
                elif key == "РасчСчет":
                    header.account = value

        i += 1

    return header, transactions


def _build_tx(doc: dict[str, str], source_file: str) -> BankTransaction:
    """Конструирует BankTransaction из словаря полей документа."""
    tx = BankTransaction()
    tx.doc_type     = doc.get("_тип", "")
    tx.doc_number   = doc.get("Номер", "")
    tx.doc_date     = parse_date(doc.get("Дата", ""))
    tx.amount       = parse_amount(doc.get("Сумма", "0"))
    tx.purpose      = doc.get("НазначениеПлатежа", "")
    tx.kbk          = doc.get("ПоказательКБК", "")
    tx.oktmo        = doc.get("ОКАТО", "")
    tx.payment_basis  = doc.get("ПоказательОснования", "")
    tx.payment_period = doc.get("ПоказательПериода", "")
    tx.code_nazn    = doc.get("КодНазПлатежа", "")
    tx.priority     = doc.get("Очередность", "")
    tx.source_file  = source_file

    payer_account   = doc.get("ПлательщикСчет", "")
    receiver_account = doc.get("ПолучательСчет", "")

    if receiver_account == OWN_ACCOUNT:
        # Входящий платёж
        tx.direction          = "IN"
        tx.value_date         = parse_date(doc.get("ДатаПоступило", ""))
        tx.counterparty       = doc.get("Плательщик1", "")
        tx.counterparty_inn   = doc.get("ПлательщикИНН", "")
        tx.counterparty_account = payer_account
    else:
        # Исходящий платёж
        tx.direction          = "OUT"
        tx.value_date         = parse_date(doc.get("ДатаСписано", ""))
        tx.counterparty       = doc.get("Получатель1", "")
        tx.counterparty_inn   = doc.get("ПолучательИНН", "")
        tx.counterparty_account = receiver_account

    return tx


def make_dedup_key(tx: BankTransaction) -> str:
    """Уникальный ключ для дедупликации при повторном импорте."""
    raw = f"{tx.doc_number}|{tx.doc_date}|{tx.amount:.2f}|{tx.direction}|{tx.counterparty_inn}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Запись в БД
# ---------------------------------------------------------------------------

def import_to_db(
    db_path: Path,
    header: BankHeader,
    transactions: list[BankTransaction],
    source_file: str,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    Записывает транзакции в БД. Возвращает (добавлено, пропущено).
    """
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    cur = con.cursor()

    added = 0
    skipped = 0

    for tx in transactions:
        key = make_dedup_key(tx)
        try:
            if not dry_run:
                cur.execute("""
                    INSERT INTO bank_transactions (
                        doc_type, doc_number, doc_date, value_date,
                        amount, direction, counterparty, counterparty_inn,
                        counterparty_account, purpose, kbk, oktmo,
                        payment_basis, payment_period, code_nazn, priority,
                        source_file, dedup_key
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    tx.doc_type,
                    tx.doc_number,
                    tx.doc_date.isoformat() if tx.doc_date else None,
                    tx.value_date.isoformat() if tx.value_date else None,
                    tx.amount,
                    tx.direction,
                    tx.counterparty,
                    tx.counterparty_inn,
                    tx.counterparty_account,
                    tx.purpose,
                    tx.kbk,
                    tx.oktmo,
                    tx.payment_basis,
                    tx.payment_period,
                    tx.code_nazn,
                    tx.priority,
                    tx.source_file,
                    key,
                ))
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1

    if not dry_run:
        # Обновляем состояние баланса
        cur.execute("""
            INSERT OR REPLACE INTO state (key, value, updated)
            VALUES ('balance_current', ?, datetime('now','localtime'))
        """, (str(header.balance_close),))
        cur.execute("""
            INSERT OR REPLACE INTO state (key, value, updated)
            VALUES ('balance_date', ?, datetime('now','localtime'))
        """, (header.period_to.isoformat() if header.period_to else "",))
        cur.execute("""
            INSERT OR REPLACE INTO state (key, value, updated)
            VALUES ('last_import_file', ?, datetime('now','localtime'))
        """, (source_file,))

        # Журнал импорта
        cur.execute("""
            INSERT INTO import_log (
                filename, period_from, period_to, account,
                balance_open, balance_close, total_in, total_out,
                tx_added, tx_skipped
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            source_file,
            header.period_from.isoformat() if header.period_from else None,
            header.period_to.isoformat() if header.period_to else None,
            header.account,
            header.balance_open,
            header.balance_close,
            header.total_in,
            header.total_out,
            added,
            skipped,
        ))

        con.commit()

    con.close()
    return added, skipped


# ---------------------------------------------------------------------------
# Отчёт о результате
# ---------------------------------------------------------------------------

def print_summary(header: BankHeader, transactions: list[BankTransaction], added: int, skipped: int):
    print()
    print("=" * 56)
    print("  ИМПОРТ БАНКОВСКОЙ ВЫПИСКИ — ООО «РАМЕВА»")
    print("=" * 56)
    print(f"  Счёт:       {header.account}")
    print(f"  Период:     {header.period_from} — {header.period_to}")
    print(f"  Остаток нач:{header.balance_open:>14,.2f} руб.")
    print(f"  Поступило:  {header.total_in:>14,.2f} руб.")
    print(f"  Списано:    {header.total_out:>14,.2f} руб.")
    print(f"  Остаток кон:{header.balance_close:>14,.2f} руб.")
    print("-" * 56)
    print(f"  Документов в файле: {len(transactions)}")
    print(f"  ✅ Добавлено:       {added}")
    print(f"  ⏭  Пропущено (дубли): {skipped}")
    print("=" * 56)
    print()
    print("  Транзакции:")
    for tx in transactions:
        arrow = "→" if tx.direction == "OUT" else "←"
        vdate = tx.value_date or tx.doc_date
        cp = (tx.counterparty[:30] + "…") if len(tx.counterparty) > 31 else tx.counterparty
        print(f"  {vdate} {arrow} {tx.amount:>12,.2f}  {cp}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Импорт банковской выписки 1C в БД субагента Рамева"
    )
    parser.add_argument("file", type=Path, help="Файл выписки (.txt, формат 1CClientBankExchange)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Путь к SQLite БД")
    parser.add_argument("--dry-run", action="store_true", help="Только парсинг, без записи в БД")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"❌ Файл не найден: {args.file}")
        sys.exit(1)

    print(f"📂 Читаю: {args.file.name}")
    header, transactions = parse_1c_file(args.file)

    if args.dry_run:
        added, skipped = len(transactions), 0
        print("ℹ️  Режим dry-run: в БД ничего не записывается")
    else:
        if not args.db.exists():
            print(f"❌ БД не найдена: {args.db}")
            print("   Сначала выполните: python3 init_db.py")
            sys.exit(1)
        added, skipped = import_to_db(args.db, header, transactions, args.file.name)

    print_summary(header, transactions, added, skipped)

    if skipped > 0:
        print(f"ℹ️  {skipped} транзакций уже были в БД (дедупликация по ключу).")
    if added > 0 and not args.dry_run:
        print(f"✅ Импорт завершён. Запустите classify.py для разметки категорий.")


if __name__ == "__main__":
    main()
