"""
parse_registry.py — импорт реестра документов из Контур.Экстерн (CSV)
Заполняет таблицы documents и counterparties в БД.

Запуск:
    python3 parse_registry.py <РеестрВсеДокументы....csv> [--db rameva.db] [--dry-run]
"""

import csv
import sqlite3
import argparse
import sys
import re
from pathlib import Path
from datetime import datetime

DEFAULT_DB = Path("~/.hermes/skills/rameva/rameva.db").expanduser()

# Маппинг типов документов из реестра → внутренние типы БД
DOC_TYPE_MAP = {
    "Исходящее платежное поручение": "payment_out",
    "Входящее платежное поручение":  "payment_in",
    "Входящий УПД":                  "upd_in",
    "Исходящий УПД":                 "upd_out",
    "Входящий счёт":                 "bill_in",
    "Исходящий счёт":                "bill_out",
    "Входящий акт":                  "act_in",
    "Исходящий акт":                 "act_out",
    "Бухгалтерская справка":         "accounting_note",
    "Уплата налогов и взносов":      "tax_payment",
    "Распределение денег с ЕНС":     "ens_distribution",
    "Счёт-фактура":          "invoice",
    "Банковский ордер":             "bank_order",
    "Исходящий счет на оплату":     "bill_out",
    "Входящий счет на оплату":      "bill_in",
    "Исходящий акт приемки услуг":  "act_out",
    "Входящий акт приемки услуг":   "act_in",
    "Исходящий договор":            "contract_out",
    "Входящий договор":             "contract_in",
    "Начисление налога":            "tax_accrual",
    "Списание ТМЦ на затраты":      "writeoff",
    "Авансовый отчет":              "expense_report",
    "Возврат средств бюджетом и фондами": "refund_in",
    "Входящий счёт-фактура":         "invoice_in",
    "Исходящий счёт-фактура":        "invoice_out",
}

# Направление по типу
DOC_DIRECTION = {
    "payment_out":      "OUT",
    "payment_in":       "IN",
    "upd_in":           "IN",
    "upd_out":          "OUT",
    "bill_in":          "IN",
    "bill_out":         "OUT",
    "act_in":           "IN",
    "act_out":          "OUT",
    "accounting_note":  None,
    "tax_payment":      "OUT",
    "ens_distribution": None,
    "invoice":          None,
    "invoice_in":       "IN",
    "invoice_out":      "OUT",
    "bank_order":       "OUT",
    "contract_out":     "OUT",
    "contract_in":      "IN",
    "tax_accrual":      None,
    "writeoff":         None,
    "expense_report":   "OUT",
}


def parse_amount(s: str) -> float:
    if not s or not s.strip():
        return 0.0
    clean = s.strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    m = re.search(r"[\d.]+", clean)
    return float(m.group()) if m else 0.0


def parse_date(s: str) -> str:
    """DD.MM.YYYY → YYYY-MM-DD"""
    if not s or not s.strip():
        return ""
    s = s.strip()
    try:
        return datetime.strptime(s, "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        return s


def read_registry_csv(filepath: Path) -> tuple[dict, list[dict]]:
    """
    Читает реестр документов из CSV Контур.Экстерн.
    Возвращает (header_info, rows).
    """
    with open(filepath, encoding="windows-1251") as f:
        content = f.read()

    lines = content.splitlines()
    header_info = {}
    rows = []
    col_names = []
    data_started = False

    for line in lines:
        parts = [p.strip().strip('"').replace('""', '"') for p in line.split(";")]

        if not data_started:
            text = parts[0]
            if "Сформировано" in text:
                m = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
                if m:
                    header_info["generated"] = m.group(1)
            elif "Период:" in text:
                header_info["period"] = text.replace("Период:", "").strip()
            elif "Организация:" in text:
                header_info["org"] = text.replace("Организация:", "").strip()
            elif "Дата" in parts[0] and "Документ" in " ".join(parts):
                # Строка заголовков колонок
                col_names = parts
                data_started = True
            continue

        if not any(p.strip() for p in parts):
            continue

        row = dict(zip(col_names, parts))
        rows.append(row)

    return header_info, rows


def get_or_create_counterparty(con, name):
    """Находит контрагента по имени или создаёт нового. Возвращает id."""
    if not name or not name.strip():
        return None
    name = name.strip()
    row = con.execute(
        "SELECT id FROM counterparties WHERE name=?", (name,)
    ).fetchone()
    if row:
        return row[0]
    # Определяем тип по имени
    cp_type = "other"
    if any(k in name.upper() for k in ["ООО", "АО", "ПАО", "ИП ", "АКЦИОНЕРНОЕ", "ОБЩЕСТВО"]):
        cp_type = "supplier"
    elif name == "Казначейство России" or "ФНС" in name or "УФК" in name:
        cp_type = "fund"
    elif "СФР" in name or "ФСС" in name or "ПФР" in name:
        cp_type = "fund"
    cur = con.execute(
        "INSERT INTO counterparties (name, type) VALUES (?, ?) RETURNING id",
        (name, cp_type)
    )
    return cur.fetchone()[0]


# Workaround для Optional int (не импортируем typing лишний раз)


def import_registry(db_path: Path, rows: list[dict], dry_run: bool = False) -> tuple[int, int, int]:
    """
    Импортирует документы в таблицу documents.
    Возвращает (добавлено, обновлено, пропущено).
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    added = updated = skipped = 0
    new_counterparties = []

    for row in rows:
        # Извлекаем поля
        doc_date      = parse_date(row.get("Дата", ""))
        create_date   = parse_date(row.get("Дата создания", ""))
        doc_type_raw  = row.get("Документ", "").strip()
        operation     = row.get("Операция", "").strip()
        number        = row.get("Номер", "").strip()
        cp_name       = row.get("Контрагент", "").strip()
        invoice_ref   = row.get("Счет-фактура", "").strip()
        amount_str    = row.get("Всего, ?", row.get("Всего,  ?", "")).strip()
        comment       = row.get("Комментарий", "").strip()
        creator_login = row.get("Логин создателя", "").strip()
        creator_name  = row.get("ФИО в КБ", "").strip()

        if not doc_type_raw:
            skipped += 1
            continue

        amount    = parse_amount(amount_str)
        doc_type  = DOC_TYPE_MAP.get(doc_type_raw, "other")
        direction = DOC_DIRECTION.get(doc_type)
        status    = "draft" if operation == "Не проведен" else "signed"

        # Контрагент
        cp_id = None
        if cp_name and not dry_run:
            cp_id = get_or_create_counterparty(con, cp_name)
            if cp_name not in new_counterparties:
                new_counterparties.append(cp_name)

        # Дедупликация по типу + номеру + дате
        dedup_key = f"{doc_type}|{number}|{doc_date}"
        existing = con.execute("""
            SELECT id FROM documents WHERE type=? AND number=? AND date=?
        """, (doc_type, number, doc_date)).fetchone()

        if existing:
            skipped += 1
            continue

        if not dry_run:
            con.execute("""
                INSERT INTO documents
                    (type, number, date, counterparty_id, amount, direction,
                     status, diadok_id, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_type, number, doc_date, cp_id, amount, direction,
                status,
                invoice_ref if invoice_ref else None,
                f"{comment} | {creator_name}".strip(" |") if comment or creator_name else None,
            ))
        added += 1

    if not dry_run:
        con.commit()
    con.close()

    return added, updated, skipped, new_counterparties


def print_registry_summary(rows: list[dict]):
    """Статистика по загруженному реестру."""
    from collections import Counter
    types = Counter(r.get("Документ", "?") for r in rows)
    years = Counter()
    for r in rows:
        d = r.get("Дата", "")
        if d:
            m = re.search(r"(\d{4})", d)
            if m:
                years[m.group(1)] += 1

    print()
    print("=" * 56)
    print("  РЕЕСТР ДОКУМЕНТОВ — ООО «РАМЕВА»")
    print("=" * 56)
    print(f"  Всего документов: {len(rows)}")
    print()
    print("  По типам:")
    for tp, cnt in types.most_common():
        label = DOC_TYPE_MAP.get(tp, tp)
        print(f"  {label:<35} {cnt:>3}")
    print()
    print("  По годам:")
    for yr, cnt in sorted(years.items(), reverse=True):
        print(f"  {yr}: {cnt} документов")
    print("=" * 56)
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Импорт реестра документов Контур.Экстерн — ООО «РАМЕВА»"
    )
    parser.add_argument("file", type=Path, help="CSV файл реестра")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true",
                        help="Только анализ, без записи в БД")
    args = parser.parse_args()

    if not args.db.exists() and not args.dry_run:
        print(f"❌ БД не найдена: {args.db}"); sys.exit(1)

    print(f"📂 Читаю реестр: {args.file.name}")
    header_info, rows = read_registry_csv(args.file)

    print(f"   Сформирован: {header_info.get('generated')}")
    print(f"   Период: {header_info.get('period')}")
    print(f"   Организация: {header_info.get('org')}")

    print_registry_summary(rows)

    if args.dry_run:
        print("ℹ️  Режим dry-run. В БД ничего не записано.")
        return

    added, updated, skipped, new_cps = import_registry(args.db, rows)
    print(f"✅ Импорт завершён:")
    print(f"   Добавлено документов:    {added}")
    print(f"   Пропущено (дубли):       {skipped}")
    if new_cps:
        unique_cps = list(dict.fromkeys(new_cps))
        print(f"   Новых контрагентов:      {len(unique_cps)}")
        for cp in unique_cps[:10]:
            print(f"     • {cp}")
        if len(unique_cps) > 10:
            print(f"     ... и ещё {len(unique_cps)-10}")
    print()


if __name__ == "__main__":
    main()
