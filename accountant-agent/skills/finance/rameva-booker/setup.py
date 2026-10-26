"""
setup.py — интерактивный онбординг субагента-бухгалтера ООО «РАМЕВА»
Запускается автоматически при первом запуске навыка.

Запуск:
    python3 setup.py [--db rameva.db] [--config config.yaml]
"""

import sqlite3
import argparse
import sys
import os
from pathlib import Path
from datetime import datetime

DEFAULT_DB     = Path("~/.hermes/skills/rameva/rameva.db").expanduser()
DEFAULT_CONFIG = Path("~/.hermes/skills/rameva/config.yaml").expanduser()
SCRIPTS_DIR    = Path("~/.hermes/skills/rameva/scripts").expanduser()


def clr(code): return f"\033[{code}m"
BOLD  = clr("1"); RESET = clr("0"); GREEN = clr("32")
YELLOW= clr("33"); RED   = clr("31"); CYAN  = clr("36")


def hr(char="─", w=60):
    print(char * w)


def header(text):
    print(); hr("═"); print(f"  {BOLD}{text}{RESET}"); hr("═")


def step(n, total, text):
    print(f"\n{CYAN}[{n}/{total}]{RESET} {BOLD}{text}{RESET}")
    hr()


def ok(text):    print(f"  {GREEN}✅ {text}{RESET}")
def warn(text):  print(f"  {YELLOW}⚠️  {text}{RESET}")
def info(text):  print(f"  {CYAN}ℹ️  {text}{RESET}")
def err(text):   print(f"  {RED}❌ {text}{RESET}")


def ask(prompt, default="", required=False):
    suffix = f" [{default}]" if default else (" (обязательно)" if required else "")
    while True:
        val = input(f"  → {prompt}{suffix}: ").strip()
        if not val and default:
            return default
        if not val and required:
            print("    Поле обязательно для заполнения.")
            continue
        return val or default


def ask_file(prompt, extensions=None):
    while True:
        val = input(f"  → {prompt}: ").strip().strip("'\"")
        if not val:
            return None
        p = Path(val).expanduser()
        if not p.exists():
            warn(f"Файл не найден: {p}")
            if ask("Пропустить?", "да") == "да":
                return None
            continue
        if extensions and p.suffix.lower() not in extensions:
            warn(f"Ожидается файл {extensions}, получен {p.suffix}")
            continue
        return p


def check_db(db_path: Path) -> bool:
    """Проверяет, инициализирована ли БД."""
    if not db_path.exists():
        return False
    try:
        con = sqlite3.connect(db_path)
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        con.close()
        return len(tables) >= 5
    except Exception:
        return False


def check_first_run(db_path: Path) -> bool:
    """True если это первый запуск (нет данных в state)."""
    if not check_db(db_path):
        return True
    try:
        con = sqlite3.connect(db_path)
        setup_done = con.execute(
            "SELECT value FROM state WHERE key='setup_completed'"
        ).fetchone()
        con.close()
        return not (setup_done and setup_done[0] == "1")
    except Exception:
        return True


def run_script(script: str, *args) -> bool:
    """Запускает Python-скрипт из папки scripts/."""
    path = SCRIPTS_DIR / script
    if not path.exists():
        # Попробовать рядом с setup.py
        path = Path(__file__).parent / script
    if not path.exists():
        err(f"Скрипт не найден: {script}")
        return False
    cmd = f"python3 {path} {' '.join(str(a) for a in args)}"
    info(f"Выполняю: {cmd}")
    return os.system(cmd) == 0


def main():
    parser = argparse.ArgumentParser(description="Онбординг навыка Рамева-Бухгалтер")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--force", action="store_true",
                        help="Запустить онбординг даже если уже настроено")
    args = parser.parse_args()

    header("НАСТРОЙКА НАВЫКА «РАМЕВА-БУХГАЛТЕР»")
    print(f"\n  Это первоначальная настройка. Займёт 2–5 минут.")
    print(f"  Вы сможете загрузить начальные данные и убедиться,")
    print(f"  что всё работает правильно.\n")

    TOTAL = 6

    # ─────────────────────────────────────────────────────────
    step(1, TOTAL, "Проверка базы данных")

    if check_db(args.db) and not args.force:
        ok(f"БД уже инициализирована: {args.db}")
    else:
        info(f"Инициализирую БД: {args.db}")
        if not run_script("init_db.py", f"--db {args.db}"):
            err("Не удалось создать БД. Проверьте права доступа.")
            sys.exit(1)
        ok("БД создана.")

    if not check_first_run(args.db) and not args.force:
        print(f"\n  {YELLOW}Навык уже настроен.{RESET}")
        print(f"  Используйте --force для повторной настройки.\n")
        _print_quick_ref()
        return

    # ─────────────────────────────────────────────────────────
    step(2, TOTAL, "Проверка config.yaml")

    if args.config.exists():
        ok(f"Конфигурация найдена: {args.config}")
    else:
        warn(f"Файл config.yaml не найден: {args.config}")
        info("Скопируйте config.yaml в папку навыка и запустите setup заново.")
        sys.exit(1)

    # ─────────────────────────────────────────────────────────
    step(3, TOTAL, "Загрузка банковской выписки")

    print("  Выписка в формате 1CClientBankExchange (.txt) из ПСБ.")
    print("  Скачать: ПСБ Бизнес → Выписки → Экспорт → 1С\n")

    bank_file = ask_file(
        "Путь к файлу выписки (Enter — пропустить)",
        extensions=[".txt"]
    )

    if bank_file:
        ok_bank = run_script("parse_bank.py", bank_file, f"--db {args.db}")
        if ok_bank:
            run_script("classify.py", f"--db {args.db}")
            ok("Выписка загружена и классифицирована.")
        else:
            warn("Ошибка при импорте выписки. Можно загрузить позже командой /rameva выписка")
    else:
        warn("Выписка пропущена. Загрузите позже: /rameva выписка <файл>")

    # ─────────────────────────────────────────────────────────
    step(4, TOTAL, "Загрузка оборотно-сальдовой ведомости (ОСВ)")

    print("  ОСВ нужна для сверки остатков по счетам.")
    print("  Выгрузить: Контур.Экстерн → Отчётность → ОСВ → Экспорт CSV\n")

    osv_file = ask_file(
        "Путь к файлу ОСВ (Enter — пропустить)",
        extensions=[".csv"]
    )

    if osv_file:
        ok_osv = run_script("parse_osv.py", osv_file, f"--db {args.db}")
        if ok_osv:
            ok("ОСВ загружена. Сверка проведена.")
        else:
            warn("Ошибка при загрузке ОСВ.")
    else:
        warn("ОСВ пропущена. Загрузите позже: /rameva осв <файл>")

    # ─────────────────────────────────────────────────────────
    step(5, TOTAL, "Загрузка реестра документов (Контур.Экстерн)")

    print("  Реестр содержит все документы за всё время.")
    print("  Выгрузить: Контур.Экстерн → Документы → Реестр → Экспорт CSV\n")

    reg_file = ask_file(
        "Путь к файлу реестра (Enter — пропустить)",
        extensions=[".csv"]
    )

    if reg_file:
        ok_reg = run_script("parse_registry.py", reg_file, f"--db {args.db}")
        if ok_reg:
            ok("Реестр документов загружен.")
        else:
            warn("Ошибка при загрузке реестра.")
    else:
        warn("Реестр пропущен. Загрузите позже: /rameva реестр <файл>")

    # ─────────────────────────────────────────────────────────
    step(6, TOTAL, "Расчёт налоговых обязательств")

    current_period = datetime.now().strftime("%Y-%m")
    info(f"Считаю обязательства за {current_period}...")
    run_script("tax_calc.py", f"--db {args.db}", f"--period {current_period}", "--update")

    # ─────────────────────────────────────────────────────────
    # Отметить онбординг завершённым
    con = sqlite3.connect(args.db)
    con.execute("""
        INSERT OR REPLACE INTO state (key, value, updated)
        VALUES ('setup_completed', '1', datetime('now','localtime'))
    """)
    con.execute("""
        INSERT OR REPLACE INTO state (key, value, updated)
        VALUES ('setup_date', ?, datetime('now','localtime'))
    """, (datetime.now().isoformat(),))
    con.commit()
    con.close()

    # ─────────────────────────────────────────────────────────
    header("НАСТРОЙКА ЗАВЕРШЕНА ✅")
    _print_quick_ref()


def _print_quick_ref():
    print(f"""
  {BOLD}Быстрый справочник команд:{RESET}

  {CYAN}Статус:{RESET}
    python3 reports.py status

  {CYAN}Загрузить выписку банка:{RESET}
    python3 parse_bank.py выписка.txt && python3 classify.py

  {CYAN}Загрузить ОСВ и провести сверку:{RESET}
    python3 parse_osv.py ОСВ.csv --reconcile

  {CYAN}Загрузить реестр документов:{RESET}
    python3 parse_registry.py Реестр.csv

  {CYAN}Налоговый расчёт АУСН:{RESET}
    python3 tax_calc.py --period 2026-05

  {CYAN}Полный дашборд:{RESET}
    python3 reports.py full

  Или через Hermes: {BOLD}/rameva статус{RESET}, {BOLD}/rameva выписка{RESET}, {BOLD}/rameva отчёт{RESET}
""")


if __name__ == "__main__":
    main()
