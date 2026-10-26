"""
tax_calc.py — расчёт налогов для АУСН 8% и контроль обязательных платежей
ООО «РАМЕВА», ИНН [INN]

Запуск:
    python3 tax_calc.py [--db rameva.db] [--period 2026-05] [--update]
"""

import sqlite3
import argparse
import sys
from pathlib import Path
from datetime import date, datetime
from typing import Optional

DEFAULT_DB = Path("~/.hermes/skills/rameva/rameva.db").expanduser()

AUSN_RATE     = 0.08   # 8% от (доходы - расходы)
AUSN_MIN_RATE = 0.03   # 3% от доходов (минимальный налог)

# Категории, учитываемые в налоговой базе АУСН
INCOME_CATEGORIES  = {"revenue_it", "revenue_other"}
EXPENSE_CATEGORIES = {"salary", "sfr_ns", "bank_fee", "supplier"}

# Зарплатные показатели (из config, здесь продублированы для автономности)
SALARY_GROSS      = [SALARY_ACTUAL].00
NDFL_RATE         = 0.13
ALIMONY_RATE      = [RATE]
SFR_NS_RATE       = 0.002
SFR_NS_PAYMENT_DAY = 15   # до 15-го следующего месяца
ENP_PAYMENT_DAY   = 28


# ---------------------------------------------------------------------------
# АУСН: расчёт налога за период
# ---------------------------------------------------------------------------

def calc_ausn(db_path: Path, period: str) -> dict:
    """
    Считает налоговую базу АУСН за период (YYYY-MM).
    Возвращает словарь с суммами.
    """
    year, month = period.split("-")
    period_start = f"{year}-{month}-01"
    # Последний день месяца
    import calendar
    last_day = calendar.monthrange(int(year), int(month))[1]
    period_end = f"{year}-{month}-{last_day:02d}"

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    # Доходы
    income_row = con.execute("""
        SELECT COALESCE(SUM(amount), 0) as total
        FROM bank_transactions
        WHERE direction = 'IN'
          AND taxable = 1
          AND value_date BETWEEN ? AND ?
    """, (period_start, period_end)).fetchone()
    income = income_row["total"]

    # Расходы
    expense_row = con.execute("""
        SELECT COALESCE(SUM(amount), 0) as total
        FROM bank_transactions
        WHERE direction = 'OUT'
          AND taxable = 1
          AND value_date BETWEEN ? AND ?
    """, (period_start, period_end)).fetchone()
    expense = expense_row["total"]

    # Уже уплачено налогов (ЕНП за этот период)
    paid_row = con.execute("""
        SELECT COALESCE(SUM(paid_amount), 0) as total
        FROM tax_obligations
        WHERE period = ? AND type IN ('АУСН','ЕНП') AND status = 'paid'
    """, (period,)).fetchone()
    paid = paid_row["total"]

    con.close()

    tax_base   = max(income - expense, 0)
    tax_ausn   = round(tax_base * AUSN_RATE, 2)
    tax_min    = round(income * AUSN_MIN_RATE, 2)
    tax_due    = max(tax_ausn, tax_min)
    balance    = round(tax_due - paid, 2)

    return {
        "period":     period,
        "income":     round(income, 2),
        "expense":    round(expense, 2),
        "tax_base":   round(tax_base, 2),
        "tax_ausn":   tax_ausn,
        "tax_min":    tax_min,
        "tax_due":    tax_due,
        "paid":       round(paid, 2),
        "balance":    balance,           # > 0 = должны, < 0 = переплата
        "applies_min": tax_min > tax_ausn,
    }


# ---------------------------------------------------------------------------
# Расчёт зарплаты за период
# ---------------------------------------------------------------------------

def calc_payroll(period: str) -> dict:
    """
    Рассчитывает показатели зарплаты Бунина С.Ю. за месяц на АУСН.
    На АУСН: НДФЛ удерживает работодатель, банк перечисляет.
    Взносы СФР НС и ПЗ = 0.2% от gross.
    """
    gross    = SALARY_GROSS
    ndfl     = round(gross * NDFL_RATE)
    net      = round(gross - ndfl, 2)
    alimony  = round(net * ALIMONY_RATE, 2)
    to_hand  = round(net - alimony, 2)
    sfr_ns   = round(gross * SFR_NS_RATE, 2)

    return {
        "period":   period,
        "gross":    gross,
        "ndfl":     ndfl,
        "net":      net,
        "alimony":  alimony,
        "to_hand":  to_hand,
        "sfr_ns":   sfr_ns,
    }


# ---------------------------------------------------------------------------
# Платёжный календарь на месяц
# ---------------------------------------------------------------------------

def payment_calendar(period: str, db_path: Path) -> list[dict]:
    """
    Возвращает список обязательных платежей за период с их статусом.
    """
    year, month = map(int, period.split("-"))
    next_month = month + 1 if month < 12 else 1
    next_year  = year if month < 12 else year + 1

    pr = calc_payroll(period)
    payments = []

    # 1. Аванс (16-е текущего месяца) — межрасчётная выплата
    import calendar
    work_days_total = _count_workdays(year, month)
    work_days_first = _count_workdays(year, month, half=1)
    adv_gross   = round(SALARY_GROSS * work_days_first / work_days_total, 2)
    adv_ndfl    = round(adv_gross * NDFL_RATE)
    adv_net     = round(adv_gross - adv_ndfl, 2)
    adv_alimony = round(adv_net * ALIMONY_RATE, 2)
    adv_to_hand = round(adv_net - adv_alimony, 2)

    payments.append({
        "date": f"{year}-{month:02d}-16",
        "type": "Аванс (зарплата)",
        "recipient": "[DIRECTOR_SHORT]",
        "amount": adv_to_hand,
        "details": f"Gross {adv_gross} − НДФЛ {adv_ndfl} − алименты {adv_alimony}",
    })
    payments.append({
        "date": f"{year}-{month:02d}-16",
        "type": "Алименты (с аванса)",
        "recipient": "Бунина О.Ю.",
        "amount": adv_alimony,
        "details": f"[ALIMONY_RATE] от {adv_net} (аванс после НДФЛ)",
    })

    # 2. Зарплата — 1-е следующего месяца
    sal_gross   = round(SALARY_GROSS - adv_gross, 2)
    sal_ndfl    = round(sal_gross * NDFL_RATE)
    sal_net     = round(sal_gross - sal_ndfl, 2)
    sal_alimony = round(sal_net * ALIMONY_RATE, 2)
    sal_to_hand = round(sal_net - sal_alimony, 2)

    payments.append({
        "date": f"{next_year}-{next_month:02d}-01",
        "type": "Зарплата (вторая половина)",
        "recipient": "[DIRECTOR_SHORT]",
        "amount": sal_to_hand,
        "details": f"Gross {sal_gross} − НДФЛ {sal_ndfl} − алименты {sal_alimony}",
    })
    payments.append({
        "date": f"{next_year}-{next_month:02d}-01",
        "type": "Алименты (с зарплаты)",
        "recipient": "Бунина О.Ю.",
        "amount": sal_alimony,
        "details": f"[ALIMONY_RATE] от {sal_net} (вторая половина после НДФЛ)",
    })

    # 3. СФР НС — до 15-го следующего месяца
    payments.append({
        "date": f"{next_year}-{next_month:02d}-15",
        "type": "Взносы СФР НС и ПЗ",
        "recipient": "УФК по Липецкой области (ОСФР)",
        "amount": pr["sfr_ns"],
        "details": f"0.2% от {pr['gross']} (зарплата за {period})",
    })

    # 4. ЕНП / АУСН — до 28-го следующего месяца (ФНС выставляет уведомление)
    ausn = calc_ausn(db_path, period)
    if ausn["balance"] > 0:
        payments.append({
            "date": f"{next_year}-{next_month:02d}-28",
            "type": "ЕНП (АУСН налог)",
            "recipient": "Казначейство России (ФНС)",
            "amount": ausn["balance"],
            "details": (f"АУСН 8% × база {ausn['tax_base']:.2f} = {ausn['tax_ausn']:.2f}"
                        + (" [применён минимум 3%]" if ausn["applies_min"] else "")),
        })

    return payments


def _count_workdays(year: int, month: int, half: int = 0) -> int:
    """
    Считает рабочие дни в месяце (пн-пт, без праздников).
    half=1 → только первая половина (1–15), half=2 → вторая (16–конец), 0 → весь месяц.
    Упрощённо: без учёта переносов праздников (для производственного календаря нужен API).
    """
    import calendar
    count = 0
    _, last_day = calendar.monthrange(year, month)
    start = 1 if half != 2 else 16
    end   = 15 if half == 1 else last_day
    for day in range(start, end + 1):
        wd = date(year, month, day).weekday()
        if wd < 5:   # пн=0 … пт=4
            count += 1
    return count


# ---------------------------------------------------------------------------
# Обновление записей tax_obligations в БД
# ---------------------------------------------------------------------------

def update_tax_obligations(db_path: Path, period: str):
    """
    Создаёт/обновляет записи в tax_obligations для периода.
    """
    ausn = calc_ausn(db_path, period)
    pr   = calc_payroll(period)
    year, month = map(int, period.split("-"))
    next_month  = month + 1 if month < 12 else 1
    next_year   = year if month < 12 else year + 1

    con = sqlite3.connect(db_path)

    obligations = [
        # (period, type, amount, due_date)
        (period, "СФР_НС", pr["sfr_ns"],
         f"{next_year}-{next_month:02d}-15"),
        (period, "АУСН",   ausn["tax_due"],
         f"{next_year}-{next_month:02d}-28"),
    ]

    for p, otype, amount, due in obligations:
        existing = con.execute("""
            SELECT id FROM tax_obligations WHERE period=? AND type=?
        """, (p, otype)).fetchone()

        if existing:
            con.execute("""
                UPDATE tax_obligations SET amount=?, due_date=? WHERE id=?
            """, (amount, due, existing[0]))
        else:
            con.execute("""
                INSERT INTO tax_obligations (period, type, amount, due_date, status)
                VALUES (?, ?, ?, ?, 'pending')
            """, (p, otype, amount, due))

    con.commit()
    con.close()
    print(f"✅ Налоговые обязательства за {period} обновлены.")


# ---------------------------------------------------------------------------
# Вывод отчёта
# ---------------------------------------------------------------------------

def print_ausn_report(db_path: Path, period: str):
    ausn = calc_ausn(db_path, period)
    pr   = calc_payroll(period)
    cal  = payment_calendar(period, db_path)

    w = 54
    print()
    print("=" * w)
    print(f"  НАЛОГОВЫЙ РАСЧЁТ АУСН — {period}  |  ООО «РАМЕВА»")
    print("=" * w)
    print(f"  {'Доходы (налогооблагаемые):':<36} {ausn['income']:>12,.2f}")
    print(f"  {'Расходы (налогооблагаемые):':<36} {ausn['expense']:>12,.2f}")
    print(f"  {'Налоговая база:':<36} {ausn['tax_base']:>12,.2f}")
    print("-" * w)
    print(f"  {'Налог АУСН 8%:':<36} {ausn['tax_ausn']:>12,.2f}")
    print(f"  {'Минимальный налог 3% от доходов:':<36} {ausn['tax_min']:>12,.2f}")
    if ausn["applies_min"]:
        print(f"  {'⚠️  Применяется минимальный налог':}")
    print(f"  {'К уплате:':<36} {ausn['tax_due']:>12,.2f}")
    print(f"  {'Уже уплачено (ЕНП):':<36} {ausn['paid']:>12,.2f}")
    bal_sign = "+" if ausn["balance"] < 0 else ""
    print(f"  {'Итого (+ переплата / - долг):':<36} {bal_sign}{-ausn['balance']:>12,.2f}")
    print()
    print(f"  ЗАРПЛАТА ({period})")
    print("-" * w)
    print(f"  {'Начислено (gross):':<36} {pr['gross']:>12,.2f}")
    print(f"  {'НДФЛ 13%:':<36} {pr['ndfl']:>12,.2f}")
    print(f"  {'Алименты [ALIMONY_RATE] от net:':<36} {pr['alimony']:>12,.2f}")
    print(f"  {'На руки директору:':<36} {pr['to_hand']:>12,.2f}")
    print(f"  {'Взносы СФР НС 0.2%:':<36} {pr['sfr_ns']:>12,.2f}")
    print()
    print(f"  ПЛАТЁЖНЫЙ КАЛЕНДАРЬ")
    print("-" * w)
    for p in sorted(cal, key=lambda x: x["date"]):
        print(f"  {p['date']}  {p['type']:<28} {p['amount']:>8,.2f}")
        print(f"            → {p['recipient']}")
    print("=" * w)
    print()


def main():
    parser = argparse.ArgumentParser(description="Расчёт налогов АУСН — ООО «РАМЕВА»")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--period", default=datetime.now().strftime("%Y-%m"),
                        help="Период YYYY-MM (по умолчанию текущий)")
    parser.add_argument("--update", action="store_true",
                        help="Записать обязательства в tax_obligations")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"❌ БД не найдена: {args.db}"); sys.exit(1)

    print_ausn_report(args.db, args.period)

    if args.update:
        update_tax_obligations(args.db, args.period)


if __name__ == "__main__":
    main()
