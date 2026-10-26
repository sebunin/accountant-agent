#!/usr/bin/env python3
"""
Временный трекер задач accountant-agent.
Хранит задачи в JSON, вызывается по крону, выдаёт дайджест директору.
Удалить после готовности API программы учёта.
"""

import json
import sys
import argparse
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
from uuid import uuid4

SKILL_DIR = Path(__file__).parent.parent
DATA_DIR = SKILL_DIR / "data"
TASKS_FILE = DATA_DIR / "tasks.json"

# Дефолтные задачи на 19.10.2026 (из PROFILE.md / MEMORY.md)
DEFAULT_TASKS = [
    {
        "id": "tsk_20261013_001",
        "title": "Корр. ЕФС-1 № 002 (20 625 / 41.25)",
        "type": "reporting",
        "status": "in_progress",
        "priority": "high",
        "due_date": "2026-10-25",
        "created": "2026-10-13",
        "updated": "2026-10-19",
        "details": {"form": "ЕФС-1", "period": "2026-Q2", "correction": "002", "amount": 41.25, "base": 20625},
        "blockers": [],
        "next_action": "Сверка с требованием № 63971, подготовка XML"
    },
    {
        "id": "tsk_20261013_002",
        "title": "Корр. ЕФС-1 № 001 за полугодие ([SALARY_FULL_WITH_SPACE] / 55.00)",
        "type": "reporting",
        "status": "open",
        "priority": "high",
        "due_date": "2026-10-25",
        "created": "2026-10-13",
        "updated": "2026-10-13",
        "details": {"form": "ЕФС-1", "period": "2026-H1", "correction": "001", "amount": 55.00, "base": [SALARY_FULL]},
        "blockers": [],
        "next_action": "Подготовка корректировки после корр. № 002"
    },
    {
        "id": "tsk_20261013_003",
        "title": "Уведомление 34/02 + аванс УСН (~2 431 ₽)",
        "type": "reporting",
        "status": "open",
        "priority": "high",
        "due_date": "2026-10-25",
        "created": "2026-10-13",
        "updated": "2026-10-13",
        "details": {"form": "34/02", "period": "2026-Q2", "amount": 2431},
        "blockers": [],
        "next_action": "Расчёт аванса, заполнение уведомления"
    },
    {
        "id": "tsk_20261013_004",
        "title": "Взносы за апрель 8 127,90 + пени с 29.07",
        "type": "payment",
        "status": "open",
        "priority": "critical",
        "due_date": "2026-10-25",
        "created": "2026-10-13",
        "updated": "2026-10-13",
        "details": {"period": "2026-04", "base": 27093, "rate": 0.30, "amount": 8127.90, "penalty_from": "2026-07-29"},
        "blockers": [],
        "next_action": "Расчёт пени, формирование платёжки, ходатайство ст. 114"
    },
    {
        "id": "tsk_20261013_005",
        "title": "Штрафное ходатайство ст. 119/114 НК",
        "type": "response",
        "status": "open",
        "priority": "high",
        "due_date": "2026-11-01",
        "created": "2026-10-13",
        "updated": "2026-10-13",
        "details": {"article": "119/114 НК", "reason": "просрочка РСВ за II квартал"},
        "blockers": ["tsk_20261013_001"],
        "next_action": "Собрать доказательства устранения причин, написать ходатайство"
    },
    {
        "id": "tsk_20261013_006",
        "title": "Проверка снятия блокировки № 105718359 (Банкинформ)",
        "type": "monitoring",
        "status": "in_progress",
        "priority": "critical",
        "due_date": "2026-10-14",
        "created": "2026-10-13",
        "updated": "2026-10-14",
        "details": {"block_id": "105718359", "grund": "02", "report_accepted": "2026-10-13T03:55"},
        "blockers": [],
        "next_action": "Запрос к Банкинформу 14.10, если не снята — обращение в ИФНС"
    },
    {
        "id": "tsk_20261013_007",
        "title": "Ежемесячная сверка выписки ПСБ / классификация",
        "type": "reconciliation",
        "status": "recurring",
        "priority": "medium",
        "due_date": "monthly",
        "created": "2026-10-13",
        "updated": "2026-10-13",
        "details": {"recurring": True, "period": "monthly", "script": "parse_bank.py + classify.py"},
        "blockers": [],
        "next_action": "Загрузить выписку за прошедший месяц, классифицировать >1000₽ с подтверждением"
    },
    {
        "id": "tsk_20261013_008",
        "title": "Параллельный расчёт АУСН (8% × доходы) для контроля",
        "type": "monitoring",
        "status": "recurring",
        "priority": "medium",
        "due_date": "monthly",
        "created": "2026-10-13",
        "updated": "2026-10-13",
        "details": {"recurring": True, "period": "monthly", "script": "tax_calc.py"},
        "blockers": [],
        "next_action": "Запустить tax_calc.py после закрытия месяца, сравнить с суммой ФНС (15-е)"
    },
    {
        "id": "tsk_20261013_009",
        "title": "Годовая УСН-декларация (34) за янв–апр 2026",
        "type": "reporting",
        "status": "open",
        "priority": "medium",
        "due_date": "2027-03-25",
        "created": "2026-10-13",
        "updated": "2026-10-13",
        "details": {"form": "34", "period": "2026-01-04", "transition": True},
        "blockers": [],
        "next_action": "Подготовить после закрытия 2026 года, до 25.03.2027"
    }
]

TASK_TYPES = ["reporting", "payment", "monitoring", "reconciliation", "response", "admin"]
STATUSES = ["open", "in_progress", "blocked", "review", "done", "cancelled", "recurring"]
PRIORITIES = ["critical", "high", "medium", "low"]


def load_tasks() -> list:
    if not TASKS_FILE.exists():
        return DEFAULT_TASKS.copy()
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_TASKS.copy()


def save_tasks(tasks: list):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def parse_date(d: str) -> Optional[date]:
    if d in ("monthly", "recurring", ""):
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        return None


def is_overdue(task: dict) -> bool:
    d = parse_date(task.get("due_date", ""))
    if d is None:
        return False
    return d < date.today() and task["status"] not in ("done", "cancelled")


def is_critical(task: dict) -> bool:
    return task["priority"] == "critical" or is_overdue(task)


def fmt_task(task: dict, verbose: bool = False) -> str:
    status_icons = {
        "open": "🔘", "in_progress": "▶️", "blocked": "🚫",
        "review": "👀", "done": "✅", "cancelled": "❌", "recurring": "🔄"
    }
    priority_icons = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
    icon = status_icons.get(task["status"], "❓")
    picon = priority_icons.get(task["priority"], "⚪")
    due = task.get("due_date", "—")
    overdue_mark = " ⚠ ПРОСРОЧЕНО" if is_overdue(task) else ""
    lines = [f"{icon} {picon} [{task['id']}] {task['title']} — {task['type']} | {task['status']} | до {due}{overdue_mark}"]
    if verbose:
        if task.get("next_action"):
            lines.append(f"    → {task['next_action']}")
        if task.get("blockers"):
            lines.append(f"    🔗 Блокеры: {', '.join(task['blockers'])}")
        if task.get("details"):
            det = ", ".join(f"{k}={v}" for k, v in task["details"].items() if v)
            if det:
                lines.append(f"    📋 {det}")
    return "\n".join(lines)


def cmd_list(args):
    tasks = load_tasks()
    if args.active:
        tasks = [t for t in tasks if t["status"] not in ("done", "cancelled")]
    if args.type:
        tasks = [t for t in tasks if t["type"] == args.type]
    if args.priority:
        tasks = [t for t in tasks if t["priority"] == args.priority]
    if not tasks:
        print("Задач нет.")
        return
    for t in tasks:
        print(fmt_task(t, args.verbose))
        print()


def cmd_overdue(args):
    tasks = load_tasks()
    overdue = [t for t in tasks if is_overdue(t) or t["priority"] == "critical"]
    if not overdue:
        print("Просроченных/критичных задач нет.")
        return
    print("🔴 ПРОСРОЧЕННЫЕ И КРИТИЧНЫЕ:")
    for t in overdue:
        print(fmt_task(t, True))
        print()


def cmd_digest(args):
    tasks = load_tasks()
    active = [t for t in tasks if t["status"] not in ("done", "cancelled")]
    critical = [t for t in active if is_critical(t)]
    high = [t for t in active if t["priority"] == "high" and not is_critical(t)]
    medium = [t for t in active if t["priority"] == "medium"]
    low = [t for t in active if t["priority"] == "low"]

    today = date.today().strftime("%d.%m.%Y")
    lines = [f"📋 Дайджест задач accountant-agent — {today}", ""]

    if critical:
        lines.append("🔴 КРИТИЧНО / ПРОСРОЧЕНО:")
        for t in critical:
            due = t.get("due_date", "—")
            lines.append(f"  • [{t['id']}] {t['title']} — до {due}")
        lines.append("")

    if high:
        lines.append("🟠 ВЫСОКИЙ ПРИОРИТЕТ:")
        for t in high:
            due = t.get("due_date", "—")
            lines.append(f"  • [{t['id']}] {t['title']} — до {due}")
        lines.append("")

    if medium:
        lines.append("🟡 СРЕДНИЙ ПРИОРИТЕТ:")
        for t in medium[:5]:
            due = t.get("due_date", "—")
            lines.append(f"  • [{t['id']}] {t['title']} — до {due}")
        if len(medium) > 5:
            lines.append(f"  … и ещё {len(medium) - 5} задач")
        lines.append("")

    if low:
        lines.append(f"🟢 НИЗКИЙ ПРИОРИТЕТ: {len(low)} задач")
        lines.append("")

    lines.append(f"Всего активных: {len(active)} | Критичных: {len(critical)} | Высоких: {len(high)}")

    digest = "\n".join(lines)
    print(digest)

    if args.notify:
        # TODO: отправить в Telegram через hermes или бота
        # Пока просто сохраняем в файл для ручной отправки
        out_file = DATA_DIR / f"digest_{date.today().isoformat()}.txt"
        out_file.write_text(digest, encoding="utf-8")
        print(f"\n💾 Дайджест сохранён: {out_file}")


def cmd_add(args):
    tasks = load_tasks()
    task_id = f"tsk_{date.today().strftime('%Y%m%d')}_{len(tasks)+1:03d}"
    task = {
        "id": task_id,
        "title": args.title,
        "type": args.type,
        "status": "open",
        "priority": args.priority,
        "due_date": args.due or "",
        "created": date.today().isoformat(),
        "updated": date.today().isoformat(),
        "details": {},
        "blockers": [],
        "next_action": ""
    }
    tasks.append(task)
    save_tasks(tasks)
    print(f"✅ Задача создана: {task_id}")


def cmd_update(args):
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == args.id:
            if args.status:
                t["status"] = args.status
            if args.priority:
                t["priority"] = args.priority
            if args.due:
                t["due_date"] = args.due
            if args.next_action:
                t["next_action"] = args.next_action
            t["updated"] = date.today().isoformat()
            save_tasks(tasks)
            print(f"✅ Задача {args.id} обновлена")
            return
    print(f"❌ Задача {args.id} не найдена")
    sys.exit(1)


def cmd_sync(args):
    # Заглушка для будущей синхронизации с программой учёта
    print("⚠ Синхронизация с программой учёта пока не реализована (API недоступен)")
    if args.to_program:
        print("  Планируется: POST /api/tasks для каждой активной задачи")


def main():
    parser = argparse.ArgumentParser(description="Временный трекер задач accountant-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="Список задач")
    p_list.add_argument("--active", action="store_true", help="Только активные")
    p_list.add_argument("--type", choices=TASK_TYPES, help="Фильтр по типу")
    p_list.add_argument("--priority", choices=PRIORITIES, help="Фильтр по приоритету")
    p_list.add_argument("-v", "--verbose", action="store_true", help="Подробно")

    p_overdue = sub.add_parser("overdue", help="Просроченные и критичные")

    p_digest = sub.add_parser("digest", help="Дайджест для директора")
    p_digest.add_argument("--notify", action="store_true", help="Сохранить в файл для отправки")

    p_add = sub.add_parser("add", help="Добавить задачу")
    p_add.add_argument("--title", required=True, help="Название")
    p_add.add_argument("--type", choices=TASK_TYPES, required=True, help="Тип")
    p_add.add_argument("--due", help="Срок YYYY-MM-DD")
    p_add.add_argument("--priority", choices=PRIORITIES, default="medium", help="Приоритет")

    p_update = sub.add_parser("update", help="Обновить задачу")
    p_update.add_argument("id", help="ID задачи")
    p_update.add_argument("--status", choices=STATUSES, help="Статус")
    p_update.add_argument("--priority", choices=PRIORITIES, help="Приоритет")
    p_update.add_argument("--due", help="Срок YYYY-MM-DD")
    p_update.add_argument("--next-action", help="Следующее действие")

    p_sync = sub.add_parser("sync", help="Синхронизация с программой учёта (заглушка)")
    p_sync.add_argument("--to-program", action="store_true", help="Отправить в программу")

    args = parser.parse_args()

    if args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "overdue":
        cmd_overdue(args)
    elif args.cmd == "digest":
        cmd_digest(args)
    elif args.cmd == "add":
        cmd_add(args)
    elif args.cmd == "update":
        cmd_update(args)
    elif args.cmd == "sync":
        cmd_sync(args)


if __name__ == "__main__":
    main()