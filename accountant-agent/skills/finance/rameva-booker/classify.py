"""
classify.py — классификатор банковских транзакций для АУСН 8%
Размечает категории и признак налогооблагаемости каждой транзакции.

Запуск:
    python3 classify.py [--db rameva.db] [--unclassified] [--force]
"""

import sqlite3
import argparse
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

DEFAULT_DB = Path("~/.hermes/skills/rameva/rameva.db").expanduser()

# ---------------------------------------------------------------------------
# Справочник категорий
# ---------------------------------------------------------------------------

CATEGORIES = {
    # Код          : (название,                     taxable, direction)
    "revenue_it"   : ("Выручка IT/хостинг",          True,  "IN"),
    "revenue_other": ("Выручка прочая",               True,  "IN"),
    "salary"       : ("Зарплата",                     True,  "OUT"),   # расход АУСН
    "alimony"      : ("Алименты",                     False, "OUT"),   # не расход (из net)
    "ndfl"         : ("НДФЛ",                         False, "OUT"),   # уже учтён в зарплате
    "sfr_ns"       : ("Взносы СФР НС и ПЗ",          True,  "OUT"),   # расход АУСН
    "enp"          : ("ЕНП (налоги)",                 False, "OUT"),   # не расход
    "bank_fee"     : ("Комиссия банка",               True,  "OUT"),   # расход АУСН
    "loan_in"      : ("Получение займа",              False, "IN"),    # не доход
    "loan_out"     : ("Возврат/выдача займа",         False, "OUT"),   # не расход
    "supplier"     : ("Оплата поставщику",            True,  "OUT"),   # расход АУСН
    "refund_in"    : ("Возврат от поставщика",        False, "IN"),    # не доход (уменьшает расход)
    "refund_out"   : ("Возврат покупателю",           False, "OUT"),   # уменьшает доход
    "internal"     : ("Внутренний перевод/транзит",   False, "ANY"),
    "unclassified" : ("Не классифицировано",          None,  "ANY"),
}


# ---------------------------------------------------------------------------
# Правила классификации (порядок важен — первое совпадение побеждает)
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    category: str
    taxable: bool

    # Условия (все непустые должны совпасть)
    direction: Optional[str] = None       # 'IN' / 'OUT'
    kbk_prefix: Optional[str] = None     # начало КБК
    code_nazn: Optional[str] = None      # КодНазПлатежа (1=зп, 2=алименты)
    purpose_kw: Optional[list] = None    # ключевые слова в назначении (ANY)
    counterparty_inn: Optional[str] = None
    own_inn: str = "[INN]"          # ИНН ООО "РАМЕВА"

    def matches(self, tx: dict) -> bool:
        if self.direction and tx["direction"] != self.direction:
            return False
        if self.kbk_prefix and not (tx.get("kbk") or "").startswith(self.kbk_prefix):
            return False
        if self.code_nazn and tx.get("code_nazn") != self.code_nazn:
            return False
        if self.counterparty_inn and tx.get("counterparty_inn") != self.counterparty_inn:
            return False
        if self.purpose_kw:
            purpose = (tx.get("purpose") or "").lower()
            if not any(kw.lower() in purpose for kw in self.purpose_kw):
                return False
        return True


RULES: list[Rule] = [
    # --- Исходящие: взносы и налоги ---
    Rule("sfr_ns",    False, direction="OUT", kbk_prefix="7971021"),
    Rule("enp",       False, direction="OUT", kbk_prefix="1820106120101"),
    Rule("enp",       False, direction="OUT", purpose_kw=["енп", "единый налоговый"]),

    # --- Исходящие: зарплата и алименты (по КодНазПлатежа) ---
    Rule("salary",    True,  direction="OUT", code_nazn="1"),
    Rule("alimony",   False, direction="OUT", code_nazn="2"),

    # --- Исходящие: алименты (по назначению, если нет КодНазПлатежа) ---
    Rule("alimony",   False, direction="OUT",
         purpose_kw=["алимент"]),

    # --- Исходящие: зарплата по назначению ---
    Rule("salary",    True,  direction="OUT",
         purpose_kw=["заработная плата", "зарплата", "перевод заработной"]),

    # --- Исходящие: комиссия банка ---
    Rule("bank_fee",  True,  direction="OUT",
         purpose_kw=["комиссия за", "плата за", "обслуживание счёт",
                     "зачисление безналичных", "ведение счёт"]),

    # --- Исходящие: поставщики (общее правило) ---
    Rule("supplier",  True,  direction="OUT",
         purpose_kw=["оплат", "по счёт", "по счет", "по договор",
                     "предоплата", "аванс по"]),

    # --- Входящие: выручка IT ---
    Rule("revenue_it", True, direction="IN",
         purpose_kw=["хостинг", "сопровождение", "техническое сопровождение",
                     "программ", "сайт", "веб", "web", "разработка",
                     "интеграция", "домен", "сервер", "vps", "ssl"]),

    # --- Входящие: выручка прочая ---
    Rule("revenue_other", True, direction="IN",
         purpose_kw=["оплата по договору", "оплата услуг", "оплата за услуги",
                     "за выполненные работы", "по акту"]),
]


# ---------------------------------------------------------------------------
# Основная логика классификации
# ---------------------------------------------------------------------------

def classify_tx(tx: dict) -> tuple[str, bool]:
    """
    Возвращает (category, taxable).
    taxable=True  → учитывается в налоговой базе АУСН.
    taxable=False → не учитывается.
    taxable=None  → требует ручной проверки (unclassified).
    """
    for rule in RULES:
        if rule.matches(tx):
            return rule.category, rule.taxable

    return "unclassified", None


def run_classification(db_path: Path, only_unclassified: bool = False, force: bool = False):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    if force:
        where = "1=1"
    elif only_unclassified:
        where = "category IS NULL OR category = 'unclassified'"
    else:
        where = "category IS NULL"

    rows = cur.execute(f"""
        SELECT id, doc_type, doc_number, doc_date, value_date,
               amount, direction, counterparty, counterparty_inn,
               purpose, kbk, code_nazn
        FROM bank_transactions
        WHERE {where}
        ORDER BY value_date, id
    """).fetchall()

    if not rows:
        print("✅ Нет транзакций для классификации.")
        con.close()
        return

    stats = {}
    unclassified_ids = []

    for row in rows:
        tx = dict(row)
        category, taxable = classify_tx(tx)
        taxable_int = 1 if taxable is True else (0 if taxable is False else None)

        cur.execute("""
            UPDATE bank_transactions
            SET category = ?, taxable = ?
            WHERE id = ?
        """, (category, taxable_int, tx["id"]))

        stats[category] = stats.get(category, 0) + 1
        if category == "unclassified":
            unclassified_ids.append(tx["id"])

    # Создаём задачи для неклассифицированных
    for tx_id in unclassified_ids:
        row = cur.execute(
            "SELECT value_date, amount, direction, counterparty, purpose FROM bank_transactions WHERE id=?",
            (tx_id,)
        ).fetchone()
        if row:
            title = (f"Классифицировать транзакцию: {row['direction']} "
                     f"{row['amount']:.2f} руб. — {row['counterparty'][:40]}")
            cur.execute("""
                INSERT OR IGNORE INTO tasks (title, category, priority, related_id, notes)
                VALUES (?, 'check', 'high', ?, ?)
            """, (title, tx_id, f"Дата: {row['value_date']}\nНазначение: {row['purpose'][:200]}"))

    con.commit()
    con.close()

    # Отчёт
    print()
    print("=" * 56)
    print("  КЛАССИФИКАЦИЯ ТРАНЗАКЦИЙ — ООО «РАМЕВА»")
    print("=" * 56)
    total = sum(stats.values())
    print(f"  Обработано: {total}")
    print()
    for cat, count in sorted(stats.items(), key=lambda x: -x[1]):
        cat_name = CATEGORIES.get(cat, (cat,))[0]
        flag = "⚠️ " if cat == "unclassified" else "   "
        print(f"  {flag}{cat_name:<32} {count:>3}")
    print("-" * 56)
    if unclassified_ids:
        print(f"  ⚠️  Создано задач для проверки: {len(unclassified_ids)}")
        print("     Запустите: python3 classify.py --unclassified")
    else:
        print("  ✅ Все транзакции классифицированы")
    print()


def list_unclassified(db_path: Path):
    """Выводит список неклассифицированных транзакций."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT id, value_date, direction, amount, counterparty, purpose
        FROM bank_transactions
        WHERE category = 'unclassified' OR category IS NULL
        ORDER BY value_date
    """).fetchall()
    con.close()

    if not rows:
        print("✅ Нет неклассифицированных транзакций.")
        return

    print(f"\n⚠️  Неклассифицировано: {len(rows)} транзакций\n")
    for r in rows:
        arrow = "→" if r["direction"] == "OUT" else "←"
        print(f"  ID={r['id']} {r['value_date']} {arrow} {r['amount']:>10,.2f} руб.")
        print(f"    Контрагент: {r['counterparty']}")
        print(f"    Назначение: {(r['purpose'] or '')[:120]}")
        print()


def set_category(db_path: Path, tx_id: int, category: str):
    """Вручную задать категорию транзакции."""
    if category not in CATEGORIES:
        print(f"❌ Неизвестная категория: {category}")
        print(f"   Доступные: {', '.join(CATEGORIES.keys())}")
        return
    _, taxable_default, _ = CATEGORIES[category]
    taxable_int = 1 if taxable_default else 0

    con = sqlite3.connect(db_path)
    con.execute("""
        UPDATE bank_transactions SET category=?, taxable=? WHERE id=?
    """, (category, taxable_int, tx_id))
    con.commit()
    con.close()
    print(f"✅ Транзакция #{tx_id} → категория «{CATEGORIES[category][0]}»")


def main():
    parser = argparse.ArgumentParser(description="Классификатор транзакций АУСН")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--unclassified", action="store_true",
                        help="Показать список неклассифицированных")
    parser.add_argument("--force", action="store_true",
                        help="Переклассифицировать все (не только новые)")
    parser.add_argument("--set", nargs=2, metavar=("TX_ID", "CATEGORY"),
                        help="Вручную задать категорию: --set 5 supplier")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"❌ БД не найдена: {args.db}"); sys.exit(1)

    if args.set:
        set_category(args.db, int(args.set[0]), args.set[1])
    elif args.unclassified:
        list_unclassified(args.db)
    else:
        run_classification(args.db, force=args.force)


if __name__ == "__main__":
    main()
