#!/usr/bin/env python3
"""
Сверка ОСВ (Оборотно-сальдовая ведомость) с банковскими данными в rameva.db.
Ожидает CSV-файл ОСВ в формате:
- разделитель: ';'
- кодировка: cp1251
- структура строк: код счета; наименование; начальный сальдо дебета; начальный сальдо кредита; оборот дебета; оборот кредита; конечное сальдо дебета; конечное сальдо кредита
(как в выгрузке Контур.Экстерн).
Скрипт ищет счёт 51 "Расчетные счета" и сравнивает с банковскими totals.
"""
import sys
import csv
import sqlite3
from pathlib import Path

def read_osv(csv_path: Path):
    """Возвращает dict с данными по счёту 51 или None."""
    with csv_path.open(encoding='cp1251', newline='') as f:
        reader = csv.reader(f, delimiter=';')
        for row in reader:
            if not row:
                continue
            # Первый столбец может содержать код счета с пробелами
            code = row[0].strip()
            if code == '51':
                # Ожидаем минимум 8 колонок: код, наименование, НачД, НачК, ОбД, ОбК, КонД, КонК
                if len(row) < 8:
                    print(f"Ошибка: недостаточно колонок в строке ОСВ для счёта 51: {row}")
                    return None
                try:
                    # Преобразуем строки с запятыми как десятичный разделитель
                    def to_float(s):
                        s = s.strip().replace(' ', '').replace(',', '.')
                        return float(s) if s else 0.0
                    opening_debit = to_float(row[2])
                    opening_credit = to_float(row[3])
                    debit_turnover = to_float(row[4])
                    credit_turnover = to_float(row[5])
                    closing_debit = to_float(row[6])
                    closing_credit = to_float(row[7])
                except ValueError as e:
                    print(f"Ошибка преобразования чисел в строке ОСВ: {row} -> {e}")
                    return None
                return {
                    'account': '51',
                    'name': row[1].strip() if len(row) > 1 else '',
                    'opening_debit': opening_debit,
                    'opening_credit': opening_credit,
                    'debit_turnover': debit_turnover,
                    'credit_turnover': credit_turnover,
                    'closing_debit': closing_debit,
                    'closing_credit': closing_credit,
                }
    print("Счёт 51 не найден в ОСВ CSV.")
    return None

def get_bank_totals(db_path: Path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT direction, SUM(amount) FROM bank_transactions GROUP BY direction")
    rows = c.fetchall()
    conn.close()
    totals = {'IN': 0.0, 'OUT': 0.0}
    for direction, amount in rows:
        if direction in totals:
            totals[direction] = float(amount)
    return totals

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 reconcile_osv.py <path_to_osv.csv> <path_to_rameva.db>")
        sys.exit(1)
    osv_path = Path(sys.argv[1])
    db_path = Path(sys.argv[2])
    if not osv_path.is_file():
        print(f"Ошибка: файл ОСВ не найден: {osv_path}")
        sys.exit(1)
    if not db_path.is_file():
        print(f"Ошибка: файл БД не найден: {db_path}")
        sys.exit(1)
    osv_data = read_osv(osv_path)
    if osv_data is None:
        sys.exit(1)
    bank = get_bank_totals(db_path)
    print("=== Данные ОСВ (счёт 51 Расчётные счета) ===")
    print(f"Начальное сальдо дебета: {osv_data['opening_debit']:.2f}")
    print(f"Оборот по дебету: {osv_data['debit_turnover']:.2f}")
    print(f"Оборот по кредиту: {osv_data['credit_turnover']:.2f}")
    print(f"Конечное сальдо дебета: {osv_data['closing_debit']:.2f}")
    print()
    print("=== Банковские итогы (из rameva.db) ===")
    print(f"Поступило (IN): {bank['IN']:.2f}")
    print(f"Списано (OUT): {bank['OUT']:.2f}")
    # Вычисляем ожидаемое конечное сальдо по банку: начального сальдо (из ОСВ?) + IN - OUT
    # Начальное сальдо должно совпадать с ОСВ на начало года.
    expected_closing = osv_data['opening_debit'] + bank['IN'] - bank['OUT']
    print(f"Ожидаемое конечное сальдо (начальное ОСВ + IN - OUT): {expected_closing:.2f}")
    print(f"Фактическое конечное сальдо из ОСВ: {osv_data['closing_debit']:.2f}")
    diff = osv_data['closing_debit'] - expected_closing
    print(f"Разница (ОСВ - ожидаемое): {diff:.2f}")
    if abs(diff) < 0.01:
        print("Сальдо совпадает.")
    else:
        print("ВНИМАНИЕ: обнаружено расхождение! Требуется сверка проводок.")
    # Также сравним обороты
    print()
    print("=== Сравнение оборотов ===")
    print(f"ОСВ оборот дебета: {osv_data['debit_turnover']:.2f} vs Банк IN: {bank['IN']:.2f} => разница: {osv_data['debit_turnover'] - bank['IN']:.2f}")
    print(f"ОСВ оборот кредита: {osv_data['credit_turnover']:.2f} vs Банк OUT: {bank['OUT']:.2f} => разница: {osv_data['credit_turnover'] - bank['OUT']:.2f}")

if __name__ == '__main__':
    main()