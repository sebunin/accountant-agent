#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extern_alerts.py — проактивный мониторинг Контур.Экстерн для heartbeat Nanobot.

Вызывается автоматически через HEARTBEAT.md с заданной периодичностью.
Собирает все критичные события в ОДИН запуск (без промежуточных вызовов модели),
выводит их в stdout — агент читает и реагирует.

Использование из командной строки:
  python3 extern_alerts.py          # текстовый вывод для агента
  python3 extern_alerts.py json     # JSON-вывод для машинной обработки

Вывод:
  Текст формата KONTUR_ALERTS: с секциями 🔴 КРИТИЧНО и 🟡 Внимание.

Интеграция с HEARTBEAT.md:
  critical → немедленно уведомить пользователя
  warning  → записать в MEMORY.md, сообщить при следующем обращении
  пусто    → ничего не делать
"""

import sys
import json
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from extern_api import KonturExternClient
from extern_monitor import (
    check_reporting_deadlines,
    check_overdue_reports,
    check_pending_demands,
    get_docflows_requiring_action,
    get_certificates_info,
    _fmt_date,
    DEMAND_RESPONSE_DAYS,
)


# ─── Формирование алертов ─────────────────────────────────────────────────────

def _alerts_from_overdue(client: KonturExternClient) -> list:
    """Критические алерты по просроченным отчётам."""
    alerts = []
    try:
        for f in check_overdue_reports(client):
            alerts.append({
                "level":   "critical",
                "type":    "overdue",
                "message": (
                    f"ПРОСРОЧЕН: {f['form_name']} (КНД {f['knd']}) — {f['authority']}. "
                    f"Дедлайн был {_fmt_date(f['deadline'])}, "
                    f"просрочено на {f['days_overdue']} дн."
                ),
                "details": {k: str(v) for k, v in f.items()},
            })
    except Exception as e:
        alerts.append(_error_alert("просроченные отчёты", e))
    return alerts


def _alerts_from_demands(client: KonturExternClient) -> list:
    """Алерты по требованиям ФНС без ответа."""
    critical_days = client.settings.get("demand_critical_days", 2)
    alerts = []
    try:
        for d in check_pending_demands(client):
            remaining = d["days_remaining"]
            if remaining <= critical_days:
                level   = "critical"
                urgency = (
                    f"ОСТАЛОСЬ {remaining} р.дн. из {DEMAND_RESPONSE_DAYS}"
                    if remaining > 0 else "СРОК ИСТЁК"
                )
            else:
                level   = "warning"
                urgency = f"осталось {remaining} р.дн. из {DEMAND_RESPONSE_DAYS}"

            alerts.append({
                "level":   level,
                "type":    "demand",
                "message": (
                    f"Требование ФНС без ответа (ДО {d['docflow_id']}). "
                    f"Получено {_fmt_date(d['received_at'])}, {urgency}."
                ),
                "details": {k: str(v) for k, v in d.items()},
            })
    except Exception as e:
        alerts.append(_error_alert("требования ФНС", e))
    return alerts


def _alerts_from_deadlines(client: KonturExternClient) -> list:
    """Алерты по ближайшим дедлайнам (только неотправленные)."""
    warn_days = client.settings.get("deadline_warning_days", 5)
    alerts    = []
    try:
        for f in check_reporting_deadlines(client, days_ahead=warn_days):
            if f["is_sent"]:
                continue

            days_left = f["days_left"]
            if days_left == 0:
                urgency = "СЕГОДНЯ"
                level   = "critical"
            elif days_left == 1:
                urgency = "ЗАВТРА"
                level   = "critical"
            else:
                urgency = f"через {days_left} дн."
                level   = "warning"

            alerts.append({
                "level":   level,
                "type":    "deadline",
                "message": (
                    f"Дедлайн {urgency}: {f['form_name']} (КНД {f['knd']}) — {f['authority']}. "
                    f"Срок: {_fmt_date(f['deadline'])}. Статус: {f['status']}."
                ),
                "details": {k: str(v) for k, v in f.items()},
            })
    except Exception as e:
        alerts.append(_error_alert("дедлайны отчётности", e))
    return alerts


def _alerts_from_action_required(client: KonturExternClient) -> list:
    """Алерты по ДО, требующим ответных действий."""
    alerts = []
    try:
        for df in get_docflows_requiring_action(client):
            alerts.append({
                "level":   "warning",
                "type":    "action",
                "message": (
                    f"Требует ответного документа: {df['doc_type']} ({df['authority']}), "
                    f"ДО {df['docflow_id']}, статус: {df['status']}."
                ),
                "details": {k: str(v) for k, v in df.items()},
            })
    except Exception as e:
        alerts.append(_error_alert("ответные документы", e))
    return alerts


def _alerts_from_certs(client: KonturExternClient) -> list:
    """Алерты по истекающим или истёкшим сертификатам КЭП."""
    alerts = []
    try:
        for c in get_certificates_info(client):
            if c["is_expired"]:
                alerts.append({
                    "level":   "critical",
                    "type":    "cert",
                    "message": (
                        f"СЕРТИФИКАТ КЭП ИСТЁК: {c['fio']} (ИНН {c['inn']}). "
                        f"Срок истёк {_fmt_date(c['valid_to'])}. Необходима замена."
                    ),
                    "details": {k: str(v) for k, v in c.items()},
                })
            elif c["is_expiring_soon"]:
                alerts.append({
                    "level":   "warning",
                    "type":    "cert",
                    "message": (
                        f"Сертификат КЭП истекает через {c['days_until_expiry']} дн.: "
                        f"{c['fio']} (ИНН {c['inn']}). "
                        f"Действителен до {_fmt_date(c['valid_to'])}."
                    ),
                    "details": {k: str(v) for k, v in c.items()},
                })
    except Exception as e:
        alerts.append(_error_alert("сертификаты КЭП", e))
    return alerts


def _error_alert(check_name: str, exc: Exception) -> dict:
    return {
        "level":   "warning",
        "type":    "system",
        "message": f"Не удалось проверить {check_name}: {exc}",
        "details": {},
    }


# ─── Главная функция ──────────────────────────────────────────────────────────

def run_alerts(client: KonturExternClient = None) -> list:
    """
    Запустить все проверки и вернуть список алертов.

    Порядок проверок (от критичных к менее срочным):
    1. Просроченные отчёты
    2. Требования ФНС без ответа
    3. Ближайшие дедлайны (не отправленные)
    4. ДО, требующие ответных документов
    5. Сертификаты КЭП

    Все проверки выполняются в одном вызове — модель не вызывается
    между отдельными запросами к Контуру.
    """
    if client is None:
        client = KonturExternClient()

    alerts = []
    alerts += _alerts_from_overdue(client)
    alerts += _alerts_from_demands(client)
    alerts += _alerts_from_deadlines(client)
    alerts += _alerts_from_action_required(client)
    alerts += _alerts_from_certs(client)

    # Сортируем: critical первыми
    priority = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: priority.get(a["level"], 9))
    return alerts


def format_for_agent(alerts: list) -> str:
    """Форматировать список алертов в текст для агента."""
    if not alerts:
        return "KONTUR_ALERTS: нет событий, требующих внимания."

    critical = [a for a in alerts if a["level"] == "critical"]
    warnings = [a for a in alerts if a["level"] == "warning"]

    lines = ["KONTUR_ALERTS:"]
    if critical:
        lines.append(f"\n🔴 КРИТИЧНО ({len(critical)}):")
        for a in critical:
            lines.append(f"  • {a['message']}")
    if warnings:
        lines.append(f"\n🟡 Внимание ({len(warnings)}):")
        for a in warnings:
            lines.append(f"  • {a['message']}")

    lines.append(
        f"\nВсего: {len(alerts)} (критичных: {len(critical)}, предупреждений: {len(warnings)})"
    )
    lines.append(f"Проверено: {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}")
    return "\n".join(lines)


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    output_format = sys.argv[1] if len(sys.argv) > 1 else "text"

    try:
        client = KonturExternClient()
        alerts = run_alerts(client)
    except SystemExit:
        raise
    except Exception as e:
        error = [{
            "level":   "critical",
            "type":    "system",
            "message": f"Ошибка инициализации клиента Контур.Экстерн: {e}",
            "details": {},
        }]
        if output_format == "json":
            print(json.dumps(error, ensure_ascii=False, default=str))
        else:
            print(f"[KONTUR ERROR] {e}")
        sys.exit(1)

    if output_format == "json":
        print(json.dumps(alerts, ensure_ascii=False, default=str))
    else:
        print(format_for_agent(alerts))
